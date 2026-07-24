"""Ponto de entrada do conversor local de PDF para Markdown."""

from __future__ import annotations

from tkinter import Tk, messagebox, ttk

from constants import APP_NAME
from converter import validate_runtime_dependencies
from ui import App


def main() -> None:
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
    main()
