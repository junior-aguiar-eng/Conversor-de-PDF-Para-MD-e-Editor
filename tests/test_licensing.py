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
from admin_keygen import generate_activation_key, load_private_key
from licensing import (
    LicenseRequiredError,
    activate_software,
    deactivate_software,
    get_machine_fingerprint,
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
        ]
        for patcher in self.patchers:
            patcher.start()

    def tearDown(self) -> None:
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.tmp_dir.cleanup()

    def issue_key(self, machine_id: str | None = None) -> str:
        return generate_activation_key(machine_id or self.machine_id, self.private_key)


class LicensingUnitTests(IsolatedLicensingTestCase):
    def test_machine_fingerprint_format(self) -> None:
        machine_id = get_machine_fingerprint()
        self.assertRegex(machine_id, r"^NXJ-[0-9A-F]{4}(?:-[0-9A-F]{4}){3}$")

    def test_ed25519_key_generation_and_verification(self) -> None:
        key = self.issue_key()
        self.assertTrue(key.startswith("ACT2-01-"))
        self.assertRegex(key, r"^ACT2-01-[A-Z2-7-]+$")
        self.assertTrue(verify_license_key(self.machine_id, key))
        self.assertFalse(verify_license_key("NXJ-9999-8888-7777-6666", key))

        replacement = "A" if key[20] != "A" else "B"
        tampered_key = f"{key[:20]}{replacement}{key[21:]}"
        self.assertFalse(verify_license_key(self.machine_id, tampered_key))

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
        fake_root = MagicMock()
        with (
            patch.object(app_module, "Tk", return_value=fake_root),
            patch.object(app_module, "PdfMarkdownConverter") as converter_class,
            patch.object(app_module.messagebox, "showerror") as showerror,
        ):
            app_module.run_quick_convert(["documento.pdf"])

        converter_class.assert_not_called()
        showerror.assert_called_once()
        self.assertIn(self.machine_id, showerror.call_args.args[1])
        fake_root.destroy.assert_called_once()

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


class WebApiLicensingBridgeTests(IsolatedLicensingTestCase):
    def setUp(self) -> None:
        super().setUp()
        with patch("web_api.LibraryDatabase", return_value=MagicMock()):
            self.api = BridgeApi()
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
                {"files": [{"path": "documento.pdf"}], "output_dir": str(output_dir)}
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
        with patch("web_api.threading.Thread") as thread_class:
            result = self.api.start_conversion(
                {"files": [{"path": "documento.pdf"}], "output_dir": str(output_dir)}
            )

        self.assertTrue(result["started"])
        self.assertTrue(output_dir.is_dir())
        self.assertTrue(self.api.is_converting)
        thread_class.assert_called_once()
        thread_class.return_value.start.assert_called_once()


if __name__ == "__main__":
    unittest.main()
