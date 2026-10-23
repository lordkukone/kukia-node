@echo off
chcp 65001 >nul 2>&1
setlocal
title KUKIA - historico de incidencias
cd /d "%~dp0"

rem Sin argumentos: ultimos 7 dias. Se le puede pasar lo mismo que al script,
rem por ejemplo:  VER_INCIDENCIAS.cmd --dias 30 --detalle
rem                 VER_INCIDENCIAS.cmd --servicio surge
python -u tools\incidencias.py %*
set "CODIGO=%ERRORLEVEL%"
echo.
if not "%CODIGO%"=="0" echo (el script termino con codigo %CODIGO%)
pause
