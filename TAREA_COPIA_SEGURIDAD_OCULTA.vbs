' KUKIA - lanzador oculto de la copia semanal verificada.
'
' Para que existe: el Programador de tareas ejecuta TAREA_COPIA_SEGURIDAD.cmd,
' y un .cmd SIEMPRE abre una ventana de consola. Un script de wscript no, y con
' el segundo parametro de shell.Run a 0 arranca el .cmd sin mostrar nada.
' El tercero (True) espera a que termine, asi el Programador de tareas recibe
' el resultado de verdad (0 = todo verificado, 1 = algun fichero fallo).
'
' NO es un atajo de doble clic: para ver el progreso, ejecuta el .cmd a mano.

Dim shell, fso, carpeta, cmd
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

carpeta = fso.GetParentFolderName(WScript.ScriptFullName)
cmd = carpeta & "\TAREA_COPIA_SEGURIDAD.cmd"

If Not fso.FileExists(cmd) Then
    WScript.Quit 1
End If

' 0 = ventana oculta, True = esperar a que termine. El codigo de salida se
' devuelve tal cual: si se descarta, la tarea siempre diria "correcto" aunque
' la copia hubiera fallado.
rc = shell.Run("""" & cmd & """", 0, True)
WScript.Quit rc
