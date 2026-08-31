"""Implementação única do build de release do NexoJuris Conversor."""

from __future__ import annotations

import shutil
import subprocess
import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from constants import APP_NAME, APP_VERSION
from scripts.verify_web_assets import verify as verify_web_assets

PROJECT_ROOT = Path(__file__).resolve().parent
RELEASE_DIR = PROJECT_ROOT / "release"
DIST_DIR = RELEASE_DIR / "dist"
WORK_DIR = RELEASE_DIR / "build"
WEB_DIST_DIR = WORK_DIR / "web_dist"
ADMIN_WEB_DIST_DIR = WORK_DIR / "admin_web_dist"
ASSETS_DIR = PROJECT_ROOT / "assets"
WEB_DIR = PROJECT_ROOT / "web"
ADMIN_WEB_DIR = PROJECT_ROOT / "admin_web"
APP_ENTRYPOINT = PROJECT_ROOT / "app.py"
ADMIN_ENTRYPOINT = PROJECT_ROOT / "license_admin_app.py"
ICON_PATH = ASSETS_DIR / "nexojuris.ico"
if not ICON_PATH.exists():
    ICON_PATH = ASSETS_DIR / "boni-pdf.ico"

PYINSTALLER_PACKAGES = (
    "pymupdf4llm",
    "pymupdf",
    "webview",
    "clr_loader",
    "pythonnet",
    "rapidocr_onnxruntime",
    "onnxruntime",
    "edge_tts",
    "deep_translator",
)
PYINSTALLER_HIDDEN_IMPORTS = ("sqlite3", "winreg", "licensing", "ocr_engine", "library_db")
ADMIN_APP_NAME = "NexoJuris Licenças Admin"
ADMIN_PYINSTALLER_PACKAGES = ("webview", "clr_loader", "pythonnet", "cryptography")
ADMIN_PYINSTALLER_HIDDEN_IMPORTS = ("sqlite3", "admin_license_bridge", "admin_licensing")


def prepare_web_assets() -> None:
    """Valida e copia os assets web autocontidos para a área temporária."""
    verify_web_assets()
    if WEB_DIST_DIR.exists():
        shutil.rmtree(WEB_DIST_DIR)
    shutil.copytree(WEB_DIR, WEB_DIST_DIR)


def prepare_admin_web_assets() -> None:
    """Copia a interface administrativa autocontida para o build privado."""
    if ADMIN_WEB_DIST_DIR.exists():
        shutil.rmtree(ADMIN_WEB_DIST_DIR)
    shutil.copytree(ADMIN_WEB_DIR, ADMIN_WEB_DIST_DIR)


def generate_version_info() -> Path:
    """Gera os metadados nativos de versão do executável Windows."""
    version_file = WORK_DIR / "version_info.txt"
    parts = [int(part) for part in APP_VERSION.split(".")]
    parts.extend([0] * (4 - len(parts)))
    version_tuple = tuple(parts[:4])
    version_string = ".".join(str(part) for part in version_tuple)
    version_file.write_text(
        f"""# UTF-8
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={version_tuple},
    prodvers={version_tuple},
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
        StringStruct('FileVersion', '{version_string}'),
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
""",
        encoding="utf-8",
    )
    return version_file


def generate_admin_version_info() -> Path:
    """Gera metadados nativos do executável administrativo separado."""
    version_file = WORK_DIR / "admin_version_info.txt"
    parts = [int(part) for part in APP_VERSION.split(".")]
    parts.extend([0] * (4 - len(parts)))
    version_tuple = tuple(parts[:4])
    version_string = ".".join(str(part) for part in version_tuple)
    version_file.write_text(
        f"""# UTF-8
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={version_tuple},
    prodvers={version_tuple},
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
        StringStruct('FileDescription', 'NexoJuris - Administração local de licenças'),
        StringStruct('FileVersion', '{version_string}'),
        StringStruct('InternalName', '{ADMIN_APP_NAME}'),
        StringStruct('LegalCopyright', 'Copyright (C) 2026 NexoJuris'),
        StringStruct('OriginalFilename', '{ADMIN_APP_NAME}.exe'),
        StringStruct('ProductName', '{ADMIN_APP_NAME}'),
        StringStruct('ProductVersion', '{APP_VERSION}')]
      )
      ]
    ),
    VarFileInfo([VarStruct('Translation', [1046, 1200])])
  ]
)
""",
        encoding="utf-8",
    )
    return version_file


def pyinstaller_arguments(version_file: Path) -> list[str]:
    """Monta argumentos absolutos, independentes do diretório de execução."""
    separator = ";" if sys.platform == "win32" else ":"
    arguments = [
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
        f"{ASSETS_DIR}{separator}assets",
        "--add-data",
        f"{WEB_DIST_DIR}{separator}web",
    ]
    for package in PYINSTALLER_PACKAGES:
        arguments.extend(("--collect-all", package))
    for module in PYINSTALLER_HIDDEN_IMPORTS:
        arguments.extend(("--hidden-import", module))
    arguments.extend(
        (
            "--distpath",
            str(DIST_DIR),
            "--workpath",
            str(WORK_DIR),
            "--specpath",
            str(WORK_DIR),
            str(APP_ENTRYPOINT),
        )
    )
    return arguments


def admin_pyinstaller_arguments(version_file: Path) -> list[str]:
    """Monta o build autocontido do Admin, sem incorporar chave privada."""
    separator = ";" if sys.platform == "win32" else ":"
    arguments = [
        "--noconfirm",
        "--clean",
        "--onedir",
        "--windowed",
        "--optimize",
        "2",
        "--version-file",
        str(version_file),
        "--name",
        ADMIN_APP_NAME,
        "--icon",
        str(ICON_PATH),
        "--add-data",
        f"{ADMIN_WEB_DIST_DIR}{separator}admin_web",
    ]
    for package in ADMIN_PYINSTALLER_PACKAGES:
        arguments.extend(("--collect-all", package))
    for module in ADMIN_PYINSTALLER_HIDDEN_IMPORTS:
        arguments.extend(("--hidden-import", module))
    arguments.extend(
        (
            "--distpath",
            str(DIST_DIR),
            "--workpath",
            str(WORK_DIR),
            "--specpath",
            str(WORK_DIR),
            str(ADMIN_ENTRYPOINT),
        )
    )
    return arguments


def verify_pyinstaller_available() -> str:
    """Confirma que o ambiente criado pelo lock contém o PyInstaller."""
    try:
        return version("pyinstaller")
    except PackageNotFoundError as error:
        raise RuntimeError("PyInstaller ausente. Execute o build oficial com uv --frozen.") from error


def verify_release() -> Path:
    """Valida os artefatos mínimos necessários à execução da release."""
    app_dir = DIST_DIR / "NexoJuris Conversor"
    executable = app_dir / "NexoJuris Conversor.exe"
    converter_package = app_dir / "_internal" / "pymupdf4llm"
    if not executable.is_file():
        raise RuntimeError("Executável principal não encontrado após o build.")
    if not converter_package.is_dir():
        raise RuntimeError("A release foi criada sem pymupdf4llm.")
    return executable


def verify_admin_release() -> Path:
    """Confirma que o Admin e sua interface foram empacotados separadamente."""
    app_dir = DIST_DIR / ADMIN_APP_NAME
    executable = app_dir / f"{ADMIN_APP_NAME}.exe"
    web_entrypoint = app_dir / "_internal" / "admin_web" / "index.html"
    if not executable.is_file():
        raise RuntimeError("Executável administrativo não encontrado após o build.")
    if not web_entrypoint.is_file():
        raise RuntimeError("A release administrativa foi criada sem a interface web.")
    return executable


def build() -> Path:
    """Cria as releases separadas do Conversor e do Admin."""
    pyinstaller_version = verify_pyinstaller_available()
    print(f"=== Build: {APP_NAME} v{APP_VERSION} / PyInstaller {pyinstaller_version} ===")
    for generated_dir in (DIST_DIR, WORK_DIR):
        if generated_dir.exists():
            shutil.rmtree(generated_dir)
    DIST_DIR.mkdir(parents=True, exist_ok=True)
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    prepare_web_assets()
    prepare_admin_web_assets()
    version_file = generate_version_info()
    command = [sys.executable, "-m", "PyInstaller", *pyinstaller_arguments(version_file)]
    subprocess.run(command, cwd=PROJECT_ROOT, check=True)
    admin_version_file = generate_admin_version_info()
    admin_command = [sys.executable, "-m", "PyInstaller", *admin_pyinstaller_arguments(admin_version_file)]
    subprocess.run(admin_command, cwd=PROJECT_ROOT, check=True)
    executable = verify_release()
    admin_executable = verify_admin_release()
    shutil.rmtree(WORK_DIR)
    print(f"Release criada: {executable}")
    print(f"Release administrativa criada: {admin_executable}")
    return executable


def main() -> int:
    try:
        build()
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f"[ERRO] {error}", file=sys.stderr)
        return 1
    return 0
