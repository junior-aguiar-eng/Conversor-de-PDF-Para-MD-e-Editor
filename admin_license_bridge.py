"""Bridge restrita entre a interface administrativa e o núcleo de licenças."""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from admin_licensing import (
    ActiveEncryptedKeyProvider,
    AdminLicenseService,
    EncryptedPrivateKeyProvider,
    EncryptedSigningKeyStore,
)

logger = logging.getLogger(__name__)


def _offline_validation(payload: dict[str, Any]) -> tuple[str, int]:
    validation_mode = str(payload.get("validation_mode", "offline"))
    max_offline_days = int(payload.get("max_offline_days", 0))
    if validation_mode != "offline" or max_offline_days != 0:
        raise ValueError("O Admin local emite somente licenças offline.")
    return validation_mode, max_offline_days


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
            validation_mode, max_offline_days = _offline_validation(payload)
            license_id = self.service.issue_license(
                str(payload.get("customer_id", "")),
                term_months=int(payload.get("term_months", 0)),
                features=tuple(payload.get("features") or ("converter", "ocr", "reader")),
                validation_mode=validation_mode,
                max_offline_days=max_offline_days,
                customer_reference=payload.get("customer_reference"),
                commercial_reference=payload.get("commercial_reference"),
                machine_id=payload.get("machine_id"),
                admin_user_id=self.admin_user_id,
            )
            return {"license_id": license_id}

        return self._safe(operation)

    def create_customer_license(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Valida o pedido completo antes de criar o cliente e emitir a licença."""
        def operation() -> dict[str, Any]:
            term_months = int(payload.get("term_months", 0))
            features = tuple(payload.get("features") or ())
            validation_mode, max_offline_days = _offline_validation(payload)
            machine_id = str(payload.get("machine_id", "")).strip()
            if term_months not in {3, 6, 12}:
                raise ValueError("O prazo deve ser de 3, 6 ou 12 meses.")
            if not features or not set(features).issubset({"converter", "ocr", "reader"}):
                raise ValueError("Selecione ao menos uma funcionalidade válida.")
            if machine_id:
                import re

                if not re.fullmatch(r"NXJ2-(?:[A-Fa-f0-9]{4}-){3}[A-Fa-f0-9]{4}", machine_id):
                    raise ValueError("O código da máquina NXJ2 é inválido.")
            customer_id = self.service.create_customer(
                str(payload.get("name", "")),
                email=payload.get("email"),
                phone=payload.get("phone"),
                tax_id=payload.get("tax_id"),
                commercial_reference=payload.get("commercial_reference"),
                admin_user_id=self.admin_user_id,
            )
            license_id = self.service.issue_license(
                customer_id,
                term_months=term_months,
                features=features,
                validation_mode=validation_mode,
                max_offline_days=max_offline_days,
                machine_id=machine_id,
                commercial_reference=payload.get("commercial_reference"),
                admin_user_id=self.admin_user_id,
            )
            return {"customer_id": customer_id, "license_id": license_id}

        return self._safe(operation)

    def migrate_legacy_license(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Converte uma licença legada somente após validação e ciência explícita."""
        def operation() -> dict[str, Any]:
            machine_id = str(payload.get("machine_id", "")).strip().upper()
            activation_key = str(payload.get("activation_key", "")).strip().upper()
            confirmation = str(payload.get("confirmation", ""))
            term_months = int(payload.get("term_months", 0))
            features = tuple(payload.get("features") or ())
            validation_mode, max_offline_days = _offline_validation(payload)
            self.service.validate_legacy_migration(
                machine_id,
                activation_key,
                confirmation=confirmation,
            )
            if term_months not in {3, 6, 12}:
                raise ValueError("O prazo deve ser de 3, 6 ou 12 meses.")
            if not features or not set(features).issubset({"converter", "ocr", "reader"}):
                raise ValueError("Selecione ao menos uma funcionalidade válida.")
            customer_id = self.service.create_customer(
                str(payload.get("name", "")),
                email=payload.get("email"),
                phone=payload.get("phone"),
                tax_id=payload.get("tax_id"),
                commercial_reference=payload.get("commercial_reference"),
                admin_user_id=self.admin_user_id,
            )
            migration_id, license_id = self.service.migrate_legacy_license(
                customer_id,
                machine_id=machine_id,
                activation_key=activation_key,
                confirmation=confirmation,
                term_months=term_months,
                features=features,
                validation_mode=validation_mode,
                max_offline_days=max_offline_days,
                commercial_reference=payload.get("commercial_reference"),
                admin_user_id=self.admin_user_id,
            )
            return {"migration_id": migration_id, "customer_id": customer_id, "license_id": license_id}

        return self._safe(operation)

    def renew_license(self, license_id: str, term_months: int) -> dict[str, Any]:
        def operation() -> dict[str, Any]:
            renewal_id = self.service.renew_license(
                license_id, term_months=int(term_months), admin_user_id=self.admin_user_id
            )
            return {
                "renewal_id": renewal_id,
                "expires_at": self.service.get_license(license_id)["expires_at"],
            }

        return self._safe(operation)

    def suspend_license(self, license_id: str, reason: str) -> dict[str, Any]:
        return self._safe(
            lambda: {
                "change_id": self.service.suspend_license(license_id, reason, admin_user_id=self.admin_user_id)
            }
        )

    def reactivate_license(self, license_id: str, reason: str) -> dict[str, Any]:
        return self._safe(
            lambda: {
                "change_id": self.service.reactivate_license(license_id, reason, admin_user_id=self.admin_user_id)
            }
        )

    def revoke_license(self, license_id: str, reason: str, confirmation: str) -> dict[str, Any]:
        return self._safe(
            lambda: {
                "change_id": self.service.revoke_license(
                    license_id,
                    reason,
                    confirmation=confirmation,
                    admin_user_id=self.admin_user_id,
                )
            }
        )

    def bind_device(self, license_id: str, machine_id: str) -> dict[str, Any]:
        return self._safe(
            lambda: {
                "device_id": self.service.bind_device(license_id, machine_id, admin_user_id=self.admin_user_id)
            }
        )

    def replace_device(
        self,
        license_id: str,
        machine_id: str,
        reason: str,
        confirmation: str,
    ) -> dict[str, Any]:
        return self._safe(
            lambda: {
                "device_id": self.service.replace_device(
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
            service = self.service
            if self.signing_key_store is not None:
                provider = ActiveEncryptedKeyProvider(
                    self.signing_key_store,
                    purpose="license",
                    password_provider=lambda: private_key_password,
                )
                service = AdminLicenseService(
                    self.service.database,
                    key_id=provider.key_id,
                    private_key_provider=provider,
                )
            elif self.private_key_path is not None:
                provider = EncryptedPrivateKeyProvider(self.private_key_path, lambda: private_key_password)
                service = AdminLicenseService(
                    self.service.database,
                    key_id=self.service.key_id,
                    private_key_provider=provider,
                )
            exported = service.export_license(license_id, target, admin_user_id=self.admin_user_id)
            return {"file_name": exported.name}

        return self._safe(operation)

    @staticmethod
    def _safe(operation: Callable[[], dict[str, Any]]) -> dict[str, Any]:
        try:
            return {"ok": True, **operation()}
        except (ValueError, TypeError, PermissionError, RuntimeError) as error:
            return {"ok": False, "error": str(error)}
        except Exception:
            logger.exception("Falha inesperada na operação administrativa")
            return {"ok": False, "error": "Falha inesperada na operação administrativa."}
