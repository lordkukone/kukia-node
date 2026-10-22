# Auditoría de fallos KUKIA — 20-10-2026

Segunda revisión del día, centrada en **fallos reales** (lógica, seguridad,
disponibilidad y abuso económico), no en cosmética. Todo lo que se afirma aquí
se ejecutó contra el sistema o contra una **copia** del ledger; las cifras son
las del momento de la revisión.

- **Alcance**: `coordinator/kukia_core.py`, `worker/kukia_worker.py`,
  `ledger/sellos.py`, `ledger/kukia_wallet.py`, `dashboard/kukia_dash.py`,
  `publico/relay.js`, `publico/publicar_metricas.py`, `docs/wallet.html`,
  `docs/sw.js`, `validator/kukia_validator.py` y los datos reales.
- **Método**: lectura completa del código, pruebas de integración aisladas,
  sondas HTTP contra el Core, un Core falso para probar el panel en un
  navegador real, mediciones de coste y revisión de la base y los sellos.
- **Límite**: auditoría interna, no independiente. Sin fuzzing, sin pruebas de
  caos, sin revisión criptográfica formal y sin pentest externo.

## Resumen

No hay ningún fallo **P0** abierto (nada que permita crear dinero, robar saldo
o pagar dos veces). Había un fallo **P1 de lógica económica** que anulaba todos
los castigos, un **XSS almacenado** en el panel y un **ataque de dificultad a
coste cero**; los tres están corregidos y con prueba. Quedan dos P1 abiertos
que no rompen el dinero pero sí la confianza o la disponibilidad.

| # | Sev. | Hallazgo | Estado |
|---|---|---|---|
| 1 | P1 | El castigo por resultado incorrecto se cobraba al id del nodo, no a su cuenta de cobro: **nunca se aplicaba** | **Corregido hoy** |
| 2 | P1 | XSS almacenado en el panel con el nombre del nodo y la telemetría (`innerHTML`) | **Corregido hoy** |
| 3 | P1 | La dificultad sumaba TOPS autodeclarados con solo pedir trabajo (gratis): inflar la red costaba 0 | **Corregido hoy** |
| 4 | P1 | `lote_embeddings` solo comprobaba 3 de 256 vectores y el minero sabía cuáles (semilla pública) | **Corregido hoy (2.ª pasada)** |
| 5 | P1 | Un POST minúsculo con resultado falso obliga a recalcular la tarea entera (~32 s de CPU en el peor caso medido) | **Corregido hoy (2.ª pasada)** |
| 6 | P2 | La clave del sello vive en el mismo disco que la base: quien copie el PC puede reescribir historial **y** firmarlo | Ancla publicada y verificada; clave única (multisig diseñado) |
| 7 | P2 | Registro abierto sin prueba de posesión de la clave; `/nodos` devuelve todas las filas | **Corregido hoy (2.ª pasada)** |
| 8 | P2 | Cualquier nodo registrado puede quedarse tareas 30 min; con nodos de usar y tirar se atasca la cola | **Cerrado (4 tareas/nodo, cupo por IP/clave y poda a 6 h)** |
| 9 | P2 | El anti-replay de `pendiente` vive en memoria: reiniciar el Core lo vacía | **Corregido hoy (2.ª pasada)** |
| 10 | P2 | El relé lee el cuerpo entero antes de comprobar tamaño y recorta saldos a 2.000 sin decirlo | **Corregido, desplegado y verificado** |
| 11 | P3 | Permisos `0600` no crean una ACL en Windows (el perfil de usuario sí aísla) | Mitigado / aceptado |
| 12 | P3 | Tolerancias de verificación vienen en `params`; hoy solo las fija el admin, pero no hay tope | **Corregido hoy** |
| 13 | P3 | La billetera pinta con `innerHTML` el `detail` del Core y la dirección tecleada | **Corregido hoy** |
| 14 | P3 | El resultado del minero viaja en la query string (límite de URL y firmas en logs) | **Corregido** (`/tareas/resultado_json`) |
| 15 | P3 | `/stats` y `/saldos` recorren el ledger entero en cada llamada | **Corregido** (totales materializados) |
| 16 | P3 | Copias de seguridad manuales | **Corregido, programado a las 03:00 y restauración probada** |
| 17 | P3 | Escala de dificultad saturada en 8,0 | Observación de ajuste |

## Segunda pasada (misma tarde): Sybil y disponibilidad

Tras la primera revisión se cerraron los hallazgos 4, 5, 7, 9, 10, 12, 13 y
16. Todo lo que sigue está en disco, probado y con el Core reiniciado.

### Registro con prueba de clave (Sybil)

`POST /nodos/reto` emite un nonce de un solo uso (TTL 5 min) y
`/nodos/registrar` exige la firma Ed25519 de
`registro:<nonce>:<ts>:<clave_publica>`. Sin la clave privada no hay id de
nodo. Se puede desactivar con `KUKIA_REGISTRO_FIRMA=0` solo para migrar.
El worker nuevo pide el reto y lo firma automáticamente; el viejo recibe un
mensaje claro (`falta reto: pide uno en /nodos/reto`).

Pruebas: sin reto → 401; firma falsa → 401; reto reutilizado → 401; con firma
correcta → 200.

### `/nodos` acotado y poda

- `GET /nodos` acepta `limite` (200 por defecto, máximo 1.000) y `activos=1`.
  El total real está en `/stats.nodos`; el panel ya lo usa.
- `GET /nodos/{id}` devuelve un nodo concreto: el worker ya no descarga la lista
  entera para reutilizar su identidad.
- `POST /admin/podar_nodos` y el bucle de archivado borran nodos con `visto=0`,
  creados hace más de 24 h y **sin asientos en el ledger** (no se toca dinero).

Pruebas: `/nodos?limite=5` devuelve 5; un nodo viejo sin trabajo desaparece y
su detalle da 404. Al arrancar, la poda borró **13 nodos viejos sin trabajo ni
asientos** (36 → 23); ningún saldo se tocó.

Además, un nodo solo puede tener **4 tareas simultáneas**
(`KUKIA_MAX_TAREAS_NODO`). Antes podía llamar a `pendiente` en bucle y
quedarse con 120 tareas durante 30 min cada una. Probado: 6 peticiones → 4
tareas, luego ninguna.

### Verificación acotada

Semáforo de 2 verificaciones simultáneas (`KUKIA_VERIF_MAX`); si no hay hueco
en 5 s, la tarea vuelve a `queued` y el nodo **no** se castiga. Así una entrega
falsa pequeña no puede lanzar 40 recálculos a la vez. El tope de 4 tareas por
nodo (arriba) evita además que un solo nodo acapare la cola.

### Embeddings y tolerancias

- `lote_embeddings` verifica **todos** los vectores (antes 3 de 256, con
  índices derivados de una semilla que el minero conocía).
- El lote se limita a 64 entradas para acotar el coste.
- `tolerancia_rel ≤ 1e-6` y `tolerancia ≤ 1e-3`; se recortan al crear la tarea.

Pruebas: un lote de 65 entradas → 400; una difusión con `tolerancia_rel=0.5`
se guarda con `1e-6`.

### Nonce persistente

`nonces_pendiente(nodo_id, nonce, ts)` con clave primaria y TTL 10 min en
SQLite. Reiniciar el Core ya no permite repetir una petición capturada.
Prueba: tras el `pendiente`, la fila existe en la copia de la base.

### Relé y ancla del sello

- `POST /api/publicar` comprueba `Content-Length` **antes** de leer el cuerpo y
  mide bytes reales (UTF-8), no caracteres.
- Los saldos se ordenan y se marca `saldos_truncado` + `saldos_total`; el
  endpoint de saldo devuelve `truncado` para no mostrar un 0 falso.
- El publicador envía el último sello (`ancla`) y el relé lo valida y guarda en
  `estado.json`; `/api/metricas` lo devuelve. Probado con un Worker simulado en
  Node: ancla hasta 88.151, 2.100 saldos → 2.000 + `truncado`, clave falsa 401.
- **No desplegado**: el relé de producción sigue con el código anterior. El
  anclaje real empieza cuando se publique `publico/relay.js` y se reinicie el
  publicador.

### Billetera web

Se escapó todo texto externo (`detail` del Core, `monto`, `nonce`, dirección y
URL del Core) antes de `innerHTML`: esa página tiene la clave privada en
memoria, así que un "Core" malicioso ya no puede inyectar código.

### Copias verificadas

`tools/backup_ledger.py` copia el `.db` con la API de SQLite, recorta los
sellos a `meta.sellos_n`, verifica cadena y firmas, escribe `manifest.json` y
conserva las últimas 14. Ejecutado en real: **18 sellos, 88.151 asientos,
15.083.452,106094 KIA, VERIFICADA**, salida `0`. Doble clic:
`COPIAR_LEDGER.cmd`.

## P1 corregidos

### 1. Los castigos no se cobraban nunca

**Qué pasaba.** En `resultado()`, cuando el resultado era incorrecto, el saldo
se consultaba con `nodo_id`:

```sql
SELECT SUM(kuki) FROM ledger WHERE nodo_id = <id del nodo>
```

Pero desde el arreglo del 19-10 los pagos van a `direccion_cobro`
(`_cuenta_de(nodo_id)`). En un minero que cobra en billetera, el saldo del id
del nodo es **siempre 0**, así que `castigo_kia = min(castigo, max(0, 0)) = 0`
y no se descontaba nada. El nodo quedaba marcado (`reputacion`, `castigado_hasta`),
pero **el castigo económico era decorativo**.

**Por qué importa.** Todo el incentivo anti-trampa descansaba en esa multa.
Un nodo podía entregar resultados basura indefinidamente sin coste.

**Arreglo** (`coordinator/kukia_core.py`): se calcula `cuenta = _cuenta_de(nodo_id)`
una vez y se usa para leer el saldo, para el asiento negativo y para el asiento
de traza de 0 KIA. Es la misma cuenta a la que se paga.

**Prueba** (`tools/pruebas_seguridad.py`): se crea una tarea, el nodo entrega
`0 * 64`, el Core responde `ok=false`, y se comprueba que existe un asiento
`penalizacion por resultado incorrecto` **a nombre de la dirección de cobro** y
que su saldo baja. Resultado real: `15.000.383,390485 → 15.000.374,912085`
(multa de 8,4785 KIA = 100 tareas).

### 2. XSS almacenado en el panel

**Qué pasaba.** El panel pinta la tabla de nodos y las tarjetas de GPU con
`innerHTML`. El `nombre` del nodo lo elige quien se registra y `vram_gb` llega
en la telemetría firmada. Un nombre como
`<img src=x onerror="...">` se ejecutaba en el navegador del administrador.

**Por qué importa.** El panel corre en `127.0.0.1:8301`, en el mismo origen
que el operador usa para mirar la red. Un nodo cualquiera (el registro es
público) podía ejecutar JavaScript en esa pestaña: desfigurar el panel, lanzar
peticiones al Core o preparar un engaño. No permite firmar transferencias (las
claves no están en esa página), pero es ejecución de código en la sesión del
administrador.

**Arreglo** (`dashboard/kukia_dash.py`): función `esc()` y se aplica a todo
texto que venga del Core antes de entrar en HTML: nombre del nodo, `device`,
`vram_gb` (además se fuerza a número), `nodo_id` y `motivo` del ledger.

**Prueba real en navegador.** Se levantó un Core falso en `127.0.0.1:8399` que
devuelve `nombre = <img src=x onerror="window.__xss_nombre=1">` y
`vram_gb = <img src=y onerror="window.__xss_vram=1">`, y un panel en
`127.0.0.1:8398` apuntando a él. En el navegador:

```json
{"xssNombre": false, "xssVram": false, "imgs": 0,
 "textoNodo": "<img src=x one…=1\">"}
```

El payload se ve como **texto**, no se crea ningún `<img>` y no se ejecuta
ningún `onerror`.

### 3. Inflar la dificultad de toda la red costaba cero

**Qué pasaba.** `tops_red()` sumaba los TOPS de los nodos con `visto` en 24 h.
`visto` se actualizaba con solo llamar a `/tareas/pendiente`, que es gratis y
no exige haber completado nada. Registrarse es público (30/min). Con ~300
nodos falsos de `tops=100.000` se saturaba el factor de dificultad (tope 4×) y
las tareas de **todos** crecían hasta 3× (matriz) y hasta el techo de difusión.

**Arreglo** (`coordinator/kukia_core.py`, `tops_red`): solo cuentan los nodos
con `visto` reciente **y** `tareas_ok > 0`, es decir, con al menos una tarea
pagada. Un nodo nuevo no cuenta hasta que demuestra trabajo; un nodo fantasma
no cuenta nunca.

**Prueba** (`tools/pruebas_seguridad.py`): con el nodo de prueba (`tops=300`)
registrado y habiendo pedido trabajo, `tops_red` sigue en **200** (los dos
mineros reales); tras cobrar su primera tarea sube a **500**. Las dos
comprobaciones pasan.

## P1 abiertos

> **Actualización:** los apartados 4 y 5 se corrigieron en la segunda pasada
> (ver arriba). Se conserva el análisis original como registro.

### 4. `lote_embeddings`: spot-check predecible

`_verificar_embeddings()` comprueba solo **3 de hasta 256** vectores del lote.
Los índices salen de `random.Random(params["seed"])`, y `seed` viaja en la
tarea: el minero puede calcular **exactamente** esos 3 vectores y devolver
basura en los otros 253. Con un lote de 256, la probabilidad de que un tramposo
pase si se eligieran índices secretos sigue siendo alta (≈ 96 % con 3
muestras), así que cambiar la semilla no basta.

**Riesgo actual:** bajo, porque el worker no implementa `embeddings` y el
generador automático no crea tareas de ese tipo. **Riesgo al activarlas:** alto
(soundness del pago).

**Propuesta** (elegir una):

1. **Verificar todos** los vectores para lotes ≤ 16 y, para lotes grandes,
   compromiso tipo Merkle: el minero publica la raíz, el Core pide `k` hojas
   con un reto aleatorio **secreto** y, si falla una, no cobra y se marca el
   intento. El pago se libera al completar el reto.
2. O limitar `lote_embeddings` a un tamaño que permita verificar el 100 %.

Criterio de aceptación: un test que devuelve 3 vectores correctos y 253
aleatorios **no cobra**.

### 5. Amplificación de verificación (DoS de CPU/GPU)

Cualquier nodo registrado puede pedir una tarea pesada y entregar un número
falso: el Core **recalcula** antes de saber que es falso. Medido en esta máquina
(CPU, sin GPU):

| Tarea | Coste de verificación |
|---|---|
| difusión `n=256, pasos=1500` | 1,08 s |
| difusión `n=1024, pasos=100` | 1,68 s (extrapolado: ≈ **32 s** a 1.907 pasos) |
| matriz `n=2048` | 0,062 s (BLAS; no es el problema) |

Un POST de ~200 bytes puede costar ~32 s de CPU si la verificación cae a CPU
(por ejemplo, GPU ocupada o sin CUDA). FastAPI atiende hasta 40 hilos: 40
peticiones simultáneas saturan la máquina durante medio minuto y bloquean a los
mineros honestos. Con GPU el coste baja a ~1 s, pero también compite con el
minero por la misma tarjeta y puede agotar memoria si se lanzan muchas a la vez.

**Propuesta:**

- Semáforo de verificación con límite configurable (p. ej. 2) y **cola con
  timeout**: si no hay hueco, responder 503 y devolver la tarea a `queued` sin
  contarla como fallo.
- Presupuesto de fallos por nodo antes de verificar (p. ej. tras 3 resultados
  falsos en 10 min, verificar en diferido o exigir depósito).
- Fijar `torch` a un solo dispositivo y liberar caché; nunca caer a CPU sin
  timeout para tareas grandes (o bajar `n` automáticamente).
- Un límite de operaciones de verificación por minuto, separado del de entrega.

## P2 abiertos

> **Actualización:** 7, 9, 10, 14, 15 y 16 están corregidos y verificados; el 8
> quedó cerrado con cupo por IP/clave y poda a las 6 h; el 6 tiene el ancla
> publicada, aunque la clave sigue en el mismo disco (multisig diseñado).

### 6. Anclaje externo del historial (parcial)

Los sellos Ed25519 detectan una edición **si el atacante no tiene la clave**.
La clave está en `coordinator/sello_key.txt`, en el mismo PC que la base. Quien
copie el disco puede reescribir el ledger, regenerar la cadena y firmar sellos
nuevos. Lo único que lo impide es que alguien tenga una copia **anterior** del
sello fuera de la máquina.

**Hecho:** el relé público guarda `ancla` con el último sello firmado (hash,
asientos, suma y `legado`) y la web lo muestra. Comprobado contra producción:
firma Ed25519 OK y encadena con el sello local 88.151. **Siguiente nivel:**
OpenTimestamps/Bitcoin (para no depender del mismo dueño que el Core) y
multisig 2-de-3 para la clave (`MULTISIG.md`).

### 7. Registro y crecimiento de nodos

- `POST /nodos/registrar` no prueba que quien se registra tenga la clave
  privada de `clave_publica`. No permite robar (las firmas fallan), pero sí
  suplantar identidad, ensuciar el panel y preparar sybils.
- No hay poda: cada registro crea una fila para siempre.
- `GET /nodos` devuelve **todas** las filas: 36 nodos = 11,6 KB, ~322 B/nodo.
  A 30 registros/min (43.200/día) son ~14 MB por consulta; el panel consulta
  cada 2 s. Es un DoS de memoria/ancho de banda trivial.

**Propuesta:** reto firmado en el registro (el Core da un nonce, el nodo lo
firma), `/nodos?limite=` con paginación y filtro `activos=1`, y poda de nodos
con `visto=0` y `creado` > 24 h. Añadir un tope de nodos activos por clave/IP.

### 8. Atasco de cola con nodos de usar y tirar

Un nodo puede pedir una tarea y no entregarla: sigue `ejecutando` 30 min
(`ABANDONO_SEG`), y tras 3 abandonos la tarea se cierra como fallo. Con N nodos
gratis se pueden inmovilizar N tareas a la vez durante 30 min. El minero
honesto no pierde dinero, pero la red se queda sin trabajo útil.

**Propuesta:** límite de tareas simultáneas por IP y por clave (hoy 1 por nodo,
pero los nodos son gratis), vencimiento más corto (2–5 min) para nodos sin
historial, y subirlo con reputación. Un depósito mínimo (o exigir 1 tarea
completada antes de optar a las pesadas) encarece el ataque.

### 9. Anti-replay en memoria

`_nonces_pendiente` es un diccionario en RAM (TTL 600 s). Un reinicio del Core
lo vacía; una petición capturada **antes** del reinicio se podría repetir dentro
de su ventana de 5 min. También crece hasta 100.000 entradas antes de limpiar.

**Propuesta:** tabla `nonces(nodo_id, nonce, ts)` con índice único y borrado
periódico, o firmar con un contador monótono por nodo. Documentado en
`SEGURIDAD.md`; no se arregla hoy para no añadir una escritura a cada petición
de trabajo sin medir el coste.

### 10. Relé

- `POST /api/publicar` hace `await req.text()` y **después** comprueba
  `length > MAX_BYTES`. Un cuerpo enorme se carga en memoria antes de
  rechazarlo. Añadir comprobación de `Content-Length` (y rechazar si falta o es
  absurdo) antes de leer.
- `limpiaSaldos` corta a 2.000 direcciones sin avisar; el orden de un objeto
  JSON no es estable. Si algún día hay más, unos saldos desaparecen del relé y
  la web muestra 0. Devolver `truncado: true` y ordenar por saldo.
- No hay límite de tasa en `/api/publicar`, aunque exige la clave.

## P3 / observaciones

11. **Permisos en Windows.** `os.chmod(0600)` no crea una ACL en NTFS; el
    aislamiento real es la ACL del perfil (`kuki`). Ahora se aplica también al
    **reutilizar** `admin_token.txt` y `sello_key.txt` (antes solo al crearlos),
    lo que sí protege si el proyecto se copia a Linux/macOS. Comprobado con
    `ls` en Git Bash: sigue mostrando `644` en Windows; no es un fallo de la
    corrección, es cómo mapea MSYS.
12. **Tolerancias.** `tolerancia_rel` (difusión) y `tolerancia` (embeddings)
    vienen en `params`. Hoy solo el admin crea tareas, pero una tolerancia
    enorme haría la verificación decorativa. Poner un máximo (p. ej. `1e-6`
    para difusión y `1e-3` para embeddings) y validarlo en `validar_params`.
13. **Billetera web.** `pintarSaldo()` y los errores de `/transferir` se pintan
    con `innerHTML`: el `j.detail` de un Core malicioso (si el usuario apunta la
    billetera a uno) podría inyectar HTML. Es auto-XSS, no afecta a otros
    usuarios, pero se elimina usando `textContent`. Igual con la dirección
    tecleada. Añadir CSP y SRI a los scripts locales.
14. **Resultado en la URL.** El worker manda `metrica`, `telemetria` y `firma`
    como query string. Con los tipos actuales son pequeños, pero un proxy con
    límite de URL (Cloudflare, 8 KB) rompería `embeddings`. Pasar todo a un
    cuerpo JSON firmado (la firma ya cubre `tid:metrica`).
15. **Coste de lectura.** `/stats` hace `SUM(kuki)` sobre 88.151 filas y
    `/saldos` un `GROUP BY` completo; el panel los pide cada 2 s (con caché de
    2 s). Hoy es rápido (11,6 KB de nodos, 720 B de saldos), pero crece lineal.
    Mantener totales en `meta` y una tabla de saldos materializada.
16. **Copias de seguridad.** Sigue sin automatizarse. El script de pruebas ya
    sabe copiar `.db + sellos + clave` de forma coherente; reutilizarlo en una
    tarea programada y **probar una restauración** es más valioso que añadir
    otra función.
17. **La escala de dificultad está saturada.** Con la red real:
    `escala_tarea = 8,0` (el máximo), `matriz_n = 384`, `difusion_pasos = 1907`
    (el techo del presupuesto). El control ya no puede alargar las tareas para
    llegar a los 2 s objetivo: solo las topa. No rompe nada (el pago por tarea
    se ajusta solo), pero conviene decidir si se sube el presupuesto de
    operaciones o se acepta que la tarea dure ~1 s.

## Verificaciones que pasaron

| Control | Resultado |
|---|---|
| Suite de integración completa | **68/68**, salida `0` |
| Registro Sybil | sin reto 401, firma falsa 401, reto reutilizado 401, válido 200 |
| `/nodos` acotado y poda | límite 5 → 5 filas; nodo viejo podado → 404 |
| Nonce persistente | fila en `nonces_pendiente` tras el `pendiente` |
| Tolerancias y lote | `0.5 → 1e-6`; lote de 65 → 400 |
| Copia de seguridad | 18 sellos, 88.151 asientos, verificada, salida `0` |
| Copia diaria programada | tarea `KUKIA Backup Ledger`, 03:00, `LastTaskResult=0` |
| Restauración | Core aislado sobre la copia: supply idéntico, cadena y sellos OK, salida `0` |
| Ancla publicada | firma Ed25519 OK y encadena con el sello local 88.151 |
| Cupo de registro | cupo 3/h: `[200, 429, 429]` |
| Tope de cola por nodo | 6 peticiones → 4 tareas, después ninguna |
| Registro real con el worker | 200 con firma, replay 401, `/nodos/{id}` 200 |
| Relé simulado (Node) | ancla 88.151, 2.000/2.100 saldos + truncado, auth 401 |
| Cadena del ledger real | `cadena_ok: true`, 87.976 asientos encadenados |
| Sellos | 18, `sellos_ok: true`, último sello hasta el asiento 88.151 |
| Suministro | 15.083.452,106094 KIA; una transferencia **no** lo cambia |
| Castigo | se cobra a la cuenta de cobro (probado, 8,4785 KIA) |
| Dificultad | nodo sin trabajo no suma; con trabajo pagado suma sus TOPS |
| XSS del panel | payload inerte en navegador real (`imgs: 0`, sin `onerror`) |
| Firma de resultado | falsa → 401; sin firma → 401; tarea ajena → 409 |
| Anti-replay `pendiente` | nonce repetido → 401; minero antiguo sigue válido |
| Telemetría | firma falsa 401, >2 KB 400, `device` inventado 400 |
| Cuerpo HTTP | 300 KB → 413 antes de parsear |
| Transferencias | nonce repetido 409, firma/clave ajena 401, destino basura 400, saldo insuficiente 409 |
| Path traversal `/modelos` | usa `Path(nombre).name`; sin `../` |
| Inyección SQL | consultas parametrizadas en todo el Core |
| Doble pago | `BEGIN IMMEDIATE` + relectura del estado; segunda entrega → 409 |
| Techo de suministro | `min(base*tier, restante)`; no se pasa de 100 M |
| Validador | recomputa por su cuenta, firma con su clave y detecta discrepancias |

## Plan propuesto (por impacto / esfuerzo)

**Hecho en la segunda pasada:** reto de registro firmado, `/nodos` acotado y
poda, tope de 4 tareas por nodo, semáforo de verificación, embeddings
verificados al 100 %, nonce en SQLite, copias verificadas y escapado de la
billetera. Pruebas: **68/68**.

**Hecho en la tercera pasada (cierre de la tarde):**

1. **Ancla publicada** en el relé de producción, con `legado` dentro de la
   firma. Comprobación real: firma OK y encadena con el sello local 88.151.
2. **Cupo por IP/clave** (10/h, 50/día, 20/clave/día) y poda a las 6 h de
   nodos que no completan ninguna tarea pagada. Probado: `[200, 429, 429]`.
3. **Resultado por cuerpo JSON** (`/tareas/resultado_json`), usado por el
   worker; la query string queda para mineros antiguos.
4. **Totales materializados** desde el arranque (`saldos_cuenta` + `meta`).
5. **CSP + SRI** en la billetera y `textContent` en los campos que faltaban.
6. **Copia diaria programada** (tarea `KUKIA Backup Ledger`, 03:00) y
   **restauración probada** sobre un Core aislado (salida 0).

**Siguiente, por orden:**

1. **Multisig 2-de-3** de la clave del sello y límites de gasto diario. El
   diseño y la migración están en `MULTISIG.md`; no se activó porque las tres
   claves en el mismo disco no darían seguridad real.
2. **Ancla en git / OpenTimestamps** para que no dependa del mismo dueño que el
   Core.
3. **Réplica del ledger** en otra máquina/disco.
4. **Auditoría externa** y pruebas de caos (corte de luz, disco lleno, reloj).

## Cómo reproducir

```bash
# suite completa (copia el ledger, no toca la base real)
python tools/pruebas_seguridad.py

# copia verificada del ledger real (db + sellos + clave)
python tools/backup_ledger.py

# sintaxis del relé
node --check publico/relay.js

# cadena y sellos reales
curl -s http://127.0.0.1:8300/ledger/integridad
curl -s http://127.0.0.1:8300/stats
```

Para la prueba de XSS se usó un Core falso que devuelve el payload en `nombre`
y `vram_gb`, un panel apuntando a él y una comprobación en el navegador:
`document.querySelectorAll('#nodesBody img').length === 0` y
`window.__xss_nombre === undefined`.

## Límites de esta auditoría

- Es interna: no sustituye una revisión externa ni un pentest.
- No se probó con el minero arrancado (las GPU estaban idle); el rendimiento
  real de verificación con el minero ocupando las tarjetas queda por medir.
- No se hizo fuzzing de `params` ni pruebas de caos (corte de luz, disco lleno,
  reloj cambiado).
- La comprobación de permisos en Windows se hizo con `ls` (MSYS); la ACL real
  no se auditó con `icacls`.
- El relé con el ancla **no se desplegó**: se probó con un Worker simulado en
  Node, no en el borde de Cloudflare. El anclaje público no está activo.
- El worker no se probó minando contra el Core nuevo (las GPU estaban idle);
  la ruta de registro con reto se probó con firmas generadas en la suite.
