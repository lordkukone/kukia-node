# KUKIA Quantum AI Compute Network

**Every GPU. One intelligence network.**

Red descentralizada que conecta GPUs de hogar para ejecutar trabajo de IA
real (inferencia, imagen, video, RLVR, simulacion) y recompensa el trabajo
VERIFICADO con KUKIA Coin (KIA).

## Concepto (30 segundos)

- Tu GPU libre → se registra como nodo → pide tareas → ejecuta → devuelve hash
- KUKIA Core verifica re-ejecutando la tarea (bit a bit) → si coincide, ganas KIA
- La moneda NO se mina quemando electricidad (PoW). Se ACUNA con trabajo util verificado (Proof-of-Compute)
- **Sin precio hasta que la red demuestre movimiento real verificado** (decision del creador)

## Como se mina KIA (lo que hay de verdad)

1. El Core crea la tarea con ID unico: tipo + parametros + semilla + timestamp
2. El nodo pide trabajo firmando la peticion y ejecuta en su GPU
3. Devuelve el resultado firmado con su clave Ed25519
4. El Core comprueba el resultado: si otro nodo ejecuto la misma tarea cara y
   coincide, **no recalcula** (ver "Ejecucion redundante"). Si no, recalcula
   el y compara. Si no coincide: no cobra, pierde reputacion, se le cobra una
   penalizacion (acotada a su saldo) y no puede pedir trabajo unos minutos
5. El pago se abona a su **direccion de cobro**, no al id del nodo
6. El pago va **por trabajo medido** (FLOPs x precio del minuto, ver "Pago por
   potencia"). La **prima por tier** (x1,3 / x1,6) solo se paga si se apaga el
   pago por potencia (`KUKIA_PAGO_POR_FLOP=0`), y aun así solo si la GPU la ha
   demostrado con trabajo verificado: ver "Potencia demostrada"
7. El ledger va encadenado (hash del asiento anterior) y se sella cada pocos
   minutos con una clave Ed25519 que no esta en la base de datos

Lo que **no** existe todavia y por tanto no se promete: staking con colateral
y bounty economico por cazar mentirosos. Estan como fase 2 en
[ESTRATEGIA.md](ESTRATEGIA.md), no en el codigo.

## Pago por potencia: lo que cobra cada tarea

Hasta el 22-10-2026 cobraba lo mismo un checksum (0 FLOPs) que una difusion de
20 GFLOP, y encima el tier multiplicaba por la VRAM declarada: se pagaba dos
veces por lo mismo. Desde el 23-10 manda el trabajo medido.

- **Precio unico del minuto.** El presupuesto del minuto (lo que queda por
  emitir hasta 2150 repartido entre los minutos que faltan, mas lo que sobró o
  faltó en el minuto anterior) se divide por los GFLOP/min que entregó la red
  en el minuto anterior. De ahí sale UN precio por GFLOP, igual para todas las
  tareas de ese minuto.
- **Pago de una tarea = precio x GFLOP verificados.** A 8x trabajo, 8x pago
  (medido: x8,0000 exacto). El reparto es por trabajo, no por orden de llegada.
- **El tier no multiplica** en este modo: la prima por VRAM y la prima por
  potencia eran pagar dos veces lo mismo.
- **Techo por tarea**: 10 KIA x halving. **Fusible del minuto**: 2x el
  presupuesto, solo salta si algo va muy desmadrado.
- **La bolsa del minuto se congela al abrirlo** y los pagos se redondean hacia
  abajo, así que la suma del minuto no se pasa ni un decimal. Si no se puede
  anotar la bolsa en disco, **el pago se aborta**: mejor no pagar una tarea que
  emitir fuera de presupuesto.
- **Sin suelo por defecto**: un checksum son 0 FLOPs y cobra 0 KIA. Si algún día
  interesa un fijo pequeño por tarea: `KUKIA_GFLOP_MIN=0.5` (en GFLOP).
- **Red sin historial** (primer arranque, sin ninguna medida): cae a modo plano,
  que también pasa por la bolsa, así que tampoco puede emitir de más.
- Tope duro de **100M KIA** intacto y fin de emisión el **2150-01-01**.

Cifras de hoy (23-10, 21:20, minero parado): precio **0,003742558085 KIA/GFLOP**,
presupuesto **1,310562 KIA/min**, referencia **700,356 GFLOP/min**. Con ese
precio, una matriz de 0,128 GFLOP paga 0,000479 KIA y una de 1,024 GFLOP paga
0,003832 KIA. El número cambia cada minuto con el trabajo de la red: no es una
tabla fija.

Se comprueba sin creerse el panel: `GET /stats` devuelve `bolsa_emision` (precio,
presupuesto, pagado en el minuto, fusible) y `pago_por_potencia` (interruptor y
suelo). Pruebas aisladas sobre una **copia** del ledger (producción no se toca):

```bash
python tools/prueba_precio_minuto.py      # precio unico y reparto proporcional
python tools/prueba_bolsa_arranque.py     # la bolsa no se pasa ni en modo plano
python tools/prueba_pago_potencia.py      # FLOPs x precio, techo y tope de 100M
```

Interruptor: `KUKIA_PAGO_POR_FLOP=0` vuelve a la tarifa plana (que también va
dentro de la bolsa). Por defecto está **encendido**.

## Potencia demostrada (el tier se gana)

En Bitcoin el hashrate no se declara: se mide resolviendo. Aqui el tier salia
de la VRAM que el propio minero decia tener, asi que una CPU podia declarar
24 GB y cobrar x1,6 sin demostrar nada.

Esto decide el pago cuando se usa la **tarifa plana**. Con el pago por
potencia (el de serie desde el 23-10) **el tier no multiplica**: se cobra
por FLOPs medidos y punto. La medición sigue sirviendo para todo lo demás
(panel, reparto de trabajo y castigo a quien declara de más).

El panel (puerto 8301) muestra **tres cifras que no se mezclan**:

- **GFLOPS medidos** (tarjeta "Potencia medida" y cada GPU): lo que dio el
  benchmark del minero al arrancar, con las mismas cuentas que las tareas.
  Ahora mismo: RTX 5070 42,2 · RTX 5060 Ti 41,5 · total **83,8 GFLOPS**.
  Antes esa tarjeta se rellenaba con `duracion_ms`, un dato que el minero
  escribe a mano.
- **GFLOPS Core** (tabla de nodos): FLOPs de sus tareas verificadas entre el
  tiempo que el propio Core cronometra; incluye el viaje de red, así que es
  menor (13-17 GFLOPS por GPU aquí).
- **GFLOPS verificados** (gráfica): trabajo que el Core ha comprobado, minuto a
  minuto.
- **GFLOPS por GPU** y **latencia por GPU** (gráficas nuevas): el minero manda
  en cada tarea el tipo, los FLOPs y los ms que tardó de verdad; el Core los
  agrupa por minuto y GPU en `/red/serie_gpu`. Solo cuentan las tareas de
  cómputo (un checksum son 0 FLOPs), y un minuto sin cómputo deja un hueco, no
  un cero: el cero se leería como "la GPU no rinde" y sería mentira.
- **Mezcla de trabajo real**: proporción de matriz / checksum / difusión /
  inferencia que se ha ejecutado en la ventana.

Ahora el Core cronometra **el** (asignar -> entregar), calcula los FLOPs de la
tarea y guarda `nodos.gflops_medido`. Con 20 muestras o mas, el tier declarado
solo se paga si la GPU supera 0,25 GFLOPS por GB declarado (calibrado con
hardware real: una 5070 mide 13-14 GFLOPS). Cada pago devuelve `tier_pagado` y
`tier_declarado`, asi que se puede ver sin creerse el panel.

## Ejecucion redundante: el Core deja de recalcular

Antes el Core recalculaba **todo**: costaba lo mismo minar que verificar, asi
que la red no podia crecer mas alla de este PC. Ahora las tareas **caras**
(1 GFLOP o mas: `matriz`, `difusion_calor`, `inferencia`) las resuelven **dos
nodos distintos**:

- el que la tiene asignada y otro que pide trabajo y recibe `"replica": true`
  junto al id de la tarea ajena;
- los dos entregan; si coinciden, el Core **no recalcula** y paga el mismo
  total de siempre, repartido **70 % ejecutor / 30 % verificador** (no se paga
  dos veces la misma tarea: la emision no cambia);
- si discrepan, el Core recalcula, paga al que dijo la verdad y castiga al otro
  (dinero, reputacion y 30 min sin trabajo);
- **1 de cada 10** tareas replicadas se recalcula igualmente (muestreo
  deterministico por id), para que una colusion entre dos nodos tenga
  probabilidad medible de caer;
- como maximo **la mitad de los nodos activos** verifica a la vez; el resto
  ejecuta, que es lo que hace avanzar la cola;
- `solo_primaria=1` en `/tareas/pendiente` pide trabajo propio (lo usan las
  herramientas que necesitan una tarea concreta; el minero no lo manda).

Las tareas de milisegundos (`checksum`, `embeddings`) siguen como antes:
duplicarlas cuesta mas que recalcularlas. El Core ademas espera hasta **3 s** a
la replica cuando llega el primario (`KUKIA_REPLICA_ESPERA`): el verificador
empieza despues y sin ese margen llegaba siempre tarde (medido en la red: 131
replicas entregadas y 0 pagadas antes del margen; con el margen, las primeras
28 se pagaron solas). Configurable con `KUKIA_REPLICA=0` (desactiva),
`KUKIA_PARTE_REPLICA=0.3` (reparto), `KUKIA_AUDITORIA_1_DE=10` (muestreo) y
`KUKIA_REPLICA_FRACCION=0.5` (techo de verificadores).

## Guardia: que la red no se quede parada

El 20-10-2026 el minero estuvo 2 h 30 min caido con 121 tareas en cola y nadie
lo noto. `tools/guardia.py` vigila Core, minero, validador, publicador y panel,
y relanza lo que se caiga sin duplicar lo que ya este abierto:

```bash
python tools/guardia.py            # o doble clic en ARRANCAR_GUARDIA.cmd
```

Log en `logs/guardia.log`. Probado matando el minero: relanzado en 17 s.

Al relanzarlo hacia falta algo mas: las tareas que tenia en vuelo el proceso
muerto seguian contando en su cupo y lo dejaban **30 minutos** sin minar. Ahora
el minero avisa firmando en `POST /nodos/{id}/reinicio` y el Core le devuelve
sus propias tareas a la cola (medido: 4 por GPU, al instante).

## El minero nuevo (23-10-2026): una ventana, un minero, todo a la vista

Antes corría escondido (pythonw) y no se veía nada: si se quedaba sin cobrar,
nadie se enteraba. Ahora mina **en una ventana normal de cmd**, con cada tarea
en pantalla y un resumen por minuto.

```bash
VER_MINERO.cmd                 # o doble clic: mina aquí y lo cuenta todo
VER_MINERO.cmd minero_pc       # reutiliza la identidad de siempre
python worker/kukia_worker.py mi_nombre      # a mano, sin el .cmd
```

- **Se ve todo**: cada tarea, y cada minuto tareas/min, KIA/min, KIA/h, KIA/día
  y KIA/h por GFLOPS. Al arrancar imprime el calendario de emisión. Lo mismo
  queda en `logs/minero_visible.log`.
- **Un solo minero.** El primero que arranca apunta su PID en
  `worker/minero.lock`; si abres otra ventana, **no mina** y te dice qué PID
  está trabajando. Dos mineros en la misma GPU se reparten el cupo (4 tareas
  por nodo) y rinden la mitad.
- **`VER_MINERO.cmd` no duplica**: si el minero está escondido en segundo plano
  lo aparta y se queda minando a la vista; si ya está en otra ventana, se
  limita a enseñar cómo va (`tools/minero_en_ventana.py`).
- **Todas las GPUs a la vez**, un hilo por GPU. La CPU se añade con
  `KUKIA_USAR_CPU=1`; una CPU no recibe difusiones (0,8 s en GPU contra 17,3 s
  en CPU), aunque sí matrices, checksums e inferencia.
- **Ctrl+C no deja basura**: termina lo que tiene en vuelo antes de salir, así
  no deja tareas colgadas ocupando el cupo media hora.
- **Vigía propio**: si pasan 2 minutos sin cobrar con el cupo lleno, pide el
  reinicio firmado de su nodo y el Core le devuelve sus tareas a la cola; si
  pasan 5 y el Core está en pie, sale con código 3 para que la guardia lo
  levante limpio.
- **La guardia puede ir sin minero**: `python tools/guardia.py --sin-minero`
  (es lo que usamos cuando se para a propósito: si la guardia vigila al minero,
  lo relanza en segundos).
- Al arrancar imprime también **qué Core** está usando y, si es una dirección
  `auto`, la vuelve a resolver cada 5 minutos: si el túnel cambia, se entera
  sin reiniciar.

## Anclado en Bitcoin (lo que no depende de nosotros)

El ancla del rele la publica el mismo dueño que firma los sellos: sirve para
detectar una edicion, no para impedirla. `tools/anclar_ots.py` manda el sha256
del ultimo sello a tres calendarios de **OpenTimestamps**, que lo atan a un
bloque de Bitcoin. A partir de ahi la fecha no la garantiza este PC.

```bash
python tools/anclar_ots.py              # ancla el ultimo sello
python tools/anclar_ots.py --actualizar # cuando ya hay bloque
python tools/anclar_ots.py --verificar  # comprueba contra el bloque real
```

La guardia lo hace sola cada 10 min. Detalles y estado actual:
[ANCLAJE.md](ANCLAJE.md).

## Benchmark: lo que da tu GPU de verdad

```bash
python tools/benchmark_gpu.py --json
```

Mide con las mismas cuentas que las tareas de la red y deja el resultado en
`worker/benchmark.json`; el minero lo usa al arrancar y lo publica en su
telemetria. Medido aqui: 5070 -> 42,8 GFLOPS, 5060 Ti -> 40,6 GFLOPS.

## Estructura

```
KUKIA/
├── coordinator/kukia_core.py   <- FastAPI: nodos, tareas, verificacion, ledger (puerto 8300)
├── worker/kukia_worker.py      <- Nodo GPU: pide trabajo, ejecuta, devuelve hash
├── dashboard/kukia_dash.py     <- Dashboard web en vivo (puerto 8301)
├── ledger/kukia_wallet.py      <- Billetera local (firma + saldo + historial)
├── docs/wallet.html            <- Billetera web: crear, recuperar y ENVIAR (firma en el navegador)
├── coordinator/ledger/kukia.db <- Ledger + cadena de hashes + sellos firmados
├── validator/kukia_validator.py <- Validador independiente: recalcula y firma su conformidad
├── tools/pruebas_seguridad.py  <- Pruebas de integracion sobre una COPIA del ledger
├── docs/                       <- Whitepaper + imagenes (moneda, sello, portada)
└── tasks/                      <- Tipos de tarea
```

## Tu dinero se puede mover (no esta atrapado)

El saldo de una billetera `KUK...` se puede enviar a otra direccion con la
pestana **Enviar** de `docs/wallet.html`:

- La transaccion se firma **en el navegador** con Ed25519; la clave privada no
  viaja al Core ni se guarda.
- El Core comprueba que la clave publica derive exactamente en la direccion de
  origen y que el nonce no se haya usado (anti-replay).
- **Comision: 0 KIA.** Una transferencia no crea moneda: debita y acredita la
  misma cantidad, asi que el suministro no cambia.

Para las billeteras que ya estan en este PC (cifradas con DPAPI) no hace falta
pegar las 20 palabras en el navegador: `tools/enviar_kia.py --de <billetera>
--a KUK... --monto N` firma en el proceso, y sin `--confirmar` solo enseña lo
que haria (nada se envia).

## Validadores independientes (mas de un Core comprobando)

El Core de pagos sigue siendo **uno** (dos pagando a la vez serian doble gasto).
Lo que se puede multiplicar son los **validadores**: procesos que recalculan el
trabajo ya verificado y firman si coincide.

```bash
python validator/kukia_validator.py --core http://127.0.0.1:8300
# o doble clic en ARRANCAR_VALIDADOR.cmd
```

- Piden el trabajo a `GET /red/witness` (manifiesto firmado con la clave del
  sello del Core).
- Recalculan cada tarea por su cuenta y mandan una atestacion Ed25519 a
  `POST /validador/atestacion`.
- **No pagan, no mueven saldos y no escriben en el ledger.** Tampoco bloquean
  pagos: una discrepancia es un aviso firmado, no una orden.
- Se ven en `GET /validador/estado` y en la tarjeta del panel.

Para que aporten independencia real tienen que correr en **otras maquinas**.
Guia completa: [GUIA_VALIDADORES.md](GUIA_VALIDADORES.md).

## Minar desde otro PC (lo normal si no eres el creador)

No hace falta clonar nada: descarga `kukia-node.zip`, descomprime y doble clic
en `ARRANCAR_MINERO.cmd`. La direccion del Core ya viene puesta: el paquete
sigue la direccion estable publicada en `https://kukia-network.surge.sh/core_actual.json`,
así que no hay que editarla aunque el tunel cambie de nombre.
Paso a paso, con lo que se gana y como no perder la billetera, en
[GUIA_MINERO.md](GUIA_MINERO.md).

Como se publica el Core al exterior (tunel, direccion estable, limite de eso y
paso a dominio propio) en [TUNEL_ESTABLE.md](TUNEL_ESTABLE.md).

## Clonar y minar (3 pasos)

Requisitos: Python 3.10 o superior en el PATH y, si tienes GPU NVIDIA, los
drivers de CUDA. Sin GPU tambien se mina, en CPU (mas lento).

```bash
git clone https://github.com/lordkukone/kukia-node.git
cd kukia-node
python -m pip install -r requirements.txt
python tools/instalar_minero.py     # comprueba paquetes, GPU, disco y Core
```

Despues hay dos caminos, y solo cambia **una linea** de `core_minero.txt`:

- **Minar a la red que ya existe** (lo normal; no necesitas tu propio Core).
  Deja en `core_minero.txt` esta linea sola:
  `auto https://kukia-network.surge.sh/core_actual.json`
  y doble clic en `ARRANCAR_MINERO.cmd`. Con `auto`, el minero le pregunta al
  espejo cual es la direccion viva del Core, asi que no hay que tocarla cuando
  el tunel cambia de nombre.
- **Montar tu propio Core** (red local, para probar o para validar). Deja la
  linea por defecto `http://127.0.0.1:8300`, arranca el Core y luego el minero
  (paso a paso en [Montar tu propio Core](#montar-tu-propio-core)).

  ```bash
  cd coordinator && python -m uvicorn kukia_core:app --port 8300
  # en otra ventana
  cd worker && python kukia_worker.py minero_pc
  ```

La primera vez el minero crea tu billetera, te da la direccion de cobro y guarda
la contrasena y las 20 palabras en `ledger/wallet/RECUPERACION_*.txt`. **Sin ese
fichero no se recupera el saldo**: apuntalo aparte y no lo subas a ningun sitio.

El Core nace con su propio historial: si clonas el repo y arrancas tu propio
Core, los sellos publicados (que son de la cadena del fundador) se apartan solos
a `sellos.jsonl.de_otra_instalacion` y el tuyo empieza su cadena firmada. Los
pagos nunca quedan bloqueados por sellos de otra instalacion.

## Arranque rapido (en el PC del Core)

```bash
# 1. Core
cd coordinator && python -m uvicorn kukia_core:app --port 8300

# 2. Panel de operaciones (graficas en vivo, puerto 8301)
cd dashboard && python -m uvicorn kukia_dash:app --port 8301
#    o doble clic en ARRANCAR_PANEL.cmd

# 3. Minero: UNA ventana (usa todas las GPUs). NO lances dos.
VER_MINERO.cmd                    # o doble clic; deja ver cada tarea
VER_MINERO.cmd minero_pc          # reutiliza la identidad de siempre
#    a mano: cd worker && python kukia_worker.py nodo_5070

# 4. Wallets
cd ledger && python kukia_wallet.py nodo_fundador
```

## Montar tu propio Core

Un **Core** es el nodo que reparte trabajo, verifica los resultados y paga. Tu
copia es una red **independiente**: arranca con su propio historial y no hereda
los asientos del Core del fundador. Sirve para probar, para validar y para
levantar tu propia red cuando tengas mineros y clientes.

### 0. Qué necesitas

- **Python 3.10 o superior** en el PATH (probado con 3.12) y las dependencias:
  `python -m pip install -r requirements.txt`.
- **GPU: ninguna.** El Core verifica recalculando, y eso lo hace en CPU. (El
  minero sí quiere GPU; el Core no.)
- **CPU y RAM:** con 2 vCPU y 4 GB arranca y va; 4 vCPU y 8 GB es lo cómodo. La
  verificación ocupa cerca de un núcleo de media, y con un VPS de 1 vCPU la cola
  se atasca.
- **Disco:** el ledger crece solo. El del fundador ronda los 350 MB con ~495.000
  asientos; reserva varios GB y una copia de seguridad desde el primer día.

### 1. Arrancarlo

```bash
python -m pip install -r requirements.txt
cd coordinator
python -m uvicorn kukia_core:app --host 127.0.0.1 --port 8300
```

En el **primer arranque** crea, dentro de `coordinator/`, todo lo que necesita:

| Fichero | Qué es | Qué hacer con él |
|---|---|---|
| `ledger/kukia.db` | el ledger: asientos encadenados por hash | **no lo edites a mano** (la cadena lo delata y bloquea pagos) |
| `admin_token.txt` | token de administrador del Core | no lo compartas; quien lo tenga manda |
| `sello_key.txt` | clave Ed25519 que sella el historial | **la más importante**: sin ella no puedes volver a sellar |
| `sellos.jsonl` | sellos publicados (hash + firma) | se verifican con `GET /ledger/integridad` |

Nada de eso va a git (está en `.gitignore`). Son tu base y tus llaves.

### 2. Comprobar que está vivo (sin creerte el log)

```bash
curl http://127.0.0.1:8300/version        # versión, hora de arranque y SHA-256 del código
curl http://127.0.0.1:8300/stats          # supply, nodos, tareas, bolsa del minuto
curl http://127.0.0.1:8300/ledger/integridad   # tiene que decir "cadena_ok": true
```

Si `cadena_ok` es `false`, **no sigas**: el Core está bloqueando pagos a propósito
porque el historial no cuadra.

### 3. Darle trabajo

- **Sin mineros**, el Core se llena la cola él solo (`auto_tareas`, activo por
  defecto: matrices, inferencia y difusión). Verás tareas y pagos moverse, pero
  no es una red: es un banco de pruebas. Se apaga con `KUKIA_AUTO_TAREAS=0`.
- **Con un minero** en la misma máquina: deja `core_minero.txt` con
  `http://127.0.0.1:8300` y arranca `ARRANCAR_MINERO.cmd` o
  `cd worker && python kukia_worker.py minero_pc`.
- **Con un minero en otro PC**, tu Core tiene que ser alcanzable: abre solo lo
  necesario o publica con un túnel ([TUNEL_ESTABLE.md](TUNEL_ESTABLE.md)).
  Recuerda que el minero se niega a hablar con un Core cuyo `Host` no esté en
  `KUKIA_HOSTS`; si le pones dominio, añádelo a esa variable.

### 4. Publicarlo: PC encendido o servidor

Todo el despliegue está en la carpeta [despliegue/](despliegue/), con su propia
guía: [despliegue/LEEME.md](despliegue/LEEME.md). Dos caminos:

- **URL fija sin mover el Core** (Tailscale Funnel, gratis): sigue dependiendo de
  que tu PC esté encendido. `despliegue/funnel_kukia.cmd`.
- **Core en un servidor** (no depende de tu PC): `Dockerfile` +
  `docker-compose.yml` + `Caddyfile` con TLS automático, o
  `kukia-core.service` si prefieres systemd sin Docker. El 8300 **no se abre a
  internet**: solo Caddy (o cloudflared) lo alcanza en `127.0.0.1`.

Antes de subir nada, copia el ledger **con el Core en marcha** usando
`python despliegue/copiar_ledger_seguro.py`: copia con la API de backup de
SQLite y verifica el `sha256`. Un `cp` a pelo con el Core escribiendo da una base
rota. Después, `./despliegue/subir_al_servidor.sh usuario@servidor /srv/kukia`
compara el hash **en el servidor** y se para si no coincide.

### 5. Reglas que no se pueden saltar

- **Un Core, una red.** Dos Cores con bases distintas son **dos redes distintas**,
  aunque tengan el mismo código. Nunca dos procesos escribiendo el mismo
  `kukia.db`, y no arranques el del servidor dejando el de tu PC con el mismo
  historial: apaga uno antes.
- **Copia de seguridad desde el día uno**: `python tools/backup_ledger.py`.
- **El `admin_token.txt` no sale de la máquina** y `sello_key.txt` viaja solo si
  te llevas el Core; si se pierde, el historial ya no se puede volver a sellar.
- **El Core no necesita GPU, pero sí reloj correcto**: las firmas de los nodos
  caducan si la hora está desviada.
- **Los anclajes en Bitcoin** (`tools/anclar_ots.py`) son lo único que impide
  reescribir el pasado con la clave del sello. Anclar seguido, no una vez.

### 6. Variables de entorno que de verdad se usan

| Variable | Para qué |
|---|---|
| `KUKIA_DB` | ruta del ledger (por defecto `coordinator/ledger/kukia.db`) |
| `KUKIA_ADMIN_TOKEN` | ruta del fichero con el token de admin |
| `KUKIA_SELLO_KEY` / `KUKIA_SELLOS_FILE` | clave y fichero de sellos |
| `KUKIA_HOSTS` | hosts extra aceptados en la cabecera `Host` (dominio del túnel) |
| `KUKIA_ORIGENES` | orígenes web extra permitidos por CORS |
| `KUKIA_AUTO_TAREAS` | `0` apaga el trabajo sintético del Core |
| `KUKIA_CONFIA_PROXY` | `1` cuando hay Caddy/cloudflared delante |
| `KUKIA_ESCROW`, `KUKIA_CLIENTE_TOPE`, `KUKIA_CLIENTE_MAX_EN_COLA` | puerta de cliente |

## API

| Endpoint | Metodo | Que hace |
|---|---|---|
| /nodos/reto | POST | Reto de un solo uso para registrar (prueba de clave) |
| /nodos/{id}/reinicio | POST | El nodo avisa de que arrancó: sus tareas en vuelo vuelven a la cola (firmado) |
| /nodos/registrar | POST | Registra nodo (nombre, VRAM, ref) + firma del reto |
| /nodos | GET | Lista nodos (acotada: `?limite=200&activos=1`) |
| /nodos/{id} | GET | Un nodo concreto |
| /tareas/crear | POST | Crea tarea (tipo, params) |
| /tareas/pendiente | GET | Nodo pide trabajo (`solo_primaria=1` = trabajo propio, sin replicas) |
| /tareas/resultado | POST | Entrega resultado + firma Ed25519 (query string, mineros antiguos) |
| /tareas/resultado_json | POST | Igual, con cuerpo JSON (recomendado: sin limite de URL) |
| /tareas/replica | POST | Entrega la verificacion redundante de una tarea ajena (se paga al llegar el primario) |
| /transferir | POST | Envia KIA desde un nodo **o desde una billetera** (firma + anti-replay) |
| /ledger | GET | Historial de recompensas |
| /ledger/integridad | GET | Cadena + sellos + si los pagos estan bloqueados |
| /red/serie | GET | Serie por minuto: emision, tareas y GFLOPS medidos |
| /red/serie_gpu | GET | Serie por minuto **y GPU**: GFLOPS y ms medidos en cada tarjeta, y mezcla de tareas |
| /red/witness | GET | Manifiesto firmado de trabajo verificado (para validadores) |
| /validador/atestacion | POST | Atestacion firmada de un validador independiente |
| /validador/estado | GET | Validadores activos, tareas revalidadas y discrepancias |
| /version | GET | Que codigo esta ejecutando: version, hora de arranque, commit y SHA-256 de los ficheros clave (comparalo con GitHub con `tools/huella_codigo.py -g`) |
| /stats | GET | Supply, nodos, tareas, dificultad, integridad, bolsa del minuto y precio por GFLOP |
| /admin/clientes | POST/GET | Alta y lista de clientes (solo admin). El token se devuelve UNA vez |
| /cliente/precio | GET | Coste mínimo de una tarea antes de firmar (no gasta la bolsa) |
| /cliente/saldo | GET | Saldo disponible y lo retenido en tareas en curso |
| /cliente/tareas | POST | Encola un encargo: firma Ed25519 + cobro por adelantado |
| /cliente/tareas/{id} | GET | Estado y resultado; solo del cliente dueño |

## Pedir trabajo (clientes)

Un cliente compra cálculo: firma el encargo con su billetera, el Core le retiene
el coste por adelantado y el minero cobra de ese depósito, no de la emisión
(un cliente que paga no infla el suministro). El token de API solo identifica:
para gastar hace falta la firma de la billetera.

```bash
curl "http://127.0.0.1:8300/cliente/precio?tipo=matriz" -H "X-Cliente-Token: kia_..."
python tools/cliente_ejemplo.py pedir matriz --params "{\"n\": 256, \"seed\": 7}"
```

Guía completa, contrato de firma y tabla de errores: [GUIA_CLIENTE.md](GUIA_CLIENTE.md).

## Verificar que no esta roto

```bash
python tools/prueba_cliente.py          # 40 pruebas del ciclo cliente: escrow, cobro, devolución
python tools/pruebas_seguridad.py       # 101 pruebas sobre una COPIA del ledger
python tools/pruebas_caos.py            # 57 de caos: doble gasto, kill -9, disco lleno, basura
python tools/backup_ledger.py           # copia verificada en backups/
python tools/probar_restauracion.py     # arranca un Core sobre la ultima copia
python tools/prueba_failover_negativas.py  # la replica se NIEGA: ausente, vieja, rota, otra clave, bifurcada
python tools/replica_ledger.py          # replica verificada en OTRO disco (D:)
python tools/replica_ledger.py --comparar   # replica vs Core: misma red?
python tools/volver_al_original.py --ver    # vuelta del failover (sin escribir)
python tools/prueba_vigilante_indexado.py   # el aviso de "ya salimos en Google/Bing" (una vez, ni una mentira)
python publico/verificar_sellos.py --publicado https://<rele>/api/metricas
python tools/anclar_ots.py --verificar     # sello atado a un bloque de Bitcoin
python tools/benchmark_gpu.py              # lo que tu GPU da de verdad
```

La copia diaria esta programada a las 03:00 con la tarea de Windows
`KUKIA Backup Ledger` (`TAREA_BACKUP.cmd`, log en `backups/backup.log`).
Para recrearla: `powershell -ExecutionPolicy Bypass -File tools\programar_backup.ps1`.

Copia la base a una carpeta temporal (nunca toca la real), arranca un Core
aislado y prueba transferencias desde billetera, anti-replay, firmas falsas,
destinos invalidos, el camino antiguo de nodo, una tarea de mineria completa
(pendiente firmado -> resultado firmado -> pago) y un **validador independiente**
que la recalcula y firma. También prueba los controles del minero (nonce
anti-replay, validación local de tareas, topes de payload y clave pública).
Sale 0 solo si pasan las 93 comprobaciones.

## Tokenomics (lo que hace el codigo, no una propuesta)

- Ticker: **KIA**. Maximo: 100.000.000 KIA, fijo (`max_supply` en el Core).
- 15.000.000 KIA fundacionales al fundador en el bloque genesis
  (`KUK9E8B44CD7FE3AF178039453D3EEDF391EEC0CE`).
- El resto sale por emision de trabajo verificado: 1,31 KIA/min hoy, con
  halvings cada ~20,7 anos y fin de emision el 2150-01-01. Nunca se acuña por
  encima del tope: se comprueba en cada pago.
- El reparto 25M/40M/15M/10M/10M que habia aqui era una propuesta antigua y no
  es lo que corre: el codigo no tiene esas bolsas.
- Sin precio: ni cotiza ni se vende. Saldra cuando haya demanda real medida.

## Roadmap

1. Prototipo local (CORRIENDO) -> 2. Trabajo GPU real -> 3. Verificacion fuerte
-> 4. Multi-nodo -> 5. Testnet (Cosmos SDK/Arbitrum Sepolia) -> 6. Mainnet

El plan detallado, con lo que se puede y no se puede ganar a Bitcoin y el plan
del minero por fases: [ESTRATEGIA.md](ESTRATEGIA.md).

## HonestidadKUKIA NO promete ser la IA mas potente del mundo. Es una red util de computo
 distribuido (inferencia, imagen, video, RLVR). El valor esta en trabajo
 verificado, no en humo. (W: es un prototipo, no oferta de valores.)

### Limites conocidos (auditoria del 23-10-2026)

Para no tener que leer una auditoria entera para saber que NO esta resuelto:

- **Hay un solo Core que liquida.** Los validadores recalculan y firman
  atestaciones, pero no mueven saldos ni bloquean un pago discrepante. Si cae la
  maquina del operador, la red se para. No es Bitcoin ni pretende serlo todavia.
- **Nodos externos: ninguno por ahora.** Todo lo medido se ha medido aqui.
- **CPU/GPU se declara al registrarse.** El Core no se lo cree del todo (lo corrige
  con el latido firmado y no manda difusiones a quien no ha demostrado GPU), pero
  una CPU que diga ser GPU puede recibir trabajo que no acabe a tiempo.
- **Sin auditoria externa.** Las pruebas de este repo las escribe el propio
  proyecto; una revision adversarial de terceros sigue pendiente.

Lo que SI esta cerrado y se puede comprobar: la emision no puede pasarse del
presupuesto del minuto (`tools/prueba_bolsa_arranque.py`), el reparto es
proporcional al trabajo y no al orden de llegada (`tools/prueba_precio_minuto.py`),
la telemetria no crece sin tope y el Core publica que codigo esta ejecutando en
`/version` (`tools/huella_codigo.py`).

Detalle, medidas y lo que queda pendiente: el informe de auditoria interno del
proyecto, que no se publica en este repositorio.

=================================================================

## Derechos de autor

(c) 2026 RMR (LordKukone). Todos los derechos reservados.

El codigo de este repositorio se publica para que se pueda LEER y comprobar,
no para reutilizarlo. No se concede permiso para copiarlo, modificarlo,
redistribuirlo ni usarlo para operar otra red o servicio. El aviso completo
esta en [LICENSE](LICENSE).

KUKIA, KIA y el logo de la moneda son del autor.
