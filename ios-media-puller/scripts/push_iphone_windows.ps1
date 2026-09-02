#Requires -Version 5.1
<#
.SYNOPSIS
  Windows half of push_iphone.sh: USB native (AMDS) + go-ios install.
  Does not usbipd-attach. Does not sign.
#>
param(
  [Parameter(Position = 0)]
  [string]$IpaPath = "",
  [switch]$PrepareUsbOnly
)

$ErrorActionPreference = "Stop"
$IosExe = "C:\Users\Admin\wda\ios.exe"
$LogFile = "C:\Users\Admin\wda\push-iphone.log"

function Write-Info([string]$Message) {
  $line = "[INFO] $Message"
  Write-Host $line
  try { Add-Content -LiteralPath $LogFile -Value $line -ErrorAction SilentlyContinue } catch { }
}

function Write-Err([string]$Message) {
  $line = "[ERROR] $Message"
  Write-Host $line
  try { Add-Content -LiteralPath $LogFile -Value $line -ErrorAction SilentlyContinue } catch { }
}

function Test-IsAdmin {
  $id = [Security.Principal.WindowsIdentity]::GetCurrent()
  $p = New-Object Security.Principal.WindowsPrincipal($id)
  return $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Get-Usbipd {
  return (Join-Path ${env:ProgramFiles} "usbipd-win\usbipd.exe")
}

function Get-AppleUsbDevices([string]$Usbipd) {
  $state = & $Usbipd state | ConvertFrom-Json
  return @(
    $state.Devices | Where-Object {
      ($_.InstanceId -match 'VID_05AC&PID_12A8') -or ($_.Description -match 'Apple Mobile Device')
    }
  )
}

function Test-AppleAttachedToWsl([string]$Usbipd) {
  if (-not (Test-Path -LiteralPath $Usbipd)) { return $false }
  foreach ($d in (Get-AppleUsbDevices $Usbipd)) {
    if ($d.ClientIPAddress) { return $true }
  }
  $list = & $Usbipd list | Out-String
  return [bool]($list -match '(?m)05ac:12a[8b].*\bAttached\b')
}

function Test-AppleUsbStub {
  $usbipd = Get-Usbipd
  if (-not (Test-Path -LiteralPath $usbipd)) { return $false }
  $list = & $usbipd list | Out-String
  foreach ($line in ($list -split "`r?`n")) {
    if ($line -match '05ac:12a[8b]' -and $line -match 'USBIP Shared|\bAttached\b|Shared \(forced\)') {
      return $true
    }
  }
  return $false
}

function Get-IosListText {
  $prev = $ErrorActionPreference
  $ErrorActionPreference = "Continue"
  try {
    return ((& $IosExe list --nojson 2>&1 | ForEach-Object { "$_" }) -join "`n")
  } catch {
    return [string]$_.Exception.Message
  } finally {
    $ErrorActionPreference = $prev
  }
}

function Test-GoIosSeesDevice {
  if (-not (Test-Path -LiteralPath $IosExe)) { return $false }
  $out = Get-IosListText
  return [bool]($out -match '(?m)^[0-9A-Fa-f-]{8,}$')
}

function Test-AmdsRunning {
  $svc = Get-Service -Name "Apple Mobile Device Service" -ErrorAction SilentlyContinue
  return [bool]($svc -and $svc.Status -eq "Running")
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
        & $Usbipd policy remove --guid $Matches[1] | Out-Null
      }
    }
    foreach ($hw in @("05ac:12a8", "05ac:12ab")) {
      if ($policyOut -notmatch "Deny\s+AutoBind\s+$hw") {
        & $Usbipd policy add --effect Deny --operation AutoBind --hardware-id $hw | Out-Null
      }
    }

    for ($i = 1; $i -le 10; $i++) {
      foreach ($d in (Get-AppleUsbDevices $Usbipd)) {
        if ($d.BusId -and $d.ClientIPAddress) {
          & $Usbipd detach --busid $d.BusId | Out-Null
        }
        if ($d.PersistedGuid) {
          & $Usbipd unbind --guid $d.PersistedGuid | Out-Null
        }
        if ($d.BusId) {
          & $Usbipd unbind --busid $d.BusId | Out-Null
        }
      }
      & $Usbipd unbind --hardware-id 05ac:12a8 | Out-Null
      Start-Sleep -Seconds 2
      if (-not (Test-AppleAttachedToWsl $Usbipd)) { return }
    }
    throw "iPhone is still Attached to WSL. Unplug USB 5 seconds, replug, retry."
  } finally {
    $ErrorActionPreference = $prev
  }
}

function Start-Amds {
  Write-Info "Ensuring Apple Mobile Device Support is running..."
  $svc = Get-Service -Name "Apple Mobile Device Service" -ErrorAction SilentlyContinue
  if (-not $svc) {
    throw "Apple Mobile Device Service is not installed. Install iTunes 64-bit (not Microsoft Store)."
  }
  Set-Service -Name $svc.Name -StartupType Manual
  if ($svc.Status -ne "Running") {
    Start-Service -Name $svc.Name
  }
  Start-Sleep -Seconds 2
  $svc = Get-Service -Name $svc.Name
  if ($svc.Status -ne "Running") {
    throw "Apple Mobile Device Service status=$($svc.Status)"
  }
}

function Get-WindowsIphonePnp {
  return @(
    Get-PnpDevice -PresentOnly -ErrorAction SilentlyContinue |
      Where-Object {
        $_.InstanceId -match 'VID_05AC&PID_12A8' -and
        $_.FriendlyName -notmatch 'USBIP Shared'
      }
  )
}

function Invoke-UsbPrep {
  $usbipd = Get-Usbipd
  Write-Info "Detaching iPhone from WSL/usbipd if necessary..."
  if (Test-Path -LiteralPath $usbipd) {
    if ((Test-AppleAttachedToWsl $usbipd) -or (Test-AppleUsbStub)) {
      Release-AppleUsbToWindows $usbipd
    }
  }
  Start-Amds
}

function Test-NeedUsbPrep {
  $usbipd = Get-Usbipd
  if ((Test-Path -LiteralPath $usbipd) -and (Test-AppleAttachedToWsl $usbipd)) { return $true }
  if (Test-AppleUsbStub) { return $true }
  if (-not (Test-AmdsRunning)) { return $true }
  return $false
}

function Test-ProvisioningFailure([string]$Text) {
  return [bool]($Text -match 'invalid signature|code signature|provisioning profile|application verification failed|entitlement|Untrusted Developer|Failed to verify')
}

if ($PrepareUsbOnly) {
  if (-not (Test-IsAdmin)) {
    Write-Err "USB prep requires Administrator."
    exit 2
  }
  try {
    New-Item -ItemType Directory -Force -Path (Split-Path $LogFile) | Out-Null
    Invoke-UsbPrep
    exit 0
  } catch {
    Write-Err $_.Exception.Message
    exit 1
  }
}

if (-not $IpaPath) {
  Write-Err "missing IPA path"
  exit 2
}

$here = $MyInvocation.MyCommand.Path
$iosReady = Test-GoIosSeesDevice
if ($iosReady) {
  Write-Info "ios.exe already sees a UDID; skipping USB prep."
} elseif ((Test-NeedUsbPrep) -and -not (Test-IsAdmin)) {
  Write-Info "Requesting Windows administrator privileges..."
  $p = Start-Process -FilePath "powershell.exe" -Verb RunAs -Wait -PassThru -ArgumentList @(
    "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $here, "-PrepareUsbOnly"
  )
  if ($p.ExitCode -ne 0) {
    Write-Err "USB/AMDS preparation failed, exit $($p.ExitCode)"
    exit $p.ExitCode
  }
} elseif (Test-IsAdmin) {
  Invoke-UsbPrep
} else {
  Write-Info "Detaching iPhone from WSL/usbipd if necessary..."
  Write-Info "Ensuring Apple Mobile Device Support is running..."
}

if (-not (Test-Path -LiteralPath $IpaPath)) {
  Write-Err "Windows cannot read IPA: $IpaPath"
  exit 2
}
if (-not (Test-Path -LiteralPath $IosExe)) {
  Write-Err "go-ios not found: $IosExe"
  exit 2
}

Write-Info "Detecting iPhone through Windows PnP..."
$iosReady = Test-GoIosSeesDevice
if ((Test-AppleUsbStub) -and -not $iosReady) {
  Write-Err "Windows still has the usbipd stub (USBIP Shared Device), not the Apple driver."
  Write-Err "In Admin PowerShell: usbipd detach --busid 1-5; usbipd unbind --busid 1-5"
  exit 2
}
$pnp = Get-WindowsIphonePnp
if ($pnp.Count -eq 0 -and -not $iosReady) {
  Write-Err "iPhone was not detected by Windows as a native Apple USB device."
  exit 2
}
if ($pnp.Count -gt 0) {
  Write-Info "iPhone detected."
  foreach ($d in $pnp) {
    Write-Info ("PnP: {0} status={1}" -f $d.FriendlyName, $d.Status)
  }
}

Write-Info "Using go-ios v1.3.2"
$listOut = Get-IosListText
if ($listOut -notmatch '(?m)^[0-9A-Fa-f-]{8,}$') {
  Write-Err "ios.exe list did not see a UDID. USB is not ready for AMDS/go-ios."
  Write-Host $listOut
  exit 2
}
Write-Info "Starting IPA installation..."
Write-Info "Installing IPA..."

$installOut = New-Object System.Collections.Generic.List[string]
$prevEap = $ErrorActionPreference
$ErrorActionPreference = "Continue"
$exitCode = 1
try {
  & $IosExe install "--path=$IpaPath" --nojson 2>&1 | ForEach-Object {
    $s = "$_"
    [void]$installOut.Add($s)
    Write-Host $s
  }
  $exitCode = $LASTEXITCODE
} finally {
  $ErrorActionPreference = $prevEap
}

$blob = ($installOut -join "`n")
if ($exitCode -ne 0) {
  Write-Err "IPA installation failed."
  Write-Err "ios.exe exit code: $exitCode"
  if (Test-ProvisioningFailure $blob) {
    Write-Err "Device rejected the IPA (signature/provisioning/entitlements), not a usbipd USB failure."
  }
  exit $exitCode
}

Write-Host "[SUCCESS] IPA installation completed."
exit 0
