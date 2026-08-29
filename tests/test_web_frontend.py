from __future__ import annotations

import re
import subprocess
import unittest
from pathlib import Path


class WebFrontendRegressionTests(unittest.TestCase):
    @staticmethod
    def _read_project_file(*parts: str) -> str:
        return (Path(__file__).resolve().parents[1].joinpath(*parts)).read_text(encoding="utf-8")

    def test_successful_annotation_save_clears_pending_queue_before_render(self) -> None:
        app_js = self._read_project_file("web", "app.js")
        method_start = app_js.index("  async saveAnnotations(asCopy = false) {")
        method_end = app_js.index("\n  async triggerSnippetExtraction", method_start)
        method = app_js[method_start:method_end]

        success_start = method.index("    if (res.ok) {")
        render_position = method.index("await this.renderCurrentPage(true);", success_start)
        success_before_render = method[success_start:render_position]
        failure_start = method.index("    } else {", render_position)
        failure_branch = method[failure_start:]

        self.assertIn("this.annotations.clear();", success_before_render)
        self.assertIn("this.undoStack = [];", success_before_render)
        self.assertNotIn("this.annotations.clear();", failure_branch)

    def test_every_literal_switch_target_has_a_real_view(self) -> None:
        app_js = self._read_project_file("web", "app.js")
        declaration = re.search(r"const views = \[(.*?)\];", app_js, flags=re.DOTALL)
        self.assertIsNotNone(declaration)

        declared_views = set(re.findall(r'["\']([a-z]+)["\']', declaration.group(1)))
        switch_targets = set(re.findall(r'switchView\(["\']([a-z]+)["\']\)', app_js))

        self.assertTrue(switch_targets)
        self.assertLessEqual(switch_targets, declared_views)
        self.assertNotIn("markdown", switch_targets)

    def test_dynamic_file_paths_are_not_interpolated_into_inline_javascript(self) -> None:
        app_js = self._read_project_file("web", "app.js")

        self.assertNotRegex(app_js, r'data-action="[^"]*\$\{[^}]*\.(?:path|markdown_path)')
        self.assertIn('data-action="openQueuedPdf(${idx})"', app_js)
        self.assertIn('data-action="previewQueuedMarkdown(${idx})"', app_js)
        self.assertIn('data-action="openQueuedMarkdown(${idx})"', app_js)
        self.assertIn('data-action="appSearch.openResult(${index})"', app_js)
        self.assertNotIn("function escapeJsString", app_js)

    def test_manual_discloses_online_translation_and_voice_services(self) -> None:
        index_html = self._read_project_file("web", "index.html")
        app_js = self._read_project_file("web", "app.js")

        for content in (index_html, app_js):
            self.assertIn("Google Translator", content)
            self.assertIn("Microsoft Edge TTS", content)

        self.assertNotIn("Todo o ecossistema roda de forma 100% offline", index_html)
        self.assertNotIn("traduza o texto selecionado em tempo real com processamento local", index_html)
        self.assertIn("exigem internet", app_js)

    def test_phase1_frontend_integrity_executes_in_javascript(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        completed = subprocess.run(
            ["node", str(project_root / "tests" / "phase1_frontend.test.js")],
            cwd=project_root,
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertIn("phase1_frontend_ok", completed.stdout)

    def test_document_switch_modal_exposes_every_required_decision(self) -> None:
        index_html = self._read_project_file("web", "index.html")
        self.assertIn("Salvar e abrir outro", index_html)
        self.assertIn("Manter rascunho e abrir", index_html)
        self.assertIn("Descartar e abrir outro", index_html)
        self.assertIn("Cancelar a troca", index_html)

    def test_phase2_renderer_and_self_contained_csp(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        index_html = self._read_project_file("web", "index.html")
        app_js = self._read_project_file("web", "app.js")
        style_css = self._read_project_file("web", "style.css")

        self.assertIn("Content-Security-Policy", index_html)
        self.assertIn("script-src 'self'", index_html)
        self.assertNotRegex(index_html, r"<(?:script|link)[^>]+https?://")
        self.assertNotRegex(index_html, r"\son[a-z]+\s*=")
        self.assertNotRegex(app_js, r"\son[a-z]+\s*=")
        self.assertNotIn("fonts.googleapis.com", style_css)
        for relative in (
            "web/tailwind.css",
            "web/vendor/marked.umd.js",
            "web/vendor/purify.min.js",
            "web/fonts/plus-jakarta-sans-latin.woff2",
        ):
            self.assertTrue((project_root / relative).is_file(), relative)

        completed = subprocess.run(
            ["node", str(project_root / "tests" / "phase2_renderer.test.js")],
            cwd=project_root,
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertIn("phase2_renderer_ok", completed.stdout)


if __name__ == "__main__":
    unittest.main()
