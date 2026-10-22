# Auditoría final KUKIA — moneda, web y minero
**Fecha:** 21-10-2026, 10:00–10:35 (hora local)
**Alcance:** ledger y emisión KIA, Core, minero, relé y web publicada en `kukia-network.surge.sh`
**Método:** invariantes medidas contra la base de datos real (solo lectura), suites del propio proyecto y medición en el navegador sobre la web publicada. No es una auditoría de seguridad externa.

---

## 1. Veredicto

| Área | Estado | Comentario |
|---|---|---|
| Contabilidad de la moneda | **Sana** | Suma del ledger = saldos = `/stats`, al céntimo |
| Integridad del historial | **Sana** | 70 sellos firmados, cadena 158.263 asientos, clave pública verificada |
| Emisión y calendario | **Sano** | 1,41–1,52 KIA/min medidos frente a 1,3076 objetivo |
| Anti-fraude / anti-doble-pago | **Sano** | 93/93 pruebas, 409 en doble pago, recálculo y redundancia |
| Minero | **Estaba caído** | 16 h parado; relanzado. Ahora paga cada segundo |
| Web | **Corregida** | Números encajados y respaldo en 3 capas (relé → mismo dominio → navegador) |
| Crecimiento de datos | **P1 pendiente** | `planificador_log` y `gpu_telemetria` sin poda (142 MB y subiendo) |
| Disco | **P1 pendiente** | 4,9 % libre (88 GB en C:) |

**Conclusión:** el núcleo económico es correcto y verificable. Los riesgos reales de esta fase no son de código monetario, sino operativos (procesos caídos, disco, crecimiento de la base) y de robustez de la web (depende del relé).

---

## 2. Moneda y ledger — invariantes comprobadas

Todo se midió contra `coordinator/ledger/kukia.db` en modo solo lectura mientras el Core seguía minando.

| Comprobación | Resultado |
|---|---|
| `SUM(kuki)` del ledger | 15.084.126,988851 |
| `SUM(total)` de `saldos_cuenta` | 15.084.126,988851 |
| `supply_emitido` de `/stats` | 15.084.126,988851 |
| Techo `MAX_SUPPLY` | 100.000.000 (no se supera: el pago se capa en `min(base·tier, restante)`) |
| Asientos con importe ≤ 0 | 0 |
| Doble pago por tarea | 0 (ninguna tarea con más de 2 asientos: trabajo + réplica) |
| Sellos validados por `publico/verificar_sellos.py` | 70 sellos, **historial intacto y firmado** |
| Clave pública del sello | `2013664e12b4ac93f5a266ae5e1a4a071f51c68112e1209fae5d501ecb2f8d1c` |
| Pagos bloqueados | No |

### Composición del suministro (dato que la portada no decía)

- **15.000.000 KIA** — asignación fundacional (bloque genesis, `GENESIS_MONTO`), migrada a `KUK9E8B44CD7FE3AF178039453D3EEDF391EEC0CE`.
- **84.126,99 KIA** — minados por trabajo verificado en 154.748 tareas.
- Transferencias: 17.092,29 KIA en 15 movimientos (entrada y salida cuadran, no crean dinero).

El saldo de la dirección fundacional es **15.000.384,0057 KIA**: los 15M más lo que ha minado su propio nodo. El whitepaper declara la asignación; la portada no lo hacía al lado de la cifra, y ya se añadió la nota.

### Emisión

- Objetivo de calendario: **1,3076 KIA/min** hasta el 13-04-2150 con 84,9 M restantes.
- Medido con el minero activo: **1,515 KIA/min** (últimos 5 min) y **1,409 KIA/min** (últimos 10 min). Dentro de la banda prevista.
- Pago por tarea: **0,0113 KIA** (tier 1,0) y **0,0147 KIA** (tier 1,3). La relación 1,3 entre `cuda:1` y `cuda:0` se cumple.
- El reparto 70/30 con la réplica **no infla** la emisión (el total por tarea es el mismo).

> Nota metodológica: una lectura de "KIA/min" en una ventana que incluya tiempo con el minero apagado da cifras falsas (0,21 KIA/min). Medido solo con el minero en marcha, cuadra.

### Anti-fraude observado en el historial

- 6 asientos de `resultado incorrecto (recalculado por el Core)` a 0 KIA → el recálculo funcionó.
- 1 asiento `hash no verificado` a 0 KIA.
- 4.749 pagos de `verificacion redundante`: la réplica se paga y se contabiliza aparte.

---

## 3. Minero

- **Estaba parado desde las 22:30 del 20-10 (16 h)**. El último asiento era `22:30:53`; `/stats` mostraba 121 tareas en cola y 0 ejecutando. La causa no fue el código: el proceso murió con el reinicio del entorno.
- Relanzado a las 10:17. Primera tarea pagada: `10:18:11`. Ahora escribe en el ledger cada segundo.
- Nodos activos: `minero_pc[cuda:0]` (tier 1,0; 38.615 tareas) y `minero_pc[cuda:1]` (tier 1,3; 31.183 tareas). Reputación 5,0, 0 fallos.
- `tier_verificado` solo rebaja la prima de VRAM si la GPU no la respalda (nunca paga menos de 1,0). Con 17,16 y 16,44 GFLOPS medidos, ambos cobran su tier.
- Telemetría: `gpu_telemetria` guarda tiempo y FLOPs medidos por la propia GPU; `/red/serie_gpu` devuelve 60 puntos con `gflops` y `ms` por dispositivo.
- En el log histórico aparecen `entrega rechazada: 500 {"detail":"error interno en el pago"}` esporádicos y errores de conexión durante caídas del Core. La suite cubre el camino de pago (93/93) y no se reprodujeron hoy; quedan como P2.

### 3.1 Arreglo del minero (21-10, tarde)

Síntoma real, medido en el log: la GPU estaba encendida y el minero **no cobraba**.

| Fallo | Evidencia | Arreglo |
|---|---|---|
| Se quedaba pidiendo trabajo cada 10 s para siempre cuando el Core perdía el nodo un instante (404) | **233** líneas `nodo no registrado`; parones hasta que alguien reiniciaba el proceso | Se comprueba el mismo id (4 intentos, 2,6 s) y solo se registra otro si de verdad no existe. Si cambia, se sueltan las tareas del viejo |
| Tareas colgadas a nombre del nodo → `limite de tareas simultaneas` durante 30 min | **8** tareas `ejecutando` y **0** pagos en 120 s (reproducido) | Vigía: a los 2 min sin cobrar con el cupo lleno pide el reinicio firmado del nodo (libera 4+3 tareas en segundos). A los 5 min con el Core en pie reinicia el proceso |
| Entrega con `timeout=900 s` | cuda:1 a **0 tareas/min** con dos tareas en vuelo | 90 s y un reintento; un 409 confirma que ya estaba pagada (sin doble pago) |
| `404` de réplica interpretado como «el Core no admite réplicas» | Desactivaba la verificación redundante para siempre | Se distingue el texto: solo se desactiva si el Core no tiene la ruta |
| Sin forma de saber si rendía | El log solo tenía líneas de tarea | Resumen por minuto y por GPU: tareas/min, KIA/min, fallos (leído del propio minero) |

Medición con **un solo** minero y las mismas condiciones (5 min seguidos, pagos reales del ledger):

| Versión | Tareas/min | KIA/min | Rechazos |
|---|---|---|---|
| Antes (secuencial, sin vigilancia) | 179 | 1,82 | 0 |
| Ahora (secuencial + autocuración) | 191 | 1,51–3,17 | 0 |

El KIA/min varía porque el premio por tarea depende del tier y de la emisión (oscila entre 0,0075 y 0,0103 KIA/tarea); las tareas/min es la cifra comparable. Un intento intermedio con pipeline (pedir/entregar en hilos aparte) subió a 270 tareas/min **con dos procesos** y bajó a 126 al saturar la conexión SQLite del Core: se descartó y se quedó el bucle secuencial probado.

Dos cosas que **no** son del minero y quedan documentadas:

- El Core responde `404 nodo no registrado` (y `500 error interno en el pago`) de forma esporádica, ~2 veces por 2.700 peticiones con el nodo existiendo. Se descartó la carrera de cursores sobre la conexión compartida (18.002 lecturas sin una sola pérdida); el minero ahora lo absorbe sin perder trabajo.
- El guardia puede arrancar un segundo minero si se relanza a mano justo después de matarlo (caché de 20 s). Con dos procesos sobre los mismos nodos, el cupo de 4 se reparte y el rendimiento **cae a la mitad**. Queda como P2: que el guardia espere una vuelta antes de relanzar.

---

## 4. Web publicada

### Defecto encontrado y corregido: las cifras se salían de la tarjeta

El CSS usaba `16cqw` con respaldo `clamp(1.5rem,4.4vw,2.6rem)`. En un navegador sin *container queries* (o con HTML viejo en caché) el respaldo daba 41,6 px para una caja de 148 px: `15.053.428` medía ~250 px y se salía. En Chromium actual cabía por 4 px de margen, así que el error era invisible en una máquina y evidente en otra.

Corregido con tres capas:

1. `13cqw` en vez de `16cqw` (más margen).
2. Respaldo más conservador (`clamp(1.35rem,4.4vw,2.4rem)`).
3. **`encajarNumeros()`**: mide el texto renderizado y baja el tamaño hasta que cabe, en cada refresco, al redimensionar y al cargar las fuentes. No depende de `cqw`.

Medición sobre la web publicada (`v1.0.9-numeros-encajados`):

| Ancho | Peor caso forzado | Resultado |
|---|---|---|
| 1280 px | `100.000.000` con fuente forzada a 52 px | 0 desbordes (baja a 24 px) |
| 1280 px | datos reales (15.084.123 · 154.277 · 158.440) | 0 desbordes, sin scroll horizontal |
| 390 px | datos reales | 0 desbordes, `scrollWidth` 373 < 390 |
| 320 px | `100.000.000` | 0 desbordes |

### Otros arreglos de esta pasada

- Whitepaper: «10 KIA por tarea» → pago dinámico real (0,004–0,01 KIA según tier) y nota de redundancia 1-de-10.
- FAQ de la portada: misma corrección (era la respuesta a «¿cuánto puedo ganar minando?»).
- Nota bajo las cifras: los KIA emitidos **incluyen los 15 M fundacionales**.
- Tarjeta «100M KIA máximo» ya no muestra `0M` si el JS no anima.
- `ARRANCAR_METRICAS.cmd` decía «cada 10 segundos»; ahora dice un minuto (lo real).

### 4.1 Respaldo en tres capas (nuevo, `v1.1.0-respaldo-local`)

El relé ya no es un punto único de fallo:

1. **Relé** (dato en vivo, refresco 1 min).
2. **`metricas_snapshot.json`**, en el mismo dominio que la página: sin CORS y sin depender del hosting del relé. El publicador lo reescribe **cada minuto** (escritura atómica) y lo publica en surge cada **20 min** en régimen normal, o cada **5 min** cuando el relé falla (surge sube el directorio completo: subirlo cada minuto serían ~9 GB/día).
3. **`localStorage`**: el último dato bueno que vio ese navegador.

La etiqueta distingue siempre el origen y la edad real: `EN VIVO · hace 36 s`, `RESPALDO · instantánea servida por este dominio · hace 6 min · el relé no responde`, `SIN RELÉ · último dato guardado en este navegador · hace 61 min`.

Probado sobre la web publicada simulando relé muerto (`?core=http://127.0.0.1:9`): mostró la instantánea del dominio con su edad. Probado en local con el fichero de instantánea borrado (404): cayó al dato del navegador. Ambos casos con 0 desbordes de cifras.

### 4.2 Sello verificado en el navegador (`v1.2.0-sello-verificado`)

La instantánea y el relé llevan el **último sello firmado** del ledger. La portada ya no se fía de ninguno de los dos: verifica la firma Ed25519 **en el navegador**, con la clave pública fijada en el código (`2013664e…`), y comprueba que el sello no retroceda respecto al último que ese navegador ya vio (`kukia_ancla_visto` en `localStorage`).

**Fallo encontrado y corregido en el camino:** el cuerpo firmado del sello **no incluye** `clave_publica` (lo añade el relé al publicar). Meterlo en el mensaje hacía que *ninguna* firma cuadrase; verificado con Python: con el campo dentro → `False`, sin él → `True`. La canonización del navegador replica la de `ledger/sellos.py` (claves ordenadas, sin espacios, `suma` siempre con decimal).

Qué se rechaza: firma inválida, clave distinta de la conocida, `supply` menor que la suma del sello, y **retroceso** (menos asientos o `hasta` que el sello ya visto, o mismo número de asientos con otro hash). Un retroceso deja la página en `SIN DATOS VERIFICABLES` en vez de pintar cifras viejas como actuales.

Pruebas (navegador real):

| Caso | Resultado |
|---|---|
| Relé en vivo | `EN VIVO · hace 73 s · sello verificado` |
| Instantánea válida con relé caído | `RESPALDO · … · sello verificado` |
| Instantánea manipulada (`suma` +1) | rechazada; queda `SIN DATOS VERIFICABLES` y no se guarda ancla |
| Sello ya visto más nuevo (retroceso) | rechazada; no pinta el dato viejo |
| Sello que encadena (`prev` = hash visto) | aceptada |
| `?core=` no loopback | ignorado; sigue con el relé verificado |
| `?core=` local (`127.0.0.1`) | `modo local (sin sello)`, sin fingir verificación |

Límite honesto: el sello firma el **ledger** (hash, asientos, suma), no los contadores de la página. Un dato puede ir unos minutos por detrás del sello y la etiqueta lo dice con su edad; lo que no puede es retroceder ni cuadrar una firma falsa.

### Lo que está bien y conviene no tocar

- El HTML va a red primero en el service worker (la web vieja ya no se queda pegada).
- Guardia de versión: `version.json` y `meta kukia-build` coinciden (`v1.0.9`), sin bucles de recarga.
- `wallet.html` solo guarda en `localStorage` la dirección y la URL del Core; **ninguna clave privada**.
- El zip publicado (119 ficheros) **no contiene** claves, identidades, tokens ni billeteras.
- `docs/` no publica la clave del relé (solo aparece el nombre de la variable en `codigo.html`, que es el código fuente).
- Relé: solo lectura por GET; `POST /api/publicar` exige `Bearer`, `content-length` y ≤256 KB. Probado: 401 sin clave, saneado de series, ancla firmada con legado.
- Publicador cada 60 s = 1.440 escrituras/día frente al cupo de 2.500 (la pausa del 20-10 fue por publicar cada 5 s).

---

## 5. Hallazgos por severidad

### P0 — resuelto hoy
1. **Minero parado 16 h** con 121 tareas en cola. Relanzado; guardia en marcha.

### P1 — pendiente, requiere decisión o ventana de mantenimiento
2. **Crecimiento sin poda de la base.** `planificador_log`: 155.114 filas (+31 k/día) sin borrado; `gpu_telemetria`: 66.898 filas; `nonces_pendiente`: 1.021. DB de 142–149 MB. A este ritmo, ~1 GB al mes. Arreglo: poda por antigüedad en el hilo de archivado (p. ej. 7 días de log del planificador y 7 de telemetría), conservando `meta` y el ledger.
3. **Disco al 4,9 %** (88 GB libres de 1,8 TB en C:). No lo causa KUKIA (backups 431 MB), pero `tools/salud.py` lo marca como FALLO y un ledger que no puede escribir es la red parada.
4. ~~**La web depende del relé.**~~ **Resuelto el 21-10:** `docs/metricas_snapshot.json` se escribe cada minuto y se sirve desde el propio dominio; la portada cae a él si el relé falla, y a `localStorage` si tampoco hay instantánea. Ver §4.1.
5. **Sin repositorio Git en esta carpeta.** `git rev-parse` falla en `KUKIA/`; por tanto no hay copia en GitHub Pages verificable desde aquí. El whitepaper ya dice «aún no hay repositorio público de Git», así que el texto es correcto, pero no existe red de seguridad de versiones.

### P2 — menor
6. `saldos_cuenta` guarda polvo flotante (`2.22e-16`) en una cuenta; cosmético.
7. La cifra «Tareas por minuto» tarda un ciclo en aparecer (necesita dos lecturas).
8. `entrega rechazada: 500 error interno en el pago` esporádico en logs antiguos, no reproducido.
9. El premine de 15 M no tiene calendario de bloqueo/vesting declarado; solo figura el importe y la dirección.

---

## 6. Comprobaciones ejecutadas (con resultado)

| Comando | Resultado |
|---|---|
| `python -m py_compile` (11 ficheros clave) | OK, exit 0 |
| `python tools/pruebas_seguridad.py` | **93/93 OK**, exit 0 (un primer arranque falló sin log; no reproducible) |
| `python publico/verificar_sellos.py` | OK: 158.263 asientos enlazados, 70 sellos |
| `node publico/probar_relay.mjs` | OK: ancla, 401, saldo, serie saneada |
| `python tools/salud.py` | 1 FALLO: disco 4,9 % (ledger escribiendo, 0 sellos pendientes) |
| `curl /stats` del Core | 200, supply 15.084.126,99, 26 nodos, cola 121 |
| `curl /api/metricas` del relé | 200 con ancla firmada, `ts` de hace 26 s |
| Medición en navegador (1280/390/320) | 0 desbordes, sin scroll horizontal |
| `surge ./docs kukia-network.surge.sh` | Publicado; `version.json` = `v1.0.9-numeros-encajados` en vivo |

---

## 7. Ficheros modificados hoy

- `docs/index.html` — encaje de cifras, versión 1.0.9, nota del premine, FAQ honesta, tarjeta 100M.
- `docs/whitepaper.html` — pago por tarea real y redundancia.
- `publico/generar_web.py` — sin cambios (regeneró `version.json`, `codigo.html`, zip, SEO).
- `publico/publicar_metricas.py` — escribe `docs/metricas_snapshot.json` en cada vuelta (con el ancla firmada) y lo publica con freno; despliegue urgente cuando el relé falla.
- `docs/index.html` — verificador Ed25519 del sello, anti-retroceso y clave pública fijada.
- `docs/metricas_snapshot.json` — nuevo: instantánea pública del mismo origen.
- `publico/ARRANCAR_METRICAS.cmd` — texto «cada minuto».
- `AUDITORIA_FINAL_2026-10-21.md` — este informe.

**Publicado:** `kukia-network.surge.sh` (v1.0.9). Procesos activos: Core (uvicorn 8300), minero (2 GPUs), publicador (60 s) y guardia.

---

## 8. Recomendación para cerrar la fase

1. Poda de `planificador_log` y `gpu_telemetria` (P1, ~30 líneas en el hilo de archivado).
2. ~~Instantánea estática de respaldo servida desde el mismo origen.~~ Hecho el 21-10 (v1.1.0).
3. Repositorio Git real + copia en GitHub Pages como espejo no canónico.
4. Declarar el premine con bloqueo/vesting en el whitepaper (hoy solo el importe).
5. Auditoría de seguridad externa antes de abrir el minado público: es el único punto que este informe no puede cubrir.

---

## 9. Seguridad del paquete descargable (21-10, tarde)

El minero que se descarga guardaba en `ledger/wallet/*.json` la **clave privada
Ed25519 y las 20 palabras en claro**. Quien copiara esa carpeta (backup, USB,
otro programa) se llevaba la llave entera. Cambiado:

| Antes | Ahora | Cómo se comprobó |
|---|---|---|
| `clave_privada` y `mnemonico` en claro en el JSON | cifrados con DPAPI (`clave_privada_cifrada`, `mnemonico_cifrado`), atados al usuario de Windows | crear billetera y mirar el fichero: `clave_privada` ausente, `cifrado=dpapi-usuario-windows` |
| Fichero legible por cualquier cuenta del PC (en Windows no se hacía nada) | `icacls /inheritance:r` + solo el usuario actual | `icacls` del JSON: `kukipcwin11\kuki:(R,W)` y nada más |
| Billetera copiada a otro PC = llave utilizable | DPAPI no descifra; se reintenta con `RECUPERACION_*.txt` y, si no, se para con mensaje claro | fichero manipulado → error explicado; con recuperación → misma clave y mismas palabras |
| Zip construido a mano (un despiste publica una llave) | `generar_web.py` **aborta** si entra una billetera, `.db`, log, token o recuperación | 151 ficheros; `wallet/`, `.db`, `.log` → lista vacía |
| Sin forma de verificar la descarga | `kukia-node.zip.sha256` publicado y huella visible en `codigo.html` | descarga real del dominio: `sha256sum -c` **OK** |

Billeteras reales migradas (13 ficheros, ninguna con clave en claro). El minero
se reinició, descifró su billetera y sigue cobrando (105 a 202 tareas/min según
el momento y la mezcla de tareas; muestras de 12 s y de 5 min). Suite completa: **93/93, exit 0** (hubo que arreglar
`pruebas_seguridad.py`, que leía `clave_privada` del JSON a pelo y con el cifrado
se quedaba sin billetera de prueba).

**Prueba de extremo a extremo del paquete:** descargado del dominio, `sha256sum -c`
OK, extraído en una carpeta limpia, `instalar_minero.py` → «LISTO PARA MINAR»
(2 GPU, torch, Core), y el worker arrancado 50 s desde esa copia: tareas pagadas,
billetera creada cifrada, clave y palabras **no** aparecen en el log.

### Publicación

Desplegado en `kukia-network.surge.sh` (v1.2.0). Verificado por HTTP: índice,
`codigo.html` (588 KB), `kukia-node.zip` (257.990 B), `.sha256`, `version.json`,
`sitemap.xml`.

**Lo de Google no se puede cumplir en este dominio.** Surge sirve su propio
`robots.txt` con `Disallow: /` en los subdominios `*.surge.sh` y **ignora** el
que subimos (verificado: pide 80 B, sirve 26 B). Es política suya, no un fallo
nuestro (sintaxi/surge#288: «no permitimos indexación en nuestro dominio con
contenido de otros usuarios»). Para aparecer en Google hace falta un dominio
propio apuntando a Surge (CNAME) o publicar en GitHub Pages, y luego dar de alta
el sitio en Search Console. El `sitemap.xml` y las etiquetas ya están listos para
ese día.

### Ficheros de esta pasada

- `ledger/kukia_wallet.py` — DPAPI en reposo, migración automática, `cargar_billetera`, `icacls`.
- `worker/kukia_worker.py` — `icacls` para el fichero de recuperación y parada clara si la billetera no se puede descifrar.
- `tools/rescatar_saldos.py` — usa `cargar_billetera` (antes una billetera cifrada parecía «sin clave»).
- `tools/pruebas_seguridad.py` — igual, para que la suite siga probando transferencias.
- `publico/generar_web.py` — lista negra de ficheros, `.sha256` con salto LF y huella en la página.
- `SEGURIDAD.md` — sección «Paquete descargable y claves en reposo».
- `docs/` — `kukia-node.zip` (151 ficheros, sin secretos), `.sha256`, `codigo.html`, versión.

---

## 10. Segunda copia publica, esta si indexable (21-10)

Surge no deja que Google rastree nada bajo `*.surge.sh` (ver seccion 9). Asi que
la web vive ahora tambien en un sitio de Freebuff, cuyo `robots.txt` **no tiene
ninguna regla** (solo comentarios de senales de contenido), es decir: sin
`Disallow`, indexable.

**https://site-1ea296595daa4ba7935f1574ab9a5a13.freebuff.page** (produccion)

Para que Google no vea dos copias compitiendo, la canonica es **una** y apunta a
la copia indexable. `publico/generar_web.py --canonico <url>` escribe el
`<link rel="canonical">` de las 5 paginas, el `sitemap.xml` y el `robots.txt`;
por defecto ya usa el dominio indexable.

| Comprobacion | Resultado |
|---|---|
| `/` `/codigo` `/minar` `/wallet` `/whitepaper` | 200 (81 KB / 588 KB / 14 KB / 22 KB / 8 KB) |
| `/kukia-node.zip` | 200, 259.412 B, hash identico al local |
| `/sitemap.xml`, `/robots.txt`, `/version.json` | 200 y contenido correcto |
| Canonica servida | `…/codigo` (forma final, sin `.html`) |
| Como lo ve Googlebot (UA de Googlebot) | 200 con el `<title>` real, **sin** `X-Robots-Tag`, sin reto de Cloudflare |
| En el navegador | pagina completa con datos en vivo del rele (`/api/metricas` 200), cero errores de consola |

### Dos trampas reales del publicador (documentadas para no repetirlas)

1. **Los ficheros tienen que estar dentro del workspace del hilo.** Este hilo
   trabaja en el checkout real (`Desktop\KUKIA`) por ruta absoluta, pero
   `sites_deploy` solo acepta rutas de su propia carpeta de hilo. Con
   `.deploy/` fallaba con «Could not prepare the Sites request» sin decir por
   que; con ruta absoluta ya avisaba «Site files must be inside this thread's
   workspace». Solucion: `publico/desplegar_sitio.py` prepara la carpeta y se
   copia a `deploy_site/` dentro del hilo antes de publicar.
2. **El sitio sirve URLs limpias**: `/codigo.html` responde **307** a `/codigo`.
   La canonica y el sitemap tienen que usar la forma final (`url_limpia()`), o
   mandariamos a Google a una redireccion temporal.

### Lo que falta para Google, y por que no lo puedo hacer yo

El ping anonimo de sitemaps esta muerto (Google: 404, Bing: 410), asi que el
unico camino es Search Console, y eso exige **la cuenta de Google del dueno**
(nadie puede verificar una propiedad ajena). Pasos, ya con todo preparado:
Search Console -> propiedad de **prefijo de URL** con la direccion del sitio ->
verificar (basta el fichero HTML que da Google, se sube al sitio) -> Sitemaps ->
enviar `sitemap.xml`. Indexar tarda entre horas y semanas.

---

## 11. Falso positivo de integridad que habria parado los pagos (21-10)

Al reejecutar la suite para cerrar el despliegue, el Core de la copia arranco con
**«LEDGER: EL HISTORIAL NO CUADRA — sello 91 (hasta 196120)»** y `exit 3`. No fue
el despliegue: era el ledger real. Con `KUKIA_SELLO_BLOQUEA=1`, un ledger asi
**bloquea todos los pagos al reiniciar** (`_asiento` lanza 503 si `LEDGER_ROTO`).
El Core en marcha seguia pagando porque `LEDGER_ROTO` se fija solo al arrancar:
la trampa estaba armada para el siguiente reinicio.

**No habia ninguna alteracion.** El sello 91 cuadraba en hash, asientos y suma
(comprobado asiento a asiento: 195.945 asientos, 15.084.442,6814 KIA, hash
correcto). El fallo era de la **verificacion**:

| Donde | Como sumaba |
|---|---|
| Al firmar el sello (`estado_ledger`) | `SELECT SUM(kuki) ... WHERE id<=hasta` |
| Al verificar (`verificar_sellos`) | `acum += kuki` fila a fila, redondeando en cada paso |

Sumar en coma flotante en dos ordenes distintos da diferencias de 1e-4: en el
asiento 196120 Python daba `15084442.6813` y SQLite `15084442.6814`. La
comparacion era exacta, asi que el sello se daba por roto. Con la emision
creciendo, esto habria ido marcando sellos nuevos y, en el siguiente reinicio,
**pagos bloqueados con los sellos ya firmados y sin poder reescribirlos**.

### Arreglo

1. `estado_ledger()`: asientos y suma salen de **una sola consulta** acotada a
   `id<=hasta`. Antes el conteo no tenia cota: si otro hilo insertaba un asiento
   entre las dos consultas, el sello nacia con un asiento de mas.
2. `verificar_sellos()`: la suma acumulada se calcula con **ventana de SQL**
   (`SUM(kuki) OVER (ORDER BY id ROWS UNBOUNDED PRECEDING)`), el mismo motor y
   el mismo orden que usa el sello. La comparacion vuelve a ser exacta **y**
   sigue siendo estricta: no se relajo ninguna tolerancia.
3. Regresion permanente en la suite (`prueba_sellos`): 5.000 asientos de
   0,007797 KIA (donde el redondeo se nota), un sello que tiene que cuadrar, el
   ledger creciendo despues y un asiento cambiado que **tiene** que invalidarlo.
   Si alguien vuelve a "arreglarlo" con una tolerancia, esa prueba lo delata.

### Verificado

| Comprobacion | Resultado |
|---|---|
| Suite completa | **96/96, exit 0** (93 anteriores + 3 de sellos) |
| Verificador sobre el ledger real | `ok: True`, 0 problemas, 94 sellos |
| Core reiniciado (pid nuevo) | `sellos_ok True`, `pagos_bloqueados False`, sin problemas |
| Mineria tras el reinicio | +59 tareas en 20 s (177/min), 32 nodos, pagos activos |
| Tiempo de parada del Core | 42 s hasta volver a responder; el minero reintento solo |

---

## 12. Causa de los 404 y 500 esporadicos con el nodo existiendo (21-10)

**Causa: una sola conexion SQLite compartida por todos los hilos del Core.**

En SQLite, un `ROLLBACK` (o un `BEGIN IMMEDIATE`) ejecutado en un hilo **corta
las sentencias en vuelo de esa conexion**, aunque las haya lanzado otro hilo. El
`fetchone()` de la otra peticion vuelve `None` **sin lanzar nada**. Resultado:

| Sintoma | De donde salia |
|---|---|
| `404 nodo no registrado` con el nodo existiendo (118 en `/tareas/pendiente`, 62 en el pago, 344 en replicas) | el `SELECT * FROM nodos` de esa peticion se cortaba a mitad de un ROLLBACK ajeno |
| `404 tarea no encontrada` con la tarea existiendo | lo mismo en el `SELECT` de la tarea |
| `500 error interno en el pago` (`TypeError: 'NoneType' object is not subscriptable`) | `task2` daba `None` dentro de la transaccion de pago |

Cada 409/503 que hace ROLLBACK (doble pago, tarea reasignada, nodo castigado...)
podia tirar por tierra la lectura de otra peticion que estuviera en ese instante.
De ahi que fuera esporadico: ~2 de cada 1.000 y nunca el mismo nodo.

### Prueba de la causa

Reproducido en aislado, con una conexion compartida y dos hilos (uno leyendo
filas que existen y otro haciendo `BEGIN`/`ROLLBACK` en bucle):

```
lecturas=42324  devueltas None=2877 (6,8 %)  excepciones=8521
```

Y reproducido el 500 exacto contra un Core de verdad:
[tools/prueba_pago_robusto.py](tools/prueba_pago_robusto.py) crea una tarea, la
asigna, lanza el pago y borra la fila en la ventana de espera -> antes:
`500 {"detail":"error interno en el pago"}`; ahora:
`409 la tarea ya no existe... no reintentes` (exit 0).

### Arreglo (en el servidor)

1. **Una conexion SQLite por hilo** (`_ConSegura` con `threading.local()`). El
   rollback de un hilo ya no toca la sentencia de otro. Las escrituras siguen
   serializadas con `_pago_lock` + `BEGIN IMMEDIATE` y `busy_timeout=30 s`.
   El modo de diario se queda en DELETE a proposito: con WAL el `.db` no se
   podria copiar suelto y las copias de seguridad actuales se romperian.
2. **`task2` inexistente -> 409**, no 500. La tarea puede desaparecer entre la
   primera lectura y la transaccion (la recuperacion la cierra por abandono y el
   archivador la borra por antigua). No hay nada que pagar y se dice asi.
3. **Replica tardia -> 200 `guardada:false`**, no 404. La fila de la replica se
   borra en cuanto la tarea deja de ejecutarse; el verificador que llega tarde
   no ha hecho nada malo. Era el 65 % de los 404 del Core.
4. **La poda de nodos mide la ultima actividad, no el registro**: un nodo 7 h
   registrado, pidiendo trabajo cada segundo y sin cobrar aun (tareas lentas o
   solo replicas) se borraba mientras trabajaba y el siguiente `/tareas/pendiente`
   ya daba 404 con el nodo vivo.

### Verificado en el servidor

| Comprobacion | Antes | Despues |
|---|---|---|
| Peticiones medidas | 74.000 (2 dias) | **11.567** (desde el reinicio) |
| `404` | 526 (0,7 %) | **0** |
| `500` del pago | 18 | **0** |
| `503` / `database is locked` | — | **0** |
| Mineria | ~165/min | **141/min** con 32 nodos y cola 109 |
| Integridad | — | `cadena_ok True`, `sellos_ok True`, 102 sellos, 0 problemas |

Suite completa **96/96, exit 0** despues de los cambios, y
`tools/prueba_pago_robusto.py` en exit 0.

**Nota operativa:** el Core en marcha no era el del guardia sino uno lanzado
desde `%TEMP%\opencode\lanzar_core.cmd`, que escribia en
`C:\Users\kuki\Desktop\Fotos\core.log`. Eso explica que `logs/core.log` se
quedara parado: se estaba mirando el log equivocado. Ahora corre bajo el guardia
(`pid` hijo de `guardia.py`) y escribe en `logs/core.log`.

**Nota sobre la suite:** en 4 ejecuciones seguidas, una murio porque el Core
aislado no llego a escribir ni una linea en su log (ni traceback) con el mismo
binario arrancando bien a mano segundos antes. Se anadio un segundo intento con
puerto nuevo y el codigo de salida en el mensaje: si el Core falla de verdad
(import roto, base ilegible) fallan los dos intentos y la prueba se cae igual.
Con eso, dos pasadas completas seguidas: 96/96, exit 0.

---

## 12. Tunel publico con direccion estable y paquete que no caduca (21-10, tarde)

**El problema real.** El zip llevaba `http://127.0.0.1:8300` en
`core_minero.txt`: quien lo descargara minaba contra su propio PC y no
encontraba ningun Core. Y el tunel rapido de Cloudflare cambia de nombre en cada
arranque, asi que meter la URL del tunel en el paquete lo dejaba caducado a la
primera caida.

**La solucion montada.**

- `tools/tunel_estable.py` — abre el tunel, **espera a que sirva** (`/stats` por
  la URL publica, no solo que la URL exista), escribe `docs/core_actual.json` y
  lo publica en el espejo. Se queda vigilando y, si el tunel muere, lo relanza y
  republica la direccion nueva. Cerrojo en el puerto local 8399: nunca dos
  tuneles a la vez.
- `tools/core_destino.py` — resuelve la direccion del Core para todos los
  programas. Acepta URL directa o `auto <espejo>`, valida el esquema (rechaza
  `http://` remoto salvo `KUKIA_CORE_INSEGURO=1`), cachea 60 s y guarda la
  ultima direccion buena en `logs/core_actual_cache.txt` por si el espejo cae.
- Enganchado en `worker/kukia_worker.py` (con un hilo que detecta si el Core se
  muda, sin reiniciar el minero), `validator/kukia_validator.py`,
  `tools/guardia.py`, `tools/instalar_minero.py`, `tools/saldo_minero.py` y los
  tres `.cmd` que leian el fichero a mano.
- El zip **no** copia el `core_minero.txt` de este PC: `generar_web.py` escribe
  uno propio con `auto https://kukia-network.surge.sh/core_actual.json`.
- El tunel es hijo del guardia: sobrevive a reinicios del PC sin abrir nada.

**Pruebas hechas (no de laboratorio).**

| Prueba | Resultado |
|---|---|
| Paquete descargado del espejo, carpeta limpia, `core_destino.py --url` | resolvio la URL del tunel y `/stats` **200** con 32 nodos |
| Matar el `cloudflared` con todo en marcha | detectado en <45 s, tunel nuevo, `core_actual.json` republicado y verificado |
| Volver a resolver con **el mismo paquete sin tocar nada** | cogio la URL nueva y `/stats` **200** (`tareas_ok` 205.987 creciendo) |
| `http://` remoto en `core_minero.txt` | rechazado, exit 2, con el motivo en castellano |
| Mineria tras el cambio | 96 tareas/min con un solo minero (32 en 20 s) |

**Defecto encontrado y arreglado de paso (importante).** Al reiniciar la
guardia se duplicaron minero, validador, publicador y panel. Causa:
`_ya_corriendo()` consultaba solo `Name='python.exe'`, y la guardia silenciosa
arranca a sus hijos con `pythonw.exe`. Resultado: dos mineros repartiendose el
cupo de 4 tareas, es decir la mitad de rendimiento, sin que nada avisara. Ahora
consulta `python.exe` **y** `pythonw.exe`; verificado: tras reiniciar la guardia
responde «ya esta corriendo (abierto aparte): no se duplica» para los cinco
hijos y queda una sola instancia de cada uno.

**Lo que esto NO es.** No es un dominio: `trycloudflare.com` es anonimo y
compartido. Es una direccion estable, suficiente para que la red funcione hoy,
no para que un exchange o un auditor la tomen por infraestructura propia. El
paso a `https://kukia.tudominio.com` (cuenta gratuita de Cloudflare + dominio de
~10 EUR/ano) esta escrito en `TUNEL_ESTABLE.md` y no obliga a tocar codigo:
`core_destino.py` acepta las dos formas.

---

## 12. El paso a dominio propio, preparado y probado (21-10)

Lo de la seccion 11 era una direccion estable, no un dominio. Ahora el paso a
`https://kukia.tudominio.com` esta **hecho y verificado hasta donde se puede sin
comprar el dominio**: falta unicamente el dominio y autorizar Cloudflare.

### Un script, tres pasos

    PREPARAR_DOMINIO.cmd      (una vez: login, crear tunel, DNS, config)
    ARRANCAR_TUNEL.cmd        (elige solo: dominio si esta preparado, si no rapido)
    python tools\tunel_dominio.py --estado

`tools/tunel_dominio.py` es idempotente (`--preparar` se puede repetir sin
duplicar tuneles ni registros) y escribe `tools/cloudflared/config.yml` +
`dominio_core.txt`. La guardia (`_tunel_con_dominio()`) exige **los dos**
ficheros: si la preparacion quedo a medias sigue usando el tunel rapido, que
funciona.

### Tres defectos que se encontraron midiendo, no leyendo

1. **403 «Host no permitido».** El Core valida el Host y solo acepta loopback.
   Comprobado contra el Core vivo: `Host: 127.0.0.1:8300` -> **200**,
   `Host: kukia.tudominio.com` -> **403**. Un tunel con nombre manda el dominio
   como Host, asi que sin `originRequest.httpHostHeader: 127.0.0.1` el tunel
   conecta, el DNS resuelve, y no funciona. Ahora es una linea fija del config
   generado y `--estado` avisa si ve un 403.
2. **`cloudflared tunnel ingress validate --config X` devuelve 0 sin validar.**
   El `--config` tiene que ir **antes** del subcomando
   (`cloudflared tunnel --config X ingress validate`); detras, cloudflared
   contesta `flag provided but not defined` y sale con 0. La validacion era
   decorativa. Corregido y comprobado: un config sin regla final ahora devuelve 1.
3. **Arrancar por nombre exige `cert.pem`.** `cloudflared tunnel run kukia`
   falla con `error parsing tunnel ID: Error locating origin cert` si se pierde
   la sesion, aunque el config este bien. Se arranca por **UUID** (el del
   config): solo necesita la credencial. Verificado: con UUID llega directo al
   error de credencial, sin pedir cert.

### Lo verificado

| Prueba | Resultado |
|---|---|
| Config generado con `cloudflared tunnel ingress validate` | `OK` |
| Regla que casa con `https://dominio/stats` | `Matched rule #0` -> `http://127.0.0.1:8300` |
| Regla para cualquier otro host | `Matched rule #1` -> `http_status:404` (no se publica nada mas del PC) |
| Config roto (sin regla final) | `Validation failed`, codigo 1 |
| Eleccion de la guardia: sin dominio / a medias / completo | `tunel_estable.py` / `tunel_estable.py` / `tunel_dominio.py` |
| `_ya_corriendo` reconoce cualquiera de los dos tuneles | `True` con el rapido vivo, `False` con un nombre inventado |
| Dispatch de `ARRANCAR_TUNEL.cmd` en los tres estados | A y B -> `[modo RAPIDO]`; C -> `[modo DOMINIO]` |
| `--preparar` sin sesion de Cloudflare | pide autorizar en el navegador o da la orden exacta para hacerlo a mano; exit 2 con dominio invalido |
| `--arrancar` sin preparacion | mensaje con el comando exacto, exit 2 |
| `_prohibido()` del empaquetador con el config del tunel | sigue abortando: `tools/cloudflared/`, `.cloudflared` y `dominio_core.txt` estan en la lista negra |

**Lo que no se ha podido probar:** el camino publico completo (DNS real +
Cloudflare sirviendo el dominio), porque no hay dominio ni cuenta autorizada en
este PC. `--preparar` fue ejecutado y llego a abrir la autorizacion; al no
completarse, no se escribio `cert.pem` ni ningun estado a medias. El resto del
camino esta cubierto por las piezas de arriba, y `--estado` es precisamente lo
que lo confirma en un minuto el dia que exista el dominio.

### Lo que NO cambia

El paquete descargable sigue con `auto https://kukia-network.surge.sh/core_actual.json`:
los dos supervisores escriben el mismo `docs/core_actual.json`, asi que pasar a
dominio propio **no obliga a reempaquetar ni a que nadie reinstale**. La
credencial del tunel no sale de `%USERPROFILE%\.cloudflared`; el config del repo
lleva solo UUID y ruta.

---

## 13. Buscadores: por que no sale KUKIA y que queda listo (21-10)

### El hallazgo

Ninguna pagina de KUKIA esta indexada. `site:kukia-network.freebuff.page` en
Google devuelve **cero resultados**. La causa es doble y ninguna es el codigo:

- `kukia-network.surge.sh` responde 200 pero Surge sirve **su** `robots.txt` con
  `Disallow: /` en `*.surge.sh` e ignora el nuestro (politica suya).
- La copia canonica (`site-1ea29659….freebuff.page`) responde **503**: `Sites`
  esta en pausa («Hosting usage is being checked. Sites will resume when
  monitoring is available»), igual en preview y en produccion, con los recibos
  de deploy en `succeeded`. No se puede arreglar desplegando otra vez.

### Lo que estaba mal generado (y ahora se genera)

| Antes | Consecuencia | Ahora |
|---|---|---|
| `og:image` relativa (`moneda.jpg`) | sin vista previa al compartir; los rastreadores no la resuelven | URL absoluta |
| `og:url` no existia | el `canonical` podia decir una cosa y Open Graph otra | identicos por construccion |
| sitemap con `lastmod` = hoy en cada publicacion | señal que Google acaba ignorando | fecha real del fichero, mas `priority` |
| cero datos estructurados | sin entidad reconocible | JSON-LD `WebSite` + `Organization` + `WebPage` enlazados |
| etiquetas a mano por pagina | la pagina nueva se quedaba sin ellas | las inyecta `generar_web.py` en cada vuelta (idempotente) |
| ningun aviso a buscadores | esperar a que pasen | IndexNow: clave publicada y `tools/indexar.py --enviar` |

### Verificado

| Prueba | Resultado |
|---|---|
| `docs/` servido en local con URLs limpias (como el host) | 5/5 paginas 200, `--estado` todo OK, exit 0 |
| JSON-LD por pagina | JSON valido, 3 nodos, `@id` enlazados |
| `canonical` == `og:url` | 5/5 |
| Regenerar dos veces | `robots`/`og:url`/`ld+json` siguen a 1 por pagina |
| Clave IndexNow servida | 200 y contenido correcto |
| `--estado` contra el host real | 503 en las 5, dice por que, exit 1 |
| `--enviar` contra el host real | **se niega a enviar**, exit 1 |
| Ficheros de verificacion (`--token-google/bing/yandex`) | generados con el contenido que espera cada panel |
| `desplegar_sitio.py` con el SEO nuevo | 30 ficheros, 6,7 MB; clave, robots y sitemap dentro |

**No verificado:** el envio real a IndexNow y el alta en los paneles, porque
exigen el sitio sirviendo 200 y una cuenta. La guia con el paso a paso por motor
(el que hereda de quien y el que hay que dar de alta) esta en
`SEO_BUSCADORES.md`.

### La unica compra que desbloquea las dos cosas

Un dominio propio (~10 EUR/ano): en Surge con CNAME se sirve **tu** `robots.txt`
(el `Disallow: /` es solo de `*.surge.sh`), asi que Google puede entrar; y de
paso es la URL fija del Core, que ya espera en `PREPARAR_DOMINIO.cmd`. Con eso,
el "salir en todos los buscadores" deja de depender de la plataforma.

---

## 14. Ventanas en pantalla, y la web otra vez viva (21-10, tarde)

### El problema que veia el usuario: ventanas negras a todas horas

No era el navegador ni un virus: eran tres programas de consola lanzados desde
procesos que corren en segundo plano, sin `CREATE_NO_WINDOW`. En Windows cada
hijo de consola abre **su propia** ventana, aunque el padre sea `pythonw` (sin
consola). Los tres, encontrados midiendo:

| Sitio | Frecuencia real | Que se veia |
|---|---|---|
| `tools/guardia.py`, dos consultas de `powershell` (¿ya esta corriendo?) | cada 20-30 s por hijo | ventana negra varias veces por minuto, todo el dia |
| `worker/kukia_worker.py`, `nvidia-smi` de la telemetria | **cada 3 s** | parpadeo constante mientras se mina |
| `publico/publicar_metricas.py`, `cmd /c surge` | cada 20 min | ventana que se queda hasta que sube el sitio |
| Programador de tareas: `TAREA_BACKUP.cmd` a las 03:00 | una vez al dia | ventana de CMD a las 3 de la mañana |

Arreglo: `creationflags=0x08000000` (CREATE_NO_WINDOW) en los tres primeros, y
la copia diaria pasa a lanzarse desde `TAREA_BACKUP_OCULTA.vbs` (`shell.Run ...,
0, True`), que es la unica forma de que un `.cmd` no abra consola. La tarea
`KUKIA Backup Ledger` se re-registro apuntando al `.vbs` (XML de la anterior
guardado en `backups/tarea_backup_ledger.xml`).

### El vigilante, tambien sin ventanas

`tools/vigilante_web.py` + tarea `KUKIA Vigilante Web` cada 10 min con
`pythonw.exe`. Verificado de verdad, no solo creado: se lanzo la tarea a mano
(`schtasks /Run`) y el log gano la linea correspondiente. No publica el sitio;
avisa (notificacion de esquina) y empuja a IndexNow cuando el sitio esta listo.

### La web volvio, y ya esta en la cola de los buscadores

Entre la seccion 13 y esta, `Sites` reanudo el servicio solo: a las 18:11 el host
respondia 200 sirviendo la version vieja. A las 18:16 se publico la version con
el SEO generado y a las 18:17 el vigilante (recien instalado) detecto el estado
`listo` y mando las cinco URLs:

```
ACEPTADO (HTTP 202). Bing/Yandex/Seznam/Naver lo tendran en su cola en horas o dias.
```

### Verificado

| Prueba | Resultado |
|---|---|
| 5 paginas en vivo | 200, con `<!-- kukia-seo -->` y `og:url` absoluto |
| `/<clave>.txt` en vivo | 200 con la clave |
| `robots.txt` en vivo | 200, `Allow: /` y sitemap declarado |
| `indexar.py --estado` en vivo | 5/5 `[OK ]`, exit 0 |
| IndexNow | `ACEPTADO (HTTP 202)` |
| Tarea del vigilante | creada y **ejecutada** a mano: el log crecio |
| `_ya_corriendo` y `_gpu_stats` con la bandera | funcionan igual (True / `(4, 3363)`) sin ventana |
| Copia diaria | la tarea apunta ya a `wscript.exe ... TAREA_BACKUP_OCULTA.vbs`; el `.vbs` probado con un `.cmd` inofensivo (codigo 0, sin ventana) |

**Lo que sigue en manos del usuario:** las tres altas manuales (Search Console,
Bing Webmaster, Yandex Webmaster). Los ficheros de verificacion se generan con
`python tools\indexar.py --token-google/bing/yandex <token>`.

**Lo que no se puede afirmar todavia:** que KUKIA aparezca en Google. IndexNow
(202) es una promesa de que lo miraran, y Google no usa IndexNow. Eso depende de
Search Console y de dias, no de codigo.

---

## 15. Verificacion de Google: el 307 que la habria roto (21-10)

Search Console deja un fichero `google<token>.html` en la raiz. Al publicarlo
aparecio el problema: el host responde **307** de `/google<token>.html` a
`/google<token>` (URLs limpias), y los verificadores piden la ruta **exacta**
con un **200**. Colocado tal cual, lo mas probable es que la verificacion
fallara sin decir por que.

Arreglado por dos vias a la vez, para no depender de una:

1. **`worker.js`** (que genera `publico/desplegar_sitio.py`): si la peticion es
   de un `googleXXXX.html` o `yandex_XXX.html` en la raiz, se pide el asset sin
   extension y se devuelve su contenido con un **200**, sin redireccion.
2. **Etiqueta meta**: `generar_web.py` lee los ficheros `google*.html` que haya
   en `docs/` y pone `<meta name="google-site-verification" content="...">` en
   todas las paginas. Asi la verificacion por «Etiqueta HTML» tambien funciona,
   y el token no se escribe a mano en ningun sitio.

De paso: los ficheros de verificacion se excluyen del `sitemap.xml` (no son
paginas) y `tools/indexar.py` gana `--buscar` y `--archivo <ruta>` para coger el
fichero descargado sin teclearlo, con comprobacion de nombre y contenido.

Verificado en vivo tras publicar:

| Prueba | Resultado |
|---|---|
| `/google9d6cb22812b81092.html` sin seguir redirecciones | **200**, `text/html`, contenido exacto |
| Redireccion en esa ruta | ya no hay (`redirect_url` vacio) |
| `<meta name="google-site-verification">` en la portada | presente |
| `sitemap.xml` | 5 paginas, sin el fichero de verificacion |

## Blindaje del comando de verificacion (token pegado tal cual)

Pegar en `--token-google` la cadena completa que muestra Search Console
(`google9d6cb22812b81092`, con o sin `.html`, o incluso la URL entera) antes
generaba un fichero mal nombrado (`googlegoogle...html`), que Google rechaza.
Ahora `normaliza_token()` acepta las cuatro formas y deja siempre el nombre
correcto: `google<token>.html`.

Ademas hay comprobacion previa al boton:

    python tools/indexar.py --comprobar-google

Sale con 0 solo si la URL exacta responde 200 y la portada lleva la etiqueta
meta. Asi se sabe si "Verificar" va a pasar antes de pulsarlo.
