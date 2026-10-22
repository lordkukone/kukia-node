# KUKIA — multisig 2-de-3: diseño y migración

**Estado: diseñado, NO activado.** Este documento existe para que el paso se
haga con tres dispositivos de verdad, sin romper los 18 sellos ya firmados y
sin fingir seguridad guardando tres claves en el mismo PC.

## Por qué no está activo

Un multisig 2-de-3 significa que **dos firmas independientes** autorizan una
acción. Si las tres claves privadas viven en `coordinator/`, en el mismo disco
que `kukia.db` y `admin_token.txt`, un ladrón las copia las tres y el umbral
2-de-3 se convierte en 3-de-3 para el atacante. Eso es peor que la clave única:
da apariencia de seguridad sin cambiarla.

Para que sea real hacen falta:

1. Tres dispositivos o medios distintos (PC offline, móvil, USB en caja fuerte).
2. Una ceremonia en la que cada clave se genere **en su dispositivo** y solo
   salga de él su clave pública.
3. Un procedimiento probado de pérdida de un dispositivo (2-de-3 sigue
   funcionando) y de rotación de la clave comprometida.

Mientras eso no exista, el ancla publicada en el relé es la protección real:
detecta una reescritura aunque el atacante tenga la clave del sello.

## Qué se firma hoy

- **Sellos** (`ledger/sellos.py`): un sello v2 lleva `v, hasta, asientos, suma,
  ts, prev, legado, hash` y **una** firma Ed25519. La clave privada está en
  `coordinator/sello_key.txt`; la pública se publica en `/ledger/sellos` y en
  el ancla del relé.
- **Transferencias**: una firma de la billetera sobre
  `transferir:<de>:<a>:<monto>:<nonce>`.
- **Administración**: `X-Admin-Token` (un secreto compartido, sin firma).

## Fase 1 — Rotación de la clave del sello con 2-de-3 (recomendada primero)

Objetivo: que **cambiar la clave del sello** exija dos de tres firmas, sin
tocar los sellos ya emitidos.

### Formato del fichero de política

`coordinator/sello_politica.json` (solo claves **públicas**):

```json
{
  "version": 1,
  "umbral": 2,
  "claves": [
    {"id": "A", "publica": "<hex 32 bytes>"},
    {"id": "B", "publica": "<hex 32 bytes>"},
    {"id": "C", "publica": "<hex 32 bytes>"}
  ],
  "actual": "<hex de la clave que firma los sellos nuevos>"
}
```

Las privadas **no** están en este fichero ni en el PC del Core.

### Mensaje de rotación

```
rotar_sello:<clave_publica_actual>:<clave_publica_nueva>:<ts>:<nonce>
```

- `ts` en segundos, ventana de 10 minutos.
- `nonce` de 16 bytes aleatorios, guardado en `nonces_rotacion` (SQLite) para
  que no se repita.
- Cada firma se verifica contra una clave distinta de la política; se exigen
  `umbral` firmas distintas y ninguna de la clave nueva.

### Endpoint (cuando se implemente)

```
POST /admin/rotar_sello
{
  "nueva_clave_publica": "...",
  "ts": 1792499999,
  "nonce": "...",
  "firmas": [
    {"clave_publica": "...", "firma": "..."},
    {"clave_publica": "...", "firma": "..."}
  ]
}
```

El Core:

1. Comprueba `X-Admin-Token` **y** las dos firmas (doble control).
2. Verifica que `actual` y `nueva` son Ed25519 válidas y distintas.
3. Guarda la política nueva y la clave privada nueva **fuera de la base**
   (`sello_key.txt` se sustituye de forma atómica).
4. Escribe un sello de transición con la clave nueva que incluye
   `rotacion: {desde, hasta, ts, firmas}`; los sellos viejos se siguen
   verificando con la clave pública antigua que queda en el historial.
5. Nunca reescribe sellos anteriores.

### Compatibilidad

- Los 18 sellos actuales son v2 firmados por la clave única. El verificador
  debe aceptar v2 con la clave que cada sello declara (o la clave histórica
  publicada) y v3 con umbral.
- `verificar_sellos.py --publicado` ya compara el ancla; tras la rotación hay
  que aceptar que el ancla nueva encadene con la anterior, no que sea igual.

### Pruebas mínimas antes de activar

1. Con 1 firma → 401; con 2 firmas de la misma clave → 401; con 2 claves
   distintas → 200.
2. Nonce repetido → 409; `ts` viejo → 401.
3. Sello nuevo verifica con la clave nueva; los 18 anteriores siguen OK.
4. Reinicio del Core: la política y la clave nueva persisten.
5. Copia de seguridad y restauración con la política incluida.

## Fase 2 — Sello con umbral (2 firmas por sello)

Es la versión completa, y la que tiene un coste operativo real: el Core **no**
puede firmar solo cada 10 minutos. El flujo sería:

1. El Core crea un sello **sin firma** (`prev`, `hasta`, `asientos`, `suma`,
   `legado`, `hash`) y lo deja en `sellos_pendientes.jsonl`.
2. Un dispositivo offline lo revisa y firma; otro distinto firma también.
3. Se suben las firmas (`POST /admin/sellos/firmar`) y el Core escribe el sello
   v3 en `sellos.jsonl` cuando hay 2.
4. Mientras no haya 2 firmas, **no se bloquean los pagos**: el ledger sigue
   funcionando, pero el ancla publicada se retrasa. Hay que decidir si un sello
   pendiente de más de N horas bloquea nuevas emisiones (recomendado: avisar en
   el panel, no bloquear, para no dejar la red parada por un dispositivo
   apagado).

Formato v3 propuesto:

```json
{
  "v": 3,
  "hasta": 88151,
  "asientos": 87976,
  "suma": 15083452.1061,
  "ts": 1792492621,
  "prev": "...",
  "legado": {"hasta": 175, "n": 175, "hash": "...", "suma": 15000802.2563},
  "hash": "...",
  "firmas": [
    {"id": "A", "clave_publica": "...", "firma": "..."},
    {"id": "B", "clave_publica": "...", "firma": "..."}
  ]
}
```

`hash` se calcula sobre el cuerpo sin `firmas` (y sin `hash` si se mantiene la
regla actual de no incluir el propio hash; hay que fijarlo en una prueba de
compatibilidad antes de emitir el primer v3).

## Fase 3 — Límite diario y aprobación de movimientos grandes

Propuesta concreta, sin cambiar el modelo de dinero:

- Tabla `gasto_diario(cuenta, dia, total)`.
- `KUKIA_GASTO_DIARIO_KIA` (0 = sin límite). Si una transferencia supera el
  límite de la cuenta en el día, se rechaza con 429 y un mensaje claro.
- Para superarlo: `POST /transferir/aprobada` con la firma normal **y** dos
  firmas de la política 2-de-3 sobre
  `transferir_aprobado:<de>:<a>:<monto>:<nonce>:<ts>`.
- La aprobación caduca en 10 minutos y el nonce es de un solo uso.
- El límite se aplica **antes** de `BEGIN IMMEDIATE`; la aprobación se guarda
  en la misma transacción que mueve el dinero.

Esto protege una clave de billetera robada sin obligar a firmar cada café. No
protege contra crear billeteras nuevas (el dinero es fungible), así que no
sustituye al multisig del sello.

## Orden de implementación

1. Política + endpoint de rotación (Fase 1) y sus pruebas. **No** cambiar el
   formato de los sellos actuales.
2. Herramienta `tools/ceremonia_multisig.py` para generar las 3 claves en tres
   dispositivos y verificar las claves públicas.
3. Sello v3 con umbral (Fase 2), solo después de probar la compatibilidad v2/v3
   sobre una copia.
4. Límite diario + aprobación (Fase 3), con límite por defecto desactivado
   hasta que el usuario lo configure.

## Rollback

- Fase 1: restaurar `sello_politica.json` y `sello_key.txt` desde la copia
  verificada; los sellos nuevos firmados con la clave nueva siguen siendo
  válidos, pero el Core vuelve a firmar con la vieja. Hay que documentar la
  rotación inversa con otras dos firmas.
- Fase 2: volver a firmar con la clave única no es posible sin reescribir; por
  eso el sello v3 debe entrar con una prueba en copia y un periodo de
  convivencia, no de golpe.
- Fase 3: `KUKIA_GASTO_DIARIO_KIA=0` desactiva el límite sin tocar datos.

## Lo que este documento NO hace

- No crea claves ni activa multisig.
- No cambia el formato de los sellos actuales.
- No promete que el relé sea independiente: sigue siendo del mismo dueño.
