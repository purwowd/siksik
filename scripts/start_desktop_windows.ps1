#Requires -Version 5.1
<#
.SYNOPSIS
  SATRIA operator UI — native Windows (RAM Windows, bukan WSL).

.DESCRIPTION
  Jalankan di luar WSL (double-click .cmd / PowerShell Windows).
  Otomatis mencari folder repo siksik, lalu:
    Node Windows → npm/vite Windows → browser → proxy ke API WSL :8000
  Ctrl+C menghentikan Vite + bersihkan port.

  Tidak memanggil wsl.exe untuk UI. Backend tetap di WSL (terpisah).

.EXAMPLE
  Double-click:  scripts\start_desktop_windows.cmd
  Atau:          powershell -NoProfile -ExecutionPolicy Bypass -File ...
#>
$ErrorActionPreference = "Stop"

$ApiPort = if ($env:SADT_API_PORT) { $env:SADT_API_PORT } else { "8000" }
$UiPort = if ($env:SADT_UI_PORT) { $env:SADT_UI_PORT } else { "5173" }
$ApiHost = "127.0.0.1"
$ReadyUrl = "http://${ApiHost}:${ApiPort}/api/v1/ready"
$UiUrl = "http://${ApiHost}:${UiPort}"
$ApiWaitSeconds = 45

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
    return (Test-Path -LiteralPath $pkg) -and (Test-Path -LiteralPath $api)
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

  # Folder induk script (…\siksik\scripts\…)
  $scriptDir = $null
  if ($PSScriptRoot) {
    $scriptDir = $PSScriptRoot
  } elseif ($MyInvocation.MyCommand.Path) {
    $scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
  }
  if ($scriptDir) {
    [void]$candidates.Add((Join-Path $scriptDir ".."))
    [void]$candidates.Add($scriptDir)
  }

  # Path Windows umum (prioritas di atas \\wsl$ — I/O lebih cepat)
  $userProfile = $env:USERPROFILE
  foreach ($p in @(
      "C:\siksik",
      "D:\siksik",
      (Join-Path $userProfile "siksik"),
      (Join-Path $userProfile "Documents\siksik"),
      (Join-Path $userProfile "Desktop\siksik"),
      "C:\src\siksik",
      "C:\dev\siksik"
    )) {
    if ($p) { [void]$candidates.Add($p) }
  }

  # Repo hidup di filesystem WSL — Node tetap proses Windows (RAM Windows)
  foreach ($distro in (Get-WslDistroNames)) {
    foreach ($prefix in @("\\wsl.localhost", "\\wsl$")) {
      [void]$candidates.Add("$prefix\$distro\home\me\siksik")
      if ($env:USERNAME) {
        [void]$candidates.Add("$prefix\$distro\home\$($env:USERNAME)\siksik")
      }
    }
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

# --- Harus PowerShell Windows, bukan di dalam WSL ---
if ($env:WSL_DISTRO_NAME -or $env:WSL_INTEROP -or (Test-Path -LiteralPath "/proc/version")) {
  Write-Fail @"
Script ini harus dijalankan di Windows (luar WSL), bukan dari shell WSL.

Dari File Explorer / PowerShell Windows:
  C:\siksik\scripts\start_desktop_windows.cmd

Atau double-click file .cmd tersebut.
"@
  exit 1
}

Write-Step "Cari repo siksik"
$RepoRoot = Find-SiksikRepoRoot
if (-not $RepoRoot) {
  Write-Fail @"
Repo siksik tidak ditemukan otomatis.

Set path sekali (PowerShell), lalu jalankan lagi:
  [Environment]::SetEnvironmentVariable('SIKSIK_ROOT', 'C:\siksik', 'User')

Atau pastikan salah satu ada:
  C:\siksik
  %USERPROFILE%\siksik
  \\wsl.localhost\Ubuntu-24.04\home\me\siksik
"@
  exit 1
}

$FrontendDir = Join-Path $RepoRoot "frontend"
$onWslShare = ($RepoRoot -match '(?i)^\\\\wsl')
Write-Host "SATRIA desktop — UI native Windows (RAM Windows)" -ForegroundColor Green
Write-Host "  repo     $RepoRoot"
if ($onWslShare) {
  Write-Host "  catatan  Path \\wsl$ — Node tetap Windows; I/O lebih lambat dari C:\siksik" -ForegroundColor Yellow
}
Write-Host "  UI       $UiUrl"
Write-Host "  API      $ReadyUrl  (backend WSL)"
Write-Host "  Ctrl+C   stop"
Write-Host ""

# --- Node Windows saja ---
Write-Step "Cek Node.js Windows (bukan WSL)"
$nodeCmd = Get-Command node -ErrorAction SilentlyContinue
$npmCmd = Get-Command npm -ErrorAction SilentlyContinue
$npxCmd = Get-Command npx -ErrorAction SilentlyContinue
if (-not $nodeCmd -or -not $npmCmd -or -not $npxCmd) {
  Write-Fail @"
Node.js / npm / npx tidak ada di PATH Windows.
Install Node LTS: https://nodejs.org
Lalu buka ulang terminal Windows (bukan WSL).
"@
  exit 1
}

$nodePath = $nodeCmd.Source
if ($nodePath -match '(?i)\\windows\\system32\\wsl|\\wsl\.exe$|\\AppData\\Local\\Microsoft\\WindowsApps\\wsl') {
  Write-Fail "Command 'node' mengarah ke WSL ($nodePath). Install Node Windows asli."
  exit 1
}
# Tolak binary Linux yang kebaca lewat \\wsl$
try {
  $fs = [System.IO.File]::OpenRead($nodePath)
  $buf = New-Object byte[] 4
  [void]$fs.Read($buf, 0, 4)
  $fs.Close()
  # ELF magic = Linux
  if ($buf[0] -eq 0x7F -and $buf[1] -eq [byte][char]'E' -and $buf[2] -eq [byte][char]'L' -and $buf[3] -eq [byte][char]'F') {
    Write-Fail "Binary node adalah ELF/Linux. Pakai Node Windows (nodejs.org)."
    exit 1
  }
} catch { }

Write-Host ("  node  {0}" -f (& node -v))
Write-Host ("  path  {0}" -f $nodePath)
Write-Host ("  npm   {0}" -f (& npm -v))
Write-Host "  runtime Windows-native — UI tidak memakai RAM WSL" -ForegroundColor DarkGreen

# --- API WSL ---
Write-Step "Menunggu backend WSL di $ReadyUrl"
$deadline = (Get-Date).AddSeconds($ApiWaitSeconds)
$apiOk = $false
while ((Get-Date) -lt $deadline) {
  try {
    $res = Invoke-WebRequest -Uri $ReadyUrl -UseBasicParsing -TimeoutSec 2
    if ($res.StatusCode -ge 200 -and $res.StatusCode -lt 300) {
      $apiOk = $true
      break
    }
  } catch {
    Start-Sleep -Milliseconds 800
  }
}
if (-not $apiOk) {
  Write-Fail @"
Backend belum merespons di $ReadyUrl

Di terminal WSL (terpisah):
  cd ~/siksik && bash scripts/start_poc.sh

Atau API saja:
  cd ~/siksik/backend && source .venv/bin/activate
  uvicorn app.main:app --host 0.0.0.0 --port $ApiPort --workers 1

Kalau 127.0.0.1 gagal (Admin Windows):
  $RepoRoot\scripts\expose_lan.cmd
"@
  exit 1
}
Write-Host "  API ready"

# --- Port UI ---
Write-Step "Cek port UI :$UiPort"
try {
  $listener = [System.Net.Sockets.TcpListener]::new(
    [System.Net.IPAddress]::Parse($ApiHost),
    [int]$UiPort
  )
  $listener.Start()
  $listener.Stop()
} catch {
  Write-Fail @"
Port $UiPort sudah dipakai (sering sisa Vite di WSL).

Stop Vite di WSL, atau:
  set SADT_UI_PORT=5174
  $RepoRoot\scripts\start_desktop_windows.cmd
"@
  exit 1
}
Write-Host "  port bebas"

# --- deps (npm Windows di folder frontend) ---
Write-Step "Dependencies frontend (npm Windows)"
Push-Location $FrontendDir
try {
  if (-not (Test-Path -LiteralPath (Join-Path $FrontendDir "node_modules"))) {
    Write-Host "  npm install (pertama kali bisa lama)…"
    & npm.cmd install
    if ($LASTEXITCODE -ne 0) {
      Write-Fail "npm install gagal (exit $LASTEXITCODE)"
      exit $LASTEXITCODE
    }
  } else {
    Write-Host "  node_modules ada — skip install"
  }
} finally {
  Pop-Location
}

$env:SADT_API_PORT = "$ApiPort"
$env:SATRIA_API_PORT = "$ApiPort"
$env:SADT_UI_PORT = "$UiPort"
$env:SATRIA_UI_PORT = "$UiPort"
Remove-Item Env:SATRIA_DESKTOP -ErrorAction SilentlyContinue

Write-Step "Start Vite Windows + buka browser"
$openerArgs = @(
  "-NoProfile",
  "-ExecutionPolicy", "Bypass",
  "-Command",
  @"
`$port = $UiPort
`$url = '$UiUrl'
for (`$i = 0; `$i -lt 90; `$i++) {
  try {
    `$c = New-Object System.Net.Sockets.TcpClient
    `$c.Connect('127.0.0.1', `$port)
    `$c.Close()
    Start-Process `$url
    exit 0
  } catch {
    Start-Sleep -Milliseconds 400
  }
}
"@
)
$opener = Start-Process -FilePath "powershell.exe" `
  -ArgumentList $openerArgs `
  -WindowStyle Hidden `
  -PassThru

function Stop-SatriaUiPort {
  param([int]$Port)
  try {
    $conn = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    foreach ($c in @($conn)) {
      if ($c.OwningProcess) {
        & taskkill.exe /PID $c.OwningProcess /T /F 2>$null | Out-Null
      }
    }
  } catch { }
}

$exitCode = 0
Write-Host ""
Write-Host "UI  $UiUrl   (proses Node di Windows)" -ForegroundColor Green
Write-Host "API $ReadyUrl   (backend WSL)" -ForegroundColor Green
Write-Host "Ctrl+C untuk stop." -ForegroundColor Yellow
Write-Host ""

Push-Location $FrontendDir
try {
  & npx.cmd vite --host $ApiHost --port $UiPort --strictPort
  $exitCode = $LASTEXITCODE
} catch {
  $exitCode = 0
} finally {
  Pop-Location
  Stop-SatriaUiPort -Port ([int]$UiPort)
  if ($null -ne $opener -and -not $opener.HasExited) {
    Stop-Process -Id $opener.Id -Force -ErrorAction SilentlyContinue
  }
  Write-Host ""
  Write-Host "UI Windows stopped." -ForegroundColor DarkGray
}

exit $exitCode
