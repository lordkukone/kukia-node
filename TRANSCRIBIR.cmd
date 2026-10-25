@echo off
rem Transcribe audio o video con la GPU. Arrastra un fichero encima o pasalo por argumento.
rem   TRANSCRIBIR.cmd charla.mp3
rem   TRANSCRIBIR.cmd --presupuesto charla.mp3     (solo precio, sin GPU)
setlocal
set "AQUI=%~dp0"
set "PY=I:\pinokio\api\whisper-webui.git\app\env\Scripts\python.exe"

if "%~1"=="" (
  echo.
  echo   Uso: arrastra un audio o video sobre este .cmd
  echo        o escribe:  TRANSCRIBIR.cmd archivo.mp3
  echo.
  echo   Para dar precio sin transcribir:
  echo        TRANSCRIBIR.cmd --presupuesto archivo.mp3
  echo.
  pause
  exit /b 1
)

if not exist "%PY%" (
  echo [error] no encuentro el Python con whisper en:
  echo         %PY%
  echo Si lo tienes en otro sitio, define KUKIA_WHISPER_PY antes de lanzar.
  pause
  exit /b 2
)

"%PY%" "%AQUI%servicio\transcribir.py" %*
echo.
pause
