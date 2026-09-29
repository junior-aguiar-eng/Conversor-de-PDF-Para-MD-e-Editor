"""Mixin de acervo pessoal, visualização de markdowns e busca textual (SQLite FTS5)."""

from __future__ import annotations

import base64
import logging
import mimetypes
import os
import sqlite3
import threading
from concurrent.futures import Future
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

import fitz

from api_bridge.common import _safe_close, format_file_size
from file_authorization import ResourceAccessError

logger = logging.getLogger(__name__)


class LibraryMixin:
    """Métodos de catálogo, indexação e busca textual em Markdown e PDF."""

    def _persisted_markdowns(self, limit: int = 40) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for item in reversed(self._library.get_recent_markdowns(limit)):
            try:
                markdown = self._register_markdown(item["markdown_path"], "persisted_library")
            except (OSError, ResourceAccessError):
                continue
            results.append(
                {
                    "name": item["file_name"],
                    "markdown_path": markdown["markdown_path"],
                    "markdown_id": markdown["markdown_id"],
                    "index_status": item.get("index_status", "not_indexed"),
                    "index_error": item.get("index_error", ""),
                }
            )
        return results

    def get_recent_markdowns(self) -> dict[str, Any]:
        try:
            return {"ok": True, "items": self._persisted_markdowns()}
        except (OSError, sqlite3.Error) as error:
            return {"ok": False, "items": [], "error": str(error)}

    def rebuild_markdown_index(self) -> dict[str, Any]:
        indexed = 0
        failures: list[dict[str, str]] = []
        for item in self._library.get_recent_markdowns(10_000):
            source = Path(item["file_path"])
            markdown = Path(item["markdown_path"])
            try:
                content = markdown.read_text(encoding="utf-8")
                self._library.index_markdown_file(source, markdown, content)
                if not self._library.verify_markdown_index(source, markdown):
                    raise RuntimeError("o índice não confirmou o conteúdo gravado")
                indexed += 1
            except Exception as error:
                self._library.record_markdown_index_failure(source, markdown, str(error))
                failures.append({"markdown_path": str(markdown), "error": str(error)})
        return {"ok": not failures, "indexed": indexed, "failures": failures}

    def read_markdown_preview(self, markdown_id: str) -> dict[str, Any]:
        """Lê o conteúdo do Markdown gerado para visualização em tempo real."""
        try:
            path = self._resolve_markdown(markdown_id, "read")
            content = path.read_text(encoding="utf-8")
            size = path.stat().st_size
            return {
                "ok": True,
                "name": path.name,
                "path": str(path),
                "markdown_id": markdown_id,
                "content": content,
                "size_formatted": format_file_size(size),
            }
        except (OSError, UnicodeError, ResourceAccessError) as error:
            return {"ok": False, "error": f"Erro ao ler Markdown: {error}"}

    def read_markdown_asset(self, markdown_id: str, relative_ref: str) -> dict[str, Any]:
        """Lê imagem relativa contida na pasta do Markdown sem expor acesso genérico."""
        try:
            markdown_path = self._resolve_markdown(markdown_id, "asset_read")
            parsed = urlsplit(str(relative_ref))
            if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
                raise ResourceAccessError("Referência de imagem não permitida.")
            decoded = unquote(parsed.path)
            if not decoded or "\x00" in decoded or decoded.startswith(("/", "\\")):
                raise ResourceAccessError("Referência de imagem inválida.")
            relative = Path(decoded.replace("/", os.sep))
            if relative.is_absolute() or relative.drive or ".." in relative.parts:
                raise ResourceAccessError("A imagem está fora da pasta autorizada.")
            root = markdown_path.parent.resolve()
            asset = (root / relative).resolve()
            if not asset.is_relative_to(root) or not asset.is_file():
                raise ResourceAccessError("Imagem local não encontrada ou não autorizada.")
            allowed_types = {
                ".png": "image/png",
                ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg",
                ".gif": "image/gif",
                ".webp": "image/webp",
                ".bmp": "image/bmp",
            }
            mime = allowed_types.get(asset.suffix.lower())
            guessed, _ = mimetypes.guess_type(asset.name)
            if mime is None or guessed != mime:
                raise ResourceAccessError("Formato de imagem local não permitido.")
            if asset.stat().st_size > 20 * 1024 * 1024:
                raise ResourceAccessError("A imagem local excede o limite de 20 MB.")
            encoded = base64.b64encode(asset.read_bytes()).decode("ascii")
            return {"ok": True, "data_uri": f"data:{mime};base64,{encoded}"}
        except (OSError, ResourceAccessError) as error:
            return {"ok": False, "error": str(error)}

    def start_full_indexing(self, file_id: str, password: str | None = None) -> dict[str, Any]:
        """Dispara a indexação completa em segundo plano com suporte a progresso, pausa e cancelamento (Item 13)."""
        try:
            file_path = self._resolve_pdf(file_id)
        except ResourceAccessError as error:
            return {"ok": False, "error": str(error)}

        path_str = str(file_path.resolve())
        with self._indexing_lock:
            if path_str in self._indexing_threads and self._indexing_threads[path_str].is_alive():
                return {"ok": True, "already_running": True, "message": "Indexação já em andamento."}

            cancel_evt = threading.Event()
            pause_evt = threading.Event()
            pause_evt.set()

            self._indexing_cancel_events[path_str] = cancel_evt
            self._indexing_pause_events[path_str] = pause_evt

            import web_api

            thread_cls = getattr(getattr(web_api, "threading", threading), "Thread", threading.Thread)
            thread = thread_cls(
                target=self._run_full_indexing_worker,
                args=(path_str, file_id, password, cancel_evt, pause_evt),
                daemon=False,
                name=f"PdfIndexer-{Path(path_str).name}",
            )
            self._indexing_threads[path_str] = thread
            thread.start()

        return {"ok": True, "started": True}

    def _run_full_indexing_worker(
        self,
        file_path: str,
        file_id: str,
        password: str | None,
        cancel_evt: threading.Event,
        pause_evt: threading.Event,
    ) -> None:
        doc: fitz.Document | None = None
        try:
            doc, error, needs_pw = self._open_doc_with_auth(file_path, password)
            if not doc or error or needs_pw:
                self._emit("indexing_error", {"file_id": file_id, "error": error or "Erro ao abrir PDF."})
                return

            total_pages = len(doc)
            for idx in range(total_pages):
                if cancel_evt.is_set():
                    self._emit("indexing_cancelled", {"file_id": file_id, "page": idx, "total": total_pages})
                    return

                while not pause_evt.wait(timeout=0.2):
                    if cancel_evt.is_set():
                        self._emit("indexing_cancelled", {"file_id": file_id, "page": idx, "total": total_pages})
                        return

                text = doc[idx].get_text("text")
                self._library.index_single_pdf_page(file_path, idx, text)
                percent = round(((idx + 1) / total_pages) * 100)
                self._emit(
                    "indexing_progress",
                    {"file_id": file_id, "current_page": idx + 1, "total_pages": total_pages, "percent": percent},
                )

            self._library.finalize_incremental_pdf_index(file_path, total_pages)
            self._emit("indexing_completed", {"file_id": file_id, "total_pages": total_pages})
        except Exception as err:
            self._emit("indexing_error", {"file_id": file_id, "error": str(err)})
        finally:
            _safe_close(doc)
            with self._indexing_lock:
                self._indexing_cancel_events.pop(file_path, None)
                self._indexing_pause_events.pop(file_path, None)
                self._indexing_threads.pop(file_path, None)

    def pause_indexing(self, file_id: str) -> dict[str, Any]:
        """Alterna o estado de pausa da indexação completa do documento."""
        try:
            file_path = str(self._resolve_pdf(file_id).resolve())
        except ResourceAccessError as error:
            return {"ok": False, "error": str(error)}

        with self._indexing_lock:
            pause_evt = self._indexing_pause_events.get(file_path)
            if not pause_evt:
                return {"ok": False, "error": "Nenhuma indexação ativa para este documento."}

            if pause_evt.is_set():
                pause_evt.clear()
                return {"ok": True, "is_paused": True}

            pause_evt.set()
            return {"ok": True, "is_paused": False}

    def cancel_indexing(self, file_id: str) -> dict[str, Any]:
        """Cancela a indexação completa em segundo plano."""
        try:
            file_path = str(self._resolve_pdf(file_id).resolve())
        except ResourceAccessError as error:
            return {"ok": False, "error": str(error)}
        thread: threading.Thread | None = None
        with self._indexing_lock:
            cancel_evt = self._indexing_cancel_events.get(file_path)
            pause_evt = self._indexing_pause_events.get(file_path)
            thread = self._indexing_threads.get(file_path)
            if cancel_evt:
                cancel_evt.set()
            if pause_evt:
                pause_evt.set()

        if thread and thread is not threading.current_thread():
            thread.join(timeout=5)
            if thread.is_alive():
                return {"ok": False, "error": "A indexação ainda está encerrando; tente novamente."}
        return {"ok": True, "cancelled": True}

    def search_library(self, query: str) -> dict[str, Any]:
        """Executa busca textual instantânea em toda a biblioteca via FTS5."""
        try:
            raw_results = self._library.search(query, limit=50)
            results: list[dict[str, Any]] = []
            for item in raw_results:
                enriched = dict(item)
                if item.get("content_type") == "markdown":
                    try:
                        markdown = self._register_markdown(item.get("markdown_path", ""), "persisted_library")
                        enriched["resource_id"] = markdown["markdown_id"]
                        enriched["display_path"] = markdown["markdown_path"]
                        enriched["availability_status"] = "available"
                    except (OSError, ResourceAccessError):
                        enriched["resource_id"] = None
                        enriched["display_path"] = item.get("markdown_path", "")
                        enriched["availability_status"] = "temporarily_unavailable"
                else:
                    enriched["library_entry_id"] = self._register_library_entry(
                        item["file_path"], "search_library"
                    )
                    try:
                        pdf = self._register_pdf(item["file_path"], "persisted_library")
                        enriched["resource_id"] = pdf["file_id"]
                        enriched["display_path"] = pdf["path"]
                        enriched["availability_status"] = "available"
                    except (OSError, ResourceAccessError):
                        enriched["resource_id"] = None
                        enriched["display_path"] = item["file_path"]
                        enriched["availability_status"] = "temporarily_unavailable"
                results.append(enriched)
            return {"ok": True, "query": query, "results": results, "total": len(results)}
        except Exception as error:
            logger.error(f"Erro na busca do acervo: {error}", exc_info=True)
            return {"ok": False, "error": f"Falha na busca: {error}", "results": []}

    def get_recent_library(self) -> dict[str, Any]:
        """Retorna os documentos recentes do acervo com status de disponibilidade (Item 16)."""
        try:
            documents: list[dict[str, Any]] = []
            for doc in self._library.get_recent_documents(limit=30):
                enriched = dict(doc)
                enriched["library_entry_id"] = self._register_library_entry(
                    doc["file_path"], "recent_library"
                )
                try:
                    pdf = self._register_pdf(doc["file_path"], "recent_reopen")
                    enriched["resource_id"] = pdf["file_id"]
                    enriched["display_path"] = pdf["path"]
                    enriched["availability_status"] = "available"
                except (OSError, ResourceAccessError):
                    enriched["resource_id"] = None
                    enriched["display_path"] = doc["file_path"]
                    enriched["availability_status"] = "temporarily_unavailable"
                documents.append(enriched)
            return {"ok": True, "documents": documents}
        except Exception as error:
            return {"ok": False, "error": str(error), "documents": []}

    def relocate_library_document(self, library_entry_id: str, new_file_id: str) -> dict[str, Any]:
        """Reconecta um documento ausente do acervo a um novo caminho físico (Item 16)."""
        try:
            old_path = str(self._resolve_library_entry(library_entry_id, "relocate"))
            new_file_path = self._resolve_pdf(new_file_id, "read")
            success = self._library.relocate_document(old_path, str(new_file_path))
            if not success:
                return {"ok": False, "error": "Novo arquivo não encontrado ou inválido."}

            new_pdf = self._register_pdf(new_file_path, "relocated")
            return {"ok": True, "file_id": new_pdf["file_id"], "new_path": new_pdf["path"]}
        except Exception as error:
            return {"ok": False, "error": str(error)}

    def remove_library_document(self, library_entry_id: str) -> dict[str, Any]:
        """Marca um documento como removido do acervo pelo usuário sem excluir marcadores (Item 16)."""
        try:
            file_path = str(self._resolve_library_entry(library_entry_id, "remove"))
            success = self._library.remove_document_from_library(file_path)
            return {"ok": success}
        except Exception as error:
            return {"ok": False, "error": str(error)}

    def _safe_index_pdf(self, file_path: str, password: str | None = None) -> dict[str, Any]:
        """Reindexa o PDF após edição e retorna uma pós-condição verificável."""
        doc: fitz.Document | None = None
        try:
            doc, error, needs_pw = self._open_doc_with_auth(file_path, password)
            if doc and not error and not needs_pw:
                self._library.index_pdf_document(file_path, doc)
                return {"ok": True, "completed": True}
            return {"ok": False, "error": error or "PDF protegido; índice não atualizado."}
        except Exception as err:
            logger.warning(f"Falha na reindexação do PDF {file_path}: {err}")
            return {"ok": False, "error": str(err)}
        finally:
            _safe_close(doc)

    def _enqueue_page_index(self, file_path: str, page_number: int, page_text: str) -> None:
        """Serializa e deduplica escritas incrementais disparadas pela rolagem do leitor."""
        key = (file_path, page_number)
        with self._page_indexing_lock:
            if key in self._pending_page_indexes:
                return
            self._pending_page_indexes.add(key)

        try:
            future = self._page_indexing_executor.submit(
                self._library.index_single_pdf_page,
                file_path,
                page_number,
                page_text,
            )
        except RuntimeError as error:
            with self._page_indexing_lock:
                self._pending_page_indexes.discard(key)
            logger.debug(f"Fila de indexação indisponível para {file_path}, página {page_number + 1}: {error}")
            return

        def release(completed: Future) -> None:
            try:
                completed.result()
            except Exception as error:
                logger.debug(f"Falha na indexação incremental de {file_path}, página {page_number + 1}: {error}")
            finally:
                with self._page_indexing_lock:
                    self._pending_page_indexes.discard(key)

        future.add_done_callback(release)
