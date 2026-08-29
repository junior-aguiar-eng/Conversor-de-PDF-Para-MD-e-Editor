param(
    [Parameter(Mandatory = $true)]
    [string]$Executable,
    [int]$StartupSeconds = 8
)

$ErrorActionPreference = "Stop"
$resolved = Resolve-Path -LiteralPath $Executable
$workingDirectory = Split-Path -Parent $resolved.Path
$process = Start-Process -FilePath $resolved.Path -WorkingDirectory $workingDirectory -WindowStyle Hidden -PassThru

try {
    Start-Sleep -Seconds $StartupSeconds
    if ($process.HasExited) {
        throw "A release encerrou durante a inicialização com código $($process.ExitCode)."
    }
    Write-Output "RELEASE_START_OK PID=$($process.Id)"
} finally {
    if (-not $process.HasExited) {
        Stop-Process -Id $process.Id -Force
        $process.WaitForExit()
    }
}
