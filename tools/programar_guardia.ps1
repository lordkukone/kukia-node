# Programa la guardia de KUKIA al iniciar sesion (28-10-2026).
#
# QUE HACE
#   Registra la tarea "KUKIA Guardia": al entrar en Windows, a los 45 s (discos y
#   red aun colocandose), arranca tools\guardia.py sin ventana. La guardia levanta
#   Core, tunel publico, minero, validador, panel y publicador, y relanza lo que
#   se caiga. Es lo que hace que la direccion publica sobreviva a un reinicio.
#
#   No abre ningun puerto del router y no pide contrasena: la tarea corre como el
#   usuario que la registra (InteractiveToken).
#
# USO
#   powershell -ExecutionPolicy Bypass -File tools\programar_guardia.ps1
#   powershell -ExecutionPolicy Bypass -File tools\programar_guardia.ps1 -Desinstalar
#   powershell -ExecutionPolicy Bypass -File tools\programar_guardia.ps1 -Estado
#
# DESHACER
#   -Desinstalar borra la tarea. Nada mas: el proyecto no toca nada del sistema.
$ErrorActionPreference = 'Stop'

$nombre = 'KUKIA Guardia'
$raiz = Split-Path -Parent $PSScriptRoot
$wrapper = Join-Path $raiz 'KUKIA_GUARDIA_OCULTA.vbs'
$script = Join-Path $raiz 'tools\guardia.py'

function Mostrar-Estado {
    $t = Get-ScheduledTask -TaskName $nombre -ErrorAction SilentlyContinue
    if (-not $t) {
        "Tarea:   $nombre (NO registrada)"
        return
    }
    $i = Get-ScheduledTaskInfo -TaskName $nombre
    "Tarea:   $($t.TaskName)"
    "Estado:  $($t.State)"
    "Proxima: $($i.NextRunTime)"
    "Ultima:  $($i.LastRunTime) (resultado $($i.LastTaskResult))"
}

if ($args -contains '-Estado') {
    Mostrar-Estado
    return
}

if ($args -contains '-Desinstalar') {
    if (Get-ScheduledTask -TaskName $nombre -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $nombre -Confirm:$false
        "Tarea borrada: $nombre"
    } else {
        "No habia nada que borrar: $nombre"
    }
    return
}

if (-not (Test-Path $wrapper)) { throw "falta $wrapper" }
if (-not (Test-Path $script)) { throw "falta $script" }

$accion = New-ScheduledTaskAction -Execute 'wscript.exe' `
    -Argument ('"' + $wrapper + '"') -WorkingDirectory $raiz
$disparo = New-ScheduledTaskTrigger -AtLogOn
$disparo.Delay = 'PT45S'
$ajustes = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 2)

try {
    Register-ScheduledTask -TaskName $nombre -Action $accion -Trigger $disparo `
        -Settings $ajustes -Force -ErrorAction Stop `
        -Description 'Arranca la guardia de KUKIA al iniciar sesion: Core, tunel publico, minero, validador y panel.' | Out-Null
    Mostrar-Estado
} catch {
    # Registrar una tarea del Programador exige administrador. Sin el, se usa la
    # carpeta de Inicio, que hace exactamente lo mismo y no pide permisos: es lo
    # que usaba el proyecto antes del 26-10. Se deshace borrando ese .vbs.
    $inicio = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Startup'
    $destino = Join-Path $inicio 'KUKIA Guardia.vbs'
    Copy-Item -LiteralPath $wrapper -Destination $destino -Force
    "Sin permisos de administrador para la tarea programada; queda en la carpeta de Inicio:"
    "  $destino"
    "Para deshacerlo: borra ese fichero."
}
