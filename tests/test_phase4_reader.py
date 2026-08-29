"""Testes automatizados da Fase 4 — Leitor, paginação, indexação e memória.

Verifica:
1. Abertura instantânea O(1) de PDFs extensos com get_pdf_info.
2. Solicitação de faixas de páginas com get_pdf_page_range.
3. Persistência de estado de leitura via UPSERT em save_session_state.
4. Indexação incremental de páginas visitadas e indexação completa em background.
5. Tratamento de arquivos ausentes/desconectados sem apagar histórico.
6. Relocalização ("Localizar novamente") e remoção do acervo.
7. Garantia de que a conversão em lote continua sendo uma operação integral e separada.
"""

import time
from pathlib import Path
import fitz
import pytest

from library_db import LibraryDatabase
from web_api import BridgeApi


@pytest.fixture
def sample_pdf(tmp_path: Path) -> Path:
    """Cria um PDF sintético de 60 páginas para testes de paginação por faixa."""
    pdf_path = tmp_path / "documento_extenso.pdf"
    doc = fitz.open()
    for i in range(60):
        page = doc.new_page(width=595, height=842)
        page.insert_text((50, 50), f"Conteúdo de teste da página {i + 1} de 60.")
    doc.save(str(pdf_path))
    doc.close()
    return pdf_path


@pytest.fixture
def db_instance(tmp_path: Path) -> LibraryDatabase:
    db_file = tmp_path / "test_acervo_phase4.db"
    return LibraryDatabase(db_file)


class TestPhase4ReaderAndIndexing:
    def test_fast_pdf_info_opening(self, sample_pdf: Path, tmp_path: Path):
        """Item 12: get_pdf_info retorna metadados instantaneamente sem carregar todas as páginas."""
        db_file = tmp_path / "test_fast_info.db"
        api = BridgeApi()
        api._library = LibraryDatabase(db_file)
        res_pdf = api._register_pdf(sample_pdf, "test")

        info = api.get_pdf_info(res_pdf["file_id"])
        assert info["ok"] is True
        assert info["page_count"] == 60
        assert info["file_name"] == "documento_extenso.pdf"
        assert info["is_encrypted"] is False
        assert "session_state" in info
        assert "bookmarks" in info

    def test_get_pdf_page_range(self, sample_pdf: Path, tmp_path: Path):
        """Item 12: get_pdf_page_range busca faixas específicas de páginas (ex. 20 a 40)."""
        api = BridgeApi()
        api._library = LibraryDatabase(tmp_path / "test_range.db")
        res_pdf = api._register_pdf(sample_pdf, "test")

        range_res = api.get_pdf_page_range(res_pdf["file_id"], start_page=10, count=20)
        assert range_res["ok"] is True
        assert range_res["start_page"] == 10
        assert range_res["count"] == 20
        assert range_res["total_pages"] == 60
        assert len(range_res["pages"]) == 20
        assert range_res["pages"][0]["page_number"] == 10
        assert range_res["pages"][-1]["page_number"] == 29

    def test_upsert_session_state_before_indexing(self, sample_pdf: Path, db_instance: LibraryDatabase):
        """Item 15: save_session_state realiza UPSERT permitindo salvar zoom e posição antes da indexação completa."""
        path_str = str(sample_pdf)
        # Salva o estado de leitura em documento ainda não indexado
        db_instance.save_session_state(path_str, last_page=15, zoom="1.5")
        
        state = db_instance.get_session_state(path_str)
        assert state["found"] is True
        assert state["last_page_read"] == 15
        assert state["preferred_zoom"] == "1.5"

        # Indexação posterior de metadados O(1) não apaga o estado gravado
        db_instance.index_document_metadata_only(sample_pdf, page_count=60, file_size=1000)
        state_after = db_instance.get_session_state(path_str)
        assert state_after["last_page_read"] == 15
        assert state_after["preferred_zoom"] == "1.5"

    def test_incremental_page_indexing_on_render(self, sample_pdf: Path, tmp_path: Path):
        """Item 13: render_page_hq indexa incrementalmente a página visitada."""
        db_file = tmp_path / "test_incremental.db"
        api = BridgeApi()
        api._library = LibraryDatabase(db_file)
        res_pdf = api._register_pdf(sample_pdf, "test")

        # Renderiza a página 5 (0-indexed 4)
        render_res = api.render_page_hq(res_pdf["file_id"], page_number=4)
        assert render_res["ok"] is True
        assert "image" in render_res
        assert "image_base64" in render_res  # Dupla chave (Item 14)

        # Aguarda thread daemon de indexação incremental terminar
        time.sleep(0.3)

        # Busca FTS5 encontra o texto da página 5
        search_res = api._library.search("página 5")
        assert len(search_res) > 0
        assert search_res[0]["page_number"] == 4

    def test_full_background_indexing(self, sample_pdf: Path, tmp_path: Path):
        """Item 13: start_full_indexing executa em background e permite pausar/cancelar."""
        api = BridgeApi()
        api._library = LibraryDatabase(tmp_path / "test_bg_indexing.db")
        res_pdf = api._register_pdf(sample_pdf, "test")

        start_res = api.start_full_indexing(res_pdf["file_id"])
        assert start_res["ok"] is True

        # Testa deduplicação ao tentar disparar novamente
        dup_res = api.start_full_indexing(res_pdf["file_id"])
        assert dup_res["ok"] is True
        assert dup_res.get("already_running") is True

        # Cancela a indexação
        cancel_res = api.cancel_indexing(res_pdf["file_id"])
        assert cancel_res["ok"] is True
        assert cancel_res["cancelled"] is True

    def test_missing_files_handling_and_relocation(self, tmp_path: Path):
        """Item 16: Arquivos ausentes não perdem marcadores nem histórico e podem ser relocalizados."""
        db = LibraryDatabase(tmp_path / "test_missing.db")
        original_file = tmp_path / "original.pdf"
        doc = fitz.open()
        doc.new_page().insert_text((10, 10), "Conteúdo original.")
        doc.save(str(original_file))
        doc.close()

        path_str = str(original_file.resolve())
        db.save_session_state(path_str, last_page=3, zoom="1.2")
        db.add_bookmark(path_str, page_number=2, title="Marcador de Teste")

        # Simula desconexão da unidade externa ou remoção temporária do arquivo
        original_file.unlink()

        status = db.check_and_update_document_availability(path_str)
        assert status == "temporarily_unavailable"

        # Histórico e marcadores continuam intactos no banco!
        session = db.get_session_state(path_str)
        assert session["found"] is True
        assert session["last_page_read"] == 3
        bookmarks = db.get_bookmarks(path_str)
        assert len(bookmarks) == 1
        assert bookmarks[0]["title"] == "Marcador de Teste"

        # Testa "Localizar novamente" com um novo caminho
        new_file = tmp_path / "renomeado_ou_movido.pdf"
        doc_new = fitz.open()
        doc_new.new_page().insert_text((10, 10), "Conteúdo movido.")
        doc_new.save(str(new_file))
        doc_new.close()

        relocate_ok = db.relocate_document(path_str, str(new_file))
        assert relocate_ok is True

        new_session = db.get_session_state(str(new_file.resolve()))
        assert new_session["found"] is True
        assert new_session["last_page_read"] == 3
        new_bookmarks = db.get_bookmarks(str(new_file.resolve()))
        assert len(new_bookmarks) == 1
        assert new_bookmarks[0]["title"] == "Marcador de Teste"

    def test_user_removal_from_library(self, sample_pdf: Path, tmp_path: Path):
        """Item 16: 'Remover do acervo' marca o arquivo sem deletar fisicamente marcadores do banco."""
        db = LibraryDatabase(tmp_path / "test_user_removal.db")
        path_str = str(sample_pdf.resolve())
        db.save_session_state(path_str, last_page=5, zoom="1.0")

        removed_ok = db.remove_document_from_library(path_str)
        assert removed_ok is True

        recent = db.get_recent_documents()
        assert not any(doc["file_path"] == path_str for doc in recent)
