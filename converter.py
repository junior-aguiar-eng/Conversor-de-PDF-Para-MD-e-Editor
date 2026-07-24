"""Conversão de PDFs para Markdown e validação de runtime."""

from __future__ import annotations

from pathlib import Path

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

        self._to_markdown = pymupdf4llm.to_markdown

    def convert(
        self,
        source: Path,
        output_dir: Path,
        split_output: bool,
        max_chunk_characters: int,
    ) -> ConversionResult:
        markdown_path, assets_dir = output_paths(output_dir, source)
        assets_dir.mkdir(parents=True, exist_ok=True)
        markdown = self._to_markdown(
            str(source),
            use_ocr=False,
            force_ocr=False,
            write_images=True,
            image_path=str(assets_dir),
            header=False,
            footer=False,
        )
        absolute_assets = str(assets_dir).replace("\\", "/")
        relative_assets = f"images/{markdown_path.stem}"
        markdown = markdown.replace(absolute_assets, relative_assets).replace(
            str(assets_dir), relative_assets
        )
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
