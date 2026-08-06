from __future__ import annotations

import shutil
import unittest
from pathlib import Path

from markdown_utils import (
    asset_directory_name,
    available_output_path,
    build_table_of_contents,
    finalize_markdown,
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


if __name__ == "__main__":
    unittest.main()
