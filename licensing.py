"""Módulo de Licenciamento por Hardware (Hardware Node-Locking).

Gera identificadores de máquina estáveis e valida licenças offline assinadas com
Ed25519. O cliente contém somente a chave pública; a chave privada fica restrita
ao utilitário administrativo.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os
import platform
import re
import sqlite3
import subprocess
import uuid
from collections.abc import Generator
from contextlib import contextmanager
from functools import lru_cache
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from constants import application_root

logger = logging.getLogger(__name__)

_LICENSE_DB_PATH = application_root() / "data" / "nexojuris_acervo.db"
_LICENSE_BACKUP_PATH = application_root() / "data" / "license.sig"
_LICENSE_KEY_PREFIX = "ACT2-01-"
_LICENSE_KEY_PREFIX_V3 = "ACT3-01-"
_LICENSE_PAYLOAD_PREFIX = b"nexojuris-license:v2:"
_LICENSE_PAYLOAD_PREFIX_V3 = b"nexojuris-license:v3:"
_LICENSE_PUBLIC_KEY_B64 = "80WGyZ+9TwHmcKDPpjOncNZVVYgFHgNBl59aBK5Hpug="
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
    _LICENSE_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_LICENSE_DB_PATH), timeout=5.0)
    conn.row_factory = sqlite3.Row
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def _init_license_table() -> None:
    """Garante a existência da tabela de licença no banco SQLite."""
    try:
        with _db_conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS system_license (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    machine_id TEXT NOT NULL,
                    activation_key TEXT NOT NULL,
                    activated_at REAL NOT NULL
                )
            """)
    except Exception as err:
        logger.debug(f"Erro ao inicializar tabela de licença: {err}")


def _get_stored_license() -> tuple[str | None, str | None]:
    """Recupera (machine_id, activation_key) armazenados no banco ou no arquivo de backup."""
    _init_license_table()

    # 1. Tenta recuperar do SQLite
    try:
        with _db_conn() as conn:
            row = conn.execute("SELECT machine_id, activation_key FROM system_license WHERE id = 1").fetchone()
            if row:
                return row["machine_id"], row["activation_key"]
    except Exception as err:
        logger.debug(f"Falha ao ler licença do banco: {err}")

    # 2. Fallback: arquivo license.sig
    try:
        if _LICENSE_BACKUP_PATH.is_file():
            content = _LICENSE_BACKUP_PATH.read_text(encoding="utf-8").strip()
            parts = content.split(":")
            if len(parts) == 2:
                return parts[0].strip(), parts[1].strip()
    except Exception as err:
        logger.debug(f"Falha ao ler licença de arquivo: {err}")

    return None, None


def _save_license(machine_id: str, activation_key: str) -> bool:
    """Salva a licença no banco SQLite e no arquivo de contingência."""
    _init_license_table()
    import time

    now = time.time()
    saved = False

    # 1. Salva no banco SQLite
    try:
        with _db_conn() as conn:
            conn.execute(
                """
                INSERT INTO system_license (id, machine_id, activation_key, activated_at)
                VALUES (1, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    machine_id = excluded.machine_id,
                    activation_key = excluded.activation_key,
                    activated_at = excluded.activated_at
            """,
                (machine_id, activation_key, now),
            )
            saved = True
    except Exception as err:
        logger.error(f"Erro ao salvar licença no banco: {err}")

    # 2. Salva no arquivo de contingência
    try:
        _LICENSE_BACKUP_PATH.parent.mkdir(parents=True, exist_ok=True)
        _LICENSE_BACKUP_PATH.write_text(f"{machine_id}:{activation_key}", encoding="utf-8")
        saved = True
    except Exception as err:
        logger.error(f"Erro ao salvar licença em arquivo: {err}")

    return saved


def is_software_activated() -> tuple[bool, str]:
    """Verifica se a instalação atual do NexoJuris está licenciada para este hardware.
    Suporta licenças v1 e v2 sem invalidar ativações legítimas existentes (Item 17).
    """
    v2_id = get_machine_fingerprint(2)
    v1_id = get_machine_fingerprint(1)
    stored_mid, stored_key = _get_stored_license()

    if not stored_mid or not stored_key:
        return False, v2_id

    # 1. Valida com a licença gravada
    if verify_license_key(stored_mid, stored_key):
        if stored_mid in (v2_id, v1_id):
            return True, v2_id

    # 2. Testar fallback contra v2_id e v1_id
    if verify_license_key(v2_id, stored_key):
        return True, v2_id
    if verify_license_key(v1_id, stored_key):
        return True, v2_id

    return False, v2_id


def require_software_activation() -> str:
    """Autoriza operações protegidas ou falha antes de qualquer conversão."""
    is_activated, machine_id = is_software_activated()
    if not is_activated:
        raise LicenseRequiredError(machine_id)
    return machine_id


def activate_software(activation_key: str) -> dict[str, Any]:
    """Valida a chave de ativação para a máquina atual (v2 ou v1) e armazena permanentemente."""
    v2_id = get_machine_fingerprint_v2()
    v1_id = get_machine_fingerprint_v1()
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
