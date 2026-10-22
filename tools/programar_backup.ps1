# Programa la copia diaria verificada del ledger (03:00, se recupera si el PC
# estaba apagado). Ejecutar una vez:  powershell -ExecutionPolicy Bypass -File tools\programar_backup.ps1
$ErrorActionPreference = 'Stop'
$raiz = Split-Path -Parent $PSScriptRoot
# Se programa el .vbs, NO el .cmd: un .cmd abierto por el Programador de tareas
# abre una ventana de consola todos los dias a las 03:00. El .vbs lo lanza con
# ventana oculta (ver TAREA_BACKUP_OCULTA.vbs).
$wrapper = Join-Path $raiz 'TAREA_BACKUP_OCULTA.vbs'
$cmd = Join-Path $raiz 'TAREA_BACKUP.cmd'
if (-not (Test-Path $wrapper)) { throw "falta $wrapper" }
if (-not (Test-Path $cmd)) { throw "falta $cmd" }

$accion = New-ScheduledTaskAction -Execute 'wscript.exe' `
    -Argument ('"' + $wrapper + '"')
$disparo = New-ScheduledTaskTrigger -Daily -At 03:00
$ajustes = New-ScheduledTaskSettingsSet -StartWhenAvailable `
    -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 1)

Register-ScheduledTask -TaskName 'KUKIA Backup Ledger' -Action $accion `
    -Trigger $disparo -Settings $ajustes -Force `
    -Description 'Copia diaria verificada del ledger KUKIA (conserva 14)' | Out-Null

$t = Get-ScheduledTask -TaskName 'KUKIA Backup Ledger'
$i = Get-ScheduledTaskInfo -TaskName 'KUKIA Backup Ledger'
"Tarea:  $($t.TaskName)"
"Estado: $($t.State)"
"Proxima: $($i.NextRunTime)"
"Ultima:  $($i.LastRunTime) (resultado $($i.LastTaskResult))"
