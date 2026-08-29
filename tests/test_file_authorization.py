from __future__ import annotations

import base64
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import fitz

from file_authorization import AuthorizedResourceRegistry, ResourceAccessError
from library_db import LibraryDatabase
from web_api import BridgeApi


class AuthorizedResourceRegistryTests(unittest.TestCase):
    def test_ids_are_opaque_stable_and_enforce_kind_and_capability(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            source = Path(tmp_dir) / "documento.pdf"
            source.write_bytes(b"%PDF-1.4\n%%EOF")
            registry = AuthorizedResourceRegistry()
            first = registry.register(source, kind="pdf", origin="dialog", capabilities={"read"})
            second = registry.register(source, kind="pdf", origin="recent", capabilities={"write"})

            self.assertEqual(first.resource_id, second.resource_id)
            self.assertNotIn(str(source), first.resource_id)
            self.assertEqual(registry.resolve(first.resource_id, kind="pdf", capability="write"), source.resolve())
            self.assertEqual(second.origins, {"dialog", "recent"})
            with self.assertRaises(ResourceAccessError):
                registry.resolve("desconhecido", kind="pdf", capability="read")
            with self.assertRaises(ResourceAccessError):
                registry.resolve(first.resource_id, kind="markdown", capability="read")
            with self.assertRaises(ResourceAccessError):
                registry.resolve(first.resource_id, kind="pdf", capability="execute")


class BridgeAuthorizationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        database = LibraryDatabase(Path(self.temp_dir.name) / "authorization.db")
        patcher = patch("web_api.LibraryDatabase", return_value=database)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.api = BridgeApi()

    def _pdf(self, name: str = "documento.pdf") -> Path:
        path = Path(self.temp_dir.name) / name
        document = fitz.open()
        document.new_page().insert_text((40, 80), "conteúdo autorizado")
        document.save(path)
        document.close()
        return path

    def test_raw_paths_cannot_reach_privileged_pdf_or_markdown_operations(self) -> None:
        pdf = self._pdf()
        markdown = Path(self.temp_dir.name) / "documento.md"
        markdown.write_text("# conteúdo", encoding="utf-8")

        self.assertFalse(self.api.get_pdf_info(str(pdf))["ok"])
        self.assertFalse(self.api.read_markdown_preview(str(markdown))["ok"])
        self.assertFalse(self.api.protect_pdf(str(pdf), "senha")["ok"])

        pdf_id = self.api._register_pdf(pdf, "native_dialog")["file_id"]
        markdown_id = self.api._register_markdown(markdown, "conversion_result")["markdown_id"]
        with patch("web_api.threading.Thread"):
            self.assertTrue(self.api.get_pdf_info(pdf_id)["ok"])
        self.assertTrue(self.api.read_markdown_preview(markdown_id)["ok"])

    def test_validated_drop_filters_resources_and_grants_an_internal_id(self) -> None:
        pdf = self._pdf()
        text = Path(self.temp_dir.name) / "texto.txt"
        text.write_text("não PDF", encoding="utf-8")
        window = MagicMock()
        window.create_confirmation_dialog.return_value = True
        self.api.set_window(window)
        resources = self.api.register_dropped_files([str(pdf), str(text), str(pdf.with_name("ausente.pdf"))])

        self.assertEqual(len(resources), 1)
        self.assertTrue(resources[0]["file_id"])
        self.assertEqual(resources[0]["path"], str(pdf.resolve()))
        window.create_confirmation_dialog.assert_called_once()

    def test_renderer_cannot_silently_authorize_an_arbitrary_drop_path(self) -> None:
        pdf = self._pdf("não-autorizado.pdf")
        self.assertEqual(self.api.register_dropped_files([str(pdf)]), [])

        window = MagicMock()
        window.create_confirmation_dialog.return_value = False
        self.api.set_window(window)
        self.assertEqual(self.api.register_dropped_files([str(pdf)]), [])
        self.assertIsNone(self.api._resources.id_for_path(pdf, kind="pdf"))

    def test_markdown_assets_are_confined_to_the_authorized_parent(self) -> None:
        root = Path(self.temp_dir.name)
        markdown = root / "documento.md"
        markdown.write_text("![imagem](assets/pagina.png)", encoding="utf-8")
        assets = root / "assets"
        assets.mkdir()
        png = assets / "pagina.png"
        png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"teste")
        outside = root.parent / "segredo.png"
        outside.write_bytes(b"\x89PNG\r\n\x1a\nsegredo")
        self.addCleanup(lambda: outside.unlink(missing_ok=True))
        markdown_id = self.api._register_markdown(markdown, "conversion_result")["markdown_id"]

        allowed = self.api.read_markdown_asset(markdown_id, "assets/pagina.png")
        self.assertTrue(allowed["ok"])
        self.assertEqual(base64.b64decode(allowed["data_uri"].split(",", 1)[1]), png.read_bytes())

        for hostile in ("../segredo.png", "%2e%2e/segredo.png", str(outside), "C:/segredo.png", "https://x/foto.png"):
            with self.subTest(hostile=hostile):
                self.assertFalse(self.api.read_markdown_asset(markdown_id, hostile)["ok"])

    def test_recent_document_is_reauthorized_without_exposing_path_as_authority(self) -> None:
        pdf = self._pdf("recente.pdf")
        with fitz.open(pdf) as document:
            self.api._library.index_pdf_document(pdf, document)

        response = self.api.get_recent_library()
        self.assertTrue(response["ok"])
        recent = response["documents"][0]
        self.assertTrue(recent["resource_id"])
        self.assertEqual(recent["display_path"], str(pdf.resolve()))
        with patch("web_api.threading.Thread"):
            self.assertTrue(self.api.get_pdf_info(recent["resource_id"])["ok"])

    def test_library_relocation_requires_both_opaque_authorizations(self) -> None:
        missing = self._pdf("movido.pdf")
        missing_path = str(missing.resolve())
        self.api._library.save_session_state(missing_path, last_page=2, zoom="1.25")
        missing.unlink()
        recent = self.api.get_recent_library()["documents"][0]
        replacement = self._pdf("reencontrado.pdf")

        raw_attempt = self.api.relocate_library_document(missing_path, str(replacement))
        self.assertFalse(raw_attempt["ok"])
        unknown_attempt = self.api.relocate_library_document("desconhecido", "desconhecido")
        self.assertFalse(unknown_attempt["ok"])

        replacement_id = self.api._register_pdf(replacement, "native_dialog")["file_id"]
        relocated = self.api.relocate_library_document(recent["library_entry_id"], replacement_id)
        self.assertTrue(relocated["ok"])
        self.assertEqual(
            self.api._library.get_session_state(str(replacement.resolve()))["last_page_read"],
            2,
        )


if __name__ == "__main__":
    unittest.main()
