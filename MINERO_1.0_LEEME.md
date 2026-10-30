# Minero de KUKIA 1.0

Minero de Proof-of-Compute para la red KUKIA. Pide trabajo a un Core, lo calcula
en la GPU (o en la CPU si no hay GPU), entrega el resultado firmado y cobra en tu
billetera. **KUKIA está en TESTNET**: no hay auditoría de seguridad independiente
y KIA no tiene precio de mercado.

## Qué hay en este paquete

| Ruta | Qué es |
|---|---|
| `worker/kukia_worker.py` | El minero |
| `ledger/kukia_wallet.py`, `ledger/sellos.py`, `ledger/wordlist_es.txt` | Billetera y utilidades que usa el minero |
| `tools/core_destino.py` | Resuelve la URL del Core (opcional) |
| `ARRANCAR_MINERO.cmd` | Arranca el minero en una ventana |
| `minero.env.example` | Ejemplo de configuración (sin credenciales) |
| `VERSION.json` | Versión y hashes |
| `SHA256SUMS.txt` | SHA-256 de cada archivo del paquete |

**No incluye** `ledger/wallet/` (tu clave privada), `identidad_minero.json`, logs
ni bases de datos: eso se crea en tu equipo en el primer arranque.

## Requisitos

- Windows 10/11 con Python 3.10 o superior en el PATH.
- `requests` (`python -m pip install requests`).
- PyTorch para calcular: `python -m pip install torch --index-url https://download.pytorch.org/whl/cu121` (versión CUDA) o `python -m pip install torch` (CPU).
- Un Core de KUKIA accesible.

## Instalación

1. Descomprime el ZIP en una carpeta propia (por ejemplo `C:\KUKIA-minero`).
2. Comprueba hashes: `certutil -hashfile SHA256SUMS.txt SHA256` no sirve para
   verificarse a sí mismo; usa `Get-Content SHA256SUMS.txt` y compara cada línea
   con `certutil -hashfile <archivo> SHA256`.
3. Arranca `ARRANCAR_MINERO.cmd`.
4. La primera vez se crea la billetera y se guarda la contraseña y las 20 palabras
   en `ledger\wallet\RECUPERACION_*.txt`. **Guarda ese fichero fuera del PC.** Sin
   él no se recupera el saldo.

## Configuración (variables de entorno)

| Variable | Defecto | Para qué |
|---|---|---|
| `KUKIA_CORE` | `http://127.0.0.1:8300` | Core al que se pide trabajo |
| `KUKIA_EN_VUELO` | 4 | Tareas simultáneas por nodo (2–12) |
| `KUKIA_HILOS_GPU` | 1 | Ranuras de cálculo por tarjeta (1–4) |
| `KUKIA_COLA_ESPERA` | 30 | Segundos máximos para dejar una tarea en la cola |
| `KUKIA_MINERO_LOG_BYTES` | 5242880 | Tamaño al que el minero rota su registro |
| `KUKIA_ESTERIL_SEG` | 180 | Silencio que dispara el aviso `ESTERIL` |
| `KUKIA_USAR_CPU` | apagado | `1` añade la CPU como un nodo más |
| `KUKIA_TIMEOUT_API` | 30 | Timeout de las llamadas al Core |
| `KUKIA_TIMEOUT_ENTREGA` | 90 | Timeout de la entrega de resultados |

Copia `minero.env.example` a `minero.env` si quieres dejarlas fijas (el `.cmd` lo
lee). No pongas credenciales en ese fichero: no hacen falta.

## Qué hace y qué no

- Pide trabajo firmado con nonce, calcula, entrega y espera confirmación del Core.
  **No decide cuánto ha ganado**: el Core recalcula el resultado y paga.
- Usa todas las GPU que CUDA vea, cada una con su cola y su hilo de petición.
- Si una GPU falla, la otra sigue. Un error de cálculo se reintenta una vez y el
  cupo vuelve, así que no se queda sin capacidad.
- Si no hay GPU, reintenta 6 veces y después mina en CPU, diciéndolo.
- No reinicia drivers, no toca voltajes ni overclock, no modifica nada del sistema.
- Registro fechado en `worker\minero.log` (rotación propia, sin bytes NUL) y
  diagnóstico de arranque en `worker\arranque.log`.

## Diagnóstico

```bat
type worker\arranque.log   :: versiones, GPU, hilos, ESTERIL, HILO_MUERTO, pilas
type worker\minero.log     :: una línea por tarea, con fecha
```

- `ESTERIL cuda:N`: esa GPU lleva 180 s sin pedir trabajo. Copia el bloque `PILAS`
  que viene detrás antes de reiniciar: es el dato que falta para diagnosticarlo.
- `HILO_MUERTO`: falta el hilo que pide o el que calcula para un dispositivo.
- `Core no disponible`: el Core no responde; el minero reintenta con espera
  creciente (10, 15, 22, 34, 51, 60 s) y no se cae.

## Desinstalación

1. Cierra la ventana del minero (o termina su proceso `python.exe`).
2. Guarda `ledger\wallet\RECUPERACION_*.txt` y los `identidad_minero.json` si
   quieres conservar la billetera y el historial del nodo.
3. Borra la carpeta donde lo descomprimiste.

No se instala nada en el sistema: no hay servicios, ni registro, ni tareas
programadas.

## Versión

`VERSION.json` y `SHA256SUMS.txt` identifican exactamente lo que has descargado.
Si el hash no coincide, no lo ejecutes.
