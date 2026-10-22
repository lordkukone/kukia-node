@echo off
chcp 65001 >nul 2>&1
setlocal
title KUKIA - copia de seguridad del ledger
cd /d "%~dp0"
echo ============================================================
echo  KUKIA - copia VERIFICADA del ledger (db + sellos + clave)
echo.
echo  Crea backups\kukia_FECHA_HORA\ y comprueba la cadena y las
echo  firmas de la copia antes de darla por buena. Si algo no
echo  cuadra, sale con error y lo dice.
echo.
echo  La clave del sello se copia: sin ella la copia no se puede
echo  verificar sola. Guarda la carpeta en un sitio privado.
echo ============================================================
echo.
python -u tools\backup_ledger.py %*
echo.
if errorlevel 1 (
  echo [FALLO] la copia no se pudo verificar. Revisa el mensaje de arriba.
) else (
  echo [OK] copia verificada.
)
pause
