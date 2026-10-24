# Dominio fijo para el Core

Dos objetivos distintos que se pueden hacer por separado:

- **A. URL pública sin mover el Core** → `ARRANCAR_TUNEL.cmd` (túnel de Cloudflare).
  Sigue dependiendo de que tu PC esté encendido.
- **B. URL fija Y sin depender de tu PC** → todo lo demás de este documento (un VPS).

## A. La vía que no pide permiso a nadie: túnel de Cloudflare

El Core sigue en tu PC y `cloudflared` abre la puerta desde dentro: **ni un puerto del router
abierto, ni cuenta de Tailscale, ni el atributo `funnel` de ningún tailnet**. Cloudflare pone
el HTTPS delante.

Ya está montado y es lo que arranca la guardia:

| Pieza | Dónde |
|---|---|
| Binario | `tools\bin\cloudflared.exe` |
| Lanzador | `ARRANCAR_TUNEL.cmd` (raíz del proyecto) |
| Supervisor | `tools\tunel_estable.py`, en `127.0.0.1:8399`; relanza el túnel si se cae |
| Comprobar | `python tools\tunel_estable.py --estado` |
| Dirección viva | se escribe en `docs\core_actual.json` |

**Dos modos, y el lanzador elige solo:**

| Modo | URL | Cuándo |
|---|---|---|
| RÁPIDO (por defecto) | `https://algo.trycloudflare.com` | sin dominio ni cuenta. **El nombre cambia al reiniciar.** |
| DOMINIO | `https://tu-dominio` | tras `PREPARAR_DOMINIO.cmd`. Fija, no cambia nunca. |

Arrancarlo: doble clic en `ARRANCAR_TUNEL.cmd`. Si la guardia está viva, ya lo hace ella al
encender el PC y la ventana avisará de que hay otro supervisor (no habrá dos túneles).

**Comprobar sin trampa** (las dos órdenes que valen):

```bash
python tools\tunel_estable.py --estado
curl https://TU-URL/version     # mismo JSON que http://127.0.0.1:8300/version
```

Lo que la puerta deja hacer y lo que no, medido sobre la URL pública: `/admin/*` → **403**,
`/cliente/*` → **401**, `POST /transferir` con firma falsa → **401**. Lectura pública
(`/version`, `/stats`, `/nodos`, `/ledger`, `/red/serie`) responde 200: es lo que la web y los
nodos necesitan.

### Por qué NO se usa Tailscale Funnel

Se probó el 21/10: Tailscale está instalado, pero Funnel **no se activa desde la consola**.
Exige habilitar el atributo `funnel` en el tailnet desde el panel con cuenta
(`login.tailscale.com/f/funnel`), y sin eso el `.cmd` se queda en
`Funnel is not enabled on your tailnet`. `funnel_kukia.cmd` se queda en la carpeta por si
algún día se activa ese permiso, pero **no es la vía**: la de Cloudflare funciona hoy y no
depende de que nadie autorice nada.

Con túnel rápido la URL cambia al reiniciar; con dominio es tan fija como la de Funnel.
Límite honesto del túnel de Cloudflare: el rápido es anónimo y sin SLA; el de dominio tiene
el límite del plan gratuito (100 MB por petición), que para JSON sobra.

---

## B. El Core en un servidor: dominio fijo, sin depender de tu PC

Objetivo: que `https://TU_DOMINIO` responda siempre, aunque tu PC esté apagado.

**Son dos cosas distintas y hay que hacerlas en orden:**

1. **El Core en un servidor** → deja de depender de tu PC. *(esto es lo caro)*
2. **El dominio apuntando ahí** → deja de ser un túnel efímero. *(esto es lo barato)*

Si solo haces la 2 con el `PREPARAR_DOMINIO.cmd` que ya tienes, tendrás URL fija
pero **seguirá dependiendo de tu PC**. Las dos juntas es lo que pedías.

---

## Lo que necesitas

| Cosa | Coste real | Nota |
|---|---|---|
| Dominio | ~10 €/año | Namecheap, Porkbun o Cloudflare Registrar. Uno ya vale. |
| Servidor VPS | 5–20 €/mes | **mínimo 2 vCPU y 4 GB RAM**; recomendable 4 vCPU / 8 GB |
| Cuenta de Cloudflare | gratis | solo si eliges la vía del túnel (§4B) |

### Por qué 2 vCPU como mínimo y no el VPS de 1 €

El Core **no es un servidor web pasivo: recalcula las tareas para verificarlas.** Hoy, en tu
PC, el proceso lleva **17.891 segundos de CPU** desde que arrancó (unas 3 horas de reloj),
o sea **aproximadamente un núcleo entero ocupado de media**. Y el ledger ya pesa **334 MB**.

Un VPS de 1 vCPU y 1 GB va a atascar la cola y a quedarse sin memoria. Abajo de 2 vCPU / 4 GB
no bajes.

### Lo que NO necesita

- **GPU**: no hace falta ninguna. El Core verifica en CPU (`torch` CPU-only, ~200 MB).
- **Windows**: el código no tiene rutas absolutas ni depende de DPAPI para arrancar.
- **Abrir el 8300**: ese puerto se queda cerrado a internet; solo Caddy lo toca.

---

## 1. Copiar el ledger de forma segura

Con el Core **en marcha** en tu PC:

```bash
python despliegue/copiar_ledger_seguro.py
```

Esto **no copia el fichero a pelo**: usa la API de backup de SQLite, porque el Core está
escribiendo y una copia normal puede salir rota. Al terminar comprueba
`PRAGMA integrity_check`, cuenta asientos y te da el `sha256`.

Si dice `0 asientos`, **no lo subas**.

## 2. Subirlo al servidor

```bash
./despliegue/subir_al_servidor.sh usuario@tu-servidor /srv/kukia
```

Sube el ledger y las dos claves (`sello_key.txt`, `admin_token.txt`), pone permisos 600 y
**compara el sha256 en el servidor contra el local**. Si no coincide, para y no arranca nada.

## 3. Arrancar en el servidor

```bash
ssh usuario@tu-servidor
cd /srv/kukia
cp despliegue/.env.ejemplo despliegue/.env
nano despliegue/.env            # pon tu dominio en KUKIA_ORIGENES
nano despliegue/Caddyfile       # cambia TU_DOMINIO
docker compose -f despliegue/docker-compose.yml up -d
docker compose -f despliegue/docker-compose.yml logs -f core
```

Y comprueba desde fuera, desde tu PC, con el túnel apagado:

```bash
curl https://TU_DOMINIO/version
curl https://TU_DOMINIO/stats
curl https://TU_DOMINIO/ledger/integridad    # tiene que decir cadena_ok: true
```

## 4. Elegir cómo se publica

### 4A. IP pública + Caddy (lo más simple, recomendado)

El DNS del dominio apunta a la IP del servidor (registro `A`) y Caddy pide el certificado
solo. No hay que abrir nada más: 80 y 443.

- ✅ Sin dependencias, sin cuentas extra.
- ❌ La IP del servidor queda visible en el DNS (normal en cualquier web).

### 4B. Cloudflare Tunnel con nombre (sin abrir puertos)

Se instala `cloudflared` **en el servidor** (`apt install cloudflared`), se crea un túnel con
nombre y se apunta al `127.0.0.1:8300` local. La ventaja: el servidor no expone ni un puerto,
y Cloudflare absorbe ataques.

- ✅ Nada abierto a internet, protección DDoS incluida.
- ❌ Una dependencia más, y Cloudflare ve el tráfico.
- 📌 Ya tienes `tools/tunel_dominio.py` para la versión desde Windows; en el servidor es
  `cloudflared tunnel run kukia` y no hace falta Python.

## 5. Cambiar el punto de encuentro de los mineros

Cuando el Core del servidor esté respondiendo:

1. **Apaga el Core de tu PC.** Dos Cores con dos bases distintas son **dos redes distintas**.
2. Actualiza `docs/core_actual.json` con la URL nueva (el fichero se genera y se publica; el
   minero lo lee al arrancar).
3. Comprueba que un minero que arranque en limpio encuentra el Core nuevo.

---

## Lo que este despliegue NO resuelve

- **Sigue habiendo un solo Core.** Que esté en un servidor lo hace fiable, no descentralizado.
  Si el servidor cae, la red cae igual. Para eso hace falta un segundo Core independiente, y
  eso es otro trabajo (replicación y consenso entre Cores, que hoy no existen).
- **La clave de los sellos viaja al servidor.** `sello_key.txt` firma el historial. En un VPS
  está en una máquina que no controlas físicamente. Opciones: asumirlo (es lo que hace todo el
  mundo), o usar `KUKIA_SELLO_KEY` apuntando a un secreto montado por el proveedor.
- **Las billeteras cifradas con DPAPI de Windows no se descifran en Linux.** Eso solo afecta a
  `ledger/wallet/*.json` (tus billeteras locales). El Core no las necesita para funcionar,
  pero si algún día quieres gastar desde el servidor, habrá que re-cifrarlas allí.
- **Los 334 MB del ledger crecen.** Pon backup automático en el servidor desde el primer día
  (`tools/backup_ledger.py` ya existe): un VPS sin copia es un ledger que algún día se pierde.

---

## Comprobar que de verdad no depende de tu PC

La prueba honesta: **apaga tu PC entero** (o desconecta la red), abre el móvil con datos y
entra en `https://TU_DOMINIO/stats`. Si responde con números y la emisión ha subido, está
hecho. Si no responde, no lo está, y da igual lo que diga el `docker ps`.
