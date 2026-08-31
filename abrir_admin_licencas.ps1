$ErrorActionPreference = "Stop"

$packagedAdmin = Join-Path $PSScriptRoot "NexoJuris Licenças Admin.exe"
$developmentPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
$developmentAdmin = Join-Path $PSScriptRoot "license_admin_app.py"
$adminDataRoot = Join-Path $env:LOCALAPPDATA "NexoJuris\LicencasAdmin"
$installedPrivateKey = Join-Path $adminDataRoot "nexojuris_ed25519_private.pem"
$developmentPrivateKey = Join-Path $PSScriptRoot ".secrets\nexojuris_ed25519_private.pem"

if (Test-Path -LiteralPath $packagedAdmin -PathType Leaf) {
    $executable = $packagedAdmin
    $arguments = @()
} elseif ((Test-Path -LiteralPath $developmentPython -PathType Leaf) -and
          (Test-Path -LiteralPath $developmentAdmin -PathType Leaf)) {
    $executable = $developmentPython
    $arguments = @($developmentAdmin)
} else {
    throw "O executável do painel administrativo não foi encontrado."
}

if ($env:NEXOJURIS_ADMIN_PRIVATE_KEY) {
    $privateKey = $env:NEXOJURIS_ADMIN_PRIVATE_KEY
} elseif (Test-Path -LiteralPath $installedPrivateKey -PathType Leaf) {
    $privateKey = $installedPrivateKey
} elseif (Test-Path -LiteralPath $developmentPrivateKey -PathType Leaf) {
    $privateKey = $developmentPrivateKey
} else {
    $privateKey = $installedPrivateKey
}

$securePassword = Read-Host "Senha administrativa" -AsSecureString
$passwordPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($securePassword)

try {
    $env:NEXOJURIS_ADMIN_PASSWORD = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($passwordPointer)
    & $executable @arguments --environment local --private-key $privateKey
    if ($LASTEXITCODE -ne 0) {
        throw "O painel administrativo foi encerrado com código $LASTEXITCODE."
    }
}
finally {
    Remove-Item Env:\NEXOJURIS_ADMIN_PASSWORD -ErrorAction SilentlyContinue
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($passwordPointer)
}
