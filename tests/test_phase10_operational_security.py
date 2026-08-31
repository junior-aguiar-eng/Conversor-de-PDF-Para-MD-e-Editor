from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from admin_licensing import (
    ActiveEncryptedKeyProvider,
    AdminDatabase,
    AdminLicenseService,
    EncryptedSigningKeyStore,
    OperationalSecurityError,
    generate_totp_secret,
    totp_code,
)
from license_core import PublicKeyRing, verify_license
from license_service.api import LicenseServiceApi


class Phase10KeySecurityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.password = "senha-chave-fase10-forte"
        self.backup_password = "senha-backup-offline-fase10"

    def test_rotacao_preserva_chaves_anteriores_e_emite_com_novo_key_id(self) -> None:
        store = EncryptedSigningKeyStore(self.root / "keys", environment="test")
        first_id = "license-test-202609-a"
        second_id = "license-test-202610-b"
        store.generate(first_id, self.password, purpose="license")
        provider = ActiveEncryptedKeyProvider(store, purpose="license", password_provider=lambda: self.password)
        database = AdminDatabase(self.root / "admin.db", environment="test")
        service = AdminLicenseService(database, key_id=provider.key_id, private_key_provider=provider)
        admin_id = service.create_admin_user(
            "owner", "Owner", role="owner", password="senha-administrativa-fase10"
        )
        customer_id = service.create_customer("Cliente", admin_user_id=admin_id)
        first_license = service.issue_license(
            customer_id,
            term_months=3,
            machine_id="NXJ2-1111-2222-3333-4444",
            admin_user_id=admin_id,
        )
        store.rotate(
            "license",
            second_id,
            self.password,
            confirmation="ROTACIONAR:license:test",
        )
        second_license = service.issue_license(
            customer_id,
            term_months=6,
            machine_id="NXJ2-AAAA-BBBB-CCCC-DDDD",
            admin_user_id=admin_id,
        )
        with database.read() as connection:
            key_ids = {
                row["license_id"]: row["key_id"]
                for row in connection.execute("SELECT license_id, key_id FROM licenses").fetchall()
            }
        self.assertEqual(key_ids[first_license], first_id)
        self.assertEqual(key_ids[second_license], second_id)
        key_ring = PublicKeyRing(store.trusted_public_keys("license"))
        first_file = service.export_license(first_license, self.root / "first.nxjlic", admin_user_id=admin_id)
        second_file = service.export_license(second_license, self.root / "second.nxjlic", admin_user_id=admin_id)
        self.assertEqual(verify_license(first_file.read_bytes(), key_ring).key_id, first_id)
        self.assertEqual(verify_license(second_file.read_bytes(), key_ring).key_id, second_id)
        self.assertNotIn(b"PRIVATE KEY-----\nMC4CAQ", (self.root / "keys" / f"{first_id}.pem").read_bytes())
        self.assertIn(b"ENCRYPTED PRIVATE KEY", (self.root / "keys" / f"{first_id}.pem").read_bytes())

    def test_backup_offline_restaura_chaveiro_e_rejeita_outro_ambiente(self) -> None:
        source = EncryptedSigningKeyStore(self.root / "source", environment="production")
        key_id = "license-prod-202609-a"
        source.generate(key_id, self.password, purpose="license")
        backup = source.export_offline_backup(self.root / "offline.nxjkeys", self.backup_password)
        restored = EncryptedSigningKeyStore(self.root / "restored", environment="production")
        restored.restore_offline_backup(
            backup,
            self.backup_password,
            confirmation="RESTAURAR-CHAVES:production",
        )
        self.assertEqual(restored.active_key_id("license"), key_id)
        self.assertIsInstance(restored.load_private_key(key_id, self.password), Ed25519PrivateKey)
        wrong_environment = EncryptedSigningKeyStore(self.root / "wrong", environment="test")
        with self.assertRaisesRegex(OperationalSecurityError, "ambiente divergente"):
            wrong_environment.restore_offline_backup(
                backup,
                self.backup_password,
                confirmation="RESTAURAR-CHAVES:test",
            )

    def test_chave_comprometida_deixa_de_ser_ativa_e_confiavel(self) -> None:
        store = EncryptedSigningKeyStore(self.root / "keys", environment="test")
        key_id = "lease-test-202609-a"
        store.generate(key_id, self.password, purpose="lease")
        store.mark_compromised(key_id, confirmation=f"COMPROMETIDA:{key_id}")
        self.assertNotIn(key_id, store.trusted_public_keys("lease"))
        with self.assertRaises(OperationalSecurityError):
            store.active_key_id("lease")
        with self.assertRaisesRegex(OperationalSecurityError, "comprometida"):
            store.load_private_key(key_id, self.password)


class Phase10AdminSecurityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.database = AdminDatabase(self.root / "admin.db", environment="test")
        self.license_key = Ed25519PrivateKey.generate()
        self.lease_key = Ed25519PrivateKey.generate()
        self.service = AdminLicenseService(
            self.database,
            key_id="license-main-2026-01",
            private_key_provider=lambda: self.license_key,
        )
        self.password = "senha-administrativa-fase10"
        self.admin_id = self.service.create_admin_user(
            "owner", "Owner", role="owner", password=self.password
        )

    def test_conta_protegida_e_revogacao_invalida_usuario_e_tokens(self) -> None:
        self.assertEqual(self.service.authenticate_admin("OWNER", self.password), self.admin_id)
        with self.assertRaises(PermissionError):
            self.service.authenticate_admin("owner", "senha-incorreta")
        api = LicenseServiceApi(
            self.database,
            self.service,
            lease_key_id="lease-online-2026-01",
            lease_private_key_provider=lambda: self.lease_key,
        )
        token = "token-administrativo-fase10-com-32-caracteres"
        api.register_admin_token(token, label="revogavel", admin_user_id=self.admin_id)
        self.service.revoke_admin_user(
            self.admin_id,
            reason="credencial comprometida",
            confirmation=f"REVOGAR-ADMIN:{self.admin_id}",
            acting_admin_user_id=self.admin_id,
        )
        with self.assertRaises(PermissionError):
            self.service.authenticate_admin("owner", self.password)
        response = api.handle(
            "GET",
            "/v1/admin/search?q=",
            headers={"Authorization": f"Bearer {token}"},
        )
        self.assertEqual(response.status, 401)
        self.assertTrue(self.database.verify_audit_chain())

    def test_acesso_remoto_exige_token_e_totp_e_aceita_revogacao(self) -> None:
        secret = generate_totp_secret()
        api = LicenseServiceApi(
            self.database,
            self.service,
            lease_key_id="lease-online-2026-01",
            lease_private_key_provider=lambda: self.lease_key,
            require_admin_totp=True,
            admin_totp_secret_provider=lambda admin_id: secret if admin_id == self.admin_id else "",
        )
        token = "token-remoto-fase10-com-mais-de-32-caracteres"
        token_id = api.register_admin_token(token, label="remote", admin_user_id=self.admin_id)
        now = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
        bearer = {"Authorization": f"Bearer {token}"}
        self.assertEqual(api.handle("GET", "/v1/admin/search?q=", headers=bearer, now=now).status, 401)
        authorized = {
            **bearer,
            "X-NexoJuris-TOTP": totp_code(secret, at=now),
        }
        self.assertEqual(api.handle("GET", "/v1/admin/search?q=", headers=authorized, now=now).status, 200)
        api.revoke_admin_token(
            token_id,
            confirmation=f"REVOGAR-TOKEN:{token_id}",
            admin_user_id=self.admin_id,
        )
        self.assertEqual(api.handle("GET", "/v1/admin/search?q=", headers=authorized, now=now).status, 401)

    def test_perda_do_banco_restauracao_e_auditoria_imutavel(self) -> None:
        customer_id = self.service.create_customer("Cliente preservado", admin_user_id=self.admin_id)
        backup = self.database.create_encrypted_backup(
            self.root / "admin.nxjbackup",
            "senha-backup-banco-fase10",
            admin_user_id=self.admin_id,
        )
        self.database.path.unlink()
        recovered = AdminDatabase(self.database.path, environment="test")
        recovered.restore_encrypted_backup(
            backup,
            "senha-backup-banco-fase10",
            confirmation=f"RESTAURAR:{self.database.path.name}",
            admin_user_id=self.admin_id,
        )
        with recovered.read() as connection:
            self.assertTrue(
                connection.execute("SELECT 1 FROM customers WHERE customer_id = ?", (customer_id,)).fetchone()
            )
        self.assertTrue(recovered.verify_audit_chain())
        with self.assertRaises(sqlite3.IntegrityError):
            with recovered.transaction() as connection:
                connection.execute("UPDATE audit_events SET action = 'tampered'")

    def test_banco_nao_pode_ser_aberto_com_ambiente_divergente(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "pertence ao ambiente test"):
            AdminDatabase(self.database.path, environment="production")

    def test_banco_local_e_vinculado_sem_relaxar_producao(self) -> None:
        local_database = AdminDatabase(self.root / "local-admin.db", environment="local")
        with local_database.read() as connection:
            environment = connection.execute(
                "SELECT setting_value FROM operational_settings WHERE setting_key = 'environment'"
            ).fetchone()[0]
        self.assertEqual(environment, "local")
        with self.assertRaisesRegex(RuntimeError, "pertence ao ambiente local"):
            AdminDatabase(local_database.path, environment="production")


if __name__ == "__main__":
    unittest.main()
