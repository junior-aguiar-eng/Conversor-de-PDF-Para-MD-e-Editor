"""Primitivas de segurança operacional para o ambiente administrativo."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import struct
import tempfile
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

_ENVIRONMENTS = frozenset({"test", "production"})
_KEY_ID = re.compile(r"(?:license|lease)-[a-z0-9](?:[a-z0-9._-]{0,54}[a-z0-9])?")
_BACKUP_MAGIC = b"NXJ-KEY-BACKUP\x01"
_PASSWORD_PREFIX = "scrypt-v1"
_SCRYPT_N = 2**15
_SCRYPT_R = 8
_SCRYPT_P = 1


class OperationalSecurityError(RuntimeError):
    pass


def _password_bytes(password: str | bytes, *, label: str = "senha") -> bytes:
    encoded = password.encode("utf-8") if isinstance(password, str) else password
    if not isinstance(encoded, bytes) or len(encoded) < 12:
        raise ValueError(f"A {label} deve possuir ao menos 12 caracteres.")
    return encoded


def hash_admin_password(password: str) -> str:
    """Produz hash com salt individual; senhas administrativas nunca são armazenadas."""
    encoded = _password_bytes(password, label="senha administrativa")
    salt = os.urandom(16)
    derived = Scrypt(salt=salt, length=32, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P).derive(encoded)
    return "$".join(
        (
            _PASSWORD_PREFIX,
            str(_SCRYPT_N),
            str(_SCRYPT_R),
            str(_SCRYPT_P),
            base64.urlsafe_b64encode(salt).decode("ascii"),
            base64.urlsafe_b64encode(derived).decode("ascii"),
        )
    )


def verify_admin_password(password: str, encoded_hash: str | None) -> bool:
    if not encoded_hash:
        return False
    try:
        prefix, n_text, r_text, p_text, salt_text, expected_text = encoded_hash.split("$")
        if prefix != _PASSWORD_PREFIX:
            return False
        if (int(n_text), int(r_text), int(p_text)) != (_SCRYPT_N, _SCRYPT_R, _SCRYPT_P):
            return False
        salt = base64.urlsafe_b64decode(salt_text.encode("ascii"))
        expected = base64.urlsafe_b64decode(expected_text.encode("ascii"))
        actual = Scrypt(
            salt=salt,
            length=len(expected),
            n=int(n_text),
            r=int(r_text),
            p=int(p_text),
        ).derive(password.encode("utf-8"))
        return hmac.compare_digest(actual, expected)
    except (TypeError, ValueError, UnicodeEncodeError):
        return False


def generate_totp_secret() -> str:
    return base64.b32encode(os.urandom(20)).decode("ascii").rstrip("=")


def totp_code(secret: str, *, at: datetime | None = None, step_seconds: int = 30) -> str:
    instant = datetime.now(UTC) if at is None else at
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise ValueError("O horário TOTP deve possuir fuso horário.")
    normalized = secret.strip().replace(" ", "").upper()
    padding = "=" * (-len(normalized) % 8)
    try:
        key = base64.b32decode(normalized + padding, casefold=True)
    except ValueError as error:
        raise ValueError("O segredo TOTP é inválido.") from error
    if len(key) < 16:
        raise ValueError("O segredo TOTP deve possuir ao menos 128 bits.")
    counter = int(instant.timestamp()) // step_seconds
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return f"{value % 1_000_000:06d}"


def verify_totp(secret: str, code: str, *, at: datetime | None = None, window: int = 1) -> bool:
    if not re.fullmatch(r"[0-9]{6}", code or ""):
        return False
    instant = datetime.now(UTC) if at is None else at
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise ValueError("O horário TOTP deve possuir fuso horário.")
    return any(
        hmac.compare_digest(totp_code(secret, at=datetime.fromtimestamp(instant.timestamp() + offset * 30, UTC)), code)
        for offset in range(-window, window + 1)
    )


def _canonical(value: Mapping[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as temporary:
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.chmod(temporary_name, 0o600)
        os.replace(temporary_name, path)
    finally:
        Path(temporary_name).unlink(missing_ok=True)


class EncryptedSigningKeyStore:
    """Chaveiro criptografado, rotacionável e vinculado a test ou production."""

    def __init__(self, root: str | Path, *, environment: str) -> None:
        if environment not in _ENVIRONMENTS:
            raise ValueError("O ambiente deve ser test ou production.")
        self.root = Path(root).expanduser().resolve()
        self.environment = environment
        self.manifest_path = self.root / "key-manifest.json"
        self.root.mkdir(parents=True, exist_ok=True)
        if self.manifest_path.exists():
            manifest = self._manifest()
            if manifest["environment"] != environment:
                raise OperationalSecurityError("O chaveiro pertence a outro ambiente.")

    def generate(self, key_id: str, password: str | bytes, *, purpose: str) -> Ed25519PublicKey:
        self._validate_identity(key_id, purpose)
        manifest = self._manifest()
        if key_id in manifest["keys"]:
            raise OperationalSecurityError("O key_id já existe no chaveiro.")
        if any(item["purpose"] == purpose and item["status"] == "active" for item in manifest["keys"].values()):
            raise OperationalSecurityError("Já existe uma chave ativa para esta finalidade; use rotação.")
        return self._create_key(manifest, key_id, password, purpose=purpose)

    def rotate(
        self,
        purpose: str,
        new_key_id: str,
        password: str | bytes,
        *,
        confirmation: str,
    ) -> Ed25519PublicKey:
        expected = f"ROTACIONAR:{purpose}:{self.environment}"
        if confirmation != expected:
            raise PermissionError(f"Confirmação obrigatória: {expected}")
        self._validate_identity(new_key_id, purpose)
        manifest = self._manifest()
        if new_key_id in manifest["keys"]:
            raise OperationalSecurityError("O key_id já existe no chaveiro.")
        timestamp = self._now()
        for item in manifest["keys"].values():
            if item["purpose"] == purpose and item["status"] == "active":
                item["status"] = "retired"
                item["retired_at"] = timestamp
        return self._create_key(manifest, new_key_id, password, purpose=purpose)

    def mark_compromised(self, key_id: str, *, confirmation: str) -> None:
        expected = f"COMPROMETIDA:{key_id}"
        if confirmation != expected:
            raise PermissionError(f"Confirmação obrigatória: {expected}")
        manifest = self._manifest()
        try:
            item = manifest["keys"][key_id]
        except KeyError as error:
            raise OperationalSecurityError("Chave não encontrada.") from error
        item["status"] = "compromised"
        item["compromised_at"] = self._now()
        self._save_manifest(manifest)

    def active_key_id(self, purpose: str) -> str:
        active = [
            key_id
            for key_id, item in self._manifest()["keys"].items()
            if item["purpose"] == purpose and item["status"] == "active"
        ]
        if len(active) != 1:
            raise OperationalSecurityError("O chaveiro não possui exatamente uma chave ativa para esta finalidade.")
        return active[0]

    def load_private_key(self, key_id: str, password: str | bytes) -> Ed25519PrivateKey:
        manifest = self._manifest()
        try:
            item = manifest["keys"][key_id]
        except KeyError as error:
            raise OperationalSecurityError("Chave não encontrada.") from error
        if item["status"] == "compromised":
            raise OperationalSecurityError("A chave está marcada como comprometida.")
        try:
            loaded = serialization.load_pem_private_key(
                (self.root / item["file_name"]).read_bytes(),
                password=_password_bytes(password, label="senha da chave"),
            )
        except (OSError, TypeError, ValueError) as error:
            raise OperationalSecurityError("Não foi possível abrir a chave privada.") from error
        if not isinstance(loaded, Ed25519PrivateKey):
            raise OperationalSecurityError("A chave privada não é Ed25519.")
        return loaded

    def trusted_public_keys(self, purpose: str, *, include_compromised: bool = False) -> dict[str, bytes]:
        result: dict[str, bytes] = {}
        for key_id, item in self._manifest()["keys"].items():
            if item["purpose"] != purpose or (item["status"] == "compromised" and not include_compromised):
                continue
            result[key_id] = base64.b64decode(item["public_key_b64"], validate=True)
        return result

    def export_offline_backup(self, destination: str | Path, password: str) -> Path:
        manifest = self._manifest()
        files = {
            item["file_name"]: base64.b64encode((self.root / item["file_name"]).read_bytes()).decode("ascii")
            for item in manifest["keys"].values()
        }
        payload = _canonical({"manifest": manifest, "files": files})
        salt, nonce = os.urandom(16), os.urandom(12)
        key = Scrypt(salt=salt, length=32, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P).derive(
            _password_bytes(password, label="senha do backup")
        )
        aad = _BACKUP_MAGIC + self.environment.encode("ascii")
        target = Path(destination).expanduser().resolve()
        _atomic_write(target, _BACKUP_MAGIC + salt + nonce + AESGCM(key).encrypt(nonce, payload, aad))
        return target

    def restore_offline_backup(self, source: str | Path, password: str, *, confirmation: str) -> None:
        expected = f"RESTAURAR-CHAVES:{self.environment}"
        if confirmation != expected:
            raise PermissionError(f"Confirmação obrigatória: {expected}")
        encoded = Path(source).expanduser().resolve().read_bytes()
        if len(encoded) < len(_BACKUP_MAGIC) + 44 or not encoded.startswith(_BACKUP_MAGIC):
            raise OperationalSecurityError("Formato de backup de chaves inválido.")
        offset = len(_BACKUP_MAGIC)
        salt, nonce, ciphertext = encoded[offset : offset + 16], encoded[offset + 16 : offset + 28], encoded[offset + 28 :]
        key = Scrypt(salt=salt, length=32, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P).derive(
            _password_bytes(password, label="senha do backup")
        )
        try:
            content = AESGCM(key).decrypt(nonce, ciphertext, _BACKUP_MAGIC + self.environment.encode("ascii"))
            bundle = json.loads(content)
        except (InvalidTag, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise OperationalSecurityError("Senha incorreta, ambiente divergente ou backup adulterado.") from error
        manifest = bundle.get("manifest")
        files = bundle.get("files")
        if not isinstance(manifest, dict) or manifest.get("environment") != self.environment or not isinstance(files, dict):
            raise OperationalSecurityError("Backup de chaves incompatível com este ambiente.")
        for item in manifest.get("keys", {}).values():
            file_name = item.get("file_name", "")
            if Path(file_name).name != file_name or file_name not in files:
                raise OperationalSecurityError("Backup de chaves contém referência inválida.")
            pem = base64.b64decode(files[file_name], validate=True)
            if b"ENCRYPTED PRIVATE KEY" not in pem:
                raise OperationalSecurityError("Backup contém chave privada não criptografada.")
        for file_name, encoded_file in files.items():
            _atomic_write(self.root / file_name, base64.b64decode(encoded_file, validate=True))
        self._save_manifest(manifest)

    def _create_key(
        self,
        manifest: dict[str, Any],
        key_id: str,
        password: str | bytes,
        *,
        purpose: str,
    ) -> Ed25519PublicKey:
        private_key = Ed25519PrivateKey.generate()
        pem = private_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.BestAvailableEncryption(_password_bytes(password, label="senha da chave")),
        )
        file_name = f"{key_id}.pem"
        _atomic_write(self.root / file_name, pem)
        public_bytes = private_key.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )
        manifest["keys"][key_id] = {
            "purpose": purpose,
            "status": "active",
            "file_name": file_name,
            "public_key_b64": base64.b64encode(public_bytes).decode("ascii"),
            "created_at": self._now(),
        }
        self._save_manifest(manifest)
        return private_key.public_key()

    def _manifest(self) -> dict[str, Any]:
        if not self.manifest_path.exists():
            return {"schema": 1, "environment": self.environment, "keys": {}}
        try:
            result = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise OperationalSecurityError("Manifesto de chaves inválido.") from error
        if result.get("schema") != 1 or result.get("environment") != self.environment or not isinstance(result.get("keys"), dict):
            raise OperationalSecurityError("Manifesto de chaves incompatível.")
        return result

    def _save_manifest(self, manifest: Mapping[str, Any]) -> None:
        _atomic_write(self.manifest_path, _canonical(manifest))

    def _validate_identity(self, key_id: str, purpose: str) -> None:
        if purpose not in {"license", "lease"} or not _KEY_ID.fullmatch(key_id) or not key_id.startswith(f"{purpose}-"):
            raise ValueError("key_id ou finalidade inválida.")
        environment_marker = "test" if self.environment == "test" else "prod"
        if environment_marker not in key_id.split("-"):
            raise ValueError(f"O key_id deve identificar explicitamente o ambiente {environment_marker}.")

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


class ActiveEncryptedKeyProvider:
    """Resolve a chave ativa a cada operação, permitindo rotação sem reiniciar o serviço."""

    def __init__(
        self,
        store: EncryptedSigningKeyStore,
        *,
        purpose: str,
        password_provider: Callable[[], str | bytes],
    ) -> None:
        self.store = store
        self.purpose = purpose
        self.password_provider = password_provider

    @property
    def key_id(self) -> str:
        return self.store.active_key_id(self.purpose)

    def __call__(self) -> Ed25519PrivateKey:
        return self.store.load_private_key(self.key_id, self.password_provider())

    def for_key(self, key_id: str) -> Ed25519PrivateKey:
        return self.store.load_private_key(key_id, self.password_provider())
