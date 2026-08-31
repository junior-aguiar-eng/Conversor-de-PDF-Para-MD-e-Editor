"""Ponto de entrada do aplicativo NexoJuris Licenças Admin."""

from __future__ import annotations

import argparse
import getpass
import os
from pathlib import Path

from admin_license_bridge import AdminLicenseBridge
from admin_licensing import (
    ActiveEncryptedKeyProvider,
    AdminDatabase,
    AdminLicenseError,
    AdminLicenseService,
    EncryptedSigningKeyStore,
)

ROOT = Path(__file__).resolve().parent
DEFAULT_ADMIN_ROOT = Path.home() / "AppData" / "Local" / "NexoJuris" / "LicencasAdmin"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="NexoJuris Licenças Admin")
    parser.add_argument("--database", type=Path, default=DEFAULT_ADMIN_ROOT / "licencas-admin.db")
    parser.add_argument("--private-key", type=Path, default=ROOT / ".secrets" / "nexojuris_ed25519_private.pem")
    parser.add_argument("--key-id", default="license-main-2026-01")
    parser.add_argument("--environment", choices=("local", "test", "production"), required=True)
    parser.add_argument("--key-store", type=Path)
    parser.add_argument("--admin-password-env", default="NEXOJURIS_ADMIN_PASSWORD")
    return parser.parse_args()


def _unavailable_key() -> None:
    raise AdminLicenseError("Informe a senha da chave privada ao exportar a licença.")


def _required_secret(variable: str) -> str:
    value = os.environ.get(variable, "")
    if not value:
        raise RuntimeError(f"A variável de ambiente {variable} é obrigatória.")
    return value


def main() -> None:
    import webview

    arguments = parse_args()
    database = AdminDatabase(arguments.database, environment=arguments.environment)
    with database.read() as connection:
        admin = connection.execute(
            "SELECT admin_user_id FROM admin_users WHERE active = 1 ORDER BY rowid LIMIT 1"
        ).fetchone()
    signing_key_store = None
    private_key_provider = _unavailable_key
    key_id = arguments.key_id
    if arguments.key_store:
        signing_key_store = EncryptedSigningKeyStore(arguments.key_store, environment=arguments.environment)
        active_provider = ActiveEncryptedKeyProvider(
            signing_key_store,
            purpose="license",
            password_provider=_unavailable_key,
        )
        key_id = active_provider.key_id
        private_key_provider = active_provider
    elif arguments.environment == "production":
        raise RuntimeError("O Admin de produção exige --key-store.")
    service = AdminLicenseService(database, key_id=key_id, private_key_provider=private_key_provider)
    admin_password = _required_secret(arguments.admin_password_env)
    if admin:
        admin_user_id = str(admin[0])
        with database.read() as connection:
            admin_row = connection.execute(
                "SELECT username, password_hash FROM admin_users WHERE admin_user_id = ?", (admin_user_id,)
            ).fetchone()
        if not admin_row["password_hash"]:
            service.set_admin_password(admin_user_id, admin_password, acting_admin_user_id=admin_user_id)
        service.authenticate_admin(str(admin_row["username"]), admin_password)
    else:
        admin_user_id = service.create_admin_user(
            getpass.getuser(), getpass.getuser(), role="owner", password=admin_password
        )
    bridge = AdminLicenseBridge(
        service,
        admin_user_id=admin_user_id,
        private_key_path=arguments.private_key if signing_key_store is None else None,
        signing_key_store=signing_key_store,
    )
    window = webview.create_window(
        "NexoJuris Licenças Admin",
        str(ROOT / "admin_web" / "index.html"),
        js_api=bridge,
        width=1280,
        height=820,
        min_size=(960, 640),
    )
    bridge.set_window(window)
    webview.start(debug=False)


if __name__ == "__main__":
    main()
