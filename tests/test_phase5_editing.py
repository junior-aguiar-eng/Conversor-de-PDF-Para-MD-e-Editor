"""Testes automatizados para a Fase 5 (Itens 17, 18 e 19)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import fitz

from constants import CURRENT_TERMS_VERSION
from library_db import LibraryDatabase
from licensing import (
    get_machine_fingerprint_v1,
    get_machine_fingerprint_v2,
)
from web_api import BridgeApi


class TestPhase5LicensingTermsAndEditing(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp_dir.name)
        self.db_path = self.root / "acervo_test.db"
        self.db = LibraryDatabase(self.db_path)

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_fingerprint_v1_and_v2_formats(self) -> None:
        v1_id = get_machine_fingerprint_v1()
        v2_id = get_machine_fingerprint_v2()

        self.assertTrue(v1_id.startswith("NXJ-"))
        self.assertTrue(v2_id.startswith("NXJ2-"))
        self.assertNotEqual(v1_id, v2_id)

    def test_terms_versioning_and_reacceptance(self) -> None:
        # 1. Sem aceite prévio
        status0 = self.db.get_terms_status()
        self.assertFalse(status0["accepted"])
        self.assertIsNone(status0["terms_version"])

        # 2. Aceite da versão 0.9 (antiga)
        self.db.save_terms_acceptance("0.9")
        status1 = self.db.get_terms_status()
        self.assertTrue(status1["accepted"])
        self.assertEqual(status1["terms_version"], "0.9")

        # 3. Testar API com versão 0.9 quando CURRENT_TERMS_VERSION é 1.0
        with patch("web_api.LibraryDatabase", return_value=self.db):
            api = BridgeApi()
            api_status = api.get_terms_acceptance_status()
            self.assertTrue(api_status["needs_reacceptance"])
            self.assertFalse(api_status["accepted"])

            # Aceita versão atual
            res = api.accept_terms(CURRENT_TERMS_VERSION)
            self.assertTrue(res["ok"])

            api_status2 = api.get_terms_acceptance_status()
            self.assertFalse(api_status2["needs_reacceptance"])
            self.assertTrue(api_status2["accepted"])
            self.assertEqual(api_status2["terms_version"], CURRENT_TERMS_VERSION)

    def test_pdf_editing_save_original_vs_save_copy(self) -> None:
        # Cria um PDF de teste
        source_path = self.root / "doc_original.pdf"
        doc = fitz.open()
        page = doc.new_page(width=595, height=842)
        page.insert_text((50, 50), "Texto original para teste de edição.")
        doc.save(str(source_path))
        doc.close()

        with patch("web_api.LibraryDatabase", return_value=self.db):
            api = BridgeApi()
            api._register_directory(self.root, "test")
            pdf_reg = api._register_pdf(source_path, "test")
            file_id = pdf_reg["file_id"]

            # 1. Rotacionar no original
            rot_res = api.rotate_pdf_page(file_id, 0, 90)
            self.assertTrue(rot_res["ok"])
            self.assertEqual(rot_res["new_rotation"], 90)
            self.assertFalse(rot_res["is_copy"])

            # 2. Rotacionar como cópia
            copy_path = self.root / "doc_copia_rotacionada.pdf"
            copy_id = api._register_pdf_destination(copy_path, "test")["output_file_id"]
            rot_copy = api.rotate_pdf_page(file_id, 0, 90, output_file_id=copy_id)
            self.assertTrue(rot_copy["ok"], rot_copy.get("error"))
            self.assertTrue(rot_copy["is_copy"])
            self.assertTrue(copy_path.is_file())
            self.assertIn("new_file_id", rot_copy)

            # 3. Anotar como cópia
            annot_copy_path = self.root / "doc_copia_anotada.pdf"
            annot_copy_id = api._register_pdf_destination(annot_copy_path, "test")["output_file_id"]
            annot_payload = {
                "file_id": file_id,
                "annotations": [
                    {
                        "type": "highlight",
                        "page_number": 0,
                        "rect": [40, 40, 300, 70],
                        "color": "#ffff00",
                    }
                ],
                "output_file_id": annot_copy_id,
            }
            annot_res = api.save_pdf_annotations(annot_payload)
            self.assertTrue(annot_res["ok"])
            self.assertTrue(annot_res["is_copy"])
            self.assertTrue(annot_copy_path.is_file())

            # 4. Proteger como cópia
            prot_copy_path = self.root / "doc_copia_protegida.pdf"
            prot_copy_id = api._register_pdf_destination(prot_copy_path, "test")["output_file_id"]
            prot_res = api.protect_pdf(file_id, "senha123", output_file_id=prot_copy_id)
            self.assertTrue(prot_res["ok"])
            self.assertTrue(prot_res["is_copy"])
            self.assertTrue(prot_copy_path.is_file())

            # Verifica que o arquivo protegido precisa de senha
            doc_check = fitz.open(str(prot_copy_path))
            self.assertTrue(doc_check.is_encrypted)
            doc_check.close()

            # Caminhos brutos e IDs desconhecidos nunca autorizam destinos de escrita.
            blocked_path = self.root / "nao_autorizado.pdf"
            blocked = api.save_pdf_annotations(
                {"file_id": file_id, "annotations": [], "output_path": str(blocked_path)}
            )
            self.assertFalse(blocked["ok"])
            self.assertFalse(blocked_path.exists())
            unknown = api.rotate_pdf_page(file_id, 0, 90, output_file_id="desconhecido")
            self.assertFalse(unknown["ok"])
            self.assertFalse(blocked_path.exists())

            self.assertFalse(api.protect_pdf(file_id, "senha", output_file_id="desconhecido")["ok"])
            self.assertFalse(api.unprotect_pdf(file_id, output_file_id="desconhecido")["ok"])
            self.assertFalse(api.remove_library_document(str(source_path))["ok"])

    def test_native_save_dialog_grants_destination_and_cancellation_grants_nothing(self) -> None:
        source_path = self.root / "dialogo.pdf"
        document = fitz.open()
        document.new_page()
        document.save(str(source_path))
        document.close()

        with patch("web_api.LibraryDatabase", return_value=self.db):
            api = BridgeApi()
            file_id = api._register_pdf(source_path, "test")["file_id"]
            window = MagicMock()
            api.set_window(window)
            window.create_file_dialog.return_value = None
            self.assertIsNone(api.choose_pdf_save_destination(file_id, "copia"))

            destination = self.root / "dialogo-copia.pdf"
            window.create_file_dialog.return_value = (str(destination),)
            granted = api.choose_pdf_save_destination(file_id, "copia")
            self.assertTrue(granted["ok"])
            result = api.rotate_pdf_page(file_id, 0, 90, output_file_id=granted["output_file_id"])
            self.assertTrue(result["ok"])
            self.assertTrue(destination.is_file())


if __name__ == "__main__":
    unittest.main()
