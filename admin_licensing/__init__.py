"""Núcleo administrativo local para o ciclo de vida de licenças ACT4."""

from .database import AdminDatabase, BackupError, ConfirmationRequiredError
from .security import (
    ActiveEncryptedKeyProvider,
    EncryptedSigningKeyStore,
    OperationalSecurityError,
    generate_totp_secret,
    hash_admin_password,
    totp_code,
    verify_admin_password,
    verify_totp,
)
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
    "ActiveEncryptedKeyProvider",
    "BackupError",
    "ConfirmationRequiredError",
    "EncryptedPrivateKeyProvider",
    "EncryptedSigningKeyStore",
    "InvalidTransitionError",
    "OperationalSecurityError",
    "RecordNotFoundError",
    "generate_totp_secret",
    "hash_admin_password",
    "totp_code",
    "verify_admin_password",
    "verify_totp",
]
