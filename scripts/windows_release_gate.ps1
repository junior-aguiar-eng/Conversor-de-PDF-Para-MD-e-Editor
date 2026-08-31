param(
    [Parameter(Mandatory = $true)]
    [string]$ReleaseRoot,
    [string]$ReportPath = "release\windows-release-gate.json",
    [int]$SoakSeconds = 60,
    [switch]$RequireSignature,
    [switch]$KeepArtifacts
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

function Invoke-ReleaseProbe {
    param([string]$Executable, [string]$Destination)
    $quotedDestination = '"' + $Destination + '"'
    $process = Start-Process -FilePath $Executable -ArgumentList @("--release-probe", $quotedDestination) -PassThru -Wait -WindowStyle Hidden
    if ($process.ExitCode -ne 0) {
        throw "O probe do executável retornou código $($process.ExitCode)."
    }
    $payload = Get-Content -LiteralPath $Destination -Raw | ConvertFrom-Json
    if (-not $payload.ok -or -not $payload.checks.runtime_dependencies -or -not $payload.checks.edgechromium_backend_import -or -not $payload.checks.multiprocessing_spawn) {
        throw "O probe interno do artefato não aprovou todos os componentes."
    }
    return $payload
}

$resolvedRelease = (Resolve-Path -LiteralPath $ReleaseRoot).Path
$sourceExecutable = Join-Path $resolvedRelease "NexoJuris Conversor.exe"
if (-not (Test-Path -LiteralPath $sourceExecutable -PathType Leaf)) {
    throw "Executável não encontrado em $sourceExecutable"
}

$localAppData = [Environment]::GetFolderPath([Environment+SpecialFolder]::LocalApplicationData)
$tempBase = [IO.Path]::GetFullPath((Join-Path $localAppData "Temp")).TrimEnd('\')
$testRoot = Join-Path $tempBase ("NexoJuris-WindowsGate-" + [guid]::NewGuid().ToString("N"))
Assert-ChildPath -Path $testRoot -Root $tempBase
$installRoot = Join-Path $testRoot "installed"
$userDataRoot = Join-Path $testRoot "user-data"
$reportDestination = [IO.Path]::GetFullPath($ReportPath)
$results = [ordered]@{
    ok = $false
    generated_at = [DateTime]::UtcNow.ToString("o")
    release_root = $resolvedRelease
    signature = $null
    clean_install = $false
    internal_probe = $null
    webview2_soak = $false
    soak_seconds = $SoakSeconds
    max_working_set_bytes = 0
    ntfs_read_only = $false
    ntfs_running_image_lock = $false
    update = $false
    uninstall = $false
    user_data_preserved = $false
    error = $null
}
$gateError = $null
$guiProcess = $null
$previousDataRoot = $env:NEXOJURIS_DATA_DIR

try {
    New-Item -ItemType Directory -Path $installRoot, $userDataRoot -Force | Out-Null
    Get-ChildItem -LiteralPath $resolvedRelease -Force | Copy-Item -Destination $installRoot -Recurse -Force
    $installedExecutable = Join-Path $installRoot "NexoJuris Conversor.exe"
    if (-not (Test-Path -LiteralPath $installedExecutable -PathType Leaf)) {
        throw "A instalação limpa não contém o executável."
    }
    $results.clean_install = $true
    $env:NEXOJURIS_DATA_DIR = $userDataRoot

    $signature = Get-AuthenticodeSignature -LiteralPath $installedExecutable
    $results.signature = [ordered]@{
        status = [string]$signature.Status
        subject = if ($signature.SignerCertificate) { $signature.SignerCertificate.Subject } else { $null }
    }
    if ($RequireSignature -and $signature.Status -ne [System.Management.Automation.SignatureStatus]::Valid) {
        throw "A política desta execução exige assinatura Authenticode válida; estado: $($signature.Status)."
    }
    if (-not $RequireSignature -and $signature.Status -notin @(
        [System.Management.Automation.SignatureStatus]::Valid,
        [System.Management.Automation.SignatureStatus]::NotSigned
    )) {
        throw "Assinatura Authenticode em estado inválido: $($signature.Status)."
    }

    $probeReport = Join-Path $testRoot "release-probe.json"
    $results.internal_probe = Invoke-ReleaseProbe -Executable $installedExecutable -Destination $probeReport

    $permissionProbe = Join-Path $testRoot "permission-probe.txt"
    [IO.File]::WriteAllText($permissionProbe, "original")
    [IO.File]::SetAttributes($permissionProbe, [IO.FileAttributes]::ReadOnly)
    try {
        try {
            [IO.File]::WriteAllText($permissionProbe, "sobrescrito")
            throw "O Windows permitiu sobrescrever um arquivo marcado como somente leitura."
        } catch [UnauthorizedAccessException] {
            $results.ntfs_read_only = $true
        }
    } finally {
        [IO.File]::SetAttributes($permissionProbe, [IO.FileAttributes]::Normal)
    }

    $guiProcess = Start-Process -FilePath $installedExecutable -PassThru -WindowStyle Hidden
    $deadline = [DateTime]::UtcNow.AddSeconds($SoakSeconds)
    while ([DateTime]::UtcNow -lt $deadline) {
        Start-Sleep -Seconds 2
        $guiProcess.Refresh()
        if ($guiProcess.HasExited) {
            throw "O executável encerrou durante o smoke/soak WebView2 com código $($guiProcess.ExitCode)."
        }
        $results.max_working_set_bytes = [Math]::Max(
            [int64]$results.max_working_set_bytes,
            [int64]$guiProcess.WorkingSet64
        )
    }
    $results.webview2_soak = $true

    try {
        $exclusive = [IO.File]::Open(
            $installedExecutable,
            [IO.FileMode]::Open,
            [IO.FileAccess]::ReadWrite,
            [IO.FileShare]::None
        )
        $exclusive.Dispose()
        throw "O executável em execução não ficou protegido contra escrita exclusiva."
    } catch [IO.IOException] {
        $results.ntfs_running_image_lock = $true
    } catch [UnauthorizedAccessException] {
        $results.ntfs_running_image_lock = $true
    }

    Stop-Process -Id $guiProcess.Id -Force
    $guiProcess.WaitForExit()
    $guiProcess = $null
    $diagnosticLog = Join-Path $userDataRoot "diagnostics\nexojuris.log"
    if (-not (Test-Path -LiteralPath $diagnosticLog -PathType Leaf)) {
        throw "A release não produziu log persistente durante o smoke WebView2."
    }
    $logText = Get-Content -LiteralPath $diagnosticLog -Raw
    if ($logText -match "CRITICAL .*Falha durante a inicialização") {
        throw "O log registrou falha crítica de inicialização durante o smoke WebView2."
    }

    $sentinel = Join-Path $userDataRoot "persistencia-update.txt"
    [IO.File]::WriteAllText($sentinel, "preservar")
    Get-ChildItem -LiteralPath $resolvedRelease -Force | Copy-Item -Destination $installRoot -Recurse -Force
    $updateProbe = Join-Path $testRoot "update-probe.json"
    $null = Invoke-ReleaseProbe -Executable $installedExecutable -Destination $updateProbe
    if ((Get-Content -LiteralPath $sentinel -Raw) -ne "preservar") {
        throw "A atualização não preservou os dados externos à instalação."
    }
    $results.update = $true

    Assert-ChildPath -Path $installRoot -Root $testRoot
    Remove-Item -LiteralPath $installRoot -Recurse -Force
    if (Test-Path -LiteralPath $installRoot) {
        throw "A desinstalação simulada não removeu a pasta da aplicação."
    }
    $results.uninstall = $true
    $results.user_data_preserved = Test-Path -LiteralPath $sentinel -PathType Leaf
    if (-not $results.user_data_preserved) {
        throw "A desinstalação removeu dados persistentes do usuário."
    }
    $results.ok = $true
} catch {
    $gateError = $_
    $results.error = $_.Exception.Message
} finally {
    if ($guiProcess -and -not $guiProcess.HasExited) {
        Stop-Process -Id $guiProcess.Id -Force -ErrorAction SilentlyContinue
    }
    $env:NEXOJURIS_DATA_DIR = $previousDataRoot
    $reportParent = Split-Path -Parent $reportDestination
    if ($reportParent) {
        New-Item -ItemType Directory -Path $reportParent -Force | Out-Null
    }
    $results | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $reportDestination -Encoding utf8
    if (-not $KeepArtifacts -and (Test-Path -LiteralPath $testRoot)) {
        Assert-ChildPath -Path $testRoot -Root $tempBase
        Remove-Item -LiteralPath $testRoot -Recurse -Force
    }
}

if ($gateError) {
    throw $gateError
}
Write-Host "Windows release gate aprovado. Relatório: $reportDestination"
