# KUKIA — seguridad: qué está protegido y qué no

Actualizado 20-10-2026: **la billetera ya puede gastar** (antes el saldo se
quedaba atrapado), transferencias validadas, topes de coste en el minero,
panel de operaciones con datos medidos y **validadores independientes** que
recalculan el trabajo y firman su conformidad. Sellos firmados desde el 19-10 y **ancla del ultimo sello publicada** en el
rele desde el 20-10. Nuevo: **el tier se paga por potencia demostrada** (punto
13) y **guardia** que relanza lo que se caiga (punto 14).

## Resumen honesto

"Tan seguro como Bitcoin en todo" **no se consigue con un solo servidor**. La
seguridad de Bitcoin no viene de la criptografía (KUKIA usa la misma: SHA-256 y
Ed25519), viene de que **miles de nodos independientes validan lo mismo** y de
que reescribir el pasado cuesta energía real. KUKIA hoy tiene un único Core: si
ese Core miente, nadie lo contradice.

Lo que sí se puede —y ya se ha hecho— es que **el Core no pueda mentir sin
dejar rastro comprobable**, y que el historial no se pueda reescribir a mano sin
contradecir un sello firmado. Eso es lo que hay aquí.

| Propiedad | Bitcoin | KUKIA hoy |
|---|---|---|
| Algoritmo | SHA-256 + ECDSA | SHA-256 + Ed25519 |
| Quién valida | Miles de nodos | Un Core (recalcula cada tarea) |
| Coste de reescribir el pasado | Energía de toda la red | Tener la clave del sello + el PC |
| Historial encadenado | Sí (bloques) | Sí (ledger) + sellos firmados |
| Claves del usuario | En su wallet | En su navegador, nunca en el servidor |
| Moneda única / sin fork | No (hay forks) | No (el código es libre) |
| Pago por trabajo | Mineros compiten | El Core recalcula y paga él |

## Lo que hoy está garantizado

1. **El minero no puede mentir sobre el trabajo.** Las tareas caras las
   resuelven **dos nodos distintos** y, si coinciden, el Core no recalcula;
   **1 de cada 10** se recalcula igualmente por muestreo. Las baratas sigue
   recalculandolas el Core. En cualquier caso, si el resultado no coincide el
   asiento es 0 y el nodo se lleva un castigo. Modificar el minero no da
   dinero, solo tareas fallidas (o, en el peor caso de colusion, un 10 % de
   probabilidad de caer por tarea; ver "Lo que NO esta garantizado").
   Detalle del reparto 70/30 y del techo de verificadores en el README.
2. **Nadie entrega por otro nodo.** Pedir trabajo y entregarlo exige firma
   Ed25519 del nodo (ventana de 5 minutos en la petición).
3. **El precio no lo pone el cliente.** El pago sale de la emisión del Core
   (presupuesto hacia 2150); el minero no tiene ningún campo que lo cambie.
4. **El historial no se puede editar a mano sin que se note.** Cada asiento
   encadena el hash del anterior (`/ledger/verificar`).
5. **El pasado está fijado por sellos firmados.** Cada sello guarda cuántos
   asientos había, el último hash y la suma de dinero, firmado con una clave
   Ed25519 que **no está en la base de datos** (`coordinator/sello_key.txt`).
   Si el `.db` no cuadra con el último sello, el Core **deja de pagar** al
   arrancar (503 en los asientos positivos) hasta que se revise.
6. **La billetera no guarda la clave privada.** Se deriva en memoria; en
   `localStorage` solo queda la dirección y la URL del Core.
7. **El saldo de una billetera se puede gastar.** `POST /transferir` acepta
   origen `KUK...` si llega `clave_publica` y la direccion derivada de esa
   clave coincide con el origen. La firma es Ed25519 sobre
   `transferir:<de>:<a>:<monto>:<nonce>`. No hay que registrar la billetera
   como nodo y el camino antiguo (nodo) sigue funcionando.
8. **No se puede quemar saldo por error.** El destino tiene que ser una
   direccion `KUK...` valida o un nodo existente; una cadena basura se rechaza
   con 400 antes de tocar el ledger.
9. **El nonce no se repite.** `UNIQUE(de_nodo, nonce)` dentro de la misma
   transaccion que mueve el dinero: reintentar una transferencia da 409.
10. **Cualquiera puede comprobar el trabajo sin poder tocarlo.** Un validador
    independiente pide `/red/witness` (manifiesto firmado por el Core),
    recalcula cada tarea por su cuenta y manda una atestacion Ed25519. El Core
    guarda la conformidad o las discrepancias y las publica en
    `/validador/estado`. **No paga, no mueve saldos y no bloquea pagos**: una
    discrepancia es un aviso firmado, no una orden (si bloqueara, un validador
    malicioso podria tumbar la red).
11. **El coste de una tarea esta acotado.** `n*n*pasos` de una difusion se
    recorta a `KUKIA_MAX_OPS_DIFUSION` (2e9 por defecto) y el minero entrega
    con timeout acotado y reintentos con backoff: una tarea enorme ya no
    bloquea una GPU una hora.
12. **El ultimo sello esta publicado fuera de esta maquina.** El rele
    (`https://site-ef75d1db031c418d80071d2758f3a9e5.freebuff.page/api/metricas`)
    guarda `ancla` con el sello firmado (hash, asientos, suma y `legado`).
    Reescribir el `.db`, rehacer la cadena y firmar sellos nuevos ya no basta:
    el historial nuevo no encadena con el ancla que el rele ya vio.
    Comprobacion sin confiar en el Core:
    `python publico/verificar_sellos.py --publicado <url>/api/metricas`.
13. **El tier se paga por potencia DEMOSTRADA, no declarada.** El tier salia de
    la VRAM que el propio minero decia tener (x1,3 / x1,6), asi que una CPU
    podia declarar 24 GB y cobrar la prima sin demostrar nada. Ahora el Core
    cronometra el (asignar -> entregar) **con su propio reloj**, calcula los
    FLOPs de la tarea y guarda `nodos.gflops_medido`. Con 20 muestras o mas,
    el tier declarado solo se paga si el nodo supera 0,25 GFLOPS por GB
    declarado. Calibrado con hardware real (una 5070 mide 13-14 GFLOPS con el
    reloj del Core, que incluye red: es un suelo). Sin muestras suficientes no
    se corrige nada, para no castigar a un nodo nuevo. Cada pago devuelve
    `tier_pagado` y `tier_declarado`.
14. **La red no se queda parada en silencio.** `tools/guardia.py` comprueba
    cada 30 s el Core (`/stats`), que haya pagos nuevos, y que sigan vivos
    validador, publicador y panel; relanza lo que falte sin duplicar lo que el
    usuario tenga abierto a mano. El 20-10-2026 el minero estuvo 2 h 30 min
    caido sin que nadie lo viera: esto es la respuesta a ese fallo concreto.
    Desde el 21-10-2026 la recuperacion es **rapida**: una muerte no espera
    sondeos de red (si el proceso que arranco el guardia termina, se sabe al
    instante; si nadie escucha en el puerto 8300, se sabe en menos de 2 s), y
    solo el caso "vivo pero colgado" exige 3 fallos. Medido con el Core
    matado a la fuerza: vuelve a responder en 14 s y a pagar tareas en 17 s.
15. **El sello esta atado a Bitcoin, no solo al rele.** `tools/anclar_ots.py`
    manda el sha256 del ultimo sello a tres calendarios de OpenTimestamps, que
    lo meten en un bloque de Bitcoin. Quien controle este PC y el rele ya no
    puede cambiar la fecha de un sello anclado. `--verificar` comprueba la
    raiz de Merkle contra la cabecera del bloque real. Detalle: [ANCLAJE.md](ANCLAJE.md).
16. **Reiniciar el minero ya no lo deja 30 minutos parado.** Las tareas que
    tenia 'ejecutando' el proceso muerto seguian contando en su cupo y el Core
    le respondia "limite de tareas simultaneas" a todo. Ahora el minero avisa
    en `POST /nodos/{id}/reinicio` (firma Ed25519 sobre
    `reinicio:<nodo>:<ts>`) y solo puede soltar **sus** tareas.
17. **El registro de nodos tiene cupo por IP y por clave.** El reto firmado
    hacia que crear una identidad costara una clave; ahora ademas una misma
    IP puede registrar 10 nodos/hora y 50/dia, y una clave 20/dia. Un nodo que
    pide trabajo pero no completa **ninguna** tarea pagada se poda a las 6 h
    (con sus tareas devueltas a la cola). Los mineros que ya trabajan no se
    ven afectados.
18. **La puerta publica (tunel) funciona aunque el Core se reinicie o se
    levante sobre la replica.** Hallado en el ensayo de failover del 22-10: el
    Core solo aceptaba `Host` de loopback y devolvia **403** a todo lo que
    llegara por un tunel nuevo, aunque estuviera sirviendo. Ahora los dominios
    de tunel de Cloudflare se aceptan **por sufijo**
    (`KUKIA_HOSTS_SUFIJOS=trycloudflare.com,cfargotunnel.com`) y lo demas sigue
    cortado: `evil.example` y `nottrycloudflare.com` dan 403. No reabre el DNS
    rebinding: esos nombres resuelven en Cloudflare y solo llegan aqui por
    nuestro tunel. Probado en `tools/pruebas_caos.py` (seccion F. HOST).
19. **Promocionar una copia mala ya no es silencioso.** Hallado en el ensayo
    de negativas del 22-10: el lanzador de la replica arrancaba la red sobre una
    copia de hace **dos dias** sin decir nada, aunque el estado bueno estuviera
    en `C:`; y una replica de **otra red** pasaba la verificacion porque se
    comprobaba con la clave que ella misma traia. Ahora `arrancar_replica.py`
    se niega a arrancar si en disco hay un estado mas avanzado que la replica
    (exit 7), si la replica no desciende del ultimo sello del original (exit 8)
    o si esta firmada con otra clave (exit 9). `volver_al_original.py` exige
    que la replica contenga el ultimo sello del original antes de sustituirlo
    (exit 7) y sus copias de seguridad ya no se pisan entre si. Probado en
    `tools/prueba_failover_negativas.py` (23/23), sin tocar el ledger original.

## Lo que NO está garantizado (y hay que decirlo)

- **Quien controla el PC controla la moneda.** `admin_token.txt`,
  `sello_key.txt` y `kukia.db` están en el mismo disco. No hay multisig activo,
  ni HSM, ni claves repartidas. El ancla publicada hace **detectable** una
  reescritura, no imposible si el atacante controla el PC y el rele.
- **Dos nodos coludidos pueden mentir igual y no ser vistos.** La ejecucion
  redundante detecta a un mentiroso suelto (el otro nodo dice la verdad) pero
  no a dos que devuelvan **el mismo resultado falso**: para eso esta el
  muestreo del 10 %, que convierte esa colusion en una ruleta con 1 de cada 10
  tareas recomputadas por el Core. Con dos tareas replicadas, la probabilidad
  de pasar las dos es 0,81; con diez, 0,35. No es cero: es coste.
- **Un solo Core de pagos.** Los validadores independientes ya existen y
  firman su conformidad, pero hoy corren en la misma máquina: el Core sigue
  siendo juez y parte. No hay consenso distribuido ni un segundo equipo
  independiente que pueda contradecirlo de verdad.
- **Los sellos solo valen si salen de la máquina.** El fichero
  `sellos.jsonl` vive junto a la base de datos. Un atacante con acceso total
  puede reescribir el `.db`, rehacer la cadena y **volver a firmar** sellos
  nuevos si roba `sello_key.txt`. Lo que no puede es cambiar lo que ya se
  publicó fuera: el ancla del rele y las copias conservadas. El rele tambien
  podria reescribirse si se roba su clave (`KUKIA_CLAVE`); por eso el ancla se
  puede replicar en git o en papel.
- **El fork es libre.** Cualquiera puede copiar el código y montar su "KUKIA".
  Lo que define la moneda real es a qué Core apunta la gente.
- **No hay consenso distribuido.** La "dificultad" es de la red local, no de
  miles de máquinas: no protege contra un ataque del 51 % porque no hay cadena
  que atacar, hay un servidor que verificar.

## Cómo se autentica un envío desde billetera

```
mensaje = "transferir:<direccion>:<destino>:<monto>:<nonce>"
firma   = Ed25519(clave privada de la billetera, mensaje)
POST /transferir {de_nodo, a_direccion, monto, nonce, firma, clave_publica}
```

El Core comprueba, EN ESTE ORDEN y dentro de `BEGIN IMMEDIATE`:

1. `KUK + SHA256(clave_publica)[:38] == de_nodo` (la clave es del origen).
2. La firma Ed25519 del mensaje exacto.
3. Que el nonce no se haya usado nunca para ese origen.
4. Que el origen tenga saldo suficiente; si no, ROLLBACK y 409.
5. Solo entonces escribe los dos asientos (-monto / +monto) y hace commit.

La firma se hace en el navegador (`nacl.sign.detached`). La clave privada no
sale de la pestaña; al cerrarla desaparece de memoria.

**Probado con dinero real el 22-10-2026.** Hasta hoy `verificar_moneda.py`
avisaba de que los 15 M KIA de las billeteras estaban atrapados "porque solo se
puede transferir desde un nodo". **Era falso**: el endpoint ya aceptaba el
origen KUK... Lo que fallaba era que nadie lo habia probado nunca, y el aviso
se creyo durante dias.

```bash
python tools/prueba_gasto_billetera.py --desde kuki_fundador_nueva     --a KUKE1A0EB09FD9BF49FF41B884EDBDF6DC0E3E96F --monto 0.01
```

16 comprobaciones en verde, incluidas las que importan:

- la clave privada de la billetera deriva en su propia direccion (nadie gasta el saldo de otro);
- una firma con **otra clave** se rechaza (401) y **no toca el saldo**;
- la transferencia buena se acepta (200) y cada asiento cuadra al importe exacto;
- los dos asientos **se compensan** (no se crea moneda) y quedan **encadenados** con hash;
- repetir el mismo nonce se rechaza (409).

Con dinero de verdad, no contra una copia: 0,03 KIA movidos de la billetera
fundadora a la direccion que cobra el minero. `verificar_moneda.py` ahora
informa de lo que hay (`15.073.316 KIA en 6 billeteras: gastables firmando`)
y solo marcaria fallo si hubiera saldo en una cuenta que no es nodo ni
direccion, que es lo unico que nadie podria firmar.

## Seguridad del minero (20-10-2026)

El minero ya no confía en el Core para decidir cuánto trabajo hacer:

- **Valida la tarea antes de la GPU**: tipo soportado, `n` (1..4096),
  `pasos` (1..20000), `data` (≤4096), `params` (≤64 KB) y presupuesto de
  operaciones (`n*n*pasos` y `2*n^3`). Un Core comprometido o un MITM no puede
  mandar `n=100000` para dejar la GPU sin VRAM ni colgarla.
- **Nonce anti-replay** en `pendiente`: el worker firma
  `pendiente:<nodo>:<ts>:<nonce>` y el Core rechaza el mismo nonce durante
  10 minutos. Una petición capturada ya no sirve para robar la cola.
- **Solo HTTPS fuera de loopback**: si `core_minero.txt` apunta a una IP
  remota por `http://`, el worker se niega (las firmas viajarían en claro).
  Para una prueba consciente: `KUKIA_CORE_INSEGURO=1`. En local sigue
  valiendo `http://127.0.0.1:8300`.
- **Sin redirecciones HTTP**: una redirección no puede llevarse la firma a
  otro host.
- **Límites en el Core**: cuerpo HTTP ≤256 KB, `metrica` ≤16 KB, `telemetria`
  ≤8 KB, `duracion_ms` finito y acotado, `clave_publica` Ed25519 válida.
- **El registro prueba la clave (anti-Sybil)**: `POST /nodos/reto` da un
  nonce de un solo uso y `/nodos/registrar` exige la firma Ed25519 de
  `registro:<nonce>:<ts>:<clave_publica>`. Sin la privada no hay id de nodo, así
  que crear identidades falsas deja de ser gratis. Se puede desactivar solo
  para migrar mineros antiguos con `KUKIA_REGISTRO_FIRMA=0`.
- **Un nodo no cuenta como activo solo por registrarse**: `visto=0` hasta que
  pide trabajo firmado, y para la **dificultad** hace falta al menos una tarea
  pagada en 24 h. Un nodo fantasma ya no infla los TOPS de la red.
- **`/nodos` está acotado** (200 por defecto, máximo 1.000): la lista completa
  era un DoS de memoria/ancho de banda con el registro abierto. El total real
  está en `/stats`. Los nodos que nunca trabajaron se podan a las 24 h, y los
  que pidieron trabajo pero no completaron ninguna tarea pagada, a las 6 h
  (`KUKIA_NODO_SIN_TRABAJO_H`).
- **Cupo de registro por IP y clave** (10/hora y 50/día por IP, 20/día por
  clave; `KUKIA_REGISTRO_CUPO_*`). Detras del tunel se usa `CF-Connecting-IP`
  solo si la conexion llega desde loopback; una peticion directa de la LAN no
  puede falsear su IP con esa cabecera.
- **Anti-replay persistente**: el nonce de `pendiente` vive en SQLite
  (`nonces_pendiente`) con TTL de 10 min; reiniciar el Core ya no lo olvida.
- **Máximo 4 tareas simultáneas por nodo** (`KUKIA_MAX_TAREAS_NODO`): un nodo
  podía pedir trabajo en bucle y quedarse con toda la cola 30 min. El worker de
  serie hace una tarea por GPU/nodo, así que 4 es holgado.
- **Claves locales con permisos de dueño** (`0600` en POSIX) y `.gitignore`
  para que `ledger/wallet/`, `sello_key.txt` y las bases no acaben en git.
- **GPU en vivo firmada**: el worker manda cada 3 s `POST /nodos/telemetria`
  con utilización y VRAM; la firma cubre el hash del JSON, no paga y solo
  actualiza una fila por nodo/GPU. El panel descarta latidos de más de 5 min.

Los mineros **antiguos** siguen funcionando contra el Core nuevo (mandan el
mensaje sin nonce), pero para **registrar un nodo nuevo** hace falta el worker
actualizado: el registro exige el reto firmado. Si un worker viejo apunta a
este Core, verá `falta reto: pide uno en /nodos/reto` y deberá actualizarse.

- **Verificación acotada**: como máximo 2 verificaciones caras a la vez
  (`KUKIA_VERIF_MAX`). Si no hay hueco, la tarea vuelve a la cola y el nodo no
  se castiga; así una entrega falsa de 200 bytes no puede saturar CPU/GPU.
- **Embeddings verificados al 100 %**: antes se comprobaban 3 de 256 vectores y
  el minero conocía la semilla. Ahora se verifican todos y el lote se limita a
  64 entradas.
- **Tolerancias acotadas**: `tolerancia_rel ≤ 1e-6` y `tolerancia ≤ 1e-3`; una
  tarea no puede declarar una tolerancia que haga decorativa la verificación.

## Pruebas de integración

```bash
python tools/pruebas_seguridad.py
python tools/pruebas_caos.py     # concurrencia, corte de luz y basura
```

Copia la base real con la API de backup de SQLite a una carpeta temporal,
arranca un Core aislado y comprueba 101 casos: envío desde billetera, saldo,
suministro invariante, cadena y sellos, replay, firma falsa, clave ajena,
destino basura, origen=destino, saldo insuficiente, camino antiguo de nodo,
mina una tarea checksum de principio a fin, verifica el recorte de coste de
una difusión al límite y prueba los controles del minero (clave pública
inválida, nodo no activo hasta pedir trabajo, nonce repetido, compatibilidad
sin nonce, métrica/telemetría/duración gigantes o NaN, cuerpo HTTP de 300 KB y
validación local del worker, cupo de registro en ráfaga). También prueba la
**ejecucion redundante** de punta a punta: dos nodos coinciden y el Core no
recalcula (`verificado_por=replica`) con el premio repartido 70/30, la replica
que miente se castiga, el primario que miente pierde la tarea (que se cierra
con el resultado del verificador), el castigo bloquea de verdad la peticion
de trabajo y una **difusion cara** (1,2 GFLOP, el tipo que genera la red) se
verifica entre dos nodos sin recalculo. El **muestreo del 10 %** también se
prueba: se busca un id que caiga en la muestra y se comprueba que esa tarea
**sí** se recalcula en el Core (`verificado_por=auditoria`) y que aun así el
verificador cobra su 30 %. Además arranca un
**validador independiente** en otro proceso (recalcula y firma), comprueba que el Core lo registra, que una
firma falsa se rechaza y que una discrepancia firmada queda contada como
aviso. La base real no se toca en ningún momento.

### Pruebas de caos (22-10-2026)

La bateria secuencial no puede ver lo que solo rompe cuando pasa a la vez o
cuando se corta la luz. `tools/pruebas_caos.py` (33 casos, tambien sobre una
copia y un Core aislado) comprueba lo que de verdad separa esto de una hoja de
calculo:

- **Doble gasto**: 8 transferencias simultaneas del mismo saldo. Cobra **una**,
  el saldo nunca queda negativo y el suministro no se mueve.
- **Doble pago**: el mismo resultado entregado 6 veces a la vez. Se paga una,
  se escribe **un** asiento y la tarea queda `ok`.
- **Corte de luz**: `kill -9` (como un apagon) con 40 transferencias en vuelo y
  el Core rearrancado sobre el mismo `.db`. Cadena intacta, sellos bien,
  suministro = suma de saldos y **cero medios asientos** (ningun debito sin su
  credito). Los nonces ya cobrados siguen gastados; los que no entraron se
  pueden reintentar.
- **Basura**: cuerpos malformados, tipos cambiados, NaN, `../`, token de admin
  por `/modelos` y un cuerpo gigante. Ni un solo 500.

## Validadores: qué aportan y qué no

El Core de pagos sigue siendo **uno**: dos Cores pagando a la vez serian doble
gasto, no mas seguridad. Lo que se multiplica son los verificadores.

```bash
python validator/kukia_validator.py --core http://127.0.0.1:8300
```

- Piden trabajo verificado (`GET /red/witness`), comprueban la firma del
  manifiesto, **recalculan** y firman su atestacion.
- El Core no les da acceso a claves, saldos ni ledger; no pueden pagar ni
  bloquear.
- En el **mismo PC** no son independientes (comparten CPU, disco y sistema):
  sirven para probar el circuito. La independencia real es ejecutarlos en
  **otras maquinas**, cada uno con su clave.
- Limitacion actual: el validador toma la clave publica del Core de la misma
  conexion (no hay pin). En loopback apenas importa; para Internet, usa HTTPS.

Guia y limites completos: `GUIA_VALIDADORES.md`.

## Cómo comprobarlo tú mismo

```bash
# 1) Estado en vivo
curl http://127.0.0.1:8300/ledger/integridad
curl http://127.0.0.1:8300/ledger/sellos?limite=5

# 2) Auditoría independiente contra el Core (lee el .db y pide los sellos)
python publico/verificar_sellos.py --core http://127.0.0.1:8300

# 3) Auditoría de una copia de seguridad, sin Core
python publico/verificar_sellos.py --db copia/kukia.db --sellos copia/sellos.jsonl

# 4) Copia verificada (db + sellos + clave) en backups/kukia_FECHA
python tools/backup_ledger.py

# 5) Comparar con el ancla publicada en el rele
python publico/verificar_sellos.py --publicado https://<rele>/api/metricas

# 6) Probar una restauracion de verdad (Core aislado sobre la copia)
python tools/probar_restauracion.py
```

La copia se recorta al `meta.sellos_n` de la base y se verifica antes de darla
por buena; si no cuadra, sale con error. Deja un `manifest.json` con sha256 y
conserva las últimas 14 (cambia con `--mantener`). También hay un doble clic:
`COPIAR_LEDGER.cmd`.

**Copia diaria programada:** la tarea de Windows `KUKIA Backup Ledger` ejecuta
`TAREA_BACKUP.cmd` a las 03:00 (si el PC estaba apagado, se recupera al
encender) y escribe `backups/backup.log`. Se registra con
`powershell -ExecutionPolicy Bypass -File tools\programar_backup.ps1`.

**Restauracion probada:** `python tools/probar_restauracion.py` coge la última
copia, comprueba hashes/cadena/firmas, arranca un Core aislado contra la copia
y exige el mismo suministro, cadena, sellos y saldos. Resultado real del
20-10-2026: 18 sellos, 88.151 asientos, 15.083.452,106094 KIA, diferencia
0,000000000, `saldos=26`, salida 0.

Sale con código 0 si todo cuadra y 1 si algo está alterado, indicando el asiento
y el sello que no coinciden.

## Cómo se rompe (probado en una copia, nunca en la base real)

| Ataque | Resultado |
|---|---|
| `UPDATE ledger SET kuki=...` | cadena rota; el Core bloquea pagos al arrancar |
| Cambiar un asiento y **rehacer toda la cadena** | `cadena_ok: true`, pero el sello no cuadra (hash/asientos/suma) → bloqueado |
| Borrar `sellos.jsonl` | `meta.sellos_n` dice N y hay 0 → bloqueado |
| Robar `sello_key.txt` | puede firmar sellos nuevos, pero contradice los ya publicados fuera |
| Minero modificado que miente | resultado incorrecto → 0 KIA + castigo |
| Minero que entrega por otro nodo | firma inválida → 401 |
| Fallo a mitad de una transferencia | ROLLBACK: el débito no queda sin el crédito |
| Transferencia con ledger roto | 503 antes de abrir la transacción |
| Editar un asiento del tramo anterior a la cadena | sello v2: huella `legado` no cuadra → detectado |

## Ajustes

| Variable | Por defecto | Qué hace |
|---|---|---|
| `KUKIA_SELLO_SEG` | 600 | segundos entre sellos (si hay asientos nuevos) |
| `KUKIA_SELLO_ASIENTOS` | 2000 | sella también al acumular N asientos |
| `KUKIA_SELLO_BLOQUEA` | 1 | `0` = detectar sin bloquear pagos (solo para depurar) |
| `KUKIA_MAX_OPS_DIFUSION` | 2 000 000 000 | tope de `n*n*pasos` por tarea de difusion |
| `KUKIA_REGISTRO_FIRMA` | 1 | exige reto firmado para registrar un nodo (0 solo para migrar) |
| `KUKIA_VERIF_MAX` | 2 | verificaciones caras simultaneas (resto vuelve a la cola) |
| `KUKIA_PODA_NODOS_H` | 24 | horas tras las que se poda un nodo que nunca trabajó (0 = no podar) |
| `KUKIA_MAX_TAREAS_NODO` | 4 | tareas simultáneas que puede tener un mismo nodo |
| `KUKIA_REGISTRO_CUPO_HORA` | 10 | registros nuevos por IP en 1 h (0 = sin limite) |
| `KUKIA_REGISTRO_CUPO_DIA` | 50 | registros nuevos por IP en 24 h |
| `KUKIA_REGISTRO_CUPO_CLAVE` | 20 | registros nuevos por clave publica en 24 h |
| `KUKIA_NODO_SIN_TRABAJO_H` | 6 | horas antes de podar un nodo que no completa ninguna tarea pagada |
| `KUKIA_CONFIA_PROXY` | 1 | acepta `CF-Connecting-IP` solo desde loopback (0 = ignorar) |
| `KUKIA_TIMEOUT_ENTREGA` | 900 | segundos maximos que el minero espera al Core |
| `KUKIA_DB` | `coordinator/ledger/kukia.db` | ruta de la base (solo pruebas) |

Ficheros: `coordinator/sello_key.txt` (privada, no publicar), `coordinator/sellos.jsonl`
(append-only, se puede copiar y publicar), clave pública en `/ledger/integridad`.

## Lo que faltaría para acercarse de verdad a Bitcoin

1. **Ancla en un sitio que no controle el mismo PC**: hoy esta en el rele
   (mejor que nada), pero el rele y el Core comparten dueno. Copiar el hash en
   git y, sobre todo, sellarlo con **OpenTimestamps** en Bitcoin lo haria
   imposible de cambiar sin reescribir Bitcoin. El codigo ya publica el ancla;
   falta el paso externo.
2. **Validadores en maquinas distintas**: el circuito funciona, pero hoy los
   validadores corren en el mismo PC. Con dos que discrepen de verdad, se
   detecta al mentiroso.
3. **Multisig 2-de-3 real** para la clave del sello y para los movimientos
   grandes. No se activo a proposito: guardar las 3 claves en este mismo disco
   seria teatro, no seguridad. Necesita 3 dispositivos y una ceremonia de
   claves; el diseno y la migracion estan en `MULTISIG.md`.
4. **Réplica del ledger** en otra maquina/disco, con la misma cadena.
5. **Auditoría externa** del código (cortes de luz, disco lleno y reloj ya
   cubiertos por `tools/pruebas_caos.py`; sigue faltando el disco lleno).

Nada de eso es imposible; ninguna se hace en una tarde. Lo de hoy es el paso que
convierte "confía en el Core" en "comprueba el Core".

## Sobre "más seguro que Bitcoin"

No. Y decirlo seria vender humo. Bitcoin tiene miles de validadores
independientes y un coste de reescritura que se mide en energia de toda una
red; KUKIA tiene **un** Core. Lo que si comparte es la criptografia (SHA-256 y
Ed25519), el encadenado del historial y ahora una ruta de gasto firmada sin
custodio. La diferencia no es el algoritmo: es cuanta gente valida lo mismo.
Lo honesto es decir que KUKIA es un prototipo **verificable**, no una cadena
mas segura que Bitcoin.

## Paquete descargable y claves en reposo (21-10-2026)

Hasta hoy, el minero que se descargaba guardaba en `ledger/wallet/*.json` la
clave privada Ed25519 **y las 20 palabras en claro**. Quien copiara esa carpeta
(una copia de seguridad, un USB, otro programa con acceso al perfil) se llevaba
la llave completa. Se ha cambiado:

- **Clave cifrada en reposo.** `clave_privada` y `mnemonico` se guardan cifrados
  con DPAPI de Windows (`clave_privada_cifrada`, `mnemonico_cifrado`). La clave
  queda atada a la cuenta de Windows que mina: copiar el `.json` a otro PC u
  otra cuenta **no sirve para firmar**.
- **Recuperación intacta.** Con la contraseña y las 20 palabras
  (`RECUPERACION_<nombre>.txt`) se re-deriva la misma clave en cualquier equipo
  (`recuperar_billetera`). Si el `.json` no se puede descifrar, el minero
  intenta esa ruta solo y, si tampoco, se para con un mensaje claro en vez de
  minar un nodo mudo.
- **Permisos del dueño.** Los ficheros se escriben con `chmod 600` y, en
  Windows, un `icacls` que corta la herencia: otra cuenta del mismo PC no los
  abre.
- **El paquete no lleva nada de nadie.** `kukia-node.zip` se genera desde
  `publico/generar_web.py`, que **aborta** si aparece en la lista un fichero de
  billetera, una base de datos, un log, una clave o un fichero de recuperación.
  No incluye `admin_token.txt`, `sello_key.txt`, `kukia.db` ni `ledger/wallet/`.
- **Huella publicada.** La web entrega `kukia-node.zip.sha256` y muestra el
  SHA-256 en la página de código: si el fichero descargado no da esa huella, no
  es el nuestro.

Comprobarlo tú mismo:

```
python -c "import hashlib,sys;print(hashlib.sha256(open('kukia-node.zip','rb').read()).hexdigest())"
python -c "import zipfile;print([n for n in zipfile.ZipFile('kukia-node.zip').namelist() if 'wallet' in n and n.endswith('.json')])"
```

El segundo comando debe imprimir `[]`. El primero debe coincidir con la huella
de `kukia-node.zip.sha256`.

Lo que esto **no** resuelve: si tu sesión de Windows está comprometida mientras
minas, DPAPI no protege de un atacante que ya eres tú. Contra eso solo hay
billetera de hardware o multifirma, y ninguna de las dos está hecha.

## Integridad: como se suma (corregido el 21-10-2026)

La verificacion de sellos compara hash, numero de asientos y suma **exactos**.
Para que "exacto" signifique algo, la suma que se firma y la que se verifica
tienen que calcularse igual: las dos con `SUM(...)` de SQLite (la verificacion
usa una ventana acumulada, mismo motor y mismo orden).

Antes la verificacion sumaba en Python fila a fila. Con muchos importes
pequenos, la coma flotante da diferencias de 1e-4 entre los dos caminos: el
sello 91 del ledger real salio marcado como alterado **sin que nadie hubiera
tocado nada**, y como el Core bloquea pagos cuando la integridad falla
(`KUKIA_SELLO_BLOQUEA=1`), el siguiente reinicio habria parado todos los pagos.

Regla que queda: **nunca se añade tolerancia a una comparacion de integridad**.
Si dos caminos dan distinto, el arreglo es que calculen igual, no aceptar la
diferencia. La suite lo comprueba en `prueba_sellos` (5.000 asientos pequenos:
cuadra; un asiento cambiado: se caza).

## Cierre al exterior y claves fuera del alcance del sandbox (22-10-2026)

Esta pasada no toca el ledger ni el codigo de la moneda: toca la maquina, que
es por donde se entra.

**1. Cortafuegos: 13 reglas que dejaban entrar desde la LAN a CUALQUIER puerto.**

Estaban abiertas para `pinokio.exe`, los dos `node.exe`, `opencode.exe`, los dos
`python.exe` sueltos, `cloudflared.exe`, `quantus-miner`, `keryxd`, `ipfs`,
`btqd`, `bzminer` y `ravend`. Es decir: cualquier vecino de la red podia hablar
con el RPC de un nodo de minado, con IPFS o con el escritorio de Pinokio.

Se han **desactivado, no borrado** (`Disable-NetFirewallRule`): reactivarlas es
volver a encenderlas.

```powershell
# ver que cerraria, sin tocar nada
powershell -ExecutionPolicy Bypass -File tools\cerrar_puertos_abiertos.ps1 -EnSeco
# cerrar / volver atras
powershell -ExecutionPolicy Bypass -File tools\cerrar_puertos_abiertos.ps1
powershell -ExecutionPolicy Bypass -File tools\cerrar_puertos_abiertos.ps1 -Revertir
```

No afecta a nada que funcione: el Core (8300), el panel (8301), el minero,
ComfyUI y Ollama van por **127.0.0.1**, y el cortafuegos de Windows no filtra
loopback. El script lo comprueba al terminar (pide `/stats` al Core y la portada
al panel). Despues del cambio, el minero siguio pagando: 264.356 -> 264.403
tareas en 20 s.

Se dejan **a proposito**: uTorrent (necesita entrada para sembrar), los juegos
online y sus anticheats, y todo lo de Windows.

**2. La clave del sello estaba al alcance del sandbox de Codex.**

La app de Codex crea un grupo local `CodexSandboxUsers` con lectura heredada
sobre la carpeta del usuario. En KUKIA eso llegaba a 792 ficheros, entre ellos
`coordinator\sello_key.txt` (la clave que firma los sellos),
`admin_token.txt`, las carteras de `ledger\wallet\` y las copias de
`backups\`. Un sandbox de agentes no tiene por que poder leer la llave de la
red.

Se corto la herencia y se quito esa concesion en `coordinator\`, `ledger\`,
`backups\` y las carpetas `_backup_*`. Los permisos anteriores quedaron
guardados en `backups\acl_antes_<fecha>.txt`:

```powershell
powershell -ExecutionPolicy Bypass -File tools\proteger_claves.ps1
powershell -ExecutionPolicy Bypass -File tools\proteger_claves.ps1 -Revertir
```

Ahora `coordinator\sello_key.txt` lo leen solo `kuki`, SYSTEM y
Administradores. El **codigo** del proyecto se deja legible: lo que habia que
cerrar eran las llaves.

Detalle que se aprendio a base de intentos: `icacls /remove:g "*CodexSandboxUsers"`
contesta "correcto" y **no quita nada**. Hay que dar el nombre completo
(`KUKIPCWIN11\CodexSandboxUsers`).

**3. Lo que sigue pendiente, dicho claro:**

- Los dos tokens (CivitAI y HuggingFace) siguen en claro en
  `I:\CEREBRO\CEREBRO_TECNICO.md` y sus copias en `N:`. Ese disco es de solo
  lectura para mi: hay que rotarlos desde las webs o sacarlos de ahi a mano.
- La cuenta `kuki` es administradora y no hay UAC de por medio: quien entre en
  la sesion manda. Lo de arriba cierra puertas, no convierte el PC en una caja
  fuerte.
- `I:` sigue con reglas de cortafuegos propias de Pinokio si se reinstalan
  (sus python de miniforge/miniconda ya estaban cerrados el 21-10).

## Nodos: un nombre, una identidad; prima solo con prueba (22-10-2026)

Dos agujeros que se veian en `verificar_moneda.py` y ya no:

**1. El registro estaba lleno de fantasmas.** Cada vez que un minero volvia a
registrarse (billetera nueva, o el worker pidiendo identidad) se insertaba una
fila nueva con el mismo nombre. El registro decia **34 nodos** donde habia 20,
y `minero_pc[cuda:0]` aparecia 4 veces.

- Al registrar, si ya existe ese nombre **con la misma clave publica**, se
  reutiliza su fila (se actualizan VRAM, tier, TOPS y direccion de cobro; no se
  tocan tareas, reputacion, castigos ni `visto`).
- Las filas viejas de un nombre repetido se marcan `retirado=1`. **No se
  borran**: el ledger apunta a `nodo_id` y borrarlas dejaria pagos huerfanos.
  Dejan de contar en `/stats`, en `/nodos` y en las verificaciones.
- Comprobado en vivo: reinicie el Core, los mineros se volvieron a registrar y
  **no aparecio ni una fila nueva** (20 vivos, 14 retirados, 34 en total).

**2. La prima por tier se pagaba sin demostrar nada.** El pago ya usaba
`tier_verificado`, pero con menos de 20 muestras devolvia el tier *declarado*
"para no castigar al nuevo". Ahi cabia el fraude: declarar 24 GB, cobrar x1,6 y
no entregar nunca trabajo grande.

- Ahora **sin prueba se paga 1,0**. La prima se gana con trabajo medido por el
  Core (reloj del servidor): 20 muestras y al menos 0,25 GFLOPS por GB
  declarado, que es cuatro veces por debajo de lo que mide una GPU real.
- Lo unico que pierde un minero honesto es la diferencia de sus primeros
  minutos minando.
- `verificar_moneda.py` ya no avisa de cualquier `tier > 1`: avisa solo de los
  que cobran prima **sin respaldo**, y dice a cuanto se les paga de verdad.

Estado despues del cambio: **20 nodos vivos, 0 duplicados**, pagos saliendo
(ultimo 16:45:35) y `tareas_ok` subiendo. El aviso que queda es el de siempre:
15.073.293 KIA en 6 direcciones de billetera sin ruta de gasto.

### Lo que de verdad impedia gastar desde el navegador (22-10-2026)

Probar el endpoint no bastaba: la billetera vive en la web y por el camino habia
tres cosas rotas, todas silenciosas.

1. **La web apuntaba a `http://127.0.0.1:8300`.** En el navegador de un
   visitante eso es *su* ordenador: no habia red, ni saldo, ni forma de gastar
   nada. Ahora `wallet.html` lee `core_actual.json` (el mismo mecanismo que el
   minero: `auto`) y se queda con la direccion publica viva; el campo de arriba
   y lo que el usuario tenga guardado mandan siempre. Probado en un navegador
   de verdad: la pagina sola eligio el Core publico y leyo el saldo (HTTP 200).

2. **El Core cerraba el paso al navegador (CORS).** Solo permitia loopback y
   `lordkukone.github.io`; desde `kukia-network.surge.sh` el fetch se bloqueaba
   antes de salir. Añadidos a mano los origenes del proyecto (surge, freebuff de
   la web y del sitio), y comprobado que un origen ajeno **sigue sin permiso**:

   ```
   GET  /saldo/...  Origin: https://kukia-network.surge.sh -> ACAO correcto
   OPTIONS /transferir (preflight)                          -> 200 + metodos
   GET  /saldo/...  Origin: https://sitio-malicioso.example  -> sin cabecera
   ```

   No abre nada: gastar exige firma Ed25519 de la clave que deriva en la
   direccion.

3. **El tunel publicaba una direccion muerta.** El supervisor leia la salida de
   `cloudflared` con una expresion que tambien encajaba con
   `https://api.trycloudflare.com` (el host de la API de Cloudflare, que sale
   en su propio log). Publico esa direccion en `core_actual.json`, Cloudflare
   contesto **530** y la red desaparecio de internet sin que nada avisara: ni la
   billetera ni el minero de nadie podian conectar. Ahora solo se aceptan los
   nombres de tunel rapido (cuatro palabras con guiones) y nunca `api.`.
   Comprobado tras reiniciar el supervisor: publica
   `dimension-jet-salmon-extra.trycloudflare.com` y `/stats` responde 200.
