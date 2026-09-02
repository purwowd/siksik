#Requires -RunAsAdministrator
#Requires -Version 5.1
# Remove SATRIA netsh portproxy on 8000/5173/5175.
# Stale NAT-IP forwards survive reboot and blackhole WSL localhostForwarding.
# Always exit 0: "entry not found" is not a failure.
# After a successful elevated run, register an at-logon task so later boots
# do not need UAC or a manual netsh.
$ErrorActionPreference = "Continue"

$ports = New-Object System.Collections.Generic.List[int]
foreach ($p in @(8000, 5173, 5175)) { [void]$ports.Add($p) }
if ($env:SADT_API_PORT) {
  $n = 0
  if ([int]::TryParse($env:SADT_API_PORT, [ref]$n) -and $n -gt 0 -and -not $ports.Contains($n)) {
    [void]$ports.Add($n)
  }
}

foreach ($port in $ports) {
  foreach ($addr in @("0.0.0.0", "127.0.0.1")) {
    & netsh.exe interface portproxy delete v4tov4 listenport=$port listenaddress=$addr 2>$null | Out-Null
  }
}

$self = $MyInvocation.MyCommand.Path
$taskName = "SATRIA-ClearWslPortproxy"
if ($self -and (Test-Path -LiteralPath $self)) {
  try {
    $existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    $actionArgs = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$self`""
    $needsWrite = $true
    if ($existing) {
      $exArgs = $existing.Actions[0].Arguments
      if ($exArgs -eq $actionArgs) { $needsWrite = $false }
    }
    if ($needsWrite) {
      $action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $actionArgs
      $trigger = New-ScheduledTaskTrigger -AtLogOn
      $principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -RunLevel Highest -LogonType Interactive
      $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
      Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
    }
  } catch { }
}

exit 0
