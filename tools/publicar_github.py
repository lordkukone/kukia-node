#!/usr/bin/env python3
"""Publica en GitHub lo que haya cambiado, sin riesgo de subir claves.

Uso:
    python tools/publicar_github.py                 # publica
    python tools/publicar_github.py --ver           # solo enseña lo que haria
    python tools/publicar_github.py --mensaje "lo que sea"

(atajo: doble clic en PUBLICAR_EN_GITHUB.cmd)

QUE HACE, EN ESTE ORDEN
  1. Saca la lista de ficheros que se publicarian: los versionados MAS los
     nuevos que no esten en .gitignore. Los ignorados no se tocan nunca.
  2. Pasa los NOMBRES por una lista negra (claves, billeteras, bases, tokens).
  3. Pasa el CONTENIDO de los de texto por patrones de secreto (token de
     GitHub, clave privada, AWS, Slack, HuggingFace...).
  4. Comprueba que los ficheros sensibles que hay en el disco estan ignorados.
  5. Si algo huele a secreto: PARA y no sube nada. El mensaje dice que fichero
     y por que.
  6. Solo si todo esta limpio: clona el repo de GitHub en una carpeta temporal,
     copia encima esos ficheros (nunca borra nada de alli), hace UN commit y lo
     empuja. El repo local no se toca.

POR QUE EN UNA CARPETA TEMPORAL Y NO CON UN PUSH NORMAL
El repo local y el de GitHub tienen historias separadas (45 commits el de
GitHub, 3 el local). Un `git push` normal se rechaza por no ser descendiente, y
un `--force` borraria el historial de GitHub. Asi que se publica como
instantanea: el historial de GitHub se mantiene y encima entra un commit con el
contenido de hoy. Es lo mismo que se hizo a mano el 21 y el 22 de octubre.

La contrasena del token NO se escribe en ningun sitio: se pide a git
(`git credential fill`) y se usa solo en la orden de push, tachada en los logs.
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
REPO_POR_DEFECTO = "lordkukone/kukia-node"
RAMA = "main"
# Lo que NUNCA puede salir de este PC. Se comprueba por nombre de fichero.
PROHIBIDOS = [
    r"(^|/)sello_key\.txt$",
    r"(^|/)admin_token\.txt$",
    r"(^|/)searchconsole_cuenta.*\.json$",
    r"(^|/)ledger/wallet/",
    r"(^|/)identidad[^/]*\.json$",
    r"\.(db|sqlite)(-wal|-shm)?$",
    r"(^|/)cloudflared/",
    r"\.cloudflared",
    r"(^|/)dominio_core\.txt$",
    r"(^|/)RECUPERACION_[^/]*\.txt$",
    r"(^|/)\.env",
    r"\.(pem|key|p12|pfx)$",
]
# Lo que no puede aparecer DENTRO de un fichero. Cada patron con su etiqueta.
SECRETOS = [
    (r"\bgh[pousr]_[A-Za-z0-9]{20,}", "token de GitHub"),
    (r"\bgithub_pat_[A-Za-z0-9_]{20,}", "token de GitHub (fine-grained)"),
    (r"\bhf_[A-Za-z0-9]{30,}", "token de HuggingFace"),
    (r"\bsk-[A-Za-z0-9]{24,}", "clave tipo OpenAI"),
    (r"\bAKIA[0-9A-Z]{16}\b", "clave de AWS"),
    (r"\bxox[baprs]-[A-Za-z0-9-]{10,}", "token de Slack"),
    (r"-----BEGIN [A-Z ]*PRIVATE KEY-----", "clave privada en PEM"),
    (r"(?i)\bcivitai[_-]?token\b\s*[:=]\s*\S{16,}", "token de CivitAI"),
]
# Entradas de .gitignore sin las cuales este script NO se fia.
IMPRESCINDIBLES = ["sello_key.txt", "admin_token.txt", "ledger/wallet/", "*.db",
                   "identidad", "searchconsole_cuenta"]
SENSIBLES_EN_DISCO = [
    "coordinator/sello_key.txt",
    "coordinator/admin_token.txt",
    "coordinator/ledger/kukia.db",
    "worker/identidad_minero.json",
    "validator/identidad_validador.json",
    "searchconsole_cuenta.json",
]
MAX_TEXTO = 5 * 1024 * 1024        # no se escanea contenido de ficheros grandes
MAX_FICHERO = 95 * 1024 * 1024     # GitHub rechaza mas de 100 MB
MAX_TOTAL = 900 * 1024 * 1024


class Bloqueado(Exception):
    """Se ha encontrado algo que no debe publicarse."""


def _git(*args: str, cwd: Path | None = None, capturar: bool = True) -> str:
    r = subprocess.run(["git", *args], cwd=str(cwd or RAIZ), text=True,
                       capture_output=capturar)
    if r.returncode != 0:
        detalle = (r.stderr or r.stdout or "").strip()
        raise RuntimeError(f"git {' '.join(args)} fallo: {detalle}")
    return (r.stdout or "").strip() if capturar else ""


def _es_texto(ruta: Path) -> bool:
    try:
        if ruta.stat().st_size > MAX_TEXTO:
            return False
        trozo = ruta.open("rb").read(4096)
    except OSError:
        return False
    return b"\x00" not in trozo


def ficheros_a_publicar() -> list[str]:
    """Versionados + nuevos no ignorados. Los ignorados quedan fuera por diseño."""
    salida = _git("ls-files", "-z", "-co", "--exclude-standard")
    vistos, orden = set(), []
    for rel in salida.split("\0"):
        rel = rel.strip().replace("\\", "/")
        if not rel or rel in vistos:
            continue
        vistos.add(rel)
        orden.append(rel)
    return orden


def comprobar_nombres(ficheros: list[str]) -> None:
    malos = []
    for rel in ficheros:
        bajo = rel.lower()
        for patron in PROHIBIDOS:
            if re.search(patron, bajo):
                malos.append(rel)
                break
    if malos:
        raise Bloqueado("estos ficheros no se publican nunca:\n    "
                        + "\n    ".join(malos))


def comprobar_contenido(ficheros: list[str]) -> None:
    malos = []
    for rel in ficheros:
        ruta = RAIZ / rel
        if not ruta.is_file() or not _es_texto(ruta):
            continue
        texto = ruta.read_text(encoding="utf-8", errors="ignore")
        for patron, etiqueta in SECRETOS:
            m = re.search(patron, texto)
            if not m:
                continue
            linea = texto.count("\n", 0, m.start()) + 1
            muestra = m.group(0)
            muestra = muestra[:8] + "..." if len(muestra) > 8 else muestra
            malos.append(f"{rel}:{linea}  parece {etiqueta}  ({muestra})")
    if malos:
        raise Bloqueado("posibles secretos dentro de ficheros:\n    "
                        + "\n    ".join(malos))


def comprobar_gitignore() -> None:
    ruta = RAIZ / ".gitignore"
    if not ruta.is_file():
        raise Bloqueado("no hay .gitignore: no se publica a ciegas")
    texto = ruta.read_text(encoding="utf-8", errors="ignore")
    faltan = [x for x in IMPRESCINDIBLES if x not in texto]
    if faltan:
        raise Bloqueado("al .gitignore le faltan reglas imprescindibles: "
                        + ", ".join(faltan))
    # Los ficheros sensibles que EXISTEN en este PC tienen que estar ignorados.
    sin_ignorar = []
    for rel in SENSIBLES_EN_DISCO:
        if not (RAIZ / rel).exists():
            continue
        r = subprocess.run(["git", "check-ignore", "-q", rel], cwd=str(RAIZ))
        if r.returncode != 0:
            sin_ignorar.append(rel)
    if sin_ignorar:
        raise Bloqueado("esto existe en el disco y NO esta ignorado: "
                        + ", ".join(sin_ignorar))


def comprobar_tamanos(ficheros: list[str]) -> None:
    grandes, total = [], 0
    for rel in ficheros:
        ruta = RAIZ / rel
        if not ruta.is_file():
            continue
        n = ruta.stat().st_size
        total += n
        if n > MAX_FICHERO:
            grandes.append(f"{rel} ({n / 1024 / 1024:.1f} MB)")
    if grandes:
        raise Bloqueado("GitHub no acepta ficheros de mas de 100 MB:\n    "
                        + "\n    ".join(grandes))
    if total > MAX_TOTAL:
        raise Bloqueado(f"el paquete entero son {total / 1024 / 1024:.0f} MB, "
                        "demasiado para un push comodo")


def credenciales(host: str = "github.com") -> tuple[str, str]:
    """Usuario y token del almacen de git. Nunca se imprimen."""
    r = subprocess.run(["git", "credential", "fill"], input=f"protocol=https\nhost={host}\n\n",
                       text=True, capture_output=True)
    datos = {}
    for linea in (r.stdout or "").splitlines():
        if "=" in linea:
            k, v = linea.split("=", 1)
            datos[k.strip()] = v.strip()
    if not datos.get("password"):
        raise Bloqueado(f"no hay credencial guardada para {host}. Inicia sesion una "
                        "vez con `git push` (o guarda un token) y repite.")
    return datos.get("username", "x-access-token"), datos["password"]


def mensaje_automatico(cambios: list[str]) -> str:
    if not cambios:
        return f"Instantanea {time.strftime('%d-%m-%Y %H:%M')}"
    carpetas: dict[str, int] = {}
    for rel in cambios:
        clave = rel.split("/")[0] if "/" in rel else "(raiz)"
        carpetas[clave] = carpetas.get(clave, 0) + 1
    trozos = [f"{c} ({n})" for c, n in sorted(carpetas.items(), key=lambda x: -x[1])[:4]]
    return (f"Actualiza {len(cambios)} ficheros: " + ", ".join(trozos)
            + f" - {time.strftime('%d-%m-%Y %H:%M')}")


def publicar(ficheros: list[str], mensaje: str, repo: str, url: str = "") -> str:
    url_limpia = url or f"https://github.com/{repo}.git"
    por_http = url_limpia.startswith("http")
    usuario = token = ""
    if por_http:
        usuario, token = credenciales()
    temporal = Path(tempfile.mkdtemp(prefix="kukia_publicar_"))
    try:
        print(f"[1/4] Clonando {repo} ...", flush=True)
        subprocess.run(["git", "clone", "-q", "--depth", "1", "--branch", RAMA,
                        url_limpia, str(temporal)], check=True)

        print(f"[2/4] Copiando {len(ficheros)} ficheros ...", flush=True)
        copiados = 0
        for rel in ficheros:
            origen, destino = RAIZ / rel, temporal / rel
            if not origen.is_file():
                continue
            destino.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(origen, destino)
            copiados += 1

        # Antes de nada: comprobar el fichero de verdad, ya copiado y en el indice.
        subprocess.run(["git", "add", "-A"], cwd=str(temporal), check=True)
        indice = subprocess.run(["git", "diff", "--cached", "--name-only", "-z"],
                                cwd=str(temporal), text=True, capture_output=True).stdout
        tocados = [x for x in indice.split("\0") if x.strip()]
        for rel in tocados:
            if re.search(r"(?i)(sello_key|admin_token|\.db$|wallet/|identidad|searchconsole)",
                         rel):
                raise Bloqueado(f"algo raro ha llegado al clon: {rel}")
        if not tocados:
            print("      no hay nada nuevo: GitHub ya esta al dia")
            return ""

        subprocess.run(["git", "-c", "user.name=KUKIA", "-c", "user.email=kukia@local",
                        "commit", "-q", "-m", mensaje], cwd=str(temporal), check=True)
        huella = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=str(temporal),
                                text=True, capture_output=True).stdout.strip()

        print(f"[3/4] Empujando (commit {huella}, {len(tocados)} ficheros) ...", flush=True)
        destino = (f"https://{usuario}:{token}@github.com/{repo}.git" if por_http
                   else url_limpia)
        r = subprocess.run(["git", "push", "-q", destino, f"HEAD:{RAMA}"],
                           cwd=str(temporal), text=True, capture_output=True)
        if r.returncode != 0:
            error = ((r.stderr or "") + (r.stdout or "")).replace(token, "***")
            raise RuntimeError("el push fallo: " + error.strip()[:400])
        print(f"[4/4] Publicado: https://github.com/{repo}/commit/{huella}")
        return huella
    finally:
        shutil.rmtree(temporal, ignore_errors=True)


def main() -> int:
    ap = argparse.ArgumentParser(description="Publica en GitHub lo que haya cambiado")
    ap.add_argument("--ver", action="store_true", help="solo enseña lo que haria")
    ap.add_argument("--mensaje", default="", help="mensaje del commit")
    ap.add_argument("--repo", default=REPO_POR_DEFECTO, help="usuario/repositorio")
    # Solo para pruebas: apuntar a una copia local en vez de a GitHub.
    ap.add_argument("--url", default="", help=argparse.SUPPRESS)
    args = ap.parse_args()

    try:
        if not (RAIZ / ".git").exists():
            raise Bloqueado(f"{RAIZ} no es un repositorio git")
        comprobar_gitignore()
        ficheros = ficheros_a_publicar()
        comprobar_nombres(ficheros)
        comprobar_contenido(ficheros)
        comprobar_tamanos(ficheros)

        cambios = _git("status", "--porcelain").splitlines()
        mensaje = args.mensaje or mensaje_automatico(
            [l[3:].strip() for l in cambios if len(l) > 3])
        total_mb = sum((RAIZ / f).stat().st_size for f in ficheros
                       if (RAIZ / f).is_file()) / 1024 / 1024

        print("=" * 60)
        print(f" Repositorio : github.com/{args.repo} ({RAMA})")
        print(f" Ficheros    : {len(ficheros)} ({total_mb:.1f} MB)")
        print(f" Cambiados   : {len(cambios)}")
        print(f" Mensaje     : {mensaje}")
        print("=" * 60)
        if cambios:
            for linea in cambios[:15]:
                print(f"   {linea}")
            if len(cambios) > 15:
                print(f"   ... y {len(cambios) - 15} mas")
            print("-" * 60)
        print(" Comprobaciones: nombres OK, contenido OK, .gitignore OK, tamanos OK")

        if args.ver:
            print("\n[--ver] no se ha subido nada.")
            return 0

        huella = publicar(ficheros, mensaje, args.repo, args.url)
        if not huella:
            print("Nada que publicar.")
        print("\nListo. GitHub Pages tarda un par de minutos en rehacer la web.")
        return 0
    except Bloqueado as e:
        print("\nNO SE PUBLICA NADA.\n" + str(e), file=sys.stderr)
        return 1
    except Exception as e:                      # noqa: BLE001 - queremos el texto
        print(f"\nERROR: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    for _f in (sys.stdout, sys.stderr):
        try:
            _f.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    raise SystemExit(main())
