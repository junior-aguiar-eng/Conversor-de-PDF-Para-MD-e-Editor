"""Regressões opcionais da fonte privada; nunca distribui PDFs ou transcrições.

NEXOJURIS_STRUCTURE_CORPUS aponta para os artefatos de uma reconversão integral.
"""
import hashlib
import json
import os
import re
import unittest
from pathlib import Path

from course_editorial import _visible


@unittest.skipUnless(os.environ.get('NEXOJURIS_STRUCTURE_CORPUS'), 'acervo privado não configurado')
class PrivateStructureCorpusTests(unittest.TestCase):
    def load(self, number):
        base = Path(os.environ['NEXOJURIS_STRUCTURE_CORPUS']) / f'direito_constitucional{number}'
        return base.with_suffix('.md').read_text('utf-8'), json.loads(base.with_suffix('.json').read_text('utf-8'))

    def test_reference_exports_and_invariants(self):
        for number in (5, 6):
            markdown, data = self.load(number)
            self.assertEqual(hashlib.sha256(markdown.encode()).hexdigest(), data['markdown_sha256'])
            self.assertFalse([d for d in data['structural_validation']['diagnostics'] if d['status'] == 'failed'])
            for question in data['questions']:
                for left, right in zip(question['alternatives'], question['alternatives'][1:], strict=False):
                    self.assertLess(left['line_end'], right['line_start'])

    def test_consolidated_separator_and_table_regressions(self):
        bad = {
            5: (r'\blicençamaternidade\b', r'\blicençapaternidade\b', r'\bh abeas\b',
                r'\bcorpu s\b', r'\bm andado\b'),
            6: (r'\bcriouse\b', r'\breeditála\b', r'\bconstitucionale\b', r'\bde- têm\b',
                r'\bc ontrole\b', r'\bincidental- difuso\b', r'\bter‐ ceirização\b',
                r'\binconstituciona‐ lidade\b', r'\binconsti‐ tucionalidade\b'),
        }
        for number in (5, 6):
            markdown, data = self.load(number)
            plain = _visible(markdown)
            for pattern in bad[number]:
                self.assertIsNone(re.search(pattern, plain), f'{number}: separator regression {pattern}')
            self.assertEqual([q['number'] for q in data['questions']], list(range(1, 32 if number == 5 else 19)))
            self.assertEqual([a['number'] for a in data['answers']], list(range(1, 32 if number == 5 else 19)))
            if number == 6:
                self.assertIn('/Notas-sobre-a-teoria-do-estado-de-coisas-inconstitucional', plain)
                table = next(b for b in data['blocks'] if b['type'] == 'table' and 'Sistema Austríaco' in b['text'])
                self.assertRegex(table['markdown'], r'<br>\s*<!-- NEXOJURIS_PAGE page=10[^>]*-->\s*<strong>iii\)')

    @unittest.skipUnless(os.environ.get('NEXOJURIS_EDITORIAL_AUDIT'), 'inventário editorial privado não configurado')
    def test_all_consolidated_source_boxes_are_contained_in_single_notes(self):
        directory = Path(os.environ['NEXOJURIS_EDITORIAL_AUDIT'])
        attention = json.loads((directory / 'source-audit.json').read_text('utf8'))
        observations = json.loads((directory / 'other-callout-boxes.json').read_text('utf8'))
        def key(s):
            return ''.join(c.casefold() for c in _visible(s) if c.isalnum())
        for n in (5, 6):
            _, data = self.load(n)
            targets = [(e['page'], e['source_box']['text']) for e in attention[n - 5]['incomplete_note_candidates']]
            targets += [(e['page'], e['text']) for e in observations if e['document'] == n]
            for page, text in targets:
                with self.subTest(document=n, page=page, start=text[:30]):
                    notes = [b for b in data['blocks'] if b['type'] == 'note'
                             and b['page_start'] <= page <= b['page_end'] and key(text) in key(b['markdown'])]
                    self.assertEqual(len(notes), 1)

    def test_reference5_continuations_comment_and_hierarchy(self):
        markdown, data = self.load(5)
        note = next(b for b in data['blocks'] if b['type'] == 'note'
                    and b['page_start'] == 16 and 'Súmula 624' in b['markdown'])
        self.assertIn('Súmula 623', note['markdown'])
        self.assertNotIn('vetos presidenciais', note['markdown'])
        table = next(b for b in data['blocks'] if b['type'] == 'table' and 'Seis brasileiros' in b['markdown'])
        self.assertIn('Seis brasileiros natos integram', _visible(table['markdown']))
        self.assertIn('dada pela', _visible(table['markdown']))
        self.assertFalse([s for s in data['sections'] if s['title'].startswith(('não-residentes.', '§ 2º'))])
        self.assertIn('não-residentes.', _visible(markdown))
        self.assertIn('Súmula Vinculante 12', _visible(markdown))
        peers = [s for s in data['sections'] if re.match(r'^[HIJK]\.\s', s['title'])]
        self.assertEqual(len(peers), 4)
        self.assertEqual(len({(s['parent_id'], s['level']) for s in peers}), 1)
        doctrine = next(s for s in data['sections'] if s['title'].startswith('1. DOUTRINA'))
        remedies = next(s for s in data['sections'] if s['title'].startswith('1.1. '))
        habeas = next(s for s in data['sections'] if s['title'].startswith('1.1.1. '))
        self.assertEqual((doctrine['page_start'], doctrine['page_end']), (6, 61))
        self.assertEqual((remedies['page_start'], remedies['page_end']), (6, 28))
        self.assertEqual((habeas['page_start'], habeas['page_end']), (6, 12))
        self.assertEqual(remedies['parent_id'], doctrine['id'])
        self.assertEqual(habeas['parent_id'], remedies['id'])
        # Ambas possuem A–D na fonte; não se presume uma alternativa E.
        for number, count in ((2, 4), (30, 4)):
            question = next(q for q in data['questions'] if q['number'] == number)
            self.assertEqual(len(question['alternatives']), count)
        actual = {(t['announced_page'], t['actual_page']) for t in data['toc_discrepancies']}
        self.assertTrue({(72, 55), (80, 62), (107, 84)} <= actual)

    def test_reference6_alternatives_and_editorial_peers(self):
        _, data = self.load(6)
        table = next(b for b in data['blocks'] if b['type'] == 'table' and 'Sistema Austríaco' in b['markdown'])
        self.assertIn('Sistema Norte-Americano', table['markdown'])
        self.assertIn('Anulabilidade', _visible(table['markdown']))
        self.assertEqual((table['page_start'], table['page_end']), (9, 10))
        self.assertIn('<em>ex nunc</em>', table['markdown'])
        question10 = next(q for q in data['questions'] if q['number'] == 10)
        self.assertEqual([a['label'] for a in question10['alternatives']], list('ABCDE'))
        for alternative in question10['alternatives']:
            self.assertFalse(re.search(r'(?m)^\s*-\s*$', alternative['text']))
        question = next(q for q in data['questions'] if q['number'] == 3)
        self.assertEqual([a['label'] for a in question['alternatives']], list('ABCDE'))
        self.assertEqual(question['alternatives'][-1]['page_start'], 64)
        types = [s for s in data['sections'] if re.match(r'^[AB][.)] VÍCIO (?:FORMAL|MATERIAL)', s['title'])]
        self.assertEqual(len(types), 2)
        self.assertEqual(types[0]['parent_id'], types[1]['parent_id'])
        divisions = [s for s in data['sections'] if s['page_start'] == 46 and s['title'] in ('SÚMULAS DO STF', 'JULGADOS')]
        self.assertEqual(len(divisions), 2)
        self.assertEqual(divisions[0]['parent_id'], divisions[1]['parent_id'])
        ado_mi = [b for b in data['blocks'] if b['page_start'] == 17 and
                  re.match(r'^(?:- )?[AB]\. ', _visible(b['markdown']))]
        self.assertEqual(len(ado_mi), 2)
        self.assertEqual(ado_mi[0]['type'], ado_mi[1]['type'])
        self.assertFalse([s for s in data['sections'] if s['page_start'] == 18 and s['title'].startswith('d)')])


if __name__ == '__main__':
    unittest.main()
