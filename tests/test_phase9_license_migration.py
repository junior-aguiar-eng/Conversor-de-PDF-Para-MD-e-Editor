from __future__ import annotations

import base64
import hashlib
import tempfile
import unittest
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import licensing
from admin_keygen import generate_activation_key
from admin_license_bridge import AdminLicenseBridge
from admin_licensing import AdminDatabase, AdminLicenseService, InvalidTransitionError
from license_core import (
    LicensePayload,
    LicenseState,
    PublicKeyRing,
    issue_license,
    verify_legacy_activation_key,
    verify_license,
)


def public_b64(private_key: Ed25519PrivateKey) -> str:
    raw = private_key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return base64.b64encode(raw).decode("ascii")


class Phase9AdminMigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.legacy_private = Ed25519PrivateKey.generate()
        self.act4_private = Ed25519PrivateKey.generate()
        self.machine_id = "NXJ2-1111-2222-3333-4444"
        self.act3_key = generate_activation_key(self.machine_id, self.legacy_private, key_version=3)
        self.database = AdminDatabase(self.root / "admin.db")
        self.service = AdminLicenseService(
            self.database,
            key_id="license-main-2026-01",
            private_key_provider=lambda: self.act4_private,
            legacy_key_verifier=lambda machine, key: verify_legacy_activation_key(
                machine, key, public_key=self.legacy_private.public_key()
            ),
        )
        self.admin_id = self.service.create_admin_user("migration-admin", "Admin Migração", role="owner")
        self.customer_id = self.service.create_customer("Cliente Legado", admin_user_id=self.admin_id)

    def test_migracao_explicita_registra_hash_e_emite_somente_act4(self) -> None:
        migration_id, license_id = self.service.migrate_legacy_license(
            self.customer_id,
            machine_id=self.machine_id,
            activation_key=self.act3_key,
            confirmation=f"MIGRAR:{self.machine_id}",
            term_months=6,
            validation_mode="offline",
            max_offline_days=0,
            admin_user_id=self.admin_id,
        )
        exported = self.service.export_license(
            license_id, self.root / "migrada.nxjlic", admin_user_id=self.admin_id
        )
        payload = verify_license(
            exported.read_bytes(),
            PublicKeyRing({"license-main-2026-01": self.act4_private.public_key()}),
            expected_machine_id=self.machine_id,
            check_time=False,
        )
        self.assertEqual(payload.license_id, license_id)
        self.assertEqual(payload.validation_mode, "offline")
        with self.database.read() as connection:
            migration = connection.execute(
                "SELECT * FROM legacy_license_migrations WHERE migration_id = ?", (migration_id,)
            ).fetchone()
        self.assertEqual(migration["legacy_version"], "ACT3")
        self.assertEqual(migration["legacy_key_hash"], hashlib.sha256(self.act3_key.encode("ascii")).hexdigest())
        self.assertNotIn(self.act3_key, tuple(str(value) for value in migration))
        self.assertTrue(self.database.verify_audit_chain())
        with self.assertRaises(InvalidTransitionError):
            self.service.migrate_legacy_license(
                self.customer_id,
                machine_id=self.machine_id,
                activation_key=self.act3_key,
                confirmation=f"MIGRAR:{self.machine_id}",
                term_months=3,
                admin_user_id=self.admin_id,
            )

    def test_bridge_valida_chave_e_confirmacao_antes_de_criar_cliente(self) -> None:
        bridge = AdminLicenseBridge(self.service, admin_user_id=self.admin_id)
        with self.database.read() as connection:
            before = connection.execute("SELECT COUNT(*) FROM customers").fetchone()[0]
        result = bridge.migrate_legacy_license(
            {
                "name": "Cliente inválido",
                "machine_id": self.machine_id,
                "activation_key": self.act3_key,
                "confirmation": "sim",
                "term_months": 3,
                "validation_mode": "offline",
                "max_offline_days": 0,
                "features": ["converter"],
            }
        )
        with self.database.read() as connection:
            after = connection.execute("SELECT COUNT(*) FROM customers").fetchone()[0]
        self.assertFalse(result["ok"])
        self.assertEqual(before, after)
        self.assertIn(f"MIGRAR:{self.machine_id}", result["error"])

    def test_bridge_repetido_nao_cria_cliente_ou_licenca_orfa(self) -> None:
        bridge = AdminLicenseBridge(self.service, admin_user_id=self.admin_id)
        payload = {
            "name": "Cliente migrado pelo bridge",
            "machine_id": self.machine_id,
            "activation_key": self.act3_key,
            "confirmation": f"MIGRAR:{self.machine_id}",
            "term_months": 3,
            "validation_mode": "offline",
            "max_offline_days": 0,
            "features": ["converter"],
        }
        first = bridge.migrate_legacy_license(payload)
        self.assertTrue(first["ok"])
        with self.database.read() as connection:
            before = tuple(
                connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in ("customers", "licenses", "legacy_license_migrations")
            )

        second = bridge.migrate_legacy_license({**payload, "name": "Cliente órfão"})

        with self.database.read() as connection:
            after = tuple(
                connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in ("customers", "licenses", "legacy_license_migrations")
            )
        self.assertFalse(second["ok"])
        self.assertIn("já possui uma migração", second["error"])
        self.assertEqual(before, after)


class Phase9ClientMigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.database_path = self.root / "nexojuris.db"
        self.backup_path = self.root / "license.sig"
        self.time_path = self.root / "license-time.dat"
        self.legacy_private = Ed25519PrivateKey.generate()
        self.act4_private = Ed25519PrivateKey.generate()
        self.machine_id = "NXJ2-AAAA-BBBB-CCCC-DDDD"
        self.act3_key = generate_activation_key(self.machine_id, self.legacy_private, key_version=3)
        temporal_guard = MagicMock()
        temporal_guard.observe.side_effect = lambda instant: SimpleNamespace(
            effective_time=instant,
            clock_tampered=False,
            last_trusted_server_at=None,
        )
        self.patchers = (
            patch.object(licensing, "_LICENSE_DB_PATH", self.database_path),
            patch.object(licensing, "_LICENSE_BACKUP_PATH", self.backup_path),
            patch.object(licensing, "_LICENSE_TIME_STATE_PATH", self.time_path),
            patch.object(licensing, "_temporal_guard", return_value=temporal_guard),
            patch.object(licensing, "_LICENSE_PUBLIC_KEY_B64", public_b64(self.legacy_private)),
            patch.object(licensing, "_ACT4_PUBLIC_KEYS_B64", {"license-main-2026-01": public_b64(self.act4_private)}),
            patch.object(licensing, "get_machine_fingerprint_v1", return_value=self.machine_id),
            patch.object(licensing, "get_machine_fingerprint_v2", return_value=self.machine_id),
            patch.object(licensing, "get_machine_fingerprint", return_value=self.machine_id),
        )
        for patcher in self.patchers:
            patcher.start()
            self.addCleanup(patcher.stop)

    def act4_document(self) -> bytes:
        payload = LicensePayload(
            key_id="license-main-2026-01",
            license_id="LIC-2026-000001",
            machine_id=self.machine_id,
            issued_at="2026-08-30T00:00:00Z",
            not_before="2026-08-30T00:00:00Z",
            expires_at="2027-08-30T00:00:00Z",
            validation_mode="offline",
            max_offline_days=0,
            features=("converter", "ocr", "reader"),
            customer_reference="MIGRACAO-001",
        )
        return issue_license(payload, self.act4_private)

    def test_atualizacao_preserva_act3_apenas_para_migracao_e_bloqueia_uso(self) -> None:
        self.assertTrue(licensing._save_license(self.machine_id, self.act3_key))
        with patch.object(licensing, "_configured_online_client", side_effect=AssertionError("consulta indevida")):
            status = licensing.get_license_status(now=datetime(2026, 9, 1, tzinfo=UTC))
        self.assertEqual(status.state, LicenseState.INVALID)
        self.assertFalse(status.can_use_protected_features)
        self.assertEqual(status.license_format, "legacy")
        self.assertIn("ACT4", status.message)
        with closing(licensing.sqlite3.connect(self.database_path)) as connection:
            columns = {row[1] for row in connection.execute("PRAGMA table_info(system_license)")}
            row = connection.execute(
                "SELECT activation_key, license_format FROM system_license WHERE id = 1"
            ).fetchone()
        self.assertIn("license_document", columns)
        self.assertEqual(row, (self.act3_key, "legacy"))

    def test_ativacao_act4_falha_sem_perder_act3_e_rollback_e_controlado(self) -> None:
        self.assertTrue(licensing._save_license(self.machine_id, self.act3_key))
        document = self.act4_document()
        tampered = document.replace(b'"validation_mode":"offline"', b'"validation_mode":"hybrid"')
        self.assertFalse(licensing.activate_act4_license(tampered)["ok"])
        self.assertEqual(licensing.get_license_status().license_format, "legacy")

        self.assertTrue(licensing.activate_act4_license(document)["ok"])
        self.assertEqual(licensing.get_license_status().license_format, "act4")
        denied = licensing.rollback_license_replacement(confirmation="sim")
        self.assertFalse(denied["ok"])
        restored = licensing.rollback_license_replacement(confirmation="RESTAURAR:LICENCA-ANTERIOR")
        self.assertTrue(restored["ok"])
        self.assertEqual(restored["license_format"], "legacy")
        self.assertEqual(licensing.get_license_status().license_format, "legacy")
        self.assertFalse(licensing.get_license_status().can_use_protected_features)
        with closing(licensing.sqlite3.connect(self.database_path)) as connection:
            history = connection.execute(
                "SELECT license_format, restored_at FROM system_license_history"
            ).fetchone()
        self.assertEqual(history[0], "legacy")
        self.assertIsNotNone(history[1])


if __name__ == "__main__":
    unittest.main()
