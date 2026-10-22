@echo off
chcp 65001 >nul 2>&1
setlocal
title KUKIA - panel de operaciones (puerto 8301)
color 0B
cd /d "%~dp0"

echo ============================================================
echo  KUKIA - panel de operaciones
echo.
echo  Abre en el navegador:  http://127.0.0.1:8301
echo.
echo  Lee del Core (http://127.0.0.1:8300). NO escribe nada ni
echo  toca la base de datos: solo muestra lo que el Core publica.
echo  Si el Core esta apagado, el panel lo dice en rojo.
echo.
echo  Cerrar esta ventana para el panel (el Core y el minero
echo  siguen funcionando).
echo ============================================================
echo.

where python >nul 2>&1
if errorlevel 1 (
  echo [ERROR] Python no esta en el PATH. Instala Python 3.10+ y marca
  echo         "Add Python to PATH".
  pause
  exit /b 1
)

python -c "import fastapi, uvicorn, requests" >nul 2>&1
if errorlevel 1 (
  echo Instalando dependencias que faltan...
  python -m pip install --quiet fastapi uvicorn requests
)

if not defined KUKIA_CORE set "KUKIA_CORE=http://127.0.0.1:8300"
cd dashboard
python -m uvicorn kukia_dash:app --host 127.0.0.1 --port 8301
echo.
echo [panel cerrado] El Core y el minero no se han tocado.
pause
