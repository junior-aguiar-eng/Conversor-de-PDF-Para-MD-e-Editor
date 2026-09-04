from __future__ import annotations

import base64
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from admin_license_bridge import AdminLicenseBridge


class AdminPrivateKeyConfigurationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.private_key = Ed25519PrivateKey.generate()
        self.source = self.root / "selecionada.pem"
        self.source.write_bytes(
            self.private_key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.BestAvailableEncryption(b"senha correta"),
            )
        )
        public_bytes = self.private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        self.public_b64 = base64.b64encode(public_bytes).decode("ascii")
        self.target = self.root / "perfil" / "nexojuris_ed25519_private.pem"
        service = MagicMock()
        service.key_id = "license-test"
        self.bridge = AdminLicenseBridge(service, private_key_path=self.target)
        self.window = MagicMock()
        self.window.create_file_dialog.return_value = [str(self.source)]
        self.bridge.set_window(self.window)

    def test_seleciona_valida_e_instala_chave_criptografada_compativel(self) -> None:
        self.assertFalse(self.bridge.private_key_status()["configured"])
        with patch.dict("admin_license_bridge.LICENSE_PUBLIC_KEYS_B64", {"license-test": self.public_b64}):
            result = self.bridge.configure_private_key("senha correta")
        self.assertTrue(result["ok"])
        self.assertEqual(self.target.read_bytes(), self.source.read_bytes())
        self.assertTrue(self.bridge.private_key_status()["configured"])

    def test_recusa_senha_errada_e_chave_que_nao_corresponde_ao_cliente(self) -> None:
        wrong_password = self.bridge.configure_private_key("senha errada")
        self.assertFalse(wrong_password["ok"])
        self.assertFalse(self.target.exists())

        with patch.dict("admin_license_bridge.LICENSE_PUBLIC_KEYS_B64", {"license-test": "AAAA"}):
            wrong_key = self.bridge.configure_private_key("senha correta")
        self.assertFalse(wrong_key["ok"])
        self.assertIn("não corresponde", wrong_key["error"])
        self.assertFalse(self.target.exists())

    def test_reexportacao_nao_exige_reabrir_a_chave_privada(self) -> None:
        result = self.bridge.export_license("LIC-TEST")
        self.assertTrue(result["ok"])
        self.window.create_file_dialog.assert_called_once()
        self.bridge.service.export_license.assert_called_once()


if __name__ == "__main__":
    unittest.main()
