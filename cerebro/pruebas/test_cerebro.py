"""Pruebas del cerebro: lista negra, troceado, incrementalidad y busqueda.

No tocan la base real: indexan `meta/` en una base temporal y con embeddings
falsos, asi que no necesitan Ollama ni red.

    python cerebro/pruebas/test_cerebro.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import numpy as np

RAIZ = Path(__file__).resolve().parents[2]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from cerebro import config, ingesta, indice  # noqa: E402

PRUEBAS = []


def prueba(fn):
    PRUEBAS.append(fn)
    return fn


def comprobar(cond, mensaje):
    if not cond:
        raise AssertionError(mensaje)


def _falso(textos, modelo=None, lote=None, intentos=3):
    """Embedding determinista de 8 dimensiones: prueba la fontaneria, no el modelo."""
    salida = []
    for texto in textos:
        v = np.zeros(8, dtype=np.float32)
        for i, ch in enumerate(texto[:64]):
            v[i % 8] += (ord(ch) % 17) + 1
        salida.append(v)
    return np.asarray(salida, dtype=np.float32)


@prueba
def test_lista_negra():
    for nombre in ("clave.txt", "admin_token.txt", "sello_key.txt", ".env",
                   "RECUPERACION_worker_x.txt", "wallet.json", "id_ed25519",
                   "kukia.db", "kukia-node.zip", "cuaderno.pdf"):
        comprobar(ingesta.prohibido(nombre), f"deberia estar prohibido: {nombre}")
    for nombre in ("kukia_core.py", "TRASPASO_2026-10-28.md", "cifrar_claves.py",
                   "auditar_claves.py", "KUKIA_META_ARQUITECTURA.md"):
        comprobar(not ingesta.prohibido(nombre), f"no deberia estar prohibido: {nombre}")


@prueba
def test_troceado_con_solape():
    texto = "\n".join(f"linea {i} con contenido suficiente para contar tokens" for i in range(1, 401))
    frags = ingesta.trocear(texto)
    comprobar(len(frags) > 1, "un texto largo tiene que dar varios fragmentos")
    comprobar(frags[0]["linea_ini"] == 1, "el primero empieza en la linea 1")
    comprobar(frags[-1]["linea_fin"] == 400, "el ultimo acaba en la ultima linea")
    for anterior, siguiente in zip(frags, frags[1:]):
        comprobar(siguiente["linea_ini"] <= anterior["linea_fin"],
                  "los fragmentos tienen que solaparse para no cortar una idea")


@prueba
def test_linea_gigante():
    texto = "x" * 12000
    frags = ingesta.trocear(texto)
    comprobar(len(frags) >= 1, "una linea gigante tiene que dar fragmentos")
    comprobar(all(f["linea_ini"] == 1 for f in frags), "todas citan la linea 1")
    comprobar(all(f["texto"] for f in frags), "ningun fragmento puede salir vacio")


@prueba
def test_descubrir_sin_secretos():
    fich = ingesta.descubrir([config.RAIZ])
    comprobar(len(fich) > 100, f"esperaba cientos de ficheros, hay {len(fich)}")
    rels = [f.rel for f in fich]
    comprobar("publico/clave.txt" not in rels, "clave.txt se ha colado")
    malos = [r for r in rels if ingesta.prohibido(Path(r).name)]
    comprobar(not malos, f"nombres prohibidos colados: {malos[:3]}")
    comprobar("RECUPERACION" not in " ".join(rels).upper(), "hay ficheros RECUPERACION")
    hashes = [f.sha256 for f in fich]
    comprobar(len(hashes) == len(set(hashes)), "hay duplicados exactos sin deduplicar")


@prueba
def test_incremental_y_cache():
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "cerebro.db"
        original = indice.embeber
        indice.embeber = _falso
        try:
            raices = [config.RAIZ / "meta"]
            r1 = indice.indexar(raices, db=db)
            comprobar(r1["nuevas"] > 0, "la primera pasada tiene que indexar")
            comprobar(r1["fragmentos_nuevos"] > 0, "tiene que crear fragmentos")

            r2 = indice.indexar(raices, db=db)
            comprobar(r2["nuevas"] == 0, "sin cambios no se indexa nada de nuevo")
            comprobar(r2["fragmentos_nuevos"] == 0, "sin cambios no se embebe nada")
            comprobar(r2["saltadas"] == r1["fuentes"], "todos deberian quedar saltados")

            # Forzar el re-troceado de una fuente: los vectores salen de la cache.
            con = indice.conectar(db)
            con.execute("UPDATE fuentes SET sha256='forzado' WHERE id=(SELECT min(id) FROM fuentes)")
            con.commit()
            con.close()
            r3 = indice.indexar(raices, db=db)
            comprobar(r3["fragmentos_reusados"] > 0, "la cache de fragmentos no se ha usado")

            resultados = indice.buscar("cerebro", 3, db=db)
            comprobar(bool(resultados), "la busqueda tiene que devolver resultados")
            comprobar(len(resultados) <= 3, "no puede devolver mas de k")
            comprobar({"rel", "linea_ini", "linea_fin", "texto", "puntos"} <= set(resultados[0]),
                      "faltan campos en el resultado")
        finally:
            indice.embeber = original


def main() -> int:
    fallos = 0
    for fn in PRUEBAS:
        try:
            fn()
            print(f"OK    {fn.__name__}")
        except AssertionError as fallo:
            fallos += 1
            print(f"FALLO {fn.__name__}: {fallo}")
    print(f"{len(PRUEBAS) - fallos}/{len(PRUEBAS)} pruebas OK")
    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(main())
