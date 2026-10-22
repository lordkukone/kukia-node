@echo off
rem UTF-8: los mensajes llevan tildes y el log de Windows es cp850.
chcp 65001 >nul 2>&1
setlocal enabledelayedexpansion
title KUKIA - instalar y minar (un clic)
cd /d "%~dp0"

rem /auto: sin pausas (para probarlo desde un script).
set "PAUSA=1"
if /i "%~1"=="/auto" set "PAUSA="

echo ============================================================
echo  KUKIA - un clic: comprobar, medir la GPU y minar
echo.
echo  1. Comprueba Python y los paquetes (instala lo que falte)
echo  2. Mide tu GPU con las mismas cuentas que la red
echo  3. Arranca la GUARDIA: Core, minero, validador y panel,
echo     y los relanza solos si se caen
echo ============================================================
echo.

python --version >nul 2>&1
if errorlevel 1 (
  echo [ERROR] No hay Python en el PATH.
  echo         Instala Python 3.10 o superior desde python.org
  echo         y marca la casilla "Add Python to PATH".
  if defined PAUSA pause
  exit /b 1
)

python tools\instalar_minero.py --instalar
if errorlevel 1 (
  echo.
  echo [ATENCION] Falta algo de lo de arriba. Se puede intentar minar igual,
  echo            pero si es PyTorch no va a funcionar.
  if defined PAUSA pause
)

echo.
echo [2] Midiendo la GPU...
python tools\benchmark_gpu.py --json
if errorlevel 1 (
  echo [AVISO] No se pudo medir la GPU. Se mina igual, pero sin dato de potencia.
)

echo.
echo [3] Arrancando la guardia en segundo plano...
if exist "ARRANCAR_GUARDIA_SILENCIOSO.vbs" (
  wscript "ARRANCAR_GUARDIA_SILENCIOSO.vbs"
  echo      hecho. Log en logs\guardia.log
  echo.
  echo  Para verlo:  ARRANCAR_PANEL.cmd   ^(panel en http://127.0.0.1:8301^)
  echo  Para pararlo: cierra la ventana de la guardia o mata el proceso
  echo                pythonw que ejecuta tools\guardia.py
) else (
  echo [AVISO] no encuentro ARRANCAR_GUARDIA_SILENCIOSO.vbs
  echo         Arranca a mano: ARRANCAR_MINERO.cmd
)
echo ============================================================
if defined PAUSA pause
