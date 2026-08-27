#Requires -Version 5.1
<#
.SYNOPSIS
  SATRIA Tauri desktop on native Windows (not browser-only Vite).

.DESCRIPTION
  Double-click scripts\start_desktop_windows.cmd from Windows (not WSL).
  Starts Tauri window (WebView) against frontend on :5175 and API on :8000.
  Uses C:\siksik (satria stitch). Backend may already run in WSL; otherwise
  this script tries to start API via wsl.exe (no WSL Vite).

.EXAMPLE
  scripts\start_desktop_windows.cmd
#>
$ErrorActionPreference = "Stop"

$ApiPort = if ($env:SADT_API_PORT) { $env:SADT_API_PORT } else { "8000" }
$DesktopUiPort = if ($env:SATRIA_DESKTOP_UI_PORT) { $env:SATRIA_DESKTOP_UI_PORT } else { "5175" }
$ApiHost = "127.0.0.1"
$ReadyUrl = "http://${ApiHost}:${ApiPort}/api/v1/ready"
$DesktopUiUrl = "http://${ApiHost}:${DesktopUiPort}"
$ApiWaitSeconds = 90

function Write-Step([string]$Message) {
  Write-Host ""
  Write-Host "==> $Message" -ForegroundColor Cyan
}

function Write-Fail([string]$Message) {
  Write-Host ""
  Write-Host "ERROR: $Message" -ForegroundColor Red
}

function Test-SiksikRepo([string]$Root) {
  if ([string]::IsNullOrWhiteSpace($Root)) { return $false }
  try {
    if (-not (Test-Path -LiteralPath $Root)) { return $false }
    $pkg = Join-Path $Root "frontend\package.json"
    $api = Join-Path $Root "backend\app\main.py"
    $desktop = Join-Path $Root "desktop\package.json"
    $stitch = Join-Path $Root "frontend\src\features\operator\analysisScope.ts"
    return (Test-Path -LiteralPath $pkg) -and (Test-Path -LiteralPath $api) `
      -and (Test-Path -LiteralPath $desktop) -and (Test-Path -LiteralPath $stitch)
  } catch {
    return $false
  }
}

function Get-WslDistroNames {
  $names = New-Object System.Collections.Generic.List[string]
  try {
    $raw = & wsl.exe -l -q 2>$null
    if ($LASTEXITCODE -eq 0 -and $raw) {
      foreach ($line in @($raw)) {
        $n = ($line -replace "`0", "").Trim()
        if ($n) { [void]$names.Add($n) }
      }
    }
  } catch { }
  foreach ($fallback in @("Ubuntu-24.04", "Ubuntu", "Ubuntu-22.04")) {
    if (-not $names.Contains($fallback)) { [void]$names.Add($fallback) }
  }
  return $names
}

function Find-SiksikRepoRoot {
  $candidates = New-Object System.Collections.Generic.List[string]

  foreach ($key in @("SIKSIK_ROOT", "SATRIA_ROOT", "SADT_ROOT")) {
    $v = [Environment]::GetEnvironmentVariable($key, "Process")
    if (-not $v) { $v = [Environment]::GetEnvironmentVariable($key, "User") }
    if (-not $v) { $v = [Environment]::GetEnvironmentVariable($key, "Machine") }
    if ($v) { [void]$candidates.Add($v.Trim().TrimEnd('\', '/')) }
  }

  $scriptDir = $null
  if ($PSScriptRoot) {
    $scriptDir = $PSScriptRoot
  } elseif ($MyInvocation.MyCommand.Path) {
    $scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
  }
  if ($scriptDir) {
    [void]$candidates.Add((Join-Path $scriptDir ".."))
  }

  foreach ($p in @("C:\siksik", "D:\siksik")) {
    [void]$candidates.Add($p)
  }

  $seen = @{}
  foreach ($raw in $candidates) {
    try {
      $full = [System.IO.Path]::GetFullPath($raw)
    } catch {
      continue
    }
    $key = $full.ToLowerInvariant()
    if ($seen.ContainsKey($key)) { continue }
    $seen[$key] = $true
    if (Test-SiksikRepo $full) {
      return $full
    }
  }
  return $null
}

function Test-ApiReady {
  try {
    $res = Invoke-WebRequest -Uri $ReadyUrl -UseBasicParsing -TimeoutSec 2
    return ($res.StatusCode -ge 200 -and $res.StatusCode -lt 300)
  } catch {
    return $false
  }
}

function Start-WslSatriaApi {
  Write-Step "Backend not ready - starting API in WSL (no WSL Vite)"
  $script = @'
set -e
ROOT="$HOME/siksik"
if [ ! -f "$ROOT/backend/app/main.py" ]; then ROOT="/home/me/siksik"; fi
cd "$ROOT/backend"
if [ ! -f .venv/bin/activate ]; then echo NO_VENV; exit 2; fi
# shellcheck disable=SC1091
source .venv/bin/activate
nohup uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1 >/tmp/satria-api.log 2>&1 &
echo STARTED:$!
'@
  foreach ($distro in (Get-WslDistroNames)) {
    try {
      Write-Host "  wsl -d $distro ..."
      $out = & wsl.exe -d $distro -e bash -lc $script 2>&1 | Out-String
      if ($out -match 'STARTED:') {
        Write-Host "  API start requested ($distro)"
        return $true
      }
    } catch { continue }
  }
  return $false
}

function Stop-PortListeners([int]$Port) {
  try {
    $conn = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    foreach ($c in @($conn)) {
      if ($c.OwningProcess) {
        & taskkill.exe /PID $c.OwningProcess /T /F 2>$null | Out-Null
      }
    }
  } catch { }
}

function Ensure-CargoPath {
  $cargoBin = Join-Path $env:USERPROFILE ".cargo\bin"
  if (Test-Path -LiteralPath (Join-Path $cargoBin "cargo.exe")) {
    if ($env:Path -notlike "*$cargoBin*") {
      $env:Path = "$cargoBin;$env:Path"
    }
  }
  $cargo = Get-Command cargo -ErrorAction SilentlyContinue
  if (-not $cargo) {
    Write-Fail "cargo not in PATH. Install Rust (rustup) then reopen terminal."
    exit 1
  }
  Write-Host ("  cargo  {0}" -f (& cargo -V))
  Write-Host ("  rustc  {0}" -f (& rustc -V))
}

# --- Windows PowerShell only ---
if ($env:WSL_DISTRO_NAME -or $env:WSL_INTEROP -or (Test-Path -LiteralPath "/proc/version")) {
  Write-Fail @"
Run this from Windows (Explorer / PowerShell), not inside WSL.

  C:\siksik\scripts\start_desktop_windows.cmd
"@
  exit 1
}

Write-Step "Locate satria stitch repo"
$RepoRoot = Find-SiksikRepoRoot
if (-not $RepoRoot) {
  Write-Fail @"
Repo not found (need analysisScope.ts + desktop/).

  [Environment]::SetEnvironmentVariable('SIKSIK_ROOT', 'C:\siksik', 'User')
"@
  exit 1
}

$FrontendDir = Join-Path $RepoRoot "frontend"
$DesktopDir = Join-Path $RepoRoot "desktop"
Write-Host "SATRIA Tauri desktop (native Windows WebView)" -ForegroundColor Green
Write-Host "  repo     $RepoRoot"
Write-Host "  UI       $DesktopUiUrl  (Tauri devUrl)"
Write-Host "  API      $ReadyUrl"
Write-Host "  Ctrl+C   stop"
Write-Host ""

Write-Step "Check Node.js Windows"
$nodeCmd = Get-Command node -ErrorAction SilentlyContinue
$npmCmd = Get-Command npm -ErrorAction SilentlyContinue
if (-not $nodeCmd -or -not $npmCmd) {
  Write-Fail "Install Node.js LTS from https://nodejs.org then reopen terminal."
  exit 1
}
Write-Host ("  node  {0}" -f (& node -v))
Write-Host ("  npm   {0}" -f (& npm -v))

Write-Step "Check Rust toolchain"
Ensure-CargoPath

Write-Step "Wait for backend $ReadyUrl"
if (-not (Test-ApiReady)) {
  [void](Start-WslSatriaApi)
}
$deadline = (Get-Date).AddSeconds($ApiWaitSeconds)
$apiOk = $false
while ((Get-Date) -lt $deadline) {
  if (Test-ApiReady) { $apiOk = $true; break }
  Start-Sleep -Milliseconds 800
}
if (-not $apiOk) {
  Write-Fail @"
API not ready at $ReadyUrl

In WSL:
  cd ~/siksik/backend && source .venv/bin/activate
  uvicorn app.main:app --host 0.0.0.0 --port $ApiPort --workers 1

Do not run scripts/start_poc.sh in parallel (it starts WSL Vite).
"@
  exit 1
}
Write-Host "  API ready"

Write-Step "Free Tauri UI port :$DesktopUiPort"
Stop-PortListeners -Port ([int]$DesktopUiPort)
Start-Sleep -Milliseconds 400

Write-Step "npm install (frontend + desktop) if needed"
foreach ($dir in @($FrontendDir, $DesktopDir)) {
  Push-Location $dir
  try {
    if (-not (Test-Path -LiteralPath (Join-Path $dir "node_modules"))) {
      Write-Host "  npm install in $dir ..."
      & npm.cmd install
      if ($LASTEXITCODE -ne 0) {
        Write-Fail "npm install failed in $dir"
        exit $LASTEXITCODE
      }
    } else {
      Write-Host "  ok  $dir\node_modules"
    }
  } finally {
    Pop-Location
  }
}

# Desktop Vite port + stitch UI; beforeDevCommand inherits these.
$env:SATRIA_DESKTOP = "1"
$env:SATRIA_DESKTOP_UI_PORT = "$DesktopUiPort"
$env:SADT_UI_PORT = "$DesktopUiPort"
$env:SATRIA_UI_PORT = "$DesktopUiPort"
$env:SADT_API_PORT = "$ApiPort"
$env:SATRIA_API_PORT = "$ApiPort"

Write-Step "Start Tauri (npm run dev) - first cargo build can take several minutes"
Write-Host "  Window title: SATRIA - Sistem Analisis Terpadu" -ForegroundColor Yellow
Write-Host ""

Push-Location $DesktopDir
$exitCode = 0
try {
  & npm.cmd run dev
  $exitCode = $LASTEXITCODE
} catch {
  $exitCode = 1
} finally {
  Pop-Location
  Stop-PortListeners -Port ([int]$DesktopUiPort)
  Write-Host ""
  Write-Host "Tauri desktop stopped." -ForegroundColor DarkGray
}

exit $exitCode
