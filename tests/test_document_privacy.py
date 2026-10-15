from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pymupdf

from converter import _PRIVACY_CONTEXT, PdfMarkdownConverter, _checkpoint_signature, _save_page_checkpoint
from document_privacy import PrivacyProtectionError, plan_from_source
from markdown_utils import finalize_markdown

# Dados exclusivamente sintéticos, sem identificadores reais de usuários.
FIELDS = (
    "CPF: 111.222.333-44", "Nome: Pessoa Exemplo Teste",
    "E-mail: aluno@example.test", "Telefone: (11) 91111-2222",
)
VALUES = ("111.222.333-44", "Pessoa Exemplo Teste", "aluno@example.test", "91111-2222")
BODY = "Conteudo didatico. Nome: direito fundamental. CPF: 987.654.321-00 e um exemplo ficticio."


def create_source(path: Path):
    with pymupdf.open() as document:
        for _ in range(2):
            page = document.new_page()
            page.insert_text((72, 180), BODY, fontsize=10)
            page.insert_text((200, 20), FIELDS[0], fontsize=7)
            for index, field in enumerate(FIELDS[1:]):
                page.insert_text((24, 805 + index * 10), field, fontsize=7)
        document.save(path)


class DocumentPrivacyTests(unittest.TestCase):
    def assert_no_identification(self, text):
        for value in VALUES:
            self.assertNotIn(value, text)
        self.assertNotIn("11122233344", text)

    def test_legacy_markdown_is_cleaned_before_all_exports_in_both_profiles(self):
        for profile in ("curso", "jurisprudencia"):
            with self.subTest(profile=profile), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                source = root / "source.pdf"
                create_source(source)
                source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
                text = "# 1. Conteudo\n\n" + BODY + "\n\n" + "\n".join(FIELDS) + "\n\n" + "texto " * 30
                result = finalize_markdown(source, root / "safe.md", text, 0, True, 100, heading_profile=profile)
                self.assert_no_identification(result.markdown_path.read_text("utf-8"))
                for file in root.glob("safe.*"):
                    self.assert_no_identification(file.read_text("utf-8"))
                for file in (root / "safe_partes").glob("*.md"):
                    self.assert_no_identification(file.read_text("utf-8"))
                self.assertIn(BODY, result.markdown_path.read_text("utf-8"))
                self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), source_hash)
                data = json.loads(result.structure_path.read_text("utf-8"))
                self.assertGreater(data["privacy"]["source_identification_marks"], 0)

    def test_style_entities_and_hidden_attributes_do_not_bypass_known_values(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "source.pdf"
            create_source(source)
            plan = plan_from_source(source)
            text = ('**Nome:** **Pessoa** Exemplo Teste\n'
                    'E-mail: aluno&#64;example.test\n'
                    '<span title="111.222.333-44">seguro</span>\n'
                    'Telefone: (11) 91111-2222\n'
                    '<!-- Pessoa Exemplo Teste -->\n'
                    'CPF: 11122233344\n' + BODY)
            cleaned = plan.clean_text(text)
            self.assert_no_identification(cleaned)
            self.assertNotIn("aluno&#64;example.test", cleaned)
            self.assertNotIn("****", cleaned)
            self.assertIn(BODY, cleaned)
            self.assertEqual(cleaned, plan.clean_text(cleaned))
            self.assert_no_identification(repr(plan))

    def test_isolated_editorial_contact_is_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "source.pdf"
            with pymupdf.open() as document:
                for _ in range(2):
                    document.new_page().insert_text((24, 820), "E-mail: contato@editora.example", fontsize=7)
                document.save(source)
            plan = plan_from_source(source)
            text = "E-mail: contato@editora.example"
            self.assertEqual(plan.clean_text(text), text)
            self.assertEqual(plan.stats["source_identification_marks"], 0)

    def test_bare_identifier_is_removed_next_to_unrelated_page_numbers(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "source.pdf"
            create_source(source)
            plan = plan_from_source(source)
            for text in ("94\n111.222.333-44", "111.222.333-44\n2. Proximo capitulo"):
                self.assert_no_identification(plan.clean_text(text))

    def test_different_longer_name_identifier_and_email_are_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "source.pdf"
            create_source(source)
            plan = plan_from_source(source)
            text = "Pessoa Exemplo Testemunha\nCPF: 111.222.333-445\nE-mail: aluno@example.test.br"
            self.assertEqual(plan.clean_text(text), text)

    def test_html_and_markdown_styles_remain_balanced_when_name_is_split(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "source.pdf"
            create_source(source)
            plan = plan_from_source(source)
            for text in ("<b>Pessoa</b> Exemplo Teste em doutrina.", "**Pessoa** Exemplo Teste em doutrina.",
                         "*Pessoa* Exemplo Teste em doutrina.", "_Pessoa_ Exemplo Teste em doutrina.",
                         "Antes <b>Nome:</b> Pessoa Exemplo Teste em doutrina."):
                cleaned = plan.clean_text(text)
                self.assert_no_identification(cleaned)
                self.assertEqual(cleaned.count("<b>"), cleaned.count("</b>"))
                self.assertEqual(cleaned.count("**") % 2, 0)
                self.assertEqual(cleaned.count("*") % 2, 0)
                self.assertEqual(cleaned.count("_") % 2, 0)
                self.assertIn("em doutrina.", cleaned)

    def test_multiple_identification_fields_on_one_margin_line(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "source.pdf"
            with pymupdf.open() as document:
                for _ in range(2):
                    document.new_page().insert_text(
                        (24, 820), "CPF: 111.222.333-44 Nome: Pessoa Exemplo Teste", fontsize=7,
                    )
                document.save(source)
            plan = plan_from_source(source)
            self.assertEqual(plan.stats["source_identification_marks"], 4)
            self.assert_no_identification(plan.clean_text("CPF: 111.222.333-44 Nome: Pessoa Exemplo Teste"))

    def test_inline_page_marker_is_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "source.pdf"
            create_source(source)
            plan = plan_from_source(source)
            marker = "<!-- NEXOJURIS_PAGE page=2 status=native -->"
            cleaned = plan.clean_text("CPF: 111.222.333-44 " + marker)
            self.assert_no_identification(cleaned)
            self.assertIn(marker, cleaned)

    def test_failed_page_error_is_cleaned_before_checkpoint(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "source.pdf"
            create_source(source)
            plan = plan_from_source(source)
            token = _PRIVACY_CONTEXT.set(plan)
            converter = PdfMarkdownConverter.__new__(PdfMarkdownConverter)

            def fail_load(_index):
                raise RuntimeError("CPF: 111.222.333-44")

            try:
                rendered, coverage = converter._process_page(SimpleNamespace(load_page=fail_load), 1, root)
            finally:
                _PRIVACY_CONTEXT.reset(token)
            self.assert_no_identification(rendered)
            self.assert_no_identification(coverage.warning)
            checkpoint = root / "checkpoint"
            _save_page_checkpoint(checkpoint, {"pages": {}}, 1, rendered, coverage)
            for file in checkpoint.rglob("*"):
                if file.is_file():
                    self.assert_no_identification(file.read_text("utf-8"))

    def test_failed_source_inspection_stops_before_any_native_extraction(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "source.pdf"
            create_source(source)
            converter = PdfMarkdownConverter.__new__(PdfMarkdownConverter)
            converter._pymupdf = pymupdf
            converter._to_markdown = lambda *_args, **_kwargs: self.fail("Não deve extrair após falha da proteção")
            with patch.object(pymupdf.Page, "get_text", side_effect=RuntimeError(VALUES[0])):
                with self.assertRaises(PrivacyProtectionError) as error:
                    converter.convert(source, root / "out", False, 60000, activation_verified=True)
            self.assert_no_identification(str(error.exception))
            self.assertFalse(any((root / "out").glob("*.md")))

    def test_old_checkpoint_is_recomputed_instead_of_reusing_unprotected_pages(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "source.pdf"
            create_source(source)
            checkpoint = root / "checkpoint"
            (checkpoint / "pages").mkdir(parents=True)
            state = _checkpoint_signature(source, (1, 2))
            state.pop("privacy_policy_version", None)
            state["pages"] = {"1": {"status": "fallback"}}
            (checkpoint / "state.json").write_text(json.dumps(state), "utf-8")
            (checkpoint / "pages" / "000001.md").write_text("\n".join(FIELDS), "utf-8")
            converter = PdfMarkdownConverter.__new__(PdfMarkdownConverter)
            converter._pymupdf = pymupdf
            extracted = []

            def native(document, **kwargs):
                index = kwargs["pages"][0]
                extracted.append(index)
                return document.load_page(index).get_text()

            converter._to_markdown = native
            result = converter.convert(source, root / "out", False, 60000, activation_verified=True,
                                       checkpoint_dir=checkpoint)
            self.assertEqual(extracted, [0, 1])
            self.assert_no_identification(result.markdown_path.read_text("utf-8"))

    def test_interrupted_checkpoint_contains_only_protected_text(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "source.pdf"
            create_source(source)
            checkpoint = root / "checkpoint"
            converter = PdfMarkdownConverter.__new__(PdfMarkdownConverter)
            converter._pymupdf = pymupdf
            converter._to_markdown = lambda document, **kw: document.load_page(kw["pages"][0]).get_text()
            with patch("converter._directory_usage", side_effect=[(0, 0), RuntimeError("interrupção sintética")]):
                with self.assertRaisesRegex(RuntimeError, "interrupção sintética"):
                    converter.convert(source, root / "out", False, 60000, activation_verified=True,
                                      checkpoint_dir=checkpoint)
            self.assertTrue((checkpoint / "pages" / "000001.md").exists())
            for file in checkpoint.rglob("*"):
                if file.is_file() and file.suffix in {".md", ".json"}:
                    self.assert_no_identification(file.read_text("utf-8"))

    def test_native_fallback_and_ocr_outputs_are_cleaned(self):
        for method in ("native", "fallback", "ocr"):
            with self.subTest(method=method), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                source = root / "source.pdf"
                create_source(source)
                converter = PdfMarkdownConverter.__new__(PdfMarkdownConverter)
                converter._pymupdf = pymupdf

                def native(document, extraction=method, **kwargs):
                    if extraction == "fallback":
                        raise RuntimeError("falha sintética da extração principal")
                    return document.load_page(kwargs["pages"][0]).get_text()

                converter._to_markdown = native
                with (
                    patch("converter.is_scanned_page", return_value=method == "ocr"),
                    patch("converter.ocr_page_to_markdown", side_effect=lambda page, dpi: page.get_text()),
                ):
                    result = converter.convert(source, root / "out", False, 60000, activation_verified=True)
                self.assert_no_identification(result.markdown_path.read_text("utf-8"))
                self.assertEqual({page.status for page in result.page_coverage}, {method})
                self.assertIn(BODY, result.markdown_path.read_text("utf-8"))


if __name__ == "__main__":
    unittest.main()
