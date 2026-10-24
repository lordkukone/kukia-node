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
echo  AVISO 24/10/2026: ESTE NO ES EL CAMINO POR DEFECTO.
echo.
echo  Funnel no se activa desde la consola: hace falta habilitar el atributo
echo  "funnel" en el tailnet desde el panel de Tailscale con cuenta
echo  (login.tailscale.com/f/funnel). Sin ese permiso, esto se queda en
echo  "Funnel is not enabled on your tailnet".
echo.
echo  La via que funciona hoy y no pide permiso a nadie:
echo      ARRANCAR_TUNEL.cmd   (tunel de Cloudflare, en la raiz del proyecto)
echo.
echo  Este .cmd se conserva por si algun dia se activa ese permiso. Ver
echo  despliegue\LEEME.md, apartado A.
echo.
echo ------------------------------------------------------------
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
rem --yes: sin esto el comando se queda esperando una confirmacion que, al
rem ejecutarlo con doble clic, no se ve nunca (se queda colgado sin decir nada).
rem La salida se guarda porque Tailscale avisa del permiso que falta justo ahi.
set "LOG=%TEMP%\kukia_funnel.txt"
tailscale funnel --bg --yes 8300 > "%LOG%" 2>&1
type "%LOG%"
rem Unico caso que hay que distinguir: el tailnet no tiene Funnel habilitado.
rem Ese comando sale con exito (errorlevel 0) y solo lo dice en el texto, asi
rem que el errorlevel no sirve: se busca la frase.
findstr /i /c:"not enabled" "%LOG%" >nul 2>&1 && goto :falta_permiso
tailscale funnel status >nul 2>&1
if errorlevel 1 goto :fallo_funnel
goto :funnel_ok

:falta_permiso
echo.
echo [FALTA UN PERMISO] Funnel no esta activado en tu tailnet.
echo   Abre el enlace que sale arriba ^(login.tailscale.com/f/funnel^),
echo   pulsa Enable y vuelve a ejecutar este .cmd.
pause & exit /b 1

:fallo_funnel
echo.
echo [AVISO] No se pudo encender. Los motivos habituales son:
echo   a^) HTTPS sin habilitar en el tailnet: Panel ^> DNS ^> HTTPS Certificates ^> Enable.
echo   b^) Funnel no esta autorizado para este equipo. Panel ^> Access Controls,
echo      anade al bloque nodeAttrs:   "attr": ["funnel"],
echo      y guarda. Vuelve a ejecutar este .cmd.
pause & exit /b 1

:funnel_ok

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
