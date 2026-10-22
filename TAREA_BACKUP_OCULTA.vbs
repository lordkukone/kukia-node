' KUKIA - lanzador oculto de la copia diaria.
'
' Para que existe: el Programador de tareas ejecutaba TAREA_BACKUP.cmd
' directamente y eso abre una ventana de CMD todos los dias a las 03:00 (y se
' queda ahi hasta que acaba). Un .cmd SIEMPRE abre consola; la unica forma de
' que no se vea es lanzarlo desde un script que pida ventana oculta.
'
' wscript.exe no necesita consola, y shell.Run con el segundo parametro a 0
' arranca el .cmd sin mostrar nada. El tercero (True) espera a que termine, asi
' el Programador de tareas recibe el resultado de verdad.
'
' NO es un atajo de doble clic: no lo ejecutes a mano si quieres ver el log.
' Para eso esta TAREA_BACKUP.cmd.

Dim shell, fso, carpeta, cmd
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

carpeta = fso.GetParentFolderName(WScript.ScriptFullName)
cmd = carpeta & "\TAREA_BACKUP.cmd"

If Not fso.FileExists(cmd) Then
    WScript.Quit 1
End If

' 0 = ventana oculta, True = esperar a que termine. El codigo de salida se
' devuelve tal cual: si se descarta, la tarea siempre diria "correcto" aunque
' la copia hubiera fallado.
rc = shell.Run("""" & cmd & """", 0, True)
WScript.Quit rc
