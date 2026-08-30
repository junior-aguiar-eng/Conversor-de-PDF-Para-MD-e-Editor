from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import fitz

from converter import PdfMarkdownConverter
from library_db import LibraryDatabase
from ocr_engine import is_scanned_page, ocr_pixmap
from web_api import BridgeApi


class OcrEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.storage_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.storage_dir.cleanup)
        activation_patcher = patch("ocr_engine.require_software_activation", return_value="NXJ-TEST")
        activation_patcher.start()
        self.addCleanup(activation_patcher.stop)
        test_db_path = Path(self.storage_dir.name) / "ocr_api_acervo.db"
        library_patcher = patch(
            "web_api.LibraryDatabase",
            side_effect=lambda: LibraryDatabase(test_db_path),
        )
        library_patcher.start()
        self.addCleanup(library_patcher.stop)

    def test_is_scanned_page_with_text(self) -> None:
        doc = fitz.open()
        page = doc.new_page(width=595, height=842)
        page.insert_text(fitz.Point(50, 50), "Este é um texto vetorial longo o suficiente para não ser considerado escaneado.")
        self.assertFalse(is_scanned_page(page, min_char_count=30))
        doc.close()

    def test_is_scanned_page_empty(self) -> None:
        doc = fitz.open()
        page = doc.new_page(width=595, height=842)
        self.assertTrue(is_scanned_page(page))
        doc.close()

    def test_ocr_pixmap_mocked(self) -> None:
        pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 100, 100), 0)
        with patch("ocr_engine.get_ocr_engine") as mock_get_engine:
            mock_engine = mock_get_engine.return_value
            # Formato do RapidOCR: ( [ [box, text, score], ... ], elapse )
            mock_engine.return_value = (
                [
                    [[[10, 10], [90, 10], [90, 30], [10, 30]], "TÍTULO ESCANEADO", 0.98],
                    [[[10, 40], [90, 40], [90, 55], [10, 55]], "Parágrafo escaneado reconhecido com sucesso.", 0.95],
                ],
                [0.1, 0.1, 0.1],
            )
            text, blocks = ocr_pixmap(pix)
            self.assertIn("TÍTULO ESCANEADO", text)
            self.assertIn("Parágrafo escaneado", text)
            self.assertEqual(len(blocks), 2)

    def test_ocr_applies_heading_marker_once_to_consolidated_line(self) -> None:
        pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 200, 200), 0)
        with patch("ocr_engine.get_ocr_engine") as mock_get_engine:
            mock_get_engine.return_value.return_value = (
                [
                    [[[10, 10], [80, 10], [80, 34], [10, 34]], "TÍTULO", 0.99],
                    [[[90, 10], [180, 10], [180, 34], [90, 34]], "COMPLETO", 0.99],
                    [[[10, 50], [80, 50], [80, 60], [10, 60]], "linha", 0.99],
                    [[[90, 50], [180, 50], [180, 60], [90, 60]], "normal", 0.99],
                    [[[10, 75], [80, 75], [80, 85], [10, 85]], "outra", 0.99],
                    [[[90, 75], [180, 75], [180, 85], [90, 85]], "linha", 0.99],
                ],
                [0.1, 0.1, 0.1],
            )

            text, _ = ocr_pixmap(pix)

        self.assertTrue(text.startswith("## TÍTULO COMPLETO"))
        self.assertEqual(text.count("## "), 1)
        self.assertNotIn("TÍTULO ## COMPLETO", text)

    def test_converter_with_scanned_pdf(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            pdf_path = Path(tmp_dir) / "scanned_doc.pdf"
            doc = fitz.open()
            # Página sem texto vetorial (digitalizada)
            doc.new_page(width=595, height=842)
            doc.save(str(pdf_path))
            doc.close()
            converter = PdfMarkdownConverter()
            output_dir = Path(tmp_dir) / "output"

            with (
                patch("converter.require_software_activation", return_value="NXJ-TEST"),
                patch("converter.ocr_page_to_markdown") as mock_ocr_page,
            ):
                mock_ocr_page.return_value = "# Relatório Médico Escaneado\n\nPaciente em bom estado geral."
                res = converter.convert(pdf_path, output_dir, False, 60000)
                self.assertTrue(res.markdown_path.is_file())
                content = res.markdown_path.read_text(encoding="utf-8")
                self.assertIn("Relatório Médico Escaneado", content)

    def test_extract_snippet_triggers_ocr_on_scanned_area(self) -> None:
        api = BridgeApi()
        with tempfile.TemporaryDirectory() as tmp_dir:
            pdf_path = Path(tmp_dir) / "scan_snippet.pdf"
            doc = fitz.open()
            doc.new_page(width=595, height=842)
            doc.save(str(pdf_path))
            doc.close()
            file_id = api._register_pdf(pdf_path, "test")["file_id"]

            with patch("ocr_engine.ocr_pixmap") as mock_ocr:
                mock_ocr.return_value = ("Texto do Carimbo Notarial", [])
                payload = {
                    "file_id": file_id,
                    "page_number": 0,
                    "rect": [50, 50, 200, 200],
                }
                res = api.extract_snippet(payload)
                self.assertTrue(res["ok"])
                self.assertTrue(res["ocr_applied"])
                self.assertEqual(res["text"], "Texto do Carimbo Notarial")


if __name__ == "__main__":
    unittest.main()
