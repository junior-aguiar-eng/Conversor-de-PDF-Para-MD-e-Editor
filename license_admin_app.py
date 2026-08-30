"""Ponto de entrada do aplicativo NexoJuris Licenças Admin."""

from __future__ import annotations

import argparse
import getpass
from pathlib import Path

from admin_license_bridge import AdminLicenseBridge
from admin_licensing import AdminDatabase, AdminLicenseError, AdminLicenseService

ROOT = Path(__file__).resolve().parent
DEFAULT_ADMIN_ROOT = Path.home() / "AppData" / "Local" / "NexoJuris" / "LicencasAdmin"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="NexoJuris Licenças Admin")
    parser.add_argument("--database", type=Path, default=DEFAULT_ADMIN_ROOT / "licencas-admin.db")
    parser.add_argument("--private-key", type=Path, default=ROOT / ".secrets" / "nexojuris_ed25519_private.pem")
    parser.add_argument("--key-id", default="license-main-2026-01")
    return parser.parse_args()


def _unavailable_key() -> None:
    raise AdminLicenseError("Informe a senha da chave privada ao exportar a licença.")


def main() -> None:
    import webview

    arguments = parse_args()
    database = AdminDatabase(arguments.database)
    with database.read() as connection:
        admin = connection.execute(
            "SELECT admin_user_id FROM admin_users WHERE active = 1 ORDER BY rowid LIMIT 1"
        ).fetchone()
    service = AdminLicenseService(database, key_id=arguments.key_id, private_key_provider=_unavailable_key)
    admin_user_id = admin[0] if admin else service.create_admin_user(getpass.getuser(), getpass.getuser(), role="owner")
    bridge = AdminLicenseBridge(
        service,
        admin_user_id=admin_user_id,
        private_key_path=arguments.private_key,
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
