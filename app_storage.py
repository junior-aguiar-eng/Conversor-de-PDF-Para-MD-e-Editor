"""Caminhos persistentes e migração não destrutiva de instalações legadas."""

from __future__ import annotations

import logging
import os
import shutil
import sqlite3
import uuid
from contextlib import closing
from pathlib import Path

from constants import application_root, user_data_root

logger = logging.getLogger(__name__)


def data_directory() -> Path:
    return user_data_root() / "data"


def library_database_path() -> Path:
    return data_directory() / "nexojuris_acervo.db"


def license_backup_path() -> Path:
    return data_directory() / "license.sig"


def license_time_state_path() -> Path:
    return data_directory() / "license-time.dat"


def license_lease_path() -> Path:
    return data_directory() / "license-lease.json"


def legacy_data_directory() -> Path:
    return application_root() / "data"


def _atomic_file_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
    try:
        shutil.copy2(source, temporary)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_sqlite_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
    try:
        with closing(sqlite3.connect(str(source), timeout=5.0)) as source_conn:
            result = source_conn.execute("PRAGMA integrity_check").fetchone()
            if result is None or str(result[0]).lower() != "ok":
                raise sqlite3.DatabaseError("o banco legado falhou no PRAGMA integrity_check")
            with closing(sqlite3.connect(str(temporary), timeout=5.0)) as target_conn:
                source_conn.backup(target_conn)
                target_conn.commit()
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def migrate_legacy_user_data() -> None:
    """Copia dados legados ao perfil do usuário sem remover a origem."""
    destination_dir = data_directory()
    legacy_dir = legacy_data_directory()
    if destination_dir.resolve() == legacy_dir.resolve() or not legacy_dir.is_dir():
        return
    destination_dir.mkdir(parents=True, exist_ok=True)

    legacy_db = legacy_dir / "nexojuris_acervo.db"
    current_db = library_database_path()
    if legacy_db.is_file() and not current_db.exists():
        try:
            _atomic_sqlite_copy(legacy_db, current_db)
        except (OSError, sqlite3.Error) as error:
            logger.warning("Não foi possível migrar o banco legado: %s", error)

    for file_name, destination in (
        ("license.sig", license_backup_path()),
        ("conversion-journal.json", destination_dir / "conversion-journal.json"),
    ):
        legacy_file = legacy_dir / file_name
        if not legacy_file.is_file() or destination.exists():
            continue
        try:
            _atomic_file_copy(legacy_file, destination)
        except OSError as error:
            logger.warning("Não foi possível migrar %s: %s", file_name, error)
