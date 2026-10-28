@echo off
rem  KUKIA - cifrar las claves EN CLARO de D:  (paso 1: cifra y verifica)
rem
rem  NO borra nada: eso es borrar_claves_d.cmd, aparte y a proposito.
rem  La contrasena se pide por teclado dos veces y no se guarda.
rem  Al log va el RESULTADO (hashes, cuadra/no cuadra), no la frase.
setlocal
chcp 65001 >nul
pushd "%~dp0.."

set "LOG=%CD%\claves_d.log"

echo.
echo   KUKIA - CIFRAR LAS CLAVES EN CLARO DE D:
echo   ========================================
echo    entran : sello_key.txt y admin_token.txt de
echo             D:\KUKIA_REPLICA\actual, ...\anterior y D:\KUKIA_backups\...
echo    sale   : D:\CLAVES_KUKIA\claves_D_FECHA.kukia
echo    log    : %LOG%
echo.
echo   NO se cifran kukia.db ni sellos.jsonl: no son secretos y suman 677 MB.
echo.
echo   La contrasena se pide dos veces. Minimo 12 caracteres. No se guarda.
echo   Usa la MISMA que en el paquete de N: si quieres una sola frase que abra
echo   los dos; si las olvidas, ninguno de los dos se abre.
echo.
pause
echo.

echo === %DATE% %TIME% === cifrar claves de D: >> "%LOG%"
echo   ^>^>^> PASO UNICO: cifrar y verificar en memoria
python tools\cifrar_claves_d.py >> "%LOG%"
if errorlevel 1 goto :fallo

echo.
echo   LISTO. El paquete esta escrito y verificado. Los originales SIGUEN en
echo   D:, en claro. El borrado es el otro lanzador: borrar_claves_d.cmd
echo.
echo   Resultado en:
echo     %LOG%
echo.
pause
popd
endlocal
exit /b 0

:fallo
echo. >> "%LOG%"
echo   FALLO. No se ha borrado nada. Mira el final de:
echo     %LOG%
echo.
pause
popd
endlocal
exit /b 1
