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
from collections.abc import Generator
from contextlib import contextmanager
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
                    created_at REAL DEFAULT 0
                )
            """)

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
            # 1. Atualiza metadados do documento
            conn.execute(
                """
                INSERT INTO documents (file_path, file_name, file_size, page_count, last_accessed, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(file_path) DO UPDATE SET
                    file_name = excluded.file_name,
                    file_size = excluded.file_size,
                    page_count = excluded.page_count,
                    last_accessed = excluded.last_accessed
                """,
                (path_str, file_name, file_size, page_count, now, now),
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
                INSERT INTO documents (file_path, file_name, markdown_path, is_converted, last_accessed, created_at)
                VALUES (?, ?, ?, 1, ?, ?)
                ON CONFLICT(file_path) DO UPDATE SET
                    markdown_path = excluded.markdown_path,
                    is_converted = 1,
                    last_accessed = excluded.last_accessed
                """,
                (path_str, file_name, md_path_str, now, now),
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

        snip_open = '<mark class="bg-amber-200 text-amber-900 font-bold px-0.5 rounded">'
        snip_close = "</mark>"
        sql = f"""
            SELECT
                file_path,
                file_name,
                page_number,
                content_type,
                title,
                snippet(doc_fts, 4, '{snip_open}', '{snip_close}', '...', 22) AS match_snippet,
                bm25(doc_fts) AS rank
            FROM doc_fts
            WHERE doc_fts MATCH ?
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
                results.append(
                    {
                        "file_path": row["file_path"],
                        "file_name": row["file_name"],
                        "page_number": int(row["page_number"]),
                        "content_type": row["content_type"],
                        "title": row["title"],
                        "snippet": row["match_snippet"],
                        "rank": float(row["rank"]),
                    }
                )
            return results

    def save_session_state(self, file_path: str, last_page: int, zoom: str = "1.0") -> None:
        """Salva a última página lida e o zoom preferido do documento."""
        path_str = str(Path(file_path).resolve())
        now = time.time()

        def _do_save_session(conn: sqlite3.Connection) -> None:
            conn.execute(
                """
                UPDATE documents
                SET last_page_read = ?, preferred_zoom = ?, last_accessed = ?
                WHERE file_path = ?
                """,
                (last_page, str(zoom), now, path_str),
            )

        self._execute_write(_do_save_session)

    def get_session_state(self, file_path: str) -> dict[str, Any]:
        """Recupera o histórico e configurações de leitura de um PDF."""
        path_str = str(Path(file_path).resolve())
        with _DB_LOCK, self._connection() as conn:
            cursor = conn.execute(
                "SELECT last_page_read, preferred_zoom, markdown_path FROM documents WHERE file_path = ?",
                (path_str,),
            )
            row = cursor.fetchone()
            if row:
                return {
                    "found": True,
                    "last_page_read": row["last_page_read"],
                    "preferred_zoom": row["preferred_zoom"],
                    "markdown_path": row["markdown_path"],
                }
            return {"found": False, "last_page_read": 0, "preferred_zoom": "1.0", "markdown_path": ""}

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

    def delete_bookmark(self, bookmark_id: int) -> bool:
        """Remove um marcador de página."""

        def _do_del_bm(conn: sqlite3.Connection) -> bool:
            cursor = conn.execute("DELETE FROM bookmarks WHERE id = ?", (bookmark_id,))
            return cursor.rowcount > 0

        return bool(self._execute_write(_do_del_bm))

    def get_recent_documents(self, limit: int = 20) -> list[dict[str, Any]]:
        """Retorna os documentos acessados recentemente no acervo."""
        with _DB_LOCK, self._connection() as conn:
            cursor = conn.execute(
                """
                SELECT file_path, file_name, file_size, page_count, last_page_read,
                       preferred_zoom, markdown_path, is_converted, last_accessed
                FROM documents
                ORDER BY last_accessed DESC
                LIMIT ?
                """,
                (limit,),
            )
            rows = cursor.fetchall()
            return [
                {
                    "file_path": row["file_path"],
                    "file_name": row["file_name"],
                    "file_size": row["file_size"],
                    "page_count": row["page_count"],
                    "last_page_read": row["last_page_read"],
                    "preferred_zoom": row["preferred_zoom"],
                    "markdown_path": row["markdown_path"],
                    "is_converted": bool(row["is_converted"]),
                    "last_accessed": row["last_accessed"],
                }
                for row in rows
            ]

    def get_terms_status(self) -> dict[str, Any]:
        """Verifica se o usuário já aceitou os termos de uso formalmente."""
        with _DB_LOCK, self._connection() as conn:
            row = conn.execute("SELECT accepted, accepted_at FROM app_agreements WHERE id = 1").fetchone()
            if row and row["accepted"] == 1:
                return {"accepted": True, "accepted_at": row["accepted_at"]}
        return {"accepted": False}

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
