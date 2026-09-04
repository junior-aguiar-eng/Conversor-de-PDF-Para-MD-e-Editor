from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from admin_licensing import AdminDatabase, AdminLicenseService


class OfflineAdminSearchTests(unittest.TestCase):
    def test_search_dashboard_and_detail_expose_only_effective_time_states(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            service = AdminLicenseService(
                AdminDatabase(Path(temporary) / "admin.db", environment="test"),
                key_id="license-test-2026-01",
                private_key_provider=Ed25519PrivateKey.generate,
            )
            customer = service.create_customer("João da Silva")
            license_id = service.issue_license(
                customer,
                term_months=3,
                machine_id="NXJ2-1111-2222-3333-4444",
                at=datetime(2025, 9, 3, tzinfo=UTC),
            )
            result = service.search_licenses(license_id)
            self.assertEqual(result["total"], 1)
            self.assertEqual(result["items"][0]["effective_status"], "expired")
            self.assertEqual(service.dashboard()["counts"]["expired"], 1)
            detail = service.license_detail(license_id)
            self.assertEqual(detail["available_actions"], ["renew", "replace_device", "export"])
            self.assertNotIn("validation_mode", detail)


if __name__ == "__main__":
    unittest.main()
