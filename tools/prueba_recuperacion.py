"""Prueba de recuperacion de una billetera KUKIA, en local y sin dejar rastro.

Pensado para comprobar que una frase de recuperacion (contrasena + 20 palabras)
deriva EXACTAMENTE la misma direccion publica, sin tocar el ledger, sin mover
KIA y sin que la frase salga del equipo.

Lo que hace:
  - Pide la contrasena y las 20 palabras por teclado y OCULTAS (getpass: no se
    ven en pantalla ni acaban en el historial del shell).
  - Deriva la clave y la direccion con el mismo codigo que usa la billetera
    (`ledger/kukia_wallet.py`).
  - Dice la direccion que sale y si coincide con la esperada. Nada mas.

Lo que NO hace (a proposito):
  - No escribe ningun fichero ni log.
  - No hace NINGUNA peticion de red (ni al Core, ni a nada). Se puede correr en
    un portatil aislado o en una maquina virtual sin red.
  - No imprime la frase, ni la clave privada, ni la clave publica completa.
  - No importa la clave en ninguna billetera ni firma nada que se envie.

Uso (equipo aislado, sin copiar ficheros de billetera):
    python tools/prueba_recuperacion.py --dir KUK9E8B44CD7FE3AF178039453D3EEDF391EEC0CE --kdf 1

`--kdf` es la version del derivador que declara el fichero de la billetera:
1 si no tiene campo `kdf` (las antiguas, como la fundadora), 2 si lo tiene a 2.
Si no se sabe, se prueba primero 1 y, si no cuadra, 2: son dos intentos a mano,
nunca un barrido automatico.
"""
import argparse
import gc
import getpass
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "ledger"))
import kukia_wallet as kw   # noqa: E402


def leer_oculto(etiqueta: str) -> str:
    """Lee una linea sin mostrarla.

    En un terminal de verdad siempre getpass (no se ve lo que escribes ni va al
    historial). Si no hay terminal (tuberia, prueba automatica) se lee de
    stdin: asi el script se puede probar sin teclado, nunca al reves.
    """
    if sys.stdin is None or not sys.stdin.isatty():
        print(etiqueta, end="", flush=True)
        return (sys.stdin.readline() if sys.stdin else "").rstrip("\r\n")
    return getpass.getpass(etiqueta)


def derivar(palabras: list, password: str, kdf: int) -> tuple:
    """(direccion, publica, esquema) a partir de las palabras y la contrasena."""
    semilla = kw.derivar_clave(password, palabras, kdf)[:32]
    publica, direccion, esquema = kw._direccion_y_claves(semilla)
    del semilla
    gc.collect()
    return direccion, publica, esquema


def leer_palabras(n: int) -> list:
    """Lee las palabras ocultas y valida que esten en la lista BIP39 espanola."""
    print(f"Escribe las {n} palabras separadas por espacios.")
    print("(no se ven mientras escribes; no se guardan en ningun sitio)")
    frase = leer_oculto(f"{n} palabras: ")
    palabras = [p.strip().lower() for p in frase.split() if p.strip()]
    del frase
    if len(palabras) != n:
        raise SystemExit(f"[prueba] has escrito {len(palabras)} palabras, hacen falta {n}")
    conocidas = set(w.lower() for w in kw._wordlist())
    fuera = [i + 1 for i, p in enumerate(palabras) if p not in conocidas]
    if fuera:
        # Se dice la POSICION, no la palabra: sirve para corregir una errata sin
        # volcar la frase en la pantalla.
        print(f"[prueba] ojo: la(s) palabra(s) en la posicion {fuera} no estan en la "
              f"lista BIP39 espanola. Revisa tildes/erratas antes de seguir.")
    return palabras


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dir", required=True,
                    help="direccion publica que TIENE que salir (la que ya existe)")
    ap.add_argument("--kdf", type=int, default=1, choices=(1, 2),
                    help="version del derivador: 1 sin campo kdf (antiguas), 2 las nuevas")
    ap.add_argument("--palabras", type=int, default=20)
    args = ap.parse_args()

    esperada = args.dir.strip().upper()
    palabras = leer_palabras(args.palabras)
    password = leer_oculto("contrasena: ")

    direccion, publica, esquema = derivar(palabras, password, args.kdf)
    del password, palabras
    gc.collect()

    print()
    print(f"  kdf usado        : {args.kdf}")
    print(f"  esquema          : {esquema}")
    print(f"  direccion derivada: {direccion}")
    print(f"  direccion esperada: {esperada}")
    print(f"  clave publica    : {publica[:16]}…  (solo el principio)")
    print()
    if direccion == esperada:
        print("  RESULTADO: COINCIDE. La recuperacion deriva la misma billetera.")
        print("  No se ha firmado, ni enviado, ni importado nada. Nada mas que hacer.")
        return 0
    print("  RESULTADO: NO COINCIDE. No toques nada mas:")
    print("   - revisa erratas y tildes de las palabras (la posicion ya se ha dicho),")
    print("   - comprueba que la contrasena es la de esa billetera,")
    print(f"   - y solo si el fichero no tenia campo kdf, prueba --kdf 2 (una vez).")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
