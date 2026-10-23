# El 100 % de GPU con la tarjeta libre y un rival compitiendo (23/10/2026, tarde)

Repetido el ensayo de esta mañana con el **minero de producción parado**, para que la
GPU esté de verdad libre. Resultado: **el 100 % sí paga más, un 10,5 %**, no el doble.
Y de paso ha salido un agujero en la bolsa de emisión (apartado 4).

---

## 1. Montaje

- Parado el **guardia** (PID 14476) y luego el **minero** (PID 42004). Había que parar
  el guardia primero: si se cae el minero con el guardia vivo, lo relanza en segundos.
- GPUs comprobadas en reposo antes de empezar: **0 al 4 % y 1 al 0 %**.
- Producción sin minar durante los dos brazos, comprobado en el propio ensayo:
  `red_de_verdad` da **delta 0** en los dos resúmenes (`tareas_ok` 361.466 antes y
  después en ambos).
- Mismo guion que la vez anterior: `tools/ensayo_competencia.py`, 14 min por brazo,
  rival fijo en GPU 1 con 1 ranura, test en GPU 0 con 1 o 2 ranuras.
- Al terminar se ha **restaurado producción**: guardia nuevo (PID 48676) y él solo ha
  levantado el minero (PID 53828, 17:15:19). Comprobado que vuelve a pagar
  (+237 tareas en 60 s, `emision_por_min` 1,3106).

Ficheros:

- `medidas/ensayos/comp_r1_20261023_164259/` — test 1 ranura, GPU libre.
- `medidas/ensayos/comp_r2_20261023_165733/` — test 2 ranuras, GPU libre.

---

## 2. Los cuatro brazos, juntos

| | Contendida 1 r. | Contendida 2 r. | **Libre 1 r.** | **Libre 2 r.** |
|---|---|---|---|---|
| KIA/min del test | 0,6558 | 0,6737 | **0,6786** | **0,7496** |
| KIA/min del rival | 0,6589 | 0,6516 | 0,6764 | 0,6899 |
| Parte del test | 49,9 % | 50,8 % | 50,1 % | **52,1 %** |
| KIA/min de la red | 1,3147 | 1,3253 | 1,3550 | 1,4394 |
| Tareas pagadas al test | 1.695 | 1.803 | 2.331 | 2.385 |
| · trabajo verificado | 7,590 | 8,020 | 7,913 | **9,180** |
| · verificación redundante | 1,596 | 1,416 | 1,591 | 1,319 |
| GFLOP/min de la red (final) | 1.315 | 1.301 | 1.704 | 1.755 |
| Precio por GFLOP (final) | 0,000997 | 0,001008 | 0,000769 | 0,000747 |

Contendida = con el minero de producción minando a la vez (ensayo de antes).
Libre = con el minero parado (este ensayo).

**El 100 % con GPU libre y rival igual: 0,6786 → 0,7496 KIA/min (+10,5 %)**, con un
+2,3 % de tareas pagadas. En la GPU ocupada el mismo cambio daba +2,7 %. O sea: cuanto
más libre está la tarjeta, más se nota, pero sigue siendo un 10 %, no un 100 %.

---

## 3. Por qué (y qué cambia respecto a esta mañana)

1. **La tarta es fija.** La red pagó 1,36 y 1,44 KIA/min: el presupuesto de 1,31 más
   el agujero del apartado 4. Nadie cobra más de lo que hay.
2. **Lo que compra la segunda ranura es parte del pastel, no trabajo.** Con la GPU
   libre, el test pasó de 1.971 pagos de trabajo a 2.121 (+7,6 % de asientos) pero de
   7,913 a 9,180 KIA (+16 %): se llevó tareas más caras (difusiones 553 → 605).
3. **El precio se hunde cuando la red trabaja de verdad**: 0,00115 → 0,00075 al pasar
   de 1.315 a 1.704 GFLOP/min. El rival también cobró algo más (0,6899) porque la red
   entera movió más GFLOP, pero su parte bajó del 50,1 % al 47,9 %.
4. Traducido a un día: el test pasa de **977 KIA/día a 1.079 KIA/día** con el doble de
   ranuras. El rival pierde 0,66 → 0,69 (gana algo) pero su parte cae.

---

## 4. Defecto encontrado: la bolsa no cubre el modo «plano»

En el brazo de 2 ranuras el Core arrancó con la **ventana de 10 minutos vacía** (la red
llevaba 15 min parada). En ese caso `precio_gflop()` devuelve `modo: "plano"` y
`pago_por_trabajo()` paga con `recompensa_pago()`… **sin pasar por la bolsa**.

Medido en el ledger de esa corrida (segundos desde el arranque del brazo):

| Tramo | KIA pagados | Asientos |
|---|---|---|
| +0 a +10 s | 0,5595 | 17 |
| +10 a +20 s | **1,7081** | 51 |
| +20 a +30 s | 0,2246 | 54 |
| +30 a +120 s | ~0,22 cada 10 s | — |

Los primeros 20 s pagaron **2,27 KIA** a 0,033 KIA por tarea (tarifa plana), cuando el
presupuesto de ese tramo era ~0,44. A partir de +20 s entra el modo potencia y se
estabiliza en 1,31 KIA/min.

Consecuencia en el total del brazo: **1,4394 KIA/min** frente al presupuesto de 1,3106.
El exceso (0,129/min × 14 min ≈ 1,8 KIA) es justo lo que se pagó de más en esos 20 s.
No se parece al desastre de 47 KIA del arranque en frío (la bolsa lo habría cortado),
pero **es una fuga real**: cada vez que la red esté parada más de 10 minutos y vuelva,
los primeros 20 s se pagan sin bolsa.

Causa exacta, en el código:

```python
def pago_por_trabajo(tipo, params, tier=1.0):
    d = precio_gflop()
    if d.get("modo") != "potencia":
        pago, info = recompensa_pago(tier)      # <-- aqui NO se llama a la bolsa
        return pago, dict(info or {}, modo_pago="plano (sin medida de red)")
```

y `_precio_cache` (TTL 20 s) mantiene la decisión «plano» durante esos 20 s aunque las
tareas ya estén entrando.

**Arreglo propuesto (sin hacer todavía):** aplicar `cobrar_de_la_bolsa()` también en
esa rama, y/o acortar el TTL de la caché cuando el modo es «plano». Son 2-3 líneas, pero
cambia el Core que está en producción y hay que volver a medir.

---

## 5. Lo que hay que saber al leer los números

- **El uso de GPU de los resúmenes no vale para comparar**: la muestra es un
  `nvidia-smi` instantáneo cada 60 s, y como las tareas son de 3-500 ms, unas veces pilla
  98 % y otras 0 %. Lo que sí vale es el GFLOP/min de la red: 1.315 (contendida) contra
  1.704 (libre) en el mismo brazo de 1 ranura.
- **El primer minuto del brazo de 2 ranuras no es comparable**: arrancó con la ventana
  vacía (apartado 4). En el brazo de 1 ranura la ventana aún tenía datos de producción,
  así que empezó en modo potencia.
- El brazo libre de 2 ranuras hizo 2.385 tareas pagadas frente a 2.331 del de 1 ranura:
  el +2,3 % de tareas con +16 % de KIA de trabajo dice que la segunda ranura sirve,
  sobre todo, para coger más difusiones (605 contra 553).
- Producción perdió ~33 min de minado (16:42 a 17:15). La emisión no se pierde: el
  calendario manda y lo que no se emitió hoy se emite después; lo que no se cobró en
  ese rato son ~40 KIA de este nodo.
- Sin probar: 3 y 4 ranuras, y con la ventana ya llena desde el principio (para quitar
  el efecto del apartado 4 del todo).
