"""Motor de OCR Local de Alta Performance com RapidOCR (ONNX Runtime).

Executa reconhecimento óptico de caracteres 100% na CPU local, sem depender de GPU
ou instalação externa de binários (como Tesseract).
"""

from __future__ import annotations

import logging
from typing import Any

import fitz

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

    # Ordena blocos de texto: primariamente por Y (com tolerância de linha), secundariamente por X
    blocks.sort(key=lambda b: (round(b["y_min"] / 12) * 12, b["x_min"]))

    avg_height = sum(heights) / len(heights) if heights else 14.0

    lines: list[str] = []
    current_line_parts: list[str] = []
    last_y_max = -1.0
    last_h = avg_height

    for b in blocks:
        is_title = b["height"] >= avg_height * 1.38

        # Verifica se pertence à mesma linha horizontal
        if last_y_max > 0 and (b["y_min"] > last_y_max + (last_h * 0.45)):
            if current_line_parts:
                line_text = " ".join(current_line_parts)
                lines.append(line_text)
                current_line_parts = []

        prefix = "## " if is_title else ""
        current_line_parts.append(f"{prefix}{b['text']}")
        last_y_max = max(last_y_max, b["y_max"])
        last_h = b["height"]

    if current_line_parts:
        lines.append(" ".join(current_line_parts))

    # Junta linhas agrupando parágrafos
    formatted_text = "\n\n".join(lines)
    return formatted_text, blocks


def ocr_page_to_markdown(page: fitz.Page, dpi: int = 200) -> str:
    """Renderiza a página em alta resolução e extrai seu conteúdo em Markdown via RapidOCR."""
    pix = page.get_pixmap(dpi=dpi)
    text, _ = ocr_pixmap(pix)
    return text
