# ===========================================================================
#  KUKIA - vigilante del Core  (v2, 27-10-2026)
#  ---------------------------------------------------------------------------
#  QUE HACE, EN UNA LINEA: si el Core del puerto vigilado (8300 por defecto) no
#  atiende, reinicia SOLO al proceso que ocupa ese puerto, con la identidad
#  verificada, sin prisas y con un freno que evita el bucle de reinicios.
#
#  DE DONDE VIENE (H-01 / H-02)
#  ----------------------------
#  La version vieja (la que estaba en %TEMP%\opencode\watch_core.ps1) buscaba
#  "los Cores" por la LINEA DE COMANDOS --`Where-Object { $_.CommandLine -match
#  "uvicorn\s+kukia_core" }`-- y luego mataba a todos los que encajaran:
#
#    H-01  El filtro no miraba el puerto: encajaba tambien el Core aislado de
#          tools\pruebas_seguridad.py, una replica, un ensayo o cualquier
#          uvicorn del repo. Y el unico sondeo era "¿contesta el 8300 en 10 s?",
#          asi que una operacion lenta (p.ej. /ledger/integridad con el ledger
#          entero) contaba como muerte. Stop-Process -Force es TerminateProcess:
#          sin traceback, sin bloques finally, codigo de salida -1 (0xFFFFFFFF)
#          y la conexion en curso cortada con ConnectionResetError 10054.
#    H-02  Con varios candidatos mataba `Select-Object -Skip 1`: se quedaba con
#          el PRIMERO que devolviera Windows (orden NO garantizado) y mataba el
#          resto. Segun el orden, podia matar el Core bueno y dejar vivo otro.
#
#  QUE HACE ESTA VERSION (y por que)
#  ---------------------------------
#  1) SOLO puede tocar al proceso que OCUPA EL PUERTO. Nunca mira lineas de
#     comandos para ELEGIR. No hay "elegir de una lista": el dueno del puerto
#     es unico (si hay varios, se miran los dos, y cada uno pasa por identidad).
#  2) IDENTIDAD VERIFICADA antes de nada: nombre del proceso, ruta del
#     ejecutable y (si se exige) que la linea de comandos mencione el Core.
#     Si no se puede confirmar, NO SE TOCA y se registra "MODO SEGURO" CON
#     ALERTA en disco (29-10-2026): antes solo quedaba una linea en el log y el
#     vigilante podia pasarse dias sin proteger nada. La lista de ejecutables
#     acepta python.exe y pythonw.exe: el lanzador con ventana oculta usa el
#     segundo y con una sola ruta el vigilante se ponia en MODO SEGURO.
#  3) NO mata por UN sondeo fallido: sondea varias veces (-Intentos, 3 por
#     defecto) y antes de dar por muerto al proceso mira si esta TRABAJANDO
#     (CPU acumulada subiendo). Lo lento gasta CPU, lo colgado no. Medido el
#     27-10-2026 con el ledger real y 4 integridades a la vez: 3 de 69 sondeos
#     pasaban de 10 s en un Core sano y ocupado -> con una sola sonda el
#     vigilante viejo mataba un Core sano.
#  4) PARADA ORDENADA antes de forzar: primero se intenta un cierre limpio
#     (URL de apagado si algun dia existe, o `taskkill` SIN /F, que manda
#     WM_CLOSE) y se le da margen. Solo si no muere se usa la fuerza, y queda
#     escrito en el registro con el motivo. Un `Stop-Process -Force` sin esto
#     es justo lo que convertia una lentitud en un cuelgue sin rastro.
#  5) FRENO anti-tormenta: retroceso exponencial con jitter, tope de reinicios
#     por ventana y enfriamiento entre intentos, con el estado en disco. Si se
#     pasa del tope, NO reinicia y deja una ALERTA clara (fichero + registro).
#  6) VENTANA DE GRACIA: despues de pedir un arranque no se le exige estar listo
#     al instante; se le da tiempo y se registra si aun esta arrancando.
#  7) UNA SOLA INSTANCIA por puerto (mutex). Dos ticks a la vez no duplican la
#     accion; el segundo sale sin tocar nada.
#  8) REGISTRO en ISO 8601 con zona horaria y con un id de ejecucion por tick,
#     una linea por decision, con rotacion por tamano. Si el registro no se
#     puede escribir, el vigilante sigue: quedarse sin registro es malo,
#     quedarse sin vigilante es peor.
#
#  LO QUE NO HACE (a proposito)
#  -----------------------------
#  * No arranca el Core por su cuenta si el puerto esta libre
#    ("vigilar_si_puerto_libre": false). El arranque automatico se quito el
#    26-10-2026 y esto no lo revierte. Solo PIDE la tarea KUKIA_Core despues de
#    haber reiniciado un Core colgado, y solo si "relanzar": true.
#  * No toca procesos que no ocupen el puerto vigilado, ni Cores de pruebas en
#    otros puertos, ni nada que no sea python segun la configuracion.
#  * No cambia la base, ni los sellos, ni las claves, ni la economia: no abre
#    siquiera el .db.
#
#  CONFIGURACION
#  -------------
#  Lee tools\vigilante_core.config.json (mismo directorio que este script). Los
#  parametros de la linea de comandos pisan a la configuracion. Si el fichero
#  falta o una clave no cuadra, se usan los valores por defecto de aqui y se
#  avisa en el registro.
#
#  Uso:
#    powershell -NoProfile -ExecutionPolicy Bypass -File tools\vigilante_core.ps1
#    ... -Puerto 8399 -Simular          decir que haria, sin matar ni relanzar
#    ... -SinFreno                      (pruebas) no aplicar el freno de reinicios
#    ... -Registro <ruta>               registro alternativo
#    ... -Configuracion <ruta.json>     configuracion alternativa
#
#  Codigos de salida: 0 = nada que hacer o hecho; 2 = error de configuracion;
#  3 = se reinicio el Core y no volvio a levantar.
#
#  OJO: la tarea programada Watch_Core lanza esto con wscript.exe, que termina
#  al instante. El "resultado 0" de la tarea SOLO significa "wscript arranco":
#  lo que de verdad paso esta en logs\vigilante_core.log.
# ===========================================================================
#Requires -Version 5.1
[CmdletBinding()]
param(
    [int]$Puerto = 0,
    [string]$TareaCore = "",
    [int]$EsperaStatsSeg = 0,
    [int]$Intentos = 0,
    [int]$EsperaEntreIntentosSeg = 0,
    [switch]$SinFreno,
    [switch]$Relanzar,
    [switch]$Simular,
    [string]$Registro = "",
    [string]$Configuracion = ""
)

$ErrorActionPreference = "Continue"

$Raiz   = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$RunId  = [guid]::NewGuid().ToString("N").Substring(0, 8)
$Inicio = Get-Date

# ------------------------------------------------------------------ config
$Cfg = [ordered]@{
    puerto                    = 8300
    tarea_core                = "KUKIA_Core"
    espera_stats_seg          = 10
    intentos                  = 3
    espera_entre_intentos_seg = 6
    cpu_trabajando_seg        = 0.2
    relanzar                  = $true
    verificar_arranque_seg    = 90
    vigilar_si_puerto_libre   = $false
    proceso_esperado          = [ordered]@{
        nombre_regex            = "^python"
        # lista: el Core corre igual con python.exe o con pythonw.exe (el
        # lanzador oculto usa pythonw). Ver R-01, 29-10-2026.
        ejecutable              = @(
            "C:\Users\kuki\AppData\Local\Programs\Python312\python.exe",
            "C:\Users\kuki\AppData\Local\Programs\Python312\pythonw.exe"
        )
        linea_comandos_contiene = "kukia_core"
        exigir_linea_comandos   = $true
    }
    parada_ordenada           = [ordered]@{ url_apagado = ""; espera_seg = 10 }
    freno                     = [ordered]@{
        activo          = $true
        max_reinicios   = 3
        ventana_min     = 60
        espera_base_seg = 60
        espera_max_seg  = 900
        jitter          = 0.25
    }
    registro                  = "logs\vigilante_core.log"
    registro_max_mb           = 2
    estado                    = "logs\vigilante_core.estado.json"
    alerta                    = "logs\vigilante_core.ALERTA.txt"
    no_tocar_si_identidad_dudosa = $true
}

$script:Desconocidas = @()

function Fusionar($destino, $origen) {
    if ($null -eq $origen) { return }
    foreach ($prop in $origen.PSObject.Properties) {
        $clave = $prop.Name
        if (-not $destino.Contains($clave)) { $script:Desconocidas += , $clave; continue }
        $actual = $destino[$clave]
        if ($actual -is [System.Collections.IDictionary]) { Fusionar $actual $prop.Value }
        else { $destino[$clave] = $prop.Value }
    }
}

if (-not $Configuracion) { $Configuracion = Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) "vigilante_core.config.json" }
$avisoConfig = ""
if (Test-Path $Configuracion) {
    try {
        $json = Get-Content -Path $Configuracion -Raw | ConvertFrom-Json
        Fusionar $Cfg $json
    } catch {
        $avisoConfig = "la configuracion ${Configuracion} no se pudo leer ($($_.Exception.Message)): sigo con los valores por defecto"
    }
} else {
    # OJO con las llaves: `$Configuracion:` lo lee PowerShell como "la unidad
    # $Configuracion" y el script no arranca. Ya paso en la v1 y volvio a pasar
    # aqui; por eso va entre llaves TODO lo que lleve dos puntos detras.
    $avisoConfig = "no existe ${Configuracion}: sigo con los valores por defecto"
}
if ($script:Desconocidas.Count -gt 0) {
    $avisoConfig = ($avisoConfig + " | claves desconocidas en la configuracion (ignoradas): " +
                    ($script:Desconocidas -join ", ")).Trim(" |")
}

# la linea de comandos pisa a la configuracion
if ($Puerto -gt 0)                    { $Cfg.puerto = $Puerto }
if ($TareaCore)                       { $Cfg.tarea_core = $TareaCore }
if ($EsperaStatsSeg -gt 0)            { $Cfg.espera_stats_seg = $EsperaStatsSeg }
if ($Intentos -gt 0)                  { $Cfg.intentos = $Intentos }
if ($EsperaEntreIntentosSeg -gt 0)    { $Cfg.espera_entre_intentos_seg = $EsperaEntreIntentosSeg }
if ($Relanzar)                        { $Cfg.relanzar = $true }
if ($Registro)                        { $Cfg.registro = $Registro }
if ($SinFreno)                        { $Cfg.freno.activo = $false }

function Absoluto([string]$ruta) {
    if ([System.IO.Path]::IsPathRooted($ruta)) { return $ruta }
    return (Join-Path $Raiz $ruta)
}

$RegistroAbs = Absoluto $Cfg.registro
$EstadoAbs   = Absoluto $Cfg.estado
$AlertaAbs   = Absoluto $Cfg.alerta
$Puerto      = [int]$Cfg.puerto
$TareaCore   = [string]$Cfg.tarea_core

# ------------------------------------------------------------------- registro
function Decir([string]$texto) {
    $sello = (Get-Date).ToString("yyyy-MM-ddTHH:mm:sszzz")
    $linea = "$sello [$RunId] $texto"
    # OJO, esto NO es Write-Output A PROPOSITO (fallo grave encontrado al probar):
    # Write-Output escribe en el flujo de SALIDA de la funcion, y eso se mezcla
    # con lo que la funcion devuelve. Al hacer `$limpio = ParadaOrdenada $pid`,
    # $limpio se quedaba con las frases del registro MAS el resultado, o sea un
    # array no vacio, y `if ($limpio)` daba $true SIEMPRE: el vigilante creia que
    # el Core habia cerrado con orden aunque siguiera vivo, y NUNCA llegaba a
    # forzar. Con [Console]::WriteLine se ve en pantalla igual (y en las pruebas)
    # pero no contamina ningun valor devuelto.
    [Console]::WriteLine("[vigilante] " + $texto)
    # -ErrorAction Stop en todo lo del registro: por defecto estos comandos dan
    # errores NO terminantes, asi que el try/catch no los cogia y el fallo salia
    # a la luz como un error suelto (se vio probando el caso del registro sin
    # permisos). Con Stop, el catch hace su trabajo: se traga el fallo y sigue.
    try {
        $carpeta = Split-Path -Parent $RegistroAbs
        if ($carpeta -and -not (Test-Path $carpeta)) {
            New-Item -ItemType Directory -Path $carpeta -Force -ErrorAction Stop | Out-Null
        }
        if ((Test-Path $RegistroAbs) -and ((Get-Item $RegistroAbs -ErrorAction Stop).Length -gt $Cfg.registro_max_mb * 1MB)) {
            Move-Item -Path $RegistroAbs -Destination "$RegistroAbs.1" -Force -ErrorAction Stop
        }
        Add-Content -Path $RegistroAbs -Value $linea -Encoding UTF8 -ErrorAction Stop
    } catch {
        # Sin registro el vigilante sigue funcionando. No se puede hacer mas.
    }
}

function PonerAlerta([string]$motivo) {
    try {
        $carpeta = Split-Path -Parent $AlertaAbs
        if ($carpeta -and -not (Test-Path $carpeta)) {
            New-Item -ItemType Directory -Path $carpeta -Force -ErrorAction Stop | Out-Null
        }
        $linea = "$((Get-Date).ToString('yyyy-MM-ddTHH:mm:sszzz'))  $motivo"
        Set-Content -Path $AlertaAbs -Value $linea -Encoding UTF8 -ErrorAction Stop
    } catch { }
}

function QuitarAlerta([string]$motivo) {
    try {
        if (Test-Path $AlertaAbs) {
            Remove-Item -Path $AlertaAbs -Force -ErrorAction Stop | Out-Null
            Decir "se retira la ALERTA anterior ($motivo)"
        }
    } catch { }
}

# ------------------------------------------------------------------- utilidades
# Devuelve la lista de PIDs que escuchan en ese puerto. Si NO SE PUDO MIRAR
# (fallo de CIM/permisos), deja el motivo en $script:ErrorPuerto; si el puerto
# esta libre, la lista viene vacia y ErrorPuerto queda vacio.
#
# QUE EL VALOR DEVUELTO NO INFORMA DE NADA (a proposito): un array vacio se
# desparrama y el que llama recibe $null, igual que si la funcion no devolviera
# nada. Por eso "no se pudo mirar" NO se cuenta con el valor devuelto, sino con
# el flag $script:ErrorPuerto, y quien llama siempre envuelve con @().
#
# (Tentacion que salio mal: devolver `,@(...)` para conservar el array vacio.
# Con la coma, lo que llega es un array DENTRO de otro y `[int]$duenos[0]`
# revienta con "no se puede convertir System.Object[] a System.Int32". Se probo
# y fallaba; queda escrito para no repetirlo.)
#
# Y ojo con el defecto que aparecio al probar esto: filtrar por -LocalPort en
# Get-NetTCPConnection LANZA "no encontro objetos" cuando no hay nadie
# escuchando, en vez de devolver una lista vacia. Con -ErrorAction Stop eso se
# confundia con un error de permisos y el vigilante se ponia en modo seguro con
# el puerto libre. Se piden TODOS los listeners y se filtra en memoria.
$script:ErrorPuerto = ""
function DuenoDelPuerto([int]$p) {
    $script:ErrorPuerto = ""
    try {
        $todos = Get-NetTCPConnection -State Listen -ErrorAction Stop
    } catch {
        $script:ErrorPuerto = "$($_.Exception.Message)"
        if (-not $script:ErrorPuerto) { $script:ErrorPuerto = "fallo al consultar los puertos (sin mensaje)" }
        return
    }
    $con = @($todos | Where-Object { [int]$_.LocalPort -eq $p } |
             Select-Object -ExpandProperty OwningProcess -Unique)
    foreach ($uno in @($con | Where-Object { $_ -gt 0 })) { $uno }
}

function Sonda([int]$p) {
    try {
        $r = Invoke-WebRequest -Uri "http://127.0.0.1:$p/stats" -TimeoutSec ([int]$Cfg.espera_stats_seg) -UseBasicParsing
        if ($r.StatusCode -eq 200) { return "200" }
        return "HTTP $($r.StatusCode)"
    } catch {
        $e = $_.Exception
        if ($e -is [System.Net.WebException]) {
            if ($e.Response -ne $null -and $e.Response.StatusCode -ne $null) {
                return ("HTTP " + [int]$e.Response.StatusCode)
            }
            switch ("$($e.Status)") {
                "Timeout"               { return "timeout" }
                "ConnectFailure"        { return "conexion rechazada" }
                "NameResolutionFailure" { return "no resuelve el nombre" }
                default                 { return "error de red: $($e.Status)" }
            }
        }
        return "error: $($e.GetType().Name)"
    }
}

function Identidad([int]$idProceso) {
    try {
        $c = Get-CimInstance Win32_Process -Filter "ProcessId=$idProceso" -ErrorAction Stop
        if ($null -eq $c) { $script:ErrorIdentidad = "ningun proceso con ese PID"; return $null }
        # La fecha de creacion no se puede calcular dentro del literal: en
        # PowerShell 5.1 un try/catch no es una expresion. Se calcula antes.
        $creado = ""
        try { $creado = ([datetime]$c.CreationDate).ToString("yyyy-MM-ddTHH:mm:ss") } catch { }
        return [ordered]@{
            pid        = [int]$c.ProcessId
            nombre     = "$($c.Name)"
            ejecutable = "$($c.ExecutablePath)"
            linea      = "$($c.CommandLine)"
            creado     = $creado
            padre      = [int]$c.ParentProcessId
        }
    } catch {
        $script:ErrorIdentidad = $_.Exception.Message
        return $null
    }
}

$script:MotivoIdentidad = ""
function IdentidadEsperada($id) {
    if ($null -eq $id) {
        $detalle = ""
        if ($script:ErrorIdentidad) { $detalle = " (motivo: $($script:ErrorIdentidad))" }
        $script:MotivoIdentidad = "no se pudo leer el proceso, ya no existe o sin permisos$detalle"
        return $false
    }
    if ($id.nombre -notmatch $Cfg.proceso_esperado.nombre_regex) {
        $script:MotivoIdentidad = "su nombre ('$($id.nombre)') no encaja con '$($Cfg.proceso_esperado.nombre_regex)'"
        return $false
    }
    # El ejecutable puede venir como texto o como LISTA (29-10-2026, R-01).
    # `python.exe` y `pythonw.exe` son el mismo interprete de la misma carpeta:
    # el lanzador con ventana oculta arranca el Core con pythonw, y con la
    # configuracion de una sola ruta el vigilante se ponia en MODO SEGURO y
    # dejaba de protegerlo durante horas sin que nadie se enterara (log del
    # 28-10 a las 19:49). Ahora se aceptan las dos rutas, comparadas ENTERAS y
    # sin distinguir mayusculas: sigue sin valer cualquier otro python de otra
    # instalacion, ni un proceso que solo se llame parecido.
    $esperados = @($Cfg.proceso_esperado.ejecutable) |
                 Where-Object { "$_" } | ForEach-Object { "$_" }
    if ($esperados.Count -gt 0) {
        if (-not $id.ejecutable) { $script:MotivoIdentidad = "no se pudo leer la ruta del ejecutable"; return $false }
        $rutaReal = "$($id.ejecutable)"
        try { $rutaReal = [System.IO.Path]::GetFullPath("$($id.ejecutable)") } catch { }
        $encaja = $false
        foreach ($e in $esperados) {
            $candidato = "$e"
            try { $candidato = [System.IO.Path]::GetFullPath("$e") } catch { }
            if ($rutaReal -ieq $candidato) { $encaja = $true; break }
        }
        if (-not $encaja) {
            $script:MotivoIdentidad = ("su ejecutable ('$rutaReal') no es ninguno de los esperados ('" +
                                       ($esperados -join "', '") + "')")
            return $false
        }
    }
    if ($Cfg.proceso_esperado.exigir_linea_comandos) {
        $clave = "$($Cfg.proceso_esperado.linea_comandos_contiene)"
        if ($clave -and ($id.linea -notlike "*$clave*")) {
            $script:MotivoIdentidad = "su linea de comandos no menciona '$clave'"
            return $false
        }
    }
    return $true
}

function PeticionesEnCurso([int]$p, [int]$idProceso) {
    # Conexiones ESTABLECIDAS en el puerto vigilado cuyo dueno es ese PID: o sea,
    # el Core esta atendiendo a alguien AHORA MISMO. Una peticion larga (una
    # integridad del ledger, una copia, un pago) no gasta CPU necesariamente:
    # puede estar esperando al disco o a un bloqueo. Mirar las conexiones en
    # curso es lo que distingue "ocupado" de "colgado" cuando la CPU no lo dice.
    #
    # Sin -ErrorAction Stop a proposito: filtrar por un puerto sin conexiones
    # tambien lanza en Get-NetTCPConnection cuando no hay coincidencias.
    try {
        $c = @(Get-NetTCPConnection -LocalPort $p -State Established -ErrorAction SilentlyContinue |
               Where-Object { [int]$_.OwningProcess -eq $idProceso })
        return $c.Count
    } catch { return 0 }
}

function Cpu([int]$idProceso) {
    try { return [double](Get-Process -Id $idProceso -ErrorAction Stop).TotalProcessorTime.TotalSeconds }
    catch { return $null }
}

function SigueVivo([int]$idProceso) {
    return [bool](Get-Process -Id $idProceso -ErrorAction SilentlyContinue)
}

function EstadoTarea([string]$nombre) {
    try {
        $t = Get-ScheduledTask -TaskName $nombre -ErrorAction Stop
        $i = $t | Get-ScheduledTaskInfo
        return "$($t.State) (habilitada=$($t.Settings.Enabled), ultima vez $($i.LastRunTime), resultado $($i.LastTaskResult))"
    } catch { return "NO EXISTE o sin permiso: $($_.Exception.Message)" }
}

# ------------------------------------------------------------------- estado/freno
function LeerEstado() {
    try {
        if (Test-Path $EstadoAbs) { return (Get-Content -Path $EstadoAbs -Raw | ConvertFrom-Json) }
    } catch { }
    return $null
}

function GuardarEstado($obj) {
    try {
        $carpeta = Split-Path -Parent $EstadoAbs
        if ($carpeta -and -not (Test-Path $carpeta)) { New-Item -ItemType Directory -Path $carpeta -Force | Out-Null }
        ($obj | ConvertTo-Json -Depth 5) | Set-Content -Path $EstadoAbs -Encoding UTF8
    } catch { Decir "no se pudo guardar el estado ($($_.Exception.Message)): el freno no tendra memoria de los reinicios anteriores" }
}

function HaceCuantoMin([string]$iso) {
    try { return ((Get-Date) - [datetime]::Parse($iso)).TotalMinutes } catch { return 1e9 }
}

# ------------------------------------------------------------------- parada
function ParadaOrdenada([int]$idProceso) {
    $url = "$($Cfg.parada_ordenada.url_apagado)"
    if ($url) {
        try {
            Invoke-WebRequest -Uri $url -TimeoutSec 5 -UseBasicParsing | Out-Null
            Decir "se pidio la parada ordenada por $url"
        } catch {
            Decir "la URL de apagado ($url) no respondio: $($_.Exception.Message)"
        }
    }
    # taskkill SIN /F manda WM_CLOSE (cierre limpio). Un proceso sin ventana lo
    # rechaza: eso queda escrito, para que el uso de la fuerza tenga motivo.
    $tk = Join-Path $env:SystemRoot "System32\taskkill.exe"
    try {
        $salida = & $tk /PID $idProceso 2>&1
        $texto = (($salida | Out-String) -replace "\s+", " ").Trim()
        if ($texto) { Decir "cierre limpio (taskkill sin /F): $texto" }
    } catch {
        Decir "no se pudo intentar el cierre limpio: $($_.Exception.Message)"
    }
    $hasta = (Get-Date).AddSeconds([int]$Cfg.parada_ordenada.espera_seg)
    while ((Get-Date) -lt $hasta) {
        if (-not (SigueVivo $idProceso)) { return $true }
        Start-Sleep -Milliseconds 500
    }
    return $false
}

# =========================================================== ARRANQUE
Decir ("tick v2 (puerto $Puerto, simular=$($Simular.IsPresent), relanzar=$($Cfg.relanzar), " +
       "intentos=$($Cfg.intentos), espera=$($Cfg.espera_stats_seg)s, freno=$($Cfg.freno.activo))")
if ($avisoConfig) { Decir "configuracion: $avisoConfig" }
Decir ("config efectiva: tarea=$TareaCore, identidad exigida: nombre '$($Cfg.proceso_esperado.nombre_regex)', " +
       "ejecutable '$(@($Cfg.proceso_esperado.ejecutable) -join "' o '")', " +
       "linea contiene '$($Cfg.proceso_esperado.linea_comandos_contiene)': " +
       "$($Cfg.proceso_esperado.exigir_linea_comandos)")

# --- mutex: una sola instancia por puerto
$mutex = $null
$soyElUnico = $true
try {
    $mutex = New-Object System.Threading.Mutex($false, "KUKIA_vigilante_core_$Puerto")
    $soyElUnico = $mutex.WaitOne(0)
} catch {
    $soyElUnico = $true   # si el mutex no se puede tomar, se sigue: quedarse sin vigilante es peor
}
if (-not $soyElUnico) {
    Decir "ya hay otro vigilante en marcha para el puerto ${Puerto}: esta ejecucion sale sin tocar nada"
    exit 0
}

$estado = LeerEstado
$isoAhora = (Get-Date).ToString("yyyy-MM-ddTHH:mm:sszzz")

$duenos = @(DuenoDelPuerto $Puerto)
if ($script:ErrorPuerto) {
    Decir ("MODO SEGURO: no se pudo consultar quien ocupa el puerto $Puerto " +
           "($($script:ErrorPuerto)). No se toca NADA y se reintenta en el proximo tick.")
    exit 0
}

# --- ¿cambio el dueno del puerto desde el ultimo tick? (detecta reinicios ajenos
#     y la reutilizacion de un PID)
if ($estado -and $estado.puerto_pid -and $duenos.Count -gt 0) {
    $pidAntes = [int]$estado.puerto_pid
    if ($duenos -notcontains $pidAntes) {
        Decir "cambio de PID en el puerto ${Puerto}: antes $pidAntes, ahora $(($duenos -join ', '))"
    } elseif ($estado.pid_creado) {
        $idAhora = Identidad $pidAntes
        if ($idAhora -and $idAhora.creado -and $idAhora.creado -ne "$($estado.pid_creado)") {
            Decir ("OJO: el PID $pidAntes ocupa el puerto pero se creo en otro momento " +
                   "(antes $($estado.pid_creado), ahora $($idAhora.creado)): PID reutilizado, se trata como proceso nuevo")
        }
    }
}

# --- puerto libre
if ($duenos.Count -eq 0) {
    $infoT = EstadoTarea $TareaCore
    Decir ("el puerto $Puerto no lo ocupa nadie: el Core esta apagado. No se mata nada. " +
           "Tarea ${TareaCore}: $infoT")

    $reiniciosLibre = @()
    $siguienteLibre = ""
    if ($estado -and $estado.reinicios) { $reiniciosLibre = @($estado.reinicios) }
    if ($estado -and $estado.siguiente_permitido) { $siguienteLibre = "$($estado.siguiente_permitido)" }

    # 27-10-2026, al activar el arranque automatico: hay que distinguir "no habia
    # nada que hacer" de "lo he pedido y no ha servido". Antes TODO este bloque
    # terminaba en el mismo QuitarAlerta y en exit 0, asi que (a) si la tarea no
    # existia o estaba deshabilitada, la ALERTA se escribia y se borraba tres
    # lineas despues, y (b) si la tarea se pedia y el Core no llegaba a
    # levantarse, el vigilante se callaba con resultado 0. Las dos cosas las
    # destapo prueba_arranque_auto_aislada.py (casos a1 y a4).
    $noLevantoLibre = $false
    $pidGuardar     = 0
    $creadoGuardar  = ""

    if (-not $Cfg.vigilar_si_puerto_libre) {
        # Comportamiento por defecto: el Core se arranca a mano. El arranque
        # automatico se quito el 26-10-2026 y esto no lo revierte.
        Decir "no se arranca el Core aqui ('vigilar_si_puerto_libre' esta en false): lo arrancas tu"
    } elseif ($infoT -like "NO EXISTE*") {
        Decir "ALERTA: no puedo pedir la tarea ${TareaCore} porque no existe: el Core se queda apagado. $infoT"
        PonerAlerta "vigilante: la tarea $TareaCore no existe y el puerto $Puerto esta apagado"
        $noLevantoLibre = $true
    } elseif ($infoT -like "Disabled*") {
        Decir ("ALERTA: la tarea ${TareaCore} esta DESHABILITADA y el puerto esta apagado. " +
               "Para que el Core se levante: Enable-ScheduledTask -TaskName '$TareaCore'")
        PonerAlerta "vigilante: la tarea $TareaCore esta deshabilitada y el puerto $Puerto esta apagado"
        $noLevantoLibre = $true
    } elseif ($estado -and $siguienteLibre -and ((Get-Date) -lt [datetime]::Parse($siguienteLibre))) {
        Decir "el puerto esta libre pero en enfriamiento hasta ${siguienteLibre}: no se pide la tarea"
    } elseif (@($reiniciosLibre | Where-Object { (HaceCuantoMin $_) -lt [int]$Cfg.freno.ventana_min }).Count -ge [int]$Cfg.freno.max_reinicios) {
        Decir ("ALERTA: a la tarea $TareaCore ya se le pidio $($Cfg.freno.max_reinicios) veces en " +
               "$($Cfg.freno.ventana_min) min y el Core no arranca: dejo de pedirla (mira logs\core.log)")
        PonerAlerta "vigilante: la tarea $TareaCore no levanta el Core tras $($Cfg.freno.max_reinicios) intentos"
        $noLevantoLibre = $true
    } elseif ($Simular) {
        Decir "SIMULACION: pediria la tarea $TareaCore (puerto libre y vigilar_si_puerto_libre=true)"
    } else {
        Decir "pidiendo la tarea $TareaCore (puerto libre y vigilar_si_puerto_libre=true). Estado: $infoT"
        try {
            Start-ScheduledTask -TaskName $TareaCore -ErrorAction Stop

            # --- ventana de gracia: no se da la peticion por buena sin mirar.
            #     "Resultado 0" de la tarea SOLO significa "el lanzador arranco".
            $hastaLibre = (Get-Date).AddSeconds([int]$Cfg.verificar_arranque_seg)
            $listoLibre = $false
            while ((Get-Date) -lt $hastaLibre) {
                if ((Sonda $Puerto) -eq "200") { $listoLibre = $true; break }
                Start-Sleep -Seconds 2
            }
            if ($listoLibre) {
                $pidLibre = @(DuenoDelPuerto $Puerto)
                Decir "la tarea $TareaCore levanto el Core y /stats responde (PID $(($pidLibre -join ', ')))"
                $pidGuardar = [int](@($pidLibre)[0])
                $idNuevo = Identidad $pidGuardar
                if ($idNuevo) { $creadoGuardar = "$($idNuevo.creado)" }
            } else {
                Decir ("ALERTA: se pidio la tarea $TareaCore pero el puerto $Puerto sigue sin responder tras " +
                       "$($Cfg.verificar_arranque_seg) s. Lo que paso esta en logs\core.log.")
                PonerAlerta "vigilante: se pidio la tarea $TareaCore y el Core no arranco (el puerto $Puerto esta libre)"
                $noLevantoLibre = $true
                # el freno: no se repite la peticion en cada tick sin control
                $reiniciosLibre = @($reiniciosLibre) + , $isoAhora
                $siguienteLibre = (Get-Date).AddSeconds([int]$Cfg.freno.espera_base_seg).ToString("yyyy-MM-ddTHH:mm:sszzz")
            }
        } catch {
            Decir "ALERTA: al pedir la tarea ${TareaCore} dio error: $($_.Exception.Message)"
            PonerAlerta "vigilante: no se pudo pedir la tarea $TareaCore ($($_.Exception.Message))"
            $noLevantoLibre = $true
        }
    }

    GuardarEstado @{
        puerto = $Puerto; ultimo_tick = $isoAhora; puerto_pid = $pidGuardar; pid_creado = $creadoGuardar
        reinicios = $reiniciosLibre; siguiente_permitido = $siguienteLibre
    }
    if ($noLevantoLibre) {
        Decir "hay ALERTA pendiente en $AlertaAbs (el Core no esta corriendo y esta tarea deberia levantarlo)"
        exit 3
    }
    QuitarAlerta "el puerto esta libre, no hay Core colgado"
    exit 0
}

$reiniciados = 0
$noLevanto = $false
$alertaPuesta = $false

foreach ($idProceso in $duenos) {
    $idProceso = [int]$idProceso
    $id = Identidad $idProceso

    if (-not (IdentidadEsperada $id)) {
        Decir ("MODO SEGURO: el PID $idProceso ocupa el puerto $Puerto pero su identidad no esta " +
               "confirmada ($($script:MotivoIdentidad)). NO se toca. Si es el Core de verdad, ajusta " +
               "'proceso_esperado' en vigilante_core.config.json.")
        # 29-10-2026 (R-01): callarse aqui era el fallo de verdad. MODO SEGURO es
        # lo correcto (no se toca un proceso que no se puede identificar), pero
        # significa que el vigilante NO ESTA PROTEGIENDO el puerto: si el Core se
        # cuelga, nadie lo levanta. Antes solo quedaba una linea en el log.
        # Ahora se deja ALERTA en disco, que es lo que mira el fundador.
        PonerAlerta ("vigilante: el PID $idProceso ocupa el puerto $Puerto y su identidad no esta " +
                     "confirmada ($($script:MotivoIdentidad)). El vigilante NO protege ese Core " +
                     "mientras siga asi: si se cuelga, no se reinicia.")
        $alertaPuesta = $true
        continue
    }
    Decir ("identidad confirmada: PID $idProceso '$($id.nombre)' creado $($id.creado), padre $($id.padre), exe $($id.ejecutable)")

    $t0       = Get-Date
    $cpu0     = Cpu $idProceso
    # Peticiones en curso ANTES de sondear: o sea, sin contar las conexiones del
    # propio vigilante. Un Core atendiendo una peticion larga ya tiene la conexion
    # establecida cuando empieza el tick; si se mirara solo al final, la peticion
    # (que puede haber terminado durante los 17 s de sondas) ya no se veria.
    $enCursoInicio = PeticionesEnCurso $Puerto $idProceso
    $respondio = $false          # contesto 200
    $respuestas = @()            # contesto, pero con un codigo de error (sigue VIVO)
    $sinRespuesta = @()          # no contesto nada (timeout / rechazada / red)
    $trabajando = $false
    $detalle  = @()
    $ultimaSonda = ""

    for ($k = 1; $k -le [int]$Cfg.intentos; $k++) {
        $ultimaSonda = Sonda $Puerto
        if ($ultimaSonda -eq "200") { $respondio = $true; break }
        if (-not (SigueVivo $idProceso)) { $detalle += "intento $k : el PID ya no existe"; break }
        # OJO: "responde con error" y "no responde" NO son lo mismo. Si contesta
        # HTTP 500 el proceso esta vivo y atendiendo: eso es un estado degradado,
        # no un cuelgue, y no se mata por ello (mata el diagnostico, no el Core).
        if ($ultimaSonda -like "HTTP *") { $respuestas += "$ultimaSonda (intento $k)" }
        else { $sinRespuesta += "$ultimaSonda (intento $k)" }
        $cpu = Cpu $idProceso
        $gastado = $null
        if ($null -ne $cpu -and $null -ne $cpu0) { $gastado = $cpu - $cpu0 }
        $detalle += ("intento {0} : {1}{2}" -f $k, $ultimaSonda,
            $(if ($null -ne $gastado) { ", CPU +{0:N2} s" -f $gastado } else { "" }))
        if ($enCursoInicio -gt 0) { break }   # ya sabemos que estaba trabajando
        if ($null -ne $gastado -and $gastado -gt [double]$Cfg.cpu_trabajando_seg) { $trabajando = $true; break }
        if ($k -lt [int]$Cfg.intentos) { Start-Sleep -Seconds ([int]$Cfg.espera_entre_intentos_seg) }
    }

    if ($respondio) {
        Decir "el puerto $Puerto responde en /stats (PID $idProceso): sano, no se toca (aunque tarde)"
        QuitarAlerta "el Core responde"
        continue
    }
    if ($respuestas.Count -gt 0) {
        Decir ("el PID $idProceso CONTESTA pero con error ($($respuestas -join ', ')): el proceso esta vivo y " +
               "escuchando. NO se toca. Es un estado DEGRADADO: hay que mirar /stats a mano. " + ($detalle -join "; "))
        PonerAlerta "vigilante: el Core del puerto $Puerto contesta con error ($($respuestas -join ', ')) y NO se ha tocado (degrada, no esta colgado)"
        continue
    }
    if ($trabajando) {
        Decir ("el PID $idProceso no contesta ($ultimaSonda) pero esta TRABAJANDO (CPU subiendo durante la " +
               "espera): no se toca, se revisa en el proximo tick. " + ($detalle -join "; "))
        continue
    }
    if ($enCursoInicio -gt 0) {
        Decir ("el PID $idProceso no contesta por HTTP pero tenia $enCursoInicio conexion(es) en curso en el puerto " +
               "${Puerto} al empezar el tick: esta ATENDIENDO trabajo (puede ir lento). NO se toca. " + ($detalle -join "; "))
        continue
    }
    if (-not (SigueVivo $idProceso)) {
        Decir "el PID $idProceso desaparecio durante la comprobacion: no hay nada que reiniciar. " + ($detalle -join "; ")
        continue
    }

    # --- freno: ¿se puede reiniciar ya?
    $reinicios = @()
    if ($estado -and $estado.reinicios) { $reinicios = @($estado.reinicios | Where-Object { (HaceCuantoMin "$_") -lt 1440 }) }
    $recientes = @($reinicios | Where-Object { (HaceCuantoMin "$_") -lt [double]$Cfg.freno.ventana_min })
    $permitido = $true
    $motivoFreno = "freno desactivado"
    if ($Cfg.freno.activo) {
        $siguiente = ""
        if ($estado -and $estado.siguiente_permitido) { $siguiente = "$($estado.siguiente_permitido)" }
        if ($recientes.Count -ge [int]$Cfg.freno.max_reinicios) {
            $permitido = $false
            $motivoFreno = "ALERTA: ya se reinicio $($recientes.Count) veces en $($Cfg.freno.ventana_min) min (tope $($Cfg.freno.max_reinicios))"
        } elseif ($siguiente) {
            try {
                if ((Get-Date) -lt [datetime]::Parse($siguiente)) {
                    $permitido = $false
                    $motivoFreno = "en enfriamiento hasta $(([datetime]::Parse($siguiente)).ToString('HH:mm:ss'))"
                } else { $motivoFreno = "sin reinicios recientes" }
            } catch { $motivoFreno = "sin reinicios recientes" }
        } else { $motivoFreno = "primer reinicio" }
    }

    if (-not $permitido) {
        Decir ("NO se reinicia el PID $idProceso ($motivoFreno). Detalle de las sondas: " + ($detalle -join "; "))
        if ($motivoFreno -like "ALERTA*") {
            PonerAlerta "vigilante: $motivoFreno (puerto $Puerto, PID $idProceso no responde)"
            $alertaPuesta = $true
        }
        continue
    }

    # --- justo antes de actuar: ¿sigue siendo el dueno del puerto y el mismo proceso?
    $ahoraDuenos = @(DuenoDelPuerto $Puerto)
    if ($script:ErrorPuerto) {
        Decir ("MODO SEGURO: justo antes de actuar no se pudo confirmar quien ocupa el puerto $Puerto " +
               "($($script:ErrorPuerto)): no se toca el PID $idProceso")
        continue
    }
    if ($ahoraDuenos -notcontains $idProceso) {
        Decir ("el PID $idProceso ya no ocupa el puerto $Puerto (ahora: $(($ahoraDuenos -join ', '))): no hay nada que reiniciar")
        continue
    }
    $id2 = Identidad $idProceso
    if (-not (IdentidadEsperada $id2) -or $id2.creado -ne $id.creado) {
        Decir ("MODO SEGURO: el PID $idProceso cambio de identidad entre la comprobacion y la accion " +
               "($($script:MotivoIdentidad) creado antes $($id.creado), ahora $($id2.creado)). NO se toca.")
        continue
    }

    $ventana = [int]((Get-Date) - $t0).TotalSeconds
    if ($Simular) {
        Decir ("SIMULACION: reiniciaria el PID $idProceso (ocupa $Puerto, $($Cfg.intentos) sondas sin 200 " +
               "en $ventana s, sin gasto de CPU; ultima sonda: $ultimaSonda). " + ($detalle -join "; "))
        continue
    }

    # --- reinicio: primero cierre limpio, luego la fuerza (y se dice por que)
    Decir ("el PID $idProceso ocupa $Puerto, NO responde ($ultimaSonda tras $($Cfg.intentos) sondas en $ventana s) " +
           "y no consume CPU: se intenta cerrar con orden. " + ($detalle -join "; "))
    $limpio = ParadaOrdenada $idProceso
    if ($limpio) {
        Decir "el PID $idProceso cerro con orden (sin forzar)"
    } else {
        if (-not (SigueVivo $idProceso)) {
            Decir "el PID $idProceso ya no existe"
        } else {
            Decir ("el PID $idProceso no cerro con orden en $($Cfg.parada_ordenada.espera_seg) s y sigue sin " +
                   "responder: se fuerza (motivo: Core colgado, identidad verificada, cierre limpio agotado)")
            Stop-Process -Id $idProceso -Force -ErrorAction SilentlyContinue
        }
    }
    $reiniciados++

    # --- apuntar el reinicio y calcular el proximo permitido (retroceso + jitter)
    $reinicios += $isoAhora
    $n = @($reinicios | Where-Object { (HaceCuantoMin "$_") -lt [double]$Cfg.freno.ventana_min }).Count
    $base = [double]$Cfg.freno.espera_base_seg * [math]::Pow(2, [math]::Max(0, $n - 1))
    $espera = [math]::Min($base, [double]$Cfg.freno.espera_max_seg)
    $ruido = 1 + ([double]$Cfg.freno.jitter * ([double](Get-Random -Minimum 0 -Maximum 1000) / 1000.0))
    $espera = [math]::Round($espera * $ruido, 0)
    $proximo = (Get-Date).AddSeconds($espera).ToString("yyyy-MM-ddTHH:mm:sszzz")
    Decir "reinicio nº $n en la ventana: el proximo permitido sera $proximo (espera ${espera}s con jitter de $($Cfg.freno.jitter))"

    $estado = @{
        puerto = $Puerto; ultimo_tick = $isoAhora; puerto_pid = 0; pid_creado = ""
        reinicios = $reinicios; siguiente_permitido = $proximo
    }
    GuardarEstado $estado

    # --- recuperacion: comprobar la tarea y pedir el arranque UNA vez
    $infoTarea = EstadoTarea $TareaCore
    if (-not $Cfg.relanzar) {
        Decir "no se pide la tarea ${TareaCore} ('relanzar' esta desactivado). Estado: $infoTarea"
        continue
    }
    if ($infoTarea -like "NO EXISTE*") {
        Decir "ALERTA: no puedo pedir la tarea ${TareaCore} porque no existe: el Core se queda apagado. $infoTarea"
        PonerAlerta "vigilante: la tarea $TareaCore no existe (puerto $Puerto apagado tras reiniciar)"
        $alertaPuesta = $true
        $noLevanto = $true
        continue
    }
    if ($infoTarea -like "Disabled*") {
        Decir ("ALERTA: la tarea ${TareaCore} esta DESHABILITADA, asi que el Core no se va a levantar solo. " +
               "Para que el vigilante pueda reponerlo: Enable-ScheduledTask -TaskName '$TareaCore'")
        PonerAlerta "vigilante: la tarea $TareaCore esta deshabilitada (puerto $Puerto apagado tras reiniciar)"
        $alertaPuesta = $true
        $noLevanto = $true
        continue
    }

    Start-Sleep -Seconds 5
    if (@(DuenoDelPuerto $Puerto).Count -gt 0) {
        Decir "el puerto $Puerto sigue ocupado: no se pide la tarea (evita el bucle de puerto en uso)"
        continue
    }
    Decir "pidiendo la tarea $TareaCore (estado: $infoTarea)"
    try {
        Start-ScheduledTask -TaskName $TareaCore -ErrorAction Stop
    } catch {
        Decir "ALERTA: al pedir la tarea ${TareaCore} dio error: $($_.Exception.Message)"
        PonerAlerta "vigilante: no se pudo pedir la tarea $TareaCore ($($_.Exception.Message))"
        $alertaPuesta = $true
        $noLevanto = $true
        continue
    }

    # --- ventana de gracia: no se le exige estar listo al instante
    $hasta = (Get-Date).AddSeconds([int]$Cfg.verificar_arranque_seg)
    $listo = $false
    while ((Get-Date) -lt $hasta) {
        if ((Sonda $Puerto) -eq "200") { $listo = $true; break }
        Start-Sleep -Seconds 2
    }
    if ($listo) {
        $nuevoPid = (@(DuenoDelPuerto $Puerto) -join ", ")
        Decir "el Core volvio a responder en /stats (PID $nuevoPid) dentro de la ventana de gracia"
        QuitarAlerta "el Core volvio a responder tras el reinicio"
        if ($estado) { $estado.puerto_pid = [int](@(DuenoDelPuerto $Puerto)[0]); $estado.pid_creado = "$((Identidad ([int](@(DuenoDelPuerto $Puerto)[0]))).creado)" }
        GuardarEstado $estado
    } else {
        Decir ("el Core NO responde todavia tras $($Cfg.verificar_arranque_seg) s de gracia: se deja seguir " +
               "arrancando y se revisa en el proximo tick (no se vuelve a reiniciar: el freno manda)")
        $noLevanto = $true
    }
}

# --- estado final del tick: quien ocupa el puerto ahora y como quedo el freno
$pidFinal = 0; $creadoFinal = ""
$duenosFin = @(DuenoDelPuerto $Puerto)
if (-not $script:ErrorPuerto -and $duenosFin.Count -gt 0) {
    $pidFinal = [int](@($duenosFin)[0])
    $idFin = Identidad $pidFinal
    if ($idFin) { $creadoFinal = "$($idFin.creado)" }
}
$reiniciosFinal = @()
$siguienteFinal = ""
if ($estado -and $estado.reinicios) { $reiniciosFinal = @($estado.reinicios) }
if ($estado -and $estado.siguiente_permitido) { $siguienteFinal = "$($estado.siguiente_permitido)" }
GuardarEstado @{
    puerto = $Puerto; ultimo_tick = $isoAhora; puerto_pid = $pidFinal; pid_creado = $creadoFinal
    reinicios = $reiniciosFinal; siguiente_permitido = $siguienteFinal
}

if ($reiniciados -eq 0) {
    Decir "nada que reiniciar (0 procesos tocados)"
} else {
    Decir "reiniciados $reiniciados proceso(s) del puerto $Puerto"
}
if ($alertaPuesta) { Decir "hay ALERTA pendiente en $AlertaAbs (revisala: algo no esta yendo como debe)" }

$segundos = [int]((Get-Date) - $Inicio).TotalSeconds
Decir "fin del tick en ${segundos}s ($reiniciados proceso(s) tocado(s))"
if ($noLevanto) { exit 3 }
exit 0
