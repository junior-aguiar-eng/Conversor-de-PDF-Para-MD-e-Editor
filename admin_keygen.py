"""Emissor de contingência para licenças legadas Ed25519 do NexoJuris.

USO EXCLUSIVO DO ADMINISTRADOR / DISTRIBUIDOR.
NÃO DISTRIBUIR ESTE ARQUIVO NEM A CHAVE PRIVADA COM O EXECUTÁVEL DO CLIENTE.
O fluxo administrativo principal reside no pacote ``admin_licensing``.
"""

from __future__ import annotations

import argparse
import base64
import getpass
import os
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from licensing import license_payload

DEFAULT_PRIVATE_KEY_PATH = Path(__file__).resolve().parent / ".secrets" / "nexojuris_ed25519_private.pem"


def _password_bytes(password: str | bytes | None) -> bytes | None:
    if isinstance(password, str):
        password = password.encode("utf-8")
    return password or None


def generate_keypair(private_key_path: Path, password: str | bytes | None = None) -> str:
    """Cria a chave privada, opcionalmente criptografada, e retorna a chave pública."""
    path = private_key_path.expanduser().resolve()
    if path.exists():
        raise FileExistsError(f"A chave privada já existe em: {path}")

    private_key = Ed25519PrivateKey.generate()
    password_bytes = _password_bytes(password)
    encryption = serialization.BestAvailableEncryption(password_bytes) if password_bytes else serialization.NoEncryption()
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=encryption,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(private_pem)

    public_bytes = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return base64.b64encode(public_bytes).decode("ascii")


def load_private_key(private_key_path: Path, password: str | bytes | None = None) -> Ed25519PrivateKey:
    path = private_key_path.expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Chave privada não encontrada: {path}")

    loaded = serialization.load_pem_private_key(path.read_bytes(), password=_password_bytes(password))
    if not isinstance(loaded, Ed25519PrivateKey):
        raise TypeError("O arquivo informado não contém uma chave privada Ed25519.")
    return loaded


def generate_activation_key(machine_id: str, private_key: Ed25519PrivateKey, key_version: int = 2) -> str:
    """Assina um Machine ID (NXJ- ou NXJ2-) e retorna um token ACT2 ou ACT3 versionado."""
    mid = machine_id.strip().upper()
    version = 3 if (key_version == 3 or mid.startswith("NXJ2-")) else 2
    prefix = "ACT3-01-" if version == 3 else "ACT2-01-"
    signature = private_key.sign(license_payload(mid, version=version))
    encoded = base64.b32encode(signature).decode("ascii").rstrip("=")
    grouped = "-".join(encoded[index : index + 8] for index in range(0, len(encoded), 8))
    return f"{prefix}{grouped}"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Emissor administrativo de licenças NexoJuris.")
    parser.add_argument("machine_id", nargs="?", help="Machine ID do cliente (NXJ-XXXX-... ou NXJ2-XXXX-...).")
    parser.add_argument(
        "--private-key",
        type=Path,
        default=DEFAULT_PRIVATE_KEY_PATH,
        help=f"Chave privada Ed25519 (padrão: {DEFAULT_PRIVATE_KEY_PATH}).",
    )
    parser.add_argument(
        "--key-version",
        type=int,
        choices=[2, 3],
        default=3,
        help="Versão da chave de ativação (2=ACT2/NXJ, 3=ACT3/NXJ2; padrão: 3).",
    )
    parser.add_argument(
        "--generate-keypair",
        action="store_true",
        help="Cria uma chave privada administrativa nova e imprime a chave pública correspondente.",
    )
    parser.add_argument(
        "--encrypt-private-key",
        action="store_true",
        help="Ao criar a chave, protege o PEM com senha sem alterar a leitura de PEMs legados.",
    )
    parser.add_argument(
        "--private-key-password-env",
        metavar="VARIAVEL",
        help="Lê a senha do PEM da variável de ambiente indicada, sem expô-la na linha de comando.",
    )
    parser.add_argument(
        "--prompt-private-key-password",
        action="store_true",
        help="Solicita interativamente a senha ao abrir um PEM criptografado.",
    )
    return parser.parse_args(argv)


def _resolve_password(args: argparse.Namespace, *, confirm: bool = False) -> str | None:
    password: str | None = None
    if args.private_key_password_env:
        password = os.environ.get(args.private_key_password_env)
        if not password:
            raise ValueError(f"A variável {args.private_key_password_env} não contém uma senha.")
    elif args.prompt_private_key_password or args.encrypt_private_key:
        password = getpass.getpass("Senha da chave privada: ")
    if confirm and password is not None and not args.private_key_password_env:
        confirmation = getpass.getpass("Confirme a senha: ")
        if password != confirmation:
            raise ValueError("As senhas informadas não coincidem.")
    if confirm and args.encrypt_private_key and not password:
        raise ValueError("A senha da chave privada não pode ser vazia.")
    return password


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    if args.generate_keypair:
        try:
            password = _resolve_password(args, confirm=args.encrypt_private_key) if args.encrypt_private_key else None
            public_key_b64 = generate_keypair(args.private_key, password=password)
        except (FileExistsError, OSError, ValueError) as error:
            print(f"[Erro] {error}")
            raise SystemExit(1) from error
        print(f"Chave privada criada em: {args.private_key.expanduser().resolve()}")
        print(f"Chave pública Base64: {public_key_b64}")
        print("Faça backup seguro da chave privada; sua perda impede novas emissões.")
        return

    machine_id = (args.machine_id or input("Machine ID do cliente: ")).strip().upper()
    if not machine_id:
        print("[Erro] Nenhum Machine ID foi informado.")
        raise SystemExit(1)
    if not (machine_id.startswith("NXJ-") or machine_id.startswith("NXJ2-")):
        print("[Erro] O Machine ID deve começar com 'NXJ-' ou 'NXJ2-'.")
        raise SystemExit(1)

    try:
        password = _resolve_password(args)
        private_key = load_private_key(args.private_key, password=password)
        activation_key = generate_activation_key(machine_id, private_key, key_version=args.key_version)
    except (FileNotFoundError, OSError, TypeError, ValueError) as error:
        print(f"[Erro] {error}")
        raise SystemExit(1) from error

    print(f"Machine ID do Cliente : {machine_id}")
    print(f"Chave de Ativação     : {activation_key}")


if __name__ == "__main__":
    main(sys.argv[1:])
