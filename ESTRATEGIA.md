# KUKIA — cómo se gana a Bitcoin, y en qué no se puede

Documento de dirección, 20-10-2026. No es marketing: es lo que hay, lo que
falta y en qué orden hacerlo. Si algo de aquí no se puede comprobar con un
comando, está mal escrito.

---

## 1. Dónde está la red hoy (medido, no estimado)

| Dato | Valor real |
|---|---|
| KIA emitidos | 15.083.475 de 100.000.000 (15,08 %) |
| Tareas verificadas | 91.215 |
| Nodos registrados | 26 (2 GPU reales minando; el resto, pruebas) |
| Potencia medida | 13,9 y 14,2 GFLOPS (Core cronometrando, no el minero) |
| Emisión | 1,3076 KIA/min, con fecha de agotamiento 13-04-2150 |
| Sellos firmados | 21, cadena intacta |
| Ancla pública | `freebuff.page/api/metricas` → verifica firma Ed25519 |

Y el fallo que más importa y que no era de código: **el minero llevaba 2 h 30
min parado** (12:28 → 14:58) con 121 tareas en cola. La red se veía "en vivo"
en la web porque el dato era de hacía minutos. Nadie se enteró. Eso ya está
resuelto con `tools/guardia.py` (ver punto 5).

---

## 2. Contra Bitcoin: qué se puede ganar y qué no

Hay que decirlo sin adornos, porque el resto del plan depende de ello.

**Lo que NO se va a ganar, y prometerlo sería mentir:**

- **Descentralización.** Bitcoin tiene miles de nodos independientes que
  validan lo mismo. KUKIA tiene **un** Core que decide y paga. Un Core
  descentralizado sin consenso real es teatro.
- **Historial.** Bitcoin lleva 17 años sin caerse. KUKIA tiene 3 semanas de
  prototipo.
- **Liquidez y confianza de mercado.** No hay exchange, no hay precio, no hay
  auditores.
- **Seguridad física de las claves.** Hoy la clave del sello vive en el mismo
  disco que la base. Eso lo detecta, no lo impide.

**Lo que SÍ se puede ganar, y es una ventaja de categoría distinta:**

| | Bitcoin | KUKIA (si se hace bien) |
|---|---|---|
| Qué se calcula | hashes que no sirven para nada | inferencia, simulación, imagen, vídeo |
| Energía | se quema a propósito | se aprovecha la que ya está encendida |
| Entrada | ASIC de miles de € | la GPU que ya tienes |
| Finalidad | 10 min / 60 min | segundos |
| Emisión | fija por bloques, independiente de quién la pide | atada a una fecha (2150) **y** a trabajo verificado |
| Verificación | cualquiera la hace gratis | hoy la hace el Core, hay que abrirla |

La frase que resume la estrategia: **KUKIA no puede ser mejor que Bitcoin en lo
que Bitcoin lleva 17 años construyendo; puede ser mejor en lo único que Bitcoin
no hace: que el trabajo sirva.**

Eso solo vale si existe **alguien que pague por ese trabajo**. Si no, KIA es un
marcador en una base de datos.

---

## 3. Las tres cosas que deciden el resultado (en este orden)

### 3.1 Un cliente que pague — la única que da valor

Hoy las tareas las genera el propio Core (`crear_auto`, cola B). Es decir: la
red se paga a sí misma por trabajo que nadie pidió. Eso es un simulacro
perfectamente honesto para probar la maquinaria, y **no vale como moneda**.

Hace falta la **cola A con clientes reales**:

1. El cliente se registra con una clave (igual que un nodo).
2. Deposita KIA en depósito (`escrow`) para N tareas.
3. Envía el trabajo por `POST /tareas/cliente` firmado.
4. El minero cobra del depósito, no de la emisión.
5. Si el resultado no cuadra, se devuelve al cliente.

Criterio de aceptación: **un cliente de fuera paga 1 tarea y el asiento del
minero sale del depósito del cliente, no de la emisión.** Hasta que eso ocurra,
no hay mercado: hay una red de pruebas muy buena.

### 3.2 Verificación sin un único Core — la que da escala — **HECHO** (21-10-2026)

El Core recalculaba **todo** lo que el minero entregaba. Costaba lo mismo minar
que verificar, así que la red no podía crecer más allá del PC del Core: ese era
el cuello de botella real, no la GPU.

Implementado: **ejecución redundante con auditoría por muestreo**.

- Las tareas **caras** (≥ 1 GFLOP: `matriz`, `difusion_calor`, `inferencia`) se
  asignan a **dos** nodos distintos: el primario y otro que recibe
  `"replica": true` al pedir trabajo.
- Si los dos coinciden → el Core **no recalcula** y paga el total de siempre,
  repartido 70 % ejecutor / 30 % verificador (ni un céntimo de emisión extra).
- Si discrepan → el Core recalcula, paga al honesto, castiga al mentiroso
  (dinero, reputación y 30 min sin trabajo) y cierra la tarea con el resultado
  bueno si alguno lo tenía.
- **1 de cada 10** tareas replicadas se recalcula igualmente (muestreo
  determinista por id: el mismo id cae siempre del mismo lado).
- Techo de verificadores: como máximo **la mitad de los nodos activos**
  verifica a la vez; el resto ejecuta. Sin ese techo, todos los nodos libres
  cogían réplicas por delante de las tareas nuevas y la cola avanzaba a
  trompicones (*todo ejecutar → todo verificar*).

Medido en la suite (copia aislada del ledger, dos nodos): `verificado_por`
`replica` con `parte_replica` > 0 y `kuki=0.051906 + 0.022245` = el mismo premio
que antes por una sola tarea.

Criterio de aceptación que **falta por medir en la red real**: 500 tareas con
dos mineros, cero discrepancias, 10 % de CPU del Core y ni un pago doble. La
lógica está probada; el comportamiento con 26 nodos a la vez, todavía no.

### 3.3 Un ancla que no dependa del dueño

Hoy el ancla la publica el mismo que produce los sellos. Si alguien controla
ese PC, puede reescribir y volver a firmar. Sirve para detectar, no para
impedir.

Lo que cierra ese hueco, por orden de coste:

1. **OpenTimestamps** (gratis): se ancla el hash del último sello en la cadena
   de Bitcoin. Desde ese momento el historial de KUKIA queda atado a Bitcoin,
   no a ti. Es el paso más rentable de todo el documento.
2. **Segundo testigo independiente** (otra máquina, otra persona) que publique
   el mismo hash. Si los dos coinciden, no hay reescritura silenciosa.
3. **Publicar el código en GitHub** (ya está `docs/`) para que el ancla sea
   reproducible por cualquiera.

Criterio de aceptación: **un tercero, con el `kukia-node.zip` y el ancla,
puede verificar la cadena sin pedirte nada.**

---

## 4. El minero: cómo lo hacemos

El minero es lo que más gente toca y lo que más se rompe. Plan por fases, con
criterio de "esto está hecho" en cada una.

### Fase 0 — lo que ya está (20-10-2026)

- **Guardia** (`tools/guardia.py`, `ARRANCAR_GUARDIA.cmd`): vigila Core, minero,
  validador, publicador y panel. Relanza lo que se cae y **no duplica** lo que
  ya está abierto a mano. Probado de verdad: minero matado a las 15:06:22,
  relanzado a las 15:06:39 (17 s) y cobrando otra vez.
- **Potencia demostrada**: el tier (x1,3 / x1,6) ya no se paga por declarar
  VRAM. Se mide con el reloj del Core sobre trabajo verificado. Calibrado con
  datos reales: 13,9 GFLOPS en 16 GB y 14,2 en 12 GB; el listón anti-fraude
  está en 0,25 GFLOPS por GB declarado, cuatro veces por debajo.
- **Un solo .cmd** que comprueba Python, dependencias, Core accesible y arranca.

### Fase 1 — minero de un clic (1 semana)

Problema real: hoy hace falta Python, pip, torch y una consola abierta. Eso
pierde al 90 % de la gente.

1. **Instalador único** (`KUKIA-Minero.exe`, PyInstaller, sin consola): detecta
   GPU, crea billetera si no hay, arranca y queda en la bandeja del sistema.
2. **Auto-actualización firmada**: el minero comprueba el hash publicado en el
   ancla; si hay versión nueva y la firma cuadra, se actualiza solo. Sin esto,
   cada arreglo obliga a repartir el zip a mano.
3. **Benchmark al arrancar**: mide sus GFLOPS reales con las mismas tareas y
   los declara. Así el tier que pide es el que puede dar.
4. **Minar y validar en el mismo proceso**: una GPU que mina también recalcula
   una tarea cada N. Es la forma barata de tener validadores de verdad.
5. **Aviso de billetera**: las 20 palabras se muestran **una vez** y se
   confirma que se han copiado antes de minar.

Criterio de aceptación: **un PC con Windows limpio, sin Python, minando y
cobrando en menos de 3 minutos, y actualizándose solo al día siguiente.**

### Fase 2 — no depender de un Core concreto (2-3 semanas)

1. Lista de Cores con failover y comprobación de que el trabajo asignado sigue
   siendo del mismo Core (ya hay firma; falta la lista).
2. **Recibo de trabajo verificable**: el Core devuelve un recibo firmado
   (tarea, resultado, pago, sello). El minero lo guarda; el minero puede probar
   lo que cobró sin creerse una captura de pantalla.
3. Pago por **trabajo medido** (FLOPs verificados) en vez de por tarea:
   ahora que 3.2 está hecho, el trabajo caro ya se paga con la GPU de dos
   nodos, que es la base para medirlo.

Criterio de aceptación: **tirar el Core a mitad de tarea y que el minero siga
cobrando en otro Core sin perder ni un asiento.**

### Fase 3 — la economía (cuando 3.1 esté)

Precio por FLOP, depósito de clientes, reparto entre minero y validador, y
quema del excedente si la gobernanza lo decide. Esto **no** se toca antes de
que exista un cliente pagando: sería regular un mercado vacío.

---

## 5. Lo que se ha cambiado hoy en el código

**`tools/guardia.py` (nuevo)** — supervisor. Cada 30 s: pide `/stats` al Core;
si no responde 3 veces lo arranca; si el Core no paga tareas nuevas en 10 min
con cola pendiente, relanza el minero; mantiene vivos validador, publicador y
panel. Antes de arrancar algo mira si ya existe (se buscaba a sí mismo en la
lista de procesos y se creía que el minero seguía vivo: corregido y probado).

**`coordinator/kukia_core.py`** — potencia demostrada:
- Columnas `nodos.gflops_medido` y `nodos.potencia_muestras`.
- La muestra se toma con **el reloj del Core** entre asignar y entregar, así
  que el minero no puede acortarla. Solo cuentan tareas de más de 1 GFLOP y
  50 ms; las pequeñas las domina la red.
- `tier_verificado()`: el tier declarado se paga solo si la GPU lo respalda.
  Con menos de 20 muestras no se corrige nada (no se castiga a un nodo nuevo).
- `/nodos` y cada pago devuelven `tier_pagado` y `tier_declarado`: se ve en la
  respuesta, no hay que creerse el panel.

**`tools/pruebas_seguridad.py`** — dos cosas:
- Prueba nueva: un nodo que declara 24 GB con 1 GFLOPS medidos **entra** con
  tier 1,6 pero **cobra** tier 1,0.
- Aislamiento: el cupo anti-Sybil se limpia en la copia. Antes la suite fallaba
  según cuántos nodos se hubieran registrado en la máquina esa hora.

**Resultado:** 70/70 pruebas, salida 0 (`pruebas_final_2026-10-20.log`).

---

## 6. Lo que NO se va a hacer (y por qué)

- **Multisig con tres claves en este disco.** No aporta seguridad, solo papeleo.
  El diseño está en [MULTISIG.md](MULTISIG.md); hace falta hardware separado.
- **Decir "mejor que Bitcoin" sin auditoría.** Se puede decir "distinto y
  verificable"; lo otro se gana, no se declara.
- **Abrir registro masivo antes de 3.2.** Con el Core recalculándolo todo, 200
  nodos lo tumban en una tarde. Primero escala, después marketing.
- **Precio antes de demanda.** Un token sin comprador no tiene precio: tiene
  ilusión.

---

## 7. Los próximos 7 días, en orden

| # | Tarea | Por qué ahora | Riesgo |
|---|---|---|---|
| 1 | ~~Anclar el último sello en OpenTimestamps~~ **HECHO** (3 sellos, automático) | ata el historial a Bitcoin, gratis | bajo |
| 2 | `POST /tareas/cliente` + depósito | sin cliente no hay moneda | medio (toca dinero) |
| 3 | ~~Ejecución redundante + muestreo 10 %~~ **HECHO** (21-10-2026) | quita el cuello del Core | alto (toca pagos) |
| 4 | Instalador `.exe` del minero | sin esto no entra nadie | bajo |
| 5 | Dos validadores en dos máquinas | independencia de verdad | bajo |

Si solo se puede hacer una: **la 1**. Es barata, irreversible a favor y
convierte "confía en mí" en "compruébalo tú".
