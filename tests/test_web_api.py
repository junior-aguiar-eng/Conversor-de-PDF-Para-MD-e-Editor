from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from constants import DEFAULT_MAX_CHUNK_CHARACTERS, MAX_PAGE_COUNT
from library_db import LibraryDatabase
from models import ConversionResult
from web_api import (
    BridgeApi,
    format_duration,
    format_file_size,
)


class WebApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.storage_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.storage_dir.cleanup)
        test_db_path = Path(self.storage_dir.name) / "web_api_acervo.db"
        library_patcher = patch(
            "web_api.LibraryDatabase",
            side_effect=lambda: LibraryDatabase(test_db_path),
        )
        library_patcher.start()
        self.addCleanup(library_patcher.stop)

        activation_patcher = patch("web_api.require_software_activation", return_value="NXJ-TEST")
        activation_patcher.start()
        self.addCleanup(activation_patcher.stop)

    def test_format_duration(self) -> None:
        self.assertEqual(format_duration(30), "30s")
        self.assertEqual(format_duration(65), "1m 5s")
        self.assertEqual(format_duration(120), "2m 0s")

    def test_format_file_size(self) -> None:
        self.assertEqual(format_file_size(500), "500 B")
        self.assertEqual(format_file_size(2048), "2.0 KB")
        self.assertEqual(format_file_size(2 * 1024 * 1024), "2.0 MB")

    def test_get_app_info_returns_expected_metadata(self) -> None:
        api = BridgeApi()
        info = api.get_app_info()
        self.assertEqual(info["app_name"], "NexoJuris - Conversor")
        self.assertIn("app_version", info)
        self.assertIn("default_output_dir", info)
        self.assertEqual(info["default_chunk_limit"], DEFAULT_MAX_CHUNK_CHARACTERS)
        self.assertEqual(info["max_page_count"], MAX_PAGE_COUNT)

    def test_process_file_paths_filters_non_pdfs(self) -> None:
        api = BridgeApi()
        with tempfile.TemporaryDirectory() as tmp_dir:
            pdf_file = Path(tmp_dir) / "teste.pdf"
            txt_file = Path(tmp_dir) / "teste.txt"
            pdf_file.write_bytes(b"%PDF-1.4 test")
            txt_file.write_text("not a pdf")

            api.set_window(SimpleNamespace(create_confirmation_dialog=lambda *_: True))
            processed = api.register_dropped_files([str(pdf_file), str(txt_file), "arquivo_inexistente.pdf"])
            self.assertEqual(len(processed), 1)
            self.assertEqual(processed[0]["name"], "teste.pdf")
            self.assertTrue(processed[0]["file_id"])
            self.assertGreater(processed[0]["size"], 0)

    def test_read_markdown_preview_returns_content(self) -> None:
        api = BridgeApi()
        with tempfile.TemporaryDirectory() as tmp_dir:
            md_file = Path(tmp_dir) / "documento.md"
            md_file.write_text("# Título\n\nConteúdo em markdown.", encoding="utf-8")

            markdown_id = api._register_markdown(md_file, "test")["markdown_id"]
            res = api.read_markdown_preview(markdown_id)
            self.assertTrue(res["ok"])
            self.assertEqual(res["name"], "documento.md")
            self.assertIn("# Título", res["content"])

    def test_read_markdown_preview_handles_missing_file(self) -> None:
        api = BridgeApi()
        res = api.read_markdown_preview("inexistente.md")
        self.assertFalse(res["ok"])
        self.assertIn("não autorizado", res["error"])

    def test_start_conversion_validates_empty_files(self) -> None:
        api = BridgeApi()
        res = api.start_conversion({"files": []})
        self.assertFalse(res["started"])
        self.assertIn("Nenhum PDF", res["error"])

    def test_start_conversion_validates_small_chunk_limit(self) -> None:
        api = BridgeApi()
        res = api.start_conversion({"files": [{"file_id": "desconhecido"}], "max_chunk_characters": 500})
        self.assertFalse(res["started"])
        self.assertIn("pelo menos 1.000", res["error"])

    def test_save_annotations_expands_textbox_and_persists_long_text(self) -> None:
        import fitz

        with tempfile.TemporaryDirectory() as tmp_dir:
            pdf_path = Path(tmp_dir) / "texto-longo.pdf"
            doc = fitz.open()
            doc.new_page()
            doc.save(pdf_path)
            doc.close()

            api = BridgeApi()
            file_id = api._register_pdf(pdf_path, "test")["file_id"]
            long_text = "Texto longo efetivamente persistido no documento. " * 12
            result = api.save_pdf_annotations(
                {
                    "file_id": file_id,
                    "annotations": [
                        {
                            "type": "text",
                            "page_number": 0,
                            "x": 50,
                            "y": 50,
                            "width": 180,
                            "height": 45,
                            "fontsize": 14,
                            "style": "none",
                            "text": long_text,
                        }
                    ],
                }
            )

            self.assertTrue(result["ok"])
            self.assertEqual(result["saved_count"], 1)
            with fitz.open(pdf_path) as saved_doc:
                self.assertIn("Texto longo efetivamente", saved_doc[0].get_text())

    def test_save_annotations_does_not_claim_success_when_text_cannot_fit(self) -> None:
        import fitz

        with tempfile.TemporaryDirectory() as tmp_dir:
            pdf_path = Path(tmp_dir) / "sem-espaco.pdf"
            doc = fitz.open()
            doc.new_page()
            doc.save(pdf_path)
            doc.close()
            original_bytes = pdf_path.read_bytes()

            api = BridgeApi()
            file_id = api._register_pdf(pdf_path, "test")["file_id"]
            result = api.save_pdf_annotations(
                {
                    "file_id": file_id,
                    "annotations": [
                        {
                            "type": "text",
                            "page_number": 0,
                            "x": 50,
                            "y": 820,
                            "width": 100,
                            "height": 20,
                            "fontsize": 14,
                            "style": "none",
                            "text": "Texto sem espaço vertical disponível. " * 10,
                        }
                    ],
                }
            )

            self.assertFalse(result["ok"])
            self.assertIn("não cabe", result["error"])
            self.assertEqual(pdf_path.read_bytes(), original_bytes)

    def test_resolve_worker_count_bounds(self) -> None:
        api = BridgeApi()
        self.assertEqual(api._resolve_worker_count(1), 1)
        self.assertEqual(api._resolve_worker_count(0), 1)

        with patch("web_api.os.cpu_count", return_value=8):
            self.assertEqual(api._resolve_worker_count(10), 4)
            self.assertEqual(api._resolve_worker_count(2), 2)

        with patch("web_api.os.cpu_count", return_value=1):
            self.assertEqual(api._resolve_worker_count(10), 1)

    def test_toggle_pause_and_request_stop(self) -> None:
        api = BridgeApi()
        # Não convertendo:
        self.assertFalse(api.toggle_pause()["is_paused"])
        self.assertFalse(api.request_stop())

        # Em conversão:
        api.is_converting = True
        self.assertTrue(api.toggle_pause()["is_paused"])
        self.assertTrue(api.is_paused)
        self.assertFalse(api.toggle_pause()["is_paused"])
        self.assertFalse(api.is_paused)

        self.assertTrue(api.request_stop())
        self.assertTrue(api.cancel_requested.is_set())

    def test_emit_formats_event_code(self) -> None:
        api = BridgeApi()
        calls = []
        fake_window = SimpleNamespace(evaluate_js=lambda code: calls.append(code))
        api.set_window(fake_window)

        api._emit("status", {"message": "Processando"})
        self.assertEqual(len(calls), 1)
        self.assertIn("window.onBackendEvent", calls[0])
        self.assertIn("Processando", calls[0])

    def test_convert_sequentially_handles_success_and_error(self) -> None:
        api = BridgeApi()
        events_emitted = []
        fake_window = SimpleNamespace(evaluate_js=lambda code: events_emitted.append(code))
        api.set_window(fake_window)

        failure_err = RuntimeError("PDF quebrado")
        success_res = ConversionResult(
            source=Path("bom.pdf"),
            markdown_path=Path("bom.md"),
            asset_count=0,
            chunk_count=0,
        )

        fake_converter = SimpleNamespace(convert=self._convert_side_effect([failure_err, success_res]))

        with patch("web_api.PdfMarkdownConverter", return_value=fake_converter):
            api._convert_sequentially(
                files=[Path("ruim.pdf"), Path("bom.pdf")],
                output_dir=Path("saida"),
                split_output=False,
                max_chunk_characters=60000,
                heading_profile="jurisprudencia",
            )

        emitted_str = " ".join(events_emitted)
        self.assertIn("file_start", emitted_str)
        self.assertIn("file_error", emitted_str)
        self.assertIn("file_success", emitted_str)
        self.assertIn("batch_done", emitted_str)

    def test_convert_in_parallel_does_not_hang_when_stopped_before_any_submission(self) -> None:
        import threading

        api = BridgeApi()
        api.cancel_requested.set()
        api.resume_processing.set()

        events_emitted = []
        fake_window = SimpleNamespace(evaluate_js=lambda code: events_emitted.append(code))
        api.set_window(fake_window)

        thread = threading.Thread(
            target=api._convert_in_parallel,
            args=([Path("a.pdf"), Path("b.pdf")], Path("saida"), False, 60000, "jurisprudencia", 2),
        )
        thread.start()
        thread.join(timeout=10)

        self.assertFalse(thread.is_alive(), "conversão paralela travou ao ser parada antes da submissão")
        emitted_str = " ".join(events_emitted)
        self.assertIn("batch_stopped", emitted_str)

    def test_super_pdf_methods_with_real_pdf(self) -> None:
        import fitz

        api = BridgeApi()
        with tempfile.TemporaryDirectory() as tmp_dir:
            pdf_path = Path(tmp_dir) / "teste_super.pdf"
            doc = fitz.open()
            page = doc.new_page(width=595, height=842)
            page.insert_text((50, 100), "Texto Juridico de Teste para Extracao e Super PDF", fontsize=14)
            doc.save(str(pdf_path))
            doc.close()
            file_id = api._register_pdf(pdf_path, "test")["file_id"]

            # 1. get_pdf_info
            info = api.get_pdf_info(file_id)
            self.assertTrue(info["ok"])
            self.assertEqual(info["page_count"], 1)
            self.assertEqual(len(info["pages"]), 1)

            # 2. render_page_hq (verifica compatibilidade com ambos os formatos image e image_base64)
            render_res = api.render_page_hq(file_id, page_number=0, dpi=72)
            self.assertTrue(render_res["ok"])
            self.assertIn("data:image/png;base64,", render_res["image"])
            self.assertIn("data:image/png;base64,", render_res["image_base64"])
            self.assertEqual(render_res["page_count"], 1)

            # 3. rotate_pdf_page
            rot_res = api.rotate_pdf_page(file_id, page_number=0, degrees=90)
            self.assertTrue(rot_res["ok"])
            self.assertEqual(rot_res["new_rotation"], 90)

            # 4. extract_snippet
            snippet_res = api.extract_snippet(
                {
                    "file_id": file_id,
                    "page_number": 0,
                    "rect": [40, 80, 500, 120],
                    "dpi": 72,
                }
            )
            self.assertTrue(snippet_res["ok"])
            self.assertIn("Texto Juridico", snippet_res["text"])
            self.assertIn("data:image/png;base64,", snippet_res["image_base64"])

            # 5. save_pdf_annotations (ink, highlight, freetext)
            annot_payload = {
                "file_id": file_id,
                "annotations": [
                    {
                        "type": "ink",
                        "page_number": 0,
                        "strokes": [[[50, 50], [100, 100]]],
                        "color": [0.9, 0.1, 0.1],
                        "width": 2.0,
                    },
                    {
                        "type": "highlight",
                        "page_number": 0,
                        "rect": [50, 90, 200, 120],
                        "color": [1.0, 0.9, 0.2],
                    },
                    {
                        "type": "freetext",
                        "page_number": 0,
                        "rect": [50, 200, 300, 250],
                        "text": "Nota do Advogado",
                        "fontsize": 12,
                        "text_color": "#0358A1",
                    },
                ],
            }
            save_annot_res = api.save_pdf_annotations(annot_payload)
            self.assertTrue(save_annot_res["ok"])
            self.assertEqual(save_annot_res["saved_count"], 3)

            # 6. protect_pdf
            prot_res = api.protect_pdf(file_id, user_pw="senha123", owner_pw="mestre123")
            self.assertTrue(prot_res["ok"])

            # Verify encryption
            doc_check = fitz.open(str(pdf_path))
            self.assertTrue(doc_check.is_encrypted)
            auth_res = doc_check.authenticate("senha123")
            self.assertGreater(auth_res, 0)
            doc_check.close()

            # 7. unprotect_pdf
            unprot_res = api.unprotect_pdf(file_id, current_pw="senha123")
            self.assertTrue(unprot_res["ok"])

            # Verify that document is now unencrypted
            doc_unenc = fitz.open(str(pdf_path))
            self.assertFalse(doc_unenc.is_encrypted)
            doc_unenc.close()

    def test_tts_get_available_voices(self) -> None:
        api = BridgeApi()
        res = api.get_available_voices()
        self.assertTrue(res["ok"])
        self.assertIsInstance(res["voices"], list)
        self.assertGreater(len(res["voices"]), 0)
        ids = [v["id"] for v in res["voices"]]
        self.assertIn("pt-BR-FranciscaNeural", ids)
        self.assertIn("pt-BR-AntonioNeural", ids)

    def test_tts_synthesize_speech_validation(self) -> None:
        api = BridgeApi()
        # Texto vazio
        res_empty = api.synthesize_speech("")
        self.assertFalse(res_empty["ok"])
        self.assertIn("Nenhum texto", res_empty["error"])

    def test_translate_text_validation(self) -> None:
        api = BridgeApi()
        # Texto vazio
        res_empty = api.translate_text("")
        self.assertFalse(res_empty["ok"])
        self.assertIn("Nenhum texto", res_empty["error"])

    def test_translate_text_execution(self) -> None:
        api = BridgeApi()
        with patch("web_api.GoogleTranslator") as mock_translator_cls:
            mock_instance = mock_translator_cls.return_value
            mock_instance.translate.return_value = "Olá mundo"
            res = api.translate_text("Hello world", target_lang="pt", source_lang="en")
            self.assertTrue(res["ok"])
            self.assertEqual(res["original_text"], "Hello world")
            self.assertEqual(res["translated_text"], "Olá mundo")

    def test_tts_synthesize_speech_execution(self) -> None:
        api = BridgeApi()
        with patch("web_api.edge_tts.Communicate") as mock_comm_cls:

            class MockComm:
                async def stream(self):
                    yield {"type": "audio", "data": b"fake_mp3_data"}

            mock_comm_cls.return_value = MockComm()
            res = api.synthesize_speech("Teste de áudio neural", voice="pt-BR-FranciscaNeural")
            self.assertTrue(res["ok"])
            self.assertIn("audio_base64", res)
            self.assertTrue(res["audio_base64"].startswith("data:audio/mp3;base64,"))

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
