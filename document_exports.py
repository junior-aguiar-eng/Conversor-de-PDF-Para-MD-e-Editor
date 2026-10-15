"""Companheiros portáveis do Markdown final: índice para IA e leitura offline."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict
from pathlib import Path

from constants import resource_root
from course_editorial import build_section_index
from models import PageCoverage


def build_document_exports(
    markdown: str,
    markdown_path: Path,
    source: Path,
    heading_profile: str,
    page_coverage: tuple[PageCoverage, ...],
    editorial_stats: tuple[tuple[str, int], ...],
    editorial_issues: tuple[str, ...],
    privacy_stats: tuple[tuple[str, int], ...] = (),
) -> tuple[str, str]:
    """Deriva ambos os formatos do mesmo conteúdo, sem alterar o Markdown.

    Caminhos absolutos não são publicados. Fonte inexistente só é admitida
    pelos chamadores legados/sintéticos; seu hash fica explicitamente nulo.
    As licenças dos renderizadores acompanham o HTML autocontido.
    """
    source_hash = None
    if source.is_file():
        with source.open("rb") as stream:
            source_hash = hashlib.file_digest(stream, "sha256").hexdigest()
    structure = {
        "schema_version": 1,
        "markdown_file": markdown_path.name,
        "markdown_sha256": hashlib.sha256(markdown.encode("utf-8")).hexdigest(),
        "source_file": source.name,
        "source_sha256": source_hash,
        "heading_profile": heading_profile,
        "sections": build_section_index(markdown),
        "page_coverage": [asdict(item) for item in page_coverage],
        "editorial_stats": dict(editorial_stats),
        "editorial_issues": list(editorial_issues),
        "privacy": dict(privacy_stats),
        "privacy_scope": "Marcas textuais de identificação pessoal nas margens da fonte; não é anonimização geral do conteúdo.",
        "provenance": "page_start/page_end são páginas físicas do PDF; null significa origem não identificada.",
    }
    web = resource_root() / "web"
    payload = json.dumps({**structure, "markdown": markdown}, ensure_ascii=False)
    # application/json também obedece ao fechamento de <script> do parser HTML.
    for char in ("<", ">", "&", "\u2028", "\u2029"):
        payload = payload.replace(char, f"\\u{ord(char):04x}")
    replacements = {"__PAYLOAD__": payload}
    for token, file in (
        ("__MARKED__", "vendor/marked.umd.js"),
        ("__PURIFY__", "vendor/purify.min.js"),
        ("__READER__", "reading-export.js"),
    ):
        replacements[token] = re.sub(
            r"</script", r"<\\/script", (web / file).read_text(encoding="utf-8"), flags=re.IGNORECASE,
        )
    licenses = "\n\n".join(
        (web / "licenses" / name).read_text(encoding="utf-8")
        for name in ("marked-LICENSE.md", "dompurify-LICENSE.txt")
    )
    replacements["__LICENSES__"] = licenses.replace("--", "- -").replace("<", "&lt;")
    template = (web / "reading-export.html").read_text(encoding="utf-8")
    # Uma única substituição impede que conteúdo com tokens altere o template.
    html = re.sub(r"__PAYLOAD__|__MARKED__|__PURIFY__|__READER__|__LICENSES__", lambda m: replacements[m[0]], template)
    return json.dumps(structure, ensure_ascii=False, indent=2) + "\n", html
