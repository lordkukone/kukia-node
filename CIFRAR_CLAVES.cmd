@echo off
rem UTF-8: los mensajes llevan tildes y el log de Windows es cp850.
chcp 65001 >nul 2>&1
setlocal
title KUKIA - cifrar el paquete de claves de N:
color 0C
cd /d "%~dp0"

echo ============================================================
echo  KUKIA - CIFRAR EL PAQUETE DE CLAVES (N:\CLAVES_KUKIA)
echo.
echo  Esto deja un archivo .kukia que NO se abre sin la contrasena.
echo  Quien se lleve el disco fisico se lleva un monton de bytes.
echo.
echo  AVISO SERIO, leelo antes de escribir la contrasena:
echo    - NO se puede recuperar. Ni yo ni nadie. No hay "he olvidado la
echo      contrasena" en AES-256.
echo    - Apuntala en papel o en un gestor de contrasenas ANTES.
echo    - Las 20 palabras de recuperacion de las billeteras van DENTRO de
echo      este paquete (PALABRAS_PARA_PAPEL.txt). Si luego borras el
echo      original, esa hoja tiene que estar impresa y guardada aparte.
echo.
echo  El original NO se borra aqui. Primero se cifra y se verifica
echo  descifrandolo y comparando hash a hash. Para borrarlo despues:
echo      python tools\cifrar_claves.py --reemplazar
echo      python tools\cifrar_claves.py --verificar N:\CLAVES_KUKIA\2026-10-22_cifrado.kukia
echo ============================================================
echo.

python tools\cifrar_claves.py %*

echo.
echo [fin] Nada se ha borrado. El original sigue donde estaba.
pause
