"""Regressões da Fase 6: limites defensivos e processamento em blocos."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import fitz

from constants import (
    MAX_ANNOTATION_TEXT_CHARACTERS,
    MAX_ANNOTATIONS_PER_OPERATION,
    MAX_POINTS_PER_STROKE,
    MAX_RENDER_DPI,
    MAX_TRANSLATION_CHARACTERS,
    MAX_TTS_CHARACTERS,
)
from web_api import MAX_PARALLEL_WORKERS, BridgeApi, _validate_annotation_payload


class Phase6DefensiveLimitsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.pdf_path = self._make_pdf("limites.pdf")
        self.api = BridgeApi(conversion_journal_path=self.root / "conversion-journal.json")
        self.file_id = self.api._register_pdf(self.pdf_path, "test")["file_id"]

    def _make_pdf(self, name: str, width: float = 595, height: float = 842) -> Path:
        path = self.root / name
        document = fitz.open()
        document.new_page(width=width, height=height)
        document.save(path)
        document.close()
        return path

    def test_render_and_snippet_reject_invalid_dpi_with_functional_errors(self) -> None:
        for value in (0, MAX_RENDER_DPI + 1, "inválido", float("inf")):
            with self.subTest(value=value):
                result = self.api.render_page_hq(self.file_id, 0, value)
                self.assertFalse(result["ok"])
                self.assertIn("DPI", result["error"])

        result = self.api.extract_snippet(
            {"file_id": self.file_id, "page_number": 0, "rect": [0, 0, 100, 100], "dpi": MAX_RENDER_DPI + 1}
        )
        self.assertFalse(result["ok"])
        self.assertIn("DPI", result["error"])

    def test_render_rejects_page_that_exceeds_pixel_budget_before_rasterizing(self) -> None:
        large_pdf = self._make_pdf("pagina-gigante.pdf", width=20_000, height=20_000)
        file_id = self.api._register_pdf(large_pdf, "test")["file_id"]
        result = self.api.render_page_hq(file_id, 0, 36)
        self.assertFalse(result["ok"])
        self.assertIn("pixels", result["error"])
        oversized_clip = self.api.extract_snippet(
            {"file_id": file_id, "page_number": 0, "rect": [0, 0, 3_000, 3_000], "dpi": 36}
        )
        self.assertFalse(oversized_clip["ok"])
        self.assertIn("área", oversized_clip["error"].lower())

    def test_snippet_normalizes_reversed_rect_and_rejects_invalid_geometry(self) -> None:
        valid = self.api.extract_snippet(
            {"file_id": self.file_id, "page_number": 0, "rect": [200, 200, 50, 50], "dpi": 72}
        )
        self.assertTrue(valid["ok"], valid.get("error"))

        for rect in ([1, 2, 3], [900, 900, 950, 950], [0, 0, float("nan"), 10], [0, 0, 200_000, 10]):
            with self.subTest(rect=rect):
                result = self.api.extract_snippet(
                    {"file_id": self.file_id, "page_number": 0, "rect": rect, "dpi": 72}
                )
                self.assertFalse(result["ok"])
                self.assertTrue(result["error"])

    def test_annotation_limits_fail_before_writing_the_pdf(self) -> None:
        original = self.pdf_path.read_bytes()
        valid_highlight = {"type": "highlight", "page_number": 0, "rect": [20, 20, 80, 40]}
        payloads = [
            [valid_highlight] * (MAX_ANNOTATIONS_PER_OPERATION + 1),
            [{"type": "unknown", "page_number": 0}],
            [
                {
                    "type": "ink",
                    "page_number": 0,
                    "strokes": [[[20, 20]] * (MAX_POINTS_PER_STROKE + 1)],
                    "width": 2,
                }
            ],
            [
                {
                    "type": "text",
                    "page_number": 0,
                    "x": 20,
                    "y": 20,
                    "text": "x" * (MAX_ANNOTATION_TEXT_CHARACTERS + 1),
                }
            ],
            [{"type": "highlight", "page_number": 0, "rect": [0, 0, float("inf"), 20]}],
            [
                {
                    "type": "ink",
                    "page_number": 0,
                    "strokes": [[[20, 20], [30, 30]]],
                    "width": 73,
                }
            ],
            [{"type": "text", "page_number": 0, "x": 20, "y": 20, "fontsize": 73, "text": "x"}],
        ]
        for annotation_items in payloads:
            with self.subTest(size=len(annotation_items)):
                result = self.api.save_pdf_annotations({"file_id": self.file_id, "annotations": annotation_items})
                self.assertFalse(result["ok"])
                self.assertEqual(self.pdf_path.read_bytes(), original)

    def test_annotation_uses_the_normalized_and_clipped_rectangle(self) -> None:
        document = fitz.open(self.pdf_path)
        self.addCleanup(document.close)
        validated = _validate_annotation_payload(
            [{"type": "highlight", "page_number": 0, "rect": [100, 60, -50, 20]}],
            document,
        )
        self.assertEqual(validated[0]["rect"], [0.0, 20.0, 100.0, 60.0])

    def test_translation_uses_bounded_chunks_without_discarding_text(self) -> None:
        text = "trecho jurídico " * 400
        with patch("web_api.GoogleTranslator") as translator_class:
            translator_class.return_value.translate.side_effect = lambda chunk: chunk
            result = self.api.translate_text(text, target_lang="en")

        self.assertTrue(result["ok"])
        self.assertEqual(result["translated_text"], text.strip())
        self.assertGreater(translator_class.return_value.translate.call_count, 1)
        for call in translator_class.return_value.translate.call_args_list:
            self.assertLessEqual(len(call.args[0]), 4_500)

        spaced = "a" * 4_490 + "\n\n" + "b" * 100
        with patch("web_api.GoogleTranslator") as translator_class:
            translator_class.return_value.translate.side_effect = lambda chunk: chunk.upper()
            spaced_result = self.api.translate_text(spaced, target_lang="en")
        self.assertEqual(spaced_result["translated_text"], spaced.upper())

        too_large = self.api.translate_text("x" * (MAX_TRANSLATION_CHARACTERS + 1))
        self.assertFalse(too_large["ok"])
        self.assertIn(str(MAX_TRANSLATION_CHARACTERS), too_large["error"])

    def test_tts_replaces_silent_truncation_with_bounded_chunks(self) -> None:
        text = "voz " * 2_501
        observed_chunks: list[str] = []

        class MockCommunicate:
            def __init__(self, chunk: str, *_args, **_kwargs) -> None:
                observed_chunks.append(chunk)

            async def stream(self):
                yield {"type": "audio", "data": b"mp3"}

        with patch("web_api.edge_tts.Communicate", MockCommunicate):
            result = self.api.synthesize_speech(text)

        self.assertTrue(result["ok"])
        self.assertEqual("".join(observed_chunks), text.strip())
        self.assertGreater(result["chunk_count"], 1)
        self.assertEqual(len(result["audio_segments"]), result["chunk_count"])
        self.assertTrue(all(len(chunk) <= 5_000 for chunk in observed_chunks))

        too_large = self.api.synthesize_speech("x" * (MAX_TTS_CHARACTERS + 1))
        self.assertFalse(too_large["ok"])
        self.assertIn(str(MAX_TTS_CHARACTERS), too_large["error"])

    def test_worker_override_never_exceeds_defensive_ceiling(self) -> None:
        with patch("web_api.os.cpu_count", return_value=64):
            self.assertEqual(self.api._resolve_worker_count(100, 999), MAX_PARALLEL_WORKERS)
        with patch("web_api.require_software_activation", return_value="NXJ-TEST"):
            result = self.api.start_conversion(
                {"files": [{"file_id": "x"}], "max_workers": MAX_PARALLEL_WORKERS + 1}
            )
        self.assertFalse(result["started"])
        self.assertIn("workers", result["error"])


if __name__ == "__main__":
    unittest.main()
