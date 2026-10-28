#!/usr/bin/env python3
"""Deja el minero minando EN ESTA ventana, aunque ya hubiera uno oculto.

Por que existe: la guardia arranca el minero con pythonw (sin ventana). Abrir
VER_MINERO.cmd lanzaba un segundo worker que el cerrojo rechazaba, y la ventana
se quedaba diciendo "ya hay un minero vivo (PID N): esta ventana NO mina":
parecia que el minero estaba roto.

Ahora esta ventana se queda con el trabajo:

  - Si el minero que hay esta OCULTO (pythonw.exe), lo aparta. No basta con
    matarlo: la guardia lo relanza al instante. Se espera ese relanzamiento y se
    para ESE, dentro de los 20 s de enfriamiento que la propia guardia se pone
    entre arranques; despues ya no hay competencia y el minero arranca aqui.
  - Si el que hay ya esta en una ventana (python.exe), no se toca: dos mineros
    se reparten el cupo de tareas y rinden la mitad, asi que solo se ensena el
    visor (tools/mina_en_vivo.py).

Uso:
    python tools/minero_en_ventana.py                  (nodo minero_<PC>)
    python tools/minero_en_ventana.py minero_pc        (identidad de siempre)
"""
import json
import subprocess
import sys
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
WORKER = RAIZ / "worker"
LOCK = WORKER / "minero.lock"
VISOR = RAIZ / "tools" / "mina_en_vivo.py"
SIN_VENTANA = 0x08000000
CREATE_NEW_CONSOLE = 0x00000010


def mineros():
    """[(pid, nombre_del_exe)] de todo proceso que corre kukia_worker.py."""
    ps = ("Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like "
          "'*kukia_worker.py*' -and ($_.Name -eq 'python.exe' -or $_.Name -eq 'pythonw.exe') } | "
          "Select-Object ProcessId,Name | ConvertTo-Json -Compress")
    r = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                       capture_output=True, text=True, creationflags=SIN_VENTANA)
    try:
        d = json.loads(r.stdout or "[]")
    except Exception:
        return []
    if isinstance(d, dict):
        d = [d]
    return [(int(x["ProcessId"]), (x.get("Name") or "").lower()) for x in d]


def matar(pid):
    subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                   capture_output=True, creationflags=SIN_VENTANA)


def lock_pid():
    try:
        return int((LOCK.read_text(encoding="utf-8") or "0").strip() or 0)
    except Exception:
        return 0


def imagen(pid):
    """Nombre del ejecutable del PID (python.exe / pythonw.exe), '' si no esta."""
    r = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                       capture_output=True, text=True, creationflags=SIN_VENTANA,
                       errors="replace")
    partes = (r.stdout or "").split()
    return partes[0].lower() if partes else ""


def apartar_oculto(nombre: str) -> bool:
    """Quita de en medio el minero oculto (y el que lance la guardia) y mina aqui.

    Devuelve True si esta ventana se queda con el trabajo.
    """
    actuales = mineros()
    for pid, _ in actuales:
        print(f"[ventana] parando el minero oculto (PID {pid})...", flush=True)
        matar(pid)
    time.sleep(0.3)

    # La guardia relanza el suyo en cuanto lo ve muerto. Se espera a que
    # aparezca y se para ESE, que es lo que deja el enfriamiento de 20 s.
    antes = {p for p, _ in mineros()}
    nuevo = None
    for _ in range(50):
        time.sleep(0.4)
        diff = {p for p, _ in mineros()} - antes
        if diff:
            nuevo = diff.pop()
            break
    if nuevo:
        print(f"[ventana] la guardia relanzo el suyo (PID {nuevo}); lo paro "
              f"dentro de sus 20 s de enfriamiento", flush=True)
        matar(nuevo)
        time.sleep(0.2)
    else:
        print("[ventana] no relanzo nadie: mejor, arranco directo", flush=True)

    codigo = minar_aqui(nombre)
    print(f"\n[ventana] minero detenido (codigo {codigo})", flush=True)
    return True


def minar_aqui(nombre: str) -> int:
    """Mina en esta consola y de paso lo copia a logs\\minero_visible.log.

    Sin la copia, el visor (tools/mina_en_vivo.py) no tendria de donde sacar el
    ritmo por nodo cuando el minero corre en una ventana: el worker oculto
    escribe en worker/minero.log y este no.
    """
    LOGS = RAIZ / "logs"
    LOGS.mkdir(parents=True, exist_ok=True)
    destino = LOGS / "minero_visible.log"
    # ROTACION (27-10-2026): este fichero solo se anadia y llego a 68 MB. Se
    # rota antes de abrirlo: la cola queda en `.1` y el vivo empieza vacio.
    # Como se abre en modo "a" (O_APPEND), truncar por debajo es seguro.
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import rotar_logs
        suelto = rotar_logs.rota_ahora(destino)
        if suelto:
            print(f"[ventana] log rotado: se sueltan {rotar_logs.mb(suelto)}", flush=True)
    except Exception as e:
        print(f"[ventana] aviso: no se pudo rotar el log "
              f"({type(e).__name__}: {e})", flush=True)
    print(f"[ventana] minando en esta ventana (nodo {nombre}). Ctrl+C para parar.\n",
          flush=True)
    # Tope del log (el mismo que los demas: tools/rotar_logs.py). Esta ventana
    # es la UNICA que escribe este fichero, asi que puede rotarlo en caliente.
    tope = 5 * 1024 * 1024
    escritos = 0
    with open(destino, "a", encoding="utf-8", errors="replace") as f:
        f.write(f"\n--- ventana abierta {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n")
        f.flush()
        p = subprocess.Popen([sys.executable, "-u", "kukia_worker.py", nombre],
                             cwd=str(WORKER), stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, text=True, bufsize=1,
                             encoding="utf-8", errors="replace")
        try:
            for linea in p.stdout:
                sys.stdout.write(linea)
                sys.stdout.flush()
                f.write(linea)
                f.flush()
                escritos += len(linea)
                if escritos >= 1048576:
                    # Se mira de MB en MB, no en cada linea.
                    escritos = 0
                    try:
                        if f.tell() > tope:
                            f.close()
                            import rotar_logs
                            suelto = rotar_logs.rota_ahora(destino, forzar=True)
                            f = open(destino, "a", encoding="utf-8", errors="replace")
                            print(f"\n[ventana] log rotado: se sueltan "
                                  f"{rotar_logs.mb(suelto)}; sigue en {destino.name}",
                                  flush=True)
                    except Exception as e:
                        print(f"\n[ventana] aviso: no se pudo rotar el log "
                              f"({type(e).__name__}: {e})", flush=True)
        except KeyboardInterrupt:
            pass   # el Ctrl+C tambien le llega al worker, que para ordenado
        codigo = p.wait()
    return codigo


def main() -> int:
    for flujo in (sys.stdout, sys.stderr):
        try:
            flujo.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    nombre = sys.argv[1] if len(sys.argv) > 1 else "minero_" + (__import__("socket").gethostname())
    lp = lock_pid()
    vivo = (lp, imagen(lp)) if lp else (0, "")
    ocultos = [p for p, exe in mineros() if exe == "pythonw.exe"]
    visibles = [p for p, exe in mineros() if exe == "python.exe"]

    if visibles or vivo[1] == "python.exe":
        # Ya hay uno minando a la vista: no se duplica, se mira.
        quien = visibles[0] if visibles else vivo[0]
        print(f"[ventana] el minero ya esta minando en otra ventana (PID {quien}).",
              flush=True)
        print("[ventana] no se lanza otro: se repartiria el cupo y rendiria la mitad.",
              flush=True)
        print("[ventana] te enseno como va. Ctrl+C para cerrar solo esto.\n", flush=True)
        return subprocess.call([sys.executable, "-u", str(VISOR)], cwd=str(RAIZ))

    if ocultos or vivo[0]:
        return 0 if apartar_oculto(nombre) else 1

    # No hay nadie: se mina aqui directamente.
    print("[ventana] no hay otro minero vivo.", flush=True)
    codigo = minar_aqui(nombre)
    print(f"\n[ventana] minero detenido (codigo {codigo})", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\n[ventana] interrumpido a mano")
