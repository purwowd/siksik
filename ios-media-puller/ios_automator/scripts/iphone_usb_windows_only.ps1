#Requires -Version 5.1
# iPhone colok USB = Windows only. Jangan AutoBind / attach ke WSL.
$ErrorActionPreference = "Stop"

function Wait-Window {
  Write-Host ""
  Write-Host "Tekan Enter untuk tutup."
  try { [void][Console]::ReadLine() } catch { Start-Sleep -Seconds 20 }
}

function Test-IsAdmin {
  $id = [Security.Principal.WindowsIdentity]::GetCurrent()
  $p = New-Object Security.Principal.WindowsPrincipal($id)
  return $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
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

if (-not (Test-IsAdmin)) {
  $here = $MyInvocation.MyCommand.Path
  Start-Process -FilePath "powershell.exe" -Verb RunAs -Wait -ArgumentList @(
    "-NoExit", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $here
  )
  exit 0
}

$Usbipd = Join-Path ${env:ProgramFiles} "usbipd-win\usbipd.exe"
$exitCode = 0
try {
  Write-Host "iPhone USB -> Windows (bukan WSL)" -ForegroundColor Green
  Release-AppleUsbToWindows $Usbipd
  try {
    Set-Service -Name "Apple Mobile Device Service" -StartupType Manual
    Start-Service -Name "Apple Mobile Device Service"
  } catch {
    Write-Host "  AMDS: $($_.Exception.Message)"
  }
  Write-Host ""
  & $Usbipd list
  Write-Host ""
  $svc = Get-Service -Name "Apple Mobile Device Service" -ErrorAction SilentlyContinue
  if ($svc) { Write-Host ("AMDS  {0}" -f $svc.Status) }
  Write-Host "Baris iPhone harus Not shared / Shared, BUKAN Attached."
} catch {
  $exitCode = 1
  Write-Host "ERROR: $($_.Exception.Message)" -ForegroundColor Red
} finally {
  Wait-Window
}
exit $exitCode
