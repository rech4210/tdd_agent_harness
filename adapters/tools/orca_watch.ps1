<#
Orca 워커 감시 도구 PowerShell 진입점 (WO-H43).

사용 예:
Start-Process powershell -ArgumentList '-NoExit','-NoProfile','-ExecutionPolicy','Bypass','-File','adapters\tools\orca_watch.ps1','--terminal','<Orca 터미널 핸들>','--workspace','<작업 공간>','--agent','agy','--wo','WO-H43','--role','implementer','--cap-seconds','600'
#>

python "$PSScriptRoot\orca_watch.py" @args
Write-Host "종료 코드: $LASTEXITCODE"
exit $LASTEXITCODE
