"""Testes do detector e do corpus jurídico versionado."""

from __future__ import annotations

import unittest

from fidelity_corpus import evaluate_corpus, load_manifest
from text_fidelity import assess_page_fidelity


class _Page:
    def __init__(self, text: str) -> None:
        self.text = text

    def get_text(self, kind: str, **_kwargs):
        if kind == "words":
            return [(0, 0, 1, 1, token) for token in self.text.split()]
        return self.text


class FidelityDetectorTests(unittest.TestCase):
    def test_detects_semantic_omission_even_when_output_is_not_empty(self) -> None:
        reference = " ".join(f"termo{index}" for index in range(50))
        assessment = assess_page_fidelity(_Page(reference), "termo1 termo2 conteúdo plausível", "native")
        self.assertTrue(any("omissão" in issue for issue in assessment.issues))

    def test_detects_reversed_reading_order(self) -> None:
        tokens = [f"palavra{index}" for index in range(40)]
        assessment = assess_page_fidelity(_Page(" ".join(tokens)), " ".join(reversed(tokens)), "native")
        self.assertTrue(any("ordem de leitura" in issue for issue in assessment.issues))

    def test_marks_ocr_as_requiring_visual_review(self) -> None:
        assessment = assess_page_fidelity(_Page(""), "texto juridicamente plausível", "ocr")
        self.assertTrue(any("OCR" in issue for issue in assessment.issues))

    def test_detects_invalid_unicode(self) -> None:
        assessment = assess_page_fidelity(_Page(""), "decis�o", "native")
        self.assertTrue(any("Unicode" in issue for issue in assessment.issues))


class RealFidelityCorpusTests(unittest.TestCase):
    def test_manifest_has_official_provenance_and_distinct_hashes(self) -> None:
        cases = load_manifest()["cases"]
        self.assertEqual(len(cases), 4)
        self.assertEqual(len({case["sha256"] for case in cases}), 4)
        self.assertTrue(all(case.get("source_url") or case.get("derived_from") for case in cases))

    def test_real_corpus_gate(self) -> None:
        report = evaluate_corpus()
        failures = {case["id"]: case["failures"] for case in report["cases"] if not case["ok"]}
        self.assertTrue(report["ok"], failures)


if __name__ == "__main__":
    unittest.main()
