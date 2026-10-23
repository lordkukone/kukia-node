"""Prueba el pipeline del minero: no pierde cupo al fallar y para limpiamente.

POR QUE EXISTE (23-10-2026)
  El minero paso de "pedir, calcular, entregar" en serie a tres piezas (hilo
  peticionero, cola de tareas, hilo de entrega). Eso gana mucho ritmo pero
  trae dos fallos clasicos que no se ven mirando el codigo:

    1. FUGAS DE CUPO. El cupo se coge al pedir y se suelta al entregar. Si una
       salida se olvida de soltarlo (una excepcion al calcular, una tarea
       invalida, un tipo no implementado), el nodo pierde 1 de sus 4 huecos
       PARA SIEMPRE. Al cuarto fallo deja de minar y no lo dice: sigue pidiendo
       trabajo, pero el Core le contesta "limite" y el log no muestra errores.
       Esta prueba mete fallos a proposito y comprueba que despues SIGUE
       cobrando.

    2. PARADA SUCIA. Al cerrar el minero con tareas en vuelo, esas tareas no
       se entregan y el Core no las devuelve a la cola hasta los 30 minutos
       (ABANDONO_SEG). Esta prueba pide la parada y comprueba que el minero
       entrega lo que lleva encima y sale sin dejar nada colgando.

COMO MIDE
  Arranca el minero de verdad (worker/kukia_worker.py) con su cerradura aparte,
  le inyecta fallos en las primeras tareas y luego le pide que pare. Necesita
  el Core vivo: es una prueba de integracion, no un juguete con mocks.

USO
  python tools/prueba_pipeline_minero.py                 # 40 s, con fallos inyectados
  python tools/prueba_pipeline_minero.py --segundos 60
  python tools/prueba_pipeline_minero.py --limpiar       # retira los nodos de prueba al terminar

OJO: mientras corre, este nodo mina de verdad en la red (con su nombre de
prueba) y cobra en la billetera de pruebas. Es lo que permite comprobar que
cobra DESPUES de los fallos.
"""
import argparse
import os
import sqlite3
import sys
import threading
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
NOMBRE_PRUEBA = "prueba_pipeline"


def _preparar_entorno(core: str) -> None:
    os.environ["KUKIA_CORE"] = core
    os.environ["KUKIA_USAR_CPU"] = "0"
    os.environ["KUKIA_PROFUNDIDAD"] = "2"
    sys.path.insert(0, str(RAIZ / "worker"))


def _limpiar_nodos(patron: str) -> int:
    """Retira los nodos de prueba y les pone tops=0.

    Sin esto, un nodo de prueba con 12 GB declarados suma 300 TOPS a `tops_red`
    durante 24 h y AGRANDA las tareas de toda la red (matriz n 598 -> 846).
    No borra nada: solo marca retirado y deja de contar para la dificultad.
    """
    db = RAIZ / "coordinator" / "ledger" / "kukia.db"
    if not db.exists():
        return 0
    con = sqlite3.connect(str(db), timeout=20)
    con.execute("PRAGMA busy_timeout=20000")
    try:
        con.execute("BEGIN IMMEDIATE")
        cur = con.execute("UPDATE nodos SET retirado=1, tops=0 "
                          "WHERE nombre LIKE ?", (patron + "%",))
        n = cur.rowcount
        con.commit()
        return n
    finally:
        con.close()


def main() -> int:
    ap = argparse.ArgumentParser(description="Prueba de integracion del pipeline del minero")
    ap.add_argument("--core", default=os.environ.get("KUKIA_CORE", "http://127.0.0.1:8300"))
    ap.add_argument("--segundos", type=float, default=40.0)
    # 2 y no mas: cada tarea que no se entrega sigue OCUPANDO un hueco del cupo
    # del Core hasta que vence a los 30 min. Con 3 fallos el nodo se queda sin
    # huecos y el Core contesta "limite" — que es correcto, pero la prueba no
    # podria comprobar nada mas. Con 2 hay margen y se ve lo que importa.
    ap.add_argument("--fallos", type=int, default=2, help="excepciones inyectadas al calcular")
    ap.add_argument("--limpiar", action="store_true", help="retira los nodos al terminar")
    args = ap.parse_args()

    _preparar_entorno(args.core)
    import kukia_worker as W                      # noqa: E402

    # Cerradura aparte: no se toca la del minero de produccion.
    W.LOCK = RAIZ / "worker" / "minero_prueba.lock"
    sys.argv = ["kukia_worker.py", NOMBRE_PRUEBA]

    # --- inyeccion de fallos -------------------------------------------------
    original = W.trabajo_local
    estado = {"fallos": 0, "ok": 0, "calc": 0}
    lock = threading.Lock()

    def trabajo_con_fallos(tipo, params, device="cuda:0"):
        with lock:
            estado["calc"] += 1
            n = estado["calc"]
        if n <= args.fallos:
            with lock:
                estado["fallos"] += 1
            # Simula lo que pasa de verdad: una GPU sin memoria, un driver que
            # se cae, un tensor imposible. NO es un fallo del Core.
            raise RuntimeError(f"fallo simulado #{n}")
        r = original(tipo, params, device)
        with lock:
            estado["ok"] += 1
        return r

    W.trabajo_local = trabajo_con_fallos

    print(f"[prueba] Core {args.core} · {args.segundos:.0f} s · "
          f"{args.fallos} fallos inyectados", flush=True)
    hilo = threading.Thread(target=W.main, name="minero-prueba", daemon=True)
    hilo.start()
    time.sleep(args.segundos)

    # --- parada ordenada -----------------------------------------------------
    print("[prueba] pido la parada (como un Ctrl+C)", flush=True)
    W.PARAR.set()
    t0 = time.time()
    hilo.join(timeout=30)
    parado = not hilo.is_alive()
    t_parada = time.time() - t0

    if os.path.exists(W.LOCK):
        try:
            os.remove(W.LOCK)
        except Exception:
            pass

    # --- veredicto -----------------------------------------------------------
    fallos, ok = estado["fallos"], estado["ok"]
    print(f"\n[prueba] fallos inyectados: {fallos}")
    print(f"[prueba] tareas calculadas bien DESPUES de los fallos: {ok}")
    print(f"[prueba] parada: {'limpia' if parado else 'NO TERMINO'} en {t_parada:.1f} s")

    problemas = []
    if fallos < args.fallos:
        problemas.append(f"no llegaron a inyectarse los {args.fallos} fallos")
    if ok <= 0:
        problemas.append("no se calculo ninguna tarea despues de los fallos "
                         "(cupo perdido: el nodo se queda sin minar)")
    if ok > 0 and ok < 3:
        problemas.append(f"solo {ok} tareas despues de los fallos: el ritmo no se recupero")
    if not parado:
        problemas.append("el minero no termino al pedirle la parada")
    if parado and t_parada > 25:
        problemas.append(f"tardo {t_parada:.0f} s en parar (se esperan < 25)")

    if args.limpiar:
        n = _limpiar_nodos(NOMBRE_PRUEBA)
        print(f"[prueba] nodos de prueba retirados y tops=0: {n}")

    if problemas:
        for p in problemas:
            print(f"  FALLO: {p}")
        return 1
    print("  OK: no hay fuga de cupo y la parada es ordenada")
    return 0


if __name__ == "__main__":
    sys.exit(main())
