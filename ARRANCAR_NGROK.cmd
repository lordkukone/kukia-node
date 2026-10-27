@echo off
rem UTF-8: los mensajes llevan tildes y el log de Windows es cp850.
chcp 65001 >nul 2>&1
setlocal enabledelayedexpansion
title KUKIA - tunel con dominio fijo (ngrok)
color 0E
cd /d "%~dp0"

echo ============================================================
echo  KUKIA - Core publicado en una direccion que NO cambia
echo.
echo  ngrok con su dominio gratis: https://algo.ngrok-free.app
echo  La URL es fija aunque reinicies el PC. No es un dominio tuyo
echo  (no puedes elegir el nombre) y el trafico pasa por ngrok.
echo.
echo  Lo que NO cambia: sigue sin abrirse ni un puerto del router.
echo ============================================================
echo.

if not exist "tools\bin\ngrok.exe" (
  echo [ERROR] falta tools\bin\ngrok.exe
  echo         Descarga https://ngrok.com/download , descomprime y deja
  echo         ngrok.exe dentro de tools\bin\
  pause
  exit /b 1
)

rem --- Paso 1: el token (una sola vez) ---
if not exist "%LOCALAPPDATA%\ngrok\ngrok.yml" (
  echo [PASO 1 de 2] Falta el token de ngrok. Es gratis:
  echo     https://dashboard.ngrok.com/get-started/your-authtoken
  echo     (si no tienes cuenta: https://dashboard.ngrok.com/signup)
  echo.
  start "" "https://dashboard.ngrok.com/get-started/your-authtoken"
  echo.
  set "TOK="
  set /p "TOK=  Pega tu authtoken: "
  if "!TOK!"=="" (
    echo [ERROR] sin token no hay tunel.
    pause
    exit /b 1
  )
  "tools\bin\ngrok.exe" config add-authtoken !TOK!
  if errorlevel 1 (
    echo [ERROR] ngrok no acepto el token. Revisalo y vuelve a intentarlo.
    pause
    exit /b 1
  )
  echo   Token guardado.
  echo.
)

rem --- Paso 2: el dominio. Normalmente NO hay que escribirlo: ngrok ya
rem asigno uno a la cuenta y tools\tunel_ngrok.py lo pide a su API. Solo se
rem pide aqui si la deteccion automatica falla (dejar en blanco = intentarlo).
if not exist "ngrok_dominio.txt" (
  echo [PASO 2 de 2] Dominio fijo: lo normal es que ngrok ya te haya dado uno.
  echo     Lo intento detectar solo. Si no sale, lo tienes en
  echo     https://dashboard.ngrok.com/domains  y lo escribes aqui.
  echo.
  set "DOM="
  set /p "DOM=  Dominio (Enter para detectarlo solo): "
  if not "!DOM!"=="" (
    > ngrok_dominio.txt echo !DOM!
    echo   Guardado en ngrok_dominio.txt
  )
  echo.
)

echo [Arrancando] si la guardia esta viva, ya lo hace ella sola y esta
echo              ventana dira que hay otro supervisor: no habra dos tuneles.
echo.
python -u tools\tunel_ngrok.py %*

echo.
echo [tunel cerrado] El Core y el minero siguen funcionando en local.
pause
