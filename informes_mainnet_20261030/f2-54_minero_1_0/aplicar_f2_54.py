"""Aplica F2-54 al worker: cupo acotado de verdad y telemetria por GPU completa.

Cada sustitucion debe aparecer EXACTAMENTE una vez. Con `--revisar` solo
comprueba los anclajes y no escribe nada.

    python aplicar_f2_54.py [--revisar]
"""
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
WORKER = RAIZ / "worker" / "kukia_worker.py"

VIEJO_STATS = '''def _gpu_stats(device):
    """(utilizacion %, VRAM usada MB) reales, o (None, None) si no se puede.

    nvidia-smi es lo mas fiable y viene con el driver; torch.cuda.utilization
    necesita nvidia-ml-py, que no siempre esta instalado. Se intenta primero
    nvidia-smi y se cae a torch sin romper el minero.
    """
'''

NUEVO_STATS = '''def _gpu_stats(device):
    """(utilizacion %, VRAM usada MB, temperatura C, VRAM libre MB) de esa GPU.

    Cada valor es None si el equipo no lo da. nvidia-smi es lo mas fiable y viene
    con el driver; torch.cuda.utilization necesita nvidia-ml-py, que no siempre
    esta instalado. Se intenta primero nvidia-smi y se cae a torch sin romper el
    minero.

    F2-54 (M-06): se anaden temperatura y VRAM LIBRE. Con solo la usada no se
    distingue una tarjeta llena de una tarjeta atascada, y sin temperatura no se
    ve venir un problema termico. Son datos por dispositivo, no de la maquina.
    """
'''

VIEJO_QUERY = '''            ["nvidia-smi", "--query-gpu=utilization.gpu,memory.used",
             "--format=csv,noheader,nounits", "-i", str(idx)],
            capture_output=True, text=True, timeout=4, **extra)
        if salida.returncode == 0 and salida.stdout.strip():
            partes = [x.strip() for x in salida.stdout.strip().splitlines()[0].split(",")]
            if len(partes) >= 2:
                return int(float(partes[0])), int(float(partes[1]))
'''

NUEVO_QUERY = '''            ["nvidia-smi",
             "--query-gpu=utilization.gpu,memory.used,temperature.gpu,memory.total",
             "--format=csv,noheader,nounits", "-i", str(idx)],
            capture_output=True, text=True, timeout=4, **extra)
        if salida.returncode == 0 and salida.stdout.strip():
            partes = [x.strip() for x in salida.stdout.strip().splitlines()[0].split(",")]
            if len(partes) >= 2:
                def _num(txt):
                    try:
                        v = int(float(txt))
                        return v if v >= 0 else None
                    except Exception:
                        return None
                util = _num(partes[0])
                usada = _num(partes[1])
                temp = _num(partes[2]) if len(partes) > 2 else None
                total = _num(partes[3]) if len(partes) > 3 else None
                libre = (total - usada) if (total is not None
                                            and usada is not None) else None
                return util, usada, temp, libre
'''

VIEJO_TORCH = '''        if torch.cuda.is_available():
            util = int(torch.cuda.utilization(idx))
            libre, total = torch.cuda.mem_get_info(idx)
            return util, int((total - libre) / 1048576)
    except Exception:
        pass
    return None, None
'''

NUEVO_TORCH = '''        if torch.cuda.is_available():
            util = int(torch.cuda.utilization(idx))
            libre, total = torch.cuda.mem_get_info(idx)
            return util, int((total - libre) / 1048576), None, int(libre / 1048576)
    except Exception:
        pass
    return None, None, None, None
'''

VIEJO_TELE = '''            util, mem = _gpu_stats(device)
            datos = {"device": str(device), "vram_gb": int(vram_gb),
                     "tops_declarados": float(tops),
                     "gflops_base": _GFLOPS_BASE.get(str(device), 0.0),
                     "gpu_util_pct": int(util) if util is not None else -1,
                     "gpu_mem_mb": int(mem) if mem is not None else -1,
'''

NUEVO_TELE = '''            util, mem, temp, libre = _gpu_stats(device)
            datos = {"device": str(device), "vram_gb": int(vram_gb),
                     "tops_declarados": float(tops),
                     "gflops_base": _GFLOPS_BASE.get(str(device), 0.0),
                     "gpu_util_pct": int(util) if util is not None else -1,
                     "gpu_mem_mb": int(mem) if mem is not None else -1,
                     # F2-54 (M-06): por dispositivo, para ver una tarjeta
                     # atascada o caliente sin mirar la maquina entera.
                     "gpu_temp_c": int(temp) if temp is not None else -1,
                     "vram_libre_mb": int(libre) if libre is not None else -1,
'''

VIEJO_SEM = '''    cupo_vuelo = threading.Semaphore(max(2, min(12, int(os.environ.get("KUKIA_EN_VUELO", "4")))))
'''

NUEVO_SEM = '''    # F2-54 (M-03): BoundedSemaphore y no Semaphore. Con el acotado, liberar de
    # mas lanza ValueError en vez de inflar el contador: el tope de tareas en
    # vuelo no puede desaparecer ni por un camino de error que se olvide.
    cupo_vuelo = threading.BoundedSemaphore(
        max(2, min(12, int(os.environ.get("KUKIA_EN_VUELO", "4")))))
'''

VIEJO_SOLTAR = '''        try:
            cupo_vuelo.release()
        except Exception:
            pass
'''

NUEVO_SOLTAR = '''        try:
            cupo_vuelo.release()
        except ValueError:
            # Con BoundedSemaphore esto es un defecto de contabilidad, no ruido:
            # se anota con nombre y numero para poder cazarlo.
            _log_arranque("cupo: BoundedSemaphore rechaza una liberacion de mas")
        except Exception:
            pass
'''

PARCHES = [
    ("telemetria por GPU con temperatura y VRAM libre (M-06)", VIEJO_STATS, NUEVO_STATS),
    ("consulta a nvidia-smi con temperatura y total (M-06)", VIEJO_QUERY, NUEVO_QUERY),
    ("respaldo por torch con VRAM libre (M-06)", VIEJO_TORCH, NUEVO_TORCH),
    ("telemetria firmada con los campos nuevos (M-06)", VIEJO_TELE, NUEVO_TELE),
    ("cupo acotado de verdad (M-03)", VIEJO_SEM, NUEVO_SEM),
    ("liberacion de mas visible con el cupo acotado (M-03)", VIEJO_SOLTAR, NUEVO_SOLTAR),
]


def main() -> int:
    revisar = "--revisar" in sys.argv
    crudo = WORKER.read_text(encoding="utf-8")
    txt = crudo.replace("\r\n", "\n")
    for nombre, viejo, nuevo in PARCHES:
        n = txt.count(viejo)
        if n != 1:
            print(f"FALLO '{nombre}': el anclaje aparece {n} veces (se espera 1)")
            return 1
        if not revisar:
            txt = txt.replace(viejo, nuevo, 1)
        print(f"OK anclaje '{nombre}'")
    if revisar:
        return 0
    WORKER.write_text(txt.replace("\n", "\r\n"), encoding="utf-8", newline="")
    import py_compile
    py_compile.compile(str(WORKER), doraise=True)
    print(f"escrito y compilado: {WORKER}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
