#Requires -Version 5.1
<#
  Marcha atras de cortafuegos\cerrar_puertos_abiertos.ps1.

  Dos formas, de la mas fina a la mas gorda:

   1) Volver a encender EXACTAMENTE las reglas que se cerraron (lo normal).
      Lee reglas_cerradas_<fecha>.txt y las habilita otra vez.

        powershell -ExecutionPolicy Bypass -File cortafuegos\marcha_atras.ps1

      Sin fecha usa el .txt mas reciente. Se puede elegir uno concreto:

        ... -File cortafuegos\marcha_atras.ps1 -Fecha 20261022_1930

   2) Restaurar TODO el cortafuegos tal y como estaba (si algo se quedo raro).
      Importa el .wfw exportado antes de tocar nada. Pide confirmacion.

        ... -File cortafuegos\marcha_atras.ps1 -RespaldoCompleto 20261022_1930

  Las dos piden administrador. No borra ni crea reglas: solo enciende/apaga
  o restaura el conjunto exportado.
#>
[CmdletBinding()]
param(
    [string]$Fecha = '',
    [string]$RespaldoCompleto = ''
)

$ErrorActionPreference = 'Stop'

$Raiz = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Carpeta = Join-Path $Raiz 'cortafuegos'

$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) {
    Write-Output "[ERROR] esto necesita administrador. Abre PowerShell como administrador y repite."
    exit 2
}

# --- 2) Restaurar el cortafuegos completo desde el .wfw ---------------------
if ($RespaldoCompleto) {
    $wfw = Join-Path $Carpeta "respaldo_firewall_$RespaldoCompleto.wfw"
    if (-not (Test-Path $wfw)) {
        Write-Output "[ERROR] no existe $wfw"
        Write-Output "        Los respaldos disponibles son:"
        Get-ChildItem -Path $Carpeta -Filter 'respaldo_firewall_*.wfw' -ErrorAction SilentlyContinue |
            ForEach-Object { Write-Output ("        " + $_.Name) }
        exit 1
    }
    Write-Output "Esto REEMPLAZA la configuracion actual del cortafuegos por la de:"
    Write-Output "  $wfw"
    Write-Output ("  bytes: {0}  sha256: {1}" -f (Get-Item $wfw).Length, (Get-FileHash $wfw -Algorithm SHA256).Hash)
    $resp = Read-Host "Escribe SI para continuar"
    if ($resp -ne 'SI') { Write-Output "Cancelado: no se ha tocado nada."; exit 0 }
    & netsh advfirewall import "$wfw"
    Write-Output "Restaurado. Comprueba que todo sigue como esperabas antes de cerrar esta ventana."
    exit 0
}

# --- 1) Encender las reglas que se cerraron ---------------------------------
if (-not $Fecha) {
    $ultimo = Get-ChildItem -Path $Carpeta -Filter 'reglas_cerradas_*.txt' -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if (-not $ultimo) {
        Write-Output "[ERROR] no hay ningun reglas_cerradas_*.txt en $Carpeta"
        exit 1
    }
    $Fecha = $ultimo.BaseName -replace '^reglas_cerradas_', ''
}

$lista = Join-Path $Carpeta "reglas_cerradas_$Fecha.txt"
$json = Join-Path $Carpeta "reglas_cerradas_$Fecha.json"
if (-not (Test-Path $lista)) {
    Write-Output "[ERROR] no existe $lista"
    exit 1
}

$nombres = @(Get-Content -Path $lista -Encoding ASCII | Where-Object { $_.Trim() -ne '' })
Write-Output "Volviendo a encender $($nombres.Count) reglas de la tanda $Fecha"
if (Test-Path $json) {
    Write-Output "  (detalle en $json)"
}

$hechas = 0
$fallos = 0
foreach ($n in $nombres) {
    try {
        Enable-NetFirewallRule -Name $n -ErrorAction Stop
        $hechas++
    } catch {
        $fallos++
        Write-Output ("  [AVISO] no pude encender {0}: {1}" -f $n, $_.Exception.Message)
    }
}
Write-Output "Encendidas: $hechas   con aviso: $fallos"

# Comprobacion: que las que se encendieron estan de verdad activas.
$activas = 0
foreach ($n in $nombres) {
    try {
        $r = Get-NetFirewallRule -Name $n -ErrorAction Stop
        if ("$($r.Enabled)" -eq 'True') { $activas++ }
    } catch { }
}
Write-Output "Verificadas activas ahora: $activas de $($nombres.Count)"
Write-Output "`nSi quieres el cortafuegos ENTERO como estaba antes de la tanda:"
Write-Output "  powershell -ExecutionPolicy Bypass -File cortafuegos\marcha_atras.ps1 -RespaldoCompleto $Fecha"
