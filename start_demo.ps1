# One-click demo launcher: local FastAPI + Cloudflare quick tunnel
# Usage:  powershell -ExecutionPolicy Bypass -File .\start_demo.ps1
# Optional: -Port 8001
param([int]$Port = 8000)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$tunnel = $null
$startedServer = $false

function Test-Port {
    param([int]$P)
    return [bool](Get-NetTCPConnection -LocalPort $P -State Listen -ErrorAction SilentlyContinue)
}

Write-Host "==> Listing Generator demo" -ForegroundColor Cyan

# 1. Start backend if the port is free
if (Test-Port $Port) {
    Write-Host "    Backend already running on port $Port, reuse it." -ForegroundColor DarkGray
} else {
    if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
        Write-Host "    [ERROR] python not found in PATH." -ForegroundColor Red
        exit 1
    }
    Write-Host "    Starting backend on port $Port ..." -ForegroundColor Yellow
    $env:PORT = "$Port"
    Start-Process -FilePath "python" -ArgumentList "web/app.py" -WorkingDirectory $root -WindowStyle Minimized | Out-Null
    $startedServer = $true
    $ok = $false
    for ($i = 0; $i -lt 30; $i++) {
        Start-Sleep -Seconds 1
        if (Test-Port $Port) { $ok = $true; break }
    }
    if (-not $ok) {
        Write-Host "    [ERROR] backend did not open port $Port within 30s." -ForegroundColor Red
        exit 1
    }
    Write-Host "    Backend is up." -ForegroundColor Green
}

# 2. Ensure cloudflared exists (download to %TEMP% if missing)
$cf = Join-Path $env:TEMP "cloudflared.exe"
if (-not (Test-Path $cf)) {
    Write-Host "    Downloading cloudflared ..." -ForegroundColor Yellow
    $url = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe"
    Invoke-WebRequest -Uri $url -OutFile $cf -UseBasicParsing -TimeoutSec 180
    if (-not (Test-Path $cf)) {
        Write-Host "    [ERROR] cloudflared download failed." -ForegroundColor Red
        exit 1
    }
}

# 3. Start tunnel and capture the public URL
$logOut = Join-Path $env:TEMP "listinggen_tunnel.out.log"
$logErr = Join-Path $env:TEMP "listinggen_tunnel.err.log"
Remove-Item $logOut, $logErr -ErrorAction SilentlyContinue

Write-Host "    Opening Cloudflare tunnel ..." -ForegroundColor Yellow
$tunnel = Start-Process -FilePath $cf `
    -ArgumentList "tunnel", "--url", "http://127.0.0.1:$Port", "--no-autoupdate" `
    -RedirectStandardOutput $logOut -RedirectStandardError $logErr `
    -NoNewWindow -PassThru

$publicUrl = $null
for ($i = 0; $i -lt 45; $i++) {
    Start-Sleep -Seconds 1
    if (Test-Path $logErr) {
        $m = Select-String -Path $logErr -Pattern "https://[a-z0-9-]+\.trycloudflare\.com" -ErrorAction SilentlyContinue |
            Select-Object -First 1
        if ($m) { $publicUrl = $m.Matches[0].Value; break }
    }
}

if (-not $publicUrl) {
    Write-Host "    [ERROR] Tunnel URL not found, check $logErr" -ForegroundColor Red
    if ($tunnel) { Stop-Process -Id $tunnel.Id -Force -ErrorAction SilentlyContinue }
    exit 1
}

$check = "000"
for ($i = 0; $i -lt 10; $i++) {
    $check = curl.exe -s -o NUL -w "%{http_code}" --noproxy "*" --max-time 15 "$publicUrl/"
    if ($check -eq "200") { break }
    Start-Sleep -Seconds 3
}
if ($check -eq "200") {
    Write-Host "    Public URL verified: HTTP 200" -ForegroundColor Green
} else {
    Write-Host "    Public URL returned HTTP $check; DNS may still be propagating." -ForegroundColor Yellow
}

Write-Host ""
Write-Host "==============================================================" -ForegroundColor Cyan
Write-Host "  DEMO URL: $publicUrl" -ForegroundColor Green
Write-Host "==============================================================" -ForegroundColor Cyan
Write-Host "  Tips:"
Write-Host "   - Keep this window open. Closing it stops the tunnel."
Write-Host "   - Local page:  http://127.0.0.1:$Port"
Write-Host "   - Anyone with this URL can consume your LLM quota."
$stopHint = if ($startedServer) { "tunnel and backend." } else { "tunnel (backend was already running)." }
Write-Host "   - Press Ctrl+C to stop $stopHint"
Write-Host ""

try {
    Wait-Process -Id $tunnel.Id
} finally {
    Write-Host "`n==> Shutting down ..." -ForegroundColor Yellow
    if ($tunnel -and -not $tunnel.HasExited) {
        Stop-Process -Id $tunnel.Id -Force -ErrorAction SilentlyContinue
    }
    if ($startedServer) {
        $p = (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue).OwningProcess
        if ($p) { Stop-Process -Id $p -Force -ErrorAction SilentlyContinue }
    }
    Write-Host "    Done." -ForegroundColor Green
}
