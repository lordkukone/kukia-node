' ===========================================================================
'  KUKIA - arranca el Core real en segundo plano, sin ventana.
'  Lo llama la tarea programada KUKIA_Core (y el vigilante con -Relanzar).
'  El lanzador de verdad esta en el repo: tools\lanzar_core.cmd
'  Ventana 0 = oculta (el Core no pregunta nada: todo va al log).
' ===========================================================================
Set sh = CreateObject("WScript.Shell")
sh.Run """C:\Users\kuki\Desktop\KUKIA\tools\lanzar_core.cmd""", 0, False
