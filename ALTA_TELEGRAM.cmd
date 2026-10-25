@echo off
chcp 65001 >nul 2>&1
cd /d "%~dp0"
echo.
echo  ALTA DE LOS AVISOS DE TELEGRAM - KUKIA
echo  ======================================
echo.
echo  ANTES DE NADA: abre Telegram, busca tu bot y mandale /start.
echo  (Si aun no existe el bot, crealo en 2 minutos:)
echo.
echo   1. Abre Telegram y busca @BotFather
echo   2. Mandale:  /newbot
echo   3. Nombre: el que quieras (ej: Avisos KUKIA)
echo   4. Usuario: tiene que acabar en "bot" (ej: kukia_avisos_kuki_bot)
echo   5. BotFather te da un token tipo 8123456789:AAH...
echo.
echo  Pega el token abajo. NO se ve lo que escribes, no queda en el historial
echo  y no pasa por ningun chat: se escribe en .telegram_token, el script lo
echo  recoge y lo borra en el acto.
echo.
powershell -NoProfile -ExecutionPolicy Bypass -Command "$s = Read-Host -AsSecureString -Prompt 'token del bot (no se muestra)'; $b = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($s); $t = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($b); [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($b); if ($t.Trim().Length -gt 0) { [IO.File]::WriteAllText((Join-Path (Get-Location) '.telegram_token'), $t.Trim()) }"
if not exist ".telegram_token" (
  echo.
  echo  [ERROR] no se ha pegado ningun token: no se ha tocado nada.
  echo.
  pause
  exit /b 2
)
echo.
python tools\avisos.py --configurar --token-de .telegram_token
set CODIGO=%ERRORLEVEL%
echo.
if "%CODIGO%"=="0" (
  echo  LISTO. Los avisos ya salen por Telegram: el vigilante, la transcripcion
  echo  larga y lo que se enganche a tools\avisos.py.
) else (
  echo  No se pudo dar de alta ^(codigo %CODIGO%^). Mira el mensaje de arriba.
  echo  Lo tipico: el token no es valido, o no has mandado /start al bot.
)
echo.
pause
exit /b %CODIGO%
