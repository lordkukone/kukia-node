@echo off
rem Publica en GitHub lo que haya cambiado, con comprobacion de claves antes.
rem Doble clic y listo. Con /auto no se queda esperando al final.
rem Solo enseña lo que haria:  PUBLICAR_EN_GITHUB.cmd --ver
chcp 65001 >nul 2>&1
setlocal
title KUKIA - publicar en GitHub
cd /d "%~dp0"

set "PAUSA=1"
set "ARGS=%*"
if /i "%~1"=="/auto" (
  set "PAUSA="
  set "ARGS="
)

set "SOLO_VER="
echo %ARGS% | findstr /i "\-\-ver" >nul && set "SOLO_VER=1"

python tools\publicar_github.py %ARGS%
set "CODIGO=%ERRORLEVEL%"

echo.
if "%CODIGO%"=="0" (
  if defined SOLO_VER (
    echo  Era una prueba: no se ha subido nada.
  ) else (
    echo  Publicado. GitHub Pages tarda un par de minutos en rehacer la web.
  )
) else (
  echo  NO se ha publicado nada. El motivo esta arriba.
)
if defined PAUSA pause
exit /b %CODIGO%
