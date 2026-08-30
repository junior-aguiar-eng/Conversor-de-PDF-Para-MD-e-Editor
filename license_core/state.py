"""Máquina de estados determinística para direitos ACT4."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from .protocol import LicensePayload, _parse_timestamp


class LicenseState(StrEnum):
    UNLICENSED = "unlicensed"
    VALID = "valid"
    EXPIRING = "expiring"
    ONLINE_CHECK_REQUIRED = "online_check_required"
    EXPIRED = "expired"
    REVOKED = "revoked"
    SUSPENDED = "suspended"
    CLOCK_TAMPERED = "clock_tampered"
    MACHINE_MISMATCH = "machine_mismatch"
    INVALID = "invalid"


_USABLE_STATES = frozenset({LicenseState.VALID, LicenseState.EXPIRING})


@dataclass(frozen=True, slots=True)
class LicenseStatus:
    """Resultado completo e serializável da avaliação de uma licença."""

    state: LicenseState
    machine_id: str
    message: str
    license_id: str | None = None
    license_format: str | None = None
    expires_at: str | None = None
    days_remaining: int | None = None
    offline_until: str | None = None
    offline_seconds_remaining: int | None = None
    last_online_validation: str | None = None
    validation_mode: str | None = None
    features: tuple[str, ...] = ()

    @property
    def can_use_protected_features(self) -> bool:
        return self.state in _USABLE_STATES

    def allows(self, feature: str) -> bool:
        return self.can_use_protected_features and (self.license_format == "legacy" or feature in self.features)

    def to_mapping(self) -> dict[str, object]:
        return {
            "state": self.state.value,
            "can_use_protected_features": self.can_use_protected_features,
            "license_id": self.license_id,
            "license_format": self.license_format,
            "machine_id": self.machine_id,
            "expires_at": self.expires_at,
            "days_remaining": self.days_remaining,
            "offline_until": self.offline_until,
            "offline_seconds_remaining": self.offline_seconds_remaining,
            "last_online_validation": self.last_online_validation,
            "validation_mode": self.validation_mode,
            "features": list(self.features),
            "message": self.message,
        }


def unlicensed_status(machine_id: str) -> LicenseStatus:
    return LicenseStatus(
        state=LicenseState.UNLICENSED,
        machine_id=machine_id,
        message="Ativação pendente para esta máquina.",
    )


def invalid_status(machine_id: str, message: str = "A licença armazenada é inválida.") -> LicenseStatus:
    return LicenseStatus(state=LicenseState.INVALID, machine_id=machine_id, message=message)


def legacy_valid_status(machine_id: str) -> LicenseStatus:
    return LicenseStatus(
        state=LicenseState.VALID,
        machine_id=machine_id,
        message="Licença legada válida.",
        license_format="legacy",
        validation_mode="offline",
        features=("converter", "ocr", "reader"),
    )


def evaluate_act4(
    payload: LicensePayload,
    machine_id: str,
    *,
    at: datetime,
    online_status: str = "active",
    offline_until: datetime | None = None,
    last_online_validation: datetime | None = None,
    entitlement_expires_at: str | None = None,
    clock_tampered: bool = False,
) -> LicenseStatus:
    """Avalia o direito já autenticado sem executar efeitos colaterais."""
    if at.tzinfo is None or at.utcoffset() is None:
        raise ValueError("at deve possuir fuso horário.")
    instant = at.astimezone(UTC)
    normalized_offline_until = offline_until.astimezone(UTC) if offline_until else None
    normalized_last_validation = last_online_validation.astimezone(UTC) if last_online_validation else None
    effective_expiration_text = entitlement_expires_at or payload.expires_at
    effective_expiration = _parse_timestamp(effective_expiration_text, "entitlement_expires_at")
    base = {
        "machine_id": machine_id,
        "license_id": payload.license_id,
        "license_format": "act4",
        "expires_at": effective_expiration_text,
        "features": payload.features,
        "validation_mode": payload.validation_mode,
        "offline_until": normalized_offline_until.strftime("%Y-%m-%dT%H:%M:%SZ") if normalized_offline_until else None,
        "offline_seconds_remaining": (
            max(0, math.ceil((normalized_offline_until - instant).total_seconds()))
            if normalized_offline_until
            else None
        ),
        "last_online_validation": (
            normalized_last_validation.strftime("%Y-%m-%dT%H:%M:%SZ") if normalized_last_validation else None
        ),
    }

    if machine_id.strip().upper() != payload.machine_id:
        return LicenseStatus(
            state=LicenseState.MACHINE_MISMATCH,
            message="A licença pertence a outro computador.",
            **base,
        )
    if online_status == "revoked":
        return LicenseStatus(state=LicenseState.REVOKED, message="A licença foi revogada.", **base)
    if online_status == "suspended":
        return LicenseStatus(state=LicenseState.SUSPENDED, message="A licença está suspensa.", **base)
    if online_status == "expired":
        return LicenseStatus(state=LicenseState.EXPIRED, message="A licença expirou.", days_remaining=0, **base)
    if online_status == "machine_mismatch":
        return LicenseStatus(
            state=LicenseState.MACHINE_MISMATCH,
            message="O dispositivo ativo no serviço não corresponde a este computador.",
            **base,
        )
    if online_status == "not_found":
        return LicenseStatus(state=LicenseState.INVALID, message="A licença não foi encontrada pelo serviço.", **base)
    if online_status != "active":
        return LicenseStatus(state=LicenseState.INVALID, message="O status online da licença é inválido.", **base)
    if clock_tampered:
        return LicenseStatus(
            state=LicenseState.CLOCK_TAMPERED,
            message="Foi detectado retrocesso relevante do relógio; conecte-se para validar a licença.",
            **base,
        )

    starts = _parse_timestamp(payload.not_before, "not_before")
    expires = effective_expiration
    if instant < starts:
        return LicenseStatus(state=LicenseState.INVALID, message="A licença ainda não alcançou a data inicial.", **base)
    if instant >= expires:
        return LicenseStatus(
            state=LicenseState.EXPIRED,
            message="A licença expirou.",
            days_remaining=0,
            **base,
        )

    seconds_remaining = (expires - instant).total_seconds()
    days_remaining = max(1, math.ceil(seconds_remaining / 86_400))
    if payload.validation_mode == "hybrid" and (offline_until is None or instant >= offline_until):
        return LicenseStatus(
            state=LicenseState.ONLINE_CHECK_REQUIRED,
            message="É necessária uma validação online para continuar.",
            days_remaining=days_remaining,
            **base,
        )
    if days_remaining <= 30:
        return LicenseStatus(
            state=LicenseState.EXPIRING,
            message=f"A licença expira em {days_remaining} dia(s).",
            days_remaining=days_remaining,
            **base,
        )
    return LicenseStatus(
        state=LicenseState.VALID,
        message="Licença válida.",
        days_remaining=days_remaining,
        **base,
    )


class LicenseAccessError(PermissionError):
    """Base das recusas de acesso vinculadas a um estado explícito."""

    def __init__(self, status: LicenseStatus) -> None:
        self.status = status
        self.machine_id = status.machine_id
        super().__init__(status.message)


class UnlicensedAccessError(LicenseAccessError):
    pass


class OnlineCheckRequiredError(LicenseAccessError):
    pass


class ExpiredLicenseError(LicenseAccessError):
    pass


class RevokedLicenseError(LicenseAccessError):
    pass


class SuspendedLicenseError(LicenseAccessError):
    pass


class ClockTamperedError(LicenseAccessError):
    pass


class MachineMismatchAccessError(LicenseAccessError):
    pass


class InvalidLicenseAccessError(LicenseAccessError):
    pass


class FeatureNotLicensedError(LicenseAccessError):
    def __init__(self, status: LicenseStatus, feature: str) -> None:
        self.feature = feature
        super().__init__(status)
        self.args = (f"A licença não autoriza a funcionalidade {feature!r}.",)


_STATE_ERRORS: dict[LicenseState, type[LicenseAccessError]] = {
    LicenseState.UNLICENSED: UnlicensedAccessError,
    LicenseState.ONLINE_CHECK_REQUIRED: OnlineCheckRequiredError,
    LicenseState.EXPIRED: ExpiredLicenseError,
    LicenseState.REVOKED: RevokedLicenseError,
    LicenseState.SUSPENDED: SuspendedLicenseError,
    LicenseState.CLOCK_TAMPERED: ClockTamperedError,
    LicenseState.MACHINE_MISMATCH: MachineMismatchAccessError,
    LicenseState.INVALID: InvalidLicenseAccessError,
}


def require_feature(status: LicenseStatus, feature: str) -> None:
    """Falha com uma exceção específica antes do uso de uma feature protegida."""
    if status.allows(feature):
        return
    if status.can_use_protected_features:
        raise FeatureNotLicensedError(status, feature)
    error_type = _STATE_ERRORS.get(status.state, InvalidLicenseAccessError)
    raise error_type(status)
