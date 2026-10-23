"""Base de datos de KUKIA META: el rastro de cada afirmacion.

Regla de oro de este paquete: **nada es cierto por escribirlo**. Cada afirmacion
que entra lleva su procedencia, cada numero lleva el codigo que lo calculo y
cada resultado necesita una comprobacion INDEPENDIENTE (hecha por otro camino)
para subir de escalon. Lo que no se puede comprobar se queda como hipotesis o
como indeterminado, nunca como resultado.

Aislamiento
    Esta base es `meta/estado/meta.db`, aparte del ledger de la moneda
    (`coordinator/ledger/kukia.db`). KUKIA META no paga, no cobra, no mueve
    saldos y no toca el Core. Si algun dia pide calculo a la red, sera a traves
    del API existente y con autorizacion, no editando el Core.

Escalones epistemicos (de menos a mas)
    0 hipotesis     propuesta, sin calcular. Puede ser falsa.
    1 calculado     la ejecuto nuestro codigo; aun no la ha mirado nadie mas.
    2 reproducido   dos ejecuciones independientes dan lo mismo (repetible).
    3 validado      ademas, una verificacion por OTRO metodo concuerda.
    4 confirmado    dos verificaciones independientes (motores distintos).

    refutado         alguna verificacion discrepa. Se guarda, no se borra.
    indeterminado    no se pudo calcular o no se pudo comprobar.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
CARPETA = RAIZ / "meta" / "estado"
RUTA_POR_DEFECTO = CARPETA / "meta.db"

ESCALONES = ("hipotesis", "calculado", "reproducido", "validado", "confirmado")
TERMINALES = ("refutado", "indeterminado")
TODOS = ESCALONES + TERMINALES

# Solo se sube de escalon de uno en uno, y solo con la prueba que lo justifica.
SUBE = {
    "hipotesis": ("calculado",),
    "calculado": ("reproducido",),
    "reproducido": ("validado",),
    "validado": ("confirmado",),
}

ESQUEMA = """
CREATE TABLE IF NOT EXISTS problemas (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    titulo      TEXT NOT NULL,
    enunciado   TEXT NOT NULL,
    dominio     TEXT NOT NULL,
    autor       TEXT NOT NULL DEFAULT 'kuki',
    estado      TEXT NOT NULL DEFAULT 'abierto',
    notas       TEXT DEFAULT '',
    creado      REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS preguntas (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    problema_id INTEGER NOT NULL REFERENCES problemas(id),
    texto       TEXT NOT NULL,
    criterio    TEXT NOT NULL,          -- que tendria que pasar para darla por buena
    metodo      TEXT NOT NULL,          -- como se piensa atacar
    estado      TEXT NOT NULL DEFAULT 'abierta',
    creado      REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS hipotesis (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    pregunta_id INTEGER NOT NULL REFERENCES preguntas(id),
    afirmacion  TEXT NOT NULL,
    autor       TEXT NOT NULL,           -- quien la propone (modelo, persona, plantilla)
    valor       REAL,                    -- numero propuesto (NULL si es cualitativa)
    unidad      TEXT DEFAULT '',
    tolerancia  REAL NOT NULL DEFAULT 1e-6,
    estado      TEXT NOT NULL DEFAULT 'hipotesis',
    creado      REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS subtareas (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    pregunta_id INTEGER NOT NULL REFERENCES preguntas(id),
    hipotesis_id INTEGER REFERENCES hipotesis(id),  -- a que afirmacion sirve
    paso        TEXT NOT NULL,           -- nombre en el registro de pasos (meta/pasos.py)
    params      TEXT NOT NULL,           -- JSON
    estado      TEXT NOT NULL DEFAULT 'pendiente',
    creado      REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS ejecuciones (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    subtarea_id  INTEGER NOT NULL REFERENCES subtareas(id),
    motor        TEXT NOT NULL,          -- camino usado para calcular
    salida       REAL,
    segundos     REAL NOT NULL,
    entorno      TEXT NOT NULL,          -- python/numpy/scipy, hash del codigo
    ok           INTEGER NOT NULL DEFAULT 1,
    error        TEXT DEFAULT '',
    creado       REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS verificaciones (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ejecucion_id INTEGER NOT NULL REFERENCES ejecuciones(id),
    motor        TEXT NOT NULL,          -- camino usado para comprobar (DISTINTO)
    valor        REAL,
    diferencia   REAL,                   -- relativa al valor verificado
    tolerancia   REAL NOT NULL,
    veredicto    TEXT NOT NULL,          -- concuerda | discrepa | indeterminado
    detalle      TEXT DEFAULT '',
    creado       REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS conocimiento (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    pregunta_id   INTEGER NOT NULL REFERENCES preguntas(id),
    afirmacion    TEXT NOT NULL,
    valor         REAL,
    unidad        TEXT DEFAULT '',
    estado        TEXT NOT NULL,         -- escalon epistemico
    motores       TEXT NOT NULL,         -- JSON: quien calculo y quien comprobo
    limitaciones  TEXT DEFAULT '',
    fuentes       TEXT DEFAULT '[]',     -- JSON: procedencia
    creado        REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS contradicciones (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    pregunta_id    INTEGER NOT NULL REFERENCES preguntas(id),
    conocimiento_a INTEGER NOT NULL REFERENCES conocimiento(id),
    conocimiento_b INTEGER NOT NULL REFERENCES conocimiento(id),
    motivo         TEXT NOT NULL,
    estado         TEXT NOT NULL DEFAULT 'abierta',
    creado         REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS evaluaciones (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    banco         TEXT NOT NULL,
    version       TEXT NOT NULL,
    aciertos      INTEGER NOT NULL,
    fallos        INTEGER NOT NULL,
    indeterminados INTEGER NOT NULL,
    segundos      REAL NOT NULL,
    detalle       TEXT NOT NULL,        -- JSON con un renglon por problema
    creado        REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_preg_problema ON preguntas(problema_id);
CREATE INDEX IF NOT EXISTS idx_hip_pregunta  ON hipotesis(pregunta_id);
CREATE INDEX IF NOT EXISTS idx_sub_pregunta  ON subtareas(pregunta_id);
CREATE INDEX IF NOT EXISTS idx_sub_hipotesis ON subtareas(hipotesis_id);
CREATE INDEX IF NOT EXISTS idx_eje_subtarea  ON ejecuciones(subtarea_id);
CREATE INDEX IF NOT EXISTS idx_ver_ejecucion ON verificaciones(ejecucion_id);
CREATE INDEX IF NOT EXISTS idx_con_pregunta  ON conocimiento(pregunta_id);
"""


def conexion(ruta=None) -> sqlite3.Connection:
    """Abre (y crea si hace falta) la base de KUKIA META."""
    ruta = Path(ruta) if ruta else RUTA_POR_DEFECTO
    ruta.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(ruta))
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA journal_mode = WAL")
    con.executescript(ESQUEMA)
    con.commit()
    return con


def escalon(texto: str) -> int:
    """Numero de escalon (0..4) o -1 si es un estado terminal."""
    return ESCALONES.index(texto) if texto in ESCALONES else -1
