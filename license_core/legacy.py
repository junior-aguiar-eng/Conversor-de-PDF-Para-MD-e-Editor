"""Detecção isolada dos tokens legados; a validação permanece em ``licensing``."""

from __future__ import annotations

import re
from enum import StrEnum


class LegacyLicenseVersion(StrEnum):
    ACT2 = "ACT2"
    ACT3 = "ACT3"


_ACT2_PATTERN = re.compile(r"ACT2-01-(?:[A-Z2-7]{8}-){12}[A-Z2-7]{7}")
_ACT3_PATTERN = re.compile(r"ACT3-01-(?:[A-Z2-7]{8}-){12}[A-Z2-7]{7}")


def detect_legacy_activation_key(value: str) -> LegacyLicenseVersion | None:
    """Classifica um token ACT2/ACT3 sem aceitá-lo como documento ACT4."""
    candidate = (value or "").strip().upper()
    if _ACT2_PATTERN.fullmatch(candidate):
        return LegacyLicenseVersion.ACT2
    if _ACT3_PATTERN.fullmatch(candidate):
        return LegacyLicenseVersion.ACT3
    return None
