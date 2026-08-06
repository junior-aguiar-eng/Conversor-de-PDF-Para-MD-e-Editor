"""Conversão de PDFs para Markdown e validação de runtime."""

from __future__ import annotations

import os
import shutil
import time
import traceback
from pathlib import Path

from constants import MAX_PAGE_COUNT
from markdown_utils import finalize_markdown, output_paths
from models import ConversionFailure, ConversionResult


def validate_runtime_dependencies() -> None:
    """Falha cedo com uma mensagem clara se a dependência principal estiver ausente."""
    try:
        import pymupdf4llm  # noqa: F401
    except ImportError as error:
        raise RuntimeError(
            "O conversor não está instalado. Execute instalar_no_d.ps1 para preparar o ambiente."
        ) from error


class PdfMarkdownConverter:
    """Conversor único, leve e local para PDFs com texto nativo."""

    def __init__(self) -> None:
        validate_runtime_dependencies()
        import pymupdf
        import pymupdf4llm

        self._to_markdown = pymupdf4llm.to_markdown
        self._pymupdf = pymupdf

    def convert(
        self,
        source: Path,
        output_dir: Path,
        split_output: bool,
        max_chunk_characters: int,
        include_toc: bool = False,
    ) -> ConversionResult:
        previous_working_directory = Path.cwd()
        try:
            with self._pymupdf.open(source) as document:
                page_count = document.page_count
                if page_count > MAX_PAGE_COUNT:
                    raise ValueError(
                        f"O PDF tem {page_count:,} páginas. O limite do aplicativo é de "
                        f"{MAX_PAGE_COUNT:,} páginas."
                    )

                output_dir = output_dir.resolve()
                markdown_path, assets_dir = output_paths(output_dir, source)
                assets_dir.mkdir(parents=True, exist_ok=True)
                image_path = assets_dir.relative_to(output_dir).as_posix()

                os.chdir(output_dir)
                # Converte o documento inteiro numa só chamada: os níveis de título
                # (# / ##) são calculados a partir dos tamanhos de fonte de todas as
                # páginas, e ficam inconsistentes se cada página for processada isolada.
                extraction_start = time.perf_counter()
                markdown = self._to_markdown(
                    document,
                    use_ocr=False,
                    force_ocr=False,
                    write_images=True,
                    image_path=image_path,
                    header=False,
                    footer=False,
                )
                extraction_seconds = time.perf_counter() - extraction_start
        finally:
            os.chdir(previous_working_directory)
        asset_count = sum(1 for item in assets_dir.rglob("*") if item.is_file())
        if asset_count == 0:
            # Só removemos a subpasta própria deste arquivo. A pasta "images/"
            # pai é compartilhada por todo o lote: em conversão paralela,
            # outro processo pode estar criando sua própria subpasta ali no
            # mesmo instante, e apagar o pai causaria uma corrida entre
            # processos (arquivo "sumiu" no meio de uma operação de outro).
            shutil.rmtree(assets_dir)
        return finalize_markdown(
            source,
            markdown_path,
            markdown,
            asset_count,
            split_output,
            max_chunk_characters,
            include_toc,
            extraction_seconds,
        )


# As duas funções abaixo ficam em nível de módulo (não métodos) porque um
# ProcessPoolExecutor no Windows usa "spawn": só consegue enviar referências a
# funções importáveis do módulo para os processos filhos, não métodos ligados
# a uma instância nem closures.
_worker_converter: PdfMarkdownConverter | None = None


def init_worker() -> None:
    """Roda uma vez em cada processo do pool: paga o custo de importar
    pymupdf4llm/carregar o modelo de layout uma vez por processo, não por
    arquivo convertido."""
    global _worker_converter
    _worker_converter = PdfMarkdownConverter()


def convert_worker(
    source: Path,
    output_dir: Path,
    split_output: bool,
    max_chunk_characters: int,
    include_toc: bool,
) -> ConversionResult | ConversionFailure:
    """Converte um arquivo dentro de um processo do pool. Nunca propaga
    exceção, para que a falha de um arquivo não derrube o processo inteiro."""
    global _worker_converter
    if _worker_converter is None:
        _worker_converter = PdfMarkdownConverter()
    try:
        return _worker_converter.convert(
            source, output_dir, split_output, max_chunk_characters, include_toc
        )
    except Exception as error:
        return ConversionFailure(
            source=source,
            error_message=str(error),
            details=traceback.format_exc(),
        )
