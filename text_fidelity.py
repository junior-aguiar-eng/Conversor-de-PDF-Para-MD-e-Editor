"""Sinais conservadores de possível perda textual durante a extração de PDF."""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any

_TOKEN_PATTERN = re.compile(r"[^\W_]+", re.UNICODE)
_MAX_COMPARISON_TOKENS = 2_000


@dataclass(frozen=True)
class FidelityAssessment:
    score: float | None
    token_recall: float | None
    order_score: float | None
    issues: tuple[str, ...] = ()


def _tokens(value: str) -> list[str]:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return _TOKEN_PATTERN.findall(normalized)


def _reference_tokens(page: Any) -> list[str]:
    try:
        words = page.get_text("words", sort=True) or []
    except TypeError:
        try:
            words = page.get_text("words") or []
        except Exception:
            words = []
    except Exception:
        words = []
    if isinstance(words, list) and words and isinstance(words[0], (list, tuple)):
        return _tokens(" ".join(str(word[4]) for word in words if len(word) > 4))
    try:
        return _tokens(str(page.get_text("text") or ""))
    except Exception:
        return []


def assess_page_fidelity(page: Any, extracted_text: str, extraction_status: str) -> FidelityAssessment:
    """Compara a saída com o mapa textual do PDF e sinaliza OCR não verificável."""
    extracted_tokens = _tokens(extracted_text)
    reference_tokens = _reference_tokens(page)
    issues: list[str] = []

    suspicious_count = sum(
        1
        for character in extracted_text
        if character == "\ufffd" or (unicodedata.category(character).startswith("C") and not character.isspace())
    )
    if suspicious_count:
        issues.append(f"{suspicious_count} caractere(s) Unicode inválido(s) ou de controle")

    if extraction_status == "ocr":
        issues.append("texto produzido por OCR requer conferência visual")

    if len(reference_tokens) < 20 or not extracted_tokens:
        return FidelityAssessment(None, None, None, tuple(issues))

    reference_counter = Counter(reference_tokens)
    extracted_counter = Counter(extracted_tokens)
    matched = sum(min(count, extracted_counter[token]) for token, count in reference_counter.items())
    token_recall = matched / len(reference_tokens)

    reference_sample = reference_tokens[:_MAX_COMPARISON_TOKENS]
    allowed = set(reference_sample)
    extracted_sample = [token for token in extracted_tokens if token in allowed][:_MAX_COMPARISON_TOKENS]
    order_score = SequenceMatcher(None, reference_sample, extracted_sample, autojunk=False).ratio()

    if token_recall < 0.70:
        issues.append(f"possível omissão textual (cobertura de tokens {token_recall:.0%})")
    if token_recall >= 0.70 and order_score < 0.50:
        issues.append(f"possível inversão da ordem de leitura (similaridade {order_score:.0%})")

    unicode_factor = 0.0 if suspicious_count else 1.0
    score = round((token_recall * 0.65) + (order_score * 0.30) + (unicode_factor * 0.05), 4)
    return FidelityAssessment(score, round(token_recall, 4), round(order_score, 4), tuple(issues))
