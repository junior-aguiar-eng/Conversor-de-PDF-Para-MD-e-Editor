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
import subprocess
import threading
import time
import traceback
import webbrowser
from concurrent.futures import FIRST_COMPLETED, Future, ProcessPoolExecutor, ThreadPoolExecutor, wait
from concurrent.futures.process import BrokenProcessPool
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

import edge_tts
import fitz
from deep_translator import GoogleTranslator

from constants import (
    APP_NAME,
    APP_VERSION,
    CURRENT_TERMS_VERSION,
    DEFAULT_MAX_CHUNK_CHARACTERS,
    DEFAULT_OUTPUT_DIR,
    MAX_ANNOTATION_FONT_SIZE,
    MAX_ANNOTATION_TEXT_CHARACTERS,
    MAX_ANNOTATIONS_PER_OPERATION,
    MAX_PAGE_COUNT,
    MAX_PDF_COORDINATE,
    MAX_POINTS_PER_STROKE,
    MAX_RENDER_DPI,
    MAX_RENDER_PIXEL_AREA,
    MAX_SNIPPET_AREA_POINTS,
    MAX_STROKE_POINTS_PER_OPERATION,
    MAX_STROKE_WIDTH,
    MAX_STROKES_PER_ANNOTATION,
    MAX_TRANSLATION_CHARACTERS,
    MAX_TTS_CHARACTERS,
    MIN_RENDER_DPI,
    TRANSLATION_CHUNK_CHARACTERS,
    TTS_CHUNK_CHARACTERS,
)
from converter import (
    PdfMarkdownConverter,
    convert_worker,
    init_worker,
    validate_runtime_dependencies,
)
from file_authorization import AuthorizedResourceRegistry, ResourceAccessError
from library_db import LibraryDatabase
from licensing import (
    LicenseRequiredError,
    is_software_activated,
    require_software_activation,
)
from licensing import (
    activate_software as lic_activate_software,
)
from markdown_utils import HeadingProfile, SplitMode, reserve_batch_output_paths
from models import ConversionFailure, ConversionResult, OutputReservation, format_duration

logger = logging.getLogger(__name__)

MAX_PARALLEL_WORKERS = 4
MIN_CHUNK_CHARACTERS = 1_000

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


def _save_doc_safely(doc: fitz.Document, file_path: Path, **save_kwargs: Any) -> None:
    """Salva o documento PDF de forma segura, tratando criptografia, novas cópias e salvamento incremental."""
    target_path = file_path.resolve()
    temp_file: Path | None = None
    try:
        doc_path = Path(doc.name).resolve() if (doc.name and Path(doc.name).is_file()) else None
        # 1. Se for para alterar criptografia OU for gravação em novo arquivo ("Salvar como cópia"), usa doc.save()
        if "encryption" in save_kwargs or (doc_path and doc_path != target_path):
            temp_file = target_path.with_name(f"{target_path.stem}.tmp_{int(time.time() * 1000)}.pdf")
            doc.save(str(temp_file), **save_kwargs)
            _safe_close(doc)
            temp_file.replace(target_path)
            temp_file = None
            return

        # 2. Para edições no mesmo arquivo original, Incremental Save é mais rápido e preserva assinaturas
        try:
            doc.saveIncr()
            _safe_close(doc)
        except Exception:
            if doc is not None and not doc.is_closed and not doc.is_encrypted:
                temp_file = target_path.with_name(f"{target_path.stem}.tmp_{int(time.time() * 1000)}.pdf")
                doc.save(str(temp_file))
                _safe_close(doc)
                temp_file.replace(target_path)
                temp_file = None
            else:
                raise
    finally:
        if temp_file and temp_file.exists():
            temp_file.unlink(missing_ok=True)
        _safe_close(doc)


class BridgeApi:
    """API exposta para o JavaScript via window.pywebview.api."""

    def __init__(self) -> None:
        self._window: Any = None
        self.cancel_requested = threading.Event()
        self.resume_processing = threading.Event()
        self.resume_processing.set()
        self.is_paused = False
        self.is_converting = False
        self._batch_start_time = 0.0
        self._pdf_passwords: dict[str, str] = {}
        self._library = LibraryDatabase()
        self._resources = AuthorizedResourceRegistry()
        self._default_output_resource = self._register_directory(DEFAULT_OUTPUT_DIR, "default_output")
        self._indexing_cancel_events: dict[str, threading.Event] = {}
        self._indexing_pause_events: dict[str, threading.Event] = {}
        self._indexing_threads: dict[str, threading.Thread] = {}
        self._indexing_lock = threading.Lock()

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

    def get_app_info(self) -> dict[str, Any]:
        """Retorna metadados do aplicativo e caminhos padrão."""
        return {
            "app_name": APP_NAME,
            "app_version": APP_VERSION,
            "default_output_dir": str(DEFAULT_OUTPUT_DIR),
            "default_output_dir_id": self._default_output_resource["directory_id"],
            "default_chunk_limit": DEFAULT_MAX_CHUNK_CHARACTERS,
            "max_page_count": MAX_PAGE_COUNT,
        }

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
        """Retorna o status de ativação do software e o identificador de hardware da máquina."""
        is_activated, machine_id = is_software_activated()
        return {
            "is_activated": is_activated,
            "machine_id": machine_id,
            "message": "Software licenciado e ativado." if is_activated else "Ativação pendente para esta máquina.",
        }

    def activate_software(self, key: str) -> dict[str, Any]:
        """Processa a chave de ativação fornecida pelo usuário e desbloqueia o software."""
        result = lic_activate_software(key)
        if result["ok"]:
            self._emit("toast", {"type": "success", "message": result["message"]})
        return result

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

    def start_conversion(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Inicia a conversão em lote em uma thread em segundo plano."""
        try:
            require_software_activation()
        except LicenseRequiredError as error:
            return {
                "started": False,
                "error": str(error),
                "error_code": "license_required",
                "machine_id": error.machine_id,
            }

        if self.is_converting:
            return {"started": False, "error": "Uma conversão já está em andamento."}

        files_data = payload.get("files", [])
        if not files_data:
            return {"started": False, "error": "Nenhum PDF selecionado."}

        output_directory_id = payload.get("output_directory_id", "")
        split_output = bool(payload.get("split_output", False))
        split_mode: SplitMode = payload.get("split_mode", "semantic")
        if split_mode not in {"semantic", "strict"}:
            return {"started": False, "error": "Modo de divisão inválido."}
        max_chunk_characters = int(payload.get("max_chunk_characters", DEFAULT_MAX_CHUNK_CHARACTERS))
        heading_profile: HeadingProfile = payload.get("heading_profile", "jurisprudencia")
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

        self.cancel_requested.clear()
        self.resume_processing.set()
        self.is_paused = False
        self.is_converting = True
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
            ),
            daemon=True,
        )
        thread.start()
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
        """Alterna o estado de pausa da fila de conversão."""
        if not self.is_converting:
            return {"is_paused": False}

        if self.is_paused:
            self.resume_processing.set()
            self.is_paused = False
            self._emit("status", {"message": "Conversão retomada."})
            return {"is_paused": False}

        self.resume_processing.clear()
        self.is_paused = True
        self._emit("status", {"message": "Pausa solicitada: será aplicada antes do próximo PDF."})
        return {"is_paused": True}

    def request_stop(self) -> bool:
        """Solicita a interrupção graciosa do lote."""
        if not self.is_converting:
            return False
        self.cancel_requested.set()
        self.resume_processing.set()
        self._emit("status", {"message": "Parada solicitada: concluindo extração ativa..."})
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

    def render_page_hq(self, file_id: str, page_number: int = 0, dpi: int = 150, password: str | None = None) -> dict[str, Any]:
        """Renderiza uma página sob demanda e indexa incrementalmente no FTS5 (Fase 4)."""
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
            _validate_pixel_budget(page.rect, dpi)
            pix = page.get_pixmap(dpi=dpi)
            img_b64 = base64.b64encode(pix.tobytes("png")).decode("utf-8")
            data_uri = f"data:image/png;base64,{img_b64}"

            # Extrai texto e indexa a página visitada de forma incremental em background
            page_text = page.get_text("text")
            threading.Thread(
                target=self._library.index_single_pdf_page,
                args=(str(path), page_number, page_text),
                daemon=True,
            ).start()

            return {
                "ok": True,
                "image": data_uri,
                "image_base64": data_uri,  # Dupla chave para compatibilidade (Item 14)
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
                daemon=True,
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

            _save_doc_safely(doc, target_path)
            res = {
                "ok": True,
                "new_rotation": new_rotation,
                "message": f"Página {page_number + 1} rotacionada para {new_rotation}°.",
                "target_path": str(target_path),
                "is_copy": target_path != path,
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
            _save_doc_safely(
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
                "message": "PDF protegido com sucesso usando criptografia AES-256.",
                "target_path": str(target_path),
                "is_copy": target_path != path,
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
            _save_doc_safely(
                doc,
                target_path,
                encryption=fitz.PDF_ENCRYPT_NONE,
            )
            self._pdf_passwords.pop(str(target_path), None)

            res = {
                "ok": True,
                "message": "Proteção por senha removida com sucesso.",
                "target_path": str(target_path),
                "is_copy": target_path != path,
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
        if payload.get("output_path"):
            return {"ok": False, "error": "Destino por caminho não autorizado."}
        file_id = payload.get("file_id", "")
        password = payload.get("password")
        annotations = payload.get("annotations", [])
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
            _save_doc_safely(doc, target_path)
            res = {
                "ok": True,
                "saved_count": applied_count,
                "message": f"{applied_count} anotação(ões) salva(s) com sucesso no PDF.",
                "target_path": str(target_path),
                "is_copy": target_path != path,
            }
            if target_path != path:
                new_pdf = self._register_pdf(target_path, "copy")
                res["new_file_id"] = new_pdf["file_id"]
                res["new_file"] = new_pdf
            return res
        except Exception as error:
            return {"ok": False, "error": f"Erro ao salvar anotações: {error}"}
        finally:
            _safe_close(doc)

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
                    from ocr_engine import ocr_pixmap

                    ocr_text, _ = ocr_pixmap(pix)
                    if ocr_text.strip():
                        extracted_text = ocr_text.strip()
                        ocr_applied = True
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
        return {"ok": True, "voices": voices}

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
            # Runner seguro para evitar conflitos de event loop em background threads
            audio_segments: list[bytes]
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None

            if loop and loop.is_running():
                with ThreadPoolExecutor(max_workers=1) as pool:
                    audio_segments = pool.submit(asyncio.run, _run_tts()).result()
            else:
                audio_segments = asyncio.run(_run_tts())

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
            }
        except Exception as error:
            logger.error(f"Erro no Edge-TTS: {error}", exc_info=True)
            return {"ok": False, "error": f"Falha na síntese de voz: {error}"}

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

        try:
            translated = _translate_chunks(source_lang)
            return {
                "ok": True,
                "original_text": cleaned_text,
                "translated_text": translated or "",
                "source_lang": source_lang,
                "target_lang": target_lang,
            }
        except Exception as error:
            if source_lang != "auto":
                try:
                    translated = _translate_chunks("auto")
                    if translated:
                        return {
                            "ok": True,
                            "original_text": cleaned_text,
                            "translated_text": translated,
                            "source_lang": "auto",
                            "target_lang": target_lang,
                        }
                except Exception:
                    pass

            logger.error(f"Erro na tradução: {error}", exc_info=True)
            return {"ok": False, "error": f"Falha ao traduzir trecho: {error}"}

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

    def _safe_index_pdf(self, file_path: str, password: str | None = None) -> None:
        """Executa indexação segura do PDF no banco em background."""
        doc: fitz.Document | None = None
        try:
            doc, error, needs_pw = self._open_doc_with_auth(file_path, password)
            if doc and not error and not needs_pw:
                self._library.index_pdf_document(file_path, doc)
        except Exception as err:
            logger.debug(f"Falha na auto-indexação do PDF {file_path}: {err}")
        finally:
            _safe_close(doc)

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
    ) -> None:
        try:
            page_numbers_by_file = page_numbers_by_file or [None] * len(files)
            reservations = reserve_batch_output_paths(
                output_dir,
                files,
                retry=any(pages is not None for pages in page_numbers_by_file),
            )
            worker_count = self._resolve_worker_count(len(files), max_workers)
            if worker_count <= 1:
                self._convert_sequentially(
                    files,
                    output_dir,
                    split_output,
                    max_chunk_characters,
                    heading_profile,
                    reservations,
                    split_mode,
                    page_numbers_by_file,
                )
            else:
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
            self._emit(
                "batch_error",
                {"error_message": str(error), "details": traceback.format_exc()},
            )
        finally:
            self.is_converting = False
            self.is_paused = False

    def _resolve_worker_count(self, total_files: int, requested_workers: int | None = None) -> int:
        if total_files <= 1:
            return 1
        automatic_limit = min(total_files, MAX_PARALLEL_WORKERS, os.cpu_count() or 1)
        if requested_workers is None:
            return max(1, automatic_limit)
        return max(1, min(automatic_limit, int(requested_workers)))

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
        pending: dict[Future, Path] = {}
        next_index = 0

        with ProcessPoolExecutor(max_workers=worker_count, initializer=init_worker) as pool:
            while pending or (next_index < total and not self.cancel_requested.is_set()):
                if not self.cancel_requested.is_set() and self.resume_processing.is_set():
                    while len(pending) < worker_count and next_index < total:
                        source = files[next_index]
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
                        try:
                            future = pool.submit(
                                convert_worker,
                                source,
                                output_dir,
                                split_output,
                                max_chunk_characters,
                                heading_profile,
                                reservations[next_index - 1],
                                split_mode,
                                page_numbers_by_file[next_index - 1],
                            )
                        except BrokenProcessPool as error:
                            failure = ConversionFailure(source=source, error_message=str(error), details=traceback.format_exc())
                            failures.append(failure)
                            self._emit_file_error(failure)
                            self._emit_progress(len(successes) + len(failures), total)
                            continue
                        pending[future] = source

                if not pending:
                    self.resume_processing.wait(timeout=0.2)
                    continue

                done, _ = wait(pending.keys(), timeout=0.2, return_when=FIRST_COMPLETED)
                for future in done:
                    source = pending.pop(future)
                    try:
                        result = future.result()
                    except Exception as error:
                        result = ConversionFailure(source=source, error_message=str(error), details=traceback.format_exc())

                    if isinstance(result, ConversionFailure):
                        failures.append(result)
                        self._emit_file_error(result)
                    else:
                        successes.append(result)
                        self._emit_file_success(result)

                    self._emit_progress(len(successes) + len(failures), total)

                if self.cancel_requested.is_set() and not pending:
                    break

        if self.cancel_requested.is_set():
            self._emit_batch_stopped(successes, failures, total, output_dir)
        else:
            self._emit_batch_done(successes, failures, total, output_dir)

    def _emit_file_success(self, result: ConversionResult) -> None:
        try:
            if result.markdown_path.is_file():
                content = result.markdown_path.read_text(encoding="utf-8")
                self._library.index_markdown_file(result.source, result.markdown_path, content)
        except Exception as err:
            logger.debug(f"Falha ao indexar markdown {result.markdown_path}: {err}")

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
                    }
                    for item in result.page_coverage
                ],
                "failed_pages": list(result.failed_pages),
                "warning_pages": list(result.warning_pages),
            },
        )

    def _emit_file_error(self, failure: ConversionFailure) -> None:
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
