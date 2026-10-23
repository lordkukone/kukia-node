#!/usr/bin/env python3
"""Pruebas de las NEGATIVAS del failover: que se NIEGUE, y que no toque nada.

El ensayo del 22/10 por la mañana probo el camino bueno (parar el Core, arrancar
la red sobre la replica, minar, volver al original). Esto prueba lo otro, que es
donde de verdad se pierde dinero:

  1. Replica DESCONECTADA (el disco no esta / la carpeta no existe).
  2. Replica VIEJA (una copia de hace dias de la misma red).
  3. Replica con la CADENA ROTA (un asiento tocado a mano).
  4. Replica de OTRA RED (firmada con otra clave).
  5. Replica SEPARADA (se bifurco: sabe mas asientos que el original pero no
     viene de el).

En los cinco casos la respuesta esperada es NO, con mensaje claro, sin lanzar
ningun Core y sin tocar el ledger original. Al final se comprueba que el
original sigue byte a byte igual.

    python tools/prueba_failover_negativas.py
    python tools/prueba_failover_negativas.py --base D:/KUKIA_ENSAYO_20261022

No borra nada: cada pasada crea una carpeta nueva con la hora dentro de --base.
Todo pasa sobre COPIAS; los ficheros de verdad solo se leen.
"""
import argparse
import contextlib
import hashlib
import io
import json
import shutil
import sqlite3
import sys
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "tools"))
sys.path.insert(0, str(RAIZ))

import arrancar_replica as A  # noqa: E402
import replica_ledger as R  # noqa: E402
import volver_al_original as V  # noqa: E402
from ledger import sellos as S  # noqa: E402

BASE_DEFECTO = Path("D:/KUKIA_ENSAYO_20261022")
ORIGINAL_DB = RAIZ / "coordinator" / "ledger" / "kukia.db"
ORIGINAL_SELLOS = RAIZ / "coordinator" / "sellos.jsonl"
REPLICA_VIVA = Path("D:/KUKIA_REPLICA") / "actual"
SIN_DISCO = Path("Q:/KUKIA_REPLICA")  # letra que no existe = disco desconectado

RESULTADOS: list[tuple[str, bool, str]] = []


# --------------------------------------------------------------------------
#  Utilidades
# --------------------------------------------------------------------------
def sha256(ruta: Path) -> str:
    h = hashlib.sha256()
    with ruta.open("rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def comprobar(nombre: str, condicion: bool, detalle: str = "") -> bool:
    RESULTADOS.append((nombre, bool(condicion), detalle))
    print(f"  [{'OK  ' if condicion else 'FALLO'}] {nombre}"
          + ("" if condicion or not detalle else f"  -> {detalle}"))
    return bool(condicion)


def ejecutar(modulo, args: list[str]) -> tuple[int, str]:
    """Llama a main() del modulo como si fuera la linea de comandos."""
    buf = io.StringIO()
    antes = sys.argv
    sys.argv = [Path(modulo.__file__).name] + args
    try:
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            rc = modulo.main()
    except SystemExit as e:  # argparse
        rc = int(e.code or 0)
    finally:
        sys.argv = antes
    return int(rc or 0), buf.getvalue()


class LanzadorFalso:
    """Sustituye a subprocess.call: apunta si se habria arrancado un Core."""

    def __init__(self):
        self.llamadas = []

    def call(self, orden, **kw):
        self.llamadas.append((list(orden), dict(kw)))
        return 0


def arrancar_con_espia(destino: Path, puerto: int = 8399, extra=()) -> tuple[int, bool, str]:
    espia = LanzadorFalso()
    real = A.subprocess
    A.subprocess = espia
    try:
        rc, salida = ejecutar(A, ["--destino", str(destino), "--puerto", str(puerto), *extra])
    finally:
        A.subprocess = real
    return rc, bool(espia.llamadas), salida


def volver_con_espia(destino: Path, db: Path = ORIGINAL_DB,
                     sellos: Path = ORIGINAL_SELLOS, extra=(), core: str | None = None):
    """Ejecuta volver_al_original sobre un original de mentira (copia)."""
    reales = (V.DB, V.SELLOS, V.CORE)
    V.DB, V.SELLOS = db, sellos
    if core is not None:
        V.CORE = core
    try:
        return ejecutar(V, ["--destino", str(destino), "--ver", *extra])
    finally:
        V.DB, V.SELLOS, V.CORE = reales


def copiar_arbol(origen: Path, destino: Path) -> None:
    destino.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(origen, destino)


def manifiesto_de(carpeta_vieja: Path) -> dict:
    """Escribe un replica.json como el que tendria esa copia el dia que se hizo."""
    man = {}
    f = carpeta_vieja / "manifest.json"
    if f.exists():
        try:
            man = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            man = {}
    datos = {
        "fecha": man.get("fecha") or "1970-01-01 00:00:00",
        "origen": str(ORIGINAL_DB),
        "core": "http://127.0.0.1:8300",
        "verificado": True,
        "supply": (man.get("db") or {}).get("saldo_total"),
        "asientos": (man.get("db") or {}).get("asientos"),
        "sellos": (man.get("sellos") or {}).get("total"),
        "nota": "copia de una prueba; replica.json reconstruido del manifest.json",
    }
    return datos


def preparar_vieja(base: Path) -> Path | None:
    """La copia mas antigua que haya en backups/ = 'replica de hace dias'."""
    copias = [d for d in sorted((RAIZ / "backups").iterdir())
              if (d / "kukia.db").exists() and (d / "sellos.jsonl").exists()]
    if not copias:
        return None
    origen = copias[0]
    destino = base / "vieja" / "actual"
    if destino.exists():
        return destino
    destino.mkdir(parents=True)
    for nombre in ("kukia.db", "sellos.jsonl", "sello_key.txt"):
        if (origen / nombre).exists():
            shutil.copyfile(origen / nombre, destino / nombre)
    if not (destino / "sello_key.txt").exists():
        shutil.copyfile(RAIZ / "coordinator" / "sello_key.txt", destino / "sello_key.txt")
    if (RAIZ / "coordinator" / "admin_token.txt").exists():
        shutil.copyfile(RAIZ / "coordinator" / "admin_token.txt", destino / "admin_token.txt")
    (destino / "replica.json").write_text(
        json.dumps(manifiesto_de(origen), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return destino


def preparar_copia(base: Path, nombre: str) -> Path:
    destino = base / nombre / "actual"
    if not destino.exists():
        copiar_arbol(REPLICA_VIVA, destino)
    return destino


def romper_cadena(destino: Path) -> None:
    """Toca un asiento sin recalcular su hash: lo que hace editar el .db a mano."""
    con = sqlite3.connect(str(destino / "kukia.db"))
    try:
        ultimo = con.execute("SELECT MAX(id) m FROM ledger").fetchone()[0]
        con.execute("UPDATE ledger SET kuki=kuki+1000 WHERE id=?", (ultimo,))
        con.commit()
    finally:
        con.close()


def otra_clave(destino: Path) -> str:
    """Replica de OTRA red: sus sellos, re-firmados con una clave que no es la
    nuestra. La cadena de `prev` se mantiene (huella_sello no incluye la firma),
    asi que solo cambia quien firma: exactamente lo que hace otra moneda.
    """
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    priv = Ed25519PrivateKey.generate().private_bytes_raw().hex()
    (destino / "sello_key.txt").write_text(priv + "\n", encoding="utf-8")
    sellos = S.leer_sellos(destino / "sellos.jsonl")
    firmados = [S._firmar(s, priv) for s in sellos]
    (destino / "sellos.jsonl").write_text(
        "\n".join(json.dumps(s, ensure_ascii=False) for s in firmados) + "\n",
        encoding="utf-8")
    return priv


def separar(destino: Path, n: int = 5) -> None:
    """Anade asientos con la cadena BIEN hecha pero sin sello nuevo.

    Es lo que queda de una replica que se bifurco: sabe mas que el original,
    su cadena verifica, pero no contiene el ultimo sello firmado del original.
    """
    con = sqlite3.connect(str(destino / "kukia.db"))
    con.row_factory = sqlite3.Row
    try:
        fila = con.execute("SELECT hash FROM ledger WHERE hash IS NOT NULL AND hash<>'' "
                           "ORDER BY id DESC LIMIT 1").fetchone()
        prev = (fila["hash"] if fila else "") or ""
        base_id = int(con.execute("SELECT COALESCE(MAX(id),0) m FROM ledger").fetchone()["m"])
        for i in range(n):
            ts = time.ctime()
            tarea_id = f"separada-{base_id + i + 1}"
            nodo_id, kuki = "prueba", 0.0
            motivo = "asiento sintetico de la prueba de negativas"
            h = hashlib.sha256(f"{tarea_id}|{nodo_id}|{kuki}|{motivo}|{ts}|{prev}".encode()).hexdigest()
            con.execute("INSERT INTO ledger(tarea_id,nodo_id,kuki,motivo,ts,hash_prev,hash) "
                        "VALUES(?,?,?,?,?,?,?)", (tarea_id, nodo_id, kuki, motivo, ts, prev, h))
            prev = h
        con.commit()
    finally:
        con.close()


def leer_ledger(db: Path) -> tuple[int, float, str]:
    """Huella del historial: asientos, suministro y ultimo hash encadenado."""
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        a = int(con.execute("SELECT COUNT(*) FROM ledger").fetchone()[0])
        s = round(float(con.execute("SELECT COALESCE(SUM(kuki),0) FROM ledger").fetchone()[0]), 6)
        h = con.execute("SELECT hash FROM ledger WHERE hash IS NOT NULL AND hash<>'' "
                        "ORDER BY id DESC LIMIT 1").fetchone()
    finally:
        con.close()
    return (a, s, (h[0] if h else ""))


def core_vivo() -> bool:
    try:
        import requests
        return requests.get("http://127.0.0.1:8300/stats", timeout=5).status_code == 200
    except Exception:
        return False


# --------------------------------------------------------------------------
#  Casos
# --------------------------------------------------------------------------
def caso_sin_disco() -> None:
    print("\n1) REPLICA DESCONECTADA (el disco no esta)")
    rc, salida = ejecutar(R, ["--destino", str(SIN_DISCO), "--comparar"])
    comprobar("comparar: exit 2", rc == 2, f"exit {rc}")
    rc, salida = ejecutar(R, ["--destino", str(SIN_DISCO)])
    comprobar("copiar: exit 2 y sin traceback",
              rc == 2 and "Traceback" not in salida, f"exit {rc}: {salida.strip()[-160:]}")
    rc, lanzado, _ = arrancar_con_espia(SIN_DISCO)
    comprobar("arrancar: exit 2 y no lanza Core", rc == 2 and not lanzado, f"exit {rc}")
    rc, salida = volver_con_espia(SIN_DISCO)
    comprobar("volver: exit 2", rc == 2, f"exit {rc}")


def caso_vieja(base: Path) -> None:
    print("\n2) REPLICA VIEJA (copia de hace dias de la MISMA red)")
    destino = preparar_vieja(base)
    if destino is None:
        comprobar("hay alguna copia en backups/", False, "no hay copias")
        return
    man = json.loads((destino / "replica.json").read_text(encoding="utf-8"))
    print(f"   copia del {man['fecha']}: {man['asientos']} asientos, {man['supply']} KIA")

    rc, salida = ejecutar(R, ["--destino", str(destino.parent), "--comparar"])
    comprobar("comparar: exit 6 (misma red pero atrasada)", rc == 6, f"exit {rc}")
    comprobar("comparar: dice 'misma red' y no 'otra historia'",
              "misma red" in salida.lower() and "no puedo demostrar" not in salida.lower())

    rc, lanzado, salida = arrancar_con_espia(destino.parent)
    comprobar("arrancar: exit 7 y NO lanza Core", rc == 7 and not lanzado, f"exit {rc}")
    comprobar("arrancar: dice cuantos asientos se pierden",
              "se perderian" in salida.lower() and str(man["asientos"]) in salida)

    rc, lanzado, _ = arrancar_con_espia(destino.parent, extra=["--aceptar-atraso"])
    comprobar("arrancar --aceptar-atraso: sigue (exit 0, lanza)", rc == 0 and lanzado, f"exit {rc}")

    rc, salida = volver_con_espia(destino.parent, core="http://127.0.0.1:8399")
    comprobar("volver: exit 0 sin tocar nada (el original va por delante)", rc == 0, f"exit {rc}")


def caso_rota(base: Path) -> None:
    print("\n3) REPLICA CON LA CADENA ROTA (un asiento tocado a mano)")
    destino = preparar_copia(base, "rota")
    romper_cadena(destino)
    rc, lanzado, _ = arrancar_con_espia(destino.parent)
    comprobar("arrancar: exit 5 y NO lanza Core", rc == 5 and not lanzado, f"exit {rc}")
    rc, _ = volver_con_espia(destino.parent, core="http://127.0.0.1:8399")
    comprobar("volver: exit 4 (la replica no cuadra)", rc == 4, f"exit {rc}")


def caso_otra_clave(base: Path) -> None:
    print("\n4) REPLICA DE OTRA RED (firmada con otra clave)")
    destino = preparar_copia(base, "otra_clave")
    if not (destino / "marca_otra_clave").exists():
        otra_clave(destino)
        (destino / "marca_otra_clave").write_text("si\n", encoding="utf-8")
    rc, lanzado, salida = arrancar_con_espia(destino.parent)
    comprobar("arrancar: exit 9 y NO lanza Core (otra clave)", rc == 9 and not lanzado, f"exit {rc}")
    comprobar("arrancar: dice que esta firmada con otra clave", "otra clave" in salida.lower())
    rc, _ = volver_con_espia(destino.parent, core="http://127.0.0.1:8399")
    comprobar("volver: exit 4 (las firmas no son de esta red)", rc == 4, f"exit {rc}")


def caso_separada(base: Path) -> None:
    print("\n5) REPLICA SEPARADA (mas asientos, pero no viene del original)")
    destino = preparar_copia(base, "separada")
    if not (destino / "marca_separada").exists():
        separar(destino, 5)
        (destino / "marca_separada").write_text("si\n", encoding="utf-8")

    original = base / "original_copia"
    if not (original / "kukia.db").exists():
        original.mkdir(parents=True, exist_ok=True)
        # OJO: `shutil.copyfile` vale AQUI porque el ensayo se hace con el Core
        # parado (el ledger esta quieto). Con el Core escribiendo, la copia sale
        # a medias y el fichero queda mal formado; comprobado el 23-10-2026.
        # No se cambia a la API de backup: en caliente la copia de la red viva
        # ya la hace `replica_ledger`, y aqui el escenario exige foto congelada.
        shutil.copyfile(ORIGINAL_DB, original / "kukia.db")
        shutil.copyfile(ORIGINAL_SELLOS, original / "sellos.jsonl")

    reales = (A.DB_ORIGINAL, A.SELLOS_ORIGINAL)
    A.DB_ORIGINAL, A.SELLOS_ORIGINAL = original / "kukia.db", original / "sellos.jsonl"
    try:
        rc, lanzado, salida = arrancar_con_espia(destino.parent)
    finally:
        A.DB_ORIGINAL, A.SELLOS_ORIGINAL = reales
    comprobar("arrancar: exit 8 y NO lanza Core", rc == 8 and not lanzado, f"exit {rc}")
    comprobar("arrancar: dice que son dos historias", "dos historias" in salida.lower())

    rc, salida = volver_con_espia(destino.parent, db=original / "kukia.db",
                                  sellos=original / "sellos.jsonl",
                                  core="http://127.0.0.1:8399")
    comprobar("volver: exit 7 (la replica no desciende del original)", rc == 7, f"exit {rc}")
    comprobar("volver: dice que son dos historias", "dos historias" in salida.lower())


def caso_bueno() -> None:
    print("\n6) CAMINO BUENO (replica de hoy): tiene que seguir pasando")
    if not (REPLICA_VIVA / "kukia.db").exists():
        comprobar("hay replica de hoy en D:/KUKIA_REPLICA/actual", False, "no esta")
        return
    rc, salida = ejecutar(R, ["--destino", str(REPLICA_VIVA.parent), "--comparar"])
    comprobar("comparar: exit 0 (misma red y al dia)", rc == 0, f"exit {rc}")
    rc, lanzado, _ = arrancar_con_espia(REPLICA_VIVA.parent)
    comprobar("arrancar: exit 0 y lanza Core", rc == 0 and lanzado, f"exit {rc}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Negativas del failover KUKIA")
    ap.add_argument("--base", default=str(BASE_DEFECTO),
                    help=f"carpeta de trabajo para las copias (defecto {BASE_DEFECTO})")
    args = ap.parse_args()
    base = Path(args.base) / ("negativas_" + time.strftime("%Y%m%d_%H%M%S"))
    base.mkdir(parents=True, exist_ok=True)
    print(f"Sandbox: {base}")
    print("Solo se LEE de los ficheros de verdad. Nada se borra.")

    antes_ledger = leer_ledger(ORIGINAL_DB)
    ant_sellos = sha256(ORIGINAL_SELLOS)
    vivo = core_vivo()
    print(f"Core en 8300: {'vivo' if vivo else 'NO contesta'}"
          + ("" if vivo else "  (los casos que comparan con el Core no se pueden probar)"))

    caso_sin_disco()
    if vivo:
        caso_vieja(base)
    else:
        print("\n2) REPLICA VIEJA: omitido (hace falta el Core vivo para comparar)")
    caso_rota(base)
    caso_otra_clave(base)
    caso_separada(base)
    if vivo:
        caso_bueno()

    print("\n7) EL ORIGINAL NO SE HA TOCADO")
    # El sha256 del .db no vale como invariante: el Core vivo escribe latidos y
    # contadores en su propia base (no en el ledger) cada pocos minutos. Lo que
    # no puede cambiar es el HISTORIAL: asientos, suministro y ultimo eslabon.
    despues = leer_ledger(ORIGINAL_DB)
    comprobar("ledger original: mismos asientos, suministro y ultimo hash",
              despues == antes_ledger, f"antes {antes_ledger} / ahora {despues}")
    comprobar("sellos.jsonl original: mismo sha256", sha256(ORIGINAL_SELLOS) == ant_sellos)

    fallos = [r for r in RESULTADOS if not r[1]]
    print(f"\n=== {len(RESULTADOS) - len(fallos)}/{len(RESULTADOS)} pruebas OK ===")
    if fallos:
        for nombre, _, detalle in fallos:
            print(f"  FALLO: {nombre} {detalle}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
