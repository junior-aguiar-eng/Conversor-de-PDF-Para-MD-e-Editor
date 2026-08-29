from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import converter as converter_module
import markdown_utils
from markdown_utils import finalize_markdown, reserve_batch_output_paths, split_markdown
from models import OutputReservation, PageCoverage


class Phase3IntegrityTests(unittest.TestCase):
    def test_batch_reservation_covers_markdown_assets_and_parts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            (root / "contrato.md").write_text("anterior", encoding="utf-8")
            reservations = reserve_batch_output_paths(
                root,
                [Path("A:/contrato.pdf"), Path("B:/contrato.pdf")],
            )

            self.assertEqual([item.markdown_path.name for item in reservations], ["contrato (2).md", "contrato (3).md"])
            self.assertEqual(len({item.assets_dir for item in reservations}), 2)
            self.assertEqual(len({item.chunks_dir for item in reservations}), 2)

    def test_page_coverage_preserves_successes_and_marks_isolated_failure(self) -> None:
        class FakePage:
            def __init__(self, number: int) -> None:
                self.number = number

            def get_text(self, _kind: str = "text") -> str:
                return "texto recuperado" if self.number == 1 else ""

            def get_images(self) -> list[int]:
                return [1] if self.number == 3 else []

        class FakeDocument:
            page_count = 4

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def load_page(self, index: int) -> FakePage:
                return FakePage(index)

        def native(_document, **kwargs):
            page = kwargs["pages"][0]
            if page in {1, 3}:
                raise RuntimeError("extração nativa indisponível")
            return f"conteúdo nativo {page + 1}"

        def ocr(page, dpi):
            if page.number == 2:
                return "conteúdo OCR 3"
            raise RuntimeError("OCR indisponível")

        converter = converter_module.PdfMarkdownConverter.__new__(converter_module.PdfMarkdownConverter)
        converter._pymupdf = SimpleNamespace(open=lambda _source: FakeDocument())
        converter._to_markdown = native

        with (
            tempfile.TemporaryDirectory() as tmp_dir,
            patch.object(converter_module, "require_software_activation", return_value="NXJ-TEST"),
            patch.object(converter_module, "is_scanned_page", side_effect=lambda page: page.number == 2),
            patch.object(converter_module, "ocr_page_to_markdown", side_effect=ocr),
        ):
            result = converter.convert(Path("documento.pdf"), Path(tmp_dir), False, 60_000)
            markdown = result.markdown_path.read_text(encoding="utf-8")

        self.assertEqual([item.status for item in result.page_coverage], ["native", "fallback", "ocr", "failed"])
        self.assertEqual(result.failed_pages, (4,))
        self.assertIn("conteúdo nativo 1", markdown)
        self.assertIn("texto recuperado", markdown)
        self.assertIn("conteúdo OCR 3", markdown)
        self.assertIn("Página 4 não pôde ser recuperada", markdown)
        self.assertIn("NEXOJURIS_PAGE page=4 status=failed", markdown)

    def test_retry_converts_only_selected_pages_to_separate_output(self) -> None:
        loaded: list[int] = []

        class FakePage:
            def __init__(self, number: int) -> None:
                self.number = number

            def get_text(self, _kind: str = "text") -> str:
                return ""

            def get_images(self) -> list[object]:
                return []

        class FakeDocument:
            page_count = 5

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def load_page(self, index: int) -> FakePage:
                loaded.append(index)
                return FakePage(index)

        converter = converter_module.PdfMarkdownConverter.__new__(converter_module.PdfMarkdownConverter)
        converter._pymupdf = SimpleNamespace(open=lambda _source: FakeDocument())
        converter._to_markdown = lambda _doc, **kwargs: f"página {kwargs['pages'][0] + 1}"
        with (
            tempfile.TemporaryDirectory() as tmp_dir,
            patch.object(converter_module, "require_software_activation", return_value="NXJ-TEST"),
            patch.object(converter_module, "is_scanned_page", return_value=False),
        ):
            result = converter.convert(
                Path("documento.pdf"), Path(tmp_dir), False, 60_000, page_numbers=(2, 5)
            )

        self.assertEqual(loaded, [1, 4])
        self.assertEqual([item.page_number for item in result.page_coverage], [2, 5])
        self.assertIn("páginas reprocessadas", result.markdown_path.stem)

    def test_semantic_and_strict_split_preserve_protected_blocks(self) -> None:
        table = "| A | B |\n|---|---|\n| 1 | 2 |"
        code = "```python\nprint('conteúdo longo')\n```"
        image = "Legenda antes ![figura](images/doc/figura.png) legenda depois"
        markdown = f"# Título\n\n{'texto ' * 40}\n\n{table}\n\n{code}\n\n{image}\n\n{'fim ' * 40}"

        semantic = split_markdown(markdown, 100, "semantic")
        strict = split_markdown(markdown, 100, "strict")

        self.assertGreater(len(semantic), 1)
        self.assertTrue(all(block in "\n\n".join(semantic) for block in (table, code, image)))
        self.assertTrue(all(block in "\n\n".join(strict) for block in (table, code, image)))
        self.assertTrue(all(len(chunk) <= 100 for chunk in strict))

    def test_transaction_rolls_back_only_its_own_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            sentinel = root / "nao-tocar.txt"
            sentinel.write_text("preservado", encoding="utf-8")
            reservation = OutputReservation(root / "doc.md", root / "images" / "doc", root / "doc_partes")
            reservation.assets_dir.parent.mkdir()
            temporary_assets = Path(tempfile.mkdtemp(prefix=".assets.", dir=reservation.assets_dir.parent))
            (temporary_assets / "figura.png").write_bytes(b"png")
            real_replace = os.replace

            def fail_markdown(source, destination):
                if Path(destination) == reservation.markdown_path:
                    raise OSError("falha de promoção simulada")
                return real_replace(source, destination)

            with patch.object(markdown_utils.os, "replace", side_effect=fail_markdown), self.assertRaises(OSError):
                finalize_markdown(
                    Path("doc.pdf"), reservation.markdown_path, "texto " * 40, 1, True, 50,
                    page_coverage=(PageCoverage(1, "native"),), reservation=reservation,
                    temporary_assets_dir=temporary_assets,
                )

            self.assertEqual(sentinel.read_text(encoding="utf-8"), "preservado")
            self.assertFalse(reservation.markdown_path.exists())
            self.assertFalse(reservation.assets_dir.exists())
            self.assertFalse(reservation.chunks_dir.exists())
            self.assertFalse(any(path.name.startswith(".doc") for path in root.iterdir()))


if __name__ == "__main__":
    unittest.main()
