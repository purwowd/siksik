#Requires -RunAsAdministrator
<#
.SYNOPSIS
  Fix Windows access to SADT UI/API running in WSL2.

.DESCRIPTION
  On this machine, NAT + localhostForwarding is the working path.
  netsh portproxy to the WSL NAT IP blackholes app ports (5173/8000) because
  host→guest TCP to those ports is blocked, while WSL's localhostForwarding relay works.
  This script REMOVES conflicting portproxy entries and opens firewall helpers.
#>

$ErrorActionPreference = "Stop"

$wslconfig = Join-Path $env:USERPROFILE ".wslconfig"
$mirrored = $false
if (Test-Path $wslconfig) {
  $mirrored = Select-String -Path $wslconfig -Pattern '^\s*networkingMode\s*=\s*mirrored' -Quiet
}

$wslIp = (wsl -d Ubuntu-24.04 -e bash -lc "hostname -I | awk '{print `$1}'").Trim()
if (-not $wslIp) { throw "Could not resolve WSL IP. Is WSL running?" }
Write-Host "WSL IP: $wslIp"
Write-Host "Mode: $(if ($mirrored) { 'mirrored' } else { 'NAT + localhostForwarding' })"

foreach ($port in 5173, 8000) {
  netsh interface portproxy delete v4tov4 listenport=$port listenaddress=0.0.0.0 2>$null | Out-Null
  netsh interface portproxy delete v4tov4 listenport=$port listenaddress=127.0.0.1 2>$null | Out-Null
}
Write-Host "Removed portproxy on 5173/8000 (conflicts with localhostForwarding)."

Get-NetFirewallRule -DisplayName "SADT PoC LAN" -ErrorAction SilentlyContinue | Remove-NetFirewallRule
New-NetFirewallRule -DisplayName "SADT PoC LAN" -Direction Inbound -Action Allow -Protocol TCP -LocalPort 5173,8000 | Out-Null

$vmCreatorId = "{40E0AC32-46A5-438A-A0B2-2B479E8F2E90}"
try {
  $found = Get-NetFirewallHyperVVMCreator | Where-Object { $_.FriendlyName -eq "WSL" } | Select-Object -First 1
  if ($found) { $vmCreatorId = $found.VMCreatorId }
} catch {}
try {
  Set-NetFirewallHyperVVMSetting -Name $vmCreatorId -LoopbackEnabled True -DefaultInboundAction Allow -ErrorAction Stop
} catch {
  Write-Host "Hyper-V firewall: $($_.Exception.Message)"
}

Write-Host ""
Write-Host "Portproxy (SSH only is OK):"
netsh interface portproxy show v4tov4
Write-Host ""
Write-Host "From Windows browser open:"
Write-Host "  UI  http://127.0.0.1:5173"
Write-Host "  API http://127.0.0.1:8000/docs"
Write-Host ""
Write-Host "Stack must be running in WSL: bash scripts/start_poc.sh"
if ($mirrored) {
  Write-Host "WARNING: networkingMode=mirrored is enabled; host->WSL was broken on this PC."
  Write-Host "Prefer NAT (comment out networkingMode=mirrored in %USERPROFILE%\.wslconfig) then wsl --shutdown."
}
