$ErrorActionPreference = 'Stop'
$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$cli = Join-Path $scriptRoot 'summon_arm.py'
$runtime = 'python'
$sensecraftRoot = Join-Path $env:LOCALAPPDATA 'SenseCraft Robotics\r'
if (Test-Path -LiteralPath $sensecraftRoot) {
    foreach ($directory in (Get-ChildItem -LiteralPath $sensecraftRoot -Directory | Sort-Object LastWriteTime -Descending)) {
        $candidate = Join-Path $directory.FullName 'python.exe'
        if (Test-Path -LiteralPath $candidate) {
            $runtime = $candidate
            break
        }
    }
}
& $runtime $cli @args
exit $LASTEXITCODE
