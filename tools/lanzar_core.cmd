@echo off
rem ===========================================================================
rem  KUKIA - lanzador del Core real (127.0.0.1:8300)
rem  ---------------------------------------------------------------------------
rem  ANTES (hasta el 27-10-2026) este lanzador vivia en
rem      C:\Users\kuki\AppData\Local\Temp\opencode\lanzar_core.cmd
rem  y escribia el log en C:\Users\kuki\Desktop\Fotos\core.log.
rem
rem  DOS DEFECTOS ENCONTRADOS EL 27-10-2026 Y CORREGIDOS AQUI:
rem
rem   1) LA RUTA DE PYTHON NO EXISTIA. El lanzador decia
rem          ...\AppData\Local\Programs\Python\Python312\python.exe
rem      pero ese directorio no existe (solo hay "Python\Launcher"). El Python
rem      real esta en
rem          ...\AppData\Local\Programs\Python312\python.exe
rem      Con la ruta mala, cmd respondia "El sistema no puede encontrar la ruta
rem      especificada" y el Core NO arrancaba. Y como la tarea lanza el .vbs,
rem      que a su vez lanza este .cmd sin esperar, el Programador de tareas
rem      informaba "resultado 0" (exito) mientras no habia nada corriendo: el
rem      Core podia estar muerto y el registro decir que todo iba bien.
rem
rem   2) El log estaba fuera del repo (Carpeta "Fotos"). Ahora va a
rem      KUKIA\logs\core.log, junto al del vigilante.
rem
rem  Usa el puerto 8300 (el de produccion). Se puede pasar otro como primer
rem  argumento SOLO para pruebas:  lanzar_core.cmd 8399
rem ===========================================================================

set "PUERTO=%~1"
if "%PUERTO%"=="" set "PUERTO=8300"

set "RAIZ=C:\Users\kuki\Desktop\KUKIA"
set "LOG=%RAIZ%\logs\core.log"
rem El aviso del guardia va a un log PROPIO: el Core en marcha tiene cogido
rem core.log (es su salida), asi que un segundo lanzamiento no puede escribir
rem ahi. Lo descubri probando el guardia: se negaba a arrancar (bien) pero el
rem motivo no quedaba escrito en ningun sitio (mal).
set "LOGGUARDIA=%RAIZ%\logs\lanzador.log"

rem Ruta verificada el 27-10-2026. Si algun dia cambia, se cae al python del PATH
rem en vez de morir en silencio.
set "PY=C:\Users\kuki\AppData\Local\Programs\Python312\python.exe"
if not exist "%PY%" set "PY=python"

cd /d "%RAIZ%\coordinator"
if not exist "%RAIZ%\logs" mkdir "%RAIZ%\logs"

rem ---- GUARDIA ANTI DOBLE ARRANQUE (27-10-2026) --------------------------
rem Con el arranque automatico (al iniciar sesion, y a peticion del vigilante)
rem este lanzador puede dispararse cuando YA hay un Core. Un segundo uvicorn no
rem llegaria a escuchar (el puerto esta cogido) pero SI importaria el modulo del
rem Core y correria sus migraciones sobre la base buena antes de morir: dos
rem procesos tocando el mismo ledger para nada. Por eso se mira el puerto
rem primero. Si esta ocupado, no se arranca nada y queda escrito en el log.
set "OCUPADO="
for /f "usebackq delims=" %%i in (`powershell -NoProfile -Command "if (Get-NetTCPConnection -LocalPort %PUERTO% -State Listen -ErrorAction SilentlyContinue) { 'si' } else { 'no' }"`) do set "OCUPADO=%%i"
if /i "%OCUPADO%"=="si" (
    echo [%date% %time%] EL PUERTO %PUERTO% YA ESTA OCUPADO: no arranco un segundo Core >> "%LOGGUARDIA%"
    exit /b 0
)

rem ---- ROTACION DEL LOG (27-10-2026) --------------------------------------
rem core.log acumulaba TODO lo de cada arranque y llego a 209 MB (1,35 millones
rem de lineas, casi todas peticiones 200 OK). Aqui se rota en cada arranque, con
rem el Core parado (si estuviera vivo, arriba se habria salido): lo del arranque
rem anterior queda en core.log.1 y el fichero vivo empieza vacio. La guardia
rem hace lo mismo cada 30 s con la salida de sus hijos.
"%PY%" "%RAIZ%\tools\rotar_logs.py" --ruta "%LOG%" --forzar --silencioso

echo [%date% %time%] ARRANCANDO CORE (puerto %PUERTO%) con %PY% >> "%LOG%"
rem ---- SALIDA CON ROTACION (27-10-2026) -----------------------------------
rem Antes: uvicorn ... >> core.log. Con `>>` el fichero queda abierto de forma
rem que NADIE mas puede escribirlo (PermissionError al truncar, medido con el
rem log de 209 MB), asi que no habia manera de cortarlo mientras corria.
rem Ahora lo escribe tools\registrar_salida.py: guarda la salida en el log, la
rem ensena igual en esta consola y, cuando pasa de 5 MB, guarda la cola en
rem core.log.1 y empieza de cero. Devuelve el MISMO codigo que el Core.
rem --no-proxy-headers (auditoria 28-10-2026, A2): sin el, uvicorn reescribe la
rem IP del cliente con el X-Forwarded-For que llega, y detras del tunel eso
rem permite falsificar la IP y saltarse los cupos por IP.
"%PY%" -u "%RAIZ%\tools\registrar_salida.py" "%LOG%" -- "%PY%" -u -m uvicorn kukia_core:app --host 127.0.0.1 --port %PUERTO% --no-proxy-headers
echo [%date% %time%] CORE TERMINADO con codigo %errorlevel% >> "%LOG%"
