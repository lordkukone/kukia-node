# Cuadre del precio hasta 2150, con todo tipo de trabajo

Fecha: 25-10-2026 · Cuenta hecha con las reglas que corrían en el Core cuando se
escribió (1 KIA = 1 minuto de GPU, suelo de 0,0001 KIA por tarea, tope del
minuto). Reproducible: `python tools/cuadre_2150.py`

> **Nota del 25-10-2026, 16:26.** Desde esa hora el Core arranca con el **ancla
> GPU-minuto por presupuesto** (entonces 0,000001456213 KIA/GFLOP, 1,3106 KIA por
> minuto de GPU); la definición de 1 KIA = 1 minuto sigue disponible con
> `KUKIA_PRECIO_DEFINIDO=1`. Lo que este estudio demuestra no depende de eso:
> con cualquiera de las dos reglas el calendario no se acaba antes de 2150 salvo
> que la red vaya a tope de capacidad durante 123 años, y a tope de capacidad el
> precio que cuadra es justo ≈ 1 KIA por minuto de GPU (sección 4).
>
> **Nota del 25-10-2026, 17:45.** Kuki fijó el fin de emisión en el **13-04-2150**
> (antes, 01-01-2150): 103 días más de calendario, o sea 148.320 minutos más para
> los mismos 100M. El presupuesto del minuto baja de 1,310572 a **1,307629
> KIA/min** y el precio del ancla de 0,000001456213 a **0,000001452921 KIA/GFLOP**
> (−0,23 %). Las tablas de este estudio se calcularon con la fecha vieja; ninguna
> conclusión cambia (la diferencia es de un cuarto de punto), y reejecutando
> `python tools/cuadre_2150.py` salen ya con la fecha nueva.

---

## 1. Lo que hay que repartir (esto no es opinión, es aritmética)

| Dato | Valor |
|---|---|
| Emitido hasta hoy | 15.088.152,28 KIA |
| Falta por emitir hasta 2150 | **84.911.847,72 KIA** |
| Minutos de reloj que quedan | 64.789.920 (123,2 años) |
| Tope que paga la red por minuto | **1,310572 KIA/min** = 1,31 minutos de GPU por minuto |
| Capacidad pagable | 1.887 minutos de GPU al día, entre todos |

El calendario es fijo. Todo lo demás (cuánto cobra un minuto de GPU) sale de
dividir: **84,9 M de KIA entre los minutos de GPU que la red entregue de aquí
a 2150**. No hay otra variable. Lo que se puede elegir es cuál de las dos cosas
se respeta: la fecha o la unidad.

---

## 2. Lo que cuesta cada trabajo con la unidad de hoy (1 KIA = 1 minuto)

Referencia de esta máquina: 15.000 GFLOP/s de GPU (RTX 5070 medida).

| Trabajo | GFLOP | KIA |
|---|---|---|
| Checksum / tarea vacía | 0 | 0,00010000 (suelo) |
| Embeddings de 1 M tokens (modelo 0,3 B) | 600 | 0,00066667 |
| Inferencia 7 B, 500 tokens | 7.000 | 0,00777778 |
| Inferencia 70 B, 1.000 tokens | 140.000 | 0,15555556 |
| Matriz 800 × 800 | 1,024 | 0,00010000 (suelo) |
| Imagen SDXL 1024, 30 pasos | 90.000 | 0,10000000 |
| Vídeo 5 s 720p (14 B) | 4.500.000 | 5,00000000 |

Supuesto declarado: el clip de 5 s se ha contado a 5 minutos de GPU
(4,5 M GFLOP a 15.000 GFLOP/s). Lo demás son cuentas de manual (2·N·params·tokens)
y el suelo del Core.

**Lo importante de esta tabla:** el vídeo no es "otra moneda", son más minutos
de GPU. Un clip pesa 50.000 veces más que una matriz de 800 × 800. Por eso
"las IAs pueden hacer de todo" no cambia la cuenta: cambia cuántos minutos de
GPU consume cada cosa y, por tanto, cuánto volumen hace falta para llegar al
tope.

---

## 3. Qué pasa con la unidad de hoy (1 KIA = 1 minuto) según la demanda

| Perfil de demanda | KIA/día | Se agota el suministro |
|---|---|---|
| Hoy, medido (33,55 KIA/día) | 33,55 | **año ~8.955** |
| Un clip de 5 min al día | 5,00 | año ~48.518 |
| 50 clips de 5 min al día (estudio pequeño) | 250,00 | año ~2.957 |
| Una GPU al 30 % con tráfico real | 432,00 | año ~2.565 |
| Crecimiento 0,1 → 30 GPU-min/min (2026-2045) | 144,00 | **2150-01** (a tope) |
| Capacidad del calendario (1,31 GPU-min/min) | 1.887,25 | **2150-01** (a tope) |

Traducido: **con 1 KIA = 1 minuto, la emisión solo se acaba el 13-04-2150 si la
red va a tope de capacidad (1,31 minutos de GPU por minuto, todos los minutos,
123 años).** Con la demanda real de hoy (0,023 minutos de GPU por minuto,
1,8 % del tope) la última KIA llegaría hacia el año 8.955: 6.800 años tarde.

A tope de calendario son **1.887 minutos de GPU al día**, o sea el equivalente a
**377 clips de 5 s 720p al día** (5 min de GPU cada uno), o 4.044 inferencias
7 B de 500 tokens al día, todos los días, durante 123 años. Esa es la vara con
la que se mide si el calendario se cumple: no el precio, el trabajo entregado.

---

## 4. El precio que sí cuadra en 2150, perfil por perfil

Es el que reparte los 84,9 M entre todo el trabajo que ese perfil va a
entregar de aquí a 2150.

| Perfil | GPU-min en el horizonte | 1 GPU-min = … KIA | 1 KIA = … minutos | Uso del tope |
|---|---|---|---|---|
| Hoy, medido | 1.510.537 | **56,21** | 0,0178 | 1,8 % |
| Un clip de 5 min al día | 225.104 | **377,21** | 0,0027 | 0,3 % |
| 50 clips de 5 min al día | 11.255.190 | **7,54** | 0,1326 | 13,3 % |
| Una GPU al 30 % | 19.448.968 | **4,37** | 0,2290 | 22,9 % |
| Crecimiento 0,1 → 30 | 1.806.395.597 | 0,047 | 21,27 | 2.127 % (por encima) |
| Capacidad del calendario | 84.965.411 | **0,9994** | 1,0006 | 100,1 % |

Lectura: **1 KIA = 1 minuto de GPU es exactamente el precio que cuadra si la red
entrega justo la capacidad del calendario** (0,9994 frente a 1, error del 0,06 %
por los pasos mensuales). No es una coincidencia: la unidad se definió para eso.

Con menos demanda, el mismo calendario obliga a que el minuto de GPU valga más:

- con la demanda de hoy: **56,21 KIA por GPU-minuto** → una inferencia 7 B/500
  tokens (0,47 min de GPU) = 26,2 KIA, un clip de 5 s = 281 KIA (5 min de GPU);
- con un estudio que mueve 50 clips al día: **7,54 KIA por GPU-minuto** →
  el mismo clip = 37,7 KIA;
- con una GPU al 30 %: **4,37 KIA por GPU-minuto** → el clip de 5 s = 21,9 KIA.

---

## 5. Si la red se pasa de capacidad, el tope manda

Con el precio que cuadra en cada perfil:

| Perfil | KIA que cobra de verdad por GPU-min | % del precio nominal |
|---|---|---|
| Crecimiento 0,1 → 30 | 0,047 | 4,7 % |
| Capacidad del calendario | 0,9994 | 99,9 % |

Cuando el trabajo entregado pasa de 1,31 minutos de GPU por minuto, el reparto
del minuto se agota y cada GPU-minuto cobra lo que queda: el precio nominal
deja de tener efecto y manda el tope. Es el único caso en que un minero cobra
menos de lo que dice la tabla.

---

## 6. Conclusión, en una frase

**La cuenta cuadra sola si la red trabaja a tope: 1 KIA = 1 minuto de GPU y el
suministro se acaba el 13-04-2150.** Si la red trabaja menos, hay que elegir y
decirlo:

1. **Mantener la unidad** (1 KIA = 1 minuto) y aceptar que la emisión dura más
   de lo previsto: con la demanda de hoy, hasta el año ~8.955. El KIA conserva
   un valor estable y comparable con otras monedas de cómputo, pero el
   calendario se alarga 6.800 años.
2. **Mantener la fecha de 2150** y dejar que el precio del minuto de GPU suba a
   lo que pida el trabajo entregado: 56,21 KIA/min con la carga de hoy, 7,54 con
   un estudio de vídeo, 4,37 con una GPU al 30 %. El calendario se cumple, pero
   la unidad deja de ser un minuto y pasa a ser una fracción de minuto que
   cambia con la demanda (lo mismo que hace Bitcoin con la dificultad, pero al
   revés: aquí el que se ajusta es el precio del trabajo, no la dificultad).

Lo que **no** cuadra es prometer las dos cosas a la vez: unidad fija, fecha fija
y precio del trabajo fijo. Con un calendario cerrado y un suministro de trabajo
que no controlamos, solo dos de las tres pueden ser verdad.

Recomendación: **opción 1** como unidad pública (es la que está en la web, en
el whitepaper y en el aviso a mineros, y la que hace comparables los trabajos),
con el tope del minuto como fusible y el cuadre de este documento publicado
como "qué haría falta para llegar a 2150". Cambiar a la opción 2 es decisión de
Kuki, y si se cambia, hay que tocar la unidad en los cuatro sitios a la vez
(Core, web, whitepaper, aviso) o quedan incoherentes.

---

## 7. Cómo se ha comprobado

- **Demanda de hoy, medida en el ledger**: 0,0233 KIA/min = 2.326 pagos en 10
  minutos, todos de 0,0001 (post-suelo). Equivale a 33,55 KIA/día.
- **Tope del minuto**: `presupuesto_del_minuto()` del Core = (100 M − emitido) /
  minutos restantes = 1,310572 KIA/min hoy, y baja con el tiempo.
- **Capacidad por GPU**: 15.000 GFLOP/s medidos con `tools/benchmark_gpu.py` en
  esta máquina; el suelo de 0,0001 KIA = 90 GFLOP = 6 ms.
- **Pasos mensuales** con la fórmula del Core (`min(demanda, presupuesto)` por
  minuto); cuando la demanda alcanza el tope, la emisión sigue al calendario y
  cierra exactamente en 2150 (comprobado en el perfil de crecimiento).
- Todo con `python tools/cuadre_2150.py` (y `--json` para los datos crudos).
  Coste del cálculo: menos de un segundo.

**Lo que este documento NO dice**: cuánto vale un KIA en euros. Eso depende de
lo que alguien pague por el cómputo, y no se puede deducir del calendario. Las
cuentas de euros están en `ESTRATEGIA.md` y `tools/calculadora_valor.py`, con
sus supuestos a la vista.
