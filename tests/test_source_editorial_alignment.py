"""Regras baseadas na fonte, com vocabulário independente do acervo privado."""
import tempfile
import unittest
from pathlib import Path

import pymupdf

from course_editorial import prepare_course_markdown


class SourceEditorialAlignmentTests(unittest.TestCase):
    def test_unreadable_source_cannot_authorize_text_repair(self):
        from course_editorial import prepare_source_formatting

        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'invalid.pdf'
            path.write_bytes(b'not a PDF')
            raw = '**OBSERVAÇÃO:** Rótulo.\n\nuni verso e licençaparental.'
            self.assertEqual(prepare_source_formatting(raw, path).markdown, raw)

    def test_boundary_vocabulary_accepts_accents_and_does_not_invent_uppercase_compounds(self):
        from course_editorial import SourceEvidence, _record_hyphenation

        e = SourceEvidence()
        _record_hyphenation('Os agentes de-', 'têm o poder.', {'de', 'têm', 'detém'}, e)
        self.assertEqual(e.hyphenations[('de', 'têm')], '')
        _record_hyphenation('EX-', 'TERNO', {'externo'}, e)
        self.assertEqual(e.hyphenations[('EX', 'TERNO')], '')
        _record_hyphenation('EX-', 'DIRETOR', {'diretor'}, e)
        self.assertEqual(e.hyphenations[('EX', 'DIRETOR')], '-')

    def source(self, root, pages):
        path = Path(root) / 'source.pdf'
        with pymupdf.open() as doc:
            for lines in pages:
                page = doc.new_page()
                for y, value in lines:
                    page.insert_text((60, y), value, fontsize=10)
            doc.save(path)
        return path

    def test_context_restores_separators_across_style_boundaries(self):
        with tempfile.TemporaryDirectory() as root:
            path = self.source(root, [[(100, 'O projeto garante licença-'), (115, 'parental ao servidor.'),
                                       (140, 'Essa norma constitucional e o seu efeito.'),
                                       (170, 'A navegação percorre o universo conhecido.'),
                                       (200, 'A licença-parental é um direito.')]])
            raw = ('O projeto garante **licençaparental** ao servidor.\n\n'
                   'Essa norma **constitucionale** o seu efeito.\n\n'
                   'A navegação percorre o **uni verso** conhecido.')
            text = prepare_course_markdown(raw, source=path).markdown
            self.assertIn('**licença-parental**', text)
            self.assertIn('**constitucional e**', text)
            self.assertIn('**universo**', text)
            self.assertEqual(prepare_course_markdown(text, source=path).markdown, text)

    def test_unicode_line_hyphen_is_not_an_inline_source_space(self):
        # Unicode font is embedded by insert_htmlbox, rather than Latin-1 insert_text.
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'source.pdf'
            with pymupdf.open() as doc:
                page = doc.new_page()
                page.insert_htmlbox((60, 80, 500, 250), 'A decisão garante ter‐<br>ceirização lícita.<br>'
                                     'Preserve pré‐ constitucional e deve- se como na fonte.')
                doc.save(path)
            raw = 'A decisão garante **ter‐ ceirização** lícita.\n\nPreserve pré‐ constitucional e deve- se como na fonte.'
            text = prepare_course_markdown(raw, source=path).markdown
            self.assertIn('**terceirização**', text)
            self.assertIn('pré‐ constitucional e deve- se', text)

    def test_context_does_not_override_another_occurrence_or_code(self):
        with tempfile.TemporaryDirectory() as root:
            path = self.source(root, [[(100, 'A regra permite reavaliar o exemplo.'),
                                       (130, 'A palavra re avaliar permanece nesta transcrição.')]])
            raw = ('A regra permite **re avaliar** o exemplo.\n\n'
                   'A palavra re avaliar permanece nesta transcrição.\n\n'
                   '```text\nA regra permite re avaliar o exemplo.\n```')
            text = prepare_course_markdown(raw, source=path).markdown
            self.assertIn('**reavaliar**', text)
            self.assertIn('A palavra re avaliar', text)
            self.assertIn('```text\nA regra permite re avaliar', text)

    def test_url_line_hyphen_is_preserved_without_altering_other_url(self):
        with tempfile.TemporaryDirectory() as root:
            path = self.source(root, [[(100, 'Leia https://exemplo.test/teoria-do-estado-'),
                                       (115, 'de-coisas e confira a fonte.')]])
            raw = 'Leia https://exemplo.test/teoria-do-estadode-coisas e confira a fonte.\n\nhttps://outro.test/estadode'
            text = prepare_course_markdown(raw, source=path).markdown
            self.assertIn('https://exemplo.test/teoria-do-estado-de-coisas', text)
            self.assertIn('https://outro.test/estadode', text)

    def test_repeated_text_uses_wider_context_instead_of_guessing_a_separator(self):
        with tempfile.TemporaryDirectory() as root:
            path = self.source(root, [[(100, '(A) O exemplo da fonte garante licença maternidade ao agente.'),
                                       (130, '(B) O exemplo da fonte garante licença-maternidade ao agente.')]])
            raw = ('(A) O exemplo da fonte garante licençamaternidade ao agente.\n\n'
                   '(B) O exemplo da fonte garante licençamaternidade ao agente.')
            text = prepare_course_markdown(raw, source=path).markdown
            self.assertIn('(A) O exemplo da fonte garante licença maternidade', text)
            self.assertIn('(B) O exemplo da fonte garante licença-maternidade', text)

    def test_first_line_context_does_not_include_the_previous_page(self):
        with tempfile.TemporaryDirectory() as root:
            path = self.source(root, [[(100, 'Um texto independente da página anterior.')],
                                      [(100, 'Nota: O universo conhecido merece investigação.')]])
            raw = ('<!-- NEXOJURIS_PAGE page=1 status=native -->\n\nUm texto independente da página anterior.\n\n'
                   '<!-- NEXOJURIS_PAGE page=2 status=native -->\n\nNota: O **uni** verso conhecido merece investigação.')
            text = prepare_course_markdown(raw, source=path).markdown
            self.assertIn('**uni**verso conhecido', text)

    def test_inline_continuation_marker_is_not_a_lexical_boundary_of_one_page(self):
        with tempfile.TemporaryDirectory() as root:
            path = self.source(root, [[(100, 'O conteúdo deve continuar')], [(100, 'na próxima página.')]])
            raw = ('<!-- NEXOJURIS_PAGE page=1 status=native -->\n\nO conteúdo deve continuar\n\n'
                   '<!-- NEXOJURIS_PAGE page=2 status=native -->\n\nna próxima página.')
            text = prepare_course_markdown(raw, source=path).markdown
            self.assertIn('NEXOJURIS_CONTINUATION pages=1,2', text)
            self.assertIn('na próxima página.', text)

    def test_context_can_follow_a_column_without_interleaving_neighboring_lines(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'source.pdf'
            with pymupdf.open() as doc:
                p = doc.new_page()
                p.insert_text((60, 100), 'O sistema constitucional e a lei\nantiga possuem requisitos próprios.', fontsize=10)
                p.insert_text((340, 100), 'Outra coluna sem relação\ncom a primeira explicação.', fontsize=10)
                doc.save(path)
            raw = '| O sistema **constitucionale** a lei antiga possuem requisitos próprios. | Outra coluna sem relação |'
            text = prepare_course_markdown(raw, source=path).markdown
            self.assertIn('**constitucional e**', text)

    def test_inline_and_other_labels_group_the_entire_closed_box(self):
        for label in ('ATENÇÃO!', 'OBSERVAÇÃO:', 'IMPORTANTE:'):
            with self.subTest(label=label), tempfile.TemporaryDirectory() as root:
                path = Path(root) / 'source.pdf'
                with pymupdf.open() as doc:
                    p = doc.new_page()
                    p.draw_rect((50, 80, 500, 160))
                    p.insert_text((60, 100), label + ' Primeira regra.')
                    p.insert_text((60, 135), 'Segunda regra completa.')
                    p.insert_text((60, 190), 'Texto externo.')
                    doc.save(path)
                raw = f'**{label}** Primeira regra.\n\nSegunda regra completa.\n\nTexto externo.'
                text = prepare_course_markdown(raw, source=path).markdown
                self.assertIn('> Segunda regra completa.', text)
                self.assertNotIn('> Texto externo.', text)
                self.assertEqual(prepare_course_markdown(text, source=path).markdown, text)

    def test_source_formatting_is_shared_by_both_export_profiles(self):
        from markdown_utils import finalize_markdown

        for profile in ('curso', 'jurisprudencia'):
            with self.subTest(profile=profile), tempfile.TemporaryDirectory() as root:
                path = Path(root) / 'source.pdf'
                with pymupdf.open() as doc:
                    p = doc.new_page()
                    p.draw_rect((50, 80, 500, 160))
                    p.insert_text((60, 100), 'OBSERVAÇÃO: A primeira regra é confirmada.')
                    p.insert_text((60, 135), 'A segunda regra trata do universo conhecido.')
                    doc.save(path)
                raw = ('**OBSERVAÇÃO:** A primeira regra é confirmada.\n\n'
                       'A segunda regra trata do **uni verso** conhecido.')
                result = finalize_markdown(path, Path(root) / 'out.md', raw, 0, False, 60000, heading_profile=profile)
                text = result.markdown_path.read_text('utf8')
                self.assertIn('> A segunda regra trata do **universo** conhecido.', text)

    def test_box_with_columns_matches_complete_table_content_without_interleaving(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'source.pdf'
            with pymupdf.open() as doc:
                p = doc.new_page()
                p.draw_rect((50, 80, 550, 250))
                for x, y, value in ((60, 100, 'OBSERVAÇÃO: Quadro de referência.'),
                                     (60, 135, 'Primeira coluna.'), (300, 135, 'Segunda coluna.'),
                                     (60, 165, 'Valor esquerdo.'), (300, 165, 'Valor direito.')):
                    p.insert_text((x, y), value)
                p.insert_text((60, 300), 'Parágrafo externo.')
                doc.save(path)
            raw = ('**OBSERVAÇÃO:** Quadro de referência.\n\n'
                   '| Primeira coluna. Valor esquerdo. | Segunda coluna. Valor direito. |\n'
                   '| --- | --- |\n\nParágrafo externo.')
            text = prepare_course_markdown(raw, source=path).markdown
            self.assertIn('> | Primeira coluna.', text)
            self.assertNotIn('> Parágrafo externo.', text)
            self.assertEqual(prepare_course_markdown(text, source=path).markdown, text)

    def test_box_continuation_requires_matching_geometry_and_page_edges(self):
        for aligned in (False, True):
            with self.subTest(aligned=aligned), tempfile.TemporaryDirectory() as root:
                path = Path(root) / 'source.pdf'
                with pymupdf.open() as doc:
                    p = doc.new_page()
                    p.draw_rect((50, 700, 500, 805))
                    p.insert_text((60, 725), 'OBSERVAÇÃO: Primeira parte.')
                    p = doc.new_page()
                    p.draw_rect((50 if aligned else 100, 55, 500, 125))
                    p.insert_text((110, 80), 'Continuação comprovada.')
                    p.insert_text((60, 160), 'Texto independente.')
                    doc.save(path)
                raw = ('<!-- NEXOJURIS_PAGE page=1 status=native -->\n\n'
                       '**OBSERVAÇÃO:** Primeira parte.\n\n'
                       '<!-- NEXOJURIS_PAGE page=2 status=native -->\n\n'
                       'Continuação comprovada.\n\nTexto independente.')
                text = prepare_course_markdown(raw, source=path).markdown
                self.assertEqual('> Continuação comprovada.' in text, aligned)
                self.assertNotIn('> Texto independente.', text)
                self.assertEqual(prepare_course_markdown(text, source=path).markdown, text)

    def test_trailing_label_can_introduce_a_box_on_the_next_page(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'source.pdf'
            with pymupdf.open() as doc:
                p = doc.new_page()
                p.insert_text((60, 745), 'ATENÇÃO! Regra que continua:')
                p = doc.new_page()
                p.draw_rect((50, 55, 500, 125))
                p.insert_text((60, 80), 'Conteúdo da regra.')
                p.insert_text((60, 160), 'Parágrafo externo.')
                doc.save(path)
            raw = ('<!-- NEXOJURIS_PAGE page=1 status=native -->\n\nATENÇÃO! Regra que continua:\n\n'
                   '<!-- NEXOJURIS_PAGE page=2 status=native -->\n\nConteúdo da regra.\n\nParágrafo externo.')
            text = prepare_course_markdown(raw, source=path).markdown
            self.assertIn('> Conteúdo da regra.', text)
            self.assertNotIn('> Parágrafo externo.', text)

    def test_note_after_a_cross_page_box_keeps_its_own_source_page(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'source.pdf'
            with pymupdf.open() as doc:
                p = doc.new_page()
                p.draw_rect((50, 700, 500, 805))
                p.insert_text((60, 725), 'OBSERVAÇÃO: Primeira parte.')
                p = doc.new_page()
                p.draw_rect((50, 55, 500, 125))
                p.insert_text((60, 80), 'Continuação comprovada.')
                p.draw_rect((50, 200, 500, 300))
                p.insert_text((60, 230), 'ATENÇÃO! Outra regra.')
                p.insert_text((60, 270), 'Detalhe da outra regra.')
                doc.save(path)
            raw = ('<!-- NEXOJURIS_PAGE page=1 status=native -->\n\nOBSERVAÇÃO: Primeira parte.\n\n'
                   '<!-- NEXOJURIS_PAGE page=2 status=native -->\n\nContinuação comprovada.\n\n'
                   'ATENÇÃO! Outra regra.\n\nDetalhe da outra regra.')
            text = prepare_course_markdown(raw, source=path).markdown
            self.assertIn('> Continuação comprovada.', text)
            self.assertIn('> Detalhe da outra regra.', text)
            self.assertEqual(text.count('[!NOTE]'), 2)


if __name__ == '__main__':
    unittest.main()
