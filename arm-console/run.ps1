$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$runtime = 'C:\Users\Lenovo\AppData\Local\SenseCraft Robotics\r\c929cf611806b458\python.exe'

if (-not (Test-Path -LiteralPath (Join-Path $root 'dist\index.html'))) {
    Push-Location $root
    try {
        npm install
        npm run build
    } finally {
        Pop-Location
    }
}

if (-not (Test-Path -LiteralPath $runtime)) {
    $runtime = 'python'
}

Push-Location $root
try {
    & $runtime -m backend.app @args
} finally {
    Pop-Location
}
