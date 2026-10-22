#Requires -Version 5.1
<#
  Cierra las reglas de entrada que dejan a ciertos programas escuchar en
  CUALQUIER puerto y desde CUALQUIER IP: pinokio, node, opencode, los python
  genericos y los mineros de C:\tmp_*.

  Lo que hace, en este orden:
    1. Exporta TODO el cortafuegos a un .wfw (marcha atras completa).
    2. Apunta cada regla que va a cerrar en un JSON y en un .txt de nombres.
    3. Las DESHABILITA (no las borra: se pueden volver a encender).
    4. Relee y comprueba que ya no quedan activas.

  No toca nada mas: ni reglas de otros programas, ni puertos concretos que ya
  estuvieran cerrados, ni el cortafuegos en general. Loopback no se ve afectado:
  ComfyUI, Maestro, el Core y el TTS siguen en 127.0.0.1 igual que antes.

  USO
    powershell -ExecutionPolicy Bypass -File cortafuegos\cerrar_puertos_abiertos.ps1 -SoloListar
        (mirar que cerraria, sin tocar nada, sin ser administrador)
    powershell -ExecutionPolicy Bypass -File cortafuegos\cerrar_puertos_abiertos.ps1
        (cerrarlas de verdad: pide administrador)
#>
[CmdletBinding()]
param(
    [switch]$SoloListar
)

$ErrorActionPreference = 'Stop'

$Raiz = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Carpeta = Join-Path $Raiz 'cortafuegos'
$Fecha = Get-Date -Format 'yyyyMMdd_HHmm'
$Respaldo = Join-Path $Carpeta "respaldo_firewall_$Fecha.wfw"
$RegistroJson = Join-Path $Carpeta "reglas_cerradas_$Fecha.json"
$RegistroTxt = Join-Path $Carpeta "reglas_cerradas_$Fecha.txt"

# Que se cierra. Match = patron del programa; Excluir = lo que NO se toca.
$Patrones = @(
    @{ Etiqueta = 'pinokio';         Match = '*\pinokio.exe';  Excluir = '' },
    @{ Etiqueta = 'node';            Match = '*\node.exe';     Excluir = '' },
    @{ Etiqueta = 'opencode';        Match = '*\opencode.exe'; Excluir = '' },
    # Los python de las apps (uv cpython, Python312). Los de Pinokio
    # (miniforge/miniconda) ya se cerraron el 21/10 y no se tocan aqui.
    @{ Etiqueta = 'python generico'; Match = '*\python.exe';   Excluir = '\pinokio\bin\' },
    # Mineros de C:\tmp_*: solo los nombrados, no cualquier .exe de ahi.
    @{ Etiqueta = 'minero tmp';      Match = 'C:\tmp_*\*';
       Mineros = '(?i)\\(btqd|keryxd|ravend|bzminer|quantus-miner)[^\\]*\.exe$' }
)

function Get-ReglasAmplias {
    Get-NetFirewallRule -Direction Inbound -ErrorAction SilentlyContinue | ForEach-Object {
        $r = $_
        $app = $null
        try { $app = $r | Get-NetFirewallApplicationFilter -ErrorAction SilentlyContinue } catch { }
        if (-not $app -or -not $app.Program -or $app.Program -eq 'Any') { return }
        $prog = [string]$app.Program
        foreach ($p in $Patrones) {
            if ($prog -notlike $p.Match) { continue }
            if ($p.Excluir -and $prog -like ('*' + $p.Excluir + '*')) { break }
            if ($p.Mineros -and $prog -notmatch $p.Mineros) { break }
            $port = $null; $addr = $null
            try { $port = $r | Get-NetFirewallPortFilter -ErrorAction SilentlyContinue } catch { }
            try { $addr = $r | Get-NetFirewallAddressFilter -ErrorAction SilentlyContinue } catch { }
            [pscustomobject]@{
                Etiqueta   = $p.Etiqueta
                Nombre     = [string]$r.Name
                Visible    = [string]$r.DisplayName
                Programa   = $prog
                Habilitada = "$($r.Enabled)"
                Accion     = "$($r.Action)"
                Perfil     = "$($r.Profile)"
                Protocolo  = if ($port) { "$($port.Protocol)" } else { '' }
                Puerto     = if ($port) { "$($port.LocalPort)" } else { '' }
                Desde      = if ($addr) { "$($addr.RemoteAddress)" } else { '' }
            }
            break
        }
    }
}

Write-Output "== Reglas de entrada que dejan escuchar a cualquier puerto / cualquier IP =="
$todas = @(Get-ReglasAmplias | Sort-Object Etiqueta, Nombre)
if ($todas.Count -eq 0) {
    Write-Output "No hay ninguna: nada que cerrar."
    exit 0
}
$todas | Format-Table Etiqueta, Habilitada, Accion, Protocolo, Puerto, Desde, Visible -AutoSize | Out-String -Width 240 | Write-Output

$cerrar = @($todas | Where-Object { $_.Habilitada -eq 'True' -and $_.Accion -eq 'Allow' })
Write-Output ("Total encontradas: {0}   |   se cerrarian ahora: {1}" -f $todas.Count, $cerrar.Count)
if ($cerrar.Count -gt 0) {
    $cerrar | Format-Table Etiqueta, Nombre, Programa -AutoSize | Out-String -Width 240 | Write-Output
}

if ($SoloListar) {
    Write-Output "`n(Solo listar: no se ha tocado nada.)"
    exit 0
}

$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) {
    Write-Output "[ERROR] esto necesita administrador. Abre PowerShell como administrador y repite."
    exit 2
}
if ($cerrar.Count -eq 0) {
    Write-Output "No queda nada activo que cerrar: no se toca el cortafuegos."
    exit 0
}

New-Item -ItemType Directory -Force -Path $Carpeta | Out-Null

# 1) Respaldo completo ANTES de tocar (esto es la marcha atras de verdad).
Write-Output "`n[1/4] exportando el cortafuegos entero a $Respaldo"
& netsh advfirewall export "$Respaldo" | Out-Null
if (-not (Test-Path $Respaldo)) {
    Write-Output "[ERROR] no se pudo exportar el cortafuegos. No se toca nada."
    exit 1
}
$tam = (Get-Item $Respaldo).Length
$hash = (Get-FileHash $Respaldo -Algorithm SHA256).Hash
Write-Output ("      {0} bytes, sha256 {1}" -f $tam, $hash)

# 2) Apuntar que se cierra, antes de cerrarlo.
Write-Output "[2/4] apuntando las reglas en $RegistroJson"
$cerrar | ConvertTo-Json -Depth 4 | Set-Content -Path $RegistroJson -Encoding UTF8
$cerrar | Select-Object -ExpandProperty Nombre | Set-Content -Path $RegistroTxt -Encoding ASCII

# 3) Deshabilitar (no borrar).
Write-Output "[3/4] deshabilitando $($cerrar.Count) reglas"
$fallos = 0
foreach ($r in $cerrar) {
    try {
        Disable-NetFirewallRule -Name $r.Nombre -ErrorAction Stop
        Write-Output ("      cerrada  {0}  ({1})" -f $r.Visible, $r.Etiqueta)
    } catch {
        $fallos++
        Write-Output ("      [AVISO] no se pudo cerrar {0}: {1}" -f $r.Visible, $_.Exception.Message)
    }
}

# 4) Comprobar leyendo otra vez.
Write-Output "[4/4] comprobando"
$despues = @(Get-ReglasAmplias | Where-Object { $_.Habilitada -eq 'True' -and $_.Accion -eq 'Allow' })
if ($despues.Count -eq 0) {
    Write-Output "      OK: ya no queda ninguna de esas reglas activa."
} else {
    Write-Output "      QUEDAN $($despues.Count):"
    $despues | Format-Table Etiqueta, Nombre, Programa -AutoSize | Out-String -Width 240 | Write-Output
}

Write-Output "`nResumen: $($cerrar.Count) reglas cerradas, $fallos con aviso."
Write-Output "Marcha atras:"
Write-Output "    powershell -ExecutionPolicy Bypass -File cortafuegos\marcha_atras.ps1"
Write-Output "    (o la total: netsh advfirewall import `"$Respaldo`")"
