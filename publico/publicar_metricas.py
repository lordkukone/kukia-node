"""Publica las metricas del Core local en el rele publico (solo lectura).

El Core NO se expone a internet: este script sale a buscar /stats y /saldos
cada pocos segundos y los empuja al Worker `publico/relay.js` con una clave.
La web publica solo puede leer.

Configuracion (dos ficheros junto a este script, NO se publican):
  relay_url.txt  -> https://<tu-rele>/   (sin barra final)
  clave.txt      -> la misma clave que el secreto KUKIA_CLAVE del Worker

Uso:
  python publicar_metricas.py            (bucle, cada 60 s)
  python publicar_metricas.py --una      (una sola vez, para probar)
"""
import collections
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

AQUI = Path(__file__).parent
IDENTIDAD = AQUI.parent / "worker" / "identidad_minero.json"
# Log del minero: cada linea trae la GPU, el tipo de tarea y los ms que tardo.
# De ahi sale la potencia medida (ver flops_de / potencia mas abajo).
# RUTA UNICA (20-10-2026): antes apuntaba a minero_ok.out.log, un fichero de
# una sesion concreta que dejo de escribirse; la web mostraba 0 GFLOPS. Ahora
# es worker/minero.log, que es donde escribe la guardia y donde apunta
# ARRANCAR_MINERO.cmd cuando se redirige la salida.
LOG_MINERO = Path(os.environ.get(
    "KUKIA_LOG_MINERO", str(AQUI.parent / "worker" / "minero.log")))
CORE = os.environ.get("KUKIA_CORE", "http://127.0.0.1:8300").rstrip("/")
# 60 s: cada publicacion ESCRIBE en el almacenamiento del rele, y el plan
# gratuito da 2.500 escrituras al dia. Publicar cada 5 s son 17.280: el 20-10-2026
# el sitio entero se quedo en pausa por eso. A 60 s son 1.440, con margen.
INTERVALO = float(os.environ.get("KUKIA_PUBLICAR_SEG", "60"))

# --- Instantanea de respaldo, en el MISMO dominio que la web -----------------
# El rele es un punto unico de fallo: si el hosting lo pausa por cupo, la web
# se queda sin datos. Este fichero vive en docs/, asi que la web lo lee de su
# propio origen (sin CORS y sin depender del rele). Se reescribe en cada vuelta
# y se publica como mucho cada KUKIA_SNAPSHOT_DEPLOY_SEG (20 min por defecto);
# si el rele falla, se publica en cuanto pasan 5 min, no en cada reintento.
SNAPSHOT = AQUI.parent / "docs" / "metricas_snapshot.json"
DOMINIO_WEB = os.environ.get("KUKIA_WEB_DOMINIO", "kukia-network.surge.sh")
DEPLOY_SEG = float(os.environ.get("KUKIA_SNAPSHOT_DEPLOY_SEG", "1200"))
DEPLOY_SEG_FALLO = float(os.environ.get("KUKIA_SNAPSHOT_DEPLOY_FALLO_SEG", "300"))


def leer_fichero(nombre: str) -> str:
    try:
        return (AQUI / nombre).read_text(encoding="utf-8").strip()
    except Exception:
        return ""


def get_json(ruta: str):
    with urllib.request.urlopen(CORE + ruta, timeout=8) as r:
        return json.loads(r.read().decode("utf-8"))


# Ancla publica: el ultimo sello firmado del Core. El rele lo guarda y la web
# puede demostrar que un historial reescrito no encadena con lo ya publicado.
_ancla = {"ts": 0.0, "dato": None}

def sello_actual():
    ahora = time.time()
    if _ancla["dato"] is not None and ahora - _ancla["ts"] < 60:
        return _ancla["dato"]
    try:
        d = get_json("/ledger/sellos?limite=1")
        sellos = d.get("sellos") or []
        s = sellos[-1] if sellos else None
        if not isinstance(s, dict):
            return _ancla["dato"]
        dato = {
            "v": s.get("v"), "hasta": s.get("hasta"), "hash": s.get("hash"),
            "asientos": s.get("asientos"), "suma": s.get("suma"), "ts": s.get("ts"),
            "prev": s.get("prev", ""), "firma": s.get("firma", ""),
            # `legado` va dentro de la firma del sello v2. Si no se envia, el
            # ancla publicada no se puede verificar criptograficamente.
            "legado": s.get("legado"),
            "clave_publica": d.get("clave_publica", ""),
        }
        _ancla.update({"ts": ahora, "dato": dato})
        return dato
    except Exception:
        return _ancla["dato"]


# Ventana movil de tareas: sirve para que la web sepa si el minero esta
# minando AHORA (una tarea de hace 2 minutos no es "en vivo").
_hist = collections.deque()      # (momento, tareas_ok, supply_emitido)
_ultimo_cambio = 0.0
# Serie para las graficas de la web: un punto por publicacion (1 min).
# 180 puntos = 3 horas de historia sin crecer.
_serie = collections.deque(maxlen=180)
_ultimo_punto = 0.0
CADA_PUNTO = 60.0


def _gpus_de_identidad():
    """GPUs reales segun worker/identidad_minero.json (lo escribe el minero)."""
    try:
        d = json.loads(IDENTIDAD.read_text(encoding="utf-8"))
        claves = [k for k in d if re.match(r"^(cuda|cpu)", str(k))]
        return len(claves) or None
    except Exception:
        return None


def _potencia_tops(nodos: list) -> float:
    """TOPS de los nodos que estan minando AHORA (los del fichero de identidad).

    No se usa stats.dificultad.tops_red porque incluye nodos viejos de prueba y
    estaria inflado (hoy: 11.192 TOPS frente a 1.747 reales).
    """
    ids = _ids_de_identidad()
    if not ids:
        return 0.0
    return round(sum(float(n.get("tops") or 0.0) for n in nodos if n.get("id") in ids), 1)


def _ids_de_identidad():
    try:
        d = json.loads(IDENTIDAD.read_text(encoding="utf-8"))
        return {v.get("id") for v in d.values() if isinstance(v, dict) and v.get("id")}
    except Exception:
        return set()


# --- Potencia medida: FLOPs verificados / segundo, por GPU ---------------
# NO se usan los TOPS que el nodo declara al registrarse (son autodeclarados y
# estaban inflados). Se usa el trabajo real: cada tarea lleva un tamano fijado
# por el Core y un tiempo de GPU. FLOPs de la tarea / segundos = potencia.
VENTANA_POT = float(os.environ.get("KUKIA_POT_VENTANA_SEG", "120"))
# Formato del minero:
#   [KUKIA] kuki_minero[cuda:0] tarea 9009caad matriz -> OK +1.0 KIA (4 ms)
_RE_TAREA = re.compile(
    r"\[KUKIA\]\s+\S+\[(?P<dev>[^\]]+)\]\s+tarea\s+[0-9a-f]+\s+"
    r"(?P<tipo>[A-Za-z_]+)\s+->\s+OK\s+\+?[\d.]+\s+KIA\s+\((?P<ms>\d+)\s*ms\)")
_log_pos = 0
_log_resto = ""
_muestras = collections.deque()      # (momento, gpu, flops, segundos)
_ultima_pot = {"gflops": None, "gpus": []}


def flops_de(tipo: str, dif: dict) -> float:
    """FLOPs de una tarea, con la misma formula que usa el Core.

    matriz: 2*n^3 (producto de matrices). inferencia: 2*9*(n-2)^2 (convolucion
    3x3). difusion_calor: 2*5*(n-2)^2*pasos, con n=1024 y pasos=1500 fijos en
    el generador del Core. checksum/embeddings: sin FLOPs utiles (0).
    """
    try:
        if tipo == "matriz":
            n = float(dif.get("matriz_n") or 0)
            return 2.0 * n ** 3
        if tipo == "inferencia":
            n = float(dif.get("inferencia_n") or 0)
            return 2.0 * 9.0 * max(0.0, n - 2) ** 2
        if tipo == "difusion_calor":
            # Los pasos los fija el control de dificultad del Core (cambian con
            # el tiempo); antes eran 1500 fijos y el calculo habria quedado viejo.
            pasos = float(dif.get("difusion_pasos") or 1500)
            return 2.0 * 5.0 * (1024 - 2) ** 2 * pasos
    except (TypeError, ValueError):
        return 0.0
    return 0.0


def _leer_log(dif: dict) -> None:
    """Lee solo lo nuevo del log. En el primer arranque salta al final: el
    historico no se cuenta como si acabara de ocurrir."""
    global _log_pos, _log_resto
    try:
        tam = LOG_MINERO.stat().st_size
    except OSError:
        return
    if _log_pos == 0:
        _log_pos = tam
        return
    if tam < _log_pos:              # log rotado o truncado
        _log_pos, _log_resto = 0, ""
        return
    if tam == _log_pos:
        return
    try:
        with LOG_MINERO.open("rb") as f:
            f.seek(_log_pos)
            crudo = f.read(tam - _log_pos)
    except OSError:
        return
    _log_pos = tam
    texto = _log_resto + crudo.decode("utf-8", "ignore")
    lineas = texto.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    _log_resto = lineas.pop() if lineas else ""
    ahora = time.time()
    for linea in lineas:
        m = _RE_TAREA.search(linea)
        if not m:
            continue
        seg = int(m.group("ms")) / 1000.0
        flops = flops_de(m.group("tipo"), dif)
        if seg <= 0 or flops <= 0:
            continue                # checksum: no mide potencia, solo tareas
        _muestras.append((ahora, m.group("dev").strip(), flops, seg))
    limite = ahora - VENTANA_POT - 60
    while _muestras and _muestras[0][0] < limite:
        _muestras.popleft()


def potencia() -> dict:
    """GFLOPS por GPU en la ventana. 0 si el minero paro; None si no hay log."""
    global _ultima_pot
    ahora = time.time()
    desde = ahora - VENTANA_POT
    acum = {}
    for t, dev, flops, seg in _muestras:
        if t < desde:
            continue
        a = acum.setdefault(dev, [0.0, 0.0])
        a[0] += flops
        a[1] += seg
    gpus = [{"d": d, "gflops": round(f / s / 1e9, 2)}
            for d, (f, s) in sorted(acum.items()) if s > 0]
    if gpus:
        # Potencia TOTAL de la maquina = suma de lo que rinde cada GPU.
        # (Dividir FLOPs totales entre segundos totales daria la MEDIA entre
        # tarjetas, no el total: con 2 GPUs a 29 GFLOPS la media es 29, no 58.)
        total = sum(f / s / 1e9 for f, s in acum.values() if s > 0)
        _ultima_pot = {"gflops": round(total, 2), "gpus": gpus}
    else:
        _ultima_pot = {"gflops": (0.0 if _muestras else None), "gpus": []}
    return _ultima_pot


# --- Serie por GPU medida: GFLOPS y LATENCIA reales de cada tarjeta -------
# Antes la web solo podia dibujar la potencia que se deducia del log del
# minero; la latencia por GPU no llegaba a la web. Ahora se pide al Core
# (/red/serie_gpu), que la agrupa de la telemetria que manda el minero en
# CADA tarea: tiempo cronometrado en la GPU y FLOPs de esa tarea. Nada de
# medias declaradas ni de numeros que el minero pueda inflar.
_gpu_serie = {"dato": None, "ts": 0.0}


def gpu_serie_publica() -> dict:
    """Serie compacta (60 min) para las graficas de la web publica."""
    global _gpu_serie
    ahora = time.time()
    if _gpu_serie["dato"] is not None and ahora - _gpu_serie["ts"] < CADA_PUNTO:
        return _gpu_serie["dato"]
    try:
        d = get_json("/red/serie_gpu?minutos=60")
    except Exception:
        return _gpu_serie["dato"]
    puntos = []
    for p in (d.get("puntos") or [])[-60:]:
        if not isinstance(p, dict):
            continue
        gpus = {}
        for dev, v in (p.get("gpus") or {}).items():
            if not isinstance(v, dict):
                continue
            fila = {}
            gf, ms, n = v.get("gflops"), v.get("ms"), v.get("tareas")
            if isinstance(gf, (int, float)) and not isinstance(gf, bool):
                fila["gflops"] = round(float(gf), 2)
            if isinstance(ms, (int, float)) and not isinstance(ms, bool):
                fila["ms"] = round(float(ms), 1)
            if isinstance(n, (int, float)) and not isinstance(n, bool):
                fila["tareas"] = int(n)
            if fila:
                gpus[str(dev)[:16]] = fila
        try:
            t = int(p.get("t") or 0)
        except (TypeError, ValueError):
            continue
        if gpus and t > 0:
            puntos.append({"t": t, "gpus": gpus})
    tipos = {}
    for k, v in (d.get("tipos") or {}).items():
        if isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0:
            tipos[str(k)[:24]] = int(v)
    dato = {
        "minutos": 60,
        "devices": [str(x)[:16] for x in (d.get("devices") or [])][:4],
        "puntos": puntos[-60:],
        # Mezcla de trabajo de la ventana: que se estaba calculando de verdad.
        "tipos": dict(sorted(tipos.items(), key=lambda kv: -kv[1])[:8]),
        "fuente": "tiempo y FLOPs medidos por el minero en cada tarea",
    }
    _gpu_serie = {"dato": dato, "ts": ahora}
    return dato


def salud(nodos: list, supply: float = 0.0) -> dict:
    """Estado del minero calculado en local (el rele no ve tu Core)."""
    global _ultimo_cambio
    ahora = time.time()
    ok = float(_hist[-1][1]) if _hist else 0.0
    base = _hist[0][1] if _hist else ok
    sup_base = _hist[0][2] if _hist else supply
    seg = (ahora - _hist[0][0]) if _hist else 0.0
    # Tareas por minuto normalizadas a la ventana que haya (hasta 60 s).
    por_min = ((ok - base) / seg * 60.0) if seg > 1 else 0.0
    # Lo mismo con las monedas: asi la web puede mostrar "+N KIA/min" y se ve
    # que el suministro crece aunque el total redondeado parezca quieto.
    emision = ((supply - sup_base) / seg * 60.0) if seg > 1 else 0.0
    gpus = _gpus_de_identidad()
    if gpus is None:
        # Sin fichero de identidad: cuenta los nodos con nombre de GPU (puede
        # incluir nodos viejos, por eso el fichero manda cuando existe).
        gpus = len({n.get("nombre") for n in nodos
                    if re.search(r"\[(cuda|cpu):?\d*\]$", str(n.get("nombre", "")))})
    return {
        "gpus": gpus,
        "tareas_por_min": int(round(por_min)),
        "emision_por_min": round(max(0.0, emision), 2),
        "tops": _potencia_tops(nodos),
        "ultima_tarea_hace": int(max(0, ahora - _ultimo_cambio)) if _ultimo_cambio else None,
    }


def escribir_snapshot(datos: dict) -> Path:
    """Escribe la instantanea que la web sirve desde su propio dominio.

    Solo lo que la pagina pinta: stats, salud y ancla. Nada de saldos (son
    dato por direccion) ni de series largas. Escritura atomica: si el proceso
    muere a medias, el fichero anterior sigue entero.
    """
    d = {
        "ts": int(time.time()),
        "stats": datos.get("stats") or {},
        "salud": datos.get("salud") or {},
        "ancla": datos.get("ancla"),
        "respaldo": True,
    }
    tmp = SNAPSHOT.with_name(SNAPSHOT.name + ".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, separators=(",", ":")),
                   encoding="utf-8")
    tmp.replace(SNAPSHOT)
    return SNAPSHOT


_ultimo_deploy = 0.0


def _snapshot_arriba(ts_local) -> bool:
    """Comprueba en el dominio si la instantanea con ese `ts` ya esta publicada.

    AUDITORIA 21-10-2026: surge (Node en Windows) se queda colgado DESPUES de
    subir los ficheros y el proceso muere por timeout. Mirar solo el codigo de
    salida daba "no pude publicar" cada 20 min aunque la instantanea estuviera
    arriba. Se pregunta al dominio, que es la unica prueba que vale.
    """
    try:
        url = f"https://{DOMINIO_WEB}/metricas_snapshot.json"
        with urllib.request.urlopen(url, timeout=15) as r:
            if r.status != 200:
                return False
            remoto = json.loads(r.read().decode("utf-8"))
        return int(remoto.get("ts") or 0) == int(ts_local or 0)
    except Exception:
        return False


def desplegar_snapshot(forzado: bool = False) -> bool:
    """Publica docs/ en el dominio de la web. Con freno: no en cada vuelta.

    Surge sube el directorio completo, asi que esto NO puede ir cada minuto:
    por defecto cada 20 min, y cada 5 min extra si el rele esta fallando.
    """
    global _ultimo_deploy
    ahora = time.time()
    espera = DEPLOY_SEG_FALLO if forzado else DEPLOY_SEG
    if _ultimo_deploy and ahora - _ultimo_deploy < espera:
        return False
    surge = shutil.which("surge")
    if not surge:
        print("[KUKIA] aviso: no encuentro 'surge' en el PATH; la instantanea "
              "se queda en docs/ sin publicar", flush=True)
        _ultimo_deploy = ahora
        return False
    cmd = ([surge, str(SNAPSHOT.parent), DOMINIO_WEB] if os.name != "nt"
           else ["cmd", "/c", "surge", str(SNAPSHOT.parent), DOMINIO_WEB])
    # `cmd /c surge` es un programa de consola: sin esta bandera, el publicador
    # (que va dentro de la guardia, sin consola) abria una ventana negra cada
    # 20 minutos. Con CREATE_NO_WINDOW publica igual y no se ve nada.
    extra = {}
    if os.name == "nt":
        extra["creationflags"] = 0x08000000   # CREATE_NO_WINDOW
    # El `ts` de lo que se va a subir: sirve para saber despues si lo que hay
    # en el dominio es justo esta instantanea.
    try:
        ts_local = int(json.loads(SNAPSHOT.read_text(encoding="utf-8")).get("ts") or 0)
    except Exception:
        ts_local = 0
    try:
        # stdin=DEVNULL: surge no tiene que pedir nada por consola. Sin esto, si
        # se queda esperando una respuesta, el publicador se cuelga hasta el
        # timeout (paso de verdad varias veces el 21-10).
        r = subprocess.run(cmd, capture_output=True, timeout=240,
                           stdin=subprocess.DEVNULL, **extra)
        if r.returncode != 0:
            if ts_local and _snapshot_arriba(ts_local):
                _ultimo_deploy = ahora
                print(f"[KUKIA] instantanea publicada en {DOMINIO_WEB} "
                      f"(surge devolvio {r.returncode} pero el dominio ya la sirve)", flush=True)
                return True
            detalle = (r.stderr or r.stdout or b"").decode("utf-8", "replace")[-200:]
            print(f"[KUKIA] no pude publicar la instantanea ({r.returncode}): {detalle}", flush=True)
            _ultimo_deploy = ahora
            return False
        _ultimo_deploy = ahora
        print(f"[KUKIA] instantanea publicada en {DOMINIO_WEB}", flush=True)
        return True
    except subprocess.TimeoutExpired:
        # Subio y se colgo, o no subio: lo dice el dominio, no el reloj.
        _ultimo_deploy = ahora
        if ts_local and _snapshot_arriba(ts_local):
            print(f"[KUKIA] instantanea publicada en {DOMINIO_WEB} "
                  f"(surge se colgo al terminar, pero el dominio ya la sirve)", flush=True)
            return True
        print("[KUKIA] surge se colgo sin publicar la instantanea; se reintenta luego", flush=True)
        return False
    except Exception as e:
        print(f"[KUKIA] no pude publicar la instantanea ({type(e).__name__}: {e})", flush=True)
        _ultimo_deploy = ahora
        return False


def construir() -> dict:
    """Lee el Core y arma el cuerpo. Devuelve el dict y los bytes a enviar."""
    global _ultimo_cambio, _ultimo_punto
    stats = get_json("/stats")
    saldos = get_json("/saldos")
    nodos = get_json("/nodos")
    ok = float(stats.get("tareas_ok") or 0)
    supply = float(stats.get("supply_emitido") or 0)
    ahora = time.time()
    if not _hist or ok != _hist[-1][1]:
        _ultimo_cambio = ahora
    _hist.append((ahora, ok, supply))
    # 3 min de ventana: con publicaciones cada 60 s hacen falta varias muestras
    # para que el ritmo no salga de una sola y marque 0 o un pico falso.
    while _hist and ahora - _hist[0][0] > 180:
        _hist.popleft()
    sal = salud(nodos, supply)
    _leer_log(stats.get("dificultad") or {})
    pot = potencia()
    sal["gflops"] = pot["gflops"]
    sal["gflops_gpus"] = pot["gpus"]
    if ahora - _ultimo_punto >= CADA_PUNTO:
        _serie.append({
            "t": int(ahora),
            "supply": round(supply, 2),
            "tareas": int(ok),
            # El primer punto de cada arranque no tiene ventana suficiente: se
            # manda sin ritmo para que la web no dibuje un pico falso.
            "rate": (int(sal.get("tareas_por_min") or 0)
                     if (_hist and (ahora - _hist[0][0]) >= 19) else None),
            "gpus": int(sal.get("gpus") or 0),
            # Potencia por GPU en este instante: {"cuda:0": 12.3, ...}
            "pot": ({g["d"]: g["gflops"] for g in pot["gpus"]} or None),
        })
        _ultimo_punto = ahora
    datos = {
        "stats": stats,
        "salud": sal,
        "serie": list(_serie),
        # Por GPU y minuto: GFLOPS y latencia MEDIDOS en la tarjeta.
        "gpu_serie": gpu_serie_publica(),
        "saldos": saldos.get("saldos", {}),
        "ancla": sello_actual(),
    }
    datos["_cuerpo"] = json.dumps(datos).encode("utf-8")
    return datos


def enviar(relay: str, clave: str, cuerpo: bytes) -> dict:
    req = urllib.request.Request(
        relay + "/api/publicar",
        data=cuerpo,
        method="POST",
        # User-Agent propio: el borde de Cloudflare rechaza (403) el de
        # urllib por defecto.
        headers={"content-type": "application/json",
                 "authorization": "Bearer " + clave,
                 "user-agent": "KUKIA-publicador/1.0"},
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))


def publicar(relay: str, clave: str) -> dict:
    datos = construir()
    escribir_snapshot(datos)
    return enviar(relay, clave, datos["_cuerpo"])


def main() -> int:
    relay = leer_fichero("relay_url.txt").rstrip("/")
    clave = leer_fichero("clave.txt")
    if not relay or not clave:
        print("[KUKIA] faltan publico/relay_url.txt o publico/clave.txt", flush=True)
        return 2
    una = "--una" in sys.argv
    print(f"[KUKIA] publicando {CORE} -> {relay} cada {INTERVALO:.0f} s", flush=True)
    estado = None
    ultimo_aviso = 0.0
    while True:
        try:
            datos = construir()
            # La instantanea se escribe SIEMPRE, antes de intentar el rele: es
            # justo cuando el rele falla cuando mas falta hace.
            escribir_snapshot(datos)
            j = enviar(relay, clave, datos["_cuerpo"])
            desplegar_snapshot()
            ahora = time.time()
            if estado != "ok" or ahora - ultimo_aviso >= 60:
                s = get_json("/stats")
                print(f"[KUKIA] metricas publicadas: {s['nodos']} nodos, "
                      f"{s['tareas_ok']} tareas ok, {j.get('saldos')} saldos, "
                      f"gpu_serie={len((gpu_serie_publica() or {}).get('puntos') or [])} puntos",
                      flush=True)
                ultimo_aviso = ahora
            estado = "ok"
        except urllib.error.HTTPError as e:
            if e.code == 429:
                # El hosting gratuito se quedo sin cupo (escrituras o
                # peticiones del dia). Insistir cada minuto no arregla nada y
                # gasta el poco margen: se espera 10 min.
                if estado != "cupo" or time.time() - ultimo_aviso >= 60:
                    print("[KUKIA] el rele esta en pausa por cupo del hosting "
                          "(429); reintento en 10 min. El Core sigue vivo.",
                          flush=True)
                    ultimo_aviso = time.time()
                estado = "cupo"
                # Con el rele pausado, la unica via viva es la instantanea del
                # propio dominio: se refresca y se publica con urgencia.
                desplegar_snapshot(forzado=True)
                if una:
                    return 1
                time.sleep(min(600, max(60, DEPLOY_SEG_FALLO)))
                continue
            if estado != "http" or time.time() - ultimo_aviso >= 60:
                print(f"[KUKIA] el rele rechaza la publicacion: {e.code} "
                      f"(revisa clave.txt)", flush=True)
                ultimo_aviso = time.time()
            estado = "http"
            desplegar_snapshot(forzado=True)
        except Exception as e:
            if estado != "err" or time.time() - ultimo_aviso >= 60:
                print(f"[KUKIA] no pude publicar ({type(e).__name__}: {e}); "
                      f"el Core sigue en {CORE}", flush=True)
                ultimo_aviso = time.time()
            estado = "err"
            desplegar_snapshot(forzado=True)
        if una:
            return 0 if estado == "ok" else 1
        time.sleep(INTERVALO)


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    raise SystemExit(main())
