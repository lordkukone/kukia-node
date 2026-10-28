@echo off
rem  KUKIA - borrar las claves EN CLARO de D:  (paso 2)
rem
rem  Solo despues de cifrar_claves_d.cmd, con su log mirado.
rem  Descifra el paquete y compara fichero a fichero: si uno no cuadra, no borra.
rem  Pide escribir BORRAR a mano.
rem
rem  OJO, esto tiene consecuencias que estan escritas en el informe:
rem   - tools\arrancar_replica.py exige D:\KUKIA_REPLICA\actual\sello_key.txt
rem     para arrancar la red SOBRE la replica. Sin ella devuelve error 6.
rem     Si algun dia hace falta, se saca del paquete con cifrar_claves.py.
rem   - La proxima vez que se ejecute tools\backup_ledger.py con sus valores
rem     por defecto, volvera a copiar la clave en claro en la copia nueva.
setlocal
chcp 65001 >nul
pushd "%~dp0.."

set "LOG=%CD%\claves_d.log"

echo.
echo   KUKIA - BORRAR LAS CLAVES EN CLARO DE D:
echo   ========================================
echo    antes comprueba que el paquete descifra y cuadra con lo que hay.
echo.
echo    Consecuencia: arrancar_replica.py dejara de arrancar (error 6) hasta
echo    que devuelvas D:\KUKIA_REPLICA\actual\sello_key.txt desde el paquete.
echo.
pause
echo.

echo === %DATE% %TIME% === borrar claves en claro de D: >> "%LOG%"
python tools\cifrar_claves_d.py --borrar >> "%LOG%"
if errorlevel 1 goto :fallo

echo.
echo   Hecho. Mira el estado final en:
echo     %LOG%
echo.
pause
popd
endlocal
exit /b 0

:fallo
echo. >> "%LOG%"
echo   NO SE HA BORRADO NADA (o se ha parado antes de confirmar).
echo   Mira el final de:
echo     %LOG%
echo.
pause
popd
endlocal
exit /b 1
