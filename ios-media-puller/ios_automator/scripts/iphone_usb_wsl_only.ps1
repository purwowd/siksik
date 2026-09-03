#Requires -Version 5.1
# iPhone colok USB = WSL only. Windows AMDS/AltServer tidak boleh pegang device.
$ErrorActionPreference = "Stop"

function Test-IsAdmin {
  $id = [Security.Principal.WindowsIdentity]::GetCurrent()
  $p = New-Object Security.Principal.WindowsPrincipal($id)
  return $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Get-AppleUsbDevices([string]$Usbipd) {
  $state = & $Usbipd state | ConvertFrom-Json
  return @(
    $state.Devices | Where-Object {
      ($_.InstanceId -match 'VID_05AC&PID_12A8') -or ($_.Description -match 'Apple Mobile Device')
    }
  )
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

function Test-AppleAttachedToWsl([string]$Usbipd) {
  foreach ($d in (Get-AppleUsbDevices $Usbipd)) {
    if ($d.ClientIPAddress) { return $true }
  }
  $list = & $Usbipd list | Out-String
  return [bool]($list -match '05ac:12a8\s+\S+\s+.*Attached')
}

function Stop-WindowsAppleStack {
  Get-Process -Name "AltServer" -ErrorAction SilentlyContinue |
    Stop-Process -Force -ErrorAction SilentlyContinue
  Get-Process -Name "iTunes", "AppleMobileDeviceProcess", "AMPDevicesAgent" -ErrorAction SilentlyContinue |
    Stop-Process -Force -ErrorAction SilentlyContinue
}

function Set-IphoneAutobindForWsl([string]$Usbipd) {
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
}

function Bind-IphoneToWsl([string]$Usbipd) {
  $prev = $ErrorActionPreference
  $ErrorActionPreference = "Continue"
  try {
    Stop-WindowsAppleStack
    Start-Sleep -Seconds 2

    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
      Where-Object {
        $_.Name -match 'usbip' -and $_.CommandLine -match 'auto-attach' -and
        $_.CommandLine -match '05ac:12a8|12a8'
      } |
      ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

    $busid = Get-AppleBusId $Usbipd
    if (-not $busid) {
      Write-Host "  iPhone tidak terlihat di usbipd; tidak bind bus lain"
      return
    }
    Write-Host "  busid $busid"
    if (Test-AppleAttachedToWsl $Usbipd) {
      Write-Host "  detach dulu (AMDS sudah mati)"
      & $Usbipd detach --busid $busid | Out-Null
      Start-Sleep -Seconds 2
    }
    & $Usbipd unbind --busid $busid | Out-Null
    Start-Sleep -Seconds 1
    Write-Host "  bind --force (Windows tidak boleh pakai device)"
    & $Usbipd bind --busid $busid --force
    if ($LASTEXITCODE -ne 0) {
      throw "usbipd bind --force gagal (exit $LASTEXITCODE)"
    }
    Start-Sleep -Seconds 1

    Write-Host "  attach --wsl --auto-attach"
    $attachArgs = @("attach", "--wsl", "--auto-attach", "--busid", $busid)
    Start-Process -FilePath $Usbipd -ArgumentList $attachArgs -WindowStyle Hidden

    for ($i = 1; $i -le 20; $i++) {
      Start-Sleep -Seconds 1
      if (Test-AppleAttachedToWsl $Usbipd) {
        Write-Host "  iPhone Attached ke WSL"
        return
      }
    }
    throw "Attach ke WSL gagal. Cek usbipd list."
  } finally {
    $ErrorActionPreference = $prev
  }
}

if (-not (Test-IsAdmin)) {
  $here = $MyInvocation.MyCommand.Path
  $p = Start-Process -FilePath "powershell.exe" -Verb RunAs -Wait -PassThru -ArgumentList @(
    "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $here
  )
  exit $p.ExitCode
}

$Usbipd = Join-Path ${env:ProgramFiles} "usbipd-win\usbipd.exe"
$exitCode = 0
try {
  if (-not (Test-Path -LiteralPath $Usbipd)) {
    throw "usbipd-win tidak ada di $Usbipd"
  }
  Write-Host "iPhone USB -> WSL (Windows tidak pegang)" -ForegroundColor Green
  Set-IphoneAutobindForWsl $Usbipd
  Bind-IphoneToWsl $Usbipd
  Write-Host ""
  & $Usbipd list
  Write-Host ""
  $svc = Get-Service -Name "Apple Mobile Device Service" -ErrorAction SilentlyContinue
  if ($svc) { Write-Host ("AMDS  {0}  {1}" -f $svc.Status, $svc.StartType) }
  $nAlt = @(Get-Process -Name "AltServer" -ErrorAction SilentlyContinue).Count
  Write-Host "AltServer Windows  $nAlt proses"
  Write-Host "Baris iPhone harus Attached. AMDS boleh tetap Running (mux Linux)."
} catch {
  $exitCode = 1
  Write-Host "ERROR: $($_.Exception.Message)" -ForegroundColor Red
}
exit $exitCode
