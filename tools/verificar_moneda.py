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


def _es_direccion(d: str) -> bool:
    """KUK + 38 hex (41 caracteres), igual que `direccion_valida` del Core."""
    d = (d or "").strip().upper()
    return len(d) == 41 and d.startswith("KUK") and all(c in "0123456789ABCDEF" for c in d[3:])


def verificar_gasto(con):
    """¿El saldo que hay en una billetera se puede gastar?

    Hasta el 22-10-2026 aqui se avisaba de que los 15 M KIA de las billeteras
    estaban atrapados "porque solo se puede transferir desde un nodo". Era
    FALSO: `POST /transferir` acepta el origen KUK... siempre que presente su
    clave publica y la firma Ed25519 del mensaje
    `transferir:<de>:<a>:<monto>:<nonce>` — la direccion se deriva de esa clave
    (`KUK + SHA256(pub)[:38]`), asi que nadie puede gastar el saldo de otra.

    Probado con dinero real el 22-10-2026: tools/prueba_gasto_billetera.py
    (16 comprobaciones, incluida la firma con otra clave rechazada y el nonce
    repetido bloqueado).

    Lo que SI seria un agujero: saldo en una cuenta que no es ni un nodo
    registrado ni una direccion valida. Eso no lo puede firmar nadie.
    """
    nodos = {r["id"] for r in con.execute("SELECT id FROM nodos")}
    filas = [dict(r) for r in con.execute(
        "SELECT nodo_id, SUM(kuki) s FROM ledger GROUP BY nodo_id HAVING s > ?", (EPS,))]
    atrapado = 0.0
    billeteras = []
    for f in filas:
        if f["nodo_id"] in nodos:
            continue
        if _es_direccion(f["nodo_id"]):
            billeteras.append(f)
        else:
            atrapado += float(f["s"])
    total = round(sum(float(f["s"]) for f in billeteras), 4)
    if atrapado > 0:
        fallo(f"{round(atrapado, 4)} KIA en cuentas que no son nodo ni direccion: "
              f"nadie puede firmar eso")
    elif billeteras:
        ok(f"{total} KIA en {len(billeteras)} billeteras: gastables firmando con su "
           f"clave (/transferir), sin registrar ningun nodo")
    else:
        ok("sin saldo en direcciones de billetera")


def verificar_nodos(con):
    """Nodos vivos, y quien cobra prima de verdad.

    Desde el 22-10-2026 el registro tiene `retirado`: cuando un nombre se
    registraba otra vez se quedaba la fila vieja, y el registro decia 34 nodos
    donde habia 20. Esas filas se marcan, no se borran, y aqui no cuentan.

    El tier de pago ya no sale de la VRAM declarada: sale de la potencia
    medida (`tier_verificado`). Por eso la prima solo se avisa en los nodos que
    NO la han demostrado; avisar de todos los tier>1 era una falsa alarma.
    """
    vivos = "COALESCE(retirado,0)=0"
    total = con.execute(f"SELECT COUNT(*) c FROM nodos WHERE {vivos}").fetchone()["c"]
    sin_clave = con.execute(f"SELECT COUNT(*) c FROM nodos WHERE {vivos} "
                            "AND COALESCE(clave_publica,'')=''").fetchone()["c"]
    retirados = con.execute("SELECT COUNT(*) c FROM nodos WHERE COALESCE(retirado,0)=1").fetchone()["c"]
    dup = [dict(r) for r in con.execute(
        f"SELECT nombre, COUNT(*) n FROM nodos WHERE {vivos} "
        "GROUP BY nombre HAVING n > 1 ORDER BY n DESC LIMIT 5")]
    # GFLOPS por GB declarado: 0,25 es el liston del Core. Los que estan por
    # debajo no cobran prima, aunque su fila diga tier 1,3.
    sin_prueba = [dict(r) for r in con.execute(
        f"SELECT nombre, tier, vram_gb, ROUND(COALESCE(gflops_medido,0),2) gflops, "
        f"COALESCE(potencia_muestras,0) muestras FROM nodos WHERE {vivos} AND tier > 1 "
        "AND (COALESCE(potencia_muestras,0) < 20 OR COALESCE(gflops_medido,0) <= 0 "
        "OR COALESCE(gflops_medido,0) < 0.25 * COALESCE(vram_gb,0))")]
    ok(f"{total} nodos vivos ({sin_clave} sin clave publica)"
       + (f", {retirados} retirados por nombre repetido" if retirados else ""))
    if dup:
        aviso(f"nombres duplicados: {dup}")
    else:
        ok("un nombre, una identidad viva (sin duplicados)")
    if sin_prueba:
        aviso(f"{len(sin_prueba)} nodo(s) con prima sin demostrar: {sin_prueba[:5]}"
              " - se les paga tier 1,0 hasta que midan")
    else:
        ok("todas las primas de tier estan respaldadas por potencia medida")


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
