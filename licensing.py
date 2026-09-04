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

from app_storage import (
    library_database_path,
    license_backup_path,
    license_time_state_path,
    migrate_legacy_user_data,
)
from license_core import (
    LicenseCoreError,
    LicenseState,
    LicenseStatus,
    PublicKeyRing,
    evaluate_act4,
    invalid_status,
    unlicensed_status,
    verify_license,
)
from license_core import (
    require_feature as require_status_feature,
)
from license_key_config import LICENSE_PUBLIC_KEYS_B64
from trusted_time import TemporalGuard, TemporalStateError

logger = logging.getLogger(__name__)

_LICENSE_DB_PATH = library_database_path()
_LICENSE_BACKUP_PATH = license_backup_path()
_LICENSE_TIME_STATE_PATH = license_time_state_path()
_ACT4_PUBLIC_KEYS_B64 = dict(LICENSE_PUBLIC_KEYS_B64)


class LicenseRequiredError(PermissionError):
    """Indica que uma operação protegida exige ativação válida nesta máquina."""

    def __init__(self, machine_id: str) -> None:
        self.machine_id = machine_id
        super().__init__(f"Ativação necessária para converter arquivos. Código da máquina: {machine_id}.")


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
    """Mantém somente o armazenamento local do documento ACT4 vigente."""
    try:
        with _db_conn() as conn:
            expected_columns = {"id", "machine_id", "license_id", "revision", "activated_at", "license_document"}
            existing_columns = {
                str(row[1]) for row in conn.execute("PRAGMA table_info(system_license)").fetchall()
            }
            if existing_columns and existing_columns != expected_columns:
                conn.execute("DROP TABLE IF EXISTS system_license_history")
                conn.execute("DROP TABLE system_license")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS system_license (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    machine_id TEXT NOT NULL,
                    license_id TEXT NOT NULL,
                    revision INTEGER NOT NULL CHECK (revision >= 1),
                    activated_at REAL NOT NULL,
                    license_document BLOB NOT NULL
                )
            """)
    except Exception as err:
        logger.debug(f"Erro ao inicializar tabela de licença: {err}")


@dataclass(frozen=True, slots=True)
class _StoredLicense:
    machine_id: str
    license_id: str
    revision: int
    license_document: bytes


def _record_from_backup(content: str) -> _StoredLicense | None:
    try:
        value = json.loads(content)
    except json.JSONDecodeError:
        return None
    if not isinstance(value, dict) or frozenset(value) != {
        "format",
        "machine_id",
        "license_id",
        "revision",
        "license_document",
    }:
        return None
    if value["format"] != "nexojuris-license-backup/v3":
        return None
    if (
        not isinstance(value["machine_id"], str)
        or not isinstance(value["license_id"], str)
        or isinstance(value["revision"], bool)
        or not isinstance(value["revision"], int)
        or value["revision"] < 1
        or not isinstance(value["license_document"], str)
    ):
        return None
    return _StoredLicense(
        value["machine_id"],
        value["license_id"],
        value["revision"],
        value["license_document"].encode("utf-8"),
    )


def _get_stored_record() -> _StoredLicense | None:
    """Recupera a licença atual do banco ou do backup versionado."""
    if _LICENSE_DB_PATH.resolve() == library_database_path().resolve():
        migrate_legacy_user_data()
    _init_license_table()

    try:
        with _db_conn() as conn:
            row = conn.execute(
                """
                SELECT machine_id, license_id, revision, license_document
                FROM system_license WHERE id = 1
                """
            ).fetchone()
            if row:
                document = row["license_document"]
                if isinstance(document, str):
                    document = document.encode("utf-8")
                if isinstance(document, bytes):
                    return _StoredLicense(
                        str(row["machine_id"]),
                        str(row["license_id"]),
                        int(row["revision"]),
                        document,
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


def _backup_mapping(record: _StoredLicense) -> dict[str, str | int]:
    return {
        "format": "nexojuris-license-backup/v3",
        "machine_id": record.machine_id,
        "license_id": record.license_id,
        "revision": record.revision,
        "license_document": record.license_document.decode("utf-8"),
    }


def _save_backup(record: _StoredLicense) -> bool:
    try:
        _LICENSE_BACKUP_PATH.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{_LICENSE_BACKUP_PATH.name}.", suffix=".tmp", dir=str(_LICENSE_BACKUP_PATH.parent)
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as temporary_file:
                json.dump(
                    _backup_mapping(record),
                    temporary_file,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                temporary_file.write("\n")
                temporary_file.flush()
                os.fsync(temporary_file.fileno())
            os.replace(temporary_name, _LICENSE_BACKUP_PATH)
        finally:
            Path(temporary_name).unlink(missing_ok=True)
        return True
    except Exception as err:
        logger.error(f"Erro ao salvar licença em arquivo: {err}")
        return False


def _save_record(record: _StoredLicense) -> bool:
    """Persiste a licença vigente no SQLite e no backup de contingência."""
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
                    id, machine_id, license_id, revision, activated_at, license_document
                )
                VALUES (1, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    machine_id = excluded.machine_id,
                    license_id = excluded.license_id,
                    revision = excluded.revision,
                    activated_at = excluded.activated_at,
                    license_document = excluded.license_document
            """,
                (
                    record.machine_id,
                    record.license_id,
                    record.revision,
                    now,
                    record.license_document,
                ),
            )
            saved = True
    except Exception as err:
        logger.error(f"Erro ao salvar licença no banco: {err}")

    # 2. Salva no arquivo de contingência
    saved = _save_backup(record) or saved

    return saved


def _save_act4_license(payload: Any, document: bytes) -> bool:
    return _save_record(_StoredLicense(payload.machine_id, payload.license_id, payload.revision, document))


def _act4_key_ring() -> PublicKeyRing:
    return PublicKeyRing(
        {key_id: base64.b64decode(value, validate=True) for key_id, value in _ACT4_PUBLIC_KEYS_B64.items()}
    )


def _temporal_guard() -> TemporalGuard:
    return TemporalGuard(_LICENSE_TIME_STATE_PATH)


def get_license_status(*, now: datetime | None = None) -> LicenseStatus:
    """Avalia localmente a licença ACT4 vigente."""
    v2_id = get_machine_fingerprint(2)
    record = _get_stored_record()

    if record is None:
        return unlicensed_status(v2_id)

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
        if status.state not in {LicenseState.VALID, LicenseState.EXPIRING}:
            return {"ok": False, "error": status.message, "machine_id": machine_id, "state": status.state.value}
        current = _get_stored_record()
        if current is not None and current.license_id == payload.license_id:
            if payload.revision < current.revision:
                return {
                    "ok": False,
                    "error": "A revisão da licença é anterior à já instalada.",
                    "machine_id": machine_id,
                    "state": LicenseState.INVALID.value,
                }
            if payload.revision == current.revision and encoded != current.license_document:
                return {
                    "ok": False,
                    "error": "A revisão da licença conflita com o documento já instalado.",
                    "machine_id": machine_id,
                    "state": LicenseState.INVALID.value,
                }
    except (LicenseCoreError, TemporalStateError, OSError, TypeError, ValueError) as error:
        return {"ok": False, "error": str(error), "machine_id": machine_id, "state": LicenseState.INVALID.value}

    if not _save_act4_license(payload, encoded):
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
        "revision": payload.revision,
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
