@echo off
rem UTF-8 para que los acentos y las tildes no rompan nada.
chcp 65001 >nul 2>&1
setlocal
title KUKIA - guardia (red siempre viva)
cd /d "%~dp0"
echo ============================================================
echo  KUKIA - guardia
echo.
echo  Vigila y relanza solo si algo se cae:
echo    - Core        (puerto 8300)
echo    - Minero      (si deja de cobrar con cola pendiente)
echo    - Validador
echo    - Publicador de metricas
echo    - Panel       (puerto 8301)
echo.
echo  Cerrar esta ventana para la guardia. Los procesos que ya
echo  estaban abiertos se quedan como estan: no los duplica.
echo ============================================================
echo.

python --version >nul 2>&1
if errorlevel 1 (
  echo [ERROR] No hay Python en el PATH.
  pause
  exit /b 1
)

python -u tools\guardia.py %*
echo.
echo [guardia detenida] si el minero o el Core estaban abiertos
echo aparte, siguen funcionando. Esta ventana ya no los vigila.
pause
