# Túnel público al Core, con dirección estable

El Core vive en tu PC (`127.0.0.1:8300`). Para que alguien de fuera pueda minar
hace falta una dirección pública. Este documento explica cómo está montado,
por qué está montado así y qué falta el día que quieras una URL de verdad con
tu nombre.

## En una frase

El túnel cambia de nombre cada vez que se abre (`https://algo.trycloudflare.com`).
Lo que **no** cambia es una dirección fija en el espejo de la web:

```
https://kukia-network.surge.sh/core_actual.json
```

Ese fichero dice en cada momento cuál es la URL viva. El paquete que se
descarga (`kukia-node.zip`) apunta **ahí**, no al túnel:

```
core_minero.txt
  auto https://kukia-network.surge.sh/core_actual.json
```

Así, cuando el túnel se renueva, el paquete que ya se bajó la gente sigue
valiendo sin tocar nada.

Hay **dos modos** y el paquete no distingue entre ellos, porque los dos
escriben el mismo `core_actual.json`:

| Modo | URL | Cuándo |
|---|---|---|
| Rápido (`trycloudflare`) | cambia al arrancar | por defecto, sin cuenta ni dominio |
| Con nombre (dominio propio) | fija, tuya | el día que tengas dominio; ver más abajo |

## Decisión del 28-10-2026: seguir con el túnel rápido (y por qué no ngrok)

Esto está aquí para no volver a proponer lo mismo cada mes: qué se decidió, con
qué números y qué habría que hacer el día que se retome.

### La decisión

Se mantiene el **túnel rápido + el espejo**. No se compra dominio, no se monta
Tailscale y no se usa ngrok. El motivo es que el espejo ya resuelve el problema
que de verdad rompía cosas (que el paquete y la billetera no caduquen cuando el
túnel se renueva), y una URL fija no mueve nada de lo que hoy frena el proyecto
(demanda, mineros de fuera, réplica al día).

Lo único que se pierde: **la URL del Core cambia al reiniciar**. De ahí la regla
práctica: comparte siempre el enlace del espejo
(`https://kukia-network.surge.sh/core_actual.json`), nunca un
`algo.trycloudflare.com` pegado a mano, porque ese muere en el siguiente
arranque.

### Por qué ngrok queda descartado (medido, no opinado)

ngrok daba un subdominio fijo gratis y por eso existía `tools/tunel_ngrok.py`.
Se cae por las cuotas del plan gratuito, consultadas en su documentación el
28-10-2026 (`https://ngrok.com/docs/pricing-limits/free-plan-limits`):

| Límite del plan gratis | Valor | Qué significa en KUKIA |
|---|---|---|
| Peticiones HTTP | **20.000 / mes** | el minero hace ~240 entregas/min con 2 GPUs: se agota en **~83 minutos** |
| Transferencia | 1 GB / mes | se agota antes que las peticiones en cuanto hay imágenes o vídeo |
| Dominios | 1 dominio de desarrollo asignado | el nombre lo elige ngrok, no tú |
| Intersticial | página de aviso en tráfico de navegador | la billetera tendría que mandar `ngrok-skip-browser-warning` en cada `fetch` |

Para una demo de una tarde vale; para una red que mina, no.

### Las opciones que se evaluaron

| Opción | Coste | Límites | Veredicto |
|---|---|---|---|
| **A. Dominio + túnel con nombre de Cloudflare** | dominio ~10–15 €/año; túnel 0 € | sin cuota de peticiones; HTTPS propio | la buena cuando se retome |
| B. Tailscale Funnel | 0 € | URL fija `maquina.tailnet.ts.net`; beta; ancho de banda no garantizado; requiere cuenta e instalar Tailscale | alternativa gratis razonable |
| C. ngrok de pago | ~8 $/mes (~96 $/año) | sin cuota, pero sigue siendo un tercero | peor que A |
| D. Worker como fachada del túnel rápido | 0 € | tope diario del plan gratis (~100.000) frente a ~345.000/día del minero | solo valdría para web y billetera |

### La prueba que decide el diseño: HostGuard

Lanzado contra el Core vivo (solo lectura, 28-10-2026):

| `Host` enviado | Respuesta |
|---|---|
| `kukia.tudominio.com` (dominio propio) | **403** |
| `algo.ngrok-free.app` | **403** |
| `x.trycloudflare.com` | 200 |
| `127.0.0.1` | 200 |

Es decir: **con un dominio propio o con ngrok, el túnel abriría y el Core
contestaría 403**. El túnel con nombre lo resuelve sin tocar la lista blanca
(`coordinator/kukia_core.py`, `host_permitido`), porque su config reescribe el
`Host` a `127.0.0.1` (`originRequest.httpHostHeader`), igual que el túnel rápido
con `--http-host-header`.

### Si se retoma (plan corto)

El detalle fino está en la sección «El día que tengas dominio» de este mismo
documento. El resumen:

1. Comprar el dominio y dar de alta la zona en Cloudflare (cambiar los NS).
2. `tools\bin\cloudflared.exe tunnel login` (una vez; elige la zona).
3. `python tools\tunel_dominio.py --preparar --dominio kukia.tudominio.com`
   (crea túnel, DNS y config).
4. Parar **solo** el supervisor del túnel actual —nunca el Core— y arrancar
   `python tools\tunel_dominio.py --arrancar`; o reiniciar la guardia, que
   adopta el Core sin reiniciarlo.
5. Comprobar: `/version` desde fuera = 200, `Host` ajeno = 403,
   `netstat` solo con `127.0.0.1:8300`, `/admin` sin token = 403, y **reiniciar
   el PC** para ver que la URL no cambia.
6. Actualizar este documento y la nota de viabilidad.

### Cómo se deshace

```bash
rm dominio_core.txt                                  # la guardia vuelve al túnel rápido
tools\bin\cloudflared.exe tunnel delete kukia-core   # borra túnel y DNS
```

Sin `dominio_core.txt` (o sin `config.yml`) el guardia elige el túnel rápido en
el siguiente arranque y `core_actual.json` se reescribe solo. No hay nada que
restaurar en la base ni en las claves.

### Lo que no se toca en ningún caso

La lista de `Host` permitidos (`host_permitido`), `ORIGENES_PERMITIDOS`, el
token de admin, la tokenomics, los saldos, las claves privadas ni
`core_minero.txt` (sigue en `auto`). Si algún día se añade el dominio a la lista
blanca en vez de reescribir el `Host`, hay que hacerlo con la misma precaución
que un cambio de regla de cortafuegos: sabiendo qué se abre y pudiendo volver.

## Cómo arrancarlo

```cmd
ARRANCAR_TUNEL.cmd
```

Ese atajo hace todo esto, en orden:

1. Comprueba que el Core responde en este PC (si no, avisa y no abre nada).
2. Abre el túnel de Cloudflare hacia el Core.
3. **Espera a que el túnel sirva de verdad** (`/stats` por la URL pública, no
   solo que la URL exista).
4. Escribe `docs/core_actual.json` y lo publica en el espejo.
5. Se queda vigilando: si el túnel se cae, lo relanza y republica la nueva
   dirección sola.

Si el dominio propio ya está preparado, ese mismo atajo arranca el otro
supervisor (`tools/tunel_dominio.py`) y la URL pasa a ser fija.

Si la guardia está arrancada, **no hace falta abrir nada**: el túnel es uno de
sus hijos y lo mantiene vivo igual que el minero o el Core. La ventana de
`ARRANCAR_TUNEL.cmd` en ese caso dirá *«ya hay otro supervisor vivo»* y no
abrirá un segundo túnel.

## Comprobarlo

```cmd
python tools\tunel_estable.py --estado
python tools\tunel_dominio.py --estado
python tools\core_destino.py --url --explicar
```

El primero enseña qué dirección hay publicada y si el túnel rápido responde.
El segundo, el estado del modo con dominio: sesión de Cloudflare, túnel, DNS,
`/stats` (y avisa si ve un 403 de Host, que es el fallo silencioso de este
montaje). El tercero cuenta de dónde sale la dirección que usaría el minero. Y
por HTTP:

```bash
curl https://kukia-network.surge.sh/core_actual.json
```

## Qué protege y qué no

- El túnel **no** publica la base de datos, las billeteras ni las claves: solo
  el puerto 8300 del Core.
- `/admin` sigue pidiendo `X-Admin-Token`; sin el token no se administra nada.
- El túnel reescribe el `Host` a `127.0.0.1`, así que la comprobación de Host
  del Core sigue activa.
- Lo que sí queda público son los endpoints de minero (registro, pedir tarea,
  entregar resultado, saldo). Es el objetivo: que la red se pueda usar.
- Un `trycloudflare.com` es **anónimo y compartido**: no es un dominio de
  confianza para un exchange ni para una auditoría. Sirve para que la red
  funcione hoy.

## El día que tengas dominio: un solo script

Lo que hay montado es una **dirección estable**, no un dominio. Para tener
`https://kukia.tudominio.com` (fija para siempre, con tu nombre) hacen falta dos
cosas: un dominio (~10 €/año) y una cuenta de Cloudflare gratuita.

**Ya está preparado y probado.** El día que tengas el dominio, son tres pasos y
ninguno toca código:

### 1. Dar de alta el dominio en Cloudflare

Compra el dominio donde quieras (Namecheap, Porkbun, Cloudflare Registrar...) y
en Cloudflare pulsa *Add a site*, mete el dominio y cambia en tu registrador los
nameservers por los dos que te dé Cloudflare (`xxx.ns.cloudflare.com`). Tarda
entre minutos y unas horas en ponerse *Active*. **Hasta que diga Active, el paso
2 falla** (y lo dice claro: «ese dominio no está en la cuenta de Cloudflare»).

### 2. Prepararlo (una sola vez)

```cmd
PREPARAR_DOMINIO.cmd
```

Pregunta el dominio y hace, en orden, todo lo que antes había que hacer a mano:

1. `cloudflared tunnel login` — abre el navegador; eliges el dominio y pulsas
   *Authorize*. Es la única vez que se autoriza.
2. `cloudflared tunnel create kukia` — o reutiliza el que ya exista.
3. `cloudflared tunnel route dns kukia kukia.tudominio.com` — crea el CNAME.
4. Escribe `tools/cloudflared/config.yml` y **lo valida con cloudflared**.
5. Deja escrito `dominio_core.txt` con el dominio elegido.

Se puede repetir las veces que haga falta: no duplica nada.

### 3. Arrancarlo

```cmd
ARRANCAR_TUNEL.cmd
```

Ese atajo mira si hay dominio preparado y elige solo:

| Si existe `dominio_core.txt` + `tools/cloudflared/config.yml` | Arranca |
|---|---|
| sí | `tools/tunel_dominio.py` → URL fija |
| no | `tools/tunel_estable.py` → `trycloudflare` |

Si la guardia está arrancada no hay que hacer nada: al reiniciarse coge el modo
nuevo ella sola (el túnel es un hijo más, como el minero).

### Lo que NO hay que hacer

- **No hay que reempaquetar el zip.** El paquete sigue diciendo
  `auto .../core_actual.json` y los dos modos escriben ese mismo fichero. Quien
  ya se lo descargó no nota nada.
- **No hay que tocar `core_minero.txt` del paquete.** La indirección sigue
  siendo mejor que clavar la URL: si algún día el túnel con nombre se cae, el
  paquete sigue apuntando al sitio correcto.
- **No hay que copiar la credencial del túnel.** El `.json` con el secreto vive
  en `%USERPROFILE%\.cloudflared\` y no se mueve de ahí. El `config.yml` del
  repo solo lleva el UUID y la ruta.

### Volver al túnel rápido

```cmd
del dominio_core.txt
del tools\cloudflared\config.yml
```

La guardia vuelve a `trycloudflare` en el siguiente reinicio. El túnel con
nombre se queda creado en Cloudflare (no estorba; se borra con
`cloudflared tunnel delete kukia` si no lo quieres).

### Los tres fallos que costaría encontrar a mano

| Síntoma | Causa | Ya está resuelto así |
|---|---|---|
| El túnel conecta, el DNS apunta, y `/stats` da **403 «Host no permitido»** | El Core solo acepta Host local, y por el dominio llega `Host: kukia.tudominio.com` | El `config.yml` lleva `originRequest.httpHostHeader: 127.0.0.1`; el script lo escribe siempre y `--estado` avisa si ve un 403 |
| `error parsing tunnel ID: Error locating origin cert` | Se arrancó **por nombre** y falta `cert.pem` | Se arranca **por UUID** (el del config): solo hace falta la credencial |
| El primer arranque «no funciona» y a los diez minutos sí | El DNS aún no se ha propagado | Se espera pidiendo `/stats` de verdad, no a que cloudflared diga «conectado» |
| `cloudflared tunnel ingress validate` devuelve 0 sin validar nada | `--config` iba detrás del subcomando, donde cloudflared lo ignora | Ahora va delante: `cloudflared tunnel --config X ingress validate` |

## Problemas típicos

| Síntoma | Qué pasa | Qué hacer |
|---|---|---|
| `el Core no responde en este PC` | El Core está parado | `ARRANCAR_GUARDIA.cmd` |
| `cloudflared no dio ninguna URL` | Sin salida a Internet o falta el binario | Revisa `tools\bin\cloudflared.exe` y la conexión |
| El espejo sirve una URL que ya no va | La publicación no llegó a subir | Mira `logs\tunel_estable.log`; el supervisor republica solo en la siguiente vuelta |
| `no pude leer ...core_actual.json` | El espejo no responde | El minero usa la última dirección buena guardada en `logs\core_actual_cache.txt` |
| Dos túneles a la vez | Se abrió uno a mano con la guardia puesta | No pasa: los dos modos comparten **el mismo cerrojo** (puerto local 8399) y el segundo se cierra solo. Para cambiar de modo hay que dejar libre ese cerrojo: reiniciar la guardia o cerrar la ventana del túnel |
| Preparé el dominio y sigue saliendo `trycloudflare` | El supervisor del túnel ya estaba corriendo en modo rápido | Reinicia la guardia (o cierra la ventana de `ARRANCAR_TUNEL.cmd`): al arrancar elige el modo con nombre. `dominio_core.txt` solo, sin `tools\cloudflared\config.yml`, no basta: se exigen los dos |

## Ficheros

| Fichero | Qué es |
|---|---|
| `tools/tunel_estable.py` | Supervisor del túnel rápido: abre, verifica, publica y vigila |
| `tools/tunel_dominio.py` | Túnel con nombre: `--preparar`, `--arrancar`, `--estado` |
| `PREPARAR_DOMINIO.cmd` | Atajo de doble clic para el paso a dominio propio |
| `tools/cloudflared/config.yml` | Config del túnel con nombre (**sin secretos**), lo escribe el script |
| `dominio_core.txt` | El dominio elegido, en una línea; si no está, se usa el túnel rápido |
| `tools/core_destino.py` | Resuelve la dirección (directa o `auto`) para todos los programas |
| `docs/core_actual.json` | La dirección viva, tal cual se publica |
| `logs/tunel_estable.log` | Historial: cada apertura, cada caída, cada publicación |
