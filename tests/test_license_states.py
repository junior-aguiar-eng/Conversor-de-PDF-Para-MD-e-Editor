"""Gate da Fase 2: estados, persistência e bloqueios de licença."""

from __future__ import annotations

import base64
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import licensing as licensing_module
import ocr_engine as ocr_engine_module
from license_core import (
    ClockTamperedError,
    ExpiredLicenseError,
    FeatureNotLicensedError,
    LicensePayload,
    LicenseState,
    LicenseStatus,
    MachineMismatchAccessError,
    OnlineCheckRequiredError,
    RevokedLicenseError,
    SuspendedLicenseError,
    UnlicensedAccessError,
    evaluate_act4,
    invalid_status,
    issue_license,
    require_feature,
    unlicensed_status,
)
from trusted_time import TemporalAssessment
from web_api import BridgeApi

MACHINE_ID = "NXJ2-1111-2222-3333-4444"


def _payload(**changes: object) -> LicensePayload:
    values: dict[str, object] = {
        "schema": "nexojuris-license/v4",
        "key_id": "license-main-2026-01",
        "license_id": "LIC-2026-000001",
        "machine_id": MACHINE_ID,
        "issued_at": "2026-09-01T00:00:00Z",
        "not_before": "2026-09-01T00:00:00Z",
        "expires_at": "2027-09-01T00:00:00Z",
        "validation_mode": "offline",
        "max_offline_days": 0,
        "features": ["converter", "ocr", "reader"],
        "customer_reference": "CLI-000001",
    }
    values.update(changes)
    return LicensePayload.from_mapping(values)


class LicenseStateMachineTests(unittest.TestCase):
    def test_valid_and_expiring_states(self) -> None:
        valid = evaluate_act4(_payload(), MACHINE_ID, at=datetime(2026, 10, 1, tzinfo=UTC))
        expiring = evaluate_act4(_payload(), MACHINE_ID, at=datetime(2027, 8, 10, tzinfo=UTC))
        self.assertEqual(valid.state, LicenseState.VALID)
        self.assertTrue(valid.can_use_protected_features)
        self.assertEqual(expiring.state, LicenseState.EXPIRING)
        self.assertTrue(expiring.can_use_protected_features)
        self.assertLessEqual(expiring.days_remaining or 99, 30)

    def test_all_blocking_states(self) -> None:
        payload = _payload()
        cases = (
            (
                LicenseState.EXPIRED,
                evaluate_act4(payload, MACHINE_ID, at=datetime(2027, 9, 1, tzinfo=UTC)),
                ExpiredLicenseError,
            ),
            (
                LicenseState.REVOKED,
                evaluate_act4(payload, MACHINE_ID, at=datetime(2026, 10, 1, tzinfo=UTC), online_status="revoked"),
                RevokedLicenseError,
            ),
            (
                LicenseState.SUSPENDED,
                evaluate_act4(payload, MACHINE_ID, at=datetime(2026, 10, 1, tzinfo=UTC), online_status="suspended"),
                SuspendedLicenseError,
            ),
            (
                LicenseState.CLOCK_TAMPERED,
                evaluate_act4(payload, MACHINE_ID, at=datetime(2026, 10, 1, tzinfo=UTC), clock_tampered=True),
                ClockTamperedError,
            ),
            (
                LicenseState.MACHINE_MISMATCH,
                evaluate_act4(payload, "NXJ2-AAAA-BBBB-CCCC-DDDD", at=datetime(2026, 10, 1, tzinfo=UTC)),
                MachineMismatchAccessError,
            ),
        )
        for expected_state, status, expected_error in cases:
            with self.subTest(state=expected_state):
                self.assertEqual(status.state, expected_state)
                self.assertFalse(status.can_use_protected_features)
                with self.assertRaises(expected_error):
                    require_feature(status, "converter")

    def test_hybrid_requires_current_lease(self) -> None:
        payload = _payload(validation_mode="hybrid", max_offline_days=7)
        now = datetime(2026, 10, 1, tzinfo=UTC)
        required = evaluate_act4(payload, MACHINE_ID, at=now)
        self.assertEqual(required.state, LicenseState.ONLINE_CHECK_REQUIRED)
        with self.assertRaises(OnlineCheckRequiredError):
            require_feature(required, "converter")
        valid = evaluate_act4(payload, MACHINE_ID, at=now, offline_until=datetime(2026, 10, 8, tzinfo=UTC))
        self.assertEqual(valid.state, LicenseState.VALID)

    def test_feature_absence_has_specific_error(self) -> None:
        status = evaluate_act4(
            _payload(features=["reader"]),
            MACHINE_ID,
            at=datetime(2026, 10, 1, tzinfo=UTC),
        )
        self.assertTrue(status.can_use_protected_features)
        self.assertTrue(status.allows("reader"))
        with self.assertRaises(FeatureNotLicensedError):
            require_feature(status, "ocr")

    def test_unlicensed_and_invalid_states_have_specific_errors(self) -> None:
        unlicensed = unlicensed_status(MACHINE_ID)
        invalid = invalid_status(MACHINE_ID)
        with self.assertRaises(UnlicensedAccessError):
            require_feature(unlicensed, "converter")
        with self.assertRaisesRegex(PermissionError, "inválida"):
            require_feature(invalid, "converter")

    def test_status_mapping_contains_complete_contract(self) -> None:
        status = evaluate_act4(_payload(), MACHINE_ID, at=datetime(2026, 10, 1, tzinfo=UTC))
        self.assertEqual(
            set(status.to_mapping()),
            {
                "state",
                "can_use_protected_features",
                "license_id",
                "license_format",
                "machine_id",
                "expires_at",
                "days_remaining",
                "offline_until",
                "features",
                "message",
            },
        )


class Act4ClientIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.private_key = Ed25519PrivateKey.generate()
        public_raw = self.private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        public_b64 = base64.b64encode(public_raw).decode("ascii")
        self.now = datetime(2026, 10, 1, tzinfo=UTC)
        assessment = TemporalAssessment(False, self.now, self.now, None)
        guard = MagicMock()
        guard.observe.return_value = assessment
        self.patchers = [
            patch.object(licensing_module, "_LICENSE_DB_PATH", self.root / "license.db"),
            patch.object(licensing_module, "_LICENSE_BACKUP_PATH", self.root / "license.sig"),
            patch.object(licensing_module, "_LICENSE_TIME_STATE_PATH", self.root / "license-time.dat"),
            patch.object(licensing_module, "_ACT4_PUBLIC_KEYS_B64", {"license-main-2026-01": public_b64}),
            patch.object(licensing_module, "get_machine_fingerprint", return_value=MACHINE_ID),
            patch.object(licensing_module, "get_machine_fingerprint_v1", return_value="NXJ-AAAA-BBBB-CCCC-DDDD"),
            patch.object(licensing_module, "get_machine_fingerprint_v2", return_value=MACHINE_ID),
            patch.object(licensing_module, "_temporal_guard", return_value=guard),
        ]
        for patcher in self.patchers:
            patcher.start()
            self.addCleanup(patcher.stop)

    def _document(self, **changes: object) -> bytes:
        return issue_license(_payload(**changes), self.private_key)

    def test_import_persists_act4_and_returns_complete_status(self) -> None:
        result = licensing_module.activate_act4_license(self._document(), now=self.now)
        self.assertTrue(result["ok"])
        status = licensing_module.get_license_status(now=self.now)
        self.assertEqual(status.state, LicenseState.VALID)
        self.assertEqual(status.license_id, "LIC-2026-000001")
        self.assertTrue(status.allows("converter"))
        backup = json.loads((self.root / "license.sig").read_text(encoding="utf-8"))
        self.assertEqual(backup["format"], "nexojuris-license-backup/v2")
        self.assertEqual(backup["license_format"], "act4")

    def test_database_migration_preserves_legacy_row(self) -> None:
        with closing(sqlite3.connect(self.root / "license.db")) as connection:
            connection.execute(
                """
                CREATE TABLE system_license (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    machine_id TEXT NOT NULL,
                    activation_key TEXT NOT NULL,
                    activated_at REAL NOT NULL
                )
                """
            )
            connection.execute(
                "INSERT INTO system_license VALUES (1, 'NXJ-AAAA-BBBB-CCCC-DDDD', 'ACT3-TEST', 1.0)"
            )
            connection.commit()
        licensing_module._init_license_table()
        with closing(sqlite3.connect(self.root / "license.db")) as connection:
            columns = {row[1] for row in connection.execute("PRAGMA table_info(system_license)")}
            row = connection.execute("SELECT machine_id, activation_key, license_format FROM system_license").fetchone()
        self.assertIn("license_document", columns)
        self.assertEqual(row, ("NXJ-AAAA-BBBB-CCCC-DDDD", "ACT3-TEST", "legacy"))

    def test_act4_backup_recovers_when_database_is_absent(self) -> None:
        self.assertTrue(licensing_module.activate_act4_license(self._document(), now=self.now)["ok"])
        (self.root / "license.db").unlink()
        status = licensing_module.get_license_status(now=self.now)
        self.assertEqual(status.state, LicenseState.VALID)

    def test_temporal_rollback_surfaces_clock_tampered_state(self) -> None:
        self.assertTrue(licensing_module.activate_act4_license(self._document(), now=self.now)["ok"])
        assessment = TemporalAssessment(True, self.now, self.now, None)
        guard = MagicMock()
        guard.observe.return_value = assessment
        with patch.object(licensing_module, "_temporal_guard", return_value=guard):
            status = licensing_module.get_license_status(now=self.now)
        self.assertEqual(status.state, LicenseState.CLOCK_TAMPERED)
        self.assertFalse(status.can_use_protected_features)

    def test_expired_act4_is_rejected_before_storage(self) -> None:
        result = licensing_module.activate_act4_license(
            self._document(expires_at="2026-09-30T00:00:00Z"),
            now=self.now,
        )
        self.assertFalse(result["ok"])
        self.assertFalse((self.root / "license.db").exists())

    def test_protected_write_is_blocked_before_resource_resolution(self) -> None:
        expired = LicenseStatus(
            state=LicenseState.EXPIRED,
            machine_id=MACHINE_ID,
            message="A licença expirou.",
            license_id="LIC-2026-000001",
            license_format="act4",
            expires_at="2026-09-01T00:00:00Z",
            days_remaining=0,
            features=("reader",),
        )
        api = BridgeApi.__new__(BridgeApi)
        api._resolve_pdf = MagicMock()
        with patch("web_api.require_software_activation", side_effect=ExpiredLicenseError(expired)):
            result = api.rotate_pdf_page("file-id", 0, 90)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_code"], "expired")
        api._resolve_pdf.assert_not_called()

    def test_ocr_is_blocked_before_engine_initialization(self) -> None:
        expired = LicenseStatus(
            state=LicenseState.EXPIRED,
            machine_id=MACHINE_ID,
            message="A licença expirou.",
            license_id="LIC-2026-000001",
            license_format="act4",
            expires_at="2026-09-01T00:00:00Z",
            days_remaining=0,
            features=("ocr",),
        )
        with (
            patch.object(
                ocr_engine_module,
                "require_software_activation",
                side_effect=ExpiredLicenseError(expired),
            ),
            patch.object(ocr_engine_module, "get_ocr_engine") as engine,
        ):
            with self.assertRaises(ExpiredLicenseError):
                ocr_engine_module.ocr_pixmap(MagicMock())
        engine.assert_not_called()


if __name__ == "__main__":
    unittest.main()
