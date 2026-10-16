import hashlib
import unittest

from document_review import apply_editorial_review


class DocumentReviewTests(unittest.TestCase):
    def fixture(self):
        markdown = '**Texto destacado.**'
        block = {'id': 'block-test', 'markdown': markdown, 'page_start': 4,
                 'classification': {'status': 'ambiguous', 'reasons': ['emphasis_without_confirmed_structure']}}
        review = {'source_sha256': 'source-hash', 'markdown_sha256': hashlib.sha256(markdown.encode()).hexdigest(),
                  'entries': [{'block_id': 'block-test', 'content_sha256': hashlib.sha256(markdown.encode()).hexdigest(),
                               'decision': 'emphasized_proposition', 'reason': 'Frase de destaque no corpo da fonte.',
                               'evidence': {'page': 4, 'bbox': [40, 100, 400, 120]}}]}
        return markdown, block, review

    def test_verified_review_preserves_original_detector_result(self):
        markdown, block, review = self.fixture()
        result = apply_editorial_review([block], review, markdown, 'source-hash')
        self.assertEqual(result['reviewed_blocks'], 1)
        self.assertEqual(block['classification']['status'], 'reviewed')
        self.assertEqual(block['classification']['automatic_reasons'], ['emphasis_without_confirmed_structure'])

    def test_stale_review_cannot_clear_ambiguity(self):
        markdown, block, review = self.fixture()
        with self.assertRaises(ValueError):
            apply_editorial_review([block], review, markdown + '\nOutro texto.', 'source-hash')
        self.assertEqual(block['classification']['status'], 'ambiguous')

    def test_wrong_block_content_or_source_cannot_clear_ambiguity(self):
        for mismatch in ('source', 'content'):
            markdown, block, review = self.fixture()
            if mismatch == 'content':
                review['entries'][0]['content_sha256'] = 'wrong'
            with self.assertRaises(ValueError):
                apply_editorial_review([block], review, markdown, 'wrong' if mismatch == 'source' else 'source-hash')
            self.assertEqual(block['classification']['status'], 'ambiguous')


if __name__ == '__main__':
    unittest.main()
