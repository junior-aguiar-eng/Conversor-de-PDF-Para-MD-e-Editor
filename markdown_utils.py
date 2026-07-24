"""Funções auxiliares para preparar a saída Markdown."""

from __future__ import annotations

import re
from hashlib import sha1
from pathlib import Path

from models import ConversionResult


HEADING_PATTERN = re.compile(r"(?m)^#{1,2}\s+.+?\s*$")
ASSET_NAME_PATTERN = re.compile(r"[^A-Za-z0-9_-]+")


def split_markdown_by_headings(markdown: str, max_characters: int) -> list[str]:
    """Divide textos longos em blocos, respeitando títulos # e ##."""
    if len(markdown) <= max_characters:
        return []

    headings = list(HEADING_PATTERN.finditer(markdown))
    if not headings:
        return []

    sections: list[str] = []
    preamble = markdown[: headings[0].start()].strip()
    for index, heading in enumerate(headings):
        end = headings[index + 1].start() if index + 1 < len(headings) else len(markdown)
        section = markdown[heading.start() : end].strip()
        if index == 0 and preamble:
            section = f"{preamble}\n\n{section}"
        sections.append(section)

    chunks: list[str] = []
    current = ""
    for section in sections:
        if current and len(current) + len(section) + 2 > max_characters:
            chunks.append(current.strip())
            current = section
        else:
            current = f"{current}\n\n{section}".strip() if current else section
    if current:
        chunks.append(current.strip())
    return chunks


def available_output_path(output_dir: Path, stem: str) -> Path:
    """Retorna um caminho livre, sem substituir uma conversão já existente."""
    candidate = output_dir / f"{stem}.md"
    index = 2
    while candidate.exists():
        candidate = output_dir / f"{stem} ({index}).md"
        index += 1
    return candidate


def asset_directory_name(stem: str) -> str:
    """Gera um nome curto e seguro para os recursos extraídos do PDF."""
    normalized = ASSET_NAME_PATTERN.sub("_", stem).strip("_-") or "documento"
    return f"{normalized[:40]}-{sha1(stem.encode('utf-8')).hexdigest()[:8]}"


def output_paths(output_dir: Path, source: Path) -> tuple[Path, Path]:
    markdown_path = available_output_path(output_dir, source.stem)
    return markdown_path, output_dir / "images" / asset_directory_name(markdown_path.stem)


def finalize_markdown(
    source: Path,
    markdown_path: Path,
    markdown: str,
    asset_count: int,
    split_output: bool,
    max_chunk_characters: int,
) -> ConversionResult:
    markdown_path.write_text(markdown, encoding="utf-8")
    chunks = split_markdown_by_headings(markdown, max_chunk_characters) if split_output else []
    if chunks:
        chunks_dir = markdown_path.parent / f"{markdown_path.stem}_partes"
        chunks_dir.mkdir(parents=True, exist_ok=True)
        for index, chunk in enumerate(chunks, start=1):
            portable_chunk = chunk.replace("images/", "../images/")
            (chunks_dir / f"parte_{index:03}.md").write_text(portable_chunk, encoding="utf-8")
    return ConversionResult(source, markdown_path, asset_count, len(chunks))
