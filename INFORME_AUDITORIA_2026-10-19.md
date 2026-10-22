# Paquete de auditoría — KUKIA

**Fecha:** 19-10-2026
**Versión auditada:** Core con sellos firmados (`sha256` de cada fichero más abajo)
**Preparado para:** auditoría externa por otra IA
**Naturaleza del sistema:** moneda experimental de una sola máquina, con
verificación de trabajo y ledger propio. No es una blockchain y no pretende serlo.

---

## 0. Cómo leer esto (y qué NO creer)

Este documento lo ha escrito el mismo agente que implementó los cambios. **No es
una auditoría**: es el paquete de evidencias para que la auditoría la hagas tú.

Todo lo que se afirma aquí es comprobable con los comandos de la sección 8. Si
algo no cuadra, gana la comprobación, no el texto.

Números y hashes son una foto del **19-10-2026 ~14:45**. La emisión sigue
corriendo, así que `supply`, `tareas_ok` y el número de sellos habrán cambiado:
compáralos con `/stats`, no con este documento.

---

## 1. Qué es KUKIA

- **Core** (FastAPI + SQLite en un solo PC): reparte tareas, **recalcula cada
  resultado** y paga desde un presupuesto de emisión fijo.
- **Minero** (Python + Torch, un hilo por GPU): pide tareas, las calcula y
  entrega el resultado firmado.
- **Billetera** (HTML + TweetNaCl en el navegador): claves Ed25519, firma
  transferencias, nunca envía la clave privada.
- **Ledger**: tabla SQLite con cadena de hashes + sellos firmados.
- **Web pública** (`docs/`): estado, saldos, código fuente completo y zip
  descargable.

No hay red P2P, ni consenso, ni mempool, ni bloques. Hay **un coordinador que
verifica** y un libro de cuentas encadenado.

---

## 2. Estado medido en vivo

| Dato | Valor |
|---|---|
| Suministro emitido | 15.083.364,57 KIA de 100.000.000 |
| Fin de emisión previsto | 13-04-2150 (halving ~2046) |
| Emisión objetivo | 1,3075 KIA/min |
| Pago por tarea (ese momento) | 0,0673 KIA |
| Nodos registrados | 34 (activos reales: 2 GPUs) |
| TOPS de la red (activos 24 h) | 200 |
| Asientos en el ledger | ~85.200 encadenados |
| Sellos firmados | 9 (5 en v1 y el resto en v2, con el tramo legado) |
| Cadena / sellos | `cadena_ok: true`, `sellos_ok: true` |
| Pagos bloqueados | `false` |
| Clave pública del sello | `2013664e12b4ac93f5a266ae5e1a4a071f51c68112e1209fae5d501ecb2f8d1c` |

Tareas por minuto y premio se mueven porque un control ajusta el tamaño de la
tarea para tardar ~2 s (`KUKIA_TAREA_MS`), sin cambiar el presupuesto de emisión.

---

## 3. Mapa del código

| Fichero | Líneas | Papel |
|---|---:|---|
| `coordinator/kukia_core.py` | 1749 | Core: API, verificación, ledger, emisión, sellos |
| `worker/kukia_worker.py` | 307 | Minero multi-GPU |
| `ledger/kukia_wallet.py` | 215 | Billetera CLI (Ed25519, PBKDF2) |
| `ledger/sellos.py` | 295 | Sellos firmados y verificador (**nuevo 19-10**) |
| `publico/verificar_sellos.py` | 99 | Auditor independiente (**nuevo 19-10**) |
| `publico/publicar_metricas.py` | 202 | Publica métricas al relé |
| `publico/relay.js` | 202 | Relé Cloudflare (**sin desplegar**) |
| `docs/wallet.html` | 319 | Billetera web (claves en memoria) |
| `docs/index.html` | 633 | Web pública |

Hashes SHA-256 (para que verifiques que auditas lo mismo):

```
a926cdc94b94ca111e61c439dec57e9600ac5d765472f13b867df1ee9a2048bd  coordinator/kukia_core.py
9109e3f5da209a93c017cb85611fa02dc154ae243ba8745b68b988c79d5ae812  ledger/sellos.py
daa5588b020985edce6cd5b4949b318e95ef94eae3ac646062baa1aed0374f85  publico/verificar_sellos.py
094a8862b07d2f80c1cd7d4cca683238e5ad248ff6b154e1756e7fbfc5db3a31  worker/kukia_worker.py
39bd14473b36887bf7f672c1fe1b01e042051c9851b837425c9a45fe39b2695c  ledger/kukia_wallet.py
399e76a7f1d8fcaa92e64293325823108adcc0a3a5372466e0756d540f83857a  docs/wallet.html
0b210c2abb0c7f558d8ed3db1d4b9354b7eb53098f7c6eeef0bc00c07231ed0c  publico/relay.js
```

---

## 4. Invariantes que deben cumplirse

Son las propiedades que el sistema **pretende** garantizar. Si encuentras un
camino que las rompa, ese es el hallazgo.

- **I1 — Ningún asiento se escribe fuera de `_asiento()`.** Es el único punto de
  escritura del ledger y el que encadena hashes. (Comprueba todos los `INSERT`
  en `ledger`.)
- **I2 — Todo pago exige verificación previa.** El Core recalcula la tarea y
  compara; si no coincide, el asiento es 0.
- **I3 — Todo resultado exige firma del nodo** sobre `"{tid}:{metrica}"`.
- **I4 — Toda petición de trabajo exige firma** sobre
  `"pendiente:{nodo_id}:{int(ts)}"` con ventana de 300 s.
- **I5 — El pago lo fija el Core.** Ningún campo del cliente (más allá de qué
  tarea entrega) influye en el monto.
- **I6 — La cadena del ledger cuadra**: cada `hash = sha256(tarea|nodo|kuki|motivo|ts|hash_prev)`.
- **I7 — El historial coincide con el último sello firmado** (hash, número de
  asientos y suma), o el Core no paga.
- **I8 — La clave del sello no está en la base de datos** (`coordinator/sello_key.txt`).
- **I9 — La billetera no persiste la clave privada** (solo dirección y URL del
  Core en `localStorage`).

---

## 5. Modelo de confianza (lo que se asume, no se demuestra)

1. **El PC del Core es honesto.** Quien tiene acceso a disco puede leer
   `admin_token.txt`, `sello_key.txt` y `kukia.db`.
2. **La clave del sello no se filtra.** Si se filtra, se pueden firmar sellos
   nuevos (aunque no reescribir los ya publicados fuera).
3. **El reloj es razonable.** Las firmas de trabajo usan ventanas de 300 s.
4. **Nadie modifica el código en ejecución.** No hay atestación ni firma de
   binarios.
5. **El dinero vale lo que la gente crea que vale.** No hay respaldo ni mercado.

Lo que **no** se asume: que el minero sea honesto (se recalcula), que el cliente
diga la verdad sobre su hardware (se acota), ni que la web pública sea confiable
(es escaparate; el estado real es el Core).

---

## 6. Superficie de ataque y defensa

| Vector | Defensa | Dónde |
|---|---|---|
| Minero miente en el resultado | Recálculo en el Core + castigo | `resultado()` |
| Minero entrega por otro nodo | Firma Ed25519 + nodo registrado | `resultado()` |
| Replay de una entrega | Estado de la tarea + `verificado`, `doble` | `resultado()` |
| Robo de la cola | Firma obligatoria en `pendiente` | `pendiente()` |
| Inflar dificultad con TOPS falsos | Tope 100.000 + solo nodos vistos 24 h | `dificultad()` |
| Parámetros gigantes que tumban el Core | `LIMITE_N`, `LIMITE_PASOS`, 64 KB de params | `crear`/`resultado` |
| Quemar CPU sin clave | Autenticar **antes** de recalcular | `resultado()` |
| DNS rebinding | Validación de `Host` y `Origin` | middleware |
| Editar saldos a mano | Cadena de hashes + sellos + bloqueo | `_asiento`, `_verificar_integridad` |
| Doble gasto en transferencias | `UNIQUE(de_nodo, nonce)` + firma + transacción | `transferir()` |
| Desviar el cobro de un nodo | Firma sobre `cobro:nodo:direccion:ts` | `actualizar_cobro()` |
| Hijack de identidad al registrarse | El id lo genera el Core (`uuid4`), no el cliente | `registrar()` |
| Robo de claves en la web | Claves derivadas en memoria, nunca en `localStorage` | `wallet.html` |

---

## 7. Cambio de hoy: sellos firmados (lo que hay que auditar con lupa)

### 7.1 Motivación

La cadena de hashes detecta un asiento editado, pero **quien edita puede
reescribir la cadena entera** y volver a cuadrarla. Los sellos fijan el pasado.

### 7.2 Formato exacto de un sello

JSON canónico (`sort_keys`, separadores `,` y `:`, `ensure_ascii=False`):

```json
{"asientos":85649,"firma":"<hex 128>","hash":"<hex 64>","hasta":85824,
 "prev":"<hex 64>","suma":15083385.781,"ts":1792414267,"v":2,
 "legado":{"hash":"<hex 64>","hasta":175,"n":175,
            "suma":15000802.2563}}
```

- `hasta`: id del último asiento con hash en ese momento.
- `hash`: su `hash` de cadena.
- `asientos`: cuántos asientos con hash hay en total (no solo hasta `hasta`).
- `suma`: `SUM(kuki)` de todos los asientos con `id <= hasta`, redondeado a 4
  decimales.
- `prev`: `sha256(canonico(sello_anterior sin "firma"))`, o `""` en el primero.
- `firma`: Ed25519 sobre el JSON canónico **sin** el campo `firma`.
- `legado` (solo v2): huella de los **175 asientos anteriores a la cadena**
  (sin hash). `hash = sha256` incremental sobre
  `id|tarea|nodo|kuki|motivo|ts` de cada fila. Los sellos v1 (los 5 primeros) no
  lo llevan y siguen siendo válidos; a partir del v2, editar cualquier asiento
  de ese tramo rompe el sello aunque la cadena siga cuadrando.

### 7.3 Cuándo se sella

- Al arrancar, si no hay ninguno.
- Cada `KUKIA_SELLO_SEG` (600 s) **si hay asientos nuevos**.
- Al acumular `KUKIA_SELLO_ASIENTOS` (2.000) desde el último.

Ficheros: `coordinator/sellos.jsonl` (una línea por sello, `fsync` tras escribir)
y `coordinator/sello_key.txt` (semilla hex de 64 caracteres).

### 7.4 Qué pasa si no cuadra

Al importar el módulo, `_verificar_integridad()`:

1. Recalcula la cadena completa.
2. Verifica firmas y encadenado de todos los sellos.
3. Comprueba cada sello contra el `.db` (hash, asientos, suma).
4. Compara `meta.sellos_n` con los sellos del fichero (detecta borrado).
5. Si algo falla: `LEDGER_ROTO = True` → **503 en asientos positivos**, 503 en
   transferencias, y el generador de tareas se detiene. Los asientos de 0 y los
   castigos sí se anotan.

### 7.5 Lo que este diseño NO hace

- No impide editar el `.db`: lo **detecta**.
- No protege contra quien tenga a la vez el `.db` y `sello_key.txt` y además
  borre los sellos publicados fuera de la máquina.
- No ancla nada en una cadena externa (pendiente).

---

## 8. Cómo reproducir (sin tocar la base real)

```bash
cd C:/Users/kuki/Desktop/KUKIA

# Estado
curl -s http://127.0.0.1:8300/ledger/integridad
curl -s http://127.0.0.1:8300/ledger/sellos?limite=5
curl -s http://127.0.0.1:8300/ledger/verificar
curl -s http://127.0.0.1:8300/stats

# Auditoría independiente
python publico/verificar_sellos.py --core http://127.0.0.1:8300
python publico/verificar_sellos.py --db <copia>/kukia.db --sellos <copia>/sellos.jsonl

# Prueba de manipulación (SIEMPRE sobre copia)
cp coordinator/ledger/kukia.db /tmp/copia.db
python - <<'EOF'
import sqlite3, sys
sys.path.insert(0, ".")
from ledger import sellos as S
con = sqlite3.connect("/tmp/copia.db"); con.row_factory = sqlite3.Row
con.execute("UPDATE ledger SET kuki=kuki+1000 WHERE id=(SELECT MIN(id) FROM ledger WHERE hash<>'')")
con.commit()
print(S.verificar_cadena_db(con)["ok"], S.verificar_sellos(con, S.leer_sellos("coordinator/sellos.jsonl"), S.publica_de(S.leer_clave("coordinator/sello_key.txt")))["ok"])
EOF
```

Para probar el arranque con historial alterado: copia `coordinator/` y `ledger/`
a una carpeta aparte con la misma estructura, altera la copia del `.db` e
importa el módulo (`python -c "import kukia_core"`); debe imprimir el bloqueo y
salir con `LEDGER_ROTO = True`.

---

## 9. Pruebas ejecutadas (19-10-2026)

| Prueba | Resultado |
|---|---|
| `UPDATE ledger SET kuki=...` en copia | cadena rota, sello descuadrado → detectado |
| Cambiar un importe y **rehacer toda la cadena** | `cadena_ok: true`, sello no cuadra → detectado |
| Arranque con base alterada | `LEDGER_ROTO=True`, bloqueo anunciado |
| Pago con ledger roto (`_asiento` +1 KIA) | HTTP 503, **0 filas escritas** |
| Asiento de 0 con ledger roto | se anota (rastro de rechazos) |
| Transferencia con ledger roto | 503 **antes** de abrir transacción |
| Fallo simulado a mitad de transferencia | rollback, saldo intacto, `in_transaction=False` |
| Sellado periódico | 3.º sello a **+601 s** del 2.º (cadencia de 600 s) |
| Auditor contra Core vivo | OK, exit 0 |
| Tocar un asiento del tramo **sin cadena** (id 50) | `cadena_ok: true`, pero el sello v2 no cuadra → detectado |
| Zip público | sin claves ni tokens; incluye `sellos.py` y el auditor |

### No probado (limitaciones de esta sesión)

- Cortes de luz / apagado a mitad de escritura.
- Concurrencia con muchos mineros simultáneos (solo 2 GPUs).
- Ataque de red (MITM, DNS, proxy).
- Robo físico del disco o volcado de memoria.
- Crecimiento a largo plazo del `.db` con sellos (1 sello cada 10 min ≈ 52.000
  sellos/año, ~7 MB/año; no se ha probado el rendimiento del verificador con
  miles de sellos).
- Anclaje externo: no implementado.

---

## 10. Debilidades conocidas, por severidad

1. **CRÍTICA — Un solo validador.** El Core decide, verifica y paga. Si miente,
   no hay nadie que lo contradiga. Es una limitación de diseño, no un bug.
2. **CRÍTICA — Claves en el mismo disco.** `sello_key.txt` y `admin_token.txt`
   en claro junto al `.db`. Quien tenga el PC puede fabricar sellos y editar
   saldos; solo se detecta si existe una copia externa.
3. **ALTA — Sin anclaje externo.** Los sellos no salen de la máquina. La
   protección real empieza cuando el hash se publica fuera (web, git,
   OpenTimestamps).
4. **MEDIA — Sin frase de paso en la clave del sello.** Cualquier proceso del
   usuario puede leerla.
5. **MEDIA — Token de admin sin rotación ni límite de intentos.** Es local
   (loopback), pero está en claro.
6. **MEDIA — Copias de seguridad sin cifrar.** Si se copia `coordinator/`
   completo, se copian las claves.
7. **BAJA — `/ledger/integridad` recorre 85.000 filas.** Cacheado 30 s; varias
   peticiones simultáneas podrían costar CPU.
8. **BAJA — El relé de métricas no está desplegado** y, cuando lo esté, sus
   datos son informativos: no se verifican contra el Core.
9. **OPERATIVA — Sin supervisor.** Si el Core o el minero mueren, no se
   reinician solos.
10. **OPERATIVA — Nodos antiguos sin clave pública** no pueden cobrar.

---

## 11. Preguntas concretas para el auditor

Responde con evidencia (fichero:línea o petición reproducible), no con opinión.

1. ¿Existe **algún** camino para insertar en `ledger` sin pasar por `_asiento()`?
2. ¿Se puede **cobrar dos veces** la misma tarea con dos nodos y una carrera?
3. ¿Se puede **reutilizar** una firma de `pendiente` o de `resultado`?
4. ¿Se puede **desviar** el pago de otro nodo (id, `direccion_cobro`, colisión)?
5. ¿Se puede **falsificar un sello** sin `sello_key.txt`? ¿Y con el fichero
   borrado pero `meta.sellos_n` intacto? ¿Y borrando ambos?
6. ¿El bloqueo por `LEDGER_ROTO` se puede **saltar** por alguna ruta que cree
   dinero (bounty, referido, genesis, castigo negativo)?
7. ¿La **suma a 4 decimales** permite esconder una diferencia explotable
   (redondeo repetido)?
8. ¿El **recálculo** del Core es realmente independiente del minero (misma
   semilla, distinto código, tolerancias)? ¿Hay tipos de tarea con comparación
   laxa explotable?
9. ¿La emisión puede **desviarse** de 1,3075 KIA/min de forma sostenida?
10. ¿`/tareas/crear` (admin) permite **inundar** la cola o crear tareas con
    parámetros que bloqueen el Core?
11. ¿La billetera web filtra la clave por memoria, portapapeles, historial o
    `localStorage`? ¿El service worker sirve HTML viejo?
12. ¿El zip público contiene algo que no debería (rutas absolutas, tokens)?
13. ¿Qué pasa si el reloj del sistema se atrasa o adelanta 10 minutos?
14. ¿Qué pasa si el disco se llena a mitad de `_asiento` (SQLite + fsync)?

---

## 12. Anexos

### 12.1 Endpoints

| Método | Ruta | Auth |
|---|---|---|
| POST | `/nodos/registrar` | no (id lo asigna el Core) |
| POST | `/nodos/{id}/cobro` | firma Ed25519 del nodo |
| GET | `/nodos` | no |
| POST | `/tareas/crear` | admin |
| GET/POST | `/tareas/pendiente` | firma del nodo |
| POST | `/tareas/resultado` | firma del nodo |
| GET | `/ledger`, `/ledger/verificar` | no |
| GET | `/ledger/sellos`, `/ledger/integridad` | no (**nuevo**) |
| GET | `/stats`, `/saldo/{dir}`, `/saldos` | no |
| POST | `/transferir` | firma del emisor |
| POST | `/admin/*` | `X-Admin-Token` |
| GET | `/registros` | admin |
| GET | `/red/telemetria` | no |

### 12.2 Variables de entorno

| Variable | Defecto | Efecto |
|---|---|---|
| `KUKIA_TAREA_MS` | 2000 | duración objetivo por tarea |
| `KUKIA_AUTO_TAREAS` | 1 | generador de trabajo |
| `KUKIA_AUTO_COLA` | 120 | tareas en cola objetivo |
| `KUKIA_AUTO_SEG` | 5 | intervalo del generador |
| `KUKIA_SELLO_SEG` | 600 | segundos entre sellos |
| `KUKIA_SELLO_ASIENTOS` | 2000 | sella al acumular N asientos |
| `KUKIA_SELLO_BLOQUEA` | 1 | 0 = detectar sin bloquear pagos |
| `KUKIA_VERIF_GPU` | 1 | verificación de difusión en GPU |
| `KUKIA_HOSTS` / `KUKIA_ORIGENES` | vacío | hosts/orígenes extra permitidos |

### 12.3 Ficheros sensibles (no publicar)

```
coordinator/admin_token.txt     token de admin (64 hex)
coordinator/sello_key.txt       clave privada del sello (64 hex)
coordinator/ledger/kukia.db     ledger completo (saldos)
coordinator/sellos.jsonl        sellos (se pueden copiar/publicar)
```

### 12.4 Cómo falsificar este informe

1. Comprueba los `sha256` de la sección 3 y los números de `/stats`.
2. Repite la sección 8 sobre copias.
3. Intenta romper I1–I9 con peticiones HTTP reales, no solo leyendo código.
4. Si algo de aquí no se sostiene, dilo con la evidencia y la línea exacta.

Eso es exactamente lo que se pide.
