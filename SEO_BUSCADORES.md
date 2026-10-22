# Salir en los buscadores (Google, Bing, Yandex y los que beben de ellos)

## Estado hoy (21-10-2026, comprobado por HTTP)

| Copia | HTTP | ¿Deja entrar a los buscadores? |
|---|---|---|
| `site-1ea29659….freebuff.page` (la canónica) | **200** | **Sí.** Sirve las 5 páginas con el SEO generado, `robots.txt` abierto y la clave de IndexNow. Aviso enviado: **IndexNow aceptó (HTTP 202)** |
| `kukia-network.surge.sh` | 200 | **No.** Surge sirve **su propio** `robots.txt` con `Disallow: /` en todo `*.surge.sh` e ignora el nuestro (sintaxi/surge#288). No es configurable: es su política. Se queda como espejo de ficheros (el `core_actual.json` del túnel), no como web indexable |

Unas horas antes el host de `Sites` estaba en pausa («Hosting usage is being
checked»), devolvía 503 en preview y producción, y por eso **nada** estaba
indexado (`site:` daba cero resultados). Volvió a las 18:11; se publicó la
versión con el SEO a las 18:16 y el aviso a IndexNow salió a las 18:17.

Lo que queda es **tiempo** (los rastreadores tardan días) y las **tres altas
manuales** de los paneles. No hay nada roto por nuestra parte.

## Lo que ya está hecho, para que el día que el host vuelva sea un comando

`publico/generar_web.py` ahora **genera** el SEO en cada publicación (antes cada
página lo llevaba a mano y ninguna tenía datos estructurados):

- `<meta name="robots" content="index,follow,max-image-preview:large">` en las 5 páginas.
- **Open Graph y Twitter Cards** completos, con URL **absoluta** (antes `og:image`
  era `moneda.jpg`, una ruta relativa que los rastreadores no resuelven).
- **JSON-LD** válido en cada página: `WebSite` + `Organization` + `WebPage`,
  enlazados entre sí con `@id`.
- `og:url` idéntico al `canonical` (si difieren, Google indexa el otro).
- `sitemap.xml` con `lastmod` **real del fichero** (no la fecha de hoy, que se
  ignora por ruido), `changefreq` y `priority` por página.
- `robots.txt` que declara el sitemap.
- Clave pública de **IndexNow** servida en `/<clave>.txt` (la clave no es un
  secreto: va publicada a propósito).

Los ficheros de verificación de cada panel se crean con un comando, sin editar HTML:

```cmd
python tools\indexar.py --token-google <lo que te dé Search Console>
python tools\indexar.py --token-bing   <lo que te dé Bing>
python tools\indexar.py --token-yandex <lo que te dé Yandex>
```

## Comprobar el estado en un comando

```cmd
python tools\indexar.py --estado
```

Dice, URL por URL, si un buscador podría indexarla y **por qué no**: código HTTP,
`robots.txt` que prohíbe, `noindex`, canonical que apunta a otro sitio, JSON-LD
ausente, fichero de clave que no se sirve. Si alguna no responde 200, el envío se
niega: avisar de una URL rota enseña al buscador a no volver.

## El vigilante: se entera solo cuando el sitio vuelve

Como el host ya se cayó una vez sin avisar, hay un vigilante silencioso:

```cmd
python tools\vigilante_web.py --estado      ver qué hay y cuándo miró
python tools\vigilante_web.py --instalar    tarea de Windows cada 10 min
```

Cada 10 minutos mira el sitio **sin abrir ninguna ventana** y:

- si está caído, no dice nada (no tiene sentido avisar cada diez minutos);
- si vuelve pero sirve una versión sin el SEO, avisa: «ya sirve, falta publicar»;
- si vuelve con el SEO puesto, **manda las URLs a IndexNow solo** y avisa con una
  notificación de Windows (esquina, no ventana).

No publica el sitio: un despliegue se hace a propósito. Deja el parte en
`ESTADO_BUSCADORES.txt` y el historial en `logs/vigilante_web.log`.

## ¿Ya salimos? El vigilante lo mira una vez al día (22-10)

Dentro de la misma tarea que ya corre cada 10 minutos (no hay tarea nueva):
**una vez al día** pregunta si KUKIA ya aparece en Google y en Bing, y **avisa
una sola vez**, con notificación de Windows, el día que sale por primera vez en
cada motor. La línea del parte queda en `ESTADO_BUSCADORES.txt` y el detalle en
`logs/vigilante_web.json`.

```cmd
python tools\vigilante_web.py --indexado      mirar ahora, sin esperar al día siguiente
python tools\vigilante_web.py --estado        ver lo último que se miró
```

### Cómo se pregunta a cada motor (y por qué así)

Ninguno de los dos tiene un «¿está mi página dentro?» gratis y sin cuenta, y los
dos cierran la puerta a los programas. Comprobado el 22-10 desde este PC:

| Motor | Qué pasa al preguntarle desde un programa | Cómo se pregunta aquí |
|---|---|---|
| **Google** | Sirve 200 con una página **vacía** que se rellena con JavaScript: 92 KB y ni un resultado | La **API de inspección de URLs de Search Console** con una cuenta de servicio: es la palabra de Google. Sin la clave: se intenta la web, se ve vacía y se dice «sin comprobar» |
| **Bing** | Devuelve resultados que **no son de la consulta** (a `site:es.wikipedia.org` contestó con tiendas de grava); el RSS, igual | Se pregunta a **DuckDuckGo**, que sí contesta y **usa el índice de Bing**: el mismo índice por otra puerta |

**Comprobado en un navegador de verdad el 22-10 a las 12:30**: Google contesta
«La búsqueda de site:… no obtuvo ningún resultado» y Bing no tiene ninguna
página de KUKIA (devuelve 1.810 resultados de radios, que es lo que hace cuando
ignora el `site:` porque no conoce el dominio). Es decir: IndexNow está aceptado y
el aviso mandado, pero **todavía no está indexado en ninguno de los dos**, y eso,
17 horas después, es lo normal.

### «No sale» y «no se ha podido comprobar» no son lo mismo

Cada consulta lleva un **control**: el `site:` de un dominio que sale seguro
(`es.wikipedia.org`). Si el control no devuelve nada, el motor nos está
cerrando la puerta y **no se saca ninguna conclusión**: queda «sin comprobar»,
nunca «no sale». Decir que no estás indexado cuando en realidad no te han
dejado preguntar es lo que hace que se dejen de mandar avisos creyendo que no
hacen falta.

DuckDuckGo contesta a veces **HTTP 202** ("vuelve luego") cuando se le pregunta
mucho: eso tampoco es «no hay resultados». Se reintenta a los 12 s y, si sigue
igual, se vuelve a mirar el mismo día: hasta **tres intentos, separados 2 h**.
Ese reintento es solo para bloqueos que pueden irse solos (el de Bing); Google
sin cuenta de servicio no se reintenta, porque mañana contestará lo mismo.

### La clave de Google: ya no se puede sacar (comprobado el 22-10)

Lo que decía este documento esta mañana ya no vale, y prefiero decirlo antes de
que pierdas media hora en los paneles. Google cerró el **Custom Search JSON API
a clientes nuevos** y, desde el **20 de enero de 2026**, los buscadores
programables nuevos ya no pueden activar «Search the entire web»: solo «Sites
to search». Los que ya tenían acceso lo conservan hasta el **1 de enero de
2027**. Fuentes: [aviso de Programmable Search](https://support.google.com/programmable-search/answer/12397162)
y [estado del JSON API](https://developers.google.com/custom-search/v1/overview).

Consecuencia práctica: **por ahí no hay clave nueva**. Por eso el código ya no
usa esa vía: para Google se usa la **API de inspección de URLs de Search
Console**, que sí está abierta y además dice la verdad (es la misma respuesta
que el panel). Cómo montarla, justo abajo.

### Entonces, ¿cómo sé si Google ya tiene KUKIA?

**A mano, 30 segundos, y es la fuente de verdad** (la propiedad ya está
verificada: `google9d6cb22812b81092.html` se sirve en 200):

1. Entra en [Search Console](https://search.google.com/search-console) con la
   cuenta con la que diste de alta la propiedad.
2. Arriba hay una barra que dice **«Inspección de URL»**: pega
   `https://site-1ea296595daa4ba7935f1574ab9a5a13.freebuff.page/` y pulsa Intro.
3. El panel dice el **«Estado de la indexación de la URL»**: «La URL está en
   Google» o «La URL no está en Google».
4. Si no está, botón **«Probar URL publicada»** (para ver qué ve Google ahora) y
   luego **«Solicitar indexación»**.
5. En **«Sitemaps»** añade `sitemap.xml` si no está; ahí se ve si Google lo ha
   leído y cuántas URLs tiene en cola.

Eso no lo puede hacer el vigilante por sí solo, porque necesita tu sesión. Pero
sí puede preguntárselo a **la API de inspección de URLs de Search Console**, que
es la misma respuesta que te da ese panel. Está **montado y probado** en
`tools/vigilante_web.py`; lo único que falta es la clave, y son cinco minutos:

### La cuenta de servicio (una vez, a mano)

1. [console.cloud.google.com](https://console.cloud.google.com): arriba, crea un
   proyecto (`KUKIA` vale) y espera a que lo seleccione solo.
2. Menú → **«APIs y servicios»** → **«Biblioteca»** → busca
   **«Google Search Console API»** → **«Habilitar»**.
3. Menú → **«IAM y administración»** → **«Cuentas de servicio»** →
   **«Crear cuenta de servicio»**. Nombre: `vigia-kukia`. **«Crear y continuar»**
   → sin roles → **«Listo»**.
4. En la lista, pincha esa cuenta → pestaña **«Claves»** → **«Agregar clave»**
   → **«Crear clave nueva»** → **JSON** → **«Crear»**. Se descarga un `.json`.
5. Ese `.json` va al repo tal cual, con este nombre exacto:
   `C:\Users\kuki\Desktop\KUKIA\searchconsole_cuenta.json` (está en el
   `.gitignore`: no se sube a ningún sitio).
6. Abre el `.json` y copia lo que hay en **`client_email`** (algo como
   `vigia-kukia@kukia-xxxx.iam.gserviceaccount.com`).
7. En [Search Console](https://search.google.com/search-console) → **«Configuración»**
   → **«Usuarios y permisos»** → **«Añadir usuario»**: pega ese correo, permiso
   **«Completo»**, **«Añadir»**.
8. Si la propiedad la diste de alta como **«dominio»** (aparece como
   `sc-domain:…`), escribe esa misma cadena en `google_site.txt`. Si es
   **«prefijo de URL»** (lo que hay ahora), no hay que hacer nada.

Comprobación, un comando:

```cmd
python tools\vigilante_web.py --indexado
```

Si responde `Google  sale · PASS · Enviada e indexada` o
`Google  no · FAIL · Descubierta: actualmente sin indexar`, está funcionando: es
la palabra de Google. Si responde `sin_comprobar` con `HTTP 403`, es que falta el
paso 7 (el permiso en Search Console); con `invalid_grant`, que el `.json` no es
de ese proyecto o está mal copiado.

Con eso el aviso de «ya salimos en Google» sale solo, igual que el de Bing, y no
depende de tu sesión ni de adivinar con `site:`.

### Probarlo

```cmd
python tools\prueba_vigilante_indexado.py                     todo, incluida la red
python tools\prueba_vigilante_indexado.py --sin-red            solo la lógica
python tools\vigilante_web.py --indexado --dominio es.wikipedia.org --solo-ver
```

La última comprueba la detección contra un dominio que sale seguro, sin
guardar nada ni avisar. Si el control no sale, la prueba lo dice como «no se
pudo comprobar», no como fallo: estar bloqueado no es un bug.

## Los pasos, en orden

### 1. Que el host sirva 200 ✅ (hecho)

Publicado el 21-10 a las 18:16. `python tools\indexar.py --estado` da las cinco
en `[OK ]`.

### 2. Avisar a IndexNow (Bing, Yandex, Seznam, Naver) ✅ (hecho)

Sin cuenta, sin alta, sin clave secreta: un POST a `api.indexnow.org` reparte el
aviso a todos los que comparten el protocolo. Resultado real de esta pasada:

```
ACEPTADO (HTTP 202). Bing/Yandex/Seznam/Naver lo tendran en su cola en horas o dias.
```

Se puede repetir cuando cambien las páginas:

```cmd
python tools\indexar.py --enviar
```

Si alguna URL no responde 200, el script **no envía nada** y dice cuál falla.

### 3. Dar de alta el sitio en cada panel (esto sí es a mano, una vez)

| Motor | De dónde saca el índice | Qué hay que hacer |
|---|---|---|
| **Google** | propio | [Search Console](https://search.google.com/search-console): añadir propiedad (prefijo de URL), verificar con `--token-google`, enviar `sitemap.xml` |
| **Bing** | propio | [Bing Webmaster Tools](https://www.bing.com/webmasters): «Importar desde Google Search Console» (un clic) o `--token-bing`; IndexNow va por su cuenta |
| **Yandex** | propio | [Yandex Webmaster](https://webmaster.yandex.com): añadir sitio, `--token-yandex`, enviar sitemap. IndexNow también le llega |
| **DuckDuckGo** | Bing | **Nada.** No tiene alta propia; aparece cuando Bing tenga la página |
| **Brave Search** | propio + Google/Bing | Nada que enviar; rastrea solo y tarda semanas |
| **Ecosia** | Bing | Nada |
| **Qwant** | Bing | Nada |
| **Startpage** | Google | Nada |
| **Mojeek** | propio | Su [formulario de alta](https://www.mojeek.com/add-site/), gratis |
| **Seznam** | propio | IndexNow (ya va con `--enviar`) |
| **Naver** | propio | IndexNow + Search Advisor |
| **Baidu** | propio | Cuenta + ICP (registro chino). Sin ICP no indexa. Se deja fuera |

Es decir: «todos los buscadores» son, de verdad, **tres altas manuales** (Google,
Bing, Yandex) y un comando. El resto hereda.

## Tiempos realistas

- **IndexNow**: horas o un par de días para que la página exista en Bing.
- **Google**: de días a un par de semanas para la primera visita; para aparecer
  por la palabra «KUKIA» basta con que la portada esté viva y enlazada. Para
  competir por «criptomoneda GPU» hacen falta meses y contenido, no etiquetas.
- Repetir el envío todos los días sin cambiar nada no acelera: se ignora.

## La alternativa que resuelve dos problemas a la vez

Si `Sites` tarda en volver (o no vuelve), un **dominio propio** (~10 €/año)
resuelve esto y el túnel del Core en el mismo movimiento:

1. En Surge, un dominio con CNAME sirve **tu** `robots.txt` (el `Disallow: /` es
   solo de `*.surge.sh`), así que Google sí puede entrar.
2. Y de paso es la URL fija del Core: `PREPARAR_DOMINIO.cmd` ya está hecho para
   eso (ver [TUNEL_ESTABLE.md](TUNEL_ESTABLE.md)).

Con dominio propio, el paso 1 de la lista deja de depender de nadie.

## Lo que NO se hace

- Comprar enlaces, tráfico o reseñas: penalización y pérdida de tiempo.
- Servir una página a los bots y otra a las personas (cloaking).
- Meter la misma keyword veinte veces: desde 2019 no ayuda y hoy estorba.
- Enviar a IndexNow URLs que devuelven 404/503: el script ya lo impide.

## Ficheros

| Fichero | Qué es |
|---|---|
| `publico/generar_web.py` | Genera el SEO (robots, sitemap, JSON-LD, Open Graph, clave IndexNow) |
| `tools/indexar.py` | `--estado`, `--enviar`, `--token-google/bing/yandex` |
| `docs/robots.txt` | Permite todo y declara el sitemap |
| `docs/sitemap.xml` | 5 URLs con prioridad y `lastmod` real |
| `docs/<clave>.txt` | Clave pública de IndexNow |
| `indexnow_key.txt` | La misma clave, en el repo, para no rotarla por accidente |
