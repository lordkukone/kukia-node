"""Que entra al cerebro y como se trocea.

No escribe nada: devuelve ficheros y fragmentos. Quien guarda es `cerebro.indice`.
Cada fragmento sabe de que fichero y de que lineas sale: sin cita no hay memoria.
"""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path

from . import config

try:
    import tiktoken
    _ENC = tiktoken.get_encoding("cl100k_base")
except Exception:  # sin tiktoken se estima; el indice sigue funcionando
    _ENC = None


def cuenta_tokens(texto: str) -> int:
    if _ENC is None:
        return max(1, len(texto) // 3)
    return len(_ENC.encode(texto)) or 1


def prohibido(nombre: str) -> bool:
    """True si ese nombre de fichero no puede entrar al cerebro."""
    bajo = nombre.lower()
    if bajo in config.NOMBRES_PROHIBIDOS:
        return True
    return any(p.search(bajo) for p in config.PATRONES_PROHIBIDOS)


def prohibido_ruta(rel: str) -> bool:
    """True si alguna parte de la ruta relativa esta prohibida.

    El nombre del fichero no basta: `ledger/wallet/nodo_5070.json` se llama como
    cualquier otro JSON y guarda la clave privada. Lo que lo delata es la carpeta.
    """
    for parte in rel.replace("\\", "/").split("/"):
        if not parte:
            continue
        bajo = parte.lower()
        if bajo in config.SEGMENTOS_PROHIBIDOS:
            return True
        if any(p.search(bajo) for p in config.PATRONES_RUTA):
            return True
    return False


@dataclass(frozen=True)
class Fichero:
    ruta: str
    rel: str
    sha256: str
    bytes: int
    mtime: float


@dataclass(frozen=True)
class Fragmento:
    rel: str
    linea_ini: int
    linea_fin: int
    texto: str
    sha256: str


def _rel(p: Path, raiz: Path) -> str:
    try:
        r = p.relative_to(raiz).as_posix()
    except ValueError:
        r = p.name
    if raiz.name.upper() == "CEREBRO":
        r = "CEREBRO/" + r
    return r


def descubrir(raices=None) -> list[Fichero]:
    """Ficheros permitidos, sin duplicados exactos y ya filtrados por la lista negra."""
    salida: list[Fichero] = []
    vistos: set[str] = set()
    for raiz in (raices or config.RAICES):
        raiz = Path(raiz)
        if not raiz.is_dir():
            continue
        for dp, dns, fns in os.walk(raiz):
            dns[:] = sorted(
                d for d in dns
                if d.lower() not in config.DIRS_PROHIBIDOS
                and not d.lower().startswith(config.PREFIJOS_PROHIBIDOS)
            )
            for f in sorted(fns):
                if Path(f).suffix.lower() not in config.EXTENSIONES:
                    continue
                if prohibido(f):
                    continue
                rel = _rel(Path(dp) / f, raiz)
                if prohibido_ruta(rel):
                    continue
                p = Path(dp) / f
                try:
                    datos = p.read_bytes()
                    estado = p.stat()
                except OSError:
                    continue
                if not datos or len(datos) > config.MAX_BYTES:
                    continue
                h = hashlib.sha256(datos).hexdigest()
                if h in vistos:
                    continue
                vistos.add(h)
                salida.append(Fichero(str(p), rel, h, len(datos), estado.st_mtime))
    return salida


def _unidades(texto: str) -> list[tuple[int, str]]:
    """(numero de linea, texto). Las lineas gigantes se parten para no romper el troceado."""
    unidades: list[tuple[int, str]] = []
    for n, linea in enumerate(texto.splitlines(), start=1):
        if len(linea) <= config.MAX_CHARS_LINEA:
            unidades.append((n, linea))
            continue
        for i in range(0, len(linea), config.MAX_CHARS_LINEA):
            unidades.append((n, linea[i:i + config.MAX_CHARS_LINEA]))
    return unidades


def trocear(texto: str) -> list[dict]:
    """Fragmentos de ~400 tokens con 60 de solape, cada uno con sus lineas."""
    unidades = _unidades(texto)
    if not unidades:
        return []
    tokens = [cuenta_tokens(u[1]) for u in unidades]
    frags: list[dict] = []
    i, total = 0, len(unidades)
    while i < total:
        usado, j = 0, i
        while j < total and (usado + tokens[j] <= config.TOKENS_FRAGMENTO or j == i):
            usado += tokens[j]
            j += 1
        cuerpo = "\n".join(u[1] for u in unidades[i:j]).strip()
        if cuerpo:
            frags.append({
                "linea_ini": unidades[i][0],
                "linea_fin": unidades[j - 1][0],
                "texto": cuerpo,
                "sha256": hashlib.sha256(cuerpo.encode("utf-8")).hexdigest(),
            })
        if j >= total:
            break
        # Retrocede hasta ~60 tokens para que el corte no parta una idea.
        k, sol = j, 0
        while k > i and sol < config.TOKENS_SOLAPE:
            k -= 1
            sol += tokens[k]
        i = max(k, i + 1)
    return frags


def fragmentos(ficheros) -> list[Fragmento]:
    """Fragmentos de una lista de `Fichero`, con su ruta relativa ya puesta."""
    salida: list[Fragmento] = []
    for f in ficheros:
        try:
            texto = Path(f.ruta).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for fr in trocear(texto):
            salida.append(Fragmento(f.rel, fr["linea_ini"], fr["linea_fin"],
                                    fr["texto"], fr["sha256"]))
    return salida
