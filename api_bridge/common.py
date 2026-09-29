"""Constantes, modelos de erro e funções auxiliares da API Bridge."""

from __future__ import annotations

import json
import logging
import math
import os
import shutil
import uuid
from pathlib import Path
from typing import Any

import fitz

from app_storage import data_directory
from constants import (
    MAX_ANNOTATION_FONT_SIZE,
    MAX_ANNOTATION_TEXT_CHARACTERS,
    MAX_ANNOTATIONS_PER_OPERATION,
    MAX_PDF_COORDINATE,
    MAX_POINTS_PER_STROKE,
    MAX_RENDER_DPI,
    MAX_RENDER_PIXEL_AREA,
    MAX_SNIPPET_AREA_POINTS,
    MAX_STROKE_POINTS_PER_OPERATION,
    MAX_STROKE_WIDTH,
    MAX_STROKES_PER_ANNOTATION,
    MIN_RENDER_DPI,
)
from license_core import LicenseAccessError, LicenseState
from licensing import LicenseRequiredError

logger = logging.getLogger(__name__)

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
        except (ValueError, TypeError):
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

        import web_api

        replace_fn = getattr(getattr(web_api, "os", os), "replace", os.replace)

        if overwriting_original:
            backup_path = _pdf_backup_path(target_path)
            shutil.copy2(target_path, backup_temp)
            _flush_file(backup_temp)
            replace_fn(backup_temp, backup_path)

        replace_fn(temp_file, target_path)
        return backup_path
    finally:
        temp_file.unlink(missing_ok=True)
        backup_temp.unlink(missing_ok=True)
        _safe_close(doc)
