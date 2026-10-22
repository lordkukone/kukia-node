#!/usr/bin/env python3
"""Comprueba que ninguna clave privada sale de ledger/wallet.

Busca los valores REALES de las billeteras (clave_privada y las 20 palabras)
en todo el proyecto, en carpetas extra y, si se pide, en la web publica.
Nunca imprime el valor del secreto: solo el fichero y la linea.

Uso:
    python tools/auditar_claves.py
    python tools/auditar_claves.py --extra "%TEMP%\\kukia_audit"
    python tools/auditar_claves.py --web https://lordkukone.github.io/kukia-node/

Sale con 1 si encuentra algo, para poder usarlo en un script de lanzamiento.
"""
import argparse
import io
import json
import os
import sys
import urllib.request
import zipfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
WALLETS = RAIZ / "ledger" / "wallet"
SALTAR_DIR = {".git", "__pycache__", "node_modules", ".wrangler"}
# Rutas que la web publica sirve y conviene revisar.
WEB_RUTAS = ["", "index.html", "wallet.html", "codigo.html", "minar.html",
             "whitepaper.html", "metricas.json", "sw.js", "manifest.json",
             "kukia-node.zip"]


def cargar_secretos():
    """Devuelve {valor_secreto: etiqueta}. La etiqueta no revela la clave."""
    secretos = {}
    if not WALLETS.exists():
        print(f"[aviso] no existe {WALLETS}", file=sys.stderr)
        return secretos
    for f in sorted(WALLETS.glob("*.json")):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"[aviso] {f.name} no se pudo leer: {e}", file=sys.stderr)
            continue
        for elem in (d if isinstance(d, list) else [d]):
            if not isinstance(elem, dict):
                continue
            if elem.get("clave_privada"):
                secretos[str(elem["clave_privada"]).lower()] = f"clave privada de {f.name}"
            mn = elem.get("mnemonico")
            if isinstance(mn, list) and mn:
                secretos[" ".join(map(str, mn)).lower()] = f"20 palabras de {f.name}"
            elif isinstance(mn, str) and mn.strip():
                secretos[" ".join(mn.split()).lower()] = f"20 palabras de {f.name}"
    return secretos


def revisar_texto(texto, etiqueta_sitio, secretos):
    """Cuenta coincidencias e imprime solo donde estan (nunca el valor)."""
    texto = texto.lower()
    hallazgos = 0
    for valor, etiqueta in secretos.items():
        if not valor:
            continue
        pos = texto.find(valor)
        if pos == -1:
            continue
        linea = texto.count("\n", 0, pos) + 1
        print(f"  !! {etiqueta_sitio}:{linea} -> {etiqueta}")
        hallazgos += 1
    return hallazgos


def revisar_fichero(ruta, secretos):
    try:
        if ruta.stat().st_size > 40_000_000:
            return 0
        datos = ruta.read_bytes()
    except OSError:
        return 0
    if ruta.suffix.lower() == ".zip":
        try:
            with zipfile.ZipFile(io.BytesIO(datos)) as z:
                total = 0
                for nombre in z.namelist():
                    texto = z.read(nombre).decode("utf-8", "ignore")
                    total += revisar_texto(texto, f"{ruta}!/{nombre}", secretos)
                return total
        except zipfile.BadZipFile:
            return 0
    return revisar_texto(datos.decode("utf-8", "ignore"), str(ruta), secretos)


def revisar_carpeta(carpeta, secretos):
    revisados = 0
    fugas = 0
    for dp, dn, fn in os.walk(carpeta):
        dn[:] = [d for d in dn if d not in SALTAR_DIR]
        for nombre in fn:
            ruta = Path(dp) / nombre
            if ruta.parent.resolve() == WALLETS.resolve():
                continue  # las billeteras son el unico sitio donde deben estar
            revisados += 1
            fugas += revisar_fichero(ruta, secretos)
    return revisados, fugas


def revisar_web(base, secretos):
    base = base.rstrip("/") + "/"
    fugas = 0
    for ruta in WEB_RUTAS:
        url = base + ruta
        try:
            peticion = urllib.request.Request(url, headers={"User-Agent": "KUKIA-auditor"})
            with urllib.request.urlopen(peticion, timeout=25) as r:
                datos = r.read()
        except Exception as e:
            print(f"  [web] {url}: {e}")
            continue
        if ruta.endswith(".zip"):
            try:
                with zipfile.ZipFile(io.BytesIO(datos)) as z:
                    texto = "\n".join(z.read(n).decode("utf-8", "ignore") for n in z.namelist())
            except zipfile.BadZipFile:
                continue
        else:
            texto = datos.decode("utf-8", "ignore")
        fugas += revisar_texto(texto, url, secretos)
    return fugas


def main():
    ap = argparse.ArgumentParser(description="Audita fugas de claves privadas")
    ap.add_argument("--extra", action="append", default=[],
                    help="carpeta adicional a revisar (se puede repetir)")
    ap.add_argument("--web", help="URL base de la web publica a revisar")
    args = ap.parse_args()

    secretos = cargar_secretos()
    print(f"Billeteras: {len(list(WALLETS.glob('*.json')))} | "
          f"secretos distintos: {len(secretos)}")

    revisados, fugas = revisar_carpeta(RAIZ, secretos)
    print(f"Proyecto: {revisados} ficheros revisados")
    for extra in args.extra:
        carpeta = Path(extra).expanduser()
        if not carpeta.exists():
            print(f"[aviso] no existe {carpeta}", file=sys.stderr)
            continue
        n, f = revisar_carpeta(carpeta, secretos)
        revisados += n
        fugas += f
        print(f"Extra {carpeta}: {n} ficheros revisados")

    if args.web:
        f = revisar_web(args.web, secretos)
        fugas += f
        print(f"Web {args.web}: {'OK' if f == 0 else f'{f} coincidencias'}")

    print(f"TOTAL ficheros revisados: {revisados}")
    print("RESULTADO:", "LIMPIO" if fugas == 0 else f"{fugas} FUGA(S)")
    return 0 if fugas == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
