"""Aplica F2-53 al worker: doble liberacion, log propio (M-12) y muerte silenciosa.

Cada sustitucion se exige EXACTAMENTE una vez: si el fichero no es el esperado,
el script no escribe nada. Uso:
    python aplicar_f2_53.py            # aplica
    python aplicar_f2_53.py --revisar  # solo comprueba que los anclajes existen
"""
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
WORKER = RAIZ / "worker" / "kukia_worker.py"
ROTAR = RAIZ / "tools" / "rotar_logs.py"

BLOQUE_LOG = '''
# --- REGISTRO DEL MINERO (M-12) ----------------------------------------------
# POR QUE (30-10-2026): `worker/minero.log` lo escribia un `cmd >>` del lanzador
# y `tools/rotar_logs.py` lo truncaba por fuera. Truncar un fichero que otro
# proceso tiene abierto hace que ese proceso siga escribiendo en su
# desplazamiento viejo, y el hueco se rellena de bytes NUL: el 30-10-2026 habia
# 9.515.053 NUL en 9,5 MB, o sea un log inservible para diagnosticar.
#
# Ahora el UNICO escritor es este modulo: un cerrojo, una marca de tiempo por
# linea y rotacion propia (la cola pasa a .1, .1 a .2 y el vivo se trunca desde
# dentro). Si el lanzador redirige la salida a ese mismo fichero, su descriptor
# queda ocioso: nadie mas escribe en el.
MINERO_LOG = Path(__file__).parent / "minero.log"
_MINERO_MAX = max(256 * 1024, int(os.environ.get("KUKIA_MINERO_LOG_BYTES",
                                                 str(5 * 1024 * 1024))))
_MINERO_COLA = max(64 * 1024, int(os.environ.get("KUKIA_MINERO_LOG_COLA",
                                                 str(2 * 1024 * 1024))))
_MINERO_GEN = max(1, int(os.environ.get("KUKIA_MINERO_LOG_GENERACIONES", "2")))


class _Registro:
    """Escritor unico del registro del minero: fechado, con cerrojo y rotacion."""

    encoding = "utf-8"
    errors = "replace"

    def __init__(self):
        self._lock = threading.Lock()
        self._f = None
        self._buf = ""
        self._consola = None
        self.rotaciones = 0

    def instalar(self) -> None:
        """Sustituye stdout/stderr por este escritor (y espeja en la consola)."""
        consola = None
        try:
            if sys.stdout is not None and sys.stdout.isatty():
                consola = sys.stdout
        except Exception:
            consola = None
        self._consola = consola
        try:
            sys.stdout = self
            sys.stderr = self
        except Exception:
            pass

    def write(self, texto) -> int:
        if not isinstance(texto, str):
            texto = str(texto)
        if self._consola is not None:
            try:
                self._consola.write(texto)
                self._consola.flush()
            except Exception:
                pass
        self._buf += texto
        while "\\n" in self._buf:
            linea, self._buf = self._buf.split("\\n", 1)
            if linea.strip():
                self._linea(linea)
        return len(texto)

    def flush(self) -> None:
        if self._buf:
            linea, self._buf = self._buf, ""
            if linea.strip():
                self._linea(linea)
        if self._consola is not None:
            try:
                self._consola.flush()
            except Exception:
                pass

    def isatty(self) -> bool:
        return False

    def _linea(self, texto: str) -> None:
        with self._lock:
            try:
                if self._f is None:
                    self._f = MINERO_LOG.open("a", encoding="utf-8",
                                              errors="replace")
                self._f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} {texto}\\n")
                self._f.flush()
                if self._f.tell() >= _MINERO_MAX:
                    self._rotar()
            except Exception:
                pass

    def _rotar(self) -> None:
        """Rota desde dentro: nadie mas escribe este fichero."""
        try:
            if self._f is not None:
                self._f.close()
            self._f = None
            cola = b""
            try:
                total = MINERO_LOG.stat().st_size
                with MINERO_LOG.open("rb") as f:
                    f.seek(max(0, total - _MINERO_COLA))
                    cola = f.read()
            except Exception:
                cola = b""
            for i in range(_MINERO_GEN, 1, -1):
                viejo = MINERO_LOG.with_name(f"minero.log.{i - 1}")
                nuevo = MINERO_LOG.with_name(f"minero.log.{i}")
                try:
                    if viejo.exists():
                        viejo.replace(nuevo)
                except Exception:
                    pass
            if cola:
                try:
                    MINERO_LOG.with_name("minero.log.1").write_bytes(cola)
                except Exception:
                    pass
            MINERO_LOG.write_bytes(b"")
            self.rotaciones += 1
            self._f = MINERO_LOG.open("a", encoding="utf-8", errors="replace")
            self._f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} registro rotado "
                          f"({_MINERO_MAX // 1048576} MB, {_MINERO_GEN} "
                          f"generaciones; en total {self.rotaciones})\\n")
            self._f.flush()
        except Exception:
            pass


_REG = _Registro()


def _log_minero(*trozos) -> None:
    """Una linea fechada en el registro del minero (no pasa por la consola)."""
    try:
        _REG._linea(" ".join(str(t) for t in trozos))
    except Exception:
        pass

'''

PARCHES = [
    # --- 1. bloque del registro del minero ------------------------------------
    ("registro del minero (M-12)",
     "\nCUENTAS: dict = {}\n",
     BLOQUE_LOG + "\nCUENTAS: dict = {}\n"),
    # --- 2. nota de version en la cabecera ------------------------------------
    ('nota de version F2-53 en la cabecera',
     '    "esta minando" sea un dato y no una suposicion.\n"""\n',
     '    "esta minando" sea un dato y no una suposicion.\n'
     '\n'
     'AUDITORIA 2026-10-30 (F2-53, la manana del dispositivo esteril):\n'
     '  - DOBLE LIBERACION: los tres caminos de error de _pedir soltaban el\n'
     '    cupo y el finally lo volvia a soltar. El contador en_vuelo es ahora la\n'
     '    fuente de verdad: liberar de mas es imposible y queda anotado.\n'
     '  - M-12: el registro del minero lo escribe y lo rota el propio proceso,\n'
     '    con cerrojo y una marca de tiempo por linea. Nadie lo trunca por\n'
     '    fuera (eso era lo que lo llenaba de bytes NUL).\n'
     '  - M-02: una cola llena ya no deja el hilo que pide colgado para\n'
     '    siempre (put con timeout), la salud avisa si falta un hilo y un\n'
     '    BaseException en el calculo o en la entrega deja rastro antes de\n'
     '    propagarse. No se anade recuperacion automatica: la causa de la\n'
     '    manana no esta demostrada y reiniciar a ciegas borra la prueba.\n'
     '"""\n'),
    # --- 3. espera maxima para dejar una tarea en la cola --------------------
    ('espera maxima en la cola (M-02)',
     'PROFUNDIDAD = max(1, min(10, int(os.environ.get("KUKIA_PROFUNDIDAD", "2"))))\n',
     'PROFUNDIDAD = max(1, min(10, int(os.environ.get("KUKIA_PROFUNDIDAD", "2"))))\n'
     '# F2-53 (M-02): cuanto puede esperar el hilo que pide para dejar una tarea\n'
     '# en la cola. Antes esperaba PARA SIEMPRE: si el calculo se atascaba, la\n'
     '# cola se llenaba y el dispositivo quedaba mudo sin una sola linea.\n'
     'COLA_ESPERA = max(1.0, float(os.environ.get("KUKIA_COLA_ESPERA", "30")))\n'),
    # --- 4. liberacion exactamente una vez -----------------------------------
    ('''        try:
            cupo_vuelo.release()
        except Exception:
            pass
        with en_vuelo_lock:
            en_vuelo["n"] = max(0, en_vuelo["n"] - 1)
''',
     '''        # F2-53 (doble liberacion): el contador es la fuente de verdad. Solo se
        # libera si de verdad hay una tarea en vuelo, asi que soltar de mas ya
        # no puede inflar el semaforo (antes los tres caminos de error de
        # _pedir lo soltaban y el finally lo volvia a soltar).
        with en_vuelo_lock:
            if en_vuelo["n"] <= 0:
                sobran = True
            else:
                en_vuelo["n"] -= 1
                sobran = False
        if sobran:
            _log_arranque("cupo: liberacion de mas ignorada (contador en 0)")
            return
        try:
            cupo_vuelo.release()
        except Exception:
            pass
'''),
    # --- 5. los tres caminos de error de _pedir ------------------------------
    ('''                    bloqueado["hasta"] = time.time() + 20
                    _soltar()
                    continue
''',
     '''                    bloqueado["hasta"] = time.time() + 20
                    tengo_cupo = False        # F2-53: lo suelta esta rama, no el finally
                    _soltar()
                    continue
'''),
    ('''                if rp.status_code != 200:
                    detalle = (rp.text or "")[:160]
                    _soltar()
''',
     '''                if rp.status_code != 200:
                    detalle = (rp.text or "")[:160]
                    tengo_cupo = False        # F2-53: exactamente una liberacion
                    _soltar()
'''),
    ('''                if not _cuerpo.get("tarea"):
                    motivo = str(_cuerpo.get("motivo") or "")
                    _soltar()
''',
     '''                if not _cuerpo.get("tarea"):
                    motivo = str(_cuerpo.get("motivo") or "")
                    tengo_cupo = False        # F2-53: exactamente una liberacion
                    _soltar()
'''),
    # --- 6. entregar la tarea a la cola sin bloquearse para siempre ----------
    ('''                # El cupo de esta tarea lo suelta quien la entregue.
                tengo_cupo = False
                cola_tareas.put(_cuerpo)
''',
     '''                # El cupo de esta tarea lo suelta quien la entregue.
                tengo_cupo = False
                try:
                    cola_tareas.put(_cuerpo, timeout=COLA_ESPERA)
                except queue.Full:
                    # F2-53 (M-02): antes era un put sin timeout. Con el calculo
                    # atascado, la cola se llenaba y este hilo se quedaba aqui
                    # para siempre: la GPU muda y el proceso vivo, sin ninguna
                    # linea que lo delatara. Ahora se anota y el cupo vuelve.
                    _pid = str((_cuerpo.get("tarea") or {}).get("id") or "?")[:8]
                    _log_arranque(f"pedir-{device}: cola llena {COLA_ESPERA:.0f} s; "
                                  f"tarea {_pid} no admitida (vence en el Core)")
                    print(f"[KUKIA] {etiqueta} la cola de calculo no admite trabajo "
                          f"en {COLA_ESPERA:.0f} s; sigo pidiendo", flush=True)
                    _soltar()
                    time.sleep(1.0)
                    continue
'''),
    # --- 7. entrega: un BaseException deja rastro ----------------------------
    ('''        except Exception as e:
            print(f"[KUKIA] {etiqueta} entrega de {pid[:8]} fallida: "
                  f"{type(e).__name__}: {e}", flush=True)
        finally:
''',
     '''        except BaseException as e:
            print(f"[KUKIA] {etiqueta} entrega de {pid[:8]} fallida: "
                  f"{type(e).__name__}: {e}", flush=True)
            if not isinstance(e, Exception):
                # F2-53: un BaseException en este hilo se llevaba por delante la
                # entrega sin dejar rastro. Queda escrito antes de propagar.
                _log_arranque(f"entrega {str(device)}: {type(e).__name__}: {e}")
                raise
        finally:
'''),
    # --- 8. calculo: un BaseException deja rastro ----------------------------
    ('''            except Exception as e:
                if en_mano:
                    _soltar()
                    en_mano = False
''',
     '''            except BaseException as e:
                if en_mano:
                    _soltar()
                    en_mano = False
                if not isinstance(e, Exception):
                    # F2-53: un BaseException mataba este hilo en silencio; la
                    # GPU dejaba de consumir, la cola se llenaba y el hilo que
                    # pide se quedaba bloqueado (la firma exacta de M-02). Queda
                    # escrito, con pilas, antes de propagar.
                    _log_arranque(f"calc-{device}: {type(e).__name__}: {e}")
                    _volcar_pilas()
                    raise
'''),
    # --- 9. la salud avisa si falta un hilo ----------------------------------
    ('''    ultimo_aviso: dict = {}
    ultimo_dump = 0.0
''',
     '''    ultimo_aviso: dict = {}
    ultimo_dump = 0.0
    ultimo_muerto: dict = {}
'''),
    ('''            ahora = time.time()
            for dev, c in list(CUENTAS.items()):
''',
     '''            ahora = time.time()
            # F2-53 (M-02): un hilo muerto deja la GPU muda y el proceso vivo.
            # Esto solo lo DETECTA y lo escribe; no reinicia nada.
            vivos = [t.name for t in threading.enumerate()]
            for dev in list(CUENTAS):
                corto = str(dev).replace(":", "")
                falta = [n for n in (f"pedir-{corto}", f"calc-{corto}-")
                         if not any(v == n or v.startswith(n) for v in vivos)]
                if not falta:
                    continue
                if ahora - float(ultimo_muerto.get(str(dev)) or 0.0) < 300:
                    continue
                ultimo_muerto[str(dev)] = ahora
                _log_arranque(f"HILO_MUERTO {dev}: falta {', '.join(falta)}; "
                              f"vivos={', '.join(sorted(vivos))}")
                _volcar_pilas()
            for dev, c in list(CUENTAS.items()):
'''),
    # --- 10. instalar el registro al arrancar --------------------------------
    ('''    for flujo in (sys.stdout, sys.stderr):
        try:
            flujo.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    if not _tomar_lock():
''',
     '''    for flujo in (sys.stdout, sys.stderr):
        try:
            flujo.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    # F2-53 (M-12): a partir de aqui el registro lo escribe este proceso.
    _REG.instalar()
    if not _tomar_lock():
'''),
]

ROTAR_VIEJO = '''    for extra in ("minero.log", "minero_visible.log", "minero_relanzado.log"):
'''
ROTAR_NUEVO = '''    # F2-53 (M-12): `minero.log` ya NO se rota desde fuera. Lo escribe el propio
    # worker y lo rota el, con su cerrojo: truncar por fuera un fichero que otro
    # proceso tiene abierto es justo lo que lo llenaba de bytes NUL.
    for extra in ("minero_visible.log", "minero_relanzado.log"):
'''


def aplicar(ruta: Path, cambios: list, revisar: bool) -> int:
    crudo = ruta.read_text(encoding="utf-8")
    txt = crudo.replace("\r\n", "\n")
    for item in cambios:
        if len(item) == 3:
            nombre, viejo, nuevo = item
        else:                       # (viejo, nuevo): el nombre sale del anclaje
            viejo, nuevo = item
            nombre = viejo.strip().splitlines()[0][:60]
        n = txt.count(viejo)
        if n != 1:
            print(f"FALLO en '{nombre}': el anclaje aparece {n} veces (se espera 1)")
            return 1
        if not revisar:
            txt = txt.replace(viejo, nuevo, 1)
        print(f"OK anclaje '{nombre}'")
    if not revisar:
        ruta.write_text(txt.replace("\n", "\r\n"), encoding="utf-8", newline="")
        print(f"escrito: {ruta}")
    return 0


def main() -> int:
    revisar = "--revisar" in sys.argv
    print(f"worker: {WORKER}")
    if aplicar(WORKER, PARCHES, revisar):
        return 1
    print(f"rotar_logs: {ROTAR}")
    if aplicar(ROTAR, [("rotacion externa de minero.log (M-12)",
                        ROTAR_VIEJO, ROTAR_NUEVO)], revisar):
        return 1
    if not revisar:
        import py_compile
        py_compile.compile(str(WORKER), doraise=True)
        py_compile.compile(str(ROTAR), doraise=True)
        print("compila: worker y rotar_logs OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
