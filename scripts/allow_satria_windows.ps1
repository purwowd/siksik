#Requires -RunAsAdministrator
#Requires -Version 5.1
<#
.SYNOPSIS
  One-time Windows allowlist for SATRIA desktop (Defender, firewall, portproxy).

.DESCRIPTION
  Run once from Windows (double-click allow_satria_windows.cmd, UAC Yes):
  - Deletes stale portproxy on 8000/5173/5175 (blocks WSL localhost)
  - Registers AtStartup SYSTEM task SATRIA-ClearWslPortproxy
  - Registers AtLogOn task SATRIA-StartWslApi (wake WSL + satria-api systemd)
  - Adds Windows Defender path/process exclusions
  - Opens inbound firewall for SATRIA ports and cargo-built exe
  Third-party antivirus cannot be auto-configured; exclusions are printed.

.PARAMETER NoPause
  Skip "press any key" (used when elevated from start_desktop_windows.ps1).

.EXAMPLE
  scripts\allow_satria_windows.cmd
#>
param(
  [switch]$NoPause
)
$ErrorActionPreference = "Continue"

function Write-Step([string]$Message) {
  Write-Host ""
  Write-Host "==> $Message" -ForegroundColor Cyan
}

function Get-RepoRoot {
  $scriptDir = $null
  if ($PSScriptRoot) {
    $scriptDir = $PSScriptRoot
  } elseif ($MyInvocation.MyCommand.Path) {
    $scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
  }
  if ($scriptDir) {
    $parent = [IO.Path]::GetFullPath((Join-Path $scriptDir ".."))
    if (Test-Path -LiteralPath (Join-Path $parent "desktop\package.json")) {
      return $parent
    }
  }
  foreach ($key in @("SIKSIK_ROOT", "SATRIA_ROOT", "SADT_ROOT")) {
    $v = [Environment]::GetEnvironmentVariable($key, "Process")
    if (-not $v) { $v = [Environment]::GetEnvironmentVariable($key, "User") }
    if (-not $v) { $v = [Environment]::GetEnvironmentVariable($key, "Machine") }
    if ($v) {
      $full = [IO.Path]::GetFullPath($v.Trim().TrimEnd('\', '/'))
      if (Test-Path -LiteralPath (Join-Path $full "desktop\package.json")) { return $full }
    }
  }
  foreach ($p in @("C:\siksik", "D:\siksik")) {
    if (Test-Path -LiteralPath (Join-Path $p "desktop\package.json")) { return $p }
  }
  if ($scriptDir) { return [IO.Path]::GetFullPath((Join-Path $scriptDir "..")) }
  return "C:\siksik"
}

$RepoRoot = Get-RepoRoot
$CargoTarget = Join-Path $env:LOCALAPPDATA "satria-cargo-target"
$ReportTemp = Join-Path $env:TEMP "satria-desktop-reports"
$Cleaner = Join-Path $RepoRoot "scripts\clear_wsl_portproxy.ps1"

Write-Host "SATRIA Windows allowlist" -ForegroundColor Green
Write-Host "  repo  $RepoRoot"

Write-Step "Hapus portproxy 8000/5173/5175"
if (Test-Path -LiteralPath $Cleaner) {
  & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $Cleaner
} else {
  foreach ($port in @(8000, 5173, 5175)) {
    foreach ($addr in @("0.0.0.0", "127.0.0.1")) {
      & netsh.exe interface portproxy delete v4tov4 listenport=$port listenaddress=$addr 2>$null | Out-Null
    }
  }
}
Write-Host "  portproxy dibersihkan"
$task = Get-ScheduledTask -TaskName "SATRIA-ClearWslPortproxy" -ErrorAction SilentlyContinue
if ($task) {
  Write-Host "  task AtStartup  SATRIA-ClearWslPortproxy  OK"
} else {
  Write-Host "  WARN: task SATRIA-ClearWslPortproxy belum terdaftar" -ForegroundColor Yellow
}

Write-Step "Windows Defender exclusions"
$exclusionPaths = @(
  $RepoRoot,
  $CargoTarget,
  $ReportTemp,
  (Join-Path $RepoRoot "desktop"),
  (Join-Path $RepoRoot "frontend"),
  (Join-Path $RepoRoot "scripts")
)
$exclusionProcs = @(
  "satria-desktop.exe",
  "SATRIA.exe",
  "node.exe",
  "cargo.exe",
  "rustc.exe"
)
foreach ($path in $exclusionPaths) {
  try {
    New-Item -ItemType Directory -Force -Path $path -ErrorAction SilentlyContinue | Out-Null
    Add-MpPreference -ExclusionPath $path -ErrorAction Stop
    Write-Host "  path  $path"
  } catch {
    Write-Host "  skip path  $path  ($($_.Exception.Message))" -ForegroundColor DarkYellow
  }
}
foreach ($proc in $exclusionProcs) {
  try {
    Add-MpPreference -ExclusionProcess $proc -ErrorAction Stop
    Write-Host "  process  $proc"
  } catch {
    Write-Host "  skip process  $proc  ($($_.Exception.Message))" -ForegroundColor DarkYellow
  }
}

$exeCandidates = @(
  (Join-Path $CargoTarget "debug\satria-desktop.exe"),
  (Join-Path $CargoTarget "release\satria-desktop.exe")
)
foreach ($exe in $exeCandidates) {
  try {
    Add-MpPreference -ControlledFolderAccessAllowedApplications $exe -ErrorAction SilentlyContinue
  } catch { }
}

Write-Step "Firewall inbound (SATRIA)"
Get-NetFirewallRule -DisplayName "SATRIA*" -ErrorAction SilentlyContinue | Remove-NetFirewallRule -ErrorAction SilentlyContinue
Get-NetFirewallRule -DisplayName "SADT PoC LAN" -ErrorAction SilentlyContinue | Remove-NetFirewallRule -ErrorAction SilentlyContinue
try {
  New-NetFirewallRule `
    -DisplayName "SATRIA API UI Ports" `
    -Direction Inbound `
    -Action Allow `
    -Protocol TCP `
    -LocalPort 5173, 5175, 8000 `
    -Profile Any `
    -ErrorAction Stop | Out-Null
  Write-Host "  ports  5173,5175,8000"
} catch {
  Write-Host "  WARN firewall ports: $($_.Exception.Message)" -ForegroundColor Yellow
}
foreach ($exe in $exeCandidates) {
  if (-not (Test-Path -LiteralPath $exe)) { continue }
  try {
    New-NetFirewallRule `
      -DisplayName "SATRIA Desktop EXE" `
      -Direction Inbound `
      -Action Allow `
      -Program $exe `
      -Profile Any `
      -ErrorAction Stop | Out-Null
    Write-Host "  program  $exe"
  } catch {
    Write-Host "  WARN firewall exe: $($_.Exception.Message)" -ForegroundColor Yellow
  }
}

$vmCreatorId = "{40E0AC32-46A5-438A-A0B2-2B479E8F2E90}"
try {
  $found = Get-NetFirewallHyperVVMCreator | Where-Object { $_.FriendlyName -eq "WSL" } | Select-Object -First 1
  if ($found) { $vmCreatorId = $found.VMCreatorId }
  Set-NetFirewallHyperVVMSetting -Name $vmCreatorId -LoopbackEnabled True -DefaultInboundAction Allow -ErrorAction Stop
  Write-Host "  Hyper-V WSL loopback  Allow"
} catch {
  Write-Host "  Hyper-V firewall: $($_.Exception.Message)" -ForegroundColor DarkYellow
}

Write-Step "WSL autostart (systemd satria-api)"
$wslDistro = $null
try {
  $raw = & wsl.exe -l -q 2>$null
  if ($raw) {
    foreach ($line in @($raw)) {
      $n = ($line -replace "`0", "").Trim()
      if ($n) { $wslDistro = $n; break }
    }
  }
} catch { }
if (-not $wslDistro) { $wslDistro = "Ubuntu-24.04" }
$wslTaskName = "SATRIA-StartWslApi"
# Wake distro, then start user service (linger must be enabled in WSL).
$wslArgs = "-d $wslDistro -u me -- bash -lc `"systemctl --user start satria-api.service || true`""
try {
  $action = New-ScheduledTaskAction -Execute "wsl.exe" -Argument $wslArgs
  $trigger = New-ScheduledTaskTrigger -AtLogOn
  $principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
  $settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 10)
  Register-ScheduledTask `
    -TaskName $wslTaskName `
    -Action $action `
    -Trigger $trigger `
    -Principal $principal `
    -Settings $settings `
    -Force | Out-Null
  Write-Host "  task AtLogOn  $wslTaskName  ($wslDistro)"
} catch {
  Write-Host "  WARN task $wslTaskName: $($_.Exception.Message)" -ForegroundColor Yellow
}

Write-Step "Ringkasan"
Write-Host @"
Selesai. Setelah ini:
  1. Di WSL sekali: bash ~/siksik/scripts/install_satria_api_service.sh
  2. Reboot → API otomatis (task $wslTaskName + systemd satria-api)
  3. Buka SATRIA — tidak perlu start_poc.sh di terminal
  4. Smart App Control: Windows Security → App & browser control → Off (lab)

Antivirus pihak ketiga (Avast, AVG, Norton, Kaspersky, dll):
  Tambahkan exclusion manual ke folder:
    $RepoRoot
    $CargoTarget
  dan izinkan proses satria-desktop.exe / SATRIA.exe
"@

Write-Host ""
if (-not $NoPause) {
  Write-Host "Tekan tombol untuk tutup..."
  try { $null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown") } catch { Start-Sleep -Seconds 2 }
}
exit 0
