from __future__ import annotations

import re
import tomllib
import unittest
from pathlib import Path

from constants import APP_VERSION


class InstallerManifestTests(unittest.TestCase):
    def test_clean_install_copies_every_runtime_module(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        script = (project_root / "instalar_no_d.ps1").read_text(encoding="utf-8-sig")
        manifest_match = re.search(
            r"\$arquivosDoAplicativo\s*=\s*@\((.*?)\n\)",
            script,
            flags=re.DOTALL,
        )
        self.assertIsNotNone(manifest_match)
        manifest = set(re.findall(r'"([^"]+)"', manifest_match.group(1)))

        required_files = {
            "app.py",
            "app_storage.py",
            "constants.py",
            "converter.py",
            "course_editorial.py",
            "document_exports.py",
            "document_structure.py",
            "document_blocks.py",
            "document_review.py",
            "source_table_formatting.py",
            "document_privacy.py",
            "file_authorization.py",
            "library_db.py",
            "license_key_config.py",
            "licensing.py",
            "markdown_utils.py",
            "models.py",
            "ocr_engine.py",
            "online_services.py",
            "production_diagnostics.py",
            "text_fidelity.py",
            "trusted_time.py",
            "web_api.py",
        }
        self.assertEqual(required_files - manifest, set())

        for filename in required_files:
            self.assertTrue((project_root / filename).is_file(), filename)
        for package in ("license_core", "api_bridge"):
            self.assertIn(f'Join-Path $origem "{package}"', script)
            self.assertTrue((project_root / package / "__init__.py").is_file(), package)

    def test_release_versions_agree_across_runtime_lock_and_installers(self) -> None:
        root = Path(__file__).resolve().parents[1]
        project = tomllib.loads((root / "pyproject.toml").read_text("utf-8"))
        lock = tomllib.loads((root / "uv.lock").read_text("utf-8"))
        self.assertEqual(project["project"]["version"], APP_VERSION)
        package = next(item for item in lock["package"] if item["name"] == project["project"]["name"])
        self.assertEqual(package["version"], APP_VERSION)
        for filename in ("NexoJuris-Conversor.iss", "NexoJuris-Licencas-Admin.iss"):
            manifest = (root / "installer" / filename).read_text("utf-8-sig")
            self.assertIn(f'#define MyAppVersion "{APP_VERSION}"', manifest)


if __name__ == "__main__":
    unittest.main()
