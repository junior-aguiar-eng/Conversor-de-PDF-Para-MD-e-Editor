"""Preparação editorial conservadora de apostilas extraídas para Markdown."""

from __future__ import annotations

import logging
import re
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


@dataclass
class SourceEvidence:
    hyphenations: dict[tuple[str, str], str] = field(default_factory=dict)
    contextual_hyphenations: dict[tuple[str, str], str] = field(default_factory=dict)
    word_spacing: dict[str, tuple[str, str]] = field(default_factory=dict)
    has_source: bool = False


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


def read_source_evidence(source: Path | None) -> SourceEvidence:
    """Usa quebras reais do PDF; não consulta dicionários nem altera vocabulário."""
    evidence = SourceEvidence()
    if source is None or not source.is_file():
        return evidence
    import pymupdf

    with pymupdf.open(source) as document:
        pages: list[list[str]] = []
        block_pairs: list[tuple[str, str]] = []
        vocabulary: set[str] = set()
        for page in document:
            lines = []
            blocks = page.get_text('dict', flags=pymupdf.TEXTFLAGS_DICT & ~pymupdf.TEXT_PRESERVE_IMAGES)['blocks']
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
                    complete = re.sub(rf'{_LETTER}+-$', '', text)
                    if previous_hyphen:
                        complete = re.sub(rf'^{_LETTER}+\b', '', complete)
                    vocabulary.update(re.findall(rf'{_LETTER}+(?:-{_LETTER}+)*', complete))
                    previous_hyphen = text.endswith('-')
                    spans = line['spans']
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
            pages.append([item[2] for item in sorted(lines)])
        for lines in pages:
            for first, second in zip(lines, lines[1:], strict=False):
                _record_hyphenation(first, second, vocabulary, evidence)
        # Células e colunas podem intercalar-se na ordenação global por Y.
        # As linhas do mesmo bloco preservam a continuidade interna dessas regiões.
        for first, second in block_pairs:
            _record_hyphenation(first, second, vocabulary, evidence)
        for first, second in zip(pages, pages[1:], strict=False):
            if first and second:
                _record_hyphenation(first[-1], second[0], vocabulary, evidence)
        evidence.word_spacing = {word: pair for word, pair in evidence.word_spacing.items() if word not in vocabulary}
        evidence.has_source = True
    return evidence


def _record_hyphenation(first: str, second: str, vocabulary: set[str], evidence: SourceEvidence) -> None:
    left = re.search(rf'({_LETTER}+)-$', first)
    right = re.match(rf'({_LETTER}+)', second)
    if not left or not right:
        return
    a, b = left.group(1), right.group(1)
    joined, compound = a + b, a + '-' + b
    right_form = re.match(rf'{_LETTER}+(?:-{_LETTER}+)*', second).group()
    joined_form = a + right_form
    if compound in vocabulary or b.lower() in _ENCLITICS:
        separator = '-'
    elif joined in vocabulary or joined_form in vocabulary:
        separator = ''
    elif a.lower() in _COMPOUND_PREFIXES or (
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
    output = text[:matches[0].start()] + matches[0].group() + '\n\n' + bodies[0]
    previous_number = int(matches[0].group(1))
    previous_status = matches[0].group(2)
    for index, match in enumerate(matches[1:], 1):
        next_body = bodies[index]
        number, status = int(match.group(1)), match.group(2)
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


def _structure(text: str, stats: Counter) -> str:
    # Importação local evita dependência circular na integração de finalize_markdown.
    from markdown_utils import normalize_course_heading_levels

    lines: list[str] = []
    for line in text.splitlines():
        match = _HEADING.match(line)
        content = match.group(2) if match else line
        if match and _is_prose_heading(content):
            line = content
            stats['false_headings_demoted'] += 1
        lines.extend(_split_decision_labels(line))
    text = normalize_course_heading_levels('\n'.join(lines))
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


def _protect_code(text: str) -> tuple[str, list[str]]:
    protected: list[str] = []

    def replace(match: re.Match) -> str:
        protected.append(match.group())
        return f'\x00NXJCODE{len(protected) - 1}\x00'

    text = re.sub(r'(?ms)^(`{3,}|~{3,})[^\n]*\n.*?^\1[^\n]*$', replace, text)
    text = re.sub(r'(`+)[^\n]*?\1', replace, text)
    return text, protected


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
    # Espaço espúrio em sufixos de dispositivos: § 4º- B -> § 4º-B.
    text = re.sub(r'(§\s*\d+[º°o]?|\b[Aa]rt\.\s*\d+)-[ \t]+([A-Z])\b', r'\1-\2', text)
    text = re.sub(r'(?<=[\w*>_)])[ \t]+([,.;:!?])', r'\1', text)
    text = _normalize_emphasis(text, stats)
    text = _structure(text, stats)
    # A estruturação pode rebaixar um fragmento isolado que o layout chamou de
    # título. Repare a quebra agora exposta, sempre usando a evidência do PDF.
    text = _repair_hyphenation(text, evidence, stats)
    if evidence.has_source and not re.search(r'^#\s', text, re.M) and re.search(r'^##\s+(?:\*\*)?\d+\.\s', text, re.M):
        # Sem um título da obra no PDF, as seções numéricas são as raízes reais.
        # Remova o nível vazio reservado ao ramo do Direito, sem inventar uma capa.
        text = re.sub(r'(?m)^(#{2,6})(?=\s)', lambda m: m.group(1)[1:], text)
    text = re.sub(r'\n[ \t]*\n(?:[ \t]*\n)+', '\n\n', text).strip() + '\n'
    text = _clean_styles(text)
    remaining = len(re.findall(rf'{_LETTER}+-[ \t\n]+{_LETTER}+', _visible(text)))
    issues = (f'{remaining} possível(is) fragmentação(ões) lexical(is) ainda requer(em) conferência',) if remaining else ()
    stats['unresolved_hyphenations'] = remaining
    stats['section_count'] = len(build_section_index(text))
    for index, code in enumerate(protected):
        text = text.replace(f'\x00NXJCODE{index}\x00', code)
    logger.info('Preparação editorial do perfil curso: %s', dict(stats))
    for issue in issues:
        logger.warning('Conferência editorial: %s', issue)
    return EditorialResult(text, dict(stats), issues)


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
    for line_number, line in enumerate(protected.splitlines(), 1):
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
            stack.pop()
        for ancestor in stack:
            ancestor['page_end'] = page
        node = {
            'id': f'section-{len(nodes) + 1:04d}',
            'parent_id': stack[-1]['id'] if stack else None,
            'level': level,
            'title': _visible(heading.group(2)),
            'line': line_number,
            'page_start': page,
            'page_end': page,
            'context': [ancestor['title'] for ancestor in stack],
        }
        nodes.append(node)
        stack.append(node)
    return nodes
