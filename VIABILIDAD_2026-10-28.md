# Viabilidad de KUKIA — 28/10/2026

Qué se ha medido hoy, qué aguanta y qué no. Cada número de aquí sale de un
comando ejecutado en esta máquina el 28/10/2026 entre las 08:47 y las 11:00, no
de un documento anterior. Al final está lo que **no** puedo afirmar.

---

## Veredicto en una tabla

| Área | Estado real hoy | Viabilidad | Qué la decide |
|---|---|---|---|
| **Moneda / ledger** | cadena OK, 913.838 asientos, 509 sellos, suministro = suma de saldos | **Alta** (como prototipo verificable) | 109 + 61 pruebas propias en verde, incluidos doble gasto, doble pago, `kill -9` y disco lleno |
| **Monedero** | firma en el navegador, clave solo en memoria, 20 palabras | **Alta** técnicamente / **media** como producto | falta multisig, copia de la frase y una forma de entrar/salir con euros |
| **Web** | publicada y responde (2 copias), pero la versión viva es más vieja que el código | **Alta** como escaparate / **baja** como producto en vivo | depende de un túnel que hoy está caído |
| **Minero** | funciona en esta máquina (864.000 tareas pagadas), nadie de fuera ha minado nunca | **Media** | no hay un solo minero externo que haya cobrado |
| **Operación 24/7** | Core arriba desde las 08:47, minero y guardia apagados, red parada desde el 26/10 17:19 | **Baja** | sin guardia ni arranque automático, la red solo existe cuando alguien abre cuatro scripts |
| **Economía** | 15,09 M de 100 M emitidos, 0 € de ingresos, sin precio ni mercado | **Muy baja** | KIA no tiene comprador; el listado exige un mercado que no existe |
| **Descentralización** | 1 Core, 21 nodos todos de esta máquina, 1 validador | **Baja** | es un prototipo centralizado, y decirlo es parte del trabajo |
| **Poscuántico (PQC)** | Ed25519 + ML-DSA-65 híbrido, 29/29 pruebas, **apagado** | **Alta** técnicamente | falta decidir cuándo se enciende (`KUKIA_PQC=1`) y qué pasa con las direcciones v1 |

Resumen en una frase: **la moneda está bien hecha y la red no está en marcha.**
Lo técnico aguanta una auditoría; lo que no existe es demanda, disponibilidad y
un sitio fijo al que conectarse.

---

## 1. Moneda y ledger — lo que está demostrado

Medido contra el Core vivo (`127.0.0.1:8300`, v0.4.4, commit `f36a309`):

| Dato | Valor |
|---|---|
| Asientos en la base | 913.838 (913.663 vivos tras archivar) |
| Cadena | `cadena_ok: true`, `ids_alterados: []` |
| Sellos firmados | 509, `sellos_ok: true`, `pagos_bloqueados: false` |
| Suministro | 15.088.174,2976 KIA (15,09 % de 100 M) |
| Suministro vs. saldos | igual al céntimo (15.088.174,297672 en ambos lados) |
| Emisión | 1,3077 KIA/min · fin 13/04/2150 · halving 18/09/2046 |
| Precio del GFLOP | 1,453e-6 KIA (ancla GPU-minuto, suelo) |
| Anclas OpenTimestamps | 386 ficheros; las antiguas confirmadas en bloques 972335, 972338, 972355 |

Baterías pasadas **hoy**, sobre copia del ledger y Core aislado (la base real no
se toca):

- `tools/pruebas_seguridad.py` → **109/109**, salida 0.
- `tools/pruebas_caos.py` → **61/61**, salida 0.
- `python -m pqc.pruebas_pqc` → **29/29**.

Lo que eso cubre de verdad, y es lo que separa esto de una hoja de cálculo:

- 8 transferencias simultáneas del mismo saldo → cobra **una**, saldo nunca
  negativo, suministro quieto.
- El mismo resultado entregado 6 veces a la vez → se paga **una**, un asiento.
- `kill -9` con 40 transferencias en vuelo → al rearrancar: cadena intacta,
  cero medios asientos, nonces cobrados siguen gastados.
- **Disco lleno de verdad** (hoy se arregló la prueba, ver §10) → lecturas 200,
  escrituras **507** con mensaje claro, nunca 500, ni un asiento a medias, y lo
  rechazado se puede reintentar.
- Basura contra las rutas públicas (27 casos) → ni un 500; token de admin no se
  puede bajar por `/modelos`.
- Un lector ajeno (auditoría o copia) leyendo el `.db` en WAL → no bloquea un
  pago.

**Veredicto:** como ledger verificable, esto está por encima de lo que suele
verse en un proyecto de tres semanas. No es una cadena descentralizada y no
pretende serlo: hay **un** Core que decide y paga, y quien lo controle puede
reescribir la historia y volver a firmar. Lo que impide que eso pase
inadvertido es el ancla externa (OpenTimestamps en Bitcoin) y las copias.

---

## 2. Core y operación — aquí está el agujero de hoy

Estado a las 08:47 (cuando se empezó a medir): solo el Core.

```
127.0.0.1:8300   PID 9036   uvicorn kukia_core:app    (desde 08:47)
PID 2708                    registrar_salida.py       (envoltorio del log)
```

A las **09:30** se levantó la guardia (Core adoptado, túnel, minero, validador,
panel, publicador y anclaje) y se dejó en el arranque de sesión: ver §10 bis. La
red volvió a minar en menos de un minuto (2 GPUs, 977 tareas, cola de 139 a 55).

Consecuencias que se midieron **antes** de levantarla:

- **La red está parada desde el 26/10 a las 17:19** (último asiento pagado). Hay
  **139 tareas en cola** (95 matriz, 25 inferencia, 19 difusión) esperando a un
  minero que no está.
- **El túnel público está muerto.** La dirección que anunciaba `core_actual.json`
  (un `*.trycloudflare.com`) no responde (`000`), no hay proceso `cloudflared` y
  el espejo seguía anunciándola desde el 27/10 a las 22:01. Traducido: quien
  entrara a la web no podía consultar saldo, ni transferir, ni minar.
- **No hay arranque automático.** La carpeta `ARRANQUE_AUTOMATICO_QUITADO_20261026`
  guarda los dos lanzadores que se quitaron del inicio. Es una decisión legítima,
  pero mientras siga así la red depende de que alguien esté delante.
- **Copias**: la tarea `KUKIA Backup Ledger` corre a diario y verifica; la de hoy
  (08:54) está bien (913.838 asientos, 509 sellos, `VERIFICADA`). Pero está en
  `C:`, el mismo disco que la base, y con la clave del sello al lado.
- **Réplica en otro disco**: existe en `D:\KUKIA_REPLICA`, pero es del **22/10**
  y le faltan 3.046 KIA de historia. Sirve para arrancar, no para recuperar lo
  de esta semana.

**Veredicto:** la maquinaria está bien pensada (guardia que relanza, túnel que se
republica solo, réplica verificada). El régimen de operación era el fallo: nada
garantizaba que estuviera encendido. Desde las 09:30 del 28/10 sí hay arranque
automático, y **la dirección pública queda como está por decisión del 28/10**:
túnel rápido + espejo `core_actual.json`. Los motivos, las cuentas y el plan para
retomarlo están en [TUNEL_ESTABLE.md](TUNEL_ESTABLE.md) («Decisión del
28-10-2026»).

---

## 3. Web

| Dirección | Estado |
|---|---|
| `https://lordkukone.github.io/kukia-node/` | **200**, 114.229 B — versión **antigua** |
| `https://kukia-network.surge.sh/` | **200**, 128.706 B |
| `https://kukia-network.freebuff.page/` | **404** (referencia muerta en la documentación) |

Dos cosas que importan:

1. **La web viva está por detrás del código.** El `docs/index.html` del repo
   (commit `f36a309`) pesa 126.326 B; lo que sirve GitHub Pages, 114.229 B. El
   trabajo del ramo poscuántico y las últimas correcciones **no están
   publicados**: la rama `pqc-integrado-20261027` no está en `origin/main`, y
   Pages publica desde `main`.
2. **La web no tiene a qué conectarse.** El estado y la billetera leen
   `core_actual.json`, que apunta al túnel caído. La página carga, los datos no.

Lo que sí está bien: es estática (no se cae con el Core), tiene cabeceras de
seguridad desde la auditoría del 21/10, no inyecta datos de la red con
`innerHTML`, y publica el hash de los ficheros que corren en el Core para que
cualquiera los compare con GitHub.

**Veredicto:** buena como escaparate y documentación; **no funciona como
producto** hasta que el Core tenga una dirección que no cambie.

---

## 4. Monedero

Lo comprobado en `docs/wallet.html` (588 líneas):

- 20 palabras + contraseña → PBKDF2 → semilla Ed25519, **derivada en memoria**;
  la clave privada y la frase **no se guardan** en `localStorage` (solo la
  dirección y la URL del Core).
- La transferencia se **firma en el navegador**; al Core viaja la firma, no la
  clave.
- Dirección = `KUK` + sha256(clave pública), con nonce anti-repetición; el Core
  rechaza el nonce repetido con 409.
- Existe el camino v2 (direcciones y sobre híbrido Ed25519 + ML-DSA-65), pero
  está **apagado** (`/pqc/estado` → `activo: false`).
- Multisig 2-de-3: diseñado en `MULTISIG.md`, **no implementado** a propósito
  (las 3 claves en el mismo disco serían teatro).

**Veredicto:** el monedero es correcto en lo esencial (no custodial, firma
local, sin doble gasto). Lo que le falta para ser usable por alguien que no seas
tú: copia de seguridad de la frase guiada, aviso de pérdida, multisig para
saldos grandes y una forma de comprar/vender KIA. Sin eso, un usuario que pierda
el navegador pierde el saldo, y eso es un fallo de producto, no de código.

---

## 5. Minero

- 864.000 tareas pagadas en total (200.000 en tabla + 664.007 archivadas), con
  21 nodos registrados y **todos de esta máquina**.
- Las dos GPUs están en la base con potencia medida por el Core (14,20 GFLOPS en
  `cuda:0`), no declarada por el minero.
- Hoy no corre. El registro `worker/minero.log` no tiene actividad reciente y el
  último pago es del 26/10.
- El paquete descargable y la guía existen (`GUIA_MINERO.md`, `docs/kukia-node.zip`),
  pero **ningún minero de fuera ha completado una tarea y cobrado**. No hay ni
  una prueba de que el alta funcione en un PC ajeno.

**Veredicto:** el minero funciona; la red de mineros no existe. Es el punto donde
"viabilidad técnica" y "viabilidad real" se separan más.

---

## 6. Validadores, réplica y anclaje

| Pieza | Estado medido |
|---|---|
| Validadores | 1 registrado (`VALIDADOR_KUKI`), **0 activos**, 0 revisadas en 24 h; corre en el mismo PC |
| Ejecución redundante (70/30) | probada en la batería (dos nodos, uno miente → castigo) |
| Réplica del ledger | `D:\KUKIA_REPLICA` del 22/10, verificada entonces, **desactualizada** |
| Ancla en Bitcoin | 386 `.ots`; las antiguas confirmadas en 3 bloques; las nuevas pendientes de bloque (normal: tardan horas) |
| Auditoría externa del código | **no hay** |

**Veredicto:** el diseño está completo y probado en un solo PC. La propiedad que
falta —que **otra** máquina pueda contradecirte— no está ejercida: un validador
en el mismo equipo no aporta nada frente a un Core que miente.

---

## 7. Poscuántico (PQC)

- Híbrido **Ed25519 + ML-DSA-65**: sobre completo de 7.089 B (firma 3.309 B,
  clave pública 1.952 B).
- Rendimiento medido: firmar ML-DSA 0,96 ms, verificar 0,19 ms; Ed25519 0,03/0,13 ms.
- 29/29 pruebas en verde.
- **Apagado** en producción: `{"activo": false, "motivo": "desactivado: arranca el Core con KUKIA_PQC=1"}`.
  Con el flag apagado, `/transferir` sigue aceptando firmas v1.

**Veredicto:** es la pieza más diferencial del proyecto (casi ningún proyecto de
este tamaño tiene firma posecuántica híbrida probada) y está lista para
encenderse. Lo que falta no es código: es decidir la migración de direcciones v1
y que el monedero del navegador sepa firmar el sobre híbrido.

---

## 8. Economía — el agujero de verdad

| Dato | Valor |
|---|---|
| Emitido | 15.088.174 KIA de 100.000.000 (15,09 %) |
| Ingresos en euros registrados | **0 €** (`/ingresos`: `n=0, total_eur=0.0`) |
| Clientes en la tabla | 16 filas (registros de prueba, sin cobros) |
| Precio de mercado | **no existe** |
| Listado en CoinGecko/CMC | **imposible hoy**: exigen un mercado activo en un exchange que ellos sigan |
| Coste de mantener la red | la luz y el PC del operador |

Esto es lo que decide la viabilidad de verdad. La emisión paga 1,31 KIA por
minuto de GPU, y ese KIA no tiene comprador: el minero cobra una unidad que hoy
vale 0 €. La única cosa que puede cambiar eso es **alguien que pague por el
trabajo** — es la tesis del proyecto ("Proof-of-Compute", trabajo útil) y hoy no
está demostrada con un solo cliente real. Hay la herramienta para anotar cobros
en euros (`servicio/cobros.py`, asiento de 0 KIA encadenado) precisamente para
que ese día quede escrito en el ledger; está a cero.

**Veredicto: muy baja** mientras no haya demanda. No es un problema de código y
no se arregla programando más.

---

## 9. Legal y regulatorio (orientativo, no es asesoría)

- **MiCA (UE)**: emitir un criptoactivo sin oferta al público ni admisión a
  negociación tiene un régimen más liviano, pero en cuanto haya venta, listado o
  promoción con expectativa de rentabilidad, entra el régimen completo (libro
  blanco, información al inversor, obligaciones del emisor). Ya hay un
  `whitepaper` y un `SOLICITUD_LISTADO_KIA.md`; conviene que los revise alguien
  con responsabilidad legal antes de publicitar precio.
- **Consumidor/publicidad**: la web habla de "moneda" y publica suministro y
  calculadoras. Mientras no haya promesa de rentabilidad el riesgo es bajo, pero
  el lenguaje ("como Bitcoin", "sin un fallo") es lo que más expone.
- **Datos personales (RGPD)**: el servicio de transcripción trata audio de
  terceros; hace falta base legal, información y plazo de borrado aunque sea un
  favor a un conocido.
- **Fiscal**: cualquier euro cobrado por el servicio hay que facturarlo. Hoy son
  0 €, así que no hay nada que regularizar.

Nada de esto es un bloqueo para el prototipo; sí lo es para "lanzar".

---

## 10. Lo que se arregló en esta sesión

- **La prueba de disco lleno no probaba nada.** `_llenar_bd` miraba solo
  `page_count` y se rendía a las 5.000 inserciones: la base tiene ~7.019 páginas
  libres, así que el fichero no crecía, no se llegaba al tope y el test pasaba
  sin ejercitar el escenario (0 respuestas 507 de 43 transferencias). Ahora
  consume primero el freelist y después crece hasta el tope, y **falla en voz
  alta** si no lo consigue. Resultado tras el arreglo: 507 con mensaje claro,
  reintento posible, ni un medio asiento, 61/61.
- Documentación corregida donde daba cifras viejas (101 → 109 pruebas, 57 → 61
  de caos) y donde decía que faltaba el disco lleno estando ya cubierto.

## 10 bis. La red vuelve a estar encendida (28/10, 09:30)

- Guardia levantada y **metida en el arranque de sesión**
  (`%APPDATA%\...\Startup\KUKIA Guardia.vbs`, sin permisos de administrador;
  se desactiva borrando ese fichero). Arranca con 40 s de margen y relanza
  Core, túnel, minero, validador, panel, publicador y anclas.
- Túnel público arriba y comprobado **desde fuera** (r.jina.ai, otra red devolvió
  `/version` con `0.4.4` y commit `f36a309`). CORS responde a
  `https://lordkukone.github.io`. `core_actual.json` republicado.
- Minando otra vez: 2 GPUs, 977 tareas nuevas, cola de 139 a 55.
- Pendiente: cambiar el túnel rápido por una dirección **fija** (ngrok gratis o
  dominio propio); el script ya está escrito y espera la cuenta.

---

## 11. Qué convertiría esto en viable (por orden de impacto)

1. **Dirección fija del Core.** Aparcada el 28/10 por decisión explícita (ver
   [TUNEL_ESTABLE.md](TUNEL_ESTABLE.md)): el espejo resuelve que el paquete no
   caduque, y ngrok queda descartado por sus 20.000 peticiones al mes. El plan
   (dominio + túnel con nombre, ~10–15 €/año) está escrito y probado por
   partes. *Hecho cuando*: `https://<direccion-fija>/stats` responde 200 desde
   otra red y sigue respondiendo tras reiniciar el PC.
2. ~~Que la red esté encendida sin que nadie la encienda.~~ **Hecho el 28/10**:
   guardia en el arranque de sesión. Falta el aviso si se cae (hoy solo queda
   en el log). *Hecho cuando*: apagas el PC, lo enciendes, y a los 5 minutos
   todo está arriba y, si algo no lo está, te llega un aviso.
3. **Un minero de fuera, real, cobrando.** Uno solo, con la guía tal como está.
   *Hecho cuando*: un PC ajeno completa una tarea y su saldo sube.
4. **Réplica automática diaria en otro disco** (hoy es manual y está 6 días
   vieja) y una restauración probada al mes. *Hecho cuando*: la réplica tiene la
   misma suma que el Core cada mañana.
5. **Un cliente que pague por trabajo** (transcripción, imagen, inferencia), en
   euros, anotado en `servicio/cobros.py`. *Hecho cuando*: `n >= 1` en
   `/ingresos`.
6. **Mercado**: sin un exchange no hay precio, y sin precio no hay listado. Antes
   de eso, lo honesto es no hablar de valor.
7. **Multisig 2-de-3 y un validador en otra máquina** (después de 3 y 5).
8. **Auditoría externa** del código y publicación del ramo poscuántico.

Nada de la lista 1–4 es investigación: son tardes de trabajo. La 5 y la 6 no
dependen de código.

---

## 12. Lo que hoy NO puedo afirmar

- Que la web publicada sea el código que corre: **no lo es** (114 KB vivos
  frente a 126 KB del commit local `f36a309`, que no está en `origin/main`).
- Que alguien de fuera pueda usar la red: el túnel está caído.
- Que el sistema aguante un minero externo: nunca ha habido uno.
- Que las 40 pruebas del ciclo cliente sigan en verde: no se han re-ejecutado
  hoy (sí las 109 de seguridad, las 61 de caos y las 29 de PQC).
- Que el historial esté anclado hasta el último sello: las anclas nuevas están
  pendientes de bloque.
- Nada sobre valor de mercado: no hay mercado.

---

## Cómo repetir estas medidas

```bash
curl -s http://127.0.0.1:8300/ledger/integridad       # cadena, sellos, pagos
curl -s http://127.0.0.1:8300/stats                    # suministro, emisión, nodos
python publico/verificar_sellos.py --core http://127.0.0.1:8300
python tools/pruebas_seguridad.py                      # 109 casos
python tools/pruebas_caos.py                           # 61 casos
python -m pqc.pruebas_pqc                              # 29 casos
python tools/replica_ledger.py --estado                # réplica en D:
python tools/anclar_ots.py --estado                    # anclas en Bitcoin
curl -s http://127.0.0.1:8300/ingresos                 # demanda real en euros
```
