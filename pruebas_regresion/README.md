# Pruebas de regresión de KUKIA

Batería que comprueba, contra el **código vivo del repositorio**, que los fallos
ya corregidos no vuelven. Nació porque las pruebas del Core se quedaban en
`informes_mainnet_20261029/` (fuera de git) y se perdían al limpiar el disco.

```bash
python pruebas_regresion/ejecutar_todo.py              # todo lo rápido (7 pruebas, ~45 s)
python pruebas_regresion/ejecutar_todo.py --con-ab     # y la A/B de /stats (~1 min más)
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
| [prueba_panel_desglose.py](prueba_panel_desglose.py) | el panel ponía «N archivadas» y nada más, así que no se podía cuadrar con nada | Core aislado con 290 ok / 210 fallo archivadas **y el panel real** (`kukia_dash`) apuntando a él: comprueba que `/api/resumen` trae el desglose, que los valores son los sembrados, que la identidad cuadra y que el código que rellena `#kTasksArch` usa los tres campos | Core + panel, ~15 s |
| [ab_stats/prueba_ab_stats.py](ab_stats/prueba_ab_stats.py) | **F2-59** con su **A/B**: antes (cada contador en su consulta) vs después (una sola foto) | mismo esquema y misma carga en las dos variantes; solo cambia el fichero del Core. Exige 0 descuadres después y **al menos uno antes**, o no demuestra nada | 2 Cores, ~1 min (`--con-ab`) |
| [ejecutar_todo.py](ejecutar_todo.py) | — | lanzador: corre las de arriba y las de `informes_mainnet_20261030/` | — |

El lanzador incluye además estas, que ya estaban versionadas:

| Prueba | Defecto que vigila |
|---|---|
| `informes_mainnet_20261030/f2-53_minero/pruebas_f2_53.py` | NUL en `minero.log` al truncar por fuera, rotación interna, cupo liberado una sola vez (F2-53) |
| `informes_mainnet_20261030/f2-53_minero/prueba_salud_f253.py` | aviso `HILO_MUERTO` cuando faltan los hilos de una GPU (F2-53) |
| `informes_mainnet_20261030/f2-54_minero_1_0/pruebas_f2_54.py` | telemetría real por GPU, `BoundedSemaphore` que no se infla, instancia única (F2-54) |
| `informes_mainnet_20261030/f2-57_desglose_archivadas/prueba_f2_57.py` | desglose ok/fallo de archivadas en 5 rondas seguidas y resiembra de contadores (F2-57) |

## La A/B de `/stats` y su resultado

[ab_stats/](ab_stats) es la única prueba de la batería que necesita una copia de
un Core **anterior**, y por eso se versiona con su fixture:

| Fichero | Qué es |
|---|---|
| [ab_stats/prueba_ab_stats.py](ab_stats/prueba_ab_stats.py) | el arnés A/B: mide la variante de antes y la de ahora con la misma carga |
| [ab_stats/respaldo/core_pre_f259.py](ab_stats/respaldo/core_pre_f259.py) | el Core de F2-58 (antes del parche), como fixture. SHA-256 `6463fe89…`. No se llama `kukia_core_*` porque `.gitignore` ignora `KUKIA_*` y se quedaría fuera sin avisar |
| [ab_stats/resultado_referencia_20261030.json](ab_stats/resultado_referencia_20261030.json) | el resultado que validó F2-59 el 30/10/2026: **10 descuadres de 2.247** muestras antes, **0 de 2.791** después |

En la pasada del 31/10/2026 reprodujo lo mismo: **10 descuadres de 2.927**
muestras antes (siempre con desvío +1, que es la firma del commit que cae entre
dos lecturas) y **0 de 2.524** después. Las ejecuciones nuevas no pisan esos
ficheros: van a `salidas/resultado_ab_stats_<marca>.json`.

## Lo que NO está aquí, y por qué

Los arneses A/B de `informes_mainnet_20261029/` (F2-29, F2-35, F2-36, F2-38,
F2-42, F2-44, F2-46, F2-47, F2-48, F2-49), el de `informes_mainnet_20261030/`
F2-55 y los de latencia (F2-39, F2-41, F2-43, F2-45) comparan **dos versiones
del Core**, cada una en su carpeta `combined/` o contra una copia de 300 KB como
la de F2-59. Esas carpetas nunca se versionaron (son copias completas del
coordinador, una por fase) y sin ellas el arnés no arranca. Por eso se quedan en
el disco: son la medición que justificó cada parche.

Han entrado dos cosas de ahí, porque su fixture cabe en el repositorio:

- la **A/B de `/stats`** (F2-59), con su respaldo de 300 KB y su resultado
  ([ab_stats/](ab_stats)), que es la prueba de que el defecto existía;
- los **invariantes**, reescritos contra el código de hoy: la identidad de
  `/stats` ([prueba_identidad_stats.py](prueba_identidad_stats.py)) y la regla de
  claves de F2-47 ([prueba_regla_claves.py](prueba_regla_claves.py)), que antes
  se comprobaban contra una copia y ahora contra `coordinator/`.

Las A/B de latencia (F2-39, F2-41, F2-44, F2-45, F2-46) siguen fuera: necesitan
el árbol entero de cada fase, no un fichero.

## Añadir una prueba

Un script suelto, sin dependencias nuevas, que:

1. imprima una línea `[PASS]`/`[FAIL]` por comprobación;
2. termine con un JSON de una línea `{"total": N, "pass": N, "fail": N, ...}`;
3. devuelva `0` si pasa y `1` si no;
4. escriba su resultado en `pruebas_regresion/salidas/` con marca de tiempo
   (`<nombre>_AAAAMMDD_HHMMSS.json`), nunca junto al script ni en un fichero
   versionado, y no toque la base operativa.

Después se añade a la lista `SUITE`, `VERSIONADAS` o `AB` de
[ejecutar_todo.py](ejecutar_todo.py) y a la tabla de arriba. Si la prueba necesita
un Core anterior, va a la lista `AB` (se ejecuta con `--con-ab`) y su fixture se
versiona junto a ella, nunca en `salidas/`.
