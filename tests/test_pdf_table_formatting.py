"""Tabelas sintéticas: nenhuma regra depende do vocabulário das apostilas."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pymupdf

from course_editorial import _visible
from markdown_utils import finalize_markdown


class PdfTableFormattingTests(unittest.TestCase):
    def grid(self, page, ys, rows):
        for x in (50, 300, 550):
            page.draw_line((x, ys[0]), (x, ys[-1]))
        for y in ys:
            page.draw_line((50, y), (550, y))
        for top, row in zip(ys, rows, strict=False):
            for column, lines in enumerate(row):
                if isinstance(lines, str):
                    lines = [[(line, 'helv')] for line in lines.splitlines()]
                for index, fragments in enumerate(lines):
                    x = 60 + 250 * column
                    for text, font in fragments:
                        page.insert_text((x, top + 18 + index * 14), text, fontname=font, fontsize=10)
                        x += pymupdf.get_text_length(text, fontname=font, fontsize=10)

    def export(self, root, doc, raw, profile='curso'):
        source = root / 'source.pdf'
        doc.save(source)
        doc.close()
        result = finalize_markdown(source, root / 'result.md', raw, 0, False, 60000, heading_profile=profile)
        return result.markdown_path.read_text('utf-8'), json.loads(result.structure_path.read_text('utf-8'))

    def test_source_cells_restore_spacing_hyphenation_and_inline_style_in_all_profiles(self):
        for profile in ('curso', 'jurisprudencia'):
            with self.subTest(profile=profile), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                doc = pymupdf.open()
                page = doc.new_page(width=600, height=800)
                self.grid(page, [80, 160, 220], [
                    [ 'Modelo Boreal - Teoria da Repara-\nção. Neste caso.',
                      [[('Características:', 'hebo')], [('i) efeito ', 'helv'), ('ex nunc', 'heit'),
                                                          (' (por regra).', 'helv')]] ],
                    [ [[('Seis agentes ', 'helv'), ('ativos', 'hebo'), (' integram o conselho.', 'helv')]],
                      'Redação dada pela norma.' ],
                ])
                raw = ('<!-- NEXOJURIS_PAGE page=1 status=native -->\n\n'
                       '|Modelo Boreal - Teoria daRepara-|**Características:**|\n|---|---|\n'
                       '|ção.Neste caso.|i) efeito_ex nunc_(por regra).|\n'
                       '|Seis agentes**ativos**integram o conselho.|Redação dadapela norma.|\n')
                text, data = self.export(root, doc, raw, profile)
                plain = _visible(text)
                self.assertIn('Teoria da Reparação. Neste caso.', plain)
                self.assertIn('agentes ativos integram', plain)
                self.assertIn('Redação dada pela norma.', plain)
                self.assertIn('<em>ex nunc</em>', text)
                self.assertIn('<strong>Características:</strong><br>i) efeito', text)
                self.assertNotIn('_ex nunc_', text)
                self.assertEqual(data['editorial_stats']['source_tables_rebuilt'], 1)
                from document_privacy import plan_from_source
                from source_table_formatting import repair_pdf_tables

                again, _ = repair_pdf_tables(text, root / 'source.pdf', plan_from_source(root / 'source.pdf'))
                self.assertEqual(again, text)

    def test_adjacent_pages_merge_only_attested_continuation_and_keep_page_marker(self):
        for gap in ('', 'Parágrafo independente.\n\n'):
            with self.subTest(gap=gap), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                doc = pymupdf.open()
                first = doc.new_page(width=600, height=800)
                self.grid(first, [650, 740], [['Grupo azul.', 'i) primeiro;']])
                second = doc.new_page(width=600, height=800)
                self.grid(second, [60, 130, 200], [
                    ['', 'ii) segundo.'],
                    ['Grupo verde.', 'iii) terceiro.'],
                ])
                raw = ('<!-- NEXOJURIS_PAGE page=1 status=native -->\n\n'
                       '|Grupo azul.|i) primeiro;|\n|---|---|\n\n' + gap +
                       '<!-- NEXOJURIS_PAGE page=2 status=native -->\n\n'
                       '||ii) segundo.|\n|---|---|\n|Grupo verde.|iii) terceiro.|\n')
                text, data = self.export(root, doc, raw)
                self.assertIn('NEXOJURIS_PAGE page=2', text)
                self.assertIn('iii) terceiro.', _visible(text))
                expected = 0 if gap else 1
                self.assertEqual(data['editorial_stats'].get('source_table_continuations_joined', 0), expected)
                tables = [b for b in data['blocks'] if b['type'] == 'table']
                if not gap:
                    self.assertEqual(len(tables), 1)
                    self.assertEqual((tables[0]['page_start'], tables[0]['page_end']), (1, 2))
                    self.assertRegex(tables[0]['markdown'], r'primeiro;<br>\s*<!-- NEXOJURIS_PAGE page=2')

    def test_mismatched_cell_content_is_not_replaced(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            doc = pymupdf.open()
            page = doc.new_page(width=600, height=800)
            self.grid(page, [80, 140, 200], [
                ['Código', 'Valor'],
                ['Item A', '10'],
            ])
            raw = '|Código|Valor|\n|---|---|\n|Item A|999|\n'
            text, data = self.export(root, doc, raw)
            self.assertIn('999', text)
            self.assertEqual(data['editorial_stats'].get('source_tables_rebuilt', 0), 0)

    def test_decimal_punctuation_is_part_of_the_correspondence(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            doc = pymupdf.open()
            page = doc.new_page(width=600, height=800)
            self.grid(page, [80, 140, 200], [['Medida', 'Valor'], ['Item A', '12,3']])
            raw = '|Medida|Valor|\n|---|---|\n|Item A|1,23|\n'
            text, data = self.export(root, doc, raw)
            self.assertIn('1,23', text)
            self.assertEqual(data['editorial_stats'].get('source_tables_rebuilt', 0), 0)

    def test_duplicate_source_tables_require_disambiguation(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            doc = pymupdf.open()
            page = doc.new_page(width=600, height=800)
            for ys in ([80, 140, 200], [300, 360, 420]):
                self.grid(page, ys, [['Medida', 'Valor'], ['Item A', '10']])
            raw = '|Medida|Valor|\n|---|---|\n|Item A|10|\n'
            text, data = self.export(root, doc, raw)
            self.assertEqual(data['editorial_stats'].get('source_tables_rebuilt', 0), 0)
            self.assertEqual(data['editorial_stats']['source_tables_ambiguous'], 1)
            self.assertEqual(text.strip(), raw.strip())

    def test_fenced_table_example_is_not_rebuilt(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            doc = pymupdf.open()
            page = doc.new_page(width=600, height=800)
            self.grid(page, [80, 140, 200], [['Medida', 'Valor'], ['Item A', '10']])
            raw = '```md\n|Medida|Valor|\n|---|---|\n|Item A|10|\n```\n'
            text, data = self.export(root, doc, raw)
            self.assertIn(raw.strip(), text)
            self.assertEqual(data['editorial_stats'].get('source_tables_rebuilt', 0), 0)

    def test_optional_layout_detector_does_not_override_vector_cell_evidence(self):
        def unrelated_layout(page, **kwargs):
            page.layout_information = [(0, 0, 10, 10, 'table')]

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            doc = pymupdf.open()
            page = doc.new_page(width=600, height=800)
            self.grid(page, [80, 140, 200], [['Medida', 'Valor'], ['Item A', '10']])
            raw = '|Medida|Valor|\n|---|---|\n|Item A|10|\n'
            with patch.object(pymupdf.Page, 'get_layout', unrelated_layout):
                _, data = self.export(root, doc, raw)
            self.assertEqual(data['editorial_stats']['source_tables_rebuilt'], 1)

    def test_style_and_real_header_survive_source_reconstruction(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            doc = pymupdf.open()
            page = doc.new_page(width=600, height=800)
            first = [[('Descrição', 'hebo')]]
            second = [[('Valor', 'hebo')]]
            self.grid(page, [80, 140, 200], [[first, second], ['Texto sublinhado', '10']])
            page.draw_line((60, 159.5), (60 + pymupdf.get_text_length('Texto sublinhado', fontsize=10), 159.5))
            raw = '|Descrição|Valor|\n|---|---|\n|Texto sublinhado|10|\n'
            text, data = self.export(root, doc, raw)
            self.assertIn('| <strong>Descrição</strong> | <strong>Valor</strong> |', text)
            self.assertIn('<u>Texto sublinhado</u>', text)
            self.assertEqual(data['editorial_stats']['source_tables_rebuilt'], 1)

    def test_source_private_text_is_not_reintroduced(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            doc = pymupdf.open()
            page = doc.new_page(width=600, height=800)
            self.grid(page, [80, 140, 200], [['Contato', 'Categoria'],
                                          ['E-mail: pessoa@exemplo.com', 'Público']])
            for y, field in ((760, 'Nome: Pessoa Exemplo Teste'), (775, 'E-mail: pessoa@exemplo.com'),
                             (790, 'Telefone: (11) 91111-2222')):
                page.insert_text((24, y), field, fontsize=7)
            raw = '|Contato|Categoria|\n|---|---|\n|E-mail: pessoa@exemplo.com|Público|\n'
            text, data = self.export(root, doc, raw)
            self.assertNotIn('pessoa@exemplo.com', text)
            self.assertNotIn('pessoa@exemplo.com', json.dumps(data))
            self.assertGreater(data['privacy']['source_identification_marks'], 0)


if __name__ == '__main__':
    unittest.main()
