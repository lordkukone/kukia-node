"""¿Alguna tabla declara menos columnas de las que escribe su INSERT?

POR QUE EXISTE
    P-1 (27-10-2026): el `CREATE TABLE IF NOT EXISTS transferencias(...)` de
    /transferir declaraba 6 columnas y su INSERT escribia 7. En una base NUEVA la
    tabla se creaba corta, el INSERT fallaba y el Core lo contaba como "nonce ya
    usado". Ese fallo estuvo a la vista mucho tiempo y no lo cazaba nadie porque
    el esquema y su INSERT viven en sitios distintos del mismo fichero.

    Esto mira todo el codigo de producto: junta las columnas de cada tabla y
    busca cada `INSERT INTO tabla(cols...)` que escriba en una columna que su
    tabla no declare.

LAS TRES RESPUESTAS QUE DA (y en que se diferencian)
    1. FALLO — el INSERT escribe una columna que NO declara ni el CREATE ni
       ningun `ALTER TABLE ... ADD COLUMN`. Eso no puede funcionar en ninguna
       base: esta roto seguro.
    2. AVISO — la columna solo la cubre un ALTER. Es la FORMA de P-1: seguro
       mientras el CREATE de esa tabla corra ANTES de los ALTER en una base
       nueva, y roto si el CREATE sale mas tarde (al vuelo, dentro de un
       endpoint) con su lista corta. Desde el texto no se puede saber cual de
       las dos cosas es: para eso esta `--en-vivo`.
    3. `--en-vivo` — arranca un Core sobre una base NUEVA (carpeta temporal,
       puerto libre) y compara contra el esquema de verdad. Aqui no hay
       opiniones: si la columna no esta, esta mal. Las tablas que no existen
       hasta el primer uso (como `transferencias`) se dicen, no se dan por
       buenas.

COMO LEE EL CODIGO (y que NO promete)
    - Une antes los literales partidos (`"a, " "b"` -> `"a, b"`), que es como
      estan escritos estos CREATE en el Core; si no, no se verian.
    - Tablas creadas en varios sitios: se suman sus columnas.
    - `INSERT INTO t VALUES(?,?,?)` (sin lista de columnas) solo se puede contar:
      se avisa como nota, no como fallo, y solo si el numero no cuadra.
    - SQL montado en tiempo de ejecucion (f-strings con el nombre de tabla) no se
      puede leer: si aparece, se dice, en vez de callarse.
    - NO ejecuta nada salvo con `--en-vivo`, y entonces siempre sobre una base
      nueva en una carpeta temporal. El ledger real y el 8300 no se abren nunca.

QUE MIRA POR DEFECTO
    El codigo de producto: coordinator/, ledger/, worker/, validator/,
    dashboard/, servicio/ y publico/. `tools/` se deja fuera a proposito: ahi
    viven las pruebas, y varias llevan a mano el esquema VIEJO como control
    negativo (tools/prueba_p1_transferencia_v1.py, sin ir mas lejos). Para
    incluirlas: --todos.

Uso:
    python tools/esquema_vs_insert.py              # listado rapido (sin procesos)
    python tools/esquema_vs_insert.py --en-vivo    # ademas, base nueva de verdad
    python tools/esquema_vs_insert.py --json       # maquina
    python tools/esquema_vs_insert.py --todos      # incluye tools/
    python tools/esquema_vs_insert.py --autoprueba # el revisor se comprueba solo
    python tools/esquema_vs_insert.py --fichero backups/p1_20261027/kukia_core.py

Sale con 1 si hay algo roto (para poder encadenarlo en la suite).
"""

from __future__ import annotations

import argparse
import json
import re
import socket
import sqlite3
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent

DIRS_PRODUCTO = ["coordinator", "ledger", "worker", "validator", "dashboard",
                 "servicio", "publico"]

# Palabras que abren una restriccion, no una columna.
RESTRICCIONES = ("constraint", "unique", "primary", "foreign", "check")

RE_CREATE = re.compile(r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?"
                       r"([A-Za-z_][\w]*)\s*\(", re.I)
RE_INSERT_COLS = re.compile(r"INSERT\s+(?:OR\s+\w+\s+)?INTO\s+"
                            r"([A-Za-z_][\w]*)\s*\(([^)]*)\)", re.I)
RE_INSERT_VALUES = re.compile(r"INSERT\s+(?:OR\s+\w+\s+)?INTO\s+"
                              r"([A-Za-z_][\w]*)\s+VALUES\s*\(", re.I)
RE_ALTER = re.compile(r"ALTER\s+TABLE\s+([A-Za-z_][\w]*)\s+ADD\s+(?:COLUMN\s+)?"
                      r"([A-Za-z_][\w]*)", re.I)
RE_INTO_VARIABLE = re.compile(r"INSERT\s+INTO\s*[f\"]?\s*\{", re.I)
# ALTER montado al vuelo (`ALTER TABLE {tabla} ADD COLUMN {col}`): sus columnas no
# se pueden leer, asi que si algo falta hay que decir que puede venir de ahi.
RE_ALTER_VARIABLE = re.compile(r"ALTER\s+TABLE\s*(?:[f\"']?\s*)?\{", re.I)


# ---------------------------------------------------------------------------
#  Lectura del SQL
# ---------------------------------------------------------------------------
def unir_literales(texto: str) -> str:
    """Une `"a, " "b"` en `"a, b"` y quita los comentarios SQL `-- …`.

    Lo primero es como estan escritos estos CREATE en el Core (varias lineas con
    implicit concatenation). Lo segundo es obligatorio ANTES de juntar lineas:
    los comentarios `--` de SQL acaban en el salto de linea, asi que al unirlos
    todo en una linea el comentario se comeria el resto de la sentencia (paso en
    meta/esquema.py: se perdian las columnas que venian detras del comentario).
    """
    texto = re.sub(r"--[^\n]*", " ", texto)
    anterior = None
    while anterior != texto:
        anterior = texto
        texto = re.sub(r"(['\"])\s*\n?\s*\1", "", texto)
    return " ".join(texto.split())


def trozo_parentesis(texto: str, desde: int) -> str:
    """El contenido del parentesis que abre en (o despues de) `desde`."""
    i = texto.index("(", desde)
    nivel = 0
    for j in range(i, len(texto)):
        if texto[j] == "(":
            nivel += 1
        elif texto[j] == ")":
            nivel -= 1
            if nivel == 0:
                return texto[i + 1:j]
    return ""


def columnas_declaradas(cuerpo: str) -> list:
    """Columnas de un CREATE TABLE (soporta restricciones de tabla y anidadas).

    Se separa por comas de PRIMER nivel: `UNIQUE(a, b)` no puede partir la lista
    en dos, y `CHECK (x IN (1,2))` tampoco.
    """
    partes, actual, nivel = [], [], 0
    for c in cuerpo:
        if c == "(":
            nivel += 1
        elif c == ")":
            nivel -= 1
        if c == "," and nivel == 0:
            partes.append("".join(actual))
            actual = []
        else:
            actual.append(c)
    partes.append("".join(actual))

    cols = []
    for p in partes:
        p = p.strip()
        if not p:
            continue
        # El identificador de cabeza, con expresion regular y no partiendo por
        # espacios: `UNIQUE(id, x)` empieza por `unique(id,` y asi se reconoce
        # como restriccion en vez de colarse como columna llamada "UNIQUE(id,".
        m = re.match(r"[A-Za-z_][\w]*", p.strip('"[]`'))
        if not m:
            continue
        primera = m.group(0)
        if primera.lower() in RESTRICCIONES:
            continue
        cols.append(primera)
    return cols


def analizar_texto(texto: str, etiqueta: str) -> dict:
    """Tablas (CREATE y ALTER por separado) e INSERTs con lista de columnas."""
    t = unir_literales(texto)
    tablas: dict = {}

    def hueco(nombre: str) -> dict:
        return tablas.setdefault(nombre, {"crear": [], "alter": [], "origenes": []})

    for m in RE_CREATE.finditer(t):
        nombre = m.group(1).lower()
        d = hueco(nombre)
        for c in columnas_declaradas(trozo_parentesis(t, m.start(1))):
            if c not in d["crear"]:
                d["crear"].append(c)
        d["origenes"].append(f"{etiqueta}:CREATE")
    for m in RE_ALTER.finditer(t):
        nombre = m.group(1).lower()
        d = hueco(nombre)
        if m.group(2) not in d["alter"]:
            d["alter"].append(m.group(2))
        d["origenes"].append(f"{etiqueta}:ALTER")

    inserts = []
    for m in RE_INSERT_COLS.finditer(t):
        cols = [c.strip().strip('"[]`') for c in m.group(2).split(",") if c.strip()]
        inserts.append({"tabla": m.group(1).lower(), "columnas": cols,
                        "fichero": etiqueta, "sin_nombres": False})
    for m in RE_INSERT_VALUES.finditer(t):
        cuerpo = trozo_parentesis(t, m.start())
        n = len([p for p in cuerpo.split(",") if p.strip()])
        inserts.append({"tabla": m.group(1).lower(), "columnas": [], "n": n,
                        "fichero": etiqueta, "sin_nombres": True})
    return {"tablas": tablas, "inserts": inserts,
            "dinamico": bool(RE_INTO_VARIABLE.search(t)),
            "alter_dinamico": bool(RE_ALTER_VARIABLE.search(t))}


# ---------------------------------------------------------------------------
#  Comparacion
# ---------------------------------------------------------------------------
def comparar(tablas: dict, inserts: list) -> dict:
    cortas, avisos, notas, sin_tabla = [], [], [], []
    for ins in inserts:
        tabla = ins["tabla"]
        t = tablas.get(tabla)
        if t is None:
            sin_tabla.append(ins)
            continue
        crear = {c.lower() for c in t["crear"]}
        alter = {c.lower() for c in t["alter"]}
        if ins["sin_nombres"]:
            declaradas = len(set(t["crear"]) | set(t["alter"]))
            if ins["n"] != declaradas:
                notas.append({"tabla": tabla, "fichero": ins["fichero"],
                              "detalle": f"INSERT sin columnas con {ins['n']} valores y "
                                         f"{declaradas} columnas declaradas"})
            continue
        fuera_de_todo = [c for c in ins["columnas"]
                         if c.lower() not in crear and c.lower() not in alter]
        if fuera_de_todo:
            cortas.append({"tabla": tabla, "fichero": ins["fichero"],
                           "faltan": fuera_de_todo, "insert": ins["columnas"],
                           "declaradas": t["crear"], "alter": t["alter"],
                           "origenes": sorted(set(t["origenes"]))})
            continue
        solo_alter = [c for c in ins["columnas"] if c.lower() not in crear]
        if solo_alter:
            avisos.append({"tabla": tabla, "fichero": ins["fichero"],
                           "columnas": solo_alter, "crear": t["crear"],
                           "alter": t["alter"]})
    return {"cortas": cortas, "avisos": avisos, "notas": notas,
            "sin_tabla": sin_tabla}


def revisar(paths: list) -> dict:
    tablas: dict = {}
    inserts, dinamicos, alter_dinamicos = [], [], []
    for p in paths:
        etiqueta = p.relative_to(RAIZ).as_posix() if p.is_absolute() else p.as_posix()
        try:
            texto = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        r = analizar_texto(texto, etiqueta)
        if r["dinamico"] and etiqueta not in dinamicos:
            dinamicos.append(etiqueta)
        if r.get("alter_dinamico") and etiqueta not in alter_dinamicos:
            alter_dinamicos.append(etiqueta)
        for nombre, d in r["tablas"].items():
            t = tablas.setdefault(nombre, {"crear": [], "alter": [], "origenes": []})
            for c in d["crear"]:
                if c not in t["crear"]:
                    t["crear"].append(c)
            for c in d["alter"]:
                if c not in t["alter"]:
                    t["alter"].append(c)
            t["origenes"].extend(d["origenes"])
        inserts.extend(r["inserts"])
    res = comparar(tablas, inserts)
    res.update({"tablas": tablas, "inserts": inserts, "dinamicos": dinamicos,
                "alter_dinamicos": alter_dinamicos})
    return res


def revisar_texto(texto: str, etiqueta: str) -> dict:
    """Igual que `revisar` pero sobre un texto suelto (para la autoprueba)."""
    r = analizar_texto(texto, etiqueta)
    res = comparar(r["tablas"], r["inserts"])
    res["tablas"] = r["tablas"]
    return res


# ---------------------------------------------------------------------------
#  En vivo: base nueva de verdad
# ---------------------------------------------------------------------------
def puerto_libre() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def arrancar_en_limpio(tmp: Path) -> tuple:
    """Arranca el Core sobre una base NUEVA (carpeta temporal, puerto libre)."""
    import os
    import subprocess
    import time
    import urllib.request

    env = dict(os.environ)
    env.update({"KUKIA_DB": str(tmp / "kukia.db"),
                "KUKIA_SELLO_KEY": str(tmp / "sello_key.txt"),
                "KUKIA_SELLOS_FILE": str(tmp / "sellos.jsonl"),
                "KUKIA_ADMIN_TOKEN": str(tmp / "admin_token.txt"),
                "KUKIA_AUTO_TAREAS": "0", "KUKIA_ARCHIVAR": "0",
                "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"})
    puerto = puerto_libre()
    log = open(tmp / "core.log", "w", encoding="utf-8")
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "kukia_core:app",
                             "--host", "127.0.0.1", "--port", str(puerto)],
                            cwd=str(RAIZ / "coordinator"), env=env,
                            stdout=log, stderr=subprocess.STDOUT)
    for _ in range(160):
        if proc.poll() is not None:
            raise SystemExit(f"el Core murio al arrancar (codigo {proc.returncode}); "
                             f"ver {tmp / 'core.log'}")
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{puerto}/stats", timeout=2):
                return proc, log, tmp / "kukia.db"
        except Exception:
            time.sleep(0.25)
    proc.kill()
    raise SystemExit("el Core no respondio en 40 s; ver la carpeta temporal")


def columnas_reales(db: Path) -> dict:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        tablas = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")]
        return {t: [f[1] for f in con.execute(f"PRAGMA table_info({t})")]
                for t in tablas}
    finally:
        con.close()


def comprobar_en_vivo(res: dict) -> tuple:
    """(faltas, perezosas): que INSERTs escriben columnas que la base nueva no tiene."""
    import shutil
    import tempfile

    tmp = Path(tempfile.mkdtemp(prefix="esquema_vs_insert_"))
    proc = log = None
    try:
        proc, log, db = arrancar_en_limpio(tmp)
        reales = columnas_reales(db)
        faltas = []
        for ins in res["inserts"]:
            cols = reales.get(ins["tabla"])
            if cols is None:
                continue
            tengo = {x.lower() for x in cols}
            fuera = [c for c in ins["columnas"] if c.lower() not in tengo]
            if fuera:
                faltas.append((ins["tabla"], fuera, ins["fichero"]))
        escritas = {i["tabla"] for i in res["inserts"]}
        return faltas, sorted(escritas - set(reales)), len(reales)
    finally:
        if proc is not None:
            try:
                if proc.poll() is None:
                    proc.kill()
                    proc.wait(timeout=15)
            except Exception:
                pass
        if log is not None:
            try:
                log.close()
            except Exception:
                pass
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
#  Autoprueba: el revisor tiene que ver el fallo de P-1 y no inventar otros
# ---------------------------------------------------------------------------
def autoprueba() -> int:
    print("== AUTOPRUEBA ==")
    fallos = 0

    def caso(nombre: str, ok: bool, detalle: str = ""):
        nonlocal fallos
        print(f"  {'OK   ' if ok else 'FALLO'} {nombre}" + (f" ({detalle})" if detalle else ""))
        if not ok:
            fallos += 1

    viejo = ('con.execute("CREATE TABLE IF NOT EXISTS transferencias("\n'
             '            "id INTEGER PRIMARY KEY AUTOINCREMENT, de_nodo TEXT, "\n'
             '            "a_direccion TEXT, monto REAL, nonce TEXT, ts TEXT, "\n'
             '            "UNIQUE(de_nodo, nonce))")\n'
             'con.execute("INSERT INTO transferencias(de_nodo,a_direccion,monto,nonce,"\n'
             '            "ts,firma,clave_publica) VALUES(?,?,?,?,?,?,?)")\n')
    r = revisar_texto(viejo, "caso_viejo.py")
    caso("detecta el esquema corto de P-1",
         len(r["cortas"]) == 1
         and sorted(r["cortas"][0]["faltan"]) == ["clave_publica", "firma"],
         str(r["cortas"][0]["faltan"]) if r["cortas"] else "no vio nada")

    bueno = ('con.execute("CREATE TABLE IF NOT EXISTS transferencias(" "id INTEGER, "\n'
             '            "de_nodo TEXT, a_direccion TEXT, monto REAL, nonce TEXT, ts TEXT, "\n'
             '            "firma TEXT DEFAULT \'\', clave_publica TEXT DEFAULT \'\', "\n'
             '            "UNIQUE(de_nodo, nonce))")\n'
             'con.execute("INSERT INTO transferencias(de_nodo,a_direccion,monto,nonce,"\n'
             '            "ts,firma,clave_publica) VALUES(?,?,?,?,?,?,?)")\n')
    r = revisar_texto(bueno, "caso_bueno.py")
    caso("con las 8 columnas no avisa de nada", not r["cortas"] and not r["avisos"])

    con_alter = ('con.execute("CREATE TABLE IF NOT EXISTS t(id INTEGER, a TEXT, b TEXT)")\n'
                 'con.execute("ALTER TABLE t ADD COLUMN c TEXT DEFAULT \'\'")\n'
                 'con.execute("INSERT INTO t(id,a,b,c) VALUES(?,?,?,?)")\n')
    r = revisar_texto(con_alter, "caso_alter.py")
    caso("una columna que solo cubre un ALTER sale como AVISO, no como buena",
         not r["cortas"] and len(r["avisos"]) == 1
         and r["avisos"][0]["columnas"] == ["c"],
         str([a["columnas"] for a in r["avisos"]]))

    anidado = ('con.execute("CREATE TABLE IF NOT EXISTS u(id INTEGER, x TEXT, "\n'
               '"CHECK (x IN (\'a\',\'b\')), UNIQUE(id, x))")\n'
               'con.execute("INSERT INTO u(id,x) VALUES(?,?)")\n')
    r = revisar_texto(anidado, "caso_anidado.py")
    caso("lee CHECK/UNIQUE anidados sin inventar columnas",
         not r["cortas"] and r["tablas"]["u"]["crear"] == ["id", "x"],
         str(r["tablas"]["u"]["crear"]))

    con_comentario = ('SQL = """\n'
                      'CREATE TABLE IF NOT EXISTS w (\n'
                      '    id INTEGER PRIMARY KEY,\n'
                      '    a TEXT NOT NULL,\n'
                      '    detalle TEXT NOT NULL,   -- JSON con un renglon\n'
                      '    creado REAL NOT NULL\n'
                      ');\n'
                      '"""\n'
                      'con.execute("INSERT INTO w(id,a,detalle,creado) VALUES(?,?,?,?)")\n')
    r = revisar_texto(con_comentario, "caso_comentario.py")
    caso("un comentario SQL `--` no se come las columnas que vienen detras",
         not r["cortas"] and r["tablas"]["w"]["crear"] == ["id", "a", "detalle", "creado"],
         str(r["tablas"]["w"]["crear"]))

    print(f"\n  {'TODO OK' if not fallos else f'{fallos} FALLOS'} en la autoprueba")
    return 1 if fallos else 0


# ---------------------------------------------------------------------------
# Carpetas y ficheros que son COPIAS o respaldos, no codigo que corra: si se
# revisan, cada copia vieja saca los fallos de su epoca (y el de P-1 diez veces).
RUIDO = ("backups", "_backup", "_auditoria", "_mezcla", "copia_fuentes",
         "sitio_publicado", "logs")


def ficheros(dirs: list, todos: bool) -> list:
    raices = [RAIZ] if todos else [RAIZ / d for d in dirs]
    vistos = []
    for r in raices:
        if not r.exists():
            continue
        for p in sorted(r.rglob("*.py")):
            partes = p.parts
            if "__pycache__" in partes:
                continue
            if any(part.startswith(RUIDO) or part in RUIDO for part in partes):
                continue
            if p.name == "esquema_vs_insert.py":
                # El propio revisor lleva SQL de mentira (los casos de su
                # autoprueba): mirarse a si mismo solo mete ruido.
                continue
            vistos.append(p)
    return vistos


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true", help="salida para maquina")
    ap.add_argument("--todos", action="store_true", help="incluye tools/")
    ap.add_argument("--autoprueba", action="store_true",
                    help="comprueba que el propio revisor detecta el caso de P-1")
    ap.add_argument("--fichero", action="append", default=[],
                    help="revisa solo esos ficheros (se puede repetir)")
    ap.add_argument("--en-vivo", action="store_true",
                    help="ademas, arranca un Core sobre una base NUEVA y mira el "
                         "esquema de verdad (resuelve las columnas que llegan por ALTER)")
    args = ap.parse_args()

    if args.autoprueba:
        return autoprueba()

    if args.fichero:
        paths = [Path(f) if Path(f).is_absolute() else RAIZ / f for f in args.fichero]
        faltan = [str(p) for p in paths if not p.exists()]
        if faltan:
            raise SystemExit(f"no existe: {', '.join(faltan)}")
    else:
        paths = ficheros(DIRS_PRODUCTO, args.todos)

    res = revisar(paths)
    tablas, cortas, avisos = res["tablas"], res["cortas"], res["avisos"]

    if args.json:
        print(json.dumps({"ficheros": len(paths), "tablas": len(tablas),
                          "cortas": cortas, "avisos": avisos, "notas": res["notas"],
                          "dinamicos": res["dinamicos"],
                          "alter_dinamicos": res.get("alter_dinamicos", []),
                          "sin_create": sorted({i["tabla"] for i in res["sin_tabla"]})},
                         indent=2, ensure_ascii=False))
        return 1 if cortas else 0

    print(f"revisados {len(paths)} ficheros | {len(tablas)} tablas declaradas | "
          f"{len(res['inserts'])} INSERT")

    if cortas:
        print(f"\n== ROTO ({len(cortas)}): el INSERT escribe columnas que nadie declara ==")
        for f in cortas:
            print(f"  {f['tabla']}: el INSERT de {f['fichero']} escribe {f['insert']}")
            print(f"    el CREATE declara {f['declaradas']}"
                  f"{' y los ALTER añaden ' + str(f['alter']) if f['alter'] else ''}")
            print(f"    FALTAN: {f['faltan']}   (CREATE/ALTER en {f['origenes'][:3]}"
                  f"{'…' if len(f['origenes']) > 3 else ''})")
        if res.get("alter_dinamicos"):
            print("\n  OJO: hay ALTER montados al vuelo en "
                  + ", ".join(res["alter_dinamicos"]))
            print("  (p.ej. `ALTER TABLE {tabla} ADD COLUMN {col}`). Sus columnas no se")
            print("  pueden leer: si algo de arriba falta, puede venir de ahi.")
    else:
        print("\nOK: ningun INSERT escribe una columna que no declare su tabla")

    if avisos:
        print(f"\n== AVISO ({len(avisos)}): la columna solo la cubre un ALTER ==")
        print("  Es la FORMA de P-1. Seguro solo si el CREATE de esa tabla corre")
        print("  ANTES de los ALTER en una base nueva; roto si el CREATE sale tarde")
        print("  (al vuelo, en un endpoint) con su lista corta. Lo decide --en-vivo.")
        vistos = set()
        for a in avisos:
            clave = (a["tabla"], tuple(a["columnas"]))
            if clave in vistos:
                continue
            vistos.add(clave)
            print(f"  {a['tabla']} ({a['fichero']}): escribe {a['columnas']}")
            print(f"    CREATE declara {a['crear']} · ALTER añade {a['alter']}")

    if res["notas"]:
        print(f"\n== NOTAS ({len(res['notas'])}) (INSERT sin lista de columnas, no es fallo) ==")
        for n in res["notas"]:
            print(f"  {n['tabla']} ({n['fichero']}): {n['detalle']}")

    invisibles = sorted({i["tabla"] for i in res["sin_tabla"]})
    if invisibles:
        print(f"\n== SIN CREATE A LA VISTA ({len(invisibles)}) ==")
        print("  Se escribe en ellas pero su CREATE no esta en el codigo de producto")
        print("  (base que crea otro programa, o SQL montado al vuelo):")
        print("  " + ", ".join(invisibles))

    if res["dinamicos"]:
        print("\n== SQL MONTADO AL VUELO ==")
        for f in res["dinamicos"]:
            print(f"  {f}: el INSERT lleva el nombre de tabla dentro")
        print("  Eso no se puede leer sin ejecutar; el revisor no lo adivina.")

    codigo = 1 if cortas else 0

    if args.en_vivo:
        print("\n== EN VIVO: Core nuevo sobre base NUEVA (carpeta temporal) ==")
        faltas, perezosas, n_tablas = comprobar_en_vivo(res)
        print(f"  tablas creadas al arrancar: {n_tablas}")
        if faltas:
            print("  ROTO DE VERDAD (la base nueva no tiene esa columna):")
            for tabla, fuera, fichero in faltas:
                print(f"    {tabla}: el INSERT de {fichero} escribe {fuera}")
            codigo = 1
        else:
            print("  OK: todo lo que escriben los INSERT existe en la base nueva")
        if perezosas:
            print(f"  sin comprobar en vivo ({len(perezosas)}): se crean al primer uso, "
                  f"no al arrancar")
            print("  " + ", ".join(perezosas))

    return codigo


if __name__ == "__main__":
    raise SystemExit(main())
