"""Constantes compartilhadas da aplicação."""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "NexoJuris - Conversor"
APP_VERSION = "1.4.3"
CURRENT_TERMS_VERSION = "1.0"
MAX_PAGE_COUNT = 1_000
DEFAULT_MAX_CHUNK_CHARACTERS = 60_000

# Orçamento operacional por documento. Esses limites também protegem chamadas
# diretas ao conversor, não apenas a fila da interface.
MAX_PDF_FILE_SIZE_BYTES = 512 * 1024 * 1024
# Limita arquivos efetivamente extraídos. Referências repetidas no PDF não
# representam ativos distintos e, portanto, não entram neste orçamento.
MAX_IMAGES_PER_DOCUMENT = 25_000
MAX_EXTRACTED_ASSET_BYTES = 512 * 1024 * 1024
MAX_CONVERSION_MEMORY_BYTES = 1536 * 1024 * 1024
MEMORY_RESERVATION_PER_WORKER_BYTES = 1024 * 1024 * 1024
MAX_CONVERSION_SECONDS = 30 * 60
MIN_FREE_DISK_BYTES = 512 * 1024 * 1024
DISK_SPACE_SOURCE_MULTIPLIER = 3

# Limites defensivos da ponte. Todos ficam acima dos máximos oferecidos pela UI.
MIN_RENDER_DPI = 36
MAX_RENDER_DPI = 300
MAX_RENDER_PIXEL_AREA = 25_000_000
MAX_SNIPPET_AREA_POINTS = 5_000_000
MAX_PDF_COORDINATE = 100_000
MAX_ANNOTATIONS_PER_OPERATION = 2_000
MAX_STROKES_PER_ANNOTATION = 64
MAX_POINTS_PER_STROKE = 10_000
MAX_STROKE_POINTS_PER_OPERATION = 100_000
MAX_STROKE_WIDTH = 72.0
MAX_ANNOTATION_TEXT_CHARACTERS = 100_000
MAX_ANNOTATION_FONT_SIZE = 72.0
MAX_TRANSLATION_CHARACTERS = 100_000
TRANSLATION_CHUNK_CHARACTERS = 4_500
MAX_TTS_CHARACTERS = 50_000
TTS_CHUNK_CHARACTERS = 5_000

# Resiliência dos serviços externos opcionais. O prazo é global por ação,
# incluindo todas as tentativas e todos os blocos do texto.
TRANSLATION_TIMEOUT_SECONDS = 30.0
TTS_TIMEOUT_SECONDS = 45.0
ONLINE_SERVICE_MAX_ATTEMPTS = 3
ONLINE_SERVICE_BACKOFF_SECONDS = 0.35
ONLINE_SERVICE_CIRCUIT_FAILURE_THRESHOLD = 3
ONLINE_SERVICE_CIRCUIT_RESET_SECONDS = 60.0


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


def user_data_root() -> Path:
    """Retorna a pasta persistente e gravável do usuário, independente do executável."""
    override = os.environ.get("NEXOJURIS_DATA_DIR", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
    if local_app_data:
        return Path(local_app_data).resolve() / "NexoJuris" / "Conversor"
    return Path.home().resolve() / ".local" / "share" / "NexoJuris" / "Conversor"


DEFAULT_OUTPUT_DIR = application_root() / "PDFs Convertidos"
