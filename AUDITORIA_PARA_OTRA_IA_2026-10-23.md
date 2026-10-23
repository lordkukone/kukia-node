# KUKIA — dossier de auditoría del 23/10/2026 (para otra IA)

Esto es todo lo que se ha hecho hoy en el nodo, con las pruebas al lado, para que otra
IA lo audite **sin acceso a esta conversación**. Cada afirmación lleva su evidencia
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

## 2. Cambios de hoy en código, uno por uno

### 2.1 Minero: dos ranuras de cálculo por tarjeta (opcional, apagado por defecto)

- **Qué**: el bucle de cálculo se separó en `_calcular()` y por cada dispositivo se
  lanzan `KUKIA_HILOS_GPU` ranuras (1 = comportamiento de siempre).
- **Mandos nuevos** (todos por variable de entorno): `KUKIA_HILOS_GPU` (1 por defecto),
  y para acompañarla `KUKIA_PROFUNDIDAD` 2→6, `KUKIA_EN_VUELO` 4→8,
  `KUKIA_MAX_TAREAS_NODO` 4→8 (estos dos últimos en el Core).
- **Por qué está apagado**: medido, el 100 % sube la GPU del 28 % al 60 % pero
  **baja** el KIA/kWh un 27 %. La emisión va por presupuesto: el mismo KIA por más
  vatios.
- **Evidencia**: `KUKIA_GPU_100_2026-10-23.md`; `medidas/ensayos/gpu2_hora_20261023_115519/`
  (60 min de serie: 1,433 KIA/min, GPU al 27,8 %) y
  `medidas/ensayos/gpu_100pct_20261023_150358/` (10 min con 2 ranuras: 1,406 KIA/min,
  GPU al 60,2 %).
- **Riesgo**: 2 ranuras suben CPU y vatios; no se midió la fuente.

### 2.2 Difusiones fuera de los nodos CPU

- **Qué**: el Core ya no entrega `difusion_calor` a un nodo CPU, ni como tarea ni como
  réplica. El nodo declara `device` en el registro (columna nueva `nodos.device`); los
  nodos viejos se reconocen por VRAM 0 y 0 TOPS (`_nodo_es_cpu`). Además, el generador
  de tareas no fabrica difusiones si no hay GPU activa y no deja que las encoladas
  ocupen la cola.
- **Por qué**: una difusión cuesta 0,8 s en GPU y 19,5 s en CPU; al nodo CPU se le iba
  el 81 % del tiempo en la tarea que peor hace.
- **Evidencia** (`KUKIA_DIFUSIONES_CPU_2026-10-23.md`): nodo CPU solo, 6 min antes y
  después → 50 tareas y 15 difusiones vs **1.836 tareas y 0 difusiones**; parada
  ordenada 21,1 s → 0,6 s. Con nodo GPU, 148 difusiones, 0,4 s de media (no se rompió).
- **Estado en producción**: activo desde el reinicio de las 15:51. Hoy no hay ningún
  nodo CPU conectado, así que en producción aún no ha hecho nada visible.

### 2.3 Bolsa de emisión (el arreglo del arranque en frío)

- **El fallo que arregla**: el precio por GFLOP sale de los GFLOP/min de los últimos
  10 min. Con un Core recién creado esa ventana está vacía, el precio se dispara y las
  primeras tareas se pagaban a precio de oro: **47,46 KIA en el primer minuto** y 13,58
  en el segundo, contra un presupuesto de 1,31 KIA/min (×36). El techo por tarea no lo
  evitaba: 147 tareas × 0,3 KIA siguen siendo 47.
- **Qué se añadió** (`coordinator/kukia_core.py`, bloque «BOLSA DE EMISION»):
  - `presupuesto_por_minuto()`: KIA/min que toca según el calendario (mismo número que
    `/stats`).
  - `cobrar_de_la_bolsa(kia)`: recorta el pago a lo que queda en la bolsa y lo
    descuenta. La bolsa se llena a `presupuesto × (segundos desde el último pago)/60`
    y **no guarda más de 1 minuto** (`KUKIA_BOLSA_TOPE_MIN`, 1 por defecto), así que da
    igual cuánto tiempo haya estado parada la red: no hay rebote de KIA acumulados.
  - Se aplica dentro de `pago_por_trabajo()`, justo antes de devolver el pago, o sea
    **antes** de repartir el 70/30 entre ejecutor y verificador.
  - El estado vive en `meta` (`bolsa_kia`, `bolsa_ts`) y se escribe dentro de la misma
    transacción `BEGIN IMMEDIATE` con `_pago_lock` que el pago (línea ~2297 de
    `kukia_core.py`), así que si el pago no se confirma, el descuento tampoco.
  - `/stats` publica `bolsa_emision: {kia_disponible, tope_min, presupuesto_kia_min}`
    para poder verlo desde fuera.
- **Evidencia**: Core aislado recién creado, 2 GPU, 6 min pedidos / 8 min de serie
  (`medidas/ensayos/bolsa_frio_20261023_152523/resumen.json`):

  | | Antes del arreglo | Con la bolsa |
  |---|---|---|
  | Primer minuto | 47,46 KIA | — |
  | Total de la ventana | 47 + 13,58 + … | **8,83 KIA en 8 min** (1,10 KIA/min) |
  | Pagos en cero | 0 | 0 |
  | Tareas / fallos | — | 1.184 pagadas / 0 fallos |

- **Comprobado en producción** tras el reinicio: `meta` tiene `bolsa_kia` y `bolsa_ts`
  (solo las escribe esta versión) y el pago se quedó en 1,289 KIA/min.

### 2.4 Reinicio del Core de producción (15:51)

- Copia verificada antes: `backups/kukia_20261023_155117/` (396.744 asientos,
  233 sellos, «VERIFICADA: la copia cuadra con los sellos»), `kukia.db`
  319.045.632 B sha256 `47a9cbdf756a34b3…`.
- Parada: `taskkill /F /PID 42048` (no hay parada ordenada posible: corre con
  `pythonw.exe`, sin consola, y uvicorn no recibe Ctrl+C).
- Relanzado **solo por el guardia** en 7 s (PID 37892). Log `logs/core.log`:
  `integridad OK: cadena intacta, 233 sellos`, `Application startup complete`,
  `auto-tareas: cola 101 -> 120`. Después, 6 min sin un solo `ERROR`, `Traceback` ni
  500.
- Pago antes/después: **1,2937 KIA/min → 1,2588 KIA/min**, 0,0051 KIA/tarea en los dos
  lados, 0 pagos en cero, máximo por tarea 0,0222 (techo 10).
  `KUKIA_REINICIO_CORE_2026-10-23.md` y `medidas/medir_pagos_produccion.py`.

### 2.5 Ensayo de competencia (herramienta nueva)

- `tools/ensayo_competencia.py`: Core aislado + dos mineros reales en árboles separados
  (identidad y cerradura propias), uno fijo de rival y otro con `--ranuras 1|2`.
  Mide KIA por nodo **desde el ledger** (las cuentas de cobro, no lo que dice el
  minero), GFLOP/min de la red, precio y uso de cada GPU.
- Resultado con la GPU **ocupada** por el minero de producción (14 min por brazo,
  `KUKIA_COMPETENCIA_2026-10-23.md`):

  | | Test 1 ranura | Test 2 ranuras |
  |---|---|---|
  | KIA del nodo de prueba | 9,1858 | 9,4353 (**+2,7 %**) |
  | Su parte del pastel | 49,9 % | 50,8 % |
  | Tareas pagadas | 1.695 | 1.803 (+6,4 %) |
  | KIA por tarea | 0,005419 | 0,005233 (−3,4 %) |
  | KIA/min de la red | 1,3147 | 1,3253 |

- Repetido luego con el **minero de producción parado** (GPU libre), que es lo que
  pedía el dueño (`KUKIA_COMPETENCIA_GPU_LIBRE_2026-10-23.md`):

  | | Test 1 ranura | Test 2 ranuras |
  |---|---|---|
  | KIA/min del nodo de prueba | 0,6786 | **0,7496 (+10,5 %)** |
  | Su parte del pastel | 50,1 % | 52,1 % |
  | KIA/min del rival | 0,6764 | 0,6899 |
  | KIA/min de la red | 1,3550 | 1,4394 |
  | GFLOP/min de la red | 1.704 | 1.755 |

  O sea: el 100 % paga **+10,5 %** con la tarjeta libre (contra +2,7 % con la tarjeta
  ocupada), nunca el doble. El reparto manda: el pastel es fijo.

- Lectura: el pago es `presupuesto × tu parte`. Con competencia, trabajar más sube tu
  parte, pero mucho menos que tu trabajo (el precio por GFLOP baja en cuanto sube el
  GFLOP/min de la red). Y con la GPU ya ocupada por el minero de producción, la
  segunda ranura no compró potencia: el uso de la 5070 no subió (74,4 % → 71,7 %).

---

## 3. Mapa de ficheros de hoy

Código tocado (sin commitear):

| Fichero | Cambio | Tamaño |
|---|---|---|
| `coordinator/kukia_core.py` | +381 / −32 líneas (bolsa, difusiones a GPU, `device`, calendario 2150, pago por potencia) | 191.428 B |
| `worker/kukia_worker.py` | +700 líneas aprox (ranuras, `device`, pipeline) | 83.017 B |

Herramientas nuevas:

- `tools/ensayo_competencia.py` — el ensayo de dos nodos (apartado 2.5).
- `medidas/medir_pagos_produccion.py` — pago por minuto del ledger real, en solo lectura.

Informes (todos en la raíz del proyecto):

| Fichero | Qué cuenta |
|---|---|
| `KUKIA_GPU_100_2026-10-23.md` | 100 % de GPU: sube el uso, baja el KIA/kWh |
| `KUKIA_DIFUSIONES_CPU_2026-10-23.md` | difusiones fuera de los nodos CPU |
| `KUKIA_REINICIO_CORE_2026-10-23.md` | reinicio de producción y pago antes/después |
| `KUKIA_COMPETENCIA_2026-10-23.md` | dos nodos compitiendo por la emisión (GPU ocupada) |
| `KUKIA_COMPETENCIA_GPU_LIBRE_2026-10-23.md` | lo mismo con el minero de producción parado |
| `KUKIA_MINERO_VALIDACION_2026-10-23.md` | validación general (suites, 60 min, A/B) |
| `AUDITORIA_PARA_OTRA_IA_2026-10-23.md` | este documento |

Medidas crudas: `medidas/ensayos/<etiqueta>_<fecha>/` con `resumen.json`,
`muestras.jsonl`, `core.log` y los logs de cada minero. Nada se borra.

---

## 4. Cómo reproducir cada cosa

```bash
cd C:/Users/kuki/Desktop/KUKIA

# 1) Estado de producción
curl -s http://127.0.0.1:8300/stats
python medidas/medir_pagos_produccion.py 15        # pago por minuto, solo lectura

# 2) Cadena y salud de la moneda
python tools/verificar_moneda.py
python publico/verificar_sellos.py --db coordinator/ledger/kukia.db --sellos coordinator/sellos.jsonl

# 3) El arranque en frío (Core aislado, NO toca producción)
python tools/ensayo_estabilidad.py --minutos 8 --gpus 0,1 --etiqueta bolsa_frio

# 4) Competencia de dos nodos (14 min por brazo)
python tools/ensayo_competencia.py --minutos 14 --ranuras 1
python tools/ensayo_competencia.py --minutos 14 --ranuras 2

# 5) Copia verificada del ledger
python tools/backup_ledger.py --sin-clave
```

Los ensayos aíslan el Core con `KUKIA_DB` y su propia carpeta: **nunca** escriben en
`coordinator/ledger/kukia.db`. La variable correcta es `KUKIA_DB` (ver la pifia del
apartado 6.1).

---

## 5. Lo que ya estaba, y lo que se ha parado (siempre restaurado)

- El nodo de pago por potencia (`pago_por_trabajo`, `precio_gflop`, ventana 10 min,
  techo por tarea 10 KIA) es de días anteriores; hoy solo se le colgó la bolsa.
- El calendario de emisión (fin 2150-01-01, 6 eras de 20,67 años) ya estaba puesto al
  arrancar el Core de las 11:40.
- Producción se ha parado dos veces y siempre a propósito: el Core 7 s a las 15:51
  (reinicio con el parche) y el minero 33 min, de 16:42 a 17:15, para el ensayo con
  GPU libre. Las dos veces se restauró y se comprobó que volvía a pagar.
- La billetera, el ledger, los sellos y la cadena no se han tocado a mano en todo el día.

---

## 6. Pifias conocidas (las mías), para que el auditor no las confunda con bugs

### 6.1 Abrí el ledger de producción con una instancia de prueba (14:51)

Al hacer una prueba de humo puse `KUKIA_LEDGER_DIR` cuando la buena era `KUKIA_DB`.
Resultado: una instancia de prueba abrió el ledger **real** durante ~1 segundo. Qué
hizo exactamente: los `ALTER TABLE` de arranque (añadió `nodos.device`, que habría
añadido igual el Core nuevo) y el saneado de difusiones encoladas. No pagó, no creó
tareas (`KUKIA_AUTO_TAREAS=0`) ni tocó el ledger. Se comprobó después: `PRAGMA
quick_check` ok y **historial completo de sellos OK** (384.668 asientos, 227 sellos).
Está contado entero en `KUKIA_DIFUSIONES_CPU_2026-10-23.md`.

### 6.2 Parada dura en el reinicio

`taskkill /F` sobre el Core (no había otra). No se perdió trabajo: 7 tareas en
ejecución y 114 en cola en el momento de mirar, ninguna fallida, y la cadena siguió
intacta. Pero no es una parada ordenada y conviene decirlo.

### 6.3 Un falso positivo en los ensayos

`verificar_sellos.py --core` avisa «ALTERADO: sello 0 … no encadena» porque en ese modo
pide solo los últimos 200 sellos y el primero de la ventana no tiene predecesor dentro
de ella. Con el historial completo (`--db` + `--sellos`) sale OK. No es un fallo.

---

## 7. Huecos, riesgos y lo que un auditor debería mirar con lupa

### 7.1 Código sin versionar en producción

El Core que corre en producción tiene **381 líneas nuevas sin commitear**. Funciona y
está medido, pero un `git checkout` dejaría producción con código distinto al de disco
y nadie se enteraría hasta el siguiente reinicio. Decisión pendiente del dueño.

### 7.2 Los nodos existentes no declaran su `device`

El worker **sí** manda `device` al registrarse (`kukia_worker.py:416`), pero solo al
registrarse: al arrancar con una identidad guardada **no vuelve a llamar** a
`/nodos/registrar` (solo `_re_registrar` si el Core dice que el nodo no existe).
Comprobado hoy: el minero se reinició a las 17:15 con el fichero nuevo y los 38 nodos
siguen con `device` vacío; en el log del Core no hay ni un `/nodos/registrar` reciente.

Consecuencia: para **todos** los nodos que ya existían, el Core usa el plan B (VRAM 0 y
0 TOPS) para saber quién es CPU. Funciona, pero es un rodeo, y un nodo CPU con VRAM
declarada >0 se colaría como GPU. Hoy producción no tiene nodos CPU, así que no se
nota; y el corte de difusiones a CPU **sigue sin probarse en producción** (solo en
banco). Arreglo posible: que el worker mande `device` en la telemetría (ya lo manda) y
que el Core lo guarde también por ahí, o un `/nodos/registrar` al arrancar.

### 7.3 La bolsa puede premiar la prisa, no el trabajo

La bolsa se vacía **por orden de llegada**. Si la red produce trabajo por más valor que
el presupuesto del minuto, las tareas que lleguen tarde cobran menos o cero. Dato que
apunta ahí, sin aislarlo: al pasar el nodo de prueba a 2 ranuras, sus pagos por
**verificación redundante** bajaron de 282 a 266 asientos (−5,7 %) y los del rival de
326 a 316 (−3,1 %), mientras los pagos por trabajo subían. Total de la red: 1,3147 y
1,3253 KIA/min, o sea clavado en el presupuesto. **Sospecha, no hecho.** Si al dueño le
importa el reparto justo, hay que decidir si la bolsa se reparte proporcionalmente en
vez de por llegada.

### 7.4 Detalles de implementación que conviene revisar

- `cobrar_de_la_bolsa()` se traga los errores al escribir `meta` (`except Exception:
  pass`). Si esa escritura falla, el pago sale igual y la bolsa no descuenta: se podría
  emitir de más sin dejar rastro. Es una línea, pero está.
- `_bolsa_lock` es un cerrojo aparte de `_pago_lock`, aunque la bolsa solo se toca
  dentro de `_pago_lock` + `BEGIN IMMEDIATE`. Redundante, no incorrecto.
- `bolsa_disponible()` (la que enseña `/stats`) lee `meta` **fuera** de la transacción:
  es solo informativa, pero puede ir un instante por detrás.
- La bolsa no cambia el total emitido a largo plazo (el calendario manda); solo reparte
  mejor. No hay test automático de la bolsa: se probó con el ensayo, no con una suite.

### 7.6 DEFECTO ABIERTO: la bolsa no cubre el modo «plano»

Encontrado en el ensayo con GPU libre (23/10, 16:57). Si la red lleva **más de 10 min
parada**, la ventana de precios está vacía, `precio_gflop()` devuelve `modo: "plano"` y
`pago_por_trabajo()` paga con `recompensa_pago()` **sin pasar por la bolsa**:

```python
if d.get("modo") != "potencia":
    pago, info = recompensa_pago(tier)   # sin cobrar_de_la_bolsa()
    return pago, ...
```

Medido: los primeros 20 s de ese brazo pagaron **2,27 KIA** (68 asientos a 0,033 cada
uno) cuando el presupuesto del tramo era ~0,44; el total del brazo salió **1,4394
KIA/min** frente a 1,3106. La caché de precio (`PRECIO_TTL`, 20 s) mantiene la decisión
«plano» esos 20 s aunque las tareas ya estén entrando.

No es el desastre de 47 KIA (la bolsa lo habría cortado) pero **es una fuga real**, y
está en el Core que corre producción. Arreglo pendiente: aplicar la bolsa también en
esa rama (2-3 líneas) y volver a medir.

### 7.5 Lo que no se ha probado

- Reinicio del Core con un nodo CPU conectado (el corte de difusiones a CPU está probado
  en banco, no en producción).
- 3 y 4 ranuras por tarjeta.
- El brazo de 2 ranuras con la ventana de precios ya llena desde el principio (en el
  ensayo libre arrancó vacía y eso mete el efecto del apartado 7.6 en el total).
- Vatios de CPU y fuente (con 2 ranuras la CPU sube; el consumo real es peor que el
  medido).
- Arranque en frío de la máquina entera (que el guardia levante todo y el minero vuelva
  a cobrar).
- ¿El KIA/kWh con la bolsa puesta? Los ensayos de vatios son anteriores al arreglo.

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
