#!/usr/bin/env python3
"""Las 20 palabras de cada billetera, para apuntarlas en papel.

POR QUE EXISTE
  Las billeteras de KUKIA se cifran con DPAPI del usuario de Windows: en ESTE
  PC y con ESTA cuenta se abren solas. En otro PC (o tras reinstalar Windows)
  DPAPI no vale y la unica forma de recuperar la billetera es
  contrasena + 20 palabras (`derivar_clave` -> PBKDF2). Hasta hoy esas palabras
  solo existian dentro del JSON, cifradas, y en papel no estaban: si se perdia
  el PC y la contrasena, los KIA se quedaban sin puerta.

QUE HACE
  1. Abre cada billetera con DPAPI (sin pedir contrasena) y saca sus 20
     palabras y su direccion.
  2. Las escribe en una hoja lista para imprimir en el disco de las copias
     (por defecto N:/CLAVES_KUKIA/<hoy>/PALABRAS_PARA_PAPEL.txt) y las muestra
     por pantalla.
  3. `--comprobar NOMBRE` pide la contrasena a mano (no se guarda) y comprueba
     que contrasena + palabras dan LA MISMA direccion. Esa es la prueba de que
     lo que has apuntado en papel sirve de verdad.

QUE HACER CON LA HOJA
  Imprimir, escribir a mano la contrasena en la linea que queda en blanco,
  guardar el papel fuera de cualquier disco... y borrar el fichero del disco
  cuando este impreso (es un secreto en claro).

USO
    python tools/palabras_papel.py                       hoja de todas
    python tools/palabras_papel.py --incluir-pruebas     tambien las test_*
    python tools/palabras_papel.py --destino E:\\mia\\    hoja a otro sitio
    python tools/palabras_papel.py --comprobar kuki_fundador_nueva
"""

from __future__ import annotations

import argparse
import getpass
import json
import sys
from datetime import datetime
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from ledger import kukia_wallet as W  # noqa: E402

WALLET = RAIZ / "ledger" / "wallet"
DESTINO_POR_DEFECTO = Path("N:/CLAVES_KUKIA")

CABECERA = """KUKIA - PALABRAS DE RECUPERACION (para pasar a papel)
=======================================================
{Fecha}

Con la CONTRASENA (la que pusiste al crear la billetera) + las 20 palabras se
recupera LA MISMA billetera en cualquier PC:

    python ledger/kukia_wallet.py recuperar <nombre>      (o desde la web)

Escribe la contrasena A MANO en la linea de puntos. Estas palabras no se pueden
volver a sacar de ningun sitio si se pierde el PC y la copia: este papel es la
unica puerta de repuesto.
"""


def billeteras() -> list:
    return sorted(WALLET.glob("*.json"))


def pedir_contrasena() -> str:
    """La contrasena, por consola y sin dejarla escrita en ningun sitio.

    Con consola de verdad se usa getpass (no se ve al teclear). En Windows
    getpass lee del teclado y, si no hay consola, se queda esperando para
    siempre: por eso, cuando la entrada viene de una tuberia, se lee de ahi.
    """
    try:
        if sys.stdin is not None and sys.stdin.isatty():
            return getpass.getpass("contrasena: ")
    except Exception:
        pass
    print("contrasena (por la entrada estandar):")
    linea = sys.stdin.readline() if sys.stdin else ""
    return linea.strip()


def abrir(ruta: Path) -> dict | None:
    """Datos de una billetera, o None si no se puede descifrar en este PC.

    Se lee el JSON a mano y solo se descifran las palabras. NO se usa
    `cargar_billetera` a proposito: ese, con una billetera antigua en claro,
    la vuelve a escribir cifrada al leerla, y aqui solo se viene a mirar.
    """
    try:
        d = json.loads(ruta.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(d, dict):
        return None
    if d.get("mnemonico"):
        palabras = list(d["mnemonico"])
    elif d.get("mnemonico_cifrado"):
        texto = W._descifrar_texto(d["mnemonico_cifrado"]) or ""
        palabras = texto.split()
    else:
        palabras = []
    return {"nombre": ruta.stem, "direccion": d.get("direccion") or "",
            "palabras": palabras, "kdf": int(d.get("kdf", 1)),
            "cifrado": d.get("cifrado"), "version": d.get("version"),
            "saldo": d.get("saldo")}


def bloque(d: dict) -> str:
    lineas = [
        "-" * 66,
        f"{d['nombre']}",
        f"direccion: {d['direccion']}",
        f"contrasena: {'_' * 46}",
        "",
    ]
    pal = d["palabras"]
    for i in range(0, len(pal), 4):
        linea = "  ".join(f"{i + j + 1:>2}. {w:<14}" for j, w in enumerate(pal[i:i + 4]))
        lineas.append("   " + linea)
    lineas.append("")
    return "\n".join(lineas)


def hoja(destino: Path, incluir_pruebas: bool) -> int:
    con_palabras, sin_palabras, ilegibles = [], [], []
    for ruta in billeteras():
        if not incluir_pruebas and ruta.stem.startswith("test_"):
            continue
        d = abrir(ruta)
        if d is None:
            ilegibles.append(ruta.stem)
        elif len(d["palabras"]) == 20:
            con_palabras.append(d)
        else:
            sin_palabras.append(d)

    if not con_palabras:
        print("[AVISO] ninguna billetera tiene palabras que se puedan leer en este PC")
    texto = [CABECERA.format(Fecha=datetime.now().strftime("%d-%m-%Y %H:%M"))]
    for d in con_palabras:
        texto.append(bloque(d))
    if sin_palabras:
        texto.append("SIN PALABRAS GUARDADAS (solo clave cifrada; no hay papel posible)\n")
        for d in sin_palabras:
            texto.append(f"  {d['nombre']:<28} {d['direccion']}\n")
        texto.append("\n")
    if ilegibles:
        texto.append("NO SE PUDIERON ABRIR EN ESTE PC (otro usuario de Windows):\n")
        texto.append("  " + ", ".join(ilegibles) + "\n\n")
    cuerpo = "".join(texto)

    # La hoja al lado de la copia de claves, no en el proyecto.
    destino = Path(destino)
    semana = destino / datetime.now().strftime("%Y-%m-%d")
    semana.mkdir(parents=True, exist_ok=True)
    ruta_hoja = semana / "PALABRAS_PARA_PAPEL.txt"
    ruta_hoja.write_text(cuerpo, encoding="utf-8")

    print(cuerpo)
    print(f"hoja escrita en {ruta_hoja}")
    print("IMPRIMELA, escribe la contrasena a mano y BORRA el fichero:")
    print(f"  del \"{ruta_hoja}\"")
    print("\nCuando la tengas en papel, comprueba que sirve:")
    for d in con_palabras:
        print(f"  python tools/palabras_papel.py --comprobar {d['nombre']}")
    return 0


def comprobar(nombre: str) -> int:
    ruta = WALLET / f"{nombre}.json"
    if not ruta.is_file():
        ruta = WALLET / f"{nombre}"
    if not ruta.is_file():
        print(f"[ERROR] no existe {ruta}")
        return 2
    d = abrir(ruta)
    if d is None:
        print(f"[ERROR] no puedo abrir {ruta.name} en este PC (otro usuario de Windows)")
        return 2
    if len(d["palabras"]) != 20:
        print(f"[ERROR] {d['nombre']} no tiene 20 palabras guardadas")
        return 2
    print(f"billetera: {d['nombre']}  ({d['direccion']})")
    print("escribe la contrasena que apuntaste en el papel (no se guarda, no se muestra)")
    pwd = pedir_contrasena()
    if not pwd:
        print("[ERROR] sin contrasena no se puede comprobar")
        return 2
    try:
        semilla = W.derivar_clave(pwd, d["palabras"], d["kdf"])[:32]
        _pub, direccion, _esq = W._direccion_y_claves(semilla)
    except Exception as e:
        print(f"[ERROR] no pude derivar la clave: {type(e).__name__}: {e}")
        return 2
    if direccion == d["direccion"]:
        print(f"\nOK: contrasena + palabras dan {direccion}")
        print("Ese papel recupera la billetera. Puedes estar tranquilo.")
        return 0
    print(f"\nMAL: contrasena + palabras dan {direccion}, que no es {d['direccion']}")
    print("O la contrasena esta mal escrita, o las palabras apuntadas no son estas.")
    return 1


def main() -> int:
    ap = argparse.ArgumentParser(description="Las 20 palabras de las billeteras, para papel")
    ap.add_argument("--destino", default=str(DESTINO_POR_DEFECTO),
                    help="donde dejar la hoja (por defecto N:/CLAVES_KUKIA)")
    ap.add_argument("--incluir-pruebas", action="store_true",
                    help="incluir tambien las billeteras test_*")
    ap.add_argument("--comprobar", metavar="BILLETERA",
                    help="comprobar contrasena + palabras de una billetera")
    args = ap.parse_args()
    if args.comprobar:
        return comprobar(args.comprobar)
    return hoja(Path(args.destino), args.incluir_pruebas)


if __name__ == "__main__":
    for _f in (sys.stdout, sys.stderr):
        try:
            _f.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    raise SystemExit(main())
