# Conectar otro programa minero (100 % GPU)

KUKIA **no** es un minero de hashes: el Core recalcula la tarea y compara el
resultado exacto. Un programa externo puede minar, pero tiene que **calcular las
mismas tareas deterministas** y firmar el resultado con la clave del nodo. Un
minero genérico de SHA-256 (lolMiner, XMRig, etc.) no sirve para KUKIA.

## Lo que necesita cualquier minero externo

1. Una billetera `KUK...` (para cobrar) y su clave privada Ed25519. La clave
   **nunca** se envía al Core.
2. Registrar el nodo:

   ```http
   POST /nodos/registrar
   {"nombre":"rig-cuda","vram_gb":24,"tops":1300,
    "clave_publica":"<64 hex>","direccion_cobro":"KUK..."}
   ```

   Devuelve `id`. Guarda ese id: los pagos van a la dirección, pero el id
   identifica el nodo y su reputación.

3. Pedir trabajo firmado:

   ```
   mensaje = "pendiente:<nodo_id>:<ts>:<nonce>"
   GET /tareas/pendiente?nodo_id=<id>&ts=<epoch>&nonce=<aleatorio>&firma=<hex>
   ```

   `nonce` es obligatorio en el protocolo nuevo (8–64 caracteres). El Core
   rechaza repetirlo durante 10 minutos. Sin nonce solo se acepta el mensaje
   antiguo `pendiente:<id>:<ts>` (compatibilidad con mineros viejos).

4. Calcular **exactamente** lo que pide la tarea:

   | Tipo | Cálculo |
   |---|---|
   | `checksum` | `SHA-256(data)` en hex |
   | `matriz` | `A @ B` con la fórmula de `worker/kukia_worker.py`; resultado entero |
   | `inferencia` | convolución 3×3 con el kernel del worker; resultado entero |
   | `difusion_calor` | rejilla `numpy.default_rng(seed)` y `pasos` iteraciones; suma `repr(float)` |

   No copies el resultado del Core ni de otro minero: tiene que salir de tu
   propio cálculo. Los topes son `n ≤ 4096`, `pasos ≤ 20000`, `data ≤ 4096` y
   el presupuesto de operaciones del worker. Rechaza lo que no quepa **antes**
   de reservar VRAM.

5. Entregar firmado:

   ```
   mensaje = "<tid>:<metrica>"
   POST /tareas/resultado?tid=<tid>&nodo_id=<id>&metrica=<valor>
        &firma=<hex>&device=cuda:0&duracion_ms=<ms>&telemetria=<json>
   ```

   `telemetria` es opcional (máx. 8 KB): `{"device":"cuda:0","vram_gb":24,
   "tarea_ms":12.3}`. El panel la usa para las tarjetas de GPU; no cambia el
   pago. `metrica` máximo 16 KB, `duracion_ms` finito y no negativo.

## Telemetría en vivo (opcional, Core 0.4.2+)

Para que el panel muestre la GPU en tiempo real, manda un latido firmado cada
2–5 s (no paga ni mueve saldos):

```
metricas = '{"device":"cuda:0","vram_gb":24,"gpu_util_pct":100,"gpu_mem_mb":9000,"tarea_ms":12}'
h = SHA256(metricas)
mensaje = "telemetria:<nodo_id>:<ts>:<h>"
POST /nodos/telemetria {"nodo_id":..., "device":"cuda:0",
                         "metricas":metricas, "ts":<epoch>, "firma":<hex>}
```

`device` solo puede ser `cpu` o `cuda:0..99`. El Core guarda una fila por
nodo/GPU y el panel descarta latidos de más de 5 minutos. Si no lo mandas, el
minero funciona igual; la tarjeta solo muestra el último dato histórico.

## Para exprimir la GPU al 100 %

El worker actual hace **una tarea por GPU y espera la entrega**; con tareas
pequeñas la GPU se queda a ratos idle. Un minero externo puede mejorar eso sin
tocar el Core:

- Implementar los cuatro tipos como kernels CUDA/OpenCL y mantener la cola de
  la GPU llena (varias tareas en vuelo, no una).
- Reutilizar buffers y evitar copias CPU↔GPU; para `difusion_calor` la rejilla
  inicial se genera con `numpy` (misma semilla) y se sube una vez.
- Medir `duracion_ms` real y mandar `telemetria`; el panel calcula GFLOPS con
  ese tiempo. Si quieres ver **utilización** de GPU (%), hay que añadirla a la
  telemetría y al panel: es una mejora aparte, no parte del protocolo.
- No paralelizar dos procesos sobre la misma GPU: se pelean por la VRAM.

## Seguridad (obligatoria)

- Core remoto siempre por **HTTPS**. El worker oficial se niega a mandar firmas
  por `http://` a una IP que no sea loopback.
- Sin redirecciones HTTP: una redirección podría llevarse la firma a otro host.
- La clave privada se queda en el equipo; el Core solo ve la pública y las
  firmas. No la metas en el repositorio ni en una carpeta compartida.
- Valida el trabajo antes de calcular (tipo, tamaños, `n`, `pasos`, coste). Un
  Core comprometido no debe poder colgar tu GPU.
- El minero **no** abre puertos: solo hace peticiones salientes.

## Compatibilidad

- Un minero **viejo** (sin nonce) sigue funcionando contra el Core nuevo.
- Un minero **nuevo** contra un Core anterior a v0.4.1 recibirá 401: actualiza
  el Core.
- El Core sigue siendo **uno**: esto no crea consenso distribuido; solo permite
  que otro programa haga el trabajo y cobre por él.
