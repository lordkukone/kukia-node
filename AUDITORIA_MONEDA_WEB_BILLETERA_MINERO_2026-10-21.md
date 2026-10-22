# Auditoría de seguridad — moneda, web, billetera y minero

**Fecha:** 21 de octubre de 2026
**Alcance:** la moneda (ledger y consenso del Core), la web pública, la billetera
y el minero. Cuatro frentes, con el Core en marcha y el sitio publicado.

Resultado corto: **4 fallos corregidos** (1 inyección de HTML real en la
portada, 1 agujero de cabeceras en todo el sitio, 1 saneado que faltaba en el
Core y 1 sink sin escapar). Lo demás que se revisó está bien y se documenta
abajo con la prueba que lo demuestra, para que no haya que repetirlo.

---

## 1. Moneda (ledger y consenso)

### Lo que se comprobó y sale bien

| Comprobación | Resultado |
|---|---|
| Inyección SQL en el Core, el panel o los scripts | **ninguna**: cero `execute()` con f-string; todo va con parámetros `?` |
| Tope de suministro | `recompensa_pago()` paga `min(base × tier, restante)`; el ledger no puede pasar de 100 000 000 |
| Pago doble de una tarea | bloqueado en la transacción (`estado='ok'` → 409), no solo en la comprobación previa |
| Reutilizar una transferencia | `UNIQUE(de_nodo, nonce)` + firma sobre `transferir:de:a:monto:nonce` |
| Gastar el saldo de otra billetera | la dirección se **deriva** de la clave pública que firma; no se acepta una dirección suelta |
| `NaN` / infinitos en montos | rechazados antes de tocar la base |
| Falsificar identidad de un nodo | registro con reto firmado y caducidad; sin la clave privada no se registra |
| Cambiar la dirección de cobro de otro | firma `cobro:<nodo>:<direccion>:<ts>` con ventana de 5 min |
| Manipular el historial a mano en el `.db` | cadena de hashes + sellos firmados; si no cuadra, **se bloquean los pagos** |
| Sybil por registro masivo | cupo por IP/hora, por IP/día y por clave pública |

Todo esto está cubierto por `tools/pruebas_seguridad.py`, que ahora pasa
**101/101** sobre una copia del ledger real.

### Sin cambios

El diseño de la moneda no necesitaba tocarse. La limitación honesta sigue
siendo la misma y está publicada en la web: los sellos **detectan** un
historial editado, no lo impiden si alguien controla el PC.

---

## 2. Web pública

### Fallo 1 (corregido): la portada pintaba el nombre de GPU con `innerHTML`

`docs/index.html` escapaba el nombre de la tarjeta en dos gráficas y **no** en
la tercera (la de GFLOPS). El nombre lo elige el minero al entregar trabajo, así
que era una inyección de HTML que se ejecutaba en la portada de cada visitante.

- Antes: `'&#9679; ' + k + ...` → ahora `'&#9679; ' + escapar(k) + ...`
- Además el Core **limpia el nombre en la puerta** (`device_limpio()`): fuera
  `< > " ' & /`, recortado a 16 caracteres. Así queda cerrado aunque otro
  consumidor futuro (panel, PDF, un bot) lo pinte sin escapar.
- Se entrega una tarea real con `device='<img src=x onerror="alert(1)">'` y el
  Core publica `img srcx onerror`. Prueba nueva en `pruebas_seguridad.py`.

### Fallo 2 (corregido): el sitio no enviaba **ninguna** cabecera de seguridad

Medido con `curl` antes del cambio: solo `Content-Type` y `Cache-Control`.
Faltaba todo lo demás, incluido lo que impide meter la web en un iframe ajeno
(clickjacking sobre la billetera).

Ahora el worker que sirve el sitio añade, a **todas** las respuestas:

```
Strict-Transport-Security: max-age=31536000; includeSubDomains
X-Content-Type-Options: nosniff
X-Frame-Options: DENY
Referrer-Policy: strict-origin-when-cross-origin
Permissions-Policy: geolocation=(), microphone=(), camera=(), payment=(), usb=()
Content-Type: text/html; charset=utf-8
```

Y cada página lleva su **CSP** en un `<meta>` (portada y `/minar` no tenían
ninguna):

```
default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline';
img-src 'self' data:; media-src 'self'; font-src 'self' data:;
connect-src 'self' https: http://127.0.0.1:* http://localhost:*;
form-action 'self' https://formsubmit.co; object-src 'none'; base-uri 'self'
```

La CSP va por página y no como cabecera global a propósito: la de la billetera
necesita permitir su Core en `http://127.0.0.1`, y una cabecera global se
**interseca** con el `meta` y le cortaría la conexión. Comprobado en el
navegador: la portada, `/minar` y `/wallet` cargan sin un solo error de consola,
el vídeo reproduce, `nacl` y la lista de 2048 palabras cargan, el rele responde
y el QR se genera.

### Lo que se revisó y está bien

- **El ZIP público no lleva llaves.** 197 ficheros; comprobado **por contenido**
  (buscando el valor real del token de admin y de la clave del sello dentro de
  cada fichero, no solo por nombre): ninguna coincidencia, ni en el ZIP ni en
  `sitio_publicado/`. `generar_web.py` aborta si aparece `wallet/`,
  `admin_token`, `sello_key`, `.db`, `.log`, `identidad`…
- **El rele** valida las claves de potencia con regex (`^(cuda|cpu):?\d*$`) y
  recorta a 4 GPUs: no puede inyectar nombres.
- **El fichero de verificación de Google** sigue en 200 con el contenido
  exacto, ahora también con las cabeceras nuevas.

---

## 3. Billetera

### Lo que se comprobó y sale bien

| Comprobación | Resultado |
|---|---|
| ¿La clave privada se guarda en el navegador? | **no**: solo en memoria; en `localStorage` queda la dirección (pública) y la URL del Core |
| ¿Firma real? | Ed25519 en el navegador con `nacl`, verificado contra el Core |
| ¿El mensaje firmado coincide con el del Core? | sí, incluido el formato `1.0` (`formatoMonto`) que era el punto fácil de romper |
| ¿Todo lo que viene de fuera pasa por `esc()`? | sí: `detail`, montos, direcciones y errores |
| Scripts de terceros | locales, con `integrity` (SRI) |
| Cifrado en reposo en el PC del minero | DPAPI del usuario de Windows + `chmod 600`/`icacls`; migra solo las billeteras antiguas en claro |
| Recuperación | contraseña + 20 palabras re-deriva la MISMA clave; `kdf` v2 con sal por billetera |

Única limitación, ya documentada en el código: si el PC no tiene DPAPI
(Linux/macOS), el JSON se guarda en claro y lo dice en el campo `cifrado`.

---

## 4. Minero

| Comprobación | Resultado |
|---|---|
| ¿Acepta un Core remoto sin cifrar? | no: `http://` solo para loopback; el resto exige `https://` |
| ¿Se fía de los parámetros que le manda el Core? | no: `validar_tarea()` acota `n`, `pasos`, tamaño del JSON y presupuesto de operaciones |
| ¿La identidad guarda claves privadas? | no: solo `id` + `clave_publica`, con permisos del dueño |
| ¿Firma lo que entrega? | sí; sin firma el Core rechaza el resultado (401) |
| ¿Puede un minero dejar colgadas sus tareas al reiniciar? | no: aviso firmado `/nodos/{id}/reinicio` las devuelve a la cola |
| ¿Se puede suplantar a otro nodo? | no: firma sobre `tid:metrica` con la clave registrada |

El instalador de un clic comprueba Python, instala lo que falta y arranca la
guardia en segundo plano; el paquete que se descarga no lleva la URL del túnel
metida dentro (la sigue del espejo), así que renovar el túnel no rompe a nadie.

---

## 5. Lo que queda abierto (y por qué)

1. **La CSP usa `'unsafe-inline'`.** Las páginas son estáticas con scripts y
   `onclick` en línea; quitarlo obliga a mover el JS a ficheros y a calcular
   hashes en cada cambio. Es la siguiente mejora de verdad en la web, sobre todo
   para la billetera.
2. **La billetera permite apuntar a un Core cualquiera.** Es una función (minar
   contra otro nodo), pero significa que la URL que el usuario pegue manda. No
   puede robar la clave (nunca sale de la pestaña), sí puede engañar sobre el
   saldo.
3. **Cambios en el Core pendientes de reinicio.** El Core que está minando ahora
   mismo corre el código anterior; `device_limpio()` entra en vigor al
   reiniciarlo. Hasta entonces, el saneado de la portada (que ya está publicado)
   es el que protege.
4. **Sin auditoría externa.** Todo esto lo hemos comprobado nosotros; sigue sin
   haber una revisión independiente, y así está dicho en la web.

---

## Cómo volver a comprobarlo

```cmd
python tools\pruebas_seguridad.py                  :: 101/101
curl -sI https://site-1ea296595daa4ba7935f1574ab9a5a13.freebuff.page/ | findstr /i "strict nosniff frame referrer permissions"
python tools\indexar.py --comprobar-google         :: verificacion de Google
```

Ficheros tocados en esta auditoría:

- `docs/index.html` — CSP, `escapar(k)` en la gráfica de GFLOPS, `kukia-build` v1.2.1-csp
- `docs/minar.html` — CSP
- `publico/desplegar_sitio.py` — capa de cabeceras en el worker
- `coordinator/kukia_core.py` — `device_limpio()` aplicado a los 4 puntos donde se guarda el nombre de GPU
- `tools/pruebas_seguridad.py` — 5 comprobaciones nuevas (101 en total)

---

## 6. Disponibilidad: el Core se recupera en segundos (21-10-2026, tarde)

**El problema.** El guardia exigia 3 sondeos fallidos de `/stats` con un
intervalo de 30 s: entre 90 y 120 s de red parada por un Core muerto. Y no era
un caso teorico: se midio en el reinicio de las 19:35 (58 s).

**Por que estaba mal planteado.** Una muerte de proceso y un fallo de red no
son lo mismo. El guardia trataba las dos como "sondeo fallido" y pagaba el
precio del mas lento.

**Que se cambio** (`tools/guardia.py`):

1. **Muerte conocida = reinicio inmediato.** Cada hijo lleva un hilo que espera
   su proceso (`wait()`). Cuando termina, despierta al bucle con un evento: no
   se espera al siguiente sondeo. `arrancar()` ya no se cree la cache de "ya
   esta corriendo" (tenia 20 s de vida y decia que si por un proceso muerto).
2. **Puerto cerrado = no hay Core.** Un vigilante mira cada 2 s si alguien
   escucha en `127.0.0.1:8300` (socket, milisegundos). Si no, avisa al bucle.
   Cubre el caso de un Core que no arranco este guardia (p. ej. tras reiniciar
   el guardia o abrirlo a mano).
3. **Solo "vivo pero colgado" espera 3 fallos**, y ademas se sondea cada 5 s
   mientras algo esta mal. Si lleva ~1 min sin contestar con el proceso en pie,
   se mata y se relanza (antes solo escribia "revisa logs" y ahi se quedaba).
4. **Parar a proposito no es una caida**: `detener()` marca el proceso para que
   el vigilante no lo tome por un fallo (si no, el relanzamiento del minero
   atascado se pelearia consigo mismo).
5. **El resto de hijos se revisa aunque el Core este caido.** Antes el `return`
   temprano dejaba el panel o el publicador caidos hasta que volviera el Core.

**Medido de verdad, matando el Core a la fuerza** (`Stop-Process -Force`), dos
escenarios:

| Escenario | Vuelve a responder | Vuelve a pagar tareas |
|---|---|---|
| Core hijo del guardia (muerte detectada por el proceso) | 16,0 s | 21,2 s |
| Core ajeno al guardia (detectado por el puerto) | 14,2 s | 16,8 s |

Los 14 s son casi todos el arranque de uvicorn + numpy, no la deteccion: el
guardia lo relanza en 1 s desde que lo nota.

**Sin duplicar nada.** Tras reiniciar el guardia dos veces: 1 core, 1 minero,
1 panel, 1 validador, 1 publicador y 1 tunel, con los mismos PID de antes (el
guardia no mata lo que no arranco). El Core nuevo quedo como hijo suyo
(padre 30844), asi que a partir de ahora la muerte se detecta por proceso.

**Prueba automatica nueva:** `python tools\prueba_guardia_rapida.py` → **12/12**
(hijo que muere y se relanza en el acto, sin duplicar; parada a proposito que no
cuenta como caida; `esperar()` que corta la espera; vigilante del puerto).

Ficheros de esta parte: `tools/guardia.py`, `tools/prueba_guardia_rapida.py`,
`SEGURIDAD.md` (punto 14).
