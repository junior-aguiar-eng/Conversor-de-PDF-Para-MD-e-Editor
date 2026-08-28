from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import converter as converter_module
from constants import (
    APP_NAME,
    DEFAULT_MAX_CHUNK_CHARACTERS,
    DEFAULT_OUTPUT_DIR,
    MAX_PAGE_COUNT,
)
from models import (
    BatchConversionSummary,
    ConversionFailure,
    ConversionResult,
    build_summary_message,
    format_duration,
)


class ConverterTests(unittest.TestCase):
    def test_app_name_is_nexojuris_conversor(self) -> None:
        self.assertEqual(APP_NAME, "NexoJuris - Conversor")

    def test_default_output_dir_points_to_pdfs_convertidos(self) -> None:
        self.assertEqual(DEFAULT_OUTPUT_DIR.name, "PDFs Convertidos")

    def test_default_chunk_size_is_sixty_thousand(self) -> None:
        self.assertEqual(DEFAULT_MAX_CHUNK_CHARACTERS, 60_000)

    def test_format_duration(self) -> None:
        self.assertEqual(format_duration(45), "45s")
        self.assertEqual(format_duration(125), "2m 5s")

    def test_build_summary_message_includes_counts_and_failures(self) -> None:
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

        message = build_summary_message(
            "Conversão concluída.",
            r"D:\Saida",
            summary,
            elapsed_seconds=10.0,
        )

        self.assertIn("Convertidos: 1", message)
        self.assertIn("Com erro: 1", message)
        self.assertIn("Tempo total: 10s", message)
        self.assertIn(r"Arquivos salvos em:\nD:\Saida".replace(r"\n", "\n"), message)
        self.assertIn("- erro.pdf", message)

    def test_converter_rejects_pdf_above_page_limit(self) -> None:
        class FakeDocument:
            page_count = MAX_PAGE_COUNT + 1

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

        fake_pymupdf = SimpleNamespace(open=lambda _source: FakeDocument())

        converter = converter_module.PdfMarkdownConverter.__new__(converter_module.PdfMarkdownConverter)
        converter._pymupdf = fake_pymupdf
        converter._to_markdown = lambda *_args, **_kwargs: "não deveria converter"

        with self.assertRaisesRegex(ValueError, "limite do aplicativo"):
            converter.convert(
                source=Path("grande.pdf"),
                output_dir=Path("saida"),
                split_output=False,
                max_chunk_characters=1000,
            )

    def test_converter_converts_whole_document_in_a_single_call(self) -> None:
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
        converter = converter_module.PdfMarkdownConverter.__new__(converter_module.PdfMarkdownConverter)
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


if __name__ == "__main__":
    unittest.main()
