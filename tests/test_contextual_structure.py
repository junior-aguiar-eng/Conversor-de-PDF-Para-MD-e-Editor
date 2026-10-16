"""Variações editoriais independentes do vocabulário das apostilas."""
import json
import tempfile
import unittest
from pathlib import Path

from course_editorial import SourceEvidence, build_section_index, prepare_course_markdown
from document_exports import build_document_exports


class ContextualStructureTests(unittest.TestCase):
    def test_source_box_groups_only_its_attested_paragraphs(self):
        import pymupdf

        raw = ('<!-- NEXOJURIS_PAGE page=1 status=native -->\n\n'
               '# **ATENÇÃO!**\n\nPrimeira regra.\n\nSegunda regra.\n\nTexto fora da caixa.')
        for segmented in (False, True):
            with self.subTest(segmented=segmented), tempfile.TemporaryDirectory() as directory:
                source = Path(directory) / 'note.pdf'
                doc = pymupdf.open()
                page = doc.new_page()
                if segmented:
                    for rect in ((50, 80, 500, 80.4), (50, 160, 500, 160.4),
                                 (50, 80, 50.4, 160), (500, 80, 500.4, 160)):
                        page.draw_rect(rect, fill=(0, 0, 0), color=None)
                else:
                    page.draw_rect((50, 80, 500, 160))
                for y, value in ((100, 'ATENÇÃO!'), (120, 'Primeira regra.'),
                                 (140, 'Segunda regra.'), (190, 'Texto fora da caixa.')):
                    page.insert_text((60, y), value)
                doc.save(source)
                doc.close()
                result = prepare_course_markdown(raw, source=source)
                self.assertIn('> Primeira regra.\n>\n> Segunda regra.', result.markdown)
                self.assertNotIn('> Texto fora', result.markdown)
                self.assertEqual(result.stats['source_note_boxes_grouped'], 1)
                self.assertEqual(prepare_course_markdown(result.markdown, source=source).markdown, result.markdown)

    def test_note_without_box_or_complete_source_match_stays_local(self):
        import pymupdf

        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'note.pdf'
            doc = pymupdf.open()
            page = doc.new_page()
            page.draw_rect((50, 80, 500, 160))
            page.insert_text((60, 100), 'ATENÇÃO!')
            page.insert_text((60, 120), 'Regra completa da fonte.')
            doc.save(source)
            doc.close()
            raw = '**ATENÇÃO!**\n\nRegra diferente.'
            self.assertNotIn('> Regra diferente.', prepare_course_markdown(raw, source=source).markdown)
            self.assertNotIn('> Regra diferente.', prepare_course_markdown(raw).markdown)

    def test_note_grouping_requires_closed_borders_and_same_page(self):
        import pymupdf

        for closed in (False, True):
            with self.subTest(closed=closed), tempfile.TemporaryDirectory() as directory:
                source = Path(directory) / 'note.pdf'
                doc = pymupdf.open()
                page = doc.new_page()
                if closed:
                    page.draw_rect((50, 80, 500, 160))
                else:
                    page.draw_line((50, 80), (50, 160))
                    page.draw_line((500, 80), (500, 160))
                page.insert_text((60, 100), 'ATENÇÃO!')
                page.insert_text((60, 120), 'Regra completa.')
                doc.new_page()
                doc.save(source)
                doc.close()
                # Com caixa fechada, o texto só corresponde se não cruzar a página.
                boundary = '<!-- NEXOJURIS_PAGE page=2 status=native -->\n\n' if closed else ''
                raw = '**ATENÇÃO!**\n\n' + boundary + 'Regra completa.'
                self.assertNotIn('> Regra completa.', prepare_course_markdown(raw, source=source).markdown)

    def test_invalid_alternative_sequence_keeps_list_content(self):
        raw = '# 2. QUESTÕES\n\n**1. (Prova/2024)** Texto.\n\n(A) Um.\n\n- (C) Três.'
        text, data = self.export(raw)
        self.assertIn('- (C) Três.', text)
        self.assertEqual(data['questions'][0]['alternatives'], [])

    def test_list_prefixes_of_confirmed_alternatives_do_not_become_empty_items(self):
        raw = ('# 2. QUESTÕES\n\n**1. (Prova/2024)** Texto.\n\n'
               '(A) Um.\n\n- (B) Dois.\n\n- (C) Três.\n\n'
               '# 3. GABARITO\n\n**1. C**\n\n- Comentário com conteúdo.\n\n-\n')
        text, data = self.export(raw)
        self.assertNotIn('\n-\n', text.split('# 3. GABARITO')[0])
        self.assertIn('- Comentário com conteúdo.', text)
        self.assertTrue(text.rstrip().endswith('-'))
        self.assertEqual([a['text'].strip() for a in data['questions'][0]['alternatives']], ['Um.', 'Dois.', 'Três.'])

    def export(self, raw):
        text = prepare_course_markdown(raw).markdown
        data, _ = build_document_exports(text, Path('doc.md'), Path('absent.pdf'), 'curso', (), (), ())
        return text, json.loads(data)

    def test_alpha_sequence_wins_over_single_roman_letter(self):
        for sequence in ('HIJK', 'IJK', 'UVWXYZ'):
            text = '# 1.1. Espécies\n\n' + '\n\n'.join(f'# {c}. Classe {c}' for c in sequence)
            sections = build_section_index(prepare_course_markdown(text).markdown)[1:]
            self.assertEqual(len({(s['level'], s['parent_id']) for s in sections}), 1, sequence)

    def test_letter_numeric_subsections_return_to_letter_parent(self):
        raw = '# 1.1. Tema\n\n# A. Categoria\n\n# a) Detalhe\n\n# A.1. Primeiro\n\n# A.2. Segundo\n\n# B. Outra'
        sections = build_section_index(prepare_course_markdown(raw).markdown)
        parent = next(s for s in sections if s['title'] == 'A. Categoria')
        children = [s for s in sections if s['title'].startswith(('A.1.', 'A.2.'))]
        self.assertEqual(len(children), 2)
        self.assertEqual({s['parent_id'] for s in children}, {parent['id']})

    def test_letter_numeric_prose_is_not_promoted_wholesale(self):
        raw = '# 1.1. Tema\n\n# A. Categoria\n\n# **A.3. Rótulo** ' + 'Explicação extensa. ' * 12
        self.assertEqual(len(build_section_index(prepare_course_markdown(raw).markdown)), 2)

    def test_styled_sentence_tail_requires_source_line_adjacency(self):
        raw = 'Preserve a redação\n\n**<u>integral.</u>**'
        evidence = SourceEvidence(line_adjacencies={('redação', 'integral')})
        self.assertIn('redação **<u>integral.', prepare_course_markdown(raw, evidence=evidence).markdown)
        self.assertIn('redação\n\n', prepare_course_markdown(raw).markdown)

    def test_source_attested_labels_separate_explanation_and_keep_peers(self):
        evidence = SourceEvidence(editorial_labels={'A. PRIMEIRA CLASSE', 'B) SEGUNDA CLASSE'})
        raw = '# 1.1. Tipos\n\n# **A. PRIMEIRA CLASSE** – Explicação.\n\n# **B) SEGUNDA CLASSE –** Outra explicação.'
        text = prepare_course_markdown(raw, evidence=evidence).markdown
        sections = build_section_index(text)
        self.assertEqual([s['title'] for s in sections[1:]], ['A. PRIMEIRA CLASSE', 'B) SEGUNDA CLASSE'])
        self.assertEqual(sections[1]['parent_id'], sections[2]['parent_id'])
        self.assertIn('Explicação.', text)

    def test_inconsistent_alternative_sequence_preserves_text_and_reports(self):
        text, data = self.export('# 2. QUESTÕES\n\n**1. (Prova/2024)** Enunciado. (A) um. (B) dois. (D) quatro.')
        question = data['questions'][0]
        self.assertEqual(question['alternatives'], [])
        self.assertIn('(D) quatro.', text)
        self.assertIn('alternative_sequence_inconsistent', question['ambiguities'])

    def test_duplicate_answer_runs_are_not_silently_truncated(self):
        _, data = self.export('# 2. QUESTÕES\n\n**1. (Prova/2024)** Texto. (A) um. (B) dois. (A) três. (B) quatro.')
        question = data['questions'][0]
        self.assertEqual(question['alternatives'], [])
        self.assertIn('multiple_alternative_sequences', question['ambiguities'])

    def test_prose_enumeration_is_symmetric(self):
        text = '# 1.1. Critérios\n\n**a) primeiro requisito** com explicação;\n\n' \
               '**b) segundo requisito** com explicação;\n\n# **c) terceiro requisito com explicação.**'
        result = prepare_course_markdown(text).markdown
        self.assertNotIn('# **c)', result)

    def test_list_and_heading_peers_do_not_split_a_sequence(self):
        text = '# 1.1. Meios\n\n# **A. Primeiro instrumento;**\n\n- **B. Segundo instrumento.**\n'
        result = prepare_course_markdown(text).markdown
        a = next(line for line in result.splitlines() if 'A.' in line)
        b = next(line for line in result.splitlines() if 'B.' in line)
        self.assertEqual(a.startswith('#'), b.startswith('#'))

    def test_mixed_alternatives_and_inclusive_ranges(self):
        raw = ('# 2. QUESTÕES\n\n**1. (Prova/2024)** Julgue os itens I e II. '
               '(A) Um. B) Dois. c) Três.\n\n'
               '<!-- NEXOJURIS_PAGE page=2 status=native -->\ncontinua três.\n\nd) Quatro.\n\n'
               '# 3. GABARITO\n\n**1. C**\nComentário literal.')
        _, data = self.export(raw)
        question = data['questions'][0]
        self.assertEqual([a['label'] for a in question['alternatives']], list('ABCD'))
        self.assertIn('continua três', question['alternatives'][2]['text'])
        for left, right in zip(question['alternatives'], question['alternatives'][1:], strict=False):
            self.assertLess(left['line_end'], right['line_start'])
        self.assertEqual(question['answer']['label'], 'C')

    def test_internal_enumeration_before_answer_sequence_is_stem(self):
        raw = ('# 2. QUESTÕES\n\n**1. (Prova/2024)** Considere:\n\na) hipótese um;\n\nb) hipótese dois.\n\n'
               'Escolha a combinação.\n\n(A) Apenas um. (B) Ambos.\n')
        _, data = self.export(raw)
        self.assertIn('hipótese dois', data['questions'][0]['stem'])
        self.assertEqual(len(data['questions'][0]['alternatives']), 2)

    def test_extractor_can_omit_space_after_marker(self):
        _, data = self.export('# 2. QUESTÕES\n\n**1. (Prova/2024)** Texto. (A)um. (B)dois. (C)três.')
        self.assertEqual([a['label'] for a in data['questions'][0]['alternatives']], list('ABC'))

    def test_toc_dot_leaders_and_duplicate_matches(self):
        raw = ('<!-- NEXOJURIS_PAGE page=1 status=native -->\n|1. TEORIA .... .... 20|\n\n'
               '<!-- NEXOJURIS_PAGE page=4 status=native -->\n# 1. TEORIA\nTexto.\n')
        _, data = self.export(raw)
        self.assertEqual(data['toc_entries'][0]['status'], 'source_discrepancy')
        raw += '\n# 1. TEORIA\nOutro texto.'
        _, data = self.export(raw)
        self.assertEqual(data['toc_entries'][0]['status'], 'ambiguous')

    def test_common_blocks_preserve_source_and_classification(self):
        _, data = self.export('# 1.1. Tema\n\nTexto **destacado**.\n\n> Transcrição.\n\n- Item\n')
        blocks = data['blocks']
        self.assertEqual([b['type'] for b in blocks], ['heading', 'paragraph', 'quotation', 'list_item'])
        self.assertEqual(len({b['id'] for b in blocks}), len(blocks))
        self.assertEqual(blocks[1]['section_id'], data['sections'][0]['id'])
        self.assertEqual(data['structural_validation']['status'], 'passed')


if __name__ == '__main__':
    unittest.main()
