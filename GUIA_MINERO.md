# Minar KUKIA — guía del minero

KUKIA no se mina quemando electricidad: el Core te da un cálculo, lo haces en
tu GPU, el Core lo **recalcula y compara**, y solo si coincide te paga. No se
puede falsificar: un resultado inventado se rechaza y no cobra.

## Qué necesitas

- Windows 10/11 (probado) o Linux.
- **Python 3.10 o superior**, con la casilla *Add Python to PATH* marcada.
- **GPU NVIDIA** recomendada (con drivers actualizados). Sin GPU funciona en
  CPU, pero cada tarea tarda mucho más y ganas lo mismo por tarea: te sale mal.
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
3. Doble clic en **`ARRANCAR_MINERO.cmd`**.

La primera vez instala lo que falte y te pide PyTorch si no lo tienes (con GPU
NVIDIA, instálalo con el comando CUDA que muestra: es mucho más rápido).

Cuando arranca verás algo así:

```
[KUKIA] nodo 3f9c1a7b2e04 (minero_TUPC[cuda:0]) vram=12GB listo (cobra en KUK90BA...)
[KUKIA] minero_TUPC[cuda:0] tarea 7dcb4d0d checksum -> OK +0.0246 KIA (0 ms)
```

## Guarda la recuperación (lo más importante de esta guía)

La primera vez, el minero crea tu billetera y muestra **una contraseña y 20
palabras**. Apúntalas en papel o en un gestor de contraseñas. También puedes
copiar el fichero `ledger\wallet\worker_<tu_nodo>.json`.

- Con la contraseña + las 20 palabras recuperas la misma billetera en otro PC.
- Si pierdes las dos cosas, **el saldo no se puede recuperar**. No hay soporte,
  ni administrador, ni "recuperar cuenta": la clave es tuya y solo tuya.
- Nunca las compartas. Quien las tenga se lleva tus KIA.

## Cuánto se gana

- El Core paga **por tarea verificada**. El importe exacto de ahora mismo lo da
  el Core en `/stats` (`recompensa_base_actual`); cambia con el ritmo de la red.
- La red emite **~1,31 KIA por minuto** en total, repartidos entre las tareas
  que se entregan en ese minuto. Más mineros conectados = menos por tarea (más
  potencia NO adelanta la fecha de la última moneda).
- La última KIA sale el **01-01-2150**: 124 años desde el inicio, en 6 eras de
  20,67 años a la mitad cada una. Los cálculos, a la vista, con
  `python tools\calculo_moneda.py`.

### Ver el minero en una ventana de cmd

Doble clic en **`VER_MINERO.cmd`** (o `VER_MINERO.cmd minero_pc` para reusar la
identidad de siempre). La ventana enseña cada tarea, y **cada minuto** una línea
como esta:

```
[KUKIA] minero_pc[cuda:0] 42.2 GFLOPS | 49 tareas/min | 1.020 KIA/min = 61.2 KIA/h = 1469 KIA/dia (1.45 KIA/h por GFLOPS) | 0 fallos, total 143 tareas / 3.780 KIA
```

Si **ya hay un minero minando** (lo normal: la guardia lo arranca oculto al
iniciar sesión), esa ventana **se queda con el trabajo**: `tools/minero_en_ventana.py`
aparta el minero oculto —y el que la guardia relanza al verlo morir, dentro de
sus 20 s de enfriamiento— y mina ahí, a la vista. Si el minero ya está en otra
ventana (`python.exe`), no se lanza otro: abre `tools/mina_en_vivo.py`, que
cada 5 s pinta el ritmo real (tareas/min y KIA/min medidos sobre 20 s), el
estado de cada nodo y las últimas tareas, sin minar ni escribir nada.

Al arrancar dice también la emisión (KIA por tarea, KIA/min de la red) y la
fecha de la última moneda. Todo queda copiado en `logs/minero_visible.log`.
Los dos cambios que hay que entender: **KIA/h según potencia** = tareas/min
multiplicado por el pago de cada tarea, y **cerrar la ventana para el minero**
(la guardia lo vuelve a arrancar sola, pero sin ventana).

Ojo a los `+0.0 KIA`: el presupuesto por minuto (1,31 KIA) se agota tras unas
decenas de tareas y el resto del minuto se paga 0. No está roto: la emisión
total sigue en su objetivo (medido esa noche: 0,656 + 0,906 KIA/min entre los
dos nodos). Lo que se ve por tarea depende de cuándo caiga dentro del minuto.
- Una GPU de 12 GB hace tareas de milisegundos a ~7 segundos según el tipo.
- **KIA todavía no tiene precio ni mercado.** Minar hoy no da dinero: da
  posición en la red para cuando la haya. Si vienes a ganar euros, esto no es
  para ti todavía, y es mejor decirlo que dejarte conectado una semana.

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
