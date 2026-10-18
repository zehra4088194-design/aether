param(
    [string]$WorkerUrl = $env:AETHER_LICENSE_URL,
    [string]$AdminKey = $env:AETHER_ADMIN_KEY,
    [string]$LicenseKey = "",
    [int]$MaxActivations = 1,
    [string]$Note = ""
)

$ErrorActionPreference = "Stop"

if (-not $WorkerUrl) {
    throw "Set AETHER_LICENSE_URL or pass -WorkerUrl."
}

if (-not $AdminKey) {
    throw "Set AETHER_ADMIN_KEY or pass -AdminKey."
}

if (-not $LicenseKey) {
    $bytes = New-Object byte[] 16
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    $hex = ([BitConverter]::ToString($bytes) -replace '-', '').ToUpperInvariant()
    $LicenseKey = "AETH-$($hex.Substring(0, 4))-$($hex.Substring(4, 4))-$($hex.Substring(8, 4))-$($hex.Substring(12, 4))"
}

$body = @{
    license_key = $LicenseKey
    max_activations = $MaxActivations
    note = $Note
} | ConvertTo-Json -Depth 4

$response = Invoke-RestMethod -Method Post `
    -Uri ($WorkerUrl.TrimEnd("/") + "/v1/admin/issue") `
    -Headers @{ "X-Aether-Admin" = $AdminKey } `
    -ContentType "application/json" `
    -Body $body

Write-Host "License issued successfully."
Write-Host "Customer key: $LicenseKey"
Write-Host "Max activations: $MaxActivations"
if ($response.key_hash) {
    Write-Host "Key hash: $($response.key_hash)"
}
