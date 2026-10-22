@echo off
rem UTF-8: los mensajes llevan tildes y el log de Windows es cp850.
chcp 65001 >nul 2>&1
setlocal
title KUKIA - tunel publico al Core
color 0E
cd /d "%~dp0"

echo ============================================================
echo  KUKIA - tunel publico al Core (con direccion estable)
echo.
echo  Abre un tunel de Cloudflare hacia el Core de ESTE PC y publica
echo  la direccion viva en:
echo      https://kukia-network.surge.sh/core_actual.json
echo.
echo  Hay dos modos y se elige solo:
echo    - RAPIDO   (por defecto): URL https://algo.trycloudflare.com.
echo               Gratis y sin cuenta, pero el nombre cambia al
echo               arrancar. El paquete sigue valiendo porque lee la
echo               direccion estable del espejo.
echo    - DOMINIO  (si hiciste PREPARAR_DOMINIO.cmd): URL fija, tu
echo               dominio. No cambia nunca.
echo.
echo  El paquete kukia-node.zip NO lleva la URL del tunel: lleva la
echo  direccion estable. Asi sigue sirviendo en los dos modos.
echo.
echo  Cerrar esta ventana CORTA el acceso publico; el Core y el minero
echo  siguen funcionando en local. Al volver a abrirla, se publica la
echo  direccion automaticamente.
echo.
echo  Si el guardia esta arrancado, ya lo hace el solo: en ese caso esta
echo  ventana dira que hay otro supervisor vivo y no habra dos tuneles.
echo ============================================================
echo.

python --version
if errorlevel 1 (
  echo [ERROR] No hay Python en el PATH. Instala Python 3.10 o superior
  echo         desde python.org y marca "Add Python to PATH".
  pause
  exit /b 1
)

rem Los DOS ficheros a la vez: --preparar los escribe juntos al final, asi que
rem si solo hay uno la preparacion quedo a medias y toca el tunel rapido.
set "MODO=rapido"
if exist "dominio_core.txt" if exist "tools\cloudflared\config.yml" set "MODO=dominio"
echo.
if "%MODO%"=="dominio" (
  echo [modo DOMINIO] dominio propio preparado
  python -u tools\tunel_dominio.py --arrancar %*
) else (
  echo [modo RAPIDO] direccion estable con trycloudflare
  python -u tools\tunel_estable.py %*
)

echo.
echo [tunel cerrado] El Core y el minero siguen funcionando en local.
pause
