# Changelog del minero de KUKIA

Solo entran las versiones que se han probado. Lo que no se midió no se anota como
arreglado.

## F2-53 · 30/10/2026

**SHA-256 del worker:** `e005dcd06259bf3ad4ffaa08ab5b87c187e07e7ca8f9a666c2a0cda48441301a`
(anterior, F2-52: `9d906b828f3901445a20aba2f5918e506b4aafd06304f24c978e808bd2ad17aa`)

### Corregido

- **Doble liberación del cupo (defecto propio, confirmado en el código).** Los tres
  caminos de error de `_pedir` (429, HTTP distinto de 200 y cola vacía) llamaban a
  `_soltar()` y el `finally` volvía a soltarlo. Como el semáforo no era acotado, el
  contador interno subía sin límite y el tope `KUKIA_EN_VUELO` dejaba de limitar.
  Ahora el contador `en_vuelo` es la fuente de verdad: solo se libera si hay una
  tarea en vuelo, cada rama marca su bandera y liberar de más queda anotado en
  `arranque.log`. Probado: `pruebas_f2_53.py` C1–C5.
- **M-12, registro con bytes NUL.** `worker/minero.log` lo escribía un `cmd >>` y
  `tools/rotar_logs.py` lo truncaba por fuera. Se reprodujo el mecanismo exacto
  (4.400 NUL en 4.422 bytes) y se sustituyó por un escritor propio: una marca de
  tiempo por línea, cerrojo, rotación interna (5 MB, 2 generaciones) y nadie más
  escribiendo ese fichero. Probado: 8 hilos, 24.000 líneas, 29 rotaciones,
  **0 bytes NUL**, ninguna línea partida. `rotar_logs.py` ya no lo toca y
  `guardia.py` deja de rotarlo en su siguiente arranque.
- **Cola sin timeout (vía de bloqueo silencioso de M-02).** `cola_tareas.put` no
  tenía límite: con el cálculo atascado, el hilo que pide se quedaba dentro para
  siempre, la GPU muda y sin una línea de error. Ahora espera `KUKIA_COLA_ESPERA`
  (30 s), lo anota y devuelve el cupo.
- **Muerte silenciosa de hilos.** `_calcular` y `_entregar` solo capturaban
  `Exception`: un `BaseException` mataba el hilo sin rastro. Ahora queda escrito en
  `arranque.log`, con volcado de pilas, antes de propagarse.
- **Aviso nuevo `HILO_MUERTO`.** La salud vigila cada dispositivo y avisa si falta
  su hilo `pedir-*` o `calc-*`. Solo detecta y escribe: no reinicia nada.

### Sin cambios (a propósito)

- No se añade recuperación automática por dispositivo: la causa de M-02 no está
  demostrada y reiniciar a ciegas borra la prueba. La vigilancia de 12 h sigue
  abierta.
- Firmas en `dual`. No se activa `on`: el worker usa firmas v1 y haría falta que
  todos los mineros compatibles pasaran las pruebas de v2.
- No se toca el Core, ni el ledger, ni las claves, ni la tokenomics.

### Medido en esta versión

- RTX 5070 (12 GB) y RTX 5060 Ti (16 GB): tareas entregadas y aceptadas, latido a
  0 s, 42,99 y 41,81 GFLOPS medidos en el arranque.
- Core aislado con base sintética: 62 tareas `ok`, 0 nodos de prueba en la base
  operativa.
- Cola vacía 40 s: sin doble liberación, sin avisos de salud, sin bloqueo.
- Core caído 15 s: el worker sobrevive y reintenta cada 15 s.
- Core real tras el relevo: 125 entregas aceptadas en los primeros minutos, ningún
  identificador de tarea cobrado dos veces en el registro; en la base, 0 tareas con
  más de un pago por trabajo.

## F2-52 · 30/10/2026

- `worker/arranque.log` con versiones, GPU, hilos y errores con traceback.
- Contadores por dispositivo firmados en la telemetría y aviso `ESTERIL` a los 180 s
  sin pedir trabajo, con volcado de pilas. No reinicia nada.
- Timeout por defecto (10 s) en `_get`/`_post`: una llamada sin timeout no puede
  colgar un hilo para siempre.

## F2-51 · 29/10/2026

- Dos GPU detectadas a la vez, VRAM real por tarjeta y TOPS etiquetados como
  estimados (antes 0/0 por un `import torch` dentro de `main()`).
