from __future__ import annotations

import base64
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

import admin_licensing.security as security_module
from admin_licensing import (
    ActiveEncryptedKeyProvider,
    AdminDatabase,
    AdminLicenseService,
    EncryptedSigningKeyStore,
    OperationalSecurityError,
)
from license_core import PublicKeyRing, verify_license


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

    def test_backup_offline_rejeita_arquivo_extra_fora_do_chaveiro_sem_escrita_parcial(self) -> None:
        source = EncryptedSigningKeyStore(self.root / "source", environment="production")
        source.generate("license-prod-202609-a", self.password, purpose="license")
        backup = source.export_offline_backup(self.root / "offline.nxjkeys", self.backup_password)

        encoded = backup.read_bytes()
        offset = len(security_module._BACKUP_MAGIC)
        salt = encoded[offset : offset + 16]
        nonce = encoded[offset + 16 : offset + 28]
        ciphertext = encoded[offset + 28 :]
        key = Scrypt(
            salt=salt,
            length=32,
            n=security_module._SCRYPT_N,
            r=security_module._SCRYPT_R,
            p=security_module._SCRYPT_P,
        ).derive(self.backup_password.encode("utf-8"))
        aad = security_module._BACKUP_MAGIC + b"production"
        bundle = json.loads(AESGCM(key).decrypt(nonce, ciphertext, aad))
        key_id, key_item = next(iter(bundle["manifest"]["keys"].items()))
        legitimate_file_name = key_item["file_name"]
        legitimate_file = bundle["files"][legitimate_file_name]
        bundle["files"]["../fora-do-chaveiro.txt"] = base64.b64encode(b"conteudo indevido").decode("ascii")
        malicious_nonce = b"0123456789ab"
        backup.write_bytes(
            security_module._BACKUP_MAGIC
            + salt
            + malicious_nonce
            + AESGCM(key).encrypt(
                malicious_nonce,
                json.dumps(bundle, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"),
                aad,
            )
        )

        restored = EncryptedSigningKeyStore(self.root / "restored", environment="production")
        with self.assertRaisesRegex(OperationalSecurityError, "referência inválida"):
            restored.restore_offline_backup(
                backup,
                self.backup_password,
                confirmation="RESTAURAR-CHAVES:production",
            )

        self.assertFalse((self.root / "fora-do-chaveiro.txt").exists())
        self.assertEqual(list(restored.root.iterdir()), [])

        del bundle["files"]["../fora-do-chaveiro.txt"]
        del bundle["files"][legitimate_file_name]
        alternate_stream_name = f"{key_id}.pem:fluxo"
        key_item["file_name"] = alternate_stream_name
        bundle["files"][alternate_stream_name] = legitimate_file
        alternate_nonce = b"abcdefghijkl"
        backup.write_bytes(
            security_module._BACKUP_MAGIC
            + salt
            + alternate_nonce
            + AESGCM(key).encrypt(
                alternate_nonce,
                json.dumps(bundle, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"),
                aad,
            )
        )
        alternate_restore = EncryptedSigningKeyStore(self.root / "alternate", environment="production")
        with self.assertRaisesRegex(OperationalSecurityError, "referência inválida"):
            alternate_restore.restore_offline_backup(
                backup,
                self.backup_password,
                confirmation="RESTAURAR-CHAVES:production",
            )
        self.assertEqual(list(alternate_restore.root.iterdir()), [])

    def test_chave_comprometida_deixa_de_ser_ativa_e_confiavel(self) -> None:
        store = EncryptedSigningKeyStore(self.root / "keys", environment="test")
        key_id = "license-test-202609-a"
        store.generate(key_id, self.password, purpose="license")
        store.mark_compromised(key_id, confirmation=f"COMPROMETIDA:{key_id}")
        self.assertNotIn(key_id, store.trusted_public_keys("license"))
        with self.assertRaises(OperationalSecurityError):
            store.active_key_id("license")
        with self.assertRaisesRegex(OperationalSecurityError, "comprometida"):
            store.load_private_key(key_id, self.password)


class Phase10AdminSecurityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.database = AdminDatabase(self.root / "admin.db", environment="test")
        self.license_key = Ed25519PrivateKey.generate()
        self.service = AdminLicenseService(
            self.database,
            key_id="license-main-2026-01",
            private_key_provider=lambda: self.license_key,
        )
        self.password = "senha-administrativa-fase10"
        self.admin_id = self.service.create_admin_user(
            "owner", "Owner", role="owner", password=self.password
        )

    def test_conta_local_protegida_pode_ser_desativada(self) -> None:
        authenticated = self.service.authenticate_admin("OWNER", self.password)
        self.assertEqual(authenticated["admin_user_id"], self.admin_id)
        self.assertIsNone(self.service.authenticate_admin("owner", "senha-incorreta"))
        self.service.revoke_admin_user(self.admin_id, performed_by=self.admin_id)
        self.assertIsNone(self.service.authenticate_admin("owner", self.password))
        self.assertTrue(self.database.verify_audit_chain())

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

    def test_banco_local_legado_e_arquivado_antes_de_inicializar_schema_novo(self) -> None:
        legacy_path = self.root / "legacy-admin.db"
        connection = sqlite3.connect(legacy_path)
        try:
            connection.executescript(
                """
                CREATE TABLE admin_schema(singleton INTEGER PRIMARY KEY, version INTEGER NOT NULL);
                INSERT INTO admin_schema(singleton, version) VALUES (1, 5);
                CREATE TABLE legacy_marker(value TEXT NOT NULL);
                INSERT INTO legacy_marker(value) VALUES ('preservado');
                """
            )
            connection.commit()
        finally:
            connection.close()

        migrated = AdminDatabase(legacy_path, environment="local")

        self.assertIsNotNone(migrated.legacy_backup_path)
        self.assertTrue(migrated.legacy_backup_path.is_file())
        with migrated.read() as connection:
            self.assertEqual(connection.execute("SELECT version FROM admin_schema").fetchone()[0], 6)
        connection = sqlite3.connect(migrated.legacy_backup_path)
        try:
            self.assertEqual(connection.execute("SELECT value FROM legacy_marker").fetchone()[0], "preservado")
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
