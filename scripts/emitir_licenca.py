"""Utilitário de linha de comando para emissão rápida de licenças ACT4 do NexoJuris.

USO EXCLUSIVO DO ADMINISTRADOR.
Permite emitir e assinar arquivos .nxjlic em segundos a partir do Machine ID do cliente.
"""

from __future__ import annotations

import argparse
import base64
import calendar
import getpass
import os
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

# Permite rodar a partir da raiz ou da pasta scripts
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from license_core import LICENSE_FILE_SUFFIX, LicensePayload, issue_license  # noqa: E402
from license_key_config import LICENSE_MAIN_KEY_ID, LICENSE_PUBLIC_KEYS_B64  # noqa: E402

DEFAULT_ADMIN_ROOT = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))) / "NexoJuris" / "LicencasAdmin"
DEFAULT_INSTALLED_KEY = DEFAULT_ADMIN_ROOT / "nexojuris_ed25519_private.pem"
DEFAULT_DEV_KEY = PROJECT_ROOT / ".secrets" / "nexojuris_ed25519_private.pem"


def _add_months(value: datetime, months: int) -> datetime:
    month_index = value.month - 1 + months
    year, month = value.year + month_index // 12, month_index % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


def _load_private_key(path: Path, password: str | None = None) -> Ed25519PrivateKey:
    if not path.is_file():
        raise FileNotFoundError(f"Chave privada não encontrada em: {path}")

    raw_bytes = path.read_bytes()
    pwd_bytes = password.encode("utf-8") if password else None
    try:
        key = serialization.load_pem_private_key(raw_bytes, password=pwd_bytes)
    except (TypeError, ValueError) as error:
        if "password" in str(error).lower() or pwd_bytes is None:
            prompt_pwd = getpass.getpass("Senha da chave privada Ed25519: ")
            key = serialization.load_pem_private_key(raw_bytes, password=prompt_pwd.encode("utf-8"))
        else:
            raise
    if not isinstance(key, Ed25519PrivateKey):
        raise TypeError("O arquivo não contém uma chave privada Ed25519 válida.")
    return key


def generate_keypair(target_path: Path, password: str | None = None) -> tuple[str, str]:
    """Cria um novo par de chaves Ed25519, salvando o PEM criptografado."""
    target_path = target_path.expanduser().resolve()
    target_path.parent.mkdir(parents=True, exist_ok=True)
    if target_path.exists():
        raise FileExistsError(f"O arquivo de chave já existe em: {target_path}")

    private_key = Ed25519PrivateKey.generate()
    pwd_bytes = password.encode("utf-8") if password else None
    encryption = (
        serialization.BestAvailableEncryption(pwd_bytes)
        if pwd_bytes
        else serialization.NoEncryption()
    )
    pem_bytes = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=encryption,
    )
    target_path.write_bytes(pem_bytes)

    public_bytes = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    public_b64 = base64.b64encode(public_bytes).decode("ascii")
    return str(target_path), public_b64


def emitir_licenca(
    machine_id: str,
    *,
    term_months: int = 12,
    features: tuple[str, ...] = ("converter", "ocr", "reader"),
    customer_reference: str = "CLIENTE",
    output_path: Path | str | None = None,
    private_key_path: Path | str | None = None,
    password: str | None = None,
    key_id: str = LICENSE_MAIN_KEY_ID,
) -> Path:
    machine_id = machine_id.strip().upper()
    if not machine_id.startswith("NXJ2-") or len(machine_id) != 24:
        raise ValueError(f"Machine ID inválido: '{machine_id}'. Deve seguir o formato NXJ2-XXXX-XXXX-XXXX-XXXX.")

    if private_key_path:
        pk_path = Path(private_key_path).expanduser().resolve()
    elif os.environ.get("NEXOJURIS_ADMIN_PRIVATE_KEY"):
        pk_path = Path(os.environ["NEXOJURIS_ADMIN_PRIVATE_KEY"]).expanduser().resolve()
    elif DEFAULT_DEV_KEY.is_file():
        pk_path = DEFAULT_DEV_KEY
    elif DEFAULT_INSTALLED_KEY.is_file():
        pk_path = DEFAULT_INSTALLED_KEY
    else:
        raise FileNotFoundError(
            "Chave privada não localizada. Especifique com --private-key ou configure "
            f"NEXOJURIS_ADMIN_PRIVATE_KEY. Caminho procurado: {DEFAULT_INSTALLED_KEY}"
        )

    private_key = _load_private_key(pk_path, password)

    # Valida correspondência de chave pública
    public_bytes = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    actual_public_b64 = base64.b64encode(public_bytes).decode("ascii")
    expected_public_b64 = LICENSE_PUBLIC_KEYS_B64.get(key_id)
    if expected_public_b64 and actual_public_b64 != expected_public_b64:
        raise ValueError(
            f"A chave privada não corresponde à chave pública '{key_id}' incorporada ao Conversor!"
        )

    now = datetime.now(UTC).replace(microsecond=0)
    issued_at = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    expires_at = _add_months(now, term_months).strftime("%Y-%m-%dT%H:%M:%SZ")
    license_suffix = f"{int(now.timestamp()) % 1_000_000:06d}"
    license_id = f"LIC-{now.year}-{license_suffix}"

    clean_cust = re.sub(r"[^A-Z0-9._-]", "_", customer_reference.strip().upper())
    clean_cust = re.sub(r"_+", "_", clean_cust).strip("._-") or "CLIENTE"

    payload = LicensePayload(
        key_id=key_id,
        license_id=license_id,
        revision=1,
        machine_id=machine_id,
        issued_at=issued_at,
        not_before=issued_at,
        expires_at=expires_at,
        features=tuple(sorted(features)),
        customer_reference=clean_cust,
    )

    doc_bytes = issue_license(payload, private_key)

    if output_path:
        out_file = Path(output_path).expanduser().resolve()
    else:
        out_file = Path.cwd() / f"{license_id}_{machine_id}{LICENSE_FILE_SUFFIX}"

    if out_file.suffix.lower() != LICENSE_FILE_SUFFIX:
        out_file = out_file.with_suffix(LICENSE_FILE_SUFFIX)

    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_bytes(doc_bytes)
    return out_file


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Emissor rápido de licenças ACT4 (.nxjlic) do NexoJuris.")
    parser.add_argument("--machine-id", "-m", help="Machine ID do cliente (formato NXJ2-XXXX-XXXX-XXXX-XXXX).")
    parser.add_argument(
        "--meses", "-t", type=int, choices=[3, 6, 12], default=12, help="Prazo em meses (3, 6 ou 12; padrão: 12)."
    )
    parser.add_argument("--cliente", "-c", default="CLIENTE", help="Identificação ou referência do cliente.")
    parser.add_argument("--saida", "-o", type=Path, help="Caminho do arquivo .nxjlic a ser gerado.")
    parser.add_argument("--private-key", "-k", type=Path, help="Caminho do arquivo PEM da chave privada Ed25519.")
    parser.add_argument("--password", "-p", help="Senha da chave privada (opcional, para uso em automação/scripts).")
    parser.add_argument("--password-env", help="Nome da variável de ambiente com a senha da chave privada.")
    parser.add_argument("--generate-keypair", type=Path, metavar="DESTINO_PEM", help="Gera um novo par de chaves Ed25519.")

    args = parser.parse_args(argv)

    if args.generate_keypair:
        pwd = args.password
        if not pwd:
            pwd = getpass.getpass("Defina a senha para a nova chave privada: ")
            confirm = getpass.getpass("Confirme a senha: ")
            if pwd != confirm:
                print("[ERRO] As senhas não conferem.")
                return 1
        path, pub_b64 = generate_keypair(args.generate_keypair, pwd)
        print(f"\n[SUCESSO] Chave privada gerada em: {path}")
        print(f"Chave pública Base64 correspondente:\n{pub_b64}\n")
        print("Adicione a chave pública em 'license_key_config.py' do Conversor.")
        return 0

    if not args.machine_id:
        parser.error("O argumento --machine-id (-m) é obrigatório para emissão de licença.")

    password = args.password or (os.environ.get(args.password_env) if args.password_env else None)

    try:
        out_file = emitir_licenca(
            args.machine_id,
            term_months=args.meses,
            customer_reference=args.cliente,
            output_path=args.saida,
            private_key_path=args.private_key,
            password=password,
        )
        print("\n[SUCESSO] Licença ACT4 emitida com sucesso!")
        print(f"Arquivo gerado : {out_file}")
        print(f"Machine ID     : {args.machine_id.strip().upper()}")
        print(f"Prazo          : {args.meses} meses")
        print("Envie este arquivo .nxjlic ao cliente para importação no Conversor.\n")
        return 0
    except Exception as error:
        print(f"\n[ERRO] Falha ao emitir licença: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
