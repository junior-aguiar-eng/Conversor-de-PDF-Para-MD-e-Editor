"""Operações administrativas e emissão ACT4 sobre o banco local."""

from __future__ import annotations

import calendar
import hashlib
import json
import os
import re
import shlex
import tempfile
import unicodedata
import uuid
from collections.abc import Callable, Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from license_core import (
    LICENSE_FILE_SUFFIX,
    LegacyLicenseVersion,
    LicensePayload,
    detect_legacy_activation_key,
    issue_license,
    verify_legacy_activation_key,
)

from .database import AdminDatabase, ConfirmationRequiredError, utc_now_text
from .security import hash_admin_password, verify_admin_password

_MACHINE_ID = re.compile(r"NXJ2-(?:[A-F0-9]{4}-){3}[A-F0-9]{4}")
_FEATURES = frozenset({"converter", "ocr", "reader"})
_TERMS = frozenset({3, 6, 12})


class AdminLicenseError(RuntimeError):
    pass


class RecordNotFoundError(AdminLicenseError):
    pass


class InvalidTransitionError(AdminLicenseError):
    pass


def _id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex.upper()}"


def _utc(value: datetime | None = None) -> datetime:
    instant = datetime.now(UTC) if value is None else value
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise ValueError("A data deve possuir fuso horário.")
    return instant.astimezone(UTC).replace(microsecond=0)


def _timestamp(value: datetime) -> str:
    return _utc(value).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_timestamp(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


def _add_months(value: datetime, months: int) -> datetime:
    month_index = value.year * 12 + value.month - 1 + months
    year, zero_based_month = divmod(month_index, 12)
    month = zero_based_month + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


def _normalize_name(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.strip())
    plain = "".join(character for character in decomposed if not unicodedata.combining(character))
    return " ".join(plain.casefold().split())


def _optional_text(value: str | None, *, upper: bool = False) -> str | None:
    normalized = (value or "").strip()
    if not normalized:
        return None
    return normalized.upper() if upper else normalized


class EncryptedPrivateKeyProvider:
    """Carrega sob demanda somente PEM Ed25519 criptografado; não mantém a senha."""

    def __init__(self, path: str | Path, password_provider: Callable[[], str | bytes]) -> None:
        self.path = Path(path).expanduser().resolve()
        self.password_provider = password_provider

    def __call__(self) -> Ed25519PrivateKey:
        try:
            pem = self.path.read_bytes()
        except OSError as error:
            raise AdminLicenseError("Não foi possível ler a chave privada administrativa.") from error
        if b"ENCRYPTED PRIVATE KEY" not in pem:
            raise AdminLicenseError("A chave privada administrativa deve estar criptografada no disco.")
        password = self.password_provider()
        password_bytes = password.encode("utf-8") if isinstance(password, str) else password
        if not password_bytes:
            raise AdminLicenseError("A senha da chave privada não pode ser vazia.")
        try:
            loaded = serialization.load_pem_private_key(pem, password=password_bytes)
        except (TypeError, ValueError) as error:
            raise AdminLicenseError("Não foi possível abrir a chave privada administrativa.") from error
        if not isinstance(loaded, Ed25519PrivateKey):
            raise AdminLicenseError("A chave administrativa deve ser Ed25519.")
        return loaded


class AdminLicenseService:
    def __init__(
        self,
        database: AdminDatabase,
        *,
        key_id: str,
        private_key_provider: Callable[[], Ed25519PrivateKey],
        legacy_key_verifier: Callable[[str, str], bool] = verify_legacy_activation_key,
    ) -> None:
        self.database = database
        self.key_id = key_id
        self.private_key_provider = private_key_provider
        self.legacy_key_verifier = legacy_key_verifier

    def create_admin_user(
        self,
        username: str,
        display_name: str,
        *,
        role: str = "operator",
        password: str | None = None,
        password_hash: str | None = None,
    ) -> str:
        admin_id = _id("ADM")
        normalized_username = username.strip().casefold()
        if not normalized_username or not display_name.strip():
            raise ValueError("Nome de usuário e nome de exibição são obrigatórios.")
        if password is not None and password_hash is not None:
            raise ValueError("Informe a senha ou o hash, nunca ambos.")
        stored_password_hash = hash_admin_password(password) if password is not None else password_hash
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO admin_users(admin_user_id, username, display_name, role, password_hash, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (admin_id, normalized_username, display_name.strip(), role, stored_password_hash, utc_now_text()),
            )
            self._audit(connection, "admin_user.created", "admin_user", admin_id, None, {"role": role})
        return admin_id

    def authenticate_admin(self, username: str, password: str) -> str:
        normalized_username = username.strip().casefold()
        with self.database.read() as connection:
            row = connection.execute(
                "SELECT admin_user_id, password_hash, active FROM admin_users WHERE username = ?",
                (normalized_username,),
            ).fetchone()
        if not row or not row["active"] or not verify_admin_password(password, row["password_hash"]):
            raise PermissionError("Credenciais administrativas inválidas.")
        return str(row["admin_user_id"])

    def set_admin_password(
        self,
        admin_user_id: str,
        new_password: str,
        *,
        current_password: str | None = None,
        acting_admin_user_id: str | None = None,
    ) -> None:
        with self.database.transaction() as connection:
            self._require_admin(connection, acting_admin_user_id)
            row = connection.execute(
                "SELECT username, password_hash, active FROM admin_users WHERE admin_user_id = ?",
                (admin_user_id,),
            ).fetchone()
            if not row or not row["active"]:
                raise RecordNotFoundError("Usuário administrativo ativo não encontrado.")
            if row["password_hash"] and not verify_admin_password(current_password or "", row["password_hash"]):
                raise PermissionError("A senha administrativa atual é inválida.")
            connection.execute(
                "UPDATE admin_users SET password_hash = ? WHERE admin_user_id = ?",
                (hash_admin_password(new_password), admin_user_id),
            )
            self._audit(
                connection,
                "admin_user.password_changed",
                "admin_user",
                admin_user_id,
                acting_admin_user_id,
            )

    def revoke_admin_user(
        self,
        admin_user_id: str,
        *,
        reason: str,
        confirmation: str,
        acting_admin_user_id: str | None = None,
    ) -> None:
        expected = f"REVOGAR-ADMIN:{admin_user_id}"
        if confirmation != expected:
            raise ConfirmationRequiredError(f"Confirmação obrigatória: {expected}")
        if not reason.strip():
            raise ValueError("O motivo da revogação é obrigatório.")
        with self.database.transaction() as connection:
            self._require_admin(connection, acting_admin_user_id)
            row = connection.execute(
                "SELECT active FROM admin_users WHERE admin_user_id = ?", (admin_user_id,)
            ).fetchone()
            if not row or not row["active"]:
                raise RecordNotFoundError("Usuário administrativo ativo não encontrado.")
            connection.execute("UPDATE admin_users SET active = 0 WHERE admin_user_id = ?", (admin_user_id,))
            connection.execute("UPDATE admin_api_tokens SET active = 0 WHERE admin_user_id = ?", (admin_user_id,))
            self._audit(
                connection,
                "admin_user.revoked",
                "admin_user",
                admin_user_id,
                acting_admin_user_id,
                {"reason": reason.strip()},
            )

    def create_customer(
        self,
        name: str,
        *,
        email: str | None = None,
        phone: str | None = None,
        tax_id: str | None = None,
        commercial_reference: str | None = None,
        admin_user_id: str | None = None,
    ) -> str:
        normalized_name = _normalize_name(name)
        if not normalized_name:
            raise ValueError("O nome do cliente é obrigatório.")
        customer_id = _id("CUS")
        with self.database.transaction() as connection:
            self._require_admin(connection, admin_user_id)
            connection.execute(
                """
                INSERT INTO customers(
                    customer_id, name, normalized_name, email, phone, tax_id,
                    commercial_reference, created_at, created_by
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    customer_id,
                    name.strip(),
                    normalized_name,
                    _optional_text(email),
                    _optional_text(phone),
                    _optional_text(tax_id, upper=True),
                    _optional_text(commercial_reference),
                    utc_now_text(),
                    admin_user_id,
                ),
            )
            self._audit(connection, "customer.created", "customer", customer_id, admin_user_id)
        return customer_id

    def issue_license(
        self,
        customer_id: str,
        *,
        term_months: int,
        features: Iterable[str] = ("converter", "ocr", "reader"),
        validation_mode: str = "offline",
        max_offline_days: int = 0,
        starts_at: datetime | None = None,
        customer_reference: str | None = None,
        commercial_reference: str | None = None,
        machine_id: str | None = None,
        activation_secret: str | None = None,
        admin_user_id: str | None = None,
    ) -> str:
        if term_months not in _TERMS:
            raise ValueError("O prazo deve ser de 3, 6 ou 12 meses.")
        normalized_features = tuple(sorted(set(features)))
        if not normalized_features or not set(normalized_features).issubset(_FEATURES):
            raise ValueError("As funcionalidades da licença são inválidas.")
        if validation_mode == "offline" and max_offline_days != 0:
            raise ValueError("Licenças offline devem usar max_offline_days igual a 0.")
        if validation_mode == "hybrid" and not 1 <= max_offline_days <= 30:
            raise ValueError("Licenças híbridas devem usar prazo offline entre 1 e 30 dias.")
        if validation_mode not in {"offline", "hybrid"}:
            raise ValueError("O modo de validação deve ser offline ou hybrid.")
        normalized_machine = (machine_id or "").strip().upper()
        if normalized_machine and not _MACHINE_ID.fullmatch(normalized_machine):
            raise ValueError("O código da máquina NXJ2 é inválido.")
        activation_secret_hash = None
        if activation_secret is not None:
            if len(activation_secret) < 32:
                raise ValueError("O segredo de ativação deve possuir ao menos 32 caracteres.")
            activation_secret_hash = hashlib.sha256(activation_secret.encode("utf-8")).hexdigest()
        starts = _utc(starts_at)
        issued = _utc()
        if issued > starts:
            starts = issued
        expires = _add_months(starts, term_months)
        active_key_id = self._active_key_id()
        with self.database.transaction() as connection:
            self._require_admin(connection, admin_user_id)
            if not connection.execute("SELECT 1 FROM customers WHERE customer_id = ?", (customer_id,)).fetchone():
                raise RecordNotFoundError("Cliente não encontrado.")
            year = issued.year
            prefix = f"LIC-{year}-"
            row = connection.execute(
                "SELECT COALESCE(MAX(CAST(substr(license_id, 10) AS INTEGER)), 0) FROM licenses WHERE license_id LIKE ?",
                (f"{prefix}%",),
            ).fetchone()
            license_id = f"{prefix}{int(row[0]) + 1:06d}"
            reference = (customer_reference or f"CUST-{uuid.uuid4().hex[:12]}").strip().upper()
            connection.execute(
                """
                INSERT INTO licenses(
                    license_id, customer_id, key_id, status, issued_at, not_before,
                    expires_at, term_months, validation_mode, max_offline_days,
                    customer_reference, commercial_reference, created_by
                    , activation_secret_hash
                ) VALUES (?, ?, ?, 'active', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    license_id,
                    customer_id,
                    active_key_id,
                    _timestamp(issued),
                    _timestamp(starts),
                    _timestamp(expires),
                    term_months,
                    validation_mode,
                    max_offline_days,
                    reference,
                    _optional_text(commercial_reference),
                    admin_user_id,
                    activation_secret_hash,
                ),
            )
            connection.executemany(
                "INSERT INTO license_features(license_id, feature) VALUES (?, ?)",
                ((license_id, feature) for feature in normalized_features),
            )
            device_id = None
            if normalized_machine:
                device_id = _id("DEV")
                connection.execute(
                    """
                    INSERT INTO licensed_devices(device_id, license_id, machine_id, bound_at, active, created_by)
                    VALUES (?, ?, ?, ?, 1, ?)
                    """,
                    (device_id, license_id, normalized_machine, utc_now_text(), admin_user_id),
                )
            self._refresh_search(connection, license_id)
            self._audit(
                connection,
                "license.issued",
                "license",
                license_id,
                admin_user_id,
                {"customer_id": customer_id, "term_months": term_months, "validation_mode": validation_mode},
            )
            if device_id:
                self._audit(
                    connection,
                    "device.bound",
                    "license",
                    license_id,
                    admin_user_id,
                    {"device_id": device_id, "machine_id": normalized_machine},
                )
        return license_id

    def validate_legacy_migration(
        self,
        machine_id: str,
        activation_key: str,
        *,
        confirmation: str,
    ) -> LegacyLicenseVersion:
        normalized_machine = machine_id.strip().upper()
        normalized_key = activation_key.strip().upper()
        if not _MACHINE_ID.fullmatch(normalized_machine):
            raise ValueError("O código da máquina NXJ2 é inválido.")
        version = detect_legacy_activation_key(normalized_key)
        if version not in {LegacyLicenseVersion.ACT2, LegacyLicenseVersion.ACT3}:
            raise ValueError("Informe uma licença legada ACT2 ou ACT3 válida.")
        expected = f"MIGRAR:{normalized_machine}"
        if confirmation != expected:
            raise ConfirmationRequiredError(f"Confirmação obrigatória: {expected}")
        if not self.legacy_key_verifier(normalized_machine, normalized_key):
            raise ValueError("A licença legada não é válida para a máquina informada.")
        return version

    def migrate_legacy_license(
        self,
        customer_id: str,
        *,
        machine_id: str,
        activation_key: str,
        confirmation: str,
        term_months: int,
        features: Iterable[str] = ("converter", "ocr", "reader"),
        validation_mode: str = "offline",
        max_offline_days: int = 0,
        commercial_reference: str | None = None,
        admin_user_id: str | None = None,
    ) -> tuple[str, str]:
        normalized_machine = machine_id.strip().upper()
        normalized_key = activation_key.strip().upper()
        version = self.validate_legacy_migration(
            normalized_machine,
            normalized_key,
            confirmation=confirmation,
        )
        legacy_key_hash = hashlib.sha256(normalized_key.encode("ascii")).hexdigest()
        with self.database.read() as connection:
            if connection.execute(
                "SELECT 1 FROM legacy_license_migrations WHERE legacy_key_hash = ?",
                (legacy_key_hash,),
            ).fetchone():
                raise InvalidTransitionError("Esta licença legada já possui uma migração registrada.")
        license_id = self.issue_license(
            customer_id,
            term_months=term_months,
            features=features,
            validation_mode=validation_mode,
            max_offline_days=max_offline_days,
            machine_id=normalized_machine,
            commercial_reference=commercial_reference,
            admin_user_id=admin_user_id,
        )
        migration_id = _id("MIG")
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO legacy_license_migrations(
                    migration_id, legacy_version, legacy_key_hash, machine_id,
                    customer_id, license_id, acknowledged_at, admin_user_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    migration_id,
                    version.value,
                    legacy_key_hash,
                    normalized_machine,
                    customer_id,
                    license_id,
                    utc_now_text(),
                    admin_user_id,
                ),
            )
            self._audit(
                connection,
                "license.migrated_from_legacy",
                "license",
                license_id,
                admin_user_id,
                {"migration_id": migration_id, "legacy_version": version.value, "machine_id": normalized_machine},
            )
        return migration_id, license_id

    def bind_device(self, license_id: str, machine_id: str, *, admin_user_id: str | None = None) -> str:
        normalized_machine = machine_id.strip().upper()
        if not _MACHINE_ID.fullmatch(normalized_machine):
            raise ValueError("O código da máquina NXJ2 é inválido.")
        device_id = _id("DEV")
        with self.database.transaction() as connection:
            self._require_admin(connection, admin_user_id)
            license_row = self._license_row(connection, license_id)
            if license_row["status"] == "revoked":
                raise InvalidTransitionError("Não é possível vincular dispositivo a uma licença revogada.")
            if connection.execute(
                "SELECT 1 FROM licensed_devices WHERE license_id = ? AND active = 1", (license_id,)
            ).fetchone():
                raise InvalidTransitionError("A licença já possui um dispositivo ativo.")
            connection.execute(
                """
                INSERT INTO licensed_devices(device_id, license_id, machine_id, bound_at, active, created_by)
                VALUES (?, ?, ?, ?, 1, ?)
                """,
                (device_id, license_id, normalized_machine, utc_now_text(), admin_user_id),
            )
            self._refresh_search(connection, license_id)
            self._audit(
                connection,
                "device.bound",
                "license",
                license_id,
                admin_user_id,
                {"device_id": device_id, "machine_id": normalized_machine},
            )
        return device_id

    def renew_license(
        self,
        license_id: str,
        *,
        term_months: int,
        admin_user_id: str | None = None,
        now: datetime | None = None,
    ) -> str:
        if term_months not in _TERMS:
            raise ValueError("A renovação deve ser de 3, 6 ou 12 meses.")
        instant = _utc(now)
        renewal_id = _id("REN")
        with self.database.transaction() as connection:
            self._require_admin(connection, admin_user_id)
            row = self._license_row(connection, license_id)
            if row["status"] == "revoked":
                raise InvalidTransitionError("Licenças revogadas não podem ser renovadas.")
            previous = _parse_timestamp(row["expires_at"])
            base = max(previous, instant)
            new_expiration = _add_months(base, term_months)
            connection.execute(
                "UPDATE licenses SET expires_at = ?, term_months = ? WHERE license_id = ?",
                (_timestamp(new_expiration), term_months, license_id),
            )
            connection.execute(
                """
                INSERT INTO license_renewals(
                    renewal_id, license_id, previous_expires_at, new_expires_at,
                    term_months, renewed_at, admin_user_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    renewal_id,
                    license_id,
                    row["expires_at"],
                    _timestamp(new_expiration),
                    term_months,
                    _timestamp(instant),
                    admin_user_id,
                ),
            )
            self._refresh_search(connection, license_id)
            self._audit(
                connection,
                "license.renewed",
                "license",
                license_id,
                admin_user_id,
                {"renewal_id": renewal_id, "new_expires_at": _timestamp(new_expiration), "term_months": term_months},
            )
        return renewal_id

    def suspend_license(self, license_id: str, reason: str, *, admin_user_id: str | None = None) -> str:
        return self._change_status(license_id, "suspended", reason, admin_user_id=admin_user_id)

    def revoke_license(
        self,
        license_id: str,
        reason: str,
        *,
        confirmation: str,
        admin_user_id: str | None = None,
    ) -> str:
        expected = f"REVOGAR:{license_id}"
        if confirmation != expected:
            raise ConfirmationRequiredError(f"Confirmação obrigatória: {expected}")
        return self._change_status(license_id, "revoked", reason, admin_user_id=admin_user_id)

    def reactivate_license(self, license_id: str, reason: str, *, admin_user_id: str | None = None) -> str:
        return self._change_status(license_id, "active", reason, admin_user_id=admin_user_id)

    def replace_device(
        self,
        license_id: str,
        new_machine_id: str,
        *,
        confirmation: str,
        reason: str,
        admin_user_id: str | None = None,
    ) -> str:
        expected = f"TROCAR:{license_id}"
        if confirmation != expected:
            raise ConfirmationRequiredError(f"Confirmação obrigatória: {expected}")
        normalized_machine = new_machine_id.strip().upper()
        if not _MACHINE_ID.fullmatch(normalized_machine):
            raise ValueError("O código da nova máquina NXJ2 é inválido.")
        if not reason.strip():
            raise ValueError("O motivo da troca de computador é obrigatório.")
        new_device_id = _id("DEV")
        timestamp = utc_now_text()
        with self.database.transaction() as connection:
            self._require_admin(connection, admin_user_id)
            row = self._license_row(connection, license_id)
            if row["status"] == "revoked":
                raise InvalidTransitionError("Não é possível trocar o dispositivo de uma licença revogada.")
            current = connection.execute(
                "SELECT * FROM licensed_devices WHERE license_id = ? AND active = 1", (license_id,)
            ).fetchone()
            if not current:
                raise RecordNotFoundError("A licença não possui dispositivo ativo para substituição.")
            connection.execute(
                "UPDATE licensed_devices SET active = 0, unbound_at = ? WHERE device_id = ?",
                (timestamp, current["device_id"]),
            )
            connection.execute(
                """
                INSERT INTO licensed_devices(device_id, license_id, machine_id, bound_at, active, created_by)
                VALUES (?, ?, ?, ?, 1, ?)
                """,
                (new_device_id, license_id, normalized_machine, timestamp, admin_user_id),
            )
            self._refresh_search(connection, license_id)
            self._audit(
                connection,
                "device.replaced",
                "license",
                license_id,
                admin_user_id,
                {
                    "previous_device_id": current["device_id"],
                    "new_device_id": new_device_id,
                    "new_machine_id": normalized_machine,
                    "reason": reason.strip(),
                },
            )
        return new_device_id

    def export_license(
        self,
        license_id: str,
        destination: str | Path,
        *,
        admin_user_id: str | None = None,
    ) -> Path:
        with self.database.read() as connection:
            self._require_admin(connection, admin_user_id)
            row = self._license_row(connection, license_id)
            if row["status"] != "active":
                raise InvalidTransitionError("Somente licenças ativas podem ser exportadas.")
            device = connection.execute(
                "SELECT * FROM licensed_devices WHERE license_id = ? AND active = 1", (license_id,)
            ).fetchone()
            if not device:
                raise RecordNotFoundError("Vincule um dispositivo antes de exportar a licença.")
            features = tuple(
                item[0]
                for item in connection.execute(
                    "SELECT feature FROM license_features WHERE license_id = ? ORDER BY feature", (license_id,)
                ).fetchall()
            )
        payload = LicensePayload(
            key_id=row["key_id"],
            license_id=row["license_id"],
            machine_id=device["machine_id"],
            issued_at=row["issued_at"],
            not_before=row["not_before"],
            expires_at=row["expires_at"],
            validation_mode=row["validation_mode"],
            max_offline_days=row["max_offline_days"],
            features=features,
            customer_reference=row["customer_reference"],
        )
        document = issue_license(payload, self._private_key_for(row["key_id"]))
        target = Path(destination).expanduser().resolve()
        if target.suffix.lower() != LICENSE_FILE_SUFFIX:
            raise ValueError(f"A exportação deve usar a extensão {LICENSE_FILE_SUFFIX}.")
        target.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
        try:
            with os.fdopen(descriptor, "wb") as temporary:
                temporary.write(document)
                temporary.flush()
                os.fsync(temporary.fileno())
            os.replace(temporary_name, target)
        finally:
            Path(temporary_name).unlink(missing_ok=True)
        digest = hashlib.sha256(document).hexdigest()
        export_id = _id("EXP")
        with self.database.transaction() as connection:
            self._require_admin(connection, admin_user_id)
            connection.execute(
                """
                INSERT INTO offline_exports(
                    export_id, license_id, device_id, document_sha256,
                    file_name, exported_at, admin_user_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (export_id, license_id, device["device_id"], digest, target.name, utc_now_text(), admin_user_id),
            )
            self._audit(
                connection,
                "license.exported",
                "license",
                license_id,
                admin_user_id,
                {"export_id": export_id, "device_id": device["device_id"], "sha256": digest},
            )
        return target

    def get_license(self, license_id: str) -> dict[str, Any]:
        with self.database.read() as connection:
            row = self._license_row(connection, license_id)
            customer = connection.execute(
                "SELECT * FROM customers WHERE customer_id = ?", (row["customer_id"],)
            ).fetchone()
            device = connection.execute(
                "SELECT * FROM licensed_devices WHERE license_id = ? AND active = 1", (license_id,)
            ).fetchone()
            features = [
                feature[0]
                for feature in connection.execute(
                    "SELECT feature FROM license_features WHERE license_id = ? ORDER BY feature", (license_id,)
                ).fetchall()
            ]
        return {
            **dict(row),
            "customer": dict(customer),
            "active_device": dict(device) if device else None,
            "features": features,
        }

    def search_licenses(
        self,
        query: str,
        *,
        page: int = 1,
        page_size: int = 25,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Executa busca paginada; consulta vazia nunca carrega a tabela geral."""
        expression = query.strip()
        if not expression:
            return {"query": "", "page": 1, "page_size": page_size, "total": 0, "items": []}
        if len(expression) > 500:
            raise ValueError("A busca não pode exceder 500 caracteres.")
        if page < 1:
            raise ValueError("A página deve ser maior ou igual a 1.")
        if not 1 <= page_size <= 100:
            raise ValueError("O tamanho da página deve ficar entre 1 e 100.")
        instant = _utc(now)
        instant_text = _timestamp(instant)
        conditions: list[str] = []
        parameters: list[Any] = []
        free_terms: list[str] = []
        try:
            tokens = shlex.split(expression)
        except ValueError as error:
            raise ValueError("A busca contém aspas não fechadas.") from error
        if len(tokens) > 20:
            raise ValueError("A busca não pode exceder 20 termos.")
        for token in tokens:
            if ":" not in token:
                free_terms.append(token)
                continue
            field, value = token.split(":", 1)
            field = field.casefold()
            value = value.strip()
            if field == "status":
                status = value.casefold()
                if status in {"expirada", "expired"}:
                    conditions.append("l.status = 'active' AND l.expires_at <= ?")
                    parameters.append(instant_text)
                elif status in {"ativa", "active"}:
                    conditions.append("l.status = 'active' AND l.expires_at > ?")
                    parameters.append(instant_text)
                elif status in {"suspensa", "suspended"}:
                    conditions.append("l.status = 'suspended'")
                elif status in {"revogada", "revoked"}:
                    conditions.append("l.status = 'revoked'")
                elif status in {"aguardando", "aguardando_conexao"}:
                    conditions.append(
                        "l.validation_mode = 'hybrid' AND l.status = 'active' AND l.expires_at > ? "
                        "AND NOT EXISTS (SELECT 1 FROM online_leases waiting WHERE waiting.license_id = l.license_id "
                        "AND waiting.status = 'active' AND waiting.expires_at > ?)"
                    )
                    parameters.extend((instant_text, instant_text))
                else:
                    raise ValueError(f"Status de busca desconhecido: {value}.")
            elif field in {"expira", "offline"}:
                match = re.fullmatch(r"(\d{1,3})d", value.casefold())
                if not match:
                    raise ValueError(f"O filtro {field} deve usar o formato Nd, por exemplo 7d.")
                days = int(match.group(1))
                cutoff = _timestamp(instant + timedelta(days=days))
                if field == "expira":
                    conditions.append("l.status = 'active' AND l.expires_at > ? AND l.expires_at <= ?")
                    parameters.extend((instant_text, cutoff))
                else:
                    conditions.append(
                        "l.status = 'active' AND l.expires_at > ? AND "
                        "EXISTS (SELECT 1 FROM online_leases lease_filter WHERE lease_filter.license_id = l.license_id "
                        "AND lease_filter.status = 'active' AND lease_filter.expires_at > ? "
                        "AND lease_filter.expires_at <= ?)"
                    )
                    parameters.extend((instant_text, instant_text, cutoff))
            elif field == "cliente":
                conditions.append("c.normalized_name LIKE ?")
                parameters.append(f"{_normalize_name(value)}%")
            elif field in {"maquina", "machine"}:
                conditions.append("d.machine_id LIKE ?")
                parameters.append(f"{value.upper()}%")
            else:
                free_terms.append(token)
        if free_terms:
            searchable = []
            for term in free_terms:
                parts = re.findall(r"[\w@.+-]+", term, flags=re.UNICODE)
                searchable.extend(part for part in parts if part)
            if searchable:
                fts_query = " AND ".join(f'"{part.replace(chr(34), chr(34) * 2)}"*' for part in searchable)
                conditions.append(
                    "l.license_id IN (SELECT search.license_id FROM license_search search WHERE license_search MATCH ?)"
                )
                parameters.append(fts_query)
        if not conditions:
            return {"query": expression, "page": page, "page_size": page_size, "total": 0, "items": []}
        where = " AND ".join(f"({condition})" for condition in conditions)
        joins = (
            " FROM licenses l JOIN customers c ON c.customer_id = l.customer_id "
            "LEFT JOIN licensed_devices d ON d.license_id = l.license_id AND d.active = 1 "
        )
        with self.database.read() as connection:
            total = int(connection.execute(f"SELECT COUNT(*){joins}WHERE {where}", parameters).fetchone()[0])
            rows = connection.execute(
                f"""
                SELECT l.license_id, l.status, l.expires_at, l.validation_mode,
                       l.commercial_reference, c.customer_id, c.name AS customer_name,
                       c.email, c.phone, c.tax_id, d.machine_id,
                       CASE WHEN l.status = 'active' AND l.expires_at <= ?
                            THEN 'expired' ELSE l.status END AS effective_status
                {joins}
                WHERE {where}
                ORDER BY CASE WHEN l.license_id = ? THEN 0 ELSE 1 END,
                         l.expires_at ASC, l.license_id ASC
                LIMIT ? OFFSET ?
                """,
                [instant_text, *parameters, expression.upper(), page_size, (page - 1) * page_size],
            ).fetchall()
        return {
            "query": expression,
            "page": page,
            "page_size": page_size,
            "total": total,
            "items": [dict(row) for row in rows],
        }

    def dashboard(self, *, limit: int = 8, now: datetime | None = None) -> dict[str, Any]:
        if not 1 <= limit <= 25:
            raise ValueError("O limite do painel deve ficar entre 1 e 25.")
        instant = _utc(now)
        now_text = _timestamp(instant)
        expiring_at = _timestamp(instant + timedelta(days=30))
        expiring_condition = "l.status = 'active' AND l.expires_at > ? AND l.expires_at <= ?"
        awaiting_condition = """
            l.status = 'active' AND l.expires_at > ? AND l.validation_mode = 'hybrid'
            AND NOT EXISTS (
                SELECT 1 FROM online_leases ol WHERE ol.license_id = l.license_id
                AND ol.status = 'active' AND ol.expires_at > ?
            )
        """
        with self.database.read() as connection:
            activities = [
                dict(row)
                for row in connection.execute(
                    """
                    SELECT event_id, action, entity_type, entity_id, occurred_at, admin_user_id
                    FROM audit_events ORDER BY rowid DESC LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
            ]
            expiring = self._dashboard_licenses(
                connection,
                expiring_condition,
                (now_text, expiring_at),
                limit,
            )
            awaiting = self._dashboard_licenses(
                connection,
                awaiting_condition,
                (now_text, now_text),
                limit,
            )
            suspended = self._dashboard_licenses(connection, "l.status = 'suspended'", (), limit)
            revoked = self._dashboard_licenses(connection, "l.status = 'revoked'", (), limit)
            counts = {
                "expiring": self._dashboard_count(connection, expiring_condition, (now_text, expiring_at)),
                "awaiting_connection": self._dashboard_count(
                    connection, awaiting_condition, (now_text, now_text)
                ),
                "suspended": self._dashboard_count(connection, "l.status = 'suspended'", ()),
                "revoked": self._dashboard_count(connection, "l.status = 'revoked'", ()),
            }
        return {
            "counts": counts,
            "activities": activities,
            "expiring": expiring,
            "awaiting_connection": awaiting,
            "suspended": suspended,
            "revoked": revoked,
        }

    def license_detail(self, license_id: str, *, now: datetime | None = None) -> dict[str, Any]:
        instant_text = _timestamp(_utc(now))
        detail = self.get_license(license_id)
        with self.database.read() as connection:
            lease = connection.execute(
                """
                SELECT expires_at, last_seen_at, status FROM online_leases
                WHERE license_id = ? ORDER BY issued_at DESC LIMIT 1
                """,
                (license_id,),
            ).fetchone()
            history = connection.execute(
                """
                SELECT event_id, action, occurred_at, admin_user_id, details_json
                FROM audit_events WHERE entity_type = 'license' AND entity_id = ?
                ORDER BY rowid DESC LIMIT 100
                """,
                (license_id,),
            ).fetchall()
        effective_status = (
            "expired" if detail["status"] == "active" and detail["expires_at"] <= instant_text else detail["status"]
        )
        detail["effective_status"] = effective_status
        detail["last_connection"] = lease["last_seen_at"] if lease else None
        detail["offline_until"] = lease["expires_at"] if lease else None
        detail["lease_status"] = lease["status"] if lease else None
        detail["history"] = [
            {**dict(item), "details": json.loads(item["details_json"])} for item in history
        ]
        detail["available_actions"] = self._available_actions(detail["status"], bool(detail["active_device"]))
        return detail

    def history(self, entity_type: str, entity_id: str) -> list[dict[str, Any]]:
        with self.database.read() as connection:
            rows = connection.execute(
                """
                SELECT event_id, action, entity_type, entity_id, occurred_at,
                       admin_user_id, details_json, previous_hash, event_hash
                FROM audit_events
                WHERE entity_type = ? AND entity_id = ?
                ORDER BY rowid
                """,
                (entity_type, entity_id),
            ).fetchall()
        return [dict(row) for row in rows]

    def _change_status(
        self,
        license_id: str,
        new_status: str,
        reason: str,
        *,
        admin_user_id: str | None,
    ) -> str:
        if not reason.strip():
            raise ValueError("O motivo da alteração de status é obrigatório.")
        allowed = {
            ("active", "suspended"),
            ("active", "revoked"),
            ("suspended", "active"),
            ("suspended", "revoked"),
        }
        change_id = _id("STA")
        timestamp = utc_now_text()
        with self.database.transaction() as connection:
            self._require_admin(connection, admin_user_id)
            row = self._license_row(connection, license_id)
            previous = row["status"]
            if (previous, new_status) not in allowed:
                raise InvalidTransitionError(f"Transição de {previous} para {new_status} não permitida.")
            connection.execute("UPDATE licenses SET status = ? WHERE license_id = ?", (new_status, license_id))
            connection.execute(
                """
                INSERT INTO license_status_changes(
                    change_id, license_id, previous_status, new_status,
                    reason, changed_at, admin_user_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (change_id, license_id, previous, new_status, reason.strip(), timestamp, admin_user_id),
            )
            self._refresh_search(connection, license_id)
            self._audit(
                connection,
                f"license.{new_status}",
                "license",
                license_id,
                admin_user_id,
                {"change_id": change_id, "previous_status": previous, "reason": reason.strip()},
            )
        return change_id

    @staticmethod
    def _dashboard_licenses(connection: Any, condition: str, parameters: tuple[Any, ...], limit: int) -> list[dict[str, Any]]:
        rows = connection.execute(
            f"""
            SELECT l.license_id, l.status, l.expires_at, l.validation_mode,
                   c.name AS customer_name, d.machine_id
            FROM licenses l
            JOIN customers c ON c.customer_id = l.customer_id
            LEFT JOIN licensed_devices d ON d.license_id = l.license_id AND d.active = 1
            WHERE {condition}
            ORDER BY l.expires_at ASC, l.license_id ASC LIMIT ?
            """,
            (*parameters, limit),
        ).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _dashboard_count(connection: Any, condition: str, parameters: tuple[Any, ...]) -> int:
        return int(connection.execute(f"SELECT COUNT(*) FROM licenses l WHERE {condition}", parameters).fetchone()[0])

    @staticmethod
    def _available_actions(status: str, has_device: bool) -> list[str]:
        if status == "revoked":
            return []
        actions = ["renew", "export"] if has_device else ["bind_device"]
        if status == "active":
            actions.extend(("suspend", "revoke"))
        elif status == "suspended":
            actions.extend(("reactivate", "revoke"))
        if has_device:
            actions.append("replace_device")
        return actions

    @staticmethod
    def _refresh_search(connection: Any, license_id: str) -> None:
        connection.execute("DELETE FROM license_search WHERE license_id = ?", (license_id,))
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
            WHERE l.license_id = ?
            """,
            (license_id,),
        )

    @staticmethod
    def _license_row(connection: Any, license_id: str) -> Any:
        row = connection.execute("SELECT * FROM licenses WHERE license_id = ?", (license_id,)).fetchone()
        if not row:
            raise RecordNotFoundError("Licença não encontrada.")
        return row

    @staticmethod
    def _require_admin(connection: Any, admin_user_id: str | None) -> None:
        if admin_user_id is None:
            return
        row = connection.execute(
            "SELECT active FROM admin_users WHERE admin_user_id = ?", (admin_user_id,)
        ).fetchone()
        if not row or not row[0]:
            raise RecordNotFoundError("Usuário administrativo ativo não encontrado.")

    def _active_key_id(self) -> str:
        dynamic_key_id = getattr(self.private_key_provider, "key_id", None)
        return str(dynamic_key_id or self.key_id)

    def _private_key_for(self, key_id: str) -> Ed25519PrivateKey:
        resolver = getattr(self.private_key_provider, "for_key", None)
        if callable(resolver):
            return resolver(key_id)
        if key_id != self.key_id:
            raise AdminLicenseError(f"A chave privada {key_id} não está disponível para exportação.")
        return self.private_key_provider()

    def _audit(
        self,
        connection: Any,
        action: str,
        entity_type: str,
        entity_id: str,
        admin_user_id: str | None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.database.append_audit(
            connection,
            event_id=_id("EVT"),
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            admin_user_id=admin_user_id,
            details=details,
        )
