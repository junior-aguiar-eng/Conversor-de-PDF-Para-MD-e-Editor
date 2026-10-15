"""Regressões editoriais de apostilas, sem depender de material privado."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from course_editorial import SourceEvidence, build_section_index, filter_margin_images, prepare_course_markdown


class CourseEditorialTests(unittest.TestCase):
    def render(self, text: str, **kwargs) -> str:
        return prepare_course_markdown(text, **kwargs).markdown

    def test_preserves_numeric_hierarchy_and_demotes_prose(self) -> None:
        text = (
            '# **1. DOUTRINA**\n\n# **1.1. AGENTES PÚBLICOS**\n\n'
            '# **1.1.1. CARGO E FUNÇÃO**\n\n'
            '# 1. A jurisprudência dessa Corte é pacífica no sentido de que **não é recepcionada a norma**.\n\n'
            '# **b) AGENTES ADMINISTRATIVOS:** exercem atividade profissional no Estado.\n'
        )
        result = self.render(text)
        self.assertIn('### **1.1. AGENTES PÚBLICOS**', result)
        self.assertIn('#### **1.1.1. CARGO E FUNÇÃO**', result)
        self.assertNotIn('## 1. A jurisprudência', result)
        self.assertNotIn('# **b)', result)
        self.assertIn('**b) AGENTES ADMINISTRATIVOS:** exercem', result)

    def test_attention_is_local_note_and_does_not_reset_section(self) -> None:
        result = self.render(
            '# **1.1.1. CARGOS**\n\n# **ATENÇÃO! ESTABILIDADE.**\n\nTexto da nota.\n\n'
            '# **1.1.2. EMPREGOS**\n'
        )
        self.assertIn('> [!NOTE]\n> **ATENÇÃO! ESTABILIDADE.**', result)
        self.assertNotIn('## **ATENÇÃO', result)
        self.assertIn('#### **1.1.2. EMPREGOS**', result)

    def test_splits_joined_sumula_without_losing_bold_or_text(self) -> None:
        result = self.render(
            '# **2. JURISPRUDÊNCIA**\n\n**SÚMULAS DO STF**\n\n'
            '**SÚMULA VINCULANTE Nº 42**\n\n'
            '**É inconstitucional a vinculação. SÚMULA VINCULANTE Nº 43**\n\n'
            'É inconstitucional o provimento. **SÚMULA Nº 44**\n\nSó por lei.\n'
        )
        self.assertIn('### SÚMULAS DO STF', result)
        self.assertIn('#### SÚMULA VINCULANTE Nº 42', result)
        self.assertIn('**É inconstitucional a vinculação.**', result)
        self.assertIn('\n\n#### SÚMULA VINCULANTE Nº 43\n\n', result)
        self.assertIn('#### SÚMULA Nº 44', result)

    def test_tema_heading_is_scoped_under_court_and_collection(self) -> None:
        result = self.render(
            '# **2. JURISPRUDÊNCIA**\n\n**JULGADOS DO STF**\n\n'
            '**TESES DE REPERCUSSÃO GERAL**\n\n**TEMA 384 E 377**\n\nEnunciado.\n'
        )
        self.assertIn('### JULGADOS DO STF', result)
        self.assertIn('#### TESES DE REPERCUSSÃO GERAL', result)
        self.assertIn('##### TEMA 384 E 377', result)

    def test_splits_collection_fused_with_previous_citation(self) -> None:
        result = self.render('**Decisão. (STF, ADI 123, Info 100). JULGADOS DO STJ**\n\nEmenta.')
        self.assertIn('**Decisão. (STF, ADI 123, Info 100).**', result)
        self.assertIn('\n\n### JULGADOS DO STJ\n\n', result)

    def test_source_evidence_excludes_footer_from_cross_page_word_pair(self) -> None:
        import pymupdf

        from course_editorial import read_source_evidence

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'source.pdf'
            with pymupdf.open() as doc:
                first = doc.new_page()
                first.insert_text((60, 100), 'Ressalte-')
                first.insert_text((280, 790), '6')
                second = doc.new_page()
                second.insert_text((60, 100), 'se que permanece o direito.')
                doc.save(path)
            evidence = read_source_evidence(path)
        self.assertEqual(evidence.hyphenations[('Ressalte', 'se')], '-')

    def test_does_not_promote_sumula_cited_in_prose_or_inside_table(self) -> None:
        text = 'Conforme a **Súmula nº 15**, aplica-se a regra.\n\n| **SÚMULA Nº 1** | Texto |\n|---|---|'
        result = self.render(text)
        self.assertNotIn('###', result)
        self.assertIn('| **SÚMULA Nº 1** | Texto |', result)

    def test_rejoins_source_attested_word_across_inline_styles(self) -> None:
        evidence = SourceEvidence(hyphenations={('es', 'pecíficos'): '', ('MU', 'DANÇA'): ''})
        result = self.render('requisitos <u>es-</u> pecíficos.\n\n**MU-** **<u>DANÇA</u>**', evidence=evidence)
        self.assertIn('<u>es</u>pecíficos.', result)
        self.assertNotIn('MU-', result)
        self.assertNotIn('** **', result)

    def test_preserves_legal_compound_and_numeric_reference(self) -> None:
        evidence = SourceEvidence(hyphenations={('público', 'privado'): '-', ('Ressalte', 'se'): '-'})
        result = self.render('público- privado. Ressalte- se a regra do § 4º- B. Decreto-Lei 200/1967.', evidence=evidence)
        self.assertIn('público-privado', result)
        self.assertIn('Ressalte-se', result)
        self.assertIn('§ 4º-B', result)
        self.assertIn('Decreto-Lei 200/1967', result)

    def test_source_attested_split_word_with_missing_hyphen_is_rejoined(self) -> None:
        evidence = SourceEvidence(hyphenations={('per', 'centual'): ''})
        self.assertIn('percentual mínimo', self.render('um <u>per centual mínimo</u>.', evidence=evidence))

    def test_windows_line_endings_do_not_block_word_repair(self) -> None:
        evidence = SourceEvidence(hyphenations={('PREVIDENCI', 'ÁRIOS'): ''})
        self.assertIn('PREVIDENCIÁRIOS', self.render('PREVIDENCI- \r\n\r\nÁRIOS', evidence=evidence))

    def test_recovers_source_attested_spacing_at_a_style_boundary(self) -> None:
        evidence = SourceEvidence(word_spacing={'remuneraçãode': ('remuneração', 'de')})
        self.assertIn('**remuneração** de prefeito', self.render('**remuneração**de prefeito.', evidence=evidence))

    def test_source_context_repairs_glued_prefix_before_hyphenation(self) -> None:
        evidence = SourceEvidence(hyphenations={('po', 'derá'): ''}, contextual_hyphenations={('maspo', 'derá'): 'mas'})
        self.assertIn('mas poderá', self.render('maspo-<br>derá optar.', evidence=evidence))

    def test_rejoins_hyphenation_in_table_without_collapsing_cells(self) -> None:
        result = self.render(
            '| Cargo | Vínculo |\n|---|---|\n| servido-<br>res | livre<br>nomeação |',
            evidence=SourceEvidence(hyphenations={('servido', 'res'): ''}),
        )
        self.assertIn('| servidores | livre<br>nomeação |', result)
        self.assertEqual(result.count('|'), 9)

    def test_without_source_does_not_guess_a_compound_at_table_line_break(self) -> None:
        result = self.render('| A | B |\n|---|---|\n| público-<br>privado | texto |')
        self.assertIn('público-<br>privado', result)

    def test_join_spans_three_pages_without_losing_the_middle_body(self) -> None:
        text = (
            '<!-- NEXOJURIS_PAGE page=1 status=native -->\n\nO servidor permanece em\n\n---\n\n'
            '<!-- NEXOJURIS_PAGE page=2 status=native -->\n\natividade enquanto não houver\n\n---\n\n'
            '<!-- NEXOJURIS_PAGE page=3 status=native -->\n\nconclusão do procedimento.'
        )
        result = self.render(text)
        self.assertEqual(result.count('NEXOJURIS_PAGE'), 3)
        self.assertEqual(result.count('NEXOJURIS_CONTINUATION'), 2)
        self.assertEqual(result.count('atividade enquanto não houver'), 1)

    def test_margin_filter_uses_geometry_and_keeps_substantive_picture(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            import pymupdf

            root = Path(directory)
            pix = pymupdf.Pixmap(pymupdf.csRGB, (0, 0, 126, 148), False)
            pix.clear_with(255)
            for y in range(60):
                pix.set_pixel(10, y, (0, 0, 0))
            pix.save(root / 'edge.png')
            pix.save(root / 'figure.png')
            first, second = '![](images/doc/edge.png)\n\n', '![](images/doc/figure.png)\n\n'
            chunk = {'text': first + second, 'page_boxes': [
                {'class': 'picture', 'bbox': (533, 385, 593, 456), 'pos': (0, len(first))},
                {'class': 'picture', 'bbox': (150, 500, 210, 571), 'pos': (len(first), len(first + second))},
            ]}
            result = filter_margin_images(chunk, width=595, height=842, assets_dir=root)
            self.assertNotIn('edge.png', result)
            self.assertIn('figure.png', result)
            self.assertFalse((root / 'edge.png').exists())
            self.assertTrue((root / 'figure.png').exists())

    def test_cross_page_join_keeps_markers_and_continuation_provenance(self) -> None:
        evidence = SourceEvidence(hyphenations={('Ressalte', 'se'): '-'})
        text = (
            '<!-- NEXOJURIS_PAGE page=1 status=native -->\n\nRessalte-\n\n---\n\n'
            '<!-- NEXOJURIS_PAGE page=2 status=native -->\n\nse que há divergência.\n'
        )
        result = self.render(text, evidence=evidence)
        self.assertIn('Ressalte-se', result)
        self.assertEqual(result.count('NEXOJURIS_PAGE'), 2)
        self.assertIn('NEXOJURIS_CONTINUATION pages=1,2', result)
        self.assertNotIn('\n\nse que', result)

    def test_does_not_join_paragraphs_over_missing_page_or_new_section(self) -> None:
        text = (
            '<!-- NEXOJURIS_PAGE page=1 status=native -->\n\nTexto incompleto\n\n---\n\n'
            '<!-- NEXOJURIS_PAGE page=3 status=native -->\n\ncontinuação selecionada.\n\n---\n\n'
            '<!-- NEXOJURIS_PAGE page=4 status=native -->\n\n# **2. OUTRA SEÇÃO**\n\nTexto.'
        )
        result = self.render(text)
        self.assertNotIn('NEXOJURIS_CONTINUATION', result)
        self.assertIn('## **2. OUTRA SEÇÃO**', result)

    def test_keeps_failed_page_and_warning_separate(self) -> None:
        text = '<!-- NEXOJURIS_PAGE page=2 status=failed -->\n\n> [!WARNING]\n> Página não recuperada.'
        self.assertEqual(self.render(text).strip(), text)

    def test_code_and_urls_are_not_rewritten(self) -> None:
        text = '```md\n# **ATENÇÃO!**\nservido-<br>res\n**a **\n```\n\n`§ 4º- B` https://example.com/pre- fixo'
        result = self.render(text)
        self.assertIn('```md\n# **ATENÇÃO!**\nservido-<br>res\n**a **\n```', result)
        self.assertIn('`§ 4º- B`', result)

    def test_star_horizontal_rule_is_preserved(self) -> None:
        self.assertIn('\n\n****\n\n', self.render('Texto.\n\n****\n\nOutro assunto.'))

    def test_does_not_move_spaces_between_independent_bold_runs(self) -> None:
        text = '**Atenção.** A expressão **funcionários públicos** aparece no texto.'
        self.assertEqual(self.render(text).strip(), text)

    def test_table_cell_bold_has_valid_delimiters(self) -> None:
        result = self.render('|**FUNÇÃO DE CONFIANÇA **|**CARGO EM COMISSÃO**|\n|---|---|')
        self.assertIn('|**FUNÇÃO DE CONFIANÇA** |', result)

    def test_compound_prefix_keeps_its_hyphen(self) -> None:
        from course_editorial import _record_hyphenation

        evidence = SourceEvidence()
        _record_hyphenation('O ex-', 'servidor deve comparecer.', set(), evidence)
        self.assertEqual(evidence.hyphenations[('ex', 'servidor')], '-')

    def test_ambiguous_real_words_are_left_for_review(self) -> None:
        from course_editorial import _record_hyphenation

        evidence = SourceEvidence()
        _record_hyphenation('O de-', 'ver legal.', {'de', 'ver'}, evidence)
        result = prepare_course_markdown('O de-\nver legal.', evidence=evidence)
        self.assertNotIn('de-ver', result.markdown)
        self.assertNotIn('dever', result.markdown)
        self.assertTrue(result.issues)

    def test_intact_pronominal_form_in_source_disambiguates_a_split_verb(self) -> None:
        from course_editorial import _record_hyphenation

        evidence = SourceEvidence()
        _record_hyphenation('Desta-', 'que-se que a regra permanece.', {'Desta', 'que', 'Destaque-se'}, evidence)
        self.assertEqual(evidence.hyphenations[('Desta', 'que')], '')

    def test_cross_page_style_whitespace_never_deletes_a_letter(self) -> None:
        evidence = SourceEvidence(hyphenations={('inter', 'nacional'): ''})
        for first, second in [('** inter-**', 'nacional.'), ('inter-', '** nacional.**'), ('<u> inter-</u>', 'nacional.')]:
            with self.subTest(first=first, second=second):
                result = self.render(
                    f'<!-- NEXOJURIS_PAGE page=1 status=native -->\n\n{first}\n\n---\n\n'
                    f'<!-- NEXOJURIS_PAGE page=2 status=native -->\n\n{second}', evidence=evidence,
                )
                from course_editorial import _visible
                self.assertIn('internacional', _visible(result))

    def test_section_line_numbers_preserve_fenced_code_height(self) -> None:
        text = '```python\na=1\nb=2\n```\n\n# Assunto\n\nTexto.'
        self.assertEqual(build_section_index(text)[0]['line'], 6)

    def test_section_titles_keep_inline_code(self) -> None:
        self.assertEqual(build_section_index('# 1. Regra `X`')[0]['title'], '1. Regra `X`')

    def test_broken_word_falsely_promoted_to_heading_is_rejoined(self) -> None:
        evidence = SourceEvidence(hyphenations={('PREVIDENCI', 'ÁRIOS'): ''})
        result = self.render('REGIMES PREVIDENCI-\n\n# **ÁRIOS**:', evidence=evidence)
        from course_editorial import _visible
        self.assertIn('PREVIDENCIÁRIOS', _visible(result))

    def test_prepare_is_idempotent(self) -> None:
        text = '# **1. DOUTRINA**\n\n# **ATENÇÃO!**\n\nNota.\n\n**SÚMULAS DO STF**\n\n**SÚMULA Nº 1**\n\nTexto.'
        once = self.render(text)
        self.assertEqual(self.render(once), once)

    def test_page_continuations_are_idempotent(self) -> None:
        text = (
            '<!-- NEXOJURIS_PAGE page=1 status=native -->\n\nTexto em\n\n---\n\n'
            '<!-- NEXOJURIS_PAGE page=2 status=native -->\n\ncontinuação.'
        )
        once = self.render(text)
        self.assertEqual(self.render(once), once)

    def test_section_tree_tracks_parent_context_and_exact_page_end(self) -> None:
        text = (
            '<!-- NEXOJURIS_PAGE page=1 status=native -->\n\n# 1. DOUTRINA\n\n'
            '## 1.1. Assunto\n\nConteúdo.\n\n'
            '<!-- NEXOJURIS_PAGE page=2 status=native -->\n\n# 2. JURISPRUDÊNCIA\n\n'
            '## SÚMULAS DO STF\n\n### SÚMULA Nº 1\n\nEnunciado.'
        )
        nodes = build_section_index(text)
        self.assertEqual(nodes[0]['page_end'], 1)
        self.assertEqual(nodes[1]['parent_id'], nodes[0]['id'])
        self.assertEqual(nodes[-1]['context'], ['2. JURISPRUDÊNCIA', 'SÚMULAS DO STF'])
        self.assertEqual(nodes[-1]['page_start'], 2)

    def test_repeated_images_without_layout_geometry_are_preserved(self) -> None:
        import pymupdf

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for number in range(3):
                pix = pymupdf.Pixmap(pymupdf.csRGB, (0, 0, 126, 148), False)
                pix.clear_with(255)
                for y in range(60):
                    pix.set_pixel(10, y, (0, 0, 0))
                pix.set_pixel(number + 1, 135, (0, 0, 0))
                pix.save(root / f'scrap{number}.png')
            pix = pymupdf.Pixmap(pymupdf.csRGB, (0, 0, 126, 148), False)
            pix.clear_with(255)
            for x in range(20, 100):
                pix.set_pixel(x, 70, (0, 0, 0))
            pix.save(root / 'diagram.png')
            text = '\n\n'.join(f'![](images/doc/{name})' for name in ['scrap0.png', 'scrap1.png', 'scrap2.png', 'diagram.png'])
            result = prepare_course_markdown(text, assets_dir=root)
            self.assertEqual(result.stats['decoration_images_removed'], 0)
            self.assertIn('scrap', result.markdown)
            self.assertIn('diagram.png', result.markdown)
            self.assertTrue((root / 'diagram.png').exists())


if __name__ == '__main__':
    unittest.main()
