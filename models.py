"""Tipos de dados e utilitários de resumo usados pela aplicação."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


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
    if summary.failures:
        failed_names = "\n".join(f"- {failure.source.name}" for failure in summary.failures[:10])
        message.extend(["", f"Falhas nesta execução:\n{failed_names}"])
        if len(summary.failures) > 10:
            message.append(f"... e mais {len(summary.failures) - 10} arquivo(s).")
    return "\n".join(message)
