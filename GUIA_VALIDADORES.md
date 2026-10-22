# Validadores independientes de KUKIA

Un **validador** es un Core de verificación: otro proceso (mejor, otra máquina)
que **recalcula las tareas ya verificadas** y firma si coinciden o no.

No sustituye al Core de pagos: sigue habiendo **un** Core que decide y paga.
Dos Cores pagando a la vez serían un doble gasto, no más seguridad. Lo que
aporta el validador es lo que a Bitcoin le da su fuerza: **más de una máquina
comprobando el mismo trabajo**.

## Qué hace exactamente

1. Pide al Core un **manifiesto firmado** con trabajo ya verificado
   (`GET /red/witness`). La firma es Ed25519 con la clave del sello del Core.
2. Comprueba la firma del manifiesto antes de fiarse del contenido.
3. **Recalcula cada tarea por su cuenta** (numpy en CPU). No copia el
   resultado del minero ni el del Core: lo calcula otra vez.
4. Compara (exacto para checksum/matriz/inferencia; tolerancia relativa para
   difusión de calor).
5. Firma una **atestación** con su propia clave Ed25519 y la envía a
   `POST /validador/atestacion`:
   - `ok: true` → todo coincide.
   - `ok: false` + lista de discrepancias → algo no cuadra.

## Qué NO hace (a propósito)

- **No paga, no mueve saldos, no escribe en el ledger.** No tiene la clave del
  sello ni acceso a las billeteras.
- **No abre puertos.** Solo hace peticiones salientes al Core.
- **No bloquea pagos.** Una atestación con discrepancias es un aviso firmado,
  no una orden. Si cualquier validador pudiera bloquear, sería un ataque de
  denegación de servicio fácil. Las discrepancias se ven en el panel y en
  `/validador/estado`.
- **No cobra.** Hoy validar no da KIA; es una tarea de auditoría. Cualquier
  recompensa futura tendría que salir de gobernanza y estar en el código.

## Arrancarlo en el mismo PC

1. Deja el Core encendido (`http://127.0.0.1:8300`).
2. Doble clic en **`ARRANCAR_VALIDADOR.cmd`**.
3. La primera vez crea `validator/identidad_validador.json` con su clave
   privada Ed25519 (solo firma atestaciones, no dinero).

Verás algo así:

```
[VALIDADOR] identidad nueva en ...\validator\identidad_validador.json
[VALIDADOR] clave publica: 9f2c...
[VALIDADOR] VALIDADOR_TUPC verificando http://127.0.0.1:8300 (lote=10, cada 30s)
[VALIDADOR] atestacion aceptada: 10 tareas, 0 discrepancias
```

## Arrancarlo en OTRO PC (lo que de verdad aporta)

1. Copia el proyecto (o `kukia-node.zip` descomprimido) en el otro equipo.
2. En `core_minero.txt` pon la URL del Core que sea accesible desde ese PC
   (por ejemplo el túnel HTTPS, o la IP de tu red local).
3. Doble clic en `ARRANCAR_VALIDADOR.cmd`.

El validador necesita `numpy`, `requests` y `cryptography` (el lanzador los
instala si faltan). No necesita la base de datos ni `sello_key.txt`.

> **Importante:** para que la validación sea independiente de verdad, tiene que
> correr en **otra máquina** y con **otra clave**. Dos procesos en el mismo PC
> comparten CPU, disco y sistema: sirven para probar el circuito, no para
> proteger de un Core comprometido en ese mismo PC.

## Varios validadores en el mismo PC

Cada validador debe tener **su propia clave**. Si pasas un nombre al lanzador,
él crea solo su fichero de identidad (no reutiliza la clave del primero, que
sería como tener un solo validador):

```bat
ARRANCAR_VALIDADOR.cmd VALIDADOR_2
```

También puedes darle una identidad concreta (por ejemplo, una copiada de otro
PC) como segundo argumento:

```bat
ARRANCAR_VALIDADOR.cmd VALIDADOR_2 ruta\a\identidad.json
```

O lanzarlo a mano:

```bat
python validator\kukia_validator.py --nombre VALIDADOR_2 ^
  --identidad validator\identidad_VALIDADOR_2.json
```

Opciones útiles:

| Opción | Defecto | Qué hace |
|---|---|---|
| `--core` | `http://127.0.0.1:8300` | URL del Core |
| `--nombre` | `VALIDADOR_<PC>` | Nombre que aparece en `/validador/estado` |
| `--lote` | 10 | Tareas por vuelta (1–50) |
| `--intervalo` | 30 | Segundos entre vueltas |
| `--max-ms` | 45000 | Presupuesto de cálculo por vuelta |
| `--identidad` | `validator/identidad_validador.json` | Clave del validador |
| `--una-pasada` | — | Una vuelta y sale (pruebas) |

## Comprobar que está funcionando

```bash
curl http://127.0.0.1:8300/validador/estado
```

Devuelve, entre otras cosas:

```json
{
  "activos": 1,
  "total": 1,
  "revisadas_24h": 240,
  "discrepancias_24h": 0,
  "validadores": [
    {"validador": "VALIDADOR_TUPC", "ok": true, "revisadas": 10, "ts": 1792...}
  ]
}
```

En el **panel** (`http://127.0.0.1:8301`) hay una tarjeta
**“Validadores independientes”** con los activos, las tareas revalidadas y las
discrepancias de las últimas 24 h.

Si un validador encuentra una discrepancia, súbelo: el `id` de la tarea y los
valores esperado/recibido quedan registrados y firmados. Eso es exactamente lo
que hay que investigar.

## Límites honestos

- **En el mismo PC no hay independencia real.** Es la base técnica para
  ejecutarlo en varios equipos.
- **La clave pública del Core se toma de la misma conexión.** En loopback el
  riesgo es mínimo. Si el Core está en Internet, usa **HTTPS** (túnel); el
  manifiesto va firmado, pero todavía no hay *pinning* de la clave del Core en
  el validador. Es la siguiente mejora natural.
- **Una atestación es una afirmación firmada, no una prueba criptográfica.**
  Un validador malicioso puede mentir. Lo que hace fuerte al sistema es que
  varios validadores independientes digan lo mismo; si uno discrepa, se ve.
- **No hay incentivo económico** para validar todavía. No se paga por validar.

## Problemas frecuentes

| Lo que ves | Qué pasa |
|---|---|
| `el Core no firma el manifiesto` | El Core arrancó sin `ledger/sellos.py` o sin `sello_key.txt`. Revisa el arranque del Core. |
| `sin tareas nuevas` | No hay trabajo verificado reciente. Es normal si el minero está parado. |
| `error: ... (reintento en Xs)` | Core apagado, URL mal o túnel caído. El validador reintenta solo. |
| `ModuleNotFoundError: numpy` | `python -m pip install numpy requests cryptography`. |
| Discrepancias en un nodo | Mira el `id` de la tarea y el tipo. Puede ser overclock inestable en la GPU o un resultado manipulado. |
