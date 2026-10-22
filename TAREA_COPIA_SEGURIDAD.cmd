@echo off
rem Copia de seguridad SEMANAL verificada: KUKIA + CEREBRO + Xoxete -> N:
rem No abre ventana ni espera teclas: escribe en backups\copia_seguridad.log
rem Lo lanza la tarea "KUKIA Copia Seguridad" (domingos 04:00) por el .vbs.
chcp 65001 >nul 2>&1
setlocal
cd /d "%~dp0"
if not exist "backups" mkdir "backups"
set "LOG=backups\copia_seguridad.log"

rem Task Scheduler puede arrancar sin el PATH del usuario: busca Python y, si
rem no esta, usa la ruta tipica de la instalacion por usuario.
set "PY=python"
where python >nul 2>&1 || set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"

rem Destino: la copia base del 22-10-2026. Se refresca en cada pasada (lo que
rem no ha cambiado no se recopia). Se puede cambiar sin tocar el script.
set "DESTINO=N:\COPIA_SEGURIDAD_20261022"
if defined KUKIA_COPIA_DESTINO set "DESTINO=%KUKIA_COPIA_DESTINO%"

set "ORIGEN1=C:\Users\kuki\Desktop\KUKIA"
set "ORIGEN2=I:\CEREBRO"
set "ORIGEN3=H:\Xoxete"

for /f %%f in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd-HHmm"') do set "AHORA=%%f"
if not defined AHORA set "AHORA=%DATE%_%TIME%"

rem N: es un disco aparte: al encender puede tardar en aparecer. Se espera 30 min.
set /a ESPERA=0
:espera_n
if exist "N:\" goto n_listo
if %ESPERA% GEQ 1800 goto n_sin
ping -n 11 127.0.0.1 >nul
set /a ESPERA+=10
goto espera_n

:n_sin
>>"%LOG%" echo [%AHORA%] FALLO: la unidad N: no aparece. No se copia nada.
exit /b 3

:n_listo
rem Si falta cualquier origen no se copia a medias: una copia sin CEREBRO
rem (o sin Xoxete) no es la copia que se promete, y el resultado debe ser verde
rem solo cuando estan las tres cosas.
set "FALTAN="
if not exist "%ORIGEN1%\" set "FALTAN=%FALTAN% KUKIA"
if not exist "%ORIGEN2%\" set "FALTAN=%FALTAN% CEREBRO(I:)"
if not exist "%ORIGEN3%\" set "FALTAN=%FALTAN% Xoxete(H:)"
if not defined FALTAN goto copiar
>>"%LOG%" echo [%AHORA%] FALLO: falta la unidad de:%FALTAN%. No se copia nada.
exit /b 4

:copiar
>>"%LOG%" echo [%AHORA%] inicio copia verificada -^> %DESTINO%
"%PY%" -u tools\copia_seguridad.py "%DESTINO%" "%ORIGEN1%" "%ORIGEN2%" "%ORIGEN3%" >> "%LOG%" 2>&1
set "RC=%ERRORLEVEL%"

rem Historial: RESUMEN.txt se reescribe en cada pasada, asi que se guarda una
rem copia con fecha. Solo se anade: nunca se borra nada.
if not exist "%DESTINO%\RESUMEN.txt" goto sin_historial
if not exist "%DESTINO%\historial" mkdir "%DESTINO%\historial"
copy /y "%DESTINO%\RESUMEN.txt" "%DESTINO%\historial\RESUMEN_%AHORA%.txt" >nul 2>&1
set "RES=fallos=desconocido"
for /f "tokens=1,2" %%a in ('findstr /b /c:"fallos:" "%DESTINO%\RESUMEN.txt"') do set "RES=%%a %%b"
>>"%DESTINO%\historial\HISTORIAL.txt" echo [%AHORA%] resultado=%RC%  %RES%

:sin_historial
>>"%LOG%" echo [%AHORA%] fin: resultado=%RC%
exit /b %RC%
