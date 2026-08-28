"""Utilitário de Exportação de Código-Fonte e Geração de Hash SHA-512 para Registro de Software no INPI.

Cria o pacote ZIP oficial do código-fonte proprietário e emite a Declaração de Veracidade
e Autoria (DVA) contendo o Hash Criptográfico SHA-512 exigido pelo e-Software do INPI.
"""

from __future__ import annotations

import datetime
import hashlib
import os
import zipfile
from pathlib import Path

from constants import APP_NAME, APP_VERSION

PROJECT_ROOT = Path(__file__).resolve().parent
RELEASE_DIR = PROJECT_ROOT / "release"
INPI_ZIP_PATH = RELEASE_DIR / "nexojuris_inpi_source.zip"
DECLARACAO_PATH = RELEASE_DIR / "DECLARACAO_INPI.txt"

# Diretórios e extensões que devem ser excluídos da exportação INPI
EXCLUDED_DIRS = {
    ".git",
    ".github",
    ".venv",
    "venv",
    "env",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".vscode",
    ".idea",
    "release",
    "data",
    "build",
    "dist",
}

EXCLUDED_EXTENSIONS = {
    ".pyc",
    ".pyo",
    ".pyd",
    ".db",
    ".db-journal",
    ".db-wal",
    ".db-shm",
    ".sig",
    ".log",
    ".tmp",
}


EXCLUDED_FILES = {
    "admin_keygen.py",
}


def should_include_file(file_path: Path) -> bool:
    """Verifica se o arquivo deve fazer parte da base de código proprietária do INPI."""
    # Exclui arquivos administrativos/sensíveis específicos
    if file_path.name in EXCLUDED_FILES:
        return False

    relative_parts = file_path.relative_to(PROJECT_ROOT).parts

    # Exclui se estiver dentro de diretório proibido
    for part in relative_parts[:-1]:
        if part in EXCLUDED_DIRS or part.startswith("."):
            return False

    # Exclui extensões compiladas / transitórias
    if file_path.suffix.lower() in EXCLUDED_EXTENSIONS:
        return False

    if file_path.name.startswith("."):
        return False

    return True


def collect_project_files() -> list[Path]:
    """Coleta todos os arquivos relevantes do projeto de forma ordenada."""
    collected: list[Path] = []
    for root, dirs, files in os.walk(PROJECT_ROOT):
        # Filtra diretórios in-place para não descer em pastas ignoradas
        dirs[:] = [d for d in dirs if d not in EXCLUDED_DIRS and not d.startswith(".")]

        for f in sorted(files):
            full_path = Path(root) / f
            if should_include_file(full_path):
                collected.append(full_path)

    return sorted(collected, key=lambda p: str(p.relative_to(PROJECT_ROOT)))


def create_inpi_package() -> tuple[Path, str, dict[str, str]]:
    """Gera o arquivo ZIP e calcula o hash SHA-512 do pacote e SHA-256 de cada arquivo."""
    RELEASE_DIR.mkdir(parents=True, exist_ok=True)
    if INPI_ZIP_PATH.exists():
        INPI_ZIP_PATH.unlink()

    files = collect_project_files()
    file_hashes: dict[str, str] = {}

    # Cria o arquivo ZIP comprimido
    with zipfile.ZipFile(INPI_ZIP_PATH, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zip_file:
        for file_path in files:
            rel_path = file_path.relative_to(PROJECT_ROOT).as_posix()
            data = file_path.read_bytes()
            sha256 = hashlib.sha256(data).hexdigest()
            file_hashes[rel_path] = sha256

            # Adiciona ao ZIP com timestamp normalizado para reprodutibilidade
            zip_info = zipfile.ZipInfo(filename=rel_path)
            zip_info.date_time = (2026, 1, 1, 0, 0, 0)
            zip_info.compress_type = zipfile.ZIP_DEFLATED
            zip_file.writestr(zip_info, data)

    # Calcula o hash SHA-512 do arquivo ZIP gerado
    zip_bytes = INPI_ZIP_PATH.read_bytes()
    sha512_hash = hashlib.sha512(zip_bytes).hexdigest().upper()

    return INPI_ZIP_PATH, sha512_hash, file_hashes


def generate_inpi_declaration(sha512_hash: str, file_hashes: dict[str, str], zip_size: int) -> Path:
    """Emite o documento oficial de Declaração de Autoria com os dados exigidos pelo INPI."""
    now_str = datetime.datetime.now().strftime("%d/%m/%Y às %H:%M:%S")

    content = f"""================================================================================
                    INSTITUTO NACIONAL DA PROPRIEDADE INDUSTRIAL - INPI
               DECLARAÇÃO DE VERACIDADE E AUTORIA DE PROGRAMA DE COMPUTADOR
================================================================================

1. DADOS DE IDENTIFICAÇÃO DO SOFTWARE
--------------------------------------------------------------------------------
Nome do Programa        : {APP_NAME}
Versão do Software      : {APP_VERSION}
Finalidade / Segmento   : Extração de Texto Jurídico, Conversão PDF para Markdown,
                          Leitor e Editor de Jurisprudência com IA Local e Busca FTS5.
Linguagens Utilizadas   : Python 3.14, JavaScript (ES6+), HTML5, CSS3
Ambiente de Execução    : Microsoft Windows 10 / 11 (64-bit)

2. RESUMO HASH CRIPTOGRÁFICO OFICIAL (INPI / DVA)
--------------------------------------------------------------------------------
Algoritmo de Hash       : SHA-512 (Secure Hash Algorithm 512 bits)
Arquivo de Origem       : {INPI_ZIP_PATH.name}
Tamanho do Arquivo      : {zip_size:,} bytes

HASH SHA-512 COMPLETO (COPIAR E COLAR NO FORMULÁRIO DO e-SOFTWARE):
{sha512_hash}

Hash SHA-512 (Minúsculas):
{sha512_hash.lower()}

3. TITULARIDADE E DIREITOS PATRIMONIAIS
--------------------------------------------------------------------------------
Titular dos Direitos    : NexoJuris Soluções Jurídicas & Tecnologia
Tipo de Titular         : Pessoa Jurídica / Desenvolvedor Autor
País de Criação         : Brasil
Data/Hora de Geração    : {now_str}

4. MANIFESTO DE ARQUIVOS E INTEGRIDADE INDIVIDUAL (SHA-256)
--------------------------------------------------------------------------------
Total de Arquivos Inclusos: {len(file_hashes)}

{chr(10).join(f"{sha}  {path}" for path, sha in file_hashes.items())}

================================================================================
Declaro para os devidos fins de direito perante o INPI que os resumos hash acima
identificados correspondem fielmente à versão integral do código-fonte do programa.
================================================================================
"""
    DECLARACAO_PATH.write_text(content, encoding="utf-8")
    return DECLARACAO_PATH


def main() -> None:
    print("=" * 70)
    print("   NEXOJURIS - EXPORTAÇÃO DE CÓDIGO & HASH SHA-512 PARA O INPI   ")
    print("=" * 70)
    print("-> Coletando arquivos proprietários do projeto...")

    zip_path, sha512_hash, file_hashes = create_inpi_package()
    zip_size = zip_path.stat().st_size
    decl_path = generate_inpi_declaration(sha512_hash, file_hashes, zip_size)

    print(f"\n[OK] Pacote ZIP criado com sucesso: {zip_path}")
    print(f"[OK] Total de arquivos incluídos: {len(file_hashes)}")
    print(f"[OK] Tamanho do pacote: {zip_size / 1024:.1f} KB")
    print("\n" + "=" * 70)
    print("HASH CRIPTOGRÁFICO SHA-512 OFICIAL PARA O INPI:")
    print(sha512_hash)
    print("=" * 70)
    print(f"\n[OK] Declaração formal gerada em: {decl_path}")
    print("\nInstruções para o registro:")
    print("1. Acesse o portal e-Software do INPI (https://gru.inpi.gov.br/e-software/).")
    print("2. Preencha o pedido e, no campo de resumo criptográfico, selecione 'SHA-512'.")
    print("3. Cole o Hash SHA-512 exibido acima.")
    print("4. Guarde o arquivo 'nexojuris_inpi_source.zip' com segurança para comprovação.\n")


if __name__ == "__main__":
    main()
