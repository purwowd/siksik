#Requires -Version 5.1
<#
.SYNOPSIS
  Push WebDriverAgent ke iPhone lewat USB native Windows (bukan usbipd/WSL).
#>
$ErrorActionPreference = "Stop"
$LogFile = "C:\Users\Admin\wda\install-wda.log"

function Write-Step([string]$Message) {
  Write-Host ""
  Write-Host "==> $Message" -ForegroundColor Cyan
}

function Write-Fail([string]$Message) {
  Write-Host ""
  Write-Host "ERROR: $Message" -ForegroundColor Red
}

function Test-IsAdmin {
  $id = [Security.Principal.WindowsIdentity]::GetCurrent()
  $p = New-Object Security.Principal.WindowsPrincipal($id)
  return $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Get-WslDistroNames {
  $names = New-Object System.Collections.Generic.List[string]
  try {
    $raw = & wsl.exe -l -q 2>$null
    if ($LASTEXITCODE -eq 0 -and $raw) {
      foreach ($line in @($raw)) {
        $n = ($line -replace "`0", "").Trim()
        if ($n -and $n -notmatch 'docker|podman') { [void]$names.Add($n) }
      }
    }
  } catch { }
  if ($names.Count -eq 0) { [void]$names.Add("Ubuntu-24.04") }
  return $names
}

function Get-AppleUsbDevices([string]$Usbipd) {
  $state = & $Usbipd state | ConvertFrom-Json
  return @(
    $state.Devices | Where-Object {
      ($_.InstanceId -match 'VID_05AC&PID_12A8') -or ($_.Description -match 'Apple')
    }
  )
}

function Test-AppleAttachedToWsl([string]$Usbipd) {
  foreach ($d in (Get-AppleUsbDevices $Usbipd)) {
    if ($d.ClientIPAddress) { return $true }
  }
  $list = & $Usbipd list | Out-String
  return [bool]($list -match '05ac:12a8\s+\S+\s+.*Attached')
}

function Release-AppleUsbToWindows([string]$Usbipd) {
  $prev = $ErrorActionPreference
  $ErrorActionPreference = "Continue"
  try {
    Get-Process -Name "usbip-auto-attach" -ErrorAction SilentlyContinue |
      Stop-Process -Force -ErrorAction SilentlyContinue
    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
      Where-Object { $_.Name -eq "usbipd.exe" -and $_.CommandLine -match "auto-attach" } |
      ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    $policyOut = & $Usbipd policy list | Out-String
    foreach ($line in ($policyOut -split "`r?`n")) {
      if ($line -match '^\s*([0-9a-fA-F-]{36})\s+Allow\s+AutoBind\s+05ac:12a[8b]\b') {
        Write-Host "  hapus AutoBind $($Matches[1])"
        & $Usbipd policy remove --guid $Matches[1] | Out-Null
      }
    }
    foreach ($hw in @("05ac:12a8", "05ac:12ab")) {
      if ($policyOut -notmatch "Deny\s+AutoBind\s+$hw") {
        Write-Host "  Deny AutoBind $hw"
        & $Usbipd policy add --effect Deny --operation AutoBind --hardware-id $hw | Out-Null
      }
    }
    for ($i = 1; $i -le 10; $i++) {
      foreach ($d in (Get-AppleUsbDevices $Usbipd)) {
        if ($d.BusId -and $d.ClientIPAddress) {
          Write-Host "  [$i] detach $($d.BusId)"
          & $Usbipd detach --busid $d.BusId | Out-Null
        }
        if ($d.PersistedGuid) {
          Write-Host "  [$i] unbind guid $($d.PersistedGuid)"
          & $Usbipd unbind --guid $d.PersistedGuid | Out-Null
        }
        if ($d.BusId) {
          Write-Host "  [$i] unbind bus $($d.BusId)"
          & $Usbipd unbind --busid $d.BusId | Out-Null
        }
      }
      & $Usbipd unbind --hardware-id 05ac:12a8 | Out-Null
      Start-Sleep -Seconds 2
      if (-not (Test-AppleAttachedToWsl $Usbipd)) {
        Write-Host "  iPhone di host Windows (bukan WSL)"
        return
      }
    }
    throw "iPhone masih Attached ke WSL. Cabut USB 5 detik, colok lagi, lalu ulangi skrip."
  } finally {
    $ErrorActionPreference = $prev
  }
}

function Get-AppleBusId([string]$Usbipd) {
  foreach ($d in (Get-AppleUsbDevices $Usbipd)) {
    if ($d.BusId) { return [string]$d.BusId }
  }
  $list = & $Usbipd list | Out-String
  if ($list -match '(?m)^(\S+)\s+05ac:12a8\b') {
    return $Matches[1]
  }
  return ""
}

function Restore-AppleUsbToWsl([string]$Usbipd) {
  $prev = $ErrorActionPreference
  $ErrorActionPreference = "Continue"
  try {
    Get-Process -Name "AltServer" -ErrorAction SilentlyContinue |
      Stop-Process -Force -ErrorAction SilentlyContinue
    Get-Process -Name "AppleMobileDeviceProcess", "AMPDevicesAgent" -ErrorAction SilentlyContinue |
      Stop-Process -Force -ErrorAction SilentlyContinue
    $policyOut = & $Usbipd policy list | Out-String
    foreach ($line in ($policyOut -split "`r?`n")) {
      if ($line -match '^\s*([0-9a-fA-F-]{36})\s+Deny\s+AutoBind\s+05ac:12a[8b]\b') {
        Write-Host "  hapus Deny AutoBind $($Matches[1])"
        & $Usbipd policy remove --guid $Matches[1] | Out-Null
      }
    }
    $policyOut = & $Usbipd policy list | Out-String
    foreach ($hw in @("05ac:12a8", "05ac:12ab")) {
      if ($policyOut -notmatch "Allow\s+AutoBind\s+$hw") {
        Write-Host "  Allow AutoBind $hw"
        & $Usbipd policy add --effect Allow --operation AutoBind --hardware-id $hw | Out-Null
      }
    }
    $busid = Get-AppleBusId $Usbipd
    if (-not $busid) {
      throw "iPhone tidak terlihat untuk bind ke WSL."
    }
    Write-Host "  busid $busid"
    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
      Where-Object {
        $_.Name -match 'usbip' -and $_.CommandLine -match 'auto-attach' -and
        $_.CommandLine -match '05ac:12a8|12a8'
      } |
      ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    if (Test-AppleAttachedToWsl $Usbipd) {
      & $Usbipd detach --busid $busid | Out-Null
      Start-Sleep -Seconds 2
    }
    & $Usbipd unbind --busid $busid | Out-Null
    Start-Sleep -Seconds 1
    & $Usbipd bind --busid $busid --force
    if ($LASTEXITCODE -ne 0) {
      Write-Host "  bind --force gagal (exit $LASTEXITCODE) - coba tanpa --force"
      & $Usbipd bind --busid $busid | Out-Null
    }
    Start-Sleep -Seconds 1
    Write-Host "  attach --wsl --auto-attach"
    Start-Process -FilePath $Usbipd -ArgumentList @(
      "attach", "--wsl", "--auto-attach", "--busid", $busid
    ) -WindowStyle Hidden
    for ($i = 1; $i -le 20; $i++) {
      Start-Sleep -Seconds 1
      if (Test-AppleAttachedToWsl $Usbipd) {
        Write-Host "  iPhone Attached ke WSL"
        return
      }
    }
    Write-Host "  USB belum Attached ke WSL. WDA sudah terpasang - bind manual nanti."
  } finally {
    $ErrorActionPreference = $prev
  }
}

function Wait-Window {
  Write-Host ""
  Write-Host "Log: $LogFile" -ForegroundColor Yellow
  Write-Host "Tekan Enter untuk tutup jendela ini."
  try { [void][Console]::ReadLine() } catch { Start-Sleep -Seconds 20 }
}

if ($env:OS -ne "Windows_NT") {
  Write-Fail "Jalankan dari Windows (double-click .cmd), bukan bash murni."
  Wait-Window
  exit 1
}

if (-not (Test-IsAdmin)) {
  Write-Step "Minta UAC (Administrator)..."
  $here = $MyInvocation.MyCommand.Path
  $p = Start-Process -FilePath "powershell.exe" -Verb RunAs -Wait -PassThru -WorkingDirectory "C:\Users\Admin\wda" -ArgumentList @(
    "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $here
  )
  if ($null -eq $p) { exit 1 }
  exit $p.ExitCode
}

$exitCode = 1
$ok = $false
$transcriptStarted = $false
try {
  New-Item -ItemType Directory -Force -Path (Split-Path $LogFile) | Out-Null
  Start-Transcript -Path $LogFile -Force | Out-Null
  $transcriptStarted = $true
  Write-Host "SATRIA pasang WDA (USB Windows)" -ForegroundColor Green
  Write-Host "  log  $LogFile"

  $Usbipd = Join-Path ${env:ProgramFiles} "usbipd-win\usbipd.exe"
  if (-not (Test-Path -LiteralPath $Usbipd)) {
    throw "usbipd-win tidak ada di $Usbipd"
  }

  Write-Step "Kembalikan iPhone ke Windows (unbind usbipd + matikan AutoBind)"
  Release-AppleUsbToWindows $Usbipd

  Write-Step "Nyalakan Apple Mobile Device Service"
  $svc = Get-Service -Name "Apple Mobile Device Service" -ErrorAction SilentlyContinue
  if (-not $svc) {
    throw @"
Apple Mobile Device Service tidak ada.
Install iTunes 64-bit dari https://www.apple.com/itunes/download/win64
(bukan Microsoft Store), lalu jalankan skrip ini lagi.
"@
  }
  Set-Service -Name $svc.Name -StartupType Manual
  Start-Service -Name $svc.Name
  Start-Sleep -Seconds 2
  Write-Host ("  {0}  {1}" -f $svc.Name, (Get-Service -Name $svc.Name).Status)

  Write-Step "Buka usbmux Windows :27015 ke WSL"
  $wslNic = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
    Where-Object { $_.InterfaceAlias -like "vEthernet (WSL*" -and $_.IPAddress -notlike "169.254*" } |
    Select-Object -First 1
  if (-not $wslNic) {
    throw "vEthernet WSL tidak ditemukan. Pastikan WSL2 jalan."
  }
  $listenIp = $wslNic.IPAddress
  $mux = "${listenIp}:27015"
  Write-Host "  mux  $mux"
  & netsh.exe interface portproxy delete v4tov4 listenport=27015 listenaddress=$listenIp 2>$null | Out-Null
  & netsh.exe interface portproxy add v4tov4 listenaddress=$listenIp listenport=27015 connectaddress=127.0.0.1 connectport=27015
  $fwName = "SIKSIK WDA usbmux 27015"
  Get-NetFirewallRule -DisplayName $fwName -ErrorAction SilentlyContinue |
    Remove-NetFirewallRule -ErrorAction SilentlyContinue
  New-NetFirewallRule -DisplayName $fwName -Direction Inbound -LocalPort 27015 -Protocol TCP -Action Allow -Profile Any | Out-Null

  Write-Step "AltServer-Linux lewat usbmux Windows"
  Write-Host "  Kalau iPhone muncul kode 6 digit: ketik di jendela ini, lalu Enter." -ForegroundColor Yellow

  $ok = $false
  $exitCode = 1
  $prevEap = $ErrorActionPreference
  $ErrorActionPreference = "Continue"
  try {
    foreach ($distro in (Get-WslDistroNames)) {
      Write-Host "  wsl -d $distro"
      & wsl.exe -d $distro -e bash -lc @"
set -euo pipefail
export USBMUXD_SOCKET_ADDRESS='$mux'
ROOT=`$HOME/siksik/ios-media-puller
if [ ! -f "`$ROOT/ios_automator/scripts/install_wda_via_windows_mux.sh" ]; then
  ROOT=/home/me/siksik/ios-media-puller
fi
bash "`$ROOT/ios_automator/scripts/install_wda_via_windows_mux.sh"
"@
      $exitCode = $LASTEXITCODE
      if ($exitCode -eq 0) {
        $ok = $true
        break
      }
      # Jangan coba distro lain: gagal install, bukan nama distro.
      break
    }
  } finally {
    $ErrorActionPreference = $prevEap
  }

  if (-not $ok) {
    Write-Fail @"
Install WDA gagal (exit $exitCode).

iPhone sekarang di Windows (bukan WSL). Cadangan:
  1. AltServer sudah jalan di tray (ikon)
  2. Tahan Shift, klik ikon -> Sideload .ipa...
  3. Pilih iPhone, lalu C:\Users\Admin\wda\WebDriverAgentRunner.ipa
"@
  } else {
    Write-Host ""
    Write-Host "Selesai. Di iPhone: Settings -> General -> VPN & Device Management -> Trust." -ForegroundColor Green
    Write-Host "USB tetap di Windows sampai operator ketuk Sudah di-Trust di SATRIA."
    $exitCode = 0
  }
} catch {
  if (-not $ok) {
    $exitCode = 1
    Write-Fail $_.Exception.Message
    Write-Host $_.ScriptStackTrace
  } else {
    Write-Host "WARNING: $($_.Exception.Message)"
    $exitCode = 0
  }
} finally {
  if ($transcriptStarted) {
    try { Stop-Transcript | Out-Null } catch { }
  }
  Wait-Window
}

exit $exitCode
