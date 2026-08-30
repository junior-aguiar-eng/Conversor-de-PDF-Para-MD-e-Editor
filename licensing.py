"""Módulo de Licenciamento por Hardware (Hardware Node-Locking).

Gera identificadores de máquina estáveis e valida licenças offline assinadas com
Ed25519. O cliente contém somente a chave pública; a chave privada fica restrita
ao utilitário administrativo.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import platform
import re
import sqlite3
import subprocess
import tempfile
import uuid
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from app_storage import library_database_path, license_backup_path, license_time_state_path, migrate_legacy_user_data
from license_core import (
    LicenseCoreError,
    LicenseState,
    LicenseStatus,
    PublicKeyRing,
    evaluate_act4,
    invalid_status,
    legacy_valid_status,
    unlicensed_status,
    verify_license,
)
from license_core import (
    require_feature as require_status_feature,
)
from trusted_time import TemporalGuard, TemporalStateError

logger = logging.getLogger(__name__)

_LICENSE_DB_PATH = library_database_path()
_LICENSE_BACKUP_PATH = license_backup_path()
_LICENSE_TIME_STATE_PATH = license_time_state_path()
_LICENSE_KEY_PREFIX = "ACT2-01-"
_LICENSE_KEY_PREFIX_V3 = "ACT3-01-"
_LICENSE_PAYLOAD_PREFIX = b"nexojuris-license:v2:"
_LICENSE_PAYLOAD_PREFIX_V3 = b"nexojuris-license:v3:"
_LICENSE_PUBLIC_KEY_B64 = "80WGyZ+9TwHmcKDPpjOncNZVVYgFHgNBl59aBK5Hpug="
_ACT4_PUBLIC_KEYS_B64 = {"license-main-2026-01": _LICENSE_PUBLIC_KEY_B64}
_LICENSE_KEY_PATTERN = re.compile(r"ACT2-01-(?:[A-Z2-7]{8}-){12}[A-Z2-7]{7}")
_LICENSE_KEY_PATTERN_V3 = re.compile(r"ACT3-01-(?:[A-Z2-7]{8}-){12}[A-Z2-7]{7}")


class LicenseRequiredError(PermissionError):
    """Indica que uma operação protegida exige ativação válida nesta máquina."""

    def __init__(self, machine_id: str) -> None:
        self.machine_id = machine_id
        super().__init__(f"Ativação necessária para converter arquivos. Código da máquina: {machine_id}.")


def license_payload(machine_id: str, version: int = 2) -> bytes:
    """Produz o payload canônico e versionado assinado pelo emissor administrativo."""
    normalized_id = machine_id.strip().upper()
    if version == 3 or normalized_id.startswith("NXJ2-"):
        return _LICENSE_PAYLOAD_PREFIX_V3 + normalized_id.encode("ascii")
    return _LICENSE_PAYLOAD_PREFIX + normalized_id.encode("ascii")


def _decode_activation_signature(key: str) -> tuple[bytes | None, int]:
    candidate = (key or "").strip().upper()
    prefix = ""
    version = 2
    if _LICENSE_KEY_PATTERN.fullmatch(candidate):
        prefix = _LICENSE_KEY_PREFIX
        version = 2
    elif _LICENSE_KEY_PATTERN_V3.fullmatch(candidate):
        prefix = _LICENSE_KEY_PREFIX_V3
        version = 3
    else:
        return None, 2

    encoded = candidate.removeprefix(prefix).replace("-", "")
    padding = "=" * ((8 - len(encoded) % 8) % 8)
    try:
        signature = base64.b32decode(encoded + padding, casefold=False)
    except ValueError:
        return None, version
    return (signature if len(signature) == 64 else None), version


def _public_key() -> Ed25519PublicKey:
    public_bytes = base64.b64decode(_LICENSE_PUBLIC_KEY_B64, validate=True)
    return Ed25519PublicKey.from_public_bytes(public_bytes)


@lru_cache(maxsize=1)
def _get_motherboard_uuid() -> str:
    """Obtém uma identidade estável do Windows sem repetir subprocessos durante a sessão."""
    if platform.system() == "Windows":
        # Fonte primária rápida e estável. O produto ainda não possui licenças emitidas,
        # portanto esta passa a ser a identidade canônica antes da primeira release.
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography") as key:
                guid, _ = winreg.QueryValueEx(key, "MachineGuid")
                clean_guid = str(guid or "").strip().upper()
                if len(clean_guid) > 10:
                    return clean_guid
        except Exception as err:
            logger.debug(f"Winreg MachineGuid indisponível: {err}")

        # Contingência para ambientes onde o Registro não pode ser consultado.
        try:
            cmd = [
                "powershell",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                "(Get-CimInstance Win32_ComputerSystemProduct).UUID",
            ]
            out = subprocess.check_output(cmd, text=True, stderr=subprocess.DEVNULL, timeout=4.0)
            clean_uuid = out.strip().upper()
            if clean_uuid and len(clean_uuid) > 10 and clean_uuid != "FFFFFFFF-FFFF-FFFF-FFFF-FFFFFFFFFFFF":
                return clean_uuid
        except Exception as err:
            logger.debug(f"PowerShell UUID fallback: {err}")

    # Fallback genérico para ambientes não-Windows ou com restrições
    return f"FALLBACK-UUID-{os.environ.get('COMPUTERNAME', platform.node())}"


def _get_primary_mac_address() -> str:
    """Obtém o MAC Address do adaptador de rede principal ou ID de rede estável."""
    try:
        raw_node = uuid.getnode()
        mac = ":".join(f"{(raw_node >> ele) & 0xFF:02x}" for ele in range(40, -8, -8))
        return mac.upper()
    except Exception:
        return "00:00:00:00:00:00"


def get_machine_fingerprint_v1() -> str:
    """Calcula a impressão digital legada (v1) NXJ-XXXX-XXXX-XXXX-XXXX."""
    mb_uuid = _get_motherboard_uuid()
    mac_addr = _get_primary_mac_address()
    processor = platform.processor() or os.environ.get("PROCESSOR_IDENTIFIER", "CPU")
    comp_name = os.environ.get("COMPUTERNAME", platform.node())

    raw_seed = f"UUID:{mb_uuid}|MAC:{mac_addr}|CPU:{processor}|HOST:{comp_name}"
    digest = hashlib.sha256(raw_seed.encode("utf-8")).hexdigest().upper()
    return f"NXJ-{digest[0:4]}-{digest[4:8]}-{digest[8:12]}-{digest[12:16]}"


def get_machine_fingerprint_v2() -> str:
    """Calcula a nova impressão digital estável (v2) NXJ2-XXXX-XXXX-XXXX-XXXX (Item 17)."""
    mb_uuid = _get_motherboard_uuid()
    processor = platform.processor() or os.environ.get("PROCESSOR_IDENTIFIER", "CPU")

    raw_seed = f"UUID:{mb_uuid}|CPU:{processor}|SYS:V2"
    digest = hashlib.sha256(raw_seed.encode("utf-8")).hexdigest().upper()
    return f"NXJ2-{digest[0:4]}-{digest[4:8]}-{digest[8:12]}-{digest[12:16]}"


def get_machine_fingerprint(version: int = 2) -> str:
    """Calcula e retorna a impressão digital (Machine ID) padrão da máquina."""
    if version == 1:
        return get_machine_fingerprint_v1()
    return get_machine_fingerprint_v2()


def verify_license_key(machine_id: str, key: str) -> bool:
    """Valida uma assinatura Ed25519 vinculada ao Machine ID informado (suporta v1 e v2)."""
    if not machine_id or not key:
        return False

    signature, version = _decode_activation_signature(key)
    if signature is None:
        return False

    try:
        _public_key().verify(signature, license_payload(machine_id, version=version))
        return True
    except (InvalidSignature, ValueError):
        return False


@contextmanager
def _db_conn() -> Generator[sqlite3.Connection]:
    """Gerenciador de contexto seguro que fecha conexões SQLite após a execução."""
    if _LICENSE_DB_PATH.resolve() == library_database_path().resolve():
        migrate_legacy_user_data()
    _LICENSE_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_LICENSE_DB_PATH), timeout=5.0)
    conn.row_factory = sqlite3.Row
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def _init_license_table() -> None:
    """Cria ou migra ``system_license`` sem invalidar a linha ACT2/ACT3."""
    try:
        with _db_conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS system_license (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    machine_id TEXT NOT NULL,
                    activation_key TEXT NOT NULL,
                    activated_at REAL NOT NULL,
                    license_format TEXT NOT NULL DEFAULT 'legacy',
                    license_document BLOB
                )
            """)
            columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(system_license)").fetchall()}
            if "license_format" not in columns:
                conn.execute("ALTER TABLE system_license ADD COLUMN license_format TEXT NOT NULL DEFAULT 'legacy'")
            if "license_document" not in columns:
                conn.execute("ALTER TABLE system_license ADD COLUMN license_document BLOB")
    except Exception as err:
        logger.debug(f"Erro ao inicializar tabela de licença: {err}")


@dataclass(frozen=True, slots=True)
class _StoredLicense:
    license_format: str
    machine_id: str
    activation_key: str = ""
    license_document: bytes | None = None


def _record_from_backup(content: str) -> _StoredLicense | None:
    try:
        value = json.loads(content)
    except json.JSONDecodeError:
        parts = content.split(":")
        if len(parts) == 2 and parts[0].strip() and parts[1].strip():
            return _StoredLicense("legacy", parts[0].strip(), activation_key=parts[1].strip())
        return None
    if not isinstance(value, dict) or value.get("format") != "nexojuris-license-backup/v2":
        return None
    license_format = value.get("license_format")
    machine_id = value.get("machine_id")
    if license_format == "legacy" and isinstance(machine_id, str) and isinstance(value.get("activation_key"), str):
        return _StoredLicense("legacy", machine_id, activation_key=value["activation_key"])
    document = value.get("license_document")
    if license_format == "act4" and isinstance(machine_id, str) and isinstance(document, str):
        return _StoredLicense("act4", machine_id, license_document=document.encode("utf-8"))
    return None


def _get_stored_record() -> _StoredLicense | None:
    """Recupera a licença atual do banco ou do backup versionado."""
    if _LICENSE_DB_PATH.resolve() == library_database_path().resolve():
        migrate_legacy_user_data()
    _init_license_table()

    try:
        with _db_conn() as conn:
            row = conn.execute(
                """
                SELECT machine_id, activation_key, license_format, license_document
                FROM system_license WHERE id = 1
                """
            ).fetchone()
            if row:
                license_format = str(row["license_format"] or "legacy")
                document = row["license_document"]
                if isinstance(document, str):
                    document = document.encode("utf-8")
                return _StoredLicense(
                    license_format=license_format,
                    machine_id=str(row["machine_id"]),
                    activation_key=str(row["activation_key"] or ""),
                    license_document=document if isinstance(document, bytes) else None,
                )
    except Exception as err:
        logger.debug(f"Falha ao ler licença do banco: {err}")

    try:
        if _LICENSE_BACKUP_PATH.is_file():
            content = _LICENSE_BACKUP_PATH.read_text(encoding="utf-8").strip()
            return _record_from_backup(content)
    except Exception as err:
        logger.debug(f"Falha ao ler licença de arquivo: {err}")

    return None


def _get_stored_license() -> tuple[str | None, str | None]:
    """Compatibilidade interna com o formato legado ``(machine_id, key)``."""
    record = _get_stored_record()
    if record is None or record.license_format != "legacy":
        return None, None
    return record.machine_id, record.activation_key


def _backup_mapping(record: _StoredLicense) -> dict[str, str]:
    value = {
        "format": "nexojuris-license-backup/v2",
        "license_format": record.license_format,
        "machine_id": record.machine_id,
    }
    if record.license_format == "legacy":
        value["activation_key"] = record.activation_key
    elif record.license_document is not None:
        value["license_document"] = record.license_document.decode("utf-8")
    return value


def _save_record(record: _StoredLicense) -> bool:
    """Persiste uma licença e seu backup de forma transacional e versionada."""
    _init_license_table()
    import time

    now = time.time()
    saved = False

    # 1. Salva no banco SQLite
    try:
        with _db_conn() as conn:
            conn.execute(
                """
                INSERT INTO system_license (
                    id, machine_id, activation_key, activated_at, license_format, license_document
                )
                VALUES (1, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    machine_id = excluded.machine_id,
                    activation_key = excluded.activation_key,
                    activated_at = excluded.activated_at,
                    license_format = excluded.license_format,
                    license_document = excluded.license_document
            """,
                (
                    record.machine_id,
                    record.activation_key,
                    now,
                    record.license_format,
                    record.license_document,
                ),
            )
            saved = True
    except Exception as err:
        logger.error(f"Erro ao salvar licença no banco: {err}")

    # 2. Salva no arquivo de contingência
    try:
        _LICENSE_BACKUP_PATH.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{_LICENSE_BACKUP_PATH.name}.", suffix=".tmp", dir=str(_LICENSE_BACKUP_PATH.parent)
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as temporary_file:
                json.dump(_backup_mapping(record), temporary_file, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                temporary_file.write("\n")
                temporary_file.flush()
                os.fsync(temporary_file.fileno())
            os.replace(temporary_name, _LICENSE_BACKUP_PATH)
        finally:
            Path(temporary_name).unlink(missing_ok=True)
        saved = True
    except Exception as err:
        logger.error(f"Erro ao salvar licença em arquivo: {err}")

    return saved


def _save_license(machine_id: str, activation_key: str) -> bool:
    return _save_record(_StoredLicense("legacy", machine_id, activation_key=activation_key))


def _save_act4_license(machine_id: str, document: bytes) -> bool:
    return _save_record(_StoredLicense("act4", machine_id, license_document=document))


def _act4_key_ring() -> PublicKeyRing:
    return PublicKeyRing(
        {key_id: base64.b64decode(value, validate=True) for key_id, value in _ACT4_PUBLIC_KEYS_B64.items()}
    )


def _temporal_guard() -> TemporalGuard:
    return TemporalGuard(_LICENSE_TIME_STATE_PATH)


def record_trusted_server_time(server_time: datetime, *, observed_at: datetime | None = None) -> None:
    """Recupera a âncora após uma resposta online cuja assinatura já foi validada."""
    instant = datetime.now(UTC) if observed_at is None else observed_at
    _temporal_guard().observe(instant, trusted_server_time=server_time)


def get_license_status(
    *,
    now: datetime | None = None,
    online_status: str = "active",
    offline_until: datetime | None = None,
) -> LicenseStatus:
    """Avalia centralmente licenças legadas e ACT4 sem efeitos protegidos."""
    v2_id = get_machine_fingerprint(2)
    v1_id = get_machine_fingerprint(1)
    record = _get_stored_record()

    if record is None:
        return unlicensed_status(v2_id)

    if record.license_format == "legacy":
        stored_mid, stored_key = record.machine_id, record.activation_key
        if verify_license_key(stored_mid, stored_key) and stored_mid in (v2_id, v1_id):
            return legacy_valid_status(v2_id)
        if verify_license_key(v2_id, stored_key) or verify_license_key(v1_id, stored_key):
            return legacy_valid_status(v2_id)
        return invalid_status(v2_id, "A licença legada armazenada é inválida para este computador.")

    if record.license_format != "act4" or record.license_document is None:
        return invalid_status(v2_id, "O formato da licença armazenada é inválido.")

    instant = datetime.now(UTC) if now is None else now
    try:
        payload = verify_license(record.license_document, _act4_key_ring(), check_time=False)
        if payload.machine_id != v2_id:
            return evaluate_act4(payload, v2_id, at=instant)
        assessment = _temporal_guard().observe(instant)
    except (LicenseCoreError, TemporalStateError, OSError, ValueError) as error:
        logger.warning("Falha ao validar licença ACT4: %s", error)
        return invalid_status(v2_id, "A licença ACT4 ou sua proteção temporal é inválida.")
    return evaluate_act4(
        payload,
        v2_id,
        at=assessment.effective_time,
        online_status=online_status,
        offline_until=offline_until,
        last_online_validation=assessment.last_trusted_server_at,
        clock_tampered=assessment.clock_tampered,
    )


def is_software_activated() -> tuple[bool, str]:
    """Wrapper legado preservado para consumidores que esperam ``(bool, machine_id)``."""
    status = get_license_status()
    return status.allows("converter"), status.machine_id


def require_license_feature(feature: str) -> LicenseStatus:
    """Autoriza uma feature ou falha com a exceção correspondente ao estado."""
    status = get_license_status()
    if status.state == LicenseState.UNLICENSED:
        raise LicenseRequiredError(status.machine_id)
    require_status_feature(status, feature)
    return status


def require_software_activation(feature: str = "converter") -> str:
    """Compatibilidade: exige a feature informada e retorna o Machine ID."""
    return require_license_feature(feature).machine_id


def activate_act4_license(document: bytes | str, *, now: datetime | None = None) -> dict[str, Any]:
    """Valida e armazena um documento ACT4 para a máquina atual."""
    machine_id = get_machine_fingerprint_v2()
    encoded = document.encode("utf-8") if isinstance(document, str) else document
    instant = datetime.now(UTC) if now is None else now
    try:
        payload = verify_license(
            encoded,
            _act4_key_ring(),
            check_time=False,
        )
        if payload.machine_id != machine_id:
            status = evaluate_act4(payload, machine_id, at=instant)
            return {"ok": False, "error": status.message, "machine_id": machine_id, "state": status.state.value}
        assessment = _temporal_guard().observe(instant)
        status = evaluate_act4(
            payload,
            machine_id,
            at=assessment.effective_time,
            clock_tampered=assessment.clock_tampered,
        )
        if status.state not in {LicenseState.VALID, LicenseState.EXPIRING, LicenseState.ONLINE_CHECK_REQUIRED}:
            return {"ok": False, "error": status.message, "machine_id": machine_id, "state": status.state.value}
    except (LicenseCoreError, TemporalStateError, OSError, TypeError, ValueError) as error:
        return {"ok": False, "error": str(error), "machine_id": machine_id, "state": LicenseState.INVALID.value}

    if not _save_act4_license(machine_id, encoded):
        return {
            "ok": False,
            "error": "Não foi possível gravar a licença ACT4 no disco.",
            "machine_id": machine_id,
            "state": LicenseState.INVALID.value,
        }
    return {
        "ok": True,
        "message": "Licença ACT4 importada com sucesso.",
        "machine_id": machine_id,
        "state": status.state.value,
        "license_id": payload.license_id,
    }


def activate_software(activation_key: str) -> dict[str, Any]:
    """Ativa um token ACT2/ACT3 ou importa um documento ACT4."""
    v2_id = get_machine_fingerprint_v2()
    v1_id = get_machine_fingerprint_v1()
    if isinstance(activation_key, str) and activation_key.lstrip().startswith("{"):
        return activate_act4_license(activation_key)
    clean_key = (activation_key or "").strip().upper()

    if not clean_key:
        return {
            "ok": False,
            "error": "A chave de ativação não pode estar vazia.",
            "machine_id": v2_id,
        }

    target_id = None
    if verify_license_key(v2_id, clean_key):
        target_id = v2_id
    elif verify_license_key(v1_id, clean_key):
        target_id = v1_id

    if target_id is None:
        return {
            "ok": False,
            "error": "Chave de ativação inválida para este computador. Verifique o código e tente novamente.",
            "machine_id": v2_id,
        }

    success = _save_license(target_id, clean_key)
    if not success:
        return {
            "ok": False,
            "error": "Não foi possível gravar a ativação no disco. Verifique as permissões de gravação.",
            "machine_id": v2_id,
        }

    return {
        "ok": True,
        "message": "NexoJuris ativado com sucesso! Acesso completo liberado.",
        "machine_id": v2_id,
    }


def deactivate_software() -> bool:
    """Remove a licença ativa (útil para testes e suporte administrativo)."""
    try:
        with _db_conn() as conn:
            conn.execute("DELETE FROM system_license WHERE id = 1")
    except Exception:
        pass

    try:
        if _LICENSE_BACKUP_PATH.is_file():
            _LICENSE_BACKUP_PATH.unlink(missing_ok=True)
    except Exception:
        pass

    return True
