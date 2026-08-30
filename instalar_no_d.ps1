param(
    [string]$Destino = "D:\NexoJuris Conversor",
    [switch]$RecriarAmbiente
)

$ErrorActionPreference = "Stop"
$origem = $PSScriptRoot

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    throw "uv não foi encontrado. Instale o uv e execute este instalador novamente."
}

& uv python find 3.14
if ($LASTEXITCODE -ne 0) {
    throw "Python 3.14 não foi encontrado pelo uv. Instale-o com: uv python install 3.14"
}

New-Item -ItemType Directory -Force -Path $Destino | Out-Null
$arquivosDoAplicativo = @(
    "app.py",
    "app_storage.py",
    "constants.py",
    "converter.py",
    "file_authorization.py",
    "library_db.py",
    "licensing.py",
    "license_online_config.py",
    "markdown_utils.py",
    "models.py",
    "ocr_engine.py",
    "online_services.py",
    "online_license_client.py",
    "production_diagnostics.py",
    "text_fidelity.py",
    "web_api.py",
    "requirements.lock.txt",
    "iniciar.vbs"
)
Copy-Item -Path ($arquivosDoAplicativo | ForEach-Object { Join-Path $origem $_ }) -Destination $Destino -Force
Copy-Item -Path (Join-Path $origem "web") -Destination (Join-Path $Destino "web") -Recurse -Force
Copy-Item -Path (Join-Path $origem "assets") -Destination (Join-Path $Destino "assets") -Recurse -Force
Copy-Item -Path (Join-Path $origem "license_core") -Destination (Join-Path $Destino "license_core") -Recurse -Force

$python = Join-Path $Destino ".venv\Scripts\python.exe"
if ($RecriarAmbiente -and (Test-Path (Join-Path $Destino ".venv"))) {
    Remove-Item -LiteralPath (Join-Path $Destino ".venv") -Recurse -Force
}
if (-not (Test-Path $python)) {
    & uv venv --python 3.14 (Join-Path $Destino ".venv")
} else {
    Write-Host "Reutilizando o ambiente Python existente em $Destino\.venv"
}

# Instala versões fixadas para manter o ambiente reproduzível.
& uv pip install --python $python -r (Join-Path $Destino "requirements.lock.txt")
if ($LASTEXITCODE -ne 0) {
    throw "Não foi possível sincronizar as dependências do conversor."
}

# Falha durante a instalação, em vez de deixar imports ausentes aparecerem só ao abrir o aplicativo.
& $python -c "import app, converter, library_db, license_core, licensing, ocr_engine, online_license_client, web_api"
if ($LASTEXITCODE -ne 0) {
    throw "A instalação está incompleta: um ou mais módulos do aplicativo não puderam ser importados."
}

Write-Host "Instalação concluída em $Destino"
Write-Host "Abra iniciar.vbs para usar o conversor sem Prompt de Comando."
