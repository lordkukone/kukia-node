# Minar KUKIA — guía del minero

KUKIA no se mina quemando electricidad: el Core te da un cálculo, lo haces en
tu GPU, el Core lo **recalcula y compara**, y solo si coincide te paga. No se
puede falsificar: un resultado inventado se rechaza y no cobra.

Dos cosas del 23-10-2026: el minero mina **en una ventana de cmd, a la vista**
(cada tarea y un resumen por minuto) y cobra **por trabajo** (GFLOP verificados),
no por tarea entregada. Las dos se explican abajo.

## Qué necesitas

- Windows 10/11 (probado) o Linux.
- **Python 3.10 o superior**, con la casilla *Add Python to PATH* marcada.
- **GPU NVIDIA** recomendada (con drivers actualizados). Sin GPU funciona en
  CPU, pero cada tarea tarda mucho más y, desde el 23-10-2026, **cobra menos**:
  se paga por trabajo (GFLOP) y una CPU entrega mucho menos por segundo. Sigue
  mereciendo la pena si no tienes gráfica, pero no es "lo mismo más lento".
- La **URL del Core**. En el zip ya viene puesta: no hay que tocar nada (ver
  el paso 2). Si minas en el mismo PC que el Core: `http://127.0.0.1:8300`.

## Arrancar (3 pasos)

1. Descomprime `kukia-node.zip` en una carpeta normal (por ejemplo
   `C:\KUKIA`). Evita `Program Files`: Windows no deja escribir ahí sin admin.
2. **No hace falta tocar `core_minero.txt`.** Ya trae esta línea, que sigue
   sola la dirección pública del Core aunque cambie de nombre:
   `auto https://kukia-network.surge.sh/core_actual.json`
   Solo la cambias si quieres minar contra otro Core: se sustituye entera por
   su URL (por ejemplo `https://kukia.tudominio.com`). Las líneas que empiezan
   por `#` se ignoran; cuenta solo la primera que no.
3. Doble clic en **`ARRANCAR_MINERO.cmd`**. Mina **en esa ventana**: cada tarea
   sale según se entrega y cada minuto una línea con el ritmo.

La primera vez instala lo que falte y te pide PyTorch si no lo tienes (con GPU
NVIDIA, instálalo con el comando CUDA que muestra: es mucho más rápido).

Cuando arranca verás algo así:

```
[KUKIA] nodo 3f9c1a7b2e04 (minero_TUPC[cuda:0]) vram=12GB listo (cobra en KUK90BA...)
[KUKIA] minero_TUPC[cuda:0] tarea 2fb2b027 matriz -> OK +0.000507531 KIA (3 ms)
[KUKIA] minero_TUPC[cuda:0] tarea d258bd3a difusion_calor -> OK +0.020797892 KIA (373 ms)
```

Al minuto siguiente empieza a salir la línea del ritmo (la del apartado de la
ventana).

Los céntimos de KIA no son un fallo: se paga por FLOPs (ver la sección del pago).
La difusión de 0,4 s cobra 40 veces más que la matriz de 3 ms, que es justo lo que
se busca: cobra el trabajo, no la tarea.

## Guarda la recuperación (lo más importante de esta guía)

La primera vez, el minero crea tu billetera y muestra **una contraseña y 20
palabras**. Apúntalas en papel o en un gestor de contraseñas. También puedes
copiar el fichero `ledger\wallet\worker_<tu_nodo>.json`.

- Con la contraseña + las 20 palabras recuperas la misma billetera en otro PC.
- Si pierdes las dos cosas, **el saldo no se puede recuperar**. No hay soporte,
  ni administrador, ni "recuperar cuenta": la clave es tuya y solo tuya.
- Nunca las compartas. Quien las tenga se lleva tus KIA.

## Cuánto se gana: por trabajo, no por tarea

Desde el 23-10-2026 el pago va **por los FLOPs verificados de cada tarea**, no
por tarea entregada. Un checksum (0 FLOPs) y una difusión de 20 GFLOP ya no
cobran lo mismo.

- **Precio único del minuto.** El presupuesto del minuto (1,31 KIA hoy) se divide
  entre los GFLOP que entregó la red en el minuto anterior. De ahí sale un precio
  por GFLOP que vale para todas las tareas de ese minuto.
- **Pago de una tarea = precio x GFLOP de la tarea.** A 8x trabajo, 8x pago. El
  reparto es por trabajo, no por orden de llegada.
- **El tier (x1,3 / x1,6) no multiplica** en este modo: la prima por VRAM y la
  prima por potencia eran pagar dos veces lo mismo.
- **Un checksum cobra 0.** Si quieres un fijo pequeño por tarea, el operador del
  Core puede poner `KUKIA_GFLOP_MIN`; por defecto no lo hay: manda el trabajo.
- La bolsa del minuto se **congela al abrirlo** y no se puede pasar ni un
  decimal: cuando se agota, el resto del minuto paga 0 (no está roto: la emisión
  total sigue clavada en su objetivo).

Los números de ahora mismo los da el Core, no esta guía:

```bash
curl -s http://127.0.0.1:8300/stats
# bolsa_emision: precio_minuto, presupuesto_kia_min, pagado_en_minuto, fusible_x
# pago_por_potencia: activo, gflop_min_facturable, precio_gflop
```

Ejemplo medido en esta máquina el 23-10, con el precio del minuto a 0,0042
KIA/GFLOP: una matriz de 0,128 GFLOP paga 0,00054 KIA y una difusión de calor de
0,4 s paga 0,0208 KIA (40 veces más). Por GPU, un minuto con pago por trabajo dio
0,17 KIA/min; con las dos RTX, 0,34 KIA/min. Centésimas o décimas, no KIA
enteros. El precio cambia cada minuto: el bueno es el de `/stats`.

Ojo a los `+0.0 KIA` en la ventana: significan que la tarea no consumió apenas
trabajo (checksum) o que el presupuesto del minuto ya estaba repartido. No es un
fallo: mira la línea del minuto, que es la que manda.

- La red emite **~1,3076 KIA por minuto** en total. Más mineros conectados = menos
  por GFLOP (más potencia NO adelanta la fecha de la última moneda).
- La última KIA sale el **13-04-2150**: 124,28 años desde el inicio, en 6 eras de
  20,71 años a la mitad cada una. `python tools\calculo_moneda.py`.
- El reparto del minuto se puede mirar en `/stats` (`reparto`): cuántas GPUs se
  unieron, qué parte es de cada una y lo que cobra cada GPU si todas entregan lo
  mismo. `python tools\prueba_adaptador_reparto.py`.
- **KIA todavía no tiene precio ni mercado.** Minar hoy no da dinero: da posición
  en la red para cuando la haya.

## El minero, en una ventana

Doble clic en **`VER_MINERO.cmd`** (o `VER_MINERO.cmd minero_pc` para reusar la
identidad de siempre). Es el mismo minero de siempre, pero a la vista: la ventana
enseña cada tarea y **cada minuto** una línea como esta:

```
[KUKIA] minero_pc[cuda:0] 42.2 GFLOPS | 96 tareas/min | 0.171 KIA/min = 10.3 KIA/h = 246 KIA/dia (0.24 KIA/h por GFLOPS) | 0 fallos, total 474 tareas / 4.223 KIA
```

Esa línea es real, del log de esta máquina con el pago por trabajo ya activo. El
importe no es una promesa: depende del precio del minuto y del trabajo de la red.

Si **ya hay un minero minando** (lo normal: la guardia lo arranca oculto al
iniciar sesión), esa ventana **se queda con el trabajo**: `tools/minero_en_ventana.py`
aparta el minero oculto —y el que la guardia relanza al verlo morir, dentro de
sus 20 s de enfriamiento— y mina ahí, a la vista. Si el minero ya está en otra
ventana (`python.exe`), no se lanza otro: abre `tools/mina_en_vivo.py`, que
cada 5 s pinta el ritmo real (tareas/min y KIA/min medidos sobre 20 s), el
estado de cada nodo y las últimas tareas, sin minar ni escribir nada.

- **Un solo minero.** El primero apunta su PID en `worker/minero.lock`; si abres
  otra ventana, **no mina** y te dice qué PID está trabajando. Dos mineros en la
  misma GPU se reparten el cupo (4 tareas por nodo) y rinden la mitad.
- **Ctrl+C no deja basura**: termina lo que tiene en vuelo antes de salir, así no
  deja tareas colgadas ocupando el cupo media hora.
- **Vigía propio**: si pasan 2 minutos sin cobrar con el cupo lleno, pide el
  reinicio firmado de su nodo y el Core le devuelve sus tareas a la cola; si pasan
  5 y el Core está en pie, sale para que la guardia lo levante limpio.
- **Para pararlo de verdad**, la guardia no debe vigilarlo: `python tools/guardia.py
  --sin-minero` (si la guardia lo vigila, lo relanza en segundos).

Al arrancar dice también el precio por GFLOP del minuto, el presupuesto de la
red y la fecha de la última moneda. Todo queda copiado en
`logs/minero_visible.log`.

- **Cerrar la ventana para el minero** (si la guardia lo vigila, lo vuelve a
  arrancar sola, sin ventana).
- **El ritmo se calcula por trabajo**: KIA/min = GFLOP/min x precio del minuto
  (KIA/h es eso x60), no tareas x pago fijo.
- Una GPU de 12 GB hace tareas de milisegundos a ~7 segundos según el tipo.
- Si vienes a ganar euros, esto no es para ti todavía: KIA no tiene mercado.

## Ver el saldo

Doble clic en **`VER_SALDO_MINERO.cmd`**. Consulta el Core y muestra el saldo
real de cada billetera de minero y el total.

El campo `saldo` dentro del JSON de la billetera **no vale**: es una copia local
que siempre marca 0. El bueno es el que devuelve el Core.

## Enviar KIA a otra dirección

El saldo no está atrapado: se puede gastar sin comisión.

1. Abre `wallet.html` (en la web pública o en la carpeta `docs/`).
2. Pestaña **Recuperar**: pon tu contraseña y las 20 palabras del fichero
   `RECUPERACION_*.txt` (o del papel donde las apuntaste).
3. Pestaña **Enviar**: dirección destino `KUK...` y cantidad (máximo 4
   decimales). Pulsa **Firmar y enviar**.

La firma se hace **en el navegador**; la clave privada no se envía al Core ni
se guarda en disco. Si cierras la pestaña, la clave desaparece de memoria y
hay que volver a poner las 20 palabras para el siguiente envío. Eso es a
propósito.

### Desde la consola, sin escribir las 20 palabras

Si la billetera está en este PC (`ledger/wallet/*.json`, cifrada con DPAPI),
`tools/enviar_kia.py` la descifra en memoria y firma ahí mismo:

```bash
python tools/enviar_kia.py --de worker_minero_pc --a KUK... --monto 1.5
```

Sin `--confirmar` **no envía nada**: enseña la billetera, el saldo, el destino,
el importe y el mensaje exacto que firmaría. Si todo cuadra, se repite igual con
`--confirmar`. Da la comisión (0 KIA) y el comprobante (nonce), que solo vale una
vez. La clave no se imprime ni sale del proceso.

No sirve con las billeteras del 13-10 (`nodo_5070`, `nodo_5060ti`,
`nodo_fundador`): sus direcciones vienen de un esquema viejo y el Core no puede
validar su firma en `/transferir`. El programa lo detecta y no envía nada.

### Sin pasar por la web pública (probado el 23/10/2026)

La misma `wallet.html` se puede abrir desde tu PC sin que ningún fichero de la
página venga de internet:

```bat
cd docs
python -m http.server 8462 --bind 127.0.0.1
```

Y abrir `http://127.0.0.1:8462/wallet.html`. Comprobado con el navegador
vigilado, petición por petición: todas fueron a `127.0.0.1` (la página, la
imagen, `nacl.min.js`, `qrcode.min.js`, `wordlist.txt` y `core_actual.json`),
ninguna a la web pública.

Dos cosas antes de enviar:

- Abierta en `127.0.0.1` o `localhost`, la página usa el Core **de este PC**
  (`http://127.0.0.1:8300`) y **no pisa** el que tengas guardado. Si quieres
  otro, escríbelo en **Mi saldo** y pulsa **Ver saldo**: desde ahí manda ese y
  la detección no lo cambia (arreglado el 23/10/2026). Abierta desde la web
  pública sigue detectando el Core de la red, como antes.
- Con palabras que llevan tilde salen **dos direcciones** (NFC y NFD). La buena
  es la que tenga saldo. Si eliges la otra verás 0 KIA y parecerá que has
  perdido la billetera; no la has perdido, vuelve a recuperar y coge la otra.

## Parar y volver a arrancar

Cierra la ventana o pulsa `Ctrl+C`. No se pierde nada: el nodo y su saldo viven
en el Core. Al volver a arrancar se reutiliza el mismo nodo y se sigue cobrando
en la misma dirección.

## Problemas frecuentes

| Lo que ves | Qué pasa |
|---|---|
| `El Core no responde` | URL mal escrita en `core_minero.txt`, Core apagado o túnel caído. |
| `falta direccion_cobro` | Versión vieja del minero: descarga el zip actual. |
| `falta reto: pide uno en /nodos/reto` | Worker viejo contra un Core nuevo: el registro exige probar la clave. Descarga el zip actual (el worker nuevo lo firma solo). |
| `reto caducado o ya usado` | El reloj del PC está muy desfasado o se reintentó con el mismo reto. Reinicia el minero. |
| `Core saturado (429)` | El Core te está limitando; el minero espera solo. |
| `RECHAZADO (esperado …)` | Tu cálculo no coincide: GPU inestable u overclock. Baja el overclock. |
| Windows bloquea el `.cmd` | Clic derecho → Propiedades → *Desbloquear*. |
| `no module named torch` | Instala PyTorch (el lanzador te da el comando). |

## Seguridad

- No hace falta ejecutar nada como administrador.
- El minero solo abre conexiones **hacia** el Core; no abre puertos en tu PC.
- No envíes tu `clave_privada` a nadie, ni "para verificar", ni "para migrar".
- **Contra un Core remoto, usa `https://`** (el túnel ya lo es). El worker se
  niega a mandar firmas por `http://` a una IP que no sea la tuya. Solo para
  una prueba consciente: `set KUKIA_CORE_INSEGURO=1` antes de arrancar.
- El worker **valida la tarea antes de calcular**: si el Core (o un intermediario)
  manda tamaños imposibles, la descarta en vez de colgar la GPU.
- Cada petición de trabajo lleva un **nonce**; repetirla no cuela.
- **Cupo de registro:** una misma IP puede registrar 10 nodos/hora y 50/día;
  una clave, 20/día. Si arrancas muchas GPUs a la vez y ves `429`, espera o
  reparte los nodos entre horas. Un nodo que pide trabajo pero no completa
  ninguna tarea pagada se poda a las 6 h; el minero se vuelve a registrar solo.
- La billetera y `RECUPERACION_*.txt` llevan la clave privada: no los copies a
  carpetas compartidas ni al repositorio. En Windows el perfil ya es por
  usuario; en Linux/macOS quedan con permisos `0600`.
- El minero nuevo necesita el Core nuevo (v0.4.2 o superior para la GPU en
  vivo; v0.4.1 ya trae el nonce). Un minero viejo sigue valiendo contra el Core
  nuevo.
- Con el minero arrancado, el panel muestra **utilización real de GPU** (%)
  cada 3 s, además de la VRAM usada y los ms de la última tarea. Si no hay
  `nvidia-smi`, la tarjeta dice "sin dato de GPU" pero el minero sigue igual.
- **El minero dice con qué trabaja en cada conexión** (23-10-2026): manda su
  `device` al registrarse, al volver a registrarse y en el latido firmado (ahora
  también cuando mina en CPU). El Core lo guarda en el nodo y deja de adivinar
  por VRAM/TOPS. Un nodo dado de alta con un minero viejo, sin `device`, sale
  del plan B —y vuelve a recibir difusiones— en cuanto manda el primer latido.
  Un minero viejo sigue minando: no hace falta actualizarlo para eso.
