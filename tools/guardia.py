#!/usr/bin/env python3
"""Guardia: mantiene la red KUKIA viva sin que tengas que mirarla.

POR QUE EXISTE (fallo real del 20-10-2026): el minero se paro a las 12:28 y
nadie lo noto hasta las 15:00. Dos horas y media con 121 tareas en cola, la
red oficialmente "en vivo" y cero KIA acunados. Un minero que se cae y no
vuelve no es un problema de rendimiento: es la red parada.

QUE HACE, cada 30 s (y al instante si un hijo suyo se muere):
  1. Core: si el proceso que arranco el guardia ha terminado, se relanza YA
     (un proceso muerto es un hecho, no un fallo de red). Si sigue vivo pero
     /stats no contesta 3 veces seguidas, se relanza igual; y si lleva ~1 min
     sin contestar con el proceso en pie, se mata y se relanza (colgado).
  2. Minero: mira si el Core paga tareas nuevas. Si no hay minero y hay cola,
     lo arranca; si en 10 min no se ha pagado nada, es que esta atascado y se
     relanza (solo mata el proceso que arranco el propio guardia; nunca el que
     abriste tu a mano).
  3. Validador: si no esta, lo arranca (si existe el binario/identidad).
  4. Publicador de metricas: idem. Sin el, la web se queda con datos viejos.
  5. Potencia medida: si el equipo TIENE GPU NVIDIA y el nodo lleva un rato
     cobrando tareas pero no reporta ni un GFLOPS medido, AVISA (escritorio y
     Telegram). Es el sintoma de un minero que arranco en CPU con la GPU
     encendida: hasta ahora se podia quedar asi dias sin que nadie lo viera.

NO toca el ledger, no firma nada y no toma decisiones de dinero: solo
levanta procesos. Si se cae el propio guardia, no pasa nada: al volver a
arrancarlo revisa todo otra vez.

Uso:
    python tools/guardia.py              # bucle, para siempre
    python tools/guardia.py --una        # un solo ciclo (para probar)
    python tools/guardia.py --verbose    # explica cada comprobacion
    python tools/guardia.py --sin-minero # vigila Core/validador/publicador

Salida: 0 si todo quedo sano al terminar un ciclo, 1 si algo sigue caido.
"""
import argparse
import json
import os
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
LOGS = RAIZ / "logs"
LOG = LOGS / "guardia.log"


def _leer_core() -> str:
    """URL del Core: core_minero.txt (primera linea util) o 127.0.0.1:8300."""
    f = RAIZ / "core_minero.txt"
    try:
        for linea in f.read_text(encoding="utf-8", errors="replace").splitlines():
            linea = linea.strip()
            if linea and not linea.startswith("#"):
                return linea
    except Exception:
        pass
    return "http://127.0.0.1:8300"


# Lo que dice core_minero.txt tal cual: puede ser una URL o una indireccion
# ("auto https://kukia-network.surge.sh/core_actual.json"). Se guarda crudo
# para pasarselo a los hijos, que lo resuelven ellos y asi siguen funcionando
# si el tunel cambia de nombre mientras el guardia lleva horas encendido.
CORE_CRUDO = (os.environ.get("KUKIA_CORE", "") or "").strip() or _leer_core()

sys.path.insert(0, str(RAIZ / "tools"))
try:
    import core_destino
    # core_destino.py se puede importar desde cualquier sitio y no depende de
    # nada mas: si estuviera roto, el guardia tiene que seguir vivo.
except Exception as _e:  # pragma: no cover
    core_destino = None
    print(f"[guardia] AVISO: sin core_destino ({_e}); se usa la linea tal cual", flush=True)


_LOCAL = "http://127.0.0.1:8300"


def _core_actual() -> str:
    """Direccion del Core ahora mismo (resuelve la indireccion, con cache)."""
    if core_destino is None:
        return CORE_CRUDO
    try:
        return core_destino.resolver(CORE_CRUDO)
    except Exception:
        return CORE_CRUDO


CORE = _core_actual()

# Segundos sin ningun pago nuevo para considerar que el minero no trabaja.
ATASCO_SEG = float(os.environ.get("KUKIA_GUARDIA_ATASCO", "600"))
# Cada cuanto se intenta anclar el ultimo sello en Bitcoin (OpenTimestamps).
ANCLA_SEG = float(os.environ.get("KUKIA_GUARDIA_ANCLA", "600"))
# Fallos seguidos del Core antes de relanzarlo.
FALLOS_CORE = 3
# Sondeos seguidos sin respuesta CON el proceso vivo antes de darlo por colgado
# y matarlo. Son ~1 min: un recalculo largo puede tardar unos segundos en
# contestar a /stats, pero un minuto entero es que ya no sirve a nadie.
FALLOS_CORE_DURO = 12
# Mientras algo esta mal se sondea cada pocos segundos en vez de cada 30: asi
# los 3 fallos que hacen falta se juntan en ~15 s y no en 90.
INTERVALO_MALO = float(os.environ.get("KUKIA_GUARDIA_RAPIDO", "5"))
# Si hay mas de estas tareas en cola y no hay minero nuestro vivo, se arranca.
ATASCO_COLA_MIN = 5
# Arranque del minero con retraso (22-10-2026): al iniciar sesion se abre la
# ventana del minero (VER_MINERO.cmd) y esta guardia arranca a la vez. Si la
# guardia gana la carrera, lanza el minero oculto y la ventana se cierra sola
# al ver la cerradura cogida. Este margen le deja el sitio a la ventana; si a
# los 60 s no hay nadie minando, la guardia arranca el suyo como siempre.
ESPERA_MINERO_SEG = float(os.environ.get("KUKIA_GUARDIA_ESPERA_MINERO", "60"))
_INICIO = time.time()
INTERVALO = float(os.environ.get("KUKIA_GUARDIA_INTERVALO", "30"))
MAX_LOG_BYTES = 5 * 1024 * 1024


def log(msg: str) -> None:
    linea = time.strftime("%Y-%m-%d %H:%M:%S") + "  " + msg
    print(linea, flush=True)
    try:
        LOGS.mkdir(parents=True, exist_ok=True)
        if LOG.exists() and LOG.stat().st_size > MAX_LOG_BYTES:
            LOG.replace(LOG.with_suffix(".log.1"))
        with LOG.open("a", encoding="utf-8") as f:
            f.write(linea + "\n")
    except Exception:
        pass


def _peticion(ruta: str, timeout: float = 6.0):
    with urllib.request.urlopen(_core_actual() + ruta, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def _py() -> str:
    return sys.executable or "python"


def _puerto_abierto(timeout: float = 2.0) -> bool:
    """True si hay algo escuchando en el host/puerto del Core.

    Sirve para no lanzar un uvicorn duplicado cuando el Core ya existe pero
    esta devolviendo errores: el segundo moriria al no poder enlazar el
    puerto y solo llenaria el log.
    """
    try:
        from urllib.parse import urlsplit
        import socket
        # SIEMPRE el Core local: aqui se decide si lanzar el uvicorn de este PC.
        # Si se mirara la URL publica, el tunel (que casi siempre esta arriba)
        # haria creer que el Core esta en pie cuando no lo esta.
        u = urlsplit(_LOCAL)
        host = u.hostname or "127.0.0.1"
        puerto = u.port or 8300
        with socket.create_connection((host, puerto), timeout=timeout):
            return True
    except Exception:
        return False


# FALLO REAL (21-10-2026): cada consulta de procesos lanzaba un powershell.exe
# SIN esta bandera y Windows le da a cada hijo su PROPIA consola... aunque el
# padre sea pythonw. Con una consulta cada 20-30 s por hijo, al usuario le
# aparecia una ventana negra varias veces por minuto, todo el dia. La guardia
# tiene que ser invisible: esta bandera es lo que la hace invisible de verdad.
SIN_VENTANA = 0x08000000  # CREATE_NO_WINDOW

_YA_CORRIENDO = {}


def _contar_procesos(patron: str, excluir_pid: int = 0) -> int:
    """Cuantos procesos (aparte del nuestro) llevan `patron` en su linea.

    Solo se miran python.exe y pythonw.exe: si se mira todo el sistema, el
    propio PowerShell de la consulta aparece porque el patron va escrito en su
    linea de comandos (fue un fallo real: el guardia se creia que el minero
    seguia vivo).

    pythonw.exe es imprescindible: la guardia silenciosa (el .vbs) arranca a
    sus hijos CON pythonw.exe, asi que mirando solo python.exe no los veia y
    los duplicaba todos al reiniciarse. Paso de verdad el 21-10-2026: tras
    reiniciar la guardia quedaron dos mineros y dos validadores, y los dos
    mineros se repartian el cupo de 4 tareas (la mitad de rendimiento).
    """
    try:
        if os.name == "nt":
            ps = ("Get-CimInstance Win32_Process -Filter "
                  "\"Name='python.exe' or Name='pythonw.exe'\" | "
                  f"Where-Object {{ $_.CommandLine -like '*{patron}*' -and "
                  f"$_.ProcessId -ne {int(excluir_pid)} }} | "
                  "Select-Object -ExpandProperty ProcessId")
            r = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                               capture_output=True, text=True, timeout=20,
                               creationflags=SIN_VENTANA)
            return len([l for l in r.stdout.splitlines() if l.strip()])
        r = subprocess.run(["ps", "-eo", "pid=,args="], capture_output=True,
                           text=True, timeout=10)
        n = 0
        for linea in r.stdout.splitlines():
            partes = linea.strip().split(None, 1)
            if len(partes) == 2 and patron in partes[1] and partes[0] != str(excluir_pid):
                n += 1
        return n
    except Exception:
        return 0


def _ya_corriendo(patron, ttl: float = 20.0) -> bool:
    """True si YA hay un proceso abierto aparte que hace ese trabajo.

    Sin esto el guardia duplicaba todo: arrancaba un segundo publicador y un
    segundo validador sobre los que ya estaban abiertos a mano.

    FALLO REAL (20-10-2026): la consulta de Windows se buscaba a si misma. La
    linea de comandos del PowerShell que filtraba contenia el texto
    'kukia_worker.py', asi que salia siempre "ya esta corriendo" y el guardia
    no relanzo el minero caido en 90 s. Ahora solo se miran procesos
    python.exe y se excluye el propio PID.

    El resultado se cachea 20 s: en Windows cada consulta cuesta ~0,5 s y en
    un bucle de 30 s no hace falta preguntar mas.
    """
    # El tunel puede ser de dos clases (rapido o con nombre): se aceptan varias
    # marcas y basta con que exista una.
    patrones = (patron,) if isinstance(patron, str) else tuple(patron)
    clave = "|".join(patrones)
    ahora = time.time()
    cache = _YA_CORRIENDO.get(clave)
    if cache and ahora - cache[0] < ttl:
        return cache[1]
    encontrado = False
    try:
        if os.name == "nt":
            # python.exe Y pythonw.exe: los hijos de la guardia silenciosa son
            # pythonw.exe (sin consola), asi que mirando solo python.exe no se
            # veian y se duplicaban todos en cada reinicio del guardia.
            mira = " -or ".join(f"$_.CommandLine -like '*{p}*'" for p in patrones)
            ps = ("Get-CimInstance Win32_Process -Filter "
                  "\"Name='python.exe' or Name='pythonw.exe'\" | "
                  f"Where-Object {{ ({mira}) -and $_.ProcessId -ne $PID }} | "
                  "Select-Object -ExpandProperty ProcessId")
            r = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                               capture_output=True, text=True, timeout=20,
                               creationflags=SIN_VENTANA)
            encontrado = bool(r.stdout.strip())
        else:
            r = subprocess.run(["ps", "-eo", "pid=,args="], capture_output=True,
                               text=True, timeout=10)
            encontrado = any(any(p in l for p in patrones)
                             and str(os.getpid()) not in l.split()[:1]
                             for l in r.stdout.splitlines())
    except Exception:
        encontrado = False
    _YA_CORRIENDO[clave] = (ahora, encontrado)
    return encontrado


# --- GPU en el equipo sin potencia medida (26-10-2026) ---------------------
# Lo que vigila esto: la maquina TIENE GPU NVIDIA pero el nodo no reporta ni un
# GFLOPS medido. Dos causas conocidas: el minero arranco antes que el driver y
# se quedo en CPU (el minero ya lo reintenta por su cuenta, ver _detectar_gpus
# en worker/kukia_worker.py), o hay una GPU que no se esta usando. En los dos
# casos el sintoma es el mismo y hasta hoy no se veia en ningun sitio.
GPU_SIN_POTENCIA = (os.environ.get("KUKIA_GUARDIA_POTENCIA", "1") or "1") != "0"
# Entre aviso y aviso: no es un fallo nuevo, es el mismo que sigue.
POTENCIA_AVISO_SEG = float(os.environ.get("KUKIA_GUARDIA_POTENCIA_SEG", "1800"))
# Margen antes de darlo por hecho, para no avisar por un arranque que aun no ha
# terminado de calentar.
POTENCIA_ESPERA_SEG = float(os.environ.get("KUKIA_GUARDIA_POTENCIA_ESPERA", "600"))

_IDENTIDAD_MINERO = RAIZ / "worker" / "identidad_minero.json"
_GPUS_EQUIPO = {"ts": 0.0, "nombres": []}
_ULTIMO_AVISO_POTENCIA = 0.0

TOAST = r"""
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
$p = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
$t = $p.GetElementsByTagName("text")
$t.Item(0).AppendChild($p.CreateTextNode($env:KUKIA_TITULO)) | Out-Null
$t.Item(1).AppendChild($p.CreateTextNode($env:KUKIA_TEXTO)) | Out-Null
$a = "{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe"
$n = [Windows.UI.Notifications.ToastNotification]::new($p)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($a).Show($n)
"""


def _gpus_del_equipo(ttl: float = 300.0) -> list:
    """Nombres de las GPU NVIDIA que ve el DRIVER (nvidia-smi), con cache.

    Se le pregunta al driver y no a torch a proposito: el driver sabe si la
    tarjeta esta fisicamente ahi aunque CUDA (o torch) no la vean, que es justo
    el caso que hay que detectar. Si nvidia-smi no existe o no responde, la
    lista va vacia y no se avisa de nada: mejor callar que inventar.
    """
    ahora = time.time()
    if _GPUS_EQUIPO["nombres"] and ahora - _GPUS_EQUIPO["ts"] < ttl:
        return _GPUS_EQUIPO["nombres"]
    nombres = []
    try:
        r = subprocess.run(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
                           capture_output=True, text=True, timeout=8,
                           creationflags=SIN_VENTANA)
        if r.returncode == 0:
            nombres = [l.strip() for l in (r.stdout or "").splitlines() if l.strip()]
    except Exception:
        nombres = []
    _GPUS_EQUIPO.update(ts=ahora, nombres=nombres)
    return nombres


def _ids_mineros_propios() -> set:
    """Ids de los nodos de ESTE equipo (los escribe el minero al registrarse)."""
    try:
        d = json.loads(_IDENTIDAD_MINERO.read_text(encoding="utf-8"))
        return {str(v.get("id")) for v in d.values()
                if isinstance(v, dict) and v.get("id")}
    except Exception:
        return set()


def _potencia_reportada() -> dict:
    """Lo que el nodo dice medido: por tarea y en el benchmark de arranque.

    `gflops_medido` lo calcula el Core con el tiempo REAL de cada tarea (no es
    una cifra declarada por el minero). `gflops_base` es la medida que el
    minero hace con sus propias cuentas al arrancar y manda en la telemetria.
    Cualquiera de las dos > 0 significa que el nodo SI reporta potencia
    medida. Nunca lanza: sin Core devuelve cero y se reintenta en la vuelta
    siguiente.
    """
    ids = _ids_mineros_propios()
    salida = {"por_tarea": 0.0, "benchmark": 0.0, "nodos": 0}
    if not ids:
        return salida
    # Uno por uno (/nodos/<id>): son los 2-3 nodos de este equipo y la
    # respuesta es una fila. Pedir la lista entera (hasta 1000 nodos) cada 30 s
    # solo para mirar los mios seria castigar al Core por gusto.
    for nid in sorted(ids):
        try:
            n = _peticion(f"/nodos/{urllib.parse.quote(str(nid))}") or {}
        except Exception:
            continue
        salida["nodos"] += 1
        salida["por_tarea"] = max(salida["por_tarea"],
                                  float(n.get("gflops_medido") or 0.0))
    try:
        for t in (_peticion("/red/telemetria") or {}).get("telemetria") or []:
            if str(t.get("nodo_id")) not in ids:
                continue
            try:
                m = json.loads(t.get("metricas") or "{}")
            except Exception:
                continue
            salida["benchmark"] = max(salida["benchmark"],
                                      float(m.get("gflops_base") or 0.0))
    except Exception:
        pass
    return salida


def _avisar(titulo: str, texto: str) -> bool:
    """Aviso al escritorio (Windows) y al movil (Telegram). Nunca lanza.

    Mismo camino que `tools/vigilante_servicios.py`: los dos canales son
    independientes a proposito, asi que aunque el PC este sin sesion (el toast
    no se ve) el movil sigue sonando. Sin Telegram configurado
    (`python tools/avisos.py --configurar`) queda el aviso de Windows y el log.
    """
    hecho = False
    if os.name == "nt":
        entorno = dict(os.environ, KUKIA_TITULO=titulo, KUKIA_TEXTO=texto)
        try:
            r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive",
                                "-Command", TOAST],
                               capture_output=True, text=True, timeout=30,
                               env=entorno, creationflags=SIN_VENTANA)
            hecho = r.returncode == 0
        except Exception:
            hecho = False
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import avisos as _avisos
        hecho = _avisos.enviar(titulo, texto) or hecho
    except Exception:
        pass
    return hecho


def _aviso_potencia(stats: dict, estado, verbose: bool = False,
                    gpus=None, medido=None):
    """Avisa si el equipo tiene GPU y el nodo no reporta potencia medida.

    Devuelve el texto del aviso, o None si no habia nada que avisar. `gpus` y
    `medido` se pueden inyectar para probarlo sin depender de la maquina: la
    prueba esta en tools/prueba_aviso_potencia_gpu.py.
    """
    global _ULTIMO_AVISO_POTENCIA
    if not GPU_SIN_POTENCIA:
        return None
    nombres = list(gpus) if gpus is not None else _gpus_del_equipo()
    if not nombres:
        return None                      # no hay GPU: no hay nada que reclamar
    # Solo se avisa si el nodo esta COBRANDO tareas. Si no cobra, ese es el
    # problema y lo llevan los apartados de arriba (relanzar el minero).
    pagando = (estado.ultimo_ok_ts > 0
               and (time.time() - estado.ultimo_ok_ts) < POTENCIA_ESPERA_SEG)
    if not pagando:
        if verbose:
            log("[gpu] hay GPU pero el nodo no lleva tareas pagadas: no se avisa")
        return None
    p = dict(medido) if medido is not None else _potencia_reportada()
    if float(p.get("por_tarea") or 0.0) > 0 or float(p.get("benchmark") or 0.0) > 0:
        _ULTIMO_AVISO_POTENCIA = 0.0     # ha vuelto a medir: el proximo fallo avisa
        if verbose:
            log(f"[gpu] potencia medida OK ({p.get('por_tarea')} GFLOPS/tarea, "
                f"{p.get('benchmark')} GFLOPS de benchmark)")
        return None
    ahora = time.time()
    if ahora - _ULTIMO_AVISO_POTENCIA < POTENCIA_AVISO_SEG:
        return None                      # ya se aviso hace poco del mismo fallo
    _ULTIMO_AVISO_POTENCIA = ahora
    texto = (f"El equipo tiene {len(nombres)} GPU NVIDIA ({', '.join(nombres)}) pero el "
             f"nodo no reporta potencia medida: 0 GFLOPS por tarea y 0 de benchmark. "
             f"Se esta minando en CPU, o las tareas no llegan a la GPU. Comprueba "
             f"'nvidia-smi' y reinicia el minero. Si es a proposito, "
             f"KUKIA_GUARDIA_POTENCIA=0 lo desactiva.")
    log(f"[gpu] AVISO: {texto}")
    try:
        _avisar("KUKIA: GPU sin potencia medida", texto)
    except Exception:
        pass
    return texto


class Hijo:
    """Proceso que arranca el guardia. Solo se mata lo que arrancamos nosotros."""

    def __init__(self, nombre: str, argv: list, cwd: Path, logfile: Path):
        self.nombre = nombre
        self.argv = argv
        self.cwd = cwd
        self.logfile = logfile
        self.proc = None
        self.arranques = 0
        self.ultimo_arranque = 0.0
        self.patron = nombre
        # Para distinguir "se ha muerto" de "lo hemos parado nosotros".
        self.parando = False

    def vivo(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def caido(self) -> bool:
        """True si lo arrancamos nosotros y ya no existe.

        Es un HECHO, no un sondeo: `poll()` mira el proceso, no la red. Sirve
        para relanzarlo en el acto sin esperar los 3 fallos de /stats que
        existen para el caso "vivo pero atascado".
        """
        return self.proc is not None and not self.parando and self.proc.poll() is not None

    def _vigilar(self) -> None:
        """Hilo que espera la muerte del proceso y despierta al guardia."""
        p = self.proc
        if p is None:
            return

        def ojo():
            try:
                p.wait()
            except Exception:
                return
            # Si lo paramos nosotros (o ya hay otro proceso), no es una caida.
            if self.parando or self.proc is not p:
                return
            codigo = p.returncode
            log(f"[{self.nombre}] el proceso ha terminado (codigo {codigo}): "
                f"se relanza ahora")
            DESPERTAR.set()

        threading.Thread(target=ojo, daemon=True,
                         name=f"vigia-{self.nombre}").start()

    def arrancar(self) -> bool:
        """Arranca el proceso. True si esta vuelta ha lanzado algo de verdad."""
        # Nunca mas de un arranque cada 20 s: si algo esta roto de verdad
        # (falta una dependencia), no queremos 180 intentos por hora.
        if time.time() - self.ultimo_arranque < 20:
            return False
        # Se tira la cache de "ya esta corriendo": puede tener 20 s y decir que
        # si por un proceso que acaba de morir. Sin esto, el relanzamiento
        # inmediato se quedaba esperando a que caducara.
        patrones = ((PATRONES[self.nombre],) if isinstance(PATRONES[self.nombre], str)
                    else tuple(PATRONES[self.nombre]))
        _YA_CORRIENDO.pop("|".join(patrones), None)
        # Si el usuario ya tiene uno abierto, no se arranca otro.
        if not self.vivo() and _ya_corriendo(PATRONES[self.nombre]):
            log(f"[{self.nombre}] ya esta corriendo (abierto aparte): no se duplica")
            self.ultimo_arranque = time.time()
            return False
        self.detener()
        try:
            LOGS.mkdir(parents=True, exist_ok=True)
            salida = self.logfile.open("ab")
            extra = {}
            if os.name == "nt":
                extra["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
            self.proc = subprocess.Popen(
                self.argv, cwd=str(self.cwd), stdout=salida, stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL, **extra)
            self.arranques += 1
            self.ultimo_arranque = time.time()
            log(f"[{self.nombre}] arrancado pid={self.proc.pid}")
            self._vigilar()
            return True
        except Exception as e:
            self.ultimo_arranque = time.time()
            log(f"[{self.nombre}] ERROR al arrancar: {type(e).__name__}: {e}")
            return False

    def detener(self) -> None:
        # Marca de parada a proposito: el vigia no debe tomarlo por una caida.
        self.parando = True
        if self.proc is not None and self.proc.poll() is None:
            try:
                self.proc.terminate()
                self.proc.wait(timeout=10)
            except Exception:
                try:
                    self.proc.kill()
                except Exception:
                    pass
        self.proc = None
        self.parando = False


PATRONES = {
    "core": "kukia_core",
    "validador": "kukia_validator.py",
    "publicador": "publicar_metricas.py",
    "minero": "kukia_worker.py",
    "panel": "kukia_dash",
    # Cualquiera de los dos túneles cuenta como "el túnel": si no, al pasar a
    # dominio propio el guardia vería el rápido abierto aparte y no arrancaría
    # el suyo.
    "tunel": ("tunel_estable.py", "tunel_dominio.py", "tunel_ngrok.py"),
}


def _tunel_ngrok() -> bool:
    """True si hay un dominio de ngrok apuntado en ngrok_dominio.txt.

    24/10/2026: el tunel rapido de Cloudflare cambia de nombre en cada arranque,
    y un tunel CON NOMBRE de Cloudflare exige dominio propio (de pago). Ngrok da
    un dominio fijo en su plan gratis, asi que si esta configurado manda el:
    URL que no cambia sin gastar un euro. Si conviven las dos cosas, gana ngrok
    porque el fichero de dominio es una decision explicita del usuario.
    """
    registro = RAIZ / "ngrok_dominio.txt"
    if not registro.exists():
        return False
    try:
        return any(l.strip() and not l.startswith("#")
                   for l in registro.read_text(encoding="utf-8").splitlines())
    except Exception:
        return False


def _tunel_con_dominio() -> bool:
    """True si hay un tunel CON NOMBRE ya preparado (config + dominio escrito).

    Se exigen los dos ficheros a proposito: `--preparar` los escribe juntos al
    final, asi que si solo hay uno es que la preparacion quedo a medias y lo
    que toca es seguir con el tunel rapido, que funciona.
    """
    conf = RAIZ / "tools" / "cloudflared" / "config.yml"
    registro = RAIZ / "dominio_core.txt"
    if not conf.exists() or not registro.exists():
        return False
    try:
        return any(l.strip() and not l.startswith("#")
                   for l in registro.read_text(encoding="utf-8").splitlines())
    except Exception:
        return False


def construir_hijos(sin_minero: bool) -> dict:
    hijos = {}
    hijos["core"] = Hijo(
        # AUDITORIA 2026-10-28 (A2): uvicorn acepta X-Forwarded-For de
        # 127.0.0.1 y reescribe request.client con el; detras del tunel eso
        # permitia falsificar la IP en cada peticion y saltarse los cupos. Con
        # --no-proxy-headers el socket manda y la unica cabecera que se mira es
        # CF-Connecting-IP, que la pone el borde de Cloudflare.
        "core", [_py(), "-m", "uvicorn", "kukia_core:app", "--host", "127.0.0.1", "--port", "8300",
                 "--no-proxy-headers"],
        RAIZ / "coordinator", LOGS / "core.log")
    if (RAIZ / "validator" / "kukia_validator.py").exists():
        ident = RAIZ / "validator" / "identidad_validador.json"
        hijos["validador"] = Hijo(
            "validador",
            [_py(), "-u", str(RAIZ / "validator" / "kukia_validator.py"),
             "--core", CORE_CRUDO,
             "--nombre", "VALIDADOR_KUKI", "--identidad", str(ident), "--intervalo", "60"],
            RAIZ, LOGS / "validador.log")
    if (RAIZ / "dashboard" / "kukia_dash.py").exists():
        hijos["panel"] = Hijo(
            "panel", [_py(), "-m", "uvicorn", "kukia_dash:app", "--host", "127.0.0.1",
                      "--port", "8301"],
            RAIZ / "dashboard", LOGS / "panel.log")
    if (RAIZ / "publico" / "publicar_metricas.py").exists():
        hijos["publicador"] = Hijo(
            "publicador",
            [_py(), "-u", str(RAIZ / "publico" / "publicar_metricas.py")],
            RAIZ, LOGS / "publicador.log")
    if (RAIZ / "tools" / "tunel_estable.py").exists():
        # El tunel publico se vigila solo (relanza el de Cloudflare si se cae) y
        # republica la direccion viva en el espejo. Tiene que ser hijo del
        # guardia: si no, al reiniciar el PC la direccion publicada seguiria
        # apuntando a un tunel muerto y los mineros de fuera no conectarian.
        # Log aparte: el tunel ya escribe logs/tunel_*.log por su cuenta y no
        # conviene que dos procesos escriban el mismo fichero.
        # Con dominio propio se usa el otro supervisor: es el mismo trabajo con
        # una URL que no caduca. Los dos escriben el MISMO docs/core_actual.json,
        # asi que el paquete descargado no nota el cambio.
        if _tunel_ngrok():
            script = "tunel_ngrok.py"
        elif _tunel_con_dominio():
            script = "tunel_dominio.py"
        else:
            script = "tunel_estable.py"
        hijos["tunel"] = Hijo(
            "tunel", [_py(), "-u", str(RAIZ / "tools" / script)],
            RAIZ, LOGS / "tunel_stdout.log")
    if not sin_minero and (RAIZ / "worker" / "kukia_worker.py").exists():
        # El log del minero va a worker/minero.log y NO a logs/: es el fichero
        # que lee el publicador de metricas para calcular los GFLOPS por GPU.
        # Con otra ruta, la web se quedaba sin potencia medida (paso el
        # 20-10-2026: el publicador leia un log que ya nadie escribia).
        hijos["minero"] = Hijo(
            "minero", [_py(), "-u", "kukia_worker.py", "minero_pc"],
            RAIZ / "worker", RAIZ / "worker" / "minero.log")
    return hijos


def _hilo_anclaje() -> None:
    """Ancla sellos en Bitcoin y completa las pruebas pendientes, en segundo plano.

    Abrir un anclaje cada 10 min es barato (unos cientos de bytes a tres
    calendarios). Si ya esta anclado, el script lo dice y no hace nada: solo se
    escribe en el log cuando hay algo nuevo de verdad.
    """
    time.sleep(25)
    script = RAIZ / "tools" / "anclar_ots.py"
    if not script.exists():
        return
    while True:
        try:
            for modo in (None, "--actualizar"):
                argv = [_py(), str(script)] + ([modo] if modo else [])
                r = subprocess.run(argv, cwd=str(RAIZ), capture_output=True,
                                   text=True, timeout=240)
                lineas = [l for l in (r.stdout or "").splitlines() if l.strip()]
                interesantes = [l for l in lineas
                                if "enviado a" in l or "CONFIRMADO" in l
                                or "OK en bloque" in l or "FALLO" in l
                                or "ERROR" in l]
                for l in interesantes:
                    log(f"[ancla] {l.lstrip('[ancla] ').strip()}")
        except Exception as e:
            log(f"[ancla] error: {type(e).__name__}: {e}")
        time.sleep(ANCLA_SEG)

# Se enciende cuando un hijo se muere: el bucle deja de dormir y vuelve a
# revisar en el acto. Sin esto habia que esperar hasta 30 s solo para enterarse.
DESPERTAR = threading.Event()


_AVISO_PUERTO = 0.0


def vigia_core() -> None:
    """Avisa en cuanto el Core deja de escuchar, aunque no sea hijo nuestro.

    Un socket cerrado es la prueba mas barata que existe de que no hay Core: se
    puede mirar cada 2 s sin coste y no depende de que el bucle este durmiendo
    30 s. Si el puerto escucha pero /stats falla, ESE es otro caso (Core
    colgado) y lo resuelve `ciclo` con sus 3 sondeos.

    Mientras siga sin escuchar se vuelve a avisar cada 10 s (no cada 2) para no
    llenar el log: el relanzamiento real lo frena el tope de 20 s de `arrancar`.

    Se mira SIEMPRE el puerto local, aunque `CORE` sea la URL publica del tunel:
    el Core que este guardia levanta vive en 127.0.0.1:8300 (es lo que hace
    `construir_hijos`), asi que preguntarle a la URL del tunel seria preguntar
    por otra cosa. Con la comprobacion anterior este vigilante no arrancaba
    nunca en produccion y se perdia el aviso rapido.
    """
    global _AVISO_PUERTO
    while True:
        time.sleep(2.0)
        try:
            if _puerto_abierto(timeout=0.5):
                _AVISO_PUERTO = 0.0
                continue
            if time.time() - _AVISO_PUERTO > 10.0:
                _AVISO_PUERTO = time.time()
                DESPERTAR.set()
        except Exception:
            pass


def esperar(segundos: float) -> None:
    """Duerme `segundos`, pero despierta en cuanto muere un hijo.

    Se duerme en trozos de 1 s (barato) y con el evento delante, asi que una
    muerte se nota en milisegundos y no al final del intervalo.
    """
    limite = time.time() + max(0.0, segundos)
    while True:
        resta = limite - time.time()
        if resta <= 0:
            return
        if DESPERTAR.wait(timeout=min(1.0, resta)):
            DESPERTAR.clear()
            return


class Estado:
    def __init__(self):
        self.fallos_core = 0
        self.ultimo_ok_tareas = None
        self.ultimo_ok_ts = 0.0
        self.tareas_previas = None

    def core_sano(self) -> dict:
        try:
            d = _peticion("/stats")
            self.fallos_core = 0
            return d or {}
        except Exception as e:
            self.fallos_core += 1
            log(f"[core] no responde ({type(e).__name__}) fallo {self.fallos_core}/{FALLOS_CORE}")
            return {}

    def progreso(self, stats: dict, verbose: bool) -> bool:
        """True si el Core ha pagado tareas nuevas desde la ultima vuelta."""
        try:
            ok = int(stats.get("tareas_ok") or 0)
        except Exception:
            return True
        ahora = time.time()
        if self.tareas_previas is None or ok > self.tareas_previas:
            if self.tareas_previas is not None and ok > self.tareas_previas:
                self.ultimo_ok_ts = ahora
            self.tareas_previas = ok
            if self.ultimo_ok_ts == 0.0:
                self.ultimo_ok_ts = ahora
            return True
        en_cola = int(stats.get("tareas_pend") or 0)
        if en_cola <= 0:
            return True
        sin_pagar = ahora - self.ultimo_ok_ts
        if verbose:
            log(f"[minero] sin pagos desde hace {sin_pagar:.0f} s (cola {en_cola})")
        return sin_pagar < ATASCO_SEG


def ciclo(hijos: dict, estado: Estado, verbose: bool) -> int:
    problemas = 0

    # 0. MUERTE CONOCIDA (el camino rapido). Si un hijo que arranco el guardia
    #    ya no existe, se sabe por el propio proceso: se relanza en el acto.
    #    Antes esto esperaba a que /stats fallara 3 veces (hasta 90 s con un
    #    Core caido), y la red quedaba parada por un problema de relojeria.
    for nombre, h in list(hijos.items()):
        if h.caido():
            log(f"[{nombre}] estaba caido; relanzando ya")
            if h.arrancar():
                problemas += 1
                if nombre == "core":
                    estado.fallos_core = 0   # el proceso nuevo merece su intento

    # 1. Core: ahora el sondeo solo cubre "vivo pero atascado".
    stats = estado.core_sano()
    if not stats:
        core = hijos["core"]
        if not _puerto_abierto():
            # Nadie escucha en el puerto: el Core NO esta, sin ambiguedad y sin
            # importar quien lo arrancara (el guardia, tu a mano, otra sesion).
            # No hay que esperar 3 sondeos: la prueba es el socket, no la red.
            log("[core] no hay nadie escuchando en 127.0.0.1:8300; relanzando ya")
            if core.arrancar():
                problemas += 1
                estado.fallos_core = 0
        elif estado.fallos_core >= FALLOS_CORE:
            if core.vivo() and estado.fallos_core >= FALLOS_CORE_DURO:
                # Sigue en pie pero no contesta a nadie: no es un fallo de red,
                # es un Core colgado. Se relanza (solo si es el nuestro).
                log(f"[core] {estado.fallos_core} sondeos seguidos sin respuesta "
                    f"con el proceso vivo: se relanza")
                core.detener()
                if core.arrancar():
                    problemas += 1
                    estado.fallos_core = 0
            else:
                # El puerto escucha pero /stats falla y el proceso no es
                # nuestro (o esta arrancando): no se lanza un duplicado, que
                # moriria al no poder enlazar el puerto.
                log("[core] el puerto responde pero /stats falla: no se lanza otro "
                    "Core (revisa logs/core.log)")
    else:
        # 2. Minero: la senal de vida NO es que el proceso exista, es que el
        #    Core pague tareas nuevas. Un minero vivo pero atascado (CUDA
        #    colgada) no sirve de nada y hay que relanzarlo igual.
        minero = hijos.get("minero")
        if minero is not None and time.time() - _INICIO < ESPERA_MINERO_SEG:
            # Margen para que la ventana del minero (la del inicio de sesion)
            # coja la cerradura primero. No es un fallo: es esperar a proposito.
            if verbose:
                log("[minero] esperando a la ventana del inicio de sesion")
        elif minero is not None:
            progresando = estado.progreso(stats, verbose)
            cola = int(stats.get("tareas_pend") or 0)
            if not progresando:
                log(f"[minero] {ATASCO_SEG:.0f} s sin cobrar con cola {cola}: relanzando")
                if minero.vivo():
                    minero.detener()          # el nuestro: se mata y se relanza
                if minero.arrancar():         # si es de otro, arrancar() no duplica
                    problemas += 1
                estado.ultimo_ok_ts = time.time()   # margen para que arranque
            elif not minero.vivo() and cola > ATASCO_COLA_MIN:
                # Nadie mina por nuestra parte y hay trabajo esperando.
                if minero.arrancar():
                    problemas += 1

    # 3. Validador, publicador, panel y tunel: solo hay que mantenerlos vivos.
    #    Esto se hace aunque el Core este caido: si ademas se cayo el panel,
    #    antes se quedaba caido hasta que el Core volviera.
    for nombre in ("validador", "publicador", "panel", "tunel"):
        h = hijos.get(nombre)
        if h is not None and not h.vivo() and not h.caido():
            if h.arrancar():
                problemas += 1

    # 4. GPU en el equipo sin potencia medida. Este sintoma no se veia en
    #    ningun sitio: la maquina minaba en CPU con las GPU encendidas dias
    #    enteros y el log parecia normal.
    if stats:
        try:
            _aviso_potencia(stats, estado, verbose)
        except Exception as e:
            log(f"[gpu] no pude comprobar la potencia medida ({type(e).__name__}: {e})")

    if verbose:
        log(f"[ok] nodos={stats.get('nodos')} cola={stats.get('tareas_pend')} "
            f"tareas_ok={stats.get('tareas_ok')}")
    return problemas


def main() -> int:
    ap = argparse.ArgumentParser(description="Guardia de procesos KUKIA")
    ap.add_argument("--una", action="store_true", help="un solo ciclo y sale")
    ap.add_argument("--verbose", action="store_true", help="detalle en el log")
    ap.add_argument("--sin-minero", action="store_true", help="no vigilar el minero")
    args = ap.parse_args()

    # CANDADO DE INSTANCIA UNICA. Tres guardias a la vez no duplican procesos
    # (cada uno comprueba antes), pero si el minero se cae, los tres lo
    # relanzan a la vez y acabas con tres mineros peleando por la misma GPU.
    if not args.una:
        # FALLO REAL (22-10-2026): se buscaba "tools/guardia.py" (con barra
        # inclinada), pero el lanzador de Inicio arranca el guardia con la ruta
        # completa de Windows, "...\KUKIA\tools\guardia.py" (con barra
        # invertida). PowerShell -like no trata la barra invertida como
        # comodin, asi que no se veia al otro y arrancaban DOS guardias. Se
        # busca solo el nombre del fichero: vale para las dos formas.
        otros = _contar_procesos("guardia.py", excluir_pid=os.getpid())
        if otros > 0:
            log(f"ya hay {otros} guardia(s) corriendo: esta no arranca "
                f"(mata la otra o usa --una para un ciclo suelto)")
            return 0

    hijos = construir_hijos(args.sin_minero)
    estado = Estado()
    if not args.una:
        threading.Thread(target=_hilo_anclaje, daemon=True).start()
        # El Core caido se nota por el puerto en ~2 s, no al final del intervalo.
        threading.Thread(target=vigia_core, daemon=True, name="vigia-core").start()
    log(f"guardia arrancada (core={CORE}, intervalo={INTERVALO:.0f}s, "
        f"atasco={ATASCO_SEG:.0f}s)")

    if args.una:
        # En modo prueba se mira una vez y se sale, sin dejar hijos colgando.
        p = ciclo(hijos, estado, True)
        log(f"un ciclo: {p} incidencias")
        for h in hijos.values():
            h.detener()
        return 0 if p == 0 else 1

    problemas = 0
    try:
        while True:
            problemas = ciclo(hijos, estado, args.verbose)
            # Con todo sano se sondea cada 30 s (barato). Si algo esta mal se
            # sondea cada pocos segundos, y si un hijo se muere el evento corta
            # la espera en el acto: la recuperacion no depende del intervalo.
            espera = INTERVALO
            if estado.fallos_core > 0 or any(h.caido() for h in hijos.values()):
                espera = min(INTERVALO, INTERVALO_MALO)
            esperar(espera)
    except KeyboardInterrupt:
        log("guardia detenida por el usuario")
    finally:
        for h in hijos.values():
            h.detener()
    return 0 if problemas == 0 else 1


if __name__ == "__main__":
    for _f in (sys.stdout, sys.stderr):
        try:
            _f.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    raise SystemExit(main())
