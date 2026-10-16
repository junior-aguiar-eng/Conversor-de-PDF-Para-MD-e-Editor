"""Aplica decisões editoriais explícitas somente à versão conferida da fonte."""
from __future__ import annotations

import hashlib


def apply_editorial_review(blocks: list[dict], review: dict, markdown: str, source_hash: str | None) -> dict:
    digest = lambda value: hashlib.sha256(value.encode('utf-8')).hexdigest()  # noqa: E731
    if not source_hash or review.get('source_sha256') != source_hash or review.get('markdown_sha256') != digest(markdown):
        raise ValueError('Revisão editorial não corresponde à fonte/versão atual.')
    by_id = {block['id']: block for block in blocks}
    allowed = {'editorial_label', 'case_caption', 'case_reference', 'emphasized_proposition',
               'question_prompt', 'continuation', 'paragraph'}
    checked = []
    seen = set()
    # Verifica tudo antes de alterar qualquer classificação.
    for entry in review.get('entries', []):
        block = by_id.get(entry.get('block_id'))
        evidence = entry.get('evidence', {})
        if (not block or block['id'] in seen or entry.get('content_sha256') != digest(block['markdown'])
                or entry.get('decision') not in allowed or not entry.get('reason')
                or evidence.get('page') != block['page_start'] or len(evidence.get('bbox', [])) != 4):
            raise ValueError('Decisão editorial sem identidade/conteúdo/evidência correspondente.')
        seen.add(block['id'])
        checked.append((block, entry))
    for block, entry in checked:
        automatic = block['classification']
        block['classification'] = {'status': 'reviewed', 'reasons': [], 'method': 'source_review',
                                   'automatic_status': automatic['status'], 'automatic_reasons': automatic['reasons'],
                                   'decision': entry['decision'], 'review_reason': entry['reason'],
                                   'evidence': entry['evidence']}
        block['role'] = {**(block.get('role') or {}), 'editorial_role': entry['decision']}
    remaining = sum(b['classification']['status'] == 'ambiguous' for b in blocks)
    return {'status': 'completed' if not remaining else 'partial', 'reviewed_blocks': len(checked),
            'remaining_ambiguous_blocks': remaining, 'source_sha256': source_hash,
            'markdown_sha256': digest(markdown), 'source_issues': review.get('source_issues', [])}
