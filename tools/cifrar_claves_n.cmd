@echo off
rem  KUKIA - cifrar y verificar el paquete de claves de N:  (pasos 1 y 2)
rem
rem  NO borra nada: eso es borrar_claves_n.cmd, aparte y a proposito.
rem  La contrasena se pide por teclado dos veces y no se guarda.
rem  Lo que se escribe en el log es el RESULTADO (hashes, OK/MAL), no la frase.
rem
rem  Salida estandar -> log   |   error estandar -> consola (ahi va el aviso
rem  de la contrasena, para que no acabe escrita en el log).
setlocal
chcp 65001 >nul
pushd "%~dp0.."

set "PAQUETE=N:\CLAVES_KUKIA\2026-10-22"
set "ARCHIVO=%PAQUETE%_cifrado.kukia"
set "LOG=%CD%\cifrado_claves_n.log"

echo.
echo   KUKIA - CIFRAR EL PAQUETE DE CLAVES DE N:
echo   =========================================
echo    origen  : %PAQUETE%
echo    archivo : %ARCHIVO%
echo    log     : %LOG%
echo.
echo   Antes de seguir, comprueba una cosa que no se puede recuperar:
echo   las 20 palabras de PALABRAS_PARA_PAPEL.txt tienen que estar TAMBIEN
echo   escritas en papel, fuera de cualquier disco. Ese fichero no existe en
echo   ningun otro sitio de este PC.
echo.
echo   La contrasena se pide dos veces. Minimo 12 caracteres. No se guarda en
echo   ningun sitio: si la pierdes, el paquete no se abre.
echo.
pause
echo.

if not exist "%PAQUETE%" (
  echo   ERROR: no existe %PAQUETE%
  goto :fallo
)
if exist "%ARCHIVO%" (
  echo   Ya existe %ARCHIVO%. No lo sobrescribo.
  echo   Si de verdad quieres volver a cifrar, muevelo o borralo a mano.
  goto :fallo
)

echo === %DATE% %TIME% === cifrar %PAQUETE% >> "%LOG%"
echo.
echo   ^>^>^> PASO 1 de 2: cifrar y verificar en memoria
python tools\cifrar_claves.py --origen "%PAQUETE%" --salida "%ARCHIVO%" >> "%LOG%"
if errorlevel 1 goto :fallo

echo.
echo   ^>^>^> PASO 2 de 2: descifrar de verdad y comparar con el original
python tools\cifrar_claves.py --verificar "%ARCHIVO%" --contra "%PAQUETE%" >> "%LOG%"
if errorlevel 1 goto :fallo

echo. >> "%LOG%"
echo   LISTO. Cifrado y verificado. El paquete en claro SIGUE en %PAQUETE%.
echo   El siguiente paso (borrar el texto plano) es borrar_claves_n.cmd,
echo   y solo despues de que hayas leido el log.
echo.
echo   Resultado guardado en:
echo     %LOG%
echo.
pause
popd
endlocal
exit /b 0

:fallo
echo. >> "%LOG%"
echo   FALLO. No se ha borrado nada. Lee el final de:
echo     %LOG%
echo.
pause
popd
endlocal
exit /b 1
