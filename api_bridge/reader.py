"""Mixin de leitura, renderização de páginas e marcadores do PDF."""

from __future__ import annotations

import base64
import logging
from pathlib import Path
from typing import Any

from api_bridge.common import (
    _license_denial,
    _pdf_backup_path,
    _safe_close,
    _validate_pixel_budget,
    _validated_clip_rect,
    _validated_dpi,
)
from file_authorization import ResourceAccessError
from license_core import LicenseAccessError
from licensing import LicenseRequiredError

logger = logging.getLogger(__name__)


class ReaderMixin:
    """Métodos de visualização, extração de trechos e histórico de leitura."""

    def get_pdf_info(self, file_id: str, password: str | None = None) -> dict[str, Any]:
        """Retorna metadados essenciais do PDF para abertura instantânea do Leitor (Fase 4)."""
        import web_api

        try:
            web_api.require_software_activation("reader")
        except (LicenseRequiredError, LicenseAccessError) as error:
            return _license_denial(error)
        try:
            file_path = self._resolve_pdf(file_id)
        except ResourceAccessError as error:
            return {"ok": False, "error": str(error), "needs_password": False}
        doc, error, needs_password = self._open_doc_with_auth(file_path, password)
        if error or needs_password or doc is None:
            return {"ok": False, "error": error or "PDF protegido.", "needs_password": needs_password, "is_encrypted": True}

        try:
            path = Path(file_path).resolve()
            page_count = len(doc)
            is_encrypted = doc.is_encrypted
            metadata = doc.metadata or {}

            # Indexa metadados O(1) no banco sem bloquear a abertura
            self._library.index_document_metadata_only(path, page_count, path.stat().st_size if path.exists() else 0)

            session = self._library.get_session_state(str(path))
            bookmarks = self._library.get_bookmarks(str(path))

            first_chunk_limit = min(page_count, 50)
            initial_pages = []
            for idx in range(first_chunk_limit):
                p = doc[idx]
                initial_pages.append(
                    {
                        "page_number": idx,
                        "width": p.rect.width,
                        "height": p.rect.height,
                        "rotation": p.rotation,
                    }
                )

            info = {
                "ok": True,
                "file_name": path.name,
                "file_path": str(path),
                "file_id": file_id,
                "page_count": page_count,
                "is_encrypted": is_encrypted,
                "metadata": metadata,
                "session_state": session,
                "bookmarks": bookmarks,
                "pages": initial_pages,
                "backup_available": _pdf_backup_path(path).is_file(),
            }
            return info
        except Exception as error:
            return {"ok": False, "error": f"Erro ao inspecionar PDF: {error}"}
        finally:
            _safe_close(doc)

    def get_pdf_page_range(
        self, file_id: str, start_page: int = 0, count: int = 50, password: str | None = None
    ) -> dict[str, Any]:
        """Retorna dimensões e rotação de uma faixa de páginas por solicitação (Item 12)."""
        import web_api

        try:
            web_api.require_software_activation("reader")
        except (LicenseRequiredError, LicenseAccessError) as error:
            return _license_denial(error)
        try:
            file_path = self._resolve_pdf(file_id)
        except ResourceAccessError as error:
            return {"ok": False, "error": str(error)}
        doc, error, needs_password = self._open_doc_with_auth(file_path, password)
        if error or needs_password or doc is None:
            return {"ok": False, "error": error or "PDF protegido.", "needs_password": needs_password}

        try:
            page_count = len(doc)
            start_idx = max(0, start_page)
            end_idx = min(page_count, start_idx + count)
            pages = []
            for idx in range(start_idx, end_idx):
                p = doc[idx]
                pages.append(
                    {
                        "page_number": idx,
                        "width": p.rect.width,
                        "height": p.rect.height,
                        "rotation": p.rotation,
                    }
                )
            return {
                "ok": True,
                "file_id": file_id,
                "start_page": start_idx,
                "count": len(pages),
                "total_pages": page_count,
                "pages": pages,
            }
        except Exception as error:
            return {"ok": False, "error": f"Erro ao obter faixa de páginas: {error}"}
        finally:
            _safe_close(doc)

    def render_page_hq(
        self,
        file_id: str,
        page_number: int = 0,
        dpi: int = 150,
        password: str | None = None,
        rotation: int | None = None,
    ) -> dict[str, Any]:
        """Renderiza uma página sob demanda e indexa incrementalmente no FTS5 (Fase 4)."""
        import web_api

        try:
            web_api.require_software_activation("reader")
        except (LicenseRequiredError, LicenseAccessError) as error:
            return _license_denial(error)
        try:
            page_number = int(page_number)
            dpi = _validated_dpi(dpi)
        except (TypeError, ValueError) as error:
            return {"ok": False, "error": str(error), "needs_password": False}
        try:
            file_path = self._resolve_pdf(file_id)
        except ResourceAccessError as error:
            return {"ok": False, "error": str(error), "needs_password": False}
        doc, error, needs_password = self._open_doc_with_auth(file_path, password)
        if error or needs_password or doc is None:
            return {"ok": False, "error": error or "PDF protegido.", "needs_password": needs_password}

        try:
            path = Path(file_path).resolve()
            if not (0 <= page_number < len(doc)):
                return {"ok": False, "error": f"Página {page_number} fora do intervalo."}

            page = doc[page_number]
            if rotation is not None:
                rotation = int(rotation)
                if rotation not in {0, 90, 180, 270}:
                    raise ValueError("Rotação de visualização inválida.")
                page.set_rotation(rotation)
            _validate_pixel_budget(page.rect, dpi)
            pix = page.get_pixmap(dpi=dpi)
            img_b64 = base64.b64encode(pix.tobytes("png")).decode("utf-8")
            data_uri = f"data:image/png;base64,{img_b64}"

            # Extrai texto e indexa a página visitada de forma incremental em background
            page_text = page.get_text("text")
            self._enqueue_page_index(str(path), page_number, page_text)

            return {
                "ok": True,
                "image": data_uri,
                "width": page.rect.width,
                "height": page.rect.height,
                "pixel_width": pix.width,
                "pixel_height": pix.height,
                "page_count": len(doc),
                "page_number": page_number,
                "rotation": page.rotation,
                "file_name": path.name,
                "file_path": str(path),
                "file_id": file_id,
            }
        except Exception as error:
            return {"ok": False, "error": f"Erro ao renderizar: {error}"}
        finally:
            _safe_close(doc)

    def extract_snippet(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Extrai texto e imagem recortada em alta resolução de uma região retangular do PDF."""
        import web_api

        file_id = payload.get("file_id", "")
        password = payload.get("password")
        rect_coords = payload.get("rect", [0, 0, 100, 100])
        try:
            page_number = int(payload.get("page_number", 0))
            dpi = _validated_dpi(payload.get("dpi", 150))
        except (TypeError, ValueError) as error:
            return {"ok": False, "error": str(error), "needs_password": False}

        try:
            file_path = self._resolve_pdf(str(file_id), "read")
        except ResourceAccessError as error:
            return {"ok": False, "error": str(error), "needs_password": False}

        doc, error, needs_password = self._open_doc_with_auth(file_path, password)
        if error or needs_password or doc is None:
            return {"ok": False, "error": error or "PDF protegido por senha.", "needs_password": needs_password}

        try:
            if not (0 <= page_number < len(doc)):
                return {"ok": False, "error": f"Página {page_number} fora do intervalo (total: {len(doc)})."}

            page = doc[page_number]
            clip_rect = _validated_clip_rect(rect_coords, page.rect)
            _validate_pixel_budget(clip_rect, dpi)

            extracted_text = page.get_text("text", clip=clip_rect).strip()
            pix = page.get_pixmap(clip=clip_rect, dpi=dpi)
            img_bytes = pix.tobytes("png")
            img_b64 = base64.b64encode(img_bytes).decode("utf-8")

            ocr_applied = False
            if len(extracted_text) < 4:
                try:
                    web_api.require_software_activation("ocr")
                    from ocr_engine import ocr_pixmap

                    ocr_text, _ = ocr_pixmap(pix)
                    if ocr_text.strip():
                        extracted_text = ocr_text.strip()
                        ocr_applied = True
                except (LicenseRequiredError, LicenseAccessError) as error:
                    return _license_denial(error)
                except Exception as ocr_err:
                    logger.debug(f"OCR snippet fallback: {ocr_err}")

            return {
                "ok": True,
                "text": extracted_text,
                "ocr_applied": ocr_applied,
                "image_base64": f"data:image/png;base64,{img_b64}",
                "width": pix.width,
                "height": pix.height,
                "page_number": page_number,
            }
        except Exception as error:
            return {"ok": False, "error": f"Erro ao extrair trecho: {error}"}
        finally:
            _safe_close(doc)

    def save_reading_state(self, file_id: str, last_page: int, zoom: str = "1.0") -> dict[str, Any]:
        """Salva a última página lida e o zoom preferido no documento."""
        try:
            file_path = self._resolve_pdf(file_id)
            self._library.save_session_state(str(file_path), last_page, zoom)
            return {"ok": True}
        except Exception as error:
            return {"ok": False, "error": str(error)}

    def get_reading_state(self, file_id: str) -> dict[str, Any]:
        """Recupera o histórico de leitura do documento."""
        try:
            file_path = self._resolve_pdf(file_id)
            state = self._library.get_session_state(str(file_path))
            return {"ok": True, "state": state}
        except Exception as error:
            return {"ok": False, "error": str(error)}

    def add_bookmark(self, file_id: str, page_number: int, title: str = "") -> dict[str, Any]:
        """Adiciona um marcador de página no documento."""
        try:
            file_path = self._resolve_pdf(file_id)
            res = self._library.add_bookmark(str(file_path), page_number, title)
            return res
        except Exception as error:
            return {"ok": False, "error": str(error)}

    def get_bookmarks(self, file_id: str) -> dict[str, Any]:
        """Retorna todos os marcadores de um documento."""
        try:
            file_path = self._resolve_pdf(file_id)
            bookmarks = self._library.get_bookmarks(str(file_path))
            return {"ok": True, "bookmarks": bookmarks}
        except Exception as error:
            return {"ok": False, "error": str(error), "bookmarks": []}

    def delete_bookmark(self, file_id: str, bookmark_id: int) -> dict[str, Any]:
        """Exclui um marcador de página."""
        try:
            file_path = self._resolve_pdf(file_id)
            success = self._library.delete_bookmark(bookmark_id, str(file_path))
            return {"ok": success}
        except Exception as error:
            return {"ok": False, "error": str(error)}
