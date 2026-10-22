@echo off
chcp 65001 >nul 2>&1
setlocal
title KUKIA - validador independiente
color 0A
cd /d "%~dp0"

set "NOMBRE=%~1"
if not defined NOMBRE set "NOMBRE=VALIDADOR_%COMPUTERNAME%"

rem Cada validador necesita SU PROPIA clave. Si se pasa un nombre y no una
rem identidad, se usa un fichero distinto por nombre (no se reutiliza la clave
rem del primero, que seria lo mismo que tener un solo validador).
set "IDENT=%~2"
if not defined IDENT (
  if "%~1"=="" (set "IDENT=validator\identidad_validador.json") else (set "IDENT=validator\identidad_%NOMBRE%.json")
)

rem La linea de core_minero.txt puede ser una URL normal o "auto <espejo>":
rem con "auto" se pregunta al espejo cual es la URL viva del tunel ahora.
set "CORE="
for /f "usebackq delims=" %%L in (`python tools\core_destino.py --url`) do set "CORE=%%L"
if not defined CORE set "CORE=http://127.0.0.1:8300"

echo ============================================================
echo  KUKIA - validador independiente
echo.
echo  Nombre: %NOMBRE%
echo  Core:   %CORE%
echo.
echo  Recalcula las tareas verificadas y firma su conformidad.
echo  NO paga, NO mueve saldos y NO escribe en el ledger.
echo  No abre puertos: solo sale hacia el Core.
echo.
echo  Para varios validadores en el mismo PC, usa claves distintas:
echo    ARRANCAR_VALIDADOR.cmd VALIDADOR_2
echo  (crea solo validator\identidad_VALIDADOR_2.json)
echo    ARRANCAR_VALIDADOR.cmd VALIDADOR_3 ruta\a\otra_identidad.json
echo ============================================================
echo.

where python >nul 2>&1
if errorlevel 1 (
  echo [ERROR] Python no esta en el PATH. Instala Python 3.10+ y marca
  echo         "Add Python to PATH".
  pause
  exit /b 1
)

python -c "import numpy, requests, cryptography" >nul 2>&1
if errorlevel 1 (
  echo Instalando dependencias que faltan...
  python -m pip install --quiet numpy requests cryptography
)

python -u validator\kukia_validator.py --core "%CORE%" --nombre "%NOMBRE%" --identidad "%IDENT%"
echo.
echo [validador parado] El Core y las billeteras no se han tocado.
pause
