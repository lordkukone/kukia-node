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
import re
import sys
import urllib.request
import zipfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
WALLETS = RAIZ / "ledger" / "wallet"
SALTAR_DIR = {".git", "__pycache__", "node_modules", ".wrangler"}
# Una billetera copiada fuera de ledger/wallet no se detecta por el valor de la
# clave (va cifrada con DPAPI), pero si por la forma del fichero: el campo
# cifrado siempre lleva un bloque base64 largo. En el codigo ese campo aparece
# como texto corto ("x = cif_priv"), asi que no da falsos positivos.
BILLETERA_COPIADA = re.compile(
    r'"(?:clave_privada_cifrada|mnemonico_cifrado)"\s*:\s*"[A-Za-z0-9+/=]{40,}"')
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
    secretos.update(_secretos_de_recuperacion())
    return secretos


def _secretos_de_recuperacion():
    """Contrasena y 20 palabras de los RECUPERACION_*.txt, que van EN CLARO.

    AUDITORIA (27-10-2026): las billeteras se guardan cifradas con DPAPI, asi
    que `clave_privada_cifrada` no aparece nunca en claro y una copia de una
    billetera no se detectaba. Pero al lado de cada billetera hay un
    RECUPERACION_<nombre>.txt con la contrasena y las 20 palabras sin cifrar:
    copiar ESE fichero es copiar la billetera. Si no se busca, el auditor dice
    LIMPIO pase lo que pase.
    """
    secretos = {}
    for f in sorted(WALLETS.glob("RECUPERACION_*.txt")):
        try:
            lineas = f.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError as e:
            print(f"[aviso] {f.name} no se pudo leer: {e}", file=sys.stderr)
            continue
        nombre = f.stem.replace("RECUPERACION_", "")
        for linea in lineas:
            bajo = linea.lower()
            if bajo.startswith("contrasena:"):
                pwd = linea.split(":", 1)[1].strip()
                # Corta a proposito: una contrasena de 1-2 letras coincidiria
                # con medio proyecto y llenaria el informe de ruido.
                if len(pwd) >= 8:
                    secretos[pwd.lower()] = f"contrasena de {nombre}"
            elif bajo.startswith("20 palabras:"):
                palabras = " ".join(linea.split(":", 1)[1].split())
                if len(palabras.split()) == 20:
                    secretos[palabras.lower()] = f"20 palabras de {nombre}"
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


def revisar_fichero(ruta, secretos, copias_fuera=False):
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
                    crudo = z.read(nombre)
                    texto = crudo.decode("utf-8", "ignore")
                    total += revisar_texto(texto, f"{ruta}!/{nombre}", secretos)
                    if copias_fuera:
                        total += _avisar_billetera_copiada(texto, f"{ruta}!/{nombre}")
                return total
        except zipfile.BadZipFile:
            return 0
    texto = datos.decode("utf-8", "ignore")
    hallazgos = revisar_texto(texto, str(ruta), secretos)
    if copias_fuera:
        hallazgos += _avisar_billetera_copiada(texto, str(ruta))
    return hallazgos


def _avisar_billetera_copiada(texto, etiqueta):
    """Avisa si el fichero tiene pinta de ser una billetera (JSON cifrado)."""
    if BILLETERA_COPIADA.search(texto):
        print(f"  !! {etiqueta} -> COPIA DE BILLETERA fuera de ledger/wallet")
        return 1
    return 0


def revisar_carpeta(carpeta, secretos, copias_fuera=False):
    revisados = 0
    fugas = 0
    for dp, dn, fn in os.walk(carpeta):
        dn[:] = [d for d in dn if d not in SALTAR_DIR]
        for nombre in fn:
            ruta = Path(dp) / nombre
            if ruta.parent.resolve() == WALLETS.resolve():
                continue  # las billeteras son el unico sitio donde deben estar
            revisados += 1
            fugas += revisar_fichero(ruta, secretos, copias_fuera)
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
    if not secretos:
        print("[aviso] no hay ningun secreto EN CLARO que buscar (billeteras cifradas y "
              "sin RECUPERACION_*.txt): un LIMPIO aqui no prueba nada.", file=sys.stderr)

    revisados, fugas = revisar_carpeta(RAIZ, secretos)
    print(f"Proyecto: {revisados} ficheros revisados")
    for extra in args.extra:
        carpeta = Path(extra).expanduser()
        if not carpeta.exists():
            print(f"[aviso] no existe {carpeta}", file=sys.stderr)
            continue
        n, f = revisar_carpeta(carpeta, secretos, copias_fuera=True)
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
