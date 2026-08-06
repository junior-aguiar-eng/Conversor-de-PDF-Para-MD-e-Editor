"""Ponto de entrada do conversor local de PDF para Markdown."""

from __future__ import annotations

import multiprocessing
import sys
import traceback
from pathlib import Path
from tkinter import Tk, messagebox, ttk

from constants import APP_NAME, DEFAULT_MAX_CHUNK_CHARACTERS, DEFAULT_OUTPUT_DIR
from converter import PdfMarkdownConverter, validate_runtime_dependencies
from models import BatchConversionSummary, ConversionFailure, ConversionResult
from ui import App, build_summary_message


def run_quick_convert(paths: list[str]) -> None:
    """Converte PDFs recebidos por linha de comando (ex.: "Enviar para" do
    Explorer), sem abrir a janela principal — pensado para uso pontual."""
    root = Tk()
    root.withdraw()
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

    converter = PdfMarkdownConverter()
    successes: list[ConversionResult] = []
    failures: list[ConversionFailure] = []
    for raw_path in paths:
        source = Path(raw_path)
        try:
            successes.append(
                converter.convert(source, DEFAULT_OUTPUT_DIR, False, DEFAULT_MAX_CHUNK_CHARACTERS)
            )
        except Exception as error:
            failures.append(
                ConversionFailure(source=source, error_message=str(error), details=traceback.format_exc())
            )

    summary = BatchConversionSummary(successes, failures)
    message = build_summary_message("Conversão concluída.", str(DEFAULT_OUTPUT_DIR), summary)
    if successes:
        messagebox.showinfo(APP_NAME, message)
    else:
        messagebox.showerror(APP_NAME, message)
    root.destroy()


def main() -> None:
    argv_paths = sys.argv[1:]
    if argv_paths:
        run_quick_convert(argv_paths)
        return

    root = Tk()
    root.withdraw()
    try:
        validate_runtime_dependencies()
    except RuntimeError as error:
        messagebox.showerror(APP_NAME, str(error))
        root.destroy()
        return
    try:
        ttk.Style().theme_use("vista")
    except Exception:
        pass
    root.deiconify()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    # Obrigatório para o executável empacotado pelo PyInstaller: sem isso, um
    # processo filho do ProcessPoolExecutor (usado na conversão em lote
    # paralela) reexecutaria o .exe inteiro e poderia reabrir a interface
    # gráfica recursivamente.
    multiprocessing.freeze_support()
    main()
