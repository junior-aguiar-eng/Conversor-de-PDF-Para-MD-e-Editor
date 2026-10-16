"""Reconstrução conservadora de tabelas por células atestadas no PDF local."""
from __future__ import annotations

import html
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from itertools import groupby
from pathlib import Path

from document_privacy import IdentificationPlan

_PAGE = re.compile(r'<!-- NEXOJURIS_PAGE page=(\d+) status=(\w+) -->')


@dataclass
class SourceTable:
    page: int
    bbox: tuple
    height: float
    edges: tuple
    rows: list[list[str]]
    header: bool


@dataclass
class MarkdownTable:
    start: int
    end: int
    page: int
    rows: list[list[str]]
    source: SourceTable | None = None


def _key(value: str) -> str:
    from course_editorial import _visible

    plain = unicodedata.normalize('NFKD', html.unescape(_visible(value))).casefold()
    plain = re.sub(r'(?<=[^\W\d_])[-–—]\s*(?=[^\W\d_])', '', plain)
    return ''.join(c for c in plain.replace('–', '-').replace('—', '-')
                   if not c.isspace() and not unicodedata.combining(c) and c not in '*_')


def _columns(rows: list[list[str]]) -> tuple[str, ...]:
    return tuple(_key(' '.join(row[column] for row in rows)) for column in range(len(rows[0])))


def _cell(rect, blocks, underlines) -> tuple[str, bool]:
    """Lê spans protegidos por célula; não reutiliza o Markdown já fragmentado."""
    from course_editorial import SourceEvidence, _record_hyphenation, _repair_hyphenation

    selected = []
    for block in blocks:
        for line in block.get('lines', []):
            spans = []
            for span in line['spans']:
                chars = [c for c in span['chars'] if rect.x0 <= (c['bbox'][0] + c['bbox'][2]) / 2 <= rect.x1
                         and rect.y0 <= c['origin'][1] <= rect.y1]
                if chars:
                    spans.append({**span, 'chars': chars, 'text': ''.join(c['c'] for c in chars)})
            if spans and any(s['text'].strip() for s in spans):
                selected.append((line['bbox'][1], line['bbox'][0], spans))
    selected.sort(key=lambda item: item[:2])
    lines, plain_lines, bold = [], [], []
    for _, _, spans in selected:
        fragments = []
        for span in spans:
            text = span['text']
            if not text.strip():
                fragments.append(html.escape(text))
                continue
            # Preserve espaços junto da troca de fonte fora dos delimitadores.
            leading = text[:len(text) - len(text.lstrip())]
            trailing = text[len(text.rstrip()):]
            def escaped(value):
                value = html.escape(value).replace('|', '&#124;')
                for char in '*_`\\':
                    value = value.replace(char, f'&#{ord(char)};')
                return value

            def underlined(char):
                x = (char['bbox'][0] + char['bbox'][2]) / 2
                baseline = char['origin'][1]
                return any(left <= x <= right and 0 <= y - baseline <= 3 for left, right, y in underlines)

            chars = span['chars'][len(leading):len(text) - len(trailing) if trailing else len(text)]
            inner = ''.join(('<u>' if marked else '') + escaped(''.join(c['c'] for c in group))
                            + ('</u>' if marked else '') for marked, group in groupby(chars, underlined))
            if span.get('flags', 0) & 2:
                inner = '<em>' + inner + '</em>'
            if span.get('flags', 0) & 16:
                inner = '<strong>' + inner + '</strong>'
            bold.append(bool(span.get('flags', 0) & 16))
            fragments.append(leading + inner + trailing)
        lines.append(''.join(fragments).strip())
        plain_lines.append(''.join(s['text'] for s in spans).strip())
    evidence = SourceEvidence()
    vocabulary = set(re.findall(r'[^\W\d_]+', ' '.join(re.sub(r'[^\W\d_]+-$', '', s) for s in plain_lines)))
    for first, second in zip(plain_lines, plain_lines[1:], strict=False):
        _record_hyphenation(first, second, vocabulary, evidence)
    text = lines[0] if lines else ''
    item = re.compile(r'^(?:(?:[IVXLCDMivxlcdm]+|\d+|[A-Za-z])[.)]\s|[IVXLCDM]+\s*[-–—]\s)')
    for index in range(1, len(lines)):
        # Reflua linhas físicas comuns; mantenha a apresentação de listas e rótulos.
        separator = '<br>' if item.match(plain_lines[index]) or plain_lines[index - 1].endswith(':') else ' '
        text += separator + lines[index]
    return _repair_hyphenation(text, evidence, Counter()), bool(bold) and all(bold)


def _read_tables(source: Path, privacy: IdentificationPlan, pages: set[int]) -> list[SourceTable]:
    import pymupdf

    if not source.is_file():
        return []
    try:
        document = pymupdf.open(source)
    except (pymupdf.FileDataError, pymupdf.EmptyFileError):
        return []
    tables = []
    with document:
        # A proteção pode apagar caminhos compartilhados com rodapés. Guarde
        # apenas geometria; a leitura de texto ocorre após a proteção.
        paths = {n: document[n - 1].get_drawings() for n in pages if 0 < n <= len(document)}
        privacy.redact_working_document(document)
        for number, drawings in sorted(paths.items()):
            page = document[number - 1]
            if page.rotation:
                # A geometria da extração pode estar em outro referencial.
                # Preserve o original quando não houver correspondência segura.
                continue
            blocks = page.get_text('rawdict', flags=pymupdf.TEXTFLAGS_RAWDICT & ~pymupdf.TEXT_PRESERVE_IMAGES)['blocks']
            underlines = []
            for drawing in drawings:
                for item in drawing['items']:
                    if item[0] == 'l' and abs(item[1].y - item[2].y) < .5:
                        underlines.append((min(item[1].x, item[2].x), max(item[1].x, item[2].x), item[1].y))
                    elif item[0] == 're' and item[1].height <= 1.5:
                        underlines.append((item[1].x0, item[1].x1, item[1].y0))
            # PyMuPDF também consulta o modelo opcional mesmo em lines_strict.
            # Esta instância privada usa somente os caminhos capturados; nenhum
            # estado global ou opção do extrator do chamador é alterado.
            page.layout_information = None
            page.get_layout = lambda **kwargs: None
            finder = page.find_tables(paths=drawings, strategy='lines_strict')
            for table in finder.tables if finder is not None else ():
                # Markdown não representa rowspan/colspan. Não achate células mescladas.
                source_rows = table.rows
                if (not source_rows or len(source_rows[0].cells) < 2
                        or any(cell is None for row in source_rows for cell in row.cells)):
                    continue
                rows, bold_rows = [], []
                for row in source_rows:
                    cells = [_cell(pymupdf.Rect(cell) + (-.5, -.5, .5, .5), blocks, underlines) for cell in row.cells]
                    rows.append([value for value, _ in cells])
                    bold_rows.append(all(bold for _, bold in cells))
                edges = tuple([source_rows[0].cells[0][0], *[cell[2] for cell in source_rows[0].cells]])
                tables.append(SourceTable(number, table.bbox, page.rect.height, edges, rows,
                                          len(rows) > 1 and bold_rows[0]))
    return tables


def _markdown_tables(lines: list[str]) -> list[MarkdownTable]:
    tables = []
    page, index, fence = 1, 0, None
    while index < len(lines):
        line = lines[index]
        markers = list(_PAGE.finditer(line))
        if markers:
            page = int(markers[-1][1])
        code = re.match(r'^\s*(`{3,}|~{3,})', line)
        if code:
            if fence is None:
                fence = code[1]
            elif code[1][0] == fence[0] and len(code[1]) >= len(fence):
                fence = None
        if fence or not line.startswith('|'):
            index += 1
            continue
        start, rows, separators = index, [], 0
        while index < len(lines) and lines[index].startswith('|'):
            raw = lines[index].strip()
            cells = [cell.strip() for cell in re.split(r'(?<!\\)\|', raw[1:-1])] if raw.endswith('|') else []
            if cells and all(re.fullmatch(r':?-{3,}:?', cell) for cell in cells):
                separators += 1
            else:
                rows.append(cells)
            index += 1
        if separators == 1 and rows and len(rows[0]) >= 2 and all(len(row) == len(rows[0]) for row in rows):
            tables.append(MarkdownTable(start, index, page, rows))
    return tables


def _render(rows: list[list[str]], header: bool) -> list[str]:
    def line(row):
        return '| ' + ' | '.join(row) + ' |'

    headings = rows[0] if header else [''] * len(rows[0])
    body = rows[1:] if header else rows
    return [line(headings), line(['---'] * len(headings)), *[line(row) for row in body]]


def repair_pdf_tables(markdown: str, source: Path, privacy: IdentificationPlan) -> tuple[str, dict[str, int]]:
    """Substitui apenas tabelas com texto de cada coluna integralmente correspondente."""
    lines = markdown.splitlines()
    extracted = _markdown_tables(lines)
    if not extracted:
        return markdown, {}
    source_tables = _read_tables(source, privacy, {table.page for table in extracted})
    stats = Counter()
    for table in extracted:
        signature = _columns(table.rows)
        candidates = [t for t in source_tables if t.page == table.page and _columns(t.rows) == signature]
        if len(candidates) == 1:
            table.source = candidates[0]
        elif len(candidates) > 1:
            stats['source_tables_ambiguous'] += 1
    replacements = []
    index = 0
    while index < len(extracted):
        current = extracted[index]
        if current.source is None:
            index += 1
            continue
        rows = [row.copy() for row in current.source.rows]
        last, end, joined = current.source, current.end, 0
        while index + 1 < len(extracted):
            following = extracted[index + 1]
            next_source = following.source
            gap = '\n'.join(lines[end:following.start])
            if (next_source is None or next_source.page != last.page + 1 or next_source.header
                    or len(last.edges) != len(next_source.edges)
                    or any(abs(a - b) > 2 for a, b in zip(last.edges, next_source.edges, strict=True))
                    or last.bbox[3] < last.height * .85 or next_source.bbox[1] > next_source.height * .15
                    or _PAGE.sub('', gap).strip().strip('-').strip()
                    or _key(next_source.rows[0][0]) or not _key(rows[-1][0])):
                break
            marker = next((m[0] for m in _PAGE.finditer(gap) if int(m[1]) == next_source.page), None)
            if marker is None:
                break
            if not any(next_source.rows[0]):
                break
            for column, cell in enumerate(next_source.rows[0]):
                if cell:
                    from course_editorial import _visible

                    item = re.match(r'^(?:(?:[IVXLCDMivxlcdm]+|\d+|[A-Za-z])[.)]\s)', _visible(cell))
                    separator = '<br>' if item else ' '
                    rows[-1][column] += separator + marker + ' ' + cell
                    marker = ''
            rows.extend(row.copy() for row in next_source.rows[1:])
            end, last = following.end, next_source
            joined += 1
            index += 1
        replacements.append((current.start, end, _render(rows, current.source.header)))
        stats['source_tables_rebuilt'] += joined + 1
        stats['source_table_continuations_joined'] += joined
        index += 1
    for start, end, replacement in reversed(replacements):
        lines[start:end] = replacement
    return ('\n'.join(lines) + '\n', dict(stats)) if replacements else (markdown, dict(stats))
