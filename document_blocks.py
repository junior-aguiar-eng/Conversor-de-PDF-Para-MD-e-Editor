"""Projeção comum de blocos do Markdown final e invariantes documentais.

Não classifica pela matéria jurídica nem altera a transcrição. A extração PDF
e a preparação editorial decidem o texto uma vez; os formatos compartilham
essa representação e as mesmas coordenadas.
"""
from __future__ import annotations

import hashlib
import re
from collections import Counter

from course_editorial import _HEADING, _PAGE, _visible
from document_structure import _location


def build_blocks(markdown: str, sections: list[dict], questions: list[dict], answers: list[dict]) -> list[dict]:
    lines = markdown.splitlines()
    headings = {s['line']: s for s in sections}
    alternatives = {a['line_start']: (q, a) for q in questions for a in q['alternatives']}
    blocks = []
    occurrences: Counter = Counter()
    section = None
    index = 0
    while index < len(lines):
        line = lines[index]
        if not _visible(line) or line.strip() == '---':
            index += 1
            continue
        start = index
        role = None
        ambiguities = []
        if index + 1 in headings:
            section = headings[index + 1]
            kind = 'heading'
            index += 1
        elif index + 1 in alternatives:
            question, alternative = alternatives[index + 1]
            kind = 'alternative'
            role = {'question_id': question['section_id'], 'label': alternative['label']}
            index = alternative['line_end']
        else:
            fence = re.match(r'^(`{3,}|~{3,})', line)
            if fence:
                kind = 'code'
                index += 1
                while index < len(lines):
                    closing = re.match(r'^' + re.escape(fence[1][0]) + '{' + str(len(fence[1])) + r',}\s*$', lines[index])
                    index += 1
                    if closing:
                        break
            else:
                kind = ('table' if line.startswith('|') else
                        'note' if re.match(r'^>\s*\[!\w+\]', line) else
                        'quotation' if line.startswith('>') else
                        'legal_transcription' if re.match(r'^(?:§\s*\d|[Aa]rt\.\s*\d)', _visible(line)) else
                        'footnote' if re.match(r'^\[\^[^]]+\]:', line) else
                        'visual' if re.match(r'^!\[', line) else
                        'list_item' if re.match(r'^(?:[-*+]\s|\d+[.)]\s|\(?[A-Za-z][.)]\s)', _visible(line)) else
                        'paragraph')
                index += 1
                while index < len(lines) and lines[index].strip():
                    if (_HEADING.match(lines[index]) or index + 1 in alternatives
                            or _PAGE.match(lines[index]) or re.match(r'^[-*+]\s', lines[index])):
                        break
                    index += 1
                if kind == 'paragraph' and re.fullmatch(r'\*\*[^*]{1,120}\*\*\s*', line):
                    ambiguities.append('emphasis_without_confirmed_structure')
        raw = '\n'.join(lines[start:index])
        parent = section['id'] if section and kind != 'heading' else section['parent_id'] if section else None
        key = f'{parent}\x1f{kind}\x1f{_visible(raw)}'
        occurrences[key] += 1
        identity = (section['id'] if kind == 'heading' else alternative['id'] if kind == 'alternative' else
                    'block-' + hashlib.sha256(f'{key}\x1f{occurrences[key]}'.encode()).hexdigest()[:20])
        question = next((q for q in questions if q['line_start'] <= start + 1 <= q['line_end']), None)
        answer = next((a for a in answers if a['line_start'] < start + 1 <= a['line_end']), None)
        if kind == 'paragraph' and question:
            kind = 'question_stem'
        if answer and kind != 'heading':
            role = {'answer_section_id': answer['section_id'], 'question_number': answer['number']}
            if kind == 'paragraph':
                kind = 'comment'
        location = _location(lines, start, index)
        blocks.append({'id': identity, 'parent_id': parent, 'section_id': section['id'] if section else None,
                       'order': len(blocks) + 1, 'type': kind, 'markdown': raw, 'text': _visible(raw),
                       'role': role, 'classification': {'status': 'ambiguous' if ambiguities else 'recognized',
                                                       'reasons': ambiguities},
                       'source': location, **location})
    return blocks


def validate_structure(sections: list[dict], questions: list[dict], blocks: list[dict]) -> dict:
    """Verifica invariantes; não comprova acerto da classificação nem fidelidade."""
    diagnostics = []
    by_id = {s['id']: s for s in sections}
    if len(by_id) != len(sections) or len({b['id'] for b in blocks}) != len(blocks):
        diagnostics.append({'code': 'duplicate_id', 'status': 'failed'})
    for section in sections:
        parent = by_id.get(section['parent_id'])
        if section['parent_id'] and (not parent or parent['level'] >= section['level']
                                     or parent['line'] >= section['line']
                                     or parent['line_end'] < section['line_end']):
            diagnostics.append({'code': 'invalid_parent_or_interval', 'status': 'failed', 'section_id': section['id']})
        if not section['line'] <= section['direct_line_end'] <= section['line_end']:
            diagnostics.append({'code': 'invalid_direct_interval', 'status': 'failed', 'section_id': section['id']})
    siblings = {}
    for section in sections:
        previous = siblings.get(section['parent_id'])
        if previous and previous['line_end'] >= section['line']:
            diagnostics.append({'code': 'section_sibling_overlap', 'status': 'failed', 'section_id': section['id']})
        siblings[section['parent_id']] = section
    for question in questions:
        section = by_id.get(question['section_id'])
        for alternative in question['alternatives']:
            if (not section or not question['line_start'] <= alternative['line_start'] <= alternative['line_end']
                    <= question['line_end']):
                diagnostics.append({'code': 'alternative_outside_question', 'status': 'failed',
                                    'section_id': question['section_id'], 'line': alternative['line_start']})
        for previous, current in zip(question['alternatives'], question['alternatives'][1:], strict=False):
            if previous['line_end'] >= current['line_start']:
                diagnostics.append({'code': 'alternative_sibling_overlap', 'status': 'failed',
                                    'section_id': question['section_id'], 'line': current['line_start']})
        for reason in question['ambiguities']:
            diagnostics.append({'code': reason, 'status': 'inconclusive', 'section_id': question['section_id'],
                                'line': question['line_start'], 'page': question['page_start']})
    for block in blocks:
        if block['parent_id'] and block['parent_id'] not in by_id:
            diagnostics.append({'code': 'missing_block_parent', 'status': 'failed', 'block_id': block['id']})
        if not 1 <= block['line_start'] <= block['line_end']:
            diagnostics.append({'code': 'invalid_block_interval', 'status': 'failed', 'block_id': block['id']})
        for reason in block['classification']['reasons']:
            diagnostics.append({'code': reason, 'status': 'inconclusive', 'block_id': block['id'],
                                'line': block['line_start'], 'page': block['page_start']})
    status = ('failed' if any(d['status'] == 'failed' for d in diagnostics) else
              'inconclusive' if diagnostics else 'passed')
    return {'status': status, 'scope': 'tree_and_textual_intervals', 'diagnostics': diagnostics}
