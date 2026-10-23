# KUKIA META — arquitectura

Estado: **prototipo funcionando** (23-10-2026). Este documento dice cómo está
pensado el cerebro científico, qué está construido y qué no. Lo que no está
hecho se dice aquí, no se deja a la imaginación.

## 1. La idea en una frase

Un cerebro científico no es un modelo que "sabe ciencia": es un sistema que
**convierte un problema en preguntas comprobables, calcula, verifica por otra
vía, guarda el rastro y mide si ha mejorado**. Lo demás (literatura, modelos,
distribución) se cuelga de ese esqueleto.

## 2. Las cinco capas

```
   ┌──────────────────────────────────────────────────────────────┐
   │ 5. EVALUACIÓN   banco con respuestas conocidas + sabotaje     │
   │                 mide aciertos, errores cazados y estados      │
   ├──────────────────────────────────────────────────────────────┤
   │ 4. MEMORIA      conocimiento con procedencia, limitaciones,    │
   │                 escalón epistémico y contradicciones          │
   ├──────────────────────────────────────────────────────────────┤
   │ 3. VERIFICACIÓN motor independiente (otra ley, otro algoritmo, │
   │                 otra aritmética) + comparación con tolerancia  │
   ├──────────────────────────────────────────────────────────────┤
   │ 2. EJECUCIÓN    pasos registrados, doble corrida, entorno      │
   │                 anotado (python/numpy/scipy + huella del código)│
   ├──────────────────────────────────────────────────────────────┤
   │ 1. INGESTA      problema -> preguntas con criterio de éxito y  │
   │                 método; plan validado (sin código libre)       │
   └──────────────────────────────────────────────────────────────┘
```

Cada capa existe en `meta/` y funciona hoy salvo donde se indique.

### Capa 1 — Ingesta y plan (`meta/ingesta.py`, `meta/planificador.py`)

Una pregunta solo entra si trae **criterio de éxito** y **método**. El
planificador acepta únicamente pasos registrados y parámetros que encajen con su
ficha; no hay `eval`, ni `exec`, ni scripts externos. Si un modelo de lenguaje
propone un paso que no existe, se rechaza y queda escrito quién lo propuso.

Esto es deliberado: la superficie de ataque de "ejecutar ciencia" es enorme si se
permite código libre. Se empieza por un registro de pasos auditables.

### Capa 2 — Ejecución (`meta/ejecutor.py`, `meta/pasos.py`)

13 pasos cargados, de 9 disciplinas: mecánica, radiactividad, órbita,
termodinámica, óptica, crecimiento logístico, genética de poblaciones, pH,
intervalos de confianza, interés compuesto, regresión, integral simbólica y
dilatación térmica.

Cada paso corre **dos veces** para comprobar reproducibilidad y anota el entorno
(versión de Python, numpy, scipy, sympy y hash del registro de pasos). Un
resultado sin entorno ni repetición no es un resultado.

### Capa 3 — Verificación (`meta/verificador.py`)

Cada paso trae al menos **dos motores** y el que comprueba nunca es el que
calculó. La fuerza de esa independencia se declara y no se disimula:

| Tipo | Qué significa | Pasos hoy |
|---|---|---|
| `fisica` | otra ley o integración numérica del mismo fenómeno | 4 |
| `simbolico` | resolución exacta con sympy frente al cálculo numérico | 2 |
| `algoritmo` | misma física, distinto algoritmo de resolución | 4 |
| `aritmetica` | misma fórmula, aritmética exacta / otra ruta de constantes | 2 |
| `procedimiento` | se comprueba el método (cobertura del intervalo) | 1 |

Las tres primeras son comprobaciones de verdad; las dos últimas son más débiles y
así se etiquetan. Un `validado` por aritmética exacta vale menos que uno por
integración de la órbita, y el registro lo dice.

### Capa 4 — Memoria (`meta/memoria.py`, `meta/esquema.py`)

Tablas: problemas, preguntas, hipótesis, subtareas, ejecuciones, verificaciones,
conocimiento, contradicciones y evaluaciones. Base propia:
`meta/estado/meta.db`.

El escalón **no se declara, se deduce** de las pruebas guardadas:

| Escalón | Prueba exigida |
|---|---|
| hipótesis | nada |
| calculado | 1 ejecución correcta |
| reproducido | 2 ejecuciones iguales del mismo motor |
| validado | + 1 verificación concordante de otro motor |
| confirmado | + 2 verificaciones de motores distintos |
| refutado | cualquier verificación que discrepa (se conserva) |
| indeterminado | no se pudo calcular o comprobar |

`anotar_conocimiento()` **ignora** el estado que le pidan y guarda el deducido:
no hay forma de escribir "validado" sin pruebas en la base.

Las contradicciones se detectan solas: dos números validados de la misma pregunta
que no caben juntos se guardan como contradicción y la pregunta pasa a
`en_disputa`. No se borra ninguno de los dos.

### Capa 5 — Evaluación (`meta/evaluacion.py`, `meta/bancos/base.json`)

13 problemas con respuesta cerrada calculada **fuera** del sistema (fórmula de
libro o sympy aparte). Se mide:

- aciertos (valor validado = valor esperado),
- estados (cuántos validados, refutados, indeterminados),
- **detección de errores**: se sabotea el cálculo un 5 % y se mira si el
  verificador lo caza.

El banco es el juez. Si un cambio baja los aciertos, el cambio es malo, aunque
suene mejor.

## 3. Cómo crece (sin rehacer el centro)

1. **Nuevas disciplinas** = nuevas entradas en `meta/pasos.py`. No hay lista
   cerrada: cualquier rama con un método verificable entra registrando su paso y
   su motor de comprobación.
2. **Más problemas** = más JSON en `meta/bancos/`. El banco es el contrato.
3. **Literatura y procedencia** = tabla de fuentes ya prevista; falta el
   conector (descargar, hashear, citar).
4. **Distribución** = cuando haga falta potencia, la subtarea se manda a la red
   con un tipo nuevo (`kernel` con imagen fijada). Hoy **no existe** y no se
   simula que exista: el prototipo calcula en local.
5. **Modelos** = opcionales y siempre en el papel de proponer texto o plan, nunca
   de dar el número. Un LLM que "da el resultado" rompe la cadena de confianza.

## 4. Seguridad y aislamiento

- `meta/` no importa el Core, no escribe en el ledger, no paga ni cobra.
- Nada de `eval`/`exec` sobre texto; los parámetros se validan contra la ficha
  del paso.
- Cada ejecución guarda entorno y hash del registro: se puede repetir y saber si
  el código cambió.
- Los datos científicos van a su propia base, con copia aparte de la del ledger.

## 5. Lo que NO está hecho (y no se va a decir que sí)

- Reparto de un problema entre máquinas: no implementado. La VRAM no se suma.
- Ejecución de código científico arbitrario con contenedor: no implementado (y
  es requisito antes de abrir tareas a terceros).
- Literatura: modelada en la tabla de fuentes, sin conector real.
- Modelos de lenguaje: la ingesta admite un proveedor de texto (`.reescribir()`),
  pero **no hay ninguno implementado** ni conectado; nada llama a un modelo.
- Entrenamiento de modelos: fuera de alcance; lo que hay es evaluación de
  resultados, no aprendizaje de pesos.
- Dominios como medicina o biología experimental: entra cualquier disciplina
  **con método verificable**, pero un resultado computacional no es un ensayo
  clínico y el escalón máximo lo refleja.
