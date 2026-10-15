import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import pymupdf

from converter import _PRIVACY_CONTEXT, PdfMarkdownConverter
from document_privacy import plan_from_document


class VisualPageTests(unittest.TestCase):
    def test_image_cover_with_identity_does_not_export_pixels_or_run_ocr(self):
        with tempfile.TemporaryDirectory() as folder, pymupdf.open() as document:
            for _ in range(2):
                page = document.new_page()
                page.insert_text((40, 30), 'CPF: 111.222.333-44 Nome: Pessoa de Teste')
                pix = pymupdf.Pixmap(pymupdf.csRGB, (0, 0, 200, 200), False)
                pix.clear_with(150)
                page.insert_image(pymupdf.Rect(60, 80, 500, 700), pixmap=pix)
            privacy = plan_from_document(document)
            converter = PdfMarkdownConverter.__new__(PdfMarkdownConverter)
            converter._to_markdown = Mock(side_effect=AssertionError('unsafe raster export'))
            token = _PRIVACY_CONTEXT.set(privacy)
            try:
                with patch('converter.ocr_page_to_markdown', side_effect=AssertionError('unsafe OCR')):
                    text, coverage = converter._process_page(document, 1, Path(folder))
            finally:
                _PRIVACY_CONTEXT.reset(token)
            self.assertEqual(coverage.content_kind, 'image_text_unverified')
            self.assertEqual(coverage.visual_status, 'omitted_privacy')
            self.assertIsNone(coverage.fidelity_score)
            self.assertNotIn('111.222', text)
            self.assertIn('privacidade', text)

    def test_textual_identity_rectangles_are_redacted_in_working_document_only(self):
        with pymupdf.open() as document:
            for _ in range(2):
                page = document.new_page()
                page.insert_text((40, 30), 'CPF: 111.222.333-44 Nome: Pessoa de Teste')
                page.insert_text((72, 150), 'Corpo preservado.')
            privacy = plan_from_document(document)
            privacy.redact_working_document(document)
            self.assertNotIn('111.222', document[0].get_text())
            self.assertIn('Corpo preservado', document[0].get_text())

    def test_vector_diagram_is_not_declared_blank_when_extraction_fails(self):
        with pymupdf.open() as document:
            page = document.new_page()
            page.draw_rect(pymupdf.Rect(100, 100, 200, 200), fill=(0, 0, 0))
            converter = PdfMarkdownConverter.__new__(PdfMarkdownConverter)
            converter._to_markdown = lambda *_a, **_kw: ''
            with patch('converter.ocr_page_to_markdown', return_value=''):
                _, status, warning = converter._extract_page(document, page, 0, False, '.')
            self.assertNotEqual(status, 'empty')
            self.assertIn('visual', warning)


if __name__ == '__main__':
    unittest.main()
