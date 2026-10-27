#!/usr/bin/env python3
"""Cifra el paquete de claves de N: con una contrasena (AES-256-GCM + scrypt).

Para que sirve: los permisos NTFS solo protegen mientras el disco esta puesto en
este PC. Quien se lleve el disco fisico se lleva sello_key.txt y las billeteras.
Un archivo cifrado con contrasena no se abre con el disco: hace falta la frase.

Como esta hecho (sin inventar criptografia)
-------------------------------------------
  clave  = scrypt(contrasena, sal, n=2**15, r=8, p=1, 32 bytes)
  datos  = tar con los ficheros + MANIFIESTO_CIFRADO.json (sha256 de cada uno)
  salida = MAGIC | sal(16) | nonce(12) | n,r,p (3 x uint32) | AESGCM(datos)

  AES-256-GCM es cifrado autenticado: si alguien toca un solo byte del archivo,
  al descifrar falla en vez de dar basura silenciosa. La cabecera va como datos
  autenticados (AAD), asi que tampoco se puede cambiar el algoritmo por detras.

Uso
---
  python tools/cifrar_claves.py                    # cifra el paquete mas reciente
  python tools/cifrar_claves.py --origen N:\\CLAVES_KUKIA\\2026-10-22
  python tools/cifrar_claves.py --verificar N:\\CLAVES_KUKIA\\2026-10-22_cifrado.kukia
  python tools/cifrar_claves.py --verificar ... --contra N:\\CLAVES_KUKIA\\2026-10-22

  La contrasena se pide por teclado (getpass): no va en la linea de comandos, no
  queda en el historial y no la ve nadie. Minimo 12 caracteres.

Lo que NO hace solo
-------------------
  No borra el paquete original. Primero se cifra, se verifica descifrando y
  comparando hash a hash, y se avisa. Borrar el original es una decision aparte
  (--reemplazar, y pide escribir BORRAR a mano).
"""
import argparse
import getpass
import hashlib
import io
import json
import os
import struct
import sys
import tarfile
import time
from pathlib import Path

MAGIC = b"KUKIAENC1"
SAL_LARGO = 16
NONCE_LARGO = 12
SCRYPT_N, SCRYPT_R, SCRYPT_P = 2 ** 15, 8, 1
MINIMO_CLAVE = 12
RAIZ_N = Path(r"N:\CLAVES_KUKIA")


def deriva(contrasena: str, sal: bytes, n: int, r: int, p: int) -> bytes:
    # OpenSSL pone un tope de memoria (32 MB por defecto) y scrypt necesita
    # 128*N*r bytes: con N=2^15 y r=8 son 33,5 MB y fallaba con "memory limit
    # exceeded". Se pide el doble de lo que hace falta, que es honesto y sigue
    # acotado (no se puede pedir un N absurdo desde el archivo sin que reviente).
    necesaria = 128 * n * r
    return hashlib.scrypt(contrasena.encode("utf-8"), salt=sal, n=n, r=r, p=p, dklen=32,
                          maxmem=necesaria * 2 + 1024 * 1024)


def _sha256(ruta: Path) -> str:
    h = hashlib.sha256()
    with ruta.open("rb") as f:
        for trozo in iter(lambda: f.read(1 << 20), b""):
            h.update(trozo)
    return h.hexdigest()


def empaquetar(origen: Path) -> tuple[bytes, dict]:
    """tar en memoria con los ficheros + manifiesto de hashes."""
    ficheros = sorted(p for p in origen.rglob("*") if p.is_file())
    if not ficheros:
        raise SystemExit(f"[cifrar] no hay ficheros en {origen}")
    manifiesto = {"origen": str(origen), "creado": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                                               time.gmtime()),
                  "ficheros": {}}
    datos = io.BytesIO()
    with tarfile.open(fileobj=datos, mode="w") as tar:
        for p in ficheros:
            rel = str(p.relative_to(origen)).replace("\\", "/")
            h = _sha256(p)
            manifiesto["ficheros"][rel] = {"sha256": h, "bytes": p.stat().st_size}
            tar.add(str(p), arcname=rel)
        m = json.dumps(manifiesto, ensure_ascii=False, indent=2).encode("utf-8")
        info = tarfile.TarInfo("MANIFIESTO_CIFRADO.json")
        info.size = len(m)
        info.mtime = int(time.time())
        tar.addfile(info, io.BytesIO(m))
    return datos.getvalue(), manifiesto


def cifrar(datos: bytes, contrasena: str) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    sal = os.urandom(SAL_LARGO)
    nonce = os.urandom(NONCE_LARGO)
    cabecera = MAGIC + struct.pack(">III", SCRYPT_N, SCRYPT_R, SCRYPT_P)
    clave = deriva(contrasena, sal, SCRYPT_N, SCRYPT_R, SCRYPT_P)
    cifrado = AESGCM(clave).encrypt(nonce, datos, cabecera)
    return cabecera + sal + nonce + cifrado


def descifrar(blob: bytes, contrasena: str) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.exceptions import InvalidTag
    if len(blob) < len(MAGIC) + 12 + SAL_LARGO + NONCE_LARGO:
        raise SystemExit("[cifrar] el archivo es demasiado corto: no es nuestro")
    if not blob.startswith(MAGIC):
        raise SystemExit("[cifrar] no empieza por KUKIAENC1: no es un paquete cifrado "
                         "por esta herramienta")
    i = len(MAGIC)
    n, r, p = struct.unpack(">III", blob[i:i + 12])
    i += 12
    cabecera = MAGIC + struct.pack(">III", n, r, p)
    sal = blob[i:i + SAL_LARGO]
    i += SAL_LARGO
    nonce = blob[i:i + NONCE_LARGO]
    i += NONCE_LARGO
    clave = deriva(contrasena, sal, n, r, p)
    try:
        return AESGCM(clave).decrypt(nonce, blob[i:], cabecera)
    except InvalidTag:
        raise SystemExit("[cifrar] contrasena incorrecta (o el archivo esta tocado): "
                         "GCM no ha validado la firma")


def leer_manifiesto(datos_tar: bytes) -> dict:
    with tarfile.open(fileobj=io.BytesIO(datos_tar), mode="r") as tar:
        m = tar.extractfile("MANIFIESTO_CIFRADO.json")
        if m is None:
            raise SystemExit("[cifrar] el paquete no lleva manifiesto")
        return json.loads(m.read().decode("utf-8"))


def pedir_contrasena(confirmar: bool) -> str:
    c1 = getpass.getpass("  contrasena: ")
    if len(c1) < MINIMO_CLAVE:
        raise SystemExit(f"[cifrar] minimo {MINIMO_CLAVE} caracteres. No vale una palabra "
                         "corta: esto es lo unico que separa el disco de tus claves.")
    if confirmar:
        c2 = getpass.getpass("  repitela:   ")
        if c1 != c2:
            raise SystemExit("[cifrar] no coinciden")
    return c1


def paquete_mas_reciente() -> Path:
    if not RAIZ_N.exists():
        raise SystemExit(f"[cifrar] no existe {RAIZ_N}")
    carpetas = [p for p in RAIZ_N.iterdir() if p.is_dir() and p.name[:4].isdigit()]
    if not carpetas:
        raise SystemExit(f"[cifrar] no hay ningun paquete fechado en {RAIZ_N}")
    return sorted(carpetas)[-1]


def modo_cifrar(origen: Path, salida: Path) -> int:
    print(f"\n  CIFRAR EL PAQUETE DE CLAVES\n  {'=' * 52}")
    print(f"  origen  : {origen}")
    print(f"  salida  : {salida}")
    print("  La contrasena NO se guarda en ningun sitio. Si la pierdes, el paquete")
    print("  no se abre: ni yo ni nadie. Apuntala en papel o en un gestor.")
    print()
    datos, manifiesto = empaquetar(origen)
    print(f"  {len(manifiesto['ficheros'])} ficheros, {len(datos)} bytes antes de cifrar")
    contrasena = pedir_contrasena(confirmar=True)

    t0 = time.time()
    blob = cifrar(datos, contrasena)
    print(f"  cifrado en {time.time() - t0:.1f}s -> {len(blob)} bytes")

    # Verificacion honesta: se descifra EN MEMORIA con la misma contrasena y se
    # compara hash a hash. Si esto no cuadra, no se escribe nada.
    vuelta = descifrar(blob, contrasena)
    if vuelta != datos:
        raise SystemExit("[cifrar] ABORTADO: lo descifrado no es identico a lo empaquetado")
    m2 = leer_manifiesto(vuelta)
    malos = []
    for rel, info in manifiesto["ficheros"].items():
        if m2["ficheros"].get(rel, {}).get("sha256") != info["sha256"]:
            malos.append(rel)
    if malos:
        raise SystemExit("[cifrar] ABORTADO, hashes distintos: " + ", ".join(malos))

    salida.parent.mkdir(parents=True, exist_ok=True)
    tmp = salida.with_suffix(salida.suffix + ".tmp")
    tmp.write_bytes(blob)
    tmp.replace(salida)
    h = _sha256(salida)
    print(f"  verificado: {len(manifiesto['ficheros'])} ficheros identicos tras descifrar")
    print(f"  escrito  : {salida}  ({salida.stat().st_size} B)")
    print(f"  sha256   : {h}")
    print()
    print("  El paquete ORIGINAL sigue ahi, en claro. Mientras siga, el disco se")
    print("  lleva las claves igual: cifrar solo sirve si borras el original.")
    print("  Para eso: python tools/cifrar_claves.py --reemplazar")
    print()
    return 0


def modo_verificar(archivo: Path, contra: Path | None) -> int:
    print(f"\n  VERIFICAR {archivo}")
    contrasena = pedir_contrasena(confirmar=False)
    datos = descifrar(archivo.read_bytes(), contrasena)
    manifiesto = leer_manifiesto(datos)
    print(f"  dentro: {len(manifiesto['ficheros'])} ficheros, creado {manifiesto['creado']}")
    problemas = 0
    with tarfile.open(fileobj=io.BytesIO(datos), mode="r") as tar:
        for rel, info in sorted(manifiesto["ficheros"].items()):
            miembro = tar.extractfile(rel)
            real = hashlib.sha256(miembro.read()).hexdigest() if miembro else None
            ok = real == info["sha256"]
            problemas += 0 if ok else 1
            print(f"   {'OK  ' if ok else 'MAL '} {rel}  ({info['bytes']} B)")
    if contra:
        print(f"  comparando con {contra} ...")
        for rel, info in sorted(manifiesto["ficheros"].items()):
            p = contra / rel
            if not p.exists():
                print(f"   FALTA {rel}")
                problemas += 1
            elif _sha256(p) != info["sha256"]:
                print(f"   DISTINTO {rel}")
                problemas += 1
        print("  el paquete en claro coincide con lo cifrado" if problemas == 0
              else f"  {problemas} diferencias")
    print(f"\n  {problemas} problemas\n")
    return 0 if problemas == 0 else 1


def modo_reemplazar(origen: Path, archivo: Path, contra: Path | None = None) -> int:
    """Verifica el cifrado contra el original y solo entonces borra el original."""
    print(f"\n  REEMPLAZAR: borrar {origen} y quedarse con {archivo.name}")
    print("  Se descifra y se compara primero. Si algo no cuadra, no se borra nada.")
    contrasena = pedir_contrasena(confirmar=False)
    datos = descifrar(archivo.read_bytes(), contrasena)
    manifiesto = leer_manifiesto(datos)
    with tarfile.open(fileobj=io.BytesIO(datos), mode="r") as tar:
        for rel, info in manifiesto["ficheros"].items():
            p = origen / rel
            miembro = tar.extractfile(rel)
            if miembro is None or not p.exists() or _sha256(p) != hashlib.sha256(
                    miembro.read()).hexdigest():
                raise SystemExit(f"[cifrar] ABORTADO: {rel} no coincide. No borro nada.")
    print(f"  los {len(manifiesto['ficheros'])} ficheros cuadran con el original.")
    print("  Escribe BORRAR (mayusculas) para eliminar el paquete en claro:")
    if input("  > ").strip() != "BORRAR":
        print("  no se ha borrado nada.")
        return 1
    import shutil
    shutil.rmtree(origen)
    print(f"  borrado {origen}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Cifra el paquete de claves de N: (AES-256-GCM + scrypt).")
    ap.add_argument("--origen", default="", help="carpeta a cifrar (por defecto, la ultima)")
    ap.add_argument("--salida", default="", help="archivo .kukia de salida")
    ap.add_argument("--verificar", default="", help="descifra y comprueba un .kukia")
    ap.add_argument("--contra", default="", help="carpeta en claro para comparar")
    ap.add_argument("--reemplazar", action="store_true",
                    help="verifica y luego borra el original (pide confirmacion)")
    args = ap.parse_args(argv)

    origen = Path(args.origen) if args.origen else paquete_mas_reciente()
    if args.verificar:
        return modo_verificar(Path(args.verificar),
                              Path(args.contra) if args.contra else None)
    if not origen.exists():
        raise SystemExit(f"[cifrar] no existe {origen}")
    salida = Path(args.salida) if args.salida else origen.with_name(origen.name + "_cifrado.kukia")
    if args.reemplazar:
        if not salida.exists():
            raise SystemExit(f"[cifrar] no existe {salida}: cifra primero")
        return modo_reemplazar(origen, salida)
    return modo_cifrar(origen, salida)


if __name__ == "__main__":
    for _f in (sys.stdout, sys.stderr):
        try:
            _f.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    raise SystemExit(main())
