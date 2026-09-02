#Requires -Version 5.1
<#
.SYNOPSIS
  SATRIA Tauri desktop on native Windows (not browser-only Vite).

.DESCRIPTION
  Double-click scripts\start_desktop_windows.cmd from Windows (not WSL).
  Starts Tauri window (WebView) against frontend on :5175 and API on :8000.
  Tauri/npm run from NTFS C:\siksik (CMD cannot use \\wsl$\). Source is
  rsynced from WSL ~/siksik on each start. Backend stays in WSL.

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
    if ($full.StartsWith('\\')) { continue }
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

function Test-WslApiReady {
  $prev = $ErrorActionPreference
  $ErrorActionPreference = "Continue"
  try {
    foreach ($distro in (Get-WslDistroNames)) {
      $out = & wsl.exe -d $distro -e curl -sS -m 2 "http://127.0.0.1:$ApiPort/api/v1/ready" 2>$null | Out-String
      if ($out -match '"status"\s*:\s*"ok"') { return $true }
    }
  } catch { }
  finally { $ErrorActionPreference = $prev }
  return $false
}

function Test-IsAdmin {
  $id = [Security.Principal.WindowsIdentity]::GetCurrent()
  $p = New-Object Security.Principal.WindowsPrincipal($id)
  return $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Test-PortproxyOnApiPort {
  $show = ((& netsh.exe interface portproxy show v4tov4 2>$null | Out-String) -replace "`0", "")
  return [bool]($show -match "(?m)^\s*0\.0\.0\.0\s+$ApiPort\b" -or $show -match "(?m)^\s*127\.0\.0\.1\s+$ApiPort\b")
}

function Clear-PortproxyBlackhole([string]$CleanerPath) {
  if (-not (Test-PortproxyOnApiPort)) { return $true }
  # Single string: Start-Process -Verb RunAs mishandles ArgumentList arrays with spaces.
  $arg = "-NoProfile -ExecutionPolicy Bypass -File `"$CleanerPath`""
  try {
    if (Test-IsAdmin) {
      Write-Host "  hapus portproxy $ApiPort/5173 (admin)"
      $p = Start-Process -FilePath "powershell.exe" -Wait -PassThru -ArgumentList $arg
    } else {
      Write-Host "  UAC: hapus portproxy $ApiPort/5173 (menghalangi localhost WSL). Izinkan sekali."
      $p = Start-Process -FilePath "powershell.exe" -Verb RunAs -Wait -PassThru -ArgumentList $arg
    }
    if ($null -eq $p) { return -not (Test-PortproxyOnApiPort) }
  } catch {
    return -not (Test-PortproxyOnApiPort)
  }
  return -not (Test-PortproxyOnApiPort)
}

function Start-WslSatriaApi {
  Write-Step "Backend not ready - starting API in WSL (no WSL Vite)"
  $script = @"
set -e
ROOT="`$HOME/siksik"
if [ ! -f "`$ROOT/backend/app/main.py" ]; then ROOT="/home/me/siksik"; fi
cd "`$ROOT/backend"
if [ ! -f .venv/bin/activate ]; then echo NO_VENV; exit 2; fi
# shellcheck disable=SC1091
source .venv/bin/activate
nohup uvicorn app.main:app --host 0.0.0.0 --port $ApiPort --workers 1 >/tmp/satria-api.log 2>&1 &
echo STARTED:`$!
"@
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

function Stop-WslListenersOnApiPort {
  $script = @"
if command -v fuser >/dev/null 2>&1; then
  fuser -k ${ApiPort}/tcp >/dev/null 2>&1 || true
elif command -v lsof >/dev/null 2>&1; then
  pids=`$(lsof -ti:${ApiPort} || true)
  if [ -n "`$pids" ]; then kill -9 `$pids || true; fi
fi
"@
  $prev = $ErrorActionPreference
  $ErrorActionPreference = "Continue"
  try {
    foreach ($distro in (Get-WslDistroNames)) {
      & wsl.exe -d $distro -e bash -lc $script 2>$null | Out-Null
    }
  } catch { }
  finally { $ErrorActionPreference = $prev }
}

function Wait-WslApiReady([int]$Seconds = 25) {
  $deadline = (Get-Date).AddSeconds($Seconds)
  while ((Get-Date) -lt $deadline) {
    if (Test-WslApiReady) { return $true }
    Start-Sleep -Milliseconds 800
  }
  return $false
}

function Repair-WslLocalhostForwarding {
  Write-Host "  restart API di WSL supaya Windows localhost forwarding menempel"
  Stop-WslListenersOnApiPort
  Start-Sleep -Seconds 4
  if (Wait-WslApiReady -Seconds 25) { return $true }
  [void](Start-WslSatriaApi)
  return (Wait-WslApiReady -Seconds 25)
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

function Get-SmartAppControlState {
  try {
    $item = Get-ItemProperty -LiteralPath "HKLM:\SYSTEM\CurrentControlSet\Control\CI\Policy" `
      -Name "VerifiedAndReputablePolicyState" -ErrorAction Stop
    switch ([int]$item.VerifiedAndReputablePolicyState) {
      0 { return "off" }
      1 { return "on" }
      2 { return "evaluation" }
      default { return "unknown" }
    }
  } catch {
    return "unknown"
  }
}

function Initialize-SatriaCargoTarget {
  # WDAC/AppLocker often blocks unsigned cargo output under C:\siksik\...\target.
  # User LocalAppData is the usual allowed path for unsigned lab binaries.
  $dir = Join-Path $env:LOCALAPPDATA "satria-cargo-target"
  New-Item -ItemType Directory -Force -Path $dir | Out-Null
  $env:CARGO_TARGET_DIR = $dir
  Write-Host "  CARGO_TARGET_DIR  $dir"
}

function Write-AppControlHint {
  Write-Host ""
  Write-Host @"
Windows memblokir satria-desktop.exe (os error 4551 / Application Control).
Unsigned cargo debug build ditahan Smart App Control / WDAC.

Matikan Smart App Control (lab), lalu buka SATRIA lagi:
  Windows Security → App & browser control → Smart App Control → Off

Jangan edit registry VerifiedAndReputablePolicyState (bisa mengunci OS).
"@ -ForegroundColor Yellow
}

# CMD/npm/cargo need an NTFS tree. \\wsl$\ cannot be net-use'd (error 64).
function ConvertTo-WslPath([string]$WinPath) {
  $full = [IO.Path]::GetFullPath($WinPath)
  if ($full -match '^([A-Za-z]):\\(.*)$') {
    $rest = $Matches[2].Replace('\', '/')
    return "/mnt/$($Matches[1].ToLowerInvariant())/$rest"
  }
  throw "Not an NTFS path: $WinPath"
}

function Get-WindowsMirrorRoot {
  foreach ($key in @("SIKSIK_ROOT", "SATRIA_ROOT", "SADT_ROOT")) {
    $v = [Environment]::GetEnvironmentVariable($key, "Process")
    if (-not $v) { $v = [Environment]::GetEnvironmentVariable($key, "User") }
    if (-not $v) { $v = [Environment]::GetEnvironmentVariable($key, "Machine") }
    if (-not $v) { continue }
    $v = $v.Trim().TrimEnd('\', '/')
    if ($v.StartsWith('\\')) { continue }
    try { return [IO.Path]::GetFullPath($v) } catch { }
  }
  return "C:\siksik"
}

function Sync-WslSourceToWindows([string]$WinRoot) {
  $dstWsl = ConvertTo-WslPath $WinRoot
  $script = @"
set -e
SRC=`"`$HOME/siksik`"
if [ ! -f `"`$SRC/desktop/package.json`" ]; then SRC="/home/me/siksik"; fi
if [ ! -f `"`$SRC/desktop/package.json`" ]; then echo NO_WSL_REPO; exit 2; fi
if ! command -v rsync >/dev/null 2>&1; then echo NO_RSYNC; exit 3; fi
DST="$dstWsl"
mkdir -p `"`$DST/frontend`" `"`$DST/desktop`" `"`$DST/backend`" `"`$DST/scripts`"
# NTFS via /mnt/c is slow: only the trees Tauri/npm need (not android/ios/data/.venv/target).
RSYNC=(rsync -a --delete --no-perms --no-owner --no-group --modify-window=1)
"`${RSYNC[@]}" --exclude node_modules --exclude dist --exclude __pycache__ \
  `"`$SRC/frontend/`" `"`$DST/frontend/`"
"`${RSYNC[@]}" --exclude node_modules --exclude target --exclude __pycache__ \
  `"`$SRC/desktop/`" `"`$DST/desktop/`"
"`${RSYNC[@]}" --exclude .venv --exclude data --exclude __pycache__ --exclude .pytest_cache \
  `"`$SRC/backend/`" `"`$DST/backend/`"
"`${RSYNC[@]}" `"`$SRC/scripts/`" `"`$DST/scripts/`"
echo SYNCED
"@
  $prevEap = $ErrorActionPreference
  $ErrorActionPreference = "Continue"
  try {
    foreach ($distro in (Get-WslDistroNames)) {
      try {
        Write-Host "  rsync $distro -> $WinRoot"
        $out = $script | & wsl.exe -d $distro -e bash -s 2>&1 | Out-String
        if ($out -match 'SYNCED') {
          Write-Host "  source synced from WSL"
          return $true
        }
        if ($out.Trim()) { Write-Host $out.Trim() }
      } catch {
        continue
      }
    }
  } finally {
    $ErrorActionPreference = $prevEap
  }
  return $false
}

# Real Linux WSL userspace only. Windows processes started from \\wsl$\ or
# wsl.exe inherit WSL_INTEROP / WSL_DISTRO_NAME; /proc/version is also visible
# when cwd is on the WSL filesystem. Those are not "inside bash".
function Test-InsideLinuxWsl {
  if ($env:OS -eq "Windows_NT") { return $false }
  if ($env:SystemRoot -and (Test-Path -LiteralPath (Join-Path $env:SystemRoot "System32\cmd.exe"))) {
    return $false
  }
  if ($env:WSL_DISTRO_NAME -or $env:WSL_INTEROP) { return $true }
  try {
    return ((Get-Content -LiteralPath "/proc/version" -ErrorAction Stop) -match "Microsoft")
  } catch {
    return $false
  }
}

if (Test-InsideLinuxWsl) {
  Write-Fail @"
Run this from Windows (Explorer / shortcut SATRIA), not from a WSL shell.

  Double-click SATRIA on the Desktop
"@
  exit 1
}

Write-Step "Locate Windows repo (NTFS)"
$RepoRoot = Get-WindowsMirrorRoot
Write-Host "  mirror  $RepoRoot"

Write-Step "Sync frontend/desktop/backend from WSL (skip android, data, venv)"
if (-not (Sync-WslSourceToWindows $RepoRoot)) {
  Write-Fail @"
Failed to rsync WSL repo to $RepoRoot

In WSL:
  rsync -a --exclude node_modules --exclude .venv --exclude target --exclude .git \
    ~/siksik/ /mnt/c/siksik/
"@
  exit 1
}

if (-not (Test-SiksikRepo $RepoRoot)) {
  $found = Find-SiksikRepoRoot
  if ($found) { $RepoRoot = $found }
}
if (-not (Test-SiksikRepo $RepoRoot)) {
  Write-Fail @"
Repo not found at $RepoRoot (need analysisScope.ts + desktop/).

Edit in WSL ~/siksik; this script mirrors it to C:\siksik for Tauri.
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
Initialize-SatriaCargoTarget
$sacState = Get-SmartAppControlState
Write-Host "  Smart App Control  $sacState"
if ($sacState -eq "on") {
  Write-AppControlHint
}

Write-Step "Wait for backend $ReadyUrl"
$wslReady = Test-WslApiReady
if (-not (Test-ApiReady) -and $wslReady -and (Test-PortproxyOnApiPort)) {
  Write-Host "  API hidup di WSL, tapi Windows :$ApiPort kena portproxy (timeout)."
  $cleaner = Join-Path $RepoRoot "scripts\clear_wsl_portproxy.ps1"
  if (-not (Test-Path -LiteralPath $cleaner)) {
    Write-Fail "Missing $cleaner (rsync scripts/ from WSL failed)."
    exit 1
  }
  if (-not (Clear-PortproxyBlackhole -CleanerPath $cleaner)) {
    Write-Fail @"
Tolak UAC atau gagal hapus portproxy.

Jalankan PowerShell Administrator sekali:
  powershell -NoProfile -ExecutionPolicy Bypass -File $cleaner

Atau:
  netsh interface portproxy delete v4tov4 listenport=8000 listenaddress=0.0.0.0
  netsh interface portproxy delete v4tov4 listenport=5173 listenaddress=0.0.0.0
"@
    exit 1
  }
  Write-Host "  portproxy $ApiPort/5173 dihapus (task logon SATRIA-ClearWslPortproxy terpasang)"
  [void](Repair-WslLocalhostForwarding)
}

if (-not (Test-ApiReady) -and (Test-WslApiReady)) {
  Write-Host "  API WSL OK, Windows belum tembus 127.0.0.1:$ApiPort"
  [void](Repair-WslLocalhostForwarding)
}

if (-not (Test-ApiReady) -and -not (Test-WslApiReady)) {
  [void](Start-WslSatriaApi)
}
$deadline = (Get-Date).AddSeconds($ApiWaitSeconds)
$apiOk = $false
while ((Get-Date) -lt $deadline) {
  if (Test-ApiReady) { $apiOk = $true; break }
  Start-Sleep -Milliseconds 800
}
if (-not $apiOk) {
  $hint = ""
  if (Test-WslApiReady) {
    $hint = @"

API di WSL sudah OK; Windows tidak tembus 127.0.0.1:$ApiPort.
Jangan portproxy ke IP NAT WSL. Izinkan UAC, atau:
  powershell -NoProfile -ExecutionPolicy Bypass -File $RepoRoot\scripts\clear_wsl_portproxy.ps1
"@
  }
  Write-Fail @"
API not ready at $ReadyUrl
$hint
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

if ($exitCode -ne 0) {
  $sacNow = Get-SmartAppControlState
  if ($sacNow -eq "on" -or $sacNow -eq "evaluation") {
    Write-AppControlHint
  }
}

exit $exitCode
