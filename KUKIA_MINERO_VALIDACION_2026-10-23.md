# KUKIA — validación del minero y del Core (23/10/2026)

Todo lo de aquí está medido hoy en esta máquina, contra un Core aislado en un puerto
libre y con su propio ledger dentro de `medidas/ensayos/`. Producción no se tocó
durante los ensayos: cada resumen comprueba `red_de_verdad` y el delta fue **0**.

Lo que no se ha probado, se dice en el apartado 8.

---

## Resumen

- El minero que llevaba corriendo desde las 9:44 tenía cargado el código **viejo**: el
  fichero optimizado se escribió a las 10:36 y Windows no recarga un `.py` en marcha.
  La mejora **nunca había estado en producción** hasta el reinicio de las 14:19.
  Ya está corriendo y cobrando (comprobado: `tareas_ok` 335.318 → 335.656 en 3 min).
- Suites: **101/101** de seguridad, **16/16** de gasto de billetera, pipeline completo OK.
  Una aserción de `pruebas_seguridad.py` estaba desfasada (daba por hecho pago fijo) y
  se corrigió para que compruebe el pago por potencia, que es el modo real.
- **60 min con las dos GPU**: 14.817 tareas pagadas, 0 fallos, 0 atascadas, 3 reintentos,
  GPU al 28 % de media, 41 °C de máxima, parada limpia en **0,6 s**.
- **30 min CPU + GPU**: 4.968 tareas, 0 fallos, pero la CPU tarda **17,3 s por difusión**
  (la GPU 0,8 s) y el Core le paga lo mismo → es gasto de emisión, no rotura.
- **A/B previa vs nueva: NO concluyente.** La nueva salió peor en el tramo de 20 min
  (1.934 vs 2.306 tareas) con 290 errores 5xx y la cola en 61 s. No lo vendo como mejora.
- Lotes: medido **1,08 peticiones por entrega**; el margen real es pequeño, así que la
  propuesta de `KUKIA_LOTES_PROPUESTA.md` va recortada a lo que el dato sostiene.
- Hallazgo: **copiar el ledger con el Core escribiendo deja el fichero mal formado**
  (`sqlite malformed`). Queda documentado en la propia suite para que nadie repita la
  prueba en caliente; la suite sigue siendo «solo con parada».

---

## 1. Estado real al empezar (comprobado, no supuesto)

| Qué | Dato |
|---|---|
| Rama | `main`, último commit `ba03b46` (22/10, «Anclas y sellos del 22-10») |
| Sin commitear | 154 ficheros (anclas, logs, docs). **No he commiteado nada.** |
| Producción viva | Core `127.0.0.1:8300`, panel `127.0.0.1:8301` |
| Minero de producción | arrancado 9:44 con el `.py` de **antes** de la optimización |

Ese último punto es el hallazgo gordo de la fase 1: se creía que la versión optimizada
estaba en producción y no lo estaba. Producción llevaba 4 h y media con el código viejo.

### Estado de producción después del reinicio (14:19, comprobado a las 14:28)

| Qué | Dato |
|---|---|
| Core `:8300` | vivo, responde `/stats` |
| Panel `:8301` | HTTP 200 |
| Minero | corriendo el worker **nuevo**, ~210 tareas/min |
| Tareas | 335.318 → **337.142** desde el reinicio |
| Emisión real | **1,63 KIA/min** contra objetivo 1,31 (+24 %) |
| Dificultad | factor 0,779 · `tops_red` 1.060 vs referencia 1.747 |

Lo de la emisión: mientras el ajuste de dificultad converge, la red paga por encima del
presupuesto. Se corrige solo con el retarget; **no he tocado la emisión**. Si quieres que
lo mire, es aparte de esta validación.

---

## 2. Suites y resultados reales

| Suite | Resultado | Nota |
|---|---|---|
| `tools/pruebas_seguridad.py` | **101/101** | la aserción de cobro se corrigió al modo potencia |
| `tools/prueba_gasto_billetera.py` | **16/16** | gasto, recuperación y wordlist |
| Pipeline tarea→resultado→réplica→pago | **OK** | con Core aislado recién creado |
| `tools/prueba_failover_negativas.py` | **no ejecutable en caliente** | copia el ledger vivo → fichero partido → sqlite malformed |

Lo del failover no es un fallo de la moneda, es que esa suite necesita un ledger congelado.
La he dejado documentada como «solo con parada» en vez de fingir un verde.

Lo que sí he dejado escrito en `tools/prueba_failover_negativas.py` es el porqué: la
copia con `shutil.copyfile` vale **solo con el Core parado**. Con el Core escribiendo
sale a medias y SQLite la rechaza. No he cambiado la lógica de la suite; he dejado el
aviso donde se comete el error.

---

## 3. Sesenta minutos, dos GPU (`gpu2_hora_20261023_115519`)

| Métrica | Valor |
|---|---|
| Duración medida | 60,0 min (60 muestras) |
| Tareas ok en la ventana | **14.817** (delta en tabla 14.814) |
| Fallidas / atascadas >30 min | **0 / 0** |
| Reintentos | 3 |
| Ritmo | ≈247 tareas/min |
| KIA | 73,857 trabajo verificado + 12,113 verificación (2.613 réplicas) |
| Cómputo | 49,5 GFLOP/s de media (39,6 – 64,8) |
| Cola | p50 22,6 s · p90 26,9 s · máx 77,7 s |
| Entrega al Core | p50 46 ms · p90 1,28 s · máx 7,26 s |
| GPU | 28 % medio · 41,0 °C máx · VRAM 4,3 / 4,5 GB |
| CPU / RAM | 53,1 % / 43,5 % |
| Parada | ordenada en **0,6 s** |

Peticiones al Core: 23.695 `pendiente`, 14.817 `resultado`, 7.132 `replica` →
**1,08 peticiones por entrega**. Es el dato que manda para la propuesta de lotes.

---

## 4. Treinta minutos, CPU + GPU (`cpu_gpu_30m_20261023_125603`)

| Métrica | Valor |
|---|---|
| Tareas ok | **4.968** (165,9/min) |
| Fallidas / atascadas | 0 / 0 |
| KIA | 108,80 trabajo + 0,28 verificación |
| Cola | p50 35,5 s · máx 86,3 s |
| Entrega | p50 128 ms · máx 83,9 s |
| CPU | 81,5 % (era 53 % sin CPU) |
| Parada | 11,9 s |

**Lo que enseña**: la CPU tarda **17,3 s de media por difusión** (máx 24,5 s) contra
0,77–0,87 s de la GPU, y el Core le paga la misma tarifa por tarea. Salió más KIA con
menos GFLOP: 108,8 KIA por 34,7 GFLOP de cómputo equivalente. No es un bug, es
economía: quien mine en CPU con una GPU al lado gana KIA más despacio pero gana.

Regla que se deduce: **la CPU solo tiene sentido si no hay GPU**, que es lo que el
minero ya hace por defecto (`--cpu` es opt-in).

---

## 5. Comparación previa vs nueva — no concluyente

Mismo banco, misma carga, 20 min cada una, seguidas:

| | Previa | Nueva |
|---|---|---|
| Tareas | 2.306 | 1.934 |
| GFLOP/s medio | ~36 | 29,0 |
| Errores 5xx | 0 | **290** |
| Cola p50 | — | 61,5 s |
| RAM | 73 % | 80 % |

No sirve para decir «la nueva es mejor». El tramo de la nueva arrancó con la máquina
cargada (ComfyUI levantado a las 13:20, RAM al 80 %) y con 290 respuestas 5xx del Core.
**Conclusión honesta: la comparación no mide la versión, mide el estado de la máquina.**
Para que valga hay que repetirla con la máquina en reposo y con los dos tramos
alternados (A/B/A/B), no seguidos.

Lo que sí está medido y no depende del A/B: la corrida de 60 min con las dos GPU no
tuvo ni un fallo, ni una tarea atascada, ni un 5xx, y paró en 0,6 s.

---

## 6. Problemas encontrados (y en qué estado quedan)

| # | Problema | Gravedad | Estado |
|---|---|---|---|
| P1 | **Cobro de arranque en frío**: Core recién creado con ≥10 min de ventana vacía → 47,5 KIA en un minuto (presupuesto 1,31 KIA/min) | media (economía) | **abierto**, no toco la emisión sin que lo digas |
| P2 | La CPU cobra tarifa de GPU por tareas 20× más lentas | baja (diseño) | documentado; `--cpu` sigue opt-in |
| P3 | Suite de failover no corre con el ledger vivo (copia partida → `sqlite malformed`) | baja | **documentado en el código**; sigue siendo solo con parada |
| P4 | Aserción de `pruebas_seguridad.py` pedía pago fijo >0 (modo viejo) | baja | **arreglado**, 101/101 |
| P5 | Banner del minero decía «NO CUMPLE (mínimo 125)» con el fin en 2150 | cosmético | **arreglado**: ahora comprueba el largo del calendario (`calendario de 124.0 anos`), verificado contra el Core vivo |
| P6 | 290 errores 5xx en la corrida «nueva» | a investigar | **abierto**, no reproducido en la de 60 min |
| P7 | Producción corría el worker viejo desde las 9:44 | alta | **resuelto** 14:19, ya paga con el nuevo |

---

## 7. Propuesta de lotes (recortada al dato)

Está en `KUKIA_LOTES_PROPUESTA.md`. Lo esencial: con **1,08 peticiones por entrega**
medidas, agrupar resultados ahorra como mucho un ~7 % de peticiones, no el 40 % que
estimé sobre el papel. Merece la pena solo en el `pendiente` (23.695 peticiones para
14.817 tareas = 1,6 por tarea), y ahí el ahorro es de red, no de CPU.

Orden propuesto, de menos a más riesgo:
1. **Lote de `pendiente`** (el minero pide 2–4 tareas en una petición). Ahorro de red,
   cero cambio en el ledger. Es el que yo haría primero.
2. **Lote de `resultado`** solo con `?lote=1` y el Core aceptando ambos formatos.
3. **Nada de tocar `/replica`**: son 7.132 peticiones que son la garantía de integridad.

Ninguna de las tres está implementada. La 1 es la única que recomiendo sin más datos.

---

## 8. Lo que NO se ha probado

- **No hay A/B válido** entre versión previa y optimizada (apartado 5).
- **No se ha probado con dos máquinas minando a la vez** contra el mismo Core.
- **No se ha probado un corte de luz / kill -9** a mitad de entrega (sí parada ordenada).
- **No se ha probado el failover de la réplica** con el ledger congelado.
- **No se ha tocado la emisión** ni el pago por potencia: P1 sigue abierto y es decisión tuya.
- ComfyUI estaba levantado en 8188 durante los ensayos; no lo he parado.

---

## Ficheros de esta tanda

- `tools/ensayo_estabilidad.py` — banco: Core aislado + minero + muestras por minuto.
- `tools/comparar_ensayos.py` — compara dos `resumen.json`.
- `medidas/ensayos/*/resumen.json` — las cuatro corridas, con la serie completa.
- `KUKIA_LOTES_PROPUESTA.md` — propuesta de lotes.
