@echo off
rem Replica diaria verificada del ledger en D:  (D:\KUKIA_REPLICA).
rem No abre ventana ni espera teclas: escribe en backups\replica.log
rem Lo lanza la tarea "KUKIA Replica Ledger" (cada dia a las 03:40) por el .vbs.
chcp 65001 >nul 2>&1
setlocal
cd /d "%~dp0"
if not exist "backups" mkdir "backups"
set "LOG=backups\replica.log"

rem Task Scheduler puede arrancar sin el PATH del usuario: busca Python y, si
rem no esta, usa la ruta tipica de la instalacion por usuario.
set "PY=python"
where python >nul 2>&1 || set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"

for /f %%f in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd-HHmm"') do set "AHORA=%%f"
if not defined AHORA set "AHORA=%DATE%_%TIME%"

>>"%LOG%" echo === %AHORA% === replica de D: desde el Core
rem Copia coherente + verificacion de cadena y firmas ANTES de promocionar.
rem --avisar: si el disco no responde, la copia falla, no verifica o no es esta
rem red, sale un aviso al escritorio y al movil ademas de la linea de log.
"%PY%" -u tools\replica_ledger.py --avisar >> "%LOG%" 2>&1
set "RC1=%ERRORLEVEL%"
set "RC2=no ejecutada"

rem Si la copia fallo, la "actual" que ya habia sigue siendo la buena: no se
rem compara nada nuevo (seria comparar la de ayer y avisar dos veces por lo mismo).
if not "%RC1%"=="0" goto fin

rem "Se ha quedado atras" no es lo mismo que "no verifica": una replica que deja
rem de actualizarse sigue verificando y pareciendo perfecta. --comparar lo mide
rem contra el Core vivo (rc 6 = mas de 10 KIA por detras) y avisa.
"%PY%" -u tools\replica_ledger.py --comparar --avisar >> "%LOG%" 2>&1
set "RC2=%ERRORLEVEL%"
if not "%RC2%"=="0" goto fin

>>"%LOG%" echo === %AHORA% === fin: OK, replica al dia y verificada
exit /b 0

:fin
>>"%LOG%" echo === %AHORA% === fin: PROBLEMA (copia=%RC1%, comparacion=%RC2%)
exit /b 1
