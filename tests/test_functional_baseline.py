from __future__ import annotations

import hashlib
import hmac
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import fitz

import converter as converter_module
import licensing as licensing_module
from constants import MAX_PAGE_COUNT
from library_db import LibraryDatabase
from markdown_utils import reserve_batch_output_paths
from web_api import BridgeApi


class FunctionalPreservationBaselineTests(unittest.TestCase):
    @staticmethod
    def _extract_javascript_function(source: str, name: str) -> str:
        start = source.index(f"function {name}(")
        if source[max(0, start - 6) : start] == "async ":
            start -= 6
        opening_brace = source.index("{", start)
        depth = 0
        for index in range(opening_brace, len(source)):
            if source[index] == "{":
                depth += 1
            elif source[index] == "}":
                depth -= 1
                if depth == 0:
                    return source[start : index + 1]
        raise AssertionError(f"Função JavaScript incompleta: {name}")

    def test_file_selection_and_drag_drop_reach_the_same_queue_ingress(self) -> None:
        app_js = (Path(__file__).resolve().parents[1] / "web" / "app.js").read_text(encoding="utf-8")
        functions = "\n".join(
            [
                self._extract_javascript_function(app_js, "setupDragAndDrop"),
                self._extract_javascript_function(app_js, "triggerFileSelect"),
            ]
        )
        script = f"""
{functions}
const handlers = {{}};
const received = [];
const dropZone = {{ classList: {{ add() {{}}, remove() {{}} }} }};
global.document = {{ getElementById: () => dropZone }};
global.state = {{ isConverting: false }};
global.playBeep = () => {{}};
global.showToast = () => {{}};
global.addProcessedFiles = (items) => received.push(items.map((item) => item.path));
global.window = {{
  addEventListener: (name, callback) => {{ handlers[name] = callback; }},
  pywebview: {{ api: {{
    choose_files: async () => [{{ path: "C:/selecionado.pdf" }}],
    register_dropped_files: async (paths) => paths.map((path) => ({{ path }})),
  }} }},
}};
async function main() {{
  setupDragAndDrop();
  await triggerFileSelect();
  await handlers.drop({{
    preventDefault() {{}},
    stopPropagation() {{}},
    dataTransfer: {{ files: [{{ path: "C:/arrastado.pdf" }}] }},
  }});
  process.stdout.write(JSON.stringify(received));
}}
main().catch((error) => {{ console.error(error); process.exit(1); }});
"""
        completed = subprocess.run(
            ["node", "-e", script],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(
            completed.stdout,
            '[["C:/selecionado.pdf"],["C:/arrastado.pdf"]]',
        )

    def test_exactly_one_thousand_synthetic_pages_remain_convertible_in_full(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            source = root / "mil-paginas.pdf"
            output_dir = root / "saida"

            document = fitz.open()
            for _ in range(MAX_PAGE_COUNT):
                document.new_page(width=72, height=72)
            document.save(source)
            document.close()

            observed_page_counts: list[int] = []

            def fake_to_markdown(open_document, **kwargs):
                observed_page_counts.append(open_document.page_count)
                return f"# Página {kwargs['pages'][0] + 1}\n\n" + ("conteúdo integral " * 10)

            converter = converter_module.PdfMarkdownConverter.__new__(converter_module.PdfMarkdownConverter)
            converter._pymupdf = fitz
            converter._to_markdown = fake_to_markdown

            with (
                patch.object(converter_module, "require_software_activation", return_value="NXJ-TEST"),
                patch.object(converter_module, "is_scanned_page", return_value=False),
            ):
                result = converter.convert(
                    source=source,
                    output_dir=output_dir,
                    split_output=False,
                    max_chunk_characters=60_000,
                )

            self.assertEqual(observed_page_counts, [MAX_PAGE_COUNT] * MAX_PAGE_COUNT)
            self.assertEqual(len(result.page_coverage), MAX_PAGE_COUNT)
            self.assertTrue(result.markdown_path.is_file())
            self.assertIn("conteúdo integral", result.markdown_path.read_text(encoding="utf-8"))

    def test_vector_scanned_and_hybrid_extraction_routes_are_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)

            def make_pdf(name: str, page_count: int) -> Path:
                path = root / name
                document = fitz.open()
                for page_number in range(page_count):
                    page = document.new_page()
                    page.insert_text((40, 80), f"Página vetorial {page_number} com conteúdo suficiente para extração.")
                document.save(path)
                document.close()
                return path

            def run_conversion(source: Path, scanned_pages: set[int]) -> str:
                converter = converter_module.PdfMarkdownConverter.__new__(converter_module.PdfMarkdownConverter)
                converter._pymupdf = fitz
                converter._to_markdown = lambda _doc, **kwargs: f"conteúdo vetorial página {kwargs['pages'][0]} " * 3
                with (
                    patch.object(converter_module, "require_software_activation", return_value="NXJ-TEST"),
                    patch.object(
                        converter_module,
                        "is_scanned_page",
                        side_effect=lambda page: page.number in scanned_pages,
                    ),
                    patch.object(
                        converter_module,
                        "ocr_page_to_markdown",
                        side_effect=lambda page, dpi: f"conteúdo OCR página {page.number} " * 3,
                    ),
                ):
                    result = converter.convert(source, root / f"saida-{source.stem}", False, 60_000)
                return result.markdown_path.read_text(encoding="utf-8")

            vector = run_conversion(make_pdf("vetorial.pdf", 2), set())
            scanned = run_conversion(make_pdf("digitalizado.pdf", 2), {0, 1})
            hybrid = run_conversion(make_pdf("hibrido.pdf", 3), {1})

            self.assertIn("conteúdo vetorial página 0", vector)
            self.assertIn("conteúdo vetorial página 1", vector)
            self.assertIn("conteúdo OCR página 0", scanned)
            self.assertIn("conteúdo OCR página 1", scanned)
            self.assertIn("conteúdo vetorial página 0", hybrid)
            self.assertIn("conteúdo OCR página 1", hybrid)
            self.assertIn("conteúdo vetorial página 2", hybrid)

    def test_batch_reserves_distinct_destinations_for_homonymous_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            output_dir = Path(tmp_dir)
            sources = [Path("C:/origem-a/contrato.pdf"), Path("D:/origem-b/contrato.pdf")]
            reservations = reserve_batch_output_paths(output_dir, sources)

            self.assertEqual([item.markdown_path.name for item in reservations], ["contrato.md", "contrato (2).md"])
            self.assertNotEqual(reservations[0].assets_dir, reservations[1].assets_dir)
            self.assertNotEqual(reservations[0].chunks_dir, reservations[1].chunks_dir)

    def test_protected_pdf_reader_and_conversion_report_unavailable_page_without_aborting(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            source = root / "protegido.pdf"
            document = fitz.open()
            document.new_page().insert_text((40, 80), "Conteúdo protegido")
            document.save(
                source,
                encryption=fitz.PDF_ENCRYPT_AES_256,
                user_pw="senha",
                owner_pw="mestre",
            )
            document.close()

            database = LibraryDatabase(root / "acervo.db")
            with (
                patch("web_api.LibraryDatabase", return_value=database),
                patch("web_api.threading.Thread"),
            ):
                api = BridgeApi()
                file_id = api._register_pdf(source, "test")["file_id"]
                self.assertTrue(api.set_pdf_password(file_id, "senha")["ok"])
                self.assertTrue(api.get_pdf_info(file_id)["ok"])

            converter = converter_module.PdfMarkdownConverter()
            with patch.object(converter_module, "require_software_activation", return_value="NXJ-TEST"):
                result = converter.convert(source, root / "saida", False, 60_000)

            self.assertEqual(result.failed_pages, (1,))
            self.assertIn(
                "Página 1 não pôde ser recuperada",
                result.markdown_path.read_text(encoding="utf-8"),
            )

    def test_reader_opens_one_thousand_pages_without_starting_conversion(self) -> None:
        class DeferredIndexingThread:
            created = 0

            def __init__(self, *args, **kwargs):
                type(self).created += 1

            def start(self) -> None:
                return None

        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            source = root / "leitura-mil-paginas.pdf"
            document = fitz.open()
            for _ in range(MAX_PAGE_COUNT):
                document.new_page(width=72, height=72)
            document.save(source)
            document.close()

            database = LibraryDatabase(root / "acervo.db")
            with (
                patch("web_api.LibraryDatabase", return_value=database),
                patch("web_api.threading.Thread", DeferredIndexingThread),
                patch("web_api.PdfMarkdownConverter", side_effect=AssertionError("o leitor iniciou conversão")),
            ):
                api = BridgeApi()
                file_id = api._register_pdf(source, "test")["file_id"]
                result = api.get_pdf_info(file_id)

            self.assertTrue(result["ok"])
            self.assertEqual(result["page_count"], MAX_PAGE_COUNT)
            self.assertLessEqual(len(result["pages"]), 50)
            self.assertEqual(DeferredIndexingThread.created, 0)

    def test_markdown_preview_preserves_legitimate_elements_and_blocks_active_payloads(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        completed = subprocess.run(
            ["node", str(project_root / "tests" / "phase2_renderer.test.js")],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
            cwd=project_root,
        )
        self.assertIn("phase2_renderer_ok", completed.stdout)

    def test_annotations_are_persisted_on_multiple_pages(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            source = Path(tmp_dir) / "anotacoes-multiplas.pdf"
            document = fitz.open()
            document.new_page()
            document.new_page()
            document.save(source)
            document.close()

            api = BridgeApi()
            file_id = api._register_pdf(source, "test")["file_id"]
            result = api.save_pdf_annotations(
                {
                    "file_id": file_id,
                    "annotations": [
                        {"type": "highlight", "page_number": 0, "rect": [40, 40, 160, 70]},
                        {"type": "highlight", "page_number": 1, "rect": [40, 40, 160, 70]},
                    ],
                }
            )

            self.assertTrue(result["ok"])
            self.assertEqual(result["saved_count"], 2)
            with fitz.open(source) as saved:
                self.assertIsNotNone(saved[0].first_annot)
                self.assertIsNotNone(saved[1].first_annot)

    def test_history_zoom_and_bookmarks_survive_restart_and_temporary_unavailability(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            database_path = root / "acervo.db"
            source = root / "unidade-externa.pdf"
            document = fitz.open()
            document.new_page()
            document.save(source)
            document.close()

            first_session = LibraryDatabase(database_path)
            with fitz.open(source) as opened:
                first_session.index_pdf_document(source, opened)
            first_session.save_session_state(str(source), 7, "1.75")
            bookmark = first_session.add_bookmark(str(source), 3, "Fundamento central")
            self.assertTrue(bookmark["ok"])

            source.unlink()
            second_session = LibraryDatabase(database_path)
            state = second_session.get_session_state(str(source))
            bookmarks = second_session.get_bookmarks(str(source))
            recent = second_session.get_recent_documents()

            self.assertTrue(state["found"])
            self.assertEqual(state["last_page_read"], 7)
            self.assertEqual(state["preferred_zoom"], "1.75")
            self.assertEqual(bookmarks[0]["title"], "Fundamento central")
            self.assertEqual(recent[0]["file_path"], str(source.resolve()))

    def test_preexisting_hmac_license_is_identified_as_currently_incompatible(self) -> None:
        machine_id = "NXJ-1111-2222-3333-4444"
        signing_key = hashlib.sha256(
            b":".join([b"NXJ_SEC_2026", b"CORE_NODE_LOCK", b"LEGAL_TECH_MASTER", b"SHA256_OFFLINE"])
        ).digest()
        digest = hmac.new(signing_key, machine_id.encode("utf-8"), hashlib.sha256).hexdigest().upper()
        legacy_key = "ACT-" + "-".join(digest[index : index + 4] for index in range(0, 16, 4))

        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            with (
                patch.object(licensing_module, "_LICENSE_DB_PATH", root / "license.db"),
                patch.object(licensing_module, "_LICENSE_BACKUP_PATH", root / "license.sig"),
                patch.object(licensing_module, "get_machine_fingerprint", return_value=machine_id),
            ):
                self.assertFalse(licensing_module.verify_license_key(machine_id, legacy_key))
                licensing_module._save_license(machine_id, legacy_key)
                self.assertEqual(licensing_module.is_software_activated(), (False, machine_id))

    def test_online_services_return_functional_errors_when_network_is_unavailable(self) -> None:
        api = BridgeApi.__new__(BridgeApi)

        with (
            patch("web_api.GoogleTranslator") as translator_class,
            patch("web_api.logger"),
        ):
            translator_class.return_value.translate.side_effect = OSError("rede indisponível")
            translation = api.translate_text("texto", source_lang="auto")

        class OfflineSpeech:
            async def stream(self):
                raise OSError("rede indisponível")
                yield  # pragma: no cover

        with (
            patch("web_api.edge_tts.Communicate", return_value=OfflineSpeech()),
            patch("web_api.logger"),
        ):
            speech = api.synthesize_speech("texto")

        self.assertFalse(translation["ok"])
        self.assertIn("Falha ao traduzir", translation["error"])
        self.assertFalse(speech["ok"])
        self.assertIn("Falha na síntese de voz", speech["error"])


if __name__ == "__main__":
    unittest.main()
