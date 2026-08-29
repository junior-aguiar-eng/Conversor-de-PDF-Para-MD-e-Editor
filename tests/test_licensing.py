"""Testes do licenciamento offline assimétrico."""

from __future__ import annotations

import base64
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import app as app_module
import licensing as licensing_module
from admin_keygen import generate_activation_key, generate_keypair, load_private_key
from licensing import (
    LicenseRequiredError,
    activate_software,
    deactivate_software,
    get_machine_fingerprint_v1,
    get_machine_fingerprint_v2,
    is_software_activated,
    require_software_activation,
    verify_license_key,
)
from web_api import BridgeApi


class IsolatedLicensingTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        root = Path(self.tmp_dir.name)
        self.machine_id = "NXJ-1111-2222-3333-4444"
        self.private_key = Ed25519PrivateKey.generate()
        public_bytes = self.private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        public_key_b64 = base64.b64encode(public_bytes).decode("ascii")
        self.patchers = [
            patch.object(licensing_module, "_LICENSE_DB_PATH", root / "license.db"),
            patch.object(licensing_module, "_LICENSE_BACKUP_PATH", root / "license.sig"),
            patch.object(licensing_module, "_LICENSE_PUBLIC_KEY_B64", public_key_b64),
            patch.object(licensing_module, "get_machine_fingerprint", return_value=self.machine_id),
            patch.object(licensing_module, "get_machine_fingerprint_v1", return_value=self.machine_id),
            patch.object(licensing_module, "get_machine_fingerprint_v2", return_value=self.machine_id),
        ]
        for patcher in self.patchers:
            patcher.start()

    def tearDown(self) -> None:
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.tmp_dir.cleanup()

    def issue_key(self, machine_id: str | None = None, key_version: int = 2) -> str:
        return generate_activation_key(machine_id or self.machine_id, self.private_key, key_version=key_version)


class LicensingUnitTests(IsolatedLicensingTestCase):
    def test_windows_machine_identity_uses_registry_once_per_session(self) -> None:
        licensing_module._get_motherboard_uuid.cache_clear()
        try:
            with (
                patch.object(licensing_module.platform, "system", return_value="Windows"),
                patch("winreg.OpenKey") as open_key,
                patch("winreg.QueryValueEx", return_value=("machine-guid-test-1234", 1)) as query_value,
                patch.object(licensing_module.subprocess, "check_output") as check_output,
            ):
                first = licensing_module._get_motherboard_uuid()
                second = licensing_module._get_motherboard_uuid()

            self.assertEqual(first, "MACHINE-GUID-TEST-1234")
            self.assertEqual(second, first)
            open_key.assert_called_once()
            query_value.assert_called_once()
            check_output.assert_not_called()
        finally:
            licensing_module._get_motherboard_uuid.cache_clear()

    def test_machine_fingerprint_format(self) -> None:
        mid1 = get_machine_fingerprint_v1()
        mid2 = get_machine_fingerprint_v2()
        self.assertTrue(mid1.startswith("NXJ-"))
        self.assertTrue(mid2.startswith("NXJ2-"))

    def test_ed25519_key_generation_and_verification(self) -> None:
        key = self.issue_key()
        self.assertTrue(key.startswith("ACT2-01-"))
        self.assertRegex(key, r"^ACT2-01-[A-Z2-7-]+$")
        self.assertTrue(verify_license_key(self.machine_id, key))
        self.assertFalse(verify_license_key("NXJ-9999-8888-7777-6666", key))

        replacement = "A" if key[20] != "A" else "B"
        tampered_key = f"{key[:20]}{replacement}{key[21:]}"
        self.assertFalse(verify_license_key(self.machine_id, tampered_key))

    def test_v3_key_generation_and_verification(self) -> None:
        v2_mid = "NXJ2-1111-2222-3333-4444"
        key_v3 = generate_activation_key(v2_mid, self.private_key, key_version=3)
        self.assertTrue(key_v3.startswith("ACT3-01-"))
        self.assertTrue(verify_license_key(v2_mid, key_v3))

    def test_client_rejects_legacy_hmac_keys_and_has_no_emitter(self) -> None:
        self.assertFalse(verify_license_key(self.machine_id, "ACT-0000-1111-2222-3333"))
        self.assertFalse(hasattr(licensing_module, "generate_activation_key"))

    def test_rejects_malformed_version_and_key_id(self) -> None:
        key = self.issue_key()
        self.assertFalse(verify_license_key(self.machine_id, key.replace("ACT2-01-", "ACT3-01-", 1)))
        self.assertFalse(verify_license_key(self.machine_id, key.replace("ACT2-01-", "ACT2-99-", 1)))
        self.assertFalse(verify_license_key(self.machine_id, "ACT2-01-INVALID"))
        encoded = key.removeprefix("ACT2-01-").replace("-", "")
        self.assertFalse(verify_license_key(self.machine_id, f"ACT2-01-{encoded}"))
        self.assertFalse(verify_license_key(self.machine_id, key.replace("ACT2-01-", "ACT2-01--", 1)))
        self.assertFalse(verify_license_key(self.machine_id, key.replace("-", "--", 1)))

    def test_activation_lifecycle_uses_only_temporary_storage(self) -> None:
        deactivate_software()
        self.assertEqual(is_software_activated(), (False, self.machine_id))
        self.assertFalse(activate_software("ACT2-01-INVALID")["ok"])
        with self.assertRaisesRegex(LicenseRequiredError, "Ativação necessária"):
            require_software_activation()

        good_key = self.issue_key()
        self.assertTrue(activate_software(good_key)["ok"])
        self.assertEqual(is_software_activated(), (True, self.machine_id))
        self.assertEqual(require_software_activation(), self.machine_id)

    def test_quick_convert_rejects_unlicensed_machine_before_converter(self) -> None:
        with (
            patch.object(app_module, "PdfMarkdownConverter") as converter_class,
            patch.object(app_module, "_show_message") as show_message,
        ):
            app_module.run_quick_convert(["documento.pdf"])

        converter_class.assert_not_called()
        show_message.assert_called_once()
        self.assertIn(self.machine_id, show_message.call_args.args[0])
        self.assertTrue(show_message.call_args.kwargs["error"])

    def test_admin_private_key_roundtrip(self) -> None:
        private_path = Path(self.tmp_dir.name) / "admin.pem"
        private_path.write_bytes(
            self.private_key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption(),
            )
        )
        loaded_key = load_private_key(private_path)
        self.assertTrue(verify_license_key(self.machine_id, generate_activation_key(self.machine_id, loaded_key)))

    def test_admin_encrypted_pem_is_optional_and_legacy_pem_remains_readable(self) -> None:
        encrypted_path = Path(self.tmp_dir.name) / "admin-encrypted.pem"
        encrypted_public_key = generate_keypair(encrypted_path, password="senha forte de teste")
        self.assertIn(b"ENCRYPTED PRIVATE KEY", encrypted_path.read_bytes())
        with self.assertRaises((TypeError, ValueError)):
            load_private_key(encrypted_path)
        encrypted_key = load_private_key(encrypted_path, password="senha forte de teste")
        with patch.object(licensing_module, "_LICENSE_PUBLIC_KEY_B64", encrypted_public_key):
            self.assertTrue(verify_license_key(self.machine_id, generate_activation_key(self.machine_id, encrypted_key)))

        legacy_path = Path(self.tmp_dir.name) / "admin-legacy.pem"
        generate_keypair(legacy_path)
        self.assertNotIn(b"ENCRYPTED PRIVATE KEY", legacy_path.read_bytes())
        self.assertIsInstance(load_private_key(legacy_path), Ed25519PrivateKey)


class WebApiLicensingBridgeTests(IsolatedLicensingTestCase):
    def setUp(self) -> None:
        super().setUp()
        with patch("web_api.LibraryDatabase", return_value=MagicMock()):
            self.api = BridgeApi(conversion_journal_path=Path(self.tmp_dir.name) / "conversion-journal.json")
        self.api._window = MagicMock()

    def test_bridge_license_endpoints(self) -> None:
        info = self.api.get_license_info()
        self.assertFalse(info["is_activated"])
        self.assertEqual(info["machine_id"], self.machine_id)

        act_res = self.api.activate_software(self.issue_key())
        self.assertTrue(act_res["ok"])
        self.assertTrue(self.api.get_license_info()["is_activated"])

    def test_bridge_rejects_conversion_before_side_effects_without_license(self) -> None:
        output_dir = Path(self.tmp_dir.name) / "must-not-exist"
        with patch("web_api.threading.Thread") as thread_class:
            result = self.api.start_conversion(
                {"files": [{"file_id": "não-autorizado"}], "output_directory_id": "não-autorizado"}
            )

        self.assertFalse(result["started"])
        self.assertEqual(result["error_code"], "license_required")
        self.assertEqual(result["machine_id"], self.machine_id)
        self.assertFalse(output_dir.exists())
        self.assertFalse(self.api.is_converting)
        thread_class.assert_not_called()

    def test_bridge_starts_conversion_after_valid_activation(self) -> None:
        self.assertTrue(self.api.activate_software(self.issue_key())["ok"])
        output_dir = Path(self.tmp_dir.name) / "licensed-output"
        source = Path(self.tmp_dir.name) / "documento.pdf"
        source.write_bytes(b"%PDF-1.4\n%%EOF")
        file_id = self.api._register_pdf(source, "test")["file_id"]
        directory_id = self.api._register_directory(output_dir, "test")["directory_id"]
        with patch("web_api.threading.Thread") as thread_class:
            result = self.api.start_conversion(
                {"files": [{"file_id": file_id}], "output_directory_id": directory_id}
            )

        self.assertTrue(result["started"])
        self.assertTrue(output_dir.is_dir())
        self.assertTrue(self.api.is_converting)
        thread_class.assert_called_once()
        thread_class.return_value.start.assert_called_once()


if __name__ == "__main__":
    unittest.main()
