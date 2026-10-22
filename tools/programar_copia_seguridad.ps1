# Programa la copia SEMANAL verificada (KUKIA + CEREBRO + Xoxete) a N:.
# Domingos 04:00; si el PC estaba apagado se recupera al encenderlo.
# Ejecutar una vez:  powershell -ExecutionPolicy Bypass -File tools\programar_copia_seguridad.ps1
$ErrorActionPreference = 'Stop'
$raiz = Split-Path -Parent $PSScriptRoot
# Se programa el .vbs, NO el .cmd: un .cmd abierto por el Programador de tareas
# abre una ventana de consola. El .vbs lo lanza con ventana oculta y devuelve
# el codigo de salida (ver TAREA_COPIA_SEGURIDAD_OCULTA.vbs).
$wrapper = Join-Path $raiz 'TAREA_COPIA_SEGURIDAD_OCULTA.vbs'
$cmd = Join-Path $raiz 'TAREA_COPIA_SEGURIDAD.cmd'
if (-not (Test-Path $wrapper)) { throw "falta $wrapper" }
if (-not (Test-Path $cmd)) { throw "falta $cmd" }

$accion = New-ScheduledTaskAction -Execute 'wscript.exe' `
    -Argument ('"' + $wrapper + '"')
$disparo = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At 04:00
# Sin desfase horario en la fecha de inicio. Con desfase, el cambio de hora del
# 25-10 movia la copia a las 03:00 (la hora de pared deja de ser la misma).
$domingo = Get-Date -Hour 4 -Minute 0 -Second 0
while ($domingo.DayOfWeek -ne 'Sunday') { $domingo = $domingo.AddDays(1) }
$disparo.StartBoundary = $domingo.ToString('yyyy-MM-ddTHH:mm:ss')
# 3 horas de margen: la primera pasada tiene que leer y comparar los dos lados.
# En un portatil con bateria tambien corre: es la copia, no un capricho.
$ajustes = New-ScheduledTaskSettingsSet -StartWhenAvailable `
    -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 3) `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries

Register-ScheduledTask -TaskName 'KUKIA Copia Seguridad' -Action $accion `
    -Trigger $disparo -Settings $ajustes -Force `
    -Description 'Copia semanal verificada de KUKIA, CEREBRO y Xoxete a N: (SHA256 fichero a fichero)' | Out-Null

$t = Get-ScheduledTask -TaskName 'KUKIA Copia Seguridad'
$i = Get-ScheduledTaskInfo -TaskName 'KUKIA Copia Seguridad'
"Tarea:  $($t.TaskName)"
"Estado: $($t.State)"
"Proxima: $($i.NextRunTime)"
"Ultima:  $($i.LastRunTime) (resultado $($i.LastTaskResult))"
