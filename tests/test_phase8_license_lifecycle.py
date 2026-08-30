from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import licensing
from admin_licensing import AdminDatabase, AdminLicenseService
from license_core import LicenseState, LicenseStatus, PublicKeyRing, verify_license
from license_service import LeasePayload, verify_lease
from license_service.api import ApiResponse, LicenseServiceApi
from online_license_client import OnlineLicenseClient
from web_api import BridgeApi


class Phase8OnlineLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.database = AdminDatabase(self.root / "phase8.db")
        self.license_key = Ed25519PrivateKey.generate()
        self.lease_key = Ed25519PrivateKey.generate()
        self.service = AdminLicenseService(
            self.database,
            key_id="license-main-2026-01",
            private_key_provider=lambda: self.license_key,
        )
        self.admin_id = self.service.create_admin_user("phase8-admin", "Admin Fase 8", role="owner")
        self.api = LicenseServiceApi(
            self.database,
            self.service,
            lease_key_id="lease-online-2026-01",
            lease_private_key_provider=lambda: self.lease_key,
        )
        self.token = "token-administrativo-fase-8-com-entropia-suficiente"
        self.api.register_admin_token(self.token, label="phase8", admin_user_id=self.admin_id)
        self.headers = {"Authorization": f"Bearer {self.token}"}
        self.now = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)

    def admin(self, path: str, body: dict[str, object]) -> ApiResponse:
        return self.api.handle("POST", path, headers=self.headers, body=body, now=self.now)

    def public(self, operation: str, license_id: str, machine_id: str, **extra: str) -> LeasePayload:
        nonce = OnlineLicenseClient.generate_nonce()
        response = self.api.handle(
            "POST",
            f"/v1/licenses/{operation}",
            body={"license_id": license_id, "machine_id": machine_id, "nonce": nonce, **extra},
            now=self.now,
            client_ip="203.0.113.80",
        )
        self.assertEqual(response.status, 200)
        return verify_lease(
            response.body,
            {"lease-online-2026-01": self.lease_key.public_key()},
            expected_nonce=nonce,
            expected_license_id=license_id,
        )

    def test_renovacao_reativacao_revogacao_e_transferencia_ponta_a_ponta(self) -> None:
        created = self.admin(
            "/v1/admin/licenses",
            {
                "name": "Cliente Fase 8",
                "term_months": 3,
                "features": ["converter", "ocr", "reader"],
                "validation_mode": "hybrid",
                "max_offline_days": 7,
            },
        )
        self.assertEqual(created.status, 201)
        license_id = str(created.body["license_id"])
        old_machine = "NXJ2-1111-2222-3333-4444"
        new_machine = "NXJ2-AAAA-BBBB-CCCC-DDDD"
        initial = self.public(
            "activate",
            license_id,
            old_machine,
            activation_code=str(created.body["activation_code"]),
        )

        renewed = self.admin(f"/v1/admin/licenses/{license_id}/renew", {"term_months": 3})
        self.assertEqual(renewed.status, 200)
        refreshed = self.public("refresh", license_id, old_machine)
        self.assertGreater(refreshed.entitlement_expires_at, initial.entitlement_expires_at)
        self.assertEqual(refreshed.entitlement_expires_at, renewed.body["expires_at"])

        self.assertEqual(
            self.admin(f"/v1/admin/licenses/{license_id}/suspend", {"reason": "pagamento pendente"}).status,
            200,
        )
        self.assertEqual(self.public("check", license_id, old_machine).status, "suspended")
        self.assertEqual(
            self.admin(f"/v1/admin/licenses/{license_id}/reactivate", {"reason": "pagamento confirmado"}).status,
            200,
        )
        self.assertEqual(self.public("check", license_id, old_machine).status, "active")

        transferred = self.admin(
            f"/v1/admin/licenses/{license_id}/devices/transfer",
            {
                "machine_id": new_machine,
                "reason": "substituição do computador",
                "confirmation": f"TROCAR:{license_id}",
            },
        )
        self.assertEqual(transferred.status, 200)
        self.assertEqual(self.public("check", license_id, old_machine).status, "machine_mismatch")
        self.assertEqual(self.public("check", license_id, new_machine).status, "active")

        revoked = self.admin(
            f"/v1/admin/licenses/{license_id}/revoke",
            {"reason": "encerramento contratual", "confirmation": f"REVOGAR:{license_id}"},
        )
        self.assertEqual(revoked.status, 200)
        self.assertEqual(self.public("check", license_id, new_machine).status, "revoked")
        actions = [event["action"] for event in self.service.history("license", license_id)]
        self.assertIn("license.renewed", actions)
        self.assertIn("license.suspended", actions)
        self.assertIn("license.active", actions)
        self.assertIn("device.replaced", actions)
        self.assertIn("license.revoked", actions)
        self.assertTrue(self.database.verify_audit_chain())


class Phase8OfflineRenewalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.database = AdminDatabase(self.root / "offline.db")
        self.private_key = Ed25519PrivateKey.generate()
        self.service = AdminLicenseService(
            self.database,
            key_id="license-main-2026-01",
            private_key_provider=lambda: self.private_key,
        )
        self.admin_id = self.service.create_admin_user("offline-admin", "Admin Offline", role="owner")
        customer = self.service.create_customer("Cliente Offline", admin_user_id=self.admin_id)
        self.machine_id = "NXJ2-1234-5678-9ABC-DEF0"
        self.license_id = self.service.issue_license(
            customer,
            term_months=3,
            validation_mode="offline",
            max_offline_days=0,
            machine_id=self.machine_id,
            admin_user_id=self.admin_id,
        )

    def test_exportacao_renovada_substitui_act4_somente_depois_da_validacao(self) -> None:
        initial_path = self.service.export_license(
            self.license_id, self.root / "inicial.nxjlic", admin_user_id=self.admin_id
        )
        self.service.renew_license(self.license_id, term_months=6, admin_user_id=self.admin_id)
        renewed_path = self.service.export_license(
            self.license_id, self.root / "renovada.nxjlic", admin_user_id=self.admin_id
        )
        key_ring = PublicKeyRing({"license-main-2026-01": self.private_key.public_key()})
        initial = verify_license(initial_path.read_bytes(), key_ring, check_time=False)
        renewed = verify_license(renewed_path.read_bytes(), key_ring, check_time=False)
        self.assertGreater(renewed.expires_at, initial.expires_at)
        self.assertEqual(renewed.machine_id, self.machine_id)

        persisted: list[bytes] = []
        assessment = SimpleNamespace(
            effective_time=datetime.now(UTC),
            clock_tampered=False,
        )
        guard = MagicMock()
        guard.observe.return_value = assessment
        with (
            patch.object(licensing, "_act4_key_ring", return_value=key_ring),
            patch.object(licensing, "get_machine_fingerprint_v2", return_value=self.machine_id),
            patch.object(licensing, "_temporal_guard", return_value=guard),
            patch.object(
                licensing,
                "_save_act4_license",
                side_effect=lambda machine, document: persisted.append(document) or True,
            ),
        ):
            self.assertTrue(licensing.activate_act4_license(initial_path.read_bytes())["ok"])
            self.assertTrue(licensing.activate_act4_license(renewed_path.read_bytes())["ok"])
            tampered = renewed_path.read_bytes().replace(b'"validation_mode":"offline"', b'"validation_mode":"hybrid"')
            self.assertFalse(licensing.activate_act4_license(tampered)["ok"])
        self.assertEqual(persisted, [initial_path.read_bytes(), renewed_path.read_bytes()])


class Phase8AutomaticRefreshTests(unittest.TestCase):
    def test_consulta_diaria_e_estados_reversiveis_disparam_atualizacao(self) -> None:
        base = LicenseStatus(
            state=LicenseState.VALID,
            machine_id="NXJ2-1111-2222-3333-4444",
            message="Licença válida.",
            license_id="LIC-2026-000001",
            license_format="act4",
            validation_mode="hybrid",
            last_online_validation="2026-09-01T12:00:00Z",
        )
        self.assertFalse(licensing.online_refresh_is_due(base, now=datetime(2026, 9, 2, 11, 59, tzinfo=UTC)))
        self.assertTrue(licensing.online_refresh_is_due(base, now=datetime(2026, 9, 2, 12, 0, tzinfo=UTC)))
        expired = replace(base, state=LicenseState.EXPIRED)
        self.assertTrue(licensing.online_refresh_is_due(expired, now=datetime(2026, 9, 1, 12, 1, tzinfo=UTC)))

    def test_abertura_do_cliente_aplica_lease_renovado_automaticamente(self) -> None:
        expired = LicenseStatus(
            state=LicenseState.EXPIRED,
            machine_id="NXJ2-1111-2222-3333-4444",
            message="A licença expirou.",
            license_id="LIC-2026-000001",
            license_format="act4",
            validation_mode="hybrid",
        )
        renewed = LicenseStatus(
            state=LicenseState.VALID,
            machine_id=expired.machine_id,
            message="Licença válida.",
            license_id=expired.license_id,
            license_format="act4",
            validation_mode="hybrid",
            expires_at="2027-03-01T12:00:00Z",
            features=("converter",),
        )
        with (
            patch("web_api.get_license_status", side_effect=[expired, renewed]),
            patch("web_api.refresh_online_license", return_value={"ok": True}),
        ):
            result = BridgeApi.get_license_info(MagicMock())
        self.assertTrue(result["online_attempted"])
        self.assertTrue(result["is_activated"])
        self.assertEqual(result["expires_at"], renewed.expires_at)


if __name__ == "__main__":
    unittest.main()
