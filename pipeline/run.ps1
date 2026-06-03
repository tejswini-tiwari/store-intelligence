# Store Intelligence Pipeline Runner (PowerShell)
# Reads data/cameras.json to get store_id, camera_id, role, clip_start per clip.
#
# Usage:
#   powershell -File pipeline\run.ps1
#   powershell -File pipeline\run.ps1 -ClipsDir "C:\path\to\clips"
#
# Environment variables (set in .env or shell before running):
#   API_URL - defaults to http://localhost:8000
#   YOLO_MODEL - defaults to yolov8m.pt

param(
    [string]$ClipsDir = ".\data\clips",
    [string]$CamerasFile = ".\data\cameras.json",
    [string]$LayoutFile = ".\data\store_layout.json"
)

$API_URL = if ($env:API_URL) { $env:API_URL } else { "http://localhost:8000" }
$MODEL = if ($env:YOLO_MODEL) { $env:YOLO_MODEL } else { "yolov8m.pt" }

Write-Host "========================================"
Write-Host "Store Intelligence Pipeline"
Write-Host "========================================"
Write-Host "Clips dir:    $ClipsDir"
Write-Host "Cameras file: $CamerasFile"
Write-Host "Layout file:  $LayoutFile"
Write-Host "API URL:      $API_URL"
Write-Host "Model:        $MODEL"
Write-Host ""

if (-not (Test-Path $CamerasFile)) {
    Write-Host "ERROR: Cameras file not found: $CamerasFile" -ForegroundColor Red
    exit 1
}

if (-not (Test-Path $ClipsDir)) {
    Write-Host "ERROR: Clips directory not found: $ClipsDir" -ForegroundColor Red
    exit 1
}

$json = Get-Content $CamerasFile -Raw | ConvertFrom-Json
$hasClips = $false

foreach ($clipName in $json.PSObject.Properties.Name) {
    $config = $json.$clipName
    $clipPath = Join-Path $ClipsDir $clipName

    if (-not (Test-Path $clipPath)) {
        Write-Host "WARN: $clipPath not found, skipping" -ForegroundColor Yellow
        continue
    }

    $hasClips = $true
    $role = $config.role
    $clipStart = $config.clip_start
    $storeId = $config.store_id
    $cameraId = $config.camera_id

    if ($role -eq "exclude") {
        Write-Host "SKIP: $clipName has role exclude -- no events emitted"
        continue
    }

    Write-Host "Processing: $clipName"
    Write-Host "  Store:  $storeId"
    Write-Host "  Camera: $cameraId"
    Write-Host "  Role:   $role"
    Write-Host "  Clip:   $clipStart"

    $result = & python pipeline\detect.py `
        --video $clipPath `
        --store-id $storeId `
        --camera-id $cameraId `
        --role $role `
        --clip-start $clipStart `
        --model $MODEL `
        --layout $LayoutFile `
        --log-level INFO

    if ($LASTEXITCODE -ne 0) {
        Write-Host "ERROR: detect.py failed for $clipName" -ForegroundColor Red
    } else {
        Write-Host "Done: $clipName"
    }

    Write-Host ""
}

if (-not $hasClips) {
    Write-Host "WARN: No clips found in cameras.json exist in --clips dir" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "========================================"
Write-Host "Pipeline complete."
Write-Host "========================================"