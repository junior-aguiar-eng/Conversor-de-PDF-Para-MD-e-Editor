"""Camada Bridge Python-JavaScript para a interface Chromium (PyWebView)."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import math
import mimetypes
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
import threading
import time
import traceback
import uuid
import webbrowser
from concurrent.futures import FIRST_COMPLETED, Future, ProcessPoolExecutor, ThreadPoolExecutor, wait
from concurrent.futures.process import BrokenProcessPool
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

import edge_tts
import fitz
from deep_translator import GoogleTranslator

from app_storage import data_directory
from constants import (
    APP_NAME,
    APP_VERSION,
    CURRENT_TERMS_VERSION,
    DEFAULT_MAX_CHUNK_CHARACTERS,
    DEFAULT_OUTPUT_DIR,
    DISK_SPACE_SOURCE_MULTIPLIER,
    MAX_ANNOTATION_FONT_SIZE,
    MAX_ANNOTATION_TEXT_CHARACTERS,
    MAX_ANNOTATIONS_PER_OPERATION,
    MAX_CONVERSION_MEMORY_BYTES,
    MAX_CONVERSION_SECONDS,
    MAX_EXTRACTED_ASSET_BYTES,
    MAX_IMAGES_PER_DOCUMENT,
    MAX_PAGE_COUNT,
    MAX_PDF_COORDINATE,
    MAX_PDF_FILE_SIZE_BYTES,
    MAX_POINTS_PER_STROKE,
    MAX_RENDER_DPI,
    MAX_RENDER_PIXEL_AREA,
    MAX_SNIPPET_AREA_POINTS,
    MAX_STROKE_POINTS_PER_OPERATION,
    MAX_STROKE_WIDTH,
    MAX_STROKES_PER_ANNOTATION,
    MAX_TRANSLATION_CHARACTERS,
    MAX_TTS_CHARACTERS,
    MEMORY_RESERVATION_PER_WORKER_BYTES,
    MIN_FREE_DISK_BYTES,
    MIN_RENDER_DPI,
    ONLINE_SERVICE_BACKOFF_SECONDS,
    ONLINE_SERVICE_CIRCUIT_FAILURE_THRESHOLD,
    ONLINE_SERVICE_CIRCUIT_RESET_SECONDS,
    ONLINE_SERVICE_MAX_ATTEMPTS,
    TRANSLATION_CHUNK_CHARACTERS,
    TRANSLATION_TIMEOUT_SECONDS,
    TTS_CHUNK_CHARACTERS,
    TTS_TIMEOUT_SECONDS,
    user_data_root,
)
from converter import (
    PdfMarkdownConverter,
    ResourceBudgetExceeded,
    available_memory_bytes,
    convert_worker,
    init_worker,
    process_rss_bytes,
    validate_runtime_dependencies,
)
from file_authorization import AuthorizedResourceRegistry, ResourceAccessError
from library_db import LibraryDatabase
from license_core import LicenseAccessError, LicenseState
from licensing import (
    LicenseRequiredError,
    activate_act4_license,
    get_license_status,
    require_software_activation,
)
from licensing import (
    activate_software as lic_activate_software,
)
from markdown_utils import HeadingProfile, SplitMode, reserve_batch_output_paths
from models import ConversionFailure, ConversionResult, OutputReservation, format_duration
from online_services import CircuitBreaker, ServiceCircuitOpen, ServiceOperationTimeout, call_with_resilience
from production_diagnostics import (
    build_diagnostic_report,
    diagnostic_status,
    write_diagnostic_report,
)

logger = logging.getLogger(__name__)


def _license_denial(error: LicenseRequiredError | LicenseAccessError, *, started: bool | None = None) -> dict[str, Any]:
    status = getattr(error, "status", None)
    state = status.state if status is not None else LicenseState.UNLICENSED
    result: dict[str, Any] = {
        "error": str(error),
        "error_code": "license_required" if status is None else state.value,
        "license_state": state.value,
        "machine_id": error.machine_id,
    }
    result["started" if started is not None else "ok"] = False
    return result

MAX_PARALLEL_WORKERS = 4
MIN_CHUNK_CHARACTERS = 1_000
CONVERSION_JOURNAL_PATH = data_directory() / "conversion-journal.json"

ANNOTATION_TYPES = {
    "ink",
    "drawing",
    "caneta",
    "highlight_pen",
    "caneta_marca_texto",
    "pincel_marca_texto",
    "highlight",
    "highlight_block",
    "marca_texto",
    "marca-texto",
    "text",
    "freetext",
    "texto",
}
STROKE_ANNOTATION_TYPES = {
    "ink",
    "drawing",
    "caneta",
    "highlight_pen",
    "caneta_marca_texto",
    "pincel_marca_texto",
}
TEXT_ANNOTATION_TYPES = {"text", "freetext", "texto"}


def format_file_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} B"
    if size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    return f"{size_bytes / (1024 * 1024):.1f} MB"


def _finite_number(value: Any, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{label} deve ser numérico.") from error
    if not math.isfinite(number):
        raise ValueError(f"{label} deve ser um número finito.")
    return number


def _validated_dpi(value: Any) -> int:
    dpi = _finite_number(value, "DPI")
    if not dpi.is_integer() or not (MIN_RENDER_DPI <= dpi <= MAX_RENDER_DPI):
        raise ValueError(f"DPI deve estar entre {MIN_RENDER_DPI} e {MAX_RENDER_DPI}.")
    return int(dpi)


def _validate_pixel_budget(rect: fitz.Rect, dpi: int) -> None:
    estimated_pixels = (rect.width * dpi / 72.0) * (rect.height * dpi / 72.0)
    if estimated_pixels > MAX_RENDER_PIXEL_AREA:
        raise ValueError(
            f"A renderização excede o limite de {MAX_RENDER_PIXEL_AREA:,} pixels.".replace(",", ".")
        )


def _validated_clip_rect(raw_rect: Any, page_rect: fitz.Rect) -> fitz.Rect:
    if not isinstance(raw_rect, (list, tuple)) or len(raw_rect) != 4:
        raise ValueError("A área de recorte deve conter exatamente quatro coordenadas.")
    coordinates = [_finite_number(value, "Coordenada do recorte") for value in raw_rect]
    if any(abs(value) > MAX_PDF_COORDINATE for value in coordinates):
        raise ValueError(f"Coordenadas do recorte excedem o limite de {MAX_PDF_COORDINATE} pontos.")
    x0, y0, x1, y1 = coordinates
    normalized = fitz.Rect(min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
    clipped = normalized & page_rect
    if clipped.is_empty or clipped.width <= 0 or clipped.height <= 0:
        raise ValueError("A área de recorte não intersecta a página.")
    if clipped.width * clipped.height > MAX_SNIPPET_AREA_POINTS:
        raise ValueError(
            f"A área de recorte excede o limite de {MAX_SNIPPET_AREA_POINTS:,} pontos quadrados.".replace(",", ".")
        )
    return clipped


def _split_text_chunks(text: str, max_characters: int) -> list[str]:
    """Divide texto sem descartar caracteres, preferindo limites de parágrafo e espaço."""
    if len(text) <= max_characters:
        return [text]
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + max_characters, len(text))
        if end < len(text):
            paragraph_break = text.rfind("\n", start + 1, end + 1)
            space_break = text.rfind(" ", start + 1, end + 1)
            split_at = max(paragraph_break, space_break)
            if split_at > start:
                end = split_at + 1
        chunks.append(text[start:end])
        start = end
    return chunks


def _split_translation_chunks(text: str, max_characters: int) -> list[tuple[str, str]]:
    """Separa conteúdo traduzível e preserva explicitamente o espaço entre blocos."""
    chunks: list[tuple[str, str]] = []
    start = 0
    while start < len(text):
        end = min(start + max_characters, len(text))
        separator = ""
        if end < len(text):
            paragraph_break = text.rfind("\n", start + 1, end + 1)
            space_break = text.rfind(" ", start + 1, end + 1)
            split_at = max(paragraph_break, space_break)
            if split_at > start:
                end = split_at
                separator_end = end
                while separator_end < len(text) and text[separator_end].isspace():
                    separator_end += 1
                separator = text[end:separator_end]
                chunks.append((text[start:end], separator))
                start = separator_end
                continue
        chunks.append((text[start:end], separator))
        start = end
    return chunks


def _validate_page_point(x: Any, y: Any, page_rect: fitz.Rect, label: str) -> tuple[float, float]:
    point_x = _finite_number(x, f"Coordenada X de {label}")
    point_y = _finite_number(y, f"Coordenada Y de {label}")
    if abs(point_x) > MAX_PDF_COORDINATE or abs(point_y) > MAX_PDF_COORDINATE:
        raise ValueError(f"Coordenadas de {label} excedem o limite de {MAX_PDF_COORDINATE} pontos.")
    tolerance = 1.0
    if not (
        page_rect.x0 - tolerance <= point_x <= page_rect.x1 + tolerance
        and page_rect.y0 - tolerance <= point_y <= page_rect.y1 + tolerance
    ):
        raise ValueError(f"Coordenadas de {label} estão fora da página.")
    return point_x, point_y


def _validate_annotation_payload(annotations: Any, doc: fitz.Document) -> list[dict[str, Any]]:
    if not isinstance(annotations, list):
        raise ValueError("A lista de anotações é inválida.")
    if len(annotations) > MAX_ANNOTATIONS_PER_OPERATION:
        raise ValueError(
            f"A operação excede o limite de {MAX_ANNOTATIONS_PER_OPERATION} anotações. "
            "Salve em mais de uma operação."
        )

    total_stroke_points = 0
    validated: list[dict[str, Any]] = []
    for index, item in enumerate(annotations, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"Anotação {index} deve ser um objeto.")
        try:
            page_number = int(item.get("page_number", 0))
        except (TypeError, ValueError) as error:
            raise ValueError(f"Página da anotação {index} é inválida.") from error
        if not (0 <= page_number < len(doc)):
            raise ValueError(f"Página da anotação {index} está fora do intervalo do documento.")

        normalized_item = dict(item)
        annotation_type = str(item.get("type", "")).lower()
        if annotation_type not in ANNOTATION_TYPES:
            raise ValueError(f"Tipo da anotação {index} não é suportado.")
        page_rect = doc[page_number].rect

        if annotation_type in STROKE_ANNOTATION_TYPES:
            strokes = item.get("strokes")
            if not isinstance(strokes, list) or not strokes:
                raise ValueError(f"Anotação {index} não contém strokes válidos.")
            if len(strokes) > MAX_STROKES_PER_ANNOTATION:
                raise ValueError(
                    f"Anotação {index} excede o limite de {MAX_STROKES_PER_ANNOTATION} strokes."
                )
            for stroke_index, stroke in enumerate(strokes, start=1):
                if not isinstance(stroke, list) or not stroke:
                    raise ValueError(f"Stroke {stroke_index} da anotação {index} é inválido.")
                if len(stroke) > MAX_POINTS_PER_STROKE:
                    raise ValueError(
                        f"Stroke {stroke_index} excede o limite de {MAX_POINTS_PER_STROKE} pontos."
                    )
                for point in stroke:
                    if not isinstance(point, (list, tuple)) or len(point) != 2:
                        raise ValueError(f"Ponto de stroke da anotação {index} é inválido.")
                    _validate_page_point(point[0], point[1], page_rect, f"stroke da anotação {index}")
                total_stroke_points += len(stroke)
                if total_stroke_points > MAX_STROKE_POINTS_PER_OPERATION:
                    raise ValueError(
                        f"A operação excede o limite de {MAX_STROKE_POINTS_PER_OPERATION} pontos de stroke."
                    )
            width = _finite_number(item.get("width", 2.0), f"Espessura da anotação {index}")
            if not (0 < width <= MAX_STROKE_WIDTH):
                raise ValueError(f"Espessura da anotação deve estar entre 0 e {MAX_STROKE_WIDTH:g}.")

        elif annotation_type in {"highlight", "highlight_block", "marca_texto", "marca-texto"}:
            clipped_rect = _validated_clip_rect(item.get("rect"), page_rect)
            normalized_item["rect"] = [clipped_rect.x0, clipped_rect.y0, clipped_rect.x1, clipped_rect.y1]

        elif annotation_type in TEXT_ANNOTATION_TYPES:
            text = str(item.get("text", ""))
            if len(text) > MAX_ANNOTATION_TEXT_CHARACTERS:
                raise ValueError(
                    f"O texto da anotação {index} excede {MAX_ANNOTATION_TEXT_CHARACTERS} caracteres."
                )
            fontsize = _finite_number(
                item.get("fontsize", item.get("size", 14.0)),
                f"Tamanho da fonte da anotação {index}",
            )
            if not (0 < fontsize <= MAX_ANNOTATION_FONT_SIZE):
                raise ValueError(f"A fonte da anotação deve estar entre 0 e {MAX_ANNOTATION_FONT_SIZE:g} pontos.")
            rect = item.get("rect")
            if rect is not None:
                clipped_rect = _validated_clip_rect(rect, page_rect)
                normalized_item["rect"] = [clipped_rect.x0, clipped_rect.y0, clipped_rect.x1, clipped_rect.y1]
            normalized_rect = normalized_item.get("rect")
            x = item.get("x", normalized_rect[0] if normalized_rect else 50.0)
            y = item.get("y", normalized_rect[1] if normalized_rect else 50.0)
            _validate_page_point(x, y, page_rect, f"texto da anotação {index}")
            for dimension_name in ("width", "height"):
                if dimension_name in item:
                    dimension = _finite_number(item[dimension_name], f"{dimension_name} da anotação {index}")
                    if not (0 < dimension <= MAX_PDF_COORDINATE):
                        raise ValueError(f"{dimension_name} da anotação {index} é inválida.")

        validated.append(normalized_item)
    return validated


def _parse_color(c: Any, default: tuple[float, float, float] = (1.0, 0.0, 0.0)) -> tuple[float, float, float]:
    """Converte cor em lista [r,g,b] (0..1) ou string hexadecimal (#RRGGBB) para tupla float."""
    if isinstance(c, (list, tuple)) and len(c) >= 3:
        try:
            r, g, b = float(c[0]), float(c[1]), float(c[2])
            if r > 1.0 or g > 1.0 or b > 1.0:
                return (r / 255.0, g / 255.0, b / 255.0)
            return (r, g, b)
        except ValueError, TypeError:
            return default
    if isinstance(c, str) and c.startswith("#"):
        hex_str = c.lstrip("#")
        if len(hex_str) == 6:
            try:
                return (
                    int(hex_str[0:2], 16) / 255.0,
                    int(hex_str[2:4], 16) / 255.0,
                    int(hex_str[4:6], 16) / 255.0,
                )
            except ValueError:
                return default
        if len(hex_str) == 3:
            try:
                return (
                    int(hex_str[0] * 2, 16) / 255.0,
                    int(hex_str[1] * 2, 16) / 255.0,
                    int(hex_str[2] * 2, 16) / 255.0,
                )
            except ValueError:
                return default
    return default


def _fit_textbox_rect(
    page: fitz.Page,
    rect: fitz.Rect,
    text: str,
    *,
    fontname: str,
    fontsize: float,
) -> fitz.Rect | None:
    """Expande a caixa verticalmente, dentro da página, até o texto caber."""
    candidate = fitz.Rect(rect)
    shape = page.new_shape()
    remaining = shape.insert_textbox(candidate, text, fontname=fontname, fontsize=fontsize, render_mode=0, align=0)
    if remaining >= 0:
        return candidate

    candidate.y1 = min(page.rect.y1, candidate.y1 - remaining)
    if candidate.y1 <= rect.y1:
        return None

    shape = page.new_shape()
    remaining = shape.insert_textbox(candidate, text, fontname=fontname, fontsize=fontsize, render_mode=0, align=0)
    return candidate if remaining >= 0 else None


def _safe_close(doc: fitz.Document | None) -> None:
    """Fecha o documento fitz com segurança, sem disparar ValueError se já estiver fechado."""
    if doc is not None:
        try:
            if not doc.is_closed:
                doc.close()
        except Exception:
            pass


def _pdf_backup_path(file_path: Path) -> Path:
    """Retorna o caminho estável do backup imediatamente anterior ao PDF."""
    return file_path.with_name(f"{file_path.stem}.nexojuris-backup{file_path.suffix}")


def _flush_file(file_path: Path) -> None:
    """Força a entrega dos bytes do arquivo ao sistema operacional antes da promoção."""
    with file_path.open("rb+") as stream:
        stream.flush()
        os.fsync(stream.fileno())


def _atomic_write_json(file_path: Path, payload: dict[str, Any]) -> None:
    file_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = file_path.with_name(f".{file_path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, file_path)
    finally:
        temporary.unlink(missing_ok=True)


def _validate_saved_pdf(file_path: Path, expected_page_count: int) -> None:
    """Recusa promover uma saída que o PyMuPDF não consiga reabrir integralmente."""
    with fitz.open(str(file_path)) as candidate:
        if not candidate.is_pdf or candidate.page_count != expected_page_count:
            raise RuntimeError("O PDF temporário não passou na validação de integridade.")


def _save_doc_safely(doc: fitz.Document, file_path: Path, **save_kwargs: Any) -> Path | None:
    """Salva em temporário e substitui atomicamente; ao sobrescrever, mantém backup recuperável."""
    target_path = file_path.resolve()
    target_path.parent.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex
    temp_file = target_path.with_name(f".{target_path.stem}.{token}.tmp.pdf")
    backup_temp = target_path.with_name(f".{target_path.stem}.{token}.backup.tmp.pdf")
    backup_path: Path | None = None
    try:
        doc_path = Path(doc.name).resolve() if (doc.name and Path(doc.name).is_file()) else None
        overwriting_original = doc_path == target_path
        expected_page_count = doc.page_count
        effective_save_kwargs = dict(save_kwargs)
        effective_save_kwargs.setdefault("encryption", fitz.PDF_ENCRYPT_KEEP)

        doc.save(str(temp_file), **effective_save_kwargs)
        _safe_close(doc)
        _validate_saved_pdf(temp_file, expected_page_count)
        _flush_file(temp_file)

        if overwriting_original:
            backup_path = _pdf_backup_path(target_path)
            shutil.copy2(target_path, backup_temp)
            _flush_file(backup_temp)
            os.replace(backup_temp, backup_path)

        os.replace(temp_file, target_path)
        return backup_path
    finally:
        temp_file.unlink(missing_ok=True)
        backup_temp.unlink(missing_ok=True)
        _safe_close(doc)


class BridgeApi:
    """API exposta para o JavaScript via window.pywebview.api."""

    def __init__(self, conversion_journal_path: Path | str | None = None) -> None:
        self._window: Any = None
        self.cancel_requested = threading.Event()
        self.resume_processing = threading.Event()
        self.resume_processing.set()
        self.is_paused = False
        self.is_converting = False
        self.conversion_state = "stopped"
        self._conversion_state_lock = threading.Lock()
        self._active_checkpoint_dirs: set[Path] = set()
        self._active_checkpoint_lock = threading.Lock()
        self._shutdown_requested = False
        self._batch_start_time = 0.0
        self._conversion_thread: threading.Thread | None = None
        self._conversion_finished = threading.Event()
        self._conversion_finished.set()
        self._journal_path = (
            Path(conversion_journal_path).resolve()
            if conversion_journal_path is not None
            else CONVERSION_JOURNAL_PATH.resolve()
        )
        self._journal_lock = threading.Lock()
        self._recovery_token: str | None = None
        self._recovery_payload: dict[str, Any] | None = None
        self._resuming_interrupted = False
        self._close_lock = threading.Lock()
        self._services_shutdown = False
        self._pdf_passwords: dict[str, str] = {}
        try:
            self._library = LibraryDatabase()
            self._library_status = dict(self._library.recovery_status)
            self._library_status["persistent"] = True
        except (OSError, sqlite3.Error) as error:
            logger.error("Acervo persistente indisponível; iniciando armazenamento temporário: %s", error)
            fallback_path = Path(tempfile.gettempdir()) / "NexoJuris" / f"acervo-temporario-{os.getpid()}.db"
            self._library = LibraryDatabase(fallback_path)
            self._library_status = {
                "state": "degraded",
                "persistent": False,
                "database_path": str(fallback_path),
                "error": str(error),
                "recovered_from": None,
                "quarantined_path": None,
            }
        self._resources = AuthorizedResourceRegistry()
        self._default_output_dir = DEFAULT_OUTPUT_DIR
        try:
            self._default_output_resource = self._register_directory(self._default_output_dir, "default_output")
        except OSError:
            self._default_output_dir = user_data_root() / "PDFs Convertidos"
            self._default_output_resource = self._register_directory(self._default_output_dir, "default_output_fallback")
        self._indexing_cancel_events: dict[str, threading.Event] = {}
        self._indexing_pause_events: dict[str, threading.Event] = {}
        self._indexing_threads: dict[str, threading.Thread] = {}
        self._indexing_lock = threading.Lock()
        self._page_indexing_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="PageIndex")
        self._pending_page_indexes: set[tuple[str, int]] = set()
        self._page_indexing_lock = threading.Lock()
        self._online_service_breakers = {
            "translation": CircuitBreaker(
                ONLINE_SERVICE_CIRCUIT_FAILURE_THRESHOLD,
                ONLINE_SERVICE_CIRCUIT_RESET_SECONDS,
            ),
            "tts": CircuitBreaker(
                ONLINE_SERVICE_CIRCUIT_FAILURE_THRESHOLD,
                ONLINE_SERVICE_CIRCUIT_RESET_SECONDS,
            ),
        }

    def _register_pdf(self, path: str | Path, origin: str) -> dict[str, Any]:
        resolved = Path(path).expanduser().resolve()
        if not resolved.is_file() or resolved.suffix.lower() != ".pdf":
            raise ResourceAccessError("O recurso selecionado não é um PDF válido.")
        resource = self._resources.register(
            resolved,
            kind="pdf",
            origin=origin,
            capabilities={"read", "write", "convert", "open"},
        )
        size = resolved.stat().st_size
        return {
            "file_id": resource.resource_id,
            "path": str(resolved),
            "name": resolved.name,
            "size": size,
            "size_formatted": format_file_size(size),
        }

    def _register_markdown(self, path: str | Path, origin: str) -> dict[str, Any]:
        resolved = Path(path).expanduser().resolve()
        if not resolved.is_file() or resolved.suffix.lower() not in {".md", ".markdown"}:
            raise ResourceAccessError("O recurso selecionado não é um Markdown válido.")
        resource = self._resources.register(
            resolved,
            kind="markdown",
            origin=origin,
            capabilities={"read", "open", "asset_read"},
        )
        return {
            "markdown_id": resource.resource_id,
            "markdown_path": str(resolved),
            "name": resolved.name,
        }

    def _register_pdf_destination(self, path: str | Path, origin: str) -> dict[str, Any]:
        """Registra um destino PDF escolhido por diálogo nativo, sem exigir que já exista."""
        resolved = Path(path).expanduser().resolve()
        if resolved.suffix.lower() != ".pdf":
            raise ResourceAccessError("O destino escolhido deve usar a extensão .pdf.")
        resource = self._resources.register(
            resolved,
            kind="pdf_output",
            origin=origin,
            capabilities={"write"},
        )
        return {"output_file_id": resource.resource_id, "path": str(resolved), "name": resolved.name}

    def _register_library_entry(self, path: str | Path, origin: str) -> str:
        resource = self._resources.register(
            path,
            kind="library_entry",
            origin=origin,
            capabilities={"relocate", "remove"},
        )
        return resource.resource_id

    def _register_directory(self, path: str | Path, origin: str) -> dict[str, Any]:
        resolved = Path(path).expanduser().resolve()
        resolved.mkdir(parents=True, exist_ok=True)
        resource = self._resources.register(
            resolved,
            kind="directory",
            origin=origin,
            capabilities={"write", "open"},
        )
        return {"directory_id": resource.resource_id, "path": str(resolved)}

    def _resolve_pdf(self, file_id: str, capability: str = "read") -> Path:
        path = self._resources.resolve(file_id, kind="pdf", capability=capability)
        if not path.is_file() or path.suffix.lower() != ".pdf":
            raise ResourceAccessError("O PDF autorizado não está disponível.")
        return path

    def _resolve_markdown(self, markdown_id: str, capability: str = "read") -> Path:
        path = self._resources.resolve(markdown_id, kind="markdown", capability=capability)
        if not path.is_file() or path.suffix.lower() not in {".md", ".markdown"}:
            raise ResourceAccessError("O Markdown autorizado não está disponível.")
        return path

    def _resolve_directory(self, directory_id: str, capability: str = "write") -> Path:
        path = self._resources.resolve(directory_id, kind="directory", capability=capability)
        if not path.is_dir():
            raise ResourceAccessError("A pasta autorizada não está disponível.")
        return path

    def _resolve_pdf_destination(self, output_file_id: str) -> Path:
        path = self._resources.resolve(output_file_id, kind="pdf_output", capability="write")
        if path.suffix.lower() != ".pdf":
            raise ResourceAccessError("O destino autorizado não é um PDF.")
        return path

    def _resolve_library_entry(self, entry_id: str, capability: str) -> Path:
        return self._resources.resolve(entry_id, kind="library_entry", capability=capability)

    def _open_doc_with_auth(
        self, file_path: str | Path, password: str | None = None
    ) -> tuple[fitz.Document | None, str | None, bool]:
        """Abre o documento PDF autenticando caso esteja criptografado."""
        path = Path(file_path).resolve()
        if not path.is_file():
            return None, "Arquivo não encontrado.", False

        try:
            doc = fitz.open(str(path))
            if doc.is_encrypted:
                pw = password or self._pdf_passwords.get(str(path))
                if pw:
                    auth_res = doc.authenticate(pw)
                    if auth_res > 0:
                        self._pdf_passwords[str(path)] = pw
                        return doc, None, False
                doc.close()  # FECHAMENTO OBRIGATÓRIO (Impede travamento do arquivo no Windows)
                return None, "O documento PDF está protegido por senha.", True
            return doc, None, False
        except Exception as err:
            return None, f"Falha ao abrir PDF: {err}", False

    def set_window(self, window: Any) -> None:
        self._window = window

    def _emit(self, event_name: str, data: Any = None) -> None:
        """Envia um evento thread-safe para o frontend JavaScript."""
        if not self._window:
            return
        payload_json = json.dumps(data) if data is not None else "null"
        js_code = f"window.onBackendEvent && window.onBackendEvent({json.dumps(event_name)}, {payload_json});"
        try:
            self._window.evaluate_js(js_code)
        except Exception as error:
            logger.debug(f"Erro ao emitir evento {event_name}: {error}")

    def _set_conversion_state(self, state: str, **details: Any) -> None:
        if state not in {"running", "pausing", "paused", "stopping", "stopped", "completed", "failed"}:
            raise ValueError(f"Estado de conversão inválido: {state}")
        with self._conversion_state_lock:
            self.conversion_state = state
            self.is_paused = state in {"pausing", "paused"}
        self._emit("conversion_state", {"state": state, **details})

    @staticmethod
    def _write_conversion_control(checkpoint_dir: Path, action: str) -> None:
        _atomic_write_json(checkpoint_dir / "control.json", {"action": action})

    def _control_active_conversions(self, action: str) -> None:
        with self._active_checkpoint_lock:
            checkpoints = tuple(self._active_checkpoint_dirs)
        for checkpoint in checkpoints:
            try:
                self._write_conversion_control(checkpoint, action)
            except OSError as error:
                logger.warning("Falha ao controlar conversão em %s: %s", checkpoint, error)

    def _sync_active_pause_status(self) -> None:
        if self.conversion_state != "pausing":
            return
        with self._active_checkpoint_lock:
            checkpoints = tuple(self._active_checkpoint_dirs)
        if not checkpoints:
            return
        pages: list[int] = []
        for checkpoint in checkpoints:
            try:
                status = json.loads((checkpoint / "control-status.json").read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError, TypeError):
                return
            if status.get("state") != "paused":
                return
            pages.append(int(status.get("page_number", 0)))
        page = pages[0] if len(pages) == 1 else min(pages)
        self._set_conversion_state("paused", page_number=page, active_documents=len(pages))
        message = (
            f"Pausado na página {page}."
            if len(pages) == 1
            else f"{len(pages)} documentos pausados em checkpoints de página."
        )
        self._emit("status", {"message": message})

    def _persisted_markdowns(self, limit: int = 40) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for item in reversed(self._library.get_recent_markdowns(limit)):
            try:
                markdown = self._register_markdown(item["markdown_path"], "persisted_library")
            except (OSError, ResourceAccessError):
                continue
            results.append(
                {
                    "name": item["file_name"],
                    "markdown_path": markdown["markdown_path"],
                    "markdown_id": markdown["markdown_id"],
                    "index_status": item.get("index_status", "not_indexed"),
                    "index_error": item.get("index_error", ""),
                }
            )
        return results

    def get_app_info(self) -> dict[str, Any]:
        """Retorna metadados do aplicativo e caminhos padrão."""
        return {
            "app_name": APP_NAME,
            "app_version": APP_VERSION,
            "default_output_dir": str(self._default_output_dir),
            "default_output_dir_id": self._default_output_resource["directory_id"],
            "default_chunk_limit": DEFAULT_MAX_CHUNK_CHARACTERS,
            "max_page_count": MAX_PAGE_COUNT,
            "max_pdf_file_size_bytes": MAX_PDF_FILE_SIZE_BYTES,
            "max_images_per_document": MAX_IMAGES_PER_DOCUMENT,
            "max_extracted_asset_bytes": MAX_EXTRACTED_ASSET_BYTES,
            "max_conversion_memory_bytes": MAX_CONVERSION_MEMORY_BYTES,
            "max_conversion_seconds": MAX_CONVERSION_SECONDS,
            "min_free_disk_bytes": MIN_FREE_DISK_BYTES,
            "library_storage": dict(self._library_status),
            "diagnostics": diagnostic_status(),
            "conversion_state": self.conversion_state,
            "recent_markdowns": self._persisted_markdowns(),
        }

    def get_diagnostic_status(self) -> dict[str, Any]:
        """Retorna metadados operacionais sem conteúdo dos documentos."""
        return {
            "ok": True,
            **diagnostic_status(),
            "library_storage": dict(self._library_status),
            "online_services": self.get_online_services_status()["services"],
        }

    def export_diagnostic_report(self) -> dict[str, Any]:
        """Exporta relatório sanitizado para destino autorizado por diálogo nativo."""
        if not self._window:
            return {"ok": False, "error": "A janela do aplicativo não está disponível."}
        try:
            import webview

            stamp = time.strftime("%Y%m%d-%H%M%S")
            result = self._window.create_file_dialog(
                webview.SAVE_DIALOG,
                save_filename=f"NexoJuris-Diagnostico-{stamp}.txt",
                file_types=("Relatório de diagnóstico (*.txt)",),
            )
            if not result:
                return {"ok": False, "cancelled": True}
            selected = result if isinstance(result, str) else result[0]
            destination = Path(selected)
            if destination.suffix.casefold() != ".txt":
                destination = destination.with_suffix(".txt")
            report = build_diagnostic_report(
                library_status=dict(self._library_status),
                online_services=self.get_online_services_status()["services"],
            )
            write_diagnostic_report(destination, report)
            logger.info("Relatório de diagnóstico exportado para %s", destination)
            return {
                "ok": True,
                "path": str(destination.resolve()),
                "message": "Relatório de diagnóstico exportado com sucesso.",
            }
        except Exception as error:
            logger.error("Falha ao exportar relatório de diagnóstico: %s", error, exc_info=True)
            return {"ok": False, "error": f"Não foi possível exportar o diagnóstico: {error}"}

    def get_terms_acceptance_status(self) -> dict[str, Any]:
        """Verifica se o usuário já aceitou os termos de uso formalmente e se a versão vigente confere (Item 18)."""
        try:
            status = self._library.get_terms_status()
            stored_version = status.get("terms_version") or "1.0"
            needs_reacceptance = False
            if status.get("accepted"):
                if stored_version != CURRENT_TERMS_VERSION:
                    needs_reacceptance = True

            return {
                "accepted": bool(status.get("accepted")) and not needs_reacceptance,
                "accepted_at": status.get("accepted_at"),
                "terms_version": stored_version,
                "current_terms_version": CURRENT_TERMS_VERSION,
                "needs_reacceptance": needs_reacceptance,
            }
        except Exception as error:
            logger.error(f"Erro ao verificar status dos termos: {error}")
            return {
                "accepted": False,
                "terms_version": None,
                "current_terms_version": CURRENT_TERMS_VERSION,
                "needs_reacceptance": True,
            }

    def accept_terms(self, terms_version: str = CURRENT_TERMS_VERSION) -> dict[str, Any]:
        """Registra o aceite formal e irrevogável dos termos de uso (Item 18)."""
        try:
            version_to_save = terms_version or CURRENT_TERMS_VERSION
            self._library.save_terms_acceptance(version_to_save)
            return {"ok": True, "terms_version": version_to_save}
        except Exception as error:
            logger.error(f"Erro ao registrar aceite dos termos: {error}")
            return {"ok": False, "error": str(error)}

    def get_license_info(self) -> dict[str, Any]:
        """Retorna o estado completo, preservando o booleano consumido pela UI atual."""
        status = get_license_status()
        return {"is_activated": status.allows("converter"), **status.to_mapping()}

    def activate_software(self, key: str) -> dict[str, Any]:
        """Processa a chave de ativação fornecida pelo usuário e desbloqueia o software."""
        result = lic_activate_software(key)
        if result["ok"]:
            self._emit("toast", {"type": "success", "message": result["message"]})
        return result

    def import_license_file(self) -> dict[str, Any]:
        """Seleciona e importa uma licença ACT4 sem expor o caminho ao renderer."""
        if not self._window:
            return {"ok": False, "cancelled": True, "error": "Janela do aplicativo indisponível."}
        import webview

        try:
            result = self._window.create_file_dialog(
                webview.OPEN_DIALOG,
                allow_multiple=False,
                file_types=("Licença NexoJuris (*.nxjlic)",),
            )
            if not result:
                return {"ok": False, "cancelled": True}
            license_path = Path(result[0]).resolve()
            if license_path.suffix.lower() != ".nxjlic" or not license_path.is_file():
                return {"ok": False, "error": "Selecione um arquivo de licença .nxjlic válido."}
            if license_path.stat().st_size > 65_536:
                return {"ok": False, "error": "O arquivo de licença excede o limite de 64 KB."}
            response = activate_act4_license(license_path.read_bytes())
            if response.get("ok"):
                self._emit("toast", {"type": "success", "message": response["message"]})
            return response
        except OSError as error:
            logger.warning("Falha ao importar licença ACT4: %s", error)
            return {"ok": False, "error": "Não foi possível ler o arquivo de licença selecionado."}
        except Exception:
            logger.exception("Falha inesperada ao importar licença ACT4")
            return {"ok": False, "error": "Falha inesperada ao importar a licença selecionada."}

    def verify_license_now(self) -> dict[str, Any]:
        """Refaz a verificação local; a consulta remota será acoplada pelo serviço online."""
        status = get_license_status()
        if status.state == LicenseState.ONLINE_CHECK_REQUIRED:
            message = (
                "A licença local foi verificada, mas é necessário conectar-se à internet "
                "para renovar o prazo de uso offline."
            )
        elif status.can_use_protected_features:
            message = "Licença verificada neste computador."
        else:
            message = status.message
        return {
            **status.to_mapping(),
            "ok": status.can_use_protected_features,
            "online_attempted": False,
            "message": message,
        }

    def validate_environment(self) -> dict[str, Any]:
        """Verifica se o ambiente possui as dependências necessárias."""
        try:
            validate_runtime_dependencies()
            return {"ok": True, "error": None}
        except RuntimeError as error:
            return {"ok": False, "error": str(error)}

    def choose_files(self) -> list[dict[str, Any]]:
        """Abre o diálogo nativo do Windows para selecionar múltiplos PDFs."""
        if not self._window:
            return []
        import webview

        file_types = ("Arquivos PDF (*.pdf)", "Todos os arquivos (*.*)")
        try:
            result = self._window.create_file_dialog(
                webview.OPEN_DIALOG,
                allow_multiple=True,
                file_types=file_types,
            )
            if not result:
                return []
            return self._process_file_paths(list(result), origin="native_dialog")
        except Exception as error:
            self._emit("toast", {"type": "error", "message": f"Falha ao abrir diálogo: {error}"})
            return []

    def choose_output_directory(self) -> dict[str, Any] | None:
        """Abre o diálogo nativo para seleção da pasta de saída."""
        if not self._window:
            return None
        import webview

        try:
            result = self._window.create_file_dialog(webview.FOLDER_DIALOG)
            if result and len(result) > 0:
                return self._register_directory(result[0], "native_output_dialog")
            return None
        except Exception as error:
            self._emit("toast", {"type": "error", "message": f"Falha ao selecionar pasta: {error}"})
            return None

    def choose_pdf_save_destination(self, file_id: str, suffix: str = "copia") -> dict[str, Any] | None:
        """Autoriza um destino de cópia exclusivamente por meio do diálogo nativo de salvamento."""
        if not self._window:
            return None
        try:
            source = self._resolve_pdf(file_id, "read")
        except ResourceAccessError as error:
            return {"ok": False, "error": str(error)}

        safe_suffix = re.sub(r"[^0-9A-Za-z_-]+", "-", suffix or "copia").strip("-") or "copia"
        suggested_name = f"{source.stem}-{safe_suffix}.pdf"
        try:
            import webview

            result = self._window.create_file_dialog(
                webview.SAVE_DIALOG,
                save_filename=suggested_name,
                file_types=("Arquivos PDF (*.pdf)",),
            )
            if not result:
                return None
            selected = result if isinstance(result, str) else result[0]
            destination = self._register_pdf_destination(selected, "native_save_dialog")
            return {"ok": True, **destination}
        except Exception as error:
            return {"ok": False, "error": f"Falha ao selecionar destino: {error}"}

    def _process_file_paths(self, paths: list[str], *, origin: str) -> list[dict[str, Any]]:
        """Valida PDFs de uma origem de ingresso antes de conceder IDs opacos."""
        file_entries: list[dict[str, Any]] = []
        for raw_path in paths:
            try:
                file_entries.append(self._register_pdf(raw_path, origin))
            except (OSError, ResourceAccessError):
                continue
        return file_entries

    def register_dropped_files(self, paths: list[str]) -> list[dict[str, Any]]:
        """Confirma nativamente o drop antes de conceder autoridade sobre caminhos do renderer."""
        if not self._window or not isinstance(paths, list) or len(paths) > 100:
            return []
        candidates: list[str] = []
        seen: set[str] = set()
        for raw_path in paths:
            try:
                path = Path(raw_path).expanduser().resolve()
                key = str(path).casefold()
                if path.is_file() and path.suffix.lower() == ".pdf" and key not in seen:
                    candidates.append(str(path))
                    seen.add(key)
            except (OSError, TypeError, ValueError):
                continue
        if not candidates:
            return []
        names = "\n".join(f"• {Path(path).name}" for path in candidates[:10])
        remainder = len(candidates) - 10
        if remainder > 0:
            names += f"\n• e mais {remainder} arquivo(s)"
        try:
            confirmed = self._window.create_confirmation_dialog(
                "Autorizar PDFs arrastados",
                f"Deseja conceder acesso a {len(candidates)} PDF(s)?\n\n{names}",
            )
        except Exception as error:
            logger.debug(f"Falha ao confirmar arquivos arrastados: {error}")
            return []
        if not confirmed:
            return []
        return self._process_file_paths(candidates, origin="confirmed_drag_drop")

    @staticmethod
    def _checkpoint_dir(reservation: OutputReservation) -> Path:
        return reservation.markdown_path.with_name(f".{reservation.markdown_path.name}.nexojuris-checkpoint")

    def _read_conversion_journal(self) -> dict[str, Any] | None:
        with self._journal_lock:
            try:
                payload = json.loads(self._journal_path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                return None
            except (OSError, json.JSONDecodeError, TypeError, ValueError) as error:
                logger.warning(f"Journal de conversão inválido: {error}")
                return None
        return payload if isinstance(payload, dict) and payload.get("version") == 1 else None

    def _write_conversion_journal(
        self,
        files: list[Path],
        output_dir: Path,
        split_output: bool,
        max_chunk_characters: int,
        heading_profile: HeadingProfile,
        split_mode: SplitMode,
        page_numbers_by_file: list[tuple[int, ...] | None],
        max_workers: int | None,
        reservations: list[OutputReservation],
    ) -> None:
        entries = []
        for source, page_numbers, reservation in zip(files, page_numbers_by_file, reservations, strict=True):
            stat = source.stat()
            entries.append(
                {
                    "source": str(source),
                    "source_size": stat.st_size,
                    "source_mtime_ns": stat.st_mtime_ns,
                    "page_numbers": list(page_numbers) if page_numbers is not None else None,
                    "status": "pending",
                    "reservation": {
                        "markdown_path": str(reservation.markdown_path),
                        "assets_dir": str(reservation.assets_dir),
                        "chunks_dir": str(reservation.chunks_dir),
                    },
                }
            )
        payload = {
            "version": 1,
            "state": "running",
            "created_at": time.time(),
            "output_dir": str(output_dir),
            "split_output": split_output,
            "split_mode": split_mode,
            "max_chunk_characters": max_chunk_characters,
            "heading_profile": heading_profile,
            "max_workers": max_workers,
            "files": entries,
        }
        with self._journal_lock:
            _atomic_write_json(self._journal_path, payload)

    def _mark_journal_file_finished(self, source: Path, status: str) -> None:
        with self._journal_lock:
            try:
                payload = json.loads(self._journal_path.read_text(encoding="utf-8"))
            except (FileNotFoundError, OSError, json.JSONDecodeError, TypeError):
                return
            for entry in payload.get("files", []):
                if entry.get("source") == str(source.resolve()):
                    entry["status"] = status
                    _atomic_write_json(self._journal_path, payload)
                    return

    def _clear_conversion_journal(self, *, remove_checkpoints: bool) -> None:
        payload = self._read_conversion_journal() if remove_checkpoints else None
        if payload:
            try:
                output_root = Path(payload.get("output_dir", "")).resolve()
            except (OSError, TypeError, ValueError):
                output_root = None
            for entry in payload.get("files", []):
                markdown_path = entry.get("reservation", {}).get("markdown_path")
                if not markdown_path or output_root is None:
                    continue
                try:
                    path = Path(markdown_path).resolve()
                except (OSError, TypeError, ValueError):
                    continue
                if path.parent != output_root:
                    logger.warning(f"Checkpoint fora do destino autorizado foi ignorado: {path}")
                    continue
                checkpoint = path.with_name(f".{path.name}.nexojuris-checkpoint")
                if checkpoint.parent == output_root:
                    shutil.rmtree(checkpoint, ignore_errors=True)
        with self._journal_lock:
            self._journal_path.unlink(missing_ok=True)

    def get_interrupted_conversion(self) -> dict[str, Any]:
        """Reconstrói uma fila interrompida sem reutilizar IDs antigos da interface."""
        if self.is_converting:
            return {"available": False, "reason": "conversion_active"}
        payload = self._read_conversion_journal()
        if not payload:
            return {"available": False}
        try:
            output_dir = Path(payload["output_dir"]).resolve()
            output_resource = self._register_directory(output_dir, "conversion_recovery")
            recovered_files: list[dict[str, Any]] = []
            files_payload: list[dict[str, Any]] = []
            missing_files: list[str] = []
            for entry in payload.get("files", []):
                reservation_data = entry.get("reservation", {})
                expected_output = Path(reservation_data.get("markdown_path", "")).resolve()
                expected_assets = Path(reservation_data.get("assets_dir", "")).resolve()
                expected_chunks = Path(reservation_data.get("chunks_dir", "")).resolve()
                if (
                    expected_output.parent != output_dir
                    or expected_assets.parent != output_dir / "images"
                    or expected_chunks.parent != output_dir
                ):
                    raise ResourceAccessError("O journal contém um destino fora da pasta autorizada.")
                self._cleanup_conversion_temps(
                    OutputReservation(expected_output, expected_assets, expected_chunks)
                )
                if entry.get("status") in {"completed", "failed"} or expected_output.is_file():
                    continue
                source = Path(entry.get("source", "")).resolve()
                try:
                    stat = source.stat()
                    if (
                        source.suffix.casefold() != ".pdf"
                        or stat.st_size != int(entry.get("source_size", -1))
                        or stat.st_mtime_ns != int(entry.get("source_mtime_ns", -1))
                    ):
                        raise OSError("arquivo alterado")
                    recovered = self._register_pdf(source, "conversion_recovery")
                except (OSError, ResourceAccessError, TypeError, ValueError):
                    missing_files.append(source.name or "PDF indisponível")
                    continue
                recovered_files.append(recovered)
                files_payload.append(
                    {"file_id": recovered["file_id"], "page_numbers": entry.get("page_numbers")}
                )
            if not files_payload:
                if not missing_files:
                    self._clear_conversion_journal(remove_checkpoints=True)
                    return {"available": False}
                token = uuid.uuid4().hex
                self._recovery_token = token
                self._recovery_payload = None
                return {
                    "available": True,
                    "can_resume": False,
                    "resume_token": token,
                    "files": [],
                    "missing_files": missing_files,
                }
            token = uuid.uuid4().hex
            resume_payload = {
                "files": files_payload,
                "output_directory_id": output_resource["directory_id"],
                "split_output": bool(payload.get("split_output", False)),
                "split_mode": payload.get("split_mode", "semantic"),
                "max_chunk_characters": payload.get("max_chunk_characters", DEFAULT_MAX_CHUNK_CHARACTERS),
                "heading_profile": payload.get("heading_profile", "jurisprudencia"),
                "max_workers": payload.get("max_workers"),
            }
            self._recovery_token = token
            self._recovery_payload = resume_payload
            return {
                "available": True,
                "can_resume": True,
                "resume_token": token,
                "files": recovered_files,
                "missing_files": missing_files,
                "output_dir": str(output_dir),
                "output_directory_id": output_resource["directory_id"],
                "split_output": resume_payload["split_output"],
                "split_mode": resume_payload["split_mode"],
                "max_chunk_characters": resume_payload["max_chunk_characters"],
                "heading_profile": resume_payload["heading_profile"],
            }
        except (KeyError, OSError, ResourceAccessError, TypeError, ValueError) as error:
            return {"available": False, "error": f"Não foi possível recuperar a fila: {error}"}

    def resume_interrupted_conversion(self, resume_token: str) -> dict[str, Any]:
        if not resume_token or resume_token != self._recovery_token or self._recovery_payload is None:
            return {"started": False, "error": "Token de retomada inválido ou expirado."}
        payload = self._recovery_payload
        self._resuming_interrupted = True
        try:
            result = self.start_conversion(payload)
        finally:
            self._resuming_interrupted = False
        if result.get("started"):
            self._recovery_token = None
            self._recovery_payload = None
        return result

    def discard_interrupted_conversion(self, resume_token: str) -> dict[str, Any]:
        if not resume_token or resume_token != self._recovery_token:
            return {"ok": False, "error": "Token de retomada inválido ou expirado."}
        self._clear_conversion_journal(remove_checkpoints=True)
        self._recovery_token = None
        self._recovery_payload = None
        return {"ok": True}

    def start_conversion(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Inicia a conversão em lote em uma thread em segundo plano."""
        if not isinstance(payload, dict):
            return {"started": False, "error": "Dados da conversão inválidos."}
        try:
            require_software_activation("converter")
        except (LicenseRequiredError, LicenseAccessError) as error:
            return _license_denial(error, started=False)

        if self.is_converting:
            return {"started": False, "error": "Uma conversão já está em andamento."}
        if self._journal_path.is_file() and not self._resuming_interrupted:
            return {
                "started": False,
                "error": "Há uma conversão interrompida aguardando retomada ou descarte.",
                "error_code": "interrupted_conversion_pending",
            }

        files_data = payload.get("files", [])
        if not isinstance(files_data, list) or not files_data:
            return {"started": False, "error": "Nenhum PDF selecionado."}
        if any(not isinstance(item, dict) for item in files_data):
            return {"started": False, "error": "A fila de PDFs é inválida."}

        output_directory_id = payload.get("output_directory_id", "")
        split_output = bool(payload.get("split_output", False))
        split_mode: SplitMode = payload.get("split_mode", "semantic")
        if split_mode not in {"semantic", "strict"}:
            return {"started": False, "error": "Modo de divisão inválido."}
        try:
            max_chunk_characters = int(payload.get("max_chunk_characters", DEFAULT_MAX_CHUNK_CHARACTERS))
        except (TypeError, ValueError):
            return {"started": False, "error": "O limite de caracteres deve ser um número inteiro."}
        heading_profile: HeadingProfile = payload.get("heading_profile", "jurisprudencia")
        if heading_profile not in {"jurisprudencia", "curso"}:
            return {"started": False, "error": "Perfil de títulos inválido."}
        requested_workers = payload.get("max_workers")
        if requested_workers is not None:
            try:
                requested_workers = int(requested_workers)
            except (TypeError, ValueError):
                return {"started": False, "error": "Número de workers inválido."}
            if not (1 <= requested_workers <= MAX_PARALLEL_WORKERS):
                return {
                    "started": False,
                    "error": f"O número de workers deve estar entre 1 e {MAX_PARALLEL_WORKERS}.",
                }

        if max_chunk_characters < MIN_CHUNK_CHARACTERS:
            formatted_limit = f"{MIN_CHUNK_CHARACTERS:,}".replace(",", ".")
            return {
                "started": False,
                "error": f"O limite das partes deve ter pelo menos {formatted_limit} caracteres.",
            }

        try:
            output_dir = self._resolve_directory(output_directory_id, "write")
            file_paths = [self._resolve_pdf(str(item.get("file_id", "")), "convert") for item in files_data]
            page_numbers_by_file: list[tuple[int, ...] | None] = []
            for item in files_data:
                raw_pages = item.get("page_numbers")
                if raw_pages is None:
                    page_numbers_by_file.append(None)
                    continue
                if not isinstance(raw_pages, list) or not raw_pages:
                    raise ResourceAccessError("Lista de páginas para reprocessamento inválida.")
                pages = tuple(dict.fromkeys(int(value) for value in raw_pages))
                if any(value < 1 or value > MAX_PAGE_COUNT for value in pages):
                    raise ResourceAccessError("Página de reprocessamento fora do limite permitido.")
                page_numbers_by_file.append(pages)
        except (OSError, ResourceAccessError) as error:
            return {"started": False, "error": str(error)}
        except (TypeError, ValueError):
            return {"started": False, "error": "Lista de páginas para reprocessamento inválida."}

        if len(file_paths) != len(files_data):
            return {"started": False, "error": "A fila contém recursos não autorizados."}

        try:
            self._validate_batch_budget(file_paths, output_dir)
            reservations = reserve_batch_output_paths(
                output_dir,
                file_paths,
                retry=any(pages is not None for pages in page_numbers_by_file),
            )
            self._write_conversion_journal(
                file_paths,
                output_dir,
                split_output,
                max_chunk_characters,
                heading_profile,
                split_mode,
                page_numbers_by_file,
                requested_workers,
                reservations,
            )
        except (OSError, ResourceBudgetExceeded) as error:
            return {"started": False, "error": str(error), "error_code": "resource_budget"}

        self.cancel_requested.clear()
        self.resume_processing.set()
        self.is_paused = False
        self.is_converting = True
        self._set_conversion_state("running")
        self._shutdown_requested = False
        self._conversion_finished.clear()
        self._batch_start_time = time.perf_counter()

        thread = threading.Thread(
            target=self._convert_in_background,
            args=(
                file_paths,
                output_dir,
                split_output,
                max_chunk_characters,
                heading_profile,
                split_mode,
                page_numbers_by_file,
                requested_workers,
                reservations,
            ),
            daemon=False,
            name="ConversionCoordinator",
        )
        self._conversion_thread = thread
        try:
            thread.start()
        except RuntimeError as error:
            self.is_converting = False
            self._set_conversion_state("failed", error=str(error))
            self._conversion_finished.set()
            self._clear_conversion_journal(remove_checkpoints=True)
            return {"started": False, "error": f"Não foi possível iniciar a conversão: {error}"}
        return {"started": True, "error": None}

    def retry_failed_pages(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Reprocessa somente as páginas explicitamente indicadas em uma nova saída transacional."""
        return self.start_conversion(
            {
                "files": [
                    {
                        "file_id": payload.get("file_id", ""),
                        "page_numbers": payload.get("page_numbers", []),
                    }
                ],
                "output_directory_id": payload.get("output_directory_id", ""),
                "split_output": bool(payload.get("split_output", False)),
                "split_mode": payload.get("split_mode", "semantic"),
                "max_chunk_characters": payload.get("max_chunk_characters", DEFAULT_MAX_CHUNK_CHARACTERS),
                "heading_profile": payload.get("heading_profile", "jurisprudencia"),
                "max_workers": payload.get("max_workers"),
            }
        )

    def toggle_pause(self) -> dict[str, Any]:
        """Pausa ou retoma o documento ativo no próximo checkpoint de página."""
        if not self.is_converting:
            return {"is_paused": False, "state": self.conversion_state}

        if self.is_paused:
            self._control_active_conversions("running")
            self.resume_processing.set()
            self._set_conversion_state("running")
            self._emit("status", {"message": "Conversão retomada."})
            return {"is_paused": False, "state": "running"}

        self.resume_processing.clear()
        self._set_conversion_state("pausing")
        self._control_active_conversions("pause")
        self._emit("status", {"message": "Pausa solicitada: concluindo a página corrente..."})
        return {"is_paused": True, "state": "pausing"}

    def request_stop(self) -> bool:
        """Solicita a interrupção imediata dos processos de conversão."""
        if not self.is_converting:
            return False
        self._set_conversion_state("stopping")
        self.cancel_requested.set()
        self._control_active_conversions("stop")
        self.resume_processing.set()
        self._emit("status", {"message": "Parada solicitada: interrompendo a extração ativa..."})
        return True

    def has_active_work(self) -> bool:
        with self._indexing_lock, self._page_indexing_lock:
            return bool(
                self.is_converting
                or any(thread.is_alive() for thread in self._indexing_threads.values())
                or self._pending_page_indexes
            )

    def shutdown_for_close(self, timeout_seconds: float = 15.0) -> bool:
        """Interrompe coordenadamente tarefas e só confirma quando nenhuma escrita continua ativa."""
        with self._close_lock:
            if self._services_shutdown:
                return True
            deadline = time.monotonic() + max(0.1, timeout_seconds)
            self._shutdown_requested = True
            self.cancel_requested.set()
            self.resume_processing.set()

            with self._indexing_lock:
                for event in self._indexing_cancel_events.values():
                    event.set()
                for event in self._indexing_pause_events.values():
                    event.set()
                indexing_threads = list(self._indexing_threads.values())

            conversion_thread = self._conversion_thread
            if conversion_thread and conversion_thread is not threading.current_thread():
                conversion_thread.join(timeout=max(0.0, deadline - time.monotonic()))
            for thread in indexing_threads:
                if thread is not threading.current_thread():
                    thread.join(timeout=max(0.0, deadline - time.monotonic()))

            while time.monotonic() < deadline:
                with self._page_indexing_lock:
                    if not self._pending_page_indexes:
                        break
                time.sleep(0.05)

            conversion_alive = bool(conversion_thread and conversion_thread.is_alive())
            indexing_alive = any(thread.is_alive() for thread in indexing_threads)
            with self._page_indexing_lock:
                page_indexing_alive = bool(self._pending_page_indexes)
            if conversion_alive or indexing_alive or page_indexing_alive:
                return False

            self._page_indexing_executor.shutdown(wait=True, cancel_futures=True)
            self._services_shutdown = True
            return True

    def open_markdown(self, markdown_id: str) -> bool:
        """Abre o arquivo Markdown gerado no editor padrão do Windows."""
        try:
            path = self._resolve_markdown(markdown_id, "open")
        except ResourceAccessError as error:
            self._emit("toast", {"type": "error", "message": str(error)})
            return False
        try:
            os.startfile(path)  # type: ignore[attr-defined]
            return True
        except OSError as error:
            self._emit("toast", {"type": "error", "message": f"Erro ao abrir arquivo: {error}"})
            return False

    def get_recent_markdowns(self) -> dict[str, Any]:
        try:
            return {"ok": True, "items": self._persisted_markdowns()}
        except (OSError, sqlite3.Error) as error:
            return {"ok": False, "items": [], "error": str(error)}

    def rebuild_markdown_index(self) -> dict[str, Any]:
        indexed = 0
        failures: list[dict[str, str]] = []
        for item in self._library.get_recent_markdowns(10_000):
            source = Path(item["file_path"])
            markdown = Path(item["markdown_path"])
            try:
                content = markdown.read_text(encoding="utf-8")
                self._library.index_markdown_file(source, markdown, content)
                if not self._library.verify_markdown_index(source, markdown):
                    raise RuntimeError("o índice não confirmou o conteúdo gravado")
                indexed += 1
            except Exception as error:
                self._library.record_markdown_index_failure(source, markdown, str(error))
                failures.append({"markdown_path": str(markdown), "error": str(error)})
        return {"ok": not failures, "indexed": indexed, "failures": failures}

    def open_folder(self, directory_id: str) -> bool:
        """Abre no Explorer somente uma pasta previamente autorizada."""
        try:
            path = self._resolve_directory(directory_id, "open")
        except ResourceAccessError as error:
            self._emit("toast", {"type": "error", "message": str(error)})
            return False
        try:
            subprocess.run(["explorer", str(path)], check=False)
            return True
        except OSError as error:
            self._emit("toast", {"type": "error", "message": f"Erro ao abrir pasta: {error}"})
            return False

    def read_markdown_preview(self, markdown_id: str) -> dict[str, Any]:
        """Lê o conteúdo do Markdown gerado para visualização em tempo real."""
        try:
            path = self._resolve_markdown(markdown_id, "read")
            content = path.read_text(encoding="utf-8")
            size = path.stat().st_size
            return {
                "ok": True,
                "name": path.name,
                "path": str(path),
                "markdown_id": markdown_id,
                "content": content,
                "size_formatted": format_file_size(size),
            }
        except (OSError, UnicodeError, ResourceAccessError) as error:
            return {"ok": False, "error": f"Erro ao ler Markdown: {error}"}

    def read_markdown_asset(self, markdown_id: str, relative_ref: str) -> dict[str, Any]:
        """Lê imagem relativa contida na pasta do Markdown sem expor acesso genérico."""
        try:
            markdown_path = self._resolve_markdown(markdown_id, "asset_read")
            parsed = urlsplit(str(relative_ref))
            if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
                raise ResourceAccessError("Referência de imagem não permitida.")
            decoded = unquote(parsed.path)
            if not decoded or "\x00" in decoded or decoded.startswith(("/", "\\")):
                raise ResourceAccessError("Referência de imagem inválida.")
            relative = Path(decoded.replace("/", os.sep))
            if relative.is_absolute() or relative.drive or ".." in relative.parts:
                raise ResourceAccessError("A imagem está fora da pasta autorizada.")
            root = markdown_path.parent.resolve()
            asset = (root / relative).resolve()
            if not asset.is_relative_to(root) or not asset.is_file():
                raise ResourceAccessError("Imagem local não encontrada ou não autorizada.")
            allowed_types = {
                ".png": "image/png",
                ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg",
                ".gif": "image/gif",
                ".webp": "image/webp",
                ".bmp": "image/bmp",
            }
            mime = allowed_types.get(asset.suffix.lower())
            guessed, _ = mimetypes.guess_type(asset.name)
            if mime is None or guessed != mime:
                raise ResourceAccessError("Formato de imagem local não permitido.")
            if asset.stat().st_size > 20 * 1024 * 1024:
                raise ResourceAccessError("A imagem local excede o limite de 20 MB.")
            encoded = base64.b64encode(asset.read_bytes()).decode("ascii")
            return {"ok": True, "data_uri": f"data:{mime};base64,{encoded}"}
        except (OSError, ResourceAccessError) as error:
            return {"ok": False, "error": str(error)}

    def open_external_url(self, url: str) -> dict[str, Any]:
        """Abre apenas links HTTP(S) no navegador padrão do sistema."""
        parsed = urlsplit(str(url).strip())
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
            return {"ok": False, "error": "Link externo não permitido."}
        try:
            return {"ok": bool(webbrowser.open(url, new=2))}
        except webbrowser.Error as error:
            return {"ok": False, "error": str(error)}

    # --------------------------------------------------------------------------
    # Módulos do Super PDF (Leitor e Editor Integrado)
    # --------------------------------------------------------------------------
    def set_pdf_password(self, file_id: str, password: str) -> dict[str, Any]:
        """Tenta autenticar e memorizar a senha de um PDF protegido."""
        try:
            require_software_activation("reader")
        except (LicenseRequiredError, LicenseAccessError) as error:
            return _license_denial(error)
        try:
            file_path = self._resolve_pdf(file_id)
        except ResourceAccessError as error:
            return {"ok": False, "error": str(error)}
        doc, error, needs_password = self._open_doc_with_auth(file_path, password)
        if error or needs_password or doc is None:
            return {"ok": False, "error": error or "Senha incorreta."}
        doc.close()
        return {"ok": True, "message": "Senha autenticada com sucesso."}

    def get_pdf_info(self, file_id: str, password: str | None = None) -> dict[str, Any]:
        """Retorna metadados essenciais do PDF para abertura instantânea do Leitor (Fase 4)."""
        try:
            require_software_activation("reader")
        except (LicenseRequiredError, LicenseAccessError) as error:
            return _license_denial(error)
        try:
            file_path = self._resolve_pdf(file_id)
        except ResourceAccessError as error:
            return {"ok": False, "error": str(error), "needs_password": False}
        doc, error, needs_password = self._open_doc_with_auth(file_path, password)
        if error or needs_password or doc is None:
            return {"ok": False, "error": error or "PDF protegido.", "needs_password": needs_password, "is_encrypted": True}

        try:
            path = Path(file_path).resolve()
            page_count = len(doc)
            is_encrypted = doc.is_encrypted
            metadata = doc.metadata or {}

            # Indexa metadados O(1) no banco sem bloquear a abertura
            self._library.index_document_metadata_only(path, page_count, path.stat().st_size if path.exists() else 0)

            session = self._library.get_session_state(str(path))
            bookmarks = self._library.get_bookmarks(str(path))

            first_chunk_limit = min(page_count, 50)
            initial_pages = []
            for idx in range(first_chunk_limit):
                p = doc[idx]
                initial_pages.append(
                    {
                        "page_number": idx,
                        "width": p.rect.width,
                        "height": p.rect.height,
                        "rotation": p.rotation,
                    }
                )

            info = {
                "ok": True,
                "file_name": path.name,
                "file_path": str(path),
                "file_id": file_id,
                "page_count": page_count,
                "is_encrypted": is_encrypted,
                "metadata": metadata,
                "session_state": session,
                "bookmarks": bookmarks,
                "pages": initial_pages,
                "backup_available": _pdf_backup_path(path).is_file(),
            }
            return info
        except Exception as error:
            return {"ok": False, "error": f"Erro ao inspecionar PDF: {error}"}
        finally:
            _safe_close(doc)

    def get_pdf_page_range(
        self, file_id: str, start_page: int = 0, count: int = 50, password: str | None = None
    ) -> dict[str, Any]:
        """Retorna dimensões e rotação de uma faixa de páginas por solicitação (Item 12)."""
        try:
            require_software_activation("reader")
        except (LicenseRequiredError, LicenseAccessError) as error:
            return _license_denial(error)
        try:
            file_path = self._resolve_pdf(file_id)
        except ResourceAccessError as error:
            return {"ok": False, "error": str(error)}
        doc, error, needs_password = self._open_doc_with_auth(file_path, password)
        if error or needs_password or doc is None:
            return {"ok": False, "error": error or "PDF protegido.", "needs_password": needs_password}

        try:
            page_count = len(doc)
            start_idx = max(0, start_page)
            end_idx = min(page_count, start_idx + count)
            pages = []
            for idx in range(start_idx, end_idx):
                p = doc[idx]
                pages.append(
                    {
                        "page_number": idx,
                        "width": p.rect.width,
                        "height": p.rect.height,
                        "rotation": p.rotation,
                    }
                )
            return {
                "ok": True,
                "file_id": file_id,
                "start_page": start_idx,
                "count": len(pages),
                "total_pages": page_count,
                "pages": pages,
            }
        except Exception as error:
            return {"ok": False, "error": f"Erro ao obter faixa de páginas: {error}"}
        finally:
            _safe_close(doc)

    def render_page_hq(
        self,
        file_id: str,
        page_number: int = 0,
        dpi: int = 150,
        password: str | None = None,
        rotation: int | None = None,
    ) -> dict[str, Any]:
        """Renderiza uma página sob demanda e indexa incrementalmente no FTS5 (Fase 4)."""
        try:
            require_software_activation("reader")
        except (LicenseRequiredError, LicenseAccessError) as error:
            return _license_denial(error)
        try:
            page_number = int(page_number)
            dpi = _validated_dpi(dpi)
        except (TypeError, ValueError) as error:
            return {"ok": False, "error": str(error), "needs_password": False}
        try:
            file_path = self._resolve_pdf(file_id)
        except ResourceAccessError as error:
            return {"ok": False, "error": str(error), "needs_password": False}
        doc, error, needs_password = self._open_doc_with_auth(file_path, password)
        if error or needs_password or doc is None:
            return {"ok": False, "error": error or "PDF protegido.", "needs_password": needs_password}

        try:
            path = Path(file_path).resolve()
            if not (0 <= page_number < len(doc)):
                return {"ok": False, "error": f"Página {page_number} fora do intervalo."}

            page = doc[page_number]
            if rotation is not None:
                rotation = int(rotation)
                if rotation not in {0, 90, 180, 270}:
                    raise ValueError("Rotação de visualização inválida.")
                page.set_rotation(rotation)
            _validate_pixel_budget(page.rect, dpi)
            pix = page.get_pixmap(dpi=dpi)
            img_b64 = base64.b64encode(pix.tobytes("png")).decode("utf-8")
            data_uri = f"data:image/png;base64,{img_b64}"

            # Extrai texto e indexa a página visitada de forma incremental em background
            page_text = page.get_text("text")
            self._enqueue_page_index(str(path), page_number, page_text)

            return {
                "ok": True,
                "image": data_uri,
                "width": page.rect.width,
                "height": page.rect.height,
                "pixel_width": pix.width,
                "pixel_height": pix.height,
                "page_count": len(doc),
                "page_number": page_number,
                "rotation": page.rotation,
                "file_name": path.name,
                "file_path": str(path),
                "file_id": file_id,
            }
        except Exception as error:
            return {"ok": False, "error": f"Erro ao renderizar: {error}"}
        finally:
            _safe_close(doc)

    def start_full_indexing(self, file_id: str, password: str | None = None) -> dict[str, Any]:
        """Dispara a indexação completa em segundo plano com suporte a progresso, pausa e cancelamento (Item 13)."""
        try:
            file_path = self._resolve_pdf(file_id)
        except ResourceAccessError as error:
            return {"ok": False, "error": str(error)}

        path_str = str(file_path.resolve())
        with self._indexing_lock:
            if path_str in self._indexing_threads and self._indexing_threads[path_str].is_alive():
                return {"ok": True, "already_running": True, "message": "Indexação já em andamento."}

            cancel_evt = threading.Event()
            pause_evt = threading.Event()
            pause_evt.set()

            self._indexing_cancel_events[path_str] = cancel_evt
            self._indexing_pause_events[path_str] = pause_evt

            thread = threading.Thread(
                target=self._run_full_indexing_worker,
                args=(path_str, file_id, password, cancel_evt, pause_evt),
                daemon=False,
                name=f"PdfIndexer-{Path(path_str).name}",
            )
            self._indexing_threads[path_str] = thread
            thread.start()

        return {"ok": True, "started": True}

    def _run_full_indexing_worker(
        self,
        file_path: str,
        file_id: str,
        password: str | None,
        cancel_evt: threading.Event,
        pause_evt: threading.Event,
    ) -> None:
        doc: fitz.Document | None = None
        try:
            doc, error, needs_pw = self._open_doc_with_auth(file_path, password)
            if not doc or error or needs_pw:
                self._emit("indexing_error", {"file_id": file_id, "error": error or "Erro ao abrir PDF."})
                return

            total_pages = len(doc)
            for idx in range(total_pages):
                if cancel_evt.is_set():
                    self._emit("indexing_cancelled", {"file_id": file_id, "page": idx, "total": total_pages})
                    return

                while not pause_evt.wait(timeout=0.2):
                    if cancel_evt.is_set():
                        self._emit("indexing_cancelled", {"file_id": file_id, "page": idx, "total": total_pages})
                        return

                text = doc[idx].get_text("text")
                self._library.index_single_pdf_page(file_path, idx, text)
                percent = round(((idx + 1) / total_pages) * 100)
                self._emit(
                    "indexing_progress",
                    {"file_id": file_id, "current_page": idx + 1, "total_pages": total_pages, "percent": percent},
                )

            self._library.finalize_incremental_pdf_index(file_path, total_pages)
            self._emit("indexing_completed", {"file_id": file_id, "total_pages": total_pages})
        except Exception as err:
            self._emit("indexing_error", {"file_id": file_id, "error": str(err)})
        finally:
            _safe_close(doc)
            with self._indexing_lock:
                self._indexing_cancel_events.pop(file_path, None)
                self._indexing_pause_events.pop(file_path, None)
                self._indexing_threads.pop(file_path, None)

    def pause_indexing(self, file_id: str) -> dict[str, Any]:
        """Alterna o estado de pausa da indexação completa do documento."""
        try:
            file_path = str(self._resolve_pdf(file_id).resolve())
        except ResourceAccessError as error:
            return {"ok": False, "error": str(error)}

        with self._indexing_lock:
            pause_evt = self._indexing_pause_events.get(file_path)
            if not pause_evt:
                return {"ok": False, "error": "Nenhuma indexação ativa para este documento."}

            if pause_evt.is_set():
                pause_evt.clear()
                return {"ok": True, "is_paused": True}

            pause_evt.set()
            return {"ok": True, "is_paused": False}

    def cancel_indexing(self, file_id: str) -> dict[str, Any]:
        """Cancela a indexação completa em segundo plano."""
        try:
            file_path = str(self._resolve_pdf(file_id).resolve())
        except ResourceAccessError as error:
            return {"ok": False, "error": str(error)}
        thread: threading.Thread | None = None
        with self._indexing_lock:
            cancel_evt = self._indexing_cancel_events.get(file_path)
            pause_evt = self._indexing_pause_events.get(file_path)
            thread = self._indexing_threads.get(file_path)
            if cancel_evt:
                cancel_evt.set()
            if pause_evt:
                pause_evt.set()

        if thread and thread is not threading.current_thread():
            thread.join(timeout=5)
            if thread.is_alive():
                return {"ok": False, "error": "A indexação ainda está encerrando; tente novamente."}
        return {"ok": True, "cancelled": True}

    def rotate_pdf_page(
        self,
        file_id: str,
        page_number: int,
        degrees: int,
        password: str | None = None,
        output_file_id: str | None = None,
    ) -> dict[str, Any]:
        """Gira uma página específica em incrementos de 90 graus e salva no original ou como cópia (Item 19)."""
        try:
            require_software_activation("reader")
        except (LicenseRequiredError, LicenseAccessError) as error:
            return _license_denial(error)
        try:
            file_path = self._resolve_pdf(file_id, "write")
        except ResourceAccessError as error:
            return {"ok": False, "error": str(error), "needs_password": False}

        cancel_result = self.cancel_indexing(file_id)
        if not cancel_result.get("ok"):
            return cancel_result
        doc, error, needs_password = self._open_doc_with_auth(file_path, password)
        if error or needs_password or doc is None:
            return {"ok": False, "error": error or "PDF protegido.", "needs_password": needs_password}

        try:
            path = Path(file_path).resolve()
            target_path = self._resolve_pdf_destination(output_file_id) if output_file_id else path
            if not (0 <= page_number < len(doc)):
                return {"ok": False, "error": "Página inexistente."}

            page = doc[page_number]
            new_rotation = (page.rotation + degrees) % 360
            page.set_rotation(new_rotation)

            backup_path = _save_doc_safely(doc, target_path)
            res = {
                "ok": True,
                "new_rotation": new_rotation,
                "message": (
                    f"Página {page_number + 1} rotacionada para {new_rotation}°. "
                    f"Backup anterior: {backup_path.name}."
                    if backup_path
                    else f"Página {page_number + 1} rotacionada para {new_rotation}°."
                ),
                "target_path": str(target_path),
                "is_copy": target_path != path,
                "backup_path": str(backup_path) if backup_path else None,
            }
            if target_path != path:
                new_pdf = self._register_pdf(target_path, "copy")
                res["new_file_id"] = new_pdf["file_id"]
                res["new_file"] = new_pdf
            return res
        except Exception as error:
            return {"ok": False, "error": f"Erro ao rotacionar página: {error}"}
        finally:
            _safe_close(doc)

    def protect_pdf(
        self,
        file_id: str,
        user_pw: str,
        owner_pw: str = "",
        output_file_id: str | None = None,
    ) -> dict[str, Any]:
        """Aplica criptografia AES-256 no arquivo PDF com senhas no original ou como cópia (Item 19)."""
        try:
            require_software_activation("reader")
        except (LicenseRequiredError, LicenseAccessError) as error:
            return _license_denial(error)
        try:
            path = self._resolve_pdf(file_id, "write")
        except ResourceAccessError as error:
            return {"ok": False, "error": str(error)}
        if not user_pw:
            return {"ok": False, "error": "A senha do usuário não pode ser vazia."}

        cancel_result = self.cancel_indexing(file_id)
        if not cancel_result.get("ok"):
            return cancel_result
        doc, error, needs_password = self._open_doc_with_auth(path)
        if error or doc is None:
            return {"ok": False, "error": error or "Não foi possível abrir o PDF."}

        try:
            target_path = self._resolve_pdf_destination(output_file_id) if output_file_id else path
            owner = owner_pw if owner_pw else user_pw
            perm = fitz.PDF_PERM_PRINT | fitz.PDF_PERM_COPY | fitz.PDF_PERM_ANNOTATE | fitz.PDF_PERM_ACCESSIBILITY
            backup_path = _save_doc_safely(
                doc,
                target_path,
                encryption=fitz.PDF_ENCRYPT_AES_256,
                user_pw=user_pw,
                owner_pw=owner,
                permissions=perm,
            )
            self._pdf_passwords[str(target_path)] = user_pw
            res = {
                "ok": True,
                "message": (
                    f"PDF protegido com AES-256. Backup anterior: {backup_path.name}."
                    if backup_path
                    else "PDF protegido com sucesso usando criptografia AES-256."
                ),
                "target_path": str(target_path),
                "is_copy": target_path != path,
                "backup_path": str(backup_path) if backup_path else None,
            }
            if target_path != path:
                new_pdf = self._register_pdf(target_path, "copy")
                res["new_file_id"] = new_pdf["file_id"]
                res["new_file"] = new_pdf
            return res
        except Exception as error:
            return {"ok": False, "error": f"Erro ao proteger PDF: {error}"}
        finally:
            _safe_close(doc)

    def unprotect_pdf(self, file_id: str, current_pw: str = "", output_file_id: str | None = None) -> dict[str, Any]:
        """Remove a proteção por senha de um PDF no original ou como cópia (Item 19)."""
        try:
            require_software_activation("reader")
        except (LicenseRequiredError, LicenseAccessError) as error:
            return _license_denial(error)
        try:
            path = self._resolve_pdf(file_id, "write")
        except ResourceAccessError as error:
            return {"ok": False, "error": str(error)}

        cancel_result = self.cancel_indexing(file_id)
        if not cancel_result.get("ok"):
            return cancel_result
        doc, error, needs_password = self._open_doc_with_auth(path, current_pw)
        if error or doc is None:
            return {"ok": False, "error": error or "Não foi possível abrir o PDF com a senha informada."}

        try:
            target_path = self._resolve_pdf_destination(output_file_id) if output_file_id else path
            backup_path = _save_doc_safely(
                doc,
                target_path,
                encryption=fitz.PDF_ENCRYPT_NONE,
            )
            self._pdf_passwords.pop(str(target_path), None)

            res = {
                "ok": True,
                "message": (
                    f"Proteção removida. Backup anterior: {backup_path.name}."
                    if backup_path
                    else "Proteção por senha removida com sucesso."
                ),
                "target_path": str(target_path),
                "is_copy": target_path != path,
                "backup_path": str(backup_path) if backup_path else None,
            }
            if target_path != path:
                new_pdf = self._register_pdf(target_path, "copy")
                res["new_file_id"] = new_pdf["file_id"]
                res["new_file"] = new_pdf
            return res
        except Exception as error:
            return {"ok": False, "error": f"Erro ao remover senha do PDF: {error}"}
        finally:
            _safe_close(doc)

    def save_pdf_annotations(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Grava anotações nativas no arquivo PDF original ou como cópia (Item 19)."""
        try:
            require_software_activation("reader")
        except (LicenseRequiredError, LicenseAccessError) as error:
            return _license_denial(error)
        if payload.get("output_path"):
            return {"ok": False, "error": "Destino por caminho não autorizado."}
        file_id = payload.get("file_id", "")
        password = payload.get("password")
        annotations = payload.get("annotations", [])
        rotations = payload.get("rotations", [])
        output_file_id = payload.get("output_file_id")

        try:
            file_path = self._resolve_pdf(str(file_id), "write")
        except ResourceAccessError as error:
            return {"ok": False, "error": str(error), "needs_password": False}

        cancel_result = self.cancel_indexing(str(file_id))
        if not cancel_result.get("ok"):
            return cancel_result
        doc, error, needs_password = self._open_doc_with_auth(file_path, password)
        if error or needs_password or doc is None:
            return {"ok": False, "error": error or "PDF protegido por senha.", "needs_password": needs_password}

        path = Path(file_path).resolve()
        try:
            if not isinstance(rotations, list) or len(rotations) > len(doc):
                raise ValueError("Lista de rotações inválida.")
            validated_rotations: list[tuple[int, int]] = []
            seen_rotation_pages: set[int] = set()
            for item in rotations:
                if not isinstance(item, dict):
                    raise ValueError("Rotação inválida.")
                page_num = int(item.get("page_number", -1))
                degrees = int(item.get("degrees", 0))
                if page_num in seen_rotation_pages or not (0 <= page_num < len(doc)):
                    raise ValueError("Página de rotação inválida ou repetida.")
                if degrees not in {0, 90, 180, 270}:
                    raise ValueError("A rotação deve usar incrementos de 90 graus.")
                seen_rotation_pages.add(page_num)
                if degrees:
                    validated_rotations.append((page_num, degrees))

            for page_num, degrees in validated_rotations:
                page = doc[page_num]
                page.set_rotation((page.rotation + degrees) % 360)

            annotations = _validate_annotation_payload(annotations, doc)
            applied_count = 0

            for item in annotations:
                page_num = int(item.get("page_number", 0))
                page = doc[page_num]
                annot_type = str(item.get("type", "")).lower()

                # 1. Caneta Livre Tradicional
                if annot_type in ("ink", "drawing", "caneta"):
                    strokes = item.get("strokes", [])
                    if strokes:
                        annot = page.add_ink_annot(strokes)
                        color = _parse_color(item.get("color"), default=(0.1, 0.1, 0.1))
                        width = float(item.get("width", 2.0))
                        annot.set_colors(stroke=color)
                        annot.set_border(width=width)
                        annot.update()
                        applied_count += 1

                # 2. Caneta Marca-Texto Livre (Grifador Fluorescente)
                elif annot_type in ("highlight_pen", "caneta_marca_texto", "pincel_marca_texto"):
                    strokes = item.get("strokes", [])
                    if strokes:
                        annot = page.add_ink_annot(strokes)
                        color = _parse_color(item.get("color"), default=(1.0, 0.9, 0.2))
                        width = float(item.get("width", 16.0))
                        annot.set_colors(stroke=color)
                        annot.set_border(width=width)
                        annot.set_opacity(0.45)
                        annot.update()
                        applied_count += 1

                # 3. Marca-Texto em Bloco (Retângulo Delimitador)
                elif annot_type in ("highlight", "highlight_block", "marca_texto", "marca-texto"):
                    rect = item.get("rect")
                    if rect and len(rect) >= 4:
                        fitz_rect = fitz.Rect(rect[0], rect[1], rect[2], rect[3])
                        annot = page.add_highlight_annot(fitz_rect)
                        color = _parse_color(item.get("color"), default=(1.0, 0.9, 0.2))
                        annot.set_colors(stroke=color)
                        annot.set_opacity(0.45)
                        annot.update()
                        applied_count += 1

                # 4. Inserção de Texto / Card de Anotação (Com Word Wrap Automático)
                elif annot_type in ("text", "freetext", "texto"):
                    text = str(item.get("text", "")).strip()
                    if not text:
                        continue

                    rect = item.get("rect")
                    x = item.get("x")
                    y = item.get("y")
                    style = item.get("style", "none")
                    fontsize = float(item.get("fontsize", item.get("size", 14.0)))
                    fontname = "helv-bold" if item.get("bold") else "helv"

                    pt_x = float(x if x is not None else (rect[0] if rect else 50.0))
                    pt_y = float(y if y is not None else (rect[1] if rect else 50.0))

                    if style == "none" or not style:
                        text_color = _parse_color(item.get("text_color") or item.get("color"), default=(0.1, 0.1, 0.1))
                        box_w = float(item.get("width", max(300.0, fontsize * 15)))
                        box_h = float(item.get("height", fontsize * 1.5 * (text.count("\n") + 2)))
                        text_rect = fitz.Rect(pt_x, pt_y, pt_x + box_w, pt_y + box_h)
                        text_rect = _fit_textbox_rect(
                            page,
                            text_rect,
                            text,
                            fontname=fontname,
                            fontsize=fontsize,
                        )
                        if text_rect is None:
                            raise ValueError("O texto não cabe na área disponível da página.")

                        remaining = page.insert_textbox(
                            text_rect,
                            text,
                            fontname=fontname,
                            fontsize=fontsize,
                            color=text_color,
                            render_mode=0,
                            align=0,
                        )
                        if remaining < 0:
                            raise RuntimeError("O mecanismo de PDF não confirmou a inserção do texto.")
                        applied_count += 1
                    else:
                        padding_x = 8.0
                        padding_y = 5.0
                        card_w = float(item.get("width", len(text) * fontsize * 0.55 + padding_x * 2 + 10))
                        card_h = float(item.get("height", fontsize * 1.5 * (text.count("\n") + 1) + padding_y * 2 + 10))
                        card_rect = fitz.Rect(pt_x, pt_y, pt_x + card_w, pt_y + card_h)

                        if style == "postit":
                            bg_color = (0.996, 0.941, 0.541)  # #fef08a
                            left_border_color = (0.917, 0.702, 0.031)  # #eab308
                            text_color = _parse_color(item.get("text_color"), default=(0.443, 0.247, 0.071))
                        elif style == "danger":
                            bg_color = (0.996, 0.886, 0.886)  # #fee2e2
                            left_border_color = (0.937, 0.267, 0.267)  # #ef4444
                            text_color = _parse_color(item.get("text_color"), default=(0.600, 0.106, 0.106))
                        elif style == "white":
                            bg_color = (1.0, 1.0, 1.0)
                            left_border_color = (0.008, 0.518, 0.780)  # #0284c7
                            text_color = _parse_color(item.get("text_color"), default=(0.047, 0.290, 0.431))
                        else:
                            bg_color = (1.0, 1.0, 1.0)
                            left_border_color = (0.2, 0.2, 0.2)
                            text_color = _parse_color(item.get("text_color"), default=(0.1, 0.1, 0.1))

                        text_rect = fitz.Rect(
                            pt_x + padding_x + 2, pt_y + padding_y, pt_x + card_w - padding_x, pt_y + card_h - padding_y
                        )
                        fitted_text_rect = _fit_textbox_rect(
                            page,
                            text_rect,
                            text,
                            fontname=fontname,
                            fontsize=fontsize,
                        )
                        if fitted_text_rect is None:
                            raise ValueError("O texto não cabe na área disponível da página.")

                        card_rect.y1 += fitted_text_rect.y1 - text_rect.y1
                        page.draw_rect(card_rect, color=None, fill=bg_color, width=0)
                        page.draw_line(
                            fitz.Point(pt_x, pt_y),
                            fitz.Point(pt_x, card_rect.y1),
                            color=left_border_color,
                            width=3.5,
                        )

                        remaining = page.insert_textbox(
                            fitted_text_rect,
                            text,
                            fontname=fontname,
                            fontsize=fontsize,
                            color=text_color,
                            render_mode=0,
                            align=0,
                        )
                        if remaining < 0:
                            raise RuntimeError("O mecanismo de PDF não confirmou a inserção do texto.")
                        applied_count += 1

            target_path = self._resolve_pdf_destination(str(output_file_id)) if output_file_id else path
            backup_path = _save_doc_safely(doc, target_path)
            res = {
                "ok": True,
                "saved_count": applied_count,
                "rotation_count": len(validated_rotations),
                "message": (
                    f"Alterações salvas: {applied_count} anotação(ões) e {len(validated_rotations)} rotação(ões). "
                    f"Backup anterior: {backup_path.name}."
                    if backup_path
                    else f"Alterações salvas: {applied_count} anotação(ões) e {len(validated_rotations)} rotação(ões)."
                ),
                "target_path": str(target_path),
                "is_copy": target_path != path,
                "backup_path": str(backup_path) if backup_path else None,
            }
            if target_path != path:
                new_pdf = self._register_pdf(target_path, "copy")
                res["new_file_id"] = new_pdf["file_id"]
                res["new_file"] = new_pdf
            res["index_status"] = self._safe_index_pdf(str(target_path), password)
            return res
        except Exception as error:
            return {"ok": False, "error": f"Erro ao salvar anotações: {error}"}
        finally:
            _safe_close(doc)

    def restore_pdf_backup(self, file_id: str, password: str | None = None) -> dict[str, Any]:
        """Restaura atomicamente o backup imediatamente anterior e o consome após a promoção."""
        try:
            require_software_activation("reader")
        except (LicenseRequiredError, LicenseAccessError) as error:
            return _license_denial(error)
        try:
            file_path = self._resolve_pdf(file_id, "write")
        except ResourceAccessError as error:
            return {"ok": False, "error": str(error)}

        cancel_result = self.cancel_indexing(file_id)
        if not cancel_result.get("ok"):
            return cancel_result

        path = Path(file_path).resolve()
        backup_path = _pdf_backup_path(path)
        if not backup_path.is_file():
            return {"ok": False, "error": "Nenhum backup disponível para este PDF."}

        restore_temp = path.with_name(f".{path.stem}.{uuid.uuid4().hex}.restore.tmp.pdf")
        try:
            with fitz.open(str(backup_path)) as backup_doc:
                expected_page_count = backup_doc.page_count
                if not backup_doc.is_pdf:
                    raise RuntimeError("O backup não é um PDF válido.")
            shutil.copy2(backup_path, restore_temp)
            _flush_file(restore_temp)
            _validate_saved_pdf(restore_temp, expected_page_count)
            os.replace(restore_temp, path)
            backup_available = True
            try:
                backup_path.unlink(missing_ok=True)
                backup_available = False
            except OSError as cleanup_error:
                logger.warning(f"PDF restaurado, mas o backup não pôde ser consumido: {cleanup_error}")
            index_status = self._safe_index_pdf(str(path), password)
            index_message = (
                "O índice também foi atualizado."
                if index_status.get("ok")
                else "O PDF foi restaurado, mas o índice não pôde ser atualizado."
            )
            return {
                "ok": True,
                "message": f"A última gravação foi revertida. {index_message}",
                "restored_path": str(path),
                "backup_available": backup_available,
                "index_status": index_status,
            }
        except Exception as error:
            return {"ok": False, "error": f"Erro ao restaurar backup: {error}"}
        finally:
            restore_temp.unlink(missing_ok=True)

    def extract_snippet(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Extrai texto e imagem recortada em alta resolução de uma região retangular do PDF."""
        file_id = payload.get("file_id", "")
        password = payload.get("password")
        rect_coords = payload.get("rect", [0, 0, 100, 100])
        try:
            page_number = int(payload.get("page_number", 0))
            dpi = _validated_dpi(payload.get("dpi", 150))
        except (TypeError, ValueError) as error:
            return {"ok": False, "error": str(error), "needs_password": False}

        try:
            file_path = self._resolve_pdf(str(file_id), "read")
        except ResourceAccessError as error:
            return {"ok": False, "error": str(error), "needs_password": False}

        doc, error, needs_password = self._open_doc_with_auth(file_path, password)
        if error or needs_password or doc is None:
            return {"ok": False, "error": error or "PDF protegido por senha.", "needs_password": needs_password}

        try:
            if not (0 <= page_number < len(doc)):
                return {"ok": False, "error": f"Página {page_number} fora do intervalo (total: {len(doc)})."}

            page = doc[page_number]
            clip_rect = _validated_clip_rect(rect_coords, page.rect)
            _validate_pixel_budget(clip_rect, dpi)

            extracted_text = page.get_text("text", clip=clip_rect).strip()
            pix = page.get_pixmap(clip=clip_rect, dpi=dpi)
            img_bytes = pix.tobytes("png")
            img_b64 = base64.b64encode(img_bytes).decode("utf-8")

            ocr_applied = False
            if len(extracted_text) < 4:
                try:
                    require_software_activation("ocr")
                    from ocr_engine import ocr_pixmap

                    ocr_text, _ = ocr_pixmap(pix)
                    if ocr_text.strip():
                        extracted_text = ocr_text.strip()
                        ocr_applied = True
                except (LicenseRequiredError, LicenseAccessError) as error:
                    return _license_denial(error)
                except Exception as ocr_err:
                    logger.debug(f"OCR snippet fallback: {ocr_err}")

            return {
                "ok": True,
                "text": extracted_text,
                "ocr_applied": ocr_applied,
                "image_base64": f"data:image/png;base64,{img_b64}",
                "width": pix.width,
                "height": pix.height,
                "page_number": page_number,
            }
        except Exception as error:
            return {"ok": False, "error": f"Erro ao extrair trecho: {error}"}
        finally:
            _safe_close(doc)

    # --------------------------------------------------------------------------
    # Serviços online opcionais de síntese de voz neural e tradução multilíngue
    # --------------------------------------------------------------------------
    def _online_breaker(self, service: str) -> CircuitBreaker:
        breakers = getattr(self, "_online_service_breakers", None)
        if breakers is None:
            breakers = {}
            self._online_service_breakers = breakers
        if service not in breakers:
            breakers[service] = CircuitBreaker(
                ONLINE_SERVICE_CIRCUIT_FAILURE_THRESHOLD,
                ONLINE_SERVICE_CIRCUIT_RESET_SECONDS,
            )
        return breakers[service]

    def _online_service_status(self, service: str) -> dict[str, Any]:
        timeout = TRANSLATION_TIMEOUT_SECONDS if service == "translation" else TTS_TIMEOUT_SECONDS
        return {
            "service": service,
            **self._online_breaker(service).status(),
            "timeout_seconds": timeout,
            "max_attempts": ONLINE_SERVICE_MAX_ATTEMPTS,
            "external_dependency": True,
            "contractual_availability_guarantee": False,
            "critical_use_recommended": False,
        }

    def get_online_services_status(self) -> dict[str, Any]:
        """Expõe a condição operacional sem realizar chamadas aos provedores."""
        return {
            "ok": True,
            "services": {
                name: self._online_service_status(name)
                for name in ("translation", "tts")
            },
        }

    def _online_failure(self, service: str, error: BaseException) -> dict[str, Any]:
        status = self._online_service_status(service)
        prefix = "Falha ao traduzir trecho: " if service == "translation" else "Falha na síntese de voz: "
        if isinstance(error, ServiceCircuitOpen):
            error_code = "circuit_open"
            message = (
                "Serviço temporariamente suspenso após falhas consecutivas. "
                f"Tente novamente em cerca de {max(1, round(error.retry_after_seconds))} segundos."
            )
        elif isinstance(error, ServiceOperationTimeout):
            error_code = "service_timeout"
            message = "O serviço online não respondeu dentro do tempo máximo. Tente novamente mais tarde."
        else:
            error_code = "service_unavailable"
            message = "O serviço online está indisponível no momento. Tente novamente mais tarde."
        return {
            "ok": False,
            "error": f"{prefix}{message}",
            "error_code": error_code,
            "retryable": True,
            "service_status": status,
        }

    def get_available_voices(self) -> dict[str, Any]:
        """Retorna as vozes neurais suportadas para leitura com alta fidelidade."""
        voices = [
            {"id": "pt-BR-FranciscaNeural", "name": "Francisca (Português - Brasil)", "gender": "Feminina", "lang": "pt-BR"},
            {"id": "pt-BR-AntonioNeural", "name": "Antônio (Português - Brasil)", "gender": "Masculina", "lang": "pt-BR"},
            {"id": "pt-BR-ThalitaNeural", "name": "Thalita (Português - Brasil)", "gender": "Feminina", "lang": "pt-BR"},
            {"id": "en-US-JennyNeural", "name": "Jenny (Inglês - EUA)", "gender": "Feminina", "lang": "en-US"},
            {"id": "en-US-GuyNeural", "name": "Guy (Inglês - EUA)", "gender": "Masculino", "lang": "en-US"},
            {"id": "es-ES-ElviraNeural", "name": "Elvira (Espanhol - Espanha)", "gender": "Feminina", "lang": "es-ES"},
            {"id": "es-ES-AlvaroNeural", "name": "Álvaro (Espanhol - Espanha)", "gender": "Masculino", "lang": "es-ES"},
            {"id": "fr-FR-DeniseNeural", "name": "Denise (Francês - França)", "gender": "Feminina", "lang": "fr-FR"},
            {"id": "it-IT-ElsaNeural", "name": "Elsa (Italiano - Itália)", "gender": "Feminina", "lang": "it-IT"},
            {"id": "de-DE-KatjaNeural", "name": "Katja (Alemão - Alemanha)", "gender": "Feminina", "lang": "de-DE"},
        ]
        return {"ok": True, "voices": voices, "service_status": self._online_service_status("tts")}

    def synthesize_speech(
        self,
        text: str,
        voice: str = "pt-BR-FranciscaNeural",
        rate: str = "+0%",
        pitch: str = "+0Hz",
    ) -> dict[str, Any]:
        cleaned_text = (text or "").strip()
        if not cleaned_text:
            return {"ok": False, "error": "Nenhum texto informado para síntese de voz."}
        if len(cleaned_text) > MAX_TTS_CHARACTERS:
            return {
                "ok": False,
                "error": f"O texto para voz excede o limite de {MAX_TTS_CHARACTERS} caracteres.",
            }
        text_chunks = _split_text_chunks(cleaned_text, TTS_CHUNK_CHARACTERS)

        async def _run_tts() -> list[bytes]:
            audio_segments: list[bytes] = []
            for text_chunk in text_chunks:
                communicate = edge_tts.Communicate(text_chunk, voice, rate=rate, pitch=pitch)
                segment_chunks: list[bytes] = []
                async for chunk in communicate.stream():
                    if chunk["type"] == "audio":
                        segment_chunks.append(chunk["data"])
                segment = b"".join(segment_chunks)
                if not segment:
                    raise RuntimeError("O serviço não gerou um dos blocos de áudio.")
                audio_segments.append(segment)
            return audio_segments

        try:
            resilient = call_with_resilience(
                lambda: asyncio.run(_run_tts()),
                self._online_breaker("tts"),
                timeout_seconds=TTS_TIMEOUT_SECONDS,
                max_attempts=ONLINE_SERVICE_MAX_ATTEMPTS,
                backoff_seconds=ONLINE_SERVICE_BACKOFF_SECONDS,
            )
            audio_segments: list[bytes] = resilient.value

            if not audio_segments:
                return {"ok": False, "error": "Nenhum dado de áudio foi gerado."}

            data_uris = [
                f"data:audio/mp3;base64,{base64.b64encode(segment).decode('utf-8')}"
                for segment in audio_segments
            ]
            return {
                "ok": True,
                "audio_base64": data_uris[0],
                "audio_segments": data_uris,
                "voice": voice,
                "text_length": len(cleaned_text),
                "chunk_count": len(text_chunks),
                "attempts": resilient.attempts,
                "service_status": self._online_service_status("tts"),
            }
        except Exception as error:
            logger.error(f"Erro no Edge-TTS: {error}", exc_info=True)
            return self._online_failure("tts", error)

    def translate_text(self, text: str, target_lang: str = "pt", source_lang: str = "auto") -> dict[str, Any]:
        """Traduz texto online com Google Translator por meio de deep-translator."""
        cleaned_text = (text or "").strip()
        if not cleaned_text:
            return {"ok": False, "error": "Nenhum texto informado para tradução."}
        if len(cleaned_text) > MAX_TRANSLATION_CHARACTERS:
            return {
                "ok": False,
                "error": f"O texto para tradução excede o limite de {MAX_TRANSLATION_CHARACTERS} caracteres.",
            }
        text_chunks = _split_translation_chunks(cleaned_text, TRANSLATION_CHUNK_CHARACTERS)

        def _translate_chunks(source: str) -> str:
            translator = GoogleTranslator(source=source, target=target_lang)
            translated_chunks: list[str] = []
            for chunk_index, (text_chunk, separator) in enumerate(text_chunks, start=1):
                translated = translator.translate(text_chunk)
                if translated is None:
                    raise RuntimeError(f"O serviço não retornou o bloco {chunk_index} da tradução.")
                translated_chunks.append(f"{translated}{separator}")
            return "".join(translated_chunks)

        def _translate_with_fallback() -> tuple[str, str]:
            try:
                return _translate_chunks(source_lang), source_lang
            except Exception:
                if source_lang == "auto":
                    raise
                return _translate_chunks("auto"), "auto"

        try:
            resilient = call_with_resilience(
                _translate_with_fallback,
                self._online_breaker("translation"),
                timeout_seconds=TRANSLATION_TIMEOUT_SECONDS,
                max_attempts=ONLINE_SERVICE_MAX_ATTEMPTS,
                backoff_seconds=ONLINE_SERVICE_BACKOFF_SECONDS,
            )
            translated, effective_source = resilient.value
            return {
                "ok": True,
                "original_text": cleaned_text,
                "translated_text": translated or "",
                "source_lang": effective_source,
                "target_lang": target_lang,
                "attempts": resilient.attempts,
                "service_status": self._online_service_status("translation"),
            }
        except Exception as error:
            logger.error(f"Erro na tradução: {error}", exc_info=True)
            return self._online_failure("translation", error)

    # --------------------------------------------------------------------------
    # Módulos de Acervo Pessoal & Busca Textual Instantânea (SQLite FTS5)
    # --------------------------------------------------------------------------
    def search_library(self, query: str) -> dict[str, Any]:
        """Executa busca textual instantânea em toda a biblioteca via FTS5."""
        try:
            raw_results = self._library.search(query, limit=50)
            results: list[dict[str, Any]] = []
            for item in raw_results:
                enriched = dict(item)
                if item.get("content_type") == "markdown":
                    try:
                        markdown = self._register_markdown(item.get("markdown_path", ""), "persisted_library")
                        enriched["resource_id"] = markdown["markdown_id"]
                        enriched["display_path"] = markdown["markdown_path"]
                        enriched["availability_status"] = "available"
                    except (OSError, ResourceAccessError):
                        enriched["resource_id"] = None
                        enriched["display_path"] = item.get("markdown_path", "")
                        enriched["availability_status"] = "temporarily_unavailable"
                else:
                    enriched["library_entry_id"] = self._register_library_entry(
                        item["file_path"], "search_library"
                    )
                    try:
                        pdf = self._register_pdf(item["file_path"], "persisted_library")
                        enriched["resource_id"] = pdf["file_id"]
                        enriched["display_path"] = pdf["path"]
                        enriched["availability_status"] = "available"
                    except (OSError, ResourceAccessError):
                        enriched["resource_id"] = None
                        enriched["display_path"] = item["file_path"]
                        enriched["availability_status"] = "temporarily_unavailable"
                results.append(enriched)
            return {"ok": True, "query": query, "results": results, "total": len(results)}
        except Exception as error:
            logger.error(f"Erro na busca do acervo: {error}", exc_info=True)
            return {"ok": False, "error": f"Falha na busca: {error}", "results": []}

    def get_recent_library(self) -> dict[str, Any]:
        """Retorna os documentos recentes do acervo com status de disponibilidade (Item 16)."""
        try:
            documents: list[dict[str, Any]] = []
            for doc in self._library.get_recent_documents(limit=30):
                enriched = dict(doc)
                enriched["library_entry_id"] = self._register_library_entry(
                    doc["file_path"], "recent_library"
                )
                try:
                    pdf = self._register_pdf(doc["file_path"], "recent_reopen")
                    enriched["resource_id"] = pdf["file_id"]
                    enriched["display_path"] = pdf["path"]
                    enriched["availability_status"] = "available"
                except (OSError, ResourceAccessError):
                    enriched["resource_id"] = None
                    enriched["display_path"] = doc["file_path"]
                    enriched["availability_status"] = "temporarily_unavailable"
                documents.append(enriched)
            return {"ok": True, "documents": documents}
        except Exception as error:
            return {"ok": False, "error": str(error), "documents": []}

    def relocate_library_document(self, library_entry_id: str, new_file_id: str) -> dict[str, Any]:
        """Reconecta um documento ausente do acervo a um novo caminho físico (Item 16)."""
        try:
            old_path = str(self._resolve_library_entry(library_entry_id, "relocate"))
            new_file_path = self._resolve_pdf(new_file_id, "read")
            success = self._library.relocate_document(old_path, str(new_file_path))
            if not success:
                return {"ok": False, "error": "Novo arquivo não encontrado ou inválido."}

            new_pdf = self._register_pdf(new_file_path, "relocated")
            return {"ok": True, "file_id": new_pdf["file_id"], "new_path": new_pdf["path"]}
        except Exception as error:
            return {"ok": False, "error": str(error)}

    def remove_library_document(self, library_entry_id: str) -> dict[str, Any]:
        """Marca um documento como removido do acervo pelo usuário sem excluir marcadores (Item 16)."""
        try:
            file_path = str(self._resolve_library_entry(library_entry_id, "remove"))
            success = self._library.remove_document_from_library(file_path)
            return {"ok": success}
        except Exception as error:
            return {"ok": False, "error": str(error)}

    def save_reading_state(self, file_id: str, last_page: int, zoom: str = "1.0") -> dict[str, Any]:
        """Salva a última página lida e o zoom preferido no documento."""
        try:
            file_path = self._resolve_pdf(file_id)
            self._library.save_session_state(str(file_path), last_page, zoom)
            return {"ok": True}
        except Exception as error:
            return {"ok": False, "error": str(error)}

    def get_reading_state(self, file_id: str) -> dict[str, Any]:
        """Recupera o histórico de leitura do documento."""
        try:
            file_path = self._resolve_pdf(file_id)
            state = self._library.get_session_state(str(file_path))
            return {"ok": True, "state": state}
        except Exception as error:
            return {"ok": False, "error": str(error)}

    def add_bookmark(self, file_id: str, page_number: int, title: str = "") -> dict[str, Any]:
        """Adiciona um marcador de página no documento."""
        try:
            file_path = self._resolve_pdf(file_id)
            res = self._library.add_bookmark(str(file_path), page_number, title)
            return res
        except Exception as error:
            return {"ok": False, "error": str(error)}

    def get_bookmarks(self, file_id: str) -> dict[str, Any]:
        """Retorna todos os marcadores de um documento."""
        try:
            file_path = self._resolve_pdf(file_id)
            bookmarks = self._library.get_bookmarks(str(file_path))
            return {"ok": True, "bookmarks": bookmarks}
        except Exception as error:
            return {"ok": False, "error": str(error), "bookmarks": []}

    def delete_bookmark(self, file_id: str, bookmark_id: int) -> dict[str, Any]:
        """Exclui um marcador de página."""
        try:
            file_path = self._resolve_pdf(file_id)
            success = self._library.delete_bookmark(bookmark_id, str(file_path))
            return {"ok": success}
        except Exception as error:
            return {"ok": False, "error": str(error)}

    def _safe_index_pdf(self, file_path: str, password: str | None = None) -> dict[str, Any]:
        """Reindexa o PDF após edição e retorna uma pós-condição verificável."""
        doc: fitz.Document | None = None
        try:
            doc, error, needs_pw = self._open_doc_with_auth(file_path, password)
            if doc and not error and not needs_pw:
                self._library.index_pdf_document(file_path, doc)
                return {"ok": True, "completed": True}
            return {"ok": False, "error": error or "PDF protegido; índice não atualizado."}
        except Exception as err:
            logger.warning(f"Falha na reindexação do PDF {file_path}: {err}")
            return {"ok": False, "error": str(err)}
        finally:
            _safe_close(doc)

    def _enqueue_page_index(self, file_path: str, page_number: int, page_text: str) -> None:
        """Serializa e deduplica escritas incrementais disparadas pela rolagem do leitor."""
        key = (file_path, page_number)
        with self._page_indexing_lock:
            if key in self._pending_page_indexes:
                return
            self._pending_page_indexes.add(key)

        try:
            future = self._page_indexing_executor.submit(
                self._library.index_single_pdf_page,
                file_path,
                page_number,
                page_text,
            )
        except RuntimeError as error:
            with self._page_indexing_lock:
                self._pending_page_indexes.discard(key)
            logger.debug(f"Fila de indexação indisponível para {file_path}, página {page_number + 1}: {error}")
            return

        def release(completed: Future) -> None:
            try:
                completed.result()
            except Exception as error:
                logger.debug(f"Falha na indexação incremental de {file_path}, página {page_number + 1}: {error}")
            finally:
                with self._page_indexing_lock:
                    self._pending_page_indexes.discard(key)

        future.add_done_callback(release)

    def _convert_in_background(
        self,
        files: list[Path],
        output_dir: Path,
        split_output: bool,
        max_chunk_characters: int,
        heading_profile: HeadingProfile,
        split_mode: SplitMode = "semantic",
        page_numbers_by_file: list[tuple[int, ...] | None] | None = None,
        max_workers: int | None = None,
        reservations: list[OutputReservation] | None = None,
    ) -> None:
        try:
            page_numbers_by_file = page_numbers_by_file or [None] * len(files)
            reservations = reservations or reserve_batch_output_paths(
                output_dir, files, retry=any(pages is not None for pages in page_numbers_by_file)
            )
            worker_count = self._resolve_worker_count(len(files), max_workers)
            self._convert_in_parallel(
                files,
                output_dir,
                split_output,
                max_chunk_characters,
                heading_profile,
                worker_count,
                reservations,
                split_mode,
                page_numbers_by_file,
            )
        except Exception as error:
            self._set_conversion_state("failed", error=str(error))
            self._emit(
                "batch_error",
                {"error_message": str(error), "details": traceback.format_exc()},
            )
        finally:
            self.is_converting = False
            self.is_paused = False
            with self._active_checkpoint_lock:
                self._active_checkpoint_dirs.clear()
            self._conversion_finished.set()

    def _resolve_worker_count(self, total_files: int, requested_workers: int | None = None) -> int:
        if total_files <= 1:
            return 1
        automatic_limit = min(total_files, MAX_PARALLEL_WORKERS, os.cpu_count() or 1)
        available_memory = available_memory_bytes()
        if available_memory is not None:
            memory_limit = max(1, available_memory // MEMORY_RESERVATION_PER_WORKER_BYTES)
            automatic_limit = min(automatic_limit, memory_limit)
        if requested_workers is None:
            return max(1, automatic_limit)
        return max(1, min(automatic_limit, int(requested_workers)))

    @staticmethod
    def _validate_batch_budget(files: list[Path], output_dir: Path) -> None:
        source_sizes: list[int] = []
        for source in files:
            size = source.stat().st_size
            if size > MAX_PDF_FILE_SIZE_BYTES:
                raise ResourceBudgetExceeded(
                    f"{source.name}: o PDF excede o limite de {MAX_PDF_FILE_SIZE_BYTES // (1024 * 1024)} MB."
                )
            source_sizes.append(size)
        required = MIN_FREE_DISK_BYTES + sum(source_sizes) * DISK_SPACE_SOURCE_MULTIPLIER
        free = shutil.disk_usage(output_dir).free
        if free < required:
            raise ResourceBudgetExceeded(
                "Espaço livre insuficiente no destino: "
                f"são necessários ao menos {required // (1024 * 1024)} MB livres para este lote."
            )

    def _convert_sequentially(
        self,
        files: list[Path],
        output_dir: Path,
        split_output: bool,
        max_chunk_characters: int,
        heading_profile: HeadingProfile,
        reservations: list[OutputReservation] | None = None,
        split_mode: SplitMode = "semantic",
        page_numbers_by_file: list[tuple[int, ...] | None] | None = None,
    ) -> None:
        converter = PdfMarkdownConverter()
        successes: list[ConversionResult] = []
        failures: list[ConversionFailure] = []
        total = len(files)
        reservations = reservations or reserve_batch_output_paths(output_dir, files)
        page_numbers_by_file = page_numbers_by_file or [None] * total
        self._emit_progress(0, total)

        for index, source in enumerate(files, start=1):
            if self.cancel_requested.is_set():
                self._emit_batch_stopped(successes, failures, total, output_dir)
                return

            while not self.resume_processing.wait(timeout=0.2):
                if self.cancel_requested.is_set():
                    self._emit_batch_stopped(successes, failures, total, output_dir)
                    return

            self._emit(
                "file_start",
                {
                    "file_id": self._resources.id_for_path(source, kind="pdf"),
                    "path": str(source),
                    "name": source.name,
                    "index": index,
                    "total": total,
                },
            )
            self._emit("status", {"message": f"Convertendo {index}/{total}: {source.name}"})

            try:
                result = converter.convert(
                    source,
                    output_dir,
                    split_output,
                    max_chunk_characters,
                    heading_profile,
                    reservations[index - 1],
                    split_mode,
                    page_numbers_by_file[index - 1],
                    True,
                )
                successes.append(result)
                self._emit_file_success(result)
            except Exception as error:
                failure = ConversionFailure(source=source, error_message=str(error), details=traceback.format_exc())
                failures.append(failure)
                self._emit_file_error(failure)

            self._emit_progress(index, total)

        self._emit_batch_done(successes, failures, total, output_dir)

    def _convert_in_parallel(
        self,
        files: list[Path],
        output_dir: Path,
        split_output: bool,
        max_chunk_characters: int,
        heading_profile: HeadingProfile,
        worker_count: int,
        reservations: list[OutputReservation] | None = None,
        split_mode: SplitMode = "semantic",
        page_numbers_by_file: list[tuple[int, ...] | None] | None = None,
    ) -> None:
        total = len(files)
        reservations = reservations or reserve_batch_output_paths(output_dir, files)
        page_numbers_by_file = page_numbers_by_file or [None] * total
        self._emit_progress(0, total)
        successes: list[ConversionResult] = []
        failures: list[ConversionFailure] = []
        pending: dict[Future, tuple[Path, ProcessPoolExecutor, float, OutputReservation]] = {}
        next_index = 0

        while pending or (next_index < total and not self.cancel_requested.is_set()):
            if not self.cancel_requested.is_set() and self.resume_processing.is_set():
                while len(pending) < worker_count and next_index < total:
                    source = files[next_index]
                    reservation = reservations[next_index]
                    page_numbers = page_numbers_by_file[next_index]
                    next_index += 1
                    self._emit(
                        "file_start",
                        {
                            "file_id": self._resources.id_for_path(source, kind="pdf"),
                            "path": str(source),
                            "name": source.name,
                            "index": next_index,
                            "total": total,
                        },
                    )
                    self._emit("status", {"message": f"Convertendo {next_index}/{total}: {source.name}"})
                    checkpoint_dir = self._checkpoint_dir(reservation)
                    self._write_conversion_control(checkpoint_dir, "running")
                    with self._active_checkpoint_lock:
                        self._active_checkpoint_dirs.add(checkpoint_dir)
                    pool = ProcessPoolExecutor(max_workers=1, initializer=init_worker)
                    try:
                        future = pool.submit(
                            convert_worker,
                            source,
                            output_dir,
                            split_output,
                            max_chunk_characters,
                            heading_profile,
                            reservation,
                            split_mode,
                            page_numbers,
                            checkpoint_dir,
                        )
                    except (BrokenProcessPool, RuntimeError) as error:
                        with self._active_checkpoint_lock:
                            self._active_checkpoint_dirs.discard(checkpoint_dir)
                        pool.shutdown(wait=False, cancel_futures=True)
                        failure = ConversionFailure(source=source, error_message=str(error), details=traceback.format_exc())
                        failures.append(failure)
                        self._mark_journal_file_finished(source, "failed")
                        self._emit_file_error(failure)
                        self._emit_progress(len(successes) + len(failures), total)
                        continue
                    pending[future] = (source, pool, time.monotonic(), reservation)

            if not pending:
                self.resume_processing.wait(timeout=0.2)
                continue

            self._sync_active_pause_status()

            wait_timeout = 0 if self.cancel_requested.is_set() else 0.2
            done, _ = wait(pending.keys(), timeout=wait_timeout, return_when=FIRST_COMPLETED)
            for future in done:
                source, pool, _, reservation = pending.pop(future)
                with self._active_checkpoint_lock:
                    self._active_checkpoint_dirs.discard(self._checkpoint_dir(reservation))
                try:
                    result = future.result()
                except Exception as error:
                    result = ConversionFailure(source=source, error_message=str(error), details=traceback.format_exc())
                finally:
                    pool.shutdown(wait=True, cancel_futures=True)

                if isinstance(result, ConversionFailure):
                    if self.cancel_requested.is_set() and result.error_message.startswith("Conversão interrompida"):
                        pass
                    else:
                        failures.append(result)
                        self._mark_journal_file_finished(source, "failed")
                        self._emit_file_error(result)
                else:
                    successes.append(result)
                    self._mark_journal_file_finished(source, "completed")
                    self._emit_file_success(result)
                self._emit_progress(len(successes) + len(failures), total)

            if self.cancel_requested.is_set():
                for _, pool, _, reservation in pending.values():
                    self._terminate_conversion_pool(pool)
                    self._cleanup_conversion_temps(reservation)
                pending.clear()
                with self._active_checkpoint_lock:
                    self._active_checkpoint_dirs.clear()
                if not self._shutdown_requested:
                    self._clear_conversion_journal(remove_checkpoints=True)
                break

            now = time.monotonic()
            over_budget = [
                future
                for future, (_, pool, started, _) in pending.items()
                if now - started > MAX_CONVERSION_SECONDS or self._pool_memory_exceeded(pool)
            ]
            for future in over_budget:
                source, pool, started, reservation = pending.pop(future)
                with self._active_checkpoint_lock:
                    self._active_checkpoint_dirs.discard(self._checkpoint_dir(reservation))
                timed_out = now - started > MAX_CONVERSION_SECONDS
                self._terminate_conversion_pool(pool)
                self._cleanup_conversion_temps(reservation)
                failure = ConversionFailure(
                    source=source,
                    error_message=(
                        f"A conversão excedeu o prazo de {MAX_CONVERSION_SECONDS // 60} minutos."
                        if timed_out
                        else "A conversão excedeu o limite de memória do processo isolado."
                    ),
                    details="O processo isolado foi encerrado pelo watchdog.",
                )
                failures.append(failure)
                self._mark_journal_file_finished(source, "failed")
                self._emit_file_error(failure)
                self._emit_progress(len(successes) + len(failures), total)

        if self.cancel_requested.is_set():
            self._emit_batch_stopped(successes, failures, total, output_dir)
        else:
            self._clear_conversion_journal(remove_checkpoints=True)
            self._emit_batch_done(successes, failures, total, output_dir)

    @staticmethod
    def _terminate_conversion_pool(pool: ProcessPoolExecutor) -> None:
        processes = tuple((getattr(pool, "_processes", {}) or {}).values())
        try:
            pool.terminate_workers()
        except (AttributeError, BrokenProcessPool, RuntimeError):
            for process in processes:
                try:
                    process.terminate()
                except (AttributeError, OSError):
                    pass
        deadline = time.monotonic() + 2.5
        for process in processes:
            try:
                process.join(timeout=max(0.0, deadline - time.monotonic()))
                if process.is_alive():
                    process.kill()
                    process.join(timeout=max(0.0, deadline - time.monotonic()))
            except (AttributeError, OSError):
                pass
        try:
            pool.shutdown(wait=False, cancel_futures=True)
        except (AttributeError, BrokenProcessPool, RuntimeError):
            pass

    @staticmethod
    def _pool_memory_exceeded(pool: ProcessPoolExecutor) -> bool:
        processes = getattr(pool, "_processes", {}) or {}
        for process in processes.values():
            pid = getattr(process, "pid", None)
            if pid is None:
                continue
            rss = process_rss_bytes(pid)
            if rss is not None and rss > MAX_CONVERSION_MEMORY_BYTES:
                return True
        return False

    @staticmethod
    def _cleanup_conversion_temps(reservation: OutputReservation) -> None:
        patterns = (
            (reservation.markdown_path.parent, f".{reservation.markdown_path.name}.*.tmp"),
            (reservation.assets_dir.parent, f".{reservation.assets_dir.name}.*"),
            (reservation.chunks_dir.parent, f".{reservation.chunks_dir.name}.*.tmp"),
        )
        for parent, pattern in patterns:
            if not parent.is_dir():
                continue
            for candidate in parent.glob(pattern):
                if candidate.is_dir():
                    shutil.rmtree(candidate, ignore_errors=True)
                else:
                    candidate.unlink(missing_ok=True)

    def _emit_file_success(self, result: ConversionResult) -> None:
        index_status = "indexed"
        index_error = ""
        try:
            if not result.markdown_path.is_file():
                raise FileNotFoundError("o Markdown final não foi encontrado")
            content = result.markdown_path.read_text(encoding="utf-8")
            self._library.index_markdown_file(result.source, result.markdown_path, content)
            if not self._library.verify_markdown_index(result.source, result.markdown_path):
                raise RuntimeError("o índice não confirmou o conteúdo gravado")
        except Exception as err:
            index_status = "failed"
            index_error = str(err).replace("\r", " ").replace("\n", " ")[:1000]
            logger.error("Falha ao indexar markdown %s: %s", result.markdown_path, err, exc_info=True)
            try:
                self._library.record_markdown_index_failure(result.source, result.markdown_path, index_error)
            except Exception:
                logger.error("Falha ao persistir erro de indexação de %s", result.markdown_path, exc_info=True)

        markdown = self._register_markdown(result.markdown_path, "conversion_result")
        output_directory = self._register_directory(result.markdown_path.parent, "conversion_result")
        self._emit(
            "file_success",
            {
                "source_id": self._resources.id_for_path(result.source, kind="pdf"),
                "source": str(result.source),
                "name": result.source.name,
                "markdown_path": str(result.markdown_path),
                "markdown_id": markdown["markdown_id"],
                "output_directory_id": output_directory["directory_id"],
                "asset_count": result.asset_count,
                "chunk_count": result.chunk_count,
                "duration_formatted": format_duration(result.extraction_seconds),
                "extraction_seconds": result.extraction_seconds,
                "page_coverage": [
                    {
                        "page_number": item.page_number,
                        "status": item.status,
                        "warning": item.warning,
                        "fidelity_score": item.fidelity_score,
                        "fidelity_issues": list(item.fidelity_issues),
                    }
                    for item in result.page_coverage
                ],
                "failed_pages": list(result.failed_pages),
                "warning_pages": list(result.warning_pages),
                "fidelity_review_pages": list(result.fidelity_review_pages),
                "index_status": index_status,
                "index_error": index_error,
            },
        )

    def _emit_file_error(self, failure: ConversionFailure) -> None:
        logger.error(
            "Falha de conversão document=%s error=%s details=%s",
            failure.source.name,
            failure.error_message,
            failure.details,
        )
        self._emit(
            "file_error",
            {
                "source_id": self._resources.id_for_path(failure.source, kind="pdf"),
                "source": str(failure.source),
                "name": failure.source.name,
                "error_message": failure.error_message,
                "details": failure.details,
            },
        )

    def _emit_progress(self, completed: int, total: int) -> None:
        percent = 0 if total <= 0 else round((completed / total) * 100)
        self._emit("progress", {"completed": completed, "total": total, "percent": percent})

    def _emit_batch_done(
        self,
        successes: list[ConversionResult],
        failures: list[ConversionFailure],
        total: int,
        output_dir: Path,
    ) -> None:
        elapsed_seconds = time.perf_counter() - self._batch_start_time
        problem_pages = [
            {"name": result.source.name, "pages": list(result.failed_pages)}
            for result in successes
            if result.failed_pages
        ]
        fidelity_pages = [
            {"name": result.source.name, "pages": list(result.fidelity_review_pages)}
            for result in successes
            if result.fidelity_review_pages
        ]
        self._set_conversion_state("completed")
        self._emit(
            "batch_done",
            {
                "success_count": len(successes),
                "failure_count": len(failures),
                "total": total,
                "elapsed_seconds": elapsed_seconds,
                "elapsed_formatted": format_duration(elapsed_seconds),
                "output_dir": str(output_dir),
                "problem_pages": problem_pages,
                "problem_page_count": sum(len(item["pages"]) for item in problem_pages),
                "fidelity_pages": fidelity_pages,
                "fidelity_review_page_count": sum(len(item["pages"]) for item in fidelity_pages),
            },
        )

    def _emit_batch_stopped(
        self,
        successes: list[ConversionResult],
        failures: list[ConversionFailure],
        total: int,
        output_dir: Path,
    ) -> None:
        elapsed_seconds = time.perf_counter() - self._batch_start_time
        self._set_conversion_state("stopped")
        self._emit(
            "batch_stopped",
            {
                "success_count": len(successes),
                "failure_count": len(failures),
                "total": total,
                "elapsed_seconds": elapsed_seconds,
                "elapsed_formatted": format_duration(elapsed_seconds),
                "output_dir": str(output_dir),
            },
        )
