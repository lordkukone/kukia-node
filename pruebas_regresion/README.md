# Pruebas de regresión de KUKIA

Batería que comprueba, contra el **código vivo del repositorio**, que los fallos
ya corregidos no vuelven. Nació porque las pruebas del Core se quedaban en
`informes_mainnet_20261029/` (fuera de git) y se perdían al limpiar el disco.

```bash
python pruebas_regresion/ejecutar_todo.py              # todo (7 pruebas, ~30 s en un PC normal)
python pruebas_regresion/ejecutar_todo.py --solo-suite # solo las de esta carpeta
python pruebas_regresion/ejecutar_todo.py -k stats     # filtra por nombre
```

Devuelve `0` solo si pasa todo. El registro completo de la última ejecución se
guarda en un directorio temporal (la ruta se imprime al final).

Los resultados de cada prueba van a [salidas/](salidas), **ignorada por git y con
marca de tiempo** (`resultado_f2_57_20261031_104512.json`): correr la batería no
deja el árbol sucio y nunca pisa la medición anterior.

Ninguna prueba toca el Core operativo, la base real, el ledger, los saldos, las
claves ni los puertos 8300/8301: todas trabajan en un árbol temporal con base
sintética y puerto efímero, o solo leen ficheros.

## Qué guarda cada una

| Prueba | Defecto que vigila | Cómo lo demuestra | Necesita |
|---|---|---|---|
| [prueba_identidad_stats.py](prueba_identidad_stats.py) | **F2-57** (un solo contador de archivadas daba 7 de desfase) y **F2-58/59** (cada contador en su consulta: la foto de `/stats` mezclaba dos instantes) | lote mixto 7 ok / 3 fallo + archivado, y 3 escritores concurrentes mientras se piden 150+ fotos de `/stats`: cero descuadres en las dos identidades | Core aislado (uvicorn), ~10 s |
| [prueba_regla_claves.py](prueba_regla_claves.py) | **F2-47** (la regla de vigencia de claves duplicada en el Core y en SQL podría divergir) | guarda estática (el Core no contiene copias), matriz de 27 decisiones en las dos bocas, y cambiar `_REGLA` en caliente mueve las tres a la vez | solo `coordinator/`, ~1 s |
| [prueba_detector_esteril.py](prueba_detector_esteril.py) | **F2-52 / M-02** (una GPU podía quedarse sin trabajar y el fallo era invisible) | ejecuta el `_hilo_salud` real (extraído con `ast`) y comprueba `ESTERIL` + volcado `PILAS` + sin falsos positivos | solo `worker/`, ~1 s |
| [ejecutar_todo.py](ejecutar_todo.py) | — | lanzador: corre las de arriba y las de `informes_mainnet_20261030/` | — |

El lanzador incluye además estas, que ya estaban versionadas:

| Prueba | Defecto que vigila |
|---|---|
| `informes_mainnet_20261030/f2-53_minero/pruebas_f2_53.py` | NUL en `minero.log` al truncar por fuera, rotación interna, cupo liberado una sola vez (F2-53) |
| `informes_mainnet_20261030/f2-53_minero/prueba_salud_f253.py` | aviso `HILO_MUERTO` cuando faltan los hilos de una GPU (F2-53) |
| `informes_mainnet_20261030/f2-54_minero_1_0/pruebas_f2_54.py` | telemetría real por GPU, `BoundedSemaphore` que no se infla, instancia única (F2-54) |
| `informes_mainnet_20261030/f2-57_desglose_archivadas/prueba_f2_57.py` | desglose ok/fallo de archivadas en 5 rondas seguidas y resiembra de contadores (F2-57) |

## Lo que NO está aquí, y por qué

Los arneses A/B de `informes_mainnet_20261029/` (F2-29, F2-35, F2-36, F2-38,
F2-42, F2-44, F2-46, F2-47, F2-48, F2-49) y los de `informes_mainnet_20261030/`
(F2-55, F2-59) comparan
**dos versiones del Core**: la de antes y la de después, cada una en su carpeta
`combined/`. Esas carpetas nunca se versionaron (son copias completas del
coordinador, una por fase) y sin ellas el arnés no arranca. Por eso se quedan en
el disco: son la medición que justificó cada parche, no una prueba repetible.

Lo que sí se ha traído es su **invariante**, reescrito contra el código de hoy:
es el caso de la identidad de `/stats` (antes F2-59 medía el antes/después; aquí
se exige que nunca haya descuadre) y el de la regla de claves (antes F2-47
comparaba contra una copia; aquí se ejecuta contra `coordinator/`).

## Añadir una prueba

Un script suelto, sin dependencias nuevas, que:

1. imprima una línea `[PASS]`/`[FAIL]` por comprobación;
2. termine con un JSON de una línea `{"total": N, "pass": N, "fail": N, ...}`;
3. devuelva `0` si pasa y `1` si no;
4. escriba su resultado en `pruebas_regresion/salidas/` con marca de tiempo
   (`<nombre>_AAAAMMDD_HHMMSS.json`), nunca junto al script ni en un fichero
   versionado, y no toque la base operativa.

Después se añade a la lista `SUITE` (o `VERSIONADAS`) de
[ejecutar_todo.py](ejecutar_todo.py) y a la tabla de arriba.
