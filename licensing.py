"""Módulo de Licenciamento por Hardware (Hardware Node-Locking).

Gera identificadores de máquina estáveis e realiza validação offline criptográfica
utilizando assinaturas HMAC-SHA256 para impedir o uso não autorizado da aplicação.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import platform
import sqlite3
import subprocess
import uuid
from collections.abc import Generator
from contextlib import contextmanager
from typing import Any

from constants import application_root

logger = logging.getLogger(__name__)

_LICENSE_DB_PATH = application_root() / "data" / "nexojuris_acervo.db"
_LICENSE_BACKUP_PATH = application_root() / "data" / "license.sig"


def _get_signing_key() -> bytes:
    """Retorna a chave mestra criptográfica para assinatura de licenças offline."""
    env_key = os.environ.get("NEXOJURIS_LICENSE_SECRET")
    if env_key:
        return env_key.encode("utf-8")

    # Componentes seguros ofuscados para compor a chave mestra padrão
    part_a = b"NXJ_SEC_2026"
    part_b = b"CORE_NODE_LOCK"
    part_c = b"LEGAL_TECH_MASTER"
    part_d = b"SHA256_OFFLINE"
    return hashlib.sha256(b":".join([part_a, part_b, part_c, part_d])).digest()


def _get_motherboard_uuid() -> str:
    """Obtém o UUID único da placa-mãe / sistema via PowerShell, WMIC ou fallback."""
    if platform.system() == "Windows":
        # 1. PowerShell CIM Instance (Recomendado para Windows 10/11)
        try:
            cmd = [
                "powershell",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                "(Get-CimInstance Win32_ComputerSystemProduct).UUID",
            ]
            out = subprocess.check_output(cmd, text=True, stderr=subprocess.DEVNULL, timeout=2.5)
            clean_uuid = out.strip().upper()
            if clean_uuid and len(clean_uuid) > 10 and clean_uuid != "FFFFFFFF-FFFF-FFFF-FFFF-FFFFFFFFFFFF":
                return clean_uuid
        except Exception as err:
            logger.debug(f"PowerShell UUID fallback: {err}")

        # 2. WMIC Legacy
        try:
            cmd = ["wmic", "csproduct", "get", "uuid"]
            out = subprocess.check_output(cmd, text=True, stderr=subprocess.DEVNULL, timeout=2.0)
            lines = [line.strip().upper() for line in out.splitlines() if line.strip() and "UUID" not in line.upper()]
            if lines and lines[0] and len(lines[0]) > 10:
                return lines[0]
        except Exception as err:
            logger.debug(f"WMIC UUID fallback: {err}")

        # 3. Windows Registry Cryptography MachineGuid
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography") as key:
                guid, _ = winreg.QueryValueEx(key, "MachineGuid")
                if guid:
                    return str(guid).strip().upper()
        except Exception as err:
            logger.debug(f"Winreg MachineGuid fallback: {err}")

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


def get_machine_fingerprint() -> str:
    """Calcula e retorna a impressão digital (Machine ID) formatada como NXJ-XXXX-XXXX-XXXX-XXXX."""
    mb_uuid = _get_motherboard_uuid()
    mac_addr = _get_primary_mac_address()
    processor = platform.processor() or os.environ.get("PROCESSOR_IDENTIFIER", "CPU")
    comp_name = os.environ.get("COMPUTERNAME", platform.node())

    raw_seed = f"UUID:{mb_uuid}|MAC:{mac_addr}|CPU:{processor}|HOST:{comp_name}"
    digest = hashlib.sha256(raw_seed.encode("utf-8")).hexdigest().upper()

    # Formata os primeiros 16 caracteres em 4 blocos de 4 caracteres
    chunk1 = digest[0:4]
    chunk2 = digest[4:8]
    chunk3 = digest[8:12]
    chunk4 = digest[12:16]

    return f"NXJ-{chunk1}-{chunk2}-{chunk3}-{chunk4}"


def generate_activation_key(machine_id: str, master_key: bytes | None = None) -> str:
    """Gera a chave de ativação assinada com HMAC-SHA256 formatada como ACT-XXXX-XXXX-XXXX-XXXX."""
    key = master_key if master_key is not None else _get_signing_key()
    normalized_id = machine_id.strip().upper()
    h = hmac.new(key, normalized_id.encode("utf-8"), hashlib.sha256)
    digest = h.hexdigest().upper()

    chunk1 = digest[0:4]
    chunk2 = digest[4:8]
    chunk3 = digest[8:12]
    chunk4 = digest[12:16]

    return f"ACT-{chunk1}-{chunk2}-{chunk3}-{chunk4}"


def verify_license_key(machine_id: str, key: str, master_key: bytes | None = None) -> bool:
    """Valida se uma chave informada corresponde ao Machine ID via comparação em tempo constante."""
    if not machine_id or not key:
        return False

    expected_key = generate_activation_key(machine_id, master_key=master_key)
    candidate_key = key.strip().upper()

    return hmac.compare_digest(expected_key, candidate_key)


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
    """Verifica se a instalação atual do NexoJuris está licenciada para este hardware."""
    current_machine_id = get_machine_fingerprint()
    stored_mid, stored_key = _get_stored_license()

    if not stored_mid or not stored_key:
        return False, current_machine_id

    # O machine_id gravado deve ser idêntico ao da máquina atual
    if stored_mid != current_machine_id:
        return False, current_machine_id

    if verify_license_key(current_machine_id, stored_key):
        return True, current_machine_id

    return False, current_machine_id


def activate_software(activation_key: str) -> dict[str, Any]:
    """Valida a chave de ativação para a máquina atual e armazena permanentemente."""
    machine_id = get_machine_fingerprint()
    clean_key = (activation_key or "").strip().upper()

    if not clean_key:
        return {
            "ok": False,
            "error": "A chave de ativação não pode estar vazia.",
            "machine_id": machine_id,
        }

    if not verify_license_key(machine_id, clean_key):
        return {
            "ok": False,
            "error": "Chave de ativação inválida para este computador. Verifique o código e tente novamente.",
            "machine_id": machine_id,
        }

    success = _save_license(machine_id, clean_key)
    if not success:
        return {
            "ok": False,
            "error": "Não foi possível gravar a ativação no disco. Verifique as permissões de gravação.",
            "machine_id": machine_id,
        }

    return {
        "ok": True,
        "message": "NexoJuris ativado com sucesso! Acesso completo liberado.",
        "machine_id": machine_id,
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
