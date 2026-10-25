# GUÍA DEL CLIENTE — pedir trabajo y pagarlo (24/10/2026)

Un **cliente** es quien PIDE trabajo a la red: la otra mitad de KUKIA, la que
faltaba. El minero cobra por calcular; el cliente paga por hacer calcular.

Todo lo de esta guía está implementado y probado en
`coordinator/kukia_core.py` y se puede repetir con
`python tools/prueba_cliente.py` (40 comprobaciones sobre un Core aislado).

---

## 1. Las tres piezas

| Pieza | Qué es | Dónde vive |
|---|---|---|
| **Token de API** | `kia_...`. Identifica al cliente. Se guarda **hasheado**. | En el Core (`clientes.token_hash`) |
| **Billetera KUK** | La que pone el dinero y **firma** cada encargo | Del cliente (`ledger/wallet/`) |
| **Saldo KIA** | Lo que hay en esa dirección | Ledger del Core |

Sin token no hay acceso. Sin billetera **no hay gasto**: el token solo no cobra
nada. Si te roban el token, no te pueden quitar el saldo; si te roban la
billetera, sí.

---

## 2. Alta (la hace el administrador del Core, una vez)

```bash
curl -X POST http://127.0.0.1:8300/admin/clientes \
  -H "X-Admin-Token: <token de admin>" \
  -H "Content-Type: application/json" \
  -d '{"nombre": "mi-empresa", "direccion": "KUK...."}'
```

Devuelve:

```json
{"id":"fd67f3072903","nombre":"mi-empresa","direccion":"KUK...",
 "token":"kia_...","aviso":"Guarda el token: no se puede volver a ver."}
```

Ese token **no se puede recuperar** después: en la base solo queda su SHA-256.
Si se pierde, el administrador da de alta otro (una billetera tiene un solo
cliente activo).

La dirección KUK sale de tu billetera (`ledger/wallet/<nombre>.json`) o de la
web (`docs/wallet.html`). Tiene que tener saldo antes de pedir trabajo.

> **De dónde sale el saldo.** KIA no se regala: se compra a alguien de la red o
> se mina. Un cliente puede ser también minero; entonces gasta lo que gana.

---

## 3. Consultar antes de firmar

```bash
curl "http://127.0.0.1:8300/cliente/precio?tipo=matriz" -H "X-Cliente-Token: kia_..."
# {"tipo":"matriz","coste_minimo":1.311919,"moneda":"KIA", ...}

curl http://127.0.0.1:8300/cliente/saldo -H "X-Cliente-Token: kia_..."
# {"direccion":"KUK...","saldo":48.688081,"retenido":1.311919,"tareas":1}
```

`coste_minimo` es el suelo de una tarea de ese tipo (tamaño mínimo). El coste
real crece con los GFLOP de la tarea. `retenido` es lo que tienes en tareas que
todavía no han terminado.

**Pedir precio no gasta nada**: el cálculo va en modo simulación y no toca la
bolsa del minuto (esto era un fallo y se arregló el 24/10).

---

## 4. Encolar: la firma es obligatoria

El mensaje que se firma es **exactamente**:

```
cliente:<nonce>:<tipo>:<sha256_hex de los params normalizados>:<tope>
```

- `nonce`: tuyo, único, 1–64 caracteres alfanuméricos (`-` y `_` valen). Es el
  anti-replay: el mismo nonce no se puede usar dos veces.
- `params`: JSON con los tipos ya normalizados (`n`, `pasos` y `seed` como
  enteros). Se firma su `sha256` con `json.dumps(..., sort_keys=True)`.
- `tope`: **máximo en KIA que autorizas**. Va dentro de la firma, así que no se
  puede subir después (intentarlo devuelve `401`).

```bash
curl -X POST http://127.0.0.1:8300/cliente/tareas \
  -H "X-Cliente-Token: kia_..." -H "Content-Type: application/json" \
  -d '{"tipo":"matriz","params":{"n":256,"seed":7},"tope":2.0,
       "nonce":"c-2026-10-24-001","clave_publica":"<hex>",
       "firma":"<hex>"}'
```

Respuesta:

```json
{"tarea_id":"b2ebb51161bf","estado":"queued","tipo":"matriz",
 "coste":1.311919,"params":{"n":256,"seed":7},
 "recoger_en":"/cliente/tareas/b2ebb51161bf"}
```

En este mismo acto, y en una sola transacción:

1. se valida la tarea (params y tipo),
2. se calcula el coste con la tarifa de la red,
3. se comprueba la firma,
4. se comprueban nonce, cupo de cola y saldo,
5. **se retiene `coste` KIA** de tu saldo y se encola.

Ese dinero va a la cuenta `KUKIA_ESCROW`. **No se emite nada nuevo**: un
cliente que paga no infla el suministro (los dos asientos se compensan).

Qué se rechaza y con qué código:

| Caso | Respuesta |
|---|---|
| Token inválido o ausente | `401` |
| Falta firma, firma de otra clave, tope cambiado tras firmar | `401` |
| Clave pública que no es la de la billetera del cliente | `401` |
| Tope por debajo del coste | `402` (dice cuánto cuesta) |
| Saldo insuficiente | `402` |
| Mismo nonce otra vez | `409` |
| Ya tienes 8 tareas en curso | `429` |
| Ledger alterado | `503` (no se aceptan encargos nuevos) |
| Core ocupado por otra transacción | `503` (reintentar) |

---

## 5. Recoger el resultado

```bash
curl http://127.0.0.1:8300/cliente/tareas/b2ebb51161bf -H "X-Cliente-Token: kia_..."
```

```json
{"tarea_id":"b2ebb51161bf","estado":"ok","tipo":"matriz",
 "coste":1.311919,"prepago":0.0,"pagado":1.311919,
 "resultado":"-37092","verificado":true,
 "terminado":"Sat Oct 24 13:11:08 2026"}
```

- `estado`: `queued` → `ejecutando` → `ok` / `fallo`.
- `resultado` y `hash`: lo que calculó el minero y el hash con el que el Core lo
  selló. `verificado: true` significa que el Core **recalculó** la tarea en vez
  de fiarse del minero.
- `pagado`: lo que cobró el minero. Si es menor que `coste`, la diferencia ya
  volvió a tu saldo.

Tarea de otro cliente → `404` (no se distingue de "no existe": no se filtra
información).

---

## 6. El dinero: qué pasa en cada final

| Final | Qué pasa con tu dinero |
|---|---|
| `ok` y coste = pagado | Se paga al minero desde el escrow. Escrow a 0 |
| `ok` y pagado < coste | Se paga al minero y **el sobrante vuelve a ti** |
| `fallo` (nadie lo resolvió, nadie lo pudo verificar) | **Se te devuelve todo**, prepago a 0 |
| Nodo que abandona y agota reintentos | Igual que `fallo`: devolución |
| Encargo nunca entregado | Sigue `queued`; no se cobra al minero |

Una tarea solo puede pagar O devolver **una vez**: al cerrarla, `prepago` pasa a
0 en el mismo movimiento. No hay doble cobro ni doble devolución.

---

## 7. El cliente de referencia

`tools/cliente_ejemplo.py` hace todo esto contra un Core real: firma, encola,
espera y recoge. Sirve como SDK mínimo y como prueba de que el contrato se
entiende.

```bash
set KUKIA_CLIENTE_TOKEN=kia_...
python tools/cliente_ejemplo.py saldo
python tools/cliente_ejemplo.py precio matriz
python tools/cliente_ejemplo.py pedir matriz --params "{\"n\": 256, \"seed\": 7}"
python tools/cliente_ejemplo.py pedir checksum --params "{\"data\": \"hola\"}" --espera 120
python tools/cliente_ejemplo.py ver <tarea_id>
```

Opciones: `--core URL`, `--token`, `--billetera NOMBRE`, `--tope KIA`, `--json`,
`--verboso`, `--espera SEG`.

---

## 8. Límites y lo que esto NO protege

- **8 tareas en cola por cliente** y **tope de 25 KIA** si no se firma otro
  (`KUKIA_CLIENTE_MAX_EN_COLA`, `KUKIA_CLIENTE_TOPE`).
- El token se puede rotar dando de alta otro cliente con la misma billetera
  (desactivando antes el viejo).
- **No hay comisión**: el cliente paga exactamente lo que vale el trabajo, y el
  minero cobra exactamente eso. La red no se queda un porcentaje.
- **No protege de una clave comprometida.** Quien tenga tu billetera puede
  firmar encargos tuyos: la firma demuestra posesión de la clave, no identidad.
- **No hay reembolso automático si tú te arrepientes**: una tarea ya entregada a
  un nodo se paga. El encargo es un compromiso, no una reserva.
- **El precio tiene un suelo desde el 25-10-2026** (ancla GPU-minuto): nunca es
  más caro que 1 minuto de GPU de referencia por minuto de red (1,31 KIA el
  25-10), así que no se dispara cuando la red está vacía. Si la red entrega más
  trabajo que una GPU, el precio baja y el presupuesto se reparte entre los
  mineros en proporción a lo que cada uno entrega. Si se apaga el ancla
  (`KUKIA_ANCLA_GPU_MIN=0`), vuelve a subir y bajar sólo con el trabajo medido.

---

## 9. Resumen en cinco líneas

1. El administrador te da un token `kia_...` (una sola vez).
2. Tu billetera KUK firma cada encargo; el token identifica, la clave paga.
3. Al encolar se **retiene** el coste en escrow; sin saldo no hay tarea.
4. El minero cobra **de ese depósito**, no de la emisión.
5. Recoges el resultado en `/cliente/tareas/<id>`; el sobrante o la devolución
   vuelven solos a tu saldo.

---

## 10. Comprobarlo tú mismo, de punta a punta

`tools/demo_cliente_punta_a_punta.py` hace el camino entero contra el Core real
en menos de un minuto: crea billetera, da de alta al cliente, le ingresa saldo
desde la billetera del fundador, pide precio, firma un encargo de inferencia,
espera al minero y verifica las cuentas.

```bat
python tools/demo_cliente_punta_a_punta.py
python tools/demo_cliente_punta_a_punta.py --params "{\"n\": 2048, \"seed\": 11}"
```

Lo que imprime (ejecución del 25-10-2026, tarea `84274510fad2`):

```
prepago cliente          KUK4E53E98FC82211048E4B714AC94  -0.000055000 KIA
prepago cliente          KUKIA_ESCROW                    +0.000055000 KIA
pago a minero (cliente)  KUKIA_ESCROW                    -0.000055000 KIA
trabajo verificado       KUKE1A0EB09FD9BF49FF41B884EDBD  +0.000055000 KIA
suma de la tarea: +0.000000000000 KIA
```

Cuatro asientos, suma cero: el dinero del cliente pasa al minero sin crear ni
un KIA. El recibo se verifica de dos formas: `/ledger/verificar` comprueba la
cadena de hashes de todo el historial (`cadena_ok`, 658.950 asientos) y la
tarea trae su `hash` con `verificado: true`, que significa que una réplica
independiente dio el mismo resultado.
