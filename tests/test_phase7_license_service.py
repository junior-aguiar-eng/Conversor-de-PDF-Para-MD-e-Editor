from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.error import URLError

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from admin_licensing import AdminDatabase, AdminLicenseService
from license_core import LicensePayload, LicenseState, evaluate_act4
from license_service import LeasePayload, LeaseSignatureError, issue_lease, verify_lease
from license_service.api import LicenseServiceApi
from license_service.server import serve_https
from online_license_client import LicenseServiceResponseError, LicenseServiceUnavailable, OnlineLicenseClient


class FakeHeaders:
    @staticmethod
    def get_content_type() -> str:
        return "application/json"


class FakeResponse:
    def __init__(self, document: dict[str, object]) -> None:
        self.document = document
        self.headers = FakeHeaders()

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self, _limit: int) -> bytes:
        return json.dumps(self.document, separators=(",", ":")).encode("utf-8")


class Phase7LeaseProtocolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.private_key = Ed25519PrivateKey.generate()
        self.payload = LeasePayload(
            key_id="lease-online-2026-01",
            license_id="LIC-2026-000001",
            status="active",
            server_time="2026-09-01T12:00:00Z",
            entitlement_expires_at="2027-09-01T00:00:00Z",
            lease_expires_at="2026-09-08T12:00:00Z",
            nonce="YWJjZGVmZ2hpamtsbW5vcHFyc3R1dnd4",
        )

    def test_roundtrip_assinado_com_chave_online_separada(self) -> None:
        document = issue_lease(self.payload, self.private_key)
        verified = verify_lease(
            document,
            {self.payload.key_id: self.private_key.public_key()},
            expected_nonce=self.payload.nonce,
            expected_license_id=self.payload.license_id,
        )
        self.assertEqual(verified, self.payload)
        self.assertTrue(self.payload.key_id.startswith("lease-"))

    def test_rejeita_assinatura_adulterada_e_nonce_divergente(self) -> None:
        document = issue_lease(self.payload, self.private_key)
        document["entitlement_expires_at"] = "2028-09-01T00:00:00Z"
        with self.assertRaises(LeaseSignatureError):
            verify_lease(
                document,
                {self.payload.key_id: self.private_key.public_key()},
                expected_nonce=self.payload.nonce,
                expected_license_id=self.payload.license_id,
            )
        document = issue_lease(self.payload, self.private_key)
        with self.assertRaisesRegex(ValueError, "nonce"):
            verify_lease(
                document,
                {self.payload.key_id: self.private_key.public_key()},
                expected_nonce=OnlineLicenseClient.generate_nonce(),
                expected_license_id=self.payload.license_id,
            )

    def test_entitlement_online_pode_prorrogar_documento_act4_antigo(self) -> None:
        license_payload = LicensePayload(
            key_id="license-main-2026-01",
            license_id="LIC-2026-000001",
            machine_id="NXJ2-1111-2222-3333-4444",
            issued_at="2026-01-01T00:00:00Z",
            not_before="2026-01-01T00:00:00Z",
            expires_at="2026-06-01T00:00:00Z",
            validation_mode="hybrid",
            max_offline_days=7,
            features=("converter",),
            customer_reference="CLI-000001",
        )
        status = evaluate_act4(
            license_payload,
            license_payload.machine_id,
            at=datetime(2026, 9, 1, tzinfo=UTC),
            offline_until=datetime(2026, 9, 8, tzinfo=UTC),
            entitlement_expires_at="2027-06-01T00:00:00Z",
        )
        self.assertEqual(status.state, LicenseState.VALID)
        self.assertEqual(status.expires_at, "2027-06-01T00:00:00Z")


class Phase7LicenseApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.database = AdminDatabase(self.root / "service.db")
        self.license_key = Ed25519PrivateKey.generate()
        self.lease_key = Ed25519PrivateKey.generate()
        self.admin_service = AdminLicenseService(
            self.database,
            key_id="license-main-2026-01",
            private_key_provider=lambda: self.license_key,
        )
        self.admin_id = self.admin_service.create_admin_user("api-admin", "API Admin", role="owner")
        self.api = LicenseServiceApi(
            self.database,
            self.admin_service,
            lease_key_id="lease-online-2026-01",
            lease_private_key_provider=lambda: self.lease_key,
        )
        self.admin_token = "admin-token-de-teste-com-mais-de-32-caracteres"
        self.api.register_admin_token(self.admin_token, label="test", admin_user_id=self.admin_id)
        self.admin_headers = {"Authorization": f"Bearer {self.admin_token}"}
        self.now = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)

    def create_online_license(self, *, term_months: int = 12) -> dict[str, object]:
        response = self.api.handle(
            "POST",
            "/v1/admin/licenses",
            headers=self.admin_headers,
            body={
                "name": "Cliente Online",
                "email": "online@example.test",
                "term_months": term_months,
                "features": ["converter", "ocr", "reader"],
                "validation_mode": "hybrid",
                "max_offline_days": 7,
            },
            now=self.now,
        )
        self.assertEqual(response.status, 201)
        return response.body

    @staticmethod
    def nonce() -> str:
        return OnlineLicenseClient.generate_nonce()

    def public_request(
        self,
        operation: str,
        license_id: str,
        machine_id: str,
        nonce: str,
        *,
        activation_code: str | None = None,
        now: datetime | None = None,
    ) -> object:
        body = {"license_id": license_id, "machine_id": machine_id, "nonce": nonce}
        if activation_code is not None:
            body["activation_code"] = activation_code
        return self.api.handle(
            "POST",
            f"/v1/licenses/{operation}",
            body=body,
            client_ip="203.0.113.10",
            now=now or self.now,
        )

    def verify_response(self, response: object, nonce: str, license_id: str) -> LeasePayload:
        return verify_lease(
            response.body,
            {"lease-online-2026-01": self.lease_key.public_key()},
            expected_nonce=nonce,
            expected_license_id=license_id,
        )

    def test_ativacao_refresh_e_replay(self) -> None:
        created = self.create_online_license()
        license_id = str(created["license_id"])
        activation_code = str(created["activation_code"])
        machine_id = "NXJ2-1111-2222-3333-4444"
        nonce = self.nonce()
        activated = self.public_request(
            "activate", license_id, machine_id, nonce, activation_code=activation_code
        )
        self.assertEqual(activated.status, 200)
        lease = self.verify_response(activated, nonce, license_id)
        self.assertEqual(lease.status, "active")
        self.assertEqual(lease.lease_expires_at, "2026-09-08T12:00:00Z")

        replay = self.public_request(
            "activate", license_id, machine_id, nonce, activation_code=activation_code
        )
        self.assertEqual(replay.status, 409)
        self.assertEqual(replay.body["error"], "replay_detected")

        refreshed_nonce = self.nonce()
        refreshed = self.public_request("refresh", license_id, machine_id, refreshed_nonce)
        self.assertEqual(self.verify_response(refreshed, refreshed_nonce, license_id).status, "active")
        with self.database.read() as connection:
            active_leases = connection.execute(
                "SELECT COUNT(*) FROM online_leases WHERE license_id = ? AND status = 'active'", (license_id,)
            ).fetchone()[0]
            stored_hash = connection.execute(
                "SELECT activation_secret_hash FROM licenses WHERE license_id = ?", (license_id,)
            ).fetchone()[0]
        self.assertEqual(active_leases, 1)
        self.assertEqual(stored_hash, hashlib.sha256(activation_code.encode()).hexdigest())
        self.assertNotEqual(stored_hash, activation_code)
        self.assertTrue(self.database.verify_audit_chain())

    def test_estados_inexistente_revogada_expirada_e_maquina_divergente(self) -> None:
        unknown_id = "LIC-2026-999999"
        unknown_nonce = self.nonce()
        unknown = self.public_request(
            "check", unknown_id, "NXJ2-1111-2222-3333-4444", unknown_nonce
        )
        self.assertEqual(self.verify_response(unknown, unknown_nonce, unknown_id).status, "not_found")

        created = self.create_online_license()
        license_id = str(created["license_id"])
        machine_id = "NXJ2-AAAA-BBBB-CCCC-DDDD"
        activation_nonce = self.nonce()
        self.public_request(
            "activate",
            license_id,
            machine_id,
            activation_nonce,
            activation_code=str(created["activation_code"]),
        )
        mismatch_nonce = self.nonce()
        mismatch = self.public_request(
            "check", license_id, "NXJ2-1111-2222-3333-4444", mismatch_nonce
        )
        self.assertEqual(self.verify_response(mismatch, mismatch_nonce, license_id).status, "machine_mismatch")

        self.admin_service.revoke_license(
            license_id,
            "teste de revogação",
            confirmation=f"REVOGAR:{license_id}",
            admin_user_id=self.admin_id,
        )
        revoked_nonce = self.nonce()
        revoked = self.public_request("check", license_id, machine_id, revoked_nonce)
        self.assertEqual(self.verify_response(revoked, revoked_nonce, license_id).status, "revoked")

        second = self.create_online_license(term_months=3)
        second_id = str(second["license_id"])
        second_machine = "NXJ2-1234-5678-9ABC-DEF0"
        self.public_request(
            "activate",
            second_id,
            second_machine,
            self.nonce(),
            activation_code=str(second["activation_code"]),
        )
        expired_nonce = self.nonce()
        expired = self.public_request(
            "check", second_id, second_machine, expired_nonce, now=datetime(2027, 1, 1, tzinfo=UTC)
        )
        self.assertEqual(self.verify_response(expired, expired_nonce, second_id).status, "expired")

    def test_separacao_admin_autenticacao_privacidade_e_rate_limit(self) -> None:
        unauthorized = self.api.handle("GET", "/v1/admin/search?q=teste")
        self.assertEqual(unauthorized.status, 401)
        privacy = self.api.handle(
            "POST",
            "/v1/licenses/check",
            body={
                "license_id": "LIC-2026-000001",
                "machine_id": "NXJ2-1111-2222-3333-4444",
                "nonce": self.nonce(),
                "content": "documento não permitido",
            },
            now=self.now,
        )
        self.assertEqual(privacy.status, 400)
        self.assertIn("content", privacy.body["message"])

        limited_api = LicenseServiceApi(
            self.database,
            self.admin_service,
            lease_key_id="lease-online-2026-01",
            lease_private_key_provider=lambda: self.lease_key,
            public_rate_limit=1,
        )
        body = {
            "license_id": "LIC-2026-999999",
            "machine_id": "NXJ2-1111-2222-3333-4444",
            "nonce": self.nonce(),
        }
        self.assertEqual(limited_api.handle("POST", "/v1/licenses/check", body=body, now=self.now).status, 200)
        body["nonce"] = self.nonce()
        self.assertEqual(limited_api.handle("POST", "/v1/licenses/check", body=body, now=self.now).status, 429)


class Phase7OnlineClientTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.lease_path = Path(self.temp.name) / "lease.json"
        self.private_key = Ed25519PrivateKey.generate()
        self.client = OnlineLicenseClient(
            "https://licenses.example.test",
            {"lease-online-2026-01": self.private_key.public_key()},
            lease_path=self.lease_path,
        )

    def response_for_request(self, request: object) -> dict[str, object]:
        request_body = json.loads(request.data)
        payload = LeasePayload(
            key_id="lease-online-2026-01",
            license_id=request_body["license_id"],
            status="active",
            server_time="2026-09-01T12:00:00Z",
            entitlement_expires_at="2027-09-01T00:00:00Z",
            lease_expires_at="2026-09-08T12:00:00Z",
            nonce=request_body["nonce"],
        )
        return issue_lease(payload, self.private_key)

    def test_cliente_https_valida_e_preserva_cache_na_indisponibilidade(self) -> None:
        def valid_response(request: object, timeout: float) -> FakeResponse:
            return FakeResponse(self.response_for_request(request))

        with patch("online_license_client.urlopen", side_effect=valid_response):
            lease = self.client.refresh("LIC-2026-000001", "NXJ2-1111-2222-3333-4444")
        self.assertEqual(lease.status, "active")
        cached_bytes = self.lease_path.read_bytes()
        self.assertEqual(self.client.load_cached(expected_license_id=lease.license_id), lease)

        with patch("online_license_client.urlopen", side_effect=URLError("offline")):
            with self.assertRaises(LicenseServiceUnavailable):
                self.client.refresh("LIC-2026-000001", "NXJ2-1111-2222-3333-4444")
        self.assertEqual(self.lease_path.read_bytes(), cached_bytes)

    def test_cliente_rejeita_assinatura_invalida_sem_substituir_cache(self) -> None:
        def valid_response(request: object, timeout: float) -> FakeResponse:
            return FakeResponse(self.response_for_request(request))

        with patch("online_license_client.urlopen", side_effect=valid_response):
            self.client.refresh("LIC-2026-000001", "NXJ2-1111-2222-3333-4444")
        cached_bytes = self.lease_path.read_bytes()

        def invalid_response(request: object, timeout: float) -> FakeResponse:
            document = self.response_for_request(request)
            document["signature"] = "A" * 86
            return FakeResponse(document)

        with patch("online_license_client.urlopen", side_effect=invalid_response):
            with self.assertRaises(LicenseServiceResponseError):
                self.client.refresh("LIC-2026-000001", "NXJ2-1111-2222-3333-4444")
        self.assertEqual(self.lease_path.read_bytes(), cached_bytes)

    def test_url_sem_tls_e_recusada(self) -> None:
        with self.assertRaisesRegex(ValueError, "HTTPS"):
            OnlineLicenseClient(
                "http://licenses.example.test",
                {"lease-online-2026-01": self.private_key.public_key()},
                lease_path=self.lease_path,
            )

    def test_servidor_configura_tls_sem_expor_modo_http(self) -> None:
        api = MagicMock()
        server = MagicMock()
        original_socket = MagicMock()
        wrapped_socket = MagicMock()
        server.socket = original_socket
        context = MagicMock()
        context.wrap_socket.return_value = wrapped_socket
        with (
            patch("license_service.server.ssl.SSLContext", return_value=context),
            patch("license_service.server.ThreadingHTTPServer", return_value=server),
        ):
            serve_https(
                api,
                host="127.0.0.1",
                port=8443,
                certificate="certificate.pem",
                private_key="tls-key.pem",
            )
        context.load_cert_chain.assert_called_once_with("certificate.pem", "tls-key.pem")
        context.wrap_socket.assert_called_once_with(original_socket, server_side=True)
        self.assertIs(server.socket, wrapped_socket)
        server.serve_forever.assert_called_once()


if __name__ == "__main__":
    unittest.main()
