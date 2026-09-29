"""Camada Bridge Python-JavaScript para a interface Chromium (PyWebView)."""

from __future__ import annotations

import json
import logging
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import FIRST_COMPLETED, Future, ProcessPoolExecutor, ThreadPoolExecutor, wait
from concurrent.futures.process import BrokenProcessPool
from pathlib import Path
from typing import Any

import edge_tts
import fitz
from deep_translator import GoogleTranslator

from api_bridge import (
    ConversionMixin,
    EditorMixin,
    LibraryMixin,
    OnlineMixin,
    ReaderMixin,
    ResourceHelperMixin,
    SystemMixin,
)
from api_bridge.common import (
    CONVERSION_JOURNAL_PATH,
    MAX_PARALLEL_WORKERS,
    MIN_CHUNK_CHARACTERS,
    _atomic_write_json,
    _fit_textbox_rect,
    _flush_file,
    _license_denial,
    _parse_color,
    _pdf_backup_path,
    _safe_close,
    _save_doc_safely,
    _validate_annotation_payload,
    _validate_pixel_budget,
    _validate_saved_pdf,
    _validated_clip_rect,
    _validated_dpi,
    format_file_size,
)
from app_storage import data_directory
from constants import (
    APP_NAME,
    APP_VERSION,
    CURRENT_TERMS_VERSION,
    DEFAULT_MAX_CHUNK_CHARACTERS,
    DEFAULT_OUTPUT_DIR,
    DISK_SPACE_SOURCE_MULTIPLIER,
    MAX_ANNOTATION_FONT_SIZE,
    MAX_ANNOTATION_TEXT_CHARACTERS,
    MAX_ANNOTATIONS_PER_OPERATION,
    MAX_CONVERSION_MEMORY_BYTES,
    MAX_CONVERSION_SECONDS,
    MAX_EXTRACTED_ASSET_BYTES,
    MAX_IMAGES_PER_DOCUMENT,
    MAX_PAGE_COUNT,
    MAX_PDF_COORDINATE,
    MAX_PDF_FILE_SIZE_BYTES,
    MAX_POINTS_PER_STROKE,
    MAX_RENDER_DPI,
    MAX_RENDER_PIXEL_AREA,
    MAX_SNIPPET_AREA_POINTS,
    MAX_STROKE_POINTS_PER_OPERATION,
    MAX_STROKE_WIDTH,
    MAX_STROKES_PER_ANNOTATION,
    MAX_TRANSLATION_CHARACTERS,
    MAX_TTS_CHARACTERS,
    MEMORY_RESERVATION_PER_WORKER_BYTES,
    MIN_FREE_DISK_BYTES,
    MIN_RENDER_DPI,
    ONLINE_SERVICE_BACKOFF_SECONDS,
    ONLINE_SERVICE_CIRCUIT_FAILURE_THRESHOLD,
    ONLINE_SERVICE_CIRCUIT_RESET_SECONDS,
    ONLINE_SERVICE_MAX_ATTEMPTS,
    TRANSLATION_CHUNK_CHARACTERS,
    TRANSLATION_TIMEOUT_SECONDS,
    TTS_CHUNK_CHARACTERS,
    TTS_TIMEOUT_SECONDS,
    user_data_root,
)
from converter import (
    PdfMarkdownConverter,
    ResourceBudgetExceeded,
    available_memory_bytes,
    convert_worker,
    init_worker,
    process_rss_bytes,
    validate_runtime_dependencies,
)
from file_authorization import AuthorizedResourceRegistry, ResourceAccessError
from library_db import LibraryDatabase
from license_core import LicenseAccessError, LicenseState
from licensing import (
    LicenseRequiredError,
    activate_act4_license,
    get_license_status,
    require_software_activation,
)
from markdown_utils import HeadingProfile, SplitMode, reserve_batch_output_paths
from models import ConversionFailure, ConversionResult, OutputReservation, format_duration
from online_services import CircuitBreaker, ServiceCircuitOpen, ServiceOperationTimeout, call_with_resilience
from production_diagnostics import (
    build_diagnostic_report,
    diagnostic_status,
    write_diagnostic_report,
)

__all__ = (
    "APP_NAME",
    "APP_VERSION",
    "AuthorizedResourceRegistry",
    "BridgeApi",
    "BrokenProcessPool",
    "CircuitBreaker",
    "ConversionFailure",
    "ConversionMixin",
    "ConversionResult",
    "CONVERSION_JOURNAL_PATH",
    "CURRENT_TERMS_VERSION",
    "DEFAULT_MAX_CHUNK_CHARACTERS",
    "DEFAULT_OUTPUT_DIR",
    "DISK_SPACE_SOURCE_MULTIPLIER",
    "EditorMixin",
    "FIRST_COMPLETED",
    "Future",
    "GoogleTranslator",
    "HeadingProfile",
    "LibraryDatabase",
    "LibraryMixin",
    "LicenseAccessError",
    "LicenseRequiredError",
    "LicenseState",
    "MAX_ANNOTATION_FONT_SIZE",
    "MAX_ANNOTATION_TEXT_CHARACTERS",
    "MAX_ANNOTATIONS_PER_OPERATION",
    "MAX_CONVERSION_MEMORY_BYTES",
    "MAX_CONVERSION_SECONDS",
    "MAX_EXTRACTED_ASSET_BYTES",
    "MAX_IMAGES_PER_DOCUMENT",
    "MAX_PAGE_COUNT",
    "MAX_PARALLEL_WORKERS",
    "MAX_PDF_COORDINATE",
    "MAX_PDF_FILE_SIZE_BYTES",
    "MAX_POINTS_PER_STROKE",
    "MAX_RENDER_DPI",
    "MAX_RENDER_PIXEL_AREA",
    "MAX_SNIPPET_AREA_POINTS",
    "MAX_STROKE_POINTS_PER_OPERATION",
    "MAX_STROKE_WIDTH",
    "MAX_STROKES_PER_ANNOTATION",
    "MAX_TRANSLATION_CHARACTERS",
    "MAX_TTS_CHARACTERS",
    "MEMORY_RESERVATION_PER_WORKER_BYTES",
    "MIN_CHUNK_CHARACTERS",
    "MIN_FREE_DISK_BYTES",
    "MIN_RENDER_DPI",
    "ONLINE_SERVICE_BACKOFF_SECONDS",
    "ONLINE_SERVICE_CIRCUIT_FAILURE_THRESHOLD",
    "ONLINE_SERVICE_CIRCUIT_RESET_SECONDS",
    "ONLINE_SERVICE_MAX_ATTEMPTS",
    "OnlineMixin",
    "OutputReservation",
    "PdfMarkdownConverter",
    "ProcessPoolExecutor",
    "ReaderMixin",
    "ResourceAccessError",
    "ResourceBudgetExceeded",
    "ResourceHelperMixin",
    "ServiceCircuitOpen",
    "ServiceOperationTimeout",
    "SplitMode",
    "SystemMixin",
    "TRANSLATION_CHUNK_CHARACTERS",
    "TRANSLATION_TIMEOUT_SECONDS",
    "TTS_CHUNK_CHARACTERS",
    "TTS_TIMEOUT_SECONDS",
    "ThreadPoolExecutor",
    "_atomic_write_json",
    "_fit_textbox_rect",
    "_flush_file",
    "_license_denial",
    "_parse_color",
    "_pdf_backup_path",
    "_safe_close",
    "_save_doc_safely",
    "_validate_annotation_payload",
    "_validate_pixel_budget",
    "_validate_saved_pdf",
    "_validated_clip_rect",
    "_validated_dpi",
    "activate_act4_license",
    "available_memory_bytes",
    "build_diagnostic_report",
    "call_with_resilience",
    "convert_worker",
    "data_directory",
    "diagnostic_status",
    "edge_tts",
    "fitz",
    "format_duration",
    "format_file_size",
    "get_license_status",
    "init_worker",
    "json",
    "os",
    "process_rss_bytes",
    "require_software_activation",
    "reserve_batch_output_paths",
    "shutil",
    "subprocess",
    "time",
    "user_data_root",
    "validate_runtime_dependencies",
    "wait",
    "write_diagnostic_report",
)

logger = logging.getLogger(__name__)


class BridgeApi(
    ResourceHelperMixin,
    SystemMixin,
    LibraryMixin,
    ReaderMixin,
    EditorMixin,
    ConversionMixin,
    OnlineMixin,
):
    """API exposta para o JavaScript via window.pywebview.api."""

    def __init__(
        self,
        conversion_journal_path: Path | str | None = None,
        *,
        library_database_path: Path | str | None = None,
    ) -> None:
        self._window: Any = None
        self.cancel_requested = threading.Event()
        self.resume_processing = threading.Event()
        self.resume_processing.set()
        self.is_paused = False
        self.is_converting = False
        self.conversion_state = "stopped"
        self._conversion_state_lock = threading.Lock()
        self._active_checkpoint_dirs: set[Path] = set()
        self._active_checkpoint_lock = threading.Lock()
        self._shutdown_requested = False
        self._batch_start_time = 0.0
        self._conversion_thread: threading.Thread | None = None
        self._conversion_finished = threading.Event()
        self._conversion_finished.set()
        self._journal_path = (
            Path(conversion_journal_path).resolve()
            if conversion_journal_path is not None
            else CONVERSION_JOURNAL_PATH.resolve()
        )
        self._journal_lock = threading.Lock()
        self._recovery_token: str | None = None
        self._recovery_payload: dict[str, Any] | None = None
        self._resuming_interrupted = False
        self._close_lock = threading.Lock()
        self._services_shutdown = False
        self._pdf_passwords: dict[str, str] = {}
        try:
            current_mod = sys.modules.get(__name__)
            lib_cls = getattr(current_mod, "LibraryDatabase", LibraryDatabase)
            self._library = (
                lib_cls()
                if library_database_path is None
                else lib_cls(library_database_path)
            )
            self._library_status = dict(self._library.recovery_status)
            self._library_status["persistent"] = True
        except (OSError, sqlite3.Error) as error:
            logger.error("Acervo persistente indisponível; iniciando armazenamento temporário: %s", error)
            fallback_path = Path(tempfile.gettempdir()) / "NexoJuris" / f"acervo-temporario-{os.getpid()}.db"
            current_mod = sys.modules.get(__name__)
            lib_cls = getattr(current_mod, "LibraryDatabase", LibraryDatabase)
            self._library = lib_cls(fallback_path)
            self._library_status = {
                "state": "degraded",
                "persistent": False,
                "database_path": str(fallback_path),
                "error": str(error),
                "recovered_from": None,
                "quarantined_path": None,
            }
        self._resources = AuthorizedResourceRegistry()
        self._default_output_dir = DEFAULT_OUTPUT_DIR
        try:
            self._default_output_resource = self._register_directory(self._default_output_dir, "default_output")
        except OSError:
            self._default_output_dir = user_data_root() / "PDFs Convertidos"
            self._default_output_resource = self._register_directory(self._default_output_dir, "default_output_fallback")
        self._indexing_cancel_events: dict[str, threading.Event] = {}
        self._indexing_pause_events: dict[str, threading.Event] = {}
        self._indexing_threads: dict[str, threading.Thread] = {}
        self._indexing_lock = threading.Lock()
        self._page_indexing_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="PageIndex")
        self._pending_page_indexes: set[tuple[str, int]] = set()
        self._page_indexing_lock = threading.Lock()
        self._online_service_breakers = {
            "translation": CircuitBreaker(
                ONLINE_SERVICE_CIRCUIT_FAILURE_THRESHOLD,
                ONLINE_SERVICE_CIRCUIT_RESET_SECONDS,
            ),
            "tts": CircuitBreaker(
                ONLINE_SERVICE_CIRCUIT_FAILURE_THRESHOLD,
                ONLINE_SERVICE_CIRCUIT_RESET_SECONDS,
            ),
        }
