@echo off
rem Minero EN VIVO en una ventana normal de cmd: se ve cada tarea, el resumen
rem por minuto (tareas/min, KIA/min, KIA/h, KIA/dia y KIA/h por GFLOPS) y el
rem calendario de emision al arrancar.
rem Todo lo que sale por pantalla queda tambien en logs\minero_visible.log.
rem
rem SI YA HAY UN MINERO CORRIENDO, esta ventana NO lanza otro: dos mineros se
rem reparten el cupo de 4 tareas y rinden la mitad. En ese caso abre
rem tools\mina_en_vivo.py, que pinta cada 5 s el ritmo (tareas/min, KIA/min),
rem el estado de cada nodo y el ultimo pago del que ya esta minando.
rem
rem Uso:  VER_MINERO.cmd            (minar; o mirar, si ya hay uno vivo)
rem       VER_MINERO.cmd minero_pc  (reutiliza la identidad de siempre)
chcp 65001 >nul 2>&1
setlocal enabledelayedexpansion
title KUKIA - minero (en vivo)
cd /d "%~dp0"

echo ============================================================
echo  KUKIA - minero (Proof-of-Compute) en vivo
echo  Calcula tareas en la GPU y cobra KIA en tu billetera.
echo  Si esta ventana mina, cerrarla PARA el minero.
echo ============================================================
echo.

set "CORE="
for /f "usebackq delims=" %%L in (`python tools\core_destino.py --url`) do set "CORE=%%L"
if not defined CORE set "CORE=http://127.0.0.1:8300"
set "KUKIA_CORE=%CORE%"
echo Core: %KUKIA_CORE%

rem El Core a veces tarda unos segundos en volver (o en contestar tras un
rem reinicio). Antes se rendia al primer intento y la ventana se quedaba en
rem "El Core no responde" para siempre: parecia que el minero no iba.
set "OKCORE="
for /l %%i in (1,1,6) do (
  if not defined OKCORE (
    python -c "import urllib.request; urllib.request.urlopen('%KUKIA_CORE%/stats', timeout=5).read()" >nul 2>&1 && set "OKCORE=1"
    if not defined OKCORE (
      echo [..] el Core no responde todavia ^(intento %%i de 6^), espero 5 s...
      timeout /t 5 /nobreak >nul
    )
  )
)
if not defined OKCORE (
  echo [ERROR] El Core no responde en %KUKIA_CORE%
  echo         Arrancalo antes de minar ^(o revisa core_minero.txt^).
  echo         Si ya estaba abierto: cierra ESTA ventana y vuelve a abrirla.
  echo.
  pause
  exit /b 1
)
echo [OK] Core accesible.

rem --- ¿Ya hay un minero vivo? Entonces esta ventana MIRA, no mina ----------
rem La guardia arranca el minero oculto al iniciar sesion, asi que lo normal es
rem que ya haya uno. Abrir este .cmd lanzaba un segundo que el cerrojo
rem (worker\minero.lock) rechazaba, y parecia que el minero estaba roto.
set "PIDMIN="
if exist "worker\minero.lock" set /p PIDMIN=<"worker\minero.lock"
set "VIVO="
rem Rutas completas a los .exe de Windows: si el PATH tiene el find de Git
rem (MSYS), "find" busca ficheros en el disco en vez de filtrar la salida.
if defined PIDMIN (
  tasklist /FI "PID eq !PIDMIN!" /NH 2>nul | "%SystemRoot%\System32\findstr.exe" /C:"!PIDMIN!" >nul 2>&1 && set "VIVO=1"
)
if defined VIVO (
  echo.
  echo [i] Ya hay un minero vivo ^(PID !PIDMIN!^). Esta ventana NO va a minar:
  echo     dos mineros se reparten el cupo y rinden la mitad.
  echo     Te enseno como va el que ya esta corriendo. Ctrl+C para cerrar.
  echo.
  rem El aviso de urllib3/chardet al importar requests no aporta nada aqui.
  set "PYTHONWARNINGS=ignore"
  python -u tools\mina_en_vivo.py
  echo.
  echo [visor cerrado; el minero sigue]
  pause
  exit /b 0
)

echo [i] No hay otro minero vivo: esta ventana mina de verdad.


set "NOMBRE=%~1"
if not defined NOMBRE set "NOMBRE=minero_%COMPUTERNAME%"
echo Nodo: %NOMBRE%
echo Log:  logs\minero_visible.log
echo.

if not exist "logs" mkdir "logs"
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "Set-Location '%~dp0worker'; & python -u kukia_worker.py '%NOMBRE%' 2>&1 | Tee-Object -FilePath '%~dp0logs\minero_visible.log' -Append"
set "CODIGO=%ERRORLEVEL%"
echo.
echo [minero detenido] codigo %CODIGO%
pause
