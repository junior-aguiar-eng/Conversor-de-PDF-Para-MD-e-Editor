from __future__ import annotations

import shutil
import unittest
from pathlib import Path

from markdown_utils import (
    asset_directory_name,
    available_output_path,
    finalize_markdown,
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


if __name__ == "__main__":
    unittest.main()
