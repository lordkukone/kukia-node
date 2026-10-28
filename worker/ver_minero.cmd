@echo off
title KUKIA MINERO - kuki_minero (RTX 5070 + RTX 5060 Ti) - solo lectura
color 0A
echo ============================================================
echo  KUKIA MINERO - salida en vivo (esto NO arranca ni para nada)
echo  El minero corre en segundo plano y escribe en este log.
echo  Cerrar esta ventana NO detiene el minero.
echo  Para pararlo:  mira el PID con: tasklist /FI "IMAGENAME eq python.exe"
echo ============================================================
echo.
rem 27-10-2026: antes seguia minero_ok.out.log, un fichero de un arranque suelto
rem del 19-10 que ya no escribe nadie (y que se borro al limpiar los logs
rem viejos). El log bueno es minero.log, en esta carpeta: lo escribe la guardia
rem (o ARRANCAR_MINERO_OCULTO.vbs) y rota solo.
powershell -NoProfile -Command "Get-Content -Path '%~dp0minero.log' -Wait -Tail 40"
echo.
echo [ventana cerrada] El minero sigue corriendo en segundo plano.
pause
