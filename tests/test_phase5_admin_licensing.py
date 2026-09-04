from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from admin_licensing import AdminDatabase, AdminLicenseService, ConfirmationRequiredError
from license_core import PublicKeyRing, verify_license


class OfflineAdminLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.key = Ed25519PrivateKey.generate()
        self.database = AdminDatabase(self.root / "admin.db", environment="test")
        self.service = AdminLicenseService(
            self.database, key_id="license-test-2026-01", private_key_provider=lambda: self.key
        )
        self.admin = self.service.create_admin_user("owner", "Owner", role="owner")
        self.customer = self.service.create_customer("Cliente Teste", admin_user_id=self.admin)

    def test_issue_renew_replace_and_reexport_preserve_license_id(self) -> None:
        issued_at = datetime(2026, 9, 3, 12, tzinfo=UTC)
        license_id = self.service.issue_license(
            self.customer,
            term_months=3,
            machine_id="NXJ2-1111-2222-3333-4444",
            admin_user_id=self.admin,
            at=issued_at,
        )
        first_bytes = self.service.export_license(license_id, self.root / "first.nxjlic").read_bytes()
        self.assertEqual(self.service.renew_license(license_id, term_months=6, at=issued_at), 2)
        with self.assertRaises(ConfirmationRequiredError):
            self.service.replace_device(
                license_id,
                "NXJ2-AAAA-BBBB-CCCC-DDDD",
                confirmation="sim",
                reason="novo computador",
            )
        self.assertEqual(
            self.service.replace_device(
                license_id,
                "NXJ2-AAAA-BBBB-CCCC-DDDD",
                confirmation=f"TROCAR:{license_id}",
                reason="novo computador",
                at=issued_at,
            ),
            3,
        )
        current = self.service.export_license(license_id, self.root / "current.nxjlic")
        repeated = self.service.export_license(license_id, self.root / "repeated.nxjlic")
        self.assertEqual(current.read_bytes(), repeated.read_bytes())
        payload = verify_license(
            current.read_bytes(),
            PublicKeyRing({"license-test-2026-01": self.key.public_key()}),
            check_time=False,
        )
        self.assertEqual(payload.license_id, license_id)
        self.assertEqual(payload.revision, 3)
        self.assertEqual(payload.machine_id, "NXJ2-AAAA-BBBB-CCCC-DDDD")
        self.assertNotEqual(first_bytes, current.read_bytes())
        self.assertTrue(self.database.verify_audit_chain())

    def test_schema_contains_only_offline_license_domain(self) -> None:
        with self.database.read() as connection:
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertTrue({"licenses", "license_revisions", "offline_exports"}.issubset(tables))
        self.assertTrue({"online_leases", "license_status_history", "license_devices"}.isdisjoint(tables))


if __name__ == "__main__":
    unittest.main()
