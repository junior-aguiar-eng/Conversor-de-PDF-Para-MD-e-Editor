"""Cliente HTTPS mínimo para obtenção e persistência de leases assinados."""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit
from urllib.request import Request, urlopen

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from license_service import LeaseFormatError, LeasePayload, verify_lease

MAX_LEASE_RESPONSE_BYTES = 64 * 1024


class LicenseServiceUnavailable(ConnectionError):
    pass


class LicenseServiceResponseError(RuntimeError):
    pass


class OnlineLicenseClient:
    def __init__(
        self,
        base_url: str,
        public_keys: Mapping[str, Ed25519PublicKey | bytes],
        *,
        lease_path: str | Path,
        timeout_seconds: float = 8.0,
    ) -> None:
        parsed = urlsplit(base_url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query:
            raise ValueError("O serviço de licenças deve usar uma URL HTTPS absoluta sem credenciais ou consulta.")
        if not public_keys:
            raise ValueError("Ao menos uma chave pública de lease é obrigatória.")
        if not 1 <= timeout_seconds <= 30:
            raise ValueError("O timeout do serviço deve ficar entre 1 e 30 segundos.")
        self.base_url = base_url.rstrip("/") + "/"
        self.public_keys = dict(public_keys)
        self.lease_path = Path(lease_path)
        self.timeout_seconds = timeout_seconds

    @staticmethod
    def generate_nonce() -> str:
        import base64

        return base64.urlsafe_b64encode(os.urandom(24)).rstrip(b"=").decode("ascii")

    def check(self, license_id: str, machine_id: str) -> LeasePayload:
        return self._request("v1/licenses/check", license_id, machine_id)

    def refresh(self, license_id: str, machine_id: str) -> LeasePayload:
        return self._request("v1/licenses/refresh", license_id, machine_id)

    def activate(self, license_id: str, machine_id: str, activation_code: str) -> LeasePayload:
        return self._request(
            "v1/licenses/activate",
            license_id,
            machine_id,
            extra={"activation_code": activation_code},
        )

    def _request(
        self,
        endpoint: str,
        license_id: str,
        machine_id: str,
        *,
        extra: Mapping[str, Any] | None = None,
    ) -> LeasePayload:
        nonce = self.generate_nonce()
        payload = {"license_id": license_id, "machine_id": machine_id, "nonce": nonce, **(extra or {})}
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        request = Request(
            urljoin(self.base_url, endpoint),
            data=encoded,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                content_type = response.headers.get_content_type()
                document_bytes = response.read(MAX_LEASE_RESPONSE_BYTES + 1)
        except HTTPError as error:
            if error.code >= 500 or error.code == 429:
                raise LicenseServiceUnavailable("O serviço de licenças está temporariamente indisponível.") from error
            raise LicenseServiceResponseError(f"O serviço de licenças recusou a solicitação ({error.code}).") from error
        except (TimeoutError, URLError, OSError) as error:
            raise LicenseServiceUnavailable("Não foi possível alcançar o serviço de licenças.") from error
        if content_type != "application/json" or len(document_bytes) > MAX_LEASE_RESPONSE_BYTES:
            raise LicenseServiceResponseError("A resposta do serviço possui tipo ou tamanho inválido.")
        try:
            document = json.loads(document_bytes.decode("utf-8"), object_pairs_hook=self._reject_duplicates)
            lease = verify_lease(
                document,
                self.public_keys,
                expected_nonce=nonce,
                expected_license_id=license_id,
            )
        except (UnicodeDecodeError, json.JSONDecodeError, LeaseFormatError, TypeError, ValueError) as error:
            raise LicenseServiceResponseError("A resposta assinada do serviço é inválida.") from error
        self._save(document)
        return lease

    def load_cached(self, *, expected_license_id: str) -> LeasePayload | None:
        if not self.lease_path.is_file():
            return None
        try:
            document = json.loads(
                self.lease_path.read_text(encoding="utf-8"), object_pairs_hook=self._reject_duplicates
            )
            nonce = document["nonce"]
            return verify_lease(
                document,
                self.public_keys,
                expected_nonce=nonce,
                expected_license_id=expected_license_id,
            )
        except (OSError, KeyError, UnicodeDecodeError, json.JSONDecodeError, LeaseFormatError, TypeError, ValueError):
            return None

    def _save(self, document: Mapping[str, Any]) -> None:
        self.lease_path.parent.mkdir(parents=True, exist_ok=True)
        encoded = (
            json.dumps(document, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode("utf-8")
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{self.lease_path.name}.", suffix=".tmp", dir=self.lease_path.parent
        )
        try:
            with os.fdopen(descriptor, "wb") as temporary:
                temporary.write(encoded)
                temporary.flush()
                os.fsync(temporary.fileno())
            os.replace(temporary_name, self.lease_path)
        finally:
            Path(temporary_name).unlink(missing_ok=True)

    @staticmethod
    def _reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"Campo JSON duplicado: {key}.")
            result[key] = value
        return result
