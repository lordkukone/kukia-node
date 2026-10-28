#!/usr/bin/env python3
"""Escribe el fichero RECUPERACION_<billetera>.txt de una billetera existente.

PARA QUE (28-10-2026)

Hay billeteras con saldo que NO tienen `RECUPERACION_<nombre>.txt`. Su JSON esta
cifrado con DPAPI, que solo abre en el usuario de Windows que lo creo: si ese PC
desaparece, el JSON no vale nada en otro equipo. Lo unico que queda son las 20
palabras del papel... y la contrasena, que nadie apunto. Ese es el hueco que
tapa este programa, para la billetera del fundador y para cualquier otra.

COMO LO HACE, Y POR QUE ASI

  1. Pide la contrasena por TECLADO (getpass): no pasa por el historial, ni por
     la linea de comandos, ni por un log, ni por el chat.
  2. Deriva la clave con las 20 palabras y comprueba que da EXACTAMENTE la
     direccion de la billetera. Si la billetera se puede descifrar en este PC,
     ademas compara la clave privada byte a byte, que es la prueba definitiva.
  3. Si no cuadra, NO escribe nada: ni un fichero a medias, ni uno con una
     contrasena equivocada (que es peor que no tenerlo).
  4. Solo si cuadra, escribe el fichero y lo vuelve a leer para comprobar que
     el propio lector de billeteras lo entiende y deriva lo mismo.

La version del KDF no se adivina: se prueban las candidatas (la que declare el
JSON, y si no declara ninguna, v1 y v2) y se queda la que reproduce la
direccion. Las billeteras de octubre son v1; las de finales de octubre, v2.

AVISO: el fichero deja la contrasena EN CLARO al lado de la billetera, como los
otros 15 que ya hay. Sirve para pasarlo a papel y para recuperar en otro PC;
mientras este en el disco, quien lo lea puede firmar pagos con ese saldo.

Uso:
    python tools\\recuperacion_billetera.py kuki_fundador_nueva
    python tools\\recuperacion_billetera.py worker_kuki_minero
    python tools\\recuperacion_billetera.py kuki_fundador_nueva --a-n
    python tools\\recuperacion_billetera.py kuki_fundador_nueva --reemplazar

Con --a-n (o --copiar-a <carpeta>) deja ademas una copia en N: y la verifica
ALLI: lee la copia y comprueba que deriva la misma direccion. Si no cuadra,
borra la copia y lo dice; el fichero local no se toca.

Sale con 0 si escribe el fichero (o si ya existe y no se pide reemplazar), con 1
si la contrasena no cuadra y con 2 si la billetera no esta.
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import shutil
import sys
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "ledger"))

import kukia_wallet as W  # noqa: E402


N_RAIZ = Path("N:/CLAVES_KUKIA")


def _ruta_billetera(nombre: str) -> Path:
    ruta = Path(nombre)
    if ruta.suffix == ".json" and ruta.exists():
        return ruta
    return W.WALLET_DIR / f"{nombre}.json"


def carpeta_n(raiz: Path | None = None) -> Path:
    """La carpeta `wallet` del paquete de claves mas reciente de N:.

    El LEEME de ese paquete dice que ahi van las billeteras y los
    RECUPERACION_*.txt, asi que el fichero nuevo va con los suyos y no en una
    carpeta suelta. Si no hay ningun paquete, se propone uno con la fecha de hoy.
    """
    raiz = raiz or N_RAIZ
    candidatas = sorted((d for d in raiz.glob("*/wallet") if d.is_dir()),
                        key=lambda d: d.parent.name)
    if candidatas:
        return candidatas[-1]
    return raiz / time.strftime("%Y-%m-%d") / "wallet"


def _leer_fichero(ruta: Path) -> tuple[str, list]:
    """(contrasena, palabras) de un RECUPERACION_*.txt, sin pasar por el wallet."""
    pwd, palabras = "", []
    for linea in ruta.read_text(encoding="utf-8", errors="replace").splitlines():
        s = linea.strip()
        if s.lower().startswith("contrasena:"):
            pwd = s.split(":", 1)[1].strip()
        elif s.lower().startswith("20 palabras:"):
            palabras = s.split(":", 1)[1].split()
    return pwd, palabras


def _versiones(d: dict) -> list[int]:
    """Candidatas de KDF: primero la que declara el JSON; si no dice nada, v1 y
    v2. Las billeteras de octubre no llevan el campo y son v1."""
    dicha = d.get("kdf")
    lista = []
    if dicha is not None:
        try:
            lista.append(int(dicha))
        except (TypeError, ValueError):
            pass          # un 'kdf' corrupto no debe impedir probar v1 y v2
    for v in (1, 2):
        if v not in lista:
            lista.append(v)
    return lista


def _cargar_json(ruta: Path) -> tuple[dict | None, str]:
    """(billetera, motivo del fallo). Un JSON roto o de otra forma se rechaza con
    un mensaje, no con un traceback: esta herramienta la va a usar una persona."""
    try:
        d = json.loads(ruta.read_text(encoding="utf-8"))
    except Exception as e:
        return None, f"no puedo leer {ruta.name} como JSON ({type(e).__name__})"
    if not isinstance(d, dict):
        return None, f"{ruta.name} no es una billetera (contiene {type(d).__name__})"
    if not d.get("direccion"):
        return None, f"{ruta.name} no tiene 'direccion': no es una billetera de este formato"
    return d, ""


def _derivar(palabras: list, contrasena: str, version: int):
    semilla = W.derivar_clave(contrasena, palabras, version)[:32]
    pub, direccion, _ = W._direccion_y_claves(semilla)
    return semilla, pub, direccion


def escribir_recuperacion(nombre: str, contrasena: str,
                          palabras: list | None = None,
                          reemplazar: bool = False,
                          copiar_a: Path | None = None) -> tuple[bool, Path | None, str]:
    """Escribe RECUPERACION_<nombre>.txt si la contrasena cuadra.

    Devuelve (ok, ruta, motivo). No imprime nada: quien llama decide que contar.
    """
    ruta_json = _ruta_billetera(nombre)
    if not ruta_json.exists():
        return False, None, f"no existe la billetera {ruta_json}"
    d, fallo = _cargar_json(ruta_json)
    if d is None:
        return False, None, fallo

    # La contrasena se escribe tal cual se tecleo: si se recortara, el fichero
    # dejaria de reproducir la clave. Se rechaza antes de derivar, que cuesta.
    if not contrasena or contrasena != contrasena.strip():
        return False, None, ("la contrasena esta vacia o empieza/acaba en espacio: "
                             "no se puede apuntar sin ambiguedad")

    # Las palabras: las del JSON (descifradas con DPAPI) o las que traiga quien
    # llama, para el caso de estar en otro PC donde el JSON no abre.
    priv_json = ""
    if palabras is None:
        if d.get("clave_privada") and not d.get("clave_privada_cifrada"):
            # Billetera antigua con la clave EN CLARO: se lee tal cual. A
            # proposito NO se llama a cargar_billetera(), porque esa funcion
            # migra el fichero a DPAPI y reescribirlo seria modificar la
            # billetera, que es justo lo que esta herramienta no hace.
            priv_json = d["clave_privada"]
            palabras = list(d.get("mnemonico") or [])
        else:
            try:
                d = W.cargar_billetera(ruta_json)
                palabras = list(d.get("mnemonico") or [])
                priv_json = d.get("clave_privada") or ""
            except Exception as e:
                return False, None, (f"no puedo descifrar {ruta_json.name} ({e}); "
                                     "pasa las 20 palabras con --palabras <fichero>")
    if len(palabras) != 20:
        return False, None, f"hacen falta 20 palabras y hay {len(palabras)}"

    esperada = d.get("direccion") or ""
    if not esperada:
        return False, None, f"{ruta_json.name} no tiene 'direccion'"
    acertada, pruebas = None, []
    for v in _versiones(d):
        semilla, pub, direccion = _derivar(palabras, contrasena, v)
        cuadra = direccion == esperada
        if cuadra and priv_json and semilla.hex() != priv_json:
            # Misma direccion pero otra privada es imposible: si pasa, el JSON
            # esta incoherente y no se escribe nada.
            cuadra = False
        pruebas.append((v, direccion, cuadra))
        if cuadra and acertada is None:
            acertada = (v, semilla, pub, direccion)

    if acertada is None:
        detalle = "; ".join(f"v{v} -> {dirw}" for v, dirw, _ in pruebas)
        return False, None, ("la contrasena no reproduce esta billetera. "
                             f"Probado: {detalle}")
    version, semilla, pub, direccion = acertada

    destino = ruta_json.parent / f"RECUPERACION_{ruta_json.stem}.txt"
    if destino.exists() and not reemplazar:
        return True, destino, "ya existia (no se toca; usa --reemplazar)"

    texto = (
        f"KUKIA - recuperacion de la billetera {ruta_json.stem}\n"
        + "=" * 49 + "\n"
        f"nombre:      {ruta_json.stem}\n"
        f"direccion:   {direccion}\n"
        f"contrasena:  {contrasena}\n"
        f"20 palabras: {' '.join(palabras)}\n"
        "\n"
        "Con la contrasena + las 20 palabras se recupera ESTA MISMA billetera\n"
        f"en otro PC (kdf v{version}, {d.get('esquema') or 'ed25519-pbkdf2'} -> ed25519).\n"
        "\n"
        "Guardalo en un sitio aparte y no lo dejes solo en este PC: quien lo tenga puede\n"
        "firmar pagos con tu saldo.\n"
    )
    # Escritura atomica y con permisos desde el primer byte: se crea el fichero
    # temporal ya restringido (0600) y solo despues se escribe dentro. Si algo
    # falla, el temporal se borra: nunca queda medio fichero ni un resto con la
    # contrasena dentro.
    tmp = destino.with_suffix(".txt.tmp")
    try:
        if tmp.exists():
            tmp.unlink()          # resto de una ejecucion anterior interrumpida
        fd = os.open(tmp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except Exception as e:
        return False, None, f"no puedo crear el fichero temporal {tmp.name}: {e}"
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(texto)
            fh.flush()
            os.fsync(fh.fileno())
        try:
            W._proteger(tmp)
        except Exception:
            pass
        os.replace(tmp, destino)
    except Exception as e:
        try:
            tmp.unlink()
        except Exception:
            pass
        return False, None, f"fallo escribiendo {destino.name}: {e}"

    # Ida y vuelta: que lo lea el lector de verdad (el que usa el Core) y que
    # vuelva a derivar la misma direccion desde el fichero recien escrito.
    pwd_leida, palabras_leidas = W._recuperacion_de(ruta_json)
    _, _, direccion_leida = _derivar(palabras_leidas, pwd_leida, version)
    if direccion_leida != direccion:
        return False, destino, ("el fichero escrito NO deriva la direccion al leerlo "
                                f"({direccion_leida}); no lo uses")
    motivo = f"escrito y verificado (kdf v{version})"

    # Copia fuera del PC (N:) y verificacion ALLI, no aqui: un fichero que se
    # copia mal (o se corta) es peor que no tenerlo, porque parece que lo tienes.
    if copiar_a is not None:
        try:
            copiar_a.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            return True, destino, f"{motivo}; NO se copio a {copiar_a}: {e}"
        alli = copiar_a / destino.name
        if alli.exists() and not reemplazar:
            return True, destino, f"{motivo}; en {copiar_a} ya habia uno (no se pisa)"
        try:
            shutil.copy2(destino, alli)
            try:
                W._proteger(alli)
            except Exception:
                pass
            pwd_c, pal_c = _leer_fichero(alli)
            _, _, dir_c = _derivar(pal_c, pwd_c, version)
        except Exception as e:
            return True, destino, f"{motivo}; fallo copiando a {copiar_a}: {e}"
        if dir_c != direccion:
            try:
                alli.unlink()
            except Exception:
                pass
            return False, destino, ("la copia de "
                                    f"{copiar_a} no deriva la misma direccion ({dir_c}); "
                                    "la he borrado, el fichero local sigue bien")
        motivo += f"; copia en {alli} verificada ({alli.stat().st_size} B)"
    return True, destino, motivo


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Escribe el fichero de recuperacion de una billetera existente")
    ap.add_argument("billetera", help="nombre (kuki_fundador_nueva) o ruta al .json")
    ap.add_argument("--reemplazar", action="store_true",
                    help="sobrescribe el fichero si ya existe")
    ap.add_argument("--palabras", default="",
                    help="fichero con las 20 palabras, si el JSON no se puede descifrar")
    ap.add_argument("--copiar-a", default="",
                    help="copia el fichero a esta carpeta y lo verifica alli "
                         "(por ejemplo N:\\CLAVES_KUKIA\\2026-10-22\\wallet)")
    ap.add_argument("--a-n", action="store_true",
                    help="atajo de --copiar-a: la carpeta wallet del paquete de N: "
                         "mas reciente")
    args = ap.parse_args()
    for f in (sys.stdout, sys.stderr):
        try:
            f.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    if args.copiar_a and args.a_n:
        print("[recuperacion] usa --copiar-a o --a-n, no las dos.")
        return 1
    copiar_a = Path(args.copiar_a) if args.copiar_a else (carpeta_n() if args.a_n else None)
    if copiar_a is not None:
        print(f"[recuperacion] copia en: {copiar_a}")

    ruta_json = _ruta_billetera(args.billetera)
    if not ruta_json.exists():
        print(f"[recuperacion] ERROR: no existe {ruta_json}")
        return 2
    print(f"[recuperacion] billetera: {ruta_json}")
    print("[recuperacion] la contrasena se pide por teclado: no se guarda en ningun log.")

    palabras = None
    if args.palabras:
        try:
            crudo = Path(args.palabras).read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            print(f"[recuperacion] ERROR leyendo {args.palabras}: {e}")
            return 2
        palabras = crudo.split()
        # Solo el numero: las palabras no se imprimen nunca.
        print(f"[recuperacion] palabras leidas de {args.palabras} ({len(palabras)})")

    contrasena = getpass.getpass("contrasena: ")
    if not contrasena:
        print("[recuperacion] contrasena vacia: no se escribe nada.")
        return 1
    otra = getpass.getpass("repitela: ")
    if otra != contrasena:
        print("[recuperacion] las dos no coinciden: no se escribe nada.")
        return 1

    ok, ruta, motivo = escribir_recuperacion(args.billetera, contrasena,
                                             palabras=palabras,
                                             reemplazar=args.reemplazar,
                                             copiar_a=copiar_a)
    print(f"[recuperacion] {'OK' if ok else 'NO'}: {motivo}")
    if ok and ruta is not None:
        print(f"[recuperacion] fichero: {ruta}  ({ruta.stat().st_size} B)")
        print("[recuperacion] deja la contrasena en claro: pasalo a papel y no lo "
              "dejes suelto en el disco.")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
