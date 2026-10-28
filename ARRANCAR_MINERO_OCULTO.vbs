' KUKIA - arranca el minero SIN ventana, cuando tu quieras (doble clic).

' Por que existe: el 26-10-2026 se quito el arranque automatico del minero
' (el KUKIA Minero.cmd de la carpeta de Inicio y el minero de la guardia).
' Este fichero lo levanta a mano, en segundo plano y sin ventana negra.
'
' La salida se sigue anotando en worker\minero.log, que es de donde el
' publicador de metricas saca la potencia medida de cada GPU.
'
' Para pararlo: cierra el proceso (el log del propio minero dice el PID en la
' cerradura worker\minero.lock), o en una consola:
'     taskkill /IM pythonw.exe /F
' (ojo: eso para tambien otros pythonw; mejor mira el PID en el Administrador
'  de tareas, buscando kukia_worker.py).

Option Explicit

Dim shell, fso, pyw, py, raiz, q, cmd, rot

Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

raiz = "C:\Users\kuki\Desktop\KUKIA"
pyw = "C:\Users\kuki\AppData\Local\Programs\Python312\pythonw.exe"
If Not fso.FileExists(pyw) Then
    pyw = "pythonw.exe"
End If

If Not fso.FolderExists(raiz & "\worker") Then
    MsgBox "No encuentro " & raiz & "\worker", 16, "KUKIA"
    WScript.Quit 1
End If

q = Chr(34)
shell.CurrentDirectory = raiz & "\worker"

' ROTACION DEL LOG (27-10-2026).
' Con `>>` el fichero queda abierto de forma que nadie mas puede truncarlo
' mientras el minero corre (medido con el log del Core: PermissionError). Asi
' que se rota AQUI, antes de arrancar: lo del arranque anterior queda en
' minero.log.1 (y .2) y el fichero vivo empieza vacio. Sin esto, minero.log
' solo crecia. Ver tools/rotar_logs.py.
py = Replace(pyw, "pythonw.exe", "python.exe")
If Not fso.FileExists(py) Then
    py = "python.exe"
End If
rot = q & py & q & " " & q & raiz & "\tools\rotar_logs.py" & q & " --ruta " & q & raiz & "\worker\minero.log" & q & " --forzar --silencioso"
On Error Resume Next
shell.Run rot, 0, True
On Error GoTo 0

' cmd /c ""pythonw.exe" -u kukia_worker.py minero_pc >> minero.log 2>&1"
' El cmd solo hace falta para poder anadir al final del log (> >); pythonw
' solo no sabe redirigir.
cmd = "cmd /c " & q & q & pyw & q & " -u kukia_worker.py minero_pc >> minero.log 2>&1" & q
shell.Run cmd, 0, False
