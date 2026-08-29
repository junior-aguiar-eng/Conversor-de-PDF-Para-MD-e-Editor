from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import converter as converter_module
from constants import (
    APP_NAME,
    DEFAULT_MAX_CHUNK_CHARACTERS,
    DEFAULT_OUTPUT_DIR,
    MAX_CONVERSION_MEMORY_BYTES,
    MAX_PAGE_COUNT,
)
from licensing import LicenseRequiredError
from models import (
    BatchConversionSummary,
    ConversionFailure,
    ConversionResult,
    build_summary_message,
    format_duration,
)


class ConverterTests(unittest.TestCase):
    def setUp(self) -> None:
        activation_patcher = patch.object(converter_module, "require_software_activation", return_value="NXJ-TEST")
        activation_patcher.start()
        self.addCleanup(activation_patcher.stop)

    def test_app_name_is_nexojuris_conversor(self) -> None:
        self.assertEqual(APP_NAME, "NexoJuris - Conversor")

    def test_default_output_dir_points_to_pdfs_convertidos(self) -> None:
        self.assertEqual(DEFAULT_OUTPUT_DIR.name, "PDFs Convertidos")

    def test_default_chunk_size_is_sixty_thousand(self) -> None:
        self.assertEqual(DEFAULT_MAX_CHUNK_CHARACTERS, 60_000)

    def test_image_markdown_reference_resolves_to_saved_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            output_dir = Path(tmp_dir) / "saida"
            assets_dir = output_dir / "images" / "documento-abcd1234"

            markdown_ref, saved_path = converter_module._safe_image_md_path(
                str(assets_dir),
                "pagina-1.png",
            )

            self.assertEqual(markdown_ref, "images/documento-abcd1234/pagina-1.png")
            self.assertEqual((output_dir / markdown_ref).resolve(), Path(saved_path).resolve())
            Path(saved_path).write_bytes(b"imagem")
            self.assertTrue((output_dir / markdown_ref).is_file())

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

    def test_converter_rejects_unlicensed_machine_before_opening_pdf(self) -> None:
        fake_open = unittest.mock.MagicMock()
        converter = converter_module.PdfMarkdownConverter.__new__(converter_module.PdfMarkdownConverter)
        converter._pymupdf = SimpleNamespace(open=fake_open)

        with (
            patch.object(
                converter_module,
                "require_software_activation",
                side_effect=LicenseRequiredError("NXJ-1111-2222-3333-4444"),
            ),
            self.assertRaises(LicenseRequiredError),
        ):
            converter.convert(Path("documento.pdf"), Path("saida"), False, 1000)

        fake_open.assert_not_called()

    def test_prevalidated_batch_does_not_repeat_license_check_in_converter(self) -> None:
        class OversizedDocument:
            page_count = MAX_PAGE_COUNT + 1

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

        converter = converter_module.PdfMarkdownConverter.__new__(converter_module.PdfMarkdownConverter)
        converter._pymupdf = SimpleNamespace(open=lambda _source: OversizedDocument())
        converter._to_markdown = lambda *_args, **_kwargs: ""

        with (
            patch.object(converter_module, "require_software_activation") as activation_check,
            self.assertRaisesRegex(ValueError, "limite do aplicativo"),
        ):
            converter.convert(
                Path("documento.pdf"),
                Path("saida"),
                False,
                1000,
                activation_verified=True,
            )
        activation_check.assert_not_called()

    def test_converter_rejects_source_above_file_size_budget(self) -> None:
        converter = converter_module.PdfMarkdownConverter.__new__(converter_module.PdfMarkdownConverter)
        with tempfile.TemporaryDirectory() as tmp_dir:
            source = Path(tmp_dir) / "grande.pdf"
            source.write_bytes(b"pdf")
            with (
                patch.object(converter_module, "MAX_PDF_FILE_SIZE_BYTES", 2),
                self.assertRaisesRegex(converter_module.ResourceBudgetExceeded, "limite de 0 MB"),
            ):
                converter.convert(source, Path(tmp_dir) / "saida", False, 1000)

    def test_converter_rejects_process_memory_above_budget(self) -> None:
        converter = converter_module.PdfMarkdownConverter.__new__(converter_module.PdfMarkdownConverter)
        with (
            tempfile.TemporaryDirectory() as tmp_dir,
            patch.object(
                converter_module,
                "current_process_rss_bytes",
                return_value=MAX_CONVERSION_MEMORY_BYTES + 1,
            ),
            self.assertRaisesRegex(converter_module.ResourceBudgetExceeded, "limite de memória"),
        ):
            converter.convert(Path("documento.pdf"), Path(tmp_dir), False, 1000)

    def test_converter_rejects_expired_document_deadline(self) -> None:
        converter = converter_module.PdfMarkdownConverter.__new__(converter_module.PdfMarkdownConverter)
        with (
            tempfile.TemporaryDirectory() as tmp_dir,
            patch.object(converter_module, "MAX_CONVERSION_SECONDS", -1),
            self.assertRaisesRegex(converter_module.ResourceBudgetExceeded, "prazo"),
        ):
            converter.convert(Path("documento.pdf"), Path(tmp_dir), False, 1000)

    def test_converter_tracks_every_page_independently(self) -> None:
        class FakePage:
            def __init__(self, number: int) -> None:
                self.number = number

            def get_text(self, _kind: str = "text") -> str:
                return ""

            def get_images(self) -> list[object]:
                return []

        class FakeDocument:
            page_count = 3

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            def load_page(self, page_index: int) -> FakePage:
                return FakePage(page_index)

        calls: list[tuple[object, dict]] = []

        def fake_to_markdown(doc, **kwargs):
            calls.append((doc, kwargs))
            return f"conteudo pagina {kwargs['pages'][0] + 1}"

        fake_document = FakeDocument()
        converter = converter_module.PdfMarkdownConverter.__new__(converter_module.PdfMarkdownConverter)
        converter._pymupdf = SimpleNamespace(open=lambda _source: fake_document)
        converter._to_markdown = fake_to_markdown

        with (
            tempfile.TemporaryDirectory() as tmp_dir,
            patch.object(converter_module, "is_scanned_page", return_value=False),
        ):
            result = converter.convert(
                source=Path("documento.pdf"),
                output_dir=Path(tmp_dir),
                split_output=False,
                max_chunk_characters=1000,
            )

            self.assertEqual(result.failed_pages, ())
            self.assertEqual([item.status for item in result.page_coverage], ["native"] * 3)
            self.assertIn("conteudo pagina 3", result.markdown_path.read_text(encoding="utf-8"))

        self.assertEqual(len(calls), 3)
        self.assertEqual([kwargs["pages"] for _, kwargs in calls], [[0], [1], [2]])

    def test_converter_rejects_document_above_image_budget(self) -> None:
        class FakePage:
            def get_images(self, full: bool = False) -> list[object]:
                return [object()]

        class FakeDocument:
            page_count = 1

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            def load_page(self, _page_index: int) -> FakePage:
                return FakePage()

        converter = converter_module.PdfMarkdownConverter.__new__(converter_module.PdfMarkdownConverter)
        converter._pymupdf = SimpleNamespace(open=lambda _source: FakeDocument())

        with (
            tempfile.TemporaryDirectory() as tmp_dir,
            patch.object(converter_module, "MAX_IMAGES_PER_DOCUMENT", 0),
            self.assertRaisesRegex(converter_module.ResourceBudgetExceeded, "limite de 0 imagens"),
        ):
            converter.convert(Path("documento.pdf"), Path(tmp_dir), False, 1000)

    def test_converter_resumes_from_last_page_checkpoint(self) -> None:
        class FakePage:
            def __init__(self, number: int) -> None:
                self.number = number

            def get_images(self, full: bool = False) -> list[object]:
                return []

        class FakeDocument:
            page_count = 3

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            def load_page(self, page_index: int) -> FakePage:
                return FakePage(page_index)

        calls: list[int] = []
        converter = converter_module.PdfMarkdownConverter.__new__(converter_module.PdfMarkdownConverter)
        converter._pymupdf = SimpleNamespace(open=lambda _source: FakeDocument())
        converter._to_markdown = lambda _doc, **kwargs: calls.append(kwargs["pages"][0]) or f"página {kwargs['pages'][0] + 1}"

        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            source = root / "documento.pdf"
            source.write_bytes(b"%PDF checkpoint")
            output = root / "saida"
            checkpoint = root / "checkpoint"
            with (
                patch.object(converter_module, "is_scanned_page", return_value=False),
                patch.object(
                    converter_module,
                    "_directory_usage",
                    side_effect=[(0, 0), RuntimeError("interrupção")],
                ),
                self.assertRaisesRegex(RuntimeError, "interrupção"),
            ):
                converter.convert(source, output, False, 1000, checkpoint_dir=checkpoint)

            self.assertTrue((checkpoint / "pages" / "000001.md").is_file())
            with patch.object(converter_module, "is_scanned_page", return_value=False):
                result = converter.convert(source, output, False, 1000, checkpoint_dir=checkpoint)

            self.assertEqual(calls, [0, 1, 2])
            self.assertIn("página 3", result.markdown_path.read_text(encoding="utf-8"))
            self.assertFalse(checkpoint.exists())


if __name__ == "__main__":
    unittest.main()
