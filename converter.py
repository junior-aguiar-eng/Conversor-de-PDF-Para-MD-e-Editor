"""Conversão de PDFs para Markdown e validação de runtime."""

from __future__ import annotations

import logging
import shutil
import tempfile
import time
import traceback
from contextvars import ContextVar
from pathlib import Path

from constants import MAX_PAGE_COUNT
from licensing import require_software_activation
from markdown_utils import HeadingProfile, SplitMode, finalize_markdown, reserve_batch_output_paths
from models import ConversionFailure, ConversionResult, OutputReservation, PageCoverage
from ocr_engine import is_scanned_page, ocr_page_to_markdown

logger = logging.getLogger(__name__)

_IMAGE_OUTPUT_CONTEXT: ContextVar[tuple[Path, Path] | None] = ContextVar("nexojuris_image_output", default=None)


def _safe_image_md_path(folder: str, filename: str) -> tuple[str, str]:
    """Retorna a referência relativa ao Markdown e o caminho físico da imagem."""
    base = Path(folder).expanduser().resolve() if folder.strip() else Path.cwd()
    base.mkdir(parents=True, exist_ok=True)
    full_path = base / Path(filename).name
    output_context = _IMAGE_OUTPUT_CONTEXT.get()
    reference_path = full_path
    if output_context and base == output_context[0]:
        reference_path = output_context[1] / Path(filename).name

    # output_paths() salva em <saida>/images/<documento>/; o Markdown fica em
    # <saida> e, portanto, precisa conservar também o segmento "images/".
    reference_base = reference_path.parent
    reference_root = reference_base.parent.parent if reference_base.parent.name.casefold() == "images" else reference_base.parent
    try:
        md_ref = reference_path.relative_to(reference_root).as_posix()
    except ValueError:
        md_ref = reference_path.as_posix()

    clean_md_ref = md_ref.replace("(", "-").replace(")", "-").replace("[", "-").replace("]", "-").replace(" ", "%20")
    return clean_md_ref, str(full_path)


def _patch_pymupdf4llm_md_path() -> None:
    """Garante que o salvamento de imagens do PyMuPDF4LLM utilize caminhos absolutos seguros no Windows."""
    try:
        import pymupdf4llm.helpers.utils as utils

        if not getattr(utils, "_nexojuris_patched", False):
            utils.md_path = _safe_image_md_path
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
        reservation: OutputReservation | None = None,
        split_mode: SplitMode = "semantic",
        page_numbers: tuple[int, ...] | None = None,
    ) -> ConversionResult:
        require_software_activation()
        output_dir = output_dir.resolve()
        reservation = reservation or reserve_batch_output_paths(output_dir, [source], retry=bool(page_numbers))[0]
        reservation.assets_dir.parent.mkdir(parents=True, exist_ok=True)
        temporary_assets_dir = Path(
            tempfile.mkdtemp(prefix=f".{reservation.assets_dir.name}.", dir=reservation.assets_dir.parent)
        )
        context_token = _IMAGE_OUTPUT_CONTEXT.set((temporary_assets_dir.resolve(), reservation.assets_dir.resolve()))
        try:
            with self._pymupdf.open(source) as document:
                page_count = document.page_count
                if page_count > MAX_PAGE_COUNT:
                    raise ValueError(
                        f"O PDF tem {page_count:,} páginas. O limite do aplicativo é de {MAX_PAGE_COUNT:,} páginas."
                    )
                selected = tuple(range(1, page_count + 1)) if page_numbers is None else tuple(dict.fromkeys(page_numbers))
                if not selected or any(number < 1 or number > page_count for number in selected):
                    raise ValueError("A seleção de páginas para conversão é inválida.")

                extraction_start = time.perf_counter()
                rendered_pages: list[str] = []
                coverage: list[PageCoverage] = []
                for page_number in selected:
                    page_index = page_number - 1
                    try:
                        page = document.load_page(page_index)
                    except Exception as error:
                        warning = self._safe_warning(error)
                        coverage.append(PageCoverage(page_number, "failed", warning))
                        rendered_pages.append(self._format_page(page_number, "failed", "", warning))
                        continue

                    detection_warning = ""
                    try:
                        scanned = is_scanned_page(page)
                    except Exception as error:
                        scanned = False
                        detection_warning = f"Detecção de página digitalizada falhou: {self._safe_warning(error)}"
                    text, status, warning = self._extract_page(
                        document,
                        page,
                        page_index,
                        scanned,
                        str(temporary_assets_dir),
                    )
                    combined_warning = "; ".join(item for item in (detection_warning, warning) if item)
                    coverage.append(PageCoverage(page_number, status, combined_warning))
                    rendered_pages.append(self._format_page(page_number, status, text, combined_warning))

                markdown = "\n\n---\n\n".join(rendered_pages)
                extraction_seconds = time.perf_counter() - extraction_start
        except Exception:
            shutil.rmtree(temporary_assets_dir, ignore_errors=True)
            raise
        finally:
            _IMAGE_OUTPUT_CONTEXT.reset(context_token)

        asset_count = sum(1 for item in temporary_assets_dir.rglob("*") if item.is_file())
        return finalize_markdown(
            source,
            reservation.markdown_path,
            markdown,
            asset_count,
            split_output,
            max_chunk_characters,
            extraction_seconds,
            heading_profile,
            split_mode,
            tuple(coverage),
            reservation,
            temporary_assets_dir,
        )

    @staticmethod
    def _safe_warning(error: object) -> str:
        return str(error).replace("\r", " ").replace("\n", " ").replace("<", "&lt;").replace(">", "&gt;")[:300]

    def _native_markdown(self, document: object, page_index: int, image_path: str) -> str:
        return str(
            self._to_markdown(
                document,
                pages=[page_index],
                use_ocr=False,
                force_ocr=False,
                write_images=True,
                image_path=image_path,
                header=False,
                footer=False,
            )
            or ""
        ).strip()

    @staticmethod
    def _direct_text(page: object) -> str:
        try:
            return str(page.get_text("text") or "").strip()
        except Exception:
            return ""

    @staticmethod
    def _has_images(page: object) -> bool:
        try:
            return bool(page.get_images())
        except Exception:
            return True

    def _extract_page(
        self,
        document: object,
        page: object,
        page_index: int,
        scanned: bool,
        image_path: str,
    ) -> tuple[str, str, str]:
        errors: list[str] = []
        if scanned:
            try:
                text = str(ocr_page_to_markdown(page, dpi=200) or "").strip()
                if text:
                    return text, "ocr", ""
            except Exception as error:
                errors.append(f"OCR: {self._safe_warning(error)}")
            direct = self._direct_text(page)
            if direct:
                return direct, "fallback", "; ".join(errors) or "OCR vazio; texto vetorial recuperado."
            try:
                native = self._native_markdown(document, page_index, image_path)
                if native:
                    return native, "fallback", "; ".join(errors) or "OCR vazio; conteúdo recuperado pelo extrator nativo."
            except Exception as error:
                errors.append(f"extrator nativo: {self._safe_warning(error)}")
        else:
            try:
                native = self._native_markdown(document, page_index, image_path)
                if native:
                    return native, "native", ""
            except Exception as error:
                errors.append(f"extrator nativo: {self._safe_warning(error)}")
            direct = self._direct_text(page)
            if direct:
                return direct, "fallback", "; ".join(errors) or "Extração estruturada vazia; texto simples recuperado."
            try:
                text = str(ocr_page_to_markdown(page, dpi=200) or "").strip()
                if text:
                    return text, "ocr", "; ".join(errors) or "Extração nativa vazia; página recuperada por OCR."
            except Exception as error:
                errors.append(f"OCR: {self._safe_warning(error)}")

        if not self._has_images(page) and not self._direct_text(page):
            return "", "empty", "; ".join(errors)
        return "", "failed", "; ".join(errors) or "Nenhum método recuperou conteúdo da página."

    @staticmethod
    def _format_page(page_number: int, status: str, text: str, warning: str) -> str:
        marker = f"<!-- NEXOJURIS_PAGE page={page_number} status={status} -->"
        if status == "failed":
            detail = warning or "Falha sem detalhe adicional."
            return f"{marker}\n\n> [!WARNING]\n> **Página {page_number} não pôde ser recuperada.** {detail}"
        if status == "empty":
            return f"{marker}\n\n*Página {page_number} vazia confirmada.*"
        warning_block = f"\n\n> [!NOTE]\n> Página {page_number}: {warning}" if warning else ""
        return f"{marker}\n\n{text.strip()}{warning_block}".strip()


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
    reservation: OutputReservation | None = None,
    split_mode: SplitMode = "semantic",
    page_numbers: tuple[int, ...] | None = None,
) -> ConversionResult | ConversionFailure:
    """Converte um arquivo dentro de um processo do pool. Nunca propaga
    exceção, para que a falha de um arquivo não derrube o processo inteiro."""
    global _worker_converter
    if _worker_converter is None:
        _worker_converter = PdfMarkdownConverter()
    try:
        return _worker_converter.convert(
            source,
            output_dir,
            split_output,
            max_chunk_characters,
            heading_profile,
            reservation,
            split_mode,
            page_numbers,
        )
    except Exception as error:
        return ConversionFailure(
            source=source,
            error_message=str(error),
            details=traceback.format_exc(),
        )
