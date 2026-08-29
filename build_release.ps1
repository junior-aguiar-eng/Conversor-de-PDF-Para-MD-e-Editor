$ErrorActionPreference = "Stop"

Stop-Process -Name "NexoJuris Conversor", "Boni Conversor PDF Markdown" -Force -ErrorAction SilentlyContinue

$project = $PSScriptRoot
$release = Join-Path $project "release"
$dist = Join-Path $release "dist"
$work = Join-Path $release "build"
$icon = Join-Path $project "assets\nexojuris.ico"
if (-not (Test-Path $icon)) {
    $icon = Join-Path $project "assets\boni-pdf.ico"
}
$assets = Join-Path $project "assets"
$web = Join-Path $project "web"
$webDist = Join-Path $work "web_dist"
$versionFile = Join-Path $work "version_info.txt"
$venvPython = Join-Path $project ".venv\Scripts\python.exe"

& uv run python (Join-Path $project "scripts\verify_web_assets.py")
if ($LASTEXITCODE -ne 0) {
    throw "A validação dos assets web falhou. Execute npm ci e npm run build:web."
}

Remove-Item -LiteralPath $release -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path $dist, $work, $webDist | Out-Null

# Minifica e prepara os arquivos web para a release e gera metadados de versão do Windows
& uv run python -c "
import shutil
from pathlib import Path
from constants import APP_NAME, APP_VERSION

src = Path('web')
dst = Path(r'$webDist')
if dst.exists():
    shutil.rmtree(dst)
shutil.copytree(src, dst)

# Version Info para o Windows Explorer (.exe Properties)
parts = [int(p) for p in APP_VERSION.split('.')]
while len(parts) < 4:
    parts.append(0)
v_tuple = tuple(parts[:4])
v_str = '.'.join(str(p) for p in v_tuple)

version_res = f'''# UTF-8
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
'''
Path(r'$versionFile').write_text(version_res, encoding='utf-8')
"

Push-Location $project
try {
    $arguments = @(
        "--noconfirm",
        "--clean",
        "--onedir",
        "--windowed",
        "--optimize", "2",
        "--version-file", $versionFile,
        "--name", "NexoJuris Conversor",
        "--icon", $icon,
        "--add-data", "$assets;assets",
        "--add-data", "$webDist;web",
        "--collect-all", "pymupdf4llm",
        "--collect-all", "pymupdf",
        "--collect-all", "webview",
        "--collect-all", "clr_loader",
        "--collect-all", "pythonnet",
        "--collect-all", "rapidocr_onnxruntime",
        "--collect-all", "onnxruntime",
        "--collect-all", "edge_tts",
        "--collect-all", "deep_translator",
        "--hidden-import", "sqlite3",
        "--hidden-import", "ocr_engine",
        "--hidden-import", "library_db",
        "--distpath", $dist,
        "--workpath", $work,
        "--specpath", $work,
        "app.py"
    )

    $venvHasPyInstaller = $false
    if (Test-Path $venvPython) {
        & $venvPython -c "import importlib.util, sys; sys.exit(0 if importlib.util.find_spec('PyInstaller') else 1)"
        $venvHasPyInstaller = ($LASTEXITCODE -eq 0)
    }

    if ($venvHasPyInstaller) {
        & $venvPython -m PyInstaller @arguments
    } else {
        # Never use a PyInstaller from PATH: it may belong to another Python
        # environment and omit this project's runtime dependencies.
        & uv run --with pyinstaller pyinstaller @arguments
    }

    if ($LASTEXITCODE -ne 0) {
        throw "A criação do executável falhou."
    }
    if ($LASTEXITCODE -eq 0) {
        $bundledConverter = Join-Path $dist "NexoJuris Conversor\_internal\pymupdf4llm"
        if (-not (Test-Path -LiteralPath $bundledConverter)) {
            throw "A release foi criada sem a dependência principal do conversor."
        }
        Remove-Item -LiteralPath $work -Recurse -Force
    }
} finally {
    Pop-Location
}

Write-Host "Release criada em $dist"
