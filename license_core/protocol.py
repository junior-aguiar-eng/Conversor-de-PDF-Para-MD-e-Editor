"""Protocolo criptográfico versionado para licenças NexoJuris ACT4."""

from __future__ import annotations

import base64
import binascii
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from .errors import (
    LicenseExpiredError,
    LicenseFormatError,
    LicenseNotYetValidError,
    MachineMismatchError,
    SignatureVerificationError,
    UnknownKeyError,
    UnsupportedSchemaError,
)

LICENSE_SCHEMA = "nexojuris-license/v4"
LICENSE_FILE_FORMAT = "nexojuris-license-file/v1"
LICENSE_FILE_SUFFIX = ".nxjlic"
MAX_LICENSE_FILE_BYTES = 64 * 1024

_PAYLOAD_FIELDS = frozenset(
    {
        "schema",
        "key_id",
        "license_id",
        "machine_id",
        "issued_at",
        "not_before",
        "expires_at",
        "validation_mode",
        "max_offline_days",
        "features",
        "customer_reference",
    }
)
_DOCUMENT_FIELDS = frozenset({"format", "payload", "signature"})
_KEY_ID_PATTERN = re.compile(r"[a-z0-9](?:[a-z0-9._-]{1,62}[a-z0-9])?")
_LICENSE_KEY_ID_PATTERN = re.compile(r"license-[a-z0-9](?:[a-z0-9._-]{0,54}[a-z0-9])?")
_LICENSE_ID_PATTERN = re.compile(r"LIC-[0-9]{4}-[0-9]{6,12}")
_MACHINE_ID_PATTERN = re.compile(r"NXJ2-(?:[A-F0-9]{4}-){3}[A-F0-9]{4}")
_CUSTOMER_REFERENCE_PATTERN = re.compile(r"[A-Z0-9](?:[A-Z0-9._-]{0,62}[A-Z0-9])?")
_TIMESTAMP_PATTERN = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")
_SIGNATURE_PATTERN = re.compile(r"[A-Za-z0-9_-]{86}")
_ALLOWED_FEATURES = frozenset({"converter", "ocr", "reader"})
_VALIDATION_MODES = frozenset({"offline", "hybrid"})


def _parse_timestamp(value: str, field_name: str) -> datetime:
    if not isinstance(value, str) or not _TIMESTAMP_PATTERN.fullmatch(value):
        raise LicenseFormatError(f"{field_name} deve usar UTC no formato YYYY-MM-DDTHH:MM:SSZ.")
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError as error:
        raise LicenseFormatError(f"{field_name} contém uma data inexistente.") from error


def _require_pattern(value: object, pattern: re.Pattern[str], field_name: str) -> str:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise LicenseFormatError(f"{field_name} possui formato inválido.")
    return value


def _reject_bool_integer(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise LicenseFormatError(f"{field_name} deve ser inteiro.")
    return value


@dataclass(frozen=True, slots=True)
class LicensePayload:
    """Payload estrito e integralmente coberto pela assinatura Ed25519."""

    key_id: str
    license_id: str
    machine_id: str
    issued_at: str
    not_before: str
    expires_at: str
    validation_mode: str
    max_offline_days: int
    features: tuple[str, ...]
    customer_reference: str
    schema: str = LICENSE_SCHEMA

    def __post_init__(self) -> None:
        if self.schema != LICENSE_SCHEMA:
            raise UnsupportedSchemaError(f"Schema não suportado: {self.schema!r}.")
        _require_pattern(self.key_id, _LICENSE_KEY_ID_PATTERN, "key_id")
        _require_pattern(self.license_id, _LICENSE_ID_PATTERN, "license_id")
        _require_pattern(self.machine_id, _MACHINE_ID_PATTERN, "machine_id")
        _require_pattern(self.customer_reference, _CUSTOMER_REFERENCE_PATTERN, "customer_reference")

        issued = _parse_timestamp(self.issued_at, "issued_at")
        starts = _parse_timestamp(self.not_before, "not_before")
        expires = _parse_timestamp(self.expires_at, "expires_at")
        if issued > starts:
            raise LicenseFormatError("issued_at não pode ser posterior a not_before.")
        if starts >= expires:
            raise LicenseFormatError("not_before deve ser anterior a expires_at.")

        if self.validation_mode not in _VALIDATION_MODES:
            raise LicenseFormatError("validation_mode deve ser 'offline' ou 'hybrid'.")
        offline_days = _reject_bool_integer(self.max_offline_days, "max_offline_days")
        if self.validation_mode == "offline" and offline_days != 0:
            raise LicenseFormatError("Licenças offline devem usar max_offline_days igual a 0.")
        if self.validation_mode == "hybrid" and not 1 <= offline_days <= 30:
            raise LicenseFormatError("Licenças hybrid devem usar max_offline_days entre 1 e 30.")

        if not isinstance(self.features, tuple) or not self.features:
            raise LicenseFormatError("features deve conter ao menos uma funcionalidade.")
        if any(not isinstance(feature, str) or feature not in _ALLOWED_FEATURES for feature in self.features):
            raise LicenseFormatError("features contém funcionalidade desconhecida.")
        if tuple(sorted(set(self.features))) != self.features:
            raise LicenseFormatError("features deve ser única e ordenada.")

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> LicensePayload:
        if not isinstance(value, Mapping):
            raise LicenseFormatError("payload deve ser um objeto JSON.")
        fields = frozenset(value)
        if fields != _PAYLOAD_FIELDS:
            missing = sorted(_PAYLOAD_FIELDS - fields)
            extra = sorted(fields - _PAYLOAD_FIELDS)
            raise LicenseFormatError(f"Campos inválidos no payload; ausentes={missing}, extras={extra}.")
        features = value["features"]
        if not isinstance(features, list):
            raise LicenseFormatError("features deve ser uma lista JSON.")
        return cls(
            schema=value["schema"],
            key_id=value["key_id"],
            license_id=value["license_id"],
            machine_id=value["machine_id"],
            issued_at=value["issued_at"],
            not_before=value["not_before"],
            expires_at=value["expires_at"],
            validation_mode=value["validation_mode"],
            max_offline_days=value["max_offline_days"],
            features=tuple(features),
            customer_reference=value["customer_reference"],
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "key_id": self.key_id,
            "license_id": self.license_id,
            "machine_id": self.machine_id,
            "issued_at": self.issued_at,
            "not_before": self.not_before,
            "expires_at": self.expires_at,
            "validation_mode": self.validation_mode,
            "max_offline_days": self.max_offline_days,
            "features": list(self.features),
            "customer_reference": self.customer_reference,
        }


def canonicalize_payload(payload: LicensePayload | Mapping[str, Any]) -> bytes:
    """Serializa os tipos restritos do ACT4 em JSON UTF-8 determinístico."""
    normalized = payload if isinstance(payload, LicensePayload) else LicensePayload.from_mapping(payload)
    return json.dumps(
        normalized.to_mapping(),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _encode_signature(signature: bytes) -> str:
    return base64.urlsafe_b64encode(signature).rstrip(b"=").decode("ascii")


def _decode_signature(value: object) -> bytes:
    if not isinstance(value, str) or not _SIGNATURE_PATTERN.fullmatch(value):
        raise LicenseFormatError("signature não é uma assinatura Ed25519 Base64URL canônica.")
    try:
        decoded = base64.b64decode(value + "==", altchars=b"-_", validate=True)
    except (binascii.Error, ValueError) as error:
        raise LicenseFormatError("signature contém Base64URL inválido.") from error
    if len(decoded) != 64 or _encode_signature(decoded) != value:
        raise LicenseFormatError("signature não é uma assinatura Ed25519 canônica.")
    return decoded


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise LicenseFormatError(f"Campo JSON duplicado: {key}.")
        result[key] = value
    return result


def issue_license(payload: LicensePayload, private_key: Ed25519PrivateKey) -> bytes:
    """Assina e devolve o conteúdo canônico de um arquivo ``.nxjlic``."""
    if not isinstance(private_key, Ed25519PrivateKey):
        raise TypeError("private_key deve ser Ed25519PrivateKey.")
    signature = private_key.sign(canonicalize_payload(payload))
    document = {
        "format": LICENSE_FILE_FORMAT,
        "payload": payload.to_mapping(),
        "signature": _encode_signature(signature),
    }
    return (
        json.dumps(document, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")


def parse_license(document: bytes | str) -> tuple[LicensePayload, bytes]:
    """Analisa um documento ACT4 sem ainda confiar em seu conteúdo."""
    if isinstance(document, str):
        encoded = document.encode("utf-8")
    elif isinstance(document, bytes):
        encoded = document
    else:
        raise TypeError("document deve ser bytes ou str.")
    if not encoded or len(encoded) > MAX_LICENSE_FILE_BYTES:
        raise LicenseFormatError("Arquivo de licença vazio ou acima do limite permitido.")
    try:
        text = encoded.decode("utf-8")
    except UnicodeDecodeError as error:
        raise LicenseFormatError("Arquivo de licença não está em UTF-8.") from error
    try:
        value = json.loads(text, object_pairs_hook=_reject_duplicate_keys)
    except LicenseFormatError:
        raise
    except (json.JSONDecodeError, TypeError, ValueError) as error:
        raise LicenseFormatError("Arquivo de licença não contém JSON válido.") from error
    if not isinstance(value, dict) or frozenset(value) != _DOCUMENT_FIELDS:
        raise LicenseFormatError("Envelope da licença possui campos inválidos.")
    if value["format"] != LICENSE_FILE_FORMAT:
        raise UnsupportedSchemaError(f"Formato de arquivo não suportado: {value['format']!r}.")
    return LicensePayload.from_mapping(value["payload"]), _decode_signature(value["signature"])


class PublicKeyRing:
    """Registro explícito de chaves públicas confiáveis, indexadas por ``key_id``."""

    def __init__(self, keys: Mapping[str, Ed25519PublicKey | bytes]) -> None:
        if not keys:
            raise ValueError("O chaveiro público não pode ser vazio.")
        normalized: dict[str, Ed25519PublicKey] = {}
        for key_id, public_key in keys.items():
            _require_pattern(key_id, _KEY_ID_PATTERN, "key_id")
            if isinstance(public_key, bytes):
                try:
                    public_key = Ed25519PublicKey.from_public_bytes(public_key)
                except ValueError as error:
                    raise ValueError(f"Chave pública inválida para {key_id}.") from error
            if not isinstance(public_key, Ed25519PublicKey):
                raise TypeError(f"Chave pública inválida para {key_id}.")
            normalized[key_id] = public_key
        self._keys = normalized

    def get(self, key_id: str) -> Ed25519PublicKey:
        try:
            return self._keys[key_id]
        except KeyError as error:
            raise UnknownKeyError(f"Chave pública desconhecida: {key_id}.") from error


def verify_license(
    document: bytes | str,
    key_ring: PublicKeyRing,
    *,
    expected_machine_id: str | None = None,
    at: datetime | None = None,
    check_time: bool = True,
) -> LicensePayload:
    """Verifica formato, assinatura, máquina e janela temporal de uma ACT4."""
    payload, signature = parse_license(document)
    try:
        key_ring.get(payload.key_id).verify(signature, canonicalize_payload(payload))
    except InvalidSignature as error:
        raise SignatureVerificationError("Assinatura ACT4 inválida.") from error

    if expected_machine_id is not None:
        normalized_machine_id = expected_machine_id.strip().upper()
        if normalized_machine_id != payload.machine_id:
            raise MachineMismatchError("A licença ACT4 pertence a outro computador.")

    if not check_time:
        return payload

    instant = datetime.now(UTC) if at is None else at
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise ValueError("at deve possuir fuso horário.")
    instant = instant.astimezone(UTC)
    starts = _parse_timestamp(payload.not_before, "not_before")
    expires = _parse_timestamp(payload.expires_at, "expires_at")
    if instant < starts:
        raise LicenseNotYetValidError("A licença ACT4 ainda não é válida.")
    if instant >= expires:
        raise LicenseExpiredError("A licença ACT4 está expirada.")
    return payload


def load_license_file(
    path: str | Path,
    key_ring: PublicKeyRing,
    *,
    expected_machine_id: str | None = None,
    at: datetime | None = None,
) -> LicensePayload:
    """Lê e verifica um arquivo com extensão obrigatória ``.nxjlic``."""
    license_path = Path(path)
    if license_path.suffix.lower() != LICENSE_FILE_SUFFIX:
        raise LicenseFormatError(f"A licença deve usar a extensão {LICENSE_FILE_SUFFIX}.")
    try:
        document = license_path.read_bytes()
    except OSError as error:
        raise LicenseFormatError(f"Não foi possível ler a licença: {license_path}.") from error
    return verify_license(
        document,
        key_ring,
        expected_machine_id=expected_machine_id,
        at=at,
    )
