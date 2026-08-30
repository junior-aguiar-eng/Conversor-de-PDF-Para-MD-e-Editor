"""API de domínio separada do adaptador HTTP/TLS."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import sqlite3
import threading
import uuid
from collections import defaultdict, deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlsplit

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from admin_licensing import AdminDatabase, AdminLicenseService
from admin_licensing.database import utc_now_text
from admin_licensing.security import verify_totp

from .protocol import LeasePayload, issue_lease, validate_nonce

_LICENSE_ID = re.compile(r"LIC-[0-9]{4}-[0-9]{6,12}")
_MACHINE_ID = re.compile(r"NXJ2-(?:[A-F0-9]{4}-){3}[A-F0-9]{4}")


@dataclass(frozen=True, slots=True)
class ApiResponse:
    status: int
    body: dict[str, Any]


class ApiRequestError(ValueError):
    def __init__(self, status: int, message: str) -> None:
        self.status = status
        super().__init__(message)


class SlidingWindowRateLimiter:
    def __init__(self, *, limit: int, window_seconds: int = 60) -> None:
        self.limit = limit
        self.window_seconds = window_seconds
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str, now: datetime) -> bool:
        moment = now.timestamp()
        threshold = moment - self.window_seconds
        with self._lock:
            events = self._events[key]
            while events and events[0] <= threshold:
                events.popleft()
            if len(events) >= self.limit:
                return False
            events.append(moment)
            return True


class LicenseServiceApi:
    """Implementa rotas públicas mínimas e rotas administrativas autenticadas."""

    def __init__(
        self,
        database: AdminDatabase,
        admin_service: AdminLicenseService,
        *,
        lease_key_id: str,
        lease_private_key_provider: Callable[[], Ed25519PrivateKey],
        public_rate_limit: int = 60,
        admin_rate_limit: int = 120,
        require_admin_totp: bool = False,
        admin_totp_secret_provider: Callable[[str], str] | None = None,
    ) -> None:
        self.database = database
        self.admin_service = admin_service
        self.lease_key_id = lease_key_id
        self.lease_private_key_provider = lease_private_key_provider
        self.public_limiter = SlidingWindowRateLimiter(limit=public_rate_limit)
        self.admin_limiter = SlidingWindowRateLimiter(limit=admin_rate_limit)
        if require_admin_totp and admin_totp_secret_provider is None:
            raise ValueError("O segundo fator administrativo requer um provedor de segredo TOTP.")
        self.require_admin_totp = require_admin_totp
        self.admin_totp_secret_provider = admin_totp_secret_provider

    def register_admin_token(
        self,
        token: str,
        *,
        label: str,
        admin_user_id: str | None = None,
        requires_totp: bool | None = None,
    ) -> str:
        if len(token) < 32:
            raise ValueError("O token administrativo deve possuir ao menos 32 caracteres.")
        token_id = f"TOK-{uuid.uuid4().hex.upper()}"
        effective_totp = self.require_admin_totp if requires_totp is None else requires_totp
        if effective_totp and admin_user_id is None:
            raise ValueError("Tokens com segundo fator devem pertencer a um usuário administrativo.")
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO admin_api_tokens(
                    token_id, token_hash, label, requires_totp, created_at, admin_user_id
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    token_id,
                    self._token_hash(token),
                    label.strip(),
                    int(effective_totp),
                    utc_now_text(),
                    admin_user_id,
                ),
            )
            self._audit(
                connection,
                "admin_token.created",
                "admin_token",
                token_id,
                admin_user_id,
                {"label": label.strip(), "requires_totp": effective_totp},
            )
        return token_id

    def revoke_admin_token(
        self,
        token_id: str,
        *,
        confirmation: str,
        admin_user_id: str | None = None,
    ) -> None:
        expected = f"REVOGAR-TOKEN:{token_id}"
        if confirmation != expected:
            raise PermissionError(f"Confirmação obrigatória: {expected}")
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT active FROM admin_api_tokens WHERE token_id = ?", (token_id,)
            ).fetchone()
            if not row or not row["active"]:
                raise ValueError("Token administrativo ativo não encontrado.")
            connection.execute(
                "UPDATE admin_api_tokens SET active = 0, revoked_at = ? WHERE token_id = ?",
                (utc_now_text(), token_id),
            )
            self._audit(
                connection,
                "admin_token.revoked",
                "admin_token",
                token_id,
                admin_user_id,
                {},
            )

    def handle(
        self,
        method: str,
        target: str,
        *,
        body: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        client_ip: str = "unknown",
        now: datetime | None = None,
    ) -> ApiResponse:
        instant = self._utc(now)
        parsed = urlsplit(target)
        path = parsed.path.rstrip("/") or "/"
        is_admin = path.startswith("/v1/admin/")
        limiter = self.admin_limiter if is_admin else self.public_limiter
        if not limiter.allow(f"{client_ip}:{'admin' if is_admin else 'public'}", instant):
            return ApiResponse(429, {"error": "rate_limit_exceeded", "message": "Limite de requisições excedido."})
        try:
            admin_user_id = self._authenticate(headers or {}, instant) if is_admin else None
            if method == "POST" and path in {
                "/v1/licenses/check",
                "/v1/licenses/activate",
                "/v1/licenses/refresh",
            }:
                return self._public_lease(path.rsplit("/", 1)[-1], body, instant, client_ip)
            if method == "POST" and path == "/v1/admin/licenses":
                return self._admin_create_license(body, admin_user_id)
            route = re.fullmatch(
                r"/v1/admin/licenses/(LIC-[0-9]{4}-[0-9]{6,12})/(renew|suspend|reactivate|revoke)", path
            )
            if method == "POST" and route:
                return self._admin_status_action(route.group(1), route.group(2), body, admin_user_id, instant)
            transfer = re.fullmatch(
                r"/v1/admin/licenses/(LIC-[0-9]{4}-[0-9]{6,12})/devices/transfer", path
            )
            if method == "POST" and transfer:
                return self._admin_transfer(transfer.group(1), body, admin_user_id)
            events = re.fullmatch(r"/v1/admin/licenses/(LIC-[0-9]{4}-[0-9]{6,12})/events", path)
            if method == "GET" and events:
                return ApiResponse(200, {"events": self.admin_service.history("license", events.group(1))})
            if method == "GET" and path == "/v1/admin/search":
                query = parse_qs(parsed.query, keep_blank_values=True)
                expression = query.get("q", [""])[0]
                page = int(query.get("page", ["1"])[0])
                page_size = int(query.get("page_size", ["25"])[0])
                return ApiResponse(200, self.admin_service.search_licenses(expression, page=page, page_size=page_size))
            return ApiResponse(404, {"error": "not_found", "message": "Endpoint não encontrado."})
        except ApiRequestError as error:
            return ApiResponse(error.status, {"error": self._error_code(error.status), "message": str(error)})
        except (ValueError, TypeError, RuntimeError) as error:
            return ApiResponse(400, {"error": "invalid_request", "message": str(error)})

    def _public_lease(
        self,
        operation: str,
        body: Mapping[str, Any] | None,
        now: datetime,
        client_ip: str,
    ) -> ApiResponse:
        allowed = {"license_id", "machine_id", "nonce"}
        if operation == "activate":
            allowed.add("activation_code")
        data = self._strict_body(body, allowed)
        license_id = str(data.get("license_id", "")).strip().upper()
        machine_id = str(data.get("machine_id", "")).strip().upper()
        nonce = validate_nonce(data.get("nonce"))
        if not _LICENSE_ID.fullmatch(license_id):
            raise ApiRequestError(400, "license_id possui formato inválido.")
        if not _MACHINE_ID.fullmatch(machine_id):
            raise ApiRequestError(400, "machine_id possui formato inválido.")
        activation_code = str(data.get("activation_code", ""))
        try:
            lease = self._evaluate_public_request(
                operation,
                license_id,
                machine_id,
                nonce,
                activation_code,
                now,
                client_ip,
            )
        except sqlite3.IntegrityError as error:
            if "request_nonces" in str(error) or "UNIQUE constraint failed: request_nonces" in str(error):
                raise ApiRequestError(409, "Nonce já utilizado; gere uma nova solicitação.") from error
            raise
        return ApiResponse(200, lease)

    def _evaluate_public_request(
        self,
        operation: str,
        license_id: str,
        machine_id: str,
        nonce: str,
        activation_code: str,
        now: datetime,
        client_ip: str,
    ) -> dict[str, str]:
        server_time = self._timestamp(now)
        nonce_hash = hashlib.sha256(nonce.encode("ascii")).hexdigest()
        with self.database.transaction() as connection:
            connection.execute("DELETE FROM request_nonces WHERE expires_at <= ?", (server_time,))
            connection.execute(
                "INSERT INTO request_nonces(nonce_hash, scope, seen_at, expires_at) VALUES (?, ?, ?, ?)",
                (nonce_hash, operation, server_time, self._timestamp(now + timedelta(hours=24))),
            )
            row = connection.execute(
                """
                SELECT l.*, d.device_id, d.machine_id
                FROM licenses l
                LEFT JOIN licensed_devices d ON d.license_id = l.license_id AND d.active = 1
                WHERE l.license_id = ?
                """,
                (license_id,),
            ).fetchone()
            status = "not_found"
            entitlement = server_time
            lease_expiration = server_time
            device_id = None
            if row:
                entitlement = row["expires_at"]
                device_id = row["device_id"]
                if operation == "activate":
                    expected_hash = row["activation_secret_hash"]
                    supplied_hash = hashlib.sha256(activation_code.encode("utf-8")).hexdigest()
                    if not expected_hash or not hmac.compare_digest(expected_hash, supplied_hash):
                        row = None
                        status = "not_found"
                        entitlement = server_time
                    elif row["machine_id"] is None:
                        device_id = f"DEV-{uuid.uuid4().hex.upper()}"
                        connection.execute(
                            """
                            INSERT INTO licensed_devices(device_id, license_id, machine_id, bound_at, active)
                            VALUES (?, ?, ?, ?, 1)
                            """,
                            (device_id, license_id, machine_id, server_time),
                        )
                        self.admin_service._refresh_search(connection, license_id)
                        self._audit(
                            connection,
                            "device.activated_online",
                            "license",
                            license_id,
                            None,
                            {"device_id": device_id, "machine_id": machine_id},
                        )
                    elif row["machine_id"] != machine_id:
                        status = "machine_mismatch"
                elif row["machine_id"] != machine_id:
                    status = "machine_mismatch"
                if row and status != "machine_mismatch":
                    if row["status"] in {"suspended", "revoked"}:
                        status = row["status"]
                    elif row["expires_at"] <= server_time:
                        status = "expired"
                    else:
                        status = "active"
                        offline_days = int(row["max_offline_days"] or 1)
                        lease_end = min(
                            datetime.strptime(row["expires_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC),
                            now + timedelta(days=offline_days),
                        )
                        lease_expiration = self._timestamp(lease_end)
            lease_key_id = self._active_lease_key_id()
            payload = LeasePayload(
                key_id=lease_key_id,
                license_id=license_id,
                status=status,
                server_time=server_time,
                entitlement_expires_at=entitlement,
                lease_expires_at=lease_expiration,
                nonce=nonce,
            )
            document = issue_lease(payload, self._lease_private_key_for(lease_key_id))
            if status == "active" and device_id:
                connection.execute(
                    "UPDATE online_leases SET status = 'expired' WHERE license_id = ? AND status = 'active'",
                    (license_id,),
                )
                connection.execute(
                    """
                    INSERT INTO online_leases(
                        lease_id, license_id, device_id, issued_at, expires_at,
                        last_seen_at, status, nonce_hash, key_id, lease_document
                    ) VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?, ?)
                    """,
                    (
                        f"LEA-{uuid.uuid4().hex.upper()}",
                        license_id,
                        device_id,
                        server_time,
                        lease_expiration,
                        server_time,
                        nonce_hash,
                        lease_key_id,
                        json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8"),
                    ),
                )
            self._audit(
                connection,
                f"license.{operation}_online",
                "license",
                license_id,
                None,
                {"status": status, "client_ip_hash": hashlib.sha256(client_ip.encode()).hexdigest()},
            )
        return document

    def _admin_create_license(self, body: Mapping[str, Any] | None, admin_user_id: str | None) -> ApiResponse:
        allowed = {
            "name",
            "email",
            "phone",
            "tax_id",
            "commercial_reference",
            "term_months",
            "features",
            "validation_mode",
            "max_offline_days",
            "machine_id",
        }
        data = self._strict_body(body, allowed)
        activation_code = secrets.token_urlsafe(32)
        customer_id = self.admin_service.create_customer(
            str(data.get("name", "")),
            email=data.get("email"),
            phone=data.get("phone"),
            tax_id=data.get("tax_id"),
            commercial_reference=data.get("commercial_reference"),
            admin_user_id=admin_user_id,
        )
        license_id = self.admin_service.issue_license(
            customer_id,
            term_months=int(data.get("term_months", 0)),
            features=tuple(data.get("features") or ("converter", "ocr", "reader")),
            validation_mode=str(data.get("validation_mode", "hybrid")),
            max_offline_days=int(data.get("max_offline_days", 7)),
            machine_id=data.get("machine_id"),
            commercial_reference=data.get("commercial_reference"),
            activation_secret=activation_code,
            admin_user_id=admin_user_id,
        )
        return ApiResponse(
            201,
            {"customer_id": customer_id, "license_id": license_id, "activation_code": activation_code},
        )

    def _admin_status_action(
        self,
        license_id: str,
        action: str,
        body: Mapping[str, Any] | None,
        admin_user_id: str | None,
        now: datetime,
    ) -> ApiResponse:
        if action == "renew":
            data = self._strict_body(body, {"term_months"})
            result_id = self.admin_service.renew_license(
                license_id,
                term_months=int(data.get("term_months", 0)),
                admin_user_id=admin_user_id,
                now=now,
            )
            expires_at = self.admin_service.get_license(license_id)["expires_at"]
            return ApiResponse(200, {"id": result_id, "expires_at": expires_at})
        elif action == "suspend":
            data = self._strict_body(body, {"reason"})
            result_id = self.admin_service.suspend_license(
                license_id, str(data.get("reason", "")), admin_user_id=admin_user_id
            )
        elif action == "reactivate":
            data = self._strict_body(body, {"reason"})
            result_id = self.admin_service.reactivate_license(
                license_id, str(data.get("reason", "")), admin_user_id=admin_user_id
            )
        else:
            data = self._strict_body(body, {"reason", "confirmation"})
            result_id = self.admin_service.revoke_license(
                license_id,
                str(data.get("reason", "")),
                confirmation=str(data.get("confirmation", "")),
                admin_user_id=admin_user_id,
            )
        return ApiResponse(200, {"id": result_id})

    def _admin_transfer(
        self,
        license_id: str,
        body: Mapping[str, Any] | None,
        admin_user_id: str | None,
    ) -> ApiResponse:
        data = self._strict_body(body, {"machine_id", "reason", "confirmation"})
        device_id = self.admin_service.replace_device(
            license_id,
            str(data.get("machine_id", "")),
            reason=str(data.get("reason", "")),
            confirmation=str(data.get("confirmation", "")),
            admin_user_id=admin_user_id,
        )
        return ApiResponse(200, {"device_id": device_id})

    def _authenticate(self, headers: Mapping[str, str], now: datetime) -> str | None:
        authorization = next((value for key, value in headers.items() if key.casefold() == "authorization"), "")
        if not authorization.startswith("Bearer "):
            raise ApiRequestError(401, "Autenticação administrativa obrigatória.")
        token = authorization[7:]
        if len(token) < 32:
            raise ApiRequestError(401, "Token administrativo inválido.")
        token_hash = self._token_hash(token)
        with self.database.transaction() as connection:
            row = connection.execute(
                """
                SELECT token.token_id, token.admin_user_id, token.requires_totp
                FROM admin_api_tokens token
                LEFT JOIN admin_users admin ON admin.admin_user_id = token.admin_user_id
                WHERE token.token_hash = ? AND token.active = 1
                  AND (token.admin_user_id IS NULL OR admin.active = 1)
                """,
                (token_hash,),
            ).fetchone()
            if not row:
                raise ApiRequestError(401, "Token administrativo inválido.")
            if self.require_admin_totp or row["requires_totp"]:
                if row["admin_user_id"] is None or self.admin_totp_secret_provider is None:
                    raise ApiRequestError(401, "Segundo fator administrativo indisponível.")
                supplied_code = next(
                    (value for key, value in headers.items() if key.casefold() == "x-nexojuris-totp"), ""
                )
                secret = self.admin_totp_secret_provider(str(row["admin_user_id"]))
                if not verify_totp(secret, supplied_code, at=now):
                    raise ApiRequestError(401, "Segundo fator administrativo inválido.")
            connection.execute(
                "UPDATE admin_api_tokens SET last_used_at = ? WHERE token_id = ?",
                (self._timestamp(now), row["token_id"]),
            )
        return row["admin_user_id"]

    @staticmethod
    def _strict_body(body: Mapping[str, Any] | None, allowed: set[str]) -> Mapping[str, Any]:
        if not isinstance(body, Mapping):
            raise ApiRequestError(400, "O corpo JSON deve ser um objeto.")
        extra = set(body) - allowed
        if extra:
            raise ApiRequestError(400, f"Campos não permitidos: {', '.join(sorted(extra))}.")
        return body

    @staticmethod
    def _token_hash(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def _active_lease_key_id(self) -> str:
        dynamic_key_id = getattr(self.lease_private_key_provider, "key_id", None)
        return str(dynamic_key_id or self.lease_key_id)

    def _lease_private_key_for(self, key_id: str) -> Ed25519PrivateKey:
        resolver = getattr(self.lease_private_key_provider, "for_key", None)
        if callable(resolver):
            return resolver(key_id)
        if key_id != self.lease_key_id:
            raise RuntimeError(f"A chave privada {key_id} não está disponível para emissão de lease.")
        return self.lease_private_key_provider()

    @staticmethod
    def _utc(value: datetime | None) -> datetime:
        instant = datetime.now(UTC) if value is None else value
        if instant.tzinfo is None or instant.utcoffset() is None:
            raise ValueError("O horário da API deve possuir fuso horário.")
        return instant.astimezone(UTC).replace(microsecond=0)

    @staticmethod
    def _timestamp(value: datetime) -> str:
        return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    @staticmethod
    def _error_code(status: int) -> str:
        return {400: "invalid_request", 401: "unauthorized", 409: "replay_detected"}.get(status, "request_error")

    def _audit(
        self,
        connection: sqlite3.Connection,
        action: str,
        entity_type: str,
        entity_id: str,
        admin_user_id: str | None,
        details: Mapping[str, Any],
    ) -> None:
        self.database.append_audit(
            connection,
            event_id=f"EVT-{uuid.uuid4().hex.upper()}",
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            admin_user_id=admin_user_id,
            details=details,
        )
