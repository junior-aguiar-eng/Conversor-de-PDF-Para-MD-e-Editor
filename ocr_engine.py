"""Motor de OCR Local de Alta Performance com RapidOCR (ONNX Runtime).

Executa reconhecimento óptico de caracteres 100% na CPU local, sem depender de GPU
ou instalação externa de binários (como Tesseract).
"""

from __future__ import annotations

import logging
from typing import Any

import fitz

from licensing import require_software_activation

logger = logging.getLogger(__name__)

_RAPID_OCR_INSTANCE: Any = None


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


def ocr_pixmap(pixmap: fitz.Pixmap, min_score: float = 0.35) -> tuple[str, list[dict[str, Any]]]:
    """Executa OCR em um fitz.Pixmap e sintetiza texto estruturado com estimativa de títulos."""
    require_software_activation("ocr")
    ocr = get_ocr_engine()
    img_bytes = pixmap.tobytes("png")
    result, _ = ocr(img_bytes)

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
    return formatted_text, blocks


def ocr_page_to_markdown(page: fitz.Page, dpi: int = 200) -> str:
    """Renderiza a página em alta resolução e extrai seu conteúdo em Markdown via RapidOCR."""
    pix = page.get_pixmap(dpi=dpi)
    text, _ = ocr_pixmap(pix)
    return text
