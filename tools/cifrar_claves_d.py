#!/usr/bin/env python3
"""Cifra las copias EN CLARO de sello_key.txt y admin_token.txt que hay en D:.

Por que existe
--------------
`tools/backup_ledger.py` copia `sello_key.txt` en CADA copia de seguridad (va
por defecto; `--con-claves` anade el token) y `tools/arrancar_replica.py` deja
otra pareja en `D:\\KUKIA_REPLICA\\actual` y `...\\anterior`. Resultado: un disco
que NO es el del Core acumula copias en claro de la clave privada que firma el
ledger. Quien se lleve ese disco se lleva la moneda.

Que cifra y que no
------------------
Solo los ficheros cuyo nombre es EXACTAMENTE `sello_key.txt` o
`admin_token.txt`, buscados bajo las raices indicadas. No se cifra la carpeta
entera a proposito: `kukia.db` y `sellos.jsonl` no son secretos y suman cientos
de MB.

Formato
-------
El mismo que `tools/cifrar_claves.py` (AES-256-GCM + scrypt), reutilizando su
codigo. El paquete resultante se abre con esa misma herramienta:

    python tools/cifrar_claves.py --verificar D:\\CLAVES_KUKIA\\claves_D_....kukia

Uso
---
    python tools/cifrar_claves_d.py --listar        # solo enseña que hay
    python tools/cifrar_claves_d.py                 # cifra y verifica (no borra)
    python tools/cifrar_claves_d.py --borrar        # verifica y borra los originales

La contrasena se pide por teclado (getpass): no va en la linea de comandos, no
queda en el historial. Minimo 12 caracteres.

LO QUE NO ARREGLA
-----------------
Esto limpia lo que ya hay. La proxima vez que se ejecute `backup_ledger.py` con
sus valores por defecto volvera a aparecer una copia en claro. Para que no
vuelva hay que dejar de copiar la clave privada en las copias (`--sin-clave`) y
que `arrancar_replica.py` lea la clave del original en vez de duplicarla.
"""
import argparse
import datetime as dt
import hashlib
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cifrar_claves as cc  # noqa: E402  (mismo formato, mismo codigo)

UNIDAD = Path("D:/")
RAICES = [UNIDAD / "KUKIA_REPLICA", UNIDAD / "KUKIA_backups"]
NOMBRES = ("sello_key.txt", "admin_token.txt")
DESTINO = UNIDAD / "CLAVES_KUKIA"


def _sha256(ruta: Path) -> str:
    h = hashlib.sha256()
    with ruta.open("rb") as f:
        for trozo in iter(lambda: f.read(1 << 20), b""):
            h.update(trozo)
    return h.hexdigest()


def buscar(raices=None) -> list[Path]:
    """Los ficheros sensibles que hay ahora mismo, en orden estable."""
    encontrados = []
    for raiz in (RAICES if raices is None else raices):
        if not raiz.exists():
            continue
        for nombre in NOMBRES:
            encontrados.extend(sorted(p for p in raiz.rglob(nombre) if p.is_file()))
    return encontrados


def _etiqueta(ruta: Path) -> str:
    """Ruta relativa a la unidad, para dentro del paquete: KUKIA_REPLICA/actual/..."""
    return ruta.relative_to(UNIDAD).as_posix()


def _desde_etiqueta(rel: str) -> Path:
    """Vuelta atras: KUKIA_REPLICA/actual/sello_key.txt -> D:\\KUKIA_REPLICA\\..."""
    return UNIDAD / rel


def listar() -> int:
    ficheros = buscar()
    if not ficheros:
        print("\n  No hay sello_key.txt ni admin_token.txt en D:. Nada que hacer.\n")
        return 0
    print(f"\n  {len(ficheros)} ficheros sensibles en claro:\n")
    total = 0
    for p in ficheros:
        total += p.stat().st_size
        print(f"   {p.stat().st_size:>6} B  {_etiqueta(p)}")
        print(f"            sha256 {_sha256(p)}")
    if not DESTINO.exists():
        print(f"\n  (el paquete se escribiria en {DESTINO})")
    print(f"\n  total {total} B\n")
    return 0


def modo_cifrar(salida: Path) -> int:
    ficheros = buscar()
    if not ficheros:
        print("\n  No hay nada que cifrar en D:.\n")
        return 0
    print(f"\n  CIFRAR LAS CLAVES EN CLARO DE D:\n  {'=' * 54}")
    print(f"  ficheros: {len(ficheros)}")
    print(f"  salida  : {salida}")
    print("  La contrasena NO se guarda. Sin ella el paquete no se abre.\n")
    for p in ficheros:
        print(f"   {_etiqueta(p)}")
    print()

    # Se empaqueta desde una carpeta de paso para reutilizar el empaquetador
    # original (mismo formato, mismo manifiesto) sin meter los .db de al lado.
    paso = Path(tempfile.mkdtemp(prefix="kukia_claves_d_"))
    try:
        for p in ficheros:
            destino = paso / _etiqueta(p)
            destino.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(p, destino)
        datos, manifiesto = cc.empaquetar(paso)
    finally:
        shutil.rmtree(paso, ignore_errors=True)

    print(f"  {len(manifiesto['ficheros'])} ficheros, {len(datos)} B antes de cifrar")
    contrasena = cc.pedir_contrasena(confirmar=True)

    blob = cc.cifrar(datos, contrasena)

    # Verificacion honesta, igual que en cifrar_claves.py: descifrar y comparar
    # hash a hash. Si no cuadra, no se escribe nada.
    vuelta = cc.descifrar(blob, contrasena)
    if vuelta != datos:
        raise SystemExit("[cifrar_d] ABORTADO: lo descifrado no es identico a lo empaquetado")
    m2 = cc.leer_manifiesto(vuelta)
    malos = [rel for rel, info in manifiesto["ficheros"].items()
             if m2["ficheros"].get(rel, {}).get("sha256") != info["sha256"]]
    if malos:
        raise SystemExit("[cifrar_d] ABORTADO, hashes distintos: " + ", ".join(malos))

    salida.parent.mkdir(parents=True, exist_ok=True)
    tmp = salida.with_suffix(salida.suffix + ".tmp")
    tmp.write_bytes(blob)
    tmp.replace(salida)

    print(f"  verificado: {len(manifiesto['ficheros'])} ficheros identicos tras descifrar")
    print(f"  escrito  : {salida}  ({salida.stat().st_size} B)")
    print(f"  sha256   : {_sha256(salida)}")
    print()
    print("  Los originales SIGUEN en D:, en claro. El siguiente paso es")
    print("  python tools/cifrar_claves_d.py --borrar")
    print()
    return 0


def modo_borrar(archivo: Path) -> int:
    if not archivo.exists():
        raise SystemExit(f"[cifrar_d] no existe {archivo}: cifra primero")
    print(f"\n  BORRAR LAS CLAVES EN CLARO DE D:\n  {'=' * 54}")
    print(f"  paquete: {archivo.name}  ({archivo.stat().st_size} B)")
    print("  Se descifra y se compara fichero a fichero ANTES de borrar nada.\n")
    contrasena = cc.pedir_contrasena(confirmar=False)
    datos = cc.descifrar(archivo.read_bytes(), contrasena)
    manifiesto = cc.leer_manifiesto(datos)

    a_borrar = []
    problemas = 0
    for rel, info in sorted(manifiesto["ficheros"].items()):
        ruta = _desde_etiqueta(rel)
        if not ruta.exists():
            print(f"   YA NO ESTA  {rel}")
            continue
        if _sha256(ruta) != info["sha256"]:
            # El fichero cambio desde que se cifro: puede ser una clave nueva.
            # No se borra lo que el paquete no contiene.
            print(f"   DISTINTO    {rel}  <-- no lo toco")
            problemas += 1
            continue
        a_borrar.append((ruta, rel, info["bytes"]))
        print(f"   CUADRA      {rel}  ({info['bytes']} B)")

    if not a_borrar:
        print("\n  No hay nada que borrar con seguridad.\n")
        return 1
    if problemas:
        print(f"\n  {problemas} ficheros NO cuadran con el paquete: se quedan como estan.")
        print("  Vuelve a cifrar (los 'DISTINTO' entran en un paquete nuevo).\n")
        return 1

    print(f"\n  Se van a borrar {len(a_borrar)} ficheros. Escribe BORRAR (mayusculas):")
    if input("  > ").strip() != "BORRAR":
        print("  no se ha borrado nada.\n")
        return 1
    for ruta, rel, _bytes in a_borrar:
        ruta.unlink()
        print(f"  borrado {ruta}")

    print("\n  Estado final:")
    for raiz in RAICES:
        if raiz.exists():
            print(f"   {raiz.relative_to(UNIDAD)}: {len(buscar([raiz]))} ficheros "
                  f"sensibles en claro")
    print(f"  paquete: {archivo}")
    print()
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Cifra sello_key.txt y admin_token.txt que estan en claro en D:.")
    ap.add_argument("--listar", action="store_true", help="solo enseña que hay")
    ap.add_argument("--borrar", action="store_true", help="verifica y borra los originales")
    ap.add_argument("--verificar", default="", help="descifra y comprueba un .kukia")
    ap.add_argument("--salida", default="", help="archivo .kukia de salida")
    args = ap.parse_args(argv)

    hoy = dt.date.today().isoformat()
    salida = Path(args.salida) if args.salida else DESTINO / f"claves_D_{hoy}.kukia"

    if args.listar:
        return listar()
    if args.verificar:
        return cc.modo_verificar(Path(args.verificar), None)
    if args.borrar:
        return modo_borrar(salida)
    return modo_cifrar(salida)


if __name__ == "__main__":
    for _f in (sys.stdout, sys.stderr):
        try:
            _f.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    raise SystemExit(main())
