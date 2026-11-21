# Install (or refresh) hidden Session-1 logon tasks for Protocol HUD.
# Does not restart a healthy HUD. Idempotent: /Create /F overwrites the tasks.
# Run from the HUD folder (a git checkout of tactical-deck/hud is fine).
$ErrorActionPreference = 'Continue'
$hud = Split-Path -Parent $MyInvocation.MyCommand.Path
if ($env:PROTOCOL_HUD_PYTHON) {
  $pyw = $env:PROTOCOL_HUD_PYTHON
} else {
  $cmd = Get-Command pythonw -ErrorAction SilentlyContinue
  if (-not $cmd) { $cmd = Get-Command python -ErrorAction SilentlyContinue }
  if (-not $cmd) { throw "pythonw/python not on PATH; set PROTOCOL_HUD_PYTHON" }
  $pyw = $cmd.Source
}
$user = $env:USERNAME
$temp = $env:TEMP

function Write-TaskXml([string]$name, [string]$scriptArgs, [string]$desc, [string]$outPath) {
  $xml = @"
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>$desc</Description></RegistrationInfo>
  <Triggers>
    <LogonTrigger>
      <Enabled>true</Enabled>
      <UserId>$user</UserId>
    </LogonTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
      <UserId>$user</UserId>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <Hidden>true</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Priority>6</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>$pyw</Command>
      <Arguments>$scriptArgs</Arguments>
      <WorkingDirectory>$hud</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"@
  Set-Content -LiteralPath $outPath -Value $xml -Encoding Unicode
}

$startXml = Join-Path $temp 'protocolhud-start-task.xml'
$watchXml = Join-Path $temp 'protocolhud-watch-task.xml'
Write-TaskXml 'ProtocolHudStart' "`"$hud\start_hud_hidden.py`"" 'Start Protocol HUD hidden in the interactive session (Session 1). No console window.' $startXml
Write-TaskXml 'ProtocolHudWatchdog' "`"$hud\watchdog.py`"" 'Watch Protocol HUD /health and restart it in Session 1 if it is down or stale. Hidden, no console window.' $watchXml

foreach ($pair in @(
  @{ Name = 'ProtocolHudStart'; Xml = $startXml },
  @{ Name = 'ProtocolHudWatchdog'; Xml = $watchXml }
)) {
  schtasks.exe /Delete /TN $pair.Name /F 2>$null | Out-Null
  $create = schtasks.exe /Create /TN $pair.Name /XML $pair.Xml /F 2>&1 | Out-String
  "CREATE $($pair.Name) $($create.Trim())"
  $q = schtasks.exe /Query /TN $pair.Name /V /FO LIST 2>&1 | Out-String
  "QUERY $($pair.Name)`n$($q.Trim())"
}
