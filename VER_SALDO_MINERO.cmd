@echo off
chcp 65001 >nul 2>&1
setlocal
title KUKIA - saldo del minero
cd /d "%~dp0"

rem La linea de core_minero.txt puede ser una URL normal o "auto <espejo>":
rem con "auto" se pregunta al espejo cual es la URL viva del tunel ahora.
set "CORE="
for /f "usebackq delims=" %%L in (`python tools\core_destino.py --url`) do set "CORE=%%L"
if not defined CORE set "CORE=http://127.0.0.1:8300"
set "KUKIA_CORE=%CORE%"

python -u tools\saldo_minero.py
echo.
pause
