# LEEME del vigilante y de los lanzadores del Core

**Qué es esto:** las piezas que mantienen vivo el Core de KUKIA, qué hace cada una, cómo se
comprueban y cómo se deshacen. Escrito el 27/10/2026, cuando se pusieron bajo control de
versiones (rama `operativo-versionado-20261027`): hasta ese día solo existían en el árbol de
trabajo y en copias dentro de `logs`, así que un clon nuevo o un borrado se las llevaban por
delante.

**Lo que NO toca ninguna de estas piezas:** el ledger, los saldos, las firmas, los sellos,
las reglas económicas ni la base de datos. Solo miran un puerto y, como mucho, piden que
arranque una tarea.

---

## 1. Qué es cada fichero

| Fichero | Qué es | Quién lo usa |
|---|---|---|
| `tools\vigilante_core.ps1` | **El vigilante.** Mira quién ocupa `127.0.0.1:8300` y decide: si el Core está sano no lo toca; si está colgado lo reinicia; si el puerto está libre pide la tarea para levantarlo. Nunca busca procesos por línea de comandos. | La tarea `Watch_Core`, cada 5 min |
| `tools\vigilante_core.config.json` | **Su configuración.** Puerto, tarea que arranca el Core, tiempos, límites y freno. Es lo primero que hay que mirar antes de tocar nada. | El vigilante |
| `tools\vigilante_core.hashes.json` | **El manifiesto de sellos:** SHA-256 de las piezas de arriba. Sirve para saber si lo que corre es lo que se revisó. | `instalar_tareas_core.ps1 -VerificarHashes` |
| `tools\vigilante_core_oculto.vbs` | **Lanza el vigilante sin abrir ventana.** Es lo que ejecuta la tarea (una tarea que llamara al `.ps1` directamente parpadearía una ventana cada 5 min). | La tarea `Watch_Core` |
| `tools\lanzar_core.cmd` | **El lanzador del Core.** Comprueba la ruta de Python, arranca uvicorn en el puerto, escribe en `logs\core.log` y **se niega a arrancar un segundo Core si el puerto ya está ocupado** (un segundo uvicorn no llegaría a escuchar pero sí correría migraciones sobre la base buena). | La tarea `KUKIA_Core` y el vigilante |
| `tools\lanzar_core_oculto.vbs` | **Lanza el lanzador sin ventana** (y sin esperar: por eso el «resultado 0» de la tarea no significa que el Core esté vivo). | La tarea `KUKIA_Core` |
| `tools\instalar_tareas_core.ps1` | **Instalador y verificador de las tareas.** Copia antes de tocar, valida después y revierte solo si falla. Ojo: llevar tareas programadas pide permisos de administrador. | Kuki, a mano |
| `tools\pruebas_seguridad.py` | **La suite de seguridad** (los arreglos de H-01/H-02). No es del vigilante, pero va con el mismo lote porque es la que demuestra que el Core no se cae por los ticks. | Kuki, a mano |

Rama `operativo-versionado-20261027`. Para volver a la versión anterior de cualquier pieza,
`git log` de ese fichero en la rama y `git checkout <commit> -- <fichero>`.

---

## 2. Cómo funciona, de punta a punta

```
Watch_Core (cada 5 min)
   -> vigilante_core_oculto.vbs
      -> vigilante_core.ps1      mira el 8300 y decide
         ├─ sano ................ no toca nada (aunque tarde en responder: eso es trabajo,
         │                        no muerte; hay timeout, reintentos y CPU como pruebas)
         ├─ colgado ............. parada ordenada y, si no, reinicia SOLO al dueño del puerto,
         │                        con ventana de gracia y freno (max 3 por hora)
         └─ puerto libre ........ pide la tarea KUKIA_Core y comprueba que arranca;
                                  si no arranca: ALERTA + resultado 3 + enfriamiento

KUKIA_Core (al iniciar sesión +30 s, o cuando el vigilante la pide)
   -> lanzar_core_oculto.vbs
      -> lanzar_core.cmd         (guardia: si el 8300 ya está ocupado, no arranca nada)
         -> uvicorn kukia_core:app en 127.0.0.1:8300   ->  logs\core.log
```

Reglas de oro del vigilante, que están en el código a propósito:

- **Identifica por puerto, no por línea de comandos.** Un Core de pruebas en otro puerto
  (17802, 4163, el que sea) le es invisible. El vigilante viejo mataba «todos los
  `uvicorn kukia_core`», y así se cayeron Cores de prueba.
- **Si la identidad no está confirmada, no mata nada** (MODO SEGURO y al registro). El
  patrón esperado está en `proceso_esperado` de la configuración.
- **Un timeout no es una muerte.** Un `/stats` lento es trabajo en curso, no un cadáver.
- **Nunca mata procesos de otros puertos** ni aunque el suyo esté apagado.

---

## 3. Cómo se comprueba (sin tocar el Core real)

```powershell
cd C:\Users\kuki\Desktop\KUKIA
powershell -File tools\instalar_tareas_core.ps1 -VerificarHashes   # sellos: "Todo cuadra"
powershell -File tools\instalar_tareas_core.ps1 -Simular           # qué cambiaría, sin tocar
```

- **Prueba en aislado del arranque automático** (puertos de prueba, Core falso, nada de
  producción): `KUKIA_H01F02_20261027\prueba_arranque_auto_aislada.py`
- **Matriz adversarial del vigilante** (25 casos, puertos elegidos al vuelo):
  `KUKIA_H01F02_20261027\matriz_watchdog.py`
- **Qué ha hecho el vigilante, decisión por decisión:** `logs\vigilante_core.log`
  (una línea por motivo, con fecha ISO, identificador de ejecución y PID).
- **Si algo va mal:** `logs\vigilante_core.ALERTA.txt` (si existe, hay algo pendiente).
- **La salida del Core:** `logs\core.log`. Y `logs\lanzador.log`, que es donde el lanzador
  escribe cuando se niega a arrancar un segundo Core (no puede usar `core.log`: el Core lo
  tiene cogido).

---

## 4. Trampas que ya nos han costado tiempo (no repetirlas)

1. **Finales de línea.** El manifiesto guarda SHA-256 de los bytes. Si git convierte los
   finales de línea, un clon trae otros bytes y el sello sale roto sin que nadie haya tocado
   nada. Por eso están en `.gitattributes` con `-text`, igual que los ficheros de la web con
   SRI. Medido: 38.131 B en un clon frente a 37.355 B sellados.
2. **`$Repo` fijo en el instalador.** `instalar_tareas_core.ps1` lleva
   `$Repo = "C:\Users\kuki\Desktop\KUKIA"`. Ejecutado desde un clon verifica **el repo
   original** y dice «Todo cuadra» con otros bytes dentro del clon. Para eso está
   `KUKIA_H01F02_20261027\verificar_sellos_en_clon.ps1 -Raiz <carpeta>`.
3. **El «resultado 0» de la tarea miente.** Significa «wscript arrancó», no «el Core está
   vivo». Lo que pasó está siempre en el registro del vigilante y en `logs\core.log`.
4. **`-SellarHashes` después de tocar cualquier pieza.** Si no, `-VerificarHashes` dirá que
   hay piezas cambiadas (y hará bien).
5. **Con `vigilar_si_puerto_libre: true`**, en cuanto el 8300 se quede libre el Core real
   arranca solo en ≤5 minutos: las pruebas que necesiten ese puerto deben ocuparlo o
   deshabilitar la tarea durante la prueba.

---

## 5. Cómo se deshace

| Quiero… | Se hace con |
|---|---|
| que el Core **no** arranque solo | `Disable-ScheduledTask -TaskName "KUKIA_Core"` (el vigilante avisará si el puerto queda libre) |
| que el vigilante **no** pida la tarea | `vigilar_si_puerto_libre: false` en `tools\vigilante_core.config.json` + `-SellarHashes` |
| volver al vigilante anterior | `git checkout <commit anterior> -- tools\vigilante_core.ps1` + `-SellarHashes` |
| volver a las tareas de antes | `powershell -File tools\instalar_tareas_core.ps1 -Revertir <carpeta con los XML>` |
| quitar la instalación | `powershell -File tools\instalar_tareas_core.ps1 -Desinstalar` |
| quitar esta rama | `git branch -D operativo-versionado-20261027` (nada más se ve afectado) |

Los XML de las tareas antes de tocarlas están en
`KUKIA_H01F02_20261027\arranque_auto_backup_20261027\` (`Watch_Core_ANTES.xml`,
`KUKIA_Core_ANTES.xml`).

---

## 6. Estado el 27/10/2026 (lo comprobado, no lo supuesto)

- `Watch_Core`: Ready, habilitada, cada 5 min, resultado 0.
- `KUKIA_Core`: Ready, habilitada, disparador al iniciar sesión con 30 s de retardo.
- Core real: PID 53844 en `127.0.0.1:8300`, `/stats` → HTTP 200.
- Sin ALERTA pendiente. `-VerificarHashes`: todo cuadra.
- Sin probar todavía: el arranque por inicio de sesión en vivo, y un tick real con el Core
  caído de verdad (matarlo a propósito). Lo demás está probado en aislado y documentado en
  `ARRANQUE_AUTOMATICO_PROBADO_20261027.md`.
