# KUKIA META — resultados medidos del prototipo (23-10-2026)

Todo lo de aquí sale de ejecuciones reales, no de estimaciones. Para repetirlo:

```bash
python -m meta.cli banco            # banco completo + prueba de errores
python meta/pruebas/test_meta.py    # 26 comprobaciones de las reglas
python -m meta.cli estado           # qué hay guardado
```

## 1. Banco de problemas con respuesta conocida

13 problemas, 9 disciplinas. La respuesta esperada se calculó **fuera** del
sistema (fórmula de libro o sympy aparte) y luego se comparó con lo que produjo
el prototipo.

| problema | disciplina | estado | obtenido | esperado | dif. relativa |
|---|---|---|---|---|---|
| `mecanica_mrua` | fisica | validado | 42.0 | 42.0 | 0 |
| `decaimiento_exponencial` | fisica | validado | 367.87944117144235 | 367.87944117144235 | 0 |
| `orbita_baja` | astronomia | validado | 5828.598860022618 | 5828.598860022618 | 0 |
| `logistica_poblacion` | biologia | validado | 355.4609871366469 | 355.4609871366469 | 0 |
| `gas_ideal` | fisica | validado | 99773.55141783887 | 99773.55141783887 | 0 |
| `snell_agua_vidrio` | fisica | validado | 34.31260793693417 | 34.31260793693417 | 0 |
| `ph_acido` | quimica | validado | 2.8752770615501357 | 2.8752770615501357 | 0 |
| `hardy_weinberg` | biologia | validado | 0.42 | 0.42 | 0 |
| `ic_media` | estadistica | validado | 0.26383089735676074 | 0.26383089735676074 | 0 |
| `interes_compuesto` | economia | validado | 1819.3967340322902 | 1819.3967340322902 | 0 |
| `regresion_lineal` | inteligencia artificial | validado | 1.9857142857142862 | 1.985714285714285 | 5,6e-16 |
| `integral` | matematicas | validado | 0.9061160404823432 | 0.9061160404823432 | 0 |
| `dilatacion` | materiales | validado | 0.00345 | 0.00345 | 0 |

**13/13 validados y correctos. 0 refutados. 0 indeterminados. 4,26 s.**

## 2. Prueba de que detecta errores (lo importante)

Un sistema que acierta no sirve si no es capaz de **fallar**. Se repitió cada
problema desviando el cálculo un **5 %** a propósito:

| Medida | Resultado |
|---|---|
| Errores del 5 % inyectados | 13 |
| Cazados por la verificación independiente | **13/13** |
| Diferencia observada en los cazados | 4,76e-2 (el 5 % esperado) |

La peor diferencia entre dos caminos que **sí** coinciden fue **4,31e-11**
(órbita integrada frente a la ley de Kepler). El verificador no es un sello
decorativo: separa 5 % de 1e-11 sin dudar.

## 3. Estado epistémico guardado

| Escalón | Cuántos |
|---|---|
| validado | 13 |
| refutado | 13 (los sabotajes) |
| resto | 0 |

Ninguno llegó a `confirmado` porque el prototipo exige **dos** verificaciones
independientes de motores distintos para ese escalón y cada paso tiene una sola
comprobación registrada. Es el comportamiento correcto: el sistema se queda
corto antes que inflarse.

## 4. Fuerza real de las comprobaciones (sin adornos)

| Tipo de independencia | Pasos | Cuáles |
|---|---|---|
| `fisica` (otra ley o integración) | 4 | cinemática, decaimiento, órbita, logística |
| `simbolico` (sympy exacto) | 2 | Hardy-Weinberg, integral |
| `algoritmo` (otra resolución) | 4 | Snell (fase k), pH (brentq), interés (bucle), regresión (SVD) |
| `aritmetica` (exacta) | 2 | gas ideal, dilatación |
| `procedimiento` (cobertura del IC) | 1 | intervalo de confianza |

Esto es la mitad del valor del informe: **4 de 13 validaciones son físicas de
verdad**; las de aritmética exacta comprueban la cuenta, no la ley. Llamar a
todas igual sería mentir con números bonitos.

## 5. Las reglas, probadas (26 comprobaciones, 0 fallos)

`python meta/pruebas/test_meta.py` → **26 OK, 0 FALLO**, entre ellas:

- el escalón no se puede saltar (sin ejecuciones no hay `validado`);
- un valor falso deja el resultado en `refutado` y no en `aprobado`;
- el estado se deduce de las pruebas: `anotar_conocimiento()` ignora el estado
  que le pidan;
- el verificador no aprueba con el mismo motor que calculó;
- pasos inventados, parámetros que faltan o que sobran → rechazados;
- una propuesta inválida de un modelo se rechaza y se explica;
- dos verdades incompatibles → contradicción guardada y pregunta `en_disputa`,
  sin borrar ninguna de las dos;
- no hay `eval`/`exec` sobre texto en todo el paquete;
- el banco completo acierta y caza los 13 sabotajes.

## 6. Lo que estos números NO demuestran

- **13 problemas es poco** y los eligió quien escribe el código. Es una prueba de
  fontanería y de honestidad, no un examen de ciencia.
- Los pasos cubren cálculos cerrados. **No hay** descubrimiento, ni literatura,
  ni simulación de sistemas reales, ni datos experimentales.
- La mitad de las verificaciones son algebraicas: sirven para detectar errores de
  implementación, no para validar una ley física nueva.
- No hay distribución entre máquinas: todo se calcula en este PC.
- El banco no lo ha revisado nadie independiente. Ese es el siguiente paso serio.

## 7. Qué medir la próxima vez (para que "mejora" signifique algo)

1. Ampliar el banco a 100+ problemas con valores de referencia publicados (NIST,
   libros, problemas resueltos con fuente) y congelar la versión.
2. Añadir pasos con verificación `fisica` (simulación vs teoría) en cada dominio.
3. Medir **tiempo** y **coste** además de aciertos: un resultado correcto que
   tarda una hora no escala.
4. Antes de repartir tareas a la red: contenedor con imagen fijada, entrada
   hasheada y salida verificable. Sin eso, no se abre.
