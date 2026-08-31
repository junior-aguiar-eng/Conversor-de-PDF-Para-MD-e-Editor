"""Testes automatizados da Fase 4 — leitor, paginação, indexação e memória."""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import fitz

from library_db import LibraryDatabase
from web_api import BridgeApi


class TestPhase4ReaderAndIndexing(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp_dir.name)
        activation_patcher = patch("web_api.require_software_activation", return_value="NXJ-TEST")
        activation_patcher.start()
        self.addCleanup(activation_patcher.stop)
        self.sample_pdf = self._create_pdf("documento_extenso.pdf", 60)

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def _create_pdf(self, name: str, pages: int) -> Path:
        path = self.root / name
        doc = fitz.open()
        for index in range(pages):
            page = doc.new_page(width=595, height=842)
            page.insert_text((50, 50), f"Conteúdo de teste da página {index + 1} de {pages}.")
        doc.save(str(path))
        doc.close()
        return path

    def _api(self, db_name: str) -> BridgeApi:
        return BridgeApi(library_database_path=self.root / db_name)

    def test_fast_pdf_info_opening(self) -> None:
        api = self._api("fast.db")
        resource = api._register_pdf(self.sample_pdf, "test")
        info = api.get_pdf_info(resource["file_id"])
        self.assertTrue(info["ok"])
        self.assertEqual(info["page_count"], 60)
        self.assertEqual(info["file_name"], "documento_extenso.pdf")
        self.assertFalse(info["is_encrypted"])
        self.assertIn("session_state", info)
        self.assertIn("bookmarks", info)

    def test_get_pdf_page_range(self) -> None:
        api = self._api("range.db")
        resource = api._register_pdf(self.sample_pdf, "test")
        result = api.get_pdf_page_range(resource["file_id"], start_page=10, count=20)
        self.assertTrue(result["ok"])
        self.assertEqual(result["start_page"], 10)
        self.assertEqual(result["count"], 20)
        self.assertEqual(result["total_pages"], 60)
        self.assertEqual(result["pages"][0]["page_number"], 10)
        self.assertEqual(result["pages"][-1]["page_number"], 29)

    def test_upsert_session_state_before_indexing(self) -> None:
        db = LibraryDatabase(self.root / "upsert.db")
        db.save_session_state(str(self.sample_pdf), last_page=15, zoom="1.5")
        state = db.get_session_state(str(self.sample_pdf))
        self.assertTrue(state["found"])
        self.assertEqual(state["last_page_read"], 15)
        db.index_document_metadata_only(self.sample_pdf, page_count=60, file_size=1000)
        state_after = db.get_session_state(str(self.sample_pdf))
        self.assertEqual(state_after["last_page_read"], 15)
        self.assertEqual(state_after["preferred_zoom"], "1.5")

    def test_incremental_page_indexing_on_render(self) -> None:
        api = self._api("incremental.db")
        resource = api._register_pdf(self.sample_pdf, "test")
        result = api.render_page_hq(resource["file_id"], page_number=4)
        self.assertTrue(result["ok"])
        self.assertIn("image", result)
        self.assertNotIn("image_base64", result)
        deadline = time.monotonic() + 2
        matches = []
        while time.monotonic() < deadline:
            matches = api._library.search("página 5")
            if matches:
                break
            time.sleep(0.02)
        self.assertTrue(matches)
        self.assertEqual(matches[0]["page_number"], 4)

    def test_incremental_page_indexing_is_serialized_and_deduplicated(self) -> None:
        api = self._api("deduplicated.db")
        future = MagicMock()

        with patch.object(api._page_indexing_executor, "submit", return_value=future) as submit:
            api._enqueue_page_index(str(self.sample_pdf), 4, "Página 5")
            api._enqueue_page_index(str(self.sample_pdf), 4, "Página 5")

        submit.assert_called_once()
        completion_callback = future.add_done_callback.call_args.args[0]
        future.result.return_value = None
        completion_callback(future)
        self.assertNotIn((str(self.sample_pdf), 4), api._pending_page_indexes)

    def test_full_background_indexing_can_be_cancelled(self) -> None:
        api = self._api("background.db")
        resource = api._register_pdf(self.sample_pdf, "test")
        self.assertTrue(api.start_full_indexing(resource["file_id"])["ok"])
        duplicate = api.start_full_indexing(resource["file_id"])
        self.assertTrue(duplicate["ok"])
        self.assertTrue(duplicate.get("already_running"))
        cancelled = api.cancel_indexing(resource["file_id"])
        self.assertTrue(cancelled["ok"])
        self.assertTrue(cancelled["cancelled"])

    def test_missing_files_preserve_state_and_can_be_relocated(self) -> None:
        db = LibraryDatabase(self.root / "missing.db")
        original = self._create_pdf("original.pdf", 1)
        original_path = str(original.resolve())
        db.save_session_state(original_path, last_page=3, zoom="1.2")
        db.add_bookmark(original_path, page_number=2, title="Marcador de Teste")
        original.unlink()
        self.assertEqual(db.check_and_update_document_availability(original_path), "temporarily_unavailable")
        replacement = self._create_pdf("renomeado.pdf", 1)
        self.assertTrue(db.relocate_document(original_path, str(replacement)))
        session = db.get_session_state(str(replacement.resolve()))
        self.assertTrue(session["found"])
        self.assertEqual(session["last_page_read"], 3)
        self.assertEqual(db.get_bookmarks(str(replacement.resolve()))[0]["title"], "Marcador de Teste")

    def test_relocation_requires_entry_and_removal_is_logical(self) -> None:
        db = LibraryDatabase(self.root / "library.db")
        replacement = self._create_pdf("replacement.pdf", 1)
        self.assertFalse(db.relocate_document(str(self.root / "unknown.pdf"), str(replacement)))
        db.save_session_state(str(self.sample_pdf), last_page=5, zoom="1.0")
        self.assertTrue(db.remove_document_from_library(str(self.sample_pdf)))
        recent = db.get_recent_documents()
        self.assertFalse(any(doc["file_path"] == str(self.sample_pdf.resolve()) for doc in recent))


if __name__ == "__main__":
    unittest.main()
