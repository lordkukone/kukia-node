# Réplica del ledger en otro disco

Actualizado: 22/10/2026 · Comprobado en caliente contra el Core del 8300

## El problema que resuelve

El ledger vive en `C:\...\KUKIA\coordinator\ledger\kukia.db` y **las copias de
`backups/` también están en `C:`**. Si ese disco muere, mueren las dos a la vez.
No había nada fuera de `C:` con el historial completo y listo para arrancar.

La réplica es una copia **coherente, verificada y comparada con el Core vivo**
en otro disco físico (`D:` por defecto), con la clave del sello y el token de
admin al lado, para que un Core nuevo pueda levantar la red sin pedir nada más.

## Lo que hay

```
D:\KUKIA_REPLICA\
  actual\      kukia.db + sellos.jsonl + sello_key.txt + admin_token.txt + replica.json
  anterior\    la copia buena de la vez anterior (una sola)
```

Cabe en 12 s y 236 MB. `replica.json` guarda fecha, sha256 del `.db`, asientos,
suministro, sellos, último sello y el resultado de la comparación.

## Cómo se usa

```bash
# 1) Copiar y verificar (con el Core en marcha, no hay que pararlo)
python tools/replica_ledger.py

# 2) ¿Sigue siendo la misma red que el Core?  (exit 0 = sí)
python tools/replica_ledger.py --comparar

# 3) Foto de lo que hay
python tools/replica_ledger.py --estado
```

Si un día el Core no arranca:

```
ARRANCAR_REPLICA.cmd        (doble clic; o python tools\arrancar_replica.py)
```

Arranca la red **en el mismo puerto 8300**, así que el panel, el minero y el
túnel siguen igual, sin cambiar ni una línea. Antes de levantar nada:

1. Exige que el manifiesto diga `verificado: true`.
2. **Se niega si el 8300 sigue contestando** (dos Cores = dos historias = doble
   gasto). Solo se salta con `--forzar`, y solo si sabes que es un zombi.
3. Recalcula la cadena y los sellos de la réplica. Si no cuadran, no arranca.

## Arrancar en un puerto libre (sin pisar el túnel)

El 8300 es el puerto bueno **para una recuperación de verdad**: el panel, el
minero y el túnel lo dan por hecho y no hay que cambiar nada. Pero mientras el
Core principal siga vivo no se puede usar (dos Cores a la vez son dos
historias), y para **probar** que la réplica arranca no se debe ocupar: se usa
un puerto libre y se deja el 8300 en paz.

### Qué ocupa cada puerto

| Puerto | Quién | Qué es |
|---|---|---|
| **8300** | Core (`uvicorn`) | el que hablan el panel, el minero y el túnel |
| **8399** | `tools/tunel_estable.py` | cerrojo de instancia única: un socket que el supervisor del túnel abre y deja abierto para que no puedan correr dos. **No sirve tráfico.** Se cambia con `KUKIA_TUNEL_PUERTO` |
| otro cualquiera | túnel `cloudflared` | **ninguno**: `cloudflared tunnel --url http://127.0.0.1:8300` sale hacia trycloudflare, no abre puerto local. Lo que aparece escuchando en 8399 es el cerrojo de arriba, no el túnel |

Consecuencia práctica: si el 8399 está ocupado, ya hay un supervisor del túnel
vivo (es lo normal). Y si te hace falta arrancar la réplica con el túnel
levantado, el cerrojo no estorba: solo molesta el 8300.

### Cómo pedir un puerto libre y arrancar

```bash
# 1) Que el sistema te dé uno libre (y lo cierra al instante)
python -c "import socket;s=socket.socket();s.bind(('127.0.0.1',0));print(s.getsockname()[1]);s.close()"

# 2) Arrancar la réplica ahí, aceptando el atraso a propósito
python tools\arrancar_replica.py --destino D:\KUKIA_REPLICA --puerto 61405 --aceptar-atraso
```

- `--puerto` es solo para esto: **una prueba**. El panel, el minero y el túnel
  siguen apuntando al 8300, así que no verán nada nuevo. En un failover real se
  deja el valor por defecto (`8300`).
- `--aceptar-atraso` hace falta porque la guardia 1 se niega a arrancar si en
  disco hay un estado **más nuevo** que la copia, y con el Core vivo eso siempre
  pasa. Aceptarlo en una prueba es correcto; en una recuperación significa que
  pierdes lo posterior a la copia, así que se acepta sabiendo qué se pierde.
- El Core escucha en `127.0.0.1`, no en `0.0.0.0`: el puerto no queda expuesto a
  la red local por el simple hecho de arrancarlo.
- Si el puerto está cogido, uvicorn sale con `[winerror 10048]`. Otra pista de
  quién lo tiene: `netstat -ano | findstr :8300`.

### La prueba automática (recomendada)

```bash
python tools\prueba_arranque_replica.py            # réplica de D: por defecto
python tools\prueba_arranque_replica.py --origen D:\KUKIA_REPLICA --conservar
```

Hace todo lo anterior sin que tengas que acordarte: copia la réplica a `%TEMP%`,
coge un puerto libre, arranca, comprueba 16 cosas (suministro y asientos de la
copia, `cadena_ok`, clave pública y sello del **original**, `/admin/clientes`
200 con tu token y 403 con uno falso, que no se invente un `admin_token.txt`,
que `/ledger/verificar` siga siendo público), mata el árbol del proceso, borra la
copia y verifica que `D:` sigue byte a byte igual. Si el 8300 estaba vivo al
empezar, exige que siga vivo al terminar. Sale con 0 si todo pasa.

## Qué comprueba la comparación (y por qué así)

| Comprobación | Qué caza |
|---|---|
| La cadena de la réplica verifica sola | un `.db` tocado a mano o una copia a medias |
| Suministro de la réplica **no mayor** que el del Core | una réplica de otra red (o de un fork) |
| Asientos de la réplica **no más** que los del Core | historial que el Core no tiene |
| El último sello de la réplica **lo ha publicado el Core** | copia de otro día promocionada por error |

Si la réplica va *por detrás* unas décimas de KIA es lo normal (se copió hace un
rato): eso es aviso, no error. Si va por delante, no se promociona.

## Lo que se pierde al promocionar (y lo que no)

- **Se pierde**: lo que el Core principal hiciera **después** de la última copia
  (tareas en vuelo y los pagos de ese rato). Ni un céntimo se duplica: esos
  asientos no están en la réplica.
- **No se pierde**: el historial. La réplica continúa **desde su último sello
  firmado**, con la misma cadena y la misma clave, así que los verificadores
  externos y el anclaje en Bitcoin siguen cuadrando.

## Volver al original

1. Para el Core de la réplica (`Ctrl+C` en su ventana).
2. `python tools/volver_al_original.py` — trae la cadena de la réplica al
   original: comprueba que nadie contesta en 8300, guarda el original como
   `kukia.db.predrill_AAAAMMDD`, sustituye el `.db` y los sellos y **verifica
   cadena y firmas ya en su sitio**. Con `--ver` solo comprueba, no escribe.
3. Arranca el Core de siempre desde `coordinator/`.
4. **Nunca los dos a la vez.** El lanzador ya lo impide, pero conviene saberlo:
   dos Cores con la misma clave y ledgers distintos son dos historias firmadas.

Si prefieres no traer nada (la réplica solo sirvió para no quedarte sin red),
salta el paso 2: el original sigue como estaba, con la historia de antes del
fallo. Lo que se minara sobre la réplica se pierde, y por eso el paso 2 es lo
normal.

## Seguridad: qué implica tener la clave en `D:`

La réplica lleva `sello_key.txt` y `admin_token.txt` para poder arrancar sola.
Eso significa que **`D:\KUKIA_REPLICA` tiene el mismo nivel de secreto que
`C:\...\coordinator\`**: quien lea esa carpeta puede firmar historia nueva y
administrar el Core. No la subas a la nube ni la dejes en un disco compartido.
Si prefieres una réplica sin secretos (solo para auditar), usa
`python tools/backup_ledger.py --sin-clave`.

## Automatizarlo (opcional, tú decides)

La copia de las 03:00 (`TAREA_BACKUP.cmd`, tarea «KUKIA Backup Ledger») ya existe
y apunta a `backups/` en `C:`. Para mantener la réplica fresca cada día, añade
esta línea al final de `TAREA_BACKUP.cmd`:

```bat
"%PY%" -u tools\replica_ledger.py >> backups\backup.log 2>&1
```

Si `D:` no está montado, la línea falla y lo escribe en el log, pero **no toca**
la copia de `C:` (que ya se hizo antes). No lo he activado yo para no cambiar
tus tareas programadas sin decírtelo.

## Lo que se probó el 22/10/2026

| Prueba | Resultado |
|---|---|
| Copia + verificación de cadena y sellos | 145 sellos, 264.984 asientos, suministro `15085117.198325` |
| Comparación con el Core vivo | idénticos: suministro, asientos y sellos |
| Lanzador con el Core principal vivo | **se niega** (exit 4) |
| Arranque sobre una copia de la réplica (puerto libre) | `/stats` 200, mismo suministro, `cadena_ok=true`, pagos activos |
| Token de admin copiado | crea tareas (200); sin token sigue cerrado (403) |
| Cadena rota o réplica de otra red | no se promociona (exit 5) |

Lo que **no** está probado: un fallo real de `C:` con `D:` en otra máquina, y el
caso de dos discos muriendo a la vez. Contra eso solo hay una solución de
verdad: una segunda réplica en otra máquina, no en otro disco del mismo PC.

---

## Ensayo de failover de verdad (22/10/2026, 10:19–10:35)

No fue simulacion: se apago el Core real, se levanto la red sobre la replica, se
mino de verdad y se volvio al original. Cronologia y numeros:

| Paso | Resultado |
|---|---|
| Copia verificada previa (`backups/kukia_20261022_101849`) | 145 sellos, suministro `15085117.198325` |
| `Stop-Process` del Core principal (PID 32636) | 8300 libre, la red se queda sin Core |
| `arrancar_replica.py` sobre `D:` | `/stats` 200 con el **mismo** suministro, asientos y hash de sello |
| Minero real arrancado contra la replica | 363 tareas pagadas (`+11,21 KIA`), **0 fallos**, las dos GPUs |
| Tunel de Cloudflare nuevo hacia 8300 | **403 «Host no permitido»** — ver abajo |
| Tunel tras el arreglo | HTTP 200 en `/stats`, `/ledger/integridad` y `/saldo` |
| Vuelta al original | replica → `C:` con la cadena verificada, Core arrancado en `C:` |
| Estado final | suministro `15085128.411941`, 244.110 tareas, 147 sellos, pagos activos |

### El fallo que encontro el ensayo

El Core solo aceptaba `Host` de loopback o los de `KUKIA_HOSTS`. Al arrancar la
replica (o al abrir un tunel nuevo) **todo lo publico devolvia 403** y la red
parecia caida aunque estuviera sirviendo: el panel y el minero en local
funcionaban, la web no. Nadie lo habia visto porque la ultima vez el Core se
arranco con el host del tunel ya metido a mano.

Arreglo: el Core acepta ahora los dominios de tunel de Cloudflare **por sufijo**
(`KUKIA_HOSTS_SUFIJOS`, por defecto `trycloudflare.com,cfargotunnel.com`) y
sigue cortando todo lo demas (`evil.example` → 403; `nottrycloudflare.com` → 403,
que solo *acaba* igual). No abre DNS rebinding: esos nombres resuelven en
Cloudflare y solo llegan aqui por **nuestro** tunel.

Probado en `tools/pruebas_caos.py`, seccion **F. HOST**: 4 casos, 37/37 en total.

### Lo que se guardo del estado anterior (nada se borro)

- `backups/kukia_20261022_101849/` — copia verificada antes de empezar.
- `coordinator/ledger/kukia.db.predrill_20261022` — el `.db` original tal cual.
- `coordinator/sellos.predrill_20261022.jsonl` — sus sellos.
- `D:\KUKIA_REPLICA\actual` y `...\anterior` — las dos ultimas copias.

### Lo que costo

Nada: las 363 tareas y los 11,21 KIA del ensayo se conservaron. La vuelta no fue
"descartar la replica", sino **traer su cadena al original** (misma cadena,
mismos sellos, verificado antes y despues). El Core volvio a `C:` con todo.

La vuelta se hizo primero a mano y luego se empaquetó en
`tools/volver_al_original.py` (probado en un sandbox: se niega con el Core vivo,
verifica antes y después, y deja las copias `*.predrill_AAAAMMDD`).

Queda apuntado para el próximo ensayo: pararlo con `D:` desenchufado de verdad
para ver el mensaje que sale cuando la replica no esta, y probar el caso de la
replica vieja (una copia de hace días) para ver que la comparación lo canta.

---

## Ensayo de NEGATIVAS (22/10/2026, 10:52–11:06)

El ensayo de la mañana probó el camino bueno. Este prueba lo que de verdad
rompe: que el sistema se niegue. Se hizo **con el Core real vivo en el 8300**,
sobre copias de verdad (la réplica de `D:` y la copia del 20/10), sin tocar el
ledger original ni lanzar ningún Core falso con la clave buena.

| Caso | Qué se le pide | Qué hace ahora |
|---|---|---|
| Réplica **desconectada** (`Q:` sin montar) | copiar, comparar, arrancar, volver | los cuatro dicen qué pasa y salen con exit 2. **Antes**: `replica_ledger.py` soltaba un `Traceback` de Python |
| Réplica **vieja** (copia del 20/10: 88.151 asientos, 1.676 KIA menos) | comparar | exit 6, «misma red, pero **ATRASADA**». **Antes**: decía «NO lo ha publicado el Core (réplica de otra historia)», que es un diagnóstico falso: era la misma red, solo vieja |
| Réplica vieja | arrancar | **NO arranca** (exit 7) y dice qué se perdería: `177.244 asientos y 1.676,305847 KIA`. Sigue solo con `--aceptar-atraso`. **Antes**: arrancaba la red dos días atrás sin decir nada |
| Cadena **rota** (un asiento tocado a mano) | arrancar / volver | exit 5 / exit 4. Sin cambios: ya estaba bien |
| Réplica de **otra red** (sellos re-firmados con otra clave) | arrancar | exit 9, «firmada con OTRA clave». **Antes**: pasaba, porque se verificaba **con la clave que la réplica traía**: otra moneda habría servido desde tu 8300 |
| Réplica **separada** (5 asientos más que el original, pero sin su último sello) | arrancar / volver | exit 8 / exit 7, «dos historias». **Antes**: `volver_al_original.py` solo miraba el número de asientos y la habría promocionado |
| Réplica **de hoy** | comparar / arrancar | exit 0 y arranca: el camino bueno sigue igual |

Los tres últimos son historiales fabricados **a propósito** en copias del sandbox
(una clave nueva y unos asientos sintéticos de 0 KIA): no se ha bifurcado nada
real ni se ha firmado historia nueva con la clave de verdad.

### Códigos de salida (para no adivinar)

```
replica_ledger   --comparar: 0 misma red al día · 6 misma red atrasada · 1/5 no es esta red · 2 sin réplica
arrancar_replica:            0 ok · 2 sin réplica · 3 manifiesto no verificado · 4 Core vivo · 5 cadena/sellos
                             6 falta clave · 7 hay estado más nuevo en disco · 8 no desciende · 9 otra clave
volver_al_original:          0 ok/nada que traer · 2 sin réplica · 3 Core vivo · 4 réplica no cuadra
                             5 copia distinta · 6 no cuadra ya en su sitio · 7 no desciende del original
```

### Repetirlo

```bash
python tools/prueba_failover_negativas.py        # 23/23, con el Core vivo
python tools/prueba_failover_negativas.py --base E:/otro_sitio
```

Necesita el Core en el 8300 para los casos que comparan (si no contesta, esos se
omiten). Deja las copias en `D:/KUKIA_ENSAYO_20261022/negativas_AAAAMMDD_HHMMSS`
(≈950 MB por pasada): son de usar y tirar, se pueden borrar cuando quieras.

### Lo que sigue SIN probar (y no lo voy a jurar)

- **`D:` desenchufado de verdad** (hardware). Se ha simulado con una letra que
  no existe, que produce el mismo error de sistema, pero no es lo mismo que
  quitar el cable.
- **El caso de verdad**: `C:` muerto. Ahí no hay original con el que comparar ni
  clave con la que contrastar; los avisos de los pasos 7/8/9 desaparecen y la
  réplica arranca en modo «confío en ti». La protección real contra eso no es
  software: es una segunda copia en **otra máquina**.
- **Réplica más vieja que 200 sellos** (≈200 días): el Core solo publica los 200
  últimos, así que ya no se puede demostrar que sea la misma red y dirá que no.
  Es conservador, pero es un «no» que en un desastre real puede doler.
