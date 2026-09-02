#Requires -Version 5.1
<#
.SYNOPSIS
  Luncurkan WebDriverAgent lewat USB native Windows (go-ios + AMDS).
  Tidak memindah usbipd, tidak minta UAC.
#>
param(
  [string]$Udid = "",
  [string]$Bundle = "com.facebook.WebDriverAgentRunner.xctrunner"
)

$ErrorActionPreference = "Continue"
$IosExe = "C:\Users\Admin\wda\ios.exe"

if ($env:OS -ne "Windows_NT") {
  Write-Host "launch_wda_windows: bukan Windows"
  exit 2
}
if (-not (Test-Path -LiteralPath $IosExe)) {
  Write-Host "launch_wda_windows: ios.exe tidak ada di $IosExe"
  exit 2
}

$args = @("launch", $Bundle)
if ($Udid) {
  $args += "--udid=$Udid"
}

$output = & $IosExe @args 2>&1 | Out-String
Write-Host $output.TrimEnd()
if ($LASTEXITCODE -ne 0) {
  exit 1
}
exit 0
