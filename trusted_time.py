"""Persistência protegida do maior horário observado pelo cliente Windows."""

from __future__ import annotations

import ctypes
import json
import os
import sys
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol

_STATE_SCHEMA = "nexojuris-time-state/v1"
_TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
_DPAPI_ENTROPY = b"NexoJuris/Conversor/license-time/v1"
_REGISTRY_PATH = r"Software\NexoJuris\Conversor"
_REGISTRY_VALUE = "LicenseTimeAnchorV1"
_CRYPTPROTECT_UI_FORBIDDEN = 0x1


class TemporalStateError(RuntimeError):
    """A âncora temporal não pôde ser lida ou protegida com segurança."""


class DataProtector(Protocol):
    def protect(self, plaintext: bytes) -> bytes: ...

    def unprotect(self, ciphertext: bytes) -> bytes: ...


class AnchorStore(Protocol):
    def read(self) -> bytes | None: ...

    def write(self, value: bytes) -> None: ...


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_uint32), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


def _blob(value: bytes) -> tuple[_DataBlob, ctypes.Array[ctypes.c_char]]:
    buffer = ctypes.create_string_buffer(value)
    pointer = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte))
    return _DataBlob(len(value), pointer), buffer


class DpapiProtector:
    """Protege o estado com DPAPI no contexto do usuário atual do Windows."""

    def __init__(self) -> None:
        if sys.platform != "win32":
            raise OSError("DPAPI está disponível somente no Windows.")

    def _transform(self, value: bytes, *, protect: bool) -> bytes:
        if not value:
            raise TemporalStateError("O conteúdo temporal protegido não pode ser vazio.")
        input_blob, input_buffer = _blob(value)
        entropy_blob, entropy_buffer = _blob(_DPAPI_ENTROPY)
        output_blob = _DataBlob()
        crypt32 = ctypes.windll.crypt32
        operation = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
        description = "NexoJuris license time" if protect else None
        result = operation(
            ctypes.byref(input_blob),
            description,
            ctypes.byref(entropy_blob),
            None,
            None,
            _CRYPTPROTECT_UI_FORBIDDEN,
            ctypes.byref(output_blob),
        )
        _ = input_buffer, entropy_buffer
        if not result:
            raise TemporalStateError(f"DPAPI falhou com código {ctypes.get_last_error()}.")
        try:
            return ctypes.string_at(output_blob.pbData, output_blob.cbData)
        finally:
            ctypes.windll.kernel32.LocalFree(output_blob.pbData)

    def protect(self, plaintext: bytes) -> bytes:
        return self._transform(plaintext, protect=True)

    def unprotect(self, ciphertext: bytes) -> bytes:
        return self._transform(ciphertext, protect=False)


class WindowsRegistryAnchor:
    """Âncora redundante que sobrevive à reinstalação dos arquivos do aplicativo."""

    def read(self) -> bytes | None:
        if sys.platform != "win32":
            return None
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _REGISTRY_PATH) as key:
                value, value_type = winreg.QueryValueEx(key, _REGISTRY_VALUE)
        except FileNotFoundError:
            return None
        except OSError as error:
            raise TemporalStateError("Não foi possível ler a âncora temporal do Registro.") from error
        if value_type != winreg.REG_BINARY or not isinstance(value, bytes):
            raise TemporalStateError("A âncora temporal do Registro possui tipo inválido.")
        return value

    def write(self, value: bytes) -> None:
        if sys.platform != "win32":
            return
        import winreg

        try:
            with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, _REGISTRY_PATH, access=winreg.KEY_SET_VALUE) as key:
                winreg.SetValueEx(key, _REGISTRY_VALUE, 0, winreg.REG_BINARY, value)
        except OSError as error:
            raise TemporalStateError("Não foi possível gravar a âncora temporal do Registro.") from error


@dataclass(frozen=True, slots=True)
class TemporalState:
    max_observed_at: datetime
    last_trusted_server_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class TemporalAssessment:
    clock_tampered: bool
    effective_time: datetime
    max_observed_at: datetime
    last_trusted_server_at: datetime | None


def _aware_utc(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} deve possuir fuso horário.")
    return value.astimezone(UTC).replace(microsecond=0)


def _format_time(value: datetime) -> str:
    return _aware_utc(value, "value").strftime(_TIMESTAMP_FORMAT)


def _parse_time(value: object, field_name: str) -> datetime:
    if not isinstance(value, str):
        raise TemporalStateError(f"{field_name} temporal inválido.")
    try:
        return datetime.strptime(value, _TIMESTAMP_FORMAT).replace(tzinfo=UTC)
    except ValueError as error:
        raise TemporalStateError(f"{field_name} temporal inválido.") from error


def _serialize_state(state: TemporalState) -> bytes:
    return json.dumps(
        {
            "schema": _STATE_SCHEMA,
            "max_observed_at": _format_time(state.max_observed_at),
            "last_trusted_server_at": (
                _format_time(state.last_trusted_server_at) if state.last_trusted_server_at is not None else None
            ),
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _deserialize_state(value: bytes) -> TemporalState:
    try:
        data = json.loads(value.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise TemporalStateError("Estado temporal protegido inválido.") from error
    if not isinstance(data, dict) or set(data) != {"schema", "max_observed_at", "last_trusted_server_at"}:
        raise TemporalStateError("Estado temporal protegido possui campos inválidos.")
    if data["schema"] != _STATE_SCHEMA:
        raise TemporalStateError("Versão do estado temporal não suportada.")
    trusted_value = data["last_trusted_server_at"]
    return TemporalState(
        max_observed_at=_parse_time(data["max_observed_at"], "max_observed_at"),
        last_trusted_server_at=(
            None if trusted_value is None else _parse_time(trusted_value, "last_trusted_server_at")
        ),
    )


class TemporalGuard:
    """Mantém âncoras redundantes e detecta retrocesso relevante do relógio."""

    def __init__(
        self,
        state_path: str | Path,
        *,
        protector: DataProtector | None = None,
        anchor: AnchorStore | None = None,
        rollback_tolerance: timedelta = timedelta(minutes=5),
    ) -> None:
        if rollback_tolerance < timedelta(0):
            raise ValueError("rollback_tolerance não pode ser negativa.")
        self.state_path = Path(state_path)
        self.protector = protector or DpapiProtector()
        self.anchor = anchor or WindowsRegistryAnchor()
        self.rollback_tolerance = rollback_tolerance

    def _read_protected(self, value: bytes) -> TemporalState:
        try:
            return _deserialize_state(self.protector.unprotect(value))
        except TemporalStateError:
            raise
        except Exception as error:
            raise TemporalStateError("Não foi possível abrir o estado temporal protegido.") from error

    def _load_state(self) -> TemporalState | None:
        candidates: list[TemporalState] = []
        failures = 0
        sources = []
        try:
            sources.append(self.state_path.read_bytes() if self.state_path.is_file() else None)
        except OSError as error:
            raise TemporalStateError("Não foi possível ler o estado temporal local.") from error
        try:
            sources.append(self.anchor.read())
        except TemporalStateError:
            sources.append(None)
            failures += 1
        for value in sources:
            if value is None:
                continue
            try:
                candidates.append(self._read_protected(value))
            except TemporalStateError:
                failures += 1
        if not candidates:
            if failures:
                raise TemporalStateError("Nenhuma âncora temporal íntegra está disponível.")
            return None
        max_observed = max(item.max_observed_at for item in candidates)
        trusted = [item.last_trusted_server_at for item in candidates if item.last_trusted_server_at is not None]
        return TemporalState(max_observed, max(trusted) if trusted else None)

    def _persist(self, state: TemporalState) -> None:
        try:
            protected = self.protector.protect(_serialize_state(state))
        except TemporalStateError:
            raise
        except Exception as error:
            raise TemporalStateError("Não foi possível proteger o estado temporal.") from error
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{self.state_path.name}.", suffix=".tmp", dir=str(self.state_path.parent)
        )
        try:
            with os.fdopen(descriptor, "wb") as temporary_file:
                temporary_file.write(protected)
                temporary_file.flush()
                os.fsync(temporary_file.fileno())
            os.replace(temporary_name, self.state_path)
        except OSError as error:
            raise TemporalStateError("Não foi possível persistir o estado temporal local.") from error
        finally:
            Path(temporary_name).unlink(missing_ok=True)
        self.anchor.write(protected)

    def observe(self, now: datetime, *, trusted_server_time: datetime | None = None) -> TemporalAssessment:
        local_time = _aware_utc(now, "now")
        trusted_time = _aware_utc(trusted_server_time, "trusted_server_time") if trusted_server_time else None
        previous = self._load_state()
        previous_max = previous.max_observed_at if previous else local_time
        previous_trusted = previous.last_trusted_server_at if previous else None

        if trusted_time is not None:
            effective = max(local_time, trusted_time)
            max_observed = max(previous_max, effective)
            last_trusted = max(filter(None, (previous_trusted, trusted_time)))
            state = TemporalState(max_observed, last_trusted)
            self._persist(state)
            return TemporalAssessment(False, effective, max_observed, last_trusted)

        if previous is not None and local_time < previous_max - self.rollback_tolerance:
            return TemporalAssessment(True, previous_max, previous_max, previous_trusted)

        effective = max(local_time, previous_max)
        state = TemporalState(effective, previous_trusted)
        self._persist(state)
        return TemporalAssessment(False, effective, effective, previous_trusted)
