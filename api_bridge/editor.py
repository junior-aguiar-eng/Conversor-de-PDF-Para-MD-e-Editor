"""Mixin de edição, anotações, rotação e proteção criptográfica de PDFs."""

from __future__ import annotations

import logging
import os
import shutil
import uuid
from pathlib import Path
from typing import Any

import fitz

from api_bridge.common import (
    _fit_textbox_rect,
    _flush_file,
    _license_denial,
    _parse_color,
    _pdf_backup_path,
    _safe_close,
    _save_doc_safely,
    _validate_annotation_payload,
    _validate_saved_pdf,
)
from file_authorization import ResourceAccessError
from license_core import LicenseAccessError
from licensing import LicenseRequiredError

logger = logging.getLogger(__name__)


class EditorMixin:
    """Métodos para rotação de páginas, anotações e proteção/desproteção de PDFs."""

    def set_pdf_password(self, file_id: str, password: str) -> dict[str, Any]:
        """Tenta autenticar e memorizar a senha de um PDF protegido."""
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
            return {"ok": False, "error": error or "Senha incorreta."}
        doc.close()
        return {"ok": True, "message": "Senha autenticada com sucesso."}

    def rotate_pdf_page(
        self,
        file_id: str,
        page_number: int,
        degrees: int,
        password: str | None = None,
        output_file_id: str | None = None,
    ) -> dict[str, Any]:
        """Gira uma página específica em incrementos de 90 graus e salva no original ou como cópia (Item 19)."""
        import web_api

        try:
            web_api.require_software_activation("reader")
        except (LicenseRequiredError, LicenseAccessError) as error:
            return _license_denial(error)
        try:
            file_path = self._resolve_pdf(file_id, "write")
        except ResourceAccessError as error:
            return {"ok": False, "error": str(error), "needs_password": False}

        cancel_result = self.cancel_indexing(file_id)
        if not cancel_result.get("ok"):
            return cancel_result
        doc, error, needs_password = self._open_doc_with_auth(file_path, password)
        if error or needs_password or doc is None:
            return {"ok": False, "error": error or "PDF protegido.", "needs_password": needs_password}

        try:
            path = Path(file_path).resolve()
            target_path = self._resolve_pdf_destination(output_file_id) if output_file_id else path
            if not (0 <= page_number < len(doc)):
                return {"ok": False, "error": "Página inexistente."}

            page = doc[page_number]
            new_rotation = (page.rotation + degrees) % 360
            page.set_rotation(new_rotation)

            backup_path = _save_doc_safely(doc, target_path)
            res = {
                "ok": True,
                "new_rotation": new_rotation,
                "message": (
                    f"Página {page_number + 1} rotacionada para {new_rotation}°. "
                    f"Backup anterior: {backup_path.name}."
                    if backup_path
                    else f"Página {page_number + 1} rotacionada para {new_rotation}°."
                ),
                "target_path": str(target_path),
                "is_copy": target_path != path,
                "backup_path": str(backup_path) if backup_path else None,
            }
            if target_path != path:
                new_pdf = self._register_pdf(target_path, "copy")
                res["new_file_id"] = new_pdf["file_id"]
                res["new_file"] = new_pdf
            return res
        except Exception as error:
            return {"ok": False, "error": f"Erro ao rotacionar página: {error}"}
        finally:
            _safe_close(doc)

    def protect_pdf(
        self,
        file_id: str,
        user_pw: str,
        owner_pw: str = "",
        output_file_id: str | None = None,
    ) -> dict[str, Any]:
        """Aplica criptografia AES-256 no arquivo PDF com senhas no original ou como cópia (Item 19)."""
        import web_api

        try:
            web_api.require_software_activation("reader")
        except (LicenseRequiredError, LicenseAccessError) as error:
            return _license_denial(error)
        try:
            path = self._resolve_pdf(file_id, "write")
        except ResourceAccessError as error:
            return {"ok": False, "error": str(error)}
        if not user_pw:
            return {"ok": False, "error": "A senha do usuário não pode ser vazia."}

        cancel_result = self.cancel_indexing(file_id)
        if not cancel_result.get("ok"):
            return cancel_result
        doc, error, needs_password = self._open_doc_with_auth(path)
        if error or doc is None:
            return {"ok": False, "error": error or "Não foi possível abrir o PDF."}

        try:
            target_path = self._resolve_pdf_destination(output_file_id) if output_file_id else path
            owner = owner_pw if owner_pw else user_pw
            perm = fitz.PDF_PERM_PRINT | fitz.PDF_PERM_COPY | fitz.PDF_PERM_ANNOTATE | fitz.PDF_PERM_ACCESSIBILITY
            backup_path = _save_doc_safely(
                doc,
                target_path,
                encryption=fitz.PDF_ENCRYPT_AES_256,
                user_pw=user_pw,
                owner_pw=owner,
                permissions=perm,
            )
            self._pdf_passwords[str(target_path)] = user_pw
            res = {
                "ok": True,
                "message": (
                    f"PDF protegido com AES-256. Backup anterior: {backup_path.name}."
                    if backup_path
                    else "PDF protegido com sucesso usando criptografia AES-256."
                ),
                "target_path": str(target_path),
                "is_copy": target_path != path,
                "backup_path": str(backup_path) if backup_path else None,
            }
            if target_path != path:
                new_pdf = self._register_pdf(target_path, "copy")
                res["new_file_id"] = new_pdf["file_id"]
                res["new_file"] = new_pdf
            return res
        except Exception as error:
            return {"ok": False, "error": f"Erro ao proteger PDF: {error}"}
        finally:
            _safe_close(doc)

    def unprotect_pdf(self, file_id: str, current_pw: str = "", output_file_id: str | None = None) -> dict[str, Any]:
        """Remove a proteção por senha de um PDF no original ou como cópia (Item 19)."""
        import web_api

        try:
            web_api.require_software_activation("reader")
        except (LicenseRequiredError, LicenseAccessError) as error:
            return _license_denial(error)
        try:
            path = self._resolve_pdf(file_id, "write")
        except ResourceAccessError as error:
            return {"ok": False, "error": str(error)}

        cancel_result = self.cancel_indexing(file_id)
        if not cancel_result.get("ok"):
            return cancel_result
        doc, error, needs_password = self._open_doc_with_auth(path, current_pw)
        if error or doc is None:
            return {"ok": False, "error": error or "Não foi possível abrir o PDF com a senha informada."}

        try:
            target_path = self._resolve_pdf_destination(output_file_id) if output_file_id else path
            backup_path = _save_doc_safely(
                doc,
                target_path,
                encryption=fitz.PDF_ENCRYPT_NONE,
            )
            self._pdf_passwords.pop(str(target_path), None)

            res = {
                "ok": True,
                "message": (
                    f"Proteção removida. Backup anterior: {backup_path.name}."
                    if backup_path
                    else "Proteção por senha removida com sucesso."
                ),
                "target_path": str(target_path),
                "is_copy": target_path != path,
                "backup_path": str(backup_path) if backup_path else None,
            }
            if target_path != path:
                new_pdf = self._register_pdf(target_path, "copy")
                res["new_file_id"] = new_pdf["file_id"]
                res["new_file"] = new_pdf
            return res
        except Exception as error:
            return {"ok": False, "error": f"Erro ao remover senha do PDF: {error}"}
        finally:
            _safe_close(doc)

    def save_pdf_annotations(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Grava anotações nativas no arquivo PDF original ou como cópia (Item 19)."""
        import web_api

        try:
            web_api.require_software_activation("reader")
        except (LicenseRequiredError, LicenseAccessError) as error:
            return _license_denial(error)
        if payload.get("output_path"):
            return {"ok": False, "error": "Destino por caminho não autorizado."}
        file_id = payload.get("file_id", "")
        password = payload.get("password")
        annotations = payload.get("annotations", [])
        rotations = payload.get("rotations", [])
        output_file_id = payload.get("output_file_id")

        try:
            file_path = self._resolve_pdf(str(file_id), "write")
        except ResourceAccessError as error:
            return {"ok": False, "error": str(error), "needs_password": False}

        cancel_result = self.cancel_indexing(str(file_id))
        if not cancel_result.get("ok"):
            return cancel_result
        doc, error, needs_password = self._open_doc_with_auth(file_path, password)
        if error or needs_password or doc is None:
            return {"ok": False, "error": error or "PDF protegido por senha.", "needs_password": needs_password}

        path = Path(file_path).resolve()
        try:
            if not isinstance(rotations, list) or len(rotations) > len(doc):
                raise ValueError("Lista de rotações inválida.")
            validated_rotations: list[tuple[int, int]] = []
            seen_rotation_pages: set[int] = set()
            for item in rotations:
                if not isinstance(item, dict):
                    raise ValueError("Rotação inválida.")
                page_num = int(item.get("page_number", -1))
                degrees = int(item.get("degrees", 0))
                if page_num in seen_rotation_pages or not (0 <= page_num < len(doc)):
                    raise ValueError("Página de rotação inválida ou repetida.")
                if degrees not in {0, 90, 180, 270}:
                    raise ValueError("A rotação deve usar incrementos de 90 graus.")
                seen_rotation_pages.add(page_num)
                if degrees:
                    validated_rotations.append((page_num, degrees))

            for page_num, degrees in validated_rotations:
                page = doc[page_num]
                page.set_rotation((page.rotation + degrees) % 360)

            annotations = _validate_annotation_payload(annotations, doc)
            applied_count = 0

            for item in annotations:
                page_num = int(item.get("page_number", 0))
                page = doc[page_num]
                annot_type = str(item.get("type", "")).lower()

                # 1. Caneta Livre Tradicional
                if annot_type in ("ink", "drawing", "caneta"):
                    strokes = item.get("strokes", [])
                    if strokes:
                        annot = page.add_ink_annot(strokes)
                        color = _parse_color(item.get("color"), default=(0.1, 0.1, 0.1))
                        width = float(item.get("width", 2.0))
                        annot.set_colors(stroke=color)
                        annot.set_border(width=width)
                        annot.update()
                        applied_count += 1

                # 2. Caneta Marca-Texto Livre (Grifador Fluorescente)
                elif annot_type in ("highlight_pen", "caneta_marca_texto", "pincel_marca_texto"):
                    strokes = item.get("strokes", [])
                    if strokes:
                        annot = page.add_ink_annot(strokes)
                        color = _parse_color(item.get("color"), default=(1.0, 0.9, 0.2))
                        width = float(item.get("width", 16.0))
                        annot.set_colors(stroke=color)
                        annot.set_border(width=width)
                        annot.set_opacity(0.45)
                        annot.update()
                        applied_count += 1

                # 3. Marca-Texto em Bloco (Retângulo Delimitador)
                elif annot_type in ("highlight", "highlight_block", "marca_texto", "marca-texto"):
                    rect = item.get("rect")
                    if rect and len(rect) >= 4:
                        fitz_rect = fitz.Rect(rect[0], rect[1], rect[2], rect[3])
                        annot = page.add_highlight_annot(fitz_rect)
                        color = _parse_color(item.get("color"), default=(1.0, 0.9, 0.2))
                        annot.set_colors(stroke=color)
                        annot.set_opacity(0.45)
                        annot.update()
                        applied_count += 1

                # 4. Inserção de Texto / Card de Anotação (Com Word Wrap Automático)
                elif annot_type in ("text", "freetext", "texto"):
                    text = str(item.get("text", "")).strip()
                    if not text:
                        continue

                    rect = item.get("rect")
                    x = item.get("x")
                    y = item.get("y")
                    style = item.get("style", "none")
                    fontsize = float(item.get("fontsize", item.get("size", 14.0)))
                    fontname = "helv-bold" if item.get("bold") else "helv"

                    pt_x = float(x if x is not None else (rect[0] if rect else 50.0))
                    pt_y = float(y if y is not None else (rect[1] if rect else 50.0))

                    if style == "none" or not style:
                        text_color = _parse_color(item.get("text_color") or item.get("color"), default=(0.1, 0.1, 0.1))
                        box_w = float(item.get("width", max(300.0, fontsize * 15)))
                        box_h = float(item.get("height", fontsize * 1.5 * (text.count("\n") + 2)))
                        text_rect = fitz.Rect(pt_x, pt_y, pt_x + box_w, pt_y + box_h)
                        text_rect = _fit_textbox_rect(
                            page,
                            text_rect,
                            text,
                            fontname=fontname,
                            fontsize=fontsize,
                        )
                        if text_rect is None:
                            raise ValueError("O texto não cabe na área disponível da página.")

                        remaining = page.insert_textbox(
                            text_rect,
                            text,
                            fontname=fontname,
                            fontsize=fontsize,
                            color=text_color,
                            render_mode=0,
                            align=0,
                        )
                        if remaining < 0:
                            raise RuntimeError("O mecanismo de PDF não confirmou a inserção do texto.")
                        applied_count += 1
                    else:
                        padding_x = 8.0
                        padding_y = 5.0
                        card_w = float(item.get("width", len(text) * fontsize * 0.55 + padding_x * 2 + 10))
                        card_h = float(item.get("height", fontsize * 1.5 * (text.count("\n") + 1) + padding_y * 2 + 10))
                        card_rect = fitz.Rect(pt_x, pt_y, pt_x + card_w, pt_y + card_h)

                        if style == "postit":
                            bg_color = (0.996, 0.941, 0.541)  # #fef08a
                            left_border_color = (0.917, 0.702, 0.031)  # #eab308
                            text_color = _parse_color(item.get("text_color"), default=(0.443, 0.247, 0.071))
                        elif style == "danger":
                            bg_color = (0.996, 0.886, 0.886)  # #fee2e2
                            left_border_color = (0.937, 0.267, 0.267)  # #ef4444
                            text_color = _parse_color(item.get("text_color"), default=(0.600, 0.106, 0.106))
                        elif style == "white":
                            bg_color = (1.0, 1.0, 1.0)
                            left_border_color = (0.008, 0.518, 0.780)  # #0284c7
                            text_color = _parse_color(item.get("text_color"), default=(0.047, 0.290, 0.431))
                        else:
                            bg_color = (1.0, 1.0, 1.0)
                            left_border_color = (0.2, 0.2, 0.2)
                            text_color = _parse_color(item.get("text_color"), default=(0.1, 0.1, 0.1))

                        text_rect = fitz.Rect(
                            pt_x + padding_x + 2, pt_y + padding_y, pt_x + card_w - padding_x, pt_y + card_h - padding_y
                        )
                        fitted_text_rect = _fit_textbox_rect(
                            page,
                            text_rect,
                            text,
                            fontname=fontname,
                            fontsize=fontsize,
                        )
                        if fitted_text_rect is None:
                            raise ValueError("O texto não cabe na área disponível da página.")

                        card_rect.y1 += fitted_text_rect.y1 - text_rect.y1
                        page.draw_rect(card_rect, color=None, fill=bg_color, width=0)
                        page.draw_line(
                            fitz.Point(pt_x, pt_y),
                            fitz.Point(pt_x, card_rect.y1),
                            color=left_border_color,
                            width=3.5,
                        )

                        remaining = page.insert_textbox(
                            fitted_text_rect,
                            text,
                            fontname=fontname,
                            fontsize=fontsize,
                            color=text_color,
                            render_mode=0,
                            align=0,
                        )
                        if remaining < 0:
                            raise RuntimeError("O mecanismo de PDF não confirmou a inserção do texto.")
                        applied_count += 1

            target_path = self._resolve_pdf_destination(str(output_file_id)) if output_file_id else path
            backup_path = _save_doc_safely(doc, target_path)
            res = {
                "ok": True,
                "saved_count": applied_count,
                "rotation_count": len(validated_rotations),
                "message": (
                    f"Alterações salvas: {applied_count} anotação(ões) e {len(validated_rotations)} rotação(ões). "
                    f"Backup anterior: {backup_path.name}."
                    if backup_path
                    else f"Alterações salvas: {applied_count} anotação(ões) e {len(validated_rotations)} rotação(ões)."
                ),
                "target_path": str(target_path),
                "is_copy": target_path != path,
                "backup_path": str(backup_path) if backup_path else None,
            }
            if target_path != path:
                new_pdf = self._register_pdf(target_path, "copy")
                res["new_file_id"] = new_pdf["file_id"]
                res["new_file"] = new_pdf
            res["index_status"] = self._safe_index_pdf(str(target_path), password)
            return res
        except Exception as error:
            return {"ok": False, "error": f"Erro ao salvar anotações: {error}"}
        finally:
            _safe_close(doc)

    def restore_pdf_backup(self, file_id: str, password: str | None = None) -> dict[str, Any]:
        """Restaura atomicamente o backup imediatamente anterior e o consome após a promoção."""
        import web_api

        try:
            web_api.require_software_activation("reader")
        except (LicenseRequiredError, LicenseAccessError) as error:
            return _license_denial(error)
        try:
            file_path = self._resolve_pdf(file_id, "write")
        except ResourceAccessError as error:
            return {"ok": False, "error": str(error)}

        cancel_result = self.cancel_indexing(file_id)
        if not cancel_result.get("ok"):
            return cancel_result

        path = Path(file_path).resolve()
        backup_path = _pdf_backup_path(path)
        if not backup_path.is_file():
            return {"ok": False, "error": "Nenhum backup disponível para este PDF."}

        restore_temp = path.with_name(f".{path.stem}.{uuid.uuid4().hex}.restore.tmp.pdf")
        try:
            with fitz.open(str(backup_path)) as backup_doc:
                expected_page_count = backup_doc.page_count
                if not backup_doc.is_pdf:
                    raise RuntimeError("O backup não é um PDF válido.")
            shutil.copy2(backup_path, restore_temp)
            _flush_file(restore_temp)
            _validate_saved_pdf(restore_temp, expected_page_count)
            os.replace(restore_temp, path)
            backup_available = True
            try:
                backup_path.unlink(missing_ok=True)
                backup_available = False
            except OSError as cleanup_error:
                logger.warning(f"PDF restaurado, mas o backup não pôde ser consumido: {cleanup_error}")
            index_status = self._safe_index_pdf(str(path), password)
            index_message = (
                "O índice também foi atualizado."
                if index_status.get("ok")
                else "O PDF foi restaurado, mas o índice não pôde ser atualizado."
            )
            return {
                "ok": True,
                "message": f"A última gravação foi revertida. {index_message}",
                "restored_path": str(path),
                "backup_available": backup_available,
                "index_status": index_status,
            }
        except Exception as error:
            return {"ok": False, "error": f"Erro ao restaurar backup: {error}"}
        finally:
            restore_temp.unlink(missing_ok=True)
