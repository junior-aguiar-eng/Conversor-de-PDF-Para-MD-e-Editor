"""Conversão de PDFs para Markdown e validação de runtime."""

from __future__ import annotations

import logging
import shutil
import time
import traceback
from pathlib import Path

from constants import MAX_PAGE_COUNT
from markdown_utils import HeadingProfile, finalize_markdown, output_paths
from models import ConversionFailure, ConversionResult
from ocr_engine import is_scanned_page, ocr_page_to_markdown

logger = logging.getLogger(__name__)


def _patch_pymupdf4llm_md_path() -> None:
    """Garante que o salvamento de imagens do PyMuPDF4LLM utilize caminhos absolutos seguros no Windows."""
    try:
        import pymupdf4llm.helpers.utils as utils

        if not getattr(utils, "_nexojuris_patched", False):

            def safe_md_path(folder: str, filename: str) -> tuple[str, str]:
                base = Path(folder).expanduser().resolve() if folder.strip() else Path.cwd()
                base.mkdir(parents=True, exist_ok=True)
                full_path = base / Path(filename).name
                try:
                    rel = full_path.relative_to(base.parent)
                    md_ref = rel.as_posix()
                except ValueError:
                    md_ref = full_path.as_posix()
                clean_md_ref = md_ref.replace("(", "-").replace(")", "-").replace("[", "-").replace("]", "-").replace(" ", "%20")
                return clean_md_ref, str(full_path)

            utils.md_path = safe_md_path
            utils._nexojuris_patched = True
    except Exception as err:
        logger.debug(f"Aviso ao inicializar patch pymupdf4llm: {err}")


def validate_runtime_dependencies() -> None:
    """Falha cedo com uma mensagem clara se a dependência principal estiver ausente."""
    try:
        import pymupdf4llm  # noqa: F401
    except ImportError as error:
        raise RuntimeError("O conversor não está instalado. Execute instalar_no_d.ps1 para preparar o ambiente.") from error

    try:
        import rapidocr_onnxruntime  # noqa: F401
    except ImportError as error:
        raise RuntimeError("O módulo de OCR Local (rapidocr-onnxruntime) não está instalado.") from error


class PdfMarkdownConverter:
    """Conversor inteligente, híbrido e local para PDFs vetoriais e digitalizados (OCR)."""

    def __init__(self) -> None:
        validate_runtime_dependencies()
        _patch_pymupdf4llm_md_path()
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
        heading_profile: HeadingProfile = "jurisprudencia",
    ) -> ConversionResult:
        with self._pymupdf.open(source) as document:
            page_count = document.page_count
            if page_count > MAX_PAGE_COUNT:
                raise ValueError(f"O PDF tem {page_count:,} páginas. O limite do aplicativo é de {MAX_PAGE_COUNT:,} páginas.")

            output_dir = output_dir.resolve()
            markdown_path, assets_dir = output_paths(output_dir, source)
            assets_dir.mkdir(parents=True, exist_ok=True)
            image_path = str(assets_dir)

            extraction_start = time.perf_counter()

            # 1. Identifica se há páginas escaneadas/digitalizadas no documento
            scanned_pages: set[int] = set()
            try:
                for page in document:
                    if is_scanned_page(page):
                        scanned_pages.add(page.number)
            except Exception as err:
                logger.debug(f"Detecção de páginas escaneadas ignorada: {err}")

            # 2. Estratégia de Extração Inteligente
            if not scanned_pages:
                # Todas as páginas são vetoriais nativas -> extração global direta
                markdown = self._to_markdown(
                    document,
                    use_ocr=False,
                    force_ocr=False,
                    write_images=True,
                    image_path=image_path,
                    header=False,
                    footer=False,
                )
            elif len(scanned_pages) == page_count:
                # Documento 100% digitalizado -> aplica OCR em todas as páginas
                page_markdowns: list[str] = []
                for page in document:
                    page_text = ocr_page_to_markdown(page, dpi=200)
                    if page_text.strip():
                        page_markdowns.append(page_text)
                markdown = "\n\n---\n\n".join(page_markdowns)
            else:
                # PDF Híbrido: processa página a página aplicando OCR apenas nas digitalizadas
                page_markdowns = []
                for page in document:
                    if page.number in scanned_pages:
                        page_text = ocr_page_to_markdown(page, dpi=200)
                    else:
                        try:
                            page_text = self._to_markdown(
                                document,
                                pages=[page.number],
                                use_ocr=False,
                                force_ocr=False,
                                write_images=True,
                                image_path=image_path,
                                header=False,
                                footer=False,
                            )
                        except Exception:
                            page_text = page.get_text("text")

                    if page_text and page_text.strip():
                        page_markdowns.append(page_text.strip())

                markdown = "\n\n---\n\n".join(page_markdowns)

            # 3. Fallback de contingência caso o texto extraído ainda seja insuficiente
            if len(markdown.strip()) < 40:
                try:
                    fallback_markdowns: list[str] = []
                    for page in document:
                        page_text = ocr_page_to_markdown(page, dpi=200)
                        if page_text.strip():
                            fallback_markdowns.append(page_text)
                    if fallback_markdowns:
                        markdown = "\n\n---\n\n".join(fallback_markdowns)
                except Exception as err:
                    logger.warning(f"Fallback OCR ignorado: {err}")

            extraction_seconds = time.perf_counter() - extraction_start

        asset_count = sum(1 for item in assets_dir.rglob("*") if item.is_file())
        if asset_count == 0:
            shutil.rmtree(assets_dir, ignore_errors=True)

        return finalize_markdown(
            source,
            markdown_path,
            markdown,
            asset_count,
            split_output,
            max_chunk_characters,
            extraction_seconds,
            heading_profile,
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
    heading_profile: HeadingProfile = "jurisprudencia",
) -> ConversionResult | ConversionFailure:
    """Converte um arquivo dentro de um processo do pool. Nunca propaga
    exceção, para que a falha de um arquivo não derrube o processo inteiro."""
    global _worker_converter
    if _worker_converter is None:
        _worker_converter = PdfMarkdownConverter()
    try:
        return _worker_converter.convert(source, output_dir, split_output, max_chunk_characters, heading_profile)
    except Exception as error:
        return ConversionFailure(
            source=source,
            error_message=str(error),
            details=traceback.format_exc(),
        )
