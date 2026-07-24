"""Conversor local e leve de PDF digital para Markdown."""

from __future__ import annotations

import queue
import re
import threading
import traceback
import os
from dataclasses import dataclass
from pathlib import Path
from tkinter import BooleanVar, StringVar, Tk, filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText


APP_NAME = "Boni - Conversor de PDF para Markdown"


@dataclass(frozen=True)
class ConversionResult:
    source: Path
    markdown_path: Path
    asset_count: int
    chunk_count: int


HEADING_PATTERN = re.compile(r"(?m)^#{1,2}\s+.+?\s*$")


def split_markdown_by_headings(markdown: str, max_characters: int) -> list[str]:
    """Divide textos longos em blocos, respeitando títulos # e ##."""
    if len(markdown) <= max_characters:
        return []

    headings = list(HEADING_PATTERN.finditer(markdown))
    if not headings:
        return []

    sections: list[str] = []
    preamble = markdown[: headings[0].start()].strip()
    for index, heading in enumerate(headings):
        end = headings[index + 1].start() if index + 1 < len(headings) else len(markdown)
        section = markdown[heading.start() : end].strip()
        if index == 0 and preamble:
            section = f"{preamble}\n\n{section}"
        sections.append(section)

    chunks: list[str] = []
    current = ""
    for section in sections:
        if current and len(current) + len(section) + 2 > max_characters:
            chunks.append(current.strip())
            current = section
        else:
            current = f"{current}\n\n{section}".strip() if current else section
    if current:
        chunks.append(current.strip())
    return chunks


def available_output_path(output_dir: Path, stem: str) -> Path:
    """Retorna um caminho livre, sem substituir uma conversão já existente."""
    candidate = output_dir / f"{stem}.md"
    index = 2
    while candidate.exists():
        candidate = output_dir / f"{stem} ({index}).md"
        index += 1
    return candidate


def output_paths(output_dir: Path, source: Path) -> tuple[Path, Path]:
    markdown_path = available_output_path(output_dir, source.stem)
    return markdown_path, output_dir / "images" / markdown_path.stem


def finalize_markdown(
    source: Path,
    markdown_path: Path,
    markdown: str,
    asset_count: int,
    split_output: bool,
    max_chunk_characters: int,
) -> ConversionResult:
    markdown_path.write_text(markdown, encoding="utf-8")
    chunks = split_markdown_by_headings(markdown, max_chunk_characters) if split_output else []
    if chunks:
        chunks_dir = markdown_path.parent / f"{markdown_path.stem}_partes"
        chunks_dir.mkdir(parents=True, exist_ok=True)
        for index, chunk in enumerate(chunks, start=1):
            portable_chunk = chunk.replace("images/", "../images/")
            (chunks_dir / f"parte_{index:03}.md").write_text(portable_chunk, encoding="utf-8")
    return ConversionResult(source, markdown_path, asset_count, len(chunks))


class PdfMarkdownConverter:
    """Conversor único, leve e local para PDFs com texto nativo."""

    def __init__(self) -> None:
        try:
            import pymupdf4llm
        except ImportError as error:
            raise RuntimeError(
                "O conversor não está instalado. Execute instalar_no_d.ps1 para atualizá-lo."
            ) from error
        self._to_markdown = pymupdf4llm.to_markdown

    def convert(self, source: Path, output_dir: Path, split_output: bool, max_chunk_characters: int) -> ConversionResult:
        markdown_path, assets_dir = output_paths(output_dir, source)
        assets_dir.mkdir(parents=True, exist_ok=True)
        markdown = self._to_markdown(
            str(source),
            use_ocr=False,
            force_ocr=False,
            write_images=True,
            image_path=str(assets_dir),
            header=False,
            footer=False,
        )
        absolute_assets = str(assets_dir).replace("\\", "/")
        relative_assets = f"images/{markdown_path.stem}"
        markdown = markdown.replace(absolute_assets, relative_assets).replace(str(assets_dir), relative_assets)
        asset_count = sum(1 for item in assets_dir.rglob("*") if item.is_file())
        if asset_count == 0:
            assets_dir.rmdir()
            parent = assets_dir.parent
            if not any(parent.iterdir()):
                parent.rmdir()
        return finalize_markdown(source, markdown_path, markdown, asset_count, split_output, max_chunk_characters)
class App:
    def __init__(self, root: Tk) -> None:
        self.root = root
        self.root.title(APP_NAME)
        self.root.minsize(760, 620)
        self.root.geometry("920x760")
        self.files: list[Path] = []
        self.results_by_source: dict[Path, ConversionResult] = {}
        self.output_dir = StringVar(value=str(Path.home() / "Documents" / "PDF para Markdown"))
        self.split_output = BooleanVar(value=False)
        self.max_chunk_characters = StringVar(value="60000")
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.cancel_requested = threading.Event()
        self.resume_processing = threading.Event()
        self.resume_processing.set()
        self.is_paused = False
        self._build()
        self.root.after(120, self._process_events)

    def _build(self) -> None:
        frame = ttk.Frame(self.root, padding=16)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(2, weight=4)
        frame.rowconfigure(6, weight=1)

        ttk.Label(frame, text=APP_NAME, font=("Segoe UI", 16, "bold")).grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(
            frame,
            text="Conversão local e leve de PDFs digitais. Os PDFs originais não são modificados.",
        ).grid(row=1, column=0, sticky="w", pady=(2, 12))

        files_box = ttk.LabelFrame(frame, text="PDFs selecionados", padding=10)
        files_box.grid(row=2, column=0, sticky="nsew")
        files_box.columnconfigure(0, weight=1)
        files_box.rowconfigure(0, weight=1)
        self.file_list = ttk.Treeview(files_box, columns=("arquivo",), show="headings", height=14)
        self.file_list.heading("arquivo", text="Arquivo")
        self.file_list.column("arquivo", width=620, anchor="w")
        self.file_list.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(files_box, orient="vertical", command=self.file_list.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.file_list.configure(yscrollcommand=scrollbar.set)
        file_actions = ttk.Frame(files_box)
        file_actions.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        ttk.Button(file_actions, text="Adicionar PDFs", command=self.choose_files).pack(side="left")
        ttk.Button(file_actions, text="Remover selecionados", command=self.remove_selected).pack(side="left", padx=8)
        ttk.Button(file_actions, text="Limpar", command=self.clear_files).pack(side="left")

        destination = ttk.LabelFrame(frame, text="Pasta de saída", padding=10)
        destination.grid(row=3, column=0, sticky="ew", pady=(12, 0))
        destination.columnconfigure(0, weight=1)
        ttk.Entry(destination, textvariable=self.output_dir).grid(row=0, column=0, sticky="ew")
        ttk.Button(destination, text="Escolher pasta", command=self.choose_output).grid(row=0, column=1, padx=(8, 0))
        mode_box = ttk.LabelFrame(frame, text="Opções de saída", padding=10)
        mode_box.grid(row=4, column=0, sticky="ew", pady=(12, 0))
        ttk.Checkbutton(
            mode_box,
            text="Gerar partes por títulos # e ## quando passar de",
            variable=self.split_output,
        ).grid(row=0, column=0, sticky="w")
        ttk.Entry(mode_box, width=8, textvariable=self.max_chunk_characters).grid(row=0, column=1, padx=(6, 4))
        ttk.Label(mode_box, text="caracteres (60.000 recomendado)").grid(row=0, column=2, sticky="w")

        actions = ttk.Frame(frame)
        actions.grid(row=5, column=0, sticky="ew", pady=(12, 0))
        self.convert_button = ttk.Button(actions, text="Converter para Markdown", command=self.start_conversion)
        self.convert_button.pack(side="left")
        self.pause_button = ttk.Button(actions, text="Pausar", command=self.toggle_pause, state="disabled")
        self.pause_button.pack(side="left", padx=(8, 0))
        self.stop_button = ttk.Button(actions, text="Parar", command=self.request_stop, state="disabled")
        self.stop_button.pack(side="left", padx=(8, 0))
        self.open_result_button = ttk.Button(
            actions,
            text="Abrir Markdown selecionado",
            command=self.open_selected_result,
            state="disabled",
        )
        self.open_result_button.pack(side="left", padx=(8, 0))
        self.progress = ttk.Progressbar(actions, mode="indeterminate", length=160)
        self.progress.pack(side="left", padx=12)
        self.status = StringVar(value="Selecione um ou mais PDFs para começar.")
        ttk.Label(actions, textvariable=self.status).pack(side="left")

        self.log = ScrolledText(frame, height=7, state="disabled", wrap="word")
        self.log.grid(row=6, column=0, sticky="nsew", pady=(12, 0))

    def choose_files(self) -> None:
        paths = filedialog.askopenfilenames(title="Selecionar PDFs", filetypes=[("PDF", "*.pdf")])
        known = {path.resolve() for path in self.files}
        for raw_path in paths:
            path = Path(raw_path)
            if path.resolve() not in known:
                self.files.append(path)
                known.add(path.resolve())
        self.refresh_files()

    def choose_output(self) -> None:
        selected = filedialog.askdirectory(title="Escolher pasta de saída")
        if selected:
            self.output_dir.set(selected)

    def refresh_files(self) -> None:
        self.file_list.delete(*self.file_list.get_children())
        for index, path in enumerate(self.files):
            self.file_list.insert("", "end", iid=str(index), values=(str(path),))
        self.status.set(f"{len(self.files)} PDF(s) selecionado(s).")

    def remove_selected(self) -> None:
        selected = sorted((int(item) for item in self.file_list.selection()), reverse=True)
        for index in selected:
            self.files.pop(index)
        self.refresh_files()

    def clear_files(self) -> None:
        self.files.clear()
        self.results_by_source.clear()
        self.open_result_button.configure(state="disabled")
        self.refresh_files()

    def open_selected_result(self) -> None:
        selected = self.file_list.selection()
        if not selected:
            messagebox.showinfo(APP_NAME, "Selecione na lista o PDF cujo Markdown deseja abrir.")
            return
        source = self.files[int(selected[0])]
        result = self.results_by_source.get(source)
        if result is None:
            messagebox.showinfo(APP_NAME, "Esse PDF ainda não foi convertido nesta sessão.")
            return
        try:
            os.startfile(result.markdown_path)  # type: ignore[attr-defined]  # Windows
        except OSError as error:
            messagebox.showerror(APP_NAME, f"Não foi possível abrir o Markdown:\n{error}")

    def start_conversion(self) -> None:
        if not self.files:
            messagebox.showwarning(APP_NAME, "Selecione pelo menos um PDF.")
            return
        try:
            max_chunk_characters = int(self.max_chunk_characters.get())
            if max_chunk_characters < 1_000:
                raise ValueError
        except ValueError:
            messagebox.showwarning(APP_NAME, "O limite das partes deve ser um número inteiro de pelo menos 1.000 caracteres.")
            return
        output_dir = Path(self.output_dir.get()).expanduser()
        try:
            output_dir.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            messagebox.showerror(APP_NAME, f"Não foi possível criar a pasta de saída:\n{error}")
            return
        self.convert_button.configure(state="disabled")
        self.pause_button.configure(state="normal", text="Pausar")
        self.stop_button.configure(state="normal")
        self.cancel_requested.clear()
        self.resume_processing.set()
        self.is_paused = False
        self.progress.start(10)
        self.status.set("Preparando o conversor local…")
        thread = threading.Thread(
            target=self._convert_in_background,
            args=(
                self.files.copy(),
                output_dir,
                self.split_output.get(),
                max_chunk_characters,
            ),
            daemon=True,
        )
        thread.start()

    def toggle_pause(self) -> None:
        if self.is_paused:
            self.resume_processing.set()
            self.is_paused = False
            self.pause_button.configure(text="Pausar")
            self.status.set("Conversão retomada.")
        else:
            self.resume_processing.clear()
            self.is_paused = True
            self.pause_button.configure(text="Retomar")
            self.status.set("Pausa solicitada: será aplicada antes do próximo PDF.")

    def request_stop(self) -> None:
        if not messagebox.askyesno(
            APP_NAME,
            "Parar a fila? O PDF atualmente em processamento será finalizado; os próximos não serão convertidos.",
        ):
            return
        self.cancel_requested.set()
        self.resume_processing.set()
        self.pause_button.configure(state="disabled", text="Pausar")
        self.stop_button.configure(state="disabled")
        self.status.set("Parada solicitada: concluindo o PDF atual…")

    def _convert_in_background(
        self, files: list[Path], output_dir: Path, split_output: bool, max_chunk_characters: int
    ) -> None:
        try:
            converter = PdfMarkdownConverter()
            results: list[ConversionResult] = []
            for index, source in enumerate(files, start=1):
                if self.cancel_requested.is_set():
                    self.events.put(("stopped", results))
                    return
                while not self.resume_processing.wait(timeout=0.2):
                    if self.cancel_requested.is_set():
                        self.events.put(("stopped", results))
                        return
                self.events.put(("status", f"Convertendo {index}/{len(files)}: {source.name}"))
                results.append(converter.convert(source, output_dir, split_output, max_chunk_characters))
            if self.cancel_requested.is_set():
                self.events.put(("stopped", results))
            else:
                self.events.put(("done", results))
        except Exception as error:  # Erros das dependências e de PDFs são mostrados na interface.
            self.events.put(("error", (error, traceback.format_exc())))

    def _process_events(self) -> None:
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "status":
                    self.status.set(str(payload))
                elif kind == "done":
                    results = payload
                    self._finish()
                    self._record_results(results)
                    self.status.set(f"Concluído: {len(results)} arquivo(s) convertido(s).")
                    for result in results:
                        self.write_log(f"OK  {result.source.name} → {result.markdown_path} ({result.asset_count} imagem(ns), {result.chunk_count} parte(s))")
                    messagebox.showinfo(APP_NAME, f"Conversão concluída.\n\nArquivos salvos em:\n{self.output_dir.get()}")
                elif kind == "stopped":
                    results = payload
                    self._finish()
                    self._record_results(results)
                    self.status.set(f"Fila interrompida: {len(results)} arquivo(s) convertido(s).")
                    for result in results:
                        self.write_log(f"OK  {result.source.name} → {result.markdown_path} ({result.asset_count} imagem(ns), {result.chunk_count} parte(s))")
                    self.write_log("Fila interrompida pelo usuário; arquivos restantes não foram processados.")
                    messagebox.showinfo(
                        APP_NAME,
                        f"Fila interrompida.\n\n{len(results)} arquivo(s) foram convertidos e permanecem na pasta de saída.",
                    )
                elif kind == "error":
                    error, details = payload
                    self._finish()
                    self.status.set("A conversão foi interrompida.")
                    self.write_log(f"ERRO  {error}\n{details}")
                    messagebox.showerror(APP_NAME, f"Não foi possível concluir a conversão:\n\n{error}\n\nConsulte o registro na janela.")
        except queue.Empty:
            pass
        self.root.after(120, self._process_events)

    def _finish(self) -> None:
        self.progress.stop()
        self.convert_button.configure(state="normal")
        self.pause_button.configure(state="disabled", text="Pausar")
        self.stop_button.configure(state="disabled")
        self.is_paused = False

    def _record_results(self, results: list[ConversionResult]) -> None:
        self.results_by_source.update({result.source: result for result in results})
        if not results:
            return
        first_index = self.files.index(results[0].source)
        self.file_list.selection_set(str(first_index))
        self.file_list.focus(str(first_index))
        self.open_result_button.configure(state="normal")

    def write_log(self, message: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", message + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")


def main() -> None:
    root = Tk()
    try:
        ttk.Style().theme_use("vista")
    except Exception:
        pass
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
