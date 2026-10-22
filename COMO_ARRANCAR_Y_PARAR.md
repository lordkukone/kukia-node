# KUKIA — arrancar, parar y ajustar (actualizado 20-10-2026)

Estado actual (comprobado el 20-10-2026): **Core v0.4.2, integridad OK (17 sellos), pagos activos, billetera puede ENVIAR KIA, minero endurecido (nonce + validación local + HTTPS remoto) y utilización de GPU en vivo en el panel**.
Arranque manual, sin launcher (nada de Pinokio).

| Que | Puerto | Comando |
|---|---|---|
| Core (coordinador) | 8300 | `python -u -m uvicorn kukia_core:app --host 127.0.0.1 --port 8300` |
| Panel de operaciones | 8301 | `ARRANCAR_PANEL.cmd` (o `uvicorn kukia_dash:app --port 8301` en `dashboard/`) |
| Minero (2 GPUs) | — | `python -u kukia_worker.py kuki_minero` |

Logs del arranque del 19-10 (procesos lanzados en segundo plano):

- Core: `coordinator/core_final.out.log` y `.err.log`
- Minero: `worker/minero_ok.out.log` y `.err.log`

Para verlo en vivo sin tocar el minero: abre `worker\ver_minero.cmd`
(ventana que sigue el log; cerrarla NO detiene el minero).

Los PID cambian en cada arranque: si no coinciden, usa los comandos de abajo
para comprobar cual esta vivo antes de matar nada.

## Cobros e identidad del minero (arreglo 19-10-2026)

Antes el Core pagaba siempre al **id del nodo** (un uuid nuevo en cada
arranque): el minero ganaba KIA que no llegaban nunca a su billetera y se
quedaban huerfanos al reiniciar. Ahora:

- El worker se registra con `direccion_cobro` = direccion de su billetera
  (`ledger/wallet/worker_kuki_minero.json`), y el Core paga ahi directamente
  (`_cuenta_de()` en el Core; penalizaciones incluidas).
- El id de cada GPU se guarda en `worker/identidad_minero.json` y se reutiliza
  al rearrancar: ya no se crean dos nodos nuevos por arranque.
- Si un nodo viejo tiene saldo huerfano, el worker lo barre a la billetera al
  arrancar (firma Ed25519 de cada transferencia) y luego cada 5 minutos.
- Para cambiar la direccion de cobro de un nodo ya registrado: `POST
  /nodos/<id>/cobro` con la firma del nodo sobre `cobro:<id>:<direccion>:<ts>`.

Comprobar el saldo real de la billetera del minero:

```bash
curl "http://127.0.0.1:8300/saldo/KUK90BA61EDA315C341434AF93CC4D82D80D9DD21"
```

## Metricas en vivo en la web (rele publico)

La web (`https://lordkukone.github.io/kukia-node/`) ya no depende de que el
visitante tenga el Core: los numeros se refrescan cada 5 s desde un rele
publico de **solo lectura**. Tu PC empuja los datos hacia fuera, asi que el
Core sigue escuchando solo en `127.0.0.1` y no hay puertos abiertos ni IP
expuesta.

Cadena completa:

```
Core 127.0.0.1:8300  --(cada 10 s, clave)-->  rele Worker (R2)
                                                     |
                         web / billetera  <--(cada 5 s, GET)--+
```

- Relé de produccion: `https://site-ef75d1db031c418d80071d2758f3a9e5.freebuff.page`
  (`GET /api/metricas`, `GET /api/saldo/<direccion>`; el `POST /api/publicar`
  exige la clave secreta `KUKIA_CLAVE`).
- La web lo encuentra en `docs/metricas.json` (`api`). Vacio = se queda con la
  instantanea del 19-10-2026.
- Si el minero se apaga, la web avisa: punto ambar y "SIN DATOS NUEVOS".

Arrancar la publicacion (en otra ventana, mientras el Core este encendido):

```bat
C:\Users\kuki\Desktop\KUKIA\publico\ARRANCAR_METRICAS.cmd
```

o directamente:

```bash
cd C:\Users\kuki\Desktop\KUKIA
python publico\publicar_metricas.py          # bucle cada 10 s
python publico\publicar_metricas.py --una    # una sola vez (probar)
```

La configuracion vive en `publico\relay_url.txt` (URL del rele) y
`publico\clave.txt` (clave secreta). **Esos dos ficheros no se publican ni se
suben a git.**

Comprobar que el rele tiene datos frescos:

```bash
curl "https://site-ef75d1db031c418d80071d2758f3a9e5.freebuff.page/api/metricas"
```

## Como entrar en la billetera (web)

1. Abre `https://lordkukone.github.io/kukia-node/wallet.html`.
2. **Nueva**: genera contrasena aleatoria + 20 palabras. Apuntalas en papel: sin
   ambas no hay recuperacion (las claves viven solo en la pestaña).
3. **Recuperar**: contrasena + las 20 palabras. Si la billetera es anterior al
   18-10-2026, marca la casilla "Billetera antigua".
4. **Mi saldo**: pega la direccion `KUK...` y pulsa "Ver saldo". Primero intenta
   tu Core local (`http://127.0.0.1:8300`); si no responde, usa el saldo que
   publica el rele (puede tener hasta 10 s de retraso).
5. **Enviar**: con la billetera creada o recuperada en esa pestaña, pon la
   direccion destino `KUK...` y la cantidad (maximo 4 decimales) y pulsa
   "Firmar y enviar". La firma se hace en el navegador, la clave privada no
   sale de la pestaña y la comision es 0. Si recargas la pagina hay que volver
   a poner las 20 palabras: las claves solo viven en memoria.

La billetera del minero es `KUK90BA61EDA315C341434AF93CC4D82D80D9DD21`. Para
**ver su saldo** basta pegarla en "Mi saldo". Ojo: esa billetera la creo el
worker con una contrasena aleatoria que no se guardo, asi que sus 20 palabras
(`ledger\wallet\worker_kuki_minero.json`) **no** sirven para recuperarla en la
web; solo para mirar el saldo y cobrar.

Comprobar que siguen vivos:

```bash
netstat -ano | findstr 8300
python -c "import psutil;[print(p.pid,' '.join(p.cmdline)) for p in psutil.process_iter(['pid','cmdline']) if 'kukia' in ' '.join(p.cmdline or [])]"
```

## Panel de operaciones (8301) - 20-10-2026

Doble clic en `ARRANCAR_PANEL.cmd` y abre http://127.0.0.1:8301.

- Lee del Core por HTTP; **no escribe** en la base ni toca el minero.
- Muestra emision acumulada, tareas/minuto, GFLOPS **medidos** (FLOPs de tareas
  verificadas / segundos reales), nodos activos, cola, pago por tarea.
- Muestra el semaforo de seguridad: cadena del ledger, sellos firmados, pagos
  bloqueados y el hash del ultimo sello.
- Si el Core esta apagado, lo dice en rojo y mantiene la ultima foto buena
  (nunca inventa datos).
- Cerrar la ventana para el panel; el Core y el minero siguen.

## Arrancar

```bash
# 1) Core (desde la carpeta coordinator)
cd C:\Users\kuki\Desktop\KUKIA\coordinator
python -u -m uvicorn kukia_core:app --host 127.0.0.1 --port 8300

# 2) Minero, en OTRA ventana (desde la carpeta worker)
cd C:\Users\kuki\Desktop\KUKIA\worker
python -u kukia_worker.py kuki_minero
```

Con `python -u` los mensajes salen en vivo. El minero detecta las GPUs solo y
abre un hilo por GPU (ahora: RTX 5070 `cuda:0` y 5060 Ti `cuda:1`).

### Mejor: arrancar con la guardia (20-10-2026)

```bash
python tools/guardia.py        # o doble clic en ARRANCAR_GUARDIA.cmd
```

La guardia vigila Core, minero, validador, publicador y panel. Cada 30 s mira
si el Core responde y si estan pagandose tareas; si algo se ha caido lo
relanza. **No duplica** lo que ya tengas abierto a mano: antes de arrancar
mira la linea de comandos de los procesos.

Log: `logs/guardia.log`. Un ciclo suelto para ver que ve:
`python tools/guardia.py --una --verbose`. Solo puede haber **una** guardia a
la vez: si arrancas otra, se cierra sola diciendolo en el log.

Para que se levante sola al encender el PC (sin ventana), el lanzador
`ARRANCAR_GUARDIA_SILENCIOSO.vbs` esta copiado en la carpeta de Inicio del
usuario. Para quitarlo: Win+R -> `shell:startup` -> borrar `KUKIA Guardia.vbs`.
Eso no para nada de lo que este corriendo; solo deja de arrancarlo al entrar.

### Que arranca solo al encender el PC (22-10-2026)

Al entrar en Windows se lanza **solo la guardia** (el .vbs de la carpeta de
Inicio). La guardia levanta, en este orden: **Core, minero, panel, validador,
publicador y tunel**. No hace falta abrir nada a mano ni tener tareas
programadas para el minero y el panel.

Dos fallos de ese arranque, corregidos hoy:

1. El .vbs de Inicio sacaba la ruta del proyecto de **su propia carpeta**
   (`...\Startup\tools\guardia.py`), que no existe: salia con codigo 1 y no
   arrancaba nada. Por eso el 22-10 no habia minero ni panel al encender.
   Ahora la ruta va escrita entera, y espera 40 s (discos, red y drivers).
2. El candado de instancia unica buscaba `tools/guardia.py` con barra
   inclinada, pero el .vbs arranca el guardia con ruta de Windows
   (`...\tools\guardia.py`, barra invertida) y no se veia al otro: llegaron a
   correr **dos** guardias. Ahora se busca solo `guardia.py`, que vale para
   las dos formas. Probado: el segundo se cierra con
   `ya hay 1 guardia(s) corriendo`.

Comprobarlo sin reiniciar: `netstat -ano | findstr "8300 8301"` (Core y
panel), y las ultimas lineas de `logs/guardia.log` tienen que decir
`[minero] arrancado` o `ya esta corriendo`.

Motivo: el 20-10-2026 el minero se callo a las 12:28 y nadie lo noto hasta las
15:00, con 121 tareas en cola. La guardia lo detecta en menos de un minuto
(probado matando el minero: relanzado en 17 s).

## Parar

Para la guardia primero (cierra su ventana) y luego los procesos que quieras;
si dejas la guardia abierta, volvera a levantarlos.

```bash
# comprobar primero los PID reales (cambian en cada arranque)
netstat -ano | findstr 8300
get-process python | select Id,Path

taskkill /PID 9300 /F      # minero (deja de minar, no pierde nada)
taskkill /PID 35156 /F     # Core (la cola queda guardada en el .db)
```

**OJO, esto nos paso hoy:** si arrancas con `cmd /c ... &` o desde un script, el
`python` es un proceso HIJO. Matar el proceso padre (el `shell`) **no mata al
python**: sigue escuchando en el 8300 y el siguiente arranque falla con
"address already in use" mientras el viejo sigue sirviendo. Antes de arrancar,
comprueba que el 8300 esta libre (`netstat -ano | findstr 8300`).

## Que hace el Core solo (para que la red no se pare)

- **Generador de trabajo**: mantiene la cola con tareas. Sin esto la cola se
  vacia, el minero no tiene nada que pedir y la red se queda quieta (paso hoy).
  Por defecto: hasta 120 tareas en cola, hasta 40 nuevas cada 10 s, mezcla de
  `matriz`, `checksum`, `inferencia` y `difusion_calor` (marcadas
  `campana='auto'`).
- **Recuperacion**: cada 60 s devuelve a la cola las tareas colgadas (antes se
  quedaron 41 tareas 50 horas atascadas porque la recuperacion solo corria si
  alguien pedia trabajo).
- **Archivado**: a ~400 tareas/minuto la tabla `tareas` mete ~575.000 filas al
  dia (~450 MB). Cada 120 s borra las terminadas (`ok`/`fallo`) mas antiguas,
  en lotes de 20.000, dejando siempre las ultimas **200.000**. No toca el
  ledger ni los saldos, y el ritmo de emision sale de contadores historicos
  (tabla `meta`), asi que borrar tareas **no** cambia el premio por tarea.

Ajustes por variable de entorno (antes de arrancar el Core):

| Variable | Por defecto | Que hace |
|---|---|---|
| `KUKIA_AUTO_TAREAS` | `1` | `0` apaga el generador |
| `KUKIA_AUTO_COLA` | `120` | tareas que intenta mantener en cola |
| `KUKIA_AUTO_SEG` | `10` | cada cuanto revisa la cola |
| `KUKIA_AUTO_LOTE` | `40` | maximo que crea de golpe |
| `KUKIA_ARCHIVAR` | `1` | `0` apaga el archivado de tareas viejas |
| `KUKIA_RETENCION_TAREAS` | `200000` | terminadas que se guardan (bajarlo = base mas pequeña) |
| `KUKIA_ARCHIVAR_LOTE` | `20000` | filas borradas por pasada |
| `KUKIA_ARCHIVAR_SEG` | `120` | cada cuanto archiva |
| `KUKIA_VERIF_GPU` | `1` | `0` verifica en CPU (mucho mas lento) |
| `KUKIA_HOSTS` | vacio | hosts extra permitidos, separados por comas |
| `KUKIA_HOSTS_SUFIJOS` | `trycloudflare.com,cfargotunnel.com` | dominios de tunel aceptados por sufijo (vaciar = solo `KUKIA_HOSTS`) |
| `KUKIA_CORE` | `http://127.0.0.1:8300` | a que Core apunta el minero |

Y en caliente, con el token de admin (`coordinator\admin_token.txt`):

```bash
curl -X POST -H "X-Admin-Token: <token>" "http://127.0.0.1:8300/admin/auto_tareas?activo=1&objetivo=120"
curl -X POST -H "X-Admin-Token: <token>" "http://127.0.0.1:8300/admin/liberar_atascadas"
curl -X POST -H "X-Admin-Token: <token>" "http://127.0.0.1:8300/admin/archivar?retencion=50000"
curl "http://127.0.0.1:8300/ledger/verificar"
```

`/stats` devuelve ahora `tareas_ok` (**historico real**, aunque se archiven),
`tareas_ok_en_tabla` (las guardadas), `tareas_archivadas` y la configuracion
del archivado:

```bash
curl "http://127.0.0.1:8300/stats"
```

## Por que el premio por tarea baja

El pago no es fijo: el Core calcula `objetivo = restante / tareas_restantes`
para agotar los 100M justo el **13-04-2150**. Cuando la red mina mas tareas por
segundo, el pago por tarea baja y el total sigue acotado: hoy el presupuesto es
**1,31 KIA/minuto** (686.000 KIA/año), o sea **0,0037 KIA por tarea** a unas
500.000 tareas/dia.

Ojo con un detalle que ya costo un susto: hasta el 19-10-2026 habia un **suelo**
de `techo*0,1` = 1 KIA por tarea. Con ~300 tareas/min son 432.000 KIA/dia y los
85M que quedaban se agotaban en **197 dias** (mayo de 2027), no en 2150. Ese
suelo esta quitado y el ritmo se mide con la ventana reciente (3 min), no con la
media historica, que se quedaba corta al acelerar la red.

Es el diseno, no un fallo: si quieres premios altos por tarea, deja la red
minando menos tareas por minuto (baja `KUKIA_AUTO_LOTE` o sube `KUKIA_AUTO_SEG`);
lo que se emite por minuto no cambia.

## Reglas de cobro y castigo (19-10-2026)

- **Cobrar exige billetera**: `/nodos/registrar` rechaza (400) cualquier nodo
  sin `direccion_cobro` valida. El pago se abona a esa direccion, no al id del
  nodo: asi no se queda dinero atrapado al perder la clave (habia 12.494,54
  KIA asi; el 19-10 se recuperaron 378,44 que tenian clave local y quedan
  12.116,10 atrapados por falta de clave).
- **Castigo proporcional**: un resultado incorrecto cuesta 100 tareas
  (100 x el premio del momento, suelo 0,1 KIA, tope 25) y nunca deja el saldo en
  negativo. Antes eran 25 KIA fijos: con el premio actual, 6.757 tareas por un
  solo fallo.
- **Tamano de tarea por tiempo**: el Core persigue ~2 s por tarea
  (`KUKIA_TAREA_MS`). Si las tareas salen cortas, las agranda; si se pasan, las
  encoge. Asi el ritmo se queda estable (~50 tareas/min con 2 GPUs) y el premio
  por tarea sube a ~0,025 KIA, sin mover el presupuesto de 1,31 KIA/min ni la
  fecha de 2150. Techo: `difusion_pasos` 20.000 y matriz `n` 3x.
- **TOPS solo de nodos activos**: la dificultad cuenta unicamente nodos vistos
  en las ultimas 24 h. Antes sumaba todos los registros (11.192 TOPS fantasma
  frente a 200 reales) y agrandaba las tareas 2,5x.

## Recuperar saldos atrapados en nodos antiguos

Los nodos registrados antes del 19-10 cobraban al **id del nodo**, no a una
direccion. Si conservas la clave privada con la que se registraron, ese saldo se
puede mover con el `/transferir` firmado que ya existe:

```bash
# ver que se moveria (no toca nada)
python tools/rescatar_saldos.py --wallet ruta/a/billetera.json

# moverlo de verdad, a la direccion que elijas
python tools/rescatar_saldos.py --wallet ruta/a/billetera.json        --destino KUK9E8B44CD7FE3AF178039453D3EEDF391EEC0CE --confirmar
```

Es idempotente (repetirlo no mueve dos veces), no usa el token de admin y deja
dos asientos por movimiento (`transferencia salida` / `entrada`): el suministro
total no cambia. **Sin la clave privada no hay forma criptografica de mover ese
saldo**; queda congelado a la vista en el ledger.

## Abrir la moneda a Internet por tunel (19-10-2026, revisado 21-10-2026)

```cmd
ARRANCAR_TUNEL.cmd
```

Abre un tunel de Cloudflare hacia el Core local y **publica la direccion viva**
en `https://kukia-network.surge.sh/core_actual.json`. La URL del tunel sigue
cambiando en cada arranque, pero la direccion que usan los mineros no: el
paquete descargable trae `auto https://kukia-network.surge.sh/core_actual.json`
en `core_minero.txt` y pregunta ahi cual es la URL buena en cada arranque.

- Lo vigila `tools/tunel_estable.py`: si el tunel se cae, lo relanza y republica
  solo. La guardia lo lleva como un hijo mas, asi que con la guardia puesta no
  hace falta abrir nada.
- Un solo supervisor a la vez (cerrojo en el puerto local 8399): abrir dos
  ventanas no crea dos tuneles.
- El Core no se expone directamente: el tunel reescribe el Host a `127.0.0.1`,
  asi que la guardia de Host sigue activa.
- `/admin` sigue exigiendo `X-Admin-Token` (403 sin el), pero es alcanzable
  desde Internet mientras el tunel este abierto.
- Estado en cualquier momento: `python tools\tunel_estable.py --estado`.
- Para una **URL fija con nombre propio** esta ya todo hecho, esperando el
  dominio: `PREPARAR_DOMINIO.cmd` una vez (pide el dominio y autoriza
  Cloudflare en el navegador) y despues `ARRANCAR_TUNEL.cmd` arranca solo el
  modo nuevo. La guardia lo detecta y cambia de supervisor ella sola; el
  paquete descargado no hay que rehacerlo. Detalle en
  [TUNEL_ESTABLE.md](TUNEL_ESTABLE.md).

## Salud y copias (19-10-2026)

```bash
# veredicto en una linea: OK / AVISO / FALLO (codigo 1 si hay fallo)
python tools/salud.py --backups D:/KUKIA_backups --json

# copia consistente aunque el Core este escribiendo + verificacion contra sellos
python tools/backup_ledger.py --destino D:/KUKIA_backups --con-claves --mantener 14
```

`salud.py` comprueba Core, cadena, sellos, pagos bloqueados, si el ledger se
esta escribiendo, disco, antiguedad del sello, emision y ultima copia.
`backup_ledger.py` usa la API de backup de SQLite, verifica la copia y escribe
un `manifest.json` con hashes; si la copia no cuadra, devuelve error.

**Copia diaria programada (20-10-2026):** la tarea de Windows
`KUKIA Backup Ledger` ejecuta `TAREA_BACKUP.cmd` a las 03:00 y escribe en
`backups/backup.log`; conserva 14 copias y se recupera si el PC estaba apagado.
Para crearla o repararla:

```powershell
powershell -ExecutionPolicy Bypass -File tools\programar_backup.ps1
```

**Copia SEMANAL verificada a N: (22-10-2026):** la tarea `KUKIA Copia
Seguridad` ejecuta `TAREA_COPIA_SEGURIDAD.cmd` los **domingos a las 04:00**
(oculta, por el `.vbs`) y deja en `N:\COPIA_SEGURIDAD_20261022` una copia de
`KUKIA`, `I:\CEREBRO` y `H:\Xoxete` con el SHA256 de cada fichero comparado
contra el original. Lo que no ha cambiado no se recopia; lo que cambio se
verifica otra vez. Log en `backups/copia_seguridad.log` y, en el destino,
`RESUMEN.txt` (ultima pasada) e `historial/` (una linea y un resumen por
pasada; ahi no se borra nada).

```bash
# a mano, viendo el progreso
TAREA_COPIA_SEGURIDAD.cmd

# comprobar una copia ya hecha (relee los ficheros y compara hashes)
python tools\copia_seguridad.py --verificar N:\COPIA_SEGURIDAD_20261022

# lanzar la tarea sin esperar al domingo
schtasks /Run /TN "KUKIA Copia Seguridad"
schtasks /Query /TN "KUKIA Copia Seguridad" /V /FO LIST   :: ultimo resultado
schtasks /Delete /TN "KUKIA Copia Seguridad" /F           :: quitar la copia semanal
```

Para volver a crearla: `powershell -ExecutionPolicy Bypass -File
tools\programar_copia_seguridad.ps1`. Para cambiar el destino (otro disco, otra
carpeta) sin tocar el script, define `KUKIA_COPIA_DESTINO` antes de ejecutar
el `.cmd`.

Como sale `0` solo si no hubo ni un fallo, un resultado distinto de 0 en el
Programador de tareas significa que hay algo que mirar en el `RESUMEN.txt`.
Las bases SQLite abiertas se copian con `VACUUM INTO` (14 s el ledger de
244 MB con el Core escribiendo); la API `backup()` se reinicia sola en cada
escritura del Core y con un ledger asi **no termina nunca** (probado: 12 min
y seguia). Los `-wal`/`-shm` de las bases no se copian: sobran.

**Restauracion probada** (no basta con copiar):

```bash
python tools/probar_restauracion.py
```

Coge la ultima copia, comprueba hashes, cadena y firmas, arranca un Core
aislado contra ella y exige el mismo suministro, cadena, sellos y saldos.

### Replica en OTRO disco (22-10-2026)

`backups/` y el ledger estan los dos en `C:`: si ese disco muere, se van los
dos. La replica cierra ese agujero.

```bash
python tools/replica_ledger.py              # copia + verifica + compara (12 s, 236 MB)
python tools/replica_ledger.py --comparar   # ¿sigue siendo la misma red?
python tools/replica_ledger.py --estado
```

Queda en `D:\KUKIA_REPLICA\actual` (db, sellos, clave del sello y token de
admin) mas una copia `anterior`. Si el Core no arranca, `ARRANCAR_REPLICA.cmd`
levanta la red sobre la replica **en el mismo 8300**, asi que panel, minero y
tunel no cambian. Se niega a arrancar si el 8300 sigue contestando (dos Cores =
dos historias) y recalcula cadena y sellos antes de levantar nada. Desde el
ensayo de negativas del 22-10 tambien se niega si en disco hay un estado MAS
NUEVO que la replica (te dice cuantos asientos perderias; se salta con
`--aceptar-atraso`), si la replica no desciende del ultimo sello del original y
si esta firmada con otra clave.

Se pierde lo que se hiciera despues de la ultima copia; la cadena no se rompe.
Para volver: `python tools/volver_al_original.py` (con todo parado) trae la
cadena de la replica al original, guarda `*.predrill_AAAAMMDD` (o con hora si
ya habia otra vuelta ese dia) y verifica.

Que se niegue bien tambien se prueba: `python tools/prueba_failover_negativas.py`
(replica ausente, vieja, cadena rota, otra clave y bifurcada; 23/23 y no toca el
ledger original).
Detalle, riesgos de tener la clave en `D:` y como volver atras:
[REPLICA_LEDGER.md](REPLICA_LEDGER.md).

Plan completo de lanzamiento y bloqueadores: [PLAN_LANZAMIENTO.md](PLAN_LANZAMIENTO.md).

## Sellos del ledger (integridad) - 19-10-2026

El ledger ya tenia cadena de hashes, pero quien edita el `.db` puede rehacerla
entera y volver a cuadrarla. Ahora el Core sella el historial cada 10 minutos
(o cada 2.000 asientos) con una clave Ed25519 que vive en
`coordinator/sello_key.txt`, **fuera de la base de datos**:

- `coordinator/sellos.jsonl` - un sello por linea (append-only, se puede copiar).
- Los sellos **v2** incluyen `legado`: la huella de los 175 asientos anteriores a
  la cadena (los primeros, sin hash). Asi tambien quedan fijados sin reescribir
  el historial, que invalidaria los sellos anteriores.
- `GET /ledger/integridad` - cadena, ultimo sello y si los pagos estan bloqueados.
- `GET /ledger/sellos` - sellos firmados y la clave publica.
- `python publico/verificar_sellos.py --core http://127.0.0.1:8300` - auditoria
  independiente: codigo 0 = todo cuadra, 1 = alterado (dice que asiento).

Si al arrancar la cadena o un sello no cuadran, el Core **no crea dinero**:
responde 503 en los asientos positivos y deja de generar tareas. Los asientos de
0 (rechazos) y los castigos si se anotan, para no perder el rastro.

Ajustes: `KUKIA_SELLO_SEG` (600 s), `KUKIA_SELLO_ASIENTOS` (2000) y
`KUKIA_SELLO_BLOQUEA=0` para detectar sin bloquear (solo depurar).
Modelo de seguridad completo y limites: [SEGURIDAD.md](SEGURIDAD.md).

## Lo que NO esta hecho

- **Los 10 nodos viejos sin clave publica** no pueden cobrar: hay que
  registrarlos otra vez con clave (el Core no puede inventar su clave privada).
- **El acceso a internet del Core es de solo lectura**: la web ve metricas y
  saldos a traves del rele, pero no se pueden pedir tareas ni registrar nodos
  desde fuera (para eso haria falta abrir la API, y no esta abierta).
- **La publicacion de metricas es un proceso aparte**: si cierras
  `ARRANCAR_METRICAS.cmd` (o reinicias el PC y no lo arrancas), la web se queda
  con el ultimo dato y lo avisa. No se autoarranca todavia.
- Las tareas son pequeñas (milisegundos): la GPU mina, pero no esta al 100 % de
  uso. Es un flujo de trabajo verificado, no computo pesado tipo GRID.
- **Los sellos son locales**: `sellos.jsonl` vive en el mismo PC que la base.
  Sirven para detectar ediciones, no para impedir que quien tiene el PC y la
  `sello_key.txt` fabrique sellos nuevos. Falta copiarlos fuera (web publica,
  git, OpenTimestamps en Bitcoin) y, mas adelante, un segundo Core que valide.
- El respaldo de lo que habia antes de la auditoria esta en
  `_backup_seguridad_20261018-2040\`.

## Sin ventanas (21-10-2026)

Si el PC empieza a abrir ventanas negras de CMD o PowerShell él solo, no es el
navegador ni un virus: son programas de consola lanzados desde procesos que
deberian ser invisibles. En Windows, cada hijo de consola abre **su propia**
ventana aunque el padre no tenga consola. Quedaron tres focos, ya tapados:

| Que | Antes | Ahora |
|---|---|---|
| Guardia preguntando «¿esto ya esta corriendo?» | `powershell` visible cada 20-30 s | `CREATE_NO_WINDOW` |
| Minero leyendo la GPU | `nvidia-smi` visible **cada 3 s** | `CREATE_NO_WINDOW` |
| Publicador de metricas | `cmd /c surge` visible cada 20 min | `CREATE_NO_WINDOW` |
| Copia diaria de las 03:00 | ventana de CMD | `TAREA_BACKUP_OCULTA.vbs` (oculta) |
| Copia semanal de seguridad | no existia | `TAREA_COPIA_SEGURIDAD_OCULTA.vbs` (oculta) |
| Vigilante de la web | no existia | `pythonw` + tarea cada 10 min |

Las dos tareas programadas y como se quitan:

```cmd
schtasks /Query /TN "KUKIA Vigilante Web"       :: vigila la web y avisa/empuja
schtasks /Query /TN "KUKIA Backup Ledger"       :: copia diaria a las 03:00
schtasks /Query /TN "KUKIA Copia Seguridad"     :: copia semanal a N: (domingos)

python tools\vigilante_web.py --quitar          :: quitar el vigilante
schtasks /Delete /TN "KUKIA Backup Ledger" /F   :: quitar la copia diaria
schtasks /Delete /TN "KUKIA Copia Seguridad" /F :: quitar la copia semanal
```

Si alguna vez vuelve a aparecer una ventana, el sospechoso es un
`subprocess.run([...])` nuevo sin `creationflags`: se busca con

```bash
grep -rn "subprocess\.\(run\|Popen\)" --include=*.py . | grep -v creationflags
```
