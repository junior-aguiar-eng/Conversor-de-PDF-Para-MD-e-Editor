"""Mixin de registro e autorização segura de recursos locais."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import fitz

from api_bridge.common import format_file_size
from file_authorization import ResourceAccessError


class ResourceHelperMixin:
    """Métodos de validação, autorização e abertura controlada de arquivos."""

    def _register_pdf(self, path: str | Path, origin: str) -> dict[str, Any]:
        resolved = Path(path).expanduser().resolve()
        if not resolved.is_file() or resolved.suffix.lower() != ".pdf":
            raise ResourceAccessError("O recurso selecionado não é um PDF válido.")
        resource = self._resources.register(
            resolved,
            kind="pdf",
            origin=origin,
            capabilities={"read", "write", "convert", "open"},
        )
        size = resolved.stat().st_size
        return {
            "file_id": resource.resource_id,
            "path": str(resolved),
            "name": resolved.name,
            "size": size,
            "size_formatted": format_file_size(size),
        }

    def _register_markdown(self, path: str | Path, origin: str) -> dict[str, Any]:
        resolved = Path(path).expanduser().resolve()
        if not resolved.is_file() or resolved.suffix.lower() not in {".md", ".markdown"}:
            raise ResourceAccessError("O recurso selecionado não é um Markdown válido.")
        resource = self._resources.register(
            resolved,
            kind="markdown",
            origin=origin,
            capabilities={"read", "open", "asset_read"},
        )
        return {
            "markdown_id": resource.resource_id,
            "markdown_path": str(resolved),
            "name": resolved.name,
        }

    def _register_pdf_destination(self, path: str | Path, origin: str) -> dict[str, Any]:
        """Registra um destino PDF escolhido por diálogo nativo, sem exigir que já exista."""
        resolved = Path(path).expanduser().resolve()
        if resolved.suffix.lower() != ".pdf":
            raise ResourceAccessError("O destino escolhido deve usar a extensão .pdf.")
        resource = self._resources.register(
            resolved,
            kind="pdf_output",
            origin=origin,
            capabilities={"write"},
        )
        return {"output_file_id": resource.resource_id, "path": str(resolved), "name": resolved.name}

    def _register_library_entry(self, path: str | Path, origin: str) -> str:
        resource = self._resources.register(
            path,
            kind="library_entry",
            origin=origin,
            capabilities={"relocate", "remove"},
        )
        return resource.resource_id

    def _register_directory(self, path: str | Path, origin: str) -> dict[str, Any]:
        resolved = Path(path).expanduser().resolve()
        resolved.mkdir(parents=True, exist_ok=True)
        resource = self._resources.register(
            resolved,
            kind="directory",
            origin=origin,
            capabilities={"write", "open"},
        )
        return {"directory_id": resource.resource_id, "path": str(resolved)}

    def _resolve_pdf(self, file_id: str, capability: str = "read") -> Path:
        path = self._resources.resolve(file_id, kind="pdf", capability=capability)
        if not path.is_file() or path.suffix.lower() != ".pdf":
            raise ResourceAccessError("O PDF autorizado não está disponível.")
        return path

    def _resolve_markdown(self, markdown_id: str, capability: str = "read") -> Path:
        path = self._resources.resolve(markdown_id, kind="markdown", capability=capability)
        if not path.is_file() or path.suffix.lower() not in {".md", ".markdown"}:
            raise ResourceAccessError("O Markdown autorizado não está disponível.")
        return path

    def _resolve_directory(self, directory_id: str, capability: str = "write") -> Path:
        path = self._resources.resolve(directory_id, kind="directory", capability=capability)
        if not path.is_dir():
            raise ResourceAccessError("A pasta autorizada não está disponível.")
        return path

    def _resolve_pdf_destination(self, output_file_id: str) -> Path:
        path = self._resources.resolve(output_file_id, kind="pdf_output", capability="write")
        if path.suffix.lower() != ".pdf":
            raise ResourceAccessError("O destino autorizado não é um PDF.")
        return path

    def _resolve_library_entry(self, entry_id: str, capability: str) -> Path:
        return self._resources.resolve(entry_id, kind="library_entry", capability=capability)

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
                doc.close()
                return None, "O documento PDF está protegido por senha.", True
            return doc, None, False
        except Exception as err:
            return None, f"Falha ao abrir PDF: {err}", False

    def _process_file_paths(self, paths: list[str], *, origin: str = "file_selection") -> list[dict[str, Any]]:
        """Valida PDFs de uma origem de ingresso antes de conceder IDs opacos."""
        file_entries: list[dict[str, Any]] = []
        for raw_path in paths:
            try:
                file_entries.append(self._register_pdf(raw_path, origin))
            except (OSError, ResourceAccessError):
                continue
        return file_entries

