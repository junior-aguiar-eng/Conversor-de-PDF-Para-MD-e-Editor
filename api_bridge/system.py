"""Mixin do sistema: diálogos nativos, licença, termos, diagnóstico e ciclo de vida."""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import threading
import time
import webbrowser
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from constants import (
    APP_NAME,
    APP_VERSION,
    CURRENT_TERMS_VERSION,
    DEFAULT_MAX_CHUNK_CHARACTERS,
    MAX_CONVERSION_MEMORY_BYTES,
    MAX_CONVERSION_SECONDS,
    MAX_EXTRACTED_ASSET_BYTES,
    MAX_IMAGES_PER_DOCUMENT,
    MAX_PAGE_COUNT,
    MAX_PDF_FILE_SIZE_BYTES,
    MIN_FREE_DISK_BYTES,
)
from converter import validate_runtime_dependencies
from file_authorization import ResourceAccessError
from licensing import activate_act4_license, get_license_status
from production_diagnostics import (
    build_diagnostic_report,
    diagnostic_status,
    write_diagnostic_report,
)

logger = logging.getLogger(__name__)


class SystemMixin:
    """Métodos de integração com o sistema operacional, UI e ciclo de vida do app."""

    def set_window(self, window: Any) -> None:
        self._window = window

    def _emit(self, event_name: str, data: Any = None) -> None:
        """Envia um evento thread-safe para o frontend JavaScript."""
        if not self._window:
            return
        payload_json = json.dumps(data) if data is not None else "null"
        js_code = f"window.onBackendEvent && window.onBackendEvent({json.dumps(event_name)}, {payload_json});"
        try:
            self._window.evaluate_js(js_code)
        except Exception as error:
            logger.debug(f"Erro ao emitir evento {event_name}: {error}")

    def get_app_info(self) -> dict[str, Any]:
        """Retorna metadados do aplicativo e caminhos padrão."""
        return {
            "app_name": APP_NAME,
            "app_version": APP_VERSION,
            "default_output_dir": str(self._default_output_dir),
            "default_output_dir_id": self._default_output_resource["directory_id"],
            "default_chunk_limit": DEFAULT_MAX_CHUNK_CHARACTERS,
            "max_page_count": MAX_PAGE_COUNT,
            "max_pdf_file_size_bytes": MAX_PDF_FILE_SIZE_BYTES,
            "max_images_per_document": MAX_IMAGES_PER_DOCUMENT,
            "max_extracted_asset_bytes": MAX_EXTRACTED_ASSET_BYTES,
            "max_conversion_memory_bytes": MAX_CONVERSION_MEMORY_BYTES,
            "max_conversion_seconds": MAX_CONVERSION_SECONDS,
            "min_free_disk_bytes": MIN_FREE_DISK_BYTES,
            "library_storage": dict(self._library_status),
            "diagnostics": diagnostic_status(),
            "conversion_state": self.conversion_state,
            "recent_markdowns": self._persisted_markdowns(),
        }

    def get_diagnostic_status(self) -> dict[str, Any]:
        """Retorna metadados operacionais sem conteúdo dos documentos."""
        return {
            "ok": True,
            **diagnostic_status(),
            "library_storage": dict(self._library_status),
            "online_services": self.get_online_services_status()["services"],
        }

    def export_diagnostic_report(self) -> dict[str, Any]:
        """Exporta relatório sanitizado para destino autorizado por diálogo nativo."""
        if not self._window:
            return {"ok": False, "error": "A janela do aplicativo não está disponível."}
        try:
            import webview

            stamp = time.strftime("%Y%m%d-%H%M%S")
            result = self._window.create_file_dialog(
                webview.SAVE_DIALOG,
                save_filename=f"NexoJuris-Diagnostico-{stamp}.txt",
                file_types=("Relatório de diagnóstico (*.txt)",),
            )
            if not result:
                return {"ok": False, "cancelled": True}
            selected = result if isinstance(result, str) else result[0]
            destination = Path(selected)
            if destination.suffix.casefold() != ".txt":
                destination = destination.with_suffix(".txt")
            import web_api

            build_report_fn = getattr(web_api, "build_diagnostic_report", build_diagnostic_report)
            report = build_report_fn(
                library_status=dict(self._library_status),
                online_services=self.get_online_services_status()["services"],
            )
            write_diagnostic_report(destination, report)
            logger.info("Relatório de diagnóstico exportado para %s", destination)
            return {
                "ok": True,
                "path": str(destination.resolve()),
                "message": "Relatório de diagnóstico exportado com sucesso.",
            }
        except Exception as error:
            logger.error("Falha ao exportar relatório de diagnóstico: %s", error, exc_info=True)
            return {"ok": False, "error": f"Não foi possível exportar o diagnóstico: {error}"}

    def get_terms_acceptance_status(self) -> dict[str, Any]:
        """Verifica se o usuário já aceitou os termos de uso formalmente e se a versão vigente confere (Item 18)."""
        try:
            status = self._library.get_terms_status()
            stored_version = status.get("terms_version") or "1.0"
            needs_reacceptance = False
            if status.get("accepted"):
                if stored_version != CURRENT_TERMS_VERSION:
                    needs_reacceptance = True

            return {
                "accepted": bool(status.get("accepted")) and not needs_reacceptance,
                "accepted_at": status.get("accepted_at"),
                "terms_version": stored_version,
                "current_terms_version": CURRENT_TERMS_VERSION,
                "needs_reacceptance": needs_reacceptance,
            }
        except Exception as error:
            logger.error(f"Erro ao verificar status dos termos: {error}")
            return {
                "accepted": False,
                "terms_version": None,
                "current_terms_version": CURRENT_TERMS_VERSION,
                "needs_reacceptance": True,
            }

    def accept_terms(self, terms_version: str = CURRENT_TERMS_VERSION) -> dict[str, Any]:
        """Registra o aceite formal e irrevogável dos termos de uso (Item 18)."""
        try:
            version_to_save = terms_version or CURRENT_TERMS_VERSION
            self._library.save_terms_acceptance(version_to_save)
            return {"ok": True, "terms_version": version_to_save}
        except Exception as error:
            logger.error(f"Erro ao registrar aceite dos termos: {error}")
            return {"ok": False, "error": str(error)}

    def get_license_info(self) -> dict[str, Any]:
        """Retorna o estado completo, preservando o booleano consumido pela UI atual."""
        import web_api

        get_status_fn = getattr(web_api, "get_license_status", get_license_status)
        status = get_status_fn()
        return {
            "is_activated": status.allows("converter"),
            **status.to_mapping(),
        }

    def exit_application(self) -> dict[str, Any]:
        """Encerra a janela sem permitir acesso à interface atrás do bloqueio de licença."""
        if not self._window:
            return {"ok": False, "error": "Janela do aplicativo indisponível."}
        if self.has_active_work():
            return {"ok": False, "error": "Existe uma operação em andamento; use o fechamento normal da janela."}
        if not self.shutdown_for_close(timeout_seconds=5.0):
            return {"ok": False, "error": "O aplicativo ainda está finalizando uma operação local."}
        self._window.destroy()
        return {"ok": True}

    def import_license_file(self) -> dict[str, Any]:
        """Seleciona e importa uma licença ACT4 sem expor o caminho ao renderer."""
        if not self._window:
            return {"ok": False, "cancelled": True, "error": "Janela do aplicativo indisponível."}
        import webview

        try:
            result = self._window.create_file_dialog(
                webview.OPEN_DIALOG,
                allow_multiple=False,
                file_types=("Licença NexoJuris (*.nxjlic)",),
            )
            if not result:
                return {"ok": False, "cancelled": True}
            license_path = Path(result[0]).resolve()
            if license_path.suffix.lower() != ".nxjlic" or not license_path.is_file():
                return {"ok": False, "error": "Selecione um arquivo de licença .nxjlic válido."}
            if license_path.stat().st_size > 65_536:
                return {"ok": False, "error": "O arquivo de licença excede o limite de 64 KB."}
            import web_api

            activate_fn = getattr(web_api, "activate_act4_license", activate_act4_license)
            response = activate_fn(license_path.read_bytes())
            if response.get("ok"):
                self._emit("toast", {"type": "success", "message": response["message"]})
            return response
        except OSError as error:
            logger.warning("Falha ao importar licença ACT4: %s", error)
            return {"ok": False, "error": "Não foi possível ler o arquivo de licença selecionado."}
        except Exception:
            logger.exception("Falha inesperada ao importar licença ACT4")
            return {"ok": False, "error": "Falha inesperada ao importar a licença selecionada."}

    def verify_license_now(self) -> dict[str, Any]:
        """Refaz localmente a verificação da licença armazenada."""
        import web_api

        get_status_fn = getattr(web_api, "get_license_status", get_license_status)
        status = get_status_fn()
        return {
            **status.to_mapping(),
            "ok": status.can_use_protected_features,
            "message": "Licença verificada neste computador." if status.can_use_protected_features else status.message,
        }

    def validate_environment(self) -> dict[str, Any]:
        """Verifica se o ambiente possui as dependências necessárias."""
        try:
            validate_runtime_dependencies()
            return {"ok": True, "error": None}
        except RuntimeError as error:
            return {"ok": False, "error": str(error)}

    def choose_files(self) -> list[dict[str, Any]]:
        """Abre o diálogo nativo do Windows para selecionar múltiplos PDFs."""
        if not self._window:
            return []
        import webview

        file_types = ("Arquivos PDF (*.pdf)", "Todos os arquivos (*.*)")
        try:
            result = self._window.create_file_dialog(
                webview.OPEN_DIALOG,
                allow_multiple=True,
                file_types=file_types,
            )
            if not result:
                return []
            return self._process_file_paths(list(result), origin="native_dialog")
        except Exception as error:
            self._emit("toast", {"type": "error", "message": f"Falha ao abrir diálogo: {error}"})
            return []

    def choose_output_directory(self) -> dict[str, Any] | None:
        """Abre o diálogo nativo para seleção da pasta de saída."""
        if not self._window:
            return None
        import webview

        try:
            result = self._window.create_file_dialog(webview.FOLDER_DIALOG)
            if result and len(result) > 0:
                return self._register_directory(result[0], "native_output_dialog")
            return None
        except Exception as error:
            self._emit("toast", {"type": "error", "message": f"Falha ao selecionar pasta: {error}"})
            return None

    def choose_pdf_save_destination(self, file_id: str, suffix: str = "copia") -> dict[str, Any] | None:
        """Autoriza um destino de cópia exclusivamente por meio do diálogo nativo de salvamento."""
        if not self._window:
            return None
        try:
            source = self._resolve_pdf(file_id, "read")
        except ResourceAccessError as error:
            return {"ok": False, "error": str(error)}

        safe_suffix = re.sub(r"[^0-9A-Za-z_-]+", "-", suffix or "copia").strip("-") or "copia"
        suggested_name = f"{source.stem}-{safe_suffix}.pdf"
        try:
            import webview

            result = self._window.create_file_dialog(
                webview.SAVE_DIALOG,
                save_filename=suggested_name,
                file_types=("Arquivos PDF (*.pdf)",),
            )
            if not result:
                return None
            selected = result if isinstance(result, str) else result[0]
            destination = self._register_pdf_destination(selected, "native_save_dialog")
            return {"ok": True, **destination}
        except Exception as error:
            return {"ok": False, "error": f"Falha ao selecionar destino: {error}"}

    def register_dropped_files(self, paths: list[str]) -> list[dict[str, Any]]:
        """Confirma nativamente o drop antes de conceder autoridade sobre caminhos do renderer."""
        if not self._window or not isinstance(paths, list) or len(paths) > 100:
            return []
        candidates: list[str] = []
        seen: set[str] = set()
        for raw_path in paths:
            try:
                path = Path(raw_path).expanduser().resolve()
                key = str(path).casefold()
                if path.is_file() and path.suffix.lower() == ".pdf" and key not in seen:
                    candidates.append(str(path))
                    seen.add(key)
            except (OSError, TypeError, ValueError):
                continue
        if not candidates:
            return []
        names = "\n".join(f"• {Path(path).name}" for path in candidates[:10])
        remainder = len(candidates) - 10
        if remainder > 0:
            names += f"\n• e mais {remainder} arquivo(s)"
        try:
            confirmed = self._window.create_confirmation_dialog(
                "Autorizar PDFs arrastados",
                f"Deseja conceder acesso a {len(candidates)} PDF(s)?\n\n{names}",
            )
        except Exception as error:
            logger.debug(f"Falha ao confirmar arquivos arrastados: {error}")
            return []
        if not confirmed:
            return []
        return self._process_file_paths(candidates, origin="confirmed_drag_drop")

    def has_active_work(self) -> bool:
        with self._indexing_lock, self._page_indexing_lock:
            return bool(
                self.is_converting
                or any(thread.is_alive() for thread in self._indexing_threads.values())
                or self._pending_page_indexes
            )

    def shutdown_for_close(self, timeout_seconds: float = 15.0) -> bool:
        """Interrompe coordenadamente tarefas e só confirma quando nenhuma escrita continua ativa."""
        with self._close_lock:
            if self._services_shutdown:
                return True
            deadline = time.monotonic() + max(0.1, timeout_seconds)
            self._shutdown_requested = True
            self.cancel_requested.set()
            self.resume_processing.set()

            with self._indexing_lock:
                for event in self._indexing_cancel_events.values():
                    event.set()
                for event in self._indexing_pause_events.values():
                    event.set()
                indexing_threads = list(self._indexing_threads.values())

            conversion_thread = self._conversion_thread
            if conversion_thread and conversion_thread is not threading.current_thread():
                conversion_thread.join(timeout=max(0.0, deadline - time.monotonic()))
            for thread in indexing_threads:
                if thread is not threading.current_thread():
                    thread.join(timeout=max(0.0, deadline - time.monotonic()))

            while time.monotonic() < deadline:
                with self._page_indexing_lock:
                    if not self._pending_page_indexes:
                        break
                time.sleep(0.05)

            conversion_alive = bool(conversion_thread and conversion_thread.is_alive())
            indexing_alive = any(thread.is_alive() for thread in indexing_threads)
            with self._page_indexing_lock:
                page_indexing_alive = bool(self._pending_page_indexes)
            if conversion_alive or indexing_alive or page_indexing_alive:
                return False

            self._page_indexing_executor.shutdown(wait=True, cancel_futures=True)
            self._services_shutdown = True
            return True

    def open_markdown(self, markdown_id: str) -> bool:
        """Abre o arquivo Markdown gerado no editor padrão do Windows."""
        try:
            path = self._resolve_markdown(markdown_id, "open")
        except ResourceAccessError as error:
            self._emit("toast", {"type": "error", "message": str(error)})
            return False
        import web_api

        startfile_fn = getattr(getattr(web_api, "os", os), "startfile", getattr(os, "startfile", None))
        try:
            if startfile_fn is None:
                raise OSError("os.startfile não disponível.")
            startfile_fn(path)
            return True
        except OSError as association_error:
            popen_fn = getattr(getattr(web_api, "subprocess", subprocess), "Popen", subprocess.Popen)
            try:
                popen_fn(["notepad.exe", str(path)])
                self._emit(
                    "toast",
                    {"type": "info", "message": "Markdown aberto no Bloco de Notas (sem aplicativo padrão associado)."},
                )
                return True
            except OSError as fallback_error:
                self._emit(
                    "toast",
                    {
                        "type": "error",
                        "message": f"Erro ao abrir arquivo: {association_error}; alternativa indisponível: {fallback_error}",
                    },
                )
                return False

    def open_folder(self, directory_id: str) -> bool:
        """Abre no Explorer somente uma pasta previamente autorizada."""
        try:
            path = self._resolve_directory(directory_id, "open")
        except ResourceAccessError as error:
            self._emit("toast", {"type": "error", "message": str(error)})
            return False
        try:
            subprocess.run(["explorer", str(path)], check=False)
            return True
        except OSError as error:
            self._emit("toast", {"type": "error", "message": f"Erro ao abrir pasta: {error}"})
            return False

    def open_external_url(self, url: str) -> dict[str, Any]:
        """Abre apenas links HTTP(S) no navegador padrão do sistema."""
        parsed = urlsplit(str(url).strip())
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
            return {"ok": False, "error": "Link externo não permitido."}
        try:
            return {"ok": bool(webbrowser.open(url, new=2))}
        except webbrowser.Error as error:
            return {"ok": False, "error": str(error)}
