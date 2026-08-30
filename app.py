"""Ponto de entrada do conversor local de PDF para Markdown."""

from __future__ import annotations

import ctypes
import json
import logging
import multiprocessing
import sys
import time
import traceback
from pathlib import Path
from typing import Any

import webview

from constants import (
    APP_NAME,
    APP_VERSION,
    DEFAULT_MAX_CHUNK_CHARACTERS,
    DEFAULT_OUTPUT_DIR,
    resource_root,
)
from converter import PdfMarkdownConverter, validate_runtime_dependencies
from file_authorization import AuthorizedResourceRegistry
from licensing import LicenseRequiredError, require_software_activation
from models import (
    BatchConversionSummary,
    ConversionFailure,
    ConversionResult,
    build_summary_message,
)
from production_diagnostics import configure_production_diagnostics, diagnostic_status, log_startup_failure
from web_api import BridgeApi

logger = logging.getLogger(__name__)


def _release_probe_worker(result_queue: Any) -> None:
    """Alvo importável usado para validar multiprocessing com spawn no executável."""
    result_queue.put({"pid": multiprocessing.current_process().pid, "spawn": True})


def run_release_probe(report_path: Path) -> int:
    """Executa verificações não interativas diretamente no artefato PyInstaller."""
    checks: dict[str, Any] = {}
    failures: list[str] = []
    try:
        validate_runtime_dependencies()
        checks["runtime_dependencies"] = True
    except Exception as error:
        checks["runtime_dependencies"] = False
        failures.append(f"runtime: {error}")

    try:
        import webview.platforms.edgechromium  # noqa: F401

        checks["edgechromium_backend_import"] = True
    except Exception as error:
        checks["edgechromium_backend_import"] = False
        failures.append(f"edgechromium: {error}")

    context = multiprocessing.get_context("spawn")
    result_queue = context.Queue()
    process = context.Process(target=_release_probe_worker, args=(result_queue,), name="ReleaseSpawnProbe")
    try:
        process.start()
        process.join(timeout=30.0)
        if process.is_alive():
            process.terminate()
            process.join(timeout=5.0)
            raise TimeoutError("o processo spawn não finalizou em 30 segundos")
        child_result = result_queue.get(timeout=2.0)
        if process.exitcode != 0 or not child_result.get("spawn"):
            raise RuntimeError(f"processo spawn retornou exitcode={process.exitcode}")
        checks["multiprocessing_spawn"] = True
        checks["spawn_child_pid"] = child_result["pid"]
    except Exception as error:
        checks["multiprocessing_spawn"] = False
        failures.append(f"spawn: {error}")
    finally:
        result_queue.close()

    payload = {
        "ok": not failures,
        "app_version": APP_VERSION,
        "executable": str(Path(sys.executable).resolve()),
        "checks": checks,
        "failures": failures,
    }
    report_path = report_path.expanduser().resolve()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = report_path.with_suffix(f"{report_path.suffix}.tmp")
    try:
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(report_path)
    finally:
        temporary.unlink(missing_ok=True)
    return 0 if payload["ok"] else 1


def _show_message(message: str, *, error: bool) -> None:
    """Exibe aviso nativo sem depender de Tcl/Tk no executável empacotado."""
    if sys.platform == "win32":
        icon = 0x10 if error else 0x40
        ctypes.windll.user32.MessageBoxW(None, message, APP_NAME, icon)
        return
    print(message, file=sys.stderr if error else sys.stdout)


def run_quick_convert(paths: list[str]) -> None:
    """Converte PDFs recebidos por linha de comando (ex.: "Enviar para" do
    Explorer), sem abrir a janela principal — pensado para uso pontual."""
    try:
        require_software_activation()
    except LicenseRequiredError as error:
        logger.warning("Conversão rápida bloqueada por licença: %s", error)
        _show_message(str(error), error=True)
        return

    try:
        validate_runtime_dependencies()
    except RuntimeError as error:
        logger.error("Dependências de runtime indisponíveis: %s", error, exc_info=True)
        _show_message(str(error), error=True)
        return

    try:
        DEFAULT_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        logger.error("Falha de permissão ou disco na pasta de saída: %s", error, exc_info=True)
        _show_message(f"Não foi possível criar a pasta de saída:\n{error}", error=True)
        return

    batch_start = time.perf_counter()
    converter = PdfMarkdownConverter()
    resources = AuthorizedResourceRegistry()
    output_resource = resources.register(
        DEFAULT_OUTPUT_DIR,
        kind="directory",
        origin="send_to_cli",
        capabilities={"write"},
    )
    authorized_output = resources.resolve(
        output_resource.resource_id,
        kind="directory",
        capability="write",
    )
    successes: list[ConversionResult] = []
    failures: list[ConversionFailure] = []
    for raw_path in paths:
        source = Path(raw_path).expanduser().resolve()
        try:
            if source.suffix.casefold() != ".pdf" or not source.is_file():
                raise ValueError("O arquivo recebido não é um PDF válido.")
            source_resource = resources.register(
                source,
                kind="pdf",
                origin="send_to_cli",
                capabilities={"read", "convert"},
            )
            authorized_source = resources.resolve(
                source_resource.resource_id,
                kind="pdf",
                capability="convert",
            )
            successes.append(
                converter.convert(
                    authorized_source,
                    authorized_output,
                    False,
                    DEFAULT_MAX_CHUNK_CHARACTERS,
                )
            )
        except Exception as error:
            logger.error("Falha na conversão rápida do PDF %s: %s", source.name, error, exc_info=True)
            failures.append(ConversionFailure(source=source, error_message=str(error), details=traceback.format_exc()))

    summary = BatchConversionSummary(successes, failures)
    elapsed_seconds = time.perf_counter() - batch_start
    message = build_summary_message("Conversão concluída.", str(DEFAULT_OUTPUT_DIR), summary, elapsed_seconds)
    if successes:
        _show_message(message, error=False)
    else:
        _show_message(message, error=True)


def configure_shutdown_handlers(window: Any, api: BridgeApi) -> None:
    """Bloqueia o fechamento até que os serviços locais tenham parado com segurança."""

    def handle_closing() -> bool:
        if api.has_active_work():
            confirmed = window.create_confirmation_dialog(
                "Conversão ou indexação em andamento",
                "Deseja encerrar o aplicativo? O progresso concluído será preservado "
                "e a fila poderá ser retomada na próxima abertura.",
            )
            if not confirmed:
                return False
        if not api.shutdown_for_close(timeout_seconds=15.0):
            window.create_confirmation_dialog(
                "Encerramento ainda em andamento",
                "Uma operação local ainda está finalizando uma escrita. Aguarde alguns segundos e tente fechar novamente.",
            )
            return False
        logger.info("Encerramento coordenado concluído")
        return True

    window.events.closing += handle_closing
    window.events.closed += lambda: api.shutdown_for_close(timeout_seconds=2.0)


def run_gui() -> None:
    """Inicia a interface gráfica moderna em Chromium com Edge WebView2."""
    api = BridgeApi()
    logger.info("Inicializando interface WebView2")
    html_path = resource_root() / "web" / "index.html"

    if not html_path.exists():
        raise FileNotFoundError(f"Arquivo da interface gráfica não encontrado em: {html_path}")

    window = webview.create_window(
        title=f"{APP_NAME} — v{APP_VERSION}",
        url=html_path.as_uri(),
        js_api=api,
        width=1040,
        height=820,
        min_size=(800, 640),
        background_color="#F0F7FF",
    )
    api.set_window(window)
    configure_shutdown_handlers(window, api)
    webview.start(gui="edgechromium", debug=False)


def main() -> int:
    try:
        configure_production_diagnostics()
    except Exception:
        # O diagnóstico jamais deve impedir o uso em um perfil somente leitura.
        pass
    argv_paths = sys.argv[1:]
    if len(argv_paths) == 2 and argv_paths[0] == "--release-probe":
        return run_release_probe(Path(argv_paths[1]))
    if argv_paths:
        run_quick_convert(argv_paths)
        return 0

    try:
        validate_runtime_dependencies()
        run_gui()
        return 0
    except Exception as error:
        log_startup_failure(error)
        status = diagnostic_status()
        diagnostic_message = (
            f"Registro de diagnóstico: {status['log_file']}"
            if status["configured"]
            else "O registro persistente de diagnóstico não pôde ser inicializado."
        )
        _show_message(
            f"Erro ao iniciar o aplicativo:\n\n{error}\n\n"
            f"{diagnostic_message}",
            error=True,
        )
        return 1


if __name__ == "__main__":
    # Obrigatório para o executável empacotado pelo PyInstaller: sem isso, um
    # processo filho do ProcessPoolExecutor (usado na conversão em lote
    # paralela) reexecutaria o .exe inteiro e poderia reabrir a interface
    # gráfica recursivamente.
    multiprocessing.freeze_support()
    raise SystemExit(main())
