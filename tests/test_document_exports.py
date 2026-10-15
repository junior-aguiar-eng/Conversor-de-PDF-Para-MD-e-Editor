from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import pymupdf

import converter as converter_module
from markdown_utils import finalize_markdown, reserve_batch_output_paths
from models import PageCoverage


class DocumentExportTests(unittest.TestCase):
    def test_every_conversion_exports_matching_json_and_offline_html(self):
        for profile in ("curso", "jurisprudencia"):
            with self.subTest(profile=profile), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                source = root / "origem.pdf"
                with pymupdf.open() as document:
                    document.new_page().insert_text((72, 72), "Texto de origem.")
                    document.save(source)
                result = finalize_markdown(
                    source, root / "apostila.md",
                    '<!-- NEXOJURIS_PAGE page=2 status=native -->\n# DIREITO ADMINISTRATIVO\n\n'
                    'Texto.\n\n## 1.1. Detalhe\n\nMais texto.',
                    0, False, 60000, heading_profile=profile,
                    page_coverage=(PageCoverage(2, "native"), PageCoverage(3, "failed", "sem texto")),
                )
                self.assertTrue((root / "apostila.json").is_file(), "a estrutura deve acompanhar o MD")
                self.assertEqual(result.structure_path, root / "apostila.json")
                self.assertEqual(result.html_path, root / "apostila.html")
                data = json.loads(result.structure_path.read_text(encoding="utf-8"))
                self.assertEqual(data["markdown_file"], result.markdown_path.name)
                self.assertEqual(data["markdown_sha256"], hashlib.sha256(result.markdown_path.read_bytes()).hexdigest())
                self.assertEqual(data["source_sha256"], hashlib.sha256(source.read_bytes()).hexdigest())
                self.assertEqual(data["sections"][1]["parent_id"], data["sections"][0]["id"])
                self.assertEqual(data["page_coverage"][1]["status"], "failed")
                html = result.html_path.read_text(encoding="utf-8")
                self.assertIn('id="toc"', html)
                self.assertIn("DOMPurify", html)
                self.assertNotIn('<script src=', html)
                self.assertIn("sem texto", html)

    def test_existing_companion_is_never_overwritten(self):
        for suffix in (".json", ".html"):
            with self.subTest(suffix=suffix), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                existing = root / f"doc{suffix}"
                existing.write_text("preservado", encoding="utf-8")
                reservation = reserve_batch_output_paths(root, [root / "doc.pdf"])[0]
                self.assertEqual(reservation.markdown_path.name, "doc (2).md")
                with self.assertRaises(FileExistsError):
                    finalize_markdown(root / "doc.pdf", root / "doc.md", "texto", 0, False, 60000)
                self.assertEqual(existing.read_text(encoding="utf-8"), "preservado")

    def test_failure_promoting_any_document_rolls_back_the_entire_set(self):
        for suffix in (".md", ".json", ".html"):
            with self.subTest(suffix=suffix), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                real_replace = os.replace

                def fail_one(source, destination, extension=suffix, replace=real_replace):
                    if Path(destination).suffix == extension:
                        raise OSError("simulated failure")
                    return replace(source, destination)

                with patch("markdown_utils.os.replace", side_effect=fail_one), self.assertRaises(OSError):
                    finalize_markdown(root / "doc.pdf", root / "doc.md", "texto", 0, False, 60000)
                self.assertEqual(list(root.iterdir()), [])

    def test_content_cannot_escape_the_embedded_json_script(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            result = finalize_markdown(
                root / "doc.pdf", root / "doc.md",
                '# Título\n\n</script><script>window.INJECTED=true</script>\n\n`__PAYLOAD__`',
                0, False, 60000,
            )
            self.assertTrue((root / "doc.html").exists())
            html = result.html_path.read_text(encoding="utf-8")
            self.assertNotIn('</script><script>window.INJECTED', html)
            payload = html.split('<script type="application/json" id="document-data">', 1)[1].split('</script>', 1)[0]
            data = json.loads(payload)
            self.assertIn('</script><script>window.INJECTED=true</script>', data["markdown"])

    def test_native_extraction_never_runs_concurrently_in_one_process(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "source.pdf"
            with pymupdf.open() as document:
                for _ in range(4):
                    document.new_page().insert_text((72, 72), "Texto de origem.")
                document.save(source)
            lock = threading.Lock()
            active = 0
            peak = 0

            def native(document, **kwargs):
                nonlocal active, peak
                with lock:
                    active += 1
                    peak = max(peak, active)
                time.sleep(0.025)
                with lock:
                    active -= 1
                return "# Título\n\nTexto de origem."

            converter = converter_module.PdfMarkdownConverter.__new__(converter_module.PdfMarkdownConverter)
            converter._pymupdf = pymupdf
            converter._to_markdown = native
            converter.convert(source, root / "out", False, 60000, activation_verified=True, page_workers=4)
            self.assertEqual(peak, 1, "PyMuPDF e seu layout devem trabalhar sem concorrência de threads")


if __name__ == "__main__":
    unittest.main()
