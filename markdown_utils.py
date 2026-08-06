"""Funções auxiliares para preparar a saída Markdown."""

from __future__ import annotations

import re
import unicodedata
from hashlib import sha1
from pathlib import Path

from models import ConversionResult

HEADING_PATTERN = re.compile(r"(?m)^#{1,2}\s+.+?\s*$")
# O pymupdf4llm rankeia até 6 tamanhos de fonte distintos como níveis de
# título (1 a 6), então a saída bruta pode conter headings de nível 3+ —
# confirmado empiricamente. normalize_heading_levels precisa localizar
# TODOS eles para reclassificar; HEADING_PATTERN continua limitado a {1,2}
# de propósito para split_markdown_by_headings/build_table_of_contents, que
# devem seguir ignorando níveis 3+ mesmo depois da normalização.
RAW_HEADING_PATTERN = re.compile(r"(?m)^#{1,6}\s+.+?\s*$")
ASSET_NAME_PATTERN = re.compile(r"[^A-Za-z0-9_-]+")
SLUG_INVALID_CHARS_PATTERN = re.compile(r"[^\w\s-]")
SLUG_WHITESPACE_PATTERN = re.compile(r"\s+")

# Nomes de ramo do direito reconhecidos como título de nível 1 (ver
# normalize_heading_levels). Lista extensível: adicione novos ramos aqui,
# em maiúsculas — a comparação já ignora variação de acentuação.
DIREITO_BRANCH_HEADINGS: frozenset[str] = frozenset(
    {
        "DIREITO CONSTITUCIONAL",
        "DIREITO ADMINISTRATIVO",
        "DIREITO CIVIL",
        "DIREITO PROCESSUAL CIVIL",
        "DIREITO PREVIDENCIÁRIO",
        "DIREITO DA CRIANÇA E DO ADOLESCENTE",
        "DIREITO DIGITAL",
        "DIREITO INTERNACIONAL",
        "DIREITO AMBIENTAL",
        "DIREITO EMPRESARIAL",
        "EXECUÇÃO PENAL",
        "DIREITO PENAL",
        "DIREITO PROCESSUAL PENAL",
        "DIREITO TRIBUTÁRIO",
        "DIREITO DO CONSUMIDOR",
        "DIREITO ELEITORAL",
    }
)
# Rótulos de seção fixa reconhecidos como título de nível 2 (além do
# fallback padrão — ver normalize_heading_levels).
SECTION_LABEL_HEADINGS: frozenset[str] = frozenset({"COMENTÁRIO"})
# Início reconhecido de um cabeçalho de dispositivo legal citado (Lei,
# Código ou Constituição Federal), com anotação opcional entre parênteses.
LEGAL_INSTRUMENT_PATTERN = re.compile(r"^(CONSTITUICAO FEDERAL|LEI\b|CODIGO\b)")


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


def _strip_accents(text: str) -> str:
    """Remove acentos para comparação tolerante a variação de acentuação
    entre PDFs de origem diferentes."""
    normalized = unicodedata.normalize("NFKD", text)
    return "".join(char for char in normalized if not unicodedata.combining(char))


_DIREITO_BRANCH_HEADINGS_NORMALIZED = frozenset(
    _strip_accents(name).upper() for name in DIREITO_BRANCH_HEADINGS
)
_SECTION_LABEL_HEADINGS_NORMALIZED = frozenset(
    _strip_accents(name).upper() for name in SECTION_LABEL_HEADINGS
)


def _canonicalize_heading_text(text: str) -> str:
    """Remove marcação de negrito e normaliza espaços/dois-pontos nas bordas,
    só para fins de comparação — o texto original é preservado na saída."""
    cleaned = text.replace("**", "").replace("__", "").strip()
    cleaned = cleaned.strip(":").strip()
    return re.sub(r"\s+", " ", cleaned)


def _classify_heading_level(text: str) -> int:
    """Reclassifica o nível de um título pelo padrão do conteúdo, não pelo
    nível que o pymupdf4llm atribuiu a partir do tamanho de fonte do PDF de
    origem (que varia entre páginas sem relação com a hierarquia real).

    Regras, em ordem de prioridade — a primeira que casar decide o nível:
    1. Nome de ramo do direito conhecido, inteiramente em maiúsculas -> nível 1.
    2. Rótulo de seção fixa (ex.: "COMENTÁRIO") -> nível 2.
    3. Cabeçalho de dispositivo legal citado (Lei, Código ou Constituição
       Federal, com anotação opcional entre parênteses) -> nível 3.
    4. Qualquer outro título — assumido como julgado individual, filho do
       ramo do direito mais próximo acima no documento -> nível 2 (padrão).
    """
    canonical = _canonicalize_heading_text(text)
    normalized = _strip_accents(canonical).upper()

    if canonical.isupper() and normalized in _DIREITO_BRANCH_HEADINGS_NORMALIZED:
        return 1
    if normalized in _SECTION_LABEL_HEADINGS_NORMALIZED:
        return 2
    if LEGAL_INSTRUMENT_PATTERN.match(normalized):
        return 3
    return 2


def _normalize_heading_match(match: re.Match[str]) -> str:
    # O \s*$ do HEADING_PATTERN é guloso e pode engolir parte da linha em
    # branco que separa o título do parágrafo seguinte. Preservamos esse
    # espaço à direita na substituição para não colapsar os parágrafos.
    raw = match.group()
    heading_line = raw.rstrip()
    trailing_whitespace = raw[len(heading_line) :]
    level = len(heading_line) - len(heading_line.lstrip("#"))
    text = heading_line[level:].strip()
    new_level = _classify_heading_level(text)
    return f"{'#' * new_level} {text}{trailing_whitespace}"


def normalize_heading_levels(markdown: str) -> str:
    """Reclassifica o nível de cada título do markdown bruto (saída de
    pymupdf4llm.to_markdown, que pode conter níveis 1 a 6) por padrões de
    conteúdo (ver _classify_heading_level), preservando o texto e a
    marcação de negrito originais de cada título."""
    return RAW_HEADING_PATTERN.sub(_normalize_heading_match, markdown)


def _slugify_heading(text: str) -> str:
    """Gera um id de âncora no estilo GitHub a partir do texto de um título."""
    slug = SLUG_INVALID_CHARS_PATTERN.sub("", text.strip().lower())
    slug = SLUG_WHITESPACE_PATTERN.sub("-", slug)
    return slug or "secao"


def build_table_of_contents(markdown: str) -> str:
    """Gera um sumário a partir dos títulos # e ## do texto.

    Os ids de âncora seguem a convenção do GitHub, por melhor esforço: os
    links funcionam nos leitores mais comuns (GitHub, VS Code, Obsidian),
    mas nem todo visualizador de Markdown gera o mesmo id de âncora.
    """
    headings = list(HEADING_PATTERN.finditer(markdown))
    if not headings:
        return ""

    seen_slugs: dict[str, int] = {}
    lines = ["## Sumário", ""]
    for match in headings:
        heading_line = match.group().strip()
        level = len(heading_line) - len(heading_line.lstrip("#"))
        text = heading_line[level:].strip()
        slug = _slugify_heading(text)
        occurrence = seen_slugs.get(slug, 0)
        seen_slugs[slug] = occurrence + 1
        if occurrence:
            slug = f"{slug}-{occurrence}"
        indent = "  " if level == 2 else ""
        lines.append(f"{indent}- [{text}](#{slug})")
    return "\n".join(lines)


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
    include_toc: bool = False,
    extraction_seconds: float = 0.0,
) -> ConversionResult:
    # Reclassifica os níveis de título por conteúdo antes de qualquer outra
    # função consumir o texto: o corte em partes e o sumário devem ver a
    # hierarquia corrigida, não a que o pymupdf4llm inferiu da fonte do PDF.
    markdown = normalize_heading_levels(markdown)
    # O sumário é calculado a partir do markdown já normalizado e só entra
    # no arquivo principal: as partes (abaixo) continuam vindo do texto sem
    # sumário, para não gerar uma parte espúria contendo só o índice.
    chunks = split_markdown_by_headings(markdown, max_chunk_characters) if split_output else []
    toc = build_table_of_contents(markdown) if include_toc else ""
    full_markdown = f"{toc}\n\n{markdown}" if toc else markdown
    markdown_path.write_text(full_markdown, encoding="utf-8")
    if chunks:
        chunks_dir = markdown_path.parent / f"{markdown_path.stem}_partes"
        chunks_dir.mkdir(parents=True, exist_ok=True)
        for index, chunk in enumerate(chunks, start=1):
            portable_chunk = chunk.replace("images/", "../images/")
            (chunks_dir / f"parte_{index:03}.md").write_text(portable_chunk, encoding="utf-8")
    return ConversionResult(source, markdown_path, asset_count, len(chunks), extraction_seconds)
