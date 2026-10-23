# KUKIA META — auditoría del repositorio (23-10-2026)

Antes de diseñar nada, esto es lo que hay de verdad en el repositorio, lo que se
puede reutilizar tal cual y lo que falta. Comprobado leyendo el código y
consultando la red viva, no de memoria.

## 1. Qué hay (medido hoy)

| Pieza | Fichero | Tamaño / estado |
|---|---|---|
| Core (coordinador, pagos, API) | `coordinator/kukia_core.py` | ~3.750 líneas, 36 rutas, **vivo** en `127.0.0.1:8300` |
| Minero multi-GPU | `worker/kukia_worker.py` | 1.634 líneas, 4 tipos de tarea |
| Validador independiente | `validator/kukia_validator.py` | recalcula con numpy y firma con Ed25519 |
| Ledger y sellos | `ledger/kukia_wallet.py`, `ledger/sellos.py` | 301 MB de SQLite, cadena de hashes + sellos |
| Panel de operaciones | `dashboard/kukia_dash.py` | vivo en `8301` |
| Guardia de procesos | `tools/guardia.py` | relanza Core/panel/validador/túnel si se caen |
| Herramientas | `tools/*.py` | salud, cálculo de moneda, benchmark GPU, copias verificadas, pruebas de seguridad |
| Red viva | — | 20 nodos, 328.243 tareas verificadas, 15,09 M KIA emitidos |

El código total del proyecto (sin backups ni docs) ronda las **21.700 líneas**.

## 2. Qué se puede reutilizar para META (y por qué)

1. **El patrón del validador** — `validator/kukia_validator.py` recalcula cada
   tarea *sin mirar* el resultado del minero y firma lo que ve. Es exactamente
   la idea que necesita la ciencia: **quien comprueba no es quien calcula**.
   META lo copia como norma dura (dos motores por paso, ver
   `meta/pasos.py`), no como recomendación.
2. **La cadena de hashes y los sellos** (`ledger/sellos.py`) — sirven para
   procedencia: qué se calculó, con qué código y cuándo, sin que se pueda
   reescribir el pasado. META guarda el hash del registro de pasos y el entorno
   en cada ejecución (`meta/ejecutor.py`), que es la versión ligera de lo mismo.
3. **La guardia** — si META llega a tener servicios (ingesta, planificador,
   verificador), ya hay un patrón probado para mantenerlos vivos sin duplicar
   procesos.
4. **El minero** — para cálculo numérico pesado (matrices, difusión) ya hay
   kernels verificados y pagados. Es el puente natural cuando META necesite
   potencia; **no hoy**, y no sin autorización explícita.
5. **La disciplina de auditoría del repo** — informes con cifras, pruebas que
   fallan si algo no cuadra, y la costumbre de distinguir lo comprobado de lo
   supuesto. META hereda esa costumbre.

## 3. Qué falta (huecos reales, no cosméticos)

- **El minero solo sabe 4 kernels** (`checksum`, `matriz`, `inferencia`,
  `difusion_calor`). No puede ejecutar código científico arbitrario ni
  reproducir un entorno (versiones, datos, semillas). Distribuir ciencia de
  verdad exige un tipo de tarea `kernel` con imagen fijada y resultados
  verificables; hoy no existe.
- **No hay base de conocimiento científico**: el ledger guarda pagos, no
  afirmaciones con procedencia, método y limitaciones.
- **No hay banco de evaluación**: nada mide si un cambio mejora o empeora la
  calidad científica. Sin eso, "aprende" es una palabra hueca.
- **No hay registro de disciplinas**: las áreas están en la cabeza, no en datos.
- **No hay detección de contradicciones**: dos resultados incompatibles pueden
  convivir sin que nadie se entere.
- **No hay capa de literatura**: se puede citar una fuente, pero no está
  modelada (referencia, hash del documento, qué afirma).
- **La VRAM no se suma**: el README ya lo dice; cualquier reparto de un problema
  grande entre máquinas exige descomposición explícita, no memoria compartida.

## 4. Riesgos que condicionan el diseño

| Riesgo | Consecuencia | Cómo se trata |
|---|---|---|
| Tocar la economía | Saldos, pagos y fecha de emisión son sagrados | META vive en `meta/`, con su propia base; no importa el Core ni escribe en el ledger |
| Ejecutar código ajeno | Un `eval` o un script de un nodo puede vaciar el PC | El prototipo **no evalúa texto libre**: solo pasos registrados con parámetros validados |
| El modelo lingüístico inventa | Un LLM puede dar un número plausible y falso | El LLM **nunca** produce el valor: como mucho propone qué paso usar; el número sale de código y se comprueba por otra vía |
| Un solo Core | Si cae, la red se para | Fuera del alcance de META; se aprovecha la guardia existente |
| Confundir guardar con aprender | Memoria de conversaciones ≠ modelo mejor | La evaluación (`meta/evaluacion.py`) mide aciertos antes y después; sin mejora medida no se dice que hay mejora |

## 5. Veredicto

El repositorio tiene lo difícil ya hecho para una red de cómputo honesta:
verificación independiente, integridad del registro, operación estable. Lo que
no tiene es **capa científica**: problemas, preguntas, hipótesis, métodos,
resultados con procedencia, verificación por otra vía, contradicciones y
evaluación.

Eso es exactamente lo que se construye en `meta/`, aislado y sin tocar nada de
lo que ya funciona.
