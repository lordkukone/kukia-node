' KUKIA - lanzador oculto de la guardia (para el Programador de tareas).
'
' Por que existe: la tarea KUKIA Guardia lanza este .vbs, que abre guardia.py
' con pythonw (sin consola). Si se lanzara guardia.py directo desde la tarea,
' Windows abriria una ventana negra en cada inicio de sesion y se quedaria ahi.
'
' Que hace la guardia: levanta Core, tunel publico, minero, validador, panel y
' publicador, y los relanza si se caen. Si ya hay una guardia viva, la nueva se
' cierra sola (tools/guardia.py tiene su propio cerrojo, no se duplica).
'
' NO es un atajo de doble clic: para arrancar a mano usa ARRANCAR_GUARDIA.cmd,
' que abre ventana y ensena el log.

Dim shell, fso, raiz, script, pyw

Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

raiz = fso.GetParentFolderName(WScript.ScriptFullName)
script = raiz & "\tools\guardia.py"

' Se puede lanzar desde el proyecto (tarea programada) o desde la carpeta de
' Inicio (donde no hay tools\guardia.py al lado). El 22-10-2026 este mismo
' fallo dejo la red sin arrancar: el .vbs vivia en Inicio, sacaba la carpeta de
' su propia ruta y buscaba "...\Startup\tools\guardia.py". Aqui, si no esta al
' lado, se usa la ruta del proyecto.
If Not fso.FileExists(script) Then
    script = "C:\Users\kuki\Desktop\KUKIA\tools\guardia.py"
End If

If Not fso.FileExists(script) Then
    WScript.Quit 1
End If

pyw = "C:\Users\kuki\AppData\Local\Programs\Python312\pythonw.exe"
If Not fso.FileExists(pyw) Then
    pyw = "pythonw.exe"
End If

' 40 s de margen al encender: discos, red y drivers aun se estan colocando
' (era lo que hacia el lanzador anterior y evita arranques en falso).
WScript.Sleep 40000

' 0 = ventana oculta. False = no esperar: la guardia no termina nunca (si
' terminara, el Programador de tareas la volveria a lanzar en el proximo
' inicio de sesion, que es justo lo que se quiere).
shell.Run """" & pyw & """ -u """ & script & """", 0, False
