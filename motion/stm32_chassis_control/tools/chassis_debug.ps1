param(
    [ValidateSet('Status', 'Stop', 'Test', 'ZeroLease', 'BadChecksum')]
    [string]$Action = 'Status',
    [switch]$ConfirmWheelsLifted,
    [string]$ProjectDirectory = (Split-Path -Parent $PSScriptRoot)
)

$ErrorActionPreference = 'Stop'
if ($Action -eq 'Test' -and -not $ConfirmWheelsLifted) {
    throw 'TEST causes movement. Lift ALL wheels, check power wiring, then explicitly add -ConfirmWheelsLifted. Never run on the ground.'
}
$programmer = 'D:\STMicroelectronics\STM32Cube\STM32CubeIDE_2.2.0\STM32CubeIDE\plugins\com.st.stm32cube.ide.mcu.externaltools.cubeprogrammer.win32_2.2.500.202603051304\tools\bin\STM32_Programmer_CLI.exe'
$mapPath = Join-Path $ProjectDirectory 'Debug\GreenhouseCar_Forward.map'
$mapText = Get-Content -Raw -LiteralPath $mapPath

function Find-Symbol([string]$Name) {
    $pattern = '(?m)^\s+(0x[0-9a-fA-F]+)\s+' + [regex]::Escape($Name) + '\s*$'
    $match = [regex]::Match($mapText, $pattern)
    if (-not $match.Success) { throw "Symbol not found: $Name. Rebuild the matching firmware first." }
    return $match.Groups[1].Value
}

function Invoke-Programmer([string[]]$Arguments) {
    $result = (& $programmer -c port=SWD freq=100 mode=HOTPLUG @Arguments 2>&1 | Out-String)
    if ($LASTEXITCODE -ne 0) { throw $result }
    return $result
}

function Read-Words([string]$Address, [int]$Count) {
    $result = Invoke-Programmer @('-r32', $Address, ([string]($Count * 4)))
    $values = @()
    foreach ($match in [regex]::Matches($result, '(?m)^0x[0-9A-Fa-f]+\s*:\s*([^\r\n]+)')) {
        foreach ($word in ($match.Groups[1].Value.Trim() -split '\s+')) {
            if ($word -match '^[0-9A-Fa-f]{8}$') { $values += [Convert]::ToUInt32($word, 16) }
        }
    }
    if ($values.Count -ne $Count) { throw "Incomplete memory read: $result" }
    return ,$values
}

function Signed-Word([uint32]$Value) {
    return [BitConverter]::ToInt32([BitConverter]::GetBytes($Value), 0)
}

$statusAddress = Find-Symbol 'chassis_status'
$requestAddress = Find-Symbol 'chassis_debug_request'
$ackAddress = Find-Symbol 'chassis_debug_ack'
$frameAddress = Find-Symbol 'chassis_debug_frame'
$encoderAddress = Find-Symbol 'chassis_encoder_status'
$speedAddress = Find-Symbol 'chassis_speed_status'

function Read-Encoders {
    for ($attempt = 0; $attempt -lt 4; ++$attempt) {
        $values = Read-Words $encoderAddress 17
        if ($values[0] -ne 0x454E4331 -or $values[2] -ne 0 -or $values[3] -ne 16) {
            throw 'Encoder firmware signature/self-test mismatch. This ELF may not be flashed yet. Keep motor power OFF.'
        }
        if (($values[1] -band 1) -eq 0 -and $values[1] -eq $values[16]) { return ,$values }
    }
    throw 'Encoder snapshot changed during reads. Retry Status; no command was sent.'
}
function Read-Speed {
    for ($attempt = 0; $attempt -lt 4; ++$attempt) {
        $values = Read-Words $speedAddress 13
        if ($values[0] -ne 0x52504D31 -or $values[2] -ne 0 -or $values[3] -ne 12 -or
            $values[7] -ne 1559500 -or $values[8] -ne 1562800 -or
            (Signed-Word $values[9]) -ne 1 -or (Signed-Word $values[10]) -ne -1) {
            throw 'RPM firmware signature/calibration/self-test mismatch. This ELF may not be flashed yet. Keep motors disconnected.'
        }
        if (($values[1] -band 1) -eq 0 -and $values[1] -eq $values[12]) { return ,$values }
    }
    throw 'RPM snapshot changed during reads. Retry Status; no command was sent.'
}
$before = Read-Words $statusAddress 14
if ($before[0] -ne 0x43484153 -or $before[1] -ne 0 -or $before[13] -ne 26) {
    throw 'Unexpected firmware signature or failed self-test. Keep motor power OFF.'
}
$encoderBefore = Read-Encoders # Also guard any action against old, unflashed firmware.
$speedBefore = Read-Speed

if ($Action -ne 'Status') {
    $payload = switch ($Action) {
        'Stop' { 'STOP' }
        'Test' { 'TEST' }
        'ZeroLease' { 'D,0,0' }
        'BadChecksum' { 'STOP' }
    }
    [int]$checksum = 0
    foreach ($byte in [Text.Encoding]::ASCII.GetBytes($payload)) { $checksum = $checksum -bxor $byte }
    if ($Action -eq 'BadChecksum') { $checksum = $checksum -bxor 1 }
    $frame = $payload + '*' + $checksum.ToString('X2')
    $bytes = New-Object byte[] 64
    [Text.Encoding]::ASCII.GetBytes($frame).CopyTo($bytes, 0)
    $words = @()
    for ($i = 0; $i -lt 64; $i += 4) { $words += ('0x{0:X8}' -f [BitConverter]::ToUInt32($bytes, $i)) }
    $prior = (Read-Words $requestAddress 1)[0]
    $sequence = (($prior -band 0xffff) + 1) -band 0xffff
    if ($sequence -eq 0) { $sequence = 1 }
    $request = [uint32](0x434D0000 -bor $sequence)
    Invoke-Programmer (@('-w32', $frameAddress) + $words + @('-w32', $requestAddress, ('0x{0:X8}' -f $request))) | Out-Null
    $ack = (Read-Words $ackAddress 1)[0]
    if ($ack -ne $request) { throw 'No firmware acknowledgement. Do not assume this command took effect.' }
    Write-Output "Acknowledged frame: $frame"
}

$snapshot = Read-Words $statusAddress 14
$encoderSnapshot = Read-Encoders
$speedSnapshot = Read-Speed
$stateNames = @('IDLE', 'REMOTE', 'TEST', 'TIMEOUT', 'BAD_FRAME', 'SELFTEST_FAULT')
$state = if ($snapshot[3] -lt $stateNames.Count) { $stateNames[$snapshot[3]] } else { 'UNKNOWN' }
[pscustomobject]@{
    State = $state
    TargetA = Signed-Word $snapshot[4]
    TargetB = Signed-Word $snapshot[5]
    AppliedA = Signed-Word $snapshot[6]
    AppliedB = Signed-Word $snapshot[7]
    UptimeMs = $snapshot[2]
    AcceptedFrames = $snapshot[8]
    RejectedFrames = $snapshot[9]
    UartErrors = $snapshot[10]
    SelfTests = $snapshot[13]
    SelfTestFailures = $snapshot[1]
    MotorOutputsLow = (($snapshot[11] -band 0x7) -eq 0 -and ($snapshot[12] -band 0xf000) -eq 0)
    EncoderCountA = Signed-Word $encoderSnapshot[7]
    EncoderCountB = Signed-Word $encoderSnapshot[8]
    EncoderCountsPerSecondA = Signed-Word $encoderSnapshot[9]
    EncoderCountsPerSecondB = Signed-Word $encoderSnapshot[10]
    EncoderActivityMask = $encoderSnapshot[13]
    EncoderFlags = $encoderSnapshot[14]
    EncoderSamples = $encoderSnapshot[15]
    EncoderUptimeMs = $encoderSnapshot[4]
    EncoderSelfTests = $encoderSnapshot[3]
    EncoderSelfTestFailures = $encoderSnapshot[2]
    WheelRpmA = (Signed-Word $speedSnapshot[5]) / 1000.0
    WheelRpmB = (Signed-Word $speedSnapshot[6]) / 1000.0
    CountsPerWheelTurnA = $speedSnapshot[7] / 1000.0
    CountsPerWheelTurnB = $speedSnapshot[8] / 1000.0
    SpeedDirectionA = Signed-Word $speedSnapshot[9]
    SpeedDirectionB = Signed-Word $speedSnapshot[10]
    SpeedFlags = $speedSnapshot[11]
    SpeedUptimeMs = $speedSnapshot[4]
    SpeedSelfTests = $speedSnapshot[3]
    SpeedSelfTestFailures = $speedSnapshot[2]
}
