"""Envelope canônico e assinado para leases temporários."""

from __future__ import annotations

import base64
import binascii
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

LEASE_SCHEMA = "nexojuris-lease/v1"
_TIMESTAMP = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")
_LICENSE_ID = re.compile(r"LIC-[0-9]{4}-[0-9]{6,12}")
_KEY_ID = re.compile(r"lease-[a-z0-9](?:[a-z0-9._-]{0,54}[a-z0-9])?")
_NONCE = re.compile(r"[A-Za-z0-9_-]{22,86}")
_SIGNATURE = re.compile(r"[A-Za-z0-9_-]{86}")
_STATUSES = frozenset({"active", "suspended", "revoked", "expired", "machine_mismatch", "not_found"})
_PAYLOAD_FIELDS = frozenset(
    {
        "schema",
        "key_id",
        "license_id",
        "status",
        "server_time",
        "entitlement_expires_at",
        "lease_expires_at",
        "nonce",
    }
)
_DOCUMENT_FIELDS = _PAYLOAD_FIELDS | {"signature"}


class LeaseFormatError(ValueError):
    pass


class LeaseSignatureError(LeaseFormatError):
    pass


def validate_nonce(value: object) -> str:
    if not isinstance(value, str) or not _NONCE.fullmatch(value):
        raise LeaseFormatError("Nonce do lease possui formato inválido.")
    try:
        padding = "=" * (-len(value) % 4)
        decoded = base64.b64decode(value + padding, altchars=b"-_", validate=True)
    except (binascii.Error, ValueError) as error:
        raise LeaseFormatError("Nonce do lease não contém Base64URL válido.") from error
    if len(decoded) < 16 or base64.urlsafe_b64encode(decoded).rstrip(b"=").decode("ascii") != value:
        raise LeaseFormatError("Nonce do lease não é canônico ou possui baixa entropia.")
    return value


def parse_time(value: object, field: str) -> datetime:
    if not isinstance(value, str) or not _TIMESTAMP.fullmatch(value):
        raise LeaseFormatError(f"{field} deve usar UTC no formato YYYY-MM-DDTHH:MM:SSZ.")
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError as error:
        raise LeaseFormatError(f"{field} contém uma data inválida.") from error


def _encode_signature(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode_signature(value: object) -> bytes:
    if not isinstance(value, str) or not _SIGNATURE.fullmatch(value):
        raise LeaseFormatError("A assinatura do lease possui formato inválido.")
    try:
        padding = "=" * (-len(value) % 4)
        decoded = base64.b64decode(value + padding, altchars=b"-_", validate=True)
    except (binascii.Error, ValueError) as error:
        raise LeaseFormatError("A assinatura do lease não contém Base64URL válido.") from error
    if len(decoded) != 64 or _encode_signature(decoded) != value:
        raise LeaseFormatError("A assinatura do lease não é canônica.")
    return decoded


@dataclass(frozen=True, slots=True)
class LeasePayload:
    key_id: str
    license_id: str
    status: str
    server_time: str
    entitlement_expires_at: str
    lease_expires_at: str
    nonce: str
    schema: str = LEASE_SCHEMA

    def __post_init__(self) -> None:
        if self.schema != LEASE_SCHEMA:
            raise LeaseFormatError("Schema de lease não suportado.")
        if not _KEY_ID.fullmatch(self.key_id):
            raise LeaseFormatError("key_id do lease possui formato inválido.")
        if not _LICENSE_ID.fullmatch(self.license_id):
            raise LeaseFormatError("license_id do lease possui formato inválido.")
        if self.status not in _STATUSES:
            raise LeaseFormatError("Status do lease desconhecido.")
        server_time = parse_time(self.server_time, "server_time")
        entitlement = parse_time(self.entitlement_expires_at, "entitlement_expires_at")
        lease_expiration = parse_time(self.lease_expires_at, "lease_expires_at")
        validate_nonce(self.nonce)
        if self.status == "active":
            if lease_expiration <= server_time:
                raise LeaseFormatError("Lease ativo deve expirar depois do horário do servidor.")
            if lease_expiration > entitlement:
                raise LeaseFormatError("Lease não pode ultrapassar a validade comercial.")
        elif lease_expiration != server_time:
            raise LeaseFormatError("Lease não ativo deve encerrar no horário do servidor.")

    def to_mapping(self) -> dict[str, str]:
        return {
            "schema": self.schema,
            "key_id": self.key_id,
            "license_id": self.license_id,
            "status": self.status,
            "server_time": self.server_time,
            "entitlement_expires_at": self.entitlement_expires_at,
            "lease_expires_at": self.lease_expires_at,
            "nonce": self.nonce,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> LeasePayload:
        if not isinstance(value, Mapping) or frozenset(value) != _PAYLOAD_FIELDS:
            raise LeaseFormatError("Payload do lease possui campos inválidos.")
        return cls(**{field: value[field] for field in _PAYLOAD_FIELDS})


def canonicalize_lease(payload: LeasePayload | Mapping[str, Any]) -> bytes:
    normalized = payload if isinstance(payload, LeasePayload) else LeasePayload.from_mapping(payload)
    return json.dumps(
        normalized.to_mapping(),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def issue_lease(payload: LeasePayload, private_key: Ed25519PrivateKey) -> dict[str, str]:
    if not isinstance(private_key, Ed25519PrivateKey):
        raise TypeError("A chave de lease deve ser Ed25519.")
    signature = _encode_signature(private_key.sign(canonicalize_lease(payload)))
    return {**payload.to_mapping(), "signature": signature}


def verify_lease(
    document: Mapping[str, Any],
    public_keys: Mapping[str, Ed25519PublicKey | bytes],
    *,
    expected_nonce: str,
    expected_license_id: str,
) -> LeasePayload:
    if not isinstance(document, Mapping) or frozenset(document) != _DOCUMENT_FIELDS:
        raise LeaseFormatError("Documento de lease possui campos inválidos.")
    payload = LeasePayload.from_mapping({field: document[field] for field in _PAYLOAD_FIELDS})
    if payload.nonce != expected_nonce:
        raise LeaseFormatError("O nonce da resposta não corresponde à solicitação.")
    if payload.license_id != expected_license_id:
        raise LeaseFormatError("O lease pertence a outra licença.")
    try:
        public_key = public_keys[payload.key_id]
    except KeyError as error:
        raise LeaseSignatureError("Chave pública de lease desconhecida.") from error
    if isinstance(public_key, bytes):
        try:
            public_key = Ed25519PublicKey.from_public_bytes(public_key)
        except ValueError as error:
            raise LeaseSignatureError("Chave pública de lease inválida.") from error
    if not isinstance(public_key, Ed25519PublicKey):
        raise LeaseSignatureError("Chave pública de lease inválida.")
    try:
        public_key.verify(_decode_signature(document["signature"]), canonicalize_lease(payload))
    except InvalidSignature as error:
        raise LeaseSignatureError("Assinatura do lease inválida.") from error
    return payload
