@echo off
rem UTF-8 en la consola: las 20 palabras de recuperacion llevan tildes y con
rem la pagina de codigos por defecto se ven mal (o rompen el minero).
chcp 65001 >nul 2>&1
setlocal enabledelayedexpansion
title KUKIA - minero
cd /d "%~dp0"
echo ============================================================
echo  KUKIA - minero (Proof-of-Compute)
echo  Pide trabajo al Core, lo calcula en tu GPU y cobra KIA en
echo  TU billetera. Cerrar esta ventana PARA el minero.
echo ============================================================
echo.

rem --- URL del Core: la resuelve tools\core_destino.py ---
rem La linea de core_minero.txt puede ser una URL normal o "auto <espejo>":
rem con "auto" se pregunta al espejo cual es la URL viva del tunel ahora.
set "CORE="
for /f "usebackq delims=" %%L in (`python tools\core_destino.py --url`) do set "CORE=%%L"
if not defined CORE set "CORE=http://127.0.0.1:8300"
set "KUKIA_CORE=%CORE%"
echo Core: %KUKIA_CORE%
echo.

rem --- Python ---
python --version >nul 2>&1
if errorlevel 1 (
  echo [ERROR] No hay Python en el PATH.
  echo         Instala Python 3.10 o superior desde python.org
  echo         y marca la casilla "Add Python to PATH".
  echo.
  pause
  exit /b 1
)

rem --- dependencias ligeras ---
python -c "import requests, numpy, cryptography" >nul 2>&1
if errorlevel 1 (
  echo [..] Instalando dependencias ^(requests, numpy, cryptography^)...
  python -m pip install --quiet requests numpy cryptography
  if errorlevel 1 (
    echo [ERROR] No se pudieron instalar. Prueba a mano:
    echo         python -m pip install requests numpy cryptography
    pause
    exit /b 1
  )
)

rem --- PyTorch (sin el no se puede calcular ninguna tarea) ---
python -c "import torch" >nul 2>&1
if errorlevel 1 (
  echo [AVISO] Falta PyTorch, es imprescindible para calcular las tareas.
  echo         Con GPU NVIDIA conviene la version CUDA ^(mucho mas rapida^):
  echo           python -m pip install torch --index-url https://download.pytorch.org/whl/cu121
  echo         Sin GPU NVIDIA:
  echo           python -m pip install torch
  echo.
  set /p R="Instalar PyTorch ahora sin CUDA? (S/N) "
  if /i not "!R!"=="S" (
    echo [minero no arrancado]
    pause
    exit /b 1
  )
  python -m pip install torch
  if errorlevel 1 (
    echo [ERROR] La instalacion de PyTorch fallo. Instalalo a mano con el comando de arriba.
    pause
    exit /b 1
  )
)

rem --- el Core tiene que responder antes de minar ---
python -c "import urllib.request; urllib.request.urlopen('%KUKIA_CORE%/stats', timeout=8).read()" >nul 2>&1
if errorlevel 1 (
  echo [ERROR] El Core no responde en %KUKIA_CORE%
  echo         Revisa core_minero.txt ^(URL correcta^) y que el Core este encendido.
  echo         Si minas en este mismo PC: abre otra ventana y arranca el Core.
  echo.
  pause
  exit /b 1
)
echo [OK] Core accesible.

rem --- nombre del nodo ---
set "NOMBRE=%~1"
if not defined NOMBRE set "NOMBRE=minero_%COMPUTERNAME%"
echo Nodo: %NOMBRE%
echo.
echo Si es la PRIMERA vez: apunta la contrasena y las 20 palabras que salgan.
echo Sin ellas no se puede recuperar la billetera si se pierde el PC.
echo.

cd worker
python -u kukia_worker.py %NOMBRE%
set "CODIGO=%ERRORLEVEL%"
cd ..
echo.
echo [minero detenido] codigo %CODIGO%
pause
