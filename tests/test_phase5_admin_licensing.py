from __future__ import annotations

import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from admin_licensing import (
    AdminDatabase,
    AdminLicenseError,
    AdminLicenseService,
    BackupError,
    ConfirmationRequiredError,
    EncryptedPrivateKeyProvider,
    InvalidTransitionError,
)
from license_core import PublicKeyRing, verify_license


class Phase5AdminLicensingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.private_key = Ed25519PrivateKey.generate()
        self.database = AdminDatabase(self.root / "admin.db")
        self.service = AdminLicenseService(
            self.database,
            key_id="license-test-2026",
            private_key_provider=lambda: self.private_key,
        )
        self.admin_id = self.service.create_admin_user("operador", "Operador Teste")
        self.customer_id = self.service.create_customer(
            "João da Silva",
            email="joao@example.test",
            commercial_reference="PED-100",
            admin_user_id=self.admin_id,
        )

    def issue(self, **overrides: object) -> str:
        arguments: dict[str, object] = {
            "term_months": 3,
            "validation_mode": "hybrid",
            "max_offline_days": 7,
            "admin_user_id": self.admin_id,
        }
        arguments.update(overrides)
        return self.service.issue_license(self.customer_id, **arguments)  # type: ignore[arg-type]

    def test_schema_contem_todo_o_modelo_administrativo(self) -> None:
        expected = {
            "customers",
            "licenses",
            "licensed_devices",
            "license_features",
            "license_renewals",
            "license_status_changes",
            "offline_exports",
            "online_leases",
            "audit_events",
            "admin_users",
        }
        with self.database.read() as connection:
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        self.assertTrue(expected.issubset(tables))

    def test_ciclo_completo_emite_renova_suspende_reativa_troca_e_exporta(self) -> None:
        license_id = self.issue(term_months=6)
        first_device = self.service.bind_device(
            license_id, "NXJ2-1111-2222-3333-4444", admin_user_id=self.admin_id
        )
        renewal_id = self.service.renew_license(
            license_id,
            term_months=12,
            admin_user_id=self.admin_id,
            now=datetime(2026, 8, 30, tzinfo=UTC),
        )
        self.assertTrue(renewal_id.startswith("REN-"))

        self.service.suspend_license(license_id, "inadimplência", admin_user_id=self.admin_id)
        with self.assertRaises(InvalidTransitionError):
            self.service.export_license(license_id, self.root / "suspensa.nxjlic", admin_user_id=self.admin_id)
        self.service.reactivate_license(license_id, "pagamento confirmado", admin_user_id=self.admin_id)

        with self.assertRaises(ConfirmationRequiredError):
            self.service.replace_device(
                license_id,
                "NXJ2-AAAA-BBBB-CCCC-DDDD",
                confirmation="sim",
                reason="troca de computador",
                admin_user_id=self.admin_id,
            )
        second_device = self.service.replace_device(
            license_id,
            "NXJ2-AAAA-BBBB-CCCC-DDDD",
            confirmation=f"TROCAR:{license_id}",
            reason="troca de computador",
            admin_user_id=self.admin_id,
        )
        exported = self.service.export_license(
            license_id, self.root / f"{license_id}.nxjlic", admin_user_id=self.admin_id
        )

        payload = verify_license(
            exported.read_bytes(),
            PublicKeyRing({"license-test-2026": self.private_key.public_key()}),
            expected_machine_id="NXJ2-AAAA-BBBB-CCCC-DDDD",
            check_time=False,
        )
        self.assertEqual(payload.license_id, license_id)
        self.assertEqual(payload.max_offline_days, 7)
        self.assertEqual(payload.features, ("converter", "ocr", "reader"))

        details = self.service.get_license(license_id)
        self.assertEqual(details["active_device"]["device_id"], second_device)
        self.assertNotEqual(first_device, second_device)
        self.assertEqual(details["term_months"], 12)
        actions = [event["action"] for event in self.service.history("license", license_id)]
        self.assertEqual(
            actions,
            [
                "license.issued",
                "device.bound",
                "license.renewed",
                "license.suspended",
                "license.active",
                "device.replaced",
                "license.exported",
            ],
        )
        self.assertTrue(self.database.verify_audit_chain())

    def test_revogacao_exige_confirmacao_e_e_irreversivel(self) -> None:
        license_id = self.issue()
        with self.assertRaises(ConfirmationRequiredError):
            self.service.revoke_license(
                license_id, "fraude", confirmation=license_id, admin_user_id=self.admin_id
            )
        self.service.revoke_license(
            license_id,
            "fraude",
            confirmation=f"REVOGAR:{license_id}",
            admin_user_id=self.admin_id,
        )
        with self.assertRaises(InvalidTransitionError):
            self.service.reactivate_license(license_id, "tentativa", admin_user_id=self.admin_id)

    def test_identificadores_e_historicos_sao_imutaveis_e_append_only(self) -> None:
        license_id = self.issue()
        self.service.suspend_license(license_id, "teste", admin_user_id=self.admin_id)
        with self.database.transaction() as connection:
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("UPDATE licenses SET license_id = 'LIC-2026-999999' WHERE license_id = ?", (license_id,))
        with self.database.transaction() as connection:
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("DELETE FROM audit_events")
        with self.database.transaction() as connection:
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("UPDATE license_status_changes SET reason = 'alterado'")
        self.assertTrue(self.database.verify_audit_chain())

    def test_persistencia_e_emissao_concorrente_preservam_ids_unicos(self) -> None:
        customer_ids = [
            self.service.create_customer(f"Cliente {index}", admin_user_id=self.admin_id) for index in range(8)
        ]

        def issue_for(customer_id: str) -> str:
            service = AdminLicenseService(
                AdminDatabase(self.database.path),
                key_id="license-test-2026",
                private_key_provider=lambda: self.private_key,
            )
            return service.issue_license(customer_id, term_months=3, admin_user_id=self.admin_id)

        with ThreadPoolExecutor(max_workers=8) as executor:
            license_ids = list(executor.map(issue_for, customer_ids))
        self.assertEqual(len(set(license_ids)), 8)

        reopened = AdminLicenseService(
            AdminDatabase(self.database.path),
            key_id="license-test-2026",
            private_key_provider=lambda: self.private_key,
        )
        for license_id in license_ids:
            self.assertEqual(reopened.get_license(license_id)["license_id"], license_id)
        self.assertTrue(reopened.database.verify_audit_chain())

    def test_backup_e_criptografado_e_restauracao_exige_confirmacao(self) -> None:
        license_id = self.issue()
        backup = self.database.create_encrypted_backup(
            self.root / "admin.nxjadb", "senha forte 123", admin_user_id=self.admin_id
        )
        encoded = backup.read_bytes()
        self.assertFalse(encoded.startswith(b"SQLite format 3"))
        self.assertNotIn(license_id.encode("ascii"), encoded)

        later_customer = self.service.create_customer("Cliente posterior", admin_user_id=self.admin_id)
        with self.assertRaises(ConfirmationRequiredError):
            self.database.restore_encrypted_backup(backup, "senha forte 123", confirmation="sim")
        with self.assertRaises(BackupError):
            self.database.restore_encrypted_backup(
                backup,
                "senha incorreta 456",
                confirmation=f"RESTAURAR:{self.database.path.name}",
            )
        self.database.restore_encrypted_backup(
            backup,
            "senha forte 123",
            confirmation=f"RESTAURAR:{self.database.path.name}",
            admin_user_id=self.admin_id,
        )
        self.assertEqual(self.service.get_license(license_id)["license_id"], license_id)
        with self.database.read() as connection:
            self.assertIsNone(
                connection.execute("SELECT 1 FROM customers WHERE customer_id = ?", (later_customer,)).fetchone()
            )
        self.assertTrue(self.database.verify_audit_chain())
        actions = [event["action"] for event in self.service.history("database", self.database.path.name)]
        self.assertEqual(actions, ["database.backup_restored"])

    def test_chave_privada_de_disco_deve_estar_criptografada(self) -> None:
        plain_path = self.root / "plain.pem"
        plain_path.write_bytes(
            self.private_key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
        with self.assertRaises(AdminLicenseError):
            EncryptedPrivateKeyProvider(plain_path, lambda: "senha")()

        encrypted_path = self.root / "encrypted.pem"
        encrypted_path.write_bytes(
            self.private_key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.BestAvailableEncryption(b"senha-da-chave"),
            )
        )
        loaded = EncryptedPrivateKeyProvider(encrypted_path, lambda: "senha-da-chave")()
        self.assertEqual(
            loaded.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw),
            self.private_key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw),
        )


if __name__ == "__main__":
    unittest.main()
