"""Máquina de estados determinística para licenças ACT4 offline."""

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
    EXPIRED = "expired"
    CLOCK_TAMPERED = "clock_tampered"
    MACHINE_MISMATCH = "machine_mismatch"
    INVALID = "invalid"


_USABLE_STATES = frozenset({LicenseState.VALID, LicenseState.EXPIRING})


@dataclass(frozen=True, slots=True)
class LicenseStatus:
    state: LicenseState
    machine_id: str
    message: str
    license_id: str | None = None
    revision: int | None = None
    expires_at: str | None = None
    days_remaining: int | None = None
    features: tuple[str, ...] = ()

    @property
    def can_use_protected_features(self) -> bool:
        return self.state in _USABLE_STATES

    def allows(self, feature: str) -> bool:
        return self.can_use_protected_features and feature in self.features

    def to_mapping(self) -> dict[str, object]:
        return {
            "state": self.state.value,
            "can_use_protected_features": self.can_use_protected_features,
            "license_id": self.license_id,
            "revision": self.revision,
            "machine_id": self.machine_id,
            "expires_at": self.expires_at,
            "days_remaining": self.days_remaining,
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


def evaluate_act4(
    payload: LicensePayload,
    machine_id: str,
    *,
    at: datetime,
    clock_tampered: bool = False,
) -> LicenseStatus:
    """Avalia localmente um direito já autenticado."""
    if at.tzinfo is None or at.utcoffset() is None:
        raise ValueError("at deve possuir fuso horário.")
    instant = at.astimezone(UTC)
    expires = _parse_timestamp(payload.expires_at, "expires_at")
    base = {
        "machine_id": machine_id,
        "license_id": payload.license_id,
        "revision": payload.revision,
        "expires_at": payload.expires_at,
        "features": payload.features,
    }

    if machine_id.strip().upper() != payload.machine_id:
        return LicenseStatus(
            state=LicenseState.MACHINE_MISMATCH,
            message="A licença pertence a outro computador.",
            **base,
        )
    if clock_tampered:
        return LicenseStatus(
            state=LicenseState.CLOCK_TAMPERED,
            message="Foi detectado retrocesso relevante do relógio.",
            **base,
        )

    starts = _parse_timestamp(payload.not_before, "not_before")
    if instant < starts:
        return LicenseStatus(state=LicenseState.INVALID, message="A licença ainda não alcançou a data inicial.", **base)
    if instant >= expires:
        return LicenseStatus(
            state=LicenseState.EXPIRED,
            message="A licença expirou.",
            days_remaining=0,
            **base,
        )

    days_remaining = max(1, math.ceil((expires - instant).total_seconds() / 86_400))
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
    def __init__(self, status: LicenseStatus) -> None:
        self.status = status
        self.machine_id = status.machine_id
        super().__init__(status.message)


class UnlicensedAccessError(LicenseAccessError):
    pass


class ExpiredLicenseError(LicenseAccessError):
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
    LicenseState.EXPIRED: ExpiredLicenseError,
    LicenseState.CLOCK_TAMPERED: ClockTamperedError,
    LicenseState.MACHINE_MISMATCH: MachineMismatchAccessError,
    LicenseState.INVALID: InvalidLicenseAccessError,
}


def require_feature(status: LicenseStatus, feature: str) -> None:
    if status.allows(feature):
        return
    if status.can_use_protected_features:
        raise FeatureNotLicensedError(status, feature)
    raise _STATE_ERRORS.get(status.state, InvalidLicenseAccessError)(status)
