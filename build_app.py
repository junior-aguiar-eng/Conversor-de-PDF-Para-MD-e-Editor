"""Script de compilação, blindagem e empacotamento comercial do NexoJuris."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from constants import APP_NAME, APP_VERSION
from scripts.verify_web_assets import verify as verify_web_assets

PROJECT_ROOT = Path(__file__).resolve().parent
RELEASE_DIR = PROJECT_ROOT / "release"
DIST_DIR = RELEASE_DIR / "dist"
WORK_DIR = RELEASE_DIR / "build"
WEB_DIST_DIR = WORK_DIR / "web_dist"
ASSETS_DIR = PROJECT_ROOT / "assets"
WEB_DIR = PROJECT_ROOT / "web"
ICON_PATH = ASSETS_DIR / "nexojuris.ico"
if not ICON_PATH.exists():
    ICON_PATH = ASSETS_DIR / "boni-pdf.ico"


def prepare_web_assets() -> None:
    """Copia e preserva integralmente os assets web estáticos sem quebrar scripts."""
    print("-> Preparando e sincronizando assets web (HTML/CSS/JS)...")
    verify_web_assets()
    if WEB_DIST_DIR.exists():
        shutil.rmtree(WEB_DIST_DIR)

    # Copia a pasta web inteira preservando subdiretórios e scripts sem regex destrutivo
    shutil.copytree(WEB_DIR, WEB_DIST_DIR)


def generate_version_info() -> Path:
    """Gera o arquivo de metadados de versão nativo do Windows para o .exe."""
    version_file = WORK_DIR / "version_info.txt"
    parts = [int(p) for p in APP_VERSION.split(".")]
    while len(parts) < 4:
        parts.append(0)
    v_tuple = tuple(parts[:4])
    v_str = ".".join(str(p) for p in v_tuple)

    version_content = f"""# UTF-8
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={v_tuple},
    prodvers={v_tuple},
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo(
      [
      StringTable(
        '041604b0',
        [StringStruct('CompanyName', 'NexoJuris'),
        StringStruct('FileDescription', '{APP_NAME} - Conversor local de PDF para Markdown'),
        StringStruct('FileVersion', '{v_str}'),
        StringStruct('InternalName', 'NexoJuris Conversor'),
        StringStruct('LegalCopyright', 'Copyright (C) 2026 NexoJuris'),
        StringStruct('OriginalFilename', 'NexoJuris Conversor.exe'),
        StringStruct('ProductName', '{APP_NAME}'),
        StringStruct('ProductVersion', '{APP_VERSION}')]
      )
      ]
    ),
    VarFileInfo([VarStruct('Translation', [1046, 1200])])
  ]
)
"""
    version_file.write_text(version_content, encoding="utf-8")
    return version_file


def build() -> None:
    """Executa o empacotamento completo com PyInstaller."""
    print(f"=== Iniciando Build Comercial: {APP_NAME} v{APP_VERSION} ===")

    # Limpeza prévia
    for generated_dir in (DIST_DIR, WORK_DIR):
        if generated_dir.exists():
            shutil.rmtree(generated_dir, ignore_errors=True)

    DIST_DIR.mkdir(parents=True, exist_ok=True)
    WORK_DIR.mkdir(parents=True, exist_ok=True)

    prepare_web_assets()
    version_file = generate_version_info()

    sep = ";" if sys.platform == "win32" else ":"

    pyinstaller_args = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onedir",
        "--windowed",
        "--optimize",
        "2",
        "--version-file",
        str(version_file),
        "--name",
        "NexoJuris Conversor",
        "--icon",
        str(ICON_PATH),
        "--add-data",
        f"{ASSETS_DIR}{sep}assets",
        "--add-data",
        f"{WEB_DIST_DIR}{sep}web",
        "--collect-all",
        "pymupdf4llm",
        "--collect-all",
        "pymupdf",
        "--collect-all",
        "webview",
        "--collect-all",
        "clr_loader",
        "--collect-all",
        "pythonnet",
        "--collect-all",
        "rapidocr_onnxruntime",
        "--collect-all",
        "onnxruntime",
        "--collect-all",
        "edge_tts",
        "--collect-all",
        "deep_translator",
        "--hidden-import",
        "sqlite3",
        "--hidden-import",
        "winreg",
        "--hidden-import",
        "licensing",
        "--hidden-import",
        "ocr_engine",
        "--hidden-import",
        "library_db",
        "--distpath",
        str(DIST_DIR),
        "--workpath",
        str(WORK_DIR),
        "--specpath",
        str(WORK_DIR),
        "app.py",
    ]

    print("-> Executando PyInstaller com otimização...")
    env = os.environ.copy()
    res = subprocess.run(pyinstaller_args, cwd=str(PROJECT_ROOT), env=env)

    if res.returncode != 0:
        print("[ERRO] Erro durante o empacotamento com PyInstaller.")
        sys.exit(res.returncode)

    # Verificação de integridade pós-build
    bundled_app_dir = DIST_DIR / "NexoJuris Conversor"
    bundled_exe = bundled_app_dir / "NexoJuris Conversor.exe"
    bundled_converter = bundled_app_dir / "_internal" / "pymupdf4llm"

    if not bundled_exe.exists():
        print("[ERRO] Executavel principal nao encontrado.")
        sys.exit(1)

    if not bundled_converter.exists():
        print("[ERRO] Pacote pymupdf4llm nao encontrado em _internal.")
        sys.exit(1)

    print("[OK] Build concluido com sucesso!")
    print(f"Pasta de Distribuicao: {DIST_DIR / 'NexoJuris Conversor'}")
    print(f"Executavel: {bundled_exe}")


if __name__ == "__main__":
    build()
