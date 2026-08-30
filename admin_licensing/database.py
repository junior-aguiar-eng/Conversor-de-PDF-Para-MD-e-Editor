"""Persistência SQLite, auditoria imutável e backups do núcleo administrativo."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
import uuid
from collections.abc import Generator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

_SCHEMA_VERSION = 5
_BACKUP_MAGIC = b"NXJ-ADMIN-BACKUP\x01"
_BACKUP_SALT_BYTES = 16
_BACKUP_NONCE_BYTES = 12
_MIN_BACKUP_PASSWORD_CHARS = 12
_REQUIRED_TABLES = frozenset(
    {
        "customers",
        "licenses",
        "licensed_devices",
        "license_features",
        "license_renewals",
        "license_status_changes",
        "offline_exports",
        "online_leases",
        "legacy_license_migrations",
        "audit_events",
        "admin_users",
    }
)


class BackupError(RuntimeError):
    pass


class ConfirmationRequiredError(PermissionError):
    pass


def utc_now_text() -> str:
    return datetime.now(UTC).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def canonical_json(value: Mapping[str, Any] | None) -> str:
    return json.dumps(value or {}, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))


def _derive_backup_key(password: str, salt: bytes) -> bytes:
    if not isinstance(password, str) or len(password) < _MIN_BACKUP_PASSWORD_CHARS:
        raise BackupError(f"A senha do backup deve possuir ao menos {_MIN_BACKUP_PASSWORD_CHARS} caracteres.")
    return Scrypt(salt=salt, length=32, n=2**15, r=8, p=1).derive(password.encode("utf-8"))


class AdminDatabase:
    """Abre conexões curtas e transações ``BEGIN IMMEDIATE`` para concorrência previsível."""

    def __init__(
        self,
        path: str | Path,
        *,
        busy_timeout_ms: int = 10_000,
        environment: str | None = None,
    ) -> None:
        self.path = Path(path).expanduser().resolve()
        self.busy_timeout_ms = busy_timeout_ms
        if environment not in {None, "test", "production"}:
            raise ValueError("O ambiente administrativo deve ser test ou production.")
        self.environment = environment
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=self.busy_timeout_ms / 1000, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(f"PRAGMA busy_timeout = {self.busy_timeout_ms}")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = FULL")
        return connection

    @contextmanager
    def transaction(self) -> Generator[sqlite3.Connection]:
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @contextmanager
    def read(self) -> Generator[sqlite3.Connection]:
        connection = self.connect()
        try:
            yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self.transaction() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS admin_users (
                    admin_user_id TEXT PRIMARY KEY,
                    username TEXT NOT NULL UNIQUE,
                    display_name TEXT NOT NULL,
                    role TEXT NOT NULL CHECK (role IN ('owner', 'administrator', 'operator', 'auditor')),
                    password_hash TEXT,
                    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS operational_settings (
                    setting_key TEXT PRIMARY KEY,
                    setting_value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS customers (
                    customer_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    normalized_name TEXT NOT NULL,
                    email TEXT,
                    phone TEXT,
                    tax_id TEXT,
                    commercial_reference TEXT,
                    created_at TEXT NOT NULL,
                    created_by TEXT REFERENCES admin_users(admin_user_id)
                );
                CREATE INDEX IF NOT EXISTS idx_customers_normalized_name ON customers(normalized_name);
                CREATE INDEX IF NOT EXISTS idx_customers_email ON customers(email);
                CREATE INDEX IF NOT EXISTS idx_customers_phone ON customers(phone);
                CREATE INDEX IF NOT EXISTS idx_customers_tax_id ON customers(tax_id);

                CREATE TABLE IF NOT EXISTS licenses (
                    license_id TEXT PRIMARY KEY,
                    customer_id TEXT NOT NULL REFERENCES customers(customer_id),
                    key_id TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ('active', 'suspended', 'revoked')),
                    issued_at TEXT NOT NULL,
                    not_before TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    term_months INTEGER NOT NULL CHECK (term_months IN (3, 6, 12)),
                    validation_mode TEXT NOT NULL CHECK (validation_mode IN ('offline', 'hybrid')),
                    max_offline_days INTEGER NOT NULL CHECK (max_offline_days BETWEEN 0 AND 30),
                    customer_reference TEXT NOT NULL UNIQUE,
                    commercial_reference TEXT,
                    activation_secret_hash TEXT,
                    created_by TEXT REFERENCES admin_users(admin_user_id)
                );
                CREATE INDEX IF NOT EXISTS idx_licenses_customer ON licenses(customer_id);
                CREATE INDEX IF NOT EXISTS idx_licenses_status_expires ON licenses(status, expires_at);

                CREATE TABLE IF NOT EXISTS licensed_devices (
                    device_id TEXT PRIMARY KEY,
                    license_id TEXT NOT NULL REFERENCES licenses(license_id),
                    machine_id TEXT NOT NULL,
                    bound_at TEXT NOT NULL,
                    unbound_at TEXT,
                    active INTEGER NOT NULL CHECK (active IN (0, 1)),
                    created_by TEXT REFERENCES admin_users(admin_user_id)
                );
                CREATE UNIQUE INDEX IF NOT EXISTS idx_one_active_device_per_license
                    ON licensed_devices(license_id) WHERE active = 1;
                CREATE INDEX IF NOT EXISTS idx_devices_machine ON licensed_devices(machine_id);

                CREATE TABLE IF NOT EXISTS license_features (
                    license_id TEXT NOT NULL REFERENCES licenses(license_id),
                    feature TEXT NOT NULL CHECK (feature IN ('converter', 'ocr', 'reader')),
                    PRIMARY KEY (license_id, feature)
                );

                CREATE TABLE IF NOT EXISTS license_renewals (
                    renewal_id TEXT PRIMARY KEY,
                    license_id TEXT NOT NULL REFERENCES licenses(license_id),
                    previous_expires_at TEXT NOT NULL,
                    new_expires_at TEXT NOT NULL,
                    term_months INTEGER NOT NULL CHECK (term_months IN (3, 6, 12)),
                    renewed_at TEXT NOT NULL,
                    admin_user_id TEXT REFERENCES admin_users(admin_user_id)
                );

                CREATE TABLE IF NOT EXISTS license_status_changes (
                    change_id TEXT PRIMARY KEY,
                    license_id TEXT NOT NULL REFERENCES licenses(license_id),
                    previous_status TEXT NOT NULL,
                    new_status TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    changed_at TEXT NOT NULL,
                    admin_user_id TEXT REFERENCES admin_users(admin_user_id)
                );

                CREATE TABLE IF NOT EXISTS offline_exports (
                    export_id TEXT PRIMARY KEY,
                    license_id TEXT NOT NULL REFERENCES licenses(license_id),
                    device_id TEXT NOT NULL REFERENCES licensed_devices(device_id),
                    document_sha256 TEXT NOT NULL,
                    file_name TEXT NOT NULL,
                    exported_at TEXT NOT NULL,
                    admin_user_id TEXT REFERENCES admin_users(admin_user_id)
                );

                CREATE TABLE IF NOT EXISTS online_leases (
                    lease_id TEXT PRIMARY KEY,
                    license_id TEXT NOT NULL REFERENCES licenses(license_id),
                    device_id TEXT NOT NULL REFERENCES licensed_devices(device_id),
                    issued_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    last_seen_at TEXT,
                    status TEXT NOT NULL CHECK (status IN ('active', 'expired', 'revoked'))
                );
                CREATE INDEX IF NOT EXISTS idx_online_leases_license ON online_leases(license_id, expires_at);

                CREATE TABLE IF NOT EXISTS legacy_license_migrations (
                    migration_id TEXT PRIMARY KEY,
                    legacy_version TEXT NOT NULL CHECK (legacy_version IN ('ACT2', 'ACT3')),
                    legacy_key_hash TEXT NOT NULL UNIQUE,
                    machine_id TEXT NOT NULL,
                    customer_id TEXT NOT NULL REFERENCES customers(customer_id),
                    license_id TEXT NOT NULL REFERENCES licenses(license_id),
                    acknowledged_at TEXT NOT NULL,
                    admin_user_id TEXT REFERENCES admin_users(admin_user_id)
                );
                CREATE INDEX IF NOT EXISTS idx_legacy_migrations_machine
                    ON legacy_license_migrations(machine_id, acknowledged_at);

                CREATE TABLE IF NOT EXISTS request_nonces (
                    nonce_hash TEXT PRIMARY KEY,
                    scope TEXT NOT NULL,
                    seen_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_request_nonces_expiry ON request_nonces(expires_at);

                CREATE TABLE IF NOT EXISTS admin_api_tokens (
                    token_id TEXT PRIMARY KEY,
                    token_hash TEXT NOT NULL UNIQUE,
                    label TEXT NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
                    requires_totp INTEGER NOT NULL DEFAULT 0 CHECK (requires_totp IN (0, 1)),
                    created_at TEXT NOT NULL,
                    last_used_at TEXT,
                    revoked_at TEXT,
                    admin_user_id TEXT REFERENCES admin_users(admin_user_id)
                );

                CREATE TABLE IF NOT EXISTS audit_events (
                    event_id TEXT PRIMARY KEY,
                    action TEXT NOT NULL,
                    entity_type TEXT NOT NULL,
                    entity_id TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    admin_user_id TEXT REFERENCES admin_users(admin_user_id),
                    details_json TEXT NOT NULL,
                    previous_hash TEXT NOT NULL,
                    event_hash TEXT NOT NULL UNIQUE
                );
                CREATE INDEX IF NOT EXISTS idx_audit_entity ON audit_events(entity_type, entity_id, occurred_at);

                CREATE VIRTUAL TABLE IF NOT EXISTS license_search USING fts5(
                    license_id UNINDEXED,
                    content,
                    tokenize = 'unicode61 remove_diacritics 2'
                );

                CREATE TABLE IF NOT EXISTS admin_schema (
                    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                    version INTEGER NOT NULL
                );
                INSERT INTO admin_schema(singleton, version) VALUES (1, 5)
                    ON CONFLICT(singleton) DO UPDATE SET version = MAX(version, excluded.version);

                CREATE TRIGGER IF NOT EXISTS immutable_customer_id
                BEFORE UPDATE OF customer_id ON customers BEGIN SELECT RAISE(ABORT, 'customer_id is immutable'); END;
                CREATE TRIGGER IF NOT EXISTS immutable_license_id
                BEFORE UPDATE OF license_id ON licenses BEGIN SELECT RAISE(ABORT, 'license_id is immutable'); END;
                CREATE TRIGGER IF NOT EXISTS immutable_device_id
                BEFORE UPDATE OF device_id ON licensed_devices BEGIN SELECT RAISE(ABORT, 'device_id is immutable'); END;
                CREATE TRIGGER IF NOT EXISTS audit_events_no_update
                BEFORE UPDATE ON audit_events BEGIN SELECT RAISE(ABORT, 'audit_events is append-only'); END;
                CREATE TRIGGER IF NOT EXISTS audit_events_no_delete
                BEFORE DELETE ON audit_events BEGIN SELECT RAISE(ABORT, 'audit_events is append-only'); END;
                CREATE TRIGGER IF NOT EXISTS status_changes_no_update
                BEFORE UPDATE ON license_status_changes BEGIN SELECT RAISE(ABORT, 'license_status_changes is append-only'); END;
                CREATE TRIGGER IF NOT EXISTS status_changes_no_delete
                BEFORE DELETE ON license_status_changes BEGIN SELECT RAISE(ABORT, 'license_status_changes is append-only'); END;
                CREATE TRIGGER IF NOT EXISTS renewals_no_update
                BEFORE UPDATE ON license_renewals BEGIN SELECT RAISE(ABORT, 'license_renewals is append-only'); END;
                CREATE TRIGGER IF NOT EXISTS renewals_no_delete
                BEFORE DELETE ON license_renewals BEGIN SELECT RAISE(ABORT, 'license_renewals is append-only'); END;
                CREATE TRIGGER IF NOT EXISTS exports_no_update
                BEFORE UPDATE ON offline_exports BEGIN SELECT RAISE(ABORT, 'offline_exports is append-only'); END;
                CREATE TRIGGER IF NOT EXISTS exports_no_delete
                BEFORE DELETE ON offline_exports BEGIN SELECT RAISE(ABORT, 'offline_exports is append-only'); END;
                CREATE TRIGGER IF NOT EXISTS legacy_migrations_no_update
                BEFORE UPDATE ON legacy_license_migrations BEGIN SELECT RAISE(ABORT, 'legacy migrations are append-only'); END;
                CREATE TRIGGER IF NOT EXISTS legacy_migrations_no_delete
                BEFORE DELETE ON legacy_license_migrations BEGIN SELECT RAISE(ABORT, 'legacy migrations are append-only'); END;
                """
            )
            if self.environment is not None:
                bound_environment = connection.execute(
                    "SELECT setting_value FROM operational_settings WHERE setting_key = 'environment'"
                ).fetchone()
                if bound_environment and bound_environment[0] != self.environment:
                    raise RuntimeError(
                        f"O banco administrativo pertence ao ambiente {bound_environment[0]}, não a {self.environment}."
                    )
                connection.execute(
                    "INSERT OR IGNORE INTO operational_settings(setting_key, setting_value) VALUES ('environment', ?)",
                    (self.environment,),
                )
            license_columns = {
                str(row[1]) for row in connection.execute("PRAGMA table_info(licenses)").fetchall()
            }
            if "activation_secret_hash" not in license_columns:
                connection.execute("ALTER TABLE licenses ADD COLUMN activation_secret_hash TEXT")
            lease_columns = {
                str(row[1]) for row in connection.execute("PRAGMA table_info(online_leases)").fetchall()
            }
            for column, declaration in (
                ("nonce_hash", "TEXT"),
                ("key_id", "TEXT"),
                ("lease_document", "BLOB"),
            ):
                if column not in lease_columns:
                    connection.execute(f"ALTER TABLE online_leases ADD COLUMN {column} {declaration}")
            token_columns = {
                str(row[1]) for row in connection.execute("PRAGMA table_info(admin_api_tokens)").fetchall()
            }
            for column, declaration in (
                ("requires_totp", "INTEGER NOT NULL DEFAULT 0 CHECK (requires_totp IN (0, 1))"),
                ("revoked_at", "TEXT"),
            ):
                if column not in token_columns:
                    connection.execute(f"ALTER TABLE admin_api_tokens ADD COLUMN {column} {declaration}")
            connection.execute(
                """
                INSERT INTO license_search(license_id, content)
                SELECT l.license_id,
                       trim(l.license_id || ' ' || c.normalized_name || ' ' || c.name || ' ' ||
                            coalesce(c.email, '') || ' ' || coalesce(c.phone, '') || ' ' ||
                            coalesce(c.tax_id, '') || ' ' || coalesce(d.machine_id, '') || ' ' ||
                            coalesce(l.commercial_reference, '') || ' ' ||
                            coalesce(c.commercial_reference, '') || ' ' || l.status || ' ' || l.expires_at)
                FROM licenses l
                JOIN customers c ON c.customer_id = l.customer_id
                LEFT JOIN licensed_devices d ON d.license_id = l.license_id AND d.active = 1
                WHERE NOT EXISTS (
                    SELECT 1 FROM license_search search WHERE search.license_id = l.license_id
                )
                """
            )

    def append_audit(
        self,
        connection: sqlite3.Connection,
        *,
        event_id: str,
        action: str,
        entity_type: str,
        entity_id: str,
        admin_user_id: str | None,
        details: Mapping[str, Any] | None = None,
        occurred_at: str | None = None,
    ) -> str:
        timestamp = occurred_at or utc_now_text()
        details_json = canonical_json(details)
        previous = connection.execute("SELECT event_hash FROM audit_events ORDER BY rowid DESC LIMIT 1").fetchone()
        previous_hash = str(previous[0]) if previous else "0" * 64
        material = "\x1f".join(
            (previous_hash, event_id, action, entity_type, entity_id, timestamp, admin_user_id or "", details_json)
        ).encode("utf-8")
        event_hash = hashlib.sha256(material).hexdigest()
        connection.execute(
            """
            INSERT INTO audit_events(
                event_id, action, entity_type, entity_id, occurred_at,
                admin_user_id, details_json, previous_hash, event_hash
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                action,
                entity_type,
                entity_id,
                timestamp,
                admin_user_id,
                details_json,
                previous_hash,
                event_hash,
            ),
        )
        return event_hash

    def verify_audit_chain(self) -> bool:
        with self.read() as connection:
            rows = connection.execute("SELECT * FROM audit_events ORDER BY rowid").fetchall()
        previous_hash = "0" * 64
        for row in rows:
            if row["previous_hash"] != previous_hash:
                return False
            material = "\x1f".join(
                (
                    previous_hash,
                    row["event_id"],
                    row["action"],
                    row["entity_type"],
                    row["entity_id"],
                    row["occurred_at"],
                    row["admin_user_id"] or "",
                    row["details_json"],
                )
            ).encode("utf-8")
            if hashlib.sha256(material).hexdigest() != row["event_hash"]:
                return False
            previous_hash = row["event_hash"]
        return True

    def create_encrypted_backup(
        self, destination: str | Path, password: str, *, admin_user_id: str | None = None
    ) -> Path:
        target = Path(destination).expanduser().resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        with self.read() as connection:
            connection.execute("PRAGMA wal_checkpoint(PASSIVE)")
            database_bytes = connection.serialize()
        salt = os.urandom(_BACKUP_SALT_BYTES)
        nonce = os.urandom(_BACKUP_NONCE_BYTES)
        key = _derive_backup_key(password, salt)
        ciphertext = AESGCM(key).encrypt(nonce, database_bytes, _BACKUP_MAGIC)
        encoded = _BACKUP_MAGIC + salt + nonce + ciphertext
        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
        try:
            with os.fdopen(descriptor, "wb") as temporary:
                temporary.write(encoded)
                temporary.flush()
                os.fsync(temporary.fileno())
            os.replace(temporary_name, target)
        finally:
            Path(temporary_name).unlink(missing_ok=True)
        with self.transaction() as connection:
            self.append_audit(
                connection,
                event_id=f"EVT-{uuid.uuid4().hex.upper()}",
                action="database.backup_created",
                entity_type="database",
                entity_id=self.path.name,
                admin_user_id=admin_user_id,
                details={"file_name": target.name, "sha256": hashlib.sha256(encoded).hexdigest()},
            )
        return target

    @staticmethod
    def _decrypt_backup(source: Path, password: str) -> bytes:
        try:
            encoded = source.read_bytes()
        except OSError as error:
            raise BackupError("Não foi possível ler o backup administrativo.") from error
        minimum = len(_BACKUP_MAGIC) + _BACKUP_SALT_BYTES + _BACKUP_NONCE_BYTES + 16
        if len(encoded) < minimum or not encoded.startswith(_BACKUP_MAGIC):
            raise BackupError("Formato de backup administrativo inválido.")
        offset = len(_BACKUP_MAGIC)
        salt = encoded[offset : offset + _BACKUP_SALT_BYTES]
        offset += _BACKUP_SALT_BYTES
        nonce = encoded[offset : offset + _BACKUP_NONCE_BYTES]
        ciphertext = encoded[offset + _BACKUP_NONCE_BYTES :]
        try:
            return AESGCM(_derive_backup_key(password, salt)).decrypt(nonce, ciphertext, _BACKUP_MAGIC)
        except InvalidTag as error:
            raise BackupError("Senha incorreta ou backup administrativo adulterado.") from error

    @staticmethod
    def _validate_database_file(path: Path) -> None:
        try:
            connection = sqlite3.connect(path)
            integrity = connection.execute("PRAGMA integrity_check").fetchone()
            tables = {
                str(row[0])
                for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
            }
            version = connection.execute("SELECT version FROM admin_schema WHERE singleton = 1").fetchone()
        except sqlite3.DatabaseError as error:
            raise BackupError("O backup não contém um banco administrativo válido.") from error
        finally:
            if "connection" in locals():
                connection.close()
        if not integrity or integrity[0] != "ok" or not _REQUIRED_TABLES.issubset(tables):
            raise BackupError("A integridade ou o esquema do backup administrativo é inválido.")
        if not version or int(version[0]) > _SCHEMA_VERSION:
            raise BackupError("A versão do backup não é suportada por este aplicativo.")

    def restore_encrypted_backup(
        self,
        source: str | Path,
        password: str,
        *,
        confirmation: str,
        admin_user_id: str | None = None,
    ) -> None:
        backup_path = Path(source).expanduser().resolve()
        expected = f"RESTAURAR:{self.path.name}"
        if confirmation != expected:
            raise ConfirmationRequiredError(f"Confirmação obrigatória: {expected}")
        database_bytes = self._decrypt_backup(backup_path, password)
        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{self.path.name}.", suffix=".restore", dir=self.path.parent)
        os.close(descriptor)
        temporary_path = Path(temporary_name)
        try:
            temporary_path.write_bytes(database_bytes)
            self._validate_database_file(temporary_path)
            for suffix in ("-wal", "-shm"):
                Path(f"{self.path}{suffix}").unlink(missing_ok=True)
            os.replace(temporary_path, self.path)
        finally:
            temporary_path.unlink(missing_ok=True)
        self._initialize()
        with self.transaction() as connection:
            self.append_audit(
                connection,
                event_id=f"EVT-{uuid.uuid4().hex.upper()}",
                action="database.backup_restored",
                entity_type="database",
                entity_id=self.path.name,
                admin_user_id=admin_user_id,
                details={"file_name": backup_path.name, "sha256": hashlib.sha256(backup_path.read_bytes()).hexdigest()},
            )
