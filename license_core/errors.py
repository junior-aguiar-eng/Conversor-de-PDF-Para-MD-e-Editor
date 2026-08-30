"""Exceções estáveis do núcleo de licenciamento ACT4."""

from __future__ import annotations


class LicenseCoreError(ValueError):
    """Base para falhas esperadas ao processar uma licença."""


class LicenseFormatError(LicenseCoreError):
    """O documento não obedece ao formato canônico ACT4."""


class UnsupportedSchemaError(LicenseFormatError):
    """A versão do schema não é suportada por este núcleo."""


class UnknownKeyError(LicenseCoreError):
    """O ``key_id`` assinado não pertence ao chaveiro confiável."""


class SignatureVerificationError(LicenseCoreError):
    """A assinatura Ed25519 não corresponde ao payload."""


class MachineMismatchError(LicenseCoreError):
    """A licença foi emitida para outro computador."""


class LicenseNotYetValidError(LicenseCoreError):
    """A licença ainda não alcançou ``not_before``."""


class LicenseExpiredError(LicenseCoreError):
    """A licença alcançou ou ultrapassou ``expires_at``."""
