"""Camada Bridge Python-JavaScript para a interface Chromium (PyWebView)."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import subprocess
import threading
import time
import traceback
from concurrent.futures import FIRST_COMPLETED, Future, ProcessPoolExecutor, ThreadPoolExecutor, wait
from concurrent.futures.process import BrokenProcessPool
from pathlib import Path
from typing import Any

import edge_tts
import fitz
from deep_translator import GoogleTranslator

from constants import (
    APP_NAME,
    APP_VERSION,
    DEFAULT_MAX_CHUNK_CHARACTERS,
    DEFAULT_OUTPUT_DIR,
    MAX_PAGE_COUNT,
)
from converter import (
    PdfMarkdownConverter,
    convert_worker,
    init_worker,
    validate_runtime_dependencies,
)
from library_db import LibraryDatabase
from licensing import (
    activate_software as lic_activate_software,
)
from licensing import (
    is_software_activated,
)
from markdown_utils import HeadingProfile
from models import ConversionFailure, ConversionResult, format_duration

logger = logging.getLogger(__name__)

MAX_PARALLEL_WORKERS = 4
MIN_CHUNK_CHARACTERS = 1_000


def format_file_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} B"
    if size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    return f"{size_bytes / (1024 * 1024):.1f} MB"


def _parse_color(c: Any, default: tuple[float, float, float] = (1.0, 0.0, 0.0)) -> tuple[float, float, float]:
    """Converte cor em lista [r,g,b] (0..1) ou string hexadecimal (#RRGGBB) para tupla float."""
    if isinstance(c, (list, tuple)) and len(c) >= 3:
        try:
            r, g, b = float(c[0]), float(c[1]), float(c[2])
            if r > 1.0 or g > 1.0 or b > 1.0:
                return (r / 255.0, g / 255.0, b / 255.0)
            return (r, g, b)
        except ValueError, TypeError:
            return default
    if isinstance(c, str) and c.startswith("#"):
        hex_str = c.lstrip("#")
        if len(hex_str) == 6:
            try:
                return (
                    int(hex_str[0:2], 16) / 255.0,
                    int(hex_str[2:4], 16) / 255.0,
                    int(hex_str[4:6], 16) / 255.0,
                )
            except ValueError:
                return default
        if len(hex_str) == 3:
            try:
                return (
                    int(hex_str[0] * 2, 16) / 255.0,
                    int(hex_str[1] * 2, 16) / 255.0,
                    int(hex_str[2] * 2, 16) / 255.0,
                )
            except ValueError:
                return default
    return default


def _safe_close(doc: fitz.Document | None) -> None:
    """Fecha o documento fitz com segurança, sem disparar ValueError se já estiver fechado."""
    if doc is not None:
        try:
            if not doc.is_closed:
                doc.close()
        except Exception:
            pass


def _save_doc_safely(doc: fitz.Document, file_path: Path, **save_kwargs: Any) -> None:
    """Salva o documento PDF de forma segura, tratando criptografia e salvamento incremental."""
    target_path = file_path.resolve()
    temp_file: Path | None = None
    try:
        # 1. Se for para DESPROTEGER ou PROTEGER, o doc.save() atômico é obrigatório
        if "encryption" in save_kwargs:
            temp_file = target_path.with_name(f"{target_path.stem}.tmp_{int(time.time() * 1000)}.pdf")
            doc.save(str(temp_file), **save_kwargs)
            _safe_close(doc)
            temp_file.replace(target_path)
            temp_file = None
            return

        # 2. Para ANOTAÇÕES normais, Incremental Save é mais rápido e preserva assinaturas
        try:
            doc.saveIncr()
            _safe_close(doc)
        except Exception:
            # Fallback apenas se o arquivo não for criptografado
            if doc is not None and not doc.is_closed and not doc.is_encrypted:
                temp_file = target_path.with_name(f"{target_path.stem}.tmp_{int(time.time() * 1000)}.pdf")
                doc.save(str(temp_file))
                _safe_close(doc)
                temp_file.replace(target_path)
                temp_file = None
            else:
                raise
    finally:
        if temp_file and temp_file.exists():
            temp_file.unlink(missing_ok=True)
        _safe_close(doc)


class BridgeApi:
    """API exposta para o JavaScript via window.pywebview.api."""

    def __init__(self) -> None:
        self._window: Any = None
        self.cancel_requested = threading.Event()
        self.resume_processing = threading.Event()
        self.resume_processing.set()
        self.is_paused = False
        self.is_converting = False
        self._batch_start_time = 0.0
        self._pdf_passwords: dict[str, str] = {}
        self._library = LibraryDatabase()

    def _open_doc_with_auth(
        self, file_path: str | Path, password: str | None = None
    ) -> tuple[fitz.Document | None, str | None, bool]:
        """Abre o documento PDF autenticando caso esteja criptografado."""
        path = Path(file_path).resolve()
        if not path.is_file():
            return None, "Arquivo não encontrado.", False

        try:
            doc = fitz.open(str(path))
            if doc.is_encrypted:
                pw = password or self._pdf_passwords.get(str(path))
                if pw:
                    auth_res = doc.authenticate(pw)
                    if auth_res > 0:
                        self._pdf_passwords[str(path)] = pw
                        return doc, None, False
                doc.close()  # FECHAMENTO OBRIGATÓRIO (Impede travamento do arquivo no Windows)
                return None, "O documento PDF está protegido por senha.", True
            return doc, None, False
        except Exception as err:
            return None, f"Falha ao abrir PDF: {err}", False

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
            "default_output_dir": str(DEFAULT_OUTPUT_DIR),
            "default_chunk_limit": DEFAULT_MAX_CHUNK_CHARACTERS,
            "max_page_count": MAX_PAGE_COUNT,
        }

    def get_terms_acceptance_status(self) -> dict[str, Any]:
        """Verifica se o usuário já aceitou os termos de uso formalmente."""
        try:
            return self._library.get_terms_status()
        except Exception as error:
            logger.error(f"Erro ao verificar status dos termos: {error}")
            return {"accepted": False}

    def accept_terms(self, terms_version: str = "1.0") -> dict[str, Any]:
        """Registra o aceite formal e irrevogável dos termos de uso."""
        try:
            self._library.save_terms_acceptance(terms_version)
            return {"ok": True}
        except Exception as error:
            logger.error(f"Erro ao registrar aceite dos termos: {error}")
            return {"ok": False, "error": str(error)}

    def get_license_info(self) -> dict[str, Any]:
        """Retorna o status de ativação do software e o identificador de hardware da máquina."""
        is_activated, machine_id = is_software_activated()
        return {
            "is_activated": is_activated,
            "machine_id": machine_id,
            "message": "Software licenciado e ativado." if is_activated else "Ativação pendente para esta máquina.",
        }

    def activate_software(self, key: str) -> dict[str, Any]:
        """Processa a chave de ativação fornecida pelo usuário e desbloqueia o software."""
        result = lic_activate_software(key)
        if result["ok"]:
            self._emit("toast", {"type": "success", "message": result["message"]})
        return result

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
            return self.process_file_paths(list(result))
        except Exception as error:
            self._emit("toast", {"type": "error", "message": f"Falha ao abrir diálogo: {error}"})
            return []

    def choose_output_directory(self) -> str | None:
        """Abre o diálogo nativo para seleção da pasta de saída."""
        if not self._window:
            return None
        import webview

        try:
            result = self._window.create_file_dialog(webview.FOLDER_DIALOG)
            if result and len(result) > 0:
                return str(Path(result[0]).resolve())
            return None
        except Exception as error:
            self._emit("toast", {"type": "error", "message": f"Falha ao selecionar pasta: {error}"})
            return None

    def process_file_paths(self, paths: list[str]) -> list[dict[str, Any]]:
        """Processa uma lista de caminhos (vindos de diálogo ou Drag & Drop)."""
        file_entries: list[dict[str, Any]] = []
        for raw_path in paths:
            try:
                path = Path(raw_path).resolve()
                if not path.is_file() or path.suffix.lower() != ".pdf":
                    continue
                size = path.stat().st_size
                file_entries.append(
                    {
                        "path": str(path),
                        "name": path.name,
                        "size": size,
                        "size_formatted": format_file_size(size),
                    }
                )
            except OSError:
                continue
        return file_entries

    def start_conversion(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Inicia a conversão em lote em uma thread em segundo plano."""
        if self.is_converting:
            return {"started": False, "error": "Uma conversão já está em andamento."}

        files_data = payload.get("files", [])
        if not files_data:
            return {"started": False, "error": "Nenhum PDF selecionado."}

        output_dir_str = payload.get("output_dir", str(DEFAULT_OUTPUT_DIR))
        split_output = bool(payload.get("split_output", False))
        max_chunk_characters = int(payload.get("max_chunk_characters", DEFAULT_MAX_CHUNK_CHARACTERS))
        heading_profile: HeadingProfile = payload.get("heading_profile", "jurisprudencia")

        if max_chunk_characters < MIN_CHUNK_CHARACTERS:
            formatted_limit = f"{MIN_CHUNK_CHARACTERS:,}".replace(",", ".")
            return {
                "started": False,
                "error": f"O limite das partes deve ter pelo menos {formatted_limit} caracteres.",
            }

        output_dir = Path(output_dir_str).expanduser().resolve()
        try:
            output_dir.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            return {"started": False, "error": f"Não foi possível criar a pasta de saída: {error}"}

        file_paths = [Path(item["path"]) for item in files_data if "path" in item]

        self.cancel_requested.clear()
        self.resume_processing.set()
        self.is_paused = False
        self.is_converting = True
        self._batch_start_time = time.perf_counter()

        thread = threading.Thread(
            target=self._convert_in_background,
            args=(file_paths, output_dir, split_output, max_chunk_characters, heading_profile),
            daemon=True,
        )
        thread.start()
        return {"started": True, "error": None}

    def toggle_pause(self) -> dict[str, Any]:
        """Alterna o estado de pausa da fila de conversão."""
        if not self.is_converting:
            return {"is_paused": False}

        if self.is_paused:
            self.resume_processing.set()
            self.is_paused = False
            self._emit("status", {"message": "Conversão retomada."})
            return {"is_paused": False}

        self.resume_processing.clear()
        self.is_paused = True
        self._emit("status", {"message": "Pausa solicitada: será aplicada antes do próximo PDF."})
        return {"is_paused": True}

    def request_stop(self) -> bool:
        """Solicita a interrupção graciosa do lote."""
        if not self.is_converting:
            return False
        self.cancel_requested.set()
        self.resume_processing.set()
        self._emit("status", {"message": "Parada solicitada: concluindo extração ativa..."})
        return True

    def open_markdown(self, file_path: str) -> bool:
        """Abre o arquivo Markdown gerado no editor padrão do Windows."""
        path = Path(file_path).resolve()
        if not path.exists():
            self._emit("toast", {"type": "error", "message": "Arquivo não encontrado."})
            return False
        try:
            os.startfile(path)  # type: ignore[attr-defined]
            return True
        except OSError as error:
            self._emit("toast", {"type": "error", "message": f"Erro ao abrir arquivo: {error}"})
            return False

    def open_folder(self, file_path: str) -> bool:
        """Abre o Windows Explorer selecionando o arquivo ou pasta indicada."""
        path = Path(file_path).resolve()
        if not path.exists():
            self._emit("toast", {"type": "error", "message": "Caminho não encontrado."})
            return False
        try:
            if path.is_file():
                subprocess.run(["explorer", f"/select,{path}"])
            else:
                subprocess.run(["explorer", str(path)])
            return True
        except OSError as error:
            self._emit("toast", {"type": "error", "message": f"Erro ao abrir pasta: {error}"})
            return False

    def read_markdown_preview(self, file_path: str) -> dict[str, Any]:
        """Lê o conteúdo do Markdown gerado para visualização em tempo real."""
        path = Path(file_path).resolve()
        if not path.is_file():
            return {"ok": False, "error": "Arquivo não encontrado."}
        try:
            content = path.read_text(encoding="utf-8")
            size = path.stat().st_size
            return {
                "ok": True,
                "name": path.name,
                "path": str(path),
                "content": content,
                "size_formatted": format_file_size(size),
            }
        except Exception as error:
            return {"ok": False, "error": f"Erro ao ler Markdown: {error}"}

    # --------------------------------------------------------------------------
    # Módulos do Super PDF (Leitor e Editor Integrado)
    # --------------------------------------------------------------------------
    def set_pdf_password(self, file_path: str, password: str) -> dict[str, Any]:
        """Tenta autenticar e memorizar a senha de um PDF protegido."""
        doc, error, needs_password = self._open_doc_with_auth(file_path, password)
        if error or needs_password or doc is None:
            return {"ok": False, "error": error or "Senha incorreta."}
        doc.close()
        return {"ok": True, "message": "Senha autenticada com sucesso."}

    def get_pdf_info(self, file_path: str, password: str | None = None) -> dict[str, Any]:
        """Retorna metadados e lista de páginas do PDF para o Leitor."""
        doc, error, needs_password = self._open_doc_with_auth(file_path, password)
        if error or needs_password or doc is None:
            return {"ok": False, "error": error or "PDF protegido.", "needs_password": needs_password, "is_encrypted": True}

        try:
            path = Path(file_path).resolve()
            pages = [
                {
                    "page_number": idx,
                    "width": p.rect.width,
                    "height": p.rect.height,
                    "rotation": p.rotation,
                }
                for idx, p in enumerate(doc)
            ]
            page_count = len(doc)
            is_encrypted = doc.is_encrypted
            metadata = doc.metadata or {}

            session = self._library.get_session_state(str(path))
            bookmarks = self._library.get_bookmarks(str(path))

            info = {
                "ok": True,
                "file_name": path.name,
                "file_path": str(path),
                "page_count": page_count,
                "is_encrypted": is_encrypted,
                "pages": pages,
                "metadata": metadata,
                "session_state": session,
                "bookmarks": bookmarks,
            }

            # Executa indexação do PDF no banco em background de forma assíncrona
            threading.Thread(
                target=self._safe_index_pdf,
                args=(str(path), password),
                daemon=True,
            ).start()

            return info
        except Exception as error:
            return {"ok": False, "error": f"Erro ao inspecionar PDF: {error}"}
        finally:
            _safe_close(doc)

    def render_page_hq(self, file_path: str, page_number: int = 0, dpi: int = 150, password: str | None = None) -> dict[str, Any]:
        """Renderiza uma página do PDF sob demanda em alta resolução em base64."""
        doc, error, needs_password = self._open_doc_with_auth(file_path, password)
        if error or needs_password or doc is None:
            return {"ok": False, "error": error or "PDF protegido.", "needs_password": needs_password}

        try:
            path = Path(file_path).resolve()
            if not (0 <= page_number < len(doc)):
                return {"ok": False, "error": f"Página {page_number} fora do intervalo."}

            page = doc[page_number]
            pix = page.get_pixmap(dpi=dpi)
            img_b64 = base64.b64encode(pix.tobytes("png")).decode("utf-8")
            data_uri = f"data:image/png;base64,{img_b64}"

            return {
                "ok": True,
                "image": data_uri,
                "image_base64": data_uri,  # Dupla chave para compatibilidade com o frontend
                "width": page.rect.width,
                "height": page.rect.height,
                "pixel_width": pix.width,
                "pixel_height": pix.height,
                "page_count": len(doc),
                "page_number": page_number,
                "rotation": page.rotation,
                "file_name": path.name,
                "file_path": str(path),
            }
        except Exception as error:
            return {"ok": False, "error": f"Erro ao renderizar: {error}"}
        finally:
            _safe_close(doc)

    def rotate_pdf_page(self, file_path: str, page_number: int, degrees: int, password: str | None = None) -> dict[str, Any]:
        """Gira uma página específica em incrementos de 90 graus e salva o PDF."""
        doc, error, needs_password = self._open_doc_with_auth(file_path, password)
        if error or needs_password or doc is None:
            return {"ok": False, "error": error or "PDF protegido.", "needs_password": needs_password}

        try:
            path = Path(file_path).resolve()
            if not (0 <= page_number < len(doc)):
                return {"ok": False, "error": "Página inexistente."}

            page = doc[page_number]
            new_rotation = (page.rotation + degrees) % 360
            page.set_rotation(new_rotation)

            _save_doc_safely(doc, path)
            return {
                "ok": True,
                "new_rotation": new_rotation,
                "message": f"Página {page_number + 1} rotacionada para {new_rotation}°.",
            }
        except Exception as error:
            return {"ok": False, "error": f"Erro ao rotacionar página: {error}"}
        finally:
            _safe_close(doc)

    def protect_pdf(self, file_path: str, user_pw: str, owner_pw: str = "") -> dict[str, Any]:
        """Aplica criptografia AES-256 no arquivo PDF com senhas de proteção."""
        path = Path(file_path).resolve()
        if not path.is_file():
            return {"ok": False, "error": "Arquivo não encontrado."}
        if not user_pw:
            return {"ok": False, "error": "A senha do usuário não pode ser vazia."}

        doc, error, needs_password = self._open_doc_with_auth(file_path)
        if error or doc is None:
            return {"ok": False, "error": error or "Não foi possível abrir o PDF."}

        try:
            owner = owner_pw if owner_pw else user_pw
            perm = fitz.PDF_PERM_PRINT | fitz.PDF_PERM_COPY | fitz.PDF_PERM_ANNOTATE | fitz.PDF_PERM_ACCESSIBILITY
            _save_doc_safely(
                doc,
                path,
                encryption=fitz.PDF_ENCRYPT_AES_256,
                user_pw=user_pw,
                owner_pw=owner,
                permissions=perm,
            )
            self._pdf_passwords[str(path)] = user_pw
            return {
                "ok": True,
                "message": "PDF protegido com sucesso usando criptografia AES-256.",
            }
        except Exception as error:
            return {"ok": False, "error": f"Erro ao proteger PDF: {error}"}
        finally:
            _safe_close(doc)

    def unprotect_pdf(self, file_path: str, current_pw: str = "") -> dict[str, Any]:
        """Remove a proteção por senha de um arquivo PDF, gravando-o descriptografado."""
        path = Path(file_path).resolve()
        if not path.is_file():
            return {"ok": False, "error": "Arquivo não encontrado."}

        doc, error, needs_password = self._open_doc_with_auth(file_path, current_pw)
        if error or doc is None:
            return {"ok": False, "error": error or "Não foi possível abrir o PDF com a senha informada."}

        try:
            _save_doc_safely(
                doc,
                path,
                encryption=fitz.PDF_ENCRYPT_NONE,
            )
            self._pdf_passwords.pop(str(path), None)

            return {
                "ok": True,
                "message": "Proteção por senha removida com sucesso.",
            }
        except Exception as error:
            return {"ok": False, "error": f"Erro ao remover senha do PDF: {error}"}
        finally:
            _safe_close(doc)

    def save_pdf_annotations(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Grava anotações nativas e caixas de texto estruturadas no arquivo PDF."""
        file_path = payload.get("file_path", "")
        password = payload.get("password")
        annotations = payload.get("annotations", [])

        doc, error, needs_password = self._open_doc_with_auth(file_path, password)
        if error or needs_password or doc is None:
            return {"ok": False, "error": error or "PDF protegido por senha.", "needs_password": needs_password}

        path = Path(file_path).resolve()
        try:
            applied_count = 0

            for item in annotations:
                page_num = int(item.get("page_number", 0))
                if not (0 <= page_num < len(doc)):
                    continue

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

                        page.insert_textbox(
                            text_rect,
                            text,
                            fontname=fontname,
                            fontsize=fontsize,
                            color=text_color,
                            render_mode=0,
                            align=0,
                        )
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

                        page.draw_rect(card_rect, color=None, fill=bg_color, width=0)
                        page.draw_line(
                            fitz.Point(pt_x, pt_y),
                            fitz.Point(pt_x, pt_y + card_h),
                            color=left_border_color,
                            width=3.5,
                        )

                        text_rect = fitz.Rect(
                            pt_x + padding_x + 2, pt_y + padding_y, pt_x + card_w - padding_x, pt_y + card_h - padding_y
                        )
                        page.insert_textbox(
                            text_rect,
                            text,
                            fontname=fontname,
                            fontsize=fontsize,
                            color=text_color,
                            render_mode=0,
                            align=0,
                        )
                        applied_count += 1

            _save_doc_safely(doc, path)
            return {
                "ok": True,
                "saved_count": applied_count,
                "message": f"{applied_count} anotação(ões) salva(s) com sucesso no PDF.",
            }
        except Exception as error:
            return {"ok": False, "error": f"Erro ao salvar anotações: {error}"}
        finally:
            _safe_close(doc)

    def extract_snippet(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Extrai texto e imagem recortada em alta resolução de uma região retangular do PDF."""
        file_path = payload.get("file_path", "")
        password = payload.get("password")
        page_number = int(payload.get("page_number", 0))
        rect_coords = payload.get("rect", [0, 0, 100, 100])
        dpi = int(payload.get("dpi", 150))

        doc, error, needs_password = self._open_doc_with_auth(file_path, password)
        if error or needs_password or doc is None:
            return {"ok": False, "error": error or "PDF protegido por senha.", "needs_password": needs_password}

        try:
            if not (0 <= page_number < len(doc)):
                return {"ok": False, "error": f"Página {page_number} fora do intervalo (total: {len(doc)})."}

            page = doc[page_number]
            x0, y0, x1, y1 = rect_coords[0], rect_coords[1], rect_coords[2], rect_coords[3]
            clip_rect = fitz.Rect(min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))

            extracted_text = page.get_text("text", clip=clip_rect).strip()
            pix = page.get_pixmap(clip=clip_rect, dpi=dpi)
            img_bytes = pix.tobytes("png")
            img_b64 = base64.b64encode(img_bytes).decode("utf-8")

            ocr_applied = False
            if len(extracted_text) < 4:
                try:
                    from ocr_engine import ocr_pixmap

                    ocr_text, _ = ocr_pixmap(pix)
                    if ocr_text.strip():
                        extracted_text = ocr_text.strip()
                        ocr_applied = True
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

    # --------------------------------------------------------------------------
    # Módulos de Síntese de Voz Neural (TTS) & Tradução Multilíngue Local
    # --------------------------------------------------------------------------
    def get_available_voices(self) -> dict[str, Any]:
        """Retorna as vozes neurais suportadas para leitura com alta fidelidade."""
        voices = [
            {"id": "pt-BR-FranciscaNeural", "name": "Francisca (Português - Brasil)", "gender": "Feminina", "lang": "pt-BR"},
            {"id": "pt-BR-AntonioNeural", "name": "Antônio (Português - Brasil)", "gender": "Masculina", "lang": "pt-BR"},
            {"id": "pt-BR-ThalitaNeural", "name": "Thalita (Português - Brasil)", "gender": "Feminina", "lang": "pt-BR"},
            {"id": "en-US-JennyNeural", "name": "Jenny (Inglês - EUA)", "gender": "Feminina", "lang": "en-US"},
            {"id": "en-US-GuyNeural", "name": "Guy (Inglês - EUA)", "gender": "Masculino", "lang": "en-US"},
            {"id": "es-ES-ElviraNeural", "name": "Elvira (Espanhol - Espanha)", "gender": "Feminina", "lang": "es-ES"},
            {"id": "es-ES-AlvaroNeural", "name": "Álvaro (Espanhol - Espanha)", "gender": "Masculino", "lang": "es-ES"},
            {"id": "fr-FR-DeniseNeural", "name": "Denise (Francês - França)", "gender": "Feminina", "lang": "fr-FR"},
            {"id": "it-IT-ElsaNeural", "name": "Elsa (Italiano - Itália)", "gender": "Feminina", "lang": "it-IT"},
            {"id": "de-DE-KatjaNeural", "name": "Katja (Alemão - Alemanha)", "gender": "Feminina", "lang": "de-DE"},
        ]
        return {"ok": True, "voices": voices}

    def synthesize_speech(
        self,
        text: str,
        voice: str = "pt-BR-FranciscaNeural",
        rate: str = "+0%",
        pitch: str = "+0Hz",
    ) -> dict[str, Any]:
        cleaned_text = (text or "").strip()
        if not cleaned_text:
            return {"ok": False, "error": "Nenhum texto informado para síntese de voz."}

        if len(cleaned_text) > 10_000:
            cleaned_text = cleaned_text[:10_000]

        async def _run_tts() -> bytes:
            communicate = edge_tts.Communicate(cleaned_text, voice, rate=rate, pitch=pitch)
            chunks: list[bytes] = []
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    chunks.append(chunk["data"])
            return b"".join(chunks)

        try:
            # Runner seguro para evitar conflitos de event loop em background threads
            audio_data = None
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None

            if loop and loop.is_running():
                with ThreadPoolExecutor(max_workers=1) as pool:
                    audio_data = pool.submit(asyncio.run, _run_tts()).result()
            else:
                audio_data = asyncio.run(_run_tts())

            if not audio_data:
                return {"ok": False, "error": "Nenhum dado de áudio foi gerado."}

            b64_audio = base64.b64encode(audio_data).decode("utf-8")
            return {
                "ok": True,
                "audio_base64": f"data:audio/mp3;base64,{b64_audio}",
                "voice": voice,
                "text_length": len(cleaned_text),
            }
        except Exception as error:
            logger.error(f"Erro no Edge-TTS: {error}", exc_info=True)
            return {"ok": False, "error": f"Falha na síntese de voz: {error}"}

    def translate_text(self, text: str, target_lang: str = "pt", source_lang: str = "auto") -> dict[str, Any]:
        """Traduz trecho de texto usando deep-translator sem dependência de API paga."""
        cleaned_text = (text or "").strip()
        if not cleaned_text:
            return {"ok": False, "error": "Nenhum texto informado para tradução."}

        try:
            translator = GoogleTranslator(source=source_lang, target=target_lang)
            translated = translator.translate(cleaned_text)
            return {
                "ok": True,
                "original_text": cleaned_text,
                "translated_text": translated or "",
                "source_lang": source_lang,
                "target_lang": target_lang,
            }
        except Exception as error:
            if source_lang != "auto":
                try:
                    fallback_trans = GoogleTranslator(source="auto", target=target_lang)
                    translated = fallback_trans.translate(cleaned_text)
                    if translated:
                        return {
                            "ok": True,
                            "original_text": cleaned_text,
                            "translated_text": translated,
                            "source_lang": "auto",
                            "target_lang": target_lang,
                        }
                except Exception:
                    pass

            logger.error(f"Erro na tradução: {error}", exc_info=True)
            return {"ok": False, "error": f"Falha ao traduzir trecho: {error}"}

    # --------------------------------------------------------------------------
    # Módulos de Acervo Pessoal & Busca Textual Instantânea (SQLite FTS5)
    # --------------------------------------------------------------------------
    def search_library(self, query: str) -> dict[str, Any]:
        """Executa busca textual instantânea em toda a biblioteca via FTS5."""
        try:
            results = self._library.search(query, limit=50)
            return {"ok": True, "query": query, "results": results, "total": len(results)}
        except Exception as error:
            logger.error(f"Erro na busca do acervo: {error}", exc_info=True)
            return {"ok": False, "error": f"Falha na busca: {error}", "results": []}

    def get_recent_library(self) -> dict[str, Any]:
        """Retorna os documentos recentes do acervo."""
        try:
            docs = self._library.get_recent_documents(limit=30)
            return {"ok": True, "documents": docs}
        except Exception as error:
            return {"ok": False, "error": str(error), "documents": []}

    def save_reading_state(self, file_path: str, last_page: int, zoom: str = "1.0") -> dict[str, Any]:
        """Salva a última página lida e o zoom preferido no documento."""
        try:
            self._library.save_session_state(file_path, last_page, zoom)
            return {"ok": True}
        except Exception as error:
            return {"ok": False, "error": str(error)}

    def get_reading_state(self, file_path: str) -> dict[str, Any]:
        """Recupera o histórico de leitura do documento."""
        try:
            state = self._library.get_session_state(file_path)
            return {"ok": True, "state": state}
        except Exception as error:
            return {"ok": False, "error": str(error)}

    def add_bookmark(self, file_path: str, page_number: int, title: str = "") -> dict[str, Any]:
        """Adiciona um marcador de página no documento."""
        try:
            res = self._library.add_bookmark(file_path, page_number, title)
            return res
        except Exception as error:
            return {"ok": False, "error": str(error)}

    def get_bookmarks(self, file_path: str) -> dict[str, Any]:
        """Retorna todos os marcadores de um documento."""
        try:
            bookmarks = self._library.get_bookmarks(file_path)
            return {"ok": True, "bookmarks": bookmarks}
        except Exception as error:
            return {"ok": False, "error": str(error), "bookmarks": []}

    def delete_bookmark(self, bookmark_id: int) -> dict[str, Any]:
        """Exclui um marcador de página."""
        try:
            success = self._library.delete_bookmark(bookmark_id)
            return {"ok": success}
        except Exception as error:
            return {"ok": False, "error": str(error)}

    def _safe_index_pdf(self, file_path: str, password: str | None = None) -> None:
        """Executa indexação segura do PDF no banco em background."""
        doc: fitz.Document | None = None
        try:
            doc, error, needs_pw = self._open_doc_with_auth(file_path, password)
            if doc and not error and not needs_pw:
                self._library.index_pdf_document(file_path, doc)
        except Exception as err:
            logger.debug(f"Falha na auto-indexação do PDF {file_path}: {err}")
        finally:
            _safe_close(doc)

    def _convert_in_background(
        self,
        files: list[Path],
        output_dir: Path,
        split_output: bool,
        max_chunk_characters: int,
        heading_profile: HeadingProfile,
        max_workers: int | None = None,
    ) -> None:
        try:
            worker_count = self._resolve_worker_count(len(files)) if max_workers is None else max(1, max_workers)
            if worker_count <= 1:
                self._convert_sequentially(files, output_dir, split_output, max_chunk_characters, heading_profile)
            else:
                self._convert_in_parallel(files, output_dir, split_output, max_chunk_characters, heading_profile, worker_count)
        except Exception as error:
            self._emit(
                "batch_error",
                {"error_message": str(error), "details": traceback.format_exc()},
            )
        finally:
            self.is_converting = False
            self.is_paused = False

    def _resolve_worker_count(self, total_files: int) -> int:
        if total_files <= 1:
            return 1
        return max(1, min(total_files, MAX_PARALLEL_WORKERS, os.cpu_count() or 1))

    def _convert_sequentially(
        self,
        files: list[Path],
        output_dir: Path,
        split_output: bool,
        max_chunk_characters: int,
        heading_profile: HeadingProfile,
    ) -> None:
        converter = PdfMarkdownConverter()
        successes: list[ConversionResult] = []
        failures: list[ConversionFailure] = []
        total = len(files)
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
                {"path": str(source), "name": source.name, "index": index, "total": total},
            )
            self._emit("status", {"message": f"Convertendo {index}/{total}: {source.name}"})

            try:
                result = converter.convert(source, output_dir, split_output, max_chunk_characters, heading_profile)
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
    ) -> None:
        total = len(files)
        self._emit_progress(0, total)
        successes: list[ConversionResult] = []
        failures: list[ConversionFailure] = []
        pending: dict[Future, Path] = {}
        next_index = 0

        with ProcessPoolExecutor(max_workers=worker_count, initializer=init_worker) as pool:
            while pending or (next_index < total and not self.cancel_requested.is_set()):
                if not self.cancel_requested.is_set() and self.resume_processing.is_set():
                    while len(pending) < worker_count and next_index < total:
                        source = files[next_index]
                        next_index += 1
                        self._emit(
                            "file_start",
                            {"path": str(source), "name": source.name, "index": next_index, "total": total},
                        )
                        self._emit("status", {"message": f"Convertendo {next_index}/{total}: {source.name}"})
                        try:
                            future = pool.submit(
                                convert_worker,
                                source,
                                output_dir,
                                split_output,
                                max_chunk_characters,
                                heading_profile,
                            )
                        except BrokenProcessPool as error:
                            failure = ConversionFailure(source=source, error_message=str(error), details=traceback.format_exc())
                            failures.append(failure)
                            self._emit_file_error(failure)
                            self._emit_progress(len(successes) + len(failures), total)
                            continue
                        pending[future] = source

                if not pending:
                    self.resume_processing.wait(timeout=0.2)
                    continue

                done, _ = wait(pending.keys(), timeout=0.2, return_when=FIRST_COMPLETED)
                for future in done:
                    source = pending.pop(future)
                    try:
                        result = future.result()
                    except Exception as error:
                        result = ConversionFailure(source=source, error_message=str(error), details=traceback.format_exc())

                    if isinstance(result, ConversionFailure):
                        failures.append(result)
                        self._emit_file_error(result)
                    else:
                        successes.append(result)
                        self._emit_file_success(result)

                    self._emit_progress(len(successes) + len(failures), total)

                if self.cancel_requested.is_set() and not pending:
                    break

        if self.cancel_requested.is_set():
            self._emit_batch_stopped(successes, failures, total, output_dir)
        else:
            self._emit_batch_done(successes, failures, total, output_dir)

    def _emit_file_success(self, result: ConversionResult) -> None:
        try:
            if result.markdown_path.is_file():
                content = result.markdown_path.read_text(encoding="utf-8")
                self._library.index_markdown_file(result.source, result.markdown_path, content)
        except Exception as err:
            logger.debug(f"Falha ao indexar markdown {result.markdown_path}: {err}")

        self._emit(
            "file_success",
            {
                "source": str(result.source),
                "name": result.source.name,
                "markdown_path": str(result.markdown_path),
                "asset_count": result.asset_count,
                "chunk_count": result.chunk_count,
                "duration_formatted": format_duration(result.extraction_seconds),
                "extraction_seconds": result.extraction_seconds,
            },
        )

    def _emit_file_error(self, failure: ConversionFailure) -> None:
        self._emit(
            "file_error",
            {
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
        self._emit(
            "batch_done",
            {
                "success_count": len(successes),
                "failure_count": len(failures),
                "total": total,
                "elapsed_seconds": elapsed_seconds,
                "elapsed_formatted": format_duration(elapsed_seconds),
                "output_dir": str(output_dir),
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
