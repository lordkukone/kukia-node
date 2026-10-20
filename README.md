# KUKIA Quantum AI Compute Network

**Every GPU. One intelligence network.**

Red descentralizada que conecta GPUs de hogar para ejecutar trabajo de IA
real (inferencia, imagen, video, RLVR, simulacion) y recompensa el trabajo
VERIFICADO con KUKIA Coin (KUKI).

## Concepto (30 segundos)

- Tu GPU libre → se registra como nodo → pide tareas → ejecuta → devuelve hash
- KUKIA Core verifica re-ejecutando la tarea (bit a bit) → si coincide, ganas KUKI
- La moneda NO se mina quemando electricidad (PoW). Se ACUNA con trabajo util verificado (Proof-of-Compute)
- **Sin precio hasta que la red demuestre movimiento real verificado** (decision del creador)

## Como se mina KUKI (lo que hay de verdad)

1. El Core crea la tarea con ID unico: tipo + parametros + semilla + timestamp
2. El nodo pide trabajo firmando la peticion y ejecuta en su GPU
3. Devuelve el resultado firmado con su clave Ed25519
4. El Core comprueba el resultado: si otro nodo ejecuto la misma tarea cara y
   coincide, **no recalcula** (ver "Ejecucion redundante"). Si no, recalcula
   el y compara. Si no coincide: no cobra, pierde reputacion, se le cobra una
   penalizacion (acotada a su saldo) y no puede pedir trabajo unos minutos
5. El pago se abona a su **direccion de cobro**, no al id del nodo
6. La **prima por tier** (x1,3 / x1,6) solo se paga si la GPU la ha demostrado
   con trabajo verificado: ver "Potencia demostrada"
7. El ledger va encadenado (hash del asiento anterior) y se sella cada pocos
   minutos con una clave Ed25519 que no esta en la base de datos

Lo que **no** existe todavia y por tanto no se promete: staking con colateral
y bounty economico por cazar mentirosos. Estan como fase 2 en
[ESTRATEGIA.md](ESTRATEGIA.md), no en el codigo.

## Potencia demostrada (el tier se gana)

En Bitcoin el hashrate no se declara: se mide resolviendo. Aqui el tier salia
de la VRAM que el propio minero decia tener, asi que una CPU podia declarar
24 GB y cobrar x1,6 sin demostrar nada.

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

No hace falta clonar nada: descarga `kukia-node.zip`, descomprime, pon la URL
del Core en `core_minero.txt` y doble clic en `ARRANCAR_MINERO.cmd`.
Paso a paso, con lo que se gana y como no perder la billetera, en
[GUIA_MINERO.md](GUIA_MINERO.md).

## Arranque rapido (en el PC del Core)

```bash
# 1. Core
cd coordinator && python -m uvicorn kukia_core:app --port 8300

# 2. Panel de operaciones (graficas en vivo, puerto 8301)
cd dashboard && python -m uvicorn kukia_dash:app --port 8301
#    o doble clic en ARRANCAR_PANEL.cmd

# 3. Worker (una instancia por GPU)
cd worker && python kukia_worker.py nodo_5070   # GPU0
cd worker && python kukia_worker.py nodo_5060ti # GPU1

# 4. Wallets
cd ledger && python kukia_wallet.py nodo_fundador
```

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
| /stats | GET | Supply, nodos, tareas, dificultad, integridad |

## Verificar que no esta roto

```bash
python tools/pruebas_seguridad.py       # 93 pruebas sobre una COPIA del ledger
python tools/backup_ledger.py           # copia verificada en backups/
python tools/probar_restauracion.py     # arranca un Core sobre la ultima copia
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

## Tokenomics (propuesta)

- Maximo: 100.000.000 KUKI (fijo)
- Fundacional 25M · Nodos 40M · Equipo 15M · Gobernanza 10M · Liquidez 10M
- Expansion SOLO por gobernanza con demanda real (nunca 10.000M sin respaldo)
- Salida a precio MUY BAJO cuando la red se mueva (condiciones en whitepaper)

## Roadmap

1. Prototipo local (CORRIENDO) -> 2. Trabajo GPU real -> 3. Verificacion fuerte
-> 4. Multi-nodo -> 5. Testnet (Cosmos SDK/Arbitrum Sepolia) -> 6. Mainnet

El plan detallado, con lo que se puede y no se puede ganar a Bitcoin y el plan
del minero por fases: [ESTRATEGIA.md](ESTRATEGIA.md).

## Honestidad

KUKIA NO promete ser la IA mas potente del mundo. Es una red util de computo
distribuido (inferencia, imagen, video, RLVR). El valor esta en trabajo
verificado, no en humo. (W: es un prototipo, no oferta de valores.)