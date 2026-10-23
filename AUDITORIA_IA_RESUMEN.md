# KUKIA — dossier de auditoría del 23/10/2026 (para otra IA)

Version CORTA (6 KB). El dossier completo, con los cinco cambios, las mediciones y los
huecos, esta en `AUDITORIA_IA.md` (y en `AUDITORIA_PARA_OTRA_IA_2026-10-23.md`). Cada afirmación lleva su evidencia
(fichero, comando o número medido). Lo que no está probado se dice en el apartado 7.

Ruta del proyecto: `C:\Users\kuki\Desktop\KUKIA` (Windows, PowerShell/Git Bash).
Lenguaje: Python 3.12 + SQLite. Nada de esto se ha commiteado.

**Resumen en cinco líneas**

1. El pago estaba mal en frío: un Core nuevo pagaba 47 KIA el primer minuto contra un
   presupuesto de 1,31 KIA/min. Arreglado con una **bolsa de emisión** (tope global de
   1 minuto de presupuesto). Medido: 8,83 KIA en 8 min en vez de 47 en el primero.
2. Se sacaron las **difusiones** de los nodos CPU (19,5 s allí contra 0,8 s en GPU).
   Medido: 50 tareas / 6 min → 1.836 tareas / 6 min.
3. El Core de producción se **reinició** a las 15:51 con ese código y se comprobó que
   sigue pagando igual (1,29 → 1,26-1,29 KIA/min, 0 errores).
4. Se midió el **100 % de GPU**: solo, no paga más (1,433 → 1,406 KIA/min con el doble
   de vatios); con un rival compitiendo y la tarjeta ocupada, paga un 2,7 % más; con la
   tarjeta **libre**, un **10,5 %** más. Nunca el doble: el pastel es fijo.
5. Queda pendiente: **un defecto abierto en la bolsa** (el modo «plano» paga sin bolsa
   cuando la red lleva 10 min parada; medido, 2,27 KIA en 20 s en vez de 0,44), y
   commitear, porque producción corre 381 líneas sin versionar.

---

## 0. Qué es KUKIA, en tres líneas

Una moneda minable con su propio Core (coordinador HTTP en `127.0.0.1:8300`), su ledger
SQLite (`coordinator/ledger/kukia.db`, 319 MB, encadenado por hash y sellado cada hora)
y su minero (`worker/kukia_worker.py`, GPU CUDA). El pago va **por trabajo demostrado**
(GFLOPs verificados × precio), la emisión tiene calendario hasta el 01-01-2150 y un
presupuesto de **1,3106 KIA/minuto**.

Reglas de la casa que un auditor NO puede romper:

- **Nada de borrar.** Solo añadir, y solo si el dueño lo pide.
- `I:\CEREBRO` es de solo lectura (el cerebro de la máquina). No se toca.
- Antes de parar o reiniciar algo: copia verificada (tamaño + hash).
- Copiar > verificar > preguntar. Nunca mover.
- No tocar Python, CUDA, drivers ni entornos de Pinokio sin autorización.
- Distinguir siempre lo comprobado de lo supuesto.

---

## 1. Estado del sistema AHORA (comprobado a las 16:44 del 23/10)

```
127.0.0.1:8300  Core       PID 37892  (relanzado por el guardia a las 15:51:46)
127.0.0.1:8301  Panel      PID 37132  HTTP 200
minero          PID 53828  (levantado por el guardia a las 17:15, tras el ensayo libre)
guardia         PID 48676  (relanzado a las 17:14; mantiene Core, panel, minero, etc.)
```

Aviso: entre las 16:42 y las 17:15 el minero y el guardia estuvieron **parados a
propósito** para medir el 100 % de GPU con la tarjeta libre. Producción perdió ~33 min
de minado (delta 0 de tareas, comprobado dentro de los dos ensayos) y se restauró
después: el guardia nuevo levantó el minero solo y volvió a pagar (+237 tareas en 60 s).

`GET /stats` en el momento de escribir esto:

| Campo | Valor |
|---|---|
| `version` | 0.4.2 |
| `supply_emitido` | 15.086.453,679954 KIA (techo 100.000.000) |
| `tareas_ok` | 360.791 · cola 95 · nodos 18 |
| `emision_por_min` | **1,3106** (el presupuesto) |
| `modo_pago` | **potencia** |
| `precio_gflop` | 0,001217892294 (GFLOP/min de la red: 1.076,089) |
| `dificultad` | `tops_red` 1.060 · factor 0,779 · matriz_n 598 · inferencia_n 149 · difusion_pasos 1.907 |
| `integridad` | 238 sellos · `pagos_bloqueados: false` |
| `emision.fin_emision` | **2150-01-01**, era 0, halving 20,67 años |

Hashes de los ficheros que están **corriendo** (para que el auditor compruebe que
audita el código que corre):

```
coordinator/kukia_core.py         191428 B  sha256 d4a84e08504e2c26…
worker/kukia_worker.py             83017 B  sha256 fd5ab5eb08b954e3…
tools/ensayo_competencia.py        16767 B  sha256 89a7870a685bbd09…
medidas/medir_pagos_produccion.py   2103 B  sha256 0fe10bbdf93210ff…
```

Ojo: el Core **sí** corre el fichero de arriba (reiniciado a las 15:51). El minero
**no**: su proceso es de las 14:17 y el fichero se tocó a las 15:15 (ver apartado 7.2).

---

## 8. Preguntas concretas para el auditor

1. ¿La bolsa está bien colocada en `pago_por_trabajo()` y no antes del reparto 70/30?
   ¿Se te ocurre un camino en el que un nodo cobre más del presupuesto entre dos
   transacciones?
2. Con el precio = presupuesto ÷ GFLOP/min de la red, ¿hay alguna forma de que un nodo
   con poca potencia pero mucha latencia (muchas tareas pequeñas) gane más que uno
   potente? Los números del ensayo de competencia dicen que la parte manda, pero no se
   ha probado un perfil así.
3. ¿El recorte de la bolsa por orden de llegada es aceptable para la moneda, o hay que
   repartir proporcionalmente? Consecuencias en el ledger y en los sellos.
4. ¿Ves riesgo en que el Core de producción corra 381 líneas sin commitear?
5. `_nodo_es_cpu()` usa «VRAM 0 y 0 TOPS» para los nodos viejos. ¿Qué pasa con un nodo
   GPU que declare mal su VRAM (drivers raros)? Se quedaría sin difusiones y cobraría
   menos; ¿lo considerarías abuso o accidente?
