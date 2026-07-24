param(
    [string]$Destino = "D:\PDF para Markdown",
    [switch]$RecriarAmbiente
)

$ErrorActionPreference = "Stop"
$origem = $PSScriptRoot

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    throw "uv não foi encontrado. Instale o uv e execute este instalador novamente."
}

& uv python find 3.13
if ($LASTEXITCODE -ne 0) {
    throw "Python 3.13 não foi encontrado pelo uv. Instale-o com: uv python install 3.13"
}

New-Item -ItemType Directory -Force -Path $Destino | Out-Null
Copy-Item -Path (Join-Path $origem "app.py"), (Join-Path $origem "requirements.txt"), (Join-Path $origem "iniciar.bat"), (Join-Path $origem "iniciar.vbs") -Destination $Destino -Force

$python = Join-Path $Destino ".venv\Scripts\python.exe"
if ($RecriarAmbiente -and (Test-Path (Join-Path $Destino ".venv"))) {
    Remove-Item -LiteralPath (Join-Path $Destino ".venv") -Recurse -Force
}
if (-not (Test-Path $python)) {
    & uv venv --python 3.13 (Join-Path $Destino ".venv")
} else {
    Write-Host "Reutilizando o ambiente Python existente em $Destino\.venv"
}
# Instala a versão fixada e todas as dependências transitivas do Marker 1.x.
& uv pip install --python $python -r (Join-Path $Destino "requirements.txt")
if ($LASTEXITCODE -ne 0) {
    throw "Não foi possível sincronizar as dependências do conversor."
}

Write-Host "Instalação concluída em $Destino"
Write-Host "Abra iniciar.vbs para usar o conversor sem Prompt de Comando."
