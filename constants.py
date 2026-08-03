"""Constantes compartilhadas da aplicação."""

from __future__ import annotations

import sys
from pathlib import Path

APP_NAME = "Boni - Conversor de PDF para Markdown"
MAX_PAGE_COUNT = 1_000
DEFAULT_MAX_CHUNK_CHARACTERS = 60_000


def application_root() -> Path:
    """Retorna a pasta-base do app, tanto no código-fonte quanto no executável."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


DEFAULT_OUTPUT_DIR = application_root() / "PDFs Convertidos"
