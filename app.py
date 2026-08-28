"""Ponto de entrada do conversor local de PDF para Markdown."""

from __future__ import annotations

import multiprocessing
import sys
import time
import traceback
from pathlib import Path
from tkinter import Tk, messagebox

import webview

from constants import (
    APP_NAME,
    APP_VERSION,
    DEFAULT_MAX_CHUNK_CHARACTERS,
    DEFAULT_OUTPUT_DIR,
    resource_root,
)
from converter import PdfMarkdownConverter, validate_runtime_dependencies
from licensing import LicenseRequiredError, require_software_activation
from models import (
    BatchConversionSummary,
    ConversionFailure,
    ConversionResult,
    build_summary_message,
)
from web_api import BridgeApi


def run_quick_convert(paths: list[str]) -> None:
    """Converte PDFs recebidos por linha de comando (ex.: "Enviar para" do
    Explorer), sem abrir a janela principal — pensado para uso pontual."""
    root = Tk()
    root.withdraw()
    try:
        require_software_activation()
    except LicenseRequiredError as error:
        messagebox.showerror(APP_NAME, str(error))
        root.destroy()
        return

    try:
        validate_runtime_dependencies()
    except RuntimeError as error:
        messagebox.showerror(APP_NAME, str(error))
        root.destroy()
        return

    try:
        DEFAULT_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        messagebox.showerror(APP_NAME, f"Não foi possível criar a pasta de saída:\n{error}")
        root.destroy()
        return

    batch_start = time.perf_counter()
    converter = PdfMarkdownConverter()
    successes: list[ConversionResult] = []
    failures: list[ConversionFailure] = []
    for raw_path in paths:
        source = Path(raw_path)
        try:
            successes.append(converter.convert(source, DEFAULT_OUTPUT_DIR, False, DEFAULT_MAX_CHUNK_CHARACTERS))
        except Exception as error:
            failures.append(ConversionFailure(source=source, error_message=str(error), details=traceback.format_exc()))

    summary = BatchConversionSummary(successes, failures)
    elapsed_seconds = time.perf_counter() - batch_start
    message = build_summary_message("Conversão concluída.", str(DEFAULT_OUTPUT_DIR), summary, elapsed_seconds)
    if successes:
        messagebox.showinfo(APP_NAME, message)
    else:
        messagebox.showerror(APP_NAME, message)
    root.destroy()


def run_gui() -> None:
    """Inicia a interface gráfica moderna em Chromium com Edge WebView2."""
    api = BridgeApi()
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
    webview.start(gui="edgechromium", debug=False)


def main() -> None:
    argv_paths = sys.argv[1:]
    if argv_paths:
        run_quick_convert(argv_paths)
        return

    try:
        validate_runtime_dependencies()
        run_gui()
    except Exception as error:
        root = Tk()
        root.withdraw()
        messagebox.showerror(
            APP_NAME,
            f"Erro ao iniciar o aplicativo:\n\n{error}\n\nDetalhes:\n{traceback.format_exc()}",
        )
        root.destroy()


if __name__ == "__main__":
    # Obrigatório para o executável empacotado pelo PyInstaller: sem isso, um
    # processo filho do ProcessPoolExecutor (usado na conversão em lote
    # paralela) reexecutaria o .exe inteiro e poderia reabrir a interface
    # gráfica recursivamente.
    multiprocessing.freeze_support()
    main()
