"""Diagnóstico persistente e exportável para builds sem console."""

from __future__ import annotations

import faulthandler
import importlib.metadata
import json
import logging
import os
import platform
import re
import shutil
import sys
import threading
import time
import traceback
import uuid
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from constants import APP_NAME, APP_VERSION, user_data_root

LOG_MAX_BYTES = 2 * 1024 * 1024
LOG_BACKUP_COUNT = 5
REPORT_LOG_LIMIT_BYTES = 256 * 1024
SESSION_ID = uuid.uuid4().hex[:12]

_configured = False
_configuration_lock = threading.Lock()
_crash_file: Any = None
_previous_sys_hook: Any = None
_previous_thread_hook: Any = None
_active_directory: Path | None = None


class _SessionFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.session_id = SESSION_ID
        return True


def diagnostics_directory() -> Path:
    return user_data_root() / "diagnostics"


def log_path() -> Path:
    return diagnostics_directory() / "nexojuris.log"


def _install_exception_hooks() -> None:
    global _previous_sys_hook, _previous_thread_hook
    logger = logging.getLogger("diagnostics.crash")
    _previous_sys_hook = sys.excepthook
    _previous_thread_hook = threading.excepthook

    def sys_hook(exc_type: type[BaseException], exc_value: BaseException, exc_tb: Any) -> None:
        logger.critical("Falha não tratada na thread principal", exc_info=(exc_type, exc_value, exc_tb))
        if _previous_sys_hook and _previous_sys_hook is not sys_hook:
            _previous_sys_hook(exc_type, exc_value, exc_tb)

    def thread_hook(args: threading.ExceptHookArgs) -> None:
        logger.critical(
            "Falha não tratada na thread %s",
            getattr(args.thread, "name", "desconhecida"),
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )
        if _previous_thread_hook and _previous_thread_hook is not thread_hook:
            _previous_thread_hook(args)

    sys.excepthook = sys_hook
    threading.excepthook = thread_hook


def configure_production_diagnostics(*, directory: Path | None = None) -> dict[str, Any]:
    """Configura logs rotativos e captura de falhas Python/nativas uma única vez."""
    global _configured, _crash_file, _active_directory
    with _configuration_lock:
        if _configured:
            return diagnostic_status()

        destination = (directory or diagnostics_directory()).resolve()
        destination.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            destination / "nexojuris.log",
            maxBytes=LOG_MAX_BYTES,
            backupCount=LOG_BACKUP_COUNT,
            encoding="utf-8",
            delay=True,
        )
        handler.addFilter(_SessionFilter())
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)sZ %(levelname)s session=%(session_id)s "
                "process=%(process)d thread=%(threadName)s logger=%(name)s %(message)s",
                datefmt="%Y-%m-%dT%H:%M:%S",
            )
        )
        handler.formatter.converter = time.gmtime
        root = logging.getLogger()
        root.setLevel(logging.DEBUG)
        root.addHandler(handler)
        logging.captureWarnings(True)
        _install_exception_hooks()

        crash_path = destination / "native-crash.log"
        if crash_path.is_file() and crash_path.stat().st_size >= LOG_MAX_BYTES:
            os.replace(crash_path, destination / "native-crash.previous.log")
        _crash_file = crash_path.open("a", encoding="utf-8")
        try:
            faulthandler.enable(file=_crash_file, all_threads=True)
        except (OSError, RuntimeError):
            logging.getLogger(__name__).warning("Não foi possível ativar o registro de falhas nativas", exc_info=True)

        _active_directory = destination
        _configured = True
        logging.getLogger(__name__).info(
            "Aplicativo iniciado app_version=%s python=%s platform=%s frozen=%s",
            APP_VERSION,
            platform.python_version(),
            platform.platform(),
            bool(getattr(sys, "frozen", False)),
        )
        return diagnostic_status(directory=destination)


def diagnostic_status(*, directory: Path | None = None) -> dict[str, Any]:
    destination = (directory or _active_directory or diagnostics_directory()).resolve()
    return {
        "configured": _configured,
        "session_id": SESSION_ID,
        "directory": str(destination),
        "log_file": str(destination / "nexojuris.log"),
        "native_crash_file": str(destination / "native-crash.log"),
        "rotation_max_bytes": LOG_MAX_BYTES,
        "rotation_backup_count": LOG_BACKUP_COUNT,
    }


def _dependency_versions() -> dict[str, str]:
    versions: dict[str, str] = {}
    for distribution in (
        "PyMuPDF",
        "onnxruntime",
        "rapidocr-onnxruntime",
        "edge-tts",
        "deep-translator",
        "pywebview",
    ):
        try:
            versions[distribution] = importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError:
            versions[distribution] = "not-installed"
    return versions


_SECRET_PATTERN = re.compile(
    r"(?i)\b(password|senha|token|license[_ -]?key|chave)\b(\s*[=:]\s*)([^\s,;]+)"
)
_MACHINE_ID_PATTERN = re.compile(r"(?i)\bNXJ(?:-[A-Z0-9]{4}){4}\b")


def _sanitize(text: str) -> str:
    sanitized = text
    home = str(Path.home().resolve())
    if home:
        sanitized = re.sub(re.escape(home), "%USERPROFILE%", sanitized, flags=re.IGNORECASE)
    sanitized = _SECRET_PATTERN.sub(r"\1\2[REDACTED]", sanitized)
    return _MACHINE_ID_PATTERN.sub("NXJ-[REDACTED]", sanitized)


def _read_recent_logs(directory: Path) -> str:
    candidates = sorted(
        directory.glob("nexojuris.log*"),
        key=lambda item: item.stat().st_mtime,
    )
    payload = bytearray()
    for candidate in reversed(candidates):
        try:
            content = candidate.read_bytes()
        except OSError:
            continue
        remaining = REPORT_LOG_LIMIT_BYTES - len(payload)
        if remaining <= 0:
            break
        payload[:0] = content[-remaining:]
    return _sanitize(payload[-REPORT_LOG_LIMIT_BYTES:].decode("utf-8", errors="replace"))


def _classify_recent_events(logs: str) -> dict[str, int]:
    patterns = {
        "pdf_or_conversion": re.compile(r"(?i)\b(pdf|fitz|conversion|conversão|document=)\b"),
        "memory_or_budget": re.compile(r"(?i)\b(memory|memória|memoryerror|orçamento|budget)\b"),
        "onnx_or_ocr": re.compile(r"(?i)\b(onnx|rapidocr|ocr)\b"),
        "external_service": re.compile(r"(?i)\b(edge-tts|googletranslator|tradução|online service|serviço online)\b"),
        "permission_or_disk": re.compile(r"(?i)\b(permission|permissionerror|acesso negado|somente leitura|disco)\b"),
        "sqlite_or_storage": re.compile(r"(?i)\b(sqlite|integrity_check|database|banco|acervo)\b"),
    }
    lines = [line for line in logs.splitlines() if " ERROR " in line or " CRITICAL " in line or " WARNING " in line]
    return {name: sum(bool(pattern.search(line)) for line in lines) for name, pattern in patterns.items()}


def build_diagnostic_report(
    *,
    library_status: dict[str, Any] | None = None,
    online_services: dict[str, Any] | None = None,
    directory: Path | None = None,
) -> str:
    """Gera relatório sem conteúdo documental, senhas ou identificadores de licença."""
    destination = (directory or diagnostics_directory()).resolve()
    try:
        disk = shutil.disk_usage(user_data_root())
        disk_status: dict[str, Any] = {"free_bytes": disk.free, "total_bytes": disk.total}
    except OSError as error:
        disk_status = {"error": type(error).__name__}
    recent_logs = _read_recent_logs(destination)
    metadata = {
        "generated_at": datetime.now(UTC).isoformat(),
        "application": APP_NAME,
        "app_version": APP_VERSION,
        "session_id": SESSION_ID,
        "platform": platform.platform(),
        "python": platform.python_version(),
        "architecture": platform.machine(),
        "frozen": bool(getattr(sys, "frozen", False)),
        "process_id": os.getpid(),
        "dependencies": _dependency_versions(),
        "storage": library_status or {},
        "online_services": online_services or {},
        "disk": disk_status,
        "recent_event_categories": _classify_recent_events(recent_logs),
        "privacy": "Não inclui conteúdo dos PDFs, texto extraído, senhas ou chaves de licença.",
    }
    native_crash = ""
    for crash_name in ("native-crash.previous.log", "native-crash.log"):
        try:
            native_crash += (destination / crash_name).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
    native_crash = _sanitize(native_crash[-REPORT_LOG_LIMIT_BYTES:])
    return (
        "NEXOJURIS - RELATÓRIO DE DIAGNÓSTICO\n"
        "===================================\n"
        f"{_sanitize(json.dumps(metadata, ensure_ascii=False, indent=2, default=str))}\n\n"
        "LOGS RECENTES (caminho do perfil sanitizado)\n"
        "--------------------------------------------\n"
        f"{recent_logs or '[nenhum log disponível]'}\n\n"
        "REGISTRO DE FALHA NATIVA\n"
        "-------------------------\n"
        f"{native_crash or '[nenhuma falha nativa registrada]'}\n"
    )


def write_diagnostic_report(destination: Path, report: str) -> None:
    destination = destination.expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(report, encoding="utf-8")
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def log_startup_failure(error: BaseException) -> None:
    logging.getLogger("diagnostics.startup").critical(
        "Falha durante a inicialização: %s\n%s",
        error,
        "".join(traceback.format_exception(type(error), error, error.__traceback__)),
    )
