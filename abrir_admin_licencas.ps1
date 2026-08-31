$ErrorActionPreference = "Stop"

$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
$adminApp = Join-Path $PSScriptRoot "license_admin_app.py"

if (-not (Test-Path -LiteralPath $python)) {
    throw "O ambiente Python do painel administrativo não foi encontrado."
}

$securePassword = Read-Host "Senha administrativa" -AsSecureString
$passwordPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($securePassword)

try {
    $env:NEXOJURIS_ADMIN_PASSWORD = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($passwordPointer)
    & $python $adminApp --environment local
}
finally {
    Remove-Item Env:\NEXOJURIS_ADMIN_PASSWORD -ErrorAction SilentlyContinue
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($passwordPointer)
}
