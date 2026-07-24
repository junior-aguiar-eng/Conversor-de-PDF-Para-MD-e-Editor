"""Interface gráfica e coordenação da fila de conversão."""

from __future__ import annotations

import os
import queue
import threading
import traceback
from pathlib import Path
from tkinter import BooleanVar, StringVar, Tk, filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText
from typing import Literal, TypeAlias

from constants import APP_NAME
from converter import PdfMarkdownConverter, validate_runtime_dependencies
from models import BatchConversionSummary, ConversionFailure, ConversionResult


EventKind: TypeAlias = Literal["status", "file_error", "done", "stopped", "error"]
UiEvent: TypeAlias = tuple[EventKind, object]


class App:
    def __init__(self, root: Tk) -> None:
        self.root = root
        self.root.title(APP_NAME)
        self.root.minsize(760, 620)
        self.root.geometry("920x760")
        self.files: list[Path] = []
        self.results_by_source: dict[Path, ConversionResult] = {}
        self.failures_by_source: dict[Path, ConversionFailure] = {}
        self.output_dir = StringVar(value=str(Path.home() / "Documents" / "PDF para Markdown"))
        self.split_output = BooleanVar(value=False)
        self.max_chunk_characters = StringVar(value="60000")
        self.events: queue.Queue[UiEvent] = queue.Queue()
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
        ttk.Button(file_actions, text="Remover selecionados", command=self.remove_selected).pack(
            side="left", padx=8
        )
        ttk.Button(file_actions, text="Limpar", command=self.clear_files).pack(side="left")

        destination = ttk.LabelFrame(frame, text="Pasta de saída", padding=10)
        destination.grid(row=3, column=0, sticky="ew", pady=(12, 0))
        destination.columnconfigure(0, weight=1)
        ttk.Entry(destination, textvariable=self.output_dir).grid(row=0, column=0, sticky="ew")
        ttk.Button(destination, text="Escolher pasta", command=self.choose_output).grid(
            row=0, column=1, padx=(8, 0)
        )

        mode_box = ttk.LabelFrame(frame, text="Opções de saída", padding=10)
        mode_box.grid(row=4, column=0, sticky="ew", pady=(12, 0))
        ttk.Checkbutton(
            mode_box,
            text="Gerar partes por títulos # e ## quando passar de",
            variable=self.split_output,
        ).grid(row=0, column=0, sticky="w")
        ttk.Entry(mode_box, width=8, textvariable=self.max_chunk_characters).grid(
            row=0, column=1, padx=(6, 4)
        )
        ttk.Label(mode_box, text="caracteres (60.000 recomendado)").grid(
            row=0, column=2, sticky="w"
        )

        actions = ttk.Frame(frame)
        actions.grid(row=5, column=0, sticky="ew", pady=(12, 0))
        self.convert_button = ttk.Button(
            actions, text="Converter para Markdown", command=self.start_conversion
        )
        self.convert_button.pack(side="left")
        self.pause_button = ttk.Button(
            actions, text="Pausar", command=self.toggle_pause, state="disabled"
        )
        self.pause_button.pack(side="left", padx=(8, 0))
        self.stop_button = ttk.Button(
            actions, text="Parar", command=self.request_stop, state="disabled"
        )
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
        if not self.files:
            messagebox.showwarning(APP_NAME, "Selecione pelo menos um PDF.")
            return
        try:
            validate_runtime_dependencies()
        except RuntimeError as error:
            messagebox.showerror(APP_NAME, str(error))
            return
        try:
            max_chunk_characters = int(self.max_chunk_characters.get())
            if max_chunk_characters < 1_000:
                raise ValueError
        except ValueError:
            messagebox.showwarning(
                APP_NAME,
                "O limite das partes deve ser um número inteiro de pelo menos 1.000 caracteres.",
            )
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
        self.status.set("Preparando o conversor local...")
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
        self.status.set("Parada solicitada: concluindo o PDF atual...")

    def _convert_in_background(
        self, files: list[Path], output_dir: Path, split_output: bool, max_chunk_characters: int
    ) -> None:
        try:
            converter = PdfMarkdownConverter()
            successes: list[ConversionResult] = []
            failures: list[ConversionFailure] = []
            for index, source in enumerate(files, start=1):
                if self.cancel_requested.is_set():
                    self.events.put(("stopped", BatchConversionSummary(successes, failures)))
                    return
                while not self.resume_processing.wait(timeout=0.2):
                    if self.cancel_requested.is_set():
                        self.events.put(("stopped", BatchConversionSummary(successes, failures)))
                        return
                self.events.put(("status", f"Convertendo {index}/{len(files)}: {source.name}"))
                try:
                    result = converter.convert(source, output_dir, split_output, max_chunk_characters)
                except Exception as error:
                    failures.append(
                        ConversionFailure(
                            source=source,
                            error_message=str(error),
                            details=traceback.format_exc(),
                        )
                    )
                    self.events.put(("file_error", failures[-1]))
                    continue
                successes.append(result)
            summary = BatchConversionSummary(successes, failures)
            if self.cancel_requested.is_set():
                self.events.put(("stopped", summary))
            else:
                self.events.put(("done", summary))
        except Exception as error:
            self.events.put(("error", (error, traceback.format_exc())))

    def _process_events(self) -> None:
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "status":
                    self.status.set(str(payload))
                elif kind == "file_error":
                    failure = payload
                    self.failures_by_source[failure.source] = failure
                    self.write_log(f"ERRO  {failure.source.name}: {failure.error_message}")
                elif kind == "done":
                    self._handle_batch_completion("Concluído", "Conversão concluída.", payload)
                elif kind == "stopped":
                    self._handle_batch_completion("Fila interrompida", "Fila interrompida.", payload)
                    self.write_log("Fila interrompida pelo usuário; arquivos restantes não foram processados.")
                elif kind == "error":
                    error, details = payload
                    self._finish()
                    self.status.set("A conversão foi interrompida.")
                    self.write_log(f"ERRO  {error}\n{details}")
                    messagebox.showerror(
                        APP_NAME,
                        f"Não foi possível concluir a conversão:\n\n{error}\n\nConsulte o registro na janela.",
                    )
        except queue.Empty:
            pass
        self.root.after(120, self._process_events)

    def _handle_batch_completion(
        self, status_prefix: str, dialog_title: str, summary: BatchConversionSummary
    ) -> None:
        self._finish()
        self._record_results(summary.successes)
        self._record_failures(summary.failures)
        self._set_batch_status(status_prefix, summary)
        self._write_summary_log(summary)
        messagebox.showinfo(APP_NAME, self._build_summary_message(dialog_title, summary))

    def _write_summary_log(self, summary: BatchConversionSummary) -> None:
        for result in summary.successes:
            self.write_log(
                f"OK  {result.source.name} -> {result.markdown_path} ({result.asset_count} imagem(ns), {result.chunk_count} parte(s))"
            )
        for failure in summary.failures:
            self.write_log(f"ERRO  {failure.source.name}: {failure.error_message}")

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

    def _record_failures(self, failures: list[ConversionFailure]) -> None:
        self.failures_by_source.update({failure.source: failure for failure in failures})

    def _set_batch_status(self, prefix: str, summary: BatchConversionSummary) -> None:
        self.status.set(
            f"{prefix}: {len(summary.successes)} convertido(s), {len(summary.failures)} com erro."
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

    def write_log(self, message: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", message + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")
