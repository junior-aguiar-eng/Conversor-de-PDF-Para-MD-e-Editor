$ErrorActionPreference = "Stop"

$project = $PSScriptRoot
Stop-Process -Name "NexoJuris Conversor", "Boni Conversor PDF Markdown" -Force -ErrorAction SilentlyContinue

Push-Location $project
try {
    & uv run --frozen --group dev python (Join-Path $project "build_app.py")
    if ($LASTEXITCODE -ne 0) {
        throw "A criação da release falhou."
    }
} finally {
    Pop-Location
}
