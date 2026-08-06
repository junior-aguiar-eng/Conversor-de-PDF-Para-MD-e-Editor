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

# Um atalho na pasta "Enviar para" do usuário faz o Explorer oferecer
# "Converter para Markdown" ao clicar com o botão direito em um ou mais
# PDFs. É só um arquivo .lnk numa pasta de usuário: sem editar o registro
# e sem precisar de privilégios elevados.
$sendTo = [Environment]::GetFolderPath("SendTo")
$atalho = Join-Path $sendTo "Converter para Markdown.lnk"
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($atalho)
$shortcut.TargetPath = $executavel
$shortcut.WorkingDirectory = $PastaRelease
$shortcut.IconLocation = "$icone,0"
$shortcut.Description = "Converte PDFs selecionados para Markdown, sem abrir a janela principal"
$shortcut.Save()

Write-Host "Atalho criado em $atalho"
Write-Host 'Agora "Converter para Markdown" aparece no menu Enviar para ao clicar com o botão direito em um ou mais PDFs.'
