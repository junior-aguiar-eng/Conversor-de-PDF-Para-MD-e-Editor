"""Registro opaco de recursos autorizados para a ponte do renderer."""

from __future__ import annotations

import secrets
import threading
from dataclasses import dataclass
from pathlib import Path


class ResourceAccessError(ValueError):
    """Indica identificador ausente, tipo incorreto ou capacidade não concedida."""


@dataclass(frozen=True, slots=True)
class AuthorizedResource:
    resource_id: str
    path: Path
    kind: str
    capabilities: frozenset[str]
    origins: frozenset[str]


class AuthorizedResourceRegistry:
    """Traduz IDs imprevisíveis em caminhos canônicos na borda confiável."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._resources: dict[str, AuthorizedResource] = {}
        self._ids_by_key: dict[tuple[str, str], str] = {}

    @staticmethod
    def _canonical(path: str | Path) -> Path:
        return Path(path).expanduser().resolve()

    def register(
        self,
        path: str | Path,
        *,
        kind: str,
        origin: str,
        capabilities: set[str] | frozenset[str],
    ) -> AuthorizedResource:
        canonical = self._canonical(path)
        key = (kind, str(canonical).casefold())
        with self._lock:
            existing_id = self._ids_by_key.get(key)
            if existing_id:
                current = self._resources[existing_id]
                updated = AuthorizedResource(
                    resource_id=current.resource_id,
                    path=current.path,
                    kind=current.kind,
                    capabilities=current.capabilities | frozenset(capabilities),
                    origins=current.origins | {origin},
                )
                self._resources[existing_id] = updated
                return updated

            resource = AuthorizedResource(
                resource_id=secrets.token_urlsafe(24),
                path=canonical,
                kind=kind,
                capabilities=frozenset(capabilities),
                origins=frozenset({origin}),
            )
            self._resources[resource.resource_id] = resource
            self._ids_by_key[key] = resource.resource_id
            return resource

    def resolve(self, resource_id: str, *, kind: str, capability: str) -> Path:
        if not isinstance(resource_id, str) or not resource_id:
            raise ResourceAccessError("Identificador de recurso ausente.")
        with self._lock:
            resource = self._resources.get(resource_id)
        if resource is None:
            raise ResourceAccessError("Recurso não autorizado.")
        if resource.kind != kind:
            raise ResourceAccessError("Tipo de recurso incompatível com a operação.")
        if capability not in resource.capabilities:
            raise ResourceAccessError("Operação não autorizada para este recurso.")
        return resource.path.resolve()

    def get(self, resource_id: str) -> AuthorizedResource | None:
        with self._lock:
            return self._resources.get(resource_id)

    def id_for_path(self, path: str | Path, *, kind: str) -> str | None:
        canonical = self._canonical(path)
        with self._lock:
            return self._ids_by_key.get((kind, str(canonical).casefold()))
