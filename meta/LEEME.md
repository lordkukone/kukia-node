# meta/ — KUKIA META (prototipo aislado)

Cerebro científico en pequeño: pregunta → cálculo → comprobación por otra vía →
memoria con procedencia → evaluación con respuestas conocidas.

**No toca la moneda.** Base propia en `meta/estado/meta.db`; no lee ni escribe el
ledger, no paga y no cobra.

## Uso rápido

```bash
python -m meta.cli pasos                 # registro científico cargado
python -m meta.cli dominios              # disciplinas con pasos
python -m meta.cli banco                 # 13 problemas + 13 sabotajes
python -m meta.cli estado                # qué hay memorizado y en qué escalón
python -m meta.cli demo --paso cinematica.mrua --params '{"v0":20,"a":-4,"t":3}'
python meta/pruebas/test_meta.py         # 26 comprobaciones de las reglas
```

`banco` devuelve 0 si todo se validó y acertó, 1 si algo falló.

## Ficheros

| Fichero | Qué hace |
|---|---|
| `esquema.py` | tablas y escalones epistémicos |
| `memoria.py` | alta de piezas, estado deducido de las pruebas, contradicciones |
| `pasos.py` | registro científico: cada paso con 2 caminos (calcular / comprobar) |
| `ingesta.py` | problema → pregunta con criterio de éxito |
| `planificador.py` | valida paso y parámetros (sin código libre) |
| `ejecutor.py` | calcula 2 veces y anota el entorno |
| `verificador.py` | recalcula por otra vía y compara con tolerancia |
| `proceso.py` | el pipeline completo |
| `evaluacion.py` | banco + detección de errores inyectados |
| `bancos/base.json` | 13 problemas con respuesta conocida |
| `pruebas/test_meta.py` | las reglas, comprobadas |

## Añadir una disciplina

1. Escribe dos caminos para el mismo cálculo (uno para calcular, otro para
   comprobar) en `pasos.py`.
2. Regístralo con `registro(nombre, dominio, descripcion, unidad, parametros,
   motores, tolerancia, independencia)`.
3. Añade un problema a `bancos/base.json` con su valor esperado calculado
   **fuera** del sistema.
4. `python -m meta.cli banco` tiene que seguir en verde.

Si el segundo camino es "la misma fórmula otra vez", se etiqueta `aritmetica` y
se dice. No se disfraza de `fisica`.

## Reglas que el código no deja romper

- Sin ejecución no hay resultado; sin repetición no hay reproducibilidad; sin
  comprobación independiente no hay `validado`.
- El estado lo decide `memoria.estado_de()`, no quien escribe.
- Un fallo se guarda como `refutado`, no se borra.
- Dos verdades incompatibles se marcan como contradicción.
- No hay `eval`/`exec`: solo pasos registrados con parámetros validados.
