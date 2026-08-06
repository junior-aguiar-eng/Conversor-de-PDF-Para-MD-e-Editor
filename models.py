"""Tipos de dados usados pela aplicação."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


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
