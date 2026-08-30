"""Ponto de entrada HTTPS do Serviço de Licenças NexoJuris."""

from __future__ import annotations

import argparse
import getpass
import hashlib
import os
from pathlib import Path

from admin_licensing import AdminDatabase, AdminLicenseService, EncryptedPrivateKeyProvider
from license_service.api import LicenseServiceApi
from license_service.server import serve_https

ROOT = Path(__file__).resolve().parent


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Serviço HTTPS de licenças NexoJuris")
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--certificate", type=Path, required=True)
    parser.add_argument("--tls-private-key", type=Path, required=True)
    parser.add_argument("--license-private-key", type=Path, required=True)
    parser.add_argument("--lease-private-key", type=Path, required=True)
    parser.add_argument("--license-key-id", default="license-main-2026-01")
    parser.add_argument("--lease-key-id", default="lease-online-2026-01")
    parser.add_argument("--license-key-password-env", default="NEXOJURIS_LICENSE_KEY_PASSWORD")
    parser.add_argument("--lease-key-password-env", default="NEXOJURIS_LEASE_KEY_PASSWORD")
    parser.add_argument("--admin-token-env", default="NEXOJURIS_ADMIN_API_TOKEN")
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
    database = AdminDatabase(arguments.database)
    with database.read() as connection:
        admin = connection.execute(
            "SELECT admin_user_id FROM admin_users WHERE active = 1 ORDER BY rowid LIMIT 1"
        ).fetchone()
    license_provider = EncryptedPrivateKeyProvider(
        arguments.license_private_key,
        lambda: _required_secret(arguments.license_key_password_env),
    )
    lease_provider = EncryptedPrivateKeyProvider(
        arguments.lease_private_key,
        lambda: _required_secret(arguments.lease_key_password_env),
    )
    admin_service = AdminLicenseService(
        database,
        key_id=arguments.license_key_id,
        private_key_provider=license_provider,
    )
    admin_user_id = admin[0] if admin else admin_service.create_admin_user(
        getpass.getuser(), getpass.getuser(), role="owner"
    )
    api = LicenseServiceApi(
        database,
        admin_service,
        lease_key_id=arguments.lease_key_id,
        lease_private_key_provider=lease_provider,
    )
    admin_token = _required_secret(arguments.admin_token_env)
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
    )


if __name__ == "__main__":
    main()
