@echo off
rem Copia diaria programada. No abre ventana ni espera teclas: escribe un log.
chcp 65001 >nul 2>&1
cd /d "%~dp0"
if not exist "backups" mkdir "backups"
rem Task Scheduler puede arrancar sin el PATH del usuario: busca Python y, si
rem no esta, usa la ruta tipica de la instalacion por usuario.
set "PY=python"
where python >nul 2>&1 || set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
"%PY%" -u tools\backup_ledger.py --mantener 14 >> backups\backup.log 2>&1
exit /b %ERRORLEVEL%
