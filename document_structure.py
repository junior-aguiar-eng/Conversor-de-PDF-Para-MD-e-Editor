"""Delimitação documental conservadora; não produz conteúdo ou gabaritos."""
from __future__ import annotations

import re
import unicodedata
from collections import Counter


def canonical(text: str) -> str:
    from course_editorial import _visible

    text = unicodedata.normalize('NFKD', _visible(text)).casefold()
    return re.sub(r'\s+', ' ', ''.join(c for c in text if not unicodedata.combining(c))).strip()


def resolve_profile(markdown: str, requested: str) -> str:
    if requested != 'auto':
        return requested
    # Exige títulos extraídos e dois prefixos aninhados distintos, não números
    # encontrados em citações, sumários em tabela ou alternativas.
    prefixes = set(re.findall(r'(?m)^#{1,6}\s+(?:\*\*)?(\d+\.\d+(?:\.\d+)*)\.?\s', markdown))
    return 'curso' if len(prefixes) >= 2 else 'jurisprudencia'


def collection_kind(title: str) -> str | None:
    text = re.sub(r'^\d+(?:\.\d+)*\.?\s*', '', canonical(title))
    if re.fullmatch(r'(?:questoes(?: de (?:concurso|prova)s?)?|exercicios)(?:\s*\([^)]*\))?', text):
        return 'questions'
    if re.fullmatch(r'(?:gabarito(?:s)?(?: comentado(?:s)?)?|respostas comentadas)', text):
        return 'answers'
    return None


def normalize_exam_collections(text: str, stats: Counter) -> str:
    from course_editorial import _HEADING, _project, _split_visible, _visible

    output = []
    kind = None
    level = 2
    for line in text.splitlines():
        heading = _HEADING.match(line)
        content = heading[2] if heading else line
        plain = _visible(content)
        found = collection_kind(plain) if heading else None
        if found:
            kind, level = found, len(heading[1])
            output.append(line)
            continue
        question = re.match(r'^(\d+)\.\s*\([^\n)]+\)', plain) if kind == 'questions' else None
        answer = re.fullmatch(r'(\d+)\.\s*([A-E])\s*', plain) if kind == 'answers' else None
        if (question or answer) and (heading or content.lstrip().startswith('**')):
            if question:
                label, rest = _split_visible(content, question.end())
                output.extend(['', '#' * min(level + 1, 6) + ' ' + label.strip(), '', rest.strip()])
            else:
                output.extend(['', '#' * min(level + 1, 6) + ' ' + content.strip(), ''])
            stats['exam_boundaries'] += 1
        elif heading and kind and len(heading[1]) <= level:
            # Comentários, alternativas e dispositivos não criam capítulos.
            if re.match(r'^(?:\([A-E]\)|§|[Aa]rt\.)', plain):
                output.append(content)
                stats['false_headings_demoted'] += 1
            else:
                kind = None
                output.append(line)
        else:
            output.append(line)
    text = '\n'.join(output)
    # Delimita alternativas dentro de cada questão somente com sequência A, B,
    # ... estrita; não quebra enumerações legais nem comentários do gabarito.
    from course_editorial import build_section_index

    lines = text.splitlines()
    sections = build_section_index(text)
    for section in reversed(sections):
        if not any(collection_kind(title) == 'questions' for title in section['context']):
            continue
        if not re.match(r'^\d+\.\s*\(', section['title']):
            continue
        start, end = section['line'], section['line_end']
        body = '\n'.join(lines[start:end])
        plain = _project(body)[0]
        markers = list(re.finditer(r'\(([A-E])\)', plain))
        labels = [match[1] for match in markers]
        if len(labels) < 2 or labels != list('ABCDE'[:len(labels)]):
            continue
        for match in reversed(markers):
            left, right = _split_visible(body, match.start())
            body = left.rstrip() + '\n\n' + right.lstrip()
        lines[start:end] = body.splitlines()
    return '\n'.join(lines)


def _location(lines: list[str], start: int, end: int) -> dict:
    from course_editorial import _PAGE, _visible

    page = None
    for line in lines[:start]:
        markers = list(_PAGE.finditer(line))
        if markers:
            page = int(markers[-1][1])
    first = None
    last = page
    for line in lines[start:end]:
        markers = list(_PAGE.finditer(line))
        if markers:
            page = int(markers[-1][1])
        if _visible(line) and line.strip() != '---':
            if first is None:
                first = page
            last = page
    return {'line_start': start + 1, 'line_end': end, 'page_start': first, 'page_end': last}


def exam_index(markdown: str, sections: list[dict]) -> tuple[list[dict], list[dict]]:
    from course_editorial import _project, _visible

    lines = markdown.splitlines()
    questions, answers = [], []
    for section in sections:
        kinds = [collection_kind(title) for title in section['context']]
        match = re.match(r'^(\d+)\.\s*\(', section['title']) if 'questions' in kinds else None
        response = re.fullmatch(r'(\d+)\.\s*([A-E])', section['title']) if 'answers' in kinds else None
        if not (match or response):
            continue
        start, end = section['line'], section['line_end']
        location = {'line_start': section['line'], 'line_end': end,
                    'page_start': section['page_start'], 'page_end': section['page_end']}
        if response:
            answers.append({'number': int(response[1]), 'label': response[2], 'section_id': section['id'],
                            'comment': '\n'.join(lines[start:end]).strip(), **location})
            continue
        body = '\n'.join(lines[start:end])
        visible = _visible(body)
        projected, mapping = _project(body)
        leading = len(projected) - len(projected.lstrip())
        markers = list(re.finditer(r'\(([A-E])\)', visible))
        labels = [item[1] for item in markers]
        safe = len(labels) >= 2 and labels == list('ABCDE'[:len(labels)])
        ambiguities = [] if safe else ['alternative_boundaries_uncertain']
        alternatives = []
        if safe:
            for index, marker in enumerate(markers):
                stop = markers[index + 1].start() if index + 1 < len(markers) else len(visible)
                raw_start = mapping[leading + marker.start()][0]
                raw_end = mapping[leading + stop - 1][1]
                first_line = start + body[:raw_start].count('\n')
                last_line = start + body[:raw_end].count('\n') + 1
                alternatives.append({'label': marker[1], 'text': visible[marker.end():stop].strip(),
                                     **_location(lines, first_line, last_line)})
        questions.append({'number': int(match[1]), 'section_id': section['id'],
                          'stem': visible[:markers[0].start()].strip() if safe else visible,
                          'alternatives': alternatives, 'answer': None, 'ambiguities': ambiguities, **location})
    counts = Counter(item['number'] for item in questions)
    for question in questions:
        candidates = [a for a in answers if a['number'] == question['number']]
        if len(candidates) == 1 and counts[question['number']] == 1:
            question['answer'] = candidates[0]
            if question['alternatives'] and candidates[0]['label'] not in {a['label'] for a in question['alternatives']}:
                question['ambiguities'].append('answer_not_in_observed_alternatives')
        else:
            question['ambiguities'].append('answer_missing_or_ambiguous')
    return questions, answers


def toc_diagnostics(markdown: str, sections: list[dict]) -> tuple[list[dict], list[dict]]:
    """Compara referências pontilhadas do sumário com títulos reais únicos."""
    from course_editorial import _PAGE

    entries, discrepancies = [], []
    page = None
    pending = ''
    for line in markdown.splitlines():
        marker = _PAGE.search(line)
        if marker:
            page = int(marker[1])
            pending = ''
        if not line.startswith('|'):
            continue
        cell = line.strip('| ').strip()
        match = re.fullmatch(r'(.+?)\s*\.{3,}\s*(\d+)', cell)
        if not match:
            if re.match(r'^\d+(?:\.\d+)*\.', cell) and not re.search(r'\.{3,}', cell):
                pending = cell
            continue
        title = (pending + ' ' + match[1]).strip()
        pending = ''
        candidates = [s for s in sections if canonical(s['title']) == canonical(title) and s['page_start'] != page]
        entry = {'title': title, 'toc_page': page, 'announced_page': int(match[2]),
                 'section_id': None, 'actual_page': None, 'status': 'unresolved'}
        if len(candidates) == 1:
            target = candidates[0]
            entry.update(section_id=target['id'], actual_page=target['page_start'], status='matched')
            if target['page_start'] is not None and target['page_start'] != int(match[2]):
                entry['status'] = 'source_discrepancy'
                discrepancies.append(dict(entry))
        entries.append(entry)
    return entries, discrepancies
