"""Regresion: la regla de vigencia de claves vive en UN solo sitio (claves.py).

Origen: F2-47 (informes_mainnet_20261029/f2-47_regla_unica/prueba_f2_47.py).
Aqui se ejecuta contra el codigo VIVO del repositorio (`coordinator/`), no
contra una copia `combined/`, para que la prueba siga sirviendo cuando el
arbol cambie.

Que se comprueba:
  TD1   guarda estatica: `kukia_core.py` no contiene NINGUNA copia de la regla
        (ni tablas de claves, ni estados de rotacion, ni la gracia). Si alguien
        la vuelve a escribir ahi, esto falla.
  TD1b  la regla esta exactamente una vez, en `claves.py`.
  TD1c  los tres usos vivos (activa, replica, resultado) estan declarados.
  PM    matriz de 27 decisiones de la tabla de paridad: cada caso se comprueba
        en las DOS bocas (booleano `clave_vigente` y lista `claves_para_firmar`).
  TD2   regla unica VIVA: se cambia `claves._REGLA` en caliente y las TRES bocas
        (lista, booleano y el predicado que el Core pega en el UPDATE) cambian
        a la vez. Es lo que demuestra que hay una sola definicion.
  TD3   uso desconocido -> ValueError, no una regla vacia que dejaria pasar todo.

No abre el ledger real ni la base operativa: usa una base sintetica en un
directorio temporal. No arranca servidores ni toca puertos.

Uso:  python pruebas_regresion/prueba_regla_claves.py
"""
from __future__ import annotations

import json
import shutil
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

AQUI = Path(__file__).resolve().parent
RAIZ = AQUI.parent
ARBOL = RAIZ / "coordinator"
RES: list[dict] = []


def check(cid, nombre, ok, esperado, obtenido):
    RES.append({"id": cid, "nombre": nombre, "pass": bool(ok),
                "esperado": esperado, "obtenido": obtenido})
    print(f"[{'PASS' if ok else 'FAIL'}] {cid} {nombre}\n"
          f"        esperado={esperado}\n        obtenido={obtenido}", flush=True)


def cargar_claves():
    sys.path.insert(0, str(ARBOL))
    if "claves" in sys.modules:
        del sys.modules["claves"]
    import claves  # noqa: F401
    return claves


def preparar_db(cl, datos: Path) -> sqlite3.Connection:
    datos.mkdir(parents=True, exist_ok=True)
    db = datos / "regla.db"
    if db.exists():
        db.unlink()
    con = sqlite3.connect(str(db))
    con.row_factory = sqlite3.Row
    con.execute("CREATE TABLE nodos(id TEXT PRIMARY KEY, nombre TEXT, "
                "clave_publica TEXT, vram_gb INT, tier REAL, reputacion REAL "
                "DEFAULT 1.0, ref TEXT, tareas_ok INT DEFAULT 0, tareas_fallos INT "
                "DEFAULT 0, ganado REAL DEFAULT 0.0, visto REAL, creado TEXT, "
                "retirado INT DEFAULT 0)")
    con.execute("CREATE TABLE tareas_replica(tarea_id TEXT PRIMARY KEY, nodo_id TEXT, "
                "estado TEXT, metrica TEXT, firma TEXT, device TEXT, "
                "duracion_ms REAL, ts REAL, asignado REAL)")
    cl.crear_esquema(con)
    return con


def nodo(con, nid, clave_registro):
    con.execute("INSERT INTO nodos(id,nombre,clave_publica,creado) VALUES(?,?,?,?)",
                (nid, nid, clave_registro, time.ctime()))


def fila(con, nid, clave, estado, motivo, gracia=None, desde=None):
    con.execute("INSERT INTO claves_nodo(nodo_id,clave_publica,estado,desde,hasta,"
                "gracia_hasta,motivo) VALUES(?,?,?,?,NULL,?,?)",
                (nid, clave, estado, desde or time.time(), gracia, motivo))


def main() -> int:
    cl = cargar_claves()
    core_txt = (ARBOL / "kukia_core.py").read_text(encoding="utf-8", errors="replace")
    claves_txt = (ARBOL / "claves.py").read_text(encoding="utf-8", errors="replace")

    # ---------------------------------------------------------------- TD1
    prohibidos = ["FROM claves_nodo", "gracia_hasta", "estado = 'replaced'",
                  "estado='replaced'", "_SQL_CLAVE", "_SQL_UPD_REPLICA",
                  "_clave_vigente"]
    sobrantes = [p for p in prohibidos if p in core_txt]
    check("TD1", "el Core no guarda ninguna copia de la regla",
          not sobrantes,
          "ninguno de " + ", ".join(prohibidos),
          f"sobrantes={sobrantes}")
    check("TD1b", "y la regla esta exactamente una vez, en claves.py",
          claves_txt.count("_REGLA = {") == 1,
          "_REGLA definido 1 vez en claves.py",
          f"_REGLA={claves_txt.count('_REGLA = {')}, "
          f"gracia en claves={claves_txt.count('gracia_hasta')}")
    check("TD1c", "los tres usos vivos estan declarados en la regla",
          sorted(cl._REGLA) == ["activa", "replica", "resultado"],
          "['activa', 'replica', 'resultado']", f"{sorted(cl._REGLA)}")

    tmp = Path(tempfile.mkdtemp(prefix="kukia_regla_"))
    try:
        con = preparar_db(cl, tmp / "datos")
        ahora = time.time()
        A, B, C = "aa" * 32, "bb" * 32, "cc" * 32

        nodo(con, "N_SIN_HIST", A)
        nodo(con, "N_ACTIVA", A)
        fila(con, "N_ACTIVA", A, "active", "registro", desde=ahora - 100)
        nodo(con, "N_ROTADA", B)
        fila(con, "N_ROTADA", A, "replaced", "planificada", gracia=ahora + 3600,
             desde=ahora - 200)
        fila(con, "N_ROTADA", B, "active", "planificada", desde=ahora - 50)
        nodo(con, "N_GRACIA_CADUCA", B)
        fila(con, "N_GRACIA_CADUCA", A, "replaced", "planificada", gracia=ahora - 1,
             desde=ahora - 200)
        fila(con, "N_GRACIA_CADUCA", B, "active", "planificada", desde=ahora - 50)
        nodo(con, "N_REVOCADA", "")
        fila(con, "N_REVOCADA", A, "revoked", "compromiso", desde=ahora - 300)
        nodo(con, "N_COMPROMISO", B)
        fila(con, "N_COMPROMISO", A, "replaced", "compromiso", desde=ahora - 300)
        fila(con, "N_COMPROMISO", B, "active", "compromiso", desde=ahora - 60)
        nodo(con, "N_ESPACIOS", f"  {A}  ")
        nodo(con, "N_VACIO", "")          # retirado, sin clave de registro
        fila(con, "N_VACIO", C, "revoked", "compromiso", desde=ahora - 300)
        con.commit()

        matriz = [
            ("N_SIN_HIST", A, "activa", True, "nodo sin historial: vale su clave de registro"),
            ("N_SIN_HIST", B, "activa", False, "nodo sin historial, otra clave"),
            ("N_SIN_HIST", A, "resultado", True, "nodo sin historial: vale para cobrar"),
            ("N_ACTIVA", A, "activa", True, "la clave activa vale"),
            ("N_ACTIVA", B, "activa", False, "clave que no es la activa"),
            ("N_ROTADA", B, "activa", True, "la nueva activa vale"),
            ("N_ROTADA", A, "activa", False, "la replica NO admite gracia"),
            ("N_ROTADA", A, "replica", False,
             "uso 'replica' (el que pide verificar_firma_replica): sin gracia"),
            ("N_ROTADA", B, "replica", True, "uso 'replica': la activa si vale"),
            ("N_SIN_HIST", A, "replica", True, "uso 'replica': nodo sin historial"),
            ("N_ROTADA", A, "resultado", True, "el pago SI admite gracia planificada"),
            ("N_ROTADA", C, "resultado", False, "clave ajena al nodo"),
            ("N_GRACIA_CADUCA", A, "resultado", False, "gracia caducada: no vale"),
            ("N_GRACIA_CADUCA", A, "activa", False, "gracia caducada: no vale (replica)"),
            ("N_GRACIA_CADUCA", B, "activa", True, "la activa sigue valiendo"),
            ("N_REVOCADA", A, "activa", False, "revocada: no vale"),
            ("N_REVOCADA", A, "resultado", False, "revocada: no vale ni para cobrar"),
            ("N_COMPROMISO", A, "resultado", False, "replaced por compromiso: sin gracia"),
            ("N_COMPROMISO", B, "activa", True, "la nueva del compromiso vale"),
            ("N_ESPACIOS", A, "activa", True, "clave de registro con espacios: se recorta"),
            ("N_ESPACIOS", A, "resultado", True, "idem para cobrar"),
            ("N_ACTIVA", "", "activa", False, "firma vacia: nunca vale"),
            ("N_ACTIVA", A.upper(), "activa", False, "mayusculas: no se relaja"),
            ("N_VACIO", "", "activa", False,
             "nodo retirado, historial sin activa y registro vacio: firma vacia NO cuela"),
            ("N_VACIO", "", "resultado", False, "idem en el pago"),
            ("NO_EXISTE", A, "resultado", False, "nodo inexistente"),
        ]

        for nid, clave, uso, esperado, desc in matriz:
            visto_booleano = cl.clave_vigente(con, nid, clave, uso)
            pares = [(p or "").strip() for p in cl.claves_para_firmar(con, nid, uso)]
            visto_lista = bool(clave) and clave in pares
            check(f"PM-{len(RES) + 1:02d}", f"[{uso}] {desc}",
                  visto_booleano == esperado and visto_lista == esperado,
                  f"esperado {esperado} en booleano y lista",
                  f"booleano={visto_booleano} lista={visto_lista}")

        # ------------------------------------------------------------ TD3
        try:
            cl.clave_vigente(con, "N_ACTIVA", A, "loquesea")
            raro = "no lanzo nada"
        except ValueError as e:
            raro = f"ValueError: {e}"
        check("TD3", "un uso desconocido no se traduce en 'vale todo'",
              raro.startswith("ValueError"), "ValueError", raro)

        # ------------------------------------------------------------ TD2
        con.execute("INSERT INTO tareas_replica(tarea_id,nodo_id,estado,metrica,asignado) "
                    "VALUES('TD2','N_ROTADA','reclamando','',?)", (time.time(),))
        con.commit()

        def update_con_predicado(clave):
            pred, pargs = cl.predicado_vigente("N_ROTADA", clave, "resultado")
            cur = con.execute(
                "UPDATE tareas_replica SET estado='entregada', metrica=? "
                "WHERE tarea_id='TD2' AND " + pred, ("m",) + pargs)
            return int(cur.rowcount or 0)

        antes = {
            "lista": A in [(p or "").strip() for p in
                           cl.claves_para_firmar(con, "N_ROTADA", "resultado")],
            "booleano": cl.clave_vigente(con, "N_ROTADA", A, "resultado"),
            "update": update_con_predicado(A),
        }
        con.rollback()

        original = dict(cl._REGLA)
        try:
            cl._REGLA["resultado"] = "estado = 'active'"      # se quita la gracia
            despues = {
                "lista": A in [(p or "").strip() for p in
                               cl.claves_para_firmar(con, "N_ROTADA", "resultado")],
                "booleano": cl.clave_vigente(con, "N_ROTADA", A, "resultado"),
                "update": update_con_predicado(A),
            }
            con.rollback()
        finally:
            cl._REGLA.clear()
            cl._REGLA.update(original)

        check("TD2a", "la regla de la gracia en un solo sitio: las tres bocas la ven",
              antes == {"lista": True, "booleano": True, "update": 1},
              "las tres aceptan la clave en gracia",
              f"lista={antes['lista']} booleano={antes['booleano']} update={antes['update']}")
        check("TD2b", "cambiar `_REGLA` cambia las tres a la vez (quitar la gracia)",
              despues == {"lista": False, "booleano": False, "update": 0},
              "las tres dejan de aceptarla",
              f"lista={despues['lista']} booleano={despues['booleano']} "
              f"update={despues['update']}")
        check("TD2c", "y al restaurar, vuelven las tres (no queda estado raro)",
              {k: (A in [(p or "").strip() for p in
                         cl.claves_para_firmar(con, "N_ROTADA", "resultado")]
                    if k == "lista" else cl.clave_vigente(con, "N_ROTADA", A, "resultado")
                    if k == "booleano" else update_con_predicado(A))
               for k in ("lista", "booleano", "update")}
              == {"lista": True, "booleano": True, "update": 1},
              "vuelven las tres", f"_REGLA={cl._REGLA}")
        con.rollback()

        # un uso desconocido en el predicado tampoco puede colar
        try:
            cl.predicado_vigente("N_ROTADA", A, "loquesea")
            raro2 = "no lanzo nada"
        except ValueError as e:
            raro2 = f"ValueError: {e}"
        check("TD3b", "el predicado del Core tambien rechaza el uso desconocido",
              raro2.startswith("ValueError"), "ValueError", raro2)

        con.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    fallos = [r for r in RES if not r["pass"]]
    print(f"\n[regla claves] {len(RES) - len(fallos)}/{len(RES)} PASS")
    print(json.dumps({"total": len(RES), "pass": len(RES) - len(fallos),
                      "fail": len(fallos),
                      "fallos": [r["id"] for r in fallos]}, ensure_ascii=False))
    return 0 if not fallos else 1


if __name__ == "__main__":
    raise SystemExit(main())
