# Quita al sandbox de Codex el acceso a las claves y al ledger de KUKIA.
#
# Por que: la app de Codex crea una cuenta local (grupo CodexSandboxUsers) con
# permisos de LECTURA heredados sobre la carpeta del usuario. En KUKIA eso
# incluye coordinator\sello_key.txt (la clave que firma los sellos),
# admin_token.txt y las carteras. Un sandbox de agentes no tiene por que poder
# leer eso.
#
# Que hace, en orden:
#   1. Guarda los permisos actuales en backups\acl_antes_<fecha>.txt (icacls /save).
#   2. Corta la herencia y quita el grupo de esas carpetas (no toca el codigo).
#   3. Comprueba que no queda ninguna concesion para el grupo.
#
#   powershell -ExecutionPolicy Bypass -File tools\proteger_claves.ps1
#   powershell -ExecutionPolicy Bypass -File tools\proteger_claves.ps1 -Revertir
param([switch]$Revertir)
$ErrorActionPreference = 'Continue'
$raiz = Split-Path -Parent $PSScriptRoot
$grupo = 'CodexSandboxUsers'
$sello = Join-Path $raiz ("backups\acl_antes_{0}.txt" -f (Get-Date -Format 'yyyyMMdd-HHmm'))

# Carpetas con secretos o con copias de secretos. El codigo se deja como esta.
$rutas = @(
    (Join-Path $raiz 'coordinator'),
    (Join-Path $raiz 'ledger'),
    (Join-Path $raiz 'backups'),
    (Join-Path $raiz 'wallet')
) + @(Get-ChildItem -Path $raiz -Directory -Filter '_backup*' -ErrorAction SilentlyContinue |
        ForEach-Object { $_.FullName })

function Acl-Actual($ruta) {
    icacls $ruta 2>$null | Out-String
}

if ($Revertir) {
    $ultimo = Get-ChildItem (Join-Path $raiz 'backups') -Filter 'acl_antes_*.txt' |
              Sort-Object Name -Descending | Select-Object -First 1
    if (-not $ultimo) { "No hay copia de permisos en backups\acl_antes_*.txt"; exit 2 }
    "Restaurando permisos desde $($ultimo.FullName)"
    icacls $raiz /restore $ultimo.FullName /C
    "Hecho. Comprueba con: icacls coordinator\sello_key.txt"
    exit 0
}

if (-not (Get-LocalGroup -Name $grupo -ErrorAction SilentlyContinue)) {
    "No existe el grupo $grupo en este equipo: no hay nada que cerrar."
    exit 0
}

# 1. Copia de los permisos actuales (reversible con -Revertir).
New-Item -ItemType Directory -Force -Path (Split-Path $sello) | Out-Null
icacls $raiz /save $sello /T /C /Q | Out-Null
"Permisos actuales guardados en: $sello"

# 2. Cortar herencia y quitar el grupo.
#
# icacls necesita el nombre completo (equipo\grupo). Con comodin ("*grupo")
# contesta que si pero deja la concesion puesta: comprobado.
$nombre = "$env:COMPUTERNAME\$grupo"

$tocadas = 0
foreach ($r in $rutas) {
    if (-not (Test-Path $r)) { continue }
    # /inheritance:d convierte lo heredado en explicito: no se pierde nada de
    # SYSTEM, Administradores ni kuki; solo se corta lo que venga de arriba.
    icacls $r /inheritance:d /T /C /Q | Out-Null
    $salida = (icacls $r /remove:g $nombre /T /C /Q 2>&1 | Out-String)
    $tocadas++
    "  cerrado: $r"
    $fallos = ([regex]::Matches($salida, 'error al procesar (\d+)')).Count
    if ($fallos -eq 0 -and $salida -match 'error') { "    (icacls dijo: $($salida.Trim()))" }
}

# 3. Comprobacion: ni una concesion al grupo en esas carpetas.
$quedan = 0
foreach ($r in $rutas) {
    if (-not (Test-Path $r)) { continue }
    $n = (Get-ChildItem -Path $r -Recurse -File -ErrorAction SilentlyContinue |
          Where-Object { (Acl-Actual $_.FullName) -match $grupo }).Count
    if ($n -gt 0) { "  AVISO: quedan $n ficheros con acceso del grupo en $r"; $quedan += $n }
}
""
"Carpetas tratadas: $tocadas"
if ($quedan -eq 0) { "Comprobado: el grupo $grupo ya no aparece en ninguna." }
else { "Quedan $quedan ficheros por limpiar (mira el AVISO de arriba)." }

"--- clave del sello ahora:"
icacls (Join-Path $raiz 'coordinator\sello_key.txt') 2>$null
