#Requires -Version 5.1
<#
.SYNOPSIS
  Cek WebDriverAgent di iPhone lewat USB native Windows (go-ios + AMDS).
  Tidak memindah usbipd, tidak minta UAC.
#>
param(
  [string]$Udid = ""
)

$ErrorActionPreference = "Continue"
$IosExe = "C:\Users\Admin\wda\ios.exe"

if ($env:OS -ne "Windows_NT") {
  Write-Host "list_wda_windows: bukan Windows"
  exit 2
}
if (-not (Test-Path -LiteralPath $IosExe)) {
  Write-Host "list_wda_windows: ios.exe tidak ada di $IosExe"
  exit 2
}

$args = @("apps", "--list", "--nojson")
if ($Udid) {
  $args += "--udid=$Udid"
}

$output = & $IosExe @args 2>&1 | Out-String
Write-Host $output.TrimEnd()
if ($LASTEXITCODE -ne 0) {
  exit 2
}
if ($output -match 'WebDriverAgentRunner|xctrunner') {
  exit 0
}
exit 1
