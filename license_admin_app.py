"""Ponto de entrada do aplicativo NexoJuris Licenças Admin."""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sqlite3
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
    parser.add_argument(
        "--private-key",
        type=Path,
        default=DEFAULT_ADMIN_ROOT / "nexojuris_ed25519_private.pem",
    )
    parser.add_argument("--key-id", default="license-main-2026-01")
    parser.add_argument("--environment", choices=("local", "test", "production"))
    parser.add_argument("--key-store", type=Path)
    parser.add_argument("--admin-password-env", default="NEXOJURIS_ADMIN_PASSWORD")
    parser.add_argument("--release-probe", type=Path)
    arguments = parser.parse_args()
    if not arguments.release_probe and not arguments.environment:
        parser.error("--environment é obrigatório fora do probe de release")
    return arguments


def _unavailable_key() -> None:
    raise AdminLicenseError("Informe a senha da chave privada ao exportar a licença.")


def _required_secret(variable: str) -> str:
    value = os.environ.get(variable, "")
    if not value:
        raise RuntimeError(f"A variável de ambiente {variable} é obrigatória.")
    return value


def _write_release_probe(target: Path) -> None:
    import webview
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    private_key = Ed25519PrivateKey.generate()
    signature = private_key.sign(b"nexojuris-admin-probe")
    private_key.public_key().verify(signature, b"nexojuris-admin-probe")
    with sqlite3.connect(":memory:") as connection:
        sqlite_ok = connection.execute("SELECT 1").fetchone() == (1,)
    checks = {
        "admin_web_assets": all((ROOT / "admin_web" / name).is_file() for name in ("index.html", "app.js", "style.css")),
        "cryptography": True,
        "private_key_not_bundled": not (ROOT / ".secrets").exists(),
        "pywebview": callable(webview.create_window),
        "sqlite": sqlite_ok,
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({"ok": all(checks.values()), "checks": checks}, indent=2), encoding="utf-8")


def main() -> None:
    import webview

    arguments = parse_args()
    if arguments.release_probe:
        _write_release_probe(arguments.release_probe)
        return
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
