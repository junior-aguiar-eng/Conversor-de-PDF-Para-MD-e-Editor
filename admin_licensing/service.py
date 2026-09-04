"""Serviço local de emissão e manutenção de licenças ACT4."""

from __future__ import annotations

import calendar
import hashlib
import os
import re
import tempfile
import unicodedata
import uuid
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from license_core import LICENSE_FILE_SUFFIX, LicensePayload, issue_license

from .database import AdminDatabase, ConfirmationRequiredError, utc_now_text
from .security import hash_admin_password, verify_admin_password

_TERMS = frozenset({3, 6, 12})
_FEATURES = frozenset({"converter", "ocr", "reader"})
_MACHINE = re.compile(r"NXJ2-(?:[A-F0-9]{4}-){3}[A-F0-9]{4}")


class AdminLicenseError(RuntimeError):
    pass


class RecordNotFoundError(AdminLicenseError):
    pass


class InvalidTransitionError(AdminLicenseError):
    """Mantida como erro de domínio para compatibilidade da API local."""


def _identifier(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex.upper()}"


def _timestamp(value: datetime | None = None) -> str:
    instant = datetime.now(UTC) if value is None else value
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise ValueError("A data deve possuir fuso horário.")
    return instant.astimezone(UTC).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_timestamp(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


def _add_months(value: datetime, months: int) -> datetime:
    month_index = value.month - 1 + months
    year, month = value.year + month_index // 12, month_index % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


def _normalize_name(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value.strip().casefold())
    return "".join(character for character in normalized if not unicodedata.combining(character))


def _optional_text(value: object) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def _validate_machine_id(value: str) -> str:
    normalized = value.strip().upper()
    if not _MACHINE.fullmatch(normalized):
        raise ValueError("O código da máquina NXJ2 é inválido.")
    return normalized


def _validate_features(values: Iterable[str]) -> tuple[str, ...]:
    features = tuple(sorted(set(values)))
    if not features or not set(features).issubset(_FEATURES):
        raise ValueError("Selecione ao menos uma funcionalidade válida.")
    return features


class EncryptedPrivateKeyProvider:
    """Carrega uma chave Ed25519 privada protegida por senha."""

    def __init__(self, path: str | Path, password_provider: Callable[[], str | bytes]) -> None:
        self.path = Path(path).expanduser().resolve()
        self.password_provider = password_provider

    def __call__(self) -> Ed25519PrivateKey:
        password = self.password_provider()
        encoded_password = password.encode("utf-8") if isinstance(password, str) else password
        try:
            key = serialization.load_pem_private_key(self.path.read_bytes(), password=encoded_password)
        except (OSError, TypeError, ValueError) as error:
            raise AdminLicenseError("Não foi possível abrir a chave privada de assinatura.") from error
        if not isinstance(key, Ed25519PrivateKey):
            raise AdminLicenseError("A chave privada não é Ed25519.")
        return key


class AdminLicenseService:
    def __init__(
        self,
        database: AdminDatabase,
        *,
        key_id: str,
        private_key_provider: Callable[[], Ed25519PrivateKey],
    ) -> None:
        self.database = database
        self.key_id = key_id
        self.private_key_provider = private_key_provider

    def create_admin_user(
        self,
        username: str,
        display_name: str,
        *,
        role: str = "operator",
        password: str | None = None,
    ) -> str:
        username = username.strip().casefold()
        display_name = display_name.strip()
        if not username or not display_name:
            raise ValueError("Usuário e nome de exibição são obrigatórios.")
        if role not in {"owner", "administrator", "operator", "auditor"}:
            raise ValueError("Perfil administrativo inválido.")
        admin_user_id = _identifier("ADM")
        with self.database.transaction() as connection:
            connection.execute(
                """INSERT INTO admin_users(admin_user_id, username, display_name, role,
                                            password_hash, active, created_at)
                   VALUES (?, ?, ?, ?, ?, 1, ?)""",
                (
                    admin_user_id,
                    username,
                    display_name,
                    role,
                    hash_admin_password(password) if password else None,
                    utc_now_text(),
                ),
            )
            self.database.append_audit(
                connection,
                event_id=_identifier("EVT"),
                action="admin_user.created",
                entity_type="admin_user",
                entity_id=admin_user_id,
                admin_user_id=admin_user_id,
                details={"username": username, "role": role},
            )
        return admin_user_id

    def authenticate_admin(self, username: str, password: str) -> dict[str, Any] | None:
        with self.database.read() as connection:
            row = connection.execute(
                "SELECT * FROM admin_users WHERE username = ? AND active = 1",
                (username.strip().casefold(),),
            ).fetchone()
        if not row or not verify_admin_password(password, row["password_hash"]):
            return None
        return dict(row)

    def set_admin_password(self, admin_user_id: str, password: str) -> None:
        encoded = hash_admin_password(password)
        with self.database.transaction() as connection:
            if not connection.execute(
                "UPDATE admin_users SET password_hash = ? WHERE admin_user_id = ? AND active = 1",
                (encoded, admin_user_id),
            ).rowcount:
                raise RecordNotFoundError("Usuário administrativo não encontrado.")
            self._audit(connection, "admin_user.password_changed", "admin_user", admin_user_id, admin_user_id, {})

    def revoke_admin_user(self, admin_user_id: str, *, performed_by: str | None = None) -> None:
        with self.database.transaction() as connection:
            if not connection.execute(
                "UPDATE admin_users SET active = 0 WHERE admin_user_id = ? AND active = 1", (admin_user_id,)
            ).rowcount:
                raise RecordNotFoundError("Usuário administrativo não encontrado.")
            self._audit(
                connection, "admin_user.deactivated", "admin_user", admin_user_id, performed_by, {}
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
        with self.database.transaction() as connection:
            return self._create_customer(
                connection,
                name,
                email=email,
                phone=phone,
                tax_id=tax_id,
                commercial_reference=commercial_reference,
                admin_user_id=admin_user_id,
            )

    def _create_customer(
        self,
        connection: Any,
        name: str,
        *,
        email: str | None,
        phone: str | None,
        tax_id: str | None,
        commercial_reference: str | None,
        admin_user_id: str | None,
    ) -> str:
        name = name.strip()
        if not name:
            raise ValueError("O nome do cliente é obrigatório.")
        customer_id = _identifier("CUS")
        connection.execute(
            """INSERT INTO customers(customer_id, name, normalized_name, email, phone, tax_id,
                                     commercial_reference, created_at, created_by)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                customer_id,
                name,
                _normalize_name(name),
                _optional_text(email),
                _optional_text(phone),
                _optional_text(tax_id),
                _optional_text(commercial_reference),
                utc_now_text(),
                admin_user_id,
            ),
        )
        self._audit(
            connection, "customer.created", "customer", customer_id, admin_user_id, {"name": name}
        )
        return customer_id

    def issue_license(
        self,
        customer_id: str,
        *,
        term_months: int,
        machine_id: str,
        features: Iterable[str] = ("converter", "ocr", "reader"),
        customer_reference: str | None = None,
        commercial_reference: str | None = None,
        admin_user_id: str | None = None,
        at: datetime | None = None,
    ) -> str:
        if term_months not in _TERMS:
            raise ValueError("O prazo deve ser de 3, 6 ou 12 meses.")
        machine_id = _validate_machine_id(machine_id)
        normalized_features = _validate_features(features)
        now = datetime.now(UTC) if at is None else at.astimezone(UTC)
        issued_at = _timestamp(now)
        expires_at = _timestamp(_add_months(now, term_months))
        key_id, private_key = self._signing_identity()
        with self.database.transaction() as connection:
            customer = connection.execute(
                "SELECT * FROM customers WHERE customer_id = ?", (customer_id,)
            ).fetchone()
            if not customer:
                raise RecordNotFoundError("Cliente não encontrado.")
            license_id = self._next_license_id(connection, now.year)
            reference = (customer_reference or license_id).strip().upper()
            payload = LicensePayload(
                key_id=key_id,
                license_id=license_id,
                revision=1,
                machine_id=machine_id,
                issued_at=issued_at,
                not_before=issued_at,
                expires_at=expires_at,
                features=normalized_features,
                customer_reference=reference,
            )
            document = issue_license(payload, private_key)
            digest = hashlib.sha256(document).hexdigest()
            connection.execute(
                """INSERT INTO licenses(license_id, customer_id, key_id, current_revision,
                                        machine_id, issued_at, not_before, expires_at, term_months,
                                        customer_reference, commercial_reference, created_by)
                   VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    license_id,
                    customer_id,
                    key_id,
                    machine_id,
                    issued_at,
                    issued_at,
                    expires_at,
                    term_months,
                    reference,
                    _optional_text(commercial_reference),
                    admin_user_id,
                ),
            )
            connection.executemany(
                "INSERT INTO license_features(license_id, feature) VALUES (?, ?)",
                ((license_id, feature) for feature in normalized_features),
            )
            self._insert_revision(
                connection, payload, "issue", term_months, document, digest, admin_user_id
            )
            self._refresh_search(connection, license_id)
            self._audit(
                connection,
                "license.issued",
                "license",
                license_id,
                admin_user_id,
                {"revision": 1, "machine_id": machine_id, "expires_at": expires_at},
            )
        return license_id

    def renew_license(
        self,
        license_id: str,
        *,
        term_months: int,
        admin_user_id: str | None = None,
        at: datetime | None = None,
    ) -> int:
        if term_months not in _TERMS:
            raise ValueError("O prazo deve ser de 3, 6 ou 12 meses.")
        now = datetime.now(UTC) if at is None else at.astimezone(UTC)
        key_id, private_key = self._signing_identity()
        with self.database.transaction() as connection:
            row = self._license_row(connection, license_id)
            features = self._features(connection, license_id)
            revision = int(row["current_revision"]) + 1
            base = max(now, _parse_timestamp(row["expires_at"]))
            issued_at, expires_at = _timestamp(now), _timestamp(_add_months(base, term_months))
            payload = LicensePayload(
                key_id=key_id,
                license_id=license_id,
                revision=revision,
                machine_id=row["machine_id"],
                issued_at=issued_at,
                not_before=issued_at,
                expires_at=expires_at,
                features=features,
                customer_reference=row["customer_reference"],
            )
            document = issue_license(payload, private_key)
            digest = hashlib.sha256(document).hexdigest()
            connection.execute(
                """UPDATE licenses SET key_id = ?, current_revision = ?, issued_at = ?,
                                       not_before = ?, expires_at = ?, term_months = ?
                   WHERE license_id = ?""",
                (key_id, revision, issued_at, issued_at, expires_at, term_months, license_id),
            )
            self._insert_revision(
                connection, payload, "renew", term_months, document, digest, admin_user_id
            )
            self._refresh_search(connection, license_id)
            self._audit(
                connection,
                "license.renewed",
                "license",
                license_id,
                admin_user_id,
                {"revision": revision, "previous_expires_at": row["expires_at"], "expires_at": expires_at},
            )
        return revision

    def replace_device(
        self,
        license_id: str,
        machine_id: str,
        *,
        confirmation: str,
        reason: str,
        admin_user_id: str | None = None,
        at: datetime | None = None,
    ) -> int:
        expected = f"TROCAR:{license_id}"
        if confirmation != expected:
            raise ConfirmationRequiredError(f"Confirmação obrigatória: {expected}")
        machine_id = _validate_machine_id(machine_id)
        reason = reason.strip()
        if not reason:
            raise ValueError("Informe o motivo da troca de computador.")
        now = datetime.now(UTC) if at is None else at.astimezone(UTC)
        issued_at = _timestamp(now)
        key_id, private_key = self._signing_identity()
        with self.database.transaction() as connection:
            row = self._license_row(connection, license_id)
            if row["machine_id"] == machine_id:
                raise InvalidTransitionError("A licença já está vinculada a esse computador.")
            revision = int(row["current_revision"]) + 1
            features = self._features(connection, license_id)
            payload = LicensePayload(
                key_id=key_id,
                license_id=license_id,
                revision=revision,
                machine_id=machine_id,
                issued_at=issued_at,
                not_before=issued_at,
                expires_at=row["expires_at"],
                features=features,
                customer_reference=row["customer_reference"],
            )
            document = issue_license(payload, private_key)
            digest = hashlib.sha256(document).hexdigest()
            connection.execute(
                """UPDATE licenses SET key_id = ?, current_revision = ?, machine_id = ?,
                                       issued_at = ?, not_before = ? WHERE license_id = ?""",
                (key_id, revision, machine_id, issued_at, issued_at, license_id),
            )
            self._insert_revision(
                connection,
                payload,
                "replace_device",
                int(row["term_months"]),
                document,
                digest,
                admin_user_id,
            )
            self._refresh_search(connection, license_id)
            self._audit(
                connection,
                "license.device_replaced",
                "license",
                license_id,
                admin_user_id,
                {
                    "revision": revision,
                    "old_machine_id": row["machine_id"],
                    "new_machine_id": machine_id,
                    "reason": reason,
                },
            )
        return revision

    def export_license(
        self, license_id: str, destination: str | Path, *, admin_user_id: str | None = None
    ) -> Path:
        target = Path(destination).expanduser().resolve()
        if target.suffix.lower() != LICENSE_FILE_SUFFIX:
            raise ValueError(f"A licença deve usar a extensão {LICENSE_FILE_SUFFIX}.")
        with self.database.transaction() as connection:
            row = connection.execute(
                """SELECT r.* FROM license_revisions r
                   JOIN licenses l ON l.license_id = r.license_id AND l.current_revision = r.revision
                   WHERE r.license_id = ?""",
                (license_id,),
            ).fetchone()
            if not row:
                raise RecordNotFoundError("Licença não encontrada.")
            document = bytes(row["document"])
            if hashlib.sha256(document).hexdigest() != row["document_sha256"]:
                raise AdminLicenseError("O documento assinado armazenado está corrompido.")
            self._atomic_write(target, document)
            export_id = _identifier("EXP")
            connection.execute(
                """INSERT INTO offline_exports(export_id, license_id, revision, document_sha256,
                                                exported_at, admin_user_id)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    export_id,
                    license_id,
                    row["revision"],
                    row["document_sha256"],
                    utc_now_text(),
                    admin_user_id,
                ),
            )
            self._audit(
                connection,
                "license.exported",
                "license",
                license_id,
                admin_user_id,
                {"revision": row["revision"], "document_sha256": row["document_sha256"]},
            )
        return target

    def get_license(self, license_id: str) -> dict[str, Any]:
        with self.database.read() as connection:
            row = connection.execute(
                """SELECT l.*, c.name AS customer_name, c.email AS customer_email,
                          c.phone AS customer_phone, c.tax_id AS customer_tax_id,
                          c.commercial_reference AS customer_commercial_reference
                   FROM licenses l JOIN customers c ON c.customer_id = l.customer_id
                   WHERE l.license_id = ?""",
                (license_id,),
            ).fetchone()
            if not row:
                raise RecordNotFoundError("Licença não encontrada.")
            result = dict(row)
            result["features"] = list(self._features(connection, license_id))
            result["effective_status"] = self._effective_status(row["expires_at"])
            result["active_device"] = {"machine_id": row["machine_id"]}
            result["customer"] = {
                "customer_id": row["customer_id"],
                "name": row["customer_name"],
                "email": row["customer_email"],
                "phone": row["customer_phone"],
                "tax_id": row["customer_tax_id"],
                "commercial_reference": row["customer_commercial_reference"],
            }
            return result

    def license_detail(self, license_id: str) -> dict[str, Any]:
        result = self.get_license(license_id)
        with self.database.read() as connection:
            revisions = connection.execute(
                """SELECT revision, operation, key_id, machine_id, issued_at, not_before,
                          expires_at, term_months, document_sha256, admin_user_id
                   FROM license_revisions WHERE license_id = ? ORDER BY revision DESC""",
                (license_id,),
            ).fetchall()
            exports = connection.execute(
                """SELECT export_id, revision, document_sha256, exported_at, admin_user_id
                   FROM offline_exports WHERE license_id = ? ORDER BY exported_at DESC""",
                (license_id,),
            ).fetchall()
        result["revisions"] = [dict(row) for row in revisions]
        result["exports"] = [dict(row) for row in exports]
        result["history"] = self.history(license_id)
        result["available_actions"] = ["renew", "replace_device", "export"]
        return result

    def history(self, license_id: str) -> list[dict[str, Any]]:
        with self.database.read() as connection:
            exists = connection.execute(
                "SELECT 1 FROM licenses WHERE license_id = ?", (license_id,)
            ).fetchone()
            if not exists:
                raise RecordNotFoundError("Licença não encontrada.")
            rows = connection.execute(
                """SELECT action, occurred_at, admin_user_id, details_json, event_hash
                   FROM audit_events WHERE entity_type = 'license' AND entity_id = ?
                   ORDER BY rowid DESC""",
                (license_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def search_licenses(self, query: str, *, page: int = 1, page_size: int = 25) -> dict[str, Any]:
        if page < 1 or page_size < 1 or page_size > 100:
            raise ValueError("Paginação inválida.")
        terms = query.strip().split()
        if not terms:
            return {"items": [], "total": 0, "page": page, "page_size": page_size}
        filters: list[str] = []
        values: list[Any] = []
        free: list[str] = []
        now = utc_now_text()
        for term in terms:
            lowered = term.casefold()
            if lowered.startswith("status:"):
                status = lowered.split(":", 1)[1]
                if status in {"valida", "ativa", "valid"}:
                    filters.append("datetime(l.expires_at) > datetime(?, '+30 days')")
                    values.append(now)
                elif status in {"expirando", "a_vencer"}:
                    filters.append(
                        "datetime(l.expires_at) > datetime(?) "
                        "AND datetime(l.expires_at) <= datetime(?, '+30 days')"
                    )
                    values.extend((now, now))
                elif status in {"expirada", "expired"}:
                    filters.append("datetime(l.expires_at) <= datetime(?)")
                    values.append(now)
                else:
                    raise ValueError("Status de busca inválido.")
            elif lowered.startswith("maquina:"):
                filters.append("l.machine_id LIKE ?")
                values.append(f"%{term.split(':', 1)[1].upper()}%")
            elif lowered.startswith("cliente:"):
                filters.append("c.normalized_name LIKE ?")
                values.append(f"%{_normalize_name(term.split(':', 1)[1])}%")
            elif lowered.startswith("expira:") and lowered.endswith("d"):
                try:
                    days = int(lowered[7:-1])
                except ValueError as error:
                    raise ValueError("Filtro expira deve usar o formato expira:30d.") from error
                if days < 0 or days > 3650:
                    raise ValueError("Período do filtro expira inválido.")
                filters.append(
                    "datetime(l.expires_at) > datetime(?) "
                    "AND datetime(l.expires_at) <= datetime(?, ?)"
                )
                values.extend((now, now, f"+{days} days"))
            else:
                free.append(term)
        if free:
            pattern = f"%{_normalize_name(' '.join(free))}%"
            filters.append(
                "(lower(l.license_id) LIKE ? OR c.normalized_name LIKE ? "
                "OR lower(l.machine_id) LIKE ? OR lower(l.customer_reference) LIKE ?)"
            )
            values.extend((pattern, pattern, pattern, pattern))
        where = " AND ".join(filters) or "1 = 1"
        offset = (page - 1) * page_size
        with self.database.read() as connection:
            total = connection.execute(
                f"SELECT COUNT(*) FROM licenses l JOIN customers c ON c.customer_id = l.customer_id WHERE {where}",
                values,
            ).fetchone()[0]
            rows = connection.execute(
                f"""SELECT l.license_id, c.name AS customer_name, l.machine_id, l.expires_at,
                           l.current_revision,
                           CASE WHEN datetime(l.expires_at) <= datetime(?) THEN 'expired'
                                WHEN datetime(l.expires_at) <= datetime(?, '+30 days') THEN 'expiring'
                                ELSE 'valid' END AS effective_status
                    FROM licenses l JOIN customers c ON c.customer_id = l.customer_id
                    WHERE {where} ORDER BY l.expires_at, l.license_id LIMIT ? OFFSET ?""",
                [now, now, *values, page_size, offset],
            ).fetchall()
        return {"items": [dict(row) for row in rows], "total": total, "page": page, "page_size": page_size}

    def dashboard(self) -> dict[str, Any]:
        now = utc_now_text()
        with self.database.read() as connection:
            counts = connection.execute(
                """SELECT SUM(
                          CASE WHEN datetime(expires_at) > datetime(?, '+30 days') THEN 1 ELSE 0 END
                          ) AS valid,
                          SUM(
                          CASE WHEN datetime(expires_at) > datetime(?)
                                 AND datetime(expires_at) <= datetime(?, '+30 days')
                               THEN 1 ELSE 0 END
                          ) AS expiring,
                          SUM(CASE WHEN datetime(expires_at) <= datetime(?) THEN 1 ELSE 0 END) AS expired
                   FROM licenses""",
                (now, now, now, now),
            ).fetchone()
            upcoming = connection.execute(
                """SELECT l.license_id, c.name AS customer_name, l.expires_at
                   FROM licenses l JOIN customers c ON c.customer_id = l.customer_id
                   WHERE datetime(l.expires_at) > datetime(?)
                     AND datetime(l.expires_at) <= datetime(?, '+30 days')
                   ORDER BY l.expires_at LIMIT 10""",
                (now, now),
            ).fetchall()
            recent = connection.execute(
                """SELECT action, entity_id, occurred_at FROM audit_events
                   WHERE entity_type IN ('license', 'customer') ORDER BY rowid DESC LIMIT 10"""
            ).fetchall()
        return {
            "counts": {name: int(counts[name] or 0) for name in ("valid", "expiring", "expired")},
            "expiring_licenses": [dict(row) for row in upcoming],
            "recent_activity": [dict(row) for row in recent],
        }

    @staticmethod
    def _atomic_write(target: Path, document: bytes) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(document)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_name, target)
        finally:
            Path(temporary_name).unlink(missing_ok=True)

    def _signing_identity(self) -> tuple[str, Ed25519PrivateKey]:
        key_id = str(getattr(self.private_key_provider, "key_id", self.key_id))
        return key_id, self.private_key_provider()

    @staticmethod
    def _next_license_id(connection: Any, year: int) -> str:
        prefix = f"LIC-{year}-"
        rows = connection.execute(
            "SELECT license_id FROM licenses WHERE license_id LIKE ?", (f"{prefix}%",)
        ).fetchall()
        sequence = max((int(row[0].removeprefix(prefix)) for row in rows), default=0) + 1
        return f"{prefix}{sequence:06d}"

    @staticmethod
    def _license_row(connection: Any, license_id: str) -> Any:
        row = connection.execute("SELECT * FROM licenses WHERE license_id = ?", (license_id,)).fetchone()
        if not row:
            raise RecordNotFoundError("Licença não encontrada.")
        return row

    @staticmethod
    def _features(connection: Any, license_id: str) -> tuple[str, ...]:
        return tuple(
            row[0]
            for row in connection.execute(
                "SELECT feature FROM license_features WHERE license_id = ? ORDER BY feature", (license_id,)
            ).fetchall()
        )

    @staticmethod
    def _insert_revision(
        connection: Any,
        payload: LicensePayload,
        operation: str,
        term_months: int,
        document: bytes,
        digest: str,
        admin_user_id: str | None,
    ) -> None:
        connection.execute(
            """INSERT INTO license_revisions(license_id, revision, operation, key_id, machine_id,
                                             issued_at, not_before, expires_at, term_months,
                                             document, document_sha256, admin_user_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                payload.license_id,
                payload.revision,
                operation,
                payload.key_id,
                payload.machine_id,
                payload.issued_at,
                payload.not_before,
                payload.expires_at,
                term_months,
                document,
                digest,
                admin_user_id,
            ),
        )

    def _refresh_search(self, connection: Any, license_id: str) -> None:
        row = connection.execute(
            """SELECT l.license_id, l.machine_id, l.customer_reference, c.name, c.email,
                      c.tax_id, c.commercial_reference
               FROM licenses l JOIN customers c ON c.customer_id = l.customer_id
               WHERE l.license_id = ?""",
            (license_id,),
        ).fetchone()
        content = " ".join(str(value or "") for value in row)
        connection.execute("DELETE FROM license_search WHERE license_id = ?", (license_id,))
        connection.execute(
            "INSERT INTO license_search(license_id, content) VALUES (?, ?)", (license_id, content)
        )

    @staticmethod
    def _effective_status(expires_at: str) -> str:
        remaining = _parse_timestamp(expires_at) - datetime.now(UTC)
        if remaining.total_seconds() <= 0:
            return "expired"
        if remaining.days <= 30:
            return "expiring"
        return "valid"

    def _audit(
        self,
        connection: Any,
        action: str,
        entity_type: str,
        entity_id: str,
        admin_user_id: str | None,
        details: dict[str, Any],
    ) -> None:
        self.database.append_audit(
            connection,
            event_id=_identifier("EVT"),
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            admin_user_id=admin_user_id,
            details=details,
        )
