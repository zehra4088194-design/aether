param(
    [string]$Executable = "src-tauri\binaries\aether-backend-x86_64-pc-windows-msvc.exe",
    [int]$Port = 18765,
    [string]$TempRoot = ""
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$executablePath = [System.IO.Path]::GetFullPath((Join-Path $projectRoot $Executable))
if (-not (Test-Path -LiteralPath $executablePath)) { throw "Sidecar missing: $executablePath" }

$TempRoot = if ($TempRoot) { [System.IO.Path]::GetFullPath($TempRoot) } else { Join-Path $projectRoot "build-artifacts\sidecar-smoke" }
$extractDirectory = Join-Path $TempRoot "extract"
$localAppDirectory = Join-Path $TempRoot "local-app-data"
New-Item -ItemType Directory -Path $extractDirectory, $localAppDirectory -Force | Out-Null

$token = "aether-smoke-$([guid]::NewGuid())"
$env:TEMP = $extractDirectory
$env:TMP = $extractDirectory
$env:LOCALAPPDATA = $localAppDirectory
$env:AETHER_API_TOKEN = $token
$env:AETHER_API_PORT = [string]$Port
$env:AETHER_PARENT_PID = [string]$PID
$env:PLAYWRIGHT_BROWSERS_PATH = (Resolve-Path (Join-Path $projectRoot "src-tauri\resources\ms-playwright")).Path
$env:AETHER_PIPER_VOICE_DIR = (Resolve-Path (Join-Path $projectRoot "src-tauri\resources\piper-voices")).Path

$process = Start-Process -FilePath $executablePath -PassThru -WindowStyle Hidden
try {
    $health = $null
    for ($attempt = 0; $attempt -lt 120; $attempt++) {
        if ($process.HasExited) { throw "Sidecar exited before becoming healthy (code $($process.ExitCode))." }
        try {
            $health = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 2
            break
        }
        catch {
            Start-Sleep -Seconds 1
        }
    }
    if (-not $health.ok) { throw "Sidecar health endpoint did not become ready." }

    $headers = @{ "X-Aether-Token" = $token }
    $state = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/v1/state" -Headers $headers -TimeoutSec 60
    [pscustomobject]@{
        Health = $health.ok
        App = $state.app.name
        Version = $state.app.version
        Tier = $state.tier
        Plugins = $state.plugins.Count
        Voices = $state.voice.profiles.Count
        Pid = $process.Id
    }
}
finally {
    if (-not $process.HasExited) {
        & "$env:SystemRoot\System32\taskkill.exe" /PID $process.Id /T /F 2>&1 | Out-Null
    }
}
