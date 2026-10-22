@echo off
rem UTF-8: los mensajes llevan tildes y el log de Windows es cp850.
chcp 65001 >nul 2>&1
setlocal enabledelayedexpansion
title KUKIA - preparar dominio propio (una sola vez)
color 0E
cd /d "%~dp0"

echo ============================================================
echo  KUKIA - tunel CON NOMBRE (dominio propio)
echo.
echo  Esto se hace UNA vez. Deja el Core publicado en una URL fija
echo  para siempre, por ejemplo https://kukia.tudominio.com
echo.
echo  ANTES HACEN FALTA DOS COSAS (gratis la cuenta, ~10 EUR/ano el
echo  dominio):
echo    1. Un dominio comprado (Namecheap, Porkbun, Cloudflare...).
echo    2. Ese dominio dado de alta en Cloudflare con sus nameservers.
echo       Cloudflare te lo explica al añadirlo; tarda un rato en
echo       activarse (a veces unas horas).
echo.
echo  El paquete que descarga la gente NO cambia y NO hay que
echo  reinstalar nada: sigue leyendo la direccion estable del espejo.
echo ============================================================
echo.

if not exist "tools\tunel_dominio.py" (
  echo [ERROR] falta tools\tunel_dominio.py
  pause
  exit /b 1
)

set "DOM="
set /p "DOM=  Dominio (ej. kukia.tudominio.com): "
if "%DOM%"=="" (
  echo [ERROR] sin dominio no hay nada que preparar.
  pause
  exit /b 1
)

echo.
echo  Se va a abrir el navegador para autorizar Cloudflare.
echo  Elige ahi tu dominio y pulsa "Authorize".
echo.
python -u tools\tunel_dominio.py --preparar --dominio "%DOM%"
set "COD=%ERRORLEVEL%"

echo.
if not "%COD%"=="0" (
  echo [ATENCION] no se completo. Lee el mensaje de arriba: casi siempre
  echo            es que el dominio aun no esta activo en Cloudflare.
  echo            Se puede repetir sin romper nada.
  pause
  exit /b %COD%
)

echo  Comprobando el resultado...
python -u tools\tunel_dominio.py --estado
echo.
echo  Ya esta. Para arrancarlo:  ARRANCAR_TUNEL.cmd
echo  (o reinicia la guardia y lo coge solo)
pause
