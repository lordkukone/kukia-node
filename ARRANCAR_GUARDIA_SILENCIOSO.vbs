' KUKIA - arranca la guardia SIN ventana negra.
'
' Se copia a la carpeta de Inicio del usuario
'   (Win+R -> shell:startup)
' para que la red se levante sola al encender el PC:
'
'   copy ARRANCAR_GUARDIA_SILENCIOSO.vbs "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\"
'
' Para desactivarlo: borra ese .vbs de la carpeta de Inicio. Nada mas.
'
' La guardia escribe su log en logs\guardia.log aunque no haya ventana.
' Si ya hay una guardia corriendo, la nueva se cierra sola (no duplica).

Dim shell, fso, carpeta, pyw, script
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

carpeta = fso.GetParentFolderName(WScript.ScriptFullName)
script = carpeta & "\tools\guardia.py"

' pythonw.exe: igual que python pero sin consola.
pyw = "C:\Users\kuki\AppData\Local\Programs\Python312\pythonw.exe"
If Not fso.FileExists(pyw) Then
    pyw = "pythonw.exe"
End If

If Not fso.FileExists(script) Then
    WScript.Quit 1
End If

shell.Run """" & pyw & """ -u """ & script & """", 0, False
