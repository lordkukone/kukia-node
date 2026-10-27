@echo off
REM Abre la web de KUKIA en un perfil limpio del navegador.
REM
REM Por que existe (26-10-2026): la web carga bien desde este PC (comprobado con
REM curl, con Chrome en modo headless y con un perfil nuevo), pero en los
REM navegadores de uso diario no abria. Eso apunta al perfil del navegador:
REM extensiones, cache vieja o un service worker atascado de una version
REM anterior. Este atajo abre la misma web en un perfil aparte, sin extensiones
REM y sin cache, asi que no toca nada de tu perfil normal.
setlocal EnableDelayedExpansion
set "URL=https://kukia-network.surge.sh/"
set "PERFIL=%TEMP%\kukia_web_limpio"

set "CHROME=%ProgramFiles%\Google\Chrome\Application\chrome.exe"
if not exist "%CHROME%" set "CHROME=%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"
if exist "%CHROME%" (
  echo Abriendo KUKIA con Chrome, perfil limpio...
  start "" "%CHROME%" --new-window --user-data-dir="%PERFIL%" --no-first-run --no-default-browser-check "%URL%"
  goto :fin
)

set "EDGE=%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"
if not exist "%EDGE%" set "EDGE=%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"
if exist "%EDGE%" (
  echo Chrome no esta, abriendo con Edge, perfil limpio...
  start "" "%EDGE%" --new-window --user-data-dir="%PERFIL%" --no-first-run --no-default-browser-check "%URL%"
  goto :fin
)

set "FIREFOX=%ProgramFiles%\Mozilla Firefox\firefox.exe"
if exist "%FIREFOX%" (
  echo Abriendo con Firefox en ventana privada...
  start "" "%FIREFOX%" -private-window "%URL%"
  goto :fin
)

echo No encuentro Chrome, Edge ni Firefox en las rutas normales.
echo Abre esta direccion a mano: %URL%
pause
exit /b 1

:fin
echo.
echo Si con este perfil limpio SI abre, el problema esta en tu navegador de
echo siempre: extensiones o cache. En Chrome: Configuracion, Privacidad,
echo Borrar datos de navegacion (cache), y revisa las extensiones.
exit /b 0
