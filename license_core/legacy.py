"""Detecção e validação compartilhadas dos tokens legados ACT2/ACT3."""

from __future__ import annotations

import base64
import re
from enum import StrEnum

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


class LegacyLicenseVersion(StrEnum):
    ACT2 = "ACT2"
    ACT3 = "ACT3"


_ACT2_PATTERN = re.compile(r"ACT2-01-(?:[A-Z2-7]{8}-){12}[A-Z2-7]{7}")
_ACT3_PATTERN = re.compile(r"ACT3-01-(?:[A-Z2-7]{8}-){12}[A-Z2-7]{7}")
_DEFAULT_PUBLIC_KEY_B64 = "80WGyZ+9TwHmcKDPpjOncNZVVYgFHgNBl59aBK5Hpug="


def detect_legacy_activation_key(value: str) -> LegacyLicenseVersion | None:
    """Classifica um token ACT2/ACT3 sem aceitá-lo como documento ACT4."""
    candidate = (value or "").strip().upper()
    if _ACT2_PATTERN.fullmatch(candidate):
        return LegacyLicenseVersion.ACT2
    if _ACT3_PATTERN.fullmatch(candidate):
        return LegacyLicenseVersion.ACT3
    return None


def legacy_license_payload(machine_id: str, version: LegacyLicenseVersion | int) -> bytes:
    normalized = machine_id.strip().upper()
    is_act3 = version == LegacyLicenseVersion.ACT3 or version == 3
    prefix = b"nexojuris-license:v3:" if is_act3 else b"nexojuris-license:v2:"
    return prefix + normalized.encode("ascii")


def _decode_signature(key: str) -> tuple[bytes | None, LegacyLicenseVersion | None]:
    candidate = (key or "").strip().upper()
    version = detect_legacy_activation_key(candidate)
    if version is None:
        return None, None
    encoded = candidate.removeprefix(f"{version.value}-01-").replace("-", "")
    padding = "=" * ((8 - len(encoded) % 8) % 8)
    try:
        signature = base64.b32decode(encoded + padding, casefold=False)
    except ValueError:
        return None, version
    return (signature if len(signature) == 64 else None), version


def verify_legacy_activation_key(
    machine_id: str,
    key: str,
    *,
    public_key: Ed25519PublicKey | bytes | None = None,
    public_key_b64: str = _DEFAULT_PUBLIC_KEY_B64,
) -> bool:
    if not machine_id or not key:
        return False
    signature, version = _decode_signature(key)
    if signature is None or version is None:
        return False
    try:
        if public_key is None:
            public_key = Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key_b64, validate=True))
        elif isinstance(public_key, bytes):
            public_key = Ed25519PublicKey.from_public_bytes(public_key)
        if not isinstance(public_key, Ed25519PublicKey):
            return False
        public_key.verify(signature, legacy_license_payload(machine_id, version))
        return True
    except (InvalidSignature, UnicodeEncodeError, ValueError):
        return False
