# Auditoría del 23-10-2026: qué se ha arreglado y qué no

Fecha de este informe: **23-10-2026, 21:05 (hora local)**.
Alcance: los hallazgos de la auditoría externa documental (H1–H6, P1, P2) y lo hecho
después, con medidas. Todo lo de abajo está comprobado en esta máquina; lo que no
se ha probado, se dice.

Regla que se ha seguido: **nada de borrar**. No se ha tocado el ledger, ni saldos,
ni tareas, ni sellos. Las pruebas que arrancan un Core lo hacen sobre una **copia**
en una carpeta temporal y lo comprueban al final.

---

## Resumen en una tabla

| # | Hallazgo | Estado hoy | Prueba |
|---|---|---|---|
| **H1** | El modo plano pagaba fuera de la bolsa de emisión | **Cerrado** | `prueba_bolsa_arranque.py` |
| **H2** | El código publicado, el del disco y el que corre podían no ser el mismo | **Cerrado y verificable** | `tools/huella_codigo.py` |
| **H3** | Un solo Core manda en pagos: punto único de control | **No se arregla con código** (limitación honesta, abajo) | README + este informe |
| **H4** | Reparto de la bolsa por orden de llegada | **Cerrado en el modo normal**; el modo plano (sin historial) sigue siendo FIFO a propósito | `prueba_precio_minuto.py` |
| **H5** | CPU/GPU se decidía con datos declarados | **Mitigado**: el Core no inventa el dispositivo y lo corrige con el latido firmado | `prueba_bolsa_estricta.py` |
| **H6** | `planificador_log` y `gpu_telemetria` crecían sin tope | **Cerrado**: poda por edad + tope duro, ya en marcha | `prueba_poda_telemetria.py` |
| **P1** | La documentación no distinguía probado / prometido | **Mitigado**: README con límites, `version.json` con versión del Core y hash | este informe |
| **P2** | El paquete descargable no era identificable/reproducible | **Cerrado**: `version.json` publica SHA-256 del zip y versión del Core | web `/version.json` |

---

## H1 · El modo plano ya no puede pasarse del presupuesto

**Lo que decía la auditoría**: 2,27 KIA pagados en 20 s contra ~0,44 presupuestados,
por la rama `modo != potencia`, y `cobrar_de_la_bolsa()` se comía las excepciones.

**Qué había de verdad en el código** (revisado hoy): la ruta de fuga existía y era
peor de lo que parecía al leer: con `KUKIA_PAGO_POR_FLOP=0` se pagaba
`recompensa_pago(tier)` a pelo, sin tope por minuto y sin anotarlo en la bolsa.

**Qué está hecho** (en `coordinator/kukia_core.py`):

1. **`pago_plano()`**: todo pago de calendario pasa ahora por la bolsa del minuto y
   se recorta a lo que queda. Ya no hay ninguna puerta que emita sin anotar.
2. **`_guardar_bolsa()` no silencia errores**: si no se puede escribir la bolsa, el
   pago se aborta (rollback) en vez de pagar "por si acaso".
3. **`presupuesto_del_minuto()`**: el presupuesto del minuto se congela al abrirlo.
   Antes se recalculaba en cada tarea y bajaba un pelo entre una y otra (queda menos
   por emitir cada segundo), así que la suma podía cerrar unas millonésimas por
   encima de lo publicado.
4. **`_redondear_kia()`**: los pagos se redondean **hacia abajo** a 9 decimales.
   `round()` podía subir y regalar fracciones de millonésima por tarea.

**Medido** (`tools/prueba_bolsa_arranque.py`, Core aislado con ledger nuevo):

```
presupuesto 1.311900 KIA/min
A_potencia      peor minuto 1.311900 KIA  -> dentro  (25 tareas)
B_plano         peor minuto 1.311900 KIA  -> dentro  (25 tareas)
C_plano_viejo   peor minuto 145.169512 KIA -> la fuga vieja, x118.3 mas
[prueba] todo OK
```

O sea: la fuga pagaba **118 veces más** en el mismo minuto por el mismo trabajo, y
las dos rutas de hoy pagan exactamente el presupuesto, ni un KIA más.

---

## H2 · Ya se puede demostrar qué código corre

**Lo que decía**: una revisión de `main` no demuestra qué está ejecutándose; había
381 líneas añadidas al Core sin commitear y el minero arrancado antes del último
cambio.

**Qué está hecho**:

- El Core publica ahora `/version` (y `codigo` dentro de `/stats`) con: versión
  (`0.4.3`), hora de arranque, commit del repo si está al lado y **SHA-256 de los 5
  ficheros clave** (Core, minero, billetera, sellos, guardia). Se calcula **al
  arrancar**, así que refleja el código cargado en memoria, no el que haya luego en
  disco: editar un `.py` no cambia lo que dice el proceso.
- **`tools/huella_codigo.py`** compara los tres mundos: Core vivo / disco / GitHub
  (`-g`). Sirve para cualquiera, no solo para el operador.
- El Core que estaba corriendo se arrancó a las **19:14:50**, antes de los arreglos
  de hoy. Se reinició a las **20:53:54** (la guardia lo levanta sola) y se comprobó:

```
 Core 0.4.3 · arrancado 2026-10-23 20:53:54 · commit bf89f11
OK kukia_core.py     43125b81fa1b2489  43125b81fa1b2489
OK kukia_worker.py   0024203daf1b3e18  0024203daf1b3e18
OK kukia_wallet.py   b5b9730531143d0b  b5b9730531143d0b
OK sellos.py         14279dbfb231ef7d  14279dbfb231ef7d
OK guardia.py        a8cea409dddd3fc4  a8cea409dddd3fc4
 El Core vivo ejecuta EXACTAMENTE el codigo del disco (5/5).
```

- Y todo eso está publicado en GitHub (mismo commit), así que un tercero puede bajar
  el fichero de `main`, sacarle el SHA-256 y compararlo con lo que responde el Core.

**Lo que sigue siendo verdad y hay que decirlo**: esto demuestra *qué* corre, no
*que sea justo*. Es trazabilidad, no auditoría externa.

---

## H3 · Un solo Core manda en los pagos

No es un fallo que se arregle con un parche: es la arquitectura actual. El Core
liquida, los validadores recalculan y firman atestaciones pero **no mueven saldos ni
bloquean un pago discrepante**, y si la máquina del operador cae, la red se para.

Lo que sí hay: código abierto, sellos anclados en Bitcoin (no se puede reescribir el
historial sin que se note), réplica del ledger (`REPLICA_LEDGER.md`), cadena de
hashes verificada en cada arranque (261 sellos comprobados hoy al importar) y copias
de seguridad diarias. Lo que falta para llamarlo descentralizado: consenso real entre
varios Core con liquidación, no un Core y varios comprobadores.

**Compromiso de redacción**: ni el README ni la web deben decir "descentralizado"
como si fuera Bitcoin. El README ya dice "prototipo"; este informe lo deja por
escrito con nombres y apellidos.

---

## H4 · Reparto por trabajo, no por orden de llegada

El 23-10 se cerró el **precio único del minuto**: el presupuesto del minuto se
reparte en proporción al trabajo (GFLOP) que entregue cada tarea, con el precio
fijado al empezar el minuto a partir del trabajo del minuto anterior.

**Medido** (`tools/prueba_precio_minuto.py`, copia del ledger):

```
precio 0.0015814163280265792 (fuente: media de lo ultimo que hay (hasta 6 h))
GFLOP minuto ant. 828.726 · presupuesto 1.310561 KIA/min
matriz n=400   0.128 GFLOP -> 0.000202421 KIA
matriz n=800   1.024 GFLOP -> 0.001619370 KIA
proporcion     pago x8.0000 · trabajo x8.0000
minuto completo 2.621122 KIA en 1621 tareas (tope 2.621123)
OK: precio unico, reparto proporcional y minuto dentro del fusible
```

Queda un caso FIFO **a propósito**: el arranque de una red sin ninguna medida (sin
historial de trabajo) cae a pago plano, y ahí sí manda el orden de llegada. Es el
caso de un ledger recién creado, no el de la red en marcha. Si algún día se quiere
cambiar también eso, hay que decidir de dónde sale el precio sin datos (y eso es una
decisión económica, no un parche).

---

## H5 · CPU contra GPU: lo declarado, lo comprobado y lo que falta

Lo comprobado hoy (`tools/prueba_bolsa_estricta.py`, Core aislado):

```
[ OK ] el Core no inventa el dispositivo (device='')
[ OK ] sin device, el plan B lo toma por CPU y no le da la difusion
[ OK ] latido firmado aceptado (200)
[ OK ] el Core guarda el dispositivo del latido (device='cuda:0')
[ OK ] con el latido, el mismo nodo recibe la difusion
```

Es decir: el Core no se cree la primera palabra del minero; guarda el dispositivo
del latido **firmado**, y mientras no lo tenga trata al nodo como CPU y no le manda
difusiones (que en CPU tardaban 17,3 s contra 0,8 s en GPU).

**Lo que sigue sin resolverse**: que una GPU diga que es CPU solo se castiga con
menos trabajo (no es un agujero), pero una CPU que diga que es GPU sí puede recibir
difusiones que no acaba a tiempo. Mitigación actual: verificación redundante, tiempos
y castigo por fallo. Prueba pendiente: nodos CPU **externos** de verdad, que hoy no
existen.

---

## H6 · La telemetría ya no crece sin tope

**Medido antes**: `planificador_log` 396.165 filas, `gpu_telemetria` 307.503, base de
349 MB, y solo se limpiaba al borrar un nodo entero.

**Qué está hecho**: `podar_telemetria()` (edad, 48 h por defecto + tope duro de
200.000 filas por tabla) dentro del bucle de archivado que ya existía, más
`POST /admin/podar_telemetria` para forzarla. Se puede cambiar sin tocar código con
`KUKIA_PODA_TELEMETRIA_H` y `KUKIA_PODA_TELEMETRIA_MAX`.

**Probado sobre una copia** (`tools/prueba_poda_telemetria.py`):

```
antes: {'planificador_log': 396165, 'gpu_telemetria': 307503}
poda:  {'planificador_log': 232322, 'gpu_telemetria': 143820} en 4.3 s
[ OK ] ledger igual (444954 -> 444954)
[ OK ] saldos igual ((30, 15086700.778253939) -> (30, 15086700.778253939))
[ OK ] tareas igual (200161 -> 200161)
[ OK ] tope duro: recortada a 1000 filas
[prueba] todo OK
```

**Ya aplicado en producción** (el Core nuevo lo hizo solo al arrancar):

```
[KUKIA Core] telemetria podada: {'planificador_log': 232449, 'gpu_telemetria': 143947}
planificador_log 163696 · gpu_telemetria 163537
ledger 444954 · tareas 200161 · saldos_cuenta 30 · supply 15.086.700,778253937 (sin cambios)
```

Ojo: el **fichero** sigue en 349 MB porque SQLite reutiliza el hueco; no crece más,
pero para devolver esos MB al disco hace falta un `VACUUM` (requiere parar el Core un
minuto). No se ha hecho: no era urgente y se hace cuando Kuki diga.
Disco hoy: `C:` 88 GB libres (96 % usado), `I:` 199 GB libres.

---

## P1 y P2 · La documentación y el paquete

- **`version.json`** de la web publica ahora: `v` (build), `generado`, `core`
  (versión real leída del código), `zip`, **`zip_sha256`** y una línea de cómo
  verificarlo. Hoy: `core 0.4.3`, `zip_sha256 bf48a5b2...`.
- **`kukia-node.zip.sha256`** ya existía y sigue publicándose junto al zip.
- **README**: se le añade una sección de **límites conocidos** para que nadie tenga
  que leer una auditoría para saber qué no está resuelto.
- Lo que la auditoría llama "presentar pruebas como promesas" se corrige con el
  README y este informe; la web ya dice "fase de pruebas" y "sin precio ni mercado".

---

## Lo que NO está arreglado (y no se arregla en un día)

1. **Auditoría externa real.** Todo lo de aquí lo he medido yo; una auditoría
   adversarial de terceros sigue pendiente. Es la recomendación nº4 de la propia
   auditoría y no se sustituye con tests propios.
2. **Descentralización del pago (H3).** Varios Core con liquidación es un rediseño,
   no un parche.
3. **Nodos externos independientes.** No hay ninguno todavía; sin ellos, todo lo
   medido es sobre esta máquina.
4. **`VACUUM` de la base** (recuperar los MB de la telemetría borrada).
5. **Tokens de CivitAI/HuggingFace en claro** en el cerebro (pendiente del 21-10, no
   tiene nada que ver con esta auditoría pero sigue ahí).

---

## Cómo comprobarlo cualquiera (3 comandos)

```bash
# 1. ¿Qué código corre mi Core y es el mismo que el del repo?
python tools/huella_codigo.py -g

# 2. ¿Se puede pasar la emisión de un minuto?
python tools/prueba_bolsa_arranque.py

# 3. ¿Se reparte por trabajo o por orden de llegada?
python tools/prueba_precio_minuto.py
```

Los tres leen/copian el ledger y **no modifican producción** (el 2 y el 3 comprueban
al final que el ledger real no ha cambiado).
