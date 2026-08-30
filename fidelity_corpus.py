"""Gate executável do corpus jurídico de fidelidade textual."""

from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
import unicodedata
from pathlib import Path
from typing import Any

from converter import PdfMarkdownConverter

CORPUS_DIR = Path(__file__).resolve().parent / "tests" / "fixtures" / "fidelity"
MANIFEST_PATH = CORPUS_DIR / "corpus.json"


def _normalized(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _anchor_normalized(value: str, *, accent_insensitive: bool) -> str:
    normalized = _normalized(value)
    if accent_insensitive:
        normalized = "".join(
            character for character in unicodedata.normalize("NFD", normalized) if not unicodedata.combining(character)
        )
    return normalized


def load_manifest(path: Path = MANIFEST_PATH) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1 or not isinstance(payload.get("cases"), list):
        raise ValueError("Manifesto do corpus de fidelidade inválido.")
    return payload


def _evaluate_case(case: dict[str, Any], output_root: Path, converter: PdfMarkdownConverter) -> dict[str, Any]:
    source = CORPUS_DIR / str(case["file"])
    failures: list[str] = []
    actual_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    if actual_hash != case["sha256"]:
        failures.append("SHA-256 divergente")

    result = converter.convert(
        source,
        output_root / str(case["id"]),
        False,
        60_000,
        page_numbers=tuple(int(number) for number in case["pages"]),
        activation_verified=True,
    )
    markdown = result.markdown_path.read_text(encoding="utf-8")
    accent_insensitive = case.get("anchor_matching") == "accent_insensitive"
    normalized = _anchor_normalized(markdown, accent_insensitive=accent_insensitive)
    cursor = 0
    missing_anchors: list[str] = []
    for anchor in case["ordered_anchors"]:
        normalized_anchor = _anchor_normalized(str(anchor), accent_insensitive=accent_insensitive)
        position = normalized.find(normalized_anchor, cursor)
        if position < 0:
            missing_anchors.append(str(anchor))
        else:
            cursor = position + len(normalized_anchor)
    if missing_anchors:
        failures.append("âncoras ausentes ou fora de ordem: " + " | ".join(missing_anchors))
    if "\ufffd" in markdown:
        failures.append("saída contém caractere de substituição Unicode")

    expected_pages = tuple(int(number) for number in case["pages"])
    actual_pages = tuple(item.page_number for item in result.page_coverage)
    if actual_pages != expected_pages:
        failures.append(f"cobertura de páginas divergente: {actual_pages}")
    if result.failed_pages:
        failures.append(f"páginas não recuperadas: {result.failed_pages}")

    required_status = case.get("required_status")
    if required_status and any(item.status != required_status for item in result.page_coverage):
        failures.append(f"método de extração diferente de {required_status}")
    if case.get("require_fidelity_review") and not result.fidelity_review_pages:
        failures.append("OCR não foi marcado para conferência humana")
    expected_review_pages = case.get("expected_fidelity_review_pages")
    if isinstance(expected_review_pages, list) and list(result.fidelity_review_pages) != expected_review_pages:
        failures.append(
            f"páginas previstas para conferência {expected_review_pages}, obtidas {list(result.fidelity_review_pages)}"
        )

    minimum_score = case.get("minimum_page_fidelity_score")
    scores = [item.fidelity_score for item in result.page_coverage if item.fidelity_score is not None]
    if isinstance(minimum_score, (int, float)) and (len(scores) != len(result.page_coverage) or min(scores) < minimum_score):
        failures.append(f"score de fidelidade abaixo de {minimum_score}")

    return {
        "id": case["id"],
        "ok": not failures,
        "failures": failures,
        "pages": list(actual_pages),
        "statuses": [item.status for item in result.page_coverage],
        "fidelity_scores": [item.fidelity_score for item in result.page_coverage],
        "fidelity_review_pages": list(result.fidelity_review_pages),
    }


def evaluate_corpus(manifest_path: Path = MANIFEST_PATH, output_root: Path | None = None) -> dict[str, Any]:
    manifest = load_manifest(manifest_path)
    converter = PdfMarkdownConverter()
    if output_root is not None:
        output_root.mkdir(parents=True, exist_ok=True)
        cases = [_evaluate_case(case, output_root, converter) for case in manifest["cases"]]
        return {"ok": all(case["ok"] for case in cases), "case_count": len(cases), "cases": cases}
    with tempfile.TemporaryDirectory(prefix="nexojuris-fidelity-") as temp_dir:
        cases = [_evaluate_case(case, Path(temp_dir), converter) for case in manifest["cases"]]
    return {"ok": all(case["ok"] for case in cases), "case_count": len(cases), "cases": cases}


def main() -> int:
    parser = argparse.ArgumentParser(description="Valida o corpus jurídico de fidelidade textual.")
    parser.add_argument("--report", type=Path, help="Caminho opcional para o relatório JSON.")
    parser.add_argument("--output-dir", type=Path, help="Preserva os Markdown gerados para diagnóstico.")
    args = parser.parse_args()
    report = evaluate_corpus(output_root=args.output_dir)
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
