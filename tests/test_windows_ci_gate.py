from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class WindowsCiGateContractTests(unittest.TestCase):
    def test_ci_builds_and_exercises_release_on_windows(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        for contract in (
            "runs-on: windows-2025",
            "uv sync --frozen --group dev",
            ".\\build_release.ps1",
            "windows_release_gate.ps1",
            "SoakSeconds = 60",
            "WINDOWS_SIGNING_REQUIRED",
            "WINDOWS_SIGNING_CERTIFICATE_BASE64",
            "signtool.exe",
            "actions/upload-artifact@v4",
        ):
            self.assertIn(contract, workflow)

    def test_release_gate_covers_windows_specific_risks(self) -> None:
        gate = (ROOT / "scripts" / "windows_release_gate.ps1").read_text(encoding="utf-8-sig")
        for contract in (
            "Get-AuthenticodeSignature",
            "--release-probe",
            "edgechromium_backend_import",
            "multiprocessing_spawn",
            "FileAttributes]::ReadOnly",
            "FileShare]::None",
            "webview2_soak",
            "persistencia-update.txt",
            "Remove-Item -LiteralPath $installRoot",
            "user_data_preserved",
        ):
            self.assertIn(contract, gate)

    def test_app_exposes_noninteractive_packaged_probe(self) -> None:
        app = (ROOT / "app.py").read_text(encoding="utf-8")
        self.assertIn('multiprocessing.get_context("spawn")', app)
        self.assertIn('argv_paths[0] == "--release-probe"', app)
        self.assertIn("edgechromium", app)


if __name__ == "__main__":
    unittest.main()
