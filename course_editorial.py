"""Preparação editorial conservadora de apostilas extraídas para Markdown."""

from __future__ import annotations

import hashlib
import logging
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)
_LETTER = r"[^\W\d_]"
_PAGE = re.compile(r"<!-- NEXOJURIS_PAGE page=(\d+) status=(\w+) -->")
_STYLE = re.compile(r"<!--.*?-->|</?(?:u|mark|em|strong|b|i|sup|sub)\b[^>]*>|<br\s*/?>|\*\*|__|(?<!\w)[*_]|[*_](?!\w)", re.S)
_IMAGE = re.compile(r"!\[[^\]\n]*\]\(([^)\n]+)\)")
_HEADING = re.compile(r"^(#{1,6})[ \t]+(.+)$")
_NUMERIC = re.compile(r"^\d+(?:\.\d+)*\.?(?:\s|$)")
_COLLECTION = re.compile(r"^(?:SÚMULAS|JULGADOS)\s+D[OA]\s+(?:STF|STJ|TST|STF/STJ)$", re.I)
_SUBCOLLECTION = re.compile(r"^(?:TESES DE REPERCUSSÃO GERAL|JURISPRUDÊNCIA EM TESES|RECURSOS REPETITIVOS)$", re.I)
_DECISION = re.compile(r"^(?:SÚMULA(?: VINCULANTE)?\s+N[º°o.]?\s*\d+|TEMA\s+\d+(?:\s+E\s+\d+)*)$", re.I)
_ENCLITICS = frozenset({"se", "me", "te", "nos", "vos", "lhe", "lhes", "lo", "la", "los", "las"})
_COMPOUND_PREFIXES = frozenset({'ex', 'vice', 'pós', 'pré', 'pró', 'recém', 'além', 'aquém', 'sem', 'não'})
_CALLOUT = re.compile(
    r'^(?:ATENÇÃO\s*!|OBSERVAÇÃO\s*:|OBS\.|IMPORTANTE\s*:|NOTA\s*:|CUIDADO\s*[!:]|DICA\s*:|LEMBRE-SE\s*[!:])', re.I)


@dataclass
class SourceEvidence:
    hyphenations: dict[tuple[str, str], str] = field(default_factory=dict)
    contextual_hyphenations: dict[tuple[str, str], str] = field(default_factory=dict)
    word_spacing: dict[str, tuple[str, str]] = field(default_factory=dict)
    has_source: bool = False
    fused_words: set[str] = field(default_factory=set)
    heading_suffixes: set[str] = field(default_factory=set)
    separated_forms: set[tuple[str, str]] = field(default_factory=set)
    editorial_labels: set[str] = field(default_factory=set)
    layout_headings: set[str] = field(default_factory=set)
    line_adjacencies: set[tuple[str, str]] = field(default_factory=set)
    note_boxes: dict[int, list[str]] = field(default_factory=dict)
    note_box_ends: dict[tuple[int, str], int] = field(default_factory=dict)
    page_texts: dict[int, str] = field(default_factory=dict)
    page_passages: dict[int, list[str]] = field(default_factory=dict)


@dataclass
class EditorialResult:
    markdown: str
    stats: dict[str, int] = field(default_factory=dict)
    issues: tuple[str, ...] = ()


def _project(text: str) -> tuple[str, list[tuple[int, int]]]:
    """Texto visível e mapa reversível; não descarta a marcação original."""
    chars: list[str] = []
    positions: list[tuple[int, int]] = []
    cursor = 0
    for match in _STYLE.finditer(text):
        chars.extend(text[cursor:match.start()])
        positions.extend((i, i + 1) for i in range(cursor, match.start()))
        if match.group().lower().startswith('<br'):
            chars.append('\n')
            positions.append(match.span())
        cursor = match.end()
    chars.extend(text[cursor:])
    positions.extend((i, i + 1) for i in range(cursor, len(text)))
    return ''.join(chars), positions


def _visible(text: str) -> str:
    return _project(text)[0].strip()


def _strip_visible_edges(text: str) -> str:
    plain, mapping = _project(text)
    first = len(plain) - len(plain.lstrip())
    last = len(plain.rstrip())
    delete = set()
    for position in list(range(first)) + list(range(last, len(plain))):
        start, end = mapping[position]
        delete.update(range(start, end))
    return ''.join(char for index, char in enumerate(text) if index not in delete)


def _clean_styles(text: str) -> str:
    for _ in range(4):
        old = text
        text = re.sub(r'<(u|mark|em|strong|b|i)>\s*</\1>', '', text)
        text = re.sub(r'</(u|mark|em|strong|b|i)><\1>', '', text)
        text = re.sub(r'(?<=\S)(?:\*{4}|_{4})(?=\S)', '', text)
        if text == old:
            break
    return text


def _split_visible(text: str, offset: int) -> tuple[str, str]:
    """Divide o texto visível fechando/reabrindo estilos que cruzam o corte."""
    plain, mapping = _project(text)
    if offset <= 0:
        return '', text
    if offset >= len(plain):
        return text, ''
    cut = mapping[offset][0]
    left, right = text[:cut], text[cut:]
    active: list[tuple[str, str]] = []
    for match in _STYLE.finditer(left):
        token = match.group()
        if token.startswith('<!--') or token.lower().startswith('<br'):
            continue
        if token.startswith('</'):
            name = token[2:-1].lower()
            if active and active[-1][1] == f'</{name}>':
                active.pop()
        elif token.startswith('<'):
            name = token[1:-1].split()[0].lower()
            active.append((token, f'</{name}>'))
        elif active and active[-1][0] == token:
            active.pop()
        else:
            active.append((token, token))
    return (
        _clean_styles(left.rstrip() + ''.join(close for _, close in reversed(active))),
        _clean_styles(''.join(opening for opening, _ in active) + right.lstrip()),
    )


def _source_box_rectangles(page) -> list[tuple[float, float, float, float]]:
    """Bordas fechadas, inclusive as quatro faixas que alguns PDFs desenham."""
    horizontal, vertical, boxes = [], [], set()
    for drawing in page.get_drawings():
        for item in drawing['items']:
            if item[0] == 're':
                rect = item[1]
                if rect.width > 60 and rect.height > 15:
                    boxes.add(tuple(rect))
                elif rect.width > 60 and rect.height <= 2:
                    horizontal.append((rect.x0, rect.x1, (rect.y0 + rect.y1) / 2))
                elif rect.height > 15 and rect.width <= 2:
                    vertical.append(((rect.x0 + rect.x1) / 2, rect.y0, rect.y1))
            elif item[0] == 'l':
                a, b = item[1:3]
                if abs(a.y - b.y) <= 1 and abs(a.x - b.x) > 60:
                    horizontal.append((min(a.x, b.x), max(a.x, b.x), (a.y + b.y) / 2))
                elif abs(a.x - b.x) <= 1 and abs(a.y - b.y) > 15:
                    vertical.append(((a.x + b.x) / 2, min(a.y, b.y), max(a.y, b.y)))
    for x0, y0, y1 in vertical:
        for x1, top, bottom in vertical:
            if x1 - x0 <= 60 or abs(top - y0) > 2 or abs(bottom - y1) > 2:
                continue
            if all(any(abs(y - edge) <= 2 and left <= x0 + 2 and right >= x1 - 2
                       for left, right, y in horizontal) for edge in (y0, y1)):
                boxes.add((x0, y0, x1, y1))
    return sorted(boxes)


def read_source_evidence(source: Path | None) -> SourceEvidence:
    """Usa quebras reais do PDF; não consulta dicionários nem altera vocabulário."""
    evidence = SourceEvidence()
    if source is None or not source.is_file():
        return evidence
    import pymupdf

    try:
        document = pymupdf.open(source)
    except (pymupdf.FileDataError, pymupdf.EmptyFileError):
        # Evidência opcional: preserve a extração já existente quando a fonte
        # não puder ser lida; nunca invente geometria ou fronteiras lexicais.
        return evidence
    with document:
        if document.needs_pass:
            return evidence
        from document_privacy import plan_from_source

        # Capture somente geometria antes da proteção: a redação pode remover
        # caminhos vetoriais compartilhados com o rodapé. Leia texto só depois.
        page_boxes = [_source_box_rectangles(page) for page in document]
        plan_from_source(source).redact_working_document(document)
        pages: list[list[str]] = []
        block_pairs: list[tuple[str, str]] = []
        vocabulary: set[str] = set()
        layout_candidates = []
        callouts = []
        all_boxes = []
        body_lines = {}
        trailing_labels = []
        heading_styles: Counter = Counter()
        for page_number, page in enumerate(document, 1):
            lines = []
            blocks = page.get_text('dict', flags=pymupdf.TEXTFLAGS_DICT & ~pymupdf.TEXT_PRESERVE_IMAGES)['blocks']
            for coordinates in page_boxes[page_number - 1]:
                box = pymupdf.Rect(coordinates) + (-1, -1, 1, 1)
                boxed = sorted((line for block in blocks for line in block.get('lines', [])
                                if box.contains(pymupdf.Rect(line['bbox']))
                                and any(s['text'].strip() for s in line['spans'])),
                               key=lambda line: (line['bbox'][1], line['bbox'][0]))
                content = [''.join(s['text'] for s in line['spans']).strip() for line in boxed]
                if content:
                    entry = (page_number, coordinates, page.rect.height, ' '.join(content))
                    all_boxes.append(entry)
                    if _CALLOUT.match(content[0]):
                        callouts.append(entry)
            for block in blocks:
                block_lines = []
                previous_hyphen = False
                for line in block.get('lines', []):
                    x0, y0, x1, y1 = line['bbox']
                    if x1 < 40 or x0 > page.rect.width - 35 or y0 < 45 or y1 > page.rect.height - 45:
                        continue
                    text = ''.join(span['text'] for span in line['spans']).strip()
                    if not text or re.match(r'^(?:CPF:|Nome:|E-mail:|Telefone:)', text):
                        continue
                    if text.isdigit() and y0 > page.rect.height * .90:
                        continue
                    lines.append((y0, x0, text))
                    block_lines.append(text)
                    # Não considere o fragmento hifenizado final uma palavra completa.
                    complete = re.sub(rf'{_LETTER}+[-‐‑]$', '', text)
                    if previous_hyphen:
                        complete = re.sub(rf'^{_LETTER}+\b', '', complete)
                    vocabulary.update(re.findall(rf'{_LETTER}+(?:-{_LETTER}+)*', complete))
                    evidence.fused_words.update(re.findall(rf'{_LETTER}{{3,}}', text))
                    evidence.separated_forms.update(re.findall(rf'\b({_LETTER}+)\s+({_LETTER}+)\b', text))
                    if re.match(r'^[A-Z]\.\s', text) and len(text) < 150 and all(
                        span.get('flags', 0) & 16 for span in line['spans'] if span['text'].strip()
                    ):
                        evidence.heading_suffixes.add(text)
                    previous_hyphen = text.endswith(('-', '‐', '‑'))
                    spans = line['spans']
                    nonempty = [span for span in spans if span['text'].strip()]
                    if nonempty and nonempty[0].get('flags', 0) & 16:
                        label = ''.join(s['text'] for s in nonempty[:1]).strip().rstrip(' –—-').strip()
                        numbered = bool(re.match(r'^(?:\d+(?:\.\d+)*\.?|[A-Z][.)])\s+', label))
                        style = (round(nonempty[0]['size'], 1), nonempty[0]['font'])
                        if numbered:
                            heading_styles[style] += 1
                            if len(label) < 120:
                                evidence.editorial_labels.add(label)
                        if (all(s.get('flags', 0) & 16 for s in nonempty) and text.isupper()
                                and len(text) < 100 and not re.search(r'[.;:!?]$', text)):
                            layout_candidates.append((text, style))
                    for left_span, right_span in zip(spans, spans[1:], strict=False):
                        first, second = left_span['text'], right_span['text']
                        if not (first.endswith(' ') or second.startswith(' ')):
                            continue
                        left_word = re.search(rf'({_LETTER}{{4,}})\s*$', first)
                        right_word = re.match(rf'\s*({_LETTER}{{2,}})', second)
                        if left_word and right_word:
                            a, b = left_word.group(1), right_word.group(1)
                            evidence.word_spacing[a + b] = (a, b)
                block_pairs.extend(zip(block_lines, block_lines[1:], strict=False))
                if block_lines:
                    evidence.page_passages.setdefault(page_number, []).append('\n'.join(block_lines))
                if (block_lines and _CALLOUT.match(block_lines[0]) and block['bbox'][1] > page.rect.height * .82
                        and not any(pymupdf.Rect(box).contains(pymupdf.Rect(block['bbox']))
                                    for box in page_boxes[page_number - 1])):
                    trailing_labels.append((page_number, block['bbox'], page.rect.height, ' '.join(block_lines)))
            ordered = [item[2] for item in sorted(lines)]
            body_lines[page_number] = lines
            pages.append(ordered)
            evidence.page_texts[page_number] = '\n'.join(ordered)
        for number, bbox, height, label in trailing_labels:
            next_boxes = [b for b in all_boxes if b[0] == number + 1 and b[1][1] <= b[2] * .18
                          and b[1][2] - b[1][0] >= (bbox[2] - bbox[0]) * .95
                          and b[1][0] <= bbox[0] + 3 and b[1][2] >= bbox[2] - 3
                          and not _CALLOUT.match(b[3])]
            if len(next_boxes) == 1 and not any(y > bbox[3] + 2 for y, _, _ in body_lines[number]):
                edge = next_boxes[0][1]
                callouts.append((number, (edge[0], bbox[1], edge[2], height), height, label))
        for number, rect, height, content in callouts:
            end = number
            while rect[3] >= height * .85:
                if any(y > rect[3] + 2 for y, _, _ in body_lines[end]):
                    break
                following = [b for b in all_boxes if b[0] == end + 1 and b[1][1] <= b[2] * .18
                             and abs(b[1][0] - rect[0]) <= 2 and abs(b[1][2] - rect[2]) <= 2
                             and not _CALLOUT.match(b[3])
                             and not any(y < b[1][1] - 2 for y, _, _ in body_lines[b[0]])]
                if len(following) != 1:
                    break
                end, rect, height, continuation = following[0]
                content += ' ' + continuation
            evidence.note_boxes.setdefault(number, []).append(content)
            evidence.note_box_ends[(number, content)] = end
        for lines in pages:
            for first, second in zip(lines, lines[1:], strict=False):
                _record_hyphenation(first, second, vocabulary, evidence)
        # Células e colunas podem intercalar-se na ordenação global por Y.
        # As linhas do mesmo bloco preservam a continuidade interna dessas regiões.
        for first, second in block_pairs:
            _record_hyphenation(first, second, vocabulary, evidence)
            left = re.search(rf'({_LETTER}+)\s*$', first)
            right = re.match(rf'^\s*({_LETTER}+)', second)
            if left and right:
                evidence.line_adjacencies.add((left[1], right[1]))
        for first, second in zip(pages, pages[1:], strict=False):
            if first and second:
                _record_hyphenation(first[-1], second[0], vocabulary, evidence)
        evidence.word_spacing = {word: pair for word, pair in evidence.word_spacing.items() if word not in vocabulary}
        evidence.layout_headings = {text for text, style in layout_candidates if heading_styles[style] >= 2}
        evidence.has_source = True
    return evidence


def _record_hyphenation(first: str, second: str, vocabulary: set[str], evidence: SourceEvidence) -> None:
    first = first.replace('‐', '-').replace('‑', '-')
    left = re.search(rf'({_LETTER}+)-$', first)
    right = re.match(rf'({_LETTER}+)', second)
    if not left or not right:
        return
    a, b = left.group(1), right.group(1)
    joined, compound = a + b, a + '-' + b
    right_form = re.match(rf'{_LETTER}+(?:-{_LETTER}+)*', second).group()
    joined_form = a + right_form
    def folded_word(word):
        return ''.join(c for c in unicodedata.normalize('NFD', word.casefold()) if not unicodedata.combining(c))

    folded = {folded_word(word) for word in vocabulary}
    if folded_word(compound) in folded or b.lower() in _ENCLITICS:
        separator = '-'
    elif folded_word(joined) in folded or folded_word(joined_form) in folded:
        separator = ''
    elif (a.lower() in _COMPOUND_PREFIXES and (not a.isupper() or folded_word(b) in folded)) or (
        b.lower() == 'feira' and a.lower() in {'segunda', 'terça', 'quarta', 'quinta', 'sexta'}
    ):
        separator = '-'
    elif a in vocabulary and b in vocabulary:
        # As duas leituras são possíveis; não escolha um composto sem evidência.
        return
    else:
        separator = ''
    evidence.hyphenations[(a, b)] = separator
    previous_word = re.search(rf'({_LETTER}+)\s+{re.escape(a)}-$', first)
    if previous_word:
        previous = previous_word.group(1)
        evidence.contextual_hyphenations[(previous + a, b)] = previous


def _repair_hyphenation(text: str, evidence: SourceEvidence, stats: Counter) -> str:
    plain, mapping = _project(text)
    deletions: set[int] = set()
    insertions: dict[int, str] = {}
    pattern = re.compile(rf'({_LETTER}+)([ \t]*)-([ \t\n]+)({_LETTER}+)')
    for match in pattern.finditer(plain):
        a, b = match.group(1), match.group(4)
        separator = evidence.hyphenations.get((a, b))
        previous_word = evidence.contextual_hyphenations.get((a, b))
        if separator is None and previous_word:
            prefix = a[len(previous_word):]
            separator = evidence.hyphenations.get((prefix, b))
            if separator is not None:
                insertions[mapping[match.start(1) + len(previous_word)][0]] = ' '
                stats['source_spacing_repaired'] += 1
        if separator is None:
            continue
        # Apenas o hífen e os espaços são removidos; tags de estilo sobrevivem.
        for position in range(match.start(2), match.start(4)):
            if plain[position] == '-' and separator:
                continue
            start, end = mapping[position]
            deletions.update(range(start, end))
        stats['hyphenations_repaired'] += 1
    text = _clean_styles(''.join(insertions.get(i, '') + char for i, char in enumerate(text) if i not in deletions))
    prefixes = {a for (a, _), separator in evidence.hyphenations.items() if separator == ''}
    if not prefixes:
        return text
    # O layout às vezes descarta o hífen e deixa apenas um espaço dentro da palavra.
    # Só una a dupla exata atestada por uma quebra de linha do PDF.
    plain, mapping = _project(text)
    alternatives = '|'.join(re.escape(word) for word in sorted(prefixes, key=len, reverse=True))
    pattern = re.compile(rf'(?<!\w)({alternatives})([ \t]+)({_LETTER}+)(?!\w)')
    deletions = set()
    for match in pattern.finditer(plain):
        if evidence.hyphenations.get((match.group(1), match.group(3))) != '':
            continue
        for position in range(match.start(2), match.end(2)):
            start, end = mapping[position]
            deletions.update(range(start, end))
        stats['spaces_in_fragmented_words_repaired'] += 1
    return _clean_styles(''.join(char for i, char in enumerate(text) if i not in deletions))


def _repair_style_spacing(text: str, evidence: SourceEvidence, stats: Counter) -> str:
    if not evidence.word_spacing:
        return text
    plain, mapping = _project(text)
    insertions = set()
    words = '|'.join(re.escape(word) for word in sorted(evidence.word_spacing, key=len, reverse=True))
    for match in re.finditer(rf'(?<!\w)(?:{words})(?!\w)', plain):
        left, _ = evidence.word_spacing[match.group()]
        insertions.add(mapping[match.start() + len(left)][0])
        stats['source_spacing_repaired'] += 1
    return ''.join((' ' if index in insertions else '') + char for index, char in enumerate(text))


def _repair_style_fragments(text: str, evidence: SourceEvidence, stats: Counter) -> str:
    """Só retira espaços em fronteiras de estilo com palavra inteira na fonte."""
    plain, mapping = _project(text)
    deletions = set()
    for match in re.finditer(rf'({_LETTER}+) ({_LETTER}+)', plain):
        # A expressão usa as mesmas classes Unicode da projeção textual.
        word = match[1] + match[2]
        gap = match.start(2) - 1
        start, end = mapping[gap]
        surrounding = text[mapping[gap - 1][1]:mapping[gap + 1][0]]
        if (word in evidence.fused_words and (match[1], match[2]) not in evidence.separated_forms
                and _STYLE.search(surrounding)):
            deletions.update(range(start, end))
            stats['style_fragments_repaired'] += 1
    return ''.join(char for index, char in enumerate(text) if index not in deletions)


def _repair_contextual_separators(text: str, evidence: SourceEvidence, stats: Counter) -> str:
    """Alinha fronteiras de letras por contexto único na mesma página protegida.

    Não troca letras, números ou pontuação substantiva. Fronteiras de parágrafo,
    código e células ficam protegidas; estilos são mantidos pelo mapa reversível.
    """
    if not evidence.page_texts:
        return text
    def unit(char):
        lower = char.lower()
        return lower if len(lower) == 1 else char

    source_pages = {}
    for page, value in evidence.page_texts.items():
        # A leitura por bloco mantém colunas; a leitura global mantém a prosa.
        # O sentinela impede correspondências que atravessem passagens distintas.
        value = '\x00'.join([value, *evidence.page_passages.get(page, [])])
        # Só hifenações reconhecidas são resolvidas; uma forma ambígua mantém o
        # hífen da fonte. URLs mantêm seu caminho literal, inclusive o hífen final.
        def join(match, original=value):
            a, hyphen, b = match[1], match[2], match[3]
            start = original.rfind('\n', 0, match.start()) + 1
            url = re.search(r'https?://\S*$', original[start:match.start()])
            separator = '-' if url else evidence.hyphenations.get((a, b), hyphen)
            return a + separator + b

        value = re.sub(rf'({_LETTER}+)([-‐‑])\s*\n\s*({_LETTER}+)', join, value)
        positions = [i for i, c in enumerate(value) if c.isalnum() or c == '\x00']
        skeleton = ''.join(unit(value[i]) for i in positions)
        source_pages[page] = (value, positions, skeleton)
    plain, mapping = _project(text)
    positions = [i for i, c in enumerate(plain) if c.isalnum()]
    skeleton = ''.join(unit(plain[i]) for i in positions)
    urls = [m.span() for m in re.finditer(r'https?://[^\s<>)]+', plain)]
    edits = []
    markers = list(_PAGE.finditer(text))
    limits = {}
    page, marker_index = 1, 0
    for index, position in enumerate(positions):
        raw_position = mapping[position][0]
        while marker_index < len(markers) and markers[marker_index].start() <= raw_position:
            page = int(markers[marker_index][1])
            marker_index += 1
        limits.setdefault(page, [index, index + 1])[1] = index + 1
    page, marker_index = 1, 0
    for k in range(1, len(positions)):
        left, right = positions[k - 1], positions[k]
        raw_position = mapping[left][0]
        while marker_index < len(markers) and markers[marker_index].start() <= raw_position:
            page = int(markers[marker_index][1])
            marker_index += 1
        gap = plain[left + 1:right]
        if (not plain[left].isalpha() or not plain[right].isalpha()
                or not re.fullmatch(r'[ \t\n\-‐‑]*', gap) or '\n\n' in gap):
            continue
        source = source_pages.get(page)
        if source is None or k >= limits[page][1]:
            continue
        value, source_positions, source_skeleton = source
        expected = None
        for radius in (18, 36, 72, 144):
            first, last = limits[page]
            a, b = max(first, k - radius), min(last, k + radius)
            context = skeleton[a:b]
            candidate = source_skeleton.find(context) if len(context) >= 20 else -1
            separators = set()
            while candidate >= 0:
                boundary = candidate + k - a
                if 0 < boundary < len(source_positions):
                    separators.add(value[source_positions[boundary - 1] + 1:source_positions[boundary]])
                candidate = source_skeleton.find(context, candidate + 1)
            if len(separators) == 1:
                expected = separators.pop()
                break
        if expected is None:
            continue
        if not re.fullmatch(r'[ \t\n\-‐‑]*', expected):
            continue
        # Preserve espaços/hífens que já existem na mesma linha da fonte.
        expected = re.sub(r'\s+', ' ', expected)
        if gap == expected:
            continue
        if any(a <= left < right < b for a, b in urls) and not (gap == '' and expected == '-'):
            # Reconstitua somente o hífen literal comprovado de um caminho;
            # quebras físicas de URL não autorizam inserir espaços no endereço.
            continue
        start, end = mapping[left][1], mapping[right][0]
        between = text[start:end]
        # Não remova comentários de proveniência nem quebras explícitas.
        if '<!--' in between or re.search(r'<br\b', between, re.I):
            continue
        raw_plain, local_map = _project(between)
        remove = {j for i, c in enumerate(raw_plain) if c.isspace() or c in '-‐‑'
                  for j in range(*local_map[i])}
        styles = ''.join(c for i, c in enumerate(between) if i not in remove)
        # O separador fica antes de tags de abertura, após fechamentos adjacentes.
        closing = re.match(r'^(?:</[^>]+>)+', styles)
        cut = closing.end() if closing else 0
        edits.append((start, end, styles[:cut] + expected + styles[cut:]))
    for start, end, value in reversed(edits):
        text = text[:start] + value + text[end:]
    stats['contextual_separators_repaired'] += len(edits)
    return _clean_styles(text)


def _split_embedded_headings(text: str, evidence: SourceEvidence, stats: Counter) -> str:
    lines = []
    for line in text.splitlines():
        match = _HEADING.match(line)
        plain = _visible(match[2]) if match else ''
        if match and _NUMERIC.match(plain):
            for suffix in sorted(evidence.heading_suffixes, key=len, reverse=True):
                if plain.endswith(' ' + suffix):
                    first, second = _split_visible(match[2], len(plain) - len(suffix))
                    lines.extend([match[1] + ' ' + first, '', match[1] + ' ' + second])
                    stats['source_headings_separated'] += 1
                    break
            else:
                lines.append(line)
        else:
            lines.append(line)
    return '\n'.join(lines)


def _normalize_emphasis(text: str, stats: Counter) -> str:
    """Corrige delimitadores com espaços internos sem confundir pares vizinhos."""
    lines = []
    for line in text.splitlines(keepends=True):
        for delimiter in ('**', '__'):
            positions = list(re.finditer(r'(?<!\\)' + re.escape(delimiter), line))
            if len(positions) % 2:
                continue
            for opening, closing in reversed(list(zip(positions[::2], positions[1::2], strict=True))):
                body = line[opening.end():closing.start()]
                if not body.strip() or body == body.strip():
                    continue
                leading = body[:len(body) - len(body.lstrip())]
                trailing = body[len(body.rstrip()):]
                replacement = leading + delimiter + body.strip() + delimiter + trailing
                line = line[:opening.start()] + replacement + line[closing.end():]
                stats['emphasis_delimiters_repaired'] += 1
        lines.append(line)
    return ''.join(lines)


def _is_sparse_edge_image(path: Path) -> bool:
    import pymupdf

    try:
        pix = pymupdf.Pixmap(str(path))
        if pix.width > 240 or pix.height > 300 or pix.n < 3:
            return False
        width, height, channels = pix.width, pix.height, pix.n
        samples = pix.samples
        ink = 0
        for y in range(height):
            for x in range(width):
                offset = (y * width + x) * channels
                if min(samples[offset:offset + 3]) >= 200:
                    continue
                ink += 1
                if width * .25 < x < width * .75:
                    return False
        return 0 < ink / (width * height) < .10
    except (OSError, RuntimeError, ValueError):
        return False


def filter_margin_images(chunk: dict, *, width: float, height: float, assets_dir: Path) -> str:
    """Remove só recortes vazios de margem comprovados pelo mapa de layout.

    Posições referem-se ao Markdown original e são aplicadas de trás para frente.
    A seleção de arquivos fica restrita ao diretório temporário deste documento.
    """
    text = str(chunk.get('text', ''))
    root = assets_dir.resolve()
    removed = 0
    for box in reversed(chunk.get('page_boxes', [])):
        if box.get('class') != 'picture':
            continue
        x0, y0, x1, y1 = box['bbox']
        if not (x0 >= width * .88 and x1 - x0 <= width * .13 and y1 - y0 <= height * .15):
            continue
        start, end = box['pos']
        fragment = text[start:end]
        for image in list(_IMAGE.finditer(fragment)):
            reference = image.group(1)
            if not reference.startswith('images/') or '..' in Path(reference).parts:
                continue
            path = root / Path(reference).name
            if path.resolve().parent != root or not path.is_file() or not _is_sparse_edge_image(path):
                continue
            fragment = fragment.replace(image.group(), '')
            # Um ativo pode ser reutilizado: só remova o arquivo sem outra referência.
            elsewhere = text[:start] + text[end:]
            if reference not in elsewhere:
                path.unlink()
            removed += 1
        text = text[:start] + fragment + text[end:]
    if removed:
        text += f'\n<!-- NEXOJURIS_EDITORIAL margin_images_removed={removed} -->\n'
    return text


def _join_pages(text: str, evidence: SourceEvidence, stats: Counter) -> str:
    matches = list(_PAGE.finditer(text))
    if not matches:
        return text
    bodies = []
    for index, match in enumerate(matches):
        body = text[match.end():matches[index + 1].start() if index + 1 < len(matches) else len(text)]
        if index + 1 < len(matches):
            body = re.sub(r'\n\s*---\s*$', '', body)
        bodies.append(body.strip())
    def in_table(match):
        return text[text.rfind('\n', 0, match.start()) + 1:match.start()].lstrip().startswith('|')

    output = text[:matches[0].start()] + matches[0].group() + (' ' if in_table(matches[0]) else '\n\n') + bodies[0]
    previous_number = int(matches[0].group(1))
    previous_status = matches[0].group(2)
    for index, match in enumerate(matches[1:], 1):
        next_body = bodies[index]
        number, status = int(match.group(1)), match.group(2)
        if in_table(match):
            # A reconstrução de células já preservou a proveniência inline.
            # Separar este marcador em parágrafos quebraria a linha da tabela.
            output += ' ' + match.group() + ' ' + next_body
            previous_number, previous_status = number, status
            continue
        if re.search(rf'<!-- NEXOJURIS_CONTINUATION pages={previous_number},{number} -->\s*$', output):
            output += ' ' + match.group() + ' ' + next_body.lstrip()
            previous_number, previous_status = number, status
            continue
        last_paragraph = output.rsplit('\n\n', 1)[-1].strip()
        next_paragraph = next_body.lstrip().split('\n\n', 1)[0]
        left = _visible(last_paragraph)
        right = _visible(next_paragraph)
        structured = re.match(r'^(?:#{1,6}\s|\||>|[-*+]\s|\d+[.)]\s|!\[)', next_paragraph)
        can_join = (
            number == previous_number + 1 and status in {'native', 'fallback'}
            and previous_status in {'native', 'fallback'} and left and right
            and not structured and not last_paragraph.startswith(('#', '|', '>'))
            and not re.search(r'[.!?:;][”"\')\]]?$', left)
            and (right[0].islower() or left.endswith('-'))
        )
        joined_word = False
        if can_join:
            provenance = f'<!-- NEXOJURIS_CONTINUATION pages={previous_number},{number} -->'
            if left.endswith('-') and re.match(rf'^{_LETTER}+', right):
                first_word = re.match(rf'^{_LETTER}+', right).group()
                a = re.search(rf'({_LETTER}+)-$', left)
                separator = evidence.hyphenations.get((a.group(1), first_word)) if a else None
                if separator is not None:
                    raw_right, _ = _project(next_paragraph)
                    leading = len(raw_right) - len(raw_right.lstrip())
                    prefix, remainder = _split_visible(next_paragraph, leading + len(first_word))
                    prefix = _strip_visible_edges(prefix)
                    if not separator:
                        # O último hífen visível pode preceder o fechamento de um estilo.
                        raw_left, mapping = _project(last_paragraph)
                        pos = output.rfind(last_paragraph) + mapping[len(raw_left.rstrip()) - 1][0]
                        output = output[:pos] + output[pos + 1:]
                    output += prefix + ' ' + provenance + ' ' + match.group() + ' '
                    output += remainder.lstrip() + next_body[len(next_paragraph):]
                    joined_word = True
                    stats['page_continuations_joined'] += 1
                    stats['hyphenations_repaired'] += 1
            if not joined_word:
                output += ' ' + provenance + ' ' + match.group() + ' ' + next_body.lstrip()
                stats['page_continuations_joined'] += 1
        else:
            output += '\n\n' + match.group() + '\n\n' + next_body
        previous_number, previous_status = number, status
    return output


def _split_decision_labels(line: str) -> list[str]:
    """Separa rótulos tipográficos fundidos a uma tese, sem usar citações em prosa."""
    if line.startswith(('|', '>', '![')):
        return [line]
    plain, _ = _project(line)
    pattern = re.compile(
        r'\b(?:SÚMULA(?: VINCULANTE)?\s+N[º°o.]?\s*\d+\b|'
        r'(?:SÚMULAS|JULGADOS)\s+D[OA]\s+(?:STF|STJ|TST)\b)'
    )
    matches = list(pattern.finditer(plain))
    for match in reversed(matches):
        before, rest = _split_visible(line, match.start())
        label, after = _split_visible(rest, match.end() - match.start())
        # Rótulos em maiúsculas, destacados e isolados ou no final de uma frase.
        prior = _visible(before)
        if not ('**' in label or '<b>' in label or '<strong>' in label):
            continue
        if prior and not re.search(r'[.!?]$', prior):
            continue
        if _visible(after):
            continue
        return ([before.strip()] if prior else []) + [label.strip()]
    return [line]


def _is_prose_heading(text: str) -> bool:
    plain = _visible(text)
    # Um rótulo em negrito seguido de uma explicação é parágrafo, não título.
    if re.match(r'^\*\*[^*]+:\*\*\s+\S', text):
        return True
    if re.match(r'^\d+\.\s+', plain) and not plain.isupper():
        # Preserva títulos nominais e rótulos de questões; rejeita frases verbais.
        if len(plain) > 150 or re.search(r'\b(?:é|são|deve|devem|será|serão|não|há|pode|podem|considera-se)\b', plain, re.I):
            return True
    return False


def _symmetric_enumerations(text: str, stats: Counter) -> str:
    """Uma enumeração local usa a mesma categoria para todos os seus itens.

    Parágrafos entre marcadores são permitidos; capítulos e reinícios fecham
    o conjunto. Não usa vocabulário nem números de páginas.
    """
    lines = text.splitlines()
    groups: list[list[tuple[int, str, str, str]]] = []
    group: list[tuple[int, str, str, str]] = []
    for index, line in enumerate(lines):
        heading = _HEADING.match(line)
        content = heading[2] if heading else re.sub(r'^[-*+]\s+', '', line)
        plain = _visible(content)
        marker = re.match(r'^([A-Za-z])([.)])\s+', plain)
        if marker:
            item = (index, marker[1], marker[2], 'heading' if heading else 'list' if content != line else 'prose')
            if group and (ord(marker[1]) != ord(group[-1][1]) + 1 or marker[2] != group[-1][2]):
                groups.append(group)
                group = []
            group.append(item)
        elif heading:
            if group:
                groups.append(group)
                group = []
    if group:
        groups.append(group)
    for group in groups:
        kinds = {item[3] for item in group}
        if len(group) < 2 or 'heading' not in kinds or kinds == {'heading'}:
            continue
        for index, _, _, kind in group:
            if kind == 'heading':
                lines[index] = ('- ' if 'list' in kinds and 'prose' not in kinds else '') + _HEADING.match(lines[index])[2]
                stats['enumeration_peers_aligned'] += 1
    return '\n'.join(lines)


def _layout_labels(text: str, evidence: SourceEvidence, stats: Counter) -> str:
    """Separa rótulo destacado e explicação mediante confirmação na fonte."""
    output = []
    for line in text.splitlines():
        heading = _HEADING.match(line)
        content = heading[2] if heading else line
        plain = _visible(content)
        if not content.startswith(('**', '__')):
            output.append(line)
            continue
        for label in sorted(evidence.editorial_labels, key=len, reverse=True):
            if plain.startswith(label) and re.match(r'^\s*[–—:-]\s+', plain[len(label):]):
                first, rest = _split_visible(content, len(label))
                output.extend([(heading[1] if heading else '###') + ' ' + first, '', rest])
                stats['source_labels_separated'] += 1
                break
        else:
            output.append(line)
    return '\n'.join(output)


def _structure(text: str, stats: Counter, evidence: SourceEvidence) -> str:
    # Importação local evita dependência circular na integração de finalize_markdown.
    from markdown_utils import normalize_course_heading_levels

    lines: list[str] = []
    for line in text.splitlines():
        match = _HEADING.match(line)
        content = match.group(2) if match else line
        if match and _is_prose_heading(content):
            line = content
            stats['false_headings_demoted'] += 1
        if not match and _visible(line) in evidence.layout_headings:
            line = '### ' + line
        lines.extend(_split_decision_labels(line))
    text = normalize_course_heading_levels('\n'.join(lines), source_headings=evidence.layout_headings)
    output: list[str] = []
    collection_level = 2
    decision_level = 4
    for line in text.splitlines():
        match = _HEADING.match(line)
        content = match.group(2) if match else line
        plain = _visible(content)
        if re.match(r'^ATENÇÃO\s*!', plain, re.I) and not line.startswith(('>', '|')):
            output.extend(['', '> [!NOTE]', '> ' + content.strip(), ''])
            stats['local_notes'] += 1
        elif _COLLECTION.fullmatch(plain) and not line.startswith(('>', '|')):
            output.extend(['', '### ' + plain, ''])
            collection_level, decision_level = 3, 4
            stats['jurisprudence_collections'] += 1
        elif _SUBCOLLECTION.fullmatch(plain) and not line.startswith(('>', '|')):
            output.extend(['', '#' * (collection_level + 1) + ' ' + plain, ''])
            decision_level = collection_level + 2
        elif _DECISION.fullmatch(plain) and not line.startswith(('>', '|')):
            output.extend(['', '#' * min(decision_level, 6) + ' ' + plain, ''])
            stats['decision_labels'] += 1
        else:
            output.append(line)
    return '\n'.join(output)


def _group_source_notes(text: str, evidence: SourceEvidence, stats: Counter) -> str:
    """Agrupa caixas completas e continuações cuja geometria foi comprovada."""
    def key(value: str) -> str:
        value = re.sub(r'\[!NOTE\]', '', value)
        return ''.join(c for c in _visible(value).casefold() if c.isalnum())

    def unquote(value: str) -> str:
        value = re.sub(r'(?m)^\s*>[ \t]?', '', value.strip())
        value = re.sub(r'^\[!NOTE\]\s*', '', value)
        return re.sub(r'(?m)^#{1,6}\s+', '', value).strip()

    parts = re.split(r'(\n[ \t]*\n)', text)
    page = 1
    part_pages = {}
    for start in range(0, len(parts), 2):
        markers = list(_PAGE.finditer(parts[start]))
        begins_at_marker = markers and not key(parts[start][:markers[0].start()])
        part_pages[start] = int(markers[0][1]) if begins_at_marker else page
        if markers:
            page = int(markers[-1][1])
    for start in range(0, len(parts), 2):
        page = part_pages[start]
        label = unquote(parts[start])
        if not _CALLOUT.match(_visible(label)):
            continue
        for source_text in evidence.note_boxes.get(page, []):
            expected, actual = key(source_text), key(label)
            prefix = actual[:80]
            end = start
            last_page = evidence.note_box_ends.get((page, source_text), page)
            contains_table = False
            while actual != expected and len(actual) < len(expected) and end + 2 < len(parts):
                following = parts[end + 2]
                pages = list(_PAGE.finditer(following))
                if (any(int(m[1]) > last_page for m in pages)
                        or following.lstrip().startswith('\x00NXJCODE')):
                    break
                contains_table |= following.lstrip().startswith('|')
                if not expected.startswith(actual) and not contains_table:
                    break
                actual += key(following)
                end += 2
            # Células são lidas por coluna; a extração vetorial pode intercalar
            # linhas de colunas vizinhas. Exija todo o conteúdo e o mesmo início.
            table_match = (contains_table and expected.startswith(prefix)
                           and Counter(actual) == Counter(expected))
            if actual != expected and not table_match:
                continue
            paragraphs = [label, *(unquote(p) for p in parts[start + 2:end + 1:2])]
            replacement = '> [!NOTE]\n' + '\n>\n'.join(
                '\n'.join('> ' + line if line.strip() else '>' for line in paragraph.strip().splitlines())
                for paragraph in paragraphs)
            if parts[start].strip() == replacement and end == start:
                continue
            parts[start] = replacement
            for index in range(start + 1, end + 1):
                parts[index] = ''
            stats['source_note_boxes_grouped'] += 1
            break
    return ''.join(parts)


def _protect_code(text: str) -> tuple[str, list[str]]:
    protected: list[str] = []

    def replace(match: re.Match) -> str:
        protected.append(match.group())
        return f'\x00NXJCODE{len(protected) - 1}\x00'

    text = re.sub(r'(?ms)^(`{3,}|~{3,})[^\n]*\n.*?^\1[^\n]*$', replace, text)
    text = re.sub(r'(`+)[^\n]*?\1', replace, text)
    return text, protected


def _join_sentence_tails(text: str, evidence: SourceEvidence, stats: Counter) -> str:
    parts = re.split(r'(\n[ \t]*\n)', text)
    for index in range(2, len(parts), 2):
        tail = parts[index]
        previous = parts[index - 2]
        plain = _visible(tail)
        left = re.search(rf'({_LETTER}+)\s*$', _visible(previous))
        right = re.fullmatch(rf'({_LETTER}+)[.!?]', plain)
        if (left and right and plain[0].islower() and tail.startswith(('**', '__', '<'))
                and not previous.lstrip().startswith(('#', '|', '>', '!', '<!--'))
                and (left[1], right[1]) in evidence.line_adjacencies):
            parts[index - 2] = previous.rstrip() + ' ' + tail.strip()
            parts[index - 1] = parts[index] = ''
            stats['sentence_tails_joined'] += 1
    return ''.join(parts)


def prepare_source_formatting(markdown: str, source: Path) -> EditorialResult:
    """Formatação comprovada pela fonte, sem impor hierarquia de curso."""
    text, protected = _protect_code(markdown)
    evidence = read_source_evidence(source)
    stats = Counter()
    text = _repair_contextual_separators(text, evidence, stats)
    text = _group_source_notes(text, evidence, stats)
    for index, code in enumerate(protected):
        text = text.replace(f'\x00NXJCODE{index}\x00', code)
    return EditorialResult(text, dict(stats))


def prepare_course_markdown(
    markdown: str,
    *,
    source: Path | None = None,
    evidence: SourceEvidence | None = None,
    assets_dir: Path | None = None,
) -> EditorialResult:
    stats: Counter = Counter({'decoration_images_removed': 0})
    text, protected = _protect_code(markdown)
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    def consume_image_metric(match: re.Match) -> str:
        stats['decoration_images_removed'] += int(match.group(1))
        return ''

    text = re.sub(r'<!-- NEXOJURIS_EDITORIAL margin_images_removed=(\d+) -->', consume_image_metric, text)
    evidence = evidence if evidence is not None else read_source_evidence(source)
    text = _normalize_emphasis(text, stats)
    text = _join_pages(text, evidence, stats)
    text = _repair_hyphenation(text, evidence, stats)
    text = _repair_style_spacing(text, evidence, stats)
    text = _repair_style_fragments(text, evidence, stats)
    text = _repair_contextual_separators(text, evidence, stats)
    # Espaço espúrio em sufixos de dispositivos: § 4º- B -> § 4º-B.
    text = re.sub(r'(§\s*\d+[º°o]?|\b[Aa]rt\.\s*\d+)-[ \t]+([A-Z])\b', r'\1-\2', text)
    text = re.sub(r'(?<=[\w*>_)])[ \t]+([,.;:!?])', r'\1', text)
    text = _normalize_emphasis(text, stats)
    text = _split_embedded_headings(text, evidence, stats)
    text = _layout_labels(text, evidence, stats)
    text = _symmetric_enumerations(text, stats)
    text = _structure(text, stats, evidence)
    from document_structure import normalize_exam_collections

    text = normalize_exam_collections(text, stats)
    text = _join_pages(text, evidence, stats)
    # A estruturação pode rebaixar um fragmento isolado que o layout chamou de
    # título. Repare a quebra agora exposta, sempre usando a evidência do PDF.
    text = _repair_hyphenation(text, evidence, stats)
    if evidence.has_source and not re.search(r'^#\s', text, re.M) and re.search(r'^##\s+(?:\*\*)?\d+\.\s', text, re.M):
        # Sem um título da obra no PDF, as seções numéricas são as raízes reais.
        # Remova o nível vazio reservado ao ramo do Direito, sem inventar uma capa.
        text = re.sub(r'(?m)^(#{2,6})(?=\s)', lambda m: m.group(1)[1:], text)
    text = _join_sentence_tails(text, evidence, stats)
    text = _group_source_notes(text, evidence, stats)
    text = re.sub(r'\n[ \t]*\n(?:[ \t]*\n)+', '\n\n', text).strip() + '\n'
    text = _clean_styles(text)
    plain, mapping = _project(text)
    fragments = [match for match in re.finditer(rf'{_LETTER}+-[ \t\n]+{_LETTER}+', plain)
                 if not re.fullmatch(r'[IVXLCDM]+', match[0].split('-', 1)[0])]
    remaining = len(fragments)
    issues = []
    for fragment in fragments[:20]:
        position = mapping[fragment.start()][0]
        pages = list(_PAGE.finditer(text[:position]))
        page = int(pages[-1][1]) if pages else None
        line = text[:position].count('\n') + 1
        issues.append(f'Possível fragmentação lexical na página {page}, linha {line}: '
                      f'{fragment[0]!r}; conferir a fonte, sem correção presumida.')
    if remaining > 20:
        issues.append(f'Mais {remaining - 20} possíveis fragmentações lexicais; conferir a fonte.')
    stats['unresolved_hyphenations'] = remaining
    stats['section_count'] = len(build_section_index(text))
    for index, code in enumerate(protected):
        text = text.replace(f'\x00NXJCODE{index}\x00', code)
    logger.info('Preparação editorial do perfil curso: %s', dict(stats))
    for issue in issues:
        logger.warning('Conferência editorial: %s', issue)
    return EditorialResult(text, dict(stats), tuple(issues))


def build_section_index(markdown: str) -> list[dict]:
    """Árvore de seções e intervalos de páginas, sem duplicar conteúdo jurídico.

    Cada pai é a seção anterior mais próxima de nível inferior. Os intervalos
    incluem os descendentes e servem de contexto para chunking por assunto.
    """
    protected, code_blocks = _protect_code(markdown)
    for index, code in enumerate(code_blocks):
        placeholder = f'\x00NXJCODE{index}\x00'
        replacement = placeholder + '\n' * code.count('\n') if '\n' in code else code
        protected = protected.replace(placeholder, replacement)
    nodes: list[dict] = []
    stack: list[dict] = []
    page: int | None = None
    occurrences: Counter = Counter()
    lines = protected.splitlines()
    for line_number, line in enumerate(lines, 1):
        page_markers = list(_PAGE.finditer(line))
        if page_markers:
            page = int(page_markers[-1].group(1))
        heading = _HEADING.match(line)
        if not heading:
            if _visible(line) and not line.lstrip().startswith('<!--'):
                for ancestor in stack:
                    ancestor['page_end'] = page
            continue
        level = len(heading.group(1))
        while stack and stack[-1]['level'] >= level:
            stack.pop()['line_end'] = line_number - 1
        if stack and stack[-1]['direct_line_end'] is None:
            stack[-1]['direct_line_end'] = line_number - 1
            stack[-1]['direct_page_end'] = stack[-1]['page_end']
        for ancestor in stack:
            ancestor['page_end'] = page
        title = _visible(heading.group(2))
        key = '\x1f'.join([ancestor['title'] for ancestor in stack] + [title])
        occurrences[key] += 1
        digest = hashlib.sha256(f'{key}\x1f{occurrences[key]}'.encode()).hexdigest()[:20]
        node = {
            'id': f'section-{digest}',
            'parent_id': stack[-1]['id'] if stack else None,
            'level': level,
            'title': title,
            'line': line_number,
            'line_end': None,
            'direct_line_end': None,
            'direct_page_end': None,
            'page_start': page,
            'page_end': page,
            'context': [ancestor['title'] for ancestor in stack],
        }
        nodes.append(node)
        stack.append(node)
    for node in stack:
        node['line_end'] = len(lines)
    for node in nodes:
        if node['direct_line_end'] is None:
            node['direct_line_end'] = node['line_end']
            node['direct_page_end'] = node['page_end']
    return nodes
