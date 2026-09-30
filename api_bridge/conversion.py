"""Mixin de execução e coordenação de conversão de PDFs em Markdown."""

from __future__ import annotations

import json
import logging
import os
import shutil
import threading
import time
import traceback
import uuid
from concurrent.futures import FIRST_COMPLETED, Future, ProcessPoolExecutor, wait
from concurrent.futures.process import BrokenProcessPool
from pathlib import Path
from typing import Any

from api_bridge.common import (
    MAX_PARALLEL_WORKERS,
    MIN_CHUNK_CHARACTERS,
    _atomic_write_json,
    _license_denial,
)
from constants import (
    DEFAULT_MAX_CHUNK_CHARACTERS,
    DISK_SPACE_SOURCE_MULTIPLIER,
    MAX_CONVERSION_MEMORY_BYTES,
    MAX_CONVERSION_SECONDS,
    MAX_PAGE_COUNT,
    MAX_PDF_FILE_SIZE_BYTES,
    MEMORY_RESERVATION_PER_WORKER_BYTES,
    MIN_FREE_DISK_BYTES,
)
from converter import (
    PdfMarkdownConverter,
    ResourceBudgetExceeded,
    available_memory_bytes,
    convert_worker,
    init_worker,
    process_rss_bytes,
)
from file_authorization import ResourceAccessError
from license_core import LicenseAccessError
from licensing import LicenseRequiredError
from markdown_utils import HeadingProfile, SplitMode, reserve_batch_output_paths
from models import ConversionFailure, ConversionResult, OutputReservation, format_duration
from production_diagnostics import get_telemetry_tracker

logger = logging.getLogger(__name__)


class ConversionMixin:
    """Métodos de ciclo de vida, journal e paralelismo da conversão em lote."""

    def _set_conversion_state(self, state: str, **details: Any) -> None:
        if state not in {"running", "pausing", "paused", "stopping", "stopped", "completed", "failed"}:
            raise ValueError(f"Estado de conversão inválido: {state}")
        with self._conversion_state_lock:
            self.conversion_state = state
            self.is_paused = state in {"pausing", "paused"}
        self._emit("conversion_state", {"state": state, **details})

    @staticmethod
    def _write_conversion_control(checkpoint_dir: Path, action: str) -> None:
        _atomic_write_json(checkpoint_dir / "control.json", {"action": action})

    def _control_active_conversions(self, action: str) -> None:
        with self._active_checkpoint_lock:
            checkpoints = tuple(self._active_checkpoint_dirs)
        for checkpoint in checkpoints:
            try:
                self._write_conversion_control(checkpoint, action)
            except OSError as error:
                logger.warning("Falha ao controlar conversão em %s: %s", checkpoint, error)

    def _sync_active_pause_status(self) -> None:
        if self.conversion_state != "pausing":
            return
        with self._active_checkpoint_lock:
            checkpoints = tuple(self._active_checkpoint_dirs)
        if not checkpoints:
            return
        pages: list[int] = []
        for checkpoint in checkpoints:
            try:
                status = json.loads((checkpoint / "control-status.json").read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError, TypeError):
                return
            if status.get("state") != "paused":
                return
            pages.append(int(status.get("page_number", 0)))
        page = pages[0] if len(pages) == 1 else min(pages)
        self._set_conversion_state("paused", page_number=page, active_documents=len(pages))
        message = (
            f"Pausado na página {page}."
            if len(pages) == 1
            else f"{len(pages)} documentos pausados em checkpoints de página."
        )
        self._emit("status", {"message": message})

    @staticmethod
    def _checkpoint_dir(reservation: OutputReservation) -> Path:
        return reservation.markdown_path.with_name(f".{reservation.markdown_path.name}.nexojuris-checkpoint")

    def _read_conversion_journal(self) -> dict[str, Any] | None:
        with self._journal_lock:
            try:
                payload = json.loads(self._journal_path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                return None
            except (OSError, json.JSONDecodeError, TypeError, ValueError) as error:
                logger.warning(f"Journal de conversão inválido: {error}")
                return None
        return payload if isinstance(payload, dict) and payload.get("version") == 1 else None

    def _write_conversion_journal(
        self,
        files: list[Path],
        output_dir: Path,
        split_output: bool,
        max_chunk_characters: int,
        heading_profile: HeadingProfile,
        split_mode: SplitMode,
        page_numbers_by_file: list[tuple[int, ...] | None],
        max_workers: int | None,
        reservations: list[OutputReservation],
    ) -> None:
        entries = []
        for source, page_numbers, reservation in zip(files, page_numbers_by_file, reservations, strict=True):
            stat = source.stat()
            entries.append(
                {
                    "source": str(source),
                    "source_size": stat.st_size,
                    "source_mtime_ns": stat.st_mtime_ns,
                    "page_numbers": list(page_numbers) if page_numbers is not None else None,
                    "status": "pending",
                    "reservation": {
                        "markdown_path": str(reservation.markdown_path),
                        "assets_dir": str(reservation.assets_dir),
                        "chunks_dir": str(reservation.chunks_dir),
                    },
                }
            )
        payload = {
            "version": 1,
            "state": "running",
            "created_at": time.time(),
            "output_dir": str(output_dir),
            "split_output": split_output,
            "split_mode": split_mode,
            "max_chunk_characters": max_chunk_characters,
            "heading_profile": heading_profile,
            "max_workers": max_workers,
            "files": entries,
        }
        with self._journal_lock:
            _atomic_write_json(self._journal_path, payload)

    def _mark_journal_file_finished(self, source: Path, status: str) -> None:
        with self._journal_lock:
            try:
                payload = json.loads(self._journal_path.read_text(encoding="utf-8"))
            except (FileNotFoundError, OSError, json.JSONDecodeError, TypeError):
                return
            for entry in payload.get("files", []):
                if entry.get("source") == str(source.resolve()):
                    entry["status"] = status
                    _atomic_write_json(self._journal_path, payload)
                    return

    def _clear_conversion_journal(self, *, remove_checkpoints: bool) -> None:
        payload = self._read_conversion_journal() if remove_checkpoints else None
        if payload:
            try:
                output_root = Path(payload.get("output_dir", "")).resolve()
            except (OSError, TypeError, ValueError):
                output_root = None
            for entry in payload.get("files", []):
                markdown_path = entry.get("reservation", {}).get("markdown_path")
                if not markdown_path or output_root is None:
                    continue
                try:
                    path = Path(markdown_path).resolve()
                except (OSError, TypeError, ValueError):
                    continue
                if path.parent != output_root:
                    logger.warning(f"Checkpoint fora do destino autorizado foi ignorado: {path}")
                    continue
                checkpoint = path.with_name(f".{path.name}.nexojuris-checkpoint")
                if checkpoint.parent == output_root:
                    shutil.rmtree(checkpoint, ignore_errors=True)
        with self._journal_lock:
            self._journal_path.unlink(missing_ok=True)

    def get_interrupted_conversion(self) -> dict[str, Any]:
        """Reconstrói uma fila interrompida sem reutilizar IDs antigos da interface."""
        if self.is_converting:
            return {"available": False, "reason": "conversion_active"}
        payload = self._read_conversion_journal()
        if not payload:
            return {"available": False}
        try:
            output_dir = Path(payload["output_dir"]).resolve()
            output_resource = self._register_directory(output_dir, "conversion_recovery")
            recovered_files: list[dict[str, Any]] = []
            files_payload: list[dict[str, Any]] = []
            missing_files: list[str] = []
            for entry in payload.get("files", []):
                reservation_data = entry.get("reservation", {})
                expected_output = Path(reservation_data.get("markdown_path", "")).resolve()
                expected_assets = Path(reservation_data.get("assets_dir", "")).resolve()
                expected_chunks = Path(reservation_data.get("chunks_dir", "")).resolve()
                if (
                    expected_output.parent != output_dir
                    or expected_assets.parent != output_dir / "images"
                    or expected_chunks.parent != output_dir
                ):
                    raise ResourceAccessError("O journal contém um destino fora da pasta autorizada.")
                self._cleanup_conversion_temps(
                    OutputReservation(expected_output, expected_assets, expected_chunks)
                )
                if entry.get("status") in {"completed", "failed"} or expected_output.is_file():
                    continue
                source = Path(entry.get("source", "")).resolve()
                try:
                    stat = source.stat()
                    if (
                        source.suffix.casefold() != ".pdf"
                        or stat.st_size != int(entry.get("source_size", -1))
                        or stat.st_mtime_ns != int(entry.get("source_mtime_ns", -1))
                    ):
                        raise OSError("arquivo alterado")
                    recovered = self._register_pdf(source, "conversion_recovery")
                except (OSError, ResourceAccessError, TypeError, ValueError):
                    missing_files.append(source.name or "PDF indisponível")
                    continue
                recovered_files.append(recovered)
                files_payload.append(
                    {"file_id": recovered["file_id"], "page_numbers": entry.get("page_numbers")}
                )
            if not files_payload:
                if not missing_files:
                    self._clear_conversion_journal(remove_checkpoints=True)
                    return {"available": False}
                token = uuid.uuid4().hex
                self._recovery_token = token
                self._recovery_payload = None
                return {
                    "available": True,
                    "can_resume": False,
                    "resume_token": token,
                    "files": [],
                    "missing_files": missing_files,
                }
            token = uuid.uuid4().hex
            resume_payload = {
                "files": files_payload,
                "output_directory_id": output_resource["directory_id"],
                "split_output": bool(payload.get("split_output", False)),
                "split_mode": payload.get("split_mode", "semantic"),
                "max_chunk_characters": payload.get("max_chunk_characters", DEFAULT_MAX_CHUNK_CHARACTERS),
                "heading_profile": payload.get("heading_profile", "jurisprudencia"),
                "max_workers": payload.get("max_workers"),
            }
            self._recovery_token = token
            self._recovery_payload = resume_payload
            return {
                "available": True,
                "can_resume": True,
                "resume_token": token,
                "files": recovered_files,
                "missing_files": missing_files,
                "output_dir": str(output_dir),
                "output_directory_id": output_resource["directory_id"],
                "split_output": resume_payload["split_output"],
                "split_mode": resume_payload["split_mode"],
                "max_chunk_characters": resume_payload["max_chunk_characters"],
                "heading_profile": resume_payload["heading_profile"],
            }
        except (KeyError, OSError, ResourceAccessError, TypeError, ValueError) as error:
            return {"available": False, "error": f"Não foi possível recuperar a fila: {error}"}

    def resume_interrupted_conversion(self, resume_token: str) -> dict[str, Any]:
        if not resume_token or resume_token != self._recovery_token or self._recovery_payload is None:
            return {"started": False, "error": "Token de retomada inválido ou expirado."}
        payload = self._recovery_payload
        self._resuming_interrupted = True
        try:
            result = self.start_conversion(payload)
        finally:
            self._resuming_interrupted = False
        if result.get("started"):
            self._recovery_token = None
            self._recovery_payload = None
        return result

    def discard_interrupted_conversion(self, resume_token: str) -> dict[str, Any]:
        if not resume_token or resume_token != self._recovery_token:
            return {"ok": False, "error": "Token de retomada inválido ou expirado."}
        self._clear_conversion_journal(remove_checkpoints=True)
        self._recovery_token = None
        self._recovery_payload = None
        return {"ok": True}

    def start_conversion(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Inicia a conversão em lote em uma thread em segundo plano."""
        import web_api

        if not isinstance(payload, dict):
            return {"started": False, "error": "Dados da conversão inválidos."}
        try:
            web_api.require_software_activation("converter")
        except (LicenseRequiredError, LicenseAccessError) as error:
            return _license_denial(error, started=False)

        if self.is_converting:
            return {"started": False, "error": "Uma conversão já está em andamento."}
        if self._journal_path.is_file() and not self._resuming_interrupted:
            return {
                "started": False,
                "error": "Há uma conversão interrompida aguardando retomada ou descarte.",
                "error_code": "interrupted_conversion_pending",
            }

        files_data = payload.get("files", [])
        if not isinstance(files_data, list) or not files_data:
            return {"started": False, "error": "Nenhum PDF selecionado."}
        if any(not isinstance(item, dict) for item in files_data):
            return {"started": False, "error": "A fila de PDFs é inválida."}

        output_directory_id = payload.get("output_directory_id", "")
        split_output = bool(payload.get("split_output", False))
        split_mode: SplitMode = payload.get("split_mode", "semantic")
        if split_mode not in {"semantic", "strict"}:
            return {"started": False, "error": "Modo de divisão inválido."}
        try:
            max_chunk_characters = int(payload.get("max_chunk_characters", DEFAULT_MAX_CHUNK_CHARACTERS))
        except (TypeError, ValueError):
            return {"started": False, "error": "O limite de caracteres deve ser um número inteiro."}
        heading_profile: HeadingProfile = payload.get("heading_profile", "jurisprudencia")
        if heading_profile not in {"jurisprudencia", "curso"}:
            return {"started": False, "error": "Perfil de títulos inválido."}
        requested_workers = payload.get("max_workers")
        if requested_workers is not None:
            try:
                requested_workers = int(requested_workers)
            except (TypeError, ValueError):
                return {"started": False, "error": "Número de workers inválido."}
            if not (1 <= requested_workers <= MAX_PARALLEL_WORKERS):
                return {
                    "started": False,
                    "error": f"O número de workers deve estar entre 1 e {MAX_PARALLEL_WORKERS}.",
                }

        if max_chunk_characters < MIN_CHUNK_CHARACTERS:
            formatted_limit = f"{MIN_CHUNK_CHARACTERS:,}".replace(",", ".")
            return {
                "started": False,
                "error": f"O limite das partes deve ter pelo menos {formatted_limit} caracteres.",
            }

        try:
            output_dir = self._resolve_directory(output_directory_id, "write")
            file_paths = [self._resolve_pdf(str(item.get("file_id", "")), "convert") for item in files_data]
            page_numbers_by_file: list[tuple[int, ...] | None] = []
            for item in files_data:
                raw_pages = item.get("page_numbers")
                if raw_pages is None:
                    page_numbers_by_file.append(None)
                    continue
                if not isinstance(raw_pages, list) or not raw_pages:
                    raise ResourceAccessError("Lista de páginas para reprocessamento inválida.")
                pages = tuple(dict.fromkeys(int(value) for value in raw_pages))
                if any(value < 1 or value > MAX_PAGE_COUNT for value in pages):
                    raise ResourceAccessError("Página de reprocessamento fora do limite permitido.")
                page_numbers_by_file.append(pages)
        except (OSError, ResourceAccessError) as error:
            return {"started": False, "error": str(error)}
        except (TypeError, ValueError):
            return {"started": False, "error": "Lista de páginas para reprocessamento inválida."}

        if len(file_paths) != len(files_data):
            return {"started": False, "error": "A fila contém recursos não autorizados."}

        try:
            self._validate_batch_budget(file_paths, output_dir)
            reservations = reserve_batch_output_paths(
                output_dir,
                file_paths,
                retry=any(pages is not None for pages in page_numbers_by_file),
            )
            self._write_conversion_journal(
                file_paths,
                output_dir,
                split_output,
                max_chunk_characters,
                heading_profile,
                split_mode,
                page_numbers_by_file,
                requested_workers,
                reservations,
            )
        except (OSError, ResourceBudgetExceeded) as error:
            return {"started": False, "error": str(error), "error_code": "resource_budget"}

        self.cancel_requested.clear()
        self.resume_processing.set()
        self.is_paused = False
        self.is_converting = True
        self._set_conversion_state("running")
        self._shutdown_requested = False
        self._conversion_finished.clear()
        self._batch_start_time = time.perf_counter()

        import web_api

        thread_cls = getattr(getattr(web_api, "threading", threading), "Thread", threading.Thread)
        thread = thread_cls(
            target=self._convert_in_background,
            args=(
                file_paths,
                output_dir,
                split_output,
                max_chunk_characters,
                heading_profile,
                split_mode,
                page_numbers_by_file,
                requested_workers,
                reservations,
            ),
            daemon=False,
            name="ConversionCoordinator",
        )
        self._conversion_thread = thread
        try:
            thread.start()
        except RuntimeError as error:
            self.is_converting = False
            self._set_conversion_state("failed", error=str(error))
            self._conversion_finished.set()
            self._clear_conversion_journal(remove_checkpoints=True)
            return {"started": False, "error": f"Não foi possível iniciar a conversão: {error}"}
        return {"started": True, "error": None}

    def retry_failed_pages(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Reprocessa somente as páginas explicitamente indicadas em uma nova saída transacional."""
        return self.start_conversion(
            {
                "files": [
                    {
                        "file_id": payload.get("file_id", ""),
                        "page_numbers": payload.get("page_numbers", []),
                    }
                ],
                "output_directory_id": payload.get("output_directory_id", ""),
                "split_output": bool(payload.get("split_output", False)),
                "split_mode": payload.get("split_mode", "semantic"),
                "max_chunk_characters": payload.get("max_chunk_characters", DEFAULT_MAX_CHUNK_CHARACTERS),
                "heading_profile": payload.get("heading_profile", "jurisprudencia"),
                "max_workers": payload.get("max_workers"),
            }
        )

    def toggle_pause(self) -> dict[str, Any]:
        """Pausa ou retoma o documento ativo no próximo checkpoint de página."""
        if not self.is_converting:
            return {"is_paused": False, "state": self.conversion_state}

        if self.is_paused:
            self._control_active_conversions("running")
            self.resume_processing.set()
            self._set_conversion_state("running")
            self._emit("status", {"message": "Conversão retomada."})
            return {"is_paused": False, "state": "running"}

        self.resume_processing.clear()
        self._set_conversion_state("pausing")
        self._control_active_conversions("pause")
        self._emit("status", {"message": "Pausa solicitada: concluindo a página corrente..."})
        return {"is_paused": True, "state": "pausing"}

    def request_stop(self) -> bool:
        """Solicita a interrupção imediata dos processos de conversão."""
        if not self.is_converting:
            return False
        self._set_conversion_state("stopping")
        self.cancel_requested.set()
        self._control_active_conversions("stop")
        self.resume_processing.set()
        self._emit("status", {"message": "Parada solicitada: interrompendo a extração ativa..."})
        return True

    def _convert_in_background(
        self,
        files: list[Path],
        output_dir: Path,
        split_output: bool,
        max_chunk_characters: int,
        heading_profile: HeadingProfile,
        split_mode: SplitMode = "semantic",
        page_numbers_by_file: list[tuple[int, ...] | None] | None = None,
        max_workers: int | None = None,
        reservations: list[OutputReservation] | None = None,
    ) -> None:
        try:
            page_numbers_by_file = page_numbers_by_file or [None] * len(files)
            reservations = reservations or reserve_batch_output_paths(
                output_dir, files, retry=any(pages is not None for pages in page_numbers_by_file)
            )
            worker_count = self._resolve_worker_count(len(files), max_workers)
            self._convert_in_parallel(
                files,
                output_dir,
                split_output,
                max_chunk_characters,
                heading_profile,
                worker_count,
                reservations,
                split_mode,
                page_numbers_by_file,
            )
        except Exception as error:
            self._set_conversion_state("failed", error=str(error))
            self._emit(
                "batch_error",
                {"error_message": str(error), "details": traceback.format_exc()},
            )
        finally:
            self.is_converting = False
            self.is_paused = False
            with self._active_checkpoint_lock:
                self._active_checkpoint_dirs.clear()
            self._conversion_finished.set()

    def _resolve_worker_count(self, total_files: int, requested_workers: int | None = None) -> int:
        if total_files <= 1:
            return 1
        import web_api

        cpu_count_fn = getattr(getattr(web_api, "os", os), "cpu_count", os.cpu_count)
        avail_mem_fn = getattr(web_api, "available_memory_bytes", available_memory_bytes)
        automatic_limit = min(total_files, MAX_PARALLEL_WORKERS, cpu_count_fn() or 1)
        available_memory = avail_mem_fn()
        if available_memory is not None:
            memory_limit = max(1, available_memory // MEMORY_RESERVATION_PER_WORKER_BYTES)
            automatic_limit = min(automatic_limit, memory_limit)
        if requested_workers is None:
            return max(1, automatic_limit)
        return max(1, min(automatic_limit, int(requested_workers)))

    @staticmethod
    def _validate_batch_budget(files: list[Path], output_dir: Path) -> None:
        source_sizes: list[int] = []
        for source in files:
            size = source.stat().st_size
            if size > MAX_PDF_FILE_SIZE_BYTES:
                raise ResourceBudgetExceeded(
                    f"{source.name}: o PDF excede o limite de {MAX_PDF_FILE_SIZE_BYTES // (1024 * 1024)} MB."
                )
            source_sizes.append(size)
        required = MIN_FREE_DISK_BYTES + sum(source_sizes) * DISK_SPACE_SOURCE_MULTIPLIER
        import web_api

        disk_usage_fn = getattr(getattr(web_api, "shutil", shutil), "disk_usage", shutil.disk_usage)
        free = disk_usage_fn(output_dir).free
        if free < required:
            raise ResourceBudgetExceeded(
                "Espaço livre insuficiente no destino: "
                f"são necessários ao menos {required // (1024 * 1024)} MB livres para este lote."
            )

    def _convert_sequentially(
        self,
        files: list[Path],
        output_dir: Path,
        split_output: bool,
        max_chunk_characters: int,
        heading_profile: HeadingProfile,
        reservations: list[OutputReservation] | None = None,
        split_mode: SplitMode = "semantic",
        page_numbers_by_file: list[tuple[int, ...] | None] | None = None,
    ) -> None:
        import web_api

        converter = getattr(web_api, "PdfMarkdownConverter", PdfMarkdownConverter)()
        successes: list[ConversionResult] = []
        failures: list[ConversionFailure] = []
        total = len(files)
        reservations = reservations or reserve_batch_output_paths(output_dir, files)
        page_numbers_by_file = page_numbers_by_file or [None] * total
        self._emit_progress(0, total)

        for index, source in enumerate(files, start=1):
            if self.cancel_requested.is_set():
                self._emit_batch_stopped(successes, failures, total, output_dir)
                return

            while not self.resume_processing.wait(timeout=0.2):
                if self.cancel_requested.is_set():
                    self._emit_batch_stopped(successes, failures, total, output_dir)
                    return

            self._emit(
                "file_start",
                {
                    "file_id": self._resources.id_for_path(source, kind="pdf"),
                    "path": str(source),
                    "name": source.name,
                    "index": index,
                    "total": total,
                },
            )
            self._emit("status", {"message": f"Convertendo {index}/{total}: {source.name}"})

            try:
                result = converter.convert(
                    source,
                    output_dir,
                    split_output,
                    max_chunk_characters,
                    heading_profile,
                    reservations[index - 1],
                    split_mode,
                    page_numbers_by_file[index - 1],
                    True,
                )
                successes.append(result)
                self._emit_file_success(result)
            except Exception as error:
                failure = ConversionFailure(source=source, error_message=str(error), details=traceback.format_exc())
                failures.append(failure)
                self._emit_file_error(failure)

            self._emit_progress(index, total)

        self._emit_batch_done(successes, failures, total, output_dir)

    def _convert_in_parallel(
        self,
        files: list[Path],
        output_dir: Path,
        split_output: bool,
        max_chunk_characters: int,
        heading_profile: HeadingProfile,
        worker_count: int,
        reservations: list[OutputReservation] | None = None,
        split_mode: SplitMode = "semantic",
        page_numbers_by_file: list[tuple[int, ...] | None] | None = None,
    ) -> None:
        import web_api

        executor_cls = getattr(web_api, "ProcessPoolExecutor", ProcessPoolExecutor)
        worker_init = getattr(web_api, "init_worker", init_worker)
        worker_fn = getattr(web_api, "convert_worker", convert_worker)

        total = len(files)
        reservations = reservations or reserve_batch_output_paths(output_dir, files)
        page_numbers_by_file = page_numbers_by_file or [None] * total
        self._emit_progress(0, total)
        successes: list[ConversionResult] = []
        failures: list[ConversionFailure] = []
        pending: dict[Future, tuple[Path, ProcessPoolExecutor, float, OutputReservation]] = {}
        idle_pools: list[ProcessPoolExecutor] = []
        next_index = 0

        cpu_count_fn = getattr(getattr(web_api, "os", os), "cpu_count", os.cpu_count)
        available_cpus = cpu_count_fn() or 1
        page_workers_per_file = max(1, min(4, available_cpus // max(1, worker_count)))

        try:
            while pending or (next_index < total and not self.cancel_requested.is_set()):
                if not self.cancel_requested.is_set() and self.resume_processing.is_set():
                    while len(pending) < worker_count and next_index < total:
                        source = files[next_index]
                        reservation = reservations[next_index]
                        page_numbers = page_numbers_by_file[next_index]
                        next_index += 1
                        self._emit(
                            "file_start",
                            {
                                "file_id": self._resources.id_for_path(source, kind="pdf"),
                                "path": str(source),
                                "name": source.name,
                                "index": next_index,
                                "total": total,
                            },
                        )
                        self._emit("status", {"message": f"Convertendo {next_index}/{total}: {source.name}"})
                        checkpoint_dir = self._checkpoint_dir(reservation)
                        self._write_conversion_control(checkpoint_dir, "running")
                        with self._active_checkpoint_lock:
                            self._active_checkpoint_dirs.add(checkpoint_dir)
                        pool = idle_pools.pop() if idle_pools else executor_cls(max_workers=1, initializer=worker_init)
                        try:
                            future = pool.submit(
                                worker_fn,
                                source,
                                output_dir,
                                split_output,
                                max_chunk_characters,
                                heading_profile,
                                reservation,
                                split_mode,
                                page_numbers,
                                checkpoint_dir,
                                page_workers_per_file,
                            )
                        except (BrokenProcessPool, RuntimeError) as error:
                            with self._active_checkpoint_lock:
                                self._active_checkpoint_dirs.discard(checkpoint_dir)
                            pool.shutdown(wait=False, cancel_futures=True)
                            failure = ConversionFailure(source=source, error_message=str(error), details=traceback.format_exc())
                            failures.append(failure)
                            self._mark_journal_file_finished(source, "failed")
                            self._emit_file_error(failure)
                            self._emit_progress(len(successes) + len(failures), total)
                            continue
                        pending[future] = (source, pool, time.monotonic(), reservation)

                if not pending:
                    self.resume_processing.wait(timeout=0.2)
                    continue

                self._sync_active_pause_status()

                wait_timeout = 0 if self.cancel_requested.is_set() else 0.2
                wait_fn = getattr(web_api, "wait", wait)
                done, _ = wait_fn(pending.keys(), timeout=wait_timeout, return_when=FIRST_COMPLETED)
                for future in done:
                    source, pool, _, reservation = pending.pop(future)
                    with self._active_checkpoint_lock:
                        self._active_checkpoint_dirs.discard(self._checkpoint_dir(reservation))
                    try:
                        result = future.result()
                    except Exception as error:
                        result = ConversionFailure(source=source, error_message=str(error), details=traceback.format_exc())
                    finally:
                        can_reuse = (
                            not self.cancel_requested.is_set()
                            and not self._pool_memory_exceeded(pool)
                            and not getattr(pool, "_broken", False)
                        )
                        if can_reuse:
                            idle_pools.append(pool)
                        else:
                            pool.shutdown(wait=True, cancel_futures=True)

                    if isinstance(result, ConversionFailure):
                        if self.cancel_requested.is_set() and result.error_message.startswith("Conversão interrompida"):
                            pass
                        else:
                            failures.append(result)
                            self._mark_journal_file_finished(source, "failed")
                            self._emit_file_error(result)
                    else:
                        successes.append(result)
                        self._mark_journal_file_finished(source, "completed")
                        self._emit_file_success(result)
                    self._emit_progress(len(successes) + len(failures), total)

                if self.cancel_requested.is_set():
                    for _, pool, _, reservation in pending.values():
                        self._terminate_conversion_pool(pool)
                        self._cleanup_conversion_temps(reservation)
                    pending.clear()
                    for pool in idle_pools:
                        self._terminate_conversion_pool(pool)
                    idle_pools.clear()
                    with self._active_checkpoint_lock:
                        self._active_checkpoint_dirs.clear()
                    if not self._shutdown_requested:
                        self._clear_conversion_journal(remove_checkpoints=True)
                    break

                now = time.monotonic()
                over_budget = [
                    future
                    for future, (_, pool, started, _) in pending.items()
                    if now - started > MAX_CONVERSION_SECONDS or self._pool_memory_exceeded(pool)
                ]
                for future in over_budget:
                    source, pool, started, reservation = pending.pop(future)
                    with self._active_checkpoint_lock:
                        self._active_checkpoint_dirs.discard(self._checkpoint_dir(reservation))
                    timed_out = now - started > MAX_CONVERSION_SECONDS
                    self._terminate_conversion_pool(pool)
                    self._cleanup_conversion_temps(reservation)
                    failure = ConversionFailure(
                        source=source,
                        error_message=(
                            f"A conversão excedeu o prazo de {MAX_CONVERSION_SECONDS // 60} minutos."
                            if timed_out
                            else "A conversão excedeu o limite de memória do processo isolado."
                        ),
                        details="O processo isolado foi encerrado pelo watchdog.",
                    )
                    failures.append(failure)
                    self._mark_journal_file_finished(source, "failed")
                    self._emit_file_error(failure)
                    self._emit_progress(len(successes) + len(failures), total)
        finally:
            for pool in idle_pools:
                try:
                    pool.shutdown(wait=False, cancel_futures=True)
                except Exception:
                    pass
            idle_pools.clear()

        if self.cancel_requested.is_set():
            self._emit_batch_stopped(successes, failures, total, output_dir)
        else:
            self._clear_conversion_journal(remove_checkpoints=True)
            self._emit_batch_done(successes, failures, total, output_dir)

    @staticmethod
    def _terminate_conversion_pool(pool: ProcessPoolExecutor) -> None:
        processes = tuple((getattr(pool, "_processes", {}) or {}).values())
        try:
            pool.terminate_workers()
        except (AttributeError, BrokenProcessPool, RuntimeError):
            for process in processes:
                try:
                    process.terminate()
                except (AttributeError, OSError):
                    pass
        deadline = time.monotonic() + 2.5
        for process in processes:
            try:
                process.join(timeout=max(0.0, deadline - time.monotonic()))
                if process.is_alive():
                    process.kill()
                    process.join(timeout=max(0.0, deadline - time.monotonic()))
            except (AttributeError, OSError):
                pass
        try:
            pool.shutdown(wait=False, cancel_futures=True)
        except (AttributeError, BrokenProcessPool, RuntimeError):
            pass

    @staticmethod
    def _pool_memory_exceeded(pool: ProcessPoolExecutor) -> bool:
        import web_api

        process_rss_fn = getattr(web_api, "process_rss_bytes", process_rss_bytes)
        processes = getattr(pool, "_processes", {}) or {}
        for process in processes.values():
            pid = getattr(process, "pid", None)
            if pid is None:
                continue
            rss = process_rss_fn(pid)
            if rss is not None and rss > MAX_CONVERSION_MEMORY_BYTES:
                return True
        return False

    @staticmethod
    def _cleanup_conversion_temps(reservation: OutputReservation) -> None:
        patterns = (
            (reservation.markdown_path.parent, f".{reservation.markdown_path.name}.*.tmp"),
            (reservation.assets_dir.parent, f".{reservation.assets_dir.name}.*"),
            (reservation.chunks_dir.parent, f".{reservation.chunks_dir.name}.*.tmp"),
        )
        for parent, pattern in patterns:
            if not parent.is_dir():
                continue
            for candidate in parent.glob(pattern):
                if candidate.is_dir():
                    shutil.rmtree(candidate, ignore_errors=True)
                else:
                    candidate.unlink(missing_ok=True)

    def _emit_file_success(self, result: ConversionResult) -> None:
        index_status = "indexed"
        index_error = ""
        try:
            if not result.markdown_path.is_file():
                raise FileNotFoundError("o Markdown final não foi encontrado")
            content = result.markdown_path.read_text(encoding="utf-8")
            self._library.index_markdown_file(result.source, result.markdown_path, content)
            if not self._library.verify_markdown_index(result.source, result.markdown_path):
                raise RuntimeError("o índice não confirmou o conteúdo gravado")
        except Exception as err:
            index_status = "failed"
            index_error = str(err).replace("\r", " ").replace("\n", " ")[:1000]
            logger.error("Falha ao indexar markdown %s: %s", result.markdown_path, err, exc_info=True)
            try:
                self._library.record_markdown_index_failure(result.source, result.markdown_path, index_error)
            except Exception:
                logger.error("Falha ao persistir erro de indexação de %s", result.markdown_path, exc_info=True)

        try:
            rss = process_rss_bytes(os.getpid()) or 0
            page_count = len(result.page_coverage) if result.page_coverage else max(1, result.chunk_count)
            get_telemetry_tracker().record_conversion(
                page_count=page_count,
                duration_seconds=result.extraction_seconds,
                page_coverage=result.page_coverage,
                current_rss_bytes=rss,
            )
        except Exception:
            logger.debug("Falha ao registrar telemetria de conversão", exc_info=True)

        markdown = self._register_markdown(result.markdown_path, "conversion_result")
        output_directory = self._register_directory(result.markdown_path.parent, "conversion_result")
        self._emit(
            "file_success",
            {
                "source_id": self._resources.id_for_path(result.source, kind="pdf"),
                "source": str(result.source),
                "name": result.source.name,
                "markdown_path": str(result.markdown_path),
                "markdown_id": markdown["markdown_id"],
                "output_directory_id": output_directory["directory_id"],
                "asset_count": result.asset_count,
                "chunk_count": result.chunk_count,
                "duration_formatted": format_duration(result.extraction_seconds),
                "extraction_seconds": result.extraction_seconds,
                "page_coverage": [
                    {
                        "page_number": item.page_number,
                        "status": item.status,
                        "warning": item.warning,
                        "fidelity_score": item.fidelity_score,
                        "fidelity_issues": list(item.fidelity_issues),
                    }
                    for item in result.page_coverage
                ],
                "failed_pages": list(result.failed_pages),
                "warning_pages": list(result.warning_pages),
                "fidelity_review_pages": list(result.fidelity_review_pages),
                "index_status": index_status,
                "index_error": index_error,
            },
        )

    def _emit_file_error(self, failure: ConversionFailure) -> None:
        logger.error(
            "Falha de conversão document=%s error=%s details=%s",
            failure.source.name,
            failure.error_message,
            failure.details,
        )
        self._emit(
            "file_error",
            {
                "source_id": self._resources.id_for_path(failure.source, kind="pdf"),
                "source": str(failure.source),
                "name": failure.source.name,
                "error_message": failure.error_message,
                "details": failure.details,
            },
        )

    def _emit_progress(self, completed: int, total: int) -> None:
        percent = 0 if total <= 0 else round((completed / total) * 100)
        self._emit("progress", {"completed": completed, "total": total, "percent": percent})

    def _emit_batch_done(
        self,
        successes: list[ConversionResult],
        failures: list[ConversionFailure],
        total: int,
        output_dir: Path,
    ) -> None:
        elapsed_seconds = time.perf_counter() - self._batch_start_time
        problem_pages = [
            {"name": result.source.name, "pages": list(result.failed_pages)}
            for result in successes
            if result.failed_pages
        ]
        fidelity_pages = [
            {"name": result.source.name, "pages": list(result.fidelity_review_pages)}
            for result in successes
            if result.fidelity_review_pages
        ]
        telemetry_stats = get_telemetry_tracker().get_summary()
        ocr_cache_info = telemetry_stats.get("ocr_cache", {})
        self._set_conversion_state("completed")
        self._emit(
            "batch_done",
            {
                "success_count": len(successes),
                "failure_count": len(failures),
                "total": total,
                "elapsed_seconds": elapsed_seconds,
                "elapsed_formatted": format_duration(elapsed_seconds),
                "output_dir": str(output_dir),
                "problem_pages": problem_pages,
                "problem_page_count": sum(len(item["pages"]) for item in problem_pages),
                "fidelity_pages": fidelity_pages,
                "fidelity_review_page_count": sum(len(item["pages"]) for item in fidelity_pages),
                "ocr_cache_hits": int(ocr_cache_info.get("hits", 0)),
                "ocr_cache_hit_ratio": float(ocr_cache_info.get("hit_ratio", 0.0)),
            },
        )

    def _emit_batch_stopped(
        self,
        successes: list[ConversionResult],
        failures: list[ConversionFailure],
        total: int,
        output_dir: Path,
    ) -> None:
        elapsed_seconds = time.perf_counter() - self._batch_start_time
        self._set_conversion_state("stopped")
        self._emit(
            "batch_stopped",
            {
                "success_count": len(successes),
                "failure_count": len(failures),
                "total": total,
                "elapsed_seconds": elapsed_seconds,
                "elapsed_formatted": format_duration(elapsed_seconds),
                "output_dir": str(output_dir),
            },
        )
