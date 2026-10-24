@echo off
rem UTF-8: los mensajes llevan tildes y el log de Windows es cp850.
chcp 65001 >nul 2>&1
setlocal enabledelayedexpansion
title KUKIA - dominio fijo con Tailscale Funnel
color 0B
cd /d "%~dp0"

echo ============================================================
echo  KUKIA - URL FIJA GRATIS (Tailscale Funnel)
echo.
echo  Deja el Core publicado en una direccion que NO cambia,
echo  sin abrir ni un puerto del router y sin comprar dominio.
echo.
echo  La direccion sera algo como:
echo      https://kukipcwin11.TU-TAILNET.ts.net
echo.
echo  REQUISITO: el Core tiene que estar arrancado en 127.0.0.1:8300
echo  y el equipo logueado en Tailscale.
echo ============================================================
echo.

where tailscale >nul 2>&1
if errorlevel 1 (
  echo [ERROR] tailscale no esta en el PATH.
  echo         Instalalo desde https://tailscale.com/download/windows
  pause & exit /b 1
)

echo [1/4] Comprobando que Tailscale esta conectado...
set "ESTADO="
for /f "delims=" %%i in ('tailscale status --json 2^>nul ^| findstr /c:"BackendState"') do set "ESTADO=%%i"
echo       !ESTADO!

tailscale status >nul 2>&1
if errorlevel 1 (
  echo.
  echo [ERROR] Tailscale no esta logueado en este equipo.
  echo         Ejecuta:  tailscale up
  echo         y entra con tu cuenta ^(gratis^). Despues vuelve a este .cmd
  pause & exit /b 1
)

echo [2/4] Comprobando que el Core responde en 8300...
powershell -NoProfile -Command "try{ $r=Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8300/version -TimeoutSec 5; if($r.StatusCode -eq 200){ '      OK: el Core responde'; exit 0 } }catch{}; '      [ERROR] el Core NO responde en 8300. Arrancalo antes.'; exit 1" 
if errorlevel 1 (
  pause & exit /b 1
)

echo [3/4] Encendiendo el tunel publico en el puerto 443...
tailscale funnel --bg 8300
if errorlevel 1 (
  echo.
  echo [AVISO] No se pudo encender. Los dos motivos habituales son:
  echo   a^) HTTPS no esta habilitado en tu tailnet.
  echo      Panel de Tailscale ^> DNS ^> HTTPS Certificates ^> Enable.
  echo   b^) Funnel no esta autorizado para este equipo.
  echo      Panel ^> Access Controls ^> anade al bloque nodeAttrs:
  echo          "attr": ["funnel"],
  echo      y guarda. Vuelve a ejecutar este .cmd.
  pause & exit /b 1
)

echo.
echo [4/4] Comprobando desde internet...
tailscale funnel status
echo.
echo ------------------------------------------------------------
echo  Tu URL fija esta arriba, en la linea "Funnel on".
echo  Comprueba desde el MOVIL con datos moviles:
echo      https://TU-EQUIPO.TU-TAILNET.ts.net/version
echo      https://TU-EQUIPO.TU-TAILNET.ts.net/stats
echo      https://TU-EQUIPO.TU-TAILNET.ts.net/ledger/integridad
echo.
echo  Para apagarlo:   tailscale funnel --bg off
echo  Para ver estado: tailscale funnel status
echo ------------------------------------------------------------
pause
