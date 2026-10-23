# ¿Paga el 100 % de GPU cuando hay competencia? (23/10/2026)

Respuesta corta: **sí, pero solo lo que te lleves del pastel.** Con otro nodo igual
peleando, subir a 2 ranuras te dio **+8,8 % de tareas pagadas y +2,7 % de KIA**
(0,6558 → 0,6737 KIA/min). El total de la red no se movió: 1,31 KIA/min en los dos
brazos. La emisión es un presupuesto fijo, así que tu pago es tu **parte**, no tu
trabajo.

Medido hoy, con Core aislado, dos mineros de verdad y 14 min por brazo.

---

## 1. Montaje

- **Core aislado** (copia verificada del ledger, puerto libre, su propia clave).
  Producción no se tocó: durante los dos brazos siguió pagando su 1,31 KIA/min
  (+2.120 y +2.385 tareas ok), comprobado en `red_de_verdad` de cada resumen.
- **Dos nodos reales**, cada uno con su árbol, su billetera y su identidad nueva:
  - `rival` en GPU 1, **siempre igual**: 1 ranura, profundidad 2, 4 en vuelo.
  - `test` en GPU 0, la variable del ensayo: **1 ranura** (de serie) o **2 ranuras**
    (el «100 %»: profundidad 6 y 8 en vuelo).
- En los dos brazos el Core lleva `KUKIA_MAX_TAREAS_NODO=8`, así que el tope por nodo
  no es la diferencia.
- 14 min por brazo, muestra cada 60 s. Herramienta nueva:
  `tools/ensayo_competencia.py` (usa las utilidades ya probadas de
  `tools/ensayo_estabilidad.py`).

---

## 2. Lo medido

| | Test 1 ranura (16:07–16:22) | Test 2 ranuras (16:22–16:37) | Cambio |
|---|---|---|---|
| KIA del nodo de prueba | **9,1858** | **9,4353** | **+2,7 %** |
| KIA/min del nodo de prueba | 0,6558 | 0,6737 | +2,7 % |
| Su parte del pastel | **49,9 %** | **50,8 %** | +0,9 pp |
| KIA del rival | 9,2298 | 9,1256 | −1,1 % |
| KIA/min de la red | 1,3147 | 1,3253 | +0,8 % |
| Asientos pagados al de prueba | 1.695 | 1.803 | **+6,4 %** |
| · trabajo verificado | 1.413 asientos / 7,590 KIA | 1.537 / 8,020 | +8,8 % tareas, +5,7 % KIA |
| · verificación redundante | 282 / 1,596 | 266 / 1,416 | −5,7 % |
| Tareas por tipo (test) | 388 difusiones, 833 matrices, 192 inferencias | 432, 888, 217 | +11 %, +7 %, +13 % |
| KIA por tarea | 0,005419 | 0,005233 | **−3,4 %** |
| Precio por GFLOP al empezar → acabar | 0,001357 → 0,000997 | 0,001349 → 0,001008 | −27 % / −25 % |
| GFLOP/min de la red (final) | 1.315 | 1.301 | igual |
| Uso de GPU (medio, toda la máquina) | g0 74,4 % · g1 35,3 % | g0 71,7 % · g1 60,1 % | sin subida |

Tamaño medio de las tareas: matriz n=837 y difusión 1.907 pasos **en los dos brazos**
(no cambió la dificultad entre brazos).

Ficheros: `medidas/ensayos/comp_r1_20261023_160747/` y
`medidas/ensayos/comp_r2_20261023_162223/` (resumen.json, muestras.jsonl, core.log y
los logs de cada minero).

---

## 3. Por qué el trabajo sube el 9 % y el cobro el 3 %

Porque el precio se recalcula contra la red entera:

```
precio por GFLOP = presupuesto KIA/min  ÷  GFLOP/min de la red (ventana 10 min)
pago de tu tarea = precio × GFLOP de la tarea
```

Si tú produces más, sube el divisor, baja el precio, y el total sigue siendo el
presupuesto. Lo único que cambia es **cuánto del presupuesto es tuyo**. Con los
números de arriba: el KIA de trabajo del nodo de prueba pasó de 7,590 a 8,020
(+5,7 %), y el rival de 7,513 a 7,342 (−2,3 %): el pastel se reparte un poco
distinto, no crece.

Aritmética con las partes medidas (esto es cálculo, no medida): si el nodo de prueba
pudiera doblar su trabajo con el rival quieto, su parte iría del 50 % al 67 %, o sea
**0,66 → 0,87 KIA/min (+32 %)**, nunca el doble. El techo es el presupuesto.

Y hay un detalle que empeora la cuenta: la **bolsa de emisión** (arreglo del arranque
en frío) pone el mismo techo global. En los dos brazos la red pagó 1,3147 y 1,3253,
justo el presupuesto de 1,3106.

### El 100 % no llegó a ser el 100 % en esta máquina

El uso de GPU **no subió** al poner la segunda ranura (74,4 % → 71,7 % en la 5070).
No es un fallo del ensayo: el minero de producción está minando en las dos tarjetas
a la vez, así que la segunda ranura del nodo de prueba se pasó el rato en cola
esperando una GPU que ya estaba ocupada por otro nodo. Por eso las tareas solo
crecieron un 8,8 % y no cerca del doble.

Es decir: en un PC donde su GPU ya la está usando otro minero, la segunda ranura no
compra potencia, compra cola. En una GPU libre sí compra potencia (medido esta
mañana con un solo nodo: 27,8 % → 60,2 % de uso), y lo que compra es **parte** del
presupuesto, no KIA extra.

---

## 4. Qué se puede concluir y qué no

**Se puede afirmar (medido):**
- Con dos nodos compitiendo, trabajar más **sí** paga algo más: +2,7 % de KIA por
  +8,8 % de tareas pagadas, y el rival pierde lo que tú ganas.
- El total de la red no se mueve: 1,31 KIA/min en los dos brazos, con y sin
  «100 %».
- El precio por GFLOP se ajusta solo (−27 % dentro de cada brazo) y el KIA por tarea
  baja un 3,4 % al trabajar más.
- Producción sigue intacta y pagando lo suyo durante los dos brazos.

**No se puede afirmar todavía:**
- Cuánto paga el «100 %» en una GPU **libre** con un rival compitiendo: aquí la
  tarjeta era compartida con el minero de producción. Para eso hay que parar el
  minero o usar una GPU sin tráfico.
- Qué pasa con 3 o 4 ranuras.
- La parte de CPU y vatios: no se midió aquí (los brazos no midieron potencia).
- El efecto del halving: con el presupuesto partido, todo esto se reduce a la mitad.

---

## 5. Contraste con la medición de esta mañana

| Situación | KIA/min del nodo | Qué lo limita |
|---|---|---|
| Solo en la red, 1 ranura | 1,433 | el presupuesto (se lleva el 100 %) |
| Solo en la red, 2 ranuras | 1,419 | el presupuesto, igual |
| Dos nodos, 1 ranura cada uno | 0,6558 | su 50 % del presupuesto |
| Dos nodos, el de prueba con 2 ranuras | 0,6737 | su 50,8 % del presupuesto |

La tabla cuenta la historia entera: el nodo cobra `presupuesto × su parte`. Con la
red vacía trabajar más no cambia nada; con competencia sube la parte, pero tu parte
crece mucho menos que tu trabajo.
