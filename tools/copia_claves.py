#!/usr/bin/env python3
"""Copia las claves a OTRO disco (N:) y comprueba que la copia sirve.

POR QUE EXISTE
  En el informe de auditoria del 19-10-2026, el punto 2 era este: la clave que
  firma los sellos (`coordinator/sello_key.txt`), el token de admin
  (`coordinator/admin_token.txt`) y las billeteras con su recuperacion viven en
  el MISMO disco que la base de datos. Si ese disco muere, se pierden a la vez
  el historial y la clave: sin la clave no se pueden firmar sellos nuevos, y sin
  los ficheros de recuperacion no se recuperan las billeteras.

QUE HACE
  1. Copia (nunca mueve) a `<destino>/<fecha>/`:
       sello_key.txt, admin_token.txt
       wallet/          (billeteras cifradas + RECUPERACION_*.txt)
  2. Relee la copia y compara el sha256 de cada fichero con el original: si
     algo no cuadra, se para y no da la copia por buena.
  3. Comprueba que la clave COPIADA es la que firma los sellos vivos: con ella
     se recalculan la publica y se compara con la del original. Una copia que
     no puede verificar los sellos no es una copia, es un fichero.
  4. Deja LEEME.txt (que es esto y como se restaura) y MANIFIESTO.json
     (nombre, bytes y sha256 de cada fichero).

  El original NO se toca y NO se borra: el Core necesita `sello_key.txt` donde
  esta. La copia es la que va fuera, para cuando el disco muera.

USO
    python tools/copia_claves.py                 copia a N:\\CLAVES_KUKIA\\<hoy>
    python tools/copia_claves.py --destino E:\\   a otra unidad (USB)
    python tools/copia_claves.py --verificar N:\\CLAVES_KUKIA\\2026-10-22
                                                 recomprueba una copia ya hecha
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

import ledger.sellos as S  # noqa: E402

COORD = RAIZ / "coordinator"
WALLET = RAIZ / "ledger" / "wallet"
CLAVE = COORD / "sello_key.txt"
TOKEN = COORD / "admin_token.txt"
DESTINO_POR_DEFECTO = Path("N:/CLAVES_KUKIA")
LOGS = RAIZ / "logs"
# Tarea de Windows: las claves de hoy copiadas solas una vez por semana, despues
# de la copia de seguridad del domingo (04:00). Asi esto no depende de acordarse.
TAREA = "KUKIA copia de claves"
DIA, HORA = "SUN", "04:20"
SIN_VENTANA = 0x08000000 if os.name == "nt" else 0

LEEME = """KUKIA - claves y recuperacion (copia de {fecha})

QUE HAY AQUI
  sello_key.txt   la clave privada que firma los sellos del ledger. Sin ella el
                  Core no puede firmar sellos nuevos (y no se puede reescribir
                  el historial que ya esta publicado).
  admin_token.txt el token de administracion del Core (loopback).
  wallet/         las billeteras (clave privada cifrada) y los ficheros
                  RECUPERACION_*.txt con la contrasena y las 20 palabras.

SON SECRETOS DE VERDAD
  Quien tenga estos ficheros y la base de datos puede fabricar sellos y mover
  saldos. Esta copia va en un disco que NO sea el del Core, y mejor si uno de
  los dos sitios (este o el del PC) esta apagado o desconectado cuando no se usa.

COMO SE RESTAURA (si se pierde el disco del Core)
  1. Instalar KUKIA en el PC nuevo.
  2. Copiar de aqui a su sitio, respetando nombres:
       sello_key.txt   -> coordinator/sello_key.txt
       admin_token.txt -> coordinator/admin_token.txt
       wallet/*        -> ledger/wallet/
  3. Recuperar la base: la ultima copia de tools/backup_ledger.py o la replica
     de N:/.../actual (kukia.db + sellos.jsonl).
  4. Comprobar que la clave es la buena:  python tools/copia_claves.py --verificar <esta carpeta>
  5. Arrancar el Core y mirar /ledger/integridad: tiene que decir OK.

LAS 20 PALABRAS, EN PAPEL
  La contrasena + las 20 palabras de RECUPERACION_*.txt rehacen la MISMA
  billetera en cualquier equipo. Eso tiene que estar tambien en papel, fuera de
  cualquier disco: es lo unico que no se puede copiar de nuevo.

Manifiesto de esta copia (bytes y sha256): MANIFIESTO.json
"""


def sha256(ruta: Path) -> str:
    h = hashlib.sha256()
    with ruta.open("rb") as f:
        for trozo in iter(lambda: f.read(1 << 20), b""):
            h.update(trozo)
    return h.hexdigest()


def ficheros_a_copiar() -> list:
    """[(origen, ruta_relativa_en_la_copia)]."""
    lista = []
    for f in (CLAVE, TOKEN):
        if f.is_file():
            lista.append((f, Path(f.name)))
    if WALLET.is_dir():
        for f in sorted(WALLET.iterdir()):
            if f.is_file():
                lista.append((f, Path("wallet") / f.name))
    return lista


def publica(clave_priv_hex: str) -> str:
    """Publica (hex) que corresponde a una privada en hex."""
    return S.publica_de(clave_priv_hex.strip())


def revisar(destino: Path) -> dict:
    """Comprueba una copia SIN imprimir nada (lo usa tambien el vigilante).

    Devuelve {ok, problemas:[...], ficheros:[(nombre, bytes, sha)],
    clave_coincide: True/False/None}. Sin ficheros que leer no lanza: si el
    disco esta desconectado, devuelve el problema como texto.
    """
    destino = Path(destino)
    manifiesto = destino / "MANIFIESTO.json"
    if not manifiesto.is_file():
        return {"ok": False, "ficheros": [], "problemas": [f"falta MANIFIESTO.json en {destino}"],
                "clave_coincide": None}
    try:
        datos = json.loads(manifiesto.read_text(encoding="utf-8"))
    except Exception as e:
        return {"ok": False, "ficheros": [],
                "problemas": [f"MANIFIESTO.json ilegible: {type(e).__name__}: {e}"],
                "clave_coincide": None}
    problemas = []
    vistos = []
    for nombre, info in (datos.get("ficheros") or {}).items():
        ruta = destino / nombre
        if not ruta.is_file():
            problemas.append(f"falta {nombre}")
            continue
        h = sha256(ruta)
        vistos.append((nombre, info.get("bytes"), h))
        if h != info.get("sha256"):
            problemas.append(f"{nombre} cambiado (sha256 {h[:12]}... != {str(info.get('sha256'))[:12]}...)")
    # La clave copiada, contra la viva (si existe en este PC).
    clave_coincide = None
    copiada = destino / "sello_key.txt"
    if copiada.is_file() and CLAVE.is_file():
        try:
            pc = publica(CLAVE.read_text(encoding="utf-8"))
            cp = publica(copiada.read_text(encoding="utf-8"))
            clave_coincide = pc == cp
            if not clave_coincide:
                problemas.append(f"la clave del sello no es la del PC ({cp[:12]}... / {pc[:12]}...)")
        except Exception as e:
            problemas.append(f"no pude comparar la clave del sello: {type(e).__name__}")
    return {"ok": not problemas, "ficheros": vistos, "problemas": problemas,
            "clave_coincide": clave_coincide}


def verificar(destino: Path, silencioso: bool = False) -> int:
    """Recomprueba una copia: hashes contra el manifiesto y clave del sello."""
    r = revisar(destino)
    if not (Path(destino) / "MANIFIESTO.json").is_file():
        print(f"[ERROR] {r['problemas'][0]}")
        return 1
    for nombre, nbytes, h in r["ficheros"]:
        if not silencioso:
            print(f"  OK     {nombre}  ({nbytes} B, {h[:12]}...)")
    for p in r["problemas"]:
        if p.startswith("la clave del sello") or p.startswith("no pude comparar") \
                or p.startswith("falta MANIFIESTO"):
            print(f"  {p}")
        elif p.startswith("falta "):
            print(f"  FALTA  {p[6:]}")
        else:
            print(f"  CAMBIADO {p}")
    if r["clave_coincide"] and not silencioso:
        print(f"  OK     la clave copiada es la del sello vivo ({publica(CLAVE.read_text(encoding='utf-8'))[:12]}...)")
    print(f"\n{len(r['ficheros'])} ficheros, {len(r['problemas'])} problemas")
    return 0 if r["ok"] else 1


def copiar(destino: Path) -> int:
    if not CLAVE.is_file():
        print(f"[ERROR] no existe {CLAVE}")
        return 1
    # Mismo disco = no protege de nada. Se avisa y se para.
    letra_origen = RAIZ.drive.upper()
    letra_destino = Path(destino).drive.upper()
    if letra_destino and letra_origen and letra_destino == letra_origen:
        print(f"[ERROR] {destino} esta en el MISMO disco que KUKIA ({letra_origen}).")
        print("        Esta copia solo sirve en otro disco (N:, USB...).")
        return 2

    # Disco desconectado (N: es un WD Elements por USB): mejor decirlo claro que
    # reventar dentro de la tarea semanal sin que nadie lo lea.
    try:
        carpeta = Path(destino) / datetime.now().strftime("%Y-%m-%d")
        carpeta.mkdir(parents=True, exist_ok=True)
        # Copia de hoy ya hecha: se hace otra al lado en vez de pisar nada.
        if (carpeta / "MANIFIESTO.json").exists():
            carpeta = Path(destino) / datetime.now().strftime("%Y-%m-%d_%H%M%S")
            carpeta.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        print(f"[ERROR] no puedo escribir en {destino}: {e}")
        print("        Si es un disco externo (N:), conectalo y repite.")
        return 3

    print(f"origen : {RAIZ}")
    print(f"destino: {carpeta}\n")
    ficheros = {}
    problemas = 0
    for origen, rel in ficheros_a_copiar():
        destino_f = carpeta / rel
        destino_f.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(origen, destino_f)
        h_origen, h_copia = sha256(origen), sha256(destino_f)
        igual = h_origen == h_copia and origen.stat().st_size == destino_f.stat().st_size
        if not igual:
            problemas += 1
            print(f"  MAL    {rel}  (no coincide el sha256)")
        else:
            print(f"  copiado {str(rel):45} {origen.stat().st_size:>7} B  {h_copia[:12]}...")
        ficheros[str(rel).replace("\\", "/")] = {
            "bytes": destino_f.stat().st_size,
            "sha256": h_copia,
            "coincide": igual,
        }

    # La prueba que importa: la clave copiada es la que firma los sellos vivos.
    sellos_ok = None
    try:
        pc = publica(CLAVE.read_text(encoding="utf-8"))
        cp = publica((carpeta / "sello_key.txt").read_text(encoding="utf-8"))
        sellos_ok = pc == cp
        print(f"\n  clave del sello: copia {cp[:12]}... / PC {pc[:12]}... -> "
              + ("la misma" if sellos_ok else "DISTINTA (ojo)"))
        if not sellos_ok:
            problemas += 1
    except Exception as e:
        print(f"\n  [AVISO] no pude comparar la clave copiada: {type(e).__name__}: {e}")

    manifiesto = {
        "que": "claves y recuperacion de KUKIA",
        "copiado": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "origen": str(RAIZ),
        "clave_sello_coincide": sellos_ok,
        "ficheros": ficheros,
    }
    (carpeta / "MANIFIESTO.json").write_text(
        json.dumps(manifiesto, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (carpeta / "LEEME.txt").write_text(
        LEEME.format(fecha=datetime.now().strftime("%d-%m-%Y %H:%M")), encoding="utf-8")

    print(f"\nLEEME.txt y MANIFIESTO.json escritos en {carpeta}")

    # Una copia no tapa el otro agujero: sin RECUPERACION_*.txt, en otro PC (o
    # tras reinstalar Windows) la contrasena y las palabras son la unica llave.
    if WALLET.is_dir():
        sin_rec = [f.stem for f in sorted(WALLET.glob("*.json"))
                   if not (WALLET / f"RECUPERACION_{f.stem}.txt").exists()
                   and not f.stem.startswith("test_")]
        if sin_rec:
            print("\nsin fichero de recuperacion (papel pendiente): " + ", ".join(sin_rec))
            print("  ver las palabras:  python tools/palabras_papel.py")

    # Recomprobacion final leyendo la copia como si viniera de fuera.
    print("\nrecomprobando la copia desde el disco:")
    problemas += verificar(carpeta)

    print("\n" + ("COPIA BIEN" if problemas == 0 else f"COPIA CON {problemas} PROBLEMAS"))
    if problemas == 0:
        print("El original sigue en su sitio (no se ha movido ni borrado nada).")
        print("Pendiente que no es de este script: las 20 palabras de cada")
        print("billetera en papel, y que la clave no viva solo en dos discos del")
        print("mismo PC.")
    return 0 if problemas == 0 else 1


def pythonw() -> str:
    candidato = Path(sys.executable).with_name("pythonw.exe")
    if candidato.exists():
        return str(candidato)
    return shutil.which("pythonw") or "pythonw.exe"


def instalar() -> int:
    """Tarea semanal: domingos 04:20, sin ventana."""
    if os.name != "nt":
        print("[ERROR] esto es para Windows (Programador de tareas)")
        return 2
    script = RAIZ / "tools" / "copia_claves.py"
    orden = f'"{pythonw()}" "{script}"'
    try:
        r = subprocess.run(["schtasks", "/Create", "/SC", "WEEKLY", "/D", DIA,
                            "/ST", HORA, "/TN", TAREA, "/TR", orden, "/F"],
                           capture_output=True, text=True, timeout=60,
                           creationflags=SIN_VENTANA)
    except Exception as e:
        print(f"[ERROR] {type(e).__name__}: {e}")
        return 1
    if r.returncode != 0:
        print("[ERROR] no se pudo crear la tarea:")
        print("        " + ((r.stdout or "") + (r.stderr or "")).strip()[:400])
        return 1
    print(f"tarea '{TAREA}' creada: domingos a las {HORA}, sin ventana")
    print(f"  {orden}")
    print(f"  quitar:  python tools/copia_claves.py --quitar")
    return 0


def quitar() -> int:
    try:
        r = subprocess.run(["schtasks", "/Delete", "/TN", TAREA, "/F"],
                           capture_output=True, text=True, timeout=60,
                           creationflags=SIN_VENTANA)
    except Exception as e:
        print(f"[ERROR] {type(e).__name__}: {e}")
        return 1
    if r.returncode != 0:
        print("[AVISO] no habia tarea o no se pudo borrar: "
              + ((r.stdout or "") + (r.stderr or "")).strip()[:200])
        return 0
    print(f"tarea '{TAREA}' borrada.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Copia las claves de KUKIA a otro disco")
    ap.add_argument("--destino", default=str(DESTINO_POR_DEFECTO),
                    help="carpeta destino (por defecto N:/CLAVES_KUKIA)")
    ap.add_argument("--verificar", metavar="CARPETA",
                    help="solo recomprobar una copia ya hecha")
    ap.add_argument("--instalar", action="store_true",
                    help="tarea de Windows: copia sola los domingos")
    ap.add_argument("--quitar", action="store_true", help="quitar esa tarea")
    args = ap.parse_args()
    if args.instalar:
        return instalar()
    if args.quitar:
        return quitar()
    if args.verificar:
        return verificar(Path(args.verificar))
    return copiar(Path(args.destino))


if __name__ == "__main__":
    # Con pythonw.exe (la tarea de Windows, sin consola) sys.stdout es None y el
    # primer print mataria la copia: se manda la salida a un fichero.
    if sys.stdout is None or sys.stderr is None:
        try:
            LOGS.mkdir(parents=True, exist_ok=True)
            _flujo = open(LOGS / "copia_claves.out.log", "a", encoding="utf-8")
            sys.stdout = sys.stdout or _flujo
            sys.stderr = sys.stderr or _flujo
        except Exception:
            pass
    for _f in (sys.stdout, sys.stderr):
        try:
            _f.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    raise SystemExit(main())
