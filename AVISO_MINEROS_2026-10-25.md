# Aviso a los mineros — 25-10-2026 (actualizado a las 17:45)

**Hoy el precio del trabajo ha cambiado dos veces. Esto es lo que corre ahora, con
números, y lo que no cambia.** No es una promesa de rentabilidad: es lo que dice el
ledger y lo que publica `/stats`.

> **17:45 — el calendario tiene día exacto y el reparto se publica.** Kuki fijó el fin
> de emisión en el **13-04-2150** (antes, 01-01-2150: 103 días más), así que el
> presupuesto del minuto baja de 1,310592 a **1,307629 KIA/min** y el precio de
> **1,456213e-6** a **1,452921e-6 KIA/GFLOP**. No cambia nada de lo que ya está
> cobrado. Además, `/stats` publica desde hoy `reparto`: cuántas GPUs se unieron al
> minuto anterior, qué parte del trabajo es de cada billetera y lo que cobra cada GPU
> si todas entregan lo mismo. Las cifras de este aviso están calculadas a las 16:26 y
> se mueven con el precio; las buenas son siempre las de `/stats`.

---

## 0. Lo de las 16:26: vuelve el ancla GPU-minuto

El precio ya no es una cifra fija. Se calcula así:

```
divisor = max(trabajo medido el minuto anterior, 60 x 15.000 GFLOP)
precio  = presupuesto del minuto / divisor

hoy: 1,310592 KIA/min / 900.000 GFLOP = 1,456213e-6 KIA/GFLOP
```

Traducido: **un minuto de GPU de referencia vale lo que paga la emisión de ese minuto
(1,31 KIA)**, y **1 KIA compra 0,76 minutos de GPU** (unos 46 segundos). Con la red
floja manda el suelo y el GFLOP vale 1,456213e-6; si la red entrega más de 1,31 minutos
de GPU por minuto, el mismo presupuesto se reparte entre todos en proporción a sus GFLOP.

A las 14:36 de hoy se encendió la variante contraria (**1 KIA = 1 minuto exacto**,
0,000001111111 KIA/GFLOP). **A las 16:26 se apagó**: ahora manda el calendario, que es
lo que hace que el suministro tenga fecha. Quien quiera la definición la enciende con
`KUKIA_PRECIO_DEFINIDO=1`, pero el Core arranca con el ancla.

---

## 1. Histórico del precio (con la fuente)

| Fecha | KIA por GFLOP | De donde salía |
|---|---|---|
| 23-10-2026, noche | **0,00374** | reparto del presupuesto (README de ese día) |
| 24-10-2026 (60.000 pagos del ledger: 343,42 KIA sobre 355.764 GFLOP) | **0,000965** | lo mismo, con más faena en la red |
| 25-10-2026, mañana | **0,0000014562** | ancla GPU-minuto (suelo de capacidad) |
| 25-10-2026, 14:36 | 0,000001111111 | definición: 1 KIA = 1 minuto de GPU |
| **25-10-2026, 16:26 (ahora)** | **0,000001456213** | **ancla GPU-minuto por presupuesto** |

De 0,00374 a 0,000001456213 hay **unas 2.568 veces menos KIA por GFLOP**. No es un
redondeo ni un error de la web: es el mismo número que publica `/stats`.

---

## 2. Lo que cobra una GPU a tope

"A tope" quiere decir **el 100 % del tiempo calculando**, y con el ancla de hoy
(1,310592 KIA por minuto de GPU):

| Tiempo a tope | GFLOP entregados | KIA |
|---|---|---|
| 1 minuto | 900.000 | **1,3106** |
| 1 hora | 54.000.000 | **78,6** |
| 1 día | 1.296.000.000 | **1.887** |
| 1 mes (30,44 días) | — | **~57.490** |

Eso es lo máximo que puede pagar la emisión. **Lo que se cobra de verdad depende de
cuánto trabajo haya y de cuánta gente lo haga**, y hay dos límites publicados:

- **El tope de la red:** la emisión del calendario son 1,31059 KIA por minuto, o sea
  **1,31 minutos de GPU por cada minuto de reloj** (1.887 minutos de GPU al día entre
  todos). Por debajo de esa capacidad no se recorta nada; por encima, ese minuto se
  corta y el exceso no se paga. Medido: 2 minutos de GPU entregados en un mismo minuto
  cobraron 1,000000 al primero y 0,311950 al segundo.
- **Las tareas de hoy duran milisegundos:** una matriz de 800 × 800 son 1,024 GFLOP (68
  microsegundos de GPU) y por trabajo valdría 0,0000015 KIA; una difusión de 20 GFLOP
  valdría 0,0000291. Con el suelo, **las dos cobran 0,0001 KIA**: el trabajo real solo
  manda a partir de **68,7 GFLOP (4,6 ms de GPU)**. Un checksum de 0 FLOPs cobra lo
  mismo, 0,0001.

### Lo que cobro esta máquina, medido (2 GPUs, RTX 5070 + 5060 Ti)

| Ventana | Pagos | KIA | Equivalente |
|---|---|---|---|
| últimos 10 min (antes del suelo) | 1.844 | 0,014469 | 0,00145 KIA/min → 2,08 KIA/día |
| últimos 30 min (antes del suelo) | 6.075 | 0,048208 | 0,00161 KIA/min → **2,31 KIA/día** |
| última hora | 10.608 | 7,200414 | entró un lote de tareas grandes: 172 KIA/día de ese tramo |

**El suelo cambia la cifra (misma tarde, 15:22).** Desde las 15:22 ninguna tarea paga
menos de **0,0001 KIA**, que es el mínimo que acepta `/transferir`. Medido 10 minutos
después del cambio: **2.326 pagos, todos de 0,000100000**, 0,0233 KIA/min =
**~33 KIA/día** con el mismo trabajo de antes (2,3 sin suelo). Depende de la cola: entre
30 y 36 KIA/día según cuántas tareas entren. El tope del minuto sigue sobradísimo (0,02
de los 1,31 KIA que puede pagar por minuto). Ojo: el suelo está en KIA, no en GFLOP, así
que no le afecta el cambio de precio; solo se mueve el tamaño de tarea a partir del cual
se paga por trabajo (90 GFLOP con la definición, 68,7 con el ancla de hoy).

**Por qué 33 y no 1.887:** porque la GPU casi nunca está "a tope". La cola tiene unas
120 tareas y cada una dura milisegundos, así que la gráfica pasa la mayor parte del
tiempo esperando trabajo, no calculando. Pagamos por trabajo entregado, no por tener la
tarjeta encendida.

**Y esto no son euros.** Hoy KIA no cotiza ni se vende: no hay mercado ni precio. El
ingreso en euros de lo minado ahora mismo es **0**, y el coste (electricidad) es real.

---

## 3. Qué NO cambia

- **Se paga por GFLOP verificados**, al precio que publique `/stats` ese minuto (hoy
  1,456213e-6). Ni comisión, ni suelo escondido: lo que paga el cliente es exactamente
  lo que cobra el minero.
- **El suelo por tarea:** 0,0001 KIA. Ninguna tarea paga menos de lo que se puede
  transferir, ni siquiera un checksum de 0 FLOPs.
- **Verificación:** el Core recalcula la tarea, y en las caras dos nodos distintos tienen
  que coincidir. Si no cuadra, 0 KIA.
- **El tier por VRAM no multiplica.** Se cobra por lo que se calcula, no por lo que se
  declara.
- **Techo y calendario:** 100.000.000 KIA como máximo y la última moneda sale el
  **13-04-2150** (fijado hoy; antes era el 01-01-2150). El techo, sin cambios.
- **Tu saldo y tus pagos anteriores no se tocan.** Lo ya cobrado sigue en el ledger.

---

## 4. Qué puede cambiar (y se avisará)

- El **precio por GFLOP**, que ahora sigue al presupuesto del minuto. Se publica cada
  minuto en `/stats`.
- El **tope de emisión**, que baja solo con el calendario hasta 2150.
- El **tamaño de las tareas** de la cola, que es lo que de verdad mueve lo que cobras al
  día.
- El **suelo por tarea** (`KUKIA_PAGO_MIN_KIA`, hoy 0,0001). Si se quita, un checksum
  vuelve a cobrar 0.

Lo que nunca se cambiará sin decirlo en el ledger y aquí: la regla de pago y el techo.

---

## 5. Compruébalo sin creerte este aviso

1. `VER_SALDO_MINERO.cmd` — tu saldo, sin adornos.
2. `GET /stats` (en tu Core) y mira:
   - `bolsa_emision.precio_minuto` → **1,456213e-6** (hoy)
   - `bolsa_emision.fuente_precio` → **ancla GPU-minuto (suelo)**
   - `bolsa_emision.kia_por_minuto_gpu` → **1,310592** (lo que vale un minuto de GPU)
   - `bolsa_emision.fusible_x` → **1,0**
   - `pago_por_potencia.precio_gflop` → el **mismo** número: lo publicado es lo pagado.
3. La cuenta a mano: **KIA al día = GFLOP que entregas al día × el precio de `/stats`**,
   con un mínimo de 0,0001 KIA por tarea. Si te da distinto de tu saldo, es un fallo y
   queremos saberlo.

---

## 6. Versión corta para pegar en Telegram

> **Aviso de precio (25-10-2026, 16:26).** La tarifa es
> **1,456213e-6 KIA/GFLOP** y sale del calendario: el presupuesto del minuto
> (1,310592 KIA) repartido entre una GPU de referencia a tope (900.000 GFLOP).
> Un minuto de GPU = **1,31 KIA**; 1 KIA = **0,76 minutos de GPU** (46 s).
> La red solo puede pagar **1,31 minutos de GPU por minuto** (1.887 al día entre todos):
> por encima de eso, ese minuto se corta.
> Medido hoy en la máquina de 2 GPUs: 2,3 KIA/día con el pago por trabajo puro;
> desde las 15:22, con el **suelo de 0,0001 KIA por tarea**, la misma máquina mide
> **~33 KIA/día** (ninguna tarea paga menos de lo transferible).
> **No cambia:** el pago por GFLOP verificados, el suelo por tarea, 0 % de comisión, el
> techo de 100M y el fin en 2150.
> La definición "1 KIA = 1 minuto de GPU" (0,000001111111) sigue disponible, pero
> apagada: el Core arranca con el ancla.
> **KIA no tiene precio en euros:** no hay mercado, el ingreso en euros hoy es 0.
> Todo comprobable en `/stats` (`bolsa_emision.precio_minuto`, `fuente_precio`).

---
