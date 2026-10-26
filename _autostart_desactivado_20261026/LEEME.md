# Autostart de KUKIA — desactivado el 26-10-2026

Orden de Kuki: **nada de KUKIA se arranca solo**; se arranca a mano, o lo arranca
el agente cuando lo pida.

Aquí está TODO lo que había antes, para poder volver atrás sin pensar. No se ha
borrado ni un fichero de su sitio: lo de la carpeta Inicio se ha **renombrado**
(añadiendo `.desactivado`) y las tareas programadas se han **desactivado**
(siguen enteras, con su configuración).

## Desactivado

### Carpeta Inicio del usuario

`C:\Users\kuki\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup\`

| Fichero (ahora) | Qué hacía al iniciar sesión |
|---|---|
| `KUKIA Guardia.vbs.desactivado` | Arrancaba la guardia oculta (`tools\guardia.py --sin-minero`): Core, panel, validador, publicador y túnel, y los relanzaba si se caían |
| `KUKIA_Resume.cmd.desactivado` | Lanzaba un `.ps1` de la carpeta temporal que **ya no existe**: no hacía nada |

Copia exacta de los dos, con el mismo hash, en esta carpeta.

### Tareas programadas (desactivadas, no borradas)

| Tarea | Qué hacía |
|---|---|
| `KUKIA_Core` | Relanzaba el Core (`uvicorn kukia_core:app` en 8300) cada pocos minutos |
| `KUKIA_Dash` | Relanzaba el panel (`uvicorn kukia_dash:app` en 8301) |
| `KUKIA Vigilante Web` | Corría `tools\vigilante_web.py` (comprueba la web publicada e indexa) |
| `KUKIA_vigilante_webs` | Corría `tools\vigilante_servicios.py --una-vez` (comprueba servicios y avisa por Telegram) |
| `Snap_Kukia` | Escribía `kukia-fase1\docs\gpu_metricas.json` con las métricas de GPU |

Se desactivan los vigilantes a propósito: si KUKIA no está arrancado (porque lo
arrancas tú), estarían avisando cada pocos minutos de que el Core está caído.

## NO se ha tocado (sigue programado)

| Tarea | Cuándo |
|---|---|
| `KUKIA Backup Ledger` | a diario — copia del ledger |
| `KUKIA copia de claves` | semanal |
| `KUKIA Copia Seguridad` | semanal |
| `Descargas_Kukia` | cada pocos minutos — reintenta descargas grandes (aria2) |

No arrancan KUKIA y son copias de seguridad o descargas tuyas: si también los
quieres fuera, se desactivan igual que los de arriba.

## Cómo volver atrás

```powershell
# tareas (una por una, o todas)
Enable-ScheduledTask -TaskName 'KUKIA_Core'
Enable-ScheduledTask -TaskName 'KUKIA_Dash'
Enable-ScheduledTask -TaskName 'KUKIA Vigilante Web'
Enable-ScheduledTask -TaskName 'KUKIA_vigilante_webs'
Enable-ScheduledTask -TaskName 'Snap_Kukia'

# o restaurando la definición original guardada aquí:
Register-ScheduledTask -Xml (Get-Content '.\tarea_KUKIA_Core.xml' -Raw) -TaskName 'KUKIA_Core' -Force
```

Y la carpeta Inicio, en Git Bash o CMD:

```bash
cd "C:/Users/kuki/AppData/Roaming/Microsoft/Windows/Start Menu/Programs/Startup"
mv "KUKIA Guardia.vbs.desactivado" "KUKIA Guardia.vbs"
mv "KUKIA_Resume.cmd.desactivado" "KUKIA_Resume.cmd"
```

## Cómo se arranca a mano

- Minero: `ARRANCAR_MINERO.cmd` (abre su ventana y mina en tus GPU)
- Toda la red de una vez (Core, panel, validador, publicador, túnel): `ARRANCAR_GUARDIA.cmd`
- Solo el Core: `ARRANCAR_PANEL.cmd` / lo que uses normalmente

Los `.xml` de esta carpeta son la definición completa de cada tarea tal y como
estaba antes de tocarla.
