param(
    [Parameter(Mandatory = $true)]
    [string]$Installer,
    [string]$ReportPath = "release\windows-installer-gate.json"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Assert-ChildPath {
    param([string]$Path, [string]$Root)
    $fullPath = [IO.Path]::GetFullPath($Path).TrimEnd('\')
    $fullRoot = [IO.Path]::GetFullPath($Root).TrimEnd('\')
    if (-not $fullPath.StartsWith($fullRoot + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw "Caminho fora da raiz temporária autorizada: $fullPath"
    }
}

function Invoke-Silent {
    param([string]$FilePath, [string[]]$Arguments)
    $process = Start-Process -FilePath $FilePath -ArgumentList $Arguments -PassThru -Wait -WindowStyle Hidden
    if ($process.ExitCode -ne 0) {
        throw "$([IO.Path]::GetFileName($FilePath)) retornou código $($process.ExitCode)."
    }
}

$resolvedInstaller = (Resolve-Path -LiteralPath $Installer).Path
$localAppData = [Environment]::GetFolderPath([Environment+SpecialFolder]::LocalApplicationData)
$tempBase = [IO.Path]::GetFullPath((Join-Path $localAppData "Temp")).TrimEnd('\')
$testRoot = Join-Path $tempBase ("NexoJuris-InstallerGate-" + [guid]::NewGuid().ToString("N"))
Assert-ChildPath -Path $testRoot -Root $tempBase
$installRoot = Join-Path $testRoot "app"
$userDataRoot = Join-Path $testRoot "user-data"
$reportDestination = [IO.Path]::GetFullPath($ReportPath)
$registryPath = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\{D3EFD865-4B37-41DB-97FB-A0DB6607E863}_is1"
$results = [ordered]@{
    ok = $false
    generated_at = [DateTime]::UtcNow.ToString("o")
    installer = $resolvedInstaller
    installer_bytes = (Get-Item -LiteralPath $resolvedInstaller).Length
    installer_sha256 = (Get-FileHash -LiteralPath $resolvedInstaller -Algorithm SHA256).Hash.ToLowerInvariant()
    clean_install = $false
    update = $false
    internal_probe = $false
    uninstall_registered = $false
    uninstall = $false
    user_data_preserved = $false
    error = $null
}
$gateError = $null
$previousDataRoot = $env:NEXOJURIS_DATA_DIR

try {
    New-Item -ItemType Directory -Path $testRoot, $userDataRoot -Force | Out-Null
    $env:NEXOJURIS_DATA_DIR = $userDataRoot
    $installArguments = @(
        "/CURRENTUSER",
        "/VERYSILENT",
        "/SUPPRESSMSGBOXES",
        "/NORESTART",
        "/SP-",
        ('/DIR="' + $installRoot + '"')
    )
    Invoke-Silent -FilePath $resolvedInstaller -Arguments $installArguments
    $executable = Join-Path $installRoot "NexoJuris Conversor.exe"
    if (-not (Test-Path -LiteralPath $executable -PathType Leaf)) {
        throw "A instalação limpa não produziu o executável esperado."
    }
    $results.clean_install = $true
    $results.uninstall_registered = Test-Path -LiteralPath $registryPath
    if (-not $results.uninstall_registered) {
        throw "A instalação não registrou o desinstalador por usuário."
    }
    $probePath = Join-Path $testRoot "probe.json"
    Invoke-Silent -FilePath $executable -Arguments @("--release-probe", ('"' + $probePath + '"'))
    $probe = Get-Content -LiteralPath $probePath -Raw | ConvertFrom-Json
    if (-not $probe.ok -or -not $probe.checks.runtime_dependencies -or -not $probe.checks.multiprocessing_spawn) {
        throw "O executável instalado falhou no probe interno."
    }
    $results.internal_probe = $true

    $sentinel = Join-Path $userDataRoot "preservar-na-atualizacao-e-desinstalacao.txt"
    [IO.File]::WriteAllText($sentinel, "preservar")
    Invoke-Silent -FilePath $resolvedInstaller -Arguments $installArguments
    if ((Get-Content -LiteralPath $sentinel -Raw) -ne "preservar") {
        throw "A atualização alterou os dados persistentes do usuário."
    }
    $results.update = $true

    $uninstaller = Join-Path $installRoot "unins000.exe"
    if (-not (Test-Path -LiteralPath $uninstaller -PathType Leaf)) {
        throw "Desinstalador não encontrado."
    }
    Invoke-Silent -FilePath $uninstaller -Arguments @("/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART")
    $results.uninstall = -not (Test-Path -LiteralPath $executable)
    $results.user_data_preserved = (Test-Path -LiteralPath $sentinel -PathType Leaf) -and (
        (Get-Content -LiteralPath $sentinel -Raw) -eq "preservar"
    )
    if (-not $results.uninstall -or -not $results.user_data_preserved) {
        throw "A desinstalação não removeu o aplicativo ou não preservou os dados externos."
    }
    if (Test-Path -LiteralPath $registryPath) {
        throw "O registro de desinstalação permaneceu após a remoção."
    }
    $results.ok = $true
} catch {
    $gateError = $_
    $results.error = $_.Exception.Message
} finally {
    $env:NEXOJURIS_DATA_DIR = $previousDataRoot
    $reportParent = Split-Path -Parent $reportDestination
    if ($reportParent) {
        New-Item -ItemType Directory -Path $reportParent -Force | Out-Null
    }
    $results | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $reportDestination -Encoding utf8
    if (Test-Path -LiteralPath $testRoot) {
        Assert-ChildPath -Path $testRoot -Root $tempBase
        Remove-Item -LiteralPath $testRoot -Recurse -Force
    }
}

if ($gateError) {
    throw $gateError
}
Write-Host "Windows installer gate aprovado. Relatório: $reportDestination"
