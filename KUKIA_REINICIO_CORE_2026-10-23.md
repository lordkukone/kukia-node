# Reinicio del Core de producción (23/10/2026, 15:51)

El Core de las 11:40 se ha parado y el guardia ha levantado el del código de disco
(parche de difusiones + bolsa de emisión). **Sigue pagando igual: 1,29 KIA/min antes y
1,26–1,29 después**, mismo pago por tarea y cero errores en el log.

Todo lo de aquí está comprobado en esta máquina; lo que no se probó se dice al final.

---

## 1. Antes de tocar nada

| Qué | Dato |
|---|---|
| Core en marcha | PID 42048, arrancado a las 11:40 con el código anterior |
| Pago medido (15:29–15:50) | **1,2937 KIA/min**, 260 tareas/min, 0,0051 KIA/tarea |
| Supply | 15.086.393,05 KIA · `emision_por_min` 1,3106 |
| Copia verificada | `backups/kukia_20261023_155117/` — 396.744 asientos, 233 sellos, **«VERIFICADA: la copia cuadra con los sellos»** |
| `kukia.db` | 319.045.632 B · sha256 `47a9cbdf756a34b3…` |
| `coordinator/kukia_core.py` | 191.428 B · sha256 `d4a84e08504e2c26…` |

La copia se hizo con `tools/backup_ledger.py` (API de backup de SQLite, no copia de
fichero en caliente: esa es la que dejó el ledger mal formado el 23/10).

---

## 2. El reinicio

- `taskkill /F /PID 42048` a las **15:51:39**. No hay parada ordenada posible: el Core
  corre con `pythonw.exe`, sin consola, y uvicorn no recibe Ctrl+C. SQLite aguanta el
  corte (journal), y no se perdió ninguna tarea en vuelo.
- El **guardia** (PID 14476) vio el puerto 8300 cerrado y lo relanzó solo: **puerto
  arriba 7 s después**, PID nuevo **37892**. No he arrancado nada a mano.
- El log de producción es `logs/core.log` y ahora sí escribe ahí (el proceso viejo
  también lo hacía, vía guardia).

### Lo que dice el log al arrancar (literal)

```
[KUKIA] integridad OK: cadena intacta, 233 sellos
INFO:     Started server process [37892]
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:8300 (Press CTRL+C to quit)
[KUKIA Core] archivado: 255 tareas viejas (total 152677)
[KUKIA Core] auto-tareas: cola 101 -> 120
```

Y en los ~6 min siguientes: **0 `ERROR`, 0 `Traceback`, 0 respuestas 500**.

### Cómo sé que corre el código nuevo

En `meta` aparecen las claves de la bolsa, que no existían antes:
`bolsa_kia` = 1,1525 y `bolsa_ts` = 1792763831,7. Eso solo lo escribe la versión de
disco. El modo de pago sigue en `potencia` y el precio por GFLOP se sigue recalculando
solo (0,0010769 ahora mismo).

---

## 3. Pago: antes y después

Mismo banco de medida, `medidas/medir_pagos_produccion.py` (ledger en solo lectura).

| Minuto | KIA | Tareas | KIA/tarea |
|---|---|---|---|
| 15:33 (antes) | 1,6116 | 253 | 0,006370 |
| 15:37 (antes) | 1,6118 | 259 | 0,006223 |
| 15:43 (antes) | 1,1508 | 271 | 0,004247 |
| 15:50 (antes) | 1,2806 | 260 | 0,004926 |
| **media antes 15:29–15:50** | **1,2937** | **260/min** | **0,0051** |
| 15:52 (después) | 1,1717 | 206 | 0,005688 |
| 15:53 | 1,1668 | 246 | 0,004743 |
| 15:54 | 1,3717 | 242 | 0,005668 |
| 15:55 | 1,3204 | 261 | 0,005059 |
| 15:56 | 1,2636 | 246 | 0,005136 |
| **media después 15:52–15:56** | **1,2588** | **240/min** | **0,0051** |

Comprobación independiente por saldo: en 60 s el supply pasó de 15.086.400,061 a
15.086.401,349 → **1,289 KIA/min** (objetivo 1,3106). Tareas: 353.789 → 354.007
(+218 en un minuto).

Y sobre lo pagado desde el reinicio (1.608 asientos): **0 pagos en cero**, mínimo
0,000000415, mediana 0,000463759, máximo 0,022247 — el reparto por GFLOP no ha
cambiado, y el máximo sigue muy por debajo del techo de 10 KIA.

Los dos minutos bajos (15:50 y 15:51 con 0,84 y 0,87 KIA) son el hueco de la parada,
no un cambio de pago.

---

## 4. Lo que hay que saber

- **La bolsa no se nota en producción** y es normal: llevaba media hora pagando
  1,31 KIA/min, justo el presupuesto. La bolsa solo recorta cuando alguien se pasa
  (red vacía, arranque en frío). Donde se ve es en el ensayo de esta tarde:
  47,46 KIA en el primer minuto → **9,17 KIA en 8 minutos**.
- **El minero es el proceso de las 14:17**, anterior al worker que declara su
  dispositivo (fichero de las 15:15). Por eso los 40 nodos tienen `device` vacío y el
  Core usa el plan B para saber quién es CPU (VRAM 0 y 0 TOPS). Hoy da igual —
  producción tiene solo 2 nodos vivos, los dos con GPU —, pero para que un nodo CPU
  declare lo suyo hay que reiniciar el minero. No lo he tocado.
- El Core de producción corre **código sin commitear** (381 líneas nuevas en
  `kukia_core.py`). No he commiteado nada.
- Parada dura (`taskkill /F`), no ordenada: es lo que hay con `pythonw`. No se perdió
  trabajo (7 tareas en ejecución y 114 en cola en el momento de mirar, ninguna fallida).

## 5. Sin probar

- Un reinicio **con nodo CPU conectado** (no hay ninguno ahora): el corte de
  difusiones a CPU está probado en banco, no en producción.
- Reinicio del Core con tareas en vuelo grandes (difusiones de 20 s): no había
  ninguna en ese momento.
- Reinicio de la máquina entera (que el guardia levante Core, minero, panel y túnel
  en frío y el minero vuelva a cobrar).
