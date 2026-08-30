from __future__ import annotations

import re
import unittest
from pathlib import Path


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
            "file_authorization.py",
            "library_db.py",
            "licensing.py",
            "markdown_utils.py",
            "models.py",
            "ocr_engine.py",
            "online_services.py",
            "production_diagnostics.py",
            "text_fidelity.py",
            "web_api.py",
        }
        self.assertEqual(required_files - manifest, set())

        for filename in required_files:
            self.assertTrue((project_root / filename).is_file(), filename)


if __name__ == "__main__":
    unittest.main()
