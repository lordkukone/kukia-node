"""Auditor: comprueba un ledger KUKIA contra sus sellos firmados.

Sirve para verificar SIN confiar en el Core: se le da una copia del .db y los
sellos (de un fichero o pedidos al Core) y comprueba todo por su cuenta:

  1. La cadena de hashes cuadra (nadie edito asientos a mano).
  2. Cada sello esta firmado con la clave Ed25519 publicada por el Core.
  3. Los sellos encadenan entre si (no falta ninguno del medio).
  4. Cada sello coincide con el .db (mismo ultimo hash, mismos asientos,
     misma suma de dinero).

Uso:
  # Contra un Core vivo (los sellos se piden al Core; el .db es una copia)
  python publico/verificar_sellos.py --core http://127.0.0.1:8300

  # Solo con ficheros (para auditar un backup)
  python publico/verificar_sellos.py --db backup/kukia.db --sellos backup/sellos.jsonl

  # Ademas, comparar con el ancla publicada en el rele
  python publico/verificar_sellos.py --publicado https://<rele>/api/metricas

Sale con codigo 0 si todo cuadra y 1 si algo no cuadra.
"""
import argparse
import json
import sys
import urllib.request
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from ledger import sellos as S  # noqa: E402


def pedir(core: str, ruta: str) -> dict:
    url = core.rstrip("/") + ruta
    with urllib.request.urlopen(url, timeout=15) as r:
        return json.loads(r.read().decode("utf-8"))


def main() -> int:
    ap = argparse.ArgumentParser(description="Verifica el ledger KUKIA contra sus sellos")
    ap.add_argument("--db", default=str(RAIZ / "coordinator" / "ledger" / "kukia.db"),
                    help="copia del kukia.db a auditar")
    ap.add_argument("--sellos", default="", help="fichero sellos.jsonl (si no se usa --core)")
    ap.add_argument("--clave", default="", help="clave publica del sello (hex); si falta, se pide al Core")
    ap.add_argument("--core", default="", help="Core vivo del que pedir sellos y clave")
    ap.add_argument("--publicado", default="",
                    help="URL /api/metricas del rele; comprueba el ancla firmada")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    db = Path(args.db)
    if not db.exists():
        print(f"[auditor] no existe el .db: {db}", file=sys.stderr)
        return 2

    if args.core:
        data = pedir(args.core, "/ledger/sellos?limite=200")
        lista = data.get("sellos") or []
        clave = data.get("clave_publica") or ""
        if not lista:
            print("[auditor] el Core no devolvio sellos todavia", file=sys.stderr)
            return 2
    else:
        if not args.sellos:
            args.sellos = str(RAIZ / "coordinator" / "sellos.jsonl")
        lista = S.leer_sellos(args.sellos)
        clave = args.clave
        if not clave:
            key_file = RAIZ / "coordinator" / "sello_key.txt"
            if key_file.exists():
                clave = S.publica_de(S.leer_clave(key_file))
            else:
                print("[auditor] falta la clave publica (--clave)", file=sys.stderr)
                return 2

    con = S._con(db)
    r = S.verificar_sellos(con, lista, clave)

    # Ancla publicada: lo que ya salio de esta maquina. Si el historial local
    # se reescribio y se volvio a firmar, no encadenara con esto.
    ancla = None
    if args.publicado:
        try:
            pet = urllib.request.Request(args.publicado,
                                         headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(pet, timeout=20) as resp:
                ancla = (json.loads(resp.read().decode("utf-8")) or {}).get("ancla")
        except Exception as e:
            print(f"[auditor] ERROR leyendo el ancla publicada: {type(e).__name__}: {e}",
                  file=sys.stderr)
            return 2
        if not ancla:
            print("[auditor] el rele no publica ancla todavia", file=sys.stderr)
            return 2
        pub_ancla = ancla.pop("clave_publica", "")
        firma_ok = S.verificar_firma(ancla, pub_ancla)
        ultimo = lista[-1] if lista else None
        coincide = bool(ultimo) and all(
            ancla.get(k) == ultimo.get(k) for k in ("hash", "hasta", "asientos", "suma"))
        print(f"[auditor] ancla publicada   : hasta {ancla.get('hasta')} "
              f"({ancla.get('asientos')} asientos, {ancla.get('suma')} KIA)")
        print(f"[auditor] firma del ancla   : {'OK' if firma_ok else 'MAL'}")
        print(f"[auditor] encadena con local: {'SI' if coincide else 'NO'}")
        r["ancla"] = {"firma_ok": firma_ok, "coincide": coincide}
        if not firma_ok or not coincide:
            r["ok"] = False
            r.setdefault("problemas", []).append(
                "el ancla publicada no encadena con el historial local")

    if args.json:
        print(json.dumps(r, ensure_ascii=False, indent=2))
    else:
        c = (r.get("cadena") or {}).get("estado") or {}
        print(f"[auditor] .db auditado      : {db}")
        print(f"[auditor] asientos enlazados: {c.get('asientos', 0)}")
        print(f"[auditor] sellos comprobados: {len(lista)}")
        print(f"[auditor] clave publica     : {clave}")
        if lista:
            u = lista[-1]
            print(f"[auditor] ultimo sello      : hasta {u.get('hasta')} "
                  f"({u.get('asientos')} asientos, {u.get('suma')} KIA)")
        if r["ok"]:
            print("[auditor] OK: historial intacto y firmado.")
        else:
            print("[auditor] ALTERADO:")
            for p in r["problemas"][:20]:
                print("  -", p)
    return 0 if r["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
