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
        $bundledConverter = Join-Path $dist "Boni Conversor PDF Markdown\_internal\pymupdf4llm"
        if (-not (Test-Path -LiteralPath $bundledConverter)) {
            throw "A release foi criada sem a dependÃªncia principal do conversor."
        }
        Remove-Item -LiteralPath $work -Recurse -Force
    }
} finally {
    Pop-Location
}

Write-Host "Release criada em $dist"
