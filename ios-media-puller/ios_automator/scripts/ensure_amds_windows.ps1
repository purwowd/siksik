#Requires -Version 5.1
# Nyalakan Apple Mobile Device Service (Manual). Jangan Disable - Linux mux butuh ini.
$ErrorActionPreference = "Stop"

function Test-IsAdmin {
  $id = [Security.Principal.WindowsIdentity]::GetCurrent()
  $p = New-Object Security.Principal.WindowsPrincipal($id)
  return $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

$svc = Get-Service -Name "Apple Mobile Device Service" -ErrorAction SilentlyContinue
if (-not $svc) {
  Write-Host "ERROR: Apple Mobile Device Service tidak ada. Install iTunes 64-bit."
  exit 2
}

if ($svc.Status -eq "Running" -and $svc.StartType -ne "Disabled") {
  Write-Host "AMDS already Running"
  exit 0
}

if (-not (Test-IsAdmin)) {
  $here = $MyInvocation.MyCommand.Path
  $p = Start-Process -FilePath "powershell.exe" -Verb RunAs -Wait -PassThru -WorkingDirectory "C:\Users\Admin\wda" -ArgumentList @(
    "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $here
  )
  if ($null -eq $p) { exit 1223 }
  exit $p.ExitCode
}

Set-Service -Name $svc.Name -StartupType Manual
Start-Service -Name $svc.Name
$after = Get-Service -Name $svc.Name
Write-Host ("AMDS {0} {1}" -f $after.Status, $after.StartType)
if ($after.Status -ne "Running") { exit 1 }
exit 0
