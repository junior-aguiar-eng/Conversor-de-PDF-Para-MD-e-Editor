"""Recria a digitalização inclinada usada pelo gate de fidelidade."""

from __future__ import annotations

import io
from pathlib import Path

import fitz
from PIL import Image

HERE = Path(__file__).resolve().parent
SOURCE = HERE / "stf_re_1276977_ed.pdf"
OUTPUT = HERE / "stf_re_1276977_ed_scanned_skewed.pdf"


def build() -> None:
    with fitz.open(SOURCE) as source:
        source_page = source.load_page(min(2, source.page_count - 1))
        pixmap = source_page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
        image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
        image = image.rotate(1.2, resample=Image.Resampling.BICUBIC, expand=False, fillcolor="white")
        buffer = io.BytesIO()
        image.save(buffer, format="PNG", optimize=True)

        target = fitz.open()
        page = target.new_page(width=source_page.rect.width, height=source_page.rect.height)
        page.insert_image(page.rect, stream=buffer.getvalue())
        target.set_metadata(
            {
                "title": "Digitalização inclinada derivada do RE 1.276.977 ED/DF",
                "author": "Fixture de teste NexoJuris; fonte pública: Supremo Tribunal Federal",
                "subject": "OCR e fidelidade textual",
            }
        )
        target.save(OUTPUT, garbage=4, deflate=True)
        target.close()


if __name__ == "__main__":
    build()
