# Migración del dinero a unidades enteras

Fecha: 25-10-2026 · Estado: **preparada y probada sobre una copia. El ledger real no se ha tocado.**

## 1. Por qué

Todo el dinero de KUKIA se guarda hoy en columnas `REAL` (coma flotante binaria):
`ledger.kuki`, `saldos_cuenta.total`, `transferencias.monto`, `tareas.prepago`,
`tareas.precio_cliente`, `tareas.pagado`, `clientes.gastado` y `meta` (suministro y bolsas).

Un `REAL` no puede representar 0,1 exactamente. Al sumar 779.000 asientos el error se
acumula y el dinero deja de cuadrar consigo mismo. Medido en la copia del 25-10-2026
a las 21:19, no supuesto:

| Qué | Coma flotante (real, hoy) | Enteros (copia migrada) |
|---|---|---|
| Suministro emitido | `15088160.871962598` | `15.088.160.8719721777` |
| Suma de asientos | `15088160.87197193` | `15.088.160.8719721777` |
| Suma de saldos | `15088160.87197194` | `15.088.160.8719721777` |
| Saldos − suministro | **0,0000093430 KIA** de desvío (93.430 unidades) | **0 unidades** |

(El informe completo, con las 45 cuentas una por una, está en
`migracion_entera/CONCILIACION.md`; la migración tardó 25 s en la copia de 398 MB.)

Equivale a unos 93.430 millonésimos de KIA que el sistema no sabe dónde están: no es
mucho, pero es la prueba de que la aritmética no es exacta. Con enteros ese desvío es
cero por construcción.

La segunda razón es la **partida doble**. Cuando el Core cobra un prepago y paga al
minero, escribe dos asientos que deben compensarse. En coma flotante, `-x` y `+x` no
siempre desaparecen al sumarlos con el resto de la cuenta; en enteros, sí: la conversión
es simétrica y dos asientos opuestos suman exactamente cero.

## 2. La unidad

**1 KIA = 10.000.000.000 unidades** (1 unidad = 1e-10 KIA). Nombre de la columna: sufijo `_u`.

Por qué ese número y no otro:

- **1e-12 KIA no cabe.** 100.000.000 KIA (el techo de la tokenomics) × 1e12 = 1e20, y
  el entero con signo de 64 bits de SQLite llega a 9,22e18. La mitad del suministro ya
  desbordaría.
- **1e-6 KIA perdería pagos reales.** Con el ancla de 1 KIA = 1 minuto de GPU hay tareas
  de 4e-8 KIA (40 unidades con esta escala). A 6 decimales se convertirían en cero.
- **1e-10 KIA** deja 9 veces de margen sobre el techo (1e18 usado de 9,22e18) y conserva
  hasta los pagos más pequeños que existen hoy. Comprobado en la copia: el asiento más
  chico del ledger es de **1e-09 KIA y se convierte en 10 unidades**. No hay ni un solo
  asiento con importe que se quede en cero (`kuki_u=0` con `kuki<>0`: 0 filas) ni uno sin
  importe que invente unidades (`kuki_u<>0` con `kuki=0`: 0 filas). Es además el mismo
  umbral que el Core ya llama "polvo" (`1e-9`).

Cambiar de escala es posible (`--escala`), pero cambiaría la unidad monetaria: eso es
una decisión de tokenomics y no se toma desde un script.

### Regla de conversión

Se convierte el número **tal como se escribe** (`repr`, el texto más corto que
reconstruye el mismo `float`), con `Decimal` y redondeo **mitad hacia arriba**, que es
simétrico: convertir `-x` da exactamente `-convertir(x)`. Esa simetría es lo que mantiene
la partida doble cuadrada al bit después de migrar.

Residuo: cada fila puede moverse como mucho **media unidad**. En el ledger entero eso
son 4.666,89 unidades = **0,0000004667 KIA** en total, repartidas entre 779.000 asientos.

## 3. Qué hace el script

`tools/migrar_entero.py`:

1. Abre el ledger real en **solo lectura** (`mode=ro` + `PRAGMA query_only=1`).
2. Copia con la **API de backup de SQLite**, no con `cp`: con WAL, copiar el fichero se
   lleva escrituras a medias (está medido en `tools/estudio_wal.py`).
3. Añade columnas `*_u` **al lado** de las de coma flotante. No borra ni reescribe nada
   existente, así que la migración se puede deshacer sin restaurar un backup.
4. Convierte cada fila.
5. **Recalcula los saldos desde el ledger**: `saldos_cuenta.total_u` es la suma de los
   asientos enteros de esa cuenta, y `meta_u.supply_emitido` es la suma de todos los
   asientos. El saldo materializado en float se convierte en referencia, no en verdad:
   la verdad es el ledger, como ya dice `recalcular_resumen` del Core.
6. **Verifica la cadena de hashes** entera con los importes originales. Si la migración
   hubiera tocado un solo importe o su texto, la cadena se rompe aquí.
7. Escribe `saldos_doble` (las dos representaciones de cada saldo) y el informe.

Salidas en `migracion_entera/`: `kukia_entera.db` (la copia migrada),
`CONCILIACION.md` (informe cuenta por cuenta), `conciliacion.json` y `sha256.txt`.

## 4. Resultado de la prueba sobre la copia real

| Comprobación | Resultado |
|---|---|
| Asientos convertidos | 779.407 |
| Saltos de la cadena de hashes | 0 (175 asientos son anteriores a la cadena y no son verificables: se cuentan, no se dan por buenos) |
| Cuentas cuyo saldo entero no es la suma de sus asientos | **0** de 45 |
| Grupos de partida doble que no suman cero exacto | **0** de 41 |
| Desvío saldos − suministro | 0 unidades (antes: 93.430) |
| Corrección aplicada al saldo de la cuenta grande | +2.372 unidades (2,4e-7 KIA) sobre 687.401 asientos |
| Desvío del suministro contra el float acumulado | 95.797 unidades (9,6e-6 KIA): deriva previa, no dinero nuevo |
| Asientos con importe que se quedan en cero | 0 |
| Doble escritura: cuentas donde las dos versiones discrepan | 0 de 45 |

Pruebas automáticas: `python tools/prueba_migracion_entera.py` — **49 comprobaciones, todas
en verde**, incluidas las negativas (un saldo tocado a mano, un asiento de dinero inventado,
un importe cambiado: las tres se detectan; si no se detectaran, el informe no valdría nada).

## 5. Doble escritura y paso al Core

La conversión no basta: mientras el Core siga escribiendo en float, el desvío vuelve.
El plan, por fases, cada una reversible:

**Fase 1 — hecha.** Copia migrada + informe + pruebas. El Core no se toca.

**Fase 2 — escribir en las dos.** En `_asiento` (el único camino de escritura del dinero)
se graba `kuki` y `kuki_u` en la misma fila, con `kuki` como espejo. Un comparador diario
(`tools/migrar_entero.py --ver` + consulta de contraste) avisa si alguna fila nueva tiene
las dos columnas descuadradas. Si algo va mal, se ignora `kuki_u`: nada depende de ella.

Criterio de aceptación: 7 días sin una sola fila descuadrada y el desvío saldos−suministro
congelado.

**Fase 3 — leer en enteros.** `/saldo`, `/saldos`, la calculadora y la billetera leen
`total_u` y lo publican; el float se queda como columna histórica. Las transferencias
validan el importe en unidades (`int`), no con `round(x, 4)`.

Criterio de aceptación: los mismos saldos que en la fase 2, con 0 discrepancias, y las
pruebas de dinero (`pruebas_seguridad.py`, `pruebas_caos.py`, `prueba_gasto_billetera.py`,
`demo_cliente_punta_a_punta.py`) en verde.

**Fase 4 — apagar el float.** Se deja de escribir `kuki` (sin borrar la columna: el
historial no se toca) y el esquema se congela con un test que falla si alguien vuelve a
meter un `REAL` de dinero.

Nada de esto se ha ejecutado. La fase 2 toca el Core de producción y necesita tu permiso
y una ventana con el Core parado.

## 6. Cómo se haría la migración real (cuando toque)

1. Core parado y `wal_checkpoint(TRUNCATE)`; sin pararlo, la copia es consistente pero el
   momento del corte no es reproducible.
2. Copia verificada (tamaño + sha256) fuera del árbol del proyecto.
3. `python tools/migrar_entero.py --ver` para el recuento y después la migración sobre la
   copia, con el informe delante.
4. Revisar `CONCILIACION.md`: 0 cuentas con desvío, 0 grupos de partida doble raros,
   cadena OK. Si algo no cuadra, **no se sigue**.
5. Promover la copia solo con la fase 2 ya desplegada, y con el Core apuntando a ella
   (`KUKIA_DB`).

## 7. Reversión

- Fase 1: no hay nada que revertir. El ledger real no tiene ni una columna nueva
  (comprobado: `PRAGMA table_info(ledger)` del ledger real sigue teniendo 8 columnas y
  ninguna `_u`).
- Fases 2-3: el Core vuelve a leer el float; las columnas `_u` se quedan sin uso.
- Fase 4: restaurar el backup verificado del paso 2.

## 8. Lo que este trabajo NO es

- No es una migración del ledger real: se ha migrado una copia, y el informe lo dice.
- No cambia la economía: mismo techo, misma emisión, mismos precios, mismos saldos (con
  la corrección de 2,4e-7 KIA que la coma flotante había ido perdiendo).
- No es una auditoría independiente. Es una prueba propia y reproducible; una revisión
  externa de la aritmética monetaria sigue pendiente.
- La cadena de hashes tiene 175 asientos iniciales sin hash (de antes de que existiera el
  sellado). No se pueden verificar y no se han tocado.
