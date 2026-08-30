from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from production_diagnostics import build_diagnostic_report, write_diagnostic_report
from web_api import BridgeApi


class ProductionDiagnosticsTests(unittest.TestCase):
    def test_report_sanitizes_secrets_and_classifies_failures(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            home = str(Path.home().resolve())
            (root / "nexojuris.log").write_text(
                "2026 ERROR logger=converter Falha no PDF por MemoryError\n"
                "2026 ERROR logger=ocr_engine Falha RapidOCR ONNX\n"
                "2026 WARNING logger=library_db SQLite banco integrity_check\n"
                f"2026 ERROR path={home}\\caso.pdf senha=segredo machine=NXJ-1111-2222-3333-4444\n",
                encoding="utf-8",
            )

            report = build_diagnostic_report(
                directory=root,
                library_status={"state": "ok"},
                online_services={"translation": {"state": "closed"}},
            )

            self.assertIn("%USERPROFILE%", report)
            self.assertIn("senha=[REDACTED]", report)
            self.assertNotIn("senha=segredo", report)
            self.assertNotIn("NXJ-1111-2222-3333-4444", report)
            self.assertIn('"memory_or_budget": 1', report)
            self.assertIn('"onnx_or_ocr": 1', report)
            self.assertIn('"sqlite_or_storage": 1', report)
            self.assertIn("Não inclui conteúdo dos PDFs", report)

    def test_report_write_is_atomic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            destination = Path(tmp_dir) / "diagnostico.txt"
            write_diagnostic_report(destination, "relatório")
            self.assertEqual(destination.read_text(encoding="utf-8"), "relatório")
            self.assertEqual(list(destination.parent.glob(".*.tmp")), [])

    def test_logging_rotates_and_captures_unhandled_thread_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            script = (
                "import json, logging, pathlib, threading; "
                "import production_diagnostics as d; "
                "d.LOG_MAX_BYTES=300; d.LOG_BACKUP_COUNT=2; "
                f"root=pathlib.Path({json.dumps(tmp_dir)}); "
                "d.configure_production_diagnostics(directory=root); "
                "[logging.getLogger('test').error('PDF ONNX SQLite permission '+str(i)) for i in range(30)]; "
                "t=threading.Thread(target=lambda: 1/0, name='failing-worker'); t.start(); t.join(); "
                "logging.shutdown(); "
                "print(json.dumps({'log':(root/'nexojuris.log').exists(), "
                "'rotated':(root/'nexojuris.log.1').exists(), "
                "'crash':'Falha não tratada na thread failing-worker' in ''.join("
                "p.read_text(encoding='utf-8') for p in root.glob('nexojuris.log*'))}))"
            )
            result = subprocess.run(
                [sys.executable, "-c", script],
                cwd=Path(__file__).resolve().parents[1],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            status = json.loads(result.stdout.strip().splitlines()[-1])
            self.assertTrue(status["log"])
            self.assertTrue(status["rotated"])
            self.assertTrue(status["crash"])

    def test_bridge_exports_report_only_to_native_dialog_destination(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            destination = Path(tmp_dir) / "support-report"

            class Window:
                @staticmethod
                def create_file_dialog(*_args, **_kwargs):
                    return str(destination)

            api = BridgeApi.__new__(BridgeApi)
            api._window = Window()
            api._library_status = {"state": "ok", "persistent": True}

            with patch("web_api.build_diagnostic_report", return_value="diagnóstico"):
                result = api.export_diagnostic_report()

            exported = destination.with_suffix(".txt")
            self.assertTrue(result["ok"])
            self.assertEqual(exported.read_text(encoding="utf-8"), "diagnóstico")


if __name__ == "__main__":
    unittest.main()
