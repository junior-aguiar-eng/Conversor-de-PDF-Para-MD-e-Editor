param(
    [string]$PastaRelease = (Join-Path $PSScriptRoot "release\dist\NexoJuris Conversor")
)

$ErrorActionPreference = "Stop"
$executavel = Join-Path $PastaRelease "NexoJuris Conversor.exe"
if (-not (Test-Path $executavel)) {
    throw "Executável não encontrado. Execute build_release.ps1 antes de criar o atalho."
}

$icone = Join-Path $PastaRelease "_internal\assets\nexojuris.ico"
if (-not (Test-Path $icone)) {
    $icone = Join-Path $PastaRelease "_internal\assets\boni-pdf.ico"
    if (-not (Test-Path $icone)) {
        $icone = $executavel
    }
}

$desktop = [Environment]::GetFolderPath("Desktop")
$atalho = Join-Path $desktop "NexoJuris - Conversor.lnk"
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($atalho)
$shortcut.TargetPath = $executavel
$shortcut.WorkingDirectory = $PastaRelease
$shortcut.IconLocation = "$icone,0"
$shortcut.Description = "NexoJuris - Conversor local de PDF para Markdown"
$shortcut.Save()

Write-Host "Atalho criado em $atalho"
