"""Conversão de PDFs para Markdown e validação de runtime."""

from __future__ import annotations

import os
from pathlib import Path

from constants import MAX_PAGE_COUNT
from markdown_utils import finalize_markdown, output_paths
from models import ConversionResult


def validate_runtime_dependencies() -> None:
    """Falha cedo com uma mensagem clara se a dependência principal estiver ausente."""
    try:
        import pymupdf4llm  # noqa: F401
    except ImportError as error:
        raise RuntimeError(
            "O conversor não está instalado. Execute instalar_no_d.ps1 para preparar o ambiente."
        ) from error


class PdfMarkdownConverter:
    """Conversor único, leve e local para PDFs com texto nativo."""

    def __init__(self) -> None:
        validate_runtime_dependencies()
        import pymupdf4llm
        import pymupdf

        self._to_markdown = pymupdf4llm.to_markdown
        self._pymupdf = pymupdf

    def convert(
        self,
        source: Path,
        output_dir: Path,
        split_output: bool,
        max_chunk_characters: int,
    ) -> ConversionResult:
        with self._pymupdf.open(source) as document:
            if document.page_count > MAX_PAGE_COUNT:
                raise ValueError(
                    f"O PDF tem {document.page_count:,} páginas. O limite do aplicativo é de "
                    f"{MAX_PAGE_COUNT:,} páginas."
                )

        output_dir = output_dir.resolve()
        markdown_path, assets_dir = output_paths(output_dir, source)
        assets_dir.mkdir(parents=True, exist_ok=True)
        image_path = assets_dir.relative_to(output_dir).as_posix()
        previous_working_directory = Path.cwd()
        try:
            os.chdir(output_dir)
            markdown = self._to_markdown(
                str(source),
                use_ocr=False,
                force_ocr=False,
                write_images=True,
                image_path=image_path,
                header=False,
                footer=False,
            )
        finally:
            os.chdir(previous_working_directory)
        asset_count = sum(1 for item in assets_dir.rglob("*") if item.is_file())
        if asset_count == 0:
            assets_dir.rmdir()
            parent = assets_dir.parent
            if not any(parent.iterdir()):
                parent.rmdir()
        return finalize_markdown(
            source,
            markdown_path,
            markdown,
            asset_count,
            split_output,
            max_chunk_characters,
        )
