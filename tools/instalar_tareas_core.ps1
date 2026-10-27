# ===========================================================================
#  KUKIA - instalador de las DOS tareas del Core + verificacion de piezas
#  (27-10-2026, v2)
#  ---------------------------------------------------------------------------
#  QUE HACE, EN UNA LINEA: que Watch_Core solo pueda actuar sobre el proceso
#  dueño del puerto 8300, que KUKIA_Core deje de ser una tarea rota, y que todo
#  se pueda verificar y deshacer.
#
#  MODOS (ninguno arranca el Core, ninguno toca la base, ninguno borra nada):
#
#    -VerificarHashes   SOLO LEE: compara las piezas del vigilante con el
#                       manifiesto (tools\vigilante_core.hashes.json).
#    -SellarHashes      escribe el manifiesto con los hashes de ahora mismo.
#                       (Se usa a proposito, despues de revisar un cambio.)
#    -Simular           dice que cambiaria, sin registrar nada.
#    -Desinstalar       vuelve a las tareas anteriores desde la ultima copia
#                       de logs\tareas_backup_*. Es el "deshacer" de una linea.
#    -Revertir <carpeta>  lo mismo, pero desde una copia concreta.
#    (sin modos)        instala: verifica hashes, comprueba el estado real,
#                       copia las tareas, las registra y valida el resultado.
#                       Si la validacion final falla, REPONE las tareas que
#                       habia (reversion automatica) y sale con codigo 4.
#
#  QUE CAMBIA AL INSTALAR (y NADA MAS):
#
#    Watch_Core   accion -> wscript.exe "<repo>\tools\vigilante_core_oculto.vbs"
#                 (su disparador de 5 minutos, su usuario y su IgnoreNew se
#                  quedan EXACTAMENTE como estaban)
#
#    KUKIA_Core   accion -> wscript.exe "<repo>\tools\lanzar_core_oculto.vbs"
#                 Enabled -> true
#                 disparadores -> UNO: al INICIAR SESION, con 30 s de retardo
#                   (27-10-2026: arranque automatico pedido por Kuki; antes se
#                   habia quitado el 26-10 y la tarea se quedaba sin
#                   disparadores). El usuario es el de la propia tarea
#                   (InteractiveToken), asi que no hay que guardar contrasena.
#                   Ademas de ese disparador, la tarea se pide desde fuera en
#                   dos casos: el vigilante cuando el 8300 esta libre
#                   (vigilante_core.config.json: vigilar_si_puerto_libre=true) y
#                   el vigilante cuando tiene que reiniciar un Core colgado
#                   (su envoltorio lleva -Relanzar).
#                 ExecutionTimeLimit -> PT0S (sin limite)
#
#  ANTES (comprobado el 27-10-2026), por que hacia falta:
#    Watch_Core -> wscript.exe %TEMP%\opencode\oc_watch.vbs  (una carpeta que
#      Windows puede vaciar). KUKIA_Core estaba deshabilitada, con un disparador
#      de una fecha pasada y ExecutionTimeLimit de 8 h.
#
#  Nota: crear o modificar tareas programadas pide permisos de administrador.
# ===========================================================================

param(
    [switch]$Simular,
    [switch]$Desinstalar,
    [switch]$VerificarHashes,
    [switch]$SellarHashes,
    [switch]$Forzar,
    [string]$Revertir = ""
)

$ErrorActionPreference = "Stop"

$Repo         = "C:\Users\kuki\Desktop\KUKIA"
$VigilanteVbs = Join-Path $Repo "tools\vigilante_core_oculto.vbs"
$CoreVbs      = Join-Path $Repo "tools\lanzar_core_oculto.vbs"
$VigilantePs1 = Join-Path $Repo "tools\vigilante_core.ps1"
$VigilanteCfg = Join-Path $Repo "tools\vigilante_core.config.json"
$LanzadorCmd  = Join-Path $Repo "tools\lanzar_core.cmd"
$Manifiesto   = Join-Path $Repo "tools\vigilante_core.hashes.json"
$Piezas = @($VigilantePs1, $VigilanteCfg, $VigilanteVbs, $LanzadorCmd, $CoreVbs)

function Morir([string]$texto, [int]$codigo = 2) {
    Write-Host "ERROR: $texto" -ForegroundColor Red
    exit $codigo
}

function HashDe([string]$ruta) {
    if (-not (Test-Path $ruta)) { return "FALTA" }
    return (Get-FileHash -Path $ruta -Algorithm SHA256).Hash.ToLower()
}

function Relativa([string]$ruta) { return $ruta.Replace($Repo + "\", "") }

function VerificarPiezas([switch]$Callar) {
    # Devuelve $true si todo cuadra con el manifiesto.
    if (-not (Test-Path $Manifiesto)) {
        if (-not $Callar) {
            Write-Host "No hay manifiesto ($Manifiesto): no puedo verificar las piezas." -ForegroundColor Yellow
            Write-Host "Se sella con:  powershell -File tools\instalar_tareas_core.ps1 -SellarHashes" -ForegroundColor Yellow
        }
        return $false
    }
    $m = Get-Content -Path $Manifiesto -Raw | ConvertFrom-Json
    $todoOk = $true
    foreach ($f in $Piezas) {
        $clave = Relativa $f
        $esperado = ""
        if ($m.ficheros.PSObject.Properties.Name -contains $clave) { $esperado = "$($m.ficheros.$clave)" }
        $ahora = HashDe $f
        if (-not $esperado) { Write-Host "  [SIN SELLO] $clave" -ForegroundColor Yellow; $todoOk = $false; continue }
        if ($ahora -eq $esperado) { Write-Host "  [OK]      $clave" -ForegroundColor Green }
        else {
            Write-Host "  [CAMBIADO] $clave" -ForegroundColor Red
            Write-Host "            sellado: $esperado" -ForegroundColor DarkGray
            Write-Host "            ahora  : $ahora" -ForegroundColor DarkGray
            $todoOk = $false
        }
    }
    return $todoOk
}

function SellarPiezas() {
    $ficheros = [ordered]@{}
    foreach ($f in $Piezas) { $ficheros[(Relativa $f)] = HashDe $f }
    $datos = [ordered]@{
        sellado  = (Get-Date).ToString("yyyy-MM-ddTHH:mm:sszzz")
        nota     = "Hashes de las piezas del vigilante. Comprobar: -VerificarHashes"
        ficheros = $ficheros
    }
    ($datos | ConvertTo-Json -Depth 4) | Set-Content -Path $Manifiesto -Encoding UTF8
    Write-Host "Manifiesto escrito en $Manifiesto"
    foreach ($k in $ficheros.Keys) { Write-Host ("  " + $ficheros[$k] + "  " + $k) }
}

function EstadoTarea([string]$nombre) {
    try {
        $t = Get-ScheduledTask -TaskName $nombre -ErrorAction Stop
        $i = $t | Get-ScheduledTaskInfo
        $accion = ($t.Actions | ForEach-Object { "$($_.Execute) $($_.Arguments)" }) -join " | "
        $disp = if ($t.Triggers.Count -eq 0) { "NINGUNO (solo a peticion)" }
                else { ($t.Triggers | ForEach-Object { $_.CimClass.CimClassName }) -join ", " }
        return @"
  $nombre
    Estado            : $($t.State)
    Ultima vez        : $($i.LastRunTime)  (resultado $($i.LastTaskResult))
    Accion            : $accion
    Disparadores      : $disp
"@
    } catch {
        return "  $nombre : NO EXISTE ($($_.Exception.Message))"
    }
}

function UltimaCopia() {
    $carpetas = @(Get-ChildItem -Path (Join-Path $Repo "logs") -Directory -Filter "tareas_backup_*" -ErrorAction SilentlyContinue |
                  Sort-Object Name -Descending)
    if ($carpetas.Count -eq 0) { return "" }
    return $carpetas[0].FullName
}

function VolverA([string]$carpeta) {
    if (-not $carpeta) { Morir "no hay ninguna copia de tareas en logs\tareas_backup_*" }
    if (-not (Test-Path $carpeta)) { Morir "no existe la carpeta $carpeta" }
    Write-Host "Volviendo a las tareas de: $carpeta"
    foreach ($nombre in @("Watch_Core", "KUKIA_Core")) {
        $xml = Join-Path $carpeta "$($nombre)_ANTES.xml"
        if (-not (Test-Path $xml)) { Morir "falta $xml (no se toca nada mas)" }
        Write-Host "  $nombre <- $xml"
        if (-not $Simular) {
            Register-ScheduledTask -Xml (Get-Content $xml -Raw) -TaskName $nombre -Force | Out-Null
        }
    }
    Write-Host "`nEstado tras volver:"
    Write-Host (EstadoTarea "Watch_Core")
    Write-Host (EstadoTarea "KUKIA_Core")
}

# ------------------------------------------------------------ MODO: HASHES
if ($VerificarHashes) {
    Write-Host "Piezas del vigilante frente al manifiesto:"
    if (VerificarPiezas) { Write-Host "`nTodo cuadra."; exit 0 }
    Write-Host "`nOJO: hay piezas cambiadas o sin sellar. Revisa el diff antes de instalar." -ForegroundColor Yellow
    exit 1
}
if ($SellarHashes) { SellarPiezas; exit 0 }

# ------------------------------------------------------- MODO: DESINSTALAR
if ($Desinstalar -or $Revertir) {
    $carpeta = $Revertir
    if (-not $carpeta) { $carpeta = UltimaCopia }
    VolverA $carpeta
    Write-Host @"

Los ficheros del repo (tools\vigilante_core*.ps1,*.vbs, lanzar_core.*) se quedan
donde estan: no estorban. Si quieres volver al vigilante anterior, la copia esta
en logs\vigilante_backup_*\. No se ha borrado la base ni ninguna tarea.
"@
    exit 0
}

# ------------------------------------------------------------ COMPROBACIONES
Write-Host "== 1) las piezas estan donde dice este instalador =="
foreach ($f in $Piezas) {
    if (-not (Test-Path $f)) { Morir "falta $f (el instalador no toca nada)" }
    Write-Host "  [OK] $f"
}

Write-Host "`n== 2) hashes de las piezas =="
if (-not (VerificarPiezas)) {
    if (-not $Forzar) {
        Morir "los hashes no cuadran con el manifiesto. Si el cambio es tuyo y lo has revisado, vuelve a sellar (-SellarHashes) o usa -Forzar. NO se ha tocado nada." 3
    }
    Write-Host "  -Forzar: sigo de todas formas (queda escrito)." -ForegroundColor Yellow
}

Write-Host "`n== 3) el vigilante arranca y dice que haria (modo -Simular) =="
& powershell -NoProfile -ExecutionPolicy Bypass -File $VigilantePs1 -Puerto 8300 -Simular | ForEach-Object { "  $_" }

Write-Host "`n== 4) estado REAL de las tareas antes de tocar =="
Write-Host (EstadoTarea "Watch_Core")
Write-Host (EstadoTarea "KUKIA_Core")

$sello  = Get-Date -Format "yyyyMMdd_HHmmss"
$backup = Join-Path $Repo "logs\tareas_backup_$sello"
New-Item -ItemType Directory -Path $backup -Force | Out-Null

Write-Host "`n== 5) copia de las tareas ACTUALES en $backup =="
$xmlAntes = @{}
foreach ($nombre in @("Watch_Core", "KUKIA_Core")) {
    try {
        $xmlAntes[$nombre] = Export-ScheduledTask -TaskName $nombre -ErrorAction Stop
    } catch {
        Morir "no puedo exportar la tarea $nombre ($($_.Exception.Message)). Sin poder copiarla, no se toca nada."
    }
    Set-Content -Path (Join-Path $backup "$($nombre)_ANTES.xml") -Value $xmlAntes[$nombre] -Encoding UTF8
    Write-Host "  $nombre -> $nombre`_ANTES.xml"
}

function Sustituir([string]$texto, [string]$patron, [string]$nuevo, [string]$que) {
    $coincidencias = ([regex]$patron).Matches($texto).Count
    if ($coincidencias -ne 1) {
        Morir "esperaba 1 '$que' y he encontrado ${coincidencias}: la tarea no tiene la forma esperada, no se toca"
    }
    return [regex]::Replace($texto, $patron, { param($m) $nuevo }, 1)
}

# ------------------------------------------------------- XML nuevo: Watch_Core
$xmlWatch = $xmlAntes["Watch_Core"]
$xmlWatch = Sustituir $xmlWatch '<Arguments>.*?</Arguments>' `
    "<Arguments>""$VigilanteVbs""</Arguments>" "argumento de Watch_Core"

# ------------------------------------------------------ XML nuevo: KUKIA_Core
$xmlCore = $xmlAntes["KUKIA_Core"]
# OJO: <Enabled>false</Enabled> SOLO aparece cuando la tarea esta deshabilitada.
# Desde que se habilito (27-10-2026) su XML no trae el elemento (ausente =
# habilitada). Antes esto exigia 1 coincidencia y el instalador moria con
# "esperaba 1 'Enabled de KUKIA_Core' y he encontrado 0": se descubrio al
# ejecutarlo hoy. Ahora es opcional y se asegura igualmente con
# Enable-ScheduledTask al final.
if ($xmlCore -match '<Enabled>false</Enabled>') {
    $xmlCore = Sustituir $xmlCore '<Enabled>false</Enabled>' '<Enabled>true</Enabled>' "Enabled de KUKIA_Core"
} else {
    Write-Host "  (KUKIA_Core no trae <Enabled>false</Enabled>: ya estaba habilitada)"
}
$xmlCore = Sustituir $xmlCore '<Arguments>.*?</Arguments>' `
    "<Arguments>""$CoreVbs""</Arguments>" "argumento de KUKIA_Core"
$xmlCore = Sustituir $xmlCore '<ExecutionTimeLimit>.*?</ExecutionTimeLimit>' `
    '<ExecutionTimeLimit>PT0S</ExecutionTimeLimit>' "ExecutionTimeLimit de KUKIA_Core"
# El usuario del disparador se saca de la PROPIA tarea (no se escribe a mano el
# SID): asi sigue siendo correcto aunque cambie el nombre o el SID.
$usuario = ([regex]'<UserId>(.*?)</UserId>').Match($xmlCore).Groups[1].Value
if (-not $usuario) {
    Morir "KUKIA_Core no declara usuario: no puedo poner el disparador de inicio de sesion (no se toca nada)"
}
$nuevoTrigger = @"
`r`n    <Triggers>
      <LogonTrigger>
        <Enabled>true</Enabled>
        <UserId>$usuario</UserId>
        <Delay>PT30S</Delay>
      </LogonTrigger>
    </Triggers>
"@
# El bloque puede venir de dos formas y hay que aceptar las DOS: sin
# disparadores, el Programador exporta `<Triggers />` (autocerrado), y con
# disparadores, `<Triggers>...</Triggers>`. El patron de antes solo entendia la
# segunda, asi que con la tarea sin disparadores moria con "esperaba 1
# 'disparadores de KUKIA_Core' y he encontrado 0" (visto al ejecutarlo hoy).
$xmlCore = Sustituir $xmlCore '(?s)\s*<Triggers\s*(/>|>.*?</Triggers>)' $nuevoTrigger "disparadores de KUKIA_Core"

Write-Host "`n== 6) cambios que se aplicarian =="
foreach ($par in @(@("Watch_Core", $xmlWatch), @("KUKIA_Core", $xmlCore))) {
    $nombre = $par[0]; $nuevo = $par[1]
    $viejo = $xmlAntes[$nombre]
    foreach ($etiqueta in @("<Arguments>", "<Enabled>", "<ExecutionTimeLimit>", "<Triggers")) {
        $a = ([regex]"(?s)$([regex]::Escape($etiqueta)).*?(?=</\w+>|/>|\r?\n)").Match($viejo).Value
        $b = ([regex]"(?s)$([regex]::Escape($etiqueta)).*?(?=</\w+>|/>|\r?\n)").Match($nuevo).Value
        if ($a -ne $b) { Write-Host "  [$nombre] $etiqueta`n    antes : $($a -replace '\s+$','')`n    ahora : $($b -replace '\s+$','')" }
    }
}

if ($Simular) {
    Write-Host "`n-Simular: no se registra nada. Para aplicar, ejecuta sin -Simular (con permisos de administrador)."
    exit 0
}

# ------------------------------------------------------------------ APLICAR
Write-Host "`n== 7) registrando las tareas =="
Register-ScheduledTask -Xml $xmlWatch -TaskName "Watch_Core" -Force | Out-Null
Register-ScheduledTask -Xml $xmlCore  -TaskName "KUKIA_Core" -Force | Out-Null
Enable-ScheduledTask -TaskName "KUKIA_Core" | Out-Null

# --------------------------------------------------------- VALIDAR RESULTADO
Write-Host "`n== 8) validando lo que ha quedado (si falla, se repone lo de antes) =="
$fallos = @()
$tWatch = Get-ScheduledTask -TaskName "Watch_Core"
$tCore  = Get-ScheduledTask -TaskName "KUKIA_Core"
$accionWatch = ($tWatch.Actions | ForEach-Object { "$($_.Execute) $($_.Arguments)" }) -join " "
$accionCore  = ($tCore.Actions  | ForEach-Object { "$($_.Execute) $($_.Arguments)" }) -join " "
if ($accionWatch -notlike "*vigilante_core_oculto.vbs*") { $fallos += "Watch_Core no apunta al vigilante del repo" }
if ($accionCore  -notlike "*lanzar_core_oculto.vbs*")    { $fallos += "KUKIA_Core no apunta al lanzador del repo" }
if ($tCore.Settings.Enabled -ne $true)                   { $fallos += "KUKIA_Core no quedo habilitada" }
if ($tCore.Triggers.Count -lt 1)                         { $fallos += "KUKIA_Core se quedo sin disparador (no arrancaria solo)" }
if (-not ($tCore.Triggers | Where-Object { $_.CimClass.CimClassName -eq "MSFT_TaskLogonTrigger" })) {
    $fallos += "KUKIA_Core no tiene el disparador de inicio de sesion"
}
if ($tWatch.Triggers.Count -lt 1)                        { $fallos += "Watch_Core se quedo sin disparador" }
if ($tCore.Settings.ExecutionTimeLimit -ne "PT0S")       { $fallos += "KUKIA_Core tiene limite de ejecucion ($($tCore.Settings.ExecutionTimeLimit))" }

if ($fallos.Count -gt 0) {
    Write-Host "  FALLOS:" -ForegroundColor Red
    $fallos | ForEach-Object { Write-Host "    - $_" -ForegroundColor Red }
    Write-Host "  Reponiendo las tareas que habia ($backup)..." -ForegroundColor Yellow
    VolverA $backup
    Morir "la validacion no paso: se han repuesto las tareas anteriores. Revisa el estado de arriba." 4
}
Write-Host "  [OK] Watch_Core -> vigilante del repo, con su disparador de 5 min" -ForegroundColor Green
Write-Host "  [OK] KUKIA_Core -> lanzador del repo, habilitada, al iniciar sesion (+30 s) y sin limite" -ForegroundColor Green

Write-Host "`nEstado despues:"
Write-Host (EstadoTarea "Watch_Core")
Write-Host (EstadoTarea "KUKIA_Core")

Write-Host @"

COMO DESHACER TODO (una linea):
  powershell -NoProfile -ExecutionPolicy Bypass -File "$Repo\tools\instalar_tareas_core.ps1" -Desinstalar

COMO COMPROBAR LAS PIEZAS CUANDO QUIERAS:
  powershell -NoProfile -ExecutionPolicy Bypass -File "$Repo\tools\instalar_tareas_core.ps1" -VerificarHashes

RECORDATORIO: este instalador NO arranca el Core. KUKIA_Core queda habilitada,
con disparador al iniciar sesion (30 s de retardo), y el vigilante la pide
cuando el puerto 8300 esta libre o cuando ha tenido que reiniciar un Core
colgado. O sea: desde ahora el Core se levanta solo al entrar en Windows y, si
se cae, el vigilante lo relanza como mucho en 5 minutos.

PARA DEJARLO APAGADO A PROPOSITO: deshabilita la tarea (el vigilante no pide una
tarea deshabilitada, y lo dice en el registro con una alerta):
  Disable-ScheduledTask -TaskName "KUKIA_Core"
Y para permitir que vuelva a arrancar:  Enable-ScheduledTask -TaskName "KUKIA_Core"

ARRANCARLO YA, sin esperar al siguiente inicio de sesion:
  Start-ScheduledTask -TaskName "KUKIA_Core"
"@
exit 0
