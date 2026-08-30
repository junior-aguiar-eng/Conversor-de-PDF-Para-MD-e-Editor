"""Tipos de dados e utilitários de resumo usados pela aplicação."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

PageExtractionStatus = Literal["native", "ocr", "fallback", "empty", "failed"]


@dataclass(frozen=True)
class OutputReservation:
    markdown_path: Path
    assets_dir: Path
    chunks_dir: Path


@dataclass(frozen=True)
class PageCoverage:
    page_number: int
    status: PageExtractionStatus
    warning: str = ""
    fidelity_score: float | None = None
    fidelity_issues: tuple[str, ...] = ()


def format_duration(seconds: float) -> str:
    total_seconds = round(seconds)
    if total_seconds < 60:
        return f"{total_seconds}s"
    minutes, secs = divmod(total_seconds, 60)
    return f"{minutes}m {secs}s"


@dataclass(frozen=True)
class ConversionResult:
    source: Path
    markdown_path: Path
    asset_count: int
    chunk_count: int
    extraction_seconds: float = 0.0
    page_coverage: tuple[PageCoverage, ...] = ()

    @property
    def failed_pages(self) -> tuple[int, ...]:
        return tuple(item.page_number for item in self.page_coverage if item.status == "failed")

    @property
    def warning_pages(self) -> tuple[int, ...]:
        return tuple(item.page_number for item in self.page_coverage if item.warning or item.fidelity_issues)

    @property
    def fidelity_review_pages(self) -> tuple[int, ...]:
        return tuple(item.page_number for item in self.page_coverage if item.fidelity_issues)


@dataclass(frozen=True)
class ConversionFailure:
    source: Path
    error_message: str
    details: str


@dataclass(frozen=True)
class BatchConversionSummary:
    successes: list[ConversionResult]
    failures: list[ConversionFailure]


def build_summary_message(
    title: str,
    output_dir: str,
    summary: BatchConversionSummary,
    elapsed_seconds: float = 0.0,
) -> str:
    message = [
        title,
        "",
        f"Convertidos: {len(summary.successes)}",
        f"Com erro: {len(summary.failures)}",
        f"Tempo total: {format_duration(elapsed_seconds)}",
    ]
    if summary.successes:
        message.extend(["", f"Arquivos salvos em:\n{output_dir}"])
        problematic = [result for result in summary.successes if result.failed_pages]
        if problematic:
            page_lines = "\n".join(
                f"- {result.source.name}: {', '.join(map(str, result.failed_pages))}"
                for result in problematic
            )
            message.extend(["", f"Páginas não recuperadas:\n{page_lines}"])
        fidelity_alerts = [result for result in summary.successes if result.fidelity_review_pages]
        if fidelity_alerts:
            fidelity_lines = "\n".join(
                f"- {result.source.name}: {', '.join(map(str, result.fidelity_review_pages))}"
                for result in fidelity_alerts
            )
            message.extend(["", f"Páginas que exigem conferência de fidelidade:\n{fidelity_lines}"])
    if summary.failures:
        failed_names = "\n".join(f"- {failure.source.name}" for failure in summary.failures[:10])
        message.extend(["", f"Falhas nesta execução:\n{failed_names}"])
        if len(summary.failures) > 10:
            message.append(f"... e mais {len(summary.failures) - 10} arquivo(s).")
    return "\n".join(message)
