from __future__ import annotations

import queue
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import converter as converter_module
import ui
from constants import DEFAULT_MAX_CHUNK_CHARACTERS, DEFAULT_OUTPUT_DIR, MAX_PAGE_COUNT
from models import BatchConversionSummary, ConversionFailure, ConversionResult


class AppFlowTests(unittest.TestCase):
    def test_default_output_dir_points_to_pdfs_convertidos(self) -> None:
        self.assertEqual(DEFAULT_OUTPUT_DIR.name, "PDFs Convertidos")

    def test_default_chunk_size_matches_current_ui_default(self) -> None:
        self.assertEqual(DEFAULT_MAX_CHUNK_CHARACTERS, 60_000)

    def test_build_summary_message_includes_counts_output_and_failures(self) -> None:
        app = ui.App.__new__(ui.App)
        app.output_dir = SimpleNamespace(get=lambda: r"D:\Saida")
        app._batch_start_time = time.perf_counter()

        summary = BatchConversionSummary(
            successes=[
                ConversionResult(
                    source=Path("ok.pdf"),
                    markdown_path=Path("ok.md"),
                    asset_count=0,
                    chunk_count=0,
                    extraction_seconds=1.5,
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
        self.assertIn("Tempo total:", message)
        self.assertIn(r"Arquivos salvos em:\nD:\Saida".replace(r"\n", "\n"), message)
        self.assertIn("- erro.pdf", message)

    def test_format_duration_switches_to_minutes_after_sixty_seconds(self) -> None:
        self.assertEqual(ui.format_duration(45), "45s")
        self.assertEqual(ui.format_duration(125), "2m 5s")

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
        request = ui.ConversionRequest(
            files=[Path("ruim.pdf"), Path("bom.pdf")],
            output_dir=Path("saida"),
            split_output=False,
            max_chunk_characters=1000,
        )

        with patch.object(ui, "PdfMarkdownConverter", return_value=fake_converter):
            # max_workers=1 força o caminho sequencial: um ProcessPoolExecutor
            # real não dá pra mockar por cima de um PdfMarkdownConverter falso.
            app._convert_in_background(request, max_workers=1)

        received_events = []
        while not app.events.empty():
            received_events.append(app.events.get_nowait())

        self.assertEqual(received_events[0], ("status", "Convertendo 1/2: ruim.pdf"))
        self.assertEqual(received_events[1][0], "file_error")
        self.assertIsInstance(received_events[1][1], ConversionFailure)
        self.assertEqual(received_events[2], ("progress", (1, 2)))
        self.assertEqual(received_events[3], ("status", "Convertendo 2/2: bom.pdf"))
        self.assertEqual(received_events[4], ("progress", (2, 2)))
        self.assertEqual(received_events[5][0], "done")

        summary = received_events[5][1]
        self.assertEqual(len(summary.successes), 1)
        self.assertEqual(len(summary.failures), 1)
        self.assertEqual(summary.successes[0].source, Path("bom.pdf"))
        self.assertEqual(summary.failures[0].source, Path("ruim.pdf"))

    def test_convert_in_parallel_does_not_hang_when_stopped_before_any_submission(self) -> None:
        # Regressão: se "Parar" já estiver marcado quando não há nenhum
        # arquivo em andamento mas ainda restam arquivos não submetidos, o
        # laço de _convert_in_parallel podia ficar preso para sempre.
        app = ui.App.__new__(ui.App)
        app.cancel_requested = threading.Event()
        app.cancel_requested.set()
        app.resume_processing = threading.Event()
        app.resume_processing.set()
        app.events = queue.Queue()

        request = ui.ConversionRequest(
            files=[Path("a.pdf"), Path("b.pdf"), Path("c.pdf")],
            output_dir=Path("saida"),
            split_output=False,
            max_chunk_characters=1000,
        )

        thread = threading.Thread(target=app._convert_in_parallel, args=(request, 2))
        thread.start()
        thread.join(timeout=10)

        self.assertFalse(thread.is_alive(), "conversão paralela travou ao ser parada antes de submeter qualquer arquivo")

        received_events = []
        while not app.events.empty():
            received_events.append(app.events.get_nowait())
        self.assertEqual(received_events[-1][0], "stopped")
        summary = received_events[-1][1]
        self.assertEqual(len(summary.successes), 0)
        self.assertEqual(len(summary.failures), 0)

    def test_resolve_worker_count_uses_a_single_worker_for_one_file(self) -> None:
        app = ui.App.__new__(ui.App)

        self.assertEqual(app._resolve_worker_count(1), 1)
        self.assertEqual(app._resolve_worker_count(0), 1)

    def test_resolve_worker_count_caps_at_max_parallel_workers_and_cpu_count(self) -> None:
        app = ui.App.__new__(ui.App)

        with patch.object(ui.os, "cpu_count", return_value=8):
            self.assertEqual(app._resolve_worker_count(10), ui.MAX_PARALLEL_WORKERS)
            self.assertEqual(app._resolve_worker_count(2), 2)

        with patch.object(ui.os, "cpu_count", return_value=1):
            self.assertEqual(app._resolve_worker_count(10), 1)

    def test_set_progress_updates_percentage_label_and_bar(self) -> None:
        app = ui.App.__new__(ui.App)
        app._active_extractions = 0
        progress_state: dict[str, int] = {}
        app.progress = SimpleNamespace(configure=lambda **kwargs: progress_state.update(kwargs))
        app.progress_label = SimpleNamespace(
            set=lambda value: progress_state.update(label=value),
            get=lambda: progress_state.get("label", "0%"),
        )

        app._set_progress(2, 5)

        self.assertEqual(progress_state["maximum"], 5)
        self.assertEqual(progress_state["value"], 2)
        self.assertEqual(progress_state["label"], "40%")

    def test_converter_rejects_pdf_above_page_limit(self) -> None:
        class FakeDocument:
            page_count = MAX_PAGE_COUNT + 1

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

        fake_pymupdf = SimpleNamespace(open=lambda _source: FakeDocument())

        converter = ui.PdfMarkdownConverter.__new__(ui.PdfMarkdownConverter)
        converter._pymupdf = fake_pymupdf
        converter._to_markdown = lambda *_args, **_kwargs: "não deveria converter"

        with self.assertRaisesRegex(ValueError, "limite do aplicativo"):
            converter.convert(
                source=Path("grande.pdf"),
                output_dir=Path("saida"),
                split_output=False,
                max_chunk_characters=1000,
            )

    def test_build_conversion_request_threads_toc_and_heading_profile_choices(self) -> None:
        app = ui.App.__new__(ui.App)
        app.files = [Path("documento.pdf")]
        app.split_output = SimpleNamespace(get=lambda: False)
        app.include_toc = SimpleNamespace(get=lambda: True)
        app.max_chunk_characters = SimpleNamespace(get=lambda: "60000")
        app.heading_profile = SimpleNamespace(get=lambda: "curso")

        # validate_runtime_dependencies() importa pymupdf4llm de verdade; se a
        # dependência não estiver instalada no Python usado para rodar os
        # testes (comum quando não é o .venv do projeto), ela levanta
        # RuntimeError e o código de tratamento abre um messagebox.showerror
        # real, que trava o teste esperando um clique que nunca chega. Mockar
        # aqui torna o teste independente do ambiente de execução.
        with (
            tempfile.TemporaryDirectory() as tmp_dir,
            patch.object(ui, "validate_runtime_dependencies"),
        ):
            app.output_dir = SimpleNamespace(get=lambda: tmp_dir)
            request = app._build_conversion_request()

        self.assertTrue(request.include_toc)
        self.assertEqual(request.heading_profile, "curso")

    def test_converter_converts_whole_document_in_a_single_call(self) -> None:
        # Chamar to_markdown por página, isoladamente, faz os níveis de título
        # (#/##) serem calculados por página em vez de pelo documento inteiro,
        # deixando a estrutura de títulos inconsistente entre páginas.
        class FakeDocument:
            page_count = 3

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

        calls: list[tuple[object, dict]] = []

        def fake_to_markdown(doc, **kwargs):
            calls.append((doc, kwargs))
            return "conteudo"

        fake_document = FakeDocument()
        converter = ui.PdfMarkdownConverter.__new__(ui.PdfMarkdownConverter)
        converter._pymupdf = SimpleNamespace(open=lambda _source: fake_document)
        converter._to_markdown = fake_to_markdown

        with (
            patch.object(
                converter_module,
                "output_paths",
                return_value=(
                    Path.cwd() / "single-call-test.md",
                    Path.cwd() / "assets" / "single-call-test",
                ),
            ),
            patch.object(converter_module, "finalize_markdown"),
        ):
            converter.convert(
                source=Path("documento.pdf"),
                output_dir=Path.cwd(),
                split_output=False,
                max_chunk_characters=1000,
            )

        self.assertEqual(len(calls), 1)
        called_doc, called_kwargs = calls[0]
        self.assertIs(called_doc, fake_document)
        self.assertNotIn("pages", called_kwargs)

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
