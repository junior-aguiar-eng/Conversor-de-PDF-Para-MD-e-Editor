param(
    [string]$PastaRelease = (Join-Path $PSScriptRoot "release\dist\Boni Conversor PDF Markdown")
)

$ErrorActionPreference = "Stop"
$executavel = Join-Path $PastaRelease "Boni Conversor PDF Markdown.exe"
if (-not (Test-Path $executavel)) {
    throw "Executável não encontrado. Execute build_release.ps1 antes de criar o atalho."
}

$icone = Join-Path $PastaRelease "_internal\assets\boni-pdf.ico"
if (-not (Test-Path $icone)) {
    $icone = $executavel
}

$desktop = [Environment]::GetFolderPath("Desktop")
$atalho = Join-Path $desktop "Boni - Conversor de PDF para Markdown.lnk"
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($atalho)
$shortcut.TargetPath = $executavel
$shortcut.WorkingDirectory = $PastaRelease
$shortcut.IconLocation = "$icone,0"
$shortcut.Description = "Conversor local e leve de PDFs digitais para Markdown"
$shortcut.Save()

Write-Host "Atalho criado em $atalho"
