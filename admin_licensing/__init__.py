"""Núcleo administrativo local para o ciclo de vida de licenças ACT4."""

from .database import AdminDatabase, BackupError, ConfirmationRequiredError
from .service import (
    AdminLicenseError,
    AdminLicenseService,
    EncryptedPrivateKeyProvider,
    InvalidTransitionError,
    RecordNotFoundError,
)

__all__ = [
    "AdminDatabase",
    "AdminLicenseError",
    "AdminLicenseService",
    "BackupError",
    "ConfirmationRequiredError",
    "EncryptedPrivateKeyProvider",
    "InvalidTransitionError",
    "RecordNotFoundError",
]
