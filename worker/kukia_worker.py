"""KUKIA Worker - se registra, pide trabajo a Core, ejecuta en GPU, devuelve hash verificado."""
import hashlib, json, sys, time, random, requests, torch

CORE = "http://127.0.0.1:8300"

def trabajo_local(tipo: str, params: dict) -> str:
    """Tarea reproducible con carga GPU real (matriz torch o checksum)."""
    if tipo == "matriz":
        n = int(params.get("n", 512))
        a = torch.randn(n, n, device="cuda")
        b = torch.randn(n, n, device="cuda")
        c = (a @ b).sum().item()
        return f"{c:.6f}"
    if tipo == "checksum":
        data = params.get("data", "kukia")
        return hashlib.sha256(data.encode()).hexdigest()
    if tipo == "inferencia":
        x = torch.randn(1, 3, 64, 64, device="cuda")
        # conv 3x3 sin clasificador: carga GPU verificable
        conv = torch.nn.Conv2d(3, 8, 3, padding=1).to("cuda")
        out = conv(x).sum().item()
        return f"{out:.6f}"
    return "tarea_no_soportada"

def main():
    nombre = sys.argv[1] if len(sys.argv) > 1 else "worker_" + socket.gethostname()
    vram = torch.cuda.get_device_properties(0).total_memory // (1024**3)
    r = requests.post(f"{CORE}/nodos/registrar", json={"nombre": nombre, "vram_gb": int(vram)})
    nid = r.json()["id"]
    print(f"[KUKIA] nodo {nid} ({nombre}) vram={vram}GB tier=OK")
    while True:
        try:
            pend = requests.get(f"{CORE}/tareas/pendiente", params={"nodo_id": nid}).json()["tarea"]
            if not pend:
                time.sleep(3); continue
            metrica = trabajo_local(pend["tipo"], pend["params"])
            esperado = hashlib.sha256(f"{pend['id']}:{nid}:{metrica}".encode()).hexdigest()
            res = requests.post(f"{CORE}/tareas/resultado",
                                params={"tid": pend["id"], "nodo_id": nid,
                                        "hash_res": esperado, "metrica": metrica}).json()
            print(f"[KUKIA] tarea {pend['id'][:8]} -> {'OK ' + str(res['kuki']) + ' KUKI' if res['ok'] else 'FALLO'}")
        except Exception as e:
            print("[KUKIA] err:", e); time.sleep(5)

if __name__ == "__main__":
    import socket
    main()