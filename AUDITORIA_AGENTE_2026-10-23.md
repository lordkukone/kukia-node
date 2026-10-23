# KUKIA — auditoría independiente del código (23/10/2026, 19:05)

Auditoría de solo lectura sobre el dossier `AUDITORIA_PARA_OTRA_IA_2026-10-23.md`.
No se ha modificado producción, ni el ledger, ni sellos, ni `I:\CEREBRO`. Las
sondas que se citan arrancan Cores aislados con `kukia.db` nuevo en una carpeta
temporal; el ledger real solo se ha abierto con `PRAGMA query_only=1`.

Código auditado: `coordinator/kukia_core.py`, 204.039 B, sha256
`F0CA5322209555232FFA8AFB2676B5EEB0A18B8800E0566EB196196845FF0900`
(18:48). Minero: `worker/kukia_worker.py`, 18:46.

## 0. Veredicto en una línea

El dossier se queda corto: la fuga que denuncia (§7.6) ya está cerrada en disco
y medida, pero hay un fallo **más grave y no detectado** — dos identidades
coludidas pueden cobrar un resultado **falso** porque la réplica sustituye al
recálculo 9 de cada 10 veces. Y producción sigue corriendo el Core de las 15:51,
con la fuga abierta.

## 1. Estado real de los cinco puntos del dossier

| Punto | En disco (18:48) | En producción (PID 37892, 15:51) |
|---|---|---|
| 7.6 fuga del modo plano | **cerrado**: `pago_plano()` (3286) y `pago_por_trabajo()` (3311) son las dos únicas puertas, las dos pasan por la bolsa | **abierto**: `/stats` no publica `precio_minuto`; es el binario anterior |
| 7.4 `except Exception: pass` en la bolsa | **cerrado**: `_guardar_bolsa()` (3143) no se come errores; si falla, ROLLBACK y `500` | **abierto** |
| 7.3 reparto por orden de llegada | **cerrado**: precio único por minuto (3220) | **abierto** |
| 7.1 código sin versionar | **cerrado**: commit `e489fa8` | desfase invertido: git/disk más nuevos que el proceso |
| 7.2 nodos sin `device` | **cerrado en código**: latido también en CPU, Core guarda `device` (1843) y `_nodo_es_cpu` (752) mira la telemetría | **abierto**: los 2 nodos vivos siguen con `device=''` |

Medido hoy sobre el código de disco (no del dossier):

- Plano con ventana vacía, 25 tareas por HTTP: **1,311897 KIA** (presupuesto
  1,311897); con el pago plano viejo, **155,1705** (×118,3). `tools/prueba_bolsa_arranque.py`.
- Bolsa rota a propósito (trigger sobre `bolsa_pagado`): `500`, suministro
  15.000.000 → 15.000.000, tarea sigue `ejecutando`, **0 asientos**. `tools/prueba_bolsa_estricta.py`.
- Reinicio a mitad de minuto: 1,311897 antes y después; el minuto no pasa del
  presupuesto. Mismo fichero.
- Proporcional: x8,0000 de trabajo → x8,0000 de KIA. `tools/prueba_precio_minuto.py`.

## 2. Hallazgos nuevos

### H1 — ALTA (integridad): dos identidades coludidas cobran trabajo falso

- **Dónde**: `kukia_core.py:2285` — si la réplica coincide con el primario y
  `_toca_auditoria(tid)` es falso, se marca `ok = True` **sin recalcular**.
  `AUDITORIA_1_DE = 10` (línea 167), o sea 9 de cada 10 tareas no se comprueban.
- **Qué permite**: dos claves en la misma máquina (el registro admite 10
  nodos/hora por IP; la réplica solo exige `t.nodo_id != primario`, línea 3900)
  dicen las dos la misma mentira: se confirman entre ellas y cobran.
- **Medido** (`tools/auditoria_replica.py`, Core aislado, HTTP real):
  - tarea `matriz n=800` (1,024 GFLOP) fuera de la muestra: A entrega `"0"`,
    B entrega `"0"` como réplica → **`200 ok=True kuki=0,918328 KIA`**,
    `verificado_por=replica`. El resultado es falso y se guarda como verificado.
  - control con la misma jugada en una tarea **dentro** de la muestra:
    `ok=False kuki=0` (el Core recalculó y lo rechazó).
- **Impacto**: integridad del ledger (trabajo "verificado" que nadie verificó) y
  degradación del servicio (se paga cómputo que no se hizo), no emisión extra:
  el pago sigue topado por la bolsa y el techo.
- **Arreglo propuesto** (por orden de coste):
  1. No aceptar como réplica un nodo con la misma IP registrada que el primario
     (`registros_nodo`, ya se guarda) ni la misma cuenta de cobro.
  2. Muestreo 1 de 3 mientras alguno de los dos nodos tenga < 50 tareas
     verificadas (hoy es fijo 1/10 desde el primer día).
  3. Pagar la parte del verificador (30 %) solo si la tarea salió auditada o si
     el par primario/réplica ya tiene historial verificado.
- **Criterio de aceptación**: `tools/auditoria_replica.py` pasa a
  "no se pudo cobrar la mentira" con los dos nodos en la misma IP, y un par en
  IPs distintas sigue cobrando 70/30 con el reparto intacto.

### H2 — MEDIA-BAJA (economía): después de un minuto sin trabajo, el siguiente paga el doble y el de después nada

- **Dónde**: `precio_unico_minuto()` (3220) cae a la ventana de 10 min cuando el
  minuto anterior no tiene trabajo, y `_cerrar_minuto_si_toca()` (3206) apunta el
  error como crédito.
- **Medido** (`tools/auditoria_credito.py`, reloj falso dentro del proceso):
  - minuto normal: precio 0,00164017 KIA/GFLOP, 799,85 GFLOP → objetivo 1,311897.
  - minuto que se pasa ×2: precio 0, objetivo 0, **pago de una difusión 0**.
  - minuto siguiente: precio 0,00820086 (ventana de 10 min, 159,97 GFLOP/min) y
    **40 tareas = 799,85 GFLOP pagaron 2,623794 KIA** (el fusible de 2×).
- **Lectura**: el par de minutos suma exactamente dos presupuestos (no se crea
  ni se pierde KIA), pero el reparto es a saltos: un minuto a cero y otro al
  doble. Con la ventana de 10 min diluida, el minuto bueno se pasa y lo corta el
  fusible en vez del precio.
- **Arreglo propuesto**: dividir la ventana de 10 min por los minutos que de
  verdad tuvieron trabajo (no por 10), y poner suelo al objetivo (p. ej. 50 %
  del presupuesto) para que ningún minuto pague cero.
- **Criterio de aceptación**: con el mismo escenario, el minuto siguiente paga
  ≈1,31 y no 2,62, y ningún minuto paga 0.

### H3 — BAJA (concurrencia): el cierre del minuto se hace fuera de `_pago_lock`

- **Dónde**: `pagado_en_minuto()` y `bolsa_disponible()` (3275) llaman a
  `_cerrar_minuto_si_toca()` (3206), que **muta** `_minuto_actual`. `/stats` los
  llama sin `_pago_lock`; un pago en vuelo puede acabar contabilizado en el
  minuto siguiente si el cierre ocurre entre su cálculo y su `_anotar_pagado`.
- **Impacto**: descuadre de contabilidad del minuto, nunca emisión por encima
  del fusible. No se ha reproducido (es una ventana de milisegundos).
- **Arreglo propuesto**: cerrar el minuto solo dentro del camino de pago (o con
  un cerrojo propio) y que `/stats` lea sin cerrar.

### H4 — BAJA (telemetría): `kia_disponible` no cuenta el fusible

- **Dónde**: `bolsa_disponible()` (3275) devuelve `presupuesto - pagado`.
  Pero el pago real admite hasta `presupuesto × FUSIBLE_MINUTO_X` (3057).
- **Impacto**: `/stats` dice "0 disponible" mientras el Core sigue pagando hasta
  2×. El panel y la web informan de una bolsa vacía que no lo está.
- **Arreglo propuesto**: publicar `kia_disponible` y `kia_disponible_fusible`
  por separado.

### H5 — MEDIA (red): el `device` del latido es autodeclarado

- **Dónde**: `/nodos/telemetria` (1843) guarda `nodos.device`; `_nodo_es_cpu`
  (752) lo usa para decidir quién recibe difusiones.
- **Qué permite**: una CPU sin GPU puede latir como `cuda:0` y recibir
  difusiones, que en CPU tardan ~20× más (19,5 s contra 0,8 s, dossier §2.2).
  No cobra más (el pago va por GFLOP), pero ocupa la cola y retrasa la red.
- **Arreglo propuesto**: no mandar difusiones hasta que el nodo tenga potencia
  medida (`gflops_medido`, ya la calcula el Core) o hasta 20 tareas verificadas.
- **Criterio de aceptación**: un nodo con `device=cuda:0` pero GFLOP medidos de
  CPU no recibe difusiones.

### H6 — BAJA (red): un nodo viejo con VRAM declarada >0 y sin `device` pasa por GPU

- **Dónde**: `_nodo_es_cpu` (752). El plan B solo marca CPU si VRAM 0 **y** TOPS 0.
- **Impacto**: una CPU que declare VRAM (torch la ve, aunque mine en CPU) se
  cuela como GPU hasta que late con `cpu` (worker nuevo) o se re-registra.
- **Arreglo propuesto**: sin `device` conocido y sin latido, no mandar
  difusiones; el latido lo resuelve en 3 s.

### H7 — MEDIA (proceso): importar el Core sin `KUKIA_DB` escribe en producción

- **Dónde**: `DB = Path(os.environ.get("KUKIA_DB") or (Path(__file__).parent / "ledger" / "kukia.db"))`
  y el arranque del módulo hace `schema()`, `genesis()` y `recalcular_resumen()`.
- **Qué pasó** (esta misma sesión, 18:31): un script que solo quería usar
  `_flops_tarea` importó el módulo sin `KUKIA_DB` → el Core de producción
  archivó **306 tareas** (mantenimiento normal, no se perdió nada; verificado
  después con `/stats`). No es un fallo de datos, es un footgun.
- **Arreglo propuesto**: `KUKIA_SOLO_LECTURA=1` que salte `schema()`, `genesis()`,
  el archivador y los hilos; o exigir `KUKIA_DB` cuando no se arranca como app.

## 3. Lo que se revisó y aguanta

- Firmas obligatorias para nodos sin clave: `EXIGIR_FIRMA_GLOBAL = True` (120);
  el camino "sin firma" no es alcanzable por defecto.
- Verificación trustless del resultado: el Core recalcula y compara; un nodo
  suelto no puede cobrar trabajo falso (probado: control de H1).
- Doble pago bloqueado por estado `ok` (409) y por `_pago_lock` + `BEGIN IMMEDIATE`.
- Réplica: un nodo no puede reclamar la réplica de su propia tarea (3900), y las
  réplicas ocupan cupo del nodo (2099).
- Transferencias: firma + nonce único, dos asientos compensados, el suministro
  no cambia; probado en `tools/pruebas_seguridad.py`.
- Castigo capado al saldo real de la cuenta (`_castigar_nodo`, 2170): no deja
  saldos negativos.
- Bolsa: un solo camino de escritura, dentro de la transacción del pago,
  persistente entre reinicios (probado con trigger y con reinicio).

## 4. Lo que NO se ha probado

- Nodo CPU real contra el Core nuevo (el corte de difusiones está probado en
  banco y con `device` simulado, no con un minero CPU en producción).
- 3 y 4 ranuras por tarjeta; vatios de CPU y fuente con la bolsa puesta.
- Arranque en frío de la máquina entera (guardia levanta todo).
- Colusión de réplicas en producción: la sonda es un Core aislado, no el vivo.

## 5. Cómo reproducir

```bash
cd C:/Users/kuki/Desktop/KUKIA
python tools/auditoria_replica.py        # H1: cobra una mentira (sale 0 si NO se puede)
python tools/auditoria_credito.py        # H2: minuto gratis + siguiente al doble
python tools/prueba_bolsa_estricta.py    # bolsa estricta, reinicio y device
python tools/prueba_bolsa_arranque.py    # fuga del plano viejo vs arreglada
python tools/prueba_precio_minuto.py     # proporcionalidad y fusible
```

Ninguna toca el ledger real: todas copian el Core a una carpeta temporal y usan
`kukia.db` nuevo.

## 6. Prioridad recomendada

1. **Operativa**: reiniciar el Core con copia verificada. Cierra la fuga §7.6
   real y pone en marcha bolsa estricta, persistencia y `device` por latido.
2. **H1**, antes de invitar a más gente: es el único fallo que permite cobrar
   sin trabajar.
3. **H2** (emisión a saltos) y **H5/H6** (difusiones a quien no puede).
4. **H3, H4, H7**: higiene, sin impacto en la emisión.

---

## 7. H1 — ARREGLADO (23/10/2026, 19:25)

Dos reglas nuevas en el Core, sin tocar pagos ni reparto:

1. **El mismo dueño no verifica.** `_mismo_dueno()` considera el mismo dueño la
   misma cuenta de cobro (`_cuenta_de`) o una IP de registro en común de las
   últimas 24 h (`registros_nodo`). `_reclamar_replica()` no asigna la réplica
   de una tarea cuyo primario es del mismo dueño; tampoco la de su propia tarea
   (eso ya estaba).
2. **Nodo sin historial no ahorra recálculo.** Mientras el primario o el
   verificador tengan menos de `KUKIA_AUDITORIA_TAREAS_CONFIANZA` (20) tareas
   verificadas, `_frecuencia_auditoria()` devuelve 1 y la tarea **se recalcula
   siempre**, haya réplica o no. A partir de 20 tareas cada uno vuelve el
   muestreo normal (1 de cada `KUKIA_AUDITORIA_1_DE` = 10). Se puede suavizar
   con `KUKIA_AUDITORIA_1_DE_JOVEN=3` sin tocar código.

Resultado de `python tools/auditoria_replica.py` (Core aislado, HTTP real):

| Caso | Antes | Ahora |
|---|---|---|
| Dos identidades, misma IP | réplica concedida y **0,918328 KIA pagados por una mentira** | **no hay réplica**: B no la recibe y la mentira sola se rechaza (0 KIA) |
| Dos identidades, IPs distintas, nodos nuevos | réplica concedida y pagada | réplica concedida, pero **el Core recalcula**: `ok=False`, 0 KIA |
| Dos identidades con historial (≥20 tareas), IPs distintas, tarea fuera de muestra | pagada | **pagada** (0,918328): es el muestreo 1 de cada 10 |

Ese tercer caso es el **residuo conocido**: sin depósito ni stake, el Core no
puede distinguir una pareja honrada de una coludida cuando las dos tienen
historial y máquinas distintas. Opciones, todas con coste, para decidir:

- bajar el muestreo a 1 de cada 3 para todos (`KUKIA_AUDITORIA_1_DE=3`): más
  CPU de verificación en el Core, ninguna promesa de eliminar el fraude;
- pagar la parte del verificador solo en tareas auditadas: acaba con la
  colusión, pero también con el incentivo de verificar;
- exigir un depósito/stake al verificador: es el arreglo de fondo, y es un
  cambio de protocolo, no un parche.

Mientras no se decida otra cosa, el arreglo cierra el ataque barato (una
máquina, dos claves) y hace que una pareja nueva no pueda cobrar nada sin que
el Core lo haya comprobado.
