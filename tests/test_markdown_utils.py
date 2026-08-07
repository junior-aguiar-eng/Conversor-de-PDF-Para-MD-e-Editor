from __future__ import annotations

import shutil
import unittest
from pathlib import Path

from markdown_utils import (
    asset_directory_name,
    available_output_path,
    build_table_of_contents,
    finalize_markdown,
    normalize_course_heading_levels,
    normalize_heading_levels,
    split_markdown_by_headings,
)

TEST_TMP_ROOT = Path(__file__).resolve().parent / "_sandbox"


class MarkdownUtilsTests(unittest.TestCase):
    def setUp(self) -> None:
        TEST_TMP_ROOT.mkdir(exist_ok=True)

    def tearDown(self) -> None:
        shutil.rmtree(TEST_TMP_ROOT, ignore_errors=True)

    def test_split_markdown_by_headings_returns_empty_when_text_is_short(self) -> None:
        markdown = "# Titulo\n\nTexto curto."

        self.assertEqual(split_markdown_by_headings(markdown, max_characters=200), [])

    def test_split_markdown_by_headings_preserves_preamble_and_sections(self) -> None:
        markdown = (
            "Introducao\n\n"
            + "# Primeira\n\n"
            + "A" * 30
            + "\n\n## Segunda\n\n"
            + "B" * 30
        )

        chunks = split_markdown_by_headings(markdown, max_characters=60)

        self.assertEqual(len(chunks), 2)
        self.assertTrue(chunks[0].startswith("Introducao\n\n# Primeira"))
        self.assertTrue(chunks[1].startswith("## Segunda"))

    def test_available_output_path_avoids_overwriting_existing_files(self) -> None:
        output_dir = TEST_TMP_ROOT / "collision"
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "arquivo.md").write_text("existente", encoding="utf-8")

        next_path = available_output_path(output_dir, "arquivo")

        self.assertEqual(next_path, output_dir / "arquivo (2).md")

    def test_asset_directory_name_is_short_and_portable(self) -> None:
        name = asset_directory_name("DIREITO CONSTITUCIONAL - E-BOOK 2026 [versão final]")

        self.assertLessEqual(len(name), 49)
        self.assertRegex(name, r"^[A-Za-z0-9_-]+$")

    def test_finalize_markdown_creates_chunk_files_with_relative_image_paths(self) -> None:
        markdown = (
            "# Parte 1\n\n"
            f"![img](images/{asset_directory_name('documento')}/pagina-1.png)\n\n"
            + "A" * 40
            + "\n\n## Parte 2\n\n"
            + "B" * 40
        )

        output_dir = TEST_TMP_ROOT / "chunks"
        output_dir.mkdir(parents=True, exist_ok=True)
        markdown_path = output_dir / "documento.md"

        result = finalize_markdown(
            source=Path("documento.pdf"),
            markdown_path=markdown_path,
            markdown=markdown,
            asset_count=1,
            split_output=True,
            max_chunk_characters=70,
        )

        self.assertEqual(result.chunk_count, 2)
        self.assertTrue(markdown_path.exists())

        first_chunk = output_dir / "documento_partes" / "parte_001.md"
        self.assertTrue(first_chunk.exists())
        self.assertIn(
            f"../images/{asset_directory_name('documento')}/",
            first_chunk.read_text(encoding="utf-8"),
        )


    def test_build_table_of_contents_returns_empty_without_headings(self) -> None:
        self.assertEqual(build_table_of_contents("Texto sem títulos."), "")

    def test_build_table_of_contents_nests_level_two_under_level_one(self) -> None:
        markdown = "# Capítulo 1\n\nTexto.\n\n## Seção 1.1\n\nMais texto."

        toc = build_table_of_contents(markdown)

        self.assertEqual(
            toc,
            "## Sumário\n\n- [Capítulo 1](#capítulo-1)\n  - [Seção 1.1](#seção-11)",
        )

    def test_build_table_of_contents_disambiguates_repeated_titles(self) -> None:
        markdown = "# Introdução\n\nA.\n\n# Introdução\n\nB."

        toc = build_table_of_contents(markdown)

        self.assertIn("[Introdução](#introdução)", toc)
        self.assertIn("[Introdução](#introdução-1)", toc)

    def test_finalize_markdown_prepends_toc_only_to_main_file_not_chunks(self) -> None:
        markdown = (
            "# Parte 1\n\n" + "A" * 40 + "\n\n## Parte 2\n\n" + "B" * 40
        )
        output_dir = TEST_TMP_ROOT / "toc"
        output_dir.mkdir(parents=True, exist_ok=True)
        markdown_path = output_dir / "documento.md"

        finalize_markdown(
            source=Path("documento.pdf"),
            markdown_path=markdown_path,
            markdown=markdown,
            asset_count=0,
            split_output=True,
            max_chunk_characters=70,
            include_toc=True,
        )

        main_content = markdown_path.read_text(encoding="utf-8")
        self.assertTrue(main_content.startswith("## Sumário"))

        first_chunk = output_dir / "documento_partes" / "parte_001.md"
        self.assertFalse(first_chunk.read_text(encoding="utf-8").startswith("## Sumário"))

    def test_normalize_heading_levels_reclassifies_comentario_label_regardless_of_origin_level(
        self,
    ) -> None:
        self.assertEqual(normalize_heading_levels("# **COMENTÁRIO:**"), "## **COMENTÁRIO:**")
        self.assertEqual(normalize_heading_levels("## **COMENTÁRIO:**"), "## **COMENTÁRIO:**")

    def test_normalize_heading_levels_reclassifies_direito_branch_to_level_one(self) -> None:
        self.assertEqual(normalize_heading_levels("## DIREITO CIVIL"), "# DIREITO CIVIL")
        self.assertEqual(normalize_heading_levels("# DIREITO CIVIL"), "# DIREITO CIVIL")

    def test_normalize_heading_levels_matches_branch_name_despite_missing_accent(self) -> None:
        # Alguns PDFs perdem acentuação na extração; a comparação deve
        # ignorar isso e reconhecer o ramo do direito do mesmo jeito.
        self.assertEqual(
            normalize_heading_levels("## DIREITO PREVIDENCIARIO"),
            "# DIREITO PREVIDENCIARIO",
        )

    def test_normalize_heading_levels_reclassifies_legal_instrument_to_level_three(self) -> None:
        heading = "## Lei n. 7.210/1984 (Lei de Execução Penal) (redação dada pela Lei n. 13.964/2019)"
        expected = "### Lei n. 7.210/1984 (Lei de Execução Penal) (redação dada pela Lei n. 13.964/2019)"

        self.assertEqual(normalize_heading_levels(heading), expected)

    def test_normalize_heading_levels_falls_back_to_level_two_for_unrecognized_titles(self) -> None:
        heading = "# Responsabilidade civil do Estado por omissão em fiscalização"
        expected = "## Responsabilidade civil do Estado por omissão em fiscalização"

        self.assertEqual(normalize_heading_levels(heading), expected)

    def test_normalize_heading_levels_is_idempotent_on_already_normalized_markdown(self) -> None:
        markdown = (
            "# DIREITO CIVIL\n\n"
            "Texto introdutório.\n\n"
            "## Responsabilidade civil do Estado por omissão\n\n"
            "Texto do julgado.\n\n"
            "## COMENTÁRIO\n\n"
            "Texto do comentário.\n\n"
            "### Lei n. 8.112/1990 (redação vigente)\n\n"
            "Texto do dispositivo citado."
        )

        self.assertEqual(normalize_heading_levels(markdown), markdown)

    def test_build_table_of_contents_ignores_level_three_after_normalization(self) -> None:
        markdown = normalize_heading_levels(
            "# DIREITO CIVIL\n\n"
            "## COMENTÁRIO\n\n"
            "## Lei n. 8.112/1990 (redação vigente)\n\n"
            "Texto."
        )

        toc = build_table_of_contents(markdown)

        self.assertIn("[DIREITO CIVIL]", toc)
        self.assertIn("[COMENTÁRIO]", toc)
        self.assertNotIn("Lei n. 8.112", toc)

    def test_normalize_course_heading_levels_uses_numeric_depth_plus_one(self) -> None:
        # Profundidade 4 ("1.3.1.2" = 4 grupos) -> nível 5.
        heading = "## **1.3.1.2. ASPECTOS CONTEXTUAIS**"
        expected = "##### **1.3.1.2. ASPECTOS CONTEXTUAIS**"

        self.assertEqual(normalize_course_heading_levels(heading), expected)

    def test_normalize_course_heading_levels_tolerates_missing_final_dot(self) -> None:
        with_dot = normalize_course_heading_levels("## **1.3.1.**")
        without_dot = normalize_course_heading_levels("## **1.3.1**")

        self.assertTrue(with_dot.startswith("#### "))
        self.assertTrue(without_dot.startswith("#### "))
        self.assertEqual(without_dot, "#### **1.3.1**")

    def test_normalize_course_heading_levels_nests_uppercase_letter_under_last_numeric(
        self,
    ) -> None:
        markdown = "## 1.3. Seção\n\nTexto.\n\n## A. Item\n\nTexto do item."

        result = normalize_course_heading_levels(markdown)

        self.assertIn("### 1.3. Seção", result)
        self.assertIn("#### A. Item", result)

    def test_normalize_course_heading_levels_nests_lowercase_and_roman_under_scope(self) -> None:
        # "ii)" (não "i)") de propósito: um único caractere ambíguo como
        # "i)" casa primeiro com a regra 4 (letra minúscula), por prioridade
        # — ver comentário em COURSE_ROMAN_PREFIX_PATTERN.
        markdown = (
            "## 1. Seção\n\n"
            "## A. Item\n\n"
            "## a) Subitem\n\n"
            "## ii) Detalhe\n\n"
        )

        result = normalize_course_heading_levels(markdown)

        self.assertIn("## 1. Seção", result)
        self.assertIn("### A. Item", result)
        self.assertIn("#### a) Subitem", result)
        self.assertIn("##### ii) Detalhe", result)

    def test_normalize_course_heading_levels_demotes_headings_without_structural_prefix(
        self,
    ) -> None:
        heading = (
            "## **O Neoconstitucionalismo possui como principais características:**"
        )

        result = normalize_course_heading_levels(heading)

        self.assertNotIn("#", result)
        self.assertEqual(
            result,
            "**O Neoconstitucionalismo possui como principais características:**",
        )

    def test_normalize_course_heading_levels_keeps_atencao_label_at_level_two(self) -> None:
        heading = "## **ATENÇÃO! JUDICIALIZAÇÃO DA SAÚDE.**"

        self.assertEqual(normalize_course_heading_levels(heading), heading)

    def test_normalize_course_heading_levels_keeps_direito_branch_at_level_one(self) -> None:
        self.assertEqual(
            normalize_course_heading_levels("# DIREITO CONSTITUCIONAL"),
            "# DIREITO CONSTITUCIONAL",
        )
        self.assertEqual(
            normalize_course_heading_levels("## DIREITO CONSTITUCIONAL"),
            "# DIREITO CONSTITUCIONAL",
        )

    def test_normalize_course_heading_levels_is_idempotent(self) -> None:
        markdown = (
            "# DIREITO CONSTITUCIONAL\n\n"
            "## 1. Neoconstitucionalismo\n\n"
            "Texto introdutório não vira heading.\n\n"
            "### 1.1. Premissas\n\n"
            "#### A. Marco histórico\n\n"
            "##### a) Constituições rígidas\n\n"
            "###### i) Efeito vinculante\n\n"
            "## **ATENÇÃO! JUDICIALIZAÇÃO DA SAÚDE.**\n\n"
        )

        once = normalize_course_heading_levels(markdown)
        twice = normalize_course_heading_levels(once)

        self.assertEqual(once, twice)

    def test_finalize_markdown_applies_course_profile_when_requested(self) -> None:
        markdown = (
            "# DIREITO CONSTITUCIONAL\n\n"
            "## **Frase de corpo capturada por engano:**\n\n"
            "## 1. Seção real\n\n"
        )
        output_dir = TEST_TMP_ROOT / "course_profile"
        output_dir.mkdir(parents=True, exist_ok=True)
        markdown_path = output_dir / "documento.md"

        finalize_markdown(
            source=Path("documento.pdf"),
            markdown_path=markdown_path,
            markdown=markdown,
            asset_count=0,
            split_output=False,
            max_chunk_characters=10_000,
            heading_profile="curso",
        )

        content = markdown_path.read_text(encoding="utf-8")
        self.assertIn("**Frase de corpo capturada por engano:**\n", content)
        self.assertNotIn("## **Frase de corpo capturada por engano:**", content)
        self.assertIn("## 1. Seção real", content)


if __name__ == "__main__":
    unittest.main()
