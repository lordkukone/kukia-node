# Anclaje en Bitcoin (OpenTimestamps)

## El problema que resuelve

Hasta hoy el ancla del ledger la publicaba el relé, y el relé es del mismo dueño
que produce los sellos. Eso sirve para **detectar** una edición del historial,
no para impedirla: quien controle ese PC puede reescribir la base, rehacer la
cadena, firmar sellos nuevos y publicar el ancla nueva. Todo "cuadra".

Con OpenTimestamps el sha256 del sello viaja a **tres calendarios
independientes** que lo meten en un árbol de Merkle y lo atan a un bloque de
Bitcoin. A partir de ese momento la fecha del sello la garantiza una red que no
es tuya.

## Cómo se usa

```bash
python tools/anclar_ots.py                 # ancla el último sello si es nuevo
python tools/anclar_ots.py --actualizar    # ¿ya hay bloque? fusiona la prueba
python tools/anclar_ots.py --verificar     # comprueba contra el bloque real
python tools/anclar_ots.py --estado        # qué hay anclado y cómo va
python tools/anclar_ots.py --autoprueba    # prueba la verificación ahora mismo
```

La guardia lo hace sola cada 10 minutos: ancla el sello nuevo y completa las
pruebas pendientes. No hace falta acordarse de nada.

## Qué se guarda

| Fichero | Qué es |
|---|---|
| `anclas/kukia_sello_<N>.txt` | el sello en texto: hasta, asientos, suma, hash y firma |
| `anclas/kukia_sello_<N>.txt.ots` | la prueba OpenTimestamps (formato oficial) |
| `anclas/registro.jsonl` | una línea por anclaje: digest, calendarios, fecha |
| `anclas/cabeceras/<altura>.hex` | cabecera del bloque, para verificar sin red |

El `.txt` no se puede tocar después: `--verificar` recalcula su sha256 y, si no
coincide con el anclado, lo dice (`el .txt NO coincide con el anclado`). **Eso es
justo lo que se quiere**: el ancla deja de valer si alguien edita el contenido.

## Cómo se verifica (sin confiar en este PC)

1. `--actualizar` pregunta a los calendarios hasta que uno devuelve la ruta a un
   bloque. Normalmente tarda entre 1 y 2 horas.
2. `--verificar` baja la cabecera de ese bloque (mempool.space o blockstream),
   toma la raíz de Merkle (bytes 36-68 de la cabecera) y comprueba que coincide
   con la que sale de aplicar las operaciones de la prueba al sha256 del `.txt`.
3. También vale el cliente oficial de OpenTimestamps:
   `ots verify anclas/kukia_sello_<N>.txt.ots`.

## Estado real hoy (20-10-2026 15:48)

Tres sellos anclados, pendientes de bloque:

| Sello | Asientos | Digest (sha256) | Calendarios |
|---|---|---|---|
| 92.551 | 92.376 | `5011dc9401172534…` | 3 |
| 94.380 | 94.205 | `5b7b7e7147f476ba…` | 3 |
| 97.199 | 97.024 | `a8d489108274fe0e…` | 3 |

El tercero lo anclo la guardia sola, sin que nadie ejecutara nada a mano.

La verificación ya está probada contra un bloque real (el 972.331): una prueba
con la raíz correcta se acepta y una con raíz falsa se rechaza
(`--autoprueba`, salida 0). Lo que falta es que Bitcoin mine el bloque que
incluya estos dos: entonces `--actualizar` los marca CONFIRMADO.

## Nota sobre el cliente `ots`

El ejecutable oficial no arranca en este Windows: `bitcoin.rpc` falla al cargar
la DLL de secp256k1 (`WinError 193`). Por eso la herramienta usa la librería
`opentimestamps` directamente y verifica la raíz de Merkle contra la cabecera
del bloque. El `.ots` que escribe es el formato estándar, así que se puede
verificar con el cliente oficial en Linux o macOS.
