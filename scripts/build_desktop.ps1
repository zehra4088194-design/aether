param(
    [string]$Python = "python",
    [string]$UpdaterPrivateKeyPath = "",
    [string]$BuildRoot = "",
    [string]$CargoTargetDir = "",
    [switch]$SkipDependencies,
    [switch]$SkipOptionalAssets,
    [switch]$SkipBackend
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$browserDirectory = Join-Path $projectRoot "src-tauri\resources\ms-playwright"
$voiceDirectory = Join-Path $projectRoot "src-tauri\resources\piper-voices"

Push-Location $projectRoot
try {
    if (-not $SkipDependencies) {
        & $Python -m pip install --default-timeout 1000 -r requirements.txt
        if ($LASTEXITCODE -ne 0) { throw "Python dependency installation failed." }
        & npm ci
        if ($LASTEXITCODE -ne 0) { throw "npm dependency installation failed." }
    }

    if (-not $SkipOptionalAssets) {
        New-Item -ItemType Directory -Path $browserDirectory -Force | Out-Null
        New-Item -ItemType Directory -Path $voiceDirectory -Force | Out-Null
        $env:PLAYWRIGHT_BROWSERS_PATH = $browserDirectory
        & $Python -m playwright install chromium
        if ($LASTEXITCODE -ne 0) { throw "Playwright Chromium installation failed." }
        & $Python -m piper.download_voices --data-dir $voiceDirectory en_US-amy-medium en_US-ryan-medium en_GB-alba-medium en_US-joe-medium
        if ($LASTEXITCODE -ne 0) { throw "Piper voice model installation failed." }
    }

    if ($SkipBackend) {
        $backendBinary = Join-Path $projectRoot "src-tauri\binaries\aether-backend-x86_64-pc-windows-msvc.exe"
        if (-not (Test-Path -LiteralPath $backendBinary)) {
            throw "Cannot skip backend build because the sidecar is missing at $backendBinary."
        }
    }
    else {
        & (Join-Path $PSScriptRoot "build_backend.ps1") -Python $Python -BuildRoot $BuildRoot
    }

    if (-not (Test-Path "src-tauri\icons\icon.ico")) {
        & npm run tauri -- icon "src-tauri\icons\aether-icon.svg"
        if ($LASTEXITCODE -ne 0) { throw "Tauri icon generation failed." }
    }

    if (-not $UpdaterPrivateKeyPath) {
        $candidate = Join-Path $projectRoot ".aether-data\aether-updater.key"
        if (Test-Path -LiteralPath $candidate) { $UpdaterPrivateKeyPath = $candidate }
    }
    if ($UpdaterPrivateKeyPath) {
        $resolvedSigningKey = (Resolve-Path $UpdaterPrivateKeyPath).Path
        # The bundler reads TAURI_SIGNING_PRIVATE_KEY; the standalone signer
        # also accepts the explicit *_PATH form.
        $env:TAURI_SIGNING_PRIVATE_KEY = $resolvedSigningKey
        $env:TAURI_SIGNING_PRIVATE_KEY_PATH = $resolvedSigningKey
        if (-not $env:TAURI_SIGNING_PRIVATE_KEY_PASSWORD) {
            $env:TAURI_SIGNING_PRIVATE_KEY_PASSWORD = & $Python -c "import keyring; print(keyring.get_password('Aether Build Signing','updater-private-key-password') or '')"
        }
    }

    if ($CargoTargetDir) { $env:CARGO_TARGET_DIR = [System.IO.Path]::GetFullPath($CargoTargetDir) }
    & npm run tauri -- build
    if ($LASTEXITCODE -ne 0) { throw "Tauri desktop build failed." }
}
finally {
    Pop-Location
}
