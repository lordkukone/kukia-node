"""Indice propio: SQLite para el texto, numpy para los vectores.

No hay servidor. ~8.000 fragmentos de 768 dimensiones son ~24 MB y una busqueda
por fuerza bruta tarda milisegundos; Qdrant queda para cuando el corpus crezca
de verdad, y despues de atarlo a loopback (Fase 0, hallazgo F0-2).

Uso:
    python -m cerebro.indice --indexar          # incremental
    python -m cerebro.indice --estado
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import time
import unicodedata
import urllib.error
import urllib.request
from pathlib import Path

import numpy as np

from . import config, ingesta

ESQUEMA = """
CREATE TABLE IF NOT EXISTS fuentes(
    id INTEGER PRIMARY KEY,
    rel TEXT UNIQUE NOT NULL,
    ruta TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    bytes INTEGER NOT NULL,
    mtime REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS fragmentos(
    id INTEGER PRIMARY KEY,
    fuente_id INTEGER NOT NULL REFERENCES fuentes(id) ON DELETE CASCADE,
    rel TEXT NOT NULL,
    linea_ini INTEGER NOT NULL,
    linea_fin INTEGER NOT NULL,
    texto TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    dim INTEGER NOT NULL,
    vector BLOB NOT NULL,
    modelo TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_frag_hash ON fragmentos(sha256);
CREATE INDEX IF NOT EXISTS idx_frag_fuente ON fragmentos(fuente_id);
CREATE INDEX IF NOT EXISTS idx_frag_rel ON fragmentos(rel);
CREATE TABLE IF NOT EXISTS meta(clave TEXT PRIMARY KEY, valor TEXT);
"""


def conectar(db=None) -> sqlite3.Connection:
    ruta = Path(db or config.DB)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(ruta)
    con.executescript(ESQUEMA)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    return con


def embeber(textos, modelo=None, lote=None, intentos: int = 3) -> np.ndarray:
    """Embeddings por Ollama, por lotes. Reintenta y falla en voz alta."""
    modelo = modelo or config.MODELO
    lote = lote or config.LOTE
    salida: list[list[float]] = []
    for k in range(0, len(textos), lote):
        trozo = list(textos[k:k + lote])
        for intento in range(intentos):
            try:
                datos = json.dumps({"model": modelo, "input": trozo}).encode("utf-8")
                peticion = urllib.request.Request(
                    config.OLLAMA.rstrip("/") + "/api/embed", data=datos,
                    headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(peticion, timeout=300) as respuesta:
                    cuerpo = json.loads(respuesta.read())
                vectores = cuerpo.get("embeddings") or []
                if len(vectores) != len(trozo):
                    raise RuntimeError(f"respuesta incompleta: {len(vectores)}/{len(trozo)}")
                salida.extend(vectores)
                break
            except (urllib.error.URLError, RuntimeError, json.JSONDecodeError) as fallo:
                if intento == intentos - 1:
                    raise RuntimeError(
                        f"Ollama no responde en {config.OLLAMA} con el modelo {modelo}: {fallo}"
                    ) from fallo
                time.sleep(2 * (intento + 1))
    return np.asarray(salida, dtype=np.float32)


def normalizar(matriz: np.ndarray) -> np.ndarray:
    """Vectores unitarios: asi el producto escalar ya es el coseno."""
    m = np.atleast_2d(np.asarray(matriz, dtype=np.float32))
    normas = np.linalg.norm(m, axis=1, keepdims=True)
    normas[normas == 0] = 1.0
    return m / normas


def indexar(raices=None, limite=None, db=None, progreso=None) -> dict:
    """Recorre, trocea y embebe solo lo que ha cambiado. Devuelve lo medido."""
    arranque = time.perf_counter()
    con = conectar(db)

    modelo_guardado = dict(con.execute("SELECT clave, valor FROM meta").fetchall()).get("modelo")
    aviso_modelo = None
    if modelo_guardado and modelo_guardado != config.MODELO:
        # Cambiar de modelo con vectores viejos compararia cosas que no son.
        con.execute("DELETE FROM fragmentos")
        con.commit()
        aviso_modelo = f"modelo cambiado ({modelo_guardado} -> {config.MODELO}): indice rehecho"

    # Purga de lo que la lista negra rechaza hoy (por ejemplo tras endurecer el
    # filtro de rutas). Va antes de construir la cache para que sus vectores no
    # vuelvan a entrar por la puerta de atras.
    purgadas = 0
    for ident, rel in con.execute("SELECT id, rel FROM fuentes").fetchall():
        # `cerebro/` no se indexa (el banco de preguntas se encontraria a si mismo);
        # el resto lo decide la lista negra, por nombre y por ruta.
        # Comparacion sensible a mayusculas a proposito: las fuentes de `I:/CEREBRO`
        # llevan el prefijo "CEREBRO/" y no son el paquete `cerebro/`. En minusculas
        # colisionaban y cada pasada purgaba las 300 fuentes sagradas.
        dentro_del_paquete = rel == "cerebro" or rel.startswith("cerebro/")
        if dentro_del_paquete or ingesta.prohibido_ruta(rel) or ingesta.prohibido(Path(rel).name):
            con.execute("DELETE FROM fragmentos WHERE fuente_id=?", (ident,))
            con.execute("DELETE FROM fuentes WHERE id=?", (ident,))
            purgadas += 1
    if purgadas:
        con.commit()

    ficheros = ingesta.descubrir(raices)
    if limite:
        ficheros = ficheros[:limite]

    conocidos = {rel: (ident, sha) for ident, rel, sha in
                 con.execute("SELECT id, rel, sha256 FROM fuentes")}

    # La cache se toma ANTES de borrar los fragmentos de las fuentes cambiadas:
    # si se toma despues, se borra justo lo que se quiere reutilizar.
    cache = {sha: (bytes(vector), dim) for sha, vector, dim in
             con.execute("SELECT sha256, vector, dim FROM fragmentos")}

    nuevas = saltadas = podadas = cambios = 0
    cambiados: list[ingesta.Fichero] = []
    for f in ficheros:
        previo = conocidos.get(f.rel)
        if previo and previo[1] == f.sha256:
            saltadas += 1
            continue
        if previo:
            con.execute("DELETE FROM fragmentos WHERE fuente_id=?", (previo[0],))
            cambios += 1
        con.execute(
            "INSERT INTO fuentes(rel, ruta, sha256, bytes, mtime) VALUES(?,?,?,?,?) "
            "ON CONFLICT(rel) DO UPDATE SET ruta=excluded.ruta, sha256=excluded.sha256, "
            "bytes=excluded.bytes, mtime=excluded.mtime",
            (f.rel, f.ruta, f.sha256, f.bytes, f.mtime))
        nuevas += 1
        cambiados.append(f)

    vivos = {f.rel for f in ficheros}
    for rel, (ident, _sha) in conocidos.items():
        if rel not in vivos:
            con.execute("DELETE FROM fragmentos WHERE fuente_id=?", (ident,))
            con.execute("DELETE FROM fuentes WHERE id=?", (ident,))
            podadas += 1
    con.commit()

    ids = {rel: ident for ident, rel in con.execute("SELECT id, rel FROM fuentes")}

    # Una fuente se escribe entera o no se escribe NADA. El hash de `fuentes` se
    # actualiza al final, dentro del mismo commit que sus fragmentos: si el
    # proceso muere a mitad, la fuente sigue constando como vieja y se reintenta.
    # Antes se marcaba primero y una interrupcion la dejaba sin fragmentos para
    # siempre (paso el 10-11-2026 con dos indexadores a la vez).
    nuevos_frag = reusados = 0
    hechas = 0
    for f in cambiados:
        try:
            texto = Path(f.ruta).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        frags = ingesta.trocear(texto)
        faltan = [fr for fr in frags if fr["sha256"] not in cache]
        if faltan:
            vectores = normalizar(embeber([fr["texto"] for fr in faltan]))
            for fr, vector in zip(faltan, vectores):
                cache[fr["sha256"]] = (np.asarray(vector, dtype=np.float32).tobytes(), len(vector))
                nuevos_frag += 1
        reusados += len(frags) - len(faltan)

        ident = ids[f.rel]
        con.execute("SAVEPOINT fuente")
        con.execute("DELETE FROM fragmentos WHERE fuente_id=?", (ident,))
        for fr in frags:
            crudo, dim = cache[fr["sha256"]]
            con.execute(
                "INSERT INTO fragmentos(fuente_id, rel, linea_ini, linea_fin, texto, sha256, dim, vector, modelo) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (ident, f.rel, fr["linea_ini"], fr["linea_fin"], fr["texto"],
                 fr["sha256"], dim, crudo, config.MODELO))
        con.execute("UPDATE fuentes SET sha256=?, ruta=?, bytes=?, mtime=? WHERE id=?",
                    (f.sha256, f.ruta, f.bytes, f.mtime, ident))
        con.execute("RELEASE fuente")
        con.commit()
        hechas += 1
        if progreso and hechas % 10 == 0:
            progreso(hechas, len(cambiados))

    con.execute("INSERT INTO meta(clave, valor) VALUES('modelo', ?) "
                "ON CONFLICT(clave) DO UPDATE SET valor=excluded.valor", (config.MODELO,))
    con.execute("INSERT INTO meta(clave, valor) VALUES('indexado_en', ?) "
                "ON CONFLICT(clave) DO UPDATE SET valor=excluded.valor",
                (time.strftime("%Y-%m-%d %H:%M:%S"),))
    con.commit()
    con.close()

    return {
        "fuentes": len(ficheros),
        "nuevas": nuevas,
        "saltadas": saltadas,
        "podadas": podadas,
        "purgadas": purgadas,
        "reescritas": cambios,
        "fragmentos_nuevos": nuevos_frag,
        "fragmentos_reusados": reusados,
        "segundos": round(time.perf_counter() - arranque, 1),
        "aviso": aviso_modelo,
    }


def estadisticas(db=None) -> dict:
    con = conectar(db)
    guardado = dict(con.execute("SELECT clave, valor FROM meta").fetchall())
    fuentes = con.execute("SELECT count(*) FROM fuentes").fetchone()[0]
    frags = con.execute("SELECT count(*) FROM fragmentos").fetchone()[0]
    con.close()
    return {"fuentes": fuentes, "fragmentos": frags,
            "modelo": guardado.get("modelo"), "indexado_en": guardado.get("indexado_en")}


def cargar(db=None):
    """(matriz normalizada, metadatos) de todo el indice del modelo actual."""
    con = conectar(db)
    guardado = dict(con.execute("SELECT clave, valor FROM meta").fetchall())
    modelo = guardado.get("modelo")
    sql = ("SELECT id, rel, linea_ini, linea_fin, texto, vector, dim FROM fragmentos"
           + (" WHERE modelo=?" if modelo else ""))
    filas = con.execute(sql, (modelo,) if modelo else ()).fetchall()
    con.close()
    if not filas:
        return np.zeros((0, 0), dtype=np.float32), []
    dim = filas[0][6]
    matriz = np.frombuffer(b"".join(f[5] for f in filas), dtype=np.float32).reshape(len(filas), dim)
    meta = [(f[0], f[1], f[2], f[3], f[4]) for f in filas]
    return matriz, meta


_PARADA = {
    "de", "la", "el", "los", "las", "que", "y", "o", "en", "un", "una", "del",
    "al", "por", "para", "con", "se", "su", "es", "como", "lo", "mas", "sin",
    "no", "the", "of", "to", "and", "cuando", "donde", "porque", "este", "esta",
}


def _plano(texto: str) -> str:
    """Minusculas y sin acentos: 'ancla' tiene que encontrar 'ancla' y 'ancla'."""
    t = unicodedata.normalize("NFKD", texto.lower())
    return "".join(c for c in t if not unicodedata.combining(c))


def _terminos(texto: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9_]{4,}", _plano(texto)) if w not in _PARADA]


def buscar(consulta: str, k: int = 8, db=None) -> list[dict]:
    """Los k fragmentos mas parecidos: coseno + coincidencia lexica y de ruta.

    El coseno solo se queda corto cuando la respuesta esta en un fichero cuyo
    nombre la anuncia ("guardia", "salud", "potencia") pero el texto usa otras
    palabras. La ruta y las palabras exactas desempatan. Se mide con
    `cerebro.evaluar`: si el banco no sube, este ajuste no vale.
    """
    matriz, meta = cargar(db)
    if not meta:
        raise RuntimeError("el indice esta vacio: lanza `python -m cerebro.indice --indexar`")
    q = normalizar(embeber([consulta]))[0]
    cos = matriz @ q
    puntos = cos.copy()
    terminos = _terminos(consulta)
    if terminos:
        candidatos = np.argsort(-cos)[:max(400, k * 40)]
        for i in candidatos:
            texto = _plano(meta[i][4])
            ruta = _plano(meta[i][1])
            en_texto = sum(1 for t in terminos if t in texto)
            en_ruta = sum(1 for t in terminos if t in ruta)
            puntos[i] = cos[i] + 0.20 * (en_texto / len(terminos)) + 0.35 * (en_ruta / len(terminos))
    orden = np.argsort(-puntos)[:max(1, k)]
    return [{"rel": meta[i][1], "linea_ini": meta[i][2], "linea_fin": meta[i][3],
             "texto": meta[i][4], "puntos": float(puntos[i])} for i in orden]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="cerebro.indice", description="Indice del cerebro KUKIA.")
    ap.add_argument("--indexar", action="store_true", help="recorre e indexa (incremental)")
    ap.add_argument("--estado", action="store_true", help="que hay indexado")
    ap.add_argument("--limite", type=int, default=None, help="solo N ficheros (pruebas)")
    ap.add_argument("--raiz", action="append", default=None, help="raiz extra a indexar")
    args = ap.parse_args(argv)

    if args.estado:
        e = estadisticas()
        print(f"fuentes={e['fuentes']} fragmentos={e['fragmentos']} "
              f"modelo={e['modelo']} indexado_en={e['indexado_en']}")
        return 0
    if not args.indexar:
        ap.print_help()
        return 2

    arranque = time.perf_counter()

    def progreso(n, total):
        print(f"  {n}/{total} fragmentos embebidos ({time.perf_counter() - arranque:.0f}s)",
              flush=True)

    r = indexar([Path(x) for x in args.raiz] if args.raiz else None, args.limite, progreso=progreso)
    if r["aviso"]:
        print(f"AVISO: {r['aviso']}")
    print(f"fuentes={r['fuentes']} nuevas={r['nuevas']} saltadas={r['saltadas']} "
          f"podadas={r['podadas']} purgadas={r['purgadas']}")
    print(f"fragmentos nuevos={r['fragmentos_nuevos']} reusados={r['fragmentos_reusados']} "
          f"en {r['segundos']}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
