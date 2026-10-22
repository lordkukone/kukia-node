@echo off
chcp 65001 >nul 2>&1
setlocal
title KUKIA - RED SOBRE LA REPLICA
cd /d "%~dp0"
echo ============================================================
echo  KUKIA - arrancar la red sobre la REPLICA del ledger
echo.
echo  Usalo SOLO si el Core principal no arranca (disco muerto,
echo  base corrupta, Windows reinstalado...).
echo.
echo  Antes de arrancar comprueba:
echo    - que el Core de siempre esta PARADO (8300 libre)
echo    - que la replica es reciente:
echo        python tools\replica_ledger.py --comparar
echo.
echo  Se pierde lo que se hiciera despues de la ultima copia.
echo  La cadena NO se rompe: sigue desde su ultimo sello firmado.
echo.
echo  Si en disco hay un estado MAS NUEVO que la replica, no arranca y te
echo  dice cuantos asientos perderias. Para forzarlo de todas formas:
echo     python tools\arrancar_replica.py --aceptar-atraso
echo ============================================================
echo.
netstat -ano | findstr /r /c:"127.0.0.1:8300 .*LISTENING" >nul
if not errorlevel 1 (
  echo [AVISO] hay algo escuchando en el 8300.
  echo         Si es el Core principal, NO sigas: serian dos historias.
  echo.
)
pause
python -u tools\arrancar_replica.py %*
echo.
echo [replica] el Core sobre la replica se ha parado.
pause
