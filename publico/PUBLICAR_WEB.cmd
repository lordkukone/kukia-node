@echo off
REM Publica la web en la direccion que usa la gente: https://kukia-network.surge.sh/
REM
REM Por que existe: la portada se publicaba a mano y se quedo vieja mas de una
REM vez. Este script regenera codigo.html, kukia-node.zip y version.json desde
REM los ficheros reales y sube SIEMPRE lo mismo a surge. Si algo falla, se
REM para y no publica a medias.
setlocal
cd /d "%~dp0.."

echo [1/2] Regenerando web desde el codigo de disco...
python publico\generar_web.py
if errorlevel 1 goto :error

echo [2/2] Publicando en kukia-network.surge.sh...
REM El dominio va como argumento y NO como docs\CNAME: si CNAME existiera,
REM GitHub Pages intentaria usar ese dominio y romperia su propia publicacion.
call surge ./docs kukia-network.surge.sh
if errorlevel 1 goto :error

echo.
echo Hecho: https://kukia-network.surge.sh/
echo Recuerda: el HTML se cachea 10 min; para verlo al instante, anade ?v=1 a la URL.
exit /b 0

:error
echo.
echo ERROR: no se publico nada. Revisa el mensaje de arriba.
exit /b 1
