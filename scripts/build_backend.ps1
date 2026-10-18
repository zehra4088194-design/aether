param(
    [string]$Python = "python",
    [string]$BuildRoot = ""
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$BuildRoot = if ($BuildRoot) { $BuildRoot } elseif ($env:AETHER_BUILD_ROOT) { $env:AETHER_BUILD_ROOT } else { Join-Path $projectRoot "build-artifacts" }
$BuildRoot = [System.IO.Path]::GetFullPath($BuildRoot)
$pyinstallerWork = Join-Path $BuildRoot "pyinstaller-work"
$pyinstallerDist = Join-Path $BuildRoot "pyinstaller-dist"
$binaryDirectory = Join-Path $projectRoot "src-tauri\binaries"
$sourceBinary = Join-Path $pyinstallerDist "aether-backend.exe"
$targetBinary = Join-Path $binaryDirectory "aether-backend-x86_64-pc-windows-msvc.exe"

Push-Location $projectRoot
try {
    New-Item -ItemType Directory -Path $BuildRoot -Force | Out-Null
    & $Python -m PyInstaller --clean --noconfirm --workpath $pyinstallerWork --distpath $pyinstallerDist "aether_backend.spec"
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed with exit code $LASTEXITCODE." }
    if (-not (Test-Path -LiteralPath $sourceBinary)) { throw "Sidecar was not produced at $sourceBinary." }
    New-Item -ItemType Directory -Path $binaryDirectory -Force | Out-Null
    Copy-Item -LiteralPath $sourceBinary -Destination $targetBinary -Force
    Write-Host "Aether backend sidecar: $targetBinary"
}
finally {
    Pop-Location
}
