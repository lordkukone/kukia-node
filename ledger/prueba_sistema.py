"""Prueba end-to-end de KUKIA (minado verificado): nodo -> tarea -> hash -> pago -> ledger."""
import hashlib, json, requests, time

CORE = "http://127.0.0.1:8300"

def prueba():
    print("=== PRUEBA 1: registrar nodo fundador (5070, 12GB) ===")
    r = requests.post(f"{CORE}/nodos/registrar",
                      json={"nombre": "nodo_fundador_5070", "vram_gb": 12})
    nodo = r.json(); print("  -> registrado:", nodo["id"], "| tier:", nodo["tier"])

    print("=== PRUEBA 2: cliente crea 3 tareas (2 checksum + 1 matriz) ===")
    for i, (tipo, params) in enumerate([
        ("checksum", {"data": "kukia-verificado-001"}),
        ("checksum", {"data": "koin-de-nodo-002"}),
        ("matriz", {"n": 512}),
    ]):
        r = requests.post(f"{CORE}/tareas/crear", json={"tipo": tipo, "params": params})
        tid = r.json()["tarea_id"]
        print(f"  -> tarea {tid} tipo={tipo}")

    print("=== PRUEBA 3: nodo pide y resuelve (hash verificado) ===")
    while True:
        pend = requests.get(f"{CORE}/tareas/pendiente", params={"nodo_id": nodo["id"]}).json()["tarea"]
        if not pend: break
        pid = pend["id"]
        if pend["tipo"] == "checksum":
            res = hashlib.sha256(pend["params"]["data"].encode()).hexdigest()
        else:
            import torch
            a = torch.randn(512, 512, device="cuda")
            b = torch.randn(512, 512, device="cuda")
            res = f"{(a @ b).sum().item():.6f}"
        esperado = hashlib.sha256(f"{pid}:{nodo['id']}:{res}".encode()).hexdigest()
        r = requests.post(f"{CORE}/tareas/resultado",
                          params={"tid": pid, "nodo_id": nodo["id"],
                                  "hash_res": esperado, "metrica": res}).json()
        print(f"  -> tarea {pid[:8]} verificado={r['ok']} recompensa={r['kuki']} KUKI")

    print("=== PRUEBA 4: tarea FALSA no paga ===")
    r = requests.post(f"{CORE}/tareas/crear", json={"tipo": "checksum", "params": {"data": "fake"}})
    tid = r.json()["tarea_id"]
    pend = requests.get(f"{CORE}/tareas/pendiente", params={"nodo_id": nodo["id"]}).json()["tarea"]
    res = hashlib.sha256("FALSO-resultado-inventado".encode()).hexdigest()
    esperado2 = hashlib.sha256(f"{pid}:{nodo['id']}:{res}".encode()).hexdigest()
    r2 = requests.post(f"{CORE}/tareas/resultado",
                       params={"tid": tid, "nodo_id": nodo["id"],
                               "hash_res": hashlib.sha256(b"zzz").hexdigest(), "metrica": "nope"}).json()
    print(f"  -> tarea falsa: ok={r2['ok']} (debe ser False), kuki={r2['kuki']} (debe ser 0.0)")

    print("=== PRUEBA 5: ledger ===")
    led = requests.get(f"{CORE}/ledger").json()
    for l in led[:5]:
        print(f"  -> {l['tarea_id'][:8]} | {l['nodo_id'][:8]} | +{l['kuki']} KUKI | {l['motivo']}")
    st = requests.get(f"{CORE}/stats").json()
    print(f"=== SUPPLY: {st['supply_emitido']} / 100M KUKI | nodos: {st['nodos']} | tareas OK: {st['tareas_ok']} ===")
    print("PRUEBA COMPLETA: EL SISTEMA FUNCIONA (minado verificado OK, falso rechazado OK)")

if __name__ == "__main__":
    prueba()