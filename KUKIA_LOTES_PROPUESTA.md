# Propuesta de entrega por LOTES en el Core (sin implementar)

Estado: **propuesta**. No hay ni una línea de código nueva en el Core. Toca
protocolo y pagos, así que se decide con números delante y autorización
explícita.

Fecha: 23-10-2026. Lo medido sale del ensayo de 60 min de esta misma fecha
(`medidas/ensayos/gpu2_hora_20261023_115519/resumen.json`).

## 1. Cómo funciona hoy `/tareas/pendiente` (leído en el Core)

```python
@app.get("/tareas/pendiente")
@app.post("/tareas/pendiente")
def pendiente(nodo_id, excluir="", colas="", ts=0.0, firma="", nonce="", solo_primaria=False)
```

- **Autenticación**: firma Ed25519 del nodo sobre `pendiente:<nodo_id>:<int(ts)>`
  (o `...:<nonce>` si viene). `ts` con ventana corta; `nonce` de un solo uso
  (10 min). Nodo no registrado → 404. Nodo castigado → 429.
- **Asignación atómica**: `BEGIN IMMEDIATE` bajo `_pago_lock`; se elige la tarea
  y se marca `ejecutando` en la misma transacción. Sin desalojo: lo que ya
  corre no se interrumpe.
- **Tope por nodo**: `MAX_TAREAS_NODO = 4` para nodos con trabajo pagado,
  `MAX_TAREAS_NODO_NUEVO = 1` para nodos sin ninguna tarea buena. Las réplicas
  reclamadas **también** ocupan turno (si no, un nodo se llenaría de réplicas
  sin minar nada propio).
- **Cola**: A > B > C; dentro de cola, prioridad y antigüedad. Solo tareas con
  `vram_min <= VRAM` del nodo y que ese nodo no haya fallado ya
  (`nodos_intento`).
- **Recuperación**: tareas `ejecutando` sin entregar vuelven a la cola a los
  30 min (`ABANDONO_SEG`); 2 min para nodos sin trabajo pagado.
- **Pago**: no ocurre aquí. Ocurre en `/tareas/resultado`, que rechaza con 409
  si la tarea ya está `ok` (doble pago bloqueado) y cobra dentro de
  `BEGIN IMMEDIATE`. Un lote no cambia esa parte.

## 2. Lo que se midió hoy con el minero optimizado (60 min, 2 GPU)

| Métrica | Valor |
|---|---|
| Entregas pagadas | 14.817 |
| Réplicas entregadas | 7.132 |
| Peticiones a `/tareas/pendiente` | 23.695 |
| **Peticiones por entrega** | **1,08** |
| Latencia de entrega p50 / p90 / máx | 46 ms / 1,28 s / 7,26 s |
| Espera en cola p50 / p90 | 22,6 s / 26,9 s (había atasco de la copia) |
| Tareas/min | 247 |
| GFLOP/s de cómputo (serie del Core) | 49,5 |

**Conclusión incómoda pero real**: el pipeline ya pide la siguiente tarea
mientras entrega la anterior, así que **no se hacen 4 peticiones por tarea: se
hace 1,08**. Un lote de 4 no puede ahorrar el 75 % de las peticiones; como
mucho, el ~8 % que queda. Si alguien promete que el lote multiplica el
rendimiento, está midiendo mal.

## 3. Dónde sí hay margen

El lote ayuda cuando el viaje HTTP pesa más que el cálculo:

1. **Tareas de 3-8 ms** (matriz pequeña, inferencia): hoy el nodo tapa el hueco
   con 1-2 tareas en cola (`KUKIA_PROFUNDIDAD`), pero cada petición sigue
   costando un viaje completo.
2. **Nodos fuera de la LAN**: 30-80 ms de RTT por petición. Ahí un lote de 4
   sí recorta de verdad.
3. **Nodos con muchas GPU**: cada GPU es un nodo distinto y cada una pide por
   su cuenta; 4 GPU = 4 peticiones en paralelo compitiendo por el mismo lock
   del Core. Un lote por dispositivo no lo arregla; reduce el número de viajes.

## 4. API propuesta

```
GET|POST /tareas/lote
  nodo_id, n (1..4), colas, excluir, solo_primaria, ts, nonce, firma
  firma = Ed25519("lote:<nodo_id>:<int(ts)>:<n>:<nonce>")
```

Respuesta:

```json
{"tareas": [ {...}, {...} ], "huecos_libres": 2, "restantes_en_cola": 96, "vence": 1792749999}
```

Reglas:

1. `n` nunca puede superar los huecos libres del nodo: si pide 4 con 1 hueco,
   se devuelve 400 con `huecos_libres=1` (mejor error claro que devolver menos
   y que el worker no sepa por qué).
2. **Reserva atómica**: un solo `BEGIN IMMEDIATE`; se reservan las n tareas con
   `UPDATE tareas SET estado='ejecutando', nodo_id=?, asignado=? WHERE id=? AND
   estado='queued'` y se comprueba `rowcount==1`. Si una falla, ese hueco queda
   libre y se devuelve el resto (nunca se reserva dos veces).
3. **Nonce de lote**: mismo mecanismo `_nonce_nuevo`; un nonce por lote, no por
   tarea. La firma incluye `n` para que nadie pida 4 con una firma de 1.
4. **Mezcla**: como máximo **una tarea cara** por lote (estimada por
   `_flops_tarea`); el resto, cortas. Sin esto, un lote con una difusión de
   15 s deja las otras 3 esperando y el lote empeora el rendimiento.
5. **Compatibilidad**: `/tareas/pendiente` se queda como está. El worker nuevo
   pide lote y, si recibe 404, cae solo al endpoint viejo. Los mineros viejos
   no notan nada.
6. **Límites por dispositivo**: no hacen falta nuevos: cada GPU/CPU es un nodo
   con su id y su tope de 4. Un nodo no puede pedir para otro (la firma es del
   nodo).
7. **Expiración**: las tareas reservadas en lote siguen sujetas a
   `ABANDONO_SEG` (30 min) y a la recuperación de atascadas. El lote no crea un
   estado nuevo.

## 5. Doble pago: sin cambios (y por qué)

El lote solo asigna. El pago sigue en `/tareas/resultado`, con:

- 409 si `estado='ok'` («tarea ya pagada»),
- 409 si `estado != 'ejecutando'`,
- 409 si la tarea es de otro nodo,
- pago dentro de `BEGIN IMMEDIATE` con `_pago_lock`.

La única puerta nueva sería la reserva: como cada tarea pasa a `ejecutando` una
sola vez (`rowcount==1`), dos lotes concurrentes no pueden llevarse la misma.

## 6. Pruebas aisladas antes de tocar nada (procedimiento)

Todo sobre una copia del ledger y un Core en puerto libre (mismo patrón que
`tools/pruebas_seguridad.py`):

1. Dos nodos pidiendo lote a la vez, 50 rondas: **ninguna tarea repetida**
   (comparar ids devueltos).
2. Un nodo pidiendo 3 lotes seguidos: `en_vuelo` nunca pasa de 4.
3. Lote con `n=0` y con `n=5`: 400 claro, sin tocar la cola.
4. Nonce repetido: 401. Firma con `n` alterado: 401.
5. Nodo sin huecos: 400 con `huecos_libres=0`.
6. Nodo que muere con un lote en vuelo: las tareas vuelven a la cola a los
   30 min (ya está probado, se repite con lote).
7. Comparar entregas/min con lote vs sin lote en la misma copia, 10 min cada
   uno, dos GPU: el número que decide es **tareas/min**, no peticiones/min.

## 7. Beneficio esperado (honesto)

- Peticiones al Core: de 1,08 por entrega a ~0,3-0,5 (lote de 2-3 medio). Es
  un ahorro de CPU en la ruta de asignación, no de trabajo útil.
- Tareas/min: **+3 a +8 %** como mucho en tareas cortas; ~0 % en difusiones
  (ahí manda la GPU).
- Riesgo si se hace mal: lotes con tareas caras → GPU parada esperando; y
  reservas de más → nodos que no pueden entregar a tiempo y tareas que vuelven
  a la cola a los 30 min.
- Lo que **no** arregla: el Core recalculando cada resultado, el techo de
  emisión, ni la calidad de las tareas.

## 8. Decisión

Implementarlo requiere: autorización explícita (toca protocolo), subir la
versión del API, y pasar las pruebas de concurrencia de arriba. Mientras tanto,
la mejora que sí está disponible y medida es la del worker (pipeline,
profundidad 2, lock único), que ya está en el árbol y probada en el ensayo de
60 min.
