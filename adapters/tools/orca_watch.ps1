<#
Orca 워커 감시 도구 PowerShell 진입점 (WO-H43).
창 클릭 시 감시 · 상한 중지가 멈추는 문제를 방지하기 위해 QuickEdit를 끈다 (WO-H87).

사용 예:
Start-Process powershell -ArgumentList '-NoExit','-NoProfile','-ExecutionPolicy','Bypass','-File','adapters\tools\orca_watch.ps1','--terminal','<Orca 터미널 핸들>','--workspace','<작업 공간>','--agent','agy','--wo','WO-H43','--role','implementer','--cap-seconds','600'
#>

$savedMode = $null
$hStdIn = [IntPtr]::Zero

try {
    if (-not ('OrcaWatchNative' -as [type])) {
        Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;

public static class OrcaWatchNative {
    [DllImport("kernel32.dll", SetLastError = true)]
    public static extern IntPtr GetStdHandle(int nStdHandle);

    [DllImport("kernel32.dll", SetLastError = true)]
    public static extern bool GetConsoleMode(IntPtr hConsoleHandle, out uint lpMode);

    [DllImport("kernel32.dll", SetLastError = true)]
    public static extern bool SetConsoleMode(IntPtr hConsoleHandle, uint dwMode);
}
"@
    }

    $hStdIn = [OrcaWatchNative]::GetStdHandle(-10)
    [uint32]$origMode = 0
    if ([OrcaWatchNative]::GetConsoleMode($hStdIn, [ref]$origMode)) {
        $savedMode = $origMode
        $newMode = [uint32](($origMode -band (-bnot 0x40)) -bor 0x80)
        [void][OrcaWatchNative]::SetConsoleMode($hStdIn, $newMode)
    }
} catch {
}

$exitCode = 0
try {
    python "$PSScriptRoot\orca_watch.py" @args
    $exitCode = $LASTEXITCODE
    Write-Host "종료 코드: $exitCode"
} finally {
    if ($null -ne $savedMode) {
        try {
            [void][OrcaWatchNative]::SetConsoleMode($hStdIn, $savedMode)
        } catch {
        }
    }
    $global:LASTEXITCODE = $exitCode
}
exit $exitCode
