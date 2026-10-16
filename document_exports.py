"""Companheiros portáveis do Markdown final: índice para IA e leitura offline."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict
from pathlib import Path

from constants import resource_root
from course_editorial import build_section_index
from document_blocks import build_blocks, validate_structure
from document_review import apply_editorial_review
from document_structure import exam_index, toc_diagnostics
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
    requested_heading_profile: str | None = None,
    editorial_review: dict | None = None,
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
    sections = build_section_index(markdown)
    questions, answers = exam_index(markdown, sections)
    toc_entries, discrepancies = toc_diagnostics(markdown, sections)
    blocks = build_blocks(markdown, sections, questions, answers)
    review_result = (apply_editorial_review(blocks, editorial_review, markdown, source_hash)
                     if editorial_review is not None else {'status': 'not_run'})
    validation = validate_structure(sections, questions, blocks)
    issues = list(editorial_issues)
    issues.extend(f"Fonte: {item['code']} — páginas {item.get('pages', [])}. {item.get('reason', '')}"
                  for item in review_result.get('source_issues', []))
    issues.extend(
        f"Sumário da fonte (página {entry['toc_page']}): {entry['title']} anuncia {entry['announced_page']}; "
        f"localização física {entry['actual_page']}. Transcrição preservada."
        for entry in discrepancies
    )
    issues.extend(f"Questão {q['number']} (página {q['page_start']}): {', '.join(q['ambiguities'])}"
                  for q in questions if q['ambiguities'])
    unresolved_toc = [entry for entry in toc_entries if entry['status'] in {'unresolved', 'ambiguous'}]
    issues.extend(f"Sumário (página {entry['toc_page']}): {entry['title']}: {entry['status']}."
                  for entry in unresolved_toc)
    issues.extend(f"Estrutura: {item['code']} (página {item.get('page')}, linha {item.get('line')})."
                  for item in validation['diagnostics'] if item.get('block_id'))
    structure = {
        "schema_version": 2,
        "structure_revision": 3,
        "document_identity": {"document_id": source_hash, "version_id": hashlib.sha256(markdown.encode('utf-8')).hexdigest(),
                              "id_scope": "document_local", "global_key": "document_id + local_id"},
        "markdown_file": markdown_path.name,
        "markdown_sha256": hashlib.sha256(markdown.encode("utf-8")).hexdigest(),
        "source_file": source.name,
        "source_sha256": source_hash,
        "heading_profile": heading_profile,
        "profile_selection": {"requested": requested_heading_profile or heading_profile,
                              "effective": heading_profile,
                              "method": "automatic_outline" if requested_heading_profile == "auto" else "explicit"},
        "sections": sections,
        "questions": questions,
        "answers": answers,
        "blocks": blocks,
        "structural_validation": validation,
        "editorial_review": review_result,
        "toc_entries": toc_entries,
        "toc_discrepancies": discrepancies,
        "toc_validation": {"status": "not_run" if not toc_entries else "inconclusive" if unresolved_toc else
                           "failed" if discrepancies else "passed",
                           "matched": sum(e['status'] == 'matched' for e in toc_entries),
                           "source_discrepancies": len(discrepancies), "unresolved_or_ambiguous": len(unresolved_toc)},
        "page_coverage": [asdict(item) for item in page_coverage],
        "editorial_stats": {**dict(editorial_stats), "section_count": len(sections),
                            "question_count": len(questions), "answer_count": len(answers),
                            "toc_discrepancy_count": len(discrepancies)},
        "editorial_issues": issues,
        "checks": {"section_index": "executed", "question_detection": "executed",
                   "structural_invariants": validation['status'],
                   "source_toc_comparison": "executed" if toc_entries else "not_run",
                   "course_editorial": "executed" if heading_profile == "curso" else "not_run",
                   "visual_fidelity": "not_run", "legal_accuracy": "not_run"},
        "fidelity_score_definition": "Heurística lexical de 0 a 1: 0,65 recall de tokens + 0,30 similaridade "
        "de ordem (até 2000 tokens) + 0,05 ausência de Unicode suspeito. Não mede fidelidade visual, "
        "estrutura ou correção jurídica. null: comparação indisponível/insuficiente. Calculada antes da etapa editorial.",
        "privacy": dict(privacy_stats),
        "privacy_scope": "Marcas textuais de identificação pessoal nas margens da fonte; não é anonimização geral do conteúdo.",
        "provenance": "page_start/page_end são páginas físicas inclusivas; null significa origem não identificada. "
        "line/line_end delimitam a seção completa (inclusive descendentes), até antes do próximo título de nível "
        "igual ou superior. direct_line_end limita o preâmbulo antes do primeiro descendente; "
        "direct_page_end é sua última página com conteúdo. Linhas são 1-based, inclusivas, do Markdown final.",
        "id_policy": "v2: SHA-256 truncado do caminho textual ancestral e ocorrência entre homônimos; "
        "inserções de outros títulos não alteram o ID. Renomear/mover ancestrais ou inserir homônimo anterior altera o ID. "
        "IDs sequenciais v1 não são preservados.",
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
