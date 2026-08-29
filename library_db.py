"""Gerenciador do Acervo Pessoal e Motor de Busca Textual Instantânea (SQLite FTS5).

Executa indexação e busca textual local ultrarrápida usando tabelas virtuais FTS5 com
ranqueamento estatístico BM25 e geração de snippets realçados.
"""

from __future__ import annotations

import logging
import re
import sqlite3
import threading
import time
import uuid
from collections.abc import Generator
from contextlib import contextmanager
from html import escape
from pathlib import Path
from typing import Any

import fitz

from constants import application_root

logger = logging.getLogger(__name__)

_DEFAULT_DB_PATH = application_root() / "data" / "nexojuris_acervo.db"
_DB_LOCK = threading.Lock()


class LibraryDatabase:
    """Repositório de persistência do acervo e índice de pesquisa FTS5."""

    def __init__(self, db_path: Path | str | None = None) -> None:
        self.db_path = Path(db_path).resolve() if db_path else _DEFAULT_DB_PATH.resolve()
        self._init_db()

    @contextmanager
    def _connection(self) -> Generator[sqlite3.Connection]:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA synchronous=NORMAL;")
            conn.execute("PRAGMA busy_timeout=30000;")
            with conn:
                yield conn
        finally:
            conn.close()

    def _execute_write(self, operation: Any, max_retries: int = 5) -> Any:
        """Executa uma operação de escrita com retry exponencial em caso de concorrência ou database locked."""
        delay = 0.05
        last_error: Exception | None = None
        for attempt in range(max_retries):
            try:
                with _DB_LOCK, self._connection() as conn:
                    return operation(conn)
            except sqlite3.OperationalError as error:
                err_msg = str(error).lower()
                if ("locked" in err_msg or "busy" in err_msg) and attempt < max_retries - 1:
                    logger.debug(
                        f"SQLite concorrência detectada (tentativa {attempt + 1}/{max_retries}): {error}. "
                        f"Aguardando {delay:.2f}s..."
                    )
                    last_error = error
                    time.sleep(delay)
                    delay *= 2
                    continue
                raise
        if last_error:
            raise last_error

    def _init_db(self) -> None:
        def _do_init(conn: sqlite3.Connection) -> None:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS documents (
                    file_path TEXT PRIMARY KEY,
                    file_name TEXT NOT NULL,
                    file_size INTEGER DEFAULT 0,
                    page_count INTEGER DEFAULT 0,
                    last_page_read INTEGER DEFAULT 0,
                    preferred_zoom TEXT DEFAULT '1.0',
                    markdown_path TEXT DEFAULT '',
                    is_converted INTEGER DEFAULT 0,
                    last_accessed REAL DEFAULT 0,
                    created_at REAL DEFAULT 0,
                    availability_status TEXT DEFAULT 'available',
                    status_updated_at REAL DEFAULT 0
                )
            """)

            # Migração graciosa para bancos legados sem colunas de disponibilidade
            cols = {row["name"] for row in conn.execute("PRAGMA table_info(documents)").fetchall()}
            if "availability_status" not in cols:
                conn.execute("ALTER TABLE documents ADD COLUMN availability_status TEXT DEFAULT 'available'")
            if "status_updated_at" not in cols:
                conn.execute("ALTER TABLE documents ADD COLUMN status_updated_at REAL DEFAULT 0")

            conn.execute("""
                CREATE TABLE IF NOT EXISTS bookmarks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    file_path TEXT NOT NULL,
                    page_number INTEGER NOT NULL,
                    title TEXT DEFAULT '',
                    created_at REAL DEFAULT 0,
                    FOREIGN KEY(file_path) REFERENCES documents(file_path) ON DELETE CASCADE
                )
            """)

            # Tabela virtual FTS5 para busca textual ultra-otimizada
            conn.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS doc_fts USING fts5(
                    file_path UNINDEXED,
                    file_name,
                    page_number UNINDEXED,
                    content_type UNINDEXED,
                    content,
                    title,
                    tokenize='unicode61'
                )
            """)

            # Tabela de controle de aceite de termos de uso e privacidade
            conn.execute("""
                CREATE TABLE IF NOT EXISTS app_agreements (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    accepted INTEGER NOT NULL DEFAULT 0,
                    accepted_at REAL NOT NULL,
                    terms_version TEXT NOT NULL
                )
            """)

        self._execute_write(_do_init)

    def index_document_metadata_only(self, file_path: str | Path, page_count: int, file_size: int = 0) -> None:
        """Indexa metadados básicos do documento em tempo O(1) sem percorrer todas as páginas."""
        path_str = str(Path(file_path).resolve())
        file_name = Path(file_path).name
        now = time.time()
        if file_size <= 0:
            try:
                file_size = Path(file_path).stat().st_size
            except OSError:
                file_size = 0

        def _do_index_meta(conn: sqlite3.Connection) -> None:
            conn.execute(
                """
                INSERT INTO documents (file_path, file_name, file_size, page_count, last_accessed, created_at, availability_status, status_updated_at)
                VALUES (?, ?, ?, ?, ?, ?, 'available', ?)
                ON CONFLICT(file_path) DO UPDATE SET
                    file_name = excluded.file_name,
                    file_size = CASE WHEN excluded.file_size > 0 THEN excluded.file_size ELSE documents.file_size END,
                    page_count = excluded.page_count,
                    last_accessed = excluded.last_accessed,
                    availability_status = 'available',
                    status_updated_at = excluded.status_updated_at
                """,
                (path_str, file_name, file_size, page_count, now, now, now),
            )

        self._execute_write(_do_index_meta)

    def index_single_pdf_page(self, file_path: str | Path, page_number: int, text: str) -> None:
        """Indexa incrementalmente o texto de uma página específica visualizada no leitor."""
        path_str = str(Path(file_path).resolve())
        file_name = Path(file_path).name
        cleaned = text.strip()
        if not cleaned:
            cleaned = f"[Página {page_number + 1} - Imagem digitalizada]"
        page_title = f"Página {page_number + 1}"

        def _do_index_page(conn: sqlite3.Connection) -> None:
            conn.execute(
                "DELETE FROM doc_fts WHERE file_path = ? AND page_number = ? AND content_type = 'pdf_page'",
                (path_str, str(page_number)),
            )
            conn.execute(
                """
                INSERT INTO doc_fts (file_path, file_name, page_number, content_type, content, title)
                VALUES (?, ?, ?, 'pdf_page', ?, ?)
                """,
                (path_str, file_name, str(page_number), cleaned, page_title),
            )

        self._execute_write(_do_index_page)

    def index_pdf_document(self, file_path: str | Path, doc: fitz.Document) -> None:
        """Indexa todas as páginas do PDF no banco de dados e no índice FTS5."""
        path_str = str(Path(file_path).resolve())
        file_name = Path(file_path).name
        file_size = 0
        try:
            file_size = Path(file_path).stat().st_size
        except OSError:
            pass

        now = time.time()
        page_count = len(doc)

        def _do_index(conn: sqlite3.Connection) -> None:
            # 1. Atualiza metadados do documento (UPSERT preservando sessão existente)
            conn.execute(
                """
                INSERT INTO documents (file_path, file_name, file_size, page_count, last_accessed, created_at, availability_status, status_updated_at)
                VALUES (?, ?, ?, ?, ?, ?, 'available', ?)
                ON CONFLICT(file_path) DO UPDATE SET
                    file_name = excluded.file_name,
                    file_size = excluded.file_size,
                    page_count = excluded.page_count,
                    last_accessed = excluded.last_accessed,
                    availability_status = 'available',
                    status_updated_at = excluded.status_updated_at
                """,
                (path_str, file_name, file_size, page_count, now, now, now),
            )

            # 2. Limpa índice anterior de páginas deste PDF
            conn.execute("DELETE FROM doc_fts WHERE file_path = ? AND content_type = 'pdf_page'", (path_str,))

            # 3. Insere o conteúdo textual de cada página
            for idx, page in enumerate(doc):
                text = page.get_text("text").strip()
                if not text:
                    text = f"[Página {idx + 1} - Imagem digitalizada]"

                page_title = f"Página {idx + 1}"
                conn.execute(
                    """
                    INSERT INTO doc_fts (file_path, file_name, page_number, content_type, content, title)
                    VALUES (?, ?, ?, 'pdf_page', ?, ?)
                    """,
                    (path_str, file_name, str(idx), text, page_title),
                )

        self._execute_write(_do_index)

    def index_markdown_file(self, file_path: str | Path, markdown_path: str | Path, content: str) -> None:
        """Indexa o conteúdo do arquivo Markdown gerado no índice FTS5."""
        path_str = str(Path(file_path).resolve())
        md_path_str = str(Path(markdown_path).resolve())
        file_name = Path(file_path).name
        now = time.time()

        def _do_index_md(conn: sqlite3.Connection) -> None:
            conn.execute(
                """
                INSERT INTO documents (file_path, file_name, markdown_path, is_converted, last_accessed, created_at, availability_status, status_updated_at)
                VALUES (?, ?, ?, 1, ?, ?, 'available', ?)
                ON CONFLICT(file_path) DO UPDATE SET
                    markdown_path = excluded.markdown_path,
                    is_converted = 1,
                    last_accessed = excluded.last_accessed,
                    availability_status = 'available',
                    status_updated_at = excluded.status_updated_at
                """,
                (path_str, file_name, md_path_str, now, now, now),
            )

            conn.execute("DELETE FROM doc_fts WHERE file_path = ? AND content_type = 'markdown'", (path_str,))

            conn.execute(
                """
                INSERT INTO doc_fts (file_path, file_name, page_number, content_type, content, title)
                VALUES (?, ?, '0', 'markdown', ?, ?)
                """,
                (path_str, file_name, content, f"Markdown: {file_name}"),
            )

        self._execute_write(_do_index_md)

    def check_and_update_document_availability(self, file_path: str) -> str:
        """Verifica a existência física do arquivo e atualiza o status de disponibilidade sem apagar dados."""
        path_str = str(Path(file_path).resolve())
        exists = Path(path_str).is_file()
        now = time.time()

        def _do_check(conn: sqlite3.Connection) -> str:
            cursor = conn.execute("SELECT availability_status FROM documents WHERE file_path = ?", (path_str,))
            row = cursor.fetchone()
            current_status = row["availability_status"] if row and "availability_status" in row.keys() else "available"

            if exists:
                new_status = "available"
            else:
                if current_status in {"user_removed", "moved"}:
                    new_status = current_status
                else:
                    new_status = "temporarily_unavailable"

            if row is not None and new_status != current_status:
                conn.execute(
                    "UPDATE documents SET availability_status = ?, status_updated_at = ? WHERE file_path = ?",
                    (new_status, now, path_str),
                )
            return new_status

        return self._execute_write(_do_check)

    def relocate_document(self, old_file_path: str, new_file_path: str) -> bool:
        """Atualiza o caminho do arquivo no acervo mantendo marcadores e histórico intactos."""
        old_str = str(Path(old_file_path).resolve())
        new_str = str(Path(new_file_path).resolve())
        new_name = Path(new_file_path).name
        now = time.time()

        if not Path(new_str).is_file():
            return False

        def _do_relocate(conn: sqlite3.Connection) -> bool:
            conn.execute(
                """
                UPDATE documents
                SET file_path = ?, file_name = ?, availability_status = 'available', status_updated_at = ?, last_accessed = ?
                WHERE file_path = ?
                """,
                (new_str, new_name, now, now, old_str),
            )
            conn.execute("UPDATE bookmarks SET file_path = ? WHERE file_path = ?", (new_str, old_str))
            conn.execute("UPDATE doc_fts SET file_path = ?, file_name = ? WHERE file_path = ?", (new_str, new_name, old_str))
            return True

        return bool(self._execute_write(_do_relocate))

    def remove_document_from_library(self, file_path: str) -> bool:
        """Marca o documento como 'user_removed' no acervo sem excluir histórico ou marcadores."""
        path_str = str(Path(file_path).resolve())
        now = time.time()

        def _do_remove(conn: sqlite3.Connection) -> bool:
            cursor = conn.execute(
                "UPDATE documents SET availability_status = 'user_removed', status_updated_at = ? WHERE file_path = ?",
                (now, path_str),
            )
            return cursor.rowcount > 0

        return bool(self._execute_write(_do_remove))

    def search(self, query: str, limit: int = 40) -> list[dict[str, Any]]:
        """Executa busca textual rápida no índice FTS5 com snippets e ranqueamento BM25."""
        cleaned_query = (query or "").strip()
        if not cleaned_query:
            return []

        # Sanitiza a query para a sintaxe do FTS5
        words = re.findall(r"\w+", cleaned_query)
        if not words:
            return []

        # Formata como prefix query para busca incremental ("termo*")
        fts_query = " ".join(f'"{w}"*' for w in words)

        marker_id = uuid.uuid4().hex
        snip_open_marker = f"NXJHIGHLIGHTOPEN{marker_id}"
        snip_close_marker = f"NXJHIGHLIGHTCLOSE{marker_id}"
        snip_open_html = '<mark class="bg-amber-200 text-amber-900 font-bold px-0.5 rounded">'
        sql = f"""
            SELECT
                doc_fts.file_path,
                doc_fts.file_name,
                doc_fts.page_number,
                doc_fts.content_type,
                doc_fts.title,
                d.markdown_path,
                d.availability_status,
                snippet(doc_fts, 4, '{snip_open_marker}', '{snip_close_marker}', '...', 22) AS match_snippet,
                bm25(doc_fts) AS rank
            FROM doc_fts
            LEFT JOIN documents AS d ON d.file_path = doc_fts.file_path
            WHERE doc_fts MATCH ? AND (d.availability_status IS NULL OR d.availability_status != 'user_removed')
            ORDER BY rank
            LIMIT ?
        """

        with _DB_LOCK, self._connection() as conn:
            try:
                cursor = conn.execute(sql, (fts_query, limit))
                rows = cursor.fetchall()
            except sqlite3.OperationalError:
                # Fallback caso a sintaxe FTS encontre caractere especial não tratado
                safe_query = f'"{cleaned_query}"'
                cursor = conn.execute(sql, (safe_query, limit))
                rows = cursor.fetchall()

            results = []
            for row in rows:
                safe_snippet = escape(str(row["match_snippet"] or ""), quote=True)
                safe_snippet = safe_snippet.replace(snip_open_marker, snip_open_html).replace(snip_close_marker, "</mark>")
                raw_path = row["file_path"]
                exists = Path(raw_path).is_file()
                status = row["availability_status"] or ("available" if exists else "temporarily_unavailable")
                if not exists and status == "available":
                    status = "temporarily_unavailable"

                results.append(
                    {
                        "file_path": raw_path,
                        "file_name": row["file_name"],
                        "page_number": int(row["page_number"]),
                        "content_type": row["content_type"],
                        "markdown_path": row["markdown_path"],
                        "title": row["title"],
                        "snippet": safe_snippet,
                        "rank": float(row["rank"]),
                        "availability_status": status,
                    }
                )
            return results

    def save_session_state(self, file_path: str, last_page: int, zoom: str = "1.0") -> None:
        """Salva a última página lida e o zoom preferido do documento via UPSERT."""
        path_str = str(Path(file_path).resolve())
        file_name = Path(file_path).name
        now = time.time()

        def _do_save_session(conn: sqlite3.Connection) -> None:
            conn.execute(
                """
                INSERT INTO documents (file_path, file_name, last_page_read, preferred_zoom, last_accessed, created_at, availability_status, status_updated_at)
                VALUES (?, ?, ?, ?, ?, ?, 'available', ?)
                ON CONFLICT(file_path) DO UPDATE SET
                    last_page_read = excluded.last_page_read,
                    preferred_zoom = excluded.preferred_zoom,
                    last_accessed = excluded.last_accessed
                """,
                (path_str, file_name, last_page, str(zoom), now, now, now),
            )

        self._execute_write(_do_save_session)

    def get_session_state(self, file_path: str) -> dict[str, Any]:
        """Recupera o histórico e configurações de leitura de um PDF."""
        path_str = str(Path(file_path).resolve())
        with _DB_LOCK, self._connection() as conn:
            cursor = conn.execute(
                "SELECT last_page_read, preferred_zoom, markdown_path, availability_status FROM documents WHERE file_path = ?",
                (path_str,),
            )
            row = cursor.fetchone()
            if row:
                return {
                    "found": True,
                    "last_page_read": row["last_page_read"],
                    "preferred_zoom": row["preferred_zoom"],
                    "markdown_path": row["markdown_path"],
                    "availability_status": row["availability_status"] or "available",
                }
            return {"found": False, "last_page_read": 0, "preferred_zoom": "1.0", "markdown_path": "", "availability_status": "available"}

    def add_bookmark(self, file_path: str, page_number: int, title: str = "") -> dict[str, Any]:
        """Adiciona um marcador de página."""
        path_str = str(Path(file_path).resolve())
        now = time.time()
        bookmark_title = title.strip() or f"Marcador - Pág. {page_number + 1}"

        def _do_add_bm(conn: sqlite3.Connection) -> dict[str, Any]:
            cursor = conn.execute(
                """
                INSERT INTO bookmarks (file_path, page_number, title, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (path_str, page_number, bookmark_title, now),
            )
            return {
                "ok": True,
                "id": cursor.lastrowid,
                "file_path": path_str,
                "page_number": page_number,
                "title": bookmark_title,
            }

        return self._execute_write(_do_add_bm)

    def get_bookmarks(self, file_path: str) -> list[dict[str, Any]]:
        """Retorna os marcadores cadastrados para um PDF."""
        path_str = str(Path(file_path).resolve())
        with _DB_LOCK, self._connection() as conn:
            cursor = conn.execute(
                """
                SELECT id, page_number, title, created_at
                FROM bookmarks
                WHERE file_path = ?
                ORDER BY page_number ASC, created_at DESC
                """,
                (path_str,),
            )
            rows = cursor.fetchall()
            return [
                {
                    "id": row["id"],
                    "page_number": row["page_number"],
                    "title": row["title"],
                    "created_at": row["created_at"],
                }
                for row in rows
            ]

    def delete_bookmark(self, bookmark_id: int, file_path: str | None = None) -> bool:
        """Remove um marcador de página."""

        path_str = str(Path(file_path).resolve()) if file_path else None

        def _do_del_bm(conn: sqlite3.Connection) -> bool:
            if path_str is None:
                cursor = conn.execute("DELETE FROM bookmarks WHERE id = ?", (bookmark_id,))
            else:
                cursor = conn.execute(
                    "DELETE FROM bookmarks WHERE id = ? AND file_path = ?",
                    (bookmark_id, path_str),
                )
            return cursor.rowcount > 0

        return bool(self._execute_write(_do_del_bm))

    def get_recent_documents(self, limit: int = 20) -> list[dict[str, Any]]:
        """Retorna os documentos acessados recentemente no acervo com status de disponibilidade."""
        with _DB_LOCK, self._connection() as conn:
            cursor = conn.execute(
                """
                SELECT file_path, file_name, file_size, page_count, last_page_read,
                       preferred_zoom, markdown_path, is_converted, last_accessed, availability_status
                FROM documents
                WHERE availability_status IS NULL OR availability_status != 'user_removed'
                ORDER BY last_accessed DESC
                LIMIT ?
                """,
                (limit,),
            )
            rows = cursor.fetchall()
            docs = []
            for row in rows:
                raw_path = row["file_path"]
                exists = Path(raw_path).is_file()
                status = row["availability_status"] or ("available" if exists else "temporarily_unavailable")
                if not exists and status == "available":
                    status = "temporarily_unavailable"

                docs.append(
                    {
                        "file_path": raw_path,
                        "file_name": row["file_name"],
                        "file_size": row["file_size"],
                        "page_count": row["page_count"],
                        "last_page_read": row["last_page_read"],
                        "preferred_zoom": row["preferred_zoom"],
                        "markdown_path": row["markdown_path"],
                        "is_converted": bool(row["is_converted"]),
                        "last_accessed": row["last_accessed"],
                        "availability_status": status,
                    }
                )
            return docs

    def get_terms_status(self) -> dict[str, Any]:
        """Verifica se o usuário já aceitou os termos de uso formalmente com sua versão (Item 18)."""
        with _DB_LOCK, self._connection() as conn:
            row = conn.execute("SELECT accepted, accepted_at, terms_version FROM app_agreements WHERE id = 1").fetchone()
            if row and row["accepted"] == 1:
                return {
                    "accepted": True,
                    "accepted_at": row["accepted_at"],
                    "terms_version": row["terms_version"] or "1.0",
                }
        return {"accepted": False, "terms_version": None}

    def save_terms_acceptance(self, terms_version: str = "1.0") -> None:
        """Registra o aceite formal e irrevogável dos termos de uso."""
        now = time.time()

        def _do_save_terms(conn: sqlite3.Connection) -> None:
            conn.execute(
                """
                INSERT INTO app_agreements (id, accepted, accepted_at, terms_version)
                VALUES (1, 1, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    accepted = 1,
                    accepted_at = excluded.accepted_at,
                    terms_version = excluded.terms_version
            """,
                (now, terms_version),
            )

        self._execute_write(_do_save_terms)
