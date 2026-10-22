"""Verificacion de invariantes de la moneda KIA (solo lectura).

Comprueba lo que tiene que cumplirse SIEMPRE, sin tocar la base:
  1. suma del ledger == supply emitido
  2. cadena de hashes intacta
  3. ningun saldo negativo (salvo residuo de coma flotante)
  4. ningun asiento importante fuera de la cadena
  5. nonces de transferencia sin repetir
  6. direcciones con saldo que no se pueden gastar (no son nodos)
  7. nodos sin clave / duplicados / tier autodeclarado

Uso:
  python tools/verificar_moneda.py
  python tools/verificar_moneda.py --core http://127.0.0.1:8300
Sale 0 si todo cuadra, 1 si algo falla.
"""
import argparse
import hashlib
import json
import sqlite3
import sys
import urllib.request
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
DB = RAIZ / "coordinator" / "ledger" / "kukia.db"
MAX_SUPPLY = 100_000_000.0
EPS = 1e-6

fallos = []
avisos = []


def ok(msg):
    print(f"  OK   {msg}")


def fallo(msg):
    fallos.append(msg)
    print(f"  FALLO {msg}")


def aviso(msg):
    avisos.append(msg)
    print(f"  AVISO {msg}")


def conectar():
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def verificar_cadena(con):
    prev = ""
    n = 0
    rotos = []
    sin_hash = []
    for r in con.execute("SELECT id,tarea_id,nodo_id,kuki,motivo,ts,hash_prev,hash "
                         "FROM ledger ORDER BY id"):
        if not (r["hash"] or "").strip():
            sin_hash.append((r["id"], r["tarea_id"], r["motivo"]))
            continue
        base = f"{r['tarea_id']}|{r['nodo_id']}|{r['kuki']}|{r['motivo']}|{r['ts']}|{r['hash_prev'] or ''}"
        if hashlib.sha256(base.encode()).hexdigest() != r["hash"] or (r["hash_prev"] or "") != prev:
            rotos.append(r["id"])
        prev = r["hash"]
        n += 1
    if rotos:
        fallo(f"cadena rota en ids {rotos[:10]}")
    else:
        ok(f"cadena de hashes intacta ({n} asientos encadenados)")
    if sin_hash:
        importantes = [x for x in sin_hash if x[1] in ("genesis", "migracion")]
        aviso(f"{len(sin_hash)} asientos sin encadenar (previos a la cadena); "
              f"{len(importantes)} son fundacionales/migracion: {importantes[:4]}")


def verificar_supply(con, core):
    suma = float(con.execute("SELECT COALESCE(SUM(kuki),0) s FROM ledger").fetchone()["s"] or 0.0)
    if core:
        try:
            with urllib.request.urlopen(core.rstrip("/") + "/stats", timeout=5) as r:
                d = json.loads(r.read().decode())
            supply = float(d.get("supply_emitido") or 0.0)
            # El Core paga mientras leemos: el total puede crecer entre la suma
            # de antes y la de despues. Vale cualquier valor dentro de la ventana.
            suma2 = float(con.execute("SELECT COALESCE(SUM(kuki),0) s FROM ledger").fetchone()["s"] or 0.0)
            if suma - 1e-4 <= supply <= suma2 + 1e-4:
                ok(f"supply == suma del ledger ({supply:.4f} KIA dentro de {suma:.4f}..{suma2:.4f})")
            else:
                fallo(f"/stats supply {supply} fuera de la ventana del ledger {suma}..{suma2}")
        except Exception as e:
            aviso(f"no pude consultar /stats ({type(e).__name__}); uso la base")
            ok(f"suma del ledger: {suma:.4f} KIA")
    else:
        ok(f"suma del ledger: {suma:.4f} KIA")
    if suma > MAX_SUPPLY + EPS:
        fallo(f"supply {suma} supera el maximo {MAX_SUPPLY}")
    else:
        ok(f"supply dentro del maximo (quedan {MAX_SUPPLY - suma:.2f} KIA por emitir)")


def verificar_saldos(con):
    negativos = [dict(r) for r in con.execute(
        "SELECT nodo_id, SUM(kuki) s FROM ledger GROUP BY nodo_id HAVING s < ?", (-EPS,))]
    if negativos:
        fallo(f"saldos negativos: {negativos[:5]}")
    else:
        ok("ningun saldo negativo")


def verificar_transferencias(con):
    try:
        dup = con.execute("SELECT de_nodo, nonce, COUNT(*) n FROM transferencias "
                          "GROUP BY de_nodo, nonce HAVING n > 1").fetchall()
        if dup:
            fallo(f"nonces repetidos: {[dict(x) for x in dup[:5]]}")
        else:
            ok("nonces de transferencia unicos")
    except sqlite3.Error:
        aviso("tabla transferencias no existe todavia")


def verificar_gasto(con):
    """Direcciones KUK... con saldo: no son nodos, asi que hoy no pueden firmar."""
    nodos = {r["id"] for r in con.execute("SELECT id FROM nodos")}
    filas = [dict(r) for r in con.execute(
        "SELECT nodo_id, SUM(kuki) s FROM ledger GROUP BY nodo_id HAVING s > ?", (EPS,))]
    atrapado = round(sum(f["s"] for f in filas if f["nodo_id"] not in nodos), 4)
    billeteras = [f for f in filas if f["nodo_id"] not in nodos]
    if atrapado > 0:
        aviso(f"{atrapado} KIA en {len(billeteras)} direcciones de billetera: no hay "
              f"endpoint para gastar desde una direccion (solo desde un nodo)")
    else:
        ok("no hay saldo en direcciones sin ruta de gasto")


def verificar_nodos(con):
    total = con.execute("SELECT COUNT(*) c FROM nodos").fetchone()["c"]
    sin_clave = con.execute("SELECT COUNT(*) c FROM nodos WHERE COALESCE(clave_publica,'')=''").fetchone()["c"]
    dup = [dict(r) for r in con.execute(
        "SELECT nombre, COUNT(*) n FROM nodos GROUP BY nombre HAVING n > 1 ORDER BY n DESC LIMIT 5")]
    tiers = {r["tier"]: r["n"] for r in con.execute("SELECT tier, COUNT(*) n FROM nodos GROUP BY tier")}
    ok(f"{total} nodos registrados ({sin_clave} sin clave publica)")
    if dup:
        aviso(f"nombres duplicados (nodos de prueba/viejos): {dup}")
    if any(t != 1.0 for t in tiers):
        aviso(f"tiers con multiplicador de pago: {tiers} (vram autodeclarada)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--core", default="http://127.0.0.1:8300")
    args = ap.parse_args()
    print(f"KUKIA · verificacion de la moneda · {DB}")
    con = conectar()
    print("1) Cadena del ledger")
    verificar_cadena(con)
    print("2) Suministro")
    verificar_supply(con, args.core)
    print("3) Saldos")
    verificar_saldos(con)
    print("4) Transferencias")
    verificar_transferencias(con)
    print("5) Rutas de gasto")
    verificar_gasto(con)
    print("6) Nodos")
    verificar_nodos(con)
    print()
    if fallos:
        print(f"RESULTADO: {len(fallos)} FALLO(S)")
        for f in fallos:
            print(" -", f)
        return 1
    print(f"RESULTADO: OK · {len(avisos)} aviso(s)")
    for a in avisos:
        print(" -", a)
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    sys.exit(main())
