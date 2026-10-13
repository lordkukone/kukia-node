# KUKIA - Red descentralizada de computo GPU

**KIA Coin (ticker: KIA)** - la moneda del trabajo verificado.

KUKIA conecta las GPUs de todo el mundo en una sola red de inteligencia artificial.
Cada nodo ejecuta tareas reales (inferencia, imagen, video, simulaciones) y cada
resultado se verifica bit a bit. El trabajo correcto se paga en KIA.

> **Estado: FASE DE PRUEBAS (prototipo funcional).** Sin precio, sin venta, sin
> promesas. Este software no tiene todavia auditoria de seguridad independiente.

---

## Como funciona (de verdad, con el codigo a la vista)

```
 [ Worker (tu GPU) ]  ->  pide tarea  ->  [ Core (coordinador) ]
        |                                          ^
        |  ejecuta en GPU (PyTorch)                |
        |  devuelve hash del resultado             |
        +--------------> verifica bit a bit -------+
                              |
                    hash correcto = paga KIA
                    hash falso    = 0 KIA + pierde reputacion
```

1. **Registro:** tu worker se registra en el Core y recibe un ID de nodo.
   La VRAM de tu GPU define tu *tier* (12GB = x1.0, 16GB = x1.3, 24GB = x1.6).
2. **Tarea:** el Core reparte tareas reproducibles (checksums, matrices, inferencia).
3. **Ejecucion:** tu GPU las ejecuta y devuelve un hash derivado del resultado.
4. **Verificacion:** el Core recalcula el hash esperado y lo compara. Si coincide,
   te paga KIA y sube tu reputacion. Si no, no cobras nada.
5. **Ledger:** todas las operaciones quedan registradas en una base SQLite publica
   (el ledger se puede consultar por API).

## Economia (parametros de la fase de prueba)

| Parametro | Valor |
|---|---|
| Techo maximo | **100.000.000 KIA** (nunca habra mas) |
| Emision | escalonada hasta **2150** |
| Asignacion fundacional | 15.000.000 KIA (una sola vez, registrada en el ledger) |
| Recompensa base | 10 KIA por tarea verificada (x tier) |
| Quema | 10-15% de comisiones (futuro) |
| Precio | **NO EXISTE todavia** - sale solo con demanda real |

## Estructura del repositorio

```
coordinator/  kukia_core.py    # Core: nodos, tareas, verificacion, ledger (FastAPI + SQLite)
worker/       kukia_worker.py  # Minero: se conecta al Core y ejecuta tareas en tu GPU
ledger/       kukia_wallet.py  # Billetera local (se genera en tu PC, tu clave nunca sale)
dashboard/    kukia_dash.py    # Panel web local con el estado de la red
docs/         index.html       # Web publica (https://kukia-network.surge.sh)
```

## Instalacion rapida

```bash
pip install fastapi uvicorn pydantic requests torch

# Terminal 1 - Core (coordinador)
cd coordinator
python -m uvicorn kukia_core:app --host 0.0.0.0 --port 8300

# Terminal 2 - Worker (minero)
cd worker
python kukia_worker.py mi_nodo
```

Tu billetera se crea automaticamente al minar (`ledger/kukia_wallet.py mi_nodo`).

## Transparencia (leer antes de instalar)

- **Es un prototipo en fase de pruebas.** No hay auditoria externa aun.
- **Sin precio:** KIA no cotiza ni se vende. Punto.
- **Rentabilidad:** incalculable hoy (sin precio, sin demanda medida, sin consumo registrado).
- **Recompensas actuales:** parametros de prueba (10 KIA/tarea), no ganancias garantizadas.
- **Tu clave:** se genera y se queda en tu PC. Nadie mas la tiene.

## Roadmap

- **Fase 1 (ahora):** pruebas locales + primeros nodos invitados
- **Fase 2:** apertura publica del minado con recompensas de bienvenida
- **Fase 3:** primeros clientes de computo -> **aqui nace el precio, solo por demanda**

## Enlaces

- Web: https://kukia-network.surge.sh
- Whitepaper: https://kukia-network.surge.sh/whitepaper.html
- Codigo fuente completo: https://kukia-network.surge.sh/codigo.html
- Nodo descargable: https://kukia-network.surge.sh/kukia-node.zip

---

*KUKIA - Every GPU. One intelligence network.*
