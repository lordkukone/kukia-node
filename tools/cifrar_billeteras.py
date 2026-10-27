"""Cifra con DPAPI las billeteras que guardan la clave privada en claro.

AUDITORIA 23-10-2026: tres ficheros del 13-10 (`nodo_5070.json`,
`nodo_5060ti.json` y `nodo_fundador.json`) guardan la semilla Ed25519 en el
campo `clave`, en claro. No usan `clave_privada`, asi que la migracion
automatica de `ledger/kukia_wallet.py` (que solo mira `clave_privada`) no los
tocaba nunca.

Este script los pasa al MISMO formato que las billeteras nuevas:
`clave_privada_cifrada` (DPAPI del usuario de Windows) + `cifrado`. Al
cargarlas, `kukia_wallet.cargar_billetera()` devuelve la clave en claro en
memoria y todo sigue igual (minero, wallet web, rescate de saldos).

Reglas que respeta:
  - NADA se borra ni se mueve. Antes de escribir, COPIA a backups/billeteras/
    y comprueba tamano y sha256 de la copia.
  - No toca una billetera si la semilla no deriva la direccion que declara el
    fichero (mejor dejarla como esta y avisar que cifrar algo que no cuadra).
  - Sin --aplicar solo enseña lo que haria (por defecto).
  - Despues de escribir, vuelve a leerla y comprueba: descifra la misma semilla,
    la direccion coincide, firma y se verifica, y el saldo del Core no cambia.

Uso:
    python tools/cifrar_billeteras.py                 # ver que haria
    python tools/cifrar_billeteras.py --aplicar       # cifrar
    python tools/cifrar_billeteras.py --nombre nodo_5070 --aplicar
"""
import argparse
import hashlib
import json
import shutil
import sys
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
BILLETERAS = RAIZ / "ledger" / "wallet"
COPIAS = RAIZ / "backups" / "billeteras"
CORE = "http://127.0.0.1:8300"

sys.path.insert(0, str(RAIZ / "ledger"))
import kukia_wallet as kw   # noqa: E402  (necesita la ruta de arriba)


def huella(ruta: Path) -> tuple:
    """(tamano, sha256) de un fichero."""
    datos = ruta.read_bytes()
    return len(datos), hashlib.sha256(datos).hexdigest()


def copia_verificada(ruta: Path) -> Path:
    """Copia el fichero a backups/billeteras y comprueba que es identico."""
    COPIAS.mkdir(parents=True, exist_ok=True)
    destino = COPIAS / f"{ruta.stem}_{time.strftime('%Y%m%d_%H%M%S')}{ruta.suffix}"
    shutil.copy2(ruta, destino)
    if huella(ruta) != huella(destino):
        raise SystemExit(f"[cifrar] la copia de {ruta.name} NO coincide: no se toca nada")
    return destino


def saldo_core(direccion: str):
    """Saldo segun el Core, o None si no responde."""
    return kw.saldo_real(direccion, core=CORE, timeout=4.0)


def semilla_en_claro(d: dict) -> str:
    """La semilla hex que haya en claro (`clave` es el campo viejo)."""
    for campo in ("clave_privada", "clave"):
        valor = (d.get(campo) or "").strip()
        if valor:
            if len(valor) != 64:
                raise SystemExit(f"[cifrar] {campo} no parece una semilla Ed25519 (32 bytes)")
            try:
                bytes.fromhex(valor)
            except ValueError:
                raise SystemExit(f"[cifrar] {campo} no es hexadecimal")
            return valor.lower()
    return ""


def direccion_legada(semilla: str) -> str:
    """Regla vieja (13-10-2026): KUK + sha256(clave privada en hex)[:38].

    La usan los tres ficheros de esa fecha, de cuando la billetera todavia no
    derivaba la direccion de la clave publica. Se reproduce solo para poder
    comprobar que el fichero no esta cruzado; la direccion no se cambia.
    """
    return "KUK" + hashlib.sha256(semilla.encode()).hexdigest()[:38].upper()


def comprobaciones(d: dict, semilla: str) -> tuple:
    """Comprueba que la semilla es la de esa direccion. Devuelve (publica, esquema)."""
    publica, direccion, _ = kw._direccion_y_claves(bytes.fromhex(semilla))
    declarada = (d.get("direccion") or "").strip().upper()
    if not declarada or declarada == direccion:
        esquema = "sha256-pubkey"
    elif declarada == direccion_legada(semilla):
        esquema = "sha256-clave-hex (legado 13-10-2026)"
    else:
        raise SystemExit(f"[cifrar] la semilla no da la direccion {declarada}: NO se toca")
    firma = kw.firmar("prueba-cifrado", semilla)
    if not kw.verificar_firma("prueba-cifrado", firma, publica):
        raise SystemExit("[cifrar] la semilla no firma bien: NO se toca")
    return publica, esquema


def migrar(ruta: Path, aplicar: bool) -> bool:
    """Cifra una billetera. Devuelve True si la ha cambiado."""
    d = json.loads(ruta.read_text(encoding="utf-8"))
    if d.get("clave_privada_cifrada"):
        print(f"  {ruta.name}: ya cifrada, nada que hacer")
        return False
    semilla = semilla_en_claro(d)
    if not semilla:
        print(f"  {ruta.name}: sin clave en claro, nada que hacer")
        return False

    publica, esquema = comprobaciones(d, semilla)
    antes = saldo_core(d.get("direccion", ""))
    print(f"  {ruta.name}: clave EN CLARO (campo viejo). Direccion cuadra "
          f"({esquema}): {d.get('direccion')} | saldo Core: {antes}")

    if not aplicar:
        print("     -> se cifraria con DPAPI (usa --aplicar para hacerlo)")
        return False

    cifrada = kw._cifrar_texto(semilla)
    if not cifrada:
        raise SystemExit("[cifrar] DPAPI no esta disponible aqui: no se toca nada")

    copia = copia_verificada(ruta)
    print(f"     copia de seguridad: {copia.relative_to(RAIZ)} "
          f"({huella(copia)[0]} bytes, sha256 {huella(copia)[1][:16]}...)")

    salida = dict(d)
    salida.pop("clave", None)          # campo viejo, ya cifrado
    salida.pop("clave_privada", None)  # por si alguna lo tuviera tambien
    salida["clave_privada_cifrada"] = cifrada
    salida["cifrado"] = "dpapi-usuario-windows"
    # Solo en las de direccion vieja: deja escrito por que la direccion no es
    # sha256 de la publica. No cambia la direccion, solo la explica.
    if esquema.startswith("sha256-clave-hex"):
        salida["direccion_esquema"] = esquema
    ruta.write_text(json.dumps(salida, indent=2, ensure_ascii=False), encoding="utf-8")
    kw._proteger(ruta)

    # --- comprobacion despues de escribir ---
    guardado = json.loads(ruta.read_text(encoding="utf-8"))
    if "clave" in guardado or "clave_privada" in guardado:
        raise SystemExit(f"[cifrar] {ruta.name} sigue con la clave en claro: revisar")
    if kw._descifrar_texto(guardado["clave_privada_cifrada"]) != semilla:
        raise SystemExit(f"[cifrar] {ruta.name} no descifra la misma semilla")
    cargada = kw.cargar_billetera(ruta)
    if cargada.get("clave_privada") != semilla or cargada.get("direccion") != d.get("direccion"):
        raise SystemExit(f"[cifrar] {ruta.name} no se carga igual despues de cifrar")
    firma = kw.firmar("prueba-cifrado", cargada["clave_privada"])
    if not kw.verificar_firma("prueba-cifrado", firma, publica):
        raise SystemExit(f"[cifrar] {ruta.name} cifrada no firma bien")
    despues = saldo_core(cargada["direccion"])
    if antes is not None and despues != antes:
        raise SystemExit(f"[cifrar] el saldo de {ruta.name} cambio de {antes} a {despues}")
    print(f"     OK cifrada: descifra, firma y la direccion es la misma "
          f"({cargada['direccion']}) | saldo Core: {despues}")
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--aplicar", action="store_true",
                    help="escribe los cambios (sin esto solo enseña)")
    ap.add_argument("--nombre", action="append", default=[],
                    help="billetera concreta, sin .json (repetible); por defecto todas")
    ap.add_argument("--carpeta", default=str(BILLETERAS))
    args = ap.parse_args()

    carpeta = Path(args.carpeta)
    if not carpeta.is_dir():
        raise SystemExit(f"[cifrar] no existe la carpeta {carpeta}")
    if args.nombre:
        rutas = [carpeta / f"{n}" if n.endswith(".json") else carpeta / f"{n}.json"
                 for n in args.nombre]
    else:
        rutas = sorted(carpeta.glob("*.json"))

    print(f"[cifrar] {carpeta} | {len(rutas)} fichero(s) | "
          f"{'APLICANDO' if args.aplicar else 'solo mirando (--aplicar para escribir)'}")
    cambiadas = 0
    for ruta in rutas:
        if not ruta.exists():
            print(f"  {ruta.name}: no existe")
            continue
        try:
            datos = json.loads(ruta.read_text(encoding="utf-8"))
            if not isinstance(datos, dict):
                print(f"  {ruta.name}: no es una billetera (no es un objeto JSON)")
                continue
            if migrar(ruta, args.aplicar):
                cambiadas += 1
        except SystemExit as e:
            print(f"  {ruta.name}: {e}")
        except Exception as e:
            print(f"  {ruta.name}: error {type(e).__name__}: {e}")
    print(f"[cifrar] {cambiadas} billetera(s) cifrada(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
