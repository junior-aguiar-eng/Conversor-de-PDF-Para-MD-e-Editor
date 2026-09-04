"""CLI administrativa para rotação, backup e recuperação das chaves ACT4."""

from __future__ import annotations

import argparse
import base64
import json
import os
import uuid
from pathlib import Path

from admin_licensing import AdminDatabase, EncryptedSigningKeyStore


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Segurança operacional das chaves NexoJuris")
    parser.add_argument("--environment", choices=("test", "production"), required=True)
    parser.add_argument("--key-store", type=Path)
    parser.add_argument("--audit-database", type=Path)
    parser.add_argument("--admin-user-id")
    subparsers = parser.add_subparsers(dest="command", required=True)

    initialize = subparsers.add_parser("initialize")
    initialize.add_argument("--purpose", choices=("license",), required=True)
    initialize.add_argument("--key-id", required=True)
    initialize.add_argument("--password-env", required=True)

    rotate = subparsers.add_parser("rotate")
    rotate.add_argument("--purpose", choices=("license",), required=True)
    rotate.add_argument("--key-id", required=True)
    rotate.add_argument("--password-env", required=True)
    rotate.add_argument("--confirmation", required=True)

    compromise = subparsers.add_parser("compromise")
    compromise.add_argument("--key-id", required=True)
    compromise.add_argument("--confirmation", required=True)

    backup = subparsers.add_parser("backup")
    backup.add_argument("--destination", type=Path, required=True)
    backup.add_argument("--password-env", required=True)

    restore = subparsers.add_parser("restore")
    restore.add_argument("--source", type=Path, required=True)
    restore.add_argument("--password-env", required=True)
    restore.add_argument("--confirmation", required=True)

    backup_database = subparsers.add_parser("backup-database")
    backup_database.add_argument("--database", type=Path, required=True)
    backup_database.add_argument("--destination", type=Path, required=True)
    backup_database.add_argument("--password-env", required=True)

    restore_database = subparsers.add_parser("restore-database")
    restore_database.add_argument("--database", type=Path, required=True)
    restore_database.add_argument("--source", type=Path, required=True)
    restore_database.add_argument("--password-env", required=True)
    restore_database.add_argument("--confirmation", required=True)

    public_keys = subparsers.add_parser("export-public-keys")
    public_keys.add_argument("--purpose", choices=("license",), required=True)
    public_keys.add_argument("--destination", type=Path, required=True)
    return parser.parse_args()


def _secret(variable: str) -> str:
    value = os.environ.get(variable, "")
    if not value:
        raise RuntimeError(f"A variável de ambiente {variable} é obrigatória.")
    return value


def _audit(arguments: argparse.Namespace, action: str, entity_id: str, details: dict[str, object]) -> None:
    if not arguments.audit_database:
        return
    database = AdminDatabase(arguments.audit_database, environment=arguments.environment)
    with database.transaction() as connection:
        database.append_audit(
            connection,
            event_id=f"EVT-{uuid.uuid4().hex.upper()}",
            action=action,
            entity_type="signing_key",
            entity_id=entity_id,
            admin_user_id=arguments.admin_user_id,
            details=details,
        )


def main() -> None:
    arguments = parse_args()
    if arguments.command == "backup-database":
        database = AdminDatabase(arguments.database, environment=arguments.environment)
        database.create_encrypted_backup(
            arguments.destination,
            _secret(arguments.password_env),
            admin_user_id=arguments.admin_user_id,
        )
        return
    if arguments.command == "restore-database":
        database = AdminDatabase(arguments.database, environment=arguments.environment)
        database.restore_encrypted_backup(
            arguments.source,
            _secret(arguments.password_env),
            confirmation=arguments.confirmation,
            admin_user_id=arguments.admin_user_id,
        )
        return
    if not arguments.key_store:
        raise RuntimeError("--key-store é obrigatório para operações com chaves.")
    store = EncryptedSigningKeyStore(arguments.key_store, environment=arguments.environment)
    if arguments.command == "initialize":
        store.generate(arguments.key_id, _secret(arguments.password_env), purpose=arguments.purpose)
        _audit(arguments, "signing_key.created", arguments.key_id, {"purpose": arguments.purpose})
    elif arguments.command == "rotate":
        store.rotate(
            arguments.purpose,
            arguments.key_id,
            _secret(arguments.password_env),
            confirmation=arguments.confirmation,
        )
        _audit(arguments, "signing_key.rotated", arguments.key_id, {"purpose": arguments.purpose})
    elif arguments.command == "compromise":
        store.mark_compromised(arguments.key_id, confirmation=arguments.confirmation)
        _audit(arguments, "signing_key.compromised", arguments.key_id, {})
    elif arguments.command == "backup":
        target = store.export_offline_backup(arguments.destination, _secret(arguments.password_env))
        _audit(arguments, "signing_key.backup_created", target.name, {})
    elif arguments.command == "restore":
        store.restore_offline_backup(
            arguments.source,
            _secret(arguments.password_env),
            confirmation=arguments.confirmation,
        )
        _audit(arguments, "signing_key.backup_restored", arguments.source.name, {})
    else:
        keys = {
            key_id: base64.b64encode(public_key).decode("ascii")
            for key_id, public_key in store.trusted_public_keys(arguments.purpose).items()
        }
        arguments.destination.parent.mkdir(parents=True, exist_ok=True)
        arguments.destination.write_text(
            json.dumps(
                {"environment": arguments.environment, "purpose": arguments.purpose, "keys": keys},
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )


if __name__ == "__main__":
    main()
