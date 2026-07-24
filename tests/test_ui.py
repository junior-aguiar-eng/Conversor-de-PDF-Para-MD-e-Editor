from __future__ import annotations

import queue
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import ui
from models import BatchConversionSummary, ConversionFailure, ConversionResult


class AppFlowTests(unittest.TestCase):
    def test_build_summary_message_includes_counts_output_and_failures(self) -> None:
        app = ui.App.__new__(ui.App)
        app.output_dir = SimpleNamespace(get=lambda: r"D:\Saida")

        summary = BatchConversionSummary(
            successes=[
                ConversionResult(
                    source=Path("ok.pdf"),
                    markdown_path=Path("ok.md"),
                    asset_count=0,
                    chunk_count=0,
                )
            ],
            failures=[
                ConversionFailure(
                    source=Path("erro.pdf"),
                    error_message="Falha ao converter",
                    details="stacktrace",
                )
            ],
        )

        message = app._build_summary_message("Conversão concluída.", summary)

        self.assertIn("Convertidos: 1", message)
        self.assertIn("Com erro: 1", message)
        self.assertIn(r"Arquivos salvos em:\nD:\Saida".replace(r"\n", "\n"), message)
        self.assertIn("- erro.pdf", message)

    def test_convert_in_background_keeps_processing_after_single_file_failure(self) -> None:
        app = ui.App.__new__(ui.App)
        app.cancel_requested = threading.Event()
        app.resume_processing = threading.Event()
        app.resume_processing.set()
        app.events = queue.Queue()

        failure = RuntimeError("PDF corrompido")
        success = ConversionResult(
            source=Path("bom.pdf"),
            markdown_path=Path("saida.md"),
            asset_count=2,
            chunk_count=1,
        )

        fake_converter = SimpleNamespace(convert=self._convert_side_effect([failure, success]))

        with patch.object(ui, "PdfMarkdownConverter", return_value=fake_converter):
            app._convert_in_background(
                files=[Path("ruim.pdf"), Path("bom.pdf")],
                output_dir=Path("saida"),
                split_output=False,
                max_chunk_characters=1000,
            )

        received_events = []
        while not app.events.empty():
            received_events.append(app.events.get_nowait())

        self.assertEqual(received_events[0], ("status", "Convertendo 1/2: ruim.pdf"))
        self.assertEqual(received_events[1][0], "file_error")
        self.assertIsInstance(received_events[1][1], ConversionFailure)
        self.assertEqual(received_events[2], ("status", "Convertendo 2/2: bom.pdf"))
        self.assertEqual(received_events[3][0], "done")

        summary = received_events[3][1]
        self.assertEqual(len(summary.successes), 1)
        self.assertEqual(len(summary.failures), 1)
        self.assertEqual(summary.successes[0].source, Path("bom.pdf"))
        self.assertEqual(summary.failures[0].source, Path("ruim.pdf"))

    @staticmethod
    def _convert_side_effect(results: list[object]):
        pending = list(results)

        def runner(*args, **kwargs):
            current = pending.pop(0)
            if isinstance(current, Exception):
                raise current
            return current

        return runner


if __name__ == "__main__":
    unittest.main()
