"""Interface gráfica e coordenação da fila de conversão."""

from __future__ import annotations

import os
import queue
import threading
import traceback
from dataclasses import dataclass
from pathlib import Path
from tkinter import BooleanVar, StringVar, Tk, filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText
from typing import Literal

from constants import APP_NAME, DEFAULT_MAX_CHUNK_CHARACTERS, DEFAULT_OUTPUT_DIR
from converter import PdfMarkdownConverter, validate_runtime_dependencies
from models import BatchConversionSummary, ConversionFailure, ConversionResult

type EventKind = Literal["status", "progress", "file_error", "done", "stopped", "error"]
type UiEvent = tuple[EventKind, object]
MIN_CHUNK_CHARACTERS = 1_000


@dataclass(frozen=True)
class ConversionRequest:
    files: list[Path]
    output_dir: Path
    split_output: bool
    max_chunk_characters: int


class App:
    def __init__(self, root: Tk) -> None:
        self.root = root
        self.root.title(APP_NAME)
        self.root.minsize(760, 620)
        self.root.geometry("960x780")
        self._configure_style()
        self.files: list[Path] = []
        self.results_by_source: dict[Path, ConversionResult] = {}
        self.failures_by_source: dict[Path, ConversionFailure] = {}
        self.output_dir = StringVar(value=str(DEFAULT_OUTPUT_DIR))
        self.split_output = BooleanVar(value=False)
        self.max_chunk_characters = StringVar(value=str(DEFAULT_MAX_CHUNK_CHARACTERS))
        self.events: queue.Queue[UiEvent] = queue.Queue()
        self.cancel_requested = threading.Event()
        self.resume_processing = threading.Event()
        self.resume_processing.set()
        self.is_paused = False
        self._build()
        self.root.after(120, self._process_events)

    def _configure_style(self) -> None:
        background = "#F4F7FB"
        surface = "#FFFFFF"
        border = "#D8E0EA"
        accent = "#176B87"
        text = "#17324D"

        self.root.configure(background=background)
        style = ttk.Style(self.root)
        style.configure("App.TFrame", background=background)
        style.configure("Header.TFrame", background=background)
        style.configure("Title.TLabel", background=background, foreground=text, font=("Segoe UI", 18, "bold"))
        style.configure("Subtitle.TLabel", background=background, foreground="#5B6B7C", font=("Segoe UI", 10))
        style.configure("Section.TLabelframe", background=background, bordercolor=border, relief="solid")
        style.configure("Section.TLabelframe.Label", background=background, foreground=text, font=("Segoe UI", 10, "bold"))
        style.configure("Accent.TButton", font=("Segoe UI", 10, "bold"), padding=(14, 8))
        style.configure("Secondary.TButton", padding=(11, 7))
        style.configure(
            "Treeview", background=surface, fieldbackground=surface, foreground=text, rowheight=30, font=("Segoe UI", 10)
        )
        style.configure("Treeview.Heading", font=("Segoe UI", 10, "bold"))
        style.map("Treeview", background=[("selected", "#D9EEF3")], foreground=[("selected", text)])
        style.configure(
            "Horizontal.TProgressbar",
            troughcolor="#E1E8F0",
            background=accent,
            bordercolor="#E1E8F0",
            lightcolor=accent,
            darkcolor=accent,
        )

    def _build(self) -> None:
        frame = ttk.Frame(self.root, padding=20, style="App.TFrame")
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(2, weight=4)
        frame.rowconfigure(6, weight=1)

        header = ttk.Frame(frame, style="Header.TFrame")
        header.grid(row=0, column=0, sticky="ew")
        ttk.Label(header, text=APP_NAME, style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            header,
            text="Conversão local de PDFs digitais para Markdown, sem alterar os originais.",
            style="Subtitle.TLabel",
        ).pack(anchor="w", pady=(3, 0))
        ttk.Separator(frame, orient="horizontal").grid(row=1, column=0, sticky="ew", pady=(16, 14))

        files_box = ttk.LabelFrame(frame, text="PDFs selecionados", padding=12, style="Section.TLabelframe")
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

        file_actions = ttk.Frame(files_box, style="App.TFrame")
        file_actions.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        self.add_files_button = ttk.Button(
            file_actions, text="Adicionar PDFs", command=self.choose_files, style="Accent.TButton"
        )
        self.add_files_button.pack(side="left")
        ttk.Button(file_actions, text="Remover selecionados", command=self.remove_selected, style="Secondary.TButton").pack(
            side="left", padx=8
        )
        ttk.Button(file_actions, text="Limpar", command=self.clear_files, style="Secondary.TButton").pack(side="left")

        destination = ttk.LabelFrame(frame, text="Pasta de saída", padding=12, style="Section.TLabelframe")
        destination.grid(row=3, column=0, sticky="ew", pady=(14, 0))
        destination.columnconfigure(0, weight=1)
        ttk.Entry(destination, textvariable=self.output_dir).grid(row=0, column=0, sticky="ew")
        self.choose_output_button = ttk.Button(
            destination, text="Escolher pasta", command=self.choose_output, style="Secondary.TButton"
        )
        self.choose_output_button.grid(row=0, column=1, padx=(8, 0))

        mode_box = ttk.LabelFrame(frame, text="Opções de saída", padding=12, style="Section.TLabelframe")
        mode_box.grid(row=4, column=0, sticky="ew", pady=(14, 0))
        ttk.Checkbutton(
            mode_box,
            text="Gerar partes por títulos # e ## quando passar de",
            variable=self.split_output,
        ).grid(row=0, column=0, sticky="w")
        ttk.Entry(mode_box, width=8, textvariable=self.max_chunk_characters).grid(
            row=0, column=1, padx=(6, 4)
        )
        ttk.Label(mode_box, text=f"caracteres ({DEFAULT_MAX_CHUNK_CHARACTERS:,} recomendado)").grid(
            row=0, column=2, sticky="w"
        )

        actions = ttk.Frame(frame, style="App.TFrame")
        actions.grid(row=5, column=0, sticky="ew", pady=(14, 0))
        self.convert_button = ttk.Button(
            actions, text="Converter para Markdown", command=self.start_conversion, style="Accent.TButton"
        )
        self.convert_button.pack(side="left")
        self.pause_button = ttk.Button(
            actions, text="Pausar", command=self.toggle_pause, state="disabled", style="Secondary.TButton"
        )
        self.pause_button.pack(side="left", padx=(8, 0))
        self.stop_button = ttk.Button(
            actions, text="Parar", command=self.request_stop, state="disabled", style="Secondary.TButton"
        )
        self.stop_button.pack(side="left", padx=(8, 0))
        self.open_result_button = ttk.Button(
            actions,
            text="Abrir Markdown selecionado",
            command=self.open_selected_result,
            state="disabled",
            style="Secondary.TButton",
        )
        self.open_result_button.pack(side="left", padx=(8, 0))
        self.progress = ttk.Progressbar(actions, mode="determinate", length=150, maximum=100)
        self.progress.pack(side="left", padx=(16, 8))
        self.progress_label = StringVar(value="0%")
        ttk.Label(actions, textvariable=self.progress_label, width=5, style="Subtitle.TLabel").pack(side="left")
        self.status = StringVar(value="Selecione um ou mais PDFs para começar.")
        ttk.Label(actions, textvariable=self.status, style="Subtitle.TLabel").pack(side="left", padx=(12, 0))

        log_box = ttk.LabelFrame(frame, text="Atividade", padding=8, style="Section.TLabelframe")
        log_box.grid(row=6, column=0, sticky="nsew", pady=(14, 0))
        log_box.columnconfigure(0, weight=1)
        log_box.rowconfigure(0, weight=1)
        self.log = ScrolledText(
            log_box,
            height=7,
            state="disabled",
            wrap="word",
            background="#FFFFFF",
            foreground="#17324D",
            relief="flat",
            font=("Consolas", 10),
        )
        self.log.grid(row=0, column=0, sticky="nsew")

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
        self.failures_by_source.clear()
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
            failure = self.failures_by_source.get(source)
            if failure is not None:
                messagebox.showinfo(
                    APP_NAME,
                    f"Esse PDF falhou nesta sessão:\n\n{failure.error_message}",
                )
                return
            messagebox.showinfo(APP_NAME, "Esse PDF ainda não foi convertido nesta sessão.")
            return

        try:
            os.startfile(result.markdown_path)  # type: ignore[attr-defined]
        except OSError as error:
            messagebox.showerror(APP_NAME, f"Não foi possível abrir o Markdown:\n{error}")

    def start_conversion(self) -> None:
        try:
            request = self._build_conversion_request()
        except ValueError:
            return

        self._begin_conversion(request)
        self.status.set("Preparando o conversor local...")
        thread = threading.Thread(target=self._convert_in_background, args=(request,), daemon=True)
        thread.start()

    def toggle_pause(self) -> None:
        if self.is_paused:
            self.resume_processing.set()
            self.is_paused = False
            self.pause_button.configure(text="Pausar")
            self.status.set(f"Conversão retomada ({self.progress_label.get()}).")
            return

        self.resume_processing.clear()
        self.is_paused = True
        self.pause_button.configure(text="Retomar")
        self.status.set(
            f"Pausa solicitada: será aplicada antes do próximo PDF ({self.progress_label.get()})."
        )

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
        self.status.set(
            f"Parada solicitada: concluindo o PDF atual ({self.progress_label.get()})."
        )

    def _build_conversion_request(self) -> ConversionRequest:
        if not self.files:
            messagebox.showwarning(APP_NAME, "Selecione pelo menos um PDF.")
            raise ValueError("no files")

        try:
            validate_runtime_dependencies()
        except RuntimeError as error:
            messagebox.showerror(APP_NAME, str(error))
            raise ValueError("runtime validation failed") from error

        return ConversionRequest(
            files=self.files.copy(),
            output_dir=self._ensure_output_directory(),
            split_output=self.split_output.get(),
            max_chunk_characters=self._parse_max_chunk_characters(),
        )

    def _parse_max_chunk_characters(self) -> int:
        try:
            max_chunk_characters = int(self.max_chunk_characters.get())
        except ValueError as error:
            self._show_chunk_limit_warning()
            raise ValueError("invalid chunk limit") from error

        if max_chunk_characters < MIN_CHUNK_CHARACTERS:
            self._show_chunk_limit_warning()
            raise ValueError("chunk limit too small")
        return max_chunk_characters

    def _show_chunk_limit_warning(self) -> None:
        messagebox.showwarning(
            APP_NAME,
            "O limite das partes deve ser um número inteiro de pelo menos 1.000 caracteres.",
        )

    def _ensure_output_directory(self) -> Path:
        output_dir = Path(self.output_dir.get()).expanduser()
        try:
            output_dir.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            messagebox.showerror(APP_NAME, f"Não foi possível criar a pasta de saída:\n{error}")
            raise ValueError("invalid output directory") from error
        return output_dir

    def _begin_conversion(self, request: ConversionRequest) -> None:
        self.convert_button.configure(state="disabled")
        self.pause_button.configure(state="normal", text="Pausar")
        self.stop_button.configure(state="normal")
        # Bloqueados durante a conversão: abrem diálogos nativos do Windows
        # enquanto a thread de conversão muda o diretório de trabalho do processo.
        self.add_files_button.configure(state="disabled")
        self.choose_output_button.configure(state="disabled")
        self.cancel_requested.clear()
        self.resume_processing.set()
        self.is_paused = False
        self._set_progress(0, len(request.files))

    def _convert_in_background(
        self,
        files: ConversionRequest | list[Path],
        output_dir: Path | None = None,
        split_output: bool | None = None,
        max_chunk_characters: int | None = None,
    ) -> None:
        current_request = self._normalize_request(
            files,
            output_dir,
            split_output,
            max_chunk_characters,
        )
        try:
            converter = PdfMarkdownConverter()
            successes: list[ConversionResult] = []
            failures: list[ConversionFailure] = []
            total = len(current_request.files)
            for index, source in enumerate(current_request.files, start=1):
                if self._stop_requested(successes, failures):
                    return
                if self._wait_until_resumed(successes, failures):
                    return

                self.events.put(("status", f"Convertendo {index}/{total}: {source.name}"))
                result = self._convert_single_file(
                    converter,
                    source,
                    current_request.output_dir,
                    current_request.split_output,
                    current_request.max_chunk_characters,
                )
                if isinstance(result, ConversionFailure):
                    failures.append(result)
                    self.events.put(("file_error", result))
                else:
                    successes.append(result)
                self._emit_progress(index, total)

            summary = BatchConversionSummary(successes, failures)
            if self.cancel_requested.is_set():
                self.events.put(("stopped", summary))
            else:
                self.events.put(("done", summary))
        except Exception as error:
            self.events.put(("error", (error, traceback.format_exc())))

    def _normalize_request(
        self,
        files: ConversionRequest | list[Path],
        output_dir: Path | None,
        split_output: bool | None,
        max_chunk_characters: int | None,
    ) -> ConversionRequest:
        if isinstance(files, ConversionRequest):
            return files
        return ConversionRequest(
            files=files,
            output_dir=output_dir if output_dir is not None else Path(),
            split_output=bool(split_output),
            max_chunk_characters=(
                max_chunk_characters
                if max_chunk_characters is not None
                else MIN_CHUNK_CHARACTERS
            ),
        )

    def _stop_requested(
        self,
        successes: list[ConversionResult],
        failures: list[ConversionFailure],
    ) -> bool:
        if not self.cancel_requested.is_set():
            return False
        self.events.put(("stopped", BatchConversionSummary(successes, failures)))
        return True

    def _wait_until_resumed(
        self,
        successes: list[ConversionResult],
        failures: list[ConversionFailure],
    ) -> bool:
        while not self.resume_processing.wait(timeout=0.2):
            if self._stop_requested(successes, failures):
                return True
        return False

    def _convert_single_file(
        self,
        converter: PdfMarkdownConverter,
        source: Path,
        output_dir: Path,
        split_output: bool,
        max_chunk_characters: int,
    ) -> ConversionResult | ConversionFailure:
        try:
            return converter.convert(
                source,
                output_dir,
                split_output,
                max_chunk_characters,
            )
        except Exception as error:
            return ConversionFailure(
                source=source,
                error_message=str(error),
                details=traceback.format_exc(),
            )

    def _emit_progress(
        self,
        completed: int,
        total: int,
    ) -> None:
        self.events.put(("progress", (completed, total)))

    def _process_events(self) -> None:
        try:
            while True:
                kind, payload = self.events.get_nowait()
                self._handle_event(kind, payload)
        except queue.Empty:
            pass
        self.root.after(120, self._process_events)

    def _handle_event(self, kind: EventKind, payload: object) -> None:
        if kind == "status":
            self.status.set(str(payload))
            return
        if kind == "progress":
            completed, total = payload
            self._set_progress(completed, total)
            return
        if kind == "file_error":
            failure = payload
            self.failures_by_source[failure.source] = failure
            self.write_log(f"ERRO  {failure.source.name}: {failure.error_message}")
            return
        if kind == "done":
            self._handle_batch_completion("Concluído", "Conversão concluída.", payload)
            return
        if kind == "stopped":
            self._handle_batch_completion("Fila interrompida", "Fila interrompida.", payload)
            self.write_log("Fila interrompida pelo usuário; arquivos restantes não foram processados.")
            return

        error, details = payload
        self._finish()
        self.status.set("A conversão foi interrompida.")
        self.write_log(f"ERRO  {error}\n{details}")
        messagebox.showerror(
            APP_NAME,
            f"Não foi possível concluir a conversão:\n\n{error}\n\nConsulte o registro na janela.",
        )

    def _handle_batch_completion(
        self,
        status_prefix: str,
        dialog_title: str,
        summary: BatchConversionSummary,
    ) -> None:
        self._finish()
        self._record_results(summary.successes)
        self._record_failures(summary.failures)
        self._set_batch_status(status_prefix, summary)
        self._write_summary_log(summary)
        messagebox.showinfo(APP_NAME, self._build_summary_message(dialog_title, summary))

    def _write_summary_log(self, summary: BatchConversionSummary) -> None:
        # As falhas já são escritas em tempo real pelo evento "file_error";
        # aqui só faltam os sucessos, que não têm evento próprio.
        for result in summary.successes:
            self.write_log(
                f"OK  {result.source.name} -> {result.markdown_path} "
                f"({result.asset_count} imagem(ns), {result.chunk_count} parte(s))"
            )

    def _finish(self) -> None:
        self.convert_button.configure(state="normal")
        self.pause_button.configure(state="disabled", text="Pausar")
        self.stop_button.configure(state="disabled")
        self.add_files_button.configure(state="normal")
        self.choose_output_button.configure(state="normal")
        self.is_paused = False

    def _record_results(self, results: list[ConversionResult]) -> None:
        self.results_by_source.update({result.source: result for result in results})
        if not results:
            return
        first_index = self.files.index(results[0].source)
        self.file_list.selection_set(str(first_index))
        self.file_list.focus(str(first_index))
        self.open_result_button.configure(state="normal")

    def _record_failures(self, failures: list[ConversionFailure]) -> None:
        self.failures_by_source.update({failure.source: failure for failure in failures})

    def _set_batch_status(self, prefix: str, summary: BatchConversionSummary) -> None:
        self.status.set(
            f"{prefix}: {len(summary.successes)} convertido(s), {len(summary.failures)} com erro ({self.progress_label.get()})."
        )

    def _build_summary_message(self, title: str, summary: BatchConversionSummary) -> str:
        message = [
            title,
            "",
            f"Convertidos: {len(summary.successes)}",
            f"Com erro: {len(summary.failures)}",
        ]
        if summary.successes:
            message.extend(["", f"Arquivos salvos em:\n{self.output_dir.get()}"])
        if summary.failures:
            failed_names = "\n".join(f"- {failure.source.name}" for failure in summary.failures[:10])
            message.extend(["", f"Falhas nesta execução:\n{failed_names}"])
            if len(summary.failures) > 10:
                message.append(f"... e mais {len(summary.failures) - 10} arquivo(s).")
        return "\n".join(message)

    def _set_progress(self, completed: int, total: int) -> None:
        percent = 0 if total <= 0 else round((completed / total) * 100)
        self.progress.configure(maximum=max(total, 1), value=completed)
        self.progress_label.set(f"{percent}%")

    def write_log(self, message: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", message + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")
