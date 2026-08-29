"""Constantes compartilhadas da aplicação."""

from __future__ import annotations

import sys
from pathlib import Path

APP_NAME = "NexoJuris - Conversor"
APP_VERSION = "1.3.2"
CURRENT_TERMS_VERSION = "1.0"
MAX_PAGE_COUNT = 1_000
DEFAULT_MAX_CHUNK_CHARACTERS = 60_000


def application_root() -> Path:
    """Retorna a pasta-base da aplicação (onde fica o .exe ou a raiz do código)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def resource_root() -> Path:
    """Retorna a pasta onde estão os recursos estáticos (web, assets),
    mesmo quando empacotado pelo PyInstaller (_MEIPASS)."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS).resolve()
    return Path(__file__).resolve().parent


DEFAULT_OUTPUT_DIR = application_root() / "PDFs Convertidos"
