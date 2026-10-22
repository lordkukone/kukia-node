# Cierra a la LAN las reglas de cortafuegos que dejan entrar a CUALQUIER puerto
# en programas que no son de Windows: mineros, nodos RPC, IPFS, opencode,
# Pinokio, python y node instalados aparte.
#
# Desactiva las reglas, NO las borra: revertir es volver a activarlas.
# 127.0.0.1 no se toca: en Windows el cortafuegos no filtra loopback, asi que
# Core (8300), panel (8301), minero, ComfyUI y compania siguen igual.
#
#   powershell -ExecutionPolicy Bypass -File tools\cerrar_puertos_abiertos.ps1 -EnSeco
#   powershell -ExecutionPolicy Bypass -File tools\cerrar_puertos_abiertos.ps1
#   powershell -ExecutionPolicy Bypass -File tools\cerrar_puertos_abiertos.ps1 -Revertir
#
# Necesita permisos de administrador para tocar reglas del cortafuegos.
param(
    [switch]$EnSeco,
    [switch]$Revertir
)
$ErrorActionPreference = 'Stop'

# Binario -> por que se cierra. Si algo de aqui se rompe, se vuelve a activar.
$objetivos = @(
    '\tmp_abrs\', '\tmp_btq\', '\tmp_keryx\',   # nodos/mineros en C:\tmp_*
    'miner',                                     # cualquier minero
    'ipfs.exe', 'keryxd.exe', 'ravend.exe', 'btqd.exe',
    '@opencode-aidesktop',                       # opencode
    '\pinokio\pinokio.exe',                      # interfaz de Pinokio
    'python.exe',                                # python sueltos (no Pinokio: ya cerrados)
    'node.exe',                                  # node sueltos (sistema y el de pinokio api)
    'cloudflared.exe'                            # el tunel es saliente: la entrada sobra
)
# Lo que NO se toca aunque tenga regla abierta: juegos, uTorrent (necesita
# entrada para sembrar), antivirus/juegos online y todo lo de Windows.
$intocables = @('\program files (x86)\steam', '\steamlibrary', '\epic games',
                '\ea games', '\makensis', 'utorrent', 'punkbuster', 'pnkbstr',
                'windowsapps', '\windows\')

$marca = 'KUKIA cerrado por cerrar_puertos_abiertos.ps1'

function Reglas-Afectadas {
    $salida = @()
    $reglas = Get-NetFirewallRule -Direction Inbound -Enabled True -Action Allow -ErrorAction SilentlyContinue
    foreach ($r in $reglas) {
        $prog = ($r | Get-NetFirewallApplicationFilter -ErrorAction SilentlyContinue).Program
        if (-not $prog -or $prog -eq 'Any' -or $prog -eq 'System') { continue }
        $p = $prog.ToLower()
        if ($intocables | Where-Object { $p.Contains($_) }) { continue }
        $toca = $false
        foreach ($o in $objetivos) { if ($p.Contains($o)) { $toca = $true; break } }
        if (-not $toca) { continue }
        $pf = $r | Get-NetFirewallPortFilter -ErrorAction SilentlyContinue
        if ($pf.Protocol -ne 'TCP') { continue }
        if ($pf.RemotePort -ne 'Any') { continue }   # un puerto concreto ya lo decide su regla
        $salida += [pscustomobject]@{ Nombre = $r.DisplayName; Programa = $prog; Regla = $r }
    }
    return $salida
}

$afectadas = Reglas-Afectadas
if ($afectadas.Count -eq 0) {
    "No hay nada que cerrar: no queda ninguna regla abierta en esos programas."
    exit 0
}

"Reglas de entrada abiertas a CUALQUIER puerto en programas de riesgo: $($afectadas.Count)"
$afectadas | Select-Object Nombre, Programa | Format-Table -AutoSize -Wrap

if ($EnSeco) {
    "`n(EN SECO: no se ha cambiado nada. Quita -EnSeco para aplicarlo.)"
    exit 0
}

$hechas = 0
foreach ($a in $afectadas) {
    try {
        if ($Revertir) {
            Enable-NetFirewallRule -Name $a.Regla.Name
        } else {
            Disable-NetFirewallRule -Name $a.Regla.Name
        }
        $hechas++
    } catch {
        "  FALLO con '$($a.Nombre)': $($_.Exception.Message)"
    }
}

if ($Revertir) {
    "Reactivadas $hechas reglas."
} else {
    "Desactivadas $hechas reglas (no borradas: se pueden reactivar con -Revertir)."
}

# Comprobacion: el Core y el panel tienen que seguir contestando en loopback.
function Contesta($url) {
    try {
        $r = Invoke-WebRequest -UseBasicParsing -Uri $url -TimeoutSec 8
        return $r.StatusCode
    } catch { return 0 }
}
$core = Contesta 'http://127.0.0.1:8300/stats'
$panel = Contesta 'http://127.0.0.1:8301/'
"Core 8300 -> $core    Panel 8301 -> $panel"
if ($core -ne 200) { "AVISO: el Core no contesta en loopback. Revisar antes de seguir." }
