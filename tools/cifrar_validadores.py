"""Cifra con DPAPI las claves privadas de los validadores y lo comprueba.

AUDITORIA 23-10-2026: `validator/identidad_validador.json` y
`identidad_validador_2.json` guardaban `clave_privada` EN CLARO. El validador
firma con ella las atestaciones que manda al Core; si alguien lee el fichero,
puede firmar en su nombre (no cobra ni mueve KIA, pero se pierde la garantia de
que la atestacion la hizo ese equipo).

Que hace, en orden y por cada identidad:
  1. Lee la clave en claro y comprueba que su publica es la que declara el
     fichero (si no cuadra, no se toca: mejor dejarla y avisar).
  2. COPIA el fichero a backups/validadores/ y verifica tamano y sha256.
  3. Cifra la clave con DPAPI y la guarda como `clave_privada_cifrada`,
     borrando la version en claro.
  4. Vuelve a leerla CON EL CODIGO DEL PROPIO VALIDADOR
     (`cargar_identidad`), firma una atestacion igual que la que manda al Core
     (mismo JSON canonico) y la verifica con su clave publica. Ese es el
     "sigue firmando" que importa.

Sin --aplicar solo enseña lo que haria. No se borra nada: las copias quedan.

Uso:
    python tools/cifrar_validadores.py
    python tools/cifrar_validadores.py --aplicar
    python tools/cifrar_validadores.py --aplicar --fichero validator/identidad_validador.json
"""
import argparse
import hashlib
import json
import shutil
import sys
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
CARPETA = RAIZ / "validator"
COPIAS = RAIZ / "backups" / "validadores"

sys.path.insert(0, str(RAIZ / "validator"))
sys.path.insert(0, str(RAIZ / "ledger"))
import kukia_validator as VAL   # noqa: E402  (el codigo que de verdad firma)
import sellos as SELLOS          # noqa: E402

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey  # noqa: E402


def huella(ruta: Path) -> tuple:
    datos = ruta.read_bytes()
    return len(datos), hashlib.sha256(datos).hexdigest()


def copia_verificada(ruta: Path) -> Path:
    COPIAS.mkdir(parents=True, exist_ok=True)
    destino = COPIAS / f"{ruta.stem}_{time.strftime('%Y%m%d_%H%M%S')}{ruta.suffix}"
    shutil.copy2(ruta, destino)
    if huella(ruta) != huella(destino):
        raise SystemExit(f"[validador] la copia de {ruta.name} NO coincide: no se toca nada")
    return destino


def atestacion_de_prueba(publica: str) -> dict:
    """Mismo formato y mismas reglas que valida el Core en /validador/atestacion."""
    return {"validador": "prueba-cifrado", "clave_publica": publica.lower(),
            "desde": round(time.time() - 60, 3), "hasta": round(time.time(), 3),
            "revisadas": 1, "ok": True, "discrepancias": [],
            "huella": "0" * 64, "ts": int(time.time())}


def firma_valida(priv: str, publica: str, datos: dict) -> bool:
    """Firma y verifica igual que el Core (JSON canonico + Ed25519)."""
    try:
        firma = VAL.firmar(priv, SELLOS.canonico(datos))
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(publica)).verify(
            bytes.fromhex(firma), SELLOS.canonico(datos).encode())
        return True
    except Exception:
        return False


def migrar(ruta: Path, aplicar: bool) -> bool:
    d = json.loads(ruta.read_text(encoding="utf-8"))
    if not isinstance(d, dict):
        print(f"  {ruta.name}: no es una identidad")
        return False
    if d.get("clave_privada_cifrada"):
        print(f"  {ruta.name}: ya cifrada, nada que hacer")
        return False
    priv = (d.get("clave_privada") or "").strip()
    if not priv:
        print(f"  {ruta.name}: sin clave privada, nada que hacer")
        return False
    publica = (d.get("clave_publica") or "").strip().lower()
    derivada = SELLOS.publica_de(priv)
    if publica and publica != derivada:
        print(f"  {ruta.name}: la privada NO da la publica del fichero: NO se toca")
        return False
    if not firma_valida(priv, derivada, atestacion_de_prueba(derivada)):
        print(f"  {ruta.name}: la clave no firma bien ANTES de cifrar: NO se toca")
        return False
    print(f"  {ruta.name}: clave EN CLARO. Publica cuadra: {derivada[:16]}…")
    if not aplicar:
        print("     -> se cifraria con DPAPI (usa --aplicar para hacerlo)")
        return False

    copia = copia_verificada(ruta)
    print(f"     copia de seguridad: {copia.relative_to(RAIZ)} "
          f"({huella(copia)[0]} bytes, sha256 {huella(copia)[1][:16]}…)")

    d["clave_publica"] = derivada
    VAL._escribir_identidad(d, ruta)

    # --- comprobacion despues, con el codigo del validador ---
    guardado = json.loads(ruta.read_text(encoding="utf-8"))
    if "clave_privada" in guardado:
        raise SystemExit(f"[validador] {ruta.name} sigue con la clave en claro: revisar")
    cargada = VAL.cargar_identidad(ruta, "")
    if (cargada.get("clave_privada") or "") != priv:
        raise SystemExit(f"[validador] {ruta.name} no devuelve la misma clave al cargar")
    if (cargada.get("clave_publica") or "").lower() != derivada:
        raise SystemExit(f"[validador] {ruta.name} cambio de clave publica al cifrar")
    if not firma_valida(cargada["clave_privada"], derivada, atestacion_de_prueba(derivada)):
        raise SystemExit(f"[validador] {ruta.name} cifrada NO firma bien")
    print(f"     OK cifrada: carga, firma y verifica con la misma publica ({derivada[:16]}…)")
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--aplicar", action="store_true",
                    help="escribe los cambios (sin esto solo enseña)")
    ap.add_argument("--fichero", action="append", default=[],
                    help="fichero concreto (repetible); por defecto identidad*.json")
    ap.add_argument("--carpeta", default=str(CARPETA))
    args = ap.parse_args()

    carpeta = Path(args.carpeta)
    rutas = ([Path(f) for f in args.fichero] if args.fichero
             else sorted(carpeta.glob("identidad*.json")))
    if not rutas:
        print(f"[validador] no hay identidades en {carpeta}")
        return 0
    print(f"[validador] {len(rutas)} identidad(es) | "
          f"{'APLICANDO' if args.aplicar else 'solo mirando (--aplicar para escribir)'}")
    cambiadas = 0
    for ruta in rutas:
        if not ruta.exists():
            print(f"  {ruta}: no existe")
            continue
        try:
            if migrar(ruta, args.aplicar):
                cambiadas += 1
        except SystemExit as e:
            print(f"  {ruta.name}: {e}")
        except Exception as e:
            print(f"  {ruta.name}: error {type(e).__name__}: {e}")
    print(f"[validador] {cambiadas} identidad(es) cifrada(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
