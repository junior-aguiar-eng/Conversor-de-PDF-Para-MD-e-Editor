"""Utilitário Administrativo Gerador de Chaves de Ativação do NexoJuris.

USO EXCLUSIVO DO ADMINISTRADOR / DISTRIBUIDOR.
NÃO DISTRIBUIR ESTE ARQUIVO JUNTO COM O EXECUTÁVEL DO CLIENTE.
"""

from __future__ import annotations

import sys

from licensing import generate_activation_key, get_machine_fingerprint, verify_license_key


def main() -> None:
    print("=" * 60)
    print("   NEXOJURIS - GERADOR DE CHAVES DE ATIVAÇÃO (KEYGEN)   ")
    print("=" * 60)

    if len(sys.argv) > 1:
        machine_id = sys.argv[1].strip()
    else:
        current_mid = get_machine_fingerprint()
        print(f"\n[Info] Machine ID deste computador: {current_mid}")
        machine_id = input("\nDigite ou cole o Machine ID do cliente (ex: NXJ-XXXX-XXXX-XXXX-XXXX): ").strip()

    if not machine_id:
        print("\n[Erro] Nenhum Machine ID foi informado.")
        sys.exit(1)

    clean_mid = machine_id.upper()
    if not clean_mid.startswith("NXJ-"):
        print("\n[Aviso] O Machine ID informado não possui o prefixo 'NXJ-'. Processando mesmo assim...")

    activation_key = generate_activation_key(clean_mid)
    is_valid = verify_license_key(clean_mid, activation_key)

    print("\n" + "-" * 60)
    print(f"Machine ID do Cliente : {clean_mid}")
    print(f"Chave de Ativação     : {activation_key}")
    print(f"Assinatura Válida     : {'[OK] SIM' if is_valid else '[FALHA]'}")
    print("-" * 60)
    print("\nCopie a Chave de Ativação acima e envie ao cliente.")


if __name__ == "__main__":
    main()
