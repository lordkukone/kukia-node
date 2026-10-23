# Pago por potencia demostrada

Estado: **ACTIVO desde el 22-10-2026 (22:42)**. El pago sigue a los FLOPs
verificados. Para volver a la tarifa plana: `set KUKIA_PAGO_POR_FLOP=0` y
reiniciar el Core; entonces la moneda paga exactamente lo mismo que antes.

## Que cambia cuando se enciende

| | Antes (tarifa plana) | Con `KUKIA_PAGO_POR_FLOP=1` |
|---|---|---|
| Que se paga | una tarea terminada | los FLOPs verificados de la tarea |
| Checksum (0 GFLOP) | igual que una difusion | **0 KIA** (fuera de la mezcla) |
| Difusion 20 GFLOP | igual que un checksum | 20 veces el precio por GFLOP |
| Tier de VRAM | x1,0 / x1,3 / x1,6 | **no multiplica** |
| CPU i9 (1,18 GFLOPS) | ~200 KIA/dia | ~53 KIA/dia |
| RTX 5070 (19,4 GFLOPS) | ~634 KIA/dia | ~869 KIA/dia |
| RTX 5060 Ti (22,7 GFLOPS) | ~813 KIA/dia | ~1.018 KIA/dia |

Las cifras son con el presupuesto de emision de hoy (1,31 KIA/min y 42,1 GFLOPS
de red). Lo que manda es la potencia medida: el doble de GFLOPS, el doble de KIA.

## De donde sale el precio (no se inventa)

```
precio_por_GFLOP = KIA que toca emitir este minuto / GFLOP que entrego la red EN EL MINUTO ANTERIOR
```

- "KIA que toca emitir" es el mismo objetivo de calendario de siempre:
  lo que queda hasta 100M dividido por los minutos que faltan hasta 2150
  (hoy 1,31 KIA/min).
- "GFLOP del minuto anterior" se cuenta de las tareas ya verificadas, y se
  cuenta igual que se paga (`max(FLOPs, suelo)`), para que el presupuesto
  cuadre al centimo.
- **Un precio por minuto, fijo.** Se calcula al empezar el minuto y no cambia
  hasta el siguiente: todas las tareas de ese minuto cobran lo mismo por GFLOP,
  sea cual sea la hora a la que se entreguen. Cada nodo cobra en proporcion
  exacta al trabajo que ha hecho (a 8x trabajo, 8x KIA).
- Si la red calcula el doble, el precio por GFLOP baja a la mitad. **La emision
  total y la fecha final no se mueven.**

### Arranque en frio, red parada y ajuste fino

- **Sin trabajo en el minuto anterior** (arranque, red parada): se mira la
  ventana de 10 min y, si tampoco hay nada, la media de lo ultimo que haya
  (hasta 6 h). Asi el primer minuto tras una parada no sale a precio de oro.
- **Sin ningun dato** (red nueva): modo plano, pago de calendario con el minuto
  cortado en un presupuesto. Es el unico caso que reparte por orden de llegada,
  porque sin precio no hay otra forma.
- **Credito**: si un minuto paga de mas o de menos (la red cambia de tamano),
  el error se apunta y se corrige en el precio del siguiente. Nunca mas de un
  presupuesto de credito, para que un mes parado no se pague doble al volver.
- **Fusible**: si un minuto se desmadra de golpe (10 veces el trabajo anterior),
  se corta en 2 presupuestos (`KUKIA_FUSIBLE_MINUTO_X`). Red de seguridad, no
  el mecanismo: en regimen normal no toca nada.

### Lo que se publica

`/stats` → `bolsa_emision` es lo que de verdad se esta pagando:

```json
"bolsa_emision": {"precio_minuto": 0.001179929480,
                  "fuente_precio": "minuto anterior",
                  "gflop_minuto_anterior": 1110.711, "presupuesto_kia_min": 1.310561,
                  "pagado_en_minuto": 0.4123, "objetivo_kia_min": 1.310561,
                  "credito_kia": 0.0, "fusible_x": 2}
```

`pago_por_potencia.precio_gflop` sigue saliendo de la ventana de 10 min
(`KUKIA_VENTANA_MIN`): es una **estimacion viva** de lo que vale la potencia de
la red, no el precio con el que se paga. Si los dos numeros difieren, manda
`bolsa_emision.precio_minuto`.

## La fuga del modo plano (cerrada el 23-10-2026, 18:4x)

La bolsa tenia una puerta abierta: `pago_de_tarea` con `KUKIA_PAGO_POR_FLOP=0`
devolvia `recompensa_pago(tier)` a pelo, **sin tope de minuto y sin apuntarlo en
la bolsa**. Y el primer arranque de una red sin historial cae al mismo pago
plano. Resultado: 25 tareas en un minuto pagaban 155,17 KIA con un presupuesto
de 1,31 (x118).

Ahora **todos** los pagos pasan por la bolsa: el plano por `pago_plano()` (corta
en lo que queda del minuto y anota con `_anotar_pagado`) y el de potencia por
`pago_por_trabajo()`. No hay otra puerta: en el Core solo se emite por
`pago_de_tarea`, y se ha comprobado con el codigo viejo al lado.

Medido con un Core aislado y `kukia.db` vacio (ventana vacia, sin precio),
entregando tareas de verdad por HTTP (`tools/prueba_bolsa_arranque.py`, 25
tareas en cada escenario):

| Escenario | Peor minuto | Presupuesto | Total |
|---|---|---|---|
| A. De serie (potencia sin precio → plano) | 1,311897 | 1,311897 | 1,3119 |
| B. `KUKIA_PAGO_POR_FLOP=0` (arreglado) | 1,311897 | 1,311897 | 1,3119 |
| C. El mismo con el pago plano VIEJO | **155,170511** | 1,311897 | 155,1705 |

El suministro sube exactamente lo pagado en A y B (1,311897), y en C sube 118
veces el presupuesto del minuto. Los tres escenarios se miden con el mismo
cliente, el mismo tipo de tarea y el mismo minuto.

## Bolsa estricta: si no se puede escribir, no se paga (23-10-2026, 18:5x)

La bolsa vivia solo en memoria y su escritura, cuando se añadio, iba envuelta en
un `except Exception: pass`. Dos agujeros:

1. **Fallo de escritura silencioso**: el pago salia igual. Ahora la bolsa se
guarda en `meta` (`bolsa_minuto`, `bolsa_pagado`, `bolsa_credito`) **dentro de la
transaccion del pago** y sin red de seguridad: si no se puede escribir, el
endpoint hace ROLLBACK y responde `500` con «no se pudo anotar la bolsa del
minuto; pago abortado». No hay asiento, ni saldo, ni tarea cerrada.
2. **Reinicio a mitad de minuto**: la bolsa volvia a cero y se podia pagar dos
veces el presupuesto del mismo minuto. Ahora se recupera al arrancar. Si la
lectura falla, el minuto arranca como **gastado** (no se paga hasta el
siguiente): el lado seguro.

Medido con Core aislado y HTTP de verdad (`tools/prueba_bolsa_estricta.py`),
forzando el fallo con un trigger de SQLite sobre `bolsa_pagado`:

| Comprobacion | Resultado |
|---|---|
| Pago con la bolsa rota | `500`, suministro 15.000.000 → 15.000.000 |
| La tarea tras el aborto | sigue `ejecutando`, 0 asientos |
| Reintento con la bolsa arreglada | `200`, 1,311897 KIA |
| Pagado en el minuto antes/despues de reiniciar | 1,311897 / 1,311897 |
| Total del minuto tras reiniciar y seguir pagando | 1,311897 ≤ 1,311897 |

Ojo al detalle de implementacion: `_guardar_bolsa()` escribe dentro de la
transaccion si ya hay una abierta (pago) y, si no la hay (pruebas, arranque),
la abre y la cierra ella misma. Sin eso dejaba la base bloqueada al siguiente
`BEGIN IMMEDIATE` y todo pago respondia 503.

## Prueba con dos nodos compitiendo (23-10-2026, 18:13 → 18:27)

Dos nodos en la misma cola del Core, un Core aislado sobre la copia del ledger,
14 minutos, 1.3313 KIA/min de red (presupuesto 1.3106). Medido en
`medidas/ensayos/comp_r2_20261023_181348/`:

| | GFLOP | % trabajo | KIA | % KIA | tareas |
|---|---|---|---|---|---|
| test (2 ranuras) | 8.334,8 | 48,0 % | 8,9446 | 48,0 % | 1.673 |
| rival (2 ranuras) | 9.026,7 | 52,0 % | 9,6996 | 52,0 % | 1.640 |

El reparto por KIA es **clavado al reparto por trabajo** (48,0 / 52,0), y no al
numero de tareas (1.673 contra 1.640). El KIA/GFLOP de los dos queda a un 0,13 %
(el precio cambia cada minuto): `0,001073161` contra `0,001074537`. Eso es lo que
se buscaba: el presupuesto sigue al trabajo, no al orden de llegada.

## Sin suelo: el que no tiene potencia cobra menos

Desde el 22-10-2026 (22:58) **no hay minimo por tarea**: el pago es
estrictamente proporcional al trabajo. Una matriz de 0,43 GFLOP cobra 0,43;
una inferencia de 0,0004 cobra 0,0004; un checksum (0 FLOPs) cobra **0**.
Se puede volver a poner un minimo con `KUKIA_GFLOP_MIN=1` si algun dia se
quiere que los nodos pequenos no se queden a cero, pero hoy no esta.

Consecuencia: los `checksum` han salido de la mezcla automatica de tareas
(`MEZCLA_AUTO`). El tipo sigue soportado, pero una tarea de 0 FLOPs no se
paga, asi que no tiene sentido que la red las genere. Esto vale igual para
GPU que para CPU: manda la potencia medida, no el tipo de hierro.

## Tope por tarea (sigue)

Nunca mas de `REWARD_BASE x emission_mult()` (hoy 10 KIA). Sin el, una red casi
vacia mediria poquisimos GFLOP/min y el precio se dispararia: en la prueba del
22/10 una difusion llego a cobrar 1.209 KIA.

## Como se enciende (y como se apaga)

Ya viene encendido: no hay que definir nada. Para APAGARLO, antes de arrancar
el Core:

```
set KUKIA_PAGO_POR_FLOP=0
```

Y para volver a encenderlo, `=1` o simplemente no definir la variable.

Opcionales:

| Variable | Por defecto | Que hace |
|---|---|---|
| `KUKIA_GFLOP_MIN` | `0` | suelo facturable por tarea (en GFLOP). A 0, pago estricto |
| `KUKIA_FUSIBLE_MINUTO_X` | `2` | tope de pago de un minuto, en presupuestos |
| `KUKIA_VENTANA_MIN` | `10` | ventana del **estimador** `pago_por_potencia` (no del pago) |

El Core publica en `/stats` el modo y la medida de red; el precio con el que se
paga esta en `bolsa_emision.precio_minuto` (ver arriba):

```json
"modo_pago": "potencia",
"pago_por_potencia": {"activo": true, "precio_gflop": 0.000511,
                      "gflop_red_por_min": 2520.0, "gflop_min_facturable": 0.0}
```

## Como se revierte

Quitar la variable y reiniciar el Core. No hay migracion ni datos nuevos: el
pago se calcula al vuelo en cada entrega. El ledger ya pagado no se toca.

## Como se comprueba

```
python tools/prueba_pago_potencia.py
```

Carga el Core en una carpeta temporal (no toca el ledger real) y verifica:
plano sigue siendo plano, encendido el pago sigue a los GFLOP, ningun pago pasa
del techo ni de lo que queda por emitir, y si la red calcula el doble el precio
por GFLOP baja.

La bolsa estricta (fallo de escritura, reinicio a mitad de minuto y nodo
antiguo que aprende su `device` por el latido):

```
python tools/prueba_bolsa_estricta.py
```

Y la bolsa en un arranque con la ventana vacia, con el pago plano viejo al lado
para ver la fuga que se cerro:

```
python tools/prueba_bolsa_arranque.py
```

Arranca tres Cores aislados con `kukia.db` vacio (no toca el ledger real),
entrega 25 tareas de verdad por HTTP en cada uno y comprueba minuto a minuto
que ninguno pasa del presupuesto. Ultimo resultado (23-10-2026 18:48):
A 1,311897, B 1,311897 (los dos dentro del presupuesto) y C 155,170511 con el
pago plano viejo.

Y el precio unico del minuto, que es lo que se cobra hoy:

```
python tools/prueba_precio_minuto.py
```

Comprueba que el precio es uno solo para el minuto, que dos tareas cobran en
proporcion exacta a sus FLOPs (a 8x trabajo, 8x pago), que un minuto no pasa
del fusible y que en modo plano se corta en el presupuesto. Ultimo resultado
(23-10-2026 18:32): precio 0,00117993 KIA/GFLOP, proporcion x8,0000 con trabajo
x8,0000, minuto completo 2,621122 KIA en 2.172 tareas con tope 2,621121.

## Aviso para el minero y la web

- El minero **no cambia**: ya reporta lo que hace y quien mide es el Core.
- La web publica sigue diciendo el pago por tarea de `/stats`, que en modo
  potencia es lo que cobra una tarea minima (servicios), no una difusion.

## Revisiones redundantes: quién puede verificar a quién (23-10-2026)

La réplica ahorra el recálculo del Core, pero no puede ser una segunda opinión
comprada. Reglas desde el arreglo H1 (ver `AUDITORIA_AGENTE_2026-10-23.md`):

- **Mismo dueño no se verifica**: ni tu propia tarea (ya no se podía) ni la de
  otra identidad con tu misma cuenta de cobro o tu misma IP de registro (24 h).
- **Nodo sin historial no ahorra recálculo**: mientras el primario o el
  verificador tengan menos de 20 tareas verificadas, la tarea se recalcula
  siempre (1 de cada 1). Después vuelve el muestreo normal de 1 de cada 10.
- El reparto 70/30 no cambia, ni el total por tarea, ni la emisión.

Mandos: `KUKIA_AUDITORIA_1_DE` (10), `KUKIA_AUDITORIA_1_DE_JOVEN` (1),
`KUKIA_AUDITORIA_TAREAS_CONFIANZA` (20). Se comprueba con
`python tools/auditoria_replica.py`.
