"""Ponto de entrada HTTPS do Serviço de Licenças NexoJuris."""

from __future__ import annotations

import argparse
import getpass
import hashlib
import os
from pathlib import Path

from admin_licensing import (
    ActiveEncryptedKeyProvider,
    AdminDatabase,
    AdminLicenseService,
    EncryptedPrivateKeyProvider,
    EncryptedSigningKeyStore,
)
from license_service.api import LicenseServiceApi
from license_service.server import serve_https

ROOT = Path(__file__).resolve().parent


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Serviço HTTPS de licenças NexoJuris")
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--certificate", type=Path, required=True)
    parser.add_argument("--tls-private-key", type=Path, required=True)
    parser.add_argument("--environment", choices=("test", "production"), required=True)
    parser.add_argument("--key-store", type=Path)
    parser.add_argument("--license-private-key", type=Path)
    parser.add_argument("--lease-private-key", type=Path)
    parser.add_argument("--license-key-id", default="license-main-2026-01")
    parser.add_argument("--lease-key-id", default="lease-online-2026-01")
    parser.add_argument("--license-key-password-env", default="NEXOJURIS_LICENSE_KEY_PASSWORD")
    parser.add_argument("--lease-key-password-env", default="NEXOJURIS_LEASE_KEY_PASSWORD")
    parser.add_argument("--tls-key-password-env", default="NEXOJURIS_TLS_KEY_PASSWORD")
    parser.add_argument("--admin-token-env", default="NEXOJURIS_ADMIN_API_TOKEN")
    parser.add_argument("--admin-password-env", default="NEXOJURIS_ADMIN_PASSWORD")
    parser.add_argument("--admin-totp-secret-env", default="NEXOJURIS_ADMIN_TOTP_SECRET")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8443)
    return parser.parse_args()


def _required_secret(variable: str) -> str:
    value = os.environ.get(variable, "")
    if not value:
        raise RuntimeError(f"A variável de ambiente {variable} é obrigatória.")
    return value


def main() -> None:
    arguments = parse_args()
    database = AdminDatabase(arguments.database, environment=arguments.environment)
    with database.read() as connection:
        admin = connection.execute(
            "SELECT admin_user_id FROM admin_users WHERE active = 1 ORDER BY rowid LIMIT 1"
        ).fetchone()
    if arguments.key_store:
        key_store = EncryptedSigningKeyStore(arguments.key_store, environment=arguments.environment)
        license_provider = ActiveEncryptedKeyProvider(
            key_store,
            purpose="license",
            password_provider=lambda: _required_secret(arguments.license_key_password_env),
        )
        lease_provider = ActiveEncryptedKeyProvider(
            key_store,
            purpose="lease",
            password_provider=lambda: _required_secret(arguments.lease_key_password_env),
        )
        license_key_id = license_provider.key_id
        lease_key_id = lease_provider.key_id
    else:
        if arguments.environment == "production":
            raise RuntimeError("O ambiente de produção exige --key-store para rotação e recuperação controladas.")
        if not arguments.license_private_key or not arguments.lease_private_key:
            raise RuntimeError("Informe --key-store ou os dois caminhos de chave privada.")
        license_provider = EncryptedPrivateKeyProvider(
            arguments.license_private_key,
            lambda: _required_secret(arguments.license_key_password_env),
        )
        lease_provider = EncryptedPrivateKeyProvider(
            arguments.lease_private_key,
            lambda: _required_secret(arguments.lease_key_password_env),
        )
        license_key_id = arguments.license_key_id
        lease_key_id = arguments.lease_key_id
    admin_service = AdminLicenseService(
        database,
        key_id=license_key_id,
        private_key_provider=license_provider,
    )
    admin_password = _required_secret(arguments.admin_password_env)
    if admin:
        admin_user_id = str(admin[0])
        with database.read() as connection:
            admin_row = connection.execute(
                "SELECT username, password_hash FROM admin_users WHERE admin_user_id = ?", (admin_user_id,)
            ).fetchone()
            username = str(admin_row["username"])
        if not admin_row["password_hash"]:
            admin_service.set_admin_password(
                admin_user_id,
                admin_password,
                acting_admin_user_id=admin_user_id,
            )
        admin_service.authenticate_admin(username, admin_password)
    else:
        admin_user_id = admin_service.create_admin_user(
            getpass.getuser(), getpass.getuser(), role="owner", password=admin_password
        )
    totp_secret = _required_secret(arguments.admin_totp_secret_env)
    api = LicenseServiceApi(
        database,
        admin_service,
        lease_key_id=lease_key_id,
        lease_private_key_provider=lease_provider,
        require_admin_totp=True,
        admin_totp_secret_provider=lambda requested_admin_id: (
            totp_secret if requested_admin_id == admin_user_id else ""
        ),
    )
    admin_token = _required_secret(arguments.admin_token_env)
    tls_key_password = _required_secret(arguments.tls_key_password_env)
    try:
        tls_key_pem = arguments.tls_private_key.read_bytes()
    except OSError as error:
        raise RuntimeError("Não foi possível ler a chave TLS privada.") from error
    if b"ENCRYPTED PRIVATE KEY" not in tls_key_pem:
        raise RuntimeError("A chave TLS privada deve estar criptografada no disco.")
    token_hash = hashlib.sha256(admin_token.encode("utf-8")).hexdigest()
    with database.read() as connection:
        token_exists = connection.execute(
            "SELECT 1 FROM admin_api_tokens WHERE token_hash = ?", (token_hash,)
        ).fetchone()
    if not token_exists:
        api.register_admin_token(admin_token, label="bootstrap", admin_user_id=admin_user_id)
    serve_https(
        api,
        host=arguments.host,
        port=arguments.port,
        certificate=arguments.certificate,
        private_key=arguments.tls_private_key,
        private_key_password=tls_key_password,
    )


if __name__ == "__main__":
    main()
