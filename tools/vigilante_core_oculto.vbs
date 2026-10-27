' ===========================================================================
'  KUKIA - lanza el vigilante del Core sin abrir ninguna ventana.
'  Lo llama la tarea programada Watch_Core cada 5 minutos.
'
'  El script de verdad es tools\vigilante_core.ps1, que SOLO puede tocar al
'  proceso que ocupa el puerto 8300 (nunca busca por linea de comandos, asi que
'  un Core de pruebas en otro puerto le es invisible).
'
'    -Puerto 8300   el puerto vigilado (explicito, no por defecto)
'    -Relanzar      si tiene que reiniciar un Core colgado, pide la tarea
'                   KUKIA_Core para volver a levantarlo. Sin esta opcion
'                   reiniciaria y no repondria nada.
' ===========================================================================
Set sh = CreateObject("WScript.Shell")
sh.Run "powershell -NoProfile -ExecutionPolicy Bypass -File ""C:\Users\kuki\Desktop\KUKIA\tools\vigilante_core.ps1"" -Puerto 8300 -Relanzar", 0, False
