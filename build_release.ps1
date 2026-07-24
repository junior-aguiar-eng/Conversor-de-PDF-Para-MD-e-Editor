$ErrorActionPreference = "Stop"

$project = $PSScriptRoot
$release = Join-Path $project "release"
$dist = Join-Path $release "dist"
$work = Join-Path $release "build"
$icon = Join-Path $project "assets\boni-pdf.ico"
$assets = Join-Path $project "assets"
$venvPython = Join-Path $project ".venv\Scripts\python.exe"

Remove-Item -LiteralPath $release -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path $dist, $work | Out-Null

Push-Location $project
try {
    $arguments = @(
        "--noconfirm",
        "--clean",
        "--onedir",
        "--windowed",
        "--name", "Boni Conversor PDF Markdown",
        "--icon", $icon,
        "--add-data", "$assets;assets",
        "--collect-all", "pymupdf4llm",
        "--collect-all", "pymupdf",
        "--distpath", $dist,
        "--workpath", $work,
        "app.py"
    )

    $venvHasPyInstaller = $false
    if (Test-Path $venvPython) {
        & $venvPython -c "import importlib.util, sys; sys.exit(0 if importlib.util.find_spec('PyInstaller') else 1)"
        $venvHasPyInstaller = ($LASTEXITCODE -eq 0)
    }

    if ($venvHasPyInstaller) {
        & $venvPython -m PyInstaller @arguments
    } elseif (Get-Command pyinstaller -ErrorAction SilentlyContinue) {
        & pyinstaller @arguments
    } else {
        & uv run --with pyinstaller pyinstaller @arguments
    }

    if ($LASTEXITCODE -ne 0) {
        throw "A criação do executável falhou."
    }
} finally {
    Pop-Location
}

Write-Host "Release criada em $dist"
