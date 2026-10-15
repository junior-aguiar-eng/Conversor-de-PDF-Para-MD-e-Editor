"""Regressões sintéticas de estrutura, sem frases ou dados do PDF privado."""
import json
import tempfile
import unittest
from pathlib import Path

from course_editorial import SourceEvidence, build_section_index, prepare_course_markdown
from document_exports import build_document_exports
from markdown_utils import finalize_markdown


class StructuralRevisionTests(unittest.TestCase):
    def test_auto_profile_uses_nested_outline_instead_of_flat_bulletin(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            result = finalize_markdown(
                root / 'absent.pdf', root / 'doc.md',
                '# DIREITO CIVIL\n\n# 1. TEORIA\n\n# 1.1. PESSOAS\n\n# 1.1.1. CAPACIDADE\n',
                0, False, 60000, heading_profile='auto',
            )
            data = json.loads(result.structure_path.read_text('utf-8'))
            self.assertEqual(data['heading_profile'], 'curso')
            self.assertEqual([s['level'] for s in data['sections']], [1, 2, 3, 4])

    def test_demoted_fragment_joins_previous_page_preserving_marker(self):
        raw = ('<!-- NEXOJURIS_PAGE page=3 status=native -->\n\nA norma alcança os\n\n---\n\n'
               '<!-- NEXOJURIS_PAGE page=4 status=native -->\n\n## interessados.\n')
        result = prepare_course_markdown(raw).markdown
        self.assertIn('NEXOJURIS_CONTINUATION pages=3,4', result)
        self.assertNotIn('## interessados', result)
        self.assertIn('page=4 status=native', result)

    def test_span_split_word_requires_source_evidence(self):
        evidence = SourceEvidence(fused_words={'Precedente'})
        raw = '**<mark>P</mark> recedente** aplicável.'
        self.assertIn('<mark>P</mark>recedente', prepare_course_markdown(raw, evidence=evidence).markdown)
        self.assertIn(' recedente', prepare_course_markdown(raw).markdown)

    def test_separate_source_heading_is_not_absorbed_into_numeric_title(self):
        raw = ('# DIREITO CIVIL\n\n# 1.1.1. DIREITOS A. PROTEÇÃO\n\n'
               '# a) primeira espécie\n\n# b) segunda espécie\n\n# B. TUTELA\nTexto.')
        evidence = SourceEvidence(heading_suffixes={'A. PROTEÇÃO'})
        result = prepare_course_markdown(raw, evidence=evidence).markdown
        sections = build_section_index(result)
        protection = next(s for s in sections if s['title'] == 'A. PROTEÇÃO')
        tutela = next(s for s in sections if s['title'] == 'B. TUTELA')
        self.assertEqual(protection['level'], tutela['level'])
        self.assertEqual(protection['parent_id'], tutela['parent_id'])

    def test_roman_list_marker_is_not_a_fragmented_word(self):
        result = prepare_course_markdown('VIII- Regra preservada.\n\npre- fixo duvidoso.')
        self.assertEqual(result.stats['unresolved_hyphenations'], 1)
        self.assertIn('pre- fixo', result.issues[0])

    def test_question_and_answer_collections_have_consistent_boundaries(self):
        raw = ('# DIREITO CIVIL\n\n# 3. QUESTÕES\n\n'
               '## **1. (Banca/2020)** Escolha a incorreta. (A) Um. (B) Dois.\n\n'
               '**2. (Banca/2021)** Enunciado. (A) Três. (B) Quatro.\n\n'
               '# 4. GABARITO COMENTADO\n\n## **1. B**\n\n(A) CORRETA. Comentário.\n\n'
               '## § 3º Texto legal.\n\n## **2. A**\n\n(B) INCORRETA. Comentário.\n')
        result = prepare_course_markdown(raw).markdown
        self.assertIn('### **1. (Banca/2020)**', result)
        self.assertIn('### **2. (Banca/2021)**', result)
        self.assertIn('### **1. B**', result)
        self.assertNotIn('## §', result)
        data = json.loads(build_document_exports(result, Path('doc.md'), Path('absent.pdf'), 'curso', (), (), ())[0])
        questions = data['questions']
        self.assertEqual([q['number'] for q in questions], [1, 2])
        self.assertEqual(questions[0]['answer']['label'], 'B')
        self.assertEqual([a['label'] for a in questions[0]['alternatives']], ['A', 'B'])

    def test_intervals_and_ids_survive_unrelated_insertion(self):
        raw = ('<!-- NEXOJURIS_PAGE page=2 status=native -->\n# Obra\n\n## Capítulo\n\n'
               'Texto.\n\n### Detalhe\n\n<!-- NEXOJURIS_PAGE page=3 status=native -->\nFinal.\n\n## Outro\nFim.')
        sections = build_section_index(raw)
        chapter = sections[1]
        self.assertEqual(chapter['page_end'], 3)
        self.assertEqual(chapter['line_end'], sections[-1]['line'] - 1)
        self.assertLess(chapter['direct_line_end'], chapter['line_end'])
        inserted = build_section_index(raw.replace('## Capítulo', '## Preâmbulo\n\n## Capítulo'))
        self.assertEqual(chapter['id'], next(s['id'] for s in inserted if s['title'] == 'Capítulo'))

    def test_source_toc_discrepancy_is_reported_without_changing_text(self):
        raw = ('<!-- NEXOJURIS_PAGE page=1 status=native -->\n# SUMÁRIO\n\n'
               '|1. TEORIA ........ 20|\n|---|\n\n'
               '<!-- NEXOJURIS_PAGE page=5 status=native -->\n## 1. TEORIA\nTexto.')
        data = json.loads(build_document_exports(raw, Path('doc.md'), Path('absent.pdf'), 'curso', (), (), ())[0])
        issue = data['toc_discrepancies'][0]
        self.assertEqual((issue['announced_page'], issue['actual_page']), (20, 5))
        self.assertEqual(data['schema_version'], 2)
        self.assertEqual(data['checks']['visual_fidelity'], 'not_run')


if __name__ == '__main__':
    unittest.main()
