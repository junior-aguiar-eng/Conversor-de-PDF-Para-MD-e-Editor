"""Conversão de PDFs para Markdown e validação de runtime."""

from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
import time
import traceback
from contextvars import ContextVar
from pathlib import Path

from constants import (
    DISK_SPACE_SOURCE_MULTIPLIER,
    MAX_CONVERSION_MEMORY_BYTES,
    MAX_CONVERSION_SECONDS,
    MAX_EXTRACTED_ASSET_BYTES,
    MAX_IMAGES_PER_DOCUMENT,
    MAX_PAGE_COUNT,
    MAX_PDF_FILE_SIZE_BYTES,
    MIN_FREE_DISK_BYTES,
)
from licensing import require_software_activation
from markdown_utils import HeadingProfile, SplitMode, finalize_markdown, reserve_batch_output_paths
from models import ConversionFailure, ConversionResult, OutputReservation, PageCoverage
from ocr_engine import is_scanned_page, ocr_page_to_markdown

logger = logging.getLogger(__name__)

_IMAGE_OUTPUT_CONTEXT: ContextVar[tuple[Path, Path] | None] = ContextVar("nexojuris_image_output", default=None)


class ResourceBudgetExceeded(RuntimeError):
    """A conversão excedeu um limite operacional seguro."""


def process_rss_bytes(pid: int | None = None) -> int | None:
    """Retorna o RSS de um processo sem introduzir dependência externa."""
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes

            class ProcessMemoryCounters(ctypes.Structure):
                _fields_ = [
                    ("cb", wintypes.DWORD),
                    ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t),
                ]

            counters = ProcessMemoryCounters()
            counters.cb = ctypes.sizeof(counters)
            process = ctypes.windll.kernel32.GetCurrentProcess() if pid is None else ctypes.windll.kernel32.OpenProcess(
                0x1000 | 0x0010, False, pid
            )
            if process:
                try:
                    if ctypes.windll.psapi.GetProcessMemoryInfo(process, ctypes.byref(counters), counters.cb):
                        return int(counters.WorkingSetSize)
                finally:
                    if pid is not None:
                        ctypes.windll.kernel32.CloseHandle(process)
        except (AttributeError, OSError):
            return None
    try:
        page_size = os.sysconf("SC_PAGE_SIZE")
        process_id = "self" if pid is None else str(pid)
        with open(f"/proc/{process_id}/statm", encoding="ascii") as stream:
            resident_pages = int(stream.read().split()[1])
        return resident_pages * page_size
    except (AttributeError, IndexError, OSError, ValueError):
        return None


def current_process_rss_bytes() -> int | None:
    return process_rss_bytes()


def available_memory_bytes() -> int | None:
    """Retorna a memória física disponível para dimensionar a concorrência."""
    if os.name == "nt":
        try:
            import ctypes

            class MemoryStatusEx(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            status = MemoryStatusEx()
            status.dwLength = ctypes.sizeof(status)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return int(status.ullAvailPhys)
        except (AttributeError, OSError):
            return None
    try:
        return int(os.sysconf("SC_AVPHYS_PAGES") * os.sysconf("SC_PAGE_SIZE"))
    except (AttributeError, OSError, ValueError):
        return None


def required_free_disk_bytes(source_size: int) -> int:
    return MIN_FREE_DISK_BYTES + source_size * DISK_SPACE_SOURCE_MULTIPLIER


def _directory_usage(directory: Path) -> tuple[int, int]:
    file_count = 0
    total_bytes = 0
    for item in directory.rglob("*"):
        if item.is_file():
            file_count += 1
            total_bytes += item.stat().st_size
    return file_count, total_bytes


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _checkpoint_signature(source: Path, selected: tuple[int, ...]) -> dict[str, object]:
    stat = source.stat()
    return {
        "version": 1,
        "source": str(source),
        "source_size": stat.st_size,
        "source_mtime_ns": stat.st_mtime_ns,
        "selected_pages": list(selected),
    }


def _load_or_initialize_checkpoint(
    checkpoint_dir: Path,
    source: Path,
    selected: tuple[int, ...],
) -> dict[str, object]:
    signature = _checkpoint_signature(source, selected)
    manifest_path = checkpoint_dir / "state.json"
    try:
        state = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        state = {}
    if any(state.get(key) != value for key, value in signature.items()):
        shutil.rmtree(checkpoint_dir, ignore_errors=True)
        state = {**signature, "pages": {}}
        _atomic_write_text(manifest_path, json.dumps(state, ensure_ascii=False, sort_keys=True))
    (checkpoint_dir / "pages").mkdir(parents=True, exist_ok=True)
    (checkpoint_dir / "assets").mkdir(parents=True, exist_ok=True)
    return state


def _save_page_checkpoint(
    checkpoint_dir: Path,
    state: dict[str, object],
    page_number: int,
    rendered_page: str,
    coverage: PageCoverage,
    image_count: int,
) -> None:
    page_path = checkpoint_dir / "pages" / f"{page_number:06}.md"
    _atomic_write_text(page_path, rendered_page)
    pages = state.setdefault("pages", {})
    if not isinstance(pages, dict):
        raise RuntimeError("Checkpoint de conversão inválido.")
    pages[str(page_number)] = {
        "status": coverage.status,
        "warning": coverage.warning,
    }
    state["image_count"] = image_count
    _atomic_write_text(
        checkpoint_dir / "state.json",
        json.dumps(state, ensure_ascii=False, sort_keys=True),
    )


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
        activation_verified: bool = False,
        checkpoint_dir: Path | None = None,
    ) -> ConversionResult:
        if not activation_verified:
            require_software_activation()
        source = source.resolve()
        output_dir = output_dir.resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        source_size = source.stat().st_size if source.is_file() else 0
        if source_size > MAX_PDF_FILE_SIZE_BYTES:
            raise ResourceBudgetExceeded(
                f"O PDF excede o limite de {MAX_PDF_FILE_SIZE_BYTES // (1024 * 1024)} MB."
            )
        self._check_resource_budget(output_dir, source_size, time.monotonic() + MAX_CONVERSION_SECONDS)
        reservation = reservation or reserve_batch_output_paths(output_dir, [source], retry=bool(page_numbers))[0]
        reservation.assets_dir.parent.mkdir(parents=True, exist_ok=True)
        temporary_assets_dir: Path | None = None
        checkpoint_state: dict[str, object] | None = None
        context_token = None
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

                if checkpoint_dir is not None:
                    checkpoint_dir = checkpoint_dir.resolve()
                    checkpoint_state = _load_or_initialize_checkpoint(checkpoint_dir, source, selected)
                    temporary_assets_dir = checkpoint_dir / "assets"
                else:
                    temporary_assets_dir = Path(
                        tempfile.mkdtemp(prefix=f".{reservation.assets_dir.name}.", dir=reservation.assets_dir.parent)
                    )
                context_token = _IMAGE_OUTPUT_CONTEXT.set(
                    (temporary_assets_dir.resolve(), reservation.assets_dir.resolve())
                )

                extraction_start = time.perf_counter()
                deadline = time.monotonic() + MAX_CONVERSION_SECONDS
                rendered_pages: list[str] = []
                coverage: list[PageCoverage] = []
                image_count = int(checkpoint_state.get("image_count", 0)) if checkpoint_state is not None else 0
                initial_asset_count, initial_asset_bytes = _directory_usage(temporary_assets_dir)
                if initial_asset_count > MAX_IMAGES_PER_DOCUMENT or initial_asset_bytes > MAX_EXTRACTED_ASSET_BYTES:
                    raise ResourceBudgetExceeded("O checkpoint excede o orçamento de imagens da conversão.")
                for page_number in selected:
                    self._check_resource_budget(output_dir, source_size, deadline)
                    checkpoint_entry = None
                    if checkpoint_state is not None:
                        checkpoint_pages = checkpoint_state.get("pages", {})
                        if isinstance(checkpoint_pages, dict):
                            checkpoint_entry = checkpoint_pages.get(str(page_number))
                    checkpoint_page = (
                        checkpoint_dir / "pages" / f"{page_number:06}.md"
                        if checkpoint_dir is not None
                        else None
                    )
                    if isinstance(checkpoint_entry, dict) and checkpoint_page is not None and checkpoint_page.is_file():
                        status = str(checkpoint_entry.get("status", "failed"))
                        if status not in {"native", "ocr", "fallback", "empty", "failed"}:
                            raise RuntimeError("Checkpoint de conversão contém status inválido.")
                        warning = str(checkpoint_entry.get("warning", ""))
                        rendered_pages.append(checkpoint_page.read_text(encoding="utf-8"))
                        coverage.append(PageCoverage(page_number, status, warning))
                        continue
                    page_index = page_number - 1
                    try:
                        page = document.load_page(page_index)
                    except Exception as error:
                        warning = self._safe_warning(error)
                        page_coverage = PageCoverage(page_number, "failed", warning)
                        rendered_page = self._format_page(page_number, "failed", "", warning)
                        coverage.append(page_coverage)
                        rendered_pages.append(rendered_page)
                        if checkpoint_dir is not None and checkpoint_state is not None:
                            _save_page_checkpoint(
                                checkpoint_dir,
                                checkpoint_state,
                                page_number,
                                rendered_page,
                                page_coverage,
                                image_count,
                            )
                        continue

                    image_count += self._page_image_count(page)
                    if image_count > MAX_IMAGES_PER_DOCUMENT:
                        raise ResourceBudgetExceeded(
                            f"O PDF excede o limite de {MAX_IMAGES_PER_DOCUMENT:,} imagens.".replace(",", ".")
                        )

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
                    page_coverage = PageCoverage(page_number, status, combined_warning)
                    rendered_page = self._format_page(page_number, status, text, combined_warning)
                    coverage.append(page_coverage)
                    rendered_pages.append(rendered_page)
                    if checkpoint_dir is not None and checkpoint_state is not None:
                        _save_page_checkpoint(
                            checkpoint_dir,
                            checkpoint_state,
                            page_number,
                            rendered_page,
                            page_coverage,
                            image_count,
                        )
                    asset_count, asset_bytes = _directory_usage(temporary_assets_dir)
                    if asset_count > MAX_IMAGES_PER_DOCUMENT:
                        raise ResourceBudgetExceeded(
                            f"A extração excedeu o limite de {MAX_IMAGES_PER_DOCUMENT:,} arquivos de imagem.".replace(",", ".")
                        )
                    if asset_bytes > MAX_EXTRACTED_ASSET_BYTES:
                        raise ResourceBudgetExceeded(
                            f"As imagens extraídas excederam {MAX_EXTRACTED_ASSET_BYTES // (1024 * 1024)} MB."
                        )
                    self._check_resource_budget(output_dir, source_size, deadline)

                markdown = "\n\n---\n\n".join(rendered_pages)
                self._check_resource_budget(output_dir, source_size, deadline)
                extraction_seconds = time.perf_counter() - extraction_start
        except Exception:
            if temporary_assets_dir is not None and checkpoint_dir is None:
                shutil.rmtree(temporary_assets_dir, ignore_errors=True)
            raise
        finally:
            if context_token is not None:
                _IMAGE_OUTPUT_CONTEXT.reset(context_token)

        if temporary_assets_dir is None:
            raise RuntimeError("Diretório temporário da conversão não foi inicializado.")
        asset_count = sum(1 for item in temporary_assets_dir.rglob("*") if item.is_file())
        finalization_assets_dir = temporary_assets_dir
        if checkpoint_dir is not None:
            finalization_assets_dir = Path(
                tempfile.mkdtemp(prefix=f".{reservation.assets_dir.name}.", dir=reservation.assets_dir.parent)
            )
            if asset_count:
                shutil.copytree(temporary_assets_dir, finalization_assets_dir, dirs_exist_ok=True)
        try:
            result = finalize_markdown(
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
                finalization_assets_dir,
            )
        except Exception:
            if checkpoint_dir is None:
                shutil.rmtree(finalization_assets_dir, ignore_errors=True)
            raise
        if checkpoint_dir is not None:
            shutil.rmtree(checkpoint_dir, ignore_errors=True)
        return result

    @staticmethod
    def _page_image_count(page: object) -> int:
        try:
            return len(page.get_images(full=True))
        except TypeError:
            try:
                return len(page.get_images())
            except Exception:
                return 0
        except Exception:
            return 0

    @staticmethod
    def _check_resource_budget(output_dir: Path, source_size: int, deadline: float) -> None:
        if time.monotonic() > deadline:
            raise ResourceBudgetExceeded(
                f"A conversão excedeu o prazo de {MAX_CONVERSION_SECONDS // 60} minutos por documento."
            )
        rss = current_process_rss_bytes()
        if rss is not None and rss > MAX_CONVERSION_MEMORY_BYTES:
            raise ResourceBudgetExceeded(
                f"A conversão excedeu o limite de memória de {MAX_CONVERSION_MEMORY_BYTES // (1024 * 1024)} MB."
            )
        required = required_free_disk_bytes(source_size)
        free = shutil.disk_usage(output_dir).free
        if free < required:
            raise ResourceBudgetExceeded(
                "Espaço livre insuficiente no destino: "
                f"são necessários ao menos {required // (1024 * 1024)} MB livres."
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
    checkpoint_dir: Path | None = None,
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
            True,
            checkpoint_dir,
        )
    except Exception as error:
        return ConversionFailure(
            source=source,
            error_message=str(error),
            details=traceback.format_exc(),
        )
