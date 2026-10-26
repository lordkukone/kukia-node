' KUKIA - arranca la guardia SIN ventana negra.
'
' Vive en la carpeta de Inicio del usuario (Win+R -> shell:startup) para que
' la red se levante sola al encender el PC: la guardia arranca Core, minero,
' panel, validador y publicador, y los relanza si se caen.
'
' ARREGLO 22-10-2026: la version anterior sacaba la carpeta del propio .vbs
' (GetParentFolderName(WScript.ScriptFullName)) y como este fichero vive en
' Inicio, buscaba "...\Startup\tools\guardia.py", que no existe: se salia con
' codigo 1 y NO arrancaba nada. Por eso el 22-10 no habia minero ni panel al
' encender. Ahora la ruta del proyecto va escrita entera.
'
' Para desactivarlo: borra este .vbs de la carpeta de Inicio. Nada mas.
' La guardia escribe su log en C:\Users\kuki\Desktop\KUKIA\logs\guardia.log.
' Si ya hay una guardia corriendo, la nueva se cierra sola (no duplica).

Dim shell, fso, pyw, script

Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

script = "C:\Users\kuki\Desktop\KUKIA\tools\guardia.py"

' pythonw.exe: igual que python pero sin consola.
pyw = "C:\Users\kuki\AppData\Local\Programs\Python312\pythonw.exe"
If Not fso.FileExists(pyw) Then
    pyw = "pythonw.exe"
End If

' Si falta la guardia, mejor no hacer nada que abrir algo roto.
If Not fso.FileExists(script) Then
    WScript.Quit 1
End If

' 40 s de margen al encender: discos, red y drivers aun se estan colocando.
WScript.Sleep 40000

shell.Run """" & pyw & """ -u """ & script & """", 0, False
