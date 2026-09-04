"""Bridge restrita entre a interface administrativa e o núcleo de licenças."""

from __future__ import annotations

import base64
import logging
import os
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import serialization

from admin_licensing import (
    ActiveEncryptedKeyProvider,
    AdminLicenseService,
    EncryptedPrivateKeyProvider,
    EncryptedSigningKeyStore,
)
from license_key_config import LICENSE_PUBLIC_KEYS_B64

logger = logging.getLogger(__name__)


class AdminLicenseBridge:
    def __init__(
        self,
        service: AdminLicenseService,
        *,
        admin_user_id: str | None = None,
        private_key_path: str | Path | None = None,
        signing_key_store: EncryptedSigningKeyStore | None = None,
    ) -> None:
        self.service = service
        self.admin_user_id = admin_user_id
        self.private_key_path = Path(private_key_path).resolve() if private_key_path else None
        self.signing_key_store = signing_key_store
        self._window: Any = None

    def set_window(self, window: Any) -> None:
        self._window = window

    def private_key_status(self) -> dict[str, Any]:
        configured = bool(self.private_key_path and self.private_key_path.is_file())
        return {
            "ok": True,
            "configured": configured,
            "file_name": self.private_key_path.name if configured and self.private_key_path else None,
        }

    def configure_private_key(self, password: str) -> dict[str, Any]:
        """Seleciona, valida e instala o PEM criptografado no perfil administrativo."""
        if not self._window:
            return {"ok": False, "error": "Janela administrativa indisponível."}
        if self.private_key_path is None:
            return {"ok": False, "error": "O destino gerenciado da chave privada não foi configurado."}
        import webview

        result = self._window.create_file_dialog(
            webview.OPEN_DIALOG,
            allow_multiple=False,
            file_types=("Chave privada criptografada (*.pem)",),
        )
        if not result:
            return {"ok": False, "cancelled": True, "error": "Seleção cancelada."}
        selected_value = result[0] if isinstance(result, (tuple, list)) else result
        selected = Path(selected_value).expanduser().resolve()

        def operation() -> dict[str, Any]:
            if not selected.is_file() or selected.stat().st_size > 65_536:
                raise ValueError("Selecione uma chave privada PEM válida de até 64 KB.")
            private_key = EncryptedPrivateKeyProvider(selected, lambda: password)()
            public_bytes = private_key.public_key().public_bytes(
                encoding=serialization.Encoding.Raw,
                format=serialization.PublicFormat.Raw,
            )
            expected = LICENSE_PUBLIC_KEYS_B64.get(self.service.key_id)
            actual = base64.b64encode(public_bytes).decode("ascii")
            if not expected or actual != expected:
                raise ValueError("A chave selecionada não corresponde à chave pública incorporada ao Conversor.")

            target = self.private_key_path
            target.parent.mkdir(parents=True, exist_ok=True)
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
            )
            os.close(descriptor)
            temporary = Path(temporary_name)
            try:
                temporary.write_bytes(selected.read_bytes())
                os.replace(temporary, target)
            finally:
                temporary.unlink(missing_ok=True)
            return {"configured": True, "file_name": target.name}

        return self._safe(operation)

    def dashboard(self) -> dict[str, Any]:
        return self._safe(lambda: self.service.dashboard())

    def search_licenses(self, query: str, page: int = 1, page_size: int = 25) -> dict[str, Any]:
        return self._safe(lambda: self.service.search_licenses(query, page=page, page_size=page_size))

    def get_license_detail(self, license_id: str) -> dict[str, Any]:
        return self._safe(lambda: self.service.license_detail(license_id))

    def create_customer(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._safe(
            lambda: {
                "customer_id": self.service.create_customer(
                    str(payload.get("name", "")),
                    email=payload.get("email"),
                    phone=payload.get("phone"),
                    tax_id=payload.get("tax_id"),
                    commercial_reference=payload.get("commercial_reference"),
                    admin_user_id=self.admin_user_id,
                )
            }
        )

    def issue_license(self, payload: dict[str, Any]) -> dict[str, Any]:
        def operation() -> dict[str, Any]:
            service = self._signing_service(str(payload.get("private_key_password", "")))
            license_id = service.issue_license(
                str(payload.get("customer_id", "")),
                term_months=int(payload.get("term_months", 0)),
                features=tuple(payload.get("features") or ("converter", "ocr", "reader")),
                customer_reference=payload.get("customer_reference"),
                commercial_reference=payload.get("commercial_reference"),
                machine_id=str(payload.get("machine_id", "")),
                admin_user_id=self.admin_user_id,
            )
            return {"license_id": license_id}

        return self._safe(operation)

    def create_customer_license(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Valida o pedido completo antes de criar o cliente e emitir a licença."""
        def operation() -> dict[str, Any]:
            term_months = int(payload.get("term_months", 0))
            features = tuple(payload.get("features") or ())
            machine_id = str(payload.get("machine_id", "")).strip()
            if term_months not in {3, 6, 12}:
                raise ValueError("O prazo deve ser de 3, 6 ou 12 meses.")
            if not features or not set(features).issubset({"converter", "ocr", "reader"}):
                raise ValueError("Selecione ao menos uma funcionalidade válida.")
            if machine_id:
                import re

                if not re.fullmatch(r"NXJ2-(?:[A-Fa-f0-9]{4}-){3}[A-Fa-f0-9]{4}", machine_id):
                    raise ValueError("O código da máquina NXJ2 é inválido.")
            service = self._signing_service(str(payload.get("private_key_password", "")))
            service.private_key_provider()
            customer_id = self.service.create_customer(
                str(payload.get("name", "")),
                email=payload.get("email"),
                phone=payload.get("phone"),
                tax_id=payload.get("tax_id"),
                commercial_reference=payload.get("commercial_reference"),
                admin_user_id=self.admin_user_id,
            )
            license_id = service.issue_license(
                customer_id,
                term_months=term_months,
                features=features,
                machine_id=machine_id,
                commercial_reference=payload.get("commercial_reference"),
                admin_user_id=self.admin_user_id,
            )
            return {"customer_id": customer_id, "license_id": license_id}

        return self._safe(operation)

    def renew_license(self, license_id: str, term_months: int, private_key_password: str = "") -> dict[str, Any]:
        def operation() -> dict[str, Any]:
            service = self._signing_service(private_key_password)
            revision = service.renew_license(
                license_id, term_months=int(term_months), admin_user_id=self.admin_user_id
            )
            return {
                "revision": revision,
                "expires_at": self.service.get_license(license_id)["expires_at"],
            }

        return self._safe(operation)

    def replace_device(
        self,
        license_id: str,
        machine_id: str,
        reason: str,
        confirmation: str,
        private_key_password: str = "",
    ) -> dict[str, Any]:
        return self._safe(
            lambda: {
                "revision": self._signing_service(private_key_password).replace_device(
                    license_id,
                    machine_id,
                    confirmation=confirmation,
                    reason=reason,
                    admin_user_id=self.admin_user_id,
                )
            }
        )

    def export_license(self, license_id: str, private_key_password: str = "") -> dict[str, Any]:
        if not self._window:
            return {"ok": False, "error": "Janela administrativa indisponível."}
        import webview

        result = self._window.create_file_dialog(
            webview.SAVE_DIALOG,
            save_filename=f"{license_id}.nxjlic",
            file_types=("Licença NexoJuris (*.nxjlic)",),
        )
        if not result:
            return {"ok": False, "cancelled": True}
        target = result[0] if isinstance(result, (tuple, list)) else result

        def operation() -> dict[str, Any]:
            exported = self.service.export_license(license_id, target, admin_user_id=self.admin_user_id)
            return {"file_name": exported.name}

        return self._safe(operation)

    def _signing_service(self, private_key_password: str) -> AdminLicenseService:
        if self.signing_key_store is not None:
            provider = ActiveEncryptedKeyProvider(
                self.signing_key_store,
                purpose="license",
                password_provider=lambda: private_key_password,
            )
            return AdminLicenseService(
                self.service.database, key_id=provider.key_id, private_key_provider=provider
            )
        if self.private_key_path is None or not self.private_key_path.is_file():
            raise ValueError("Configure a chave privada criptografada antes desta operação.")
        provider = EncryptedPrivateKeyProvider(self.private_key_path, lambda: private_key_password)
        return AdminLicenseService(
            self.service.database, key_id=self.service.key_id, private_key_provider=provider
        )

    @staticmethod
    def _safe(operation: Callable[[], dict[str, Any]]) -> dict[str, Any]:
        try:
            return {"ok": True, **operation()}
        except (ValueError, TypeError, PermissionError, RuntimeError) as error:
            return {"ok": False, "error": str(error)}
        except Exception:
            logger.exception("Falha inesperada na operação administrativa")
            return {"ok": False, "error": "Falha inesperada na operação administrativa."}
