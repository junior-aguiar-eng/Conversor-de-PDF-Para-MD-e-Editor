"""Motor de OCR Local de Alta Performance com RapidOCR (ONNX Runtime).

Executa reconhecimento óptico de caracteres 100% na CPU local, sem depender de GPU
ou instalação externa de binários (como Tesseract).
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any

import fitz

from constants import user_data_root
from licensing import require_software_activation

logger = logging.getLogger(__name__)

_RAPID_OCR_INSTANCE: Any = None
OCR_CACHE_MAX_MEMORY_ITEMS = 512
_OCR_MEM_CACHE: OrderedDict[str, tuple[str, list[dict[str, Any]]]] = OrderedDict()
_OCR_CACHE_LOCK = threading.Lock()


def get_ocr_cache_dir() -> Path:
    """Retorna o diretório persistente para cache de OCR."""
    cache_root = user_data_root() / "ocr_cache"
    cache_root.mkdir(parents=True, exist_ok=True)
    return cache_root


def clear_ocr_cache(*, memory_only: bool = False) -> None:
    """Limpa o cache de OCR em memória e opcionalmente em disco."""
    with _OCR_CACHE_LOCK:
        _OCR_MEM_CACHE.clear()
    if not memory_only:
        try:
            cache_dir = user_data_root() / "ocr_cache"
            if cache_dir.is_dir():
                shutil.rmtree(cache_dir, ignore_errors=True)
        except Exception as error:
            logger.debug("Falha ao limpar cache de OCR em disco: %s", error)


def _compute_image_cache_key(img_bytes: bytes, min_score: float) -> str:
    digest = hashlib.sha256(img_bytes).hexdigest()
    score_tag = int(round(min_score * 100))
    return f"{digest}_{score_tag}"


def _lookup_ocr_cache(cache_key: str) -> tuple[str, list[dict[str, Any]]] | None:
    with _OCR_CACHE_LOCK:
        if cache_key in _OCR_MEM_CACHE:
            _OCR_MEM_CACHE.move_to_end(cache_key)
            return _OCR_MEM_CACHE[cache_key]

    try:
        cache_file = get_ocr_cache_dir() / cache_key[:2] / f"{cache_key}.json"
        if cache_file.is_file():
            data = json.loads(cache_file.read_text(encoding="utf-8"))
            if data.get("version") == 1:
                formatted_text = str(data.get("text", ""))
                blocks = data.get("blocks", [])
                with _OCR_CACHE_LOCK:
                    _OCR_MEM_CACHE[cache_key] = (formatted_text, blocks)
                    if len(_OCR_MEM_CACHE) > OCR_CACHE_MAX_MEMORY_ITEMS:
                        _OCR_MEM_CACHE.popitem(last=False)
                return formatted_text, blocks
    except Exception as err:
        logger.debug("Falha na leitura do cache de OCR em disco: %s", err)
    return None


def _store_ocr_cache(cache_key: str, formatted_text: str, blocks: list[dict[str, Any]]) -> None:
    with _OCR_CACHE_LOCK:
        _OCR_MEM_CACHE[cache_key] = (formatted_text, blocks)
        if len(_OCR_MEM_CACHE) > OCR_CACHE_MAX_MEMORY_ITEMS:
            _OCR_MEM_CACHE.popitem(last=False)

    try:
        cache_dir = get_ocr_cache_dir() / cache_key[:2]
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_file = cache_dir / f"{cache_key}.json"
        temp_file = cache_dir / f".{cache_key}.{time.time_ns()}.tmp"
        payload = {
            "version": 1,
            "key": cache_key,
            "text": formatted_text,
            "blocks": blocks,
            "saved_at": time.time(),
        }
        temp_file.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        temp_file.replace(cache_file)
    except Exception as err:
        logger.debug("Falha na gravação do cache de OCR em disco: %s", err)


def is_blank_pixmap(pixmap: fitz.Pixmap, max_samples: int = 1024, tolerance: int = 3) -> bool:
    """Verifica de forma extremamente rápida (<0.1ms) se um Pixmap é visualmente vazio/em branco."""
    if pixmap.width < 8 or pixmap.height < 8:
        return True
    samples = pixmap.samples
    if not samples:
        return True
    step = max(1, len(samples) // max_samples)
    sampled = samples[::step]
    first = sampled[0]
    if all(abs(b - first) <= tolerance for b in sampled):
        if first > 245 or first < 10:
            return True
    return False


def get_ocr_engine() -> Any:
    """Retorna a instância singleton do motor RapidOCR."""
    global _RAPID_OCR_INSTANCE
    if _RAPID_OCR_INSTANCE is None:
        try:
            from rapidocr_onnxruntime import RapidOCR

            _RAPID_OCR_INSTANCE = RapidOCR()
        except Exception as error:
            logger.error(f"Falha ao carregar RapidOCR: {error}", exc_info=True)
            raise RuntimeError(f"Não foi possível inicializar o motor RapidOCR: {error}") from error
    return _RAPID_OCR_INSTANCE


def warmup_ocr_engine_async() -> None:
    """Dispara a inicialização assíncrona em background do RapidOCR em thread desacoplada."""
    def _warmup_worker() -> None:
        try:
            get_ocr_engine()
            logger.info("Motor RapidOCR pré-aquecido em background com sucesso.")
        except Exception as error:
            logger.debug("Warmup de RapidOCR em background ignorado: %s", error)

    threading.Thread(target=_warmup_worker, daemon=True, name="OCR-Warmup").start()


def is_scanned_page(page: fitz.Page, min_char_count: int = 40) -> bool:
    """Verifica se uma página de PDF é predominantemente digitalizada/escaneada.

    Retorna True se o texto vetorial nativo for escasso e houver imagem na página.
    """
    raw_text = page.get_text("text").strip()
    if len(raw_text) >= min_char_count:
        return False

    images = page.get_images()
    if images:
        return True

    # Se não tem texto algum, considera digitalizada para inspeção OCR
    return len(raw_text) == 0


def ocr_pixmap(
    pixmap: fitz.Pixmap,
    min_score: float = 0.35,
    use_cache: bool = True,
) -> tuple[str, list[dict[str, Any]]]:
    """Executa OCR em um fitz.Pixmap com suporte a cache semântico baseado em hash SHA-256."""
    require_software_activation("ocr")
    if is_blank_pixmap(pixmap):
        return "", []

    img_bytes = pixmap.tobytes("png")
    cache_key = _compute_image_cache_key(img_bytes, min_score) if use_cache else ""
    try:
        if use_cache and cache_key:
            cached = _lookup_ocr_cache(cache_key)
            if cached is not None:
                try:
                    from production_diagnostics import get_telemetry_tracker

                    get_telemetry_tracker().record_ocr_cache(hit=True)
                except Exception:
                    pass
                return cached

            try:
                from production_diagnostics import get_telemetry_tracker

                get_telemetry_tracker().record_ocr_cache(hit=False)
            except Exception:
                pass

        ocr = get_ocr_engine()
        result, _ = ocr(img_bytes)
    finally:
        del img_bytes

    if not result:
        return "", []

    blocks: list[dict[str, Any]] = []
    heights: list[float] = []

    for item in result:
        box, text, score = item[0], item[1], float(item[2])
        if score < min_score:
            continue

        text_str = str(text).strip()
        if not text_str:
            continue

        # Calcula bounding box
        xs = [pt[0] for pt in box]
        ys = [pt[1] for pt in box]
        x_min, x_max = min(xs), max(xs)
        y_min, y_max = min(ys), max(ys)
        box_h = y_max - y_min
        box_w = x_max - x_min

        heights.append(box_h)
        blocks.append(
            {
                "text": text_str,
                "score": score,
                "x_min": x_min,
                "y_min": y_min,
                "x_max": x_max,
                "y_max": y_max,
                "height": box_h,
                "width": box_w,
            }
        )

    if not blocks:
        return "", []

    avg_height = sum(heights) / len(heights) if heights else 14.0
    blocks.sort(key=lambda b: ((b["y_min"] + b["y_max"]) / 2, b["x_min"]))

    grouped_lines: list[list[dict[str, Any]]] = []
    for block in blocks:
        center_y = (block["y_min"] + block["y_max"]) / 2
        if grouped_lines:
            previous_line = grouped_lines[-1]
            previous_center = sum((item["y_min"] + item["y_max"]) / 2 for item in previous_line) / len(
                previous_line
            )
            previous_height = sum(item["height"] for item in previous_line) / len(previous_line)
            tolerance = max(2.0, min(previous_height, block["height"]) * 0.45)
            if abs(center_y - previous_center) <= tolerance:
                previous_line.append(block)
                continue
        grouped_lines.append([block])

    lines: list[str] = []
    for line_blocks in grouped_lines:
        line_blocks.sort(key=lambda item: item["x_min"])
        line_height = sum(item["height"] for item in line_blocks) / len(line_blocks)
        line_text = " ".join(item["text"] for item in line_blocks)
        lines.append(f"## {line_text}" if line_height >= avg_height * 1.38 else line_text)

    # Junta linhas agrupando parágrafos
    formatted_text = "\n\n".join(lines)
    if use_cache and cache_key:
        _store_ocr_cache(cache_key, formatted_text, blocks)
    return formatted_text, blocks


def ocr_page_to_markdown(page: fitz.Page, dpi: int = 200, use_cache: bool = True) -> str:
    """Renderiza a página em alta resolução e extrai seu conteúdo em Markdown via RapidOCR."""
    pix = page.get_pixmap(dpi=dpi)
    try:
        text, _ = ocr_pixmap(pix, use_cache=use_cache)
        return text
    finally:
        del pix
