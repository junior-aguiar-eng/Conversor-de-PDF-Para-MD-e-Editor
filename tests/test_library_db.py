from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

import fitz

from library_db import LibraryDatabase
from web_api import BridgeApi


class LibraryDatabaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp_dir.name) / "test_acervo.db"
        self.db = LibraryDatabase(self.db_path)

    def tearDown(self) -> None:
        try:
            self.tmp_dir.cleanup()
        except OSError:
            pass

    def test_schema_created(self) -> None:
        self.assertTrue(self.db_path.is_file())

    def test_index_and_search_pdf(self) -> None:
        doc = fitz.open()
        p1 = doc.new_page(width=595, height=842)
        p1.insert_text(fitz.Point(50, 50), "Jurisprudência relevante sobre dano moral em contrato bancário.")
        p2 = doc.new_page(width=595, height=842)
        p2.insert_text(fitz.Point(50, 50), "Habeas corpus substitutivo de recurso ordinário. Prescrição penal.")

        pdf_path = Path(self.tmp_dir.name) / "processo_123.pdf"
        doc.save(str(pdf_path))

        self.db.index_pdf_document(pdf_path, doc)
        doc.close()

        # Busca por termo existente na página 1
        results = self.db.search("dano moral")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["file_name"], "processo_123.pdf")
        self.assertEqual(results[0]["page_number"], 0)
        self.assertIn("dano", results[0]["snippet"].lower())

        # Busca por termo existente na página 2
        results_p2 = self.db.search("prescricao penal")
        self.assertEqual(len(results_p2), 1)
        self.assertEqual(results_p2[0]["page_number"], 1)

    def test_index_and_search_markdown(self) -> None:
        pdf_path = Path(self.tmp_dir.name) / "peticao.pdf"
        md_path = Path(self.tmp_dir.name) / "peticao.md"
        content = "# Petição Inicial\n\nExcelentíssimo Senhor Doutor Juiz de Direito da 1ª Vara Cível."

        self.db.index_markdown_file(pdf_path, md_path, content)

        results = self.db.search("Excelentissimo Juiz")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["content_type"], "markdown")

    def test_session_state_persistence(self) -> None:
        pdf_path = str(Path(self.tmp_dir.name) / "livro_juridico.pdf")

        # Antes de registrar
        state0 = self.db.get_session_state(pdf_path)
        self.assertFalse(state0["found"])

        # Registrando documento primeiro:
        doc = fitz.open()
        doc.new_page()
        self.db.index_pdf_document(pdf_path, doc)
        doc.close()

        self.db.save_session_state(pdf_path, 5, "1.25")
        state2 = self.db.get_session_state(pdf_path)
        self.assertTrue(state2["found"])
        self.assertEqual(state2["last_page_read"], 5)
        self.assertEqual(state2["preferred_zoom"], "1.25")

    def test_bookmarks_crud(self) -> None:
        pdf_path = str(Path(self.tmp_dir.name) / "acordao.pdf")
        doc = fitz.open()
        doc.new_page()
        self.db.index_pdf_document(pdf_path, doc)
        doc.close()

        bm1 = self.db.add_bookmark(pdf_path, 3, "Fundamentação Legal")
        self.assertTrue(bm1["ok"])
        bm_id = bm1["id"]

        bms = self.db.get_bookmarks(pdf_path)
        self.assertEqual(len(bms), 1)
        self.assertEqual(bms[0]["title"], "Fundamentação Legal")

        deleted = self.db.delete_bookmark(bm_id)
        self.assertTrue(deleted)
        self.assertEqual(len(self.db.get_bookmarks(pdf_path)), 0)

    def test_terms_acceptance_workflow(self) -> None:
        status0 = self.db.get_terms_status()
        self.assertFalse(status0["accepted"])

        self.db.save_terms_acceptance("1.5")
        status1 = self.db.get_terms_status()
        self.assertTrue(status1["accepted"])
        self.assertIn("accepted_at", status1)


class WebApiLibraryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.api = BridgeApi()
        self.api._library = LibraryDatabase(Path(self.tmp_dir.name) / "test_api_acervo.db")
        self.api._window = MagicMock()

    def tearDown(self) -> None:
        try:
            self.tmp_dir.cleanup()
        except OSError:
            pass

    def test_search_library_bridge(self) -> None:
        res = self.api.search_library("termo")
        self.assertTrue(res["ok"])
        self.assertIsInstance(res["results"], list)

    def test_reading_state_and_bookmarks_bridge(self) -> None:
        pdf_path = str(Path(self.tmp_dir.name) / "doc.pdf")
        doc = fitz.open()
        doc.new_page()
        self.api._library.index_pdf_document(pdf_path, doc)
        doc.close()

        save_res = self.api.save_reading_state(pdf_path, 2, "1.5")
        self.assertTrue(save_res["ok"])

        get_res = self.api.get_reading_state(pdf_path)
        self.assertTrue(get_res["ok"])
        self.assertEqual(get_res["state"]["last_page_read"], 2)

        bm_res = self.api.add_bookmark(pdf_path, 2, "Ponto Crítico")
        self.assertTrue(bm_res["ok"])

        list_bms = self.api.get_bookmarks(pdf_path)
        self.assertTrue(list_bms["ok"])
        self.assertEqual(len(list_bms["bookmarks"]), 1)

        del_res = self.api.delete_bookmark(bm_res["id"])
        self.assertTrue(del_res["ok"])

    def test_terms_acceptance_bridge(self) -> None:
        status0 = self.api.get_terms_acceptance_status()
        self.assertFalse(status0["accepted"])

        accept_res = self.api.accept_terms("2.0")
        self.assertTrue(accept_res["ok"])

        status1 = self.api.get_terms_acceptance_status()
        self.assertTrue(status1["accepted"])


if __name__ == "__main__":
    unittest.main()
