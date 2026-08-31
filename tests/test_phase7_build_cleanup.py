"""Regressões da Fase 7: build único e limpeza conservadora."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

import build_support
from licensing import deactivate_software

ROOT = Path(__file__).resolve().parents[1]


class Phase7BuildCleanupTests(unittest.TestCase):
    def test_official_and_compatible_builders_use_the_same_module(self) -> None:
        powershell = (ROOT / "build_release.ps1").read_text(encoding="utf-8-sig")
        wrapper = (ROOT / "build_app.py").read_text(encoding="utf-8")
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")

        self.assertIn("$PSScriptRoot", powershell)
        self.assertIn("uv run --frozen --group dev python", powershell)
        self.assertIn('from build_support import main', wrapper)
        self.assertNotIn("--collect-all", powershell + wrapper)
        self.assertRegex(pyproject, r'"pyinstaller==6\.22\.2"')

    def test_pyinstaller_arguments_are_absolute_and_preserve_runtime_packages(self) -> None:
        arguments = build_support.pyinstaller_arguments(build_support.WORK_DIR / "version_info.txt")
        self.assertTrue(Path(arguments[-1]).is_absolute())
        self.assertEqual(Path(arguments[-1]), build_support.APP_ENTRYPOINT)
        for package in ("pymupdf4llm", "rapidocr_onnxruntime", "edge_tts", "deep_translator"):
            self.assertIn(package, arguments)

    def test_admin_has_separate_self_contained_build_and_installer(self) -> None:
        arguments = build_support.admin_pyinstaller_arguments(build_support.WORK_DIR / "admin_version_info.txt")
        installer = (ROOT / "installer" / "NexoJuris-Licencas-Admin.iss").read_text(encoding="utf-8-sig")
        launcher = (ROOT / "abrir_admin_licencas.ps1").read_text(encoding="utf-8-sig")

        self.assertEqual(Path(arguments[-1]), build_support.ADMIN_ENTRYPOINT)
        self.assertTrue(any("admin_web" in argument for argument in arguments))
        for package in ("webview", "cryptography"):
            self.assertIn(package, arguments)
        self.assertIn('Source: "..\\release\\dist\\NexoJuris Licenças Admin\\*"', installer)
        self.assertIn('Source: "..\\abrir_admin_licencas.ps1"', installer)
        self.assertNotIn(".secrets", installer)
        self.assertIn('"NexoJuris Licenças Admin.exe"', launcher)
        self.assertIn('"NexoJuris\\LicencasAdmin"', launcher)
        self.assertIn("NEXOJURIS_ADMIN_PRIVATE_KEY", launcher)

    def test_apparently_idle_dependencies_remain_until_individual_release_audit(self) -> None:
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        for dependency in ("openpyxl", "pdfplumber", "pypdf", "pytesseract", "python-docx"):
            self.assertRegex(pyproject, rf'"{re.escape(dependency)}[=<>]')

    def test_obsolete_instance_zoom_is_removed_but_document_zoom_remains(self) -> None:
        app = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        self.assertNotIn("this.zoom", app)
        self.assertIn('zoom: "1.0"', app)
        self.assertIn("this.activeDocumentState.zoom = val", app)

    def test_fonts_are_declared_once_with_their_variable_weights(self) -> None:
        style = (ROOT / "web" / "style.css").read_text(encoding="utf-8")
        expected = {
            "Plus Jakarta Sans": "200 800",
            "Inter": "100 900",
            "JetBrains Mono": "100 800",
            "Cinzel": "400 900",
        }
        for family, weights in expected.items():
            blocks = re.findall(
                rf"@font-face\s*\{{[^}}]*font-family:\s*'{re.escape(family)}'[^}}]*\}}",
                style,
                flags=re.DOTALL,
            )
            self.assertEqual(len(blocks), 1, family)
            self.assertIn(f"font-weight: {weights};", blocks[0])

    def test_support_deactivation_api_is_preserved(self) -> None:
        self.assertTrue(callable(deactivate_software))


if __name__ == "__main__":
    unittest.main()
