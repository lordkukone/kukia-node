@echo off
rem  KUKIA - borrar el paquete de claves EN CLARO de N:  (paso 3)
rem
rem  Solo tiene sentido despues de cifrar_claves_n.cmd, con el log verificado.
rem  Antes de borrar, descifra el .kukia y compara fichero a fichero con el
rem  original: si uno solo no cuadra, aborta y no borra nada.
rem  Ademas pide escribir BORRAR a mano (lo pide la propia herramienta).
setlocal
chcp 65001 >nul
pushd "%~dp0.."

set "PAQUETE=N:\CLAVES_KUKIA\2026-10-22"
set "ARCHIVO=%PAQUETE%_cifrado.kukia"
set "LOG=%CD%\cifrado_claves_n.log"

if not exist "%ARCHIVO%" (
  echo   ERROR: no existe %ARCHIVO% - cifra primero con cifrar_claves_n.cmd
  goto :fallo
)
if not exist "%PAQUETE%" (
  echo   AVISO: %PAQUETE% ya no existe. No hay nada que borrar.
  goto :fin
)

echo.
echo   KUKIA - BORRAR EL TEXTO PLANO DE N:
echo   ===================================
echo    se quedara : %ARCHIVO%
echo    se borrara : %PAQUETE%
echo.
echo   Es irreversible salvo descifrando con la contrasena. Se te va a pedir
echo   la contrasena, luego BORRAR en mayusculas.
echo.
pause
echo.

echo === %DATE% %TIME% === reemplazar %PAQUETE% >> "%LOG%"
python tools\cifrar_claves.py --reemplazar --origen "%PAQUETE%" --salida "%ARCHIVO%" >> "%LOG%"
if errorlevel 1 goto :fallo

echo.
echo   ^>^>^> comprobacion final del estado en N:
echo. >> "%LOG%"
echo === comprobacion final === >> "%LOG%"
if exist "%PAQUETE%" (
  echo   AVISO: %PAQUETE% sigue existiendo. >> "%LOG%"
  echo   AVISO: %PAQUETE% sigue existiendo.
) else (
  echo   texto plano AUSENTE: %PAQUETE% >> "%LOG%"
  echo   texto plano AUSENTE.
)
for %%F in ("%ARCHIVO%") do echo   cifrado presente: %%~nxF (%%~zF B) >> "%LOG%"
python -c "import hashlib,sys;p=sys.argv[1];h=hashlib.sha256(open(p,'rb').read()).hexdigest();print('   sha256 del .kukia:',h)" "%ARCHIVO%" >> "%LOG%"
echo   estado final escrito en "%LOG%"
echo.
echo   La clave viva del PC no se ha tocado: coordinator\sello_key.txt y
echo   coordinator\admin_token.txt siguen donde estaban.
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

:fin
popd
endlocal
exit /b 0
