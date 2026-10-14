"""KUKIA Worker - minero multi-GPU: se registra, pide trabajo, ejecuta en GPU.

VERIFICACION REAL: el worker calcula el resultado (no un hash propio) y el
Core lo recalcula y compara. El worker NO puede falsificar: si el numero no
coincide exactamente con el recalculado por el Core, no cobra.

MULTI-GPU: usa TODAS las GPUs disponibles a la vez (una cola por GPU).
"""
import hashlib, json, sys, time, threading, socket
import requests
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "ledger"))
import kukia_wallet as WALLET

CORE = "http://127.0.0.1:8300"

def trabajo_local(tipo: str, params: dict, device: str = "cuda:0") -> str:
    """Calcula la tarea EXACTAMENTE como el Core (determinista)."""
    if tipo == "matriz":
        import torch
        n = int(params.get("n", 256))
        seed = int(params.get("seed", 42))
        idx = torch.arange(n, device=device, dtype=torch.float64)
        a = ((idx[:, None] * 31 + idx[None, :] * 17 + seed) % 97) - 48
        b = ((idx[:, None] * 31 + idx[None, :] * 17 + (seed + 1)) % 97) - 48
        c = float((a @ b).sum().item())
        return str(int(round(c)))
    if tipo == "checksum":
        data = str(params.get("data", "kukia"))
        return hashlib.sha256(data.encode()).hexdigest()
    if tipo == "inferencia":
        import torch
        n = int(params.get("n", 64))
        seed = int(params.get("seed", 7))
        idx = torch.arange(n, device=device, dtype=torch.float64)
        x = ((idx[:, None] * 31 + idx[None, :] * 17 + seed) % 97) - 48
        k = torch.tensor([[1.0, 2.0, 1.0], [2.0, 4.0, 1.0 * 2], [1.0, 2.0, 1.0]],
                         device=device, dtype=torch.float64)
        out = 0.0
        for i in range(3):
            for j in range(3):
                out += float((x[i:n-2+i, j:n-2+j] * k[i, j]).sum().item())
        return str(int(round(out)))
    return "tarea_no_soportada"

def ciclo_worker(nombre: str, device: str, vram_gb: int, tops: float):
    """Un hilo por GPU: registra su nodo y mina en bucle (resultados FIRMADOS)."""
    etiqueta = f"{nombre}[{device}]"
    # Billetera propia para firmar envios (nadie puede suplantar este nodo)
    w = WALLET.crear_billetera("worker_" + nombre.replace("[", "_").replace("]", "_").replace(":", ""))
    if isinstance(w, tuple):
        w = w[0]
    pubkey = w.get("clave_publica", "")
    privkey = w.get("clave_privada", "")
    try:
        r = requests.post(f"{CORE}/nodos/registrar",
                          json={"nombre": etiqueta, "vram_gb": vram_gb, "tops": tops,
                                "clave_publica": pubkey}, timeout=30)
        nid = r.json()["id"]
        print(f"[KUKIA] nodo {nid} ({etiqueta}) vram={vram_gb}GB listo")
    except Exception as e:
        print(f"[KUKIA] {etiqueta} error registro: {e}")
        return
    while True:
        try:
            pend = requests.get(f"{CORE}/tareas/pendiente", params={"nodo_id": nid}, timeout=30).json()["tarea"]
            if not pend:
                time.sleep(3)
                continue
            metrica = trabajo_local(pend["tipo"], pend["params"], device)
            firma = WALLET.firmar(f"{pend['id']}:{metrica}", privkey) if privkey else ""
            res = requests.post(f"{CORE}/tareas/resultado",
                                params={"tid": pend["id"], "nodo_id": nid, "metrica": metrica,
                                        "firma": firma},
                                timeout=60).json()
            estado = f"OK +{res['kuki']} KIA" if res.get("ok") else f"RECHAZADO (esperado {res.get('esperado')})"
            print(f"[KUKIA] {etiqueta} tarea {pend['id'][:8]} -> {estado}")
        except Exception as e:
            print(f"[KUKIA] {etiqueta} err: {e}")
            time.sleep(5)

def main():
    nombre = sys.argv[1] if len(sys.argv) > 1 else "worker_" + socket.gethostname()
    try:
        import torch
        n_gpus = torch.cuda.device_count()
    except Exception:
        n_gpus = 0

    if n_gpus == 0:
        print("[KUKIA] Sin GPUs CUDA - usando CPU (mas lento)")
        ciclo_worker(nombre, "cpu", 0, 0.0)
        return

    print(f"[KUKIA] {n_gpus} GPU(s) detectada(s) - minando en TODAS a la vez")
    hilos = []
    for g in range(n_gpus):
        try:
            props = torch.cuda.get_device_properties(g)
            vram = props.total_memory // (1024**3)
            # TOPS aproximados por clase de GPU (referencia NVIDIA)
            tops_est = {8: 60.0, 12: 300.0, 16: 760.0, 24: 1300.0}.get(int(vram), 100.0)
        except Exception:
            vram, tops_est = 0, 0.0
        t = threading.Thread(target=ciclo_worker, args=(nombre, f"cuda:{g}", vram, tops_est), daemon=True)
        t.start()
        hilos.append(t)
    for t in hilos:
        t.join()

if __name__ == "__main__":
    main()
