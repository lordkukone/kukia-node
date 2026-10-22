# AUDITORÍA DE SEGURIDAD — KUKIA (KUKI/KIA)

**Fecha:** 2026-10-18
**Alcance:** `coordinator/kukia_core.py`, `worker/kukia_worker.py`, `ledger/kukia_wallet.py`,
`dashboard/kukia_dash.py`, `docs/` (web publicada en lordkukone.github.io/kukia-node)
**Método:** copia del Core en puertos 8401/8402 con **copia** del ledger; 25 pruebas
ejecutadas de verdad. El Core de producción (8300) y su base de datos **no se han tocado**.

## Ficheros auditados (huellas)

| Fichero | Bytes | SHA-256 (16 primeros) |
|---|---|---|
| coordinator/kukia_core.py | 35.884 | 1efc89a98db6727a |
| worker/kukia_worker.py | 4.813 | 029b918e9228aeb7 |
| ledger/kukia_wallet.py | 7.675 | fb0cafed2a068c5a |
| dashboard/kukia_dash.py | 3.424 | 77d94838a99dc469 |
| coordinator/ledger/kukia.db | 217.088 | 037ea51a1a618bd4 |
| docs/wallet.html | 9.102 | 4a7e87317481f080 |

Python 3.12.9 · fastapi 0.128.8 · numpy 2.5.3 · cryptography 49.0.0

---

## 1. LO QUE ESTÁ BIEN (probado, no supuesto)

| Prueba | Resultado |
|---|---|
| Firma Ed25519 obligatoria para cobrar | 401 sin firma y 401 con firma inválida |
| Nodo B intenta cobrar la tarea del nodo A | **409** bloqueado |
| Firma de otra clave pero con el `nodo_id` de A | **401** |
| Firma de un mensaje y `metrica` de otro | **401** |
| Resultado falso con firma válida | `ok=false`, pago **0.0**, tarea cerrada |
| Doble pago del mismo resultado | **409** |
| Nodo sin clave registrada (EXIGIR_FIRMA_GLOBAL) | **401** |
| Token de admin ausente/incorrecto | **403** (usa `compare_digest`) |
| Path traversal `/modelos/../../coordinator/admin_token.txt` | **404** |
| Token real filtrado en `docs/` o en `kukia-node.zip` | **no aparece** |
| ACL de `ledger/wallet` | solo kuki + Administradores + SYSTEM |
| Escucha del Core | **127.0.0.1:8300** (loopback, no LAN) |

Los cimientos criptográficos están bien planteados: el Core recalcula el resultado y no
confía en el hash del minero, y la identidad del nodo va atada a su clave pública.

---

## 2. HALLAZGOS (por gravedad)

### CRÍTICO-1 · Cualquier nodo puede destruir la cola entera sin coste
**Verificado:** con un nodo registrado normal, 3 tareas creadas fueron condenadas a
`estado='fallo'` de forma **permanente** en 0,01–0,04 s cada una, con **2 peticiones por
tarea y sin calcular nada** (basta mandar un número falso firmado con la clave propia).

```
tarea be30cba79eb9 (matriz) -> ok=False pago=0.0 estado=fallo (0.04s)
tarea 0eae2542385a (matriz) -> ok=False pago=0.0 estado=fallo (0.01s)
tarea 427e6464c0c6 (matriz) -> ok=False pago=0.0 estado=fallo (0.01s)
estado de las 3 saboteadas: [('fallo', 3)]
```

**Por qué:** `estado='fallo'` es un callejón sin salida ("tarea ya cerrada con fallo, no
reintentable") y **no hay penalización**: no existe staking, ni slashing, ni resta de
reputación (la reputación solo sube: `reputacion=min(5,reputacion+0.05)`). El README
promete "staking" y "bounty 1.000 KUKI", pero los parámetros `BOUNTY` y `REF_BONUS`
están declarados y **nunca se usan**.

**Impacto:** un solo nodo malicioso vacía la red: cada tarea que toca muere. Y le cuesta
nada: no necesita GPU ni calcular.

**Arreglo:** `fallo` solo tras N intentos con nodos distintos (contador de intentos);
reparto obligatorio a ≥2 nodos para cerrar como fallo; reputación que **baje** y saldo en
riesgo (depósito); y no cerrar la tarea si el nodo no tiene buena reputación.

---

### CRÍTICO-2 · 97 tareas imposibles que queman el Core (medido)
**Verificado:** el Core ofrece `difusion_calor` a los mineros, pero `kukia_worker.py` no
implementa ese tipo: `trabajo_local()` devuelve el literal `"tarea_no_soportada"` y el Core
responde `ok=false` → la tarea muere como `fallo`, **pagando 0**.

Y el coste para el Core de intentarlo está medido (una sola hebra de CPU):

| n | pasos | ms/paso | 4000 pasos |
|---|---|---|---|
| 256 | — | 1,35 | 0,1 min |
| 512 | — | 5,68 | 0,4 min |
| 1024 | — | 24,43 | 1,6 min |
| 2048 | — | 111,11 | **7,4 min** |

Estado real de tu cola: **97 `difusion_calor` encoladas** → verificarlas todas cuesta
**≈ 530 min de CPU del Core** a cambio de 0 KIA. Es el motivo principal de que la red esté
parada (ver §3).

**Arreglo:** el worker debe implementar `difusion_calor` (es un Jacobi 2D, se traduce a
PyTorch en 6 líneas) **o** el Core no debe encolar tipos que ningún worker acepte. Añadir
un campo de tipos soportados por nodo y filtrar por él en `/tareas/pendiente`.

---

### CRÍTICO-3 · El Core calcula el resultado ANTES de autenticar (DoS)
**Verificado:** un envío a `/tareas/resultado` **sin firma** (o con firma basura) provoca
igual el recálculo completo. Medido con una tarea de 1,6 min: el envío sin firma no
respuesta en 20 s (seguía calculando), y 4 ataques simultáneos siguieron quemando CPU más
allá de los 25 s, mientras `/stats` (ruta libre) seguía respondiendo en 0,01–0,03 s — el
bucle de eventos no se cae, se **saturan los 20 núcleos** de la máquina.

Lo grave es que:
- **no hace falta firma** para dispararlo (el cálculo va antes de comprobarla),
- la tarea **no cambia de estado**, así que el atacante puede repetirlo indefinidamente,
- no hay ningún límite al tamaño de `params` (`n`, `pasos`).

**Arreglo:** autenticar y comprobar estado **antes** de calcular; tope duro a `n` y
`pasos` al crear la tarea; y verificación en cola con presupuesto de CPU (o muestreo).

---

### ALTO-4 · Robo de cola con un `nodo_id` inventado
**Verificado:** 5 peticiones con ids aleatorios inexistentes se llevaron **5 tareas** de la
cola y las dejaron colgadas:

```
cola queued 98 -> 93 | tareas asignadas a nodos inexistentes: 5
```

`/tareas/pendiente` no autentica nada. Y la recuperación de tareas abandonadas vive
**dentro de ese mismo endpoint**, así que si nadie pide trabajo, nada se recupera: en tu
base hay **41 tareas `ejecutando` con marca de asignación de hace ~50 horas**. Solo vuelven
a la cola cuando alguien llama a `pendiente` (lo comprobé: al llamar, la cola subió de 58 a
94). No hay endpoint ni token para liberarlas a mano.

**Arreglo:** exigir firma (o token de nodo) en `pendiente`; sacar la recuperación a un
temporizador/tarea de fondo; y no asignar a nodos que no existen.

---

### ALTO-5 · Claves privadas y frase semilla en `localStorage` de la web pública
`docs/wallet.html` (publicado en GitHub Pages) guarda en el navegador:

```js
localStorage.setItem('kukia_priv', privHex);
localStorage.setItem('kukia_words', JSON.stringify(words));   // las 20 palabras
```

Un XSS, una extensión del navegador o cualquiera con acceso al perfil se lleva la clave
privada **y la copia de seguridad** de golpe. Además el wallet llama a
`http://192.168.1.11:8300/saldo/...`: IP LAN hardcodeada en un sitio público (mezcla
HTTPS→HTTP, el navegador lo bloquea, y el Core **no devuelve cabeceras CORS** —
comprobado: ninguna), así que esa consulta de saldo **no puede funcionar** tal como está.

**Arreglo:** no persistir la clave en `localStorage` (derivar en memoria, o WebCrypto con
clave no exportable); usar la dirección del Core configurable; y si va a haber web, CORS
restringido a tu origen + HTTPS.

---

### MEDIO-6 · Sin validación de `Host` (DNS rebinding)
**Verificado:** el Core responde **HTTP 200** con cualquier `Host`, incluido
`sitio-malicioso.example`, y también con `Origin: https://evil.example`.

**Por qué importa:** el Core vive en loopback, pero una página abierta en tu PC puede
alcanzarlo por DNS rebinding. Como los parámetros van **en la URL** (`?tid=...&metrica=...`),
es una petición "simple" que el navegador lanza **sin preflight**: basta visitar la web
equivocada para que empiece a quemar CPU (§2, CRÍTICO-3) o a robarte tareas (§2, ALTO-4).

**Arreglo:** middleware que rechace `Host` distinto de `127.0.0.1`/`localhost` y cabecera
`Origin` presente con valor no permitido. Son 15 líneas.

---

### MEDIO-7 · El limitador comparte un solo contador entre 5 rutas
**Verificado:** 35 POST a `/registro` → 24×200 y 11×429. Y en el **mismo minuto**, una ruta
distinta (`/tareas/pendiente`) también recibió **429**.

`_rate_hist` está indexado solo por IP, no por ruta, y las 5 rutas
(`/registro`, `/nodos/registrar`, `/tareas/pendiente`, `/tareas/resultado`, `/transferir`)
comparten cubo: 30 peticiones/min **en total**. Un minero que pide trabajo y entrega
resultados se bloquea a sí mismo (cada tarea son 2 peticiones → techo real ~15 tareas/min,
y con 2 GPUs pidiendo trabajo sin pausa cuando hay cola, se agota en segundos).

**Arreglo:** un cubo por (IP, ruta) y límites más altos para `pendiente`/`resultado`.

---

### MEDIO-8 · El ledger no tiene cadena de hashes (el README dice que sí)
**Verificado:** columnas reales de `ledger`:

```
['id', 'tarea_id', 'nodo_id', 'kuki', 'motivo', 'ts']
```

El README promete "Ledger encadenado (hash de bloque) — nada se altera retroactivamente".
No existe ni hash previo ni firma del Core: es un SQLite normal y cualquiera con acceso al
fichero cambia saldos sin dejar rastro. Tampoco hay firma en los asientos.

**Arreglo:** añadir `hash_prev` + `hash` por asiento (SHA-256 de los datos + hash anterior)
y un comando de verificación de la cadena.

---

### MEDIO-9 · `params` sin límites
`/tareas/crear` acepta cualquier `n`/`pasos`. Un `n=100000` agota la memoria del Core al
recalcular. Ahora mismo lo crea solo el admin, pero `crear_auto` también escala con los
TOPS declarados por los nodos, que son **autodeclarados y sin tope** (`tops: 1e12`
infla `tops_red` y la dificultad hasta el factor 4; `vram_gb: 24` da tier 1.6).

**Arreglo:** topes duros (`n ≤ 4096`, `pasos ≤ 20000`) y validación de `vram_gb`/`tops`.

---

### MEDIO-10 · Transferencia con `monto = NaN`: 500 después de escribir en el ledger
**Verificado:** envío con `monto=NaN`, nonce y firma correctos → **HTTP 500** con

```
ValueError: Out of range float values are not JSON compliant: nan
```

pero **los asientos ya se habían confirmado** (aparecen `'transferencia salida'` y
`'transferencia entrada'` con `kuki = NULL`). `NaN` esquiva `if monto <= 0` y también
`if saldo < monto` (todas las comparaciones con NaN son falsas). No crea dinero
(SQLite guarda NaN como NULL y `SUM` lo ignora: el saldo quedó en 0), pero **corrompe el
registro y devuelve un error al cliente después de haber escrito**.

**Arreglo:** `if not math.isfinite(monto)` antes de nada.

---

### MEDIO-11 · 10 de tus 30 nodos no pueden cobrar nunca
`EXIGIR_FIRMA_GLOBAL = True` y 10 nodos se registraron sin `clave_publica` → cualquier
entrega suya es **401**. Son nodos que se quedaron sin pagar para siempre.

### MEDIO-12 · El saldo de la billetera no coincide con el del Core
`ledger/wallet/kuki_fundador_nueva.json` dice `"saldo": 0.0` mientras el ledger del Core
tiene **15.000.005,56 KIA** para esa dirección. `abonar()` nunca se llama desde el Core:
el campo `saldo` del JSON es un resto decorativo que puede llevar a conclusiones falsas.

### MEDIO-13 · PBKDF2 con sal fija global
`KUKIA_SALT = b"kukia-wallet-v1"` igual para todo el mundo (Python y navegador). Permite
tablas precalculadas sobre el par contraseña+20 palabras. El espacio de la frase (220 bits)
salva el caso, pero una sal por billetera es gratis y correcta.

### MEDIO-14 · IP LAN hardcodeada en la web publicada
`wallet.html` → `http://192.168.1.11:8300/saldo/`. Fuga tu dirección interna en un sitio
público y, además, no funcionará (mezcla de contenido + sin CORS).

### BAJO-15 · `/registro` guarda el `ref` en la columna `ip`
El `INSERT` mete `r.ref` en la columna `ip`: **la IP real del visitante nunca se guarda** y
la columna contiene códigos de referido.

### BAJO-16 · Otros
- `_rate_hist` crece por IP sin caducidad (fuga de memoria lenta).
- Si falta `cryptography`, `firmar()` degrada a un SHA-256 con la clave como sufijo: el
  worker firmaría así y el Core rechazaría todo → pérdida de trabajo silenciosa.
- `ia_onnx_cpu` **no existe en el proyecto** → `ONNXCPU = None` → `embeddings` y
  `lote_embeddings` no se pueden verificar ni pagar (400).
- El worker hace `res['kuki']` sin comprobar: con 429 lanza KeyError y solo imprime "err".
- `admin_token.txt` tiene una ACL heredada que lo deja **legible** por el grupo
  `kukipcwin11\CodexSandboxUsers` (comprobado con `icacls`).

---

## 3. POR QUÉ NO ESTÁ MINANDO (respuesta directa)

| Comprobación | Resultado |
|---|---|
| Tareas completadas en 30 s de observación | **0** |
| Último pago | **vie 16-oct 17:05** (hace ~50 h) |
| Proceso `kukia_worker.py` en marcha | **ninguno** |
| Tareas `ejecutando` con asignación de hace 50 h | **41** (nunca se recuperan solas) |
| Nodos registrados / con clave para cobrar | 30 / **20** |
| Tareas `difusion_calor` encoladas (imposibles para el worker) | **97** |
| Core (PID 11824) | vivo y ocioso, 0 % CPU |

**El Core está bien; la red está parada.** No hay minero corriendo, y cuando lo había, la
cola estaba dominada por tareas que el worker no sabe hacer: cada intento cuesta minutos de
CPU del Core, devuelve `fallo` y no paga nada. Sumado al limitador compartido (§MEDIO-7),
el minero se queda sin poder entregar resultados.

---

## 4. ORDEN DE ARREGLO RECOMENDADO

1. **Validar `Host`/`Origin`** y **autenticar `/tareas/pendiente`** (corta el DNS rebinding y
   el robo de cola). Poco código, mucho efecto.
2. **Autenticar antes de calcular** y **topes a `params`** (mata el DoS).
3. **`difusion_calor` en el worker** (o dejar de encolarla) → desbloquea las 97 tareas.
4. **Reintentos + reputación que baje + depósito** (mata el sabotaje CRÍTICO-1).
5. **Recuperación de tareas atascadas fuera de `pendiente`** y liberar las 41 actuales.
6. **Cubos de limitador por ruta.**
7. **`math.isfinite(monto)`** y **cadena de hashes en el ledger**.
8. **Wallet web: dejar de guardar claves en `localStorage`** y quitar la IP LAN.

---

## 5. REPRODUCIBILIDAD

Los scripts de prueba están en `%LOCALAPPDATA%\Temp\kukia_audit\`
(`auditoria_completa.py`, `test4.py`) y levantan una **copia** del Core en los puertos
8401/8402 con una **copia** del ledger. Comando: `python auditoria_completa.py`.
Resultado de la última ejecución: **19 pruebas, 3 fallos**, todos ellos hallazgos de este
informe (robo de cola, limitador compartido, y un caso de prueba mal construido por mí en
la transferencia de 1e12 — ese se verificó aparte con nonce y saldo insuficiente: **409
correcto**).

**Nada de esto se ha aplicado al código en producción: solo se ha auditado.**
---

# 6. ARREGLOS APLICADOS (18-10-2026, 21:00-21:10)

Todo lo de esta seccion esta **aplicado en el codigo y probado**: 34 pruebas
automaticas contra una copia, 0 fallos, y despues verificacion en produccion.

## Ficheros cambiados (con huella nueva)

| Fichero | Antes (16 primeros) | Ahora | Bytes |
|---|---|---|---|
| coordinator/kukia_core.py | 1efc89a98db6727a | **a8e61aaff84ef758** | 52.464 |
| worker/kukia_worker.py | 029b918e9228aeb7 | **54f43dfb871e4c13** | 8.593 |
| ledger/kukia_wallet.py | fb0cafed2a068c5a | **39bd14473b36887b** | 9.701 |
| docs/wallet.html | 4a7e87317481f080 | **c1bb551ecbaedf0c** | 10.638 |

**Respaldo completo (por si hay que volver atras):**
`_backup_seguridad_20261018-2040\` dentro del proyecto KUKIA, con los 4
ficheros originales y el ledger de antes de arrancar el Core nuevo.

## Que se arreglo

| # | Hallazgo | Arreglo | Prueba |
|---|---|---|---|
| 1 | Cualquier nodo condenaba tareas a `fallo` para siempre | `intentos` + `nodos_intento`: la tarea vuelve a la cola, ese nodo no la recibe otra vez, reputacion -0.5, **penalizacion de 25 KIA** (sin dejar saldo negativo) y 30 min sin tareas. Solo se cierra tras 3 fallos de 3 nodos distintos | 6.1-6.4 y 7.1 |
| 2 | 97 tareas `difusion_calor` imposibles (el worker no las sabia hacer) | `difusion_calor` implementada en el worker (rejilla numpy identica + iteracion en GPU float64) | 11.1-11.3: pagadas matriz, checksum **y difusion** |
| 3 | El Core calculaba ANTES de autenticar (DoS de CPU sin firma) | Orden nuevo: existe la tarea -> firma -> estado -> recalculo -> pago. Y ademas `n`/`pasos`/JSON acotados | 5.1-5.2: **0,00 s** en vez de 20+ s |
| 4 | Robo de cola con un `nodo_id` inventado | `/tareas/pendiente` exige firma Ed25519 sobre `pendiente:<id>:<ts>` (ts valido 5 min) y ademas el nodo existe | 3.1-3.5 (401/404) |
| 5 | Claves privadas y 20 palabras en `localStorage` de la web publica | La web ya no guarda secretos: derivan en memoria; solo se guarda la direccion (publica) y la URL del Core es configurable | grep: 0 escrituras de claves |
| 6 | Sin validar `Host`/`Origin` (DNS rebinding) | Guardia de Host/Origin: lo que no sea localhost -> 403 (ampliable con la variable `KUKIA_HOSTS`) | 1.1-1.5 |
| 7 | Un solo contador para 5 rutas (el minero se autobloqueaba) | Un cubo por ruta: `/tareas/pendiente` y `/tareas/resultado` a 300/min, rutas publicas a 30/min | 2.1: una ruta ya no bloquea a las otras |
| 8 | Ledger sin cadena (el README prometia cadena de hashes) | Cada asiento encadena `sha256(...|hash_prev)`; endpoint `/ledger/verificar` | 9.1-9.3: manipular el `.db` se detecta |
| 9 | `params` sin limites | Topes: `n` <= 4096, `pasos` <= 20000, JSON <= 64 KB, `data` <= 4096 | 4.1-4.3 |
| 10 | Transferencia con `monto=NaN` (500 despues de escribir) | `math.isfinite(monto)` -> 400 y **nada escrito** | 10.1 |
| 11 | 10 de 30 nodos sin clave no pueden cobrar | Documentado: hay que registrarlos de nuevo con clave (no se puede inventar su clave privada) | pendiente del usuario |
| 12 | Saldo local de la billetera desincronizado | `saldo_real()` pregunta al Core; el JSON queda marcado como copia local | billetera del fundador: 15.000.005,56 KIA |
| 13 | PBKDF2 con sal fija global | Billeteras NUEVAS con sal derivada de sus 20 palabras (v2). Las antiguas siguen en v1 para **no cambiarles la direccion** | v1 del fundador: direccion y clave intactas |
| 14 | IP LAN `192.168.1.11:8300` hardcodeada en la web | Campo configurable del Core (por defecto `http://127.0.0.1:8300`) | grep: no aparece |
| - | **Extra que encontro la propia prueba**: con las dos GPUs registrandose a la vez el Core devolvia **500** (`sqlite3.InterfaceError`) y una GPU se quedaba sin minar | Conexion SQLite envuelta con bloqueo + `_escritura()` (lock + `BEGIN IMMEDIATE`) en registrar / crear / registro | 3.6: 8 registros simultaneos -> 8x200 |
| - | Verificacion de `difusion_calor` a 7,4 min por tarea (97 en cola) | Recalculo de referencia en GPU float64 (se puede desactivar con `KUKIA_VERIF_GPU=0`) | 2048x4000: **3,72 s** (antes 444 s), diferencia con la CPU 2,2e-16 |

## Verificacion en produccion (ledger REAL, no copia)

| Antes (20:30) | Despues (21:07) |
|---|---|
| 0 tareas en 30 s; ultimo pago el 16-oct | **5 tareas pagadas en 30 s** |
| Ningun minero en marcha | 2 nodos: `kuki_minero[cuda:0]` (RTX 5070) y `kuki_minero[cuda:1]` (5060 Ti) |
| 41 tareas colgadas desde hacia 50 h | **0 colgadas**; la cola baja de 102 a 76 |
| 161 tareas ok | **185 tareas ok** (+24 pagadas) |
| Sin cadena de hashes | **24 asientos encadenados**, `cadena_ok: true` |
| Suministro 15.000.802,2563 | 15.001.410,744 (+608 KIA de trabajo verificado) |
| 0 penalizaciones | 0 penalizaciones (nadie ha fallado) |

Nodos del minero: reputacion 1,70 y 1,50; 14 y 10 tareas hechas; **0 castigos**;
0 errores en el log del minero.

## Lo que NO se ha tocado (y por que)

- **Los 15M del fundador**: intactos. La billetera conserva la direccion
  `KUK9E8B44CD7FE3AF178039453D3EEDF391EEC0CE` y su suministro de 15.000.005,56 KIA.
- **La base de datos no se migro a mano**: las columnas nuevas se anaden solas
  al arrancar (`ALTER TABLE ... `), sin reescribir ni borrar nada.
- **Los 10 nodos sin clave**: siguen sin poder cobrar. No se arregla desde el
  Core (no hay clave privada que usar): hay que registrarlos otra vez.
- **Staking y bounty del README**: siguen sin implementar (`REF_BONUS` y
  `BOUNTY` estan declarados y no se usan). Lo que hay ahora es reintentos +
  castigo economico + reputacion, que tapa el sabotaje.
- **La web publicada** (lordkukone.github.io) sigue sirviendo el `wallet.html`
  VIEJO hasta que se suba el cambio al repositorio.

## Como revertir

Copiar de nuevo los cuatro ficheros desde `_backup_seguridad_20261018-2040\`
a `coordinator\`, `worker\` y `ledger\` (y `wallet.html` a `docs\`).
Ojo: el ledger nuevo ya lleva los asientos encadenados; volver al Core viejo no
los borra, simplemente deja de comprobarlos.

## Pendiente util para el proximo dia

1. **Crear tareas automaticamente**: cuando la cola se vacia, el minero se queda
   esperando. Hoy hay que crear trabajo a mano (`/tareas/crear_auto`).
2. **`ia_onnx_cpu` no existe** en el proyecto: `embeddings` y
   `lote_embeddings` no se pueden verificar ni pagar (respuesta 400).
3. **Panel**: muestra las penalizaciones con signo "+" (formato).
4. **El Core sigue en loopback y sin HTTPS**: es lo correcto en pruebas; nada de
   esto vale si se expone a Internet.
---

# 7. LA RED NO SE PARA: GENERADOR DE TRABAJO (18-10-2026, 21:20-21:45)

## El problema que aparecio al arreglar lo demas

Con el minero ya funcionando, la cola bajo de 102 a **0** y la red se quedo
quieta: el minero no inventa trabajo, solo pide el que hay. Ultimo pago, otra
vez, a las 21:10. Un arreglo de seguridad no sirve de nada si la red se apaga
sola en cuanto termina la cola.

## Lo que se anadio

- **Generador automatico en el Core**: mantiene la cola con trabajo. Por defecto
  hasta **120 tareas en cola**, hasta **40 nuevas cada 10 s**, mezcla de
  `matriz`, `checksum`, `inferencia` y `difusion_calor` (todas con el tamano que
  toca segun los TOPS de la red). Van marcadas con `campana='auto'` para poder
  distinguirlas.
- **Recuperacion en hilo propio** (ya estaba de la seccion 6): cada 60 s
  devuelve a la cola lo que se quede colgado, sin depender de que alguien pida
  trabajo.
- **Interruptor de admin**: `POST /admin/auto_tareas?activo=1&objetivo=120`
  (sin token -> 403).
- **Topes mas altos** en `/tareas/pendiente` y `/tareas/resumen`/`resultado`
  (300 -> **1200/min**): con 2 GPUs el minero entrega ~240 tareas/min y se
  autofrenaba en los picos. Siguen exiguiendo firma, asi que el tope solo frena
  un bucle desbocado.

## Prueba (copia, puerto 8405)

| Prueba | Resultado |
|---|---|
| Cola vacia -> se rellena sola | **0 -> 12 en 6 s** |
| Variedad de trabajo | 4 tipos: matriz, checksum, inferencia, difusion |
| Marcadas como automaticas | 12 de 12 con `campana='auto'` |
| Apagar el generador por admin | cola se queda en **0** (no genera mas) |
| Volver a encender | cola vuelve a **12** |
| Sin token de admin | **403** |
| Minero real consume lo generado | **10 tareas pagadas en GPU**, 4 tipos distintos |
| Cadena del ledger con todo el trafico | `cadena_ok: true` |
| Nodos castigados / penalizaciones | **0 / 0** |

Resultado global de esa tanda: **13 de 14**. El unico fallo era la metrica de mi
propia prueba (contaba el crecimiento neto de la cola en vez de las creadas), no
el generador. Al corregir la metrica, la repeticion **no se pudo completar
porque el entorno mato el servidor de la copia** a mitad de la prueba: sin
volcado y sin un solo error en el registro de Windows, es decir un cierre
externo, no un fallo del codigo. Por eso la evidencia final del generador es la
de produccion, que es la que sigue.

## Estado en produccion (ledger REAL)

| Medida | Valor |
|---|---|
| Tareas verificadas y pagadas | **1.253** (eran 161 antes de la auditoria) |
| Ritmo | **232 tareas en 60 s** (~4/s entre las dos GPUs) |
| Cola | se mantiene llena sola (picos de 120, el minero la vacia y se repone) |
| Trabajo por tipo | matriz 383, checksum 346, difusion 410, inferencia 114 |
| Cadena del ledger | **1.092 asientos encadenados**, `cadena_ok: true` |
| Suministro | 15.009.827,67 KIA de 100.000.000 |
| Nodos mineros | `kuki_minero[cuda:0]` (5070) y `[cuda:1]` (5060 Ti), reputacion **5,0** |
| Nodos castigados / penalizaciones | **0 / 0** |
| Limitaciones (429) en el minero | **0** (las 3 lineas con "429" son digitos dentro de premios) |

## El premio por tarea baja, y es lo correcto

Al arrancar la red entera, el pago paso de **26,68 a 3,5 KIA** por tarea. No es
un fallo: el Core calcula `objetivo = restante / tareas_restantes` para agotar
los 100M justo el 13-04-2150, con techo y suelo por halving. Cuantas mas tareas
por segundo hace la red, menos paga cada una, y el total sigue acotado. Para
premios mas altos, menos tareas por minuto (`KUKIA_AUTO_LOTE`/`KUKIA_AUTO_SEG`).

## Gestion de los servicios

Documentado en el propio proyecto: `COMO_ARRANCAR_Y_PARAR.md` (PIDs, comandos,
variables de entorno y el aviso de que matar el `shell` **no** mata al `python`
hijo: hoy eso dejo un Core viejo escuchando en el 8300 y el siguiente arranque
fallo con "address already in use" mientras el viejo seguia sirviendo).

## Lo que sigue sin estar hecho

1. **Los 10 nodos sin clave publica** siguen sin poder cobrar: hay que
   registrarlos de nuevo con clave.
2. **La web publicada** (lordkukone.github.io) sirve el `wallet.html` viejo
   hasta que se suba el cambio al repositorio.
3. **`ia_onnx_cpu` no existe** en el proyecto: `embeddings` y
   `lote_embeddings` no se pueden verificar ni pagar (400).
4. **Las tareas son pequeñas** (milisegundos): la GPU mina pero no esta al 100 %
   de uso. Es un flujo de trabajo verificado, no computo pesado tipo GRID. Si se
   quieren tareas que expriman la GPU, hay que subir los tamaños en
   `params_auto()`.
5. **Sin HTTPS y solo en loopback**: es lo correcto en pruebas, y nada de esto
   vale si el Core se expone a Internet.
---

# 8. ESTADO FINAL (18-10-2026, 22:00) — medido, no estimado

| Medida | Antes de la auditoria (20:30) | Ahora |
|---|---|---|
| Minando | **no** (0 tareas, ultimo pago 16-oct) | **si, sin interrupcion** |
| Tareas verificadas y pagadas | 161 | **2.526** (y subiendo) |
| Ritmo | 0 | **457 tareas/minuto** entre las dos GPUs |
| Cola | 41 tareas colgadas 50 h + 102 sin usar | **103 -> 69** repuesta sola |
| Trabajo que la red sabe hacer | 3 tipos (y uno de ellos, difusion, **imposible**) | **4 tipos**, todos pagados |
| Cadena del ledger | no existia | **2.822 asientos encadenados**, `cadena_ok: true` |
| Nodos mineros | ninguno vivo | 5070 y 5060 Ti, reputacion **5,0**, 0 castigos |
| Penalizaciones / sabotajes | sin defensa | defensa activa, **0 casos** |
| Suministro | 15.000.802,26 KIA | 15.011.660,18 KIA |
| Fallos de seguridad graves abiertos | 4 criticos/altos | **0** |

## Servicios en marcha

| Que | PID | Donde |
|---|---|---|
| Core | **28448** (escucha en 127.0.0.1:8300) — *reiniciado a **47300** en el apartado 9* | `coordinator\`, log en `coordinator\core.log` |
| Minero | **48608** | `worker\`, log en `worker\minero.log`

> **18-10-2026 22:10:** esta foto es de las 22:00. El Core se reinicio despues
> con el archivado (PID **47300**) y el minero siguio sin parar. Ver apartado 9.

Comandos y variables en `COMO_ARRANCAR_Y_PARAR.md` (tambien en el CEREBRO).

## Dos avisos tecnicos que conviene tener presentes

1. **El premio por tarea baja cuanto mas rapido mina la red**: 26,7 -> 1,5 KIA
   en esta sesion. Es el diseno (agotar los 100M justo el 13-04-2150), no un
   fallo: el total emitido no cambia, solo se reparte entre mas tareas. Si
   quieres premios mas altos por tarea, mina menos tareas por minuto
   (`KUKIA_AUTO_LOTE`, `KUKIA_AUTO_SEG`).
2. **La tabla `tareas` crece rapido**: a 457 tareas/min son unos 650.000
   registros al dia (hoy ya hay 3.074). Se han creado indices
   (`idx_tareas_estado`, `idx_tareas_cola`, `idx_tareas_nodo`, `idx_tareas_campana`,
   `idx_tareas_creado`, `idx_ledger_nodo`, `idx_ledger_motivo`) para que las
   consultas de cola y saldos sigan siendo instantaneas, pero si esto va a estar
   semanas conviene archivar o purgar las tareas `ok` antiguas (el ledger, que es
   donde estan los saldos, **no** se toca).

---

# 9. ARCHIVADO DE TAREAS (18-10-2026, 21:45-22:10) — la base ya no crece sin freno

## El problema (medido)

A ~400 tareas/minuto la tabla `tareas` mete **~575.000 filas al dia** (0,8 KB por
fila: ~450 MB/dia). En pocas semanas el `.db` seria de decenas de GB, las copias
de seguridad serian inviables y el arranque del Core cada vez mas lento.

## El detalle que casi lo convierte en un fallo de dinero

El premio por tarea sale de `tasa_tareas_seg()`, y esa funcion **contaba filas**
de la tabla:

```python
n = COUNT(*) FROM tareas WHERE estado='ok'
primera = MIN(fin)   # ...de las que quedan
```

Si se borran tareas viejas, `n` baja y `primera` se desplaza. Caso peligroso:
la red se para unas horas, la ventana de retencion solo tiene tareas recientes y
el ritmo medido se hunde -> `objetivo = restante/(tasa*seg_rest)` se dispara ->
el pago pega contra el **techo x10 = 100 KIA por tarea**. Es decir: archivar
habria multiplicado por 100 lo que se paga por tarea y habria agotado el
suministro muy por delante de 2150. Nunca se llego a activar asi, pero era la
trampa.

## Lo que se hizo

1. **Contadores historicos en `meta`** (nuevo, tabla clave/valor):
   `tareas_ok_total`, `primera_ok_epoch`, `tareas_archivadas`. Se rellenan una
   vez desde la base actual y se incrementan **dentro de la misma transaccion**
   del pago (no se pueden desincronizar).
2. **`tasa_tareas_seg()` usa el contador**, no las filas: el ritmo de emision ya
   no depende de lo que se guarde o se borre.
3. **Archivado automatico** (`_bucle_archivado`): cada 120 s borra, en lotes de
   20.000 y bajo el lock de escritura, las tareas **terminadas** (`ok`/`fallo`)
   mas antiguas, dejando siempre las ultimas **200.000**. El ledger, los saldos,
   los nodos y la cola no se tocan. SQLite reutiliza las paginas libres, asi que
   el fichero deja de crecer al llegar al tamano de esa ventana.
4. **`POST /admin/archivar`** (con token) para forzar una pasada, y `/stats`
   expone `tareas_ok` (historico), `tareas_ok_en_tabla`, `tareas_archivadas` y
   la configuracion del archivado.

Variables: `KUKIA_ARCHIVAR` (1/0), `KUKIA_RETENCION_TAREAS` (200000),
`KUKIA_ARCHIVAR_LOTE` (20000), `KUKIA_ARCHIVAR_SEG` (120).

## Prueba en copia (puerto 8405, ledger copiado)

| Comprobacion | Antes | Despues de borrar 3.895 tareas |
|---|---|---|
| Historico `tareas_ok` | 4.888 | **4.888** (intacto) |
| Filas en `tareas` | 4.996 | 1.101 |
| Suministro | 15.015.856,39 KIA | **15.015.856,39 KIA** |
| Premio por tarea | 1,0 KIA | **1,0 KIA** |
| Cola pendiente | 81 | **81** |
| Ledger | 4.902 asientos, cadena OK | **4.902 asientos, cadena OK** |
| Segunda pasada | — | 0 borradas (no hay nada que borrar) |

Y el detalle que demuestra que el contador hacia falta, con la misma base:

| Formula | Ritmo medido | Objetivo por tarea |
|---|---|---|
| Contando filas (la vieja) | 24.000 tareas/dia | 0,08 KIA |
| Contador `meta` (la nueva) | 2.084 tareas/dia | 0,90 KIA |

Las dos cifras son la misma red en el mismo instante: la vieja mide solo la
ventana que queda guardada, la nueva mide la historia real.

## En produccion

- Core reiniciado con el codigo nuevo: **PID 47300**, `127.0.0.1:8300`.
- Minero intacto (**PID 48608**, 2 GPUs): no se paro, solo espero los segundos
  del reinicio.
- Tras 25 s: **+167 tareas** (~400/min), cola 98 repuesta sola, ledger con
  **5.736 asientos**, `cadena_ok: true`, suministro 15.016.689,39 KIA.
- Primer archivado real cuando la tabla pase de 200.000 terminadas (~8 h a este
  ritmo). Bajarlo: `KUKIA_RETENCION_TAREAS=50000` (~30 MB de base).
- Respaldo previo al cambio: `_backup_archivado_20261018-2147\` (codigo, `.db` y
  guia de antes).
