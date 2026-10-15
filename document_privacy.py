"""Protege marcas textuais de identificação da fonte, sem anonimizar a doutrina."""

from __future__ import annotations

import html
import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

PRIVACY_POLICY_VERSION = 1
_FIELD = re.compile(r"\b(CPF|CNPJ|RG|Nome|E-?mail|Telefone|Celular|Matr[ií]cula)\s*:\s*", re.I)
_MARKUP = re.compile(r"<!--.*?-->|<[^>]*>|&(?:\#\d+|\#x[\da-fA-F]+|[a-zA-Z]+);", re.S)


class PrivacyProtectionError(RuntimeError):
    """Uma proteção identificada não pôde ser aplicada: a conversão não prossegue."""


def _projection(text: str, *, visible: bool) -> tuple[str, list[tuple[int, int]]]:
    """Normaliza acentos, pontuação e estilos mantendo os offsets de origem."""
    chars: list[str] = []
    positions: list[tuple[int, int]] = []

    def append(value: str, start: int, end: int):
        normalized = unicodedata.normalize("NFKD", value).casefold()
        for char in normalized:
            if char.isalnum():
                chars.append(char)
                positions.append((start, end))
            elif unicodedata.combining(char) and positions:
                positions[-1] = (positions[-1][0], end)

    cursor = 0
    for match in _MARKUP.finditer(text) if visible else ():
        for index in range(cursor, match.start()):
            append(text[index], index, index + 1)
        if match[0].startswith("&"):
            append(html.unescape(match[0]), *match.span())
        cursor = match.end()
    for index in range(cursor, len(text)):
        append(text[index], index, index + 1)
    return "".join(chars), positions


def _canonical(text: str) -> str:
    # A fonte fornece texto literal; um e-mail entre <...> não é uma tag.
    return _projection(text, visible=False)[0]


def _boundary_text(text: str) -> str:
    # Mantém espaços e quebras de linha: página 94 não faz parte de um CPF
    # na linha seguinte. Delimitadores de apresentação não formam palavras.
    return re.sub(r"[*_`]", "", _MARKUP.sub(
        lambda match: html.unescape(match[0]) if match[0].startswith("&") else "", text,
    ))


@dataclass
class IdentificationPlan:
    # Valores nunca entram em repr, estatísticas, JSON, logs ou mensagens de erro.
    _rectangles: dict[int, list[tuple[float, ...]]] = field(default_factory=dict, repr=False)
    _fields: tuple[str, ...] = field(default=(), repr=False)
    _values: tuple[str, ...] = field(default=(), repr=False)
    _email_fields: frozenset[str] = field(default_factory=frozenset, repr=False)
    _email_values: frozenset[str] = field(default_factory=frozenset, repr=False)
    guarded_pages: int = 0
    text_replacements: int = 0
    uninspected_pages: int = 0

    @property
    def stats(self) -> dict[str, int]:
        return {
            "policy_version": PRIVACY_POLICY_VERSION,
            "source_identification_marks": sum(map(len, self._rectangles.values())),
            "source_identification_pages": len(self._rectangles),
            "guarded_pages": self.guarded_pages,
            "text_replacements": self.text_replacements,
            "uninspected_pages": self.uninspected_pages,
        }

    def note_guarded_page(self, page_number: int) -> None:
        if page_number in self._rectangles:
            self.guarded_pages += 1

    @property
    def has_identification(self) -> bool:
        return bool(self._rectangles)

    def redact_working_document(self, document) -> None:
        """Apaga marcas reconhecidas antes de renderizar/OCR/exportar imagens.

        Altera somente o documento aberto em memória; nunca salva sobre a fonte.
        A redação textual posterior continua protegendo mensagens e checkpoints.
        """
        if not isinstance(document, pymupdf.Document):
            return
        try:
            for number, rectangles in self._rectangles.items():
                page = document[number - 1]
                for rectangle in rectangles:
                    page.add_redact_annot(pymupdf.Rect(rectangle) + (-1, -1, 1, 1), fill=(1, 1, 1))
                page.apply_redactions(images=2, graphics=2)
        except Exception:
            raise PrivacyProtectionError('Não foi possível proteger as imagens da fonte; conversão interrompida.') from None

    def clean_text(self, text: str) -> str:
        if not self._fields:
            return text
        for patterns, replacement in ((self._fields, ""), (self._values, "[identificação pessoal removida]")):
            for visible in (True, False):
                projected, mapping = _projection(text, visible=visible)
                spans: list[tuple[int, int]] = []
                for pattern in patterns:
                    start = 0
                    while (index := projected.find(pattern, start)) >= 0:
                        stop = index + len(pattern)
                        left, right = mapping[index][0], mapping[stop - 1][1]
                        before = _boundary_text(text[:left])
                        after = _boundary_text(text[right:])
                        numeric_neighbor = (
                            (pattern[0].isdigit() and re.search(r"\d[.\-/]*\Z", before))
                            or (pattern[-1].isdigit() and re.match(r"[.\-/]*\d", after))
                        )
                        word_neighbor = (
                            (pattern[0].isalpha() and re.search(r"[^\W\d_](?:['’\-])?\Z", before))
                            or (pattern[-1].isalpha() and re.match(r"(?:['’\-])?[^\W\d_]", after))
                        )
                        email_neighbor = (
                            (pattern in self._email_fields | self._email_values and re.match(r"[\w.@+\-]", after))
                            or (pattern in self._email_values and re.search(r"[\w.@+\-]\Z", before))
                        )
                        if not (numeric_neighbor or word_neighbor or email_neighbor):
                            if not replacement:
                                line_start = text.rfind("\n", 0, left) + 1
                                line_end = text.find("\n", right)
                                line_end = len(text) if line_end < 0 else line_end
                                remainder = text[line_start:left] + text[right:line_end]
                                if "<!--" not in remainder and not _projection(remainder, visible=True)[0]:
                                    left, right = line_start, min(len(text), line_end + 1)
                            spans.append((left, right))
                        start = stop
                merged: list[tuple[int, int]] = []
                for start, end in sorted(spans):
                    if merged and start <= merged[-1][1]:
                        merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
                    else:
                        merged.append((start, end))
                for start, end in reversed(merged):
                    # Não apaga um fechamento de tag ou de negrito localizado
                    # entre os fragmentos do nome, alterando o resto do texto.
                    whole_line = (start == 0 or text[start - 1] == "\n") and (
                        end == len(text) or text[end - 1] == "\n"
                    )
                    provenance = r"<!-- NEXOJURIS_(?:PAGE|CONTINUATION)\b.*?-->"
                    tokens = (provenance if not replacement and whole_line
                              else provenance + r"|</?[A-Za-z][^>]*>|\*{1,2}|_{1,2}|`+")
                    markup = "".join(re.findall(tokens, text[start:end], re.S))
                    text = text[:start] + replacement + markup + text[end:]
                self.text_replacements += len(merged)
        return text


def plan_from_document(document) -> IdentificationPlan:
    plan = IdentificationPlan()
    # Dublês legados do conversor não representam PDFs nem precisam de redação.
    if not isinstance(document, pymupdf.Document):
        return plan
    if document.is_encrypted:
        # O conversor mantém sua cobertura de páginas indisponíveis: sem a
        # senha não há texto da fonte para exportar ou para inspecionar.
        plan.uninspected_pages = document.page_count
        return plan
    candidates = []
    try:
        for number, page in enumerate(document, 1):
            width, height = page.rect.width, page.rect.height
            blocks = page.get_text("dict", flags=pymupdf.TEXTFLAGS_DICT & ~pymupdf.TEXT_PRESERVE_IMAGES)["blocks"]
            for block in blocks:
                for line in block.get("lines", []):
                    text = "".join(span["text"] for span in line["spans"]).strip()
                    matches = list(_FIELD.finditer(text))
                    if not matches:
                        continue
                    x0, y0, x1, y1 = line["bbox"]
                    margin = y1 <= height * .10 or y0 >= height * .90 or x1 <= width * .10 or x0 >= width * .90
                    if not margin:
                        continue
                    for index, match in enumerate(matches):
                        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
                        label = _canonical(match[1])
                        value = text[match.end():end].strip(" \t|;–—")
                        digits = re.sub(r"\D", "", value)
                        numeric = bool(re.fullmatch(r"[\d.\s()/+\-]+", value))
                        valid = (
                            (label == "cpf" and numeric and len(digits) == 11)
                            or (label == "cnpj" and numeric and len(digits) == 14)
                            or (label in {"rg", "matricula"} and len(digits) >= 5 and len(value.split()) <= 2)
                            or (label in {"telefone", "celular"} and numeric and 8 <= len(digits) <= 15)
                            or (label == "email" and bool(re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value)))
                            or (label == "nome" and len(re.findall(r"[^\W\d_]+", value)) >= 2)
                        )
                        if valid:
                            candidates.append((number, label, match[0] + value, value, tuple(line["bbox"])))
    except Exception:
        raise PrivacyProtectionError(
            "Não foi possível conferir as marcas de identificação do PDF; conversão interrompida."
        ) from None
    occurrences: dict[str, set[int]] = defaultdict(set)
    for number, _label, text, _value, _bbox in candidates:
        occurrences[_canonical(text)].add(number)
    accepted = [item for item in candidates if len(document) == 1 or len(occurrences[_canonical(item[2])]) >= 2]
    labels = Counter(item[1] for item in accepted)
    # Contato editorial isolado não equivale a uma marca de identificação pessoal.
    identity_cluster = any(labels[key] for key in ("cpf", "cnpj", "rg", "matricula")) or (
        labels["nome"] and any(labels[key] for key in ("email", "telefone", "celular"))
    )
    if not identity_cluster:
        return plan
    for number, _label, _text, _value, bbox in accepted:
        plan._rectangles.setdefault(number, []).append(bbox)
    plan._fields = tuple(sorted({_canonical(item[2]) for item in accepted}, key=len, reverse=True))
    plan._values = tuple(sorted({_canonical(item[3]) for item in accepted if len(_canonical(item[3])) >= 8},
                                key=len, reverse=True))
    plan._email_fields = frozenset(_canonical(item[2]) for item in accepted if item[1] == "email")
    plan._email_values = frozenset(_canonical(item[3]) for item in accepted if item[1] == "email")
    return plan


def plan_from_source(source: Path) -> IdentificationPlan:
    if not source.is_file():
        return IdentificationPlan()
    try:
        with pymupdf.open(source) as document:
            return plan_from_document(document)
    except PrivacyProtectionError:
        raise
    except Exception:
        raise PrivacyProtectionError("Não foi possível conferir a identificação da fonte PDF.") from None
