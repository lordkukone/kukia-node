"""KUKIA Core - coordinador: nodos, tareas, verificacion REAL, ledger (SQLite).

VERIFICACION: el Core NO confia en el hash del minero.
El Core recalcula la tarea el mismo (trustless) y compara el resultado
exacto. Tareas con aritmetica determinista (enteros exactos via float64
con magnitudes acotadas) para que CPU y GPU den el MISMO numero.

AUDITORIA 2026-10-18 (todas las marcas "AUDITORIA" son de esta revision):
  - Se valida la cabecera Host y el Origin (corta el DNS rebinding).
  - /tareas/pendiente exige firma Ed25519 del nodo (nadie roba la cola).
  - /tareas/resultado AUTENTICA ANTES de recalcular (no se puede quemar CPU).
  - Topes duros a params (n, pasos) y tamano del JSON.
  - Limitador por ruta (antes un solo contador bloqueaba las 5 rutas juntas).
  - Reintentos: un resultado incorrecto no mata la tarea; castiga al nodo.
  - Recuperacion de tareas atascadas en hilo propio (no dependia de que
    alguien pidiera trabajo) + /admin/liberar_atascadas.
  - Ledger con cadena de hashes verificable (/ledger/verificar).
  - monto de transferencia finito (NaN daba 500 despues de escribir).
  - /registro guarda la IP real (antes guardaba el 'ref' en su columna).
"""
import collections, hashlib, json, math, os, secrets, sqlite3, threading, uuid, time, sys, urllib.parse
from contextlib import contextmanager
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).parent.parent))
try:
    import ia_onnx_cpu as ONNXCPU  # referencia CPU (tareas embeddings)
except Exception:
    ONNXCPU = None
try:
    from ledger import sellos as SELLOS
except Exception as _e_sellos:      # el Core arranca aunque falte el modulo
    SELLOS = None
    print(f"[KUKIA] AVISO: sellos del ledger desactivados ({_e_sellos})", flush=True)
from fastapi import FastAPI, HTTPException, Header, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

# Lock global: SQLite NO es seguro para uso concurrente en una sola conexion.
# Todo pago pasa por este lock + BEGIN IMMEDIATE (doble proteccion).
_pago_lock = threading.Lock()
# Conexion de SQLite de cada hilo (ver _ConSegura: compartir una sola hacia
# que un ROLLBACK ajeno cortase las lecturas de otra peticion).
_local_bd = threading.local()

DB = Path(os.environ.get("KUKIA_DB") or (Path(__file__).parent / "ledger" / "kukia.db"))
DB.parent.mkdir(parents=True, exist_ok=True)

# --- Token de ADMIN: solo Kuki maneja el Core ---
# Se genera UNA vez y se guarda en un fichero local (nadie mas lo tiene).
ADMIN_TOKEN_FILE = Path(os.environ.get("KUKIA_ADMIN_TOKEN") or (Path(__file__).parent / "admin_token.txt"))
def _permisos_privados(ruta) -> None:
    """Solo el dueno puede leer el fichero (en POSIX; en Windows manda la ACL)."""
    try:
        os.chmod(ruta, 0o600)
    except Exception:
        pass

def admin_token() -> str:
    if ADMIN_TOKEN_FILE.exists():
        _permisos_privados(ADMIN_TOKEN_FILE)
        return ADMIN_TOKEN_FILE.read_text(encoding="utf-8").strip()
    tok = secrets.token_hex(32)
    ADMIN_TOKEN_FILE.write_text(tok, encoding="utf-8")
    _permisos_privados(ADMIN_TOKEN_FILE)
    return tok

ADMIN_TOKEN = admin_token()

def exigir_admin(x_admin_token: str = Header(default="", alias="X-Admin-Token")):
    """Solo el fundador (con el token) puede administrar el Core."""
    if not secrets.compare_digest(x_admin_token or "", ADMIN_TOKEN):
        raise HTTPException(403, "solo el administrador del Core")

# --- Sellos del ledger: fijan el historial para que no se pueda reescribir ---
# La clave vive en coordinator/sello_key.txt, FUERA de la base de datos: editar
# el .db no basta para fabricar un historial nuevo. Ver ledger/sellos.py.
SELLO_KEY_FILE = Path(os.environ.get("KUKIA_SELLO_KEY") or (Path(__file__).parent / "sello_key.txt"))
SELLOS_FILE = Path(os.environ.get("KUKIA_SELLOS_FILE") or (Path(__file__).parent / "sellos.jsonl"))
SELLO_CADA_SEG = max(30.0, float(os.environ.get("KUKIA_SELLO_SEG", "600")))
SELLO_CADA_ASIENTOS = max(50, int(os.environ.get("KUKIA_SELLO_ASIENTOS", "2000")))
SELLO_BLOQUEA_PAGOS = os.environ.get("KUKIA_SELLO_BLOQUEA", "1") != "0"
LEDGER_ROTO = False          # lo pone _verificar_integridad() al arrancar
_sello_priv, _sello_pub = ("", "")
if SELLOS is not None:
    try:
        _sello_priv, _sello_pub = SELLOS.crear_clave(SELLO_KEY_FILE)
    except Exception as _e:
        print(f"[KUKIA] AVISO: no se pudo crear la clave del sello ({_e})", flush=True)

MAX_SUPPLY = 100_000_000          # techo fijo 100M
REWARD_BASE = 10.0                # KIA por tarea base

# --- Puerta de cliente (24-10-2026) ---
# Cuenta interna donde se guarda el dinero del cliente hasta que la tarea se
# completa. NO es una billetera: es una cuenta del ledger, y el titular no
# tiene clave privada, asi que solo el Core puede moverla.
CUENTA_ESCROW = os.environ.get("KUKIA_ESCROW", "KUKIA_ESCROW")
# INGRESOS EN EUROS (25-10-2026): la cuenta donde se anota el dinero REAL que
# entra por un servicio de fuera de la red (transcripcion, etc.). Se anota con
# asientos de 0 KIA a proposito: el euro no se convierte en KIA ni emite nada,
# solo queda escrito en la cadena que alguien pago, cuanto y por que trabajo.
# Ese es el dato que convierte "KIA vale 0" en "KIA vale lo que alguien pago".
CUENTA_INGRESOS = os.environ.get("KUKIA_CUENTA_INGRESOS", "INGRESOS_EUR")
# Tope por defecto si el cliente no dice cuanto autoriza. Evita que una firma
# vieja o un error de script vacie la billetera del cliente con una tarea cara.
CLIENTE_TOPE_DEFECTO = abs(float(os.environ.get("KUKIA_CLIENTE_TOPE", "25")))
# Cuantas tareas puede tener en cola un cliente a la vez. Sin esto, un cliente
# (o quien robe su clave) puede encolar mil y bloquear la cola de los mineros.
CLIENTE_MAX_EN_COLA = max(1, int(os.environ.get("KUKIA_CLIENTE_MAX_EN_COLA", "8")))
# AUDITORIA 24-10-2026: esta lista vivia SOLO dentro de /cliente/precio. En
# POST /cliente/tareas no se comprobaba el tipo, asi que un tipo inventado se
# aceptaba, se cobraba (al minimo facturable) y se quedaba en la cola para
# siempre: ningun minero sabe ejecutarlo. Ahora la lista es una sola y se usa
# en las dos puertas, antes de tocar dinero.
TIPOS_CLIENTE = ("checksum", "matriz", "inferencia", "embeddings",
                 "lote_embeddings", "difusion_calor")
# --- Bloque genesis fundacional (una sola vez) ---
GENESIS_DIR = "KUK77D72508BD940C9BA969B0402A3CF1F9679BE3"   # wallet fundador (Kuki)
GENESIS_MONTO = 15_000_000.0      # 15M reservados al fundador dentro del techo de 100M
TIER_MULT = {12: 1.0, 16: 1.3, 24: 1.6}   # Rent-Tier por VRAM (mejora 1)
# --- Pago por potencia demostrada (22-10-2026) - APAGADO por defecto --------
# Con KUKIA_PAGO_POR_FLOP=1 se paga el trabajo (FLOPs verificados x precio) en
# vez de una tarifa plana por tarea. Ver pago_por_trabajo() mas abajo.
# ACTIVO por defecto desde el 22-10-2026: el pago sigue a los FLOPs verificados.
# Para volver a la tarifa plana: KUKIA_PAGO_POR_FLOP=0 (y reiniciar el Core).
PAGO_POR_FLOP = os.environ.get("KUKIA_PAGO_POR_FLOP", "1").strip().lower() not in ("0", "", "no", "false", "off")
# ANCLA GPU-MINUTO (25-10-2026) - ENCENDIDA POR DEFECTO.
# El precio por GFLOP deja de salir del trabajo medido el minuto anterior y sale
# de una GPU de referencia a tope: un minuto de GPU cuesta el presupuesto del
# minuto, o sea la emision de un mes paga exactamente un mes de GPU. Con los
# numeros del 25-10 son 1,3106 KIA por minuto de GPU, 78,6 KIA la hora y
# 1,4562e-6 KIA/GFLOP (antes, sin medida de red, se cobraba ~480 veces mas).
# Se apaga con KUKIA_ANCLA_GPU_MIN=0 (y reiniciar) y vuelve el reparto por
# trabajo medido. Ver tools/prueba_ancla.py y PLAN_DEMANDA.md.
ANCLA_GPU_MIN = os.environ.get("KUKIA_ANCLA_GPU_MIN", "1").strip().lower() not in ("0", "", "no", "false", "off")
# DEFINICION DEL KIA EN MINUTOS DE GPU (25-10-2026) - APAGADA POR DEFECTO
# desde el 25-10-2026 por decision de Kuki (16:20).
#
# ENCENDIDA (KUKIA_PRECIO_DEFINIDO=1): 1 KIA = 1 minuto de la GPU de referencia
# (60 x 15.000 GFLOP = 900.000 GFLOP), asi que precio = 1/900.000 = 0,000001111111
# KIA/GFLOP y una GPU a tope cobra 1 KIA por minuto.
#
# APAGADA (defecto, y lo que corre hoy): el precio sale del PRESUPUESTO del
# minuto repartido entre el trabajo medido, con SUELO de una GPU-minuto de
# capacidad (`ANCLA_GPU_MIN`). Con los numeros de hoy: 1,310592 KIA/min entre
# 900.000 GFLOP = 1,456213e-6 KIA/GFLOP. Es decir, un minuto de GPU vale lo que
# paga la emision de ese minuto (1,31 KIA), no 1 fijo: el precio sube cuando la
# red esta floja y se queda en el suelo cuando va a tope.
#
# Se vuelve a la definicion con KUKIA_PRECIO_DEFINIDO=1 y reiniciar. El factor
# de la definicion se cambia con KUKIA_KIA_POR_MINUTO_GPU.
# Ver tools/prueba_kia_minuto.py, tools/prueba_ancla.py y PLAN_DEMANDA.md.
PRECIO_DEFINIDO = os.environ.get("KUKIA_PRECIO_DEFINIDO", "0").strip().lower() not in (
    "0", "", "no", "false", "off")
KIA_POR_MINUTO_GPU = abs(float(os.environ.get("KUKIA_KIA_POR_MINUTO_GPU", "1") or 1.0)) or 1.0
# GFLOP/s de la GPU de referencia (RTX 5070 medida en esta maquina: 15.000).
GPU_REF_GFLOPS = max(1.0, float(os.environ.get("KUKIA_GPU_REF_GFLOPS", "15000")))
# Suelo facturable por tarea, en GFLOP. A 0 (defecto desde el 22-10-2026) el
# pago es ESTRICTAMENTE proporcional al trabajo: una tarea de 0,4 GFLOP cobra
# 0,4 y un checksum (0 FLOPs) cobra 0.
GFLOP_MIN_FACTURABLE = max(0.0, float(os.environ.get("KUKIA_GFLOP_MIN", "0")))
# SUELO TRANSFERIBLE POR TAREA (25-10-2026) - ENCENDIDO POR DEFECTO.
# Con la definicion (1 KIA = 1 minuto de GPU) una tarea pequena paga 1e-9 KIA, y
# `POST /transferir` redondea el monto a 4 decimales: por debajo de 0,0001 KIA un
# saldo no se puede mover, nunca. Para que nadie trabaje por menos de lo
# transferible, ninguna tarea paga menos de PAGO_MIN_KIA (0,0001 KIA, que es el
# monto minimo que acepta /transferir).
#
# Se aplica EN KIA, no en GFLOP, a proposito: si el precio cambia (definicion
# apagada, otro KIA_POR_MINUTO_GPU), 90 GFLOP dejarian de valer 0,0001 y la
# garantia se romperia en silencio. En KIA el suelo vale siempre lo mismo.
# NO toca la medicion de GFLOP de la red (`gflop_red_por_min` sigue siendo
# trabajo real): solo cambia lo que se paga por una tarea minima.
# Consecuencia medible: las tareas de milisegundos pasan a pagar el suelo, asi
# que el KIA/dia de un minero sube sin que suba el trabajo entregado. El tope
# sigue siendo el presupuesto del minuto (1,31 KIA/min: 13.100 tareas de suelo).
# Se apaga con KUKIA_PAGO_MIN_KIA=0 y el fijo se cambia con la misma variable.
# Ver tools/prueba_suelo_transferible.py.
PAGO_MIN_KIA = max(0.0, float(os.environ.get("KUKIA_PAGO_MIN_KIA", "0.0001") or 0.0))
# Ventana para medir lo que entrega la red: corta, para que el precio siga a la
# realidad, pero no tanto como para oscilar con cada tarea.
GFLOPS_VENTANA_MIN = max(2, int(os.environ.get("KUKIA_VENTANA_MIN", "10")))
PRECIO_TTL = 20.0
REF_BONUS = 50.0                  # bonus referido al completar 10 tareas (mejora 2)
BOUNTY = 1000.0                   # bounty anti-trampa (mejora 3)
# --- Politica de firmas (migracion) ---
# False = nodos viejos sin clave siguen cobrando (compatibilidad, fase pruebas).
# True  = TODOS los nodos deben traer firma (rechaza sin clave_publica).
# Pasar a True al abrir la red publica.
EXIGIR_FIRMA_GLOBAL = True

# Tareas 'ejecutando' sin resultado tras este tiempo vuelven a 'queued'
# (recuperacion en hilo propio Y oportunista en /tareas/pendiente).
ABANDONO_SEG = 1800.0
# AUDITORIA 2026-10-20: un solo nodo podia pedir trabajo en bucle y quedarse
# con toda la cola (30 min cada tarea). El worker de serie hace UNA tarea por
# GPU/nodo, asi que 4 es holgado y corta el acaparamiento de raiz.
# Tareas simultaneas por nodo. 4 es el valor de siempre; con dos ranuras de
# calculo por tarjeta hay que subirlo a 8 (KUKIA_MAX_TAREAS_NODO=8), porque si
# no el nodo se queda sin cola y la GPU espera. Medido el 23-10-2026: la
# ocupacion sube, pero el KIA/kWh baja porque la emision va por presupuesto.
MAX_TAREAS_NODO = max(1, int(os.environ.get("KUKIA_MAX_TAREAS_NODO", "4")))
# Un nodo sin ninguna tarea pagada no puede acumular trabajo: 1 a la vez y
# vence a los 2 minutos. Asi un sybil recien creado no puede inmovilizar la
# cola mientras no demuestra trabajo real.
MAX_TAREAS_NODO_NUEVO = max(1, int(os.environ.get("KUKIA_MAX_TAREAS_NODO_NUEVO", "1")))
ABANDONO_SEG_NUEVO = max(30.0, float(os.environ.get("KUKIA_ABANDONO_NUEVO", "120")))

# AUDITORIA 2026-10-20 (2.ª pasada): cupo de registros por IP y por clave.
# El reto firmado hace que registrar cueste una clave, pero una sola persona
# puede generar claves en bucle. Estos topes no afectan a los mineros que ya
# estan dentro: solo a las identidades NUEVAS. 0 = sin limite.
REGISTRO_CUPO_HORA = max(0, int(os.environ.get("KUKIA_REGISTRO_CUPO_HORA", "10")))
REGISTRO_CUPO_DIA = max(0, int(os.environ.get("KUKIA_REGISTRO_CUPO_DIA", "50")))
REGISTRO_CUPO_CLAVE = max(0, int(os.environ.get("KUKIA_REGISTRO_CUPO_CLAVE", "20")))
# Un nodo que pide trabajo pero no completa NINGUNA tarea pagada no es un
# minero: es cola inmovilizada. Se le da este margen y luego se poda.
NODO_SIN_TRABAJO_H = max(0.5, float(os.environ.get("KUKIA_NODO_SIN_TRABAJO_H", "6")))

# ============================================================
#  EJECUCION REDUNDANTE + MUESTREO (21-10-2026)
#  El Core recalculaba TODAS las tareas: costaba lo mismo minar que
#  verificar, asi que la red no podia crecer mas alla de este PC. Ahora las
#  tareas CARAS las ejecutan DOS nodos distintos y, si coinciden, el Core no
#  recalcula nada.
#  Solo aplica a tareas caras (>= 1 GFLOP): las de milisegundos siguen el
#  camino de siempre, porque duplicarlas costaria mas que recalcularlas.
# ============================================================
REPLICA_ACTIVA = (os.environ.get("KUKIA_REPLICA", "1") != "0")
# Parte del premio que se lleva el nodo que verifica. El total NO cambia:
# 70 % ejecutor + 30 % verificador = lo mismo que antes por una tarea. Si se
# pagara el 100 % a cada uno, la emision se dispararia y la fecha final
# seria mentira.
PARTE_REPLICA = min(0.5, max(0.0, float(os.environ.get("KUKIA_PARTE_REPLICA", "0.3"))))
# 1 de cada N tareas con replica se recalcula igualmente, para que una
# colusion entre dos nodos tenga probabilidad medible de caer.
AUDITORIA_1_DE = max(0, int(os.environ.get("KUKIA_AUDITORIA_1_DE", "10")))
# MUESTREO REFORZADO PARA NODOS SIN HISTORIAL (23-10-2026). Medido: dos
# identidades nuevas decian las dos la misma mentira y el Core pagaba 0,918328
# KIA por una matriz falsa (AUDITORIA_AGENTE_2026-10-23.md, H1). Un nodo sin
# historial no puede ser "segunda opinion" barata: mientras alguno de los dos
# tenga menos de AUDITORIA_TAREAS_CONFIANZA tareas verificadas, la tarea SE
# RECALCULA SIEMPRE (AUDITORIA_1_DE_JOVEN = 1). Solo cuesta CPU al arrancar
# nodos nuevos; a partir de ahi se vuelve al muestreo normal (1 de cada 10).
# Se puede suavizar sin tocar codigo: KUKIA_AUDITORIA_1_DE_JOVEN=3 (1 de cada 3).
AUDITORIA_1_DE_JOVEN = max(1, int(os.environ.get("KUKIA_AUDITORIA_1_DE_JOVEN", "1")))
AUDITORIA_TAREAS_CONFIANZA = max(0, int(os.environ.get("KUKIA_AUDITORIA_TAREAS_CONFIANZA", "20")))
# Techo de nodos verificando A LA VEZ (fraccion de los activos). Sin techo,
# todos los nodos libres cogian replicas, nadie empezaba tareas nuevas y la
# cola se paraba por turnos: se ejecutaba todo, luego se verificaba todo.
REPLICA_FRACCION = min(1.0, max(0.0, float(os.environ.get("KUKIA_REPLICA_FRACCION", "0.5"))))
# Una replica reclamada y no entregada en este tiempo se libera.
REPLICA_TTL = max(10.0, float(os.environ.get("KUKIA_REPLICA_TTL", "120")))
# Margen que espera el Core a la replica cuando llega el primario. El
# verificador empieza ~1 s despues (hay que asignarle la tarea), asi que sin
# este margen el Core recalculaba SIEMPRE y el trabajo del verificador se
# tiraba: medido en la red el 20-10-2026, 131 replicas entregadas y 0 pagadas.
REPLICA_ESPERA_SEG = max(0.0, float(os.environ.get("KUKIA_REPLICA_ESPERA", "3")))
# Mismo umbral que la medida de potencia: por debajo, la red pesa mas que la GPU.
REPLICA_FLOPS_MIN = 1e9
# El tunel de Cloudflare conecta desde 127.0.0.1 y pone CF-Connecting-IP.
# Solo se confia en esa cabecera si la conexion es local (nunca desde LAN).
CONFIA_PROXY_LOCAL = os.environ.get("KUKIA_CONFIA_PROXY", "1") != "0"

# ============================================================
#  AUDITORIA: limites y castigos
# ============================================================
LIMITE_N = 4096            # maximo de 'n' en cualquier tarea
LIMITE_PASOS = 20000       # maximo de 'pasos' (difusion)
# AUDITORIA 2026-10-20: el control de dificultad llevo las tareas de difusion
# a n=1024/pasos=20000 (~2e10 operaciones float64). El minero y el Core
# recalculan lo mismo, asi que una tarea al limite bloquea ambos procesos.
# Presupuesto duro de operaciones por tarea (n*n*pasos), configurable.
LIMITE_OPS_DIFUSION = max(1_000_000, int(os.environ.get("KUKIA_MAX_OPS_DIFUSION", "2000000000")))
LIMITE_PARAMS_BYTES = 65536  # el JSON de params no puede pasar de 64 KB
# AUDITORIA 2026-10-20 (minero): un POST con un cuerpo gigante podia agotar
# memoria antes de llegar a la validacion de params. 256 KB cubre de sobra
# cualquier peticion legitima (params 64 KB + firma + telemetria).
MAX_CUERPO_BYTES = 262144
MAX_FALLOS = 3             # resultados INCORRECTOS antes de cerrar la tarea
MAX_ABANDONOS = 3          # abandonos antes de cerrar la tarea como fallo
PENALIZACION = 25.0        # TOPE de KIA que se le quitan por un resultado malo
PENALIZACION_MULT = 100.0  # castigo = 100 x el premio de una tarea (proporcional)
PENALIZACION_MIN = 0.1     # suelo del castigo (evita castigos ridiculos)
CASTIGO_SEG = 1800.0       # tiempo sin poder pedir tareas tras sabotear
PENDIENTE_EXIGIR_FIRMA = True
VENTANA_FIRMA = 300.0      # segundos de validez de la firma de 'pendiente'

# --- AUDITORIA 2026-10-20 (Sybil): el registro debe probar la clave ----------
# Antes cualquiera podia registrar nodos con una clave publica ajena (sin
# demostrar que tiene la privada). No permitia robar, pero llenaba la red de
# identidades y preparaba ataques de cola/dificultad. Ahora el Core da un reto
# de un solo uso y el nodo lo firma. Se puede desactivar solo para migrar
# mineros antiguos: KUKIA_REGISTRO_FIRMA=0.
EXIGIR_PRUEBA_REGISTRO = os.environ.get("KUKIA_REGISTRO_FIRMA", "1") != "0"
RETO_TTL = 300.0
_retos_registro = {}
_retos_lock = threading.Lock()

# --- AUDITORIA 2026-10-20: verificacion acotada -----------------------------
# Un POST minusculo con un resultado falso obliga a recalcular la tarea entera.
# Con 40 hilos de FastAPI, 40 entregas falsas a la vez pueden saturar CPU o
# GPU. Un semaforo limita cuantas verificaciones caras corren a la vez; si no
# hay hueco, la tarea vuelve a la cola SIN castigar al nodo.
VERIF_MAX = max(1, int(os.environ.get("KUKIA_VERIF_MAX", "2")))
VERIF_TIMEOUT = max(0.0, float(os.environ.get("KUKIA_VERIF_TIMEOUT", "5")))
_verif_sem = threading.BoundedSemaphore(VERIF_MAX)
_extra = [h.strip().lower() for h in (os.environ.get("KUKIA_HOSTS", "") or "").split(",") if h.strip()]
# La verificacion de 'difusion_calor' en CPU costaba 7,4 min por tarea pesada
# (habia 97 asi en la cola). Con la GPU baja a ~1 s. Sigue siendo un recalculo
# INDEPENDIENTE del minero; solo cambia donde se hace la aritmetica float64.
# Se puede desactivar con KUKIA_VERIF_GPU=0.
USAR_GPU_VERIF = os.environ.get("KUKIA_VERIF_GPU", "1") != "0"
HOSTS_PERMITIDOS = {"127.0.0.1", "localhost", "::1", "0.0.0.0"} | set(_extra)
# ENSAYO DE FAILOVER 2026-10-22: el Core solo aceptaba loopback + KUKIA_HOSTS,
# asi que al arrancar con la replica (o con un tunel nuevo) TODO lo que llegaba
# por el tunel publico devolvia 403 "Host no permitido" y la red parecia caida
# aunque estuviera sirviendo. Ahora se aceptan los dominios de los tuneles de
# Cloudflare por SUFIJO, que es la puerta publica documentada del proyecto.
# No abre un agujero: esos nombres resuelven en Cloudflare y solo llegan aqui a
# traves de NUESTRO tunel; no se pueden apuntar a 127.0.0.1 como el DNS
# rebinding que corta esta guardia. Se puede vaciar con KUKIA_HOSTS_SUFIJOS="".
SUFIJOS_HOST_PERMITIDOS = tuple(
    s.strip().lower().lstrip(".")
    for s in (os.environ.get("KUKIA_HOSTS_SUFIJOS",
                             "trycloudflare.com,cfargotunnel.com") or "").split(",")
    if s.strip())

def host_permitido(host: str) -> bool:
    if host in HOSTS_PERMITIDOS:
        return True
    return any(host == s or host.endswith("." + s) for s in SUFIJOS_HOST_PERMITIDOS)

# AUDITORIA 2026-10-19: la billetera web consulta el saldo desde el navegador.
# Antes el guardia solo aceptaba el Origin con el puerto DEL CORE (8300), asi
# que una pagina local en otro puerto (o GitHub Pages) recibia 403; y ademas
# faltaban las cabeceras CORS, con lo que el fetch fallaba igualmente.
# Ahora: vale cualquier origen de loopback (el puerto da igual), la web
# publica del proyecto, y lo que se anada en KUKIA_ORIGENES="https://a,...".
# WEBS PROPIAS (22-10-2026): la billetera del navegador vive en el sitio del
# proyecto. Sin estas lineas, quien abriera wallet.html en
# kukia-network.surge.sh no podia ni mirar su saldo ni gastar: el navegador
# bloqueaba el fetch por CORS. No abre nada: /transferir exige firma Ed25519 de
# la clave que deriva en la direccion, y el resto de escrituras exigen firma o
# token de admin.
ORIGENES_PERMITIDOS = {
    "https://lordkukone.github.io",
    "https://kukia-network.surge.sh",
    "https://kukia-network.freebuff.page",
    "https://site-1ea296595daa4ba7935f1574ab9a5a13.freebuff.page",
    "null",   # wallet.html abierto desde el disco (file://)
} | {
    o.strip().lower() for o in (os.environ.get("KUKIA_ORIGENES", "") or "").split(",") if o.strip()
}

def _origen_ok(origen: str) -> bool:
    if origen in ORIGENES_PERMITIDOS:
        return True
    try:
        u = urllib.parse.urlsplit(origen)
    except Exception:
        return False
    if u.scheme not in ("http", "https"):
        return False
    return (u.hostname or "").lower() in {"127.0.0.1", "localhost", "::1"}

# CUANTO ESPERAR si otro programa tiene la base cogida (25-10-2026)
# Antes: 30 s por hilo. Medido con una herramienta ajena leyendo el ledger
# (una auditoria, una copia, /publico/verificar_sellos): el telemetraje y el
# cobro de una transferencia se quedaron esperando el turno uno detras de
# otro, la transferencia tardo 64,8 s y contesto 500 "error interno en
# transferencia", que es mentira y ademas el cliente no sabe que reintentar.
# Con 5 s (configurable con KUKIA_BUSY_TIMEOUT_MS) se contesta 503 "ocupado,
# reintentar" en segundos y el cliente sabe que no se ha movido nada.
def _espera_bloqueo_s() -> float:
    texto = (os.environ.get("KUKIA_BUSY_TIMEOUT_MS") or "").strip()
    try:
        ms = int(texto)
    except Exception:
        ms = 5000
    return max(0.2, min(60.0, ms / 1000.0))

class _ConSegura:
    """Una conexion SQLite POR HILO (antes: una sola compartida por todos).

    FALLO REAL (21-10-2026, 404 y 500 esporadicos con el nodo existiendo):
    todos los hilos usaban la MISMA conexion. En SQLite, un ROLLBACK (o un
    BEGIN IMMEDIATE) ejecutado en un hilo corta las sentencias que estan en
    vuelo en esa conexion, aunque las haya lanzado otro hilo: el
    `fetchone()` de la otra peticion vuelve None sin lanzar nada. Eso daba
    - 404 "nodo no registrado" con el nodo existiendo (el SELECT del nodo
      salia vacio en mitad de un ROLLBACK ajeno),
    - 404 "tarea no encontrada" con la tarea existiendo,
    - 500 "error interno en el pago" ('NoneType' object is not subscriptable)
      cuando la lectura de `task2` se cortaba igual.

    Reproducido en aislado con una conexion compartida y dos hilos (uno
    leyendo, otro haciendo BEGIN/ROLLBACK): 2.877 de 42.324 lecturas (6,8 %)
    devolvieron None con la fila perfectamente presente.

    Con una conexion POR HILO es imposible: el rollback de un hilo no toca la
    sentencia de otro. Las escrituras siguen serializadas con _pago_lock +
    BEGIN IMMEDIATE, y busy_timeout (30 s) absorbe el turno de escritura.

    MODO DE DIARIO (25-10-2026): se pasa a WAL al arrancar (ver `_poner_en_wal`).
    Antes era DELETE a proposito para que el .db se pudiera copiar suelto, pero
    eso costaba caro: con un lector ajeno encima, un pago contestaba 503 a los
    5,5 s. En WAL el lector no frena al escritor. A cambio, el .db YA NO se
    puede copiar suelto: se copia con VACUUM INTO o con la API backup().
    """

    def __init__(self, ruta):
        self._ruta = str(ruta)
        self._row_factory = sqlite3.Row
        self._lk = threading.Lock()

    def _con(self) -> sqlite3.Connection:
        conexion = getattr(_local_bd, "con", None)
        if conexion is None:
            with self._lk:
                conexion = getattr(_local_bd, "con", None)
                if conexion is None:
                    espera = _espera_bloqueo_s()
                    conexion = sqlite3.connect(self._ruta, timeout=espera)
                    conexion.row_factory = self._row_factory
                    # Esperar un poco y, si la base sigue cogida por otro
                    # programa, contestar 503 "ocupado" en seguida (ver
                    # _espera_bloqueo_s): esperar 30 s por hilo convertia un
                    # bloqueo ajeno en una peticion de 65 s que acababa en 500.
                    conexion.execute(f"PRAGMA busy_timeout={int(espera * 1000)}")
                    # SOLO PRUEBAS (tools/pruebas_caos.py, seccion DISCO LLENO):
                    # pone un tope de paginas a la base para simular un disco
                    # lleno sin llenar el disco. SQLite responde SQLITE_FULL
                    # ("database or disk is full") al pasar del tope. Sin la
                    # variable no hace nada.
                    tope = (os.environ.get("KUKIA_DB_MAX_PAGINAS") or "").strip()
                    if tope:
                        try:
                            conexion.execute(f"PRAGMA max_page_count={int(tope)}")
                        except Exception as e:
                            print(f"[KUKIA] AVISO: tope de paginas ignorado ({e})", flush=True)
                    _local_bd.con = conexion
        return conexion

    def execute(self, sql, parametros=()):
        return self._con().execute(sql, parametros)

    def executemany(self, sql, seq):
        return self._con().executemany(sql, seq)

    def executescript(self, sql):
        return self._con().executescript(sql)

    def en_transaccion(self) -> bool:
        """¿Esta conexion (la de este hilo) tiene una transaccion abierta?

        Lo necesita la bolsa: si el pago ya abrio BEGIN IMMEDIATE, la
        escritura se suma a esa transaccion (y su ROLLBACK la deshace); si
        no hay ninguna, la abre y la cierra ella misma para no dejar la base
        bloqueada al siguiente BEGIN.
        """
        return bool(self._con().in_transaction)

    def commit(self):
        return self._con().commit()

    def rollback(self):
        return self._con().rollback()

    def close(self):
        conexion = getattr(_local_bd, "con", None)
        if conexion is not None:
            conexion.close()
            _local_bd.con = None

    @property
    def row_factory(self):
        return self._row_factory

    @row_factory.setter
    def row_factory(self, valor):
        self._row_factory = valor
        conexion = getattr(_local_bd, "con", None)
        if conexion is not None:
            conexion.row_factory = valor

con = _ConSegura(DB)
con.row_factory = sqlite3.Row

def _disco_lleno(e) -> bool:
    """¿La escritura fallo porque no queda espacio? (SQLITE_FULL / disco lleno).

    Distinguirlo importa: "no habia espacio" no es un fallo interno del Core.
    El minero o la billetera tienen que saber que NO se ha movido nada y que
    pueden reintentar cuando haya sitio, no que el ledger este roto.
    """
    t = str(e).lower()
    return ("disk is full" in t or "database or disk is full" in t
            or "sqlite_full" in t)


def _base_ocupada(e) -> bool:
    """¿La escritura no entro porque OTRO programa tiene la base cogida?

    No es un fallo interno: no se ha escrito nada y se puede reintentar. Con
    el ledger leido por una auditoria o una copia, esto pasaba a menudo y se
    contestaba 500 "error interno" (medido el 25-10-2026, 64,8 s de espera
    para acabar en un 500).
    """
    t = str(e).lower()
    return ("database is locked" in t or "database table is locked" in t
            or "database schema is locked" in t or "database is busy" in t
            or "sqlite_busy" in t)


# Columnas cuya UNIQUE significa de verdad "ese nonce ya se uso". sqlite lo
# dice en el propio mensaje: "UNIQUE constraint failed: transferencias.de_nodo,
# transferencias.nonce".
_UNIQUE_TRANSFERENCIA = ("de_nodo", "nonce")


def _nonce_repetido(e) -> bool:
    """¿Este IntegrityError es la repeticion del nonce, o otra cosa?

    sqlite3.IntegrityError es una familia entera: UNIQUE, NOT NULL, CHECK,
    FOREIGN KEY y los RAISE() de un trigger. Solo la UNIQUE (de_nodo, nonce)
    significa "nonce ya usado"; el resto son fallos de escritura y contarlos
    como repeticion deja al que reintenta dando vueltas con otro nonce y con el
    fallo real sin escribir en ningun sitio (27-10-2026).
    """
    texto = str(e).lower()
    if "unique constraint failed" not in texto:
        return False
    columnas = texto.split("unique constraint failed", 1)[1]
    return all(c in columnas for c in _UNIQUE_TRANSFERENCIA)


def _error_escritura(e, defecto="error interno escribiendo"):
    if _disco_lleno(e):
        return HTTPException(507, "sin espacio en disco: no se ha escrito nada, "
                                  "reintentar cuando haya espacio")
    if _base_ocupada(e):
        return HTTPException(503, "Core ocupado: otro programa esta leyendo el "
                                  "ledger; no se ha escrito nada, reintentar",
                             headers={"Retry-After": "2"})
    return HTTPException(500, defecto)


def _poner_en_wal() -> str:
    """Pone la base en modo WAL al arrancar (25-10-2026). Devuelve el modo final.

    POR QUE (medido en tools/estudio_wal.py):
      - En DELETE, cualquier otro programa leyendo el ledger (una auditoria,
        una copia, verificar_sellos) bloquea las escrituras: un pago tardaba
        5,5 s y contestaba 503.
      - En WAL, el mismo pago con el mismo lector encima: 200 en 0,0 s.

    LO QUE HAY QUE SABER:
      - En WAL lo ultimo vive en `<db>-wal`. Copiar SOLO el .db pierde esos
        asientos y el `quick_check` sigue diciendo "ok": se pierde EN SILENCIO.
        Se copia con VACUUM INTO (tools/copia_seguridad.py) o con la API
        backup() (tools/backup_ledger.py), que si se llevan el -wal.
      - Las copias ANTIGUAS no se rompen: el modo va en la cabecera de cada
        fichero. Probado: una copia de backups/ del 25-10 arranca como Core.
      - Si no se pudiera poner en WAL, el Core sigue arrancando como antes: se
        avisa en el log y se devuelve el modo que haya.
    """
    try:
        modo = str(con.execute("PRAGMA journal_mode=WAL").fetchone()[0]).lower()
    except Exception as e:
        print(f"[KUKIA] AVISO: no se pudo mirar el modo de diario ({e})", flush=True)
        return ""
    if modo == "wal":
        try:
            # ~4 MB de -wal antes de volcar a la base (no crece sin freno).
            con.execute("PRAGMA wal_autocheckpoint=1000")
        except Exception:
            pass
    else:
        print(f"[KUKIA] AVISO: la base NO se puso en WAL (dice '{modo}'); "
              f"sigue como antes", flush=True)
    return modo


def _rollback_seguro() -> None:
    """ROLLBACK que nunca tapa el error de verdad.

    AUDITORIA 24-10-2026 (medido en tools/pruebas_caos.py, seccion G): cuando
    una escritura falla por falta de espacio, SQLite DESHACE la transaccion
    el solo. El ROLLBACK que se lanzaba al recoger el error contestaba a su
    vez "cannot rollback - no transaction is active" y ESA era la excepcion
    que llegaba arriba: el Core devolvia 500 "error interno" en vez de 507
    "sin espacio" (15 de 40 transferencias dieron 500 y 0 dieron 507). El
    minero se quedaba sin saber que podia reintentar.
    """
    try:
        con.execute("ROLLBACK")
    except Exception:
        pass


@contextmanager
def _escritura():
    """Escritura corta y atomica: lock + BEGIN IMMEDIATE + commit.

    AUDITORIA: /nodos/registrar, /tareas/crear y /registro escribian sin
    lock (dos peticiones a la vez podian mezclar transacciones).
    """
    with _pago_lock:
        # AUDITORIA: si otro programa tiene la base bloqueada, se reintenta una
        # vez y se responde 503 rapido (antes se quedaba esperando 30 s).
        listo = False
        for intento in (1, 2):
            try:
                con.execute("BEGIN IMMEDIATE")
                listo = True
                break
            except Exception:
                if intento == 1:
                    time.sleep(0.3)
        if not listo:
            raise HTTPException(503, "Core ocupado (la base esta bloqueada), reintentar")
        try:
            yield
            con.commit()
        except HTTPException:
            try:
                _rollback_seguro()
            except Exception:
                pass
            raise
        except Exception as e:
            try:
                _rollback_seguro()
            except Exception:
                pass
            raise _error_escritura(e)

def schema():
    con.execute("CREATE TABLE IF NOT EXISTS nodos(id TEXT PRIMARY KEY, nombre TEXT, vram_gb INT, "
                "tier REAL, reputacion REAL DEFAULT 1.0, ref TEXT, tareas_ok INT DEFAULT 0, "
                "creado TEXT, tops REAL DEFAULT 0)")
    con.execute("CREATE TABLE IF NOT EXISTS tareas(id TEXT PRIMARY KEY, tipo TEXT, params TEXT, "
                "estado TEXT DEFAULT 'queued', nodo_id TEXT, resultado TEXT, hash TEXT, "
                "metrica TEXT, creado TEXT, fin TEXT, verificado INT DEFAULT 0, doble TEXT)")
    con.execute("CREATE TABLE IF NOT EXISTS ledger(id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "tarea_id TEXT, nodo_id TEXT, kuki REAL, motivo TEXT, ts TEXT)")
    # AUDITORIA 2026-10-20: totales materializados. /stats y /saldos recorrian
    # todo el ledger en cada llamada; con millones de asientos eso no escala.
    con.execute("CREATE TABLE IF NOT EXISTS saldos_cuenta(cuenta TEXT PRIMARY KEY, "
                "total REAL NOT NULL DEFAULT 0)")
    con.execute("CREATE TABLE IF NOT EXISTS registros(id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "nombre TEXT, email TEXT, gpu TEXT, tops TEXT, ceder TEXT, ip TEXT, ts TEXT)")
    # CLIENTES (24-10-2026): la puerta de pago. Cada cliente tiene una clave de
    # API (se guarda SOLO el hash) y una direccion KUK que firma los prepagos.
    # Sin direccion no puede pagar, y sin pago no se le encola nada.
    con.execute("CREATE TABLE IF NOT EXISTS clientes(id TEXT PRIMARY KEY, nombre TEXT, "
                "token_hash TEXT UNIQUE, direccion TEXT, creado TEXT, activo INT DEFAULT 1, "
                "tareas INT DEFAULT 0, gastado REAL DEFAULT 0)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_clientes_token ON clientes(token_hash)")
    # INGRESOS REALES EN EUROS (25-10-2026). Una fila por trabajo cobrado fuera de
    # la red. `huella` es unica: el mismo trabajo no se puede anotar dos veces
    # (contar dos veces el primer euro seria mentir en la unica cifra que importa).
    # `asiento_hash` apunta al asiento de 0 KIA del ledger que lo respalda: si el
    # asiento desaparece o se cambia, la cadena y los sellos lo delatan.
    con.execute("CREATE TABLE IF NOT EXISTS ingresos(id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "huella TEXT UNIQUE, servicio TEXT, cliente TEXT, trabajo TEXT, "
                "minutos REAL, precio_eur REAL, detalle TEXT, fecha TEXT, "
                "asiento_id INTEGER, asiento_hash TEXT)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_ingresos_fecha ON ingresos(fecha)")
    # Cupo anti-Sybil de registros (IP + clave publica), persistente.
    con.execute("CREATE TABLE IF NOT EXISTS registros_nodo(ip TEXT, clave_publica TEXT, ts REAL)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_registros_nodo ON registros_nodo(ip, ts)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_registros_nodo_pub ON registros_nodo(clave_publica, ts)")
    try:
        con.execute("ALTER TABLE nodos ADD COLUMN tops REAL DEFAULT 0")
    except Exception:
        pass
    try:
        con.execute("ALTER TABLE nodos ADD COLUMN clave_publica TEXT DEFAULT ''")
    except Exception:
        pass
    try:
        con.execute("ALTER TABLE tareas ADD COLUMN asignado REAL DEFAULT NULL")
    except Exception:
        pass
    for _sql in (
        "ALTER TABLE tareas ADD COLUMN cola TEXT DEFAULT 'B'",
        "ALTER TABLE tareas ADD COLUMN campana TEXT DEFAULT ''",
        "ALTER TABLE tareas ADD COLUMN prioridad REAL DEFAULT 0",
        "ALTER TABLE tareas ADD COLUMN vram_min INT DEFAULT 0",
        "ALTER TABLE tareas ADD COLUMN device TEXT DEFAULT ''",
        "ALTER TABLE tareas ADD COLUMN duracion_ms REAL DEFAULT 0",
        # AUDITORIA: reintentos, castigos y cadena de hashes
        "ALTER TABLE tareas ADD COLUMN intentos INT DEFAULT 0",
        "ALTER TABLE tareas ADD COLUMN nodos_intento TEXT DEFAULT ''",
        "ALTER TABLE nodos ADD COLUMN castigado_hasta REAL DEFAULT 0",
        "ALTER TABLE nodos ADD COLUMN fallos INT DEFAULT 0",
        "ALTER TABLE ledger ADD COLUMN hash_prev TEXT DEFAULT ''",
        "ALTER TABLE ledger ADD COLUMN hash TEXT DEFAULT ''",
        "ALTER TABLE registros ADD COLUMN ref TEXT DEFAULT ''",
        # Puerta de cliente: quien pidio la tarea, cuanto dejo en deposito y el
        # nonce del prepago (unico, para que no se pueda reutilizar la firma).
        "ALTER TABLE tareas ADD COLUMN cliente TEXT DEFAULT ''",
        "ALTER TABLE tareas ADD COLUMN prepago REAL DEFAULT 0",
        "ALTER TABLE tareas ADD COLUMN nonce_cliente TEXT DEFAULT ''",
        "ALTER TABLE tareas ADD COLUMN precio_cliente REAL DEFAULT 0",
        # Lo que se pago de verdad al cerrar (puede ser menos que el deposito).
        "ALTER TABLE tareas ADD COLUMN pagado REAL DEFAULT 0",
        "ALTER TABLE nodos ADD COLUMN direccion_cobro TEXT DEFAULT ''",
        # Ultima vez que el nodo pidio/entrego trabajo: para no contar en la
        # dificultad los nodos muertos (TOPS autodeclarados y fantasma).
        "ALTER TABLE nodos ADD COLUMN visto REAL DEFAULT 0",
        # POTENCIA DEMOSTRADA (21-10-2026): GFLOPS medidos con el reloj del Core
        # sobre trabajo verificado, y cuantas muestras los respaldan. Es lo que
        # decide si la prima por tier (x1,3 / x1,6) se paga o se anula.
        "ALTER TABLE nodos ADD COLUMN gflops_medido REAL DEFAULT 0",
        "ALTER TABLE nodos ADD COLUMN potencia_muestras INT DEFAULT 0",
        # UN NOMBRE, UNA IDENTIDAD (22-10-2026): cada vez que un minero se
        # registraba otra vez (billetera nueva, o el worker pidiendo identidad)
        # se insertaba una fila nueva con el mismo nombre. En un dia habia 8
        # nombres repetidos y el registro decia 34 nodos donde habia 20.
        # Los sobrantes se marcan retirado=1: NO se borran (sus tareas y sus
        # pagos siguen en el ledger), solo dejan de contar y de salir en listas.
        "ALTER TABLE nodos ADD COLUMN retirado INT DEFAULT 0",
        # DISPOSITIVO DECLARADO (23-10-2026): el worker dice si mina con
        # 'cuda:0' o con 'cpu'. El planificador necesita saberlo para no
        # mandarle difusiones a una CPU (0,8 s en GPU contra 17,3 s en CPU).
        "ALTER TABLE nodos ADD COLUMN device TEXT DEFAULT ''",
        # FIRMA GUARDADA (23-10-2026, auditoria de la billetera): la firma
        # Ed25519 se comprobaba al entrar y se tiraba. Con esto queda en el
        # registro y un auditor puede volver a verificarla meses despues sin
        # creerse a nadie. Las transferencias de antes de hoy no la tienen
        # (columna vacia): eso se dice, no se finge.
        "ALTER TABLE transferencias ADD COLUMN firma TEXT DEFAULT ''",
        "ALTER TABLE transferencias ADD COLUMN clave_publica TEXT DEFAULT ''",
    ):
        try:
            con.execute(_sql)
        except Exception:
            pass
    con.execute("CREATE TABLE IF NOT EXISTS planificador_log(id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "tarea_id TEXT, nodo_id TEXT, cola TEXT, motivo TEXT, ts TEXT)")
    con.execute("CREATE TABLE IF NOT EXISTS gpu_pruebas(nodo_id TEXT, prueba TEXT, creado TEXT)")
    # EJECUCION REDUNDANTE (21-10-2026): una fila por tarea replicada. Una sola
    # replica por tarea (PRIMARY KEY) para que dos nodos no hagan el mismo
    # trabajo de verificacion y luego cobren los dos.
    con.execute("CREATE TABLE IF NOT EXISTS tareas_replica(tarea_id TEXT PRIMARY KEY, "
                "nodo_id TEXT, estado TEXT DEFAULT 'reclamando', metrica TEXT, "
                "firma TEXT, device TEXT, duracion_ms REAL, asignado REAL, ts REAL)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_replica_nodo ON tareas_replica(nodo_id)")
    # AUDITORIA 2026-10-20: anti-replay de 'pendiente' que sobrevive a reinicios.
    con.execute("CREATE TABLE IF NOT EXISTS nonces_pendiente(nodo_id TEXT, nonce TEXT, ts REAL, "
                "PRIMARY KEY(nodo_id, nonce))")
    con.execute("CREATE INDEX IF NOT EXISTS idx_nonces_ts ON nonces_pendiente(ts)")
    # AUDITORIA 2026-10-20 (GPU en vivo): estado actual por nodo/GPU, sin
    # crecer como la telemetria historica. El worker manda un latido firmado
    # cada 3 s y el panel lo pinta en la tarjeta de cada GPU.
    con.execute("CREATE TABLE IF NOT EXISTS gpu_estado(nodo_id TEXT, device TEXT, "
                "metricas TEXT, ts REAL, origen TEXT DEFAULT 'live', "
                "PRIMARY KEY(nodo_id, device))")
    con.execute("CREATE TABLE IF NOT EXISTS gpu_telemetria(id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "nodo_id TEXT, device TEXT, metricas TEXT, ts TEXT, origen TEXT)")
    try:
        con.execute("ALTER TABLE nodos ADD COLUMN tops_estimado INT DEFAULT 1")
    except Exception:
        pass
    # AUDITORIA: con el generador de trabajo la tabla 'tareas' crece sola
    # (miles al dia). Sin indices, los COUNT(*) de la cola y los saldos
    # acabarian recorriendo toda la tabla.
    # ARCHIVADO (18-10-2026): la tabla 'tareas' crece con el generador
    # automatico (~500.000 filas al dia a pleno ritmo). Para poder borrar las
    # tareas viejas sin tocar el dinero ni el ritmo de emision, los totales
    # historicos viven en 'meta' y NO se recalculan contando filas.
    con.execute("CREATE TABLE IF NOT EXISTS meta(clave TEXT PRIMARY KEY, valor REAL)")
    # VALIDADORES INDEPENDIENTES (20-10-2026): cada validador recalcula las
    # tareas verificadas y manda una atestacion firmada. Se guarda el historial
    # para el panel; NO se paga ni se bloquea nada con esto.
    con.execute("CREATE TABLE IF NOT EXISTS validador_atestaciones("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, validador TEXT, clave_publica TEXT, "
                "desde REAL, hasta REAL, revisadas INTEGER, ok INTEGER, discrepancias INTEGER, "
                "detalle TEXT, huella TEXT, ts REAL, firma TEXT)")
    if con.execute("SELECT valor FROM meta WHERE clave='tareas_ok_total'").fetchone() is None:
        _ok = con.execute("SELECT COUNT(*) c FROM tareas WHERE estado='ok'").fetchone()["c"]
        _prim = con.execute("SELECT MIN(fin) m FROM tareas WHERE estado='ok' AND fin IS NOT NULL").fetchone()["m"]
        try:
            _prim_e = float(time.mktime(time.strptime(_prim))) if _prim else float(time.time())
        except Exception:
            _prim_e = float(time.time())
        con.execute("INSERT INTO meta(clave,valor) VALUES('tareas_ok_total',?)", (float(_ok),))
        con.execute("INSERT OR IGNORE INTO meta(clave,valor) VALUES('primera_ok_epoch',?)", (_prim_e,))
        con.execute("INSERT OR IGNORE INTO meta(clave,valor) VALUES('tareas_archivadas',0)")
    for _ix in (
        "CREATE INDEX IF NOT EXISTS idx_tareas_estado ON tareas(estado)",
        "CREATE INDEX IF NOT EXISTS idx_tareas_cola ON tareas(estado, cola, prioridad)",
        "CREATE INDEX IF NOT EXISTS idx_tareas_nodo ON tareas(nodo_id)",
        "CREATE INDEX IF NOT EXISTS idx_tareas_campana ON tareas(campana)",
        "CREATE INDEX IF NOT EXISTS idx_tareas_creado ON tareas(creado)",
        "CREATE INDEX IF NOT EXISTS idx_ledger_nodo ON ledger(nodo_id)",
        "CREATE INDEX IF NOT EXISTS idx_ledger_motivo ON ledger(motivo)",
        "CREATE INDEX IF NOT EXISTS idx_val_atest ON validador_atestaciones(validador, ts)",
        "CREATE INDEX IF NOT EXISTS idx_val_atest_ts ON validador_atestaciones(ts)",
    ):
        try:
            con.execute(_ix)
        except Exception:
            pass
    con.commit()

schema()

# MODO DE DIARIO: WAL desde el 25-10-2026 (lectores ajenos ya no frenan los
# pagos). Ver `_poner_en_wal`. Si el cambio no entra, se sigue en DELETE.
_MODO_DIARIO = _poner_en_wal()
if _MODO_DIARIO == "wal":
    print("[KUKIA] base en diario WAL (una auditoria o una copia ya no frenan "
          "los pagos)", flush=True)


def podar_nodos(horas: float = None) -> int:
    """Borra nodos que no tienen dinero ni trabajo pagado.

    Dos casos, ambos SIN asientos en el ledger (nada de dinero se toca):
      - nunca pidieron trabajo (`visto=0`), tras `horas` (24 h por defecto);
      - pidieron trabajo pero no completaron NINGUNA tarea pagada y llevan
        NODO_SIN_TRABAJO_H (6 h) SIN aparecer. Esto ultimo cierra el Sybil que
        pide cola y nunca entrega: no puede dejar tareas inmovilizadas para
        siempre. Se mide desde la ultima actividad, no desde el registro: un
        nodo que esta trabajando no se poda ni aunque no haya cobrado aun.
    Un worker que vuelva se registra otra vez: su identidad vieja ya no existe
    y el propio worker lo detecta con /nodos/{id}.
    """
    try:
        horas = float(os.environ.get("KUKIA_PODA_NODOS_H", "24")) if horas is None else float(horas)
    except Exception:
        horas = 24.0
    if horas <= 0:
        return 0
    ahora = time.time()
    corte = ahora - horas * 3600.0
    corte_nuevo = ahora - NODO_SIN_TRABAJO_H * 3600.0
    candidatos = []
    for f in con.execute("SELECT id, creado, COALESCE(visto,0) visto, "
                         "COALESCE(tareas_ok,0) ok FROM nodos").fetchall():
        try:
            creado = time.mktime(time.strptime(f["creado"]))
        except Exception:
            continue
        if f["visto"] and f["ok"]:
            continue
        # AUDITORIA 21-10-2026 (404 "nodo no registrado" con el nodo vivo): el
        # segundo caso medía la antiguedad desde el REGISTRO, no desde la ultima
        # actividad. Un nodo llevaba 7 h registrado, pidiendo trabajo cada segundo
        # y sin cobrar todavia (tareas lentas o solo replicas), y se borraba
        # mientras estaba trabajando: el siguiente /tareas/pendiente ya daba 404
        # con el nodo perfectamente vivo. Ahora se mira cuando se le vio por
        # ultima vez: si esta activo no se poda, nunca.
        limite = corte if not f["visto"] else corte_nuevo
        referencia = creado if not f["visto"] else float(f["visto"])
        if referencia >= limite:
            continue
        if con.execute("SELECT 1 FROM ledger WHERE nodo_id=? LIMIT 1", (f["id"],)).fetchone():
            continue
        candidatos.append(f["id"])
        if len(candidatos) >= 500:
            break
    if not candidatos:
        return 0
    try:
        with _escritura():
            for nid in candidatos:
                # Sus tareas vuelven a la cola antes de borrar la identidad.
                con.execute("UPDATE tareas SET estado='queued', nodo_id=NULL, asignado=NULL "
                            "WHERE nodo_id=? AND estado='ejecutando'", (nid,))
                con.execute("DELETE FROM nodos WHERE id=? AND COALESCE(tareas_ok,0)=0", (nid,))
                con.execute("DELETE FROM gpu_estado WHERE nodo_id=?", (nid,))
                con.execute("DELETE FROM gpu_telemetria WHERE nodo_id=?", (nid,))
        return len(candidatos)
    except Exception as e:
        print(f"[KUKIA] no se pudieron podar nodos: {e}", flush=True)
        return 0

_n_podados = podar_nodos()
if _n_podados:
    print(f"[KUKIA] podados {_n_podados} nodos que nunca trabajaron", flush=True)


def retirar_nodos_duplicados(con) -> int:
    """Deja UNA identidad viva por nombre; las demas quedan retiradas.

    No borra: la fila vieja conserva sus tareas, su reputacion y sus pagos (el
    ledger apunta a `nodo_id`, borrarla dejaria asientos huerfanos). Se queda
    la que mas ha trabajado y, a igualdad, la vista mas recientemente.

    """
    try:
        filas = con.execute(
            "SELECT id, nombre FROM nodos WHERE COALESCE(retirado,0)=0 "
            "ORDER BY COALESCE(tareas_ok,0) DESC, COALESCE(visto,0) DESC, creado ASC"
        ).fetchall()
    except Exception:
        return 0
    vistos = set()
    sobran = []
    for f in filas:
        nombre = (f["nombre"] or "")
        if nombre in vistos:
            sobran.append(f["id"])
        else:
            vistos.add(nombre)
    if not sobran:
        return 0
    try:
        with _escritura():
            for nid in sobran:
                con.execute("UPDATE nodos SET retirado=1 WHERE id=?", (nid,))
    except Exception as e:
        print(f"[KUKIA] no se pudieron retirar nodos duplicados: {e}", flush=True)
        return 0
    return len(sobran)


_n_retirados = retirar_nodos_duplicados(con)
if _n_retirados:
    print(f"[KUKIA] retirados {_n_retirados} nodos con nombre repetido "
          f"(un nombre, una identidad viva; no se borra nada)", flush=True)

def direccion_valida(d: str) -> bool:
    """Formato de direccion KUKIA: KUK + 38 hex (41 caracteres)."""
    d = (d or "").strip().upper()
    return len(d) == 41 and d.startswith("KUK") and all(c in "0123456789ABCDEF" for c in d[3:])

def direccion_de_pubkey(pub_hex: str) -> str:
    """Direccion KUK... derivada de una clave publica Ed25519.

    Misma regla que la billetera web y kukia_wallet.py:
        KUK + SHA256(clave_publica)[:38] en mayusculas.
    Asi el Core puede comprobar que quien firma una transferencia ES el dueno
    de la direccion de origen, sin registrar la billetera como nodo.
    """
    try:
        raw = bytes.fromhex((pub_hex or "").strip())
    except ValueError:
        return ""
    if len(raw) != 32:
        return ""
    return "KUK" + hashlib.sha256(raw).hexdigest()[:38].upper()


def pubkey_valida(pub_hex: str) -> bool:
    try:
        return len(bytes.fromhex((pub_hex or "").strip())) == 32
    except ValueError:
        return False

# Caracteres que puede llevar un nombre de GPU en los paneles. Todo lo demas
# (< > comillas & barra) fuera: el nombre lo elige el minero y acaba en una
# pagina web, asi que se limpia EN LA PUERTA y no en cada consumidor.
_DEVICE_OK = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
                 "0123456789 ._:()+-")


def device_limpio(valor) -> str:
    """Nombre de GPU/tarjeta apto para mostrar (max 16, sin HTML).

    AUDITORIA 2026-10-21: /tareas/resultado guardaba el 'device' tal cual
    (solo recortado a 16) y la portada lo pintaba con innerHTML. No se podia
    explotar con un payload completo por el recorte, pero era una inyeccion
    de HTML a un clic de ser XSS: se cierra aqui y no en cada lector.
    """
    limpio = "".join(c for c in str(valor or "") if c in _DEVICE_OK).strip()
    limpio = " ".join(limpio.split())
    return (limpio or "gpu")[:16]


def _device_declarado(valor) -> str:
    """Nombre del dispositivo tal cual lo declara el nodo ('cpu', 'cuda:0'...).

    A diferencia de `device_limpio` (que es para PINTAR y cae a 'gpu'), aqui
    interesa distinguir "no lo dijo" de "dijo GPU". Vacio = registro antiguo.
    """
    limpio = "".join(c for c in str(valor or "") if c.isalnum() or c in ":_-.")
    return limpio.strip().lower()[:16]


def _nodo_es_cpu(node) -> bool:
    """True si el nodo trabaja solo con CPU.

    POR QUE EXISTE (23-10-2026): una difusion medía 0,8 s en GPU y 17,3 s en
    CPU (medido en `medidas/ensayos/cpu_gpu_30m_...`). Aun así el Core le daba
    difusiones al nodo CPU, que se pasaba el turno calculando una tarea que la
    GPU hace en un suspiro, y encima cobraba igual. Eso atasca el nodo y no
    aporta nada a la red: la difusion va a quien la hace rápido.

    Se mira lo declarado en el registro (`device`) y, para los nodos que ya
    estaban dados de alta antes de existir esa columna, el patrón con el que
    el propio worker se declara CPU: VRAM 0 y 0 TOPS.
    """
    try:
        dev = str(node["device"] or "").strip().lower()
    except Exception:
        dev = ""
    if dev:
        return dev.startswith("cpu")
    # PLAN B, EN ORDEN DE FIABILIDAD: primero el ultimo latido firmado (una GPU
    # que late con cuda:<n> es GPU aunque se registrara antes de existir la
    # columna `device`); solo si no hay latido se usa VRAM 0 + TOPS 0. Asi un
    # nodo antiguo deja de estar castigado en cuanto manda su telemetria.
    try:
        nid = node["id"] if "id" in node.keys() else ""
    except Exception:
        nid = ""
    if nid:
        try:
            fila = con.execute("SELECT device FROM gpu_estado WHERE nodo_id=? AND ts>=? "
                               "AND device LIKE 'cuda:%' ORDER BY ts DESC LIMIT 1",
                               (nid, time.time() - 900.0)).fetchone()
            if fila is not None:
                return False
        except Exception:
            pass
    try:
        return int(node["vram_gb"] or 0) == 0 and float(node["tops"] or 0.0) <= 0.0
    except Exception:
        return False


def _hay_gpu_activa(ventana: float = 300.0) -> bool:
    """¿Hay algún nodo con GPU visto en los últimos 5 minutos?

    Si no lo hay, el generador deja de fabricar difusiones: nadie podría
    servírselas y se comerían la cola (las difusiones encoladas ya no cuentan
    para el objetivo de cola cuando no hay GPU).
    """
    try:
        filas = con.execute("SELECT * FROM nodos WHERE COALESCE(visto,0) > ?",
                            (time.time() - ventana,)).fetchall()
    except Exception:
        return False
    return any(not _nodo_es_cpu(n) for n in filas)


def _firmar_texto(texto: str) -> str:
    """Firma Ed25519 con la clave del sello del Core (identidad del nodo).

    Se usa para firmar los manifiestos de trabajo que reciben los validadores:
    asi un validador sabe que lo que recalcula es lo que el Core publico, y no
    lo que le sirvio un intermediario.
    """
    if not _sello_priv:
        return ""
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        return Ed25519PrivateKey.from_private_bytes(
            bytes.fromhex(_sello_priv)).sign(texto.encode()).hex()
    except Exception:
        return ""


def pasos_max_difusion(n: int) -> int:
    """Pasos de difusion que caben en el presupuesto de operaciones."""
    n = max(1, int(n))
    return max(1, min(LIMITE_PASOS, LIMITE_OPS_DIFUSION // (n * n)))


def _sanear_difusion_pendiente() -> int:
    """Recorta tareas de difusion YA ENCOLADAS que se crearon con el limite
    viejo (hasta n=1024/pasos=20000). Sin esto, tras reiniciar el Core la
    primera tarea podria bloquear la GPU y la verificacion minutos.

    Solo toca tareas 'queued' (nadie las esta calculando). Devuelve cuantas.
    """
    cambiadas = 0
    try:
        filas = con.execute("SELECT id, params FROM tareas "
                            "WHERE tipo='difusion_calor' AND estado='queued'").fetchall()
        for f in filas:
            try:
                p = json.loads(f["params"])
                n = int(p.get("n", 256))
                pasos = int(p.get("pasos", 1500))
            except Exception:
                continue
            tope = pasos_max_difusion(n)
            if pasos > tope:
                p["pasos"] = tope
                with _escritura():
                    con.execute("UPDATE tareas SET params=? WHERE id=?",
                                (json.dumps(p), f["id"]))
                cambiadas += 1
    except Exception as e:
        print(f"[KUKIA] no se pudieron sanear tareas de difusion: {e}", flush=True)
    return cambiadas

_saneadas = _sanear_difusion_pendiente()
if _saneadas:
    print(f"[KUKIA] saneadas {_saneadas} tareas de difusion encoladas al tope actual", flush=True)


def _cuenta_de(nodo_id):
    """Cuenta del ledger a la que se paga/castiga un nodo: su direccion de
    cobro si la declaro al registrarse, y si no el propio id del nodo."""
    fila = con.execute("SELECT direccion_cobro FROM nodos WHERE id=?", (nodo_id,)).fetchone()
    d = ((fila["direccion_cobro"] if fila else "") or "").strip().upper()
    return d if direccion_valida(d) else nodo_id

def _asiento(tarea_id, nodo_id, kuki, motivo):
    """AUDITORIA: unico camino para escribir en el ledger. Encadena hashes:

        hash = sha256(tarea_id|nodo_id|kuki|motivo|ts|hash_prev)

    Asi un saldo retocado a mano en el .db deja de cuadrar con la cadena y
    se detecta con /ledger/verificar. Se llama SIEMPRE dentro del lock.
    """
    # Si el historial no cuadra (alguien edito el .db a mano o falto un sello),
    # NO se crea dinero nuevo hasta que se revise. Los asientos de 0 (rechazos,
    # castigos) si se anotan: son el rastro de lo que esta pasando.
    if LEDGER_ROTO and float(kuki) > 0:
        raise HTTPException(503, "ledger alterado: pagos bloqueados (ver /ledger/integridad)")
    fila = con.execute("SELECT hash FROM ledger WHERE hash IS NOT NULL AND hash<>'' "
                       "ORDER BY id DESC LIMIT 1").fetchone()
    prev = (fila["hash"] if fila else "") or ""
    ts = time.ctime()
    base = f"{tarea_id}|{nodo_id}|{kuki}|{motivo}|{ts}|{prev}"
    h = hashlib.sha256(base.encode()).hexdigest()
    con.execute("INSERT INTO ledger(tarea_id,nodo_id,kuki,motivo,ts,hash_prev,hash) "
                "VALUES(?,?,?,?,?,?,?)", (tarea_id, nodo_id, kuki, motivo, ts, prev, h))
    # Totales materializados en la MISMA transaccion que el asiento: si el
    # asiento se revierte, el saldo tambien. Se reconstruyen al arrancar.
    con.execute("INSERT INTO saldos_cuenta(cuenta,total) VALUES(?,?)"
                "ON CONFLICT(cuenta) DO UPDATE SET total=total+excluded.total",
                (nodo_id, float(kuki)))
    # CAOS 2026-10-22: sumar y restar deja residuos de coma flotante (-4,6e-13)
    # y la billetera los pintaba como "-0.0 KIA": una cuenta parecia en deuda.
    # No es deuda, es cero. Se limpia AQUI, en el unico camino de escritura,
    # asi que ninguna cuenta puede volver a quedar con signo negativo.
    con.execute("UPDATE saldos_cuenta SET total=0.0 WHERE cuenta=? "
                "AND total > -1e-9 AND total < 1e-9", (nodo_id,))
    con.execute("INSERT INTO meta(clave,valor) VALUES('supply_emitido',?)"
                "ON CONFLICT(clave) DO UPDATE SET valor=valor+excluded.valor",
                (float(kuki),))
    return h

# ============================================================
#  PUERTA DE CLIENTE (24-10-2026)
#
#  Un cliente es quien PIDE trabajo: la otra mitad de la red, la que faltaba.
#  Reglas de diseno, para que no rompan lo que ya funciona:
#    - La clave de API se guarda hasheada; el token en claro se ensena UNA vez.
#    - Pagar exige FIRMA Ed25519 de una billetera KUK, no solo el token: si te
#      roban el token, no te pueden gastar el saldo.
#    - El dinero del cliente se retiene ANTES de encolar (escrow). El minero
#      cobra de ahi, no de la emision: un cliente que paga NO infla el
#      suministro, porque los dos asientos se compensan.
#    - Si la tarea falla o cuesta menos, el sobrante vuelve al cliente.
# ============================================================

def _hash_token(tok: str) -> str:
    return hashlib.sha256((tok or "").strip().encode()).hexdigest()


def _cliente_por_token(tok: str):
    """Fila del cliente dueno de ese token, o None. Compara por hash."""
    if not tok or len(tok) > 200:
        return None
    h = _hash_token(tok)
    return con.execute("SELECT * FROM clientes WHERE token_hash=? AND activo=1", (h,)).fetchone()


def _crear_cliente(nombre: str, direccion: str) -> dict:
    """Da de alta un cliente y devuelve el token EN CLARO una unica vez.

    El token no se puede recuperar despues: en la base solo queda su sha256.
    """
    cid = uuid.uuid4().hex[:12]
    token = "kia_" + secrets.token_urlsafe(32)
    d = (direccion or "").strip().upper()
    if d and not direccion_valida(d):
        raise HTTPException(400, "direccion invalida (formato KUK + 38 hex)")
    # Una billetera, un cliente: dos tokens sobre el mismo saldo acabarian en
    # dos cupos de cola distintos y nadie sabria quien gasto que.
    if d and con.execute("SELECT 1 FROM clientes WHERE direccion=? AND activo=1", (d,)).fetchone():
        raise HTTPException(409, "esa billetera ya tiene un cliente activo")
    with _escritura():
        con.execute("INSERT INTO clientes(id,nombre,token_hash,direccion,creado,activo) "
                    "VALUES(?,?,?,?,?,1)",
                    (cid, (nombre or "")[:80], _hash_token(token), d, time.ctime()))
    return {"id": cid, "nombre": (nombre or "")[:80], "direccion": d,
            "token": token, "aviso": "Guarda el token: no se puede volver a ver."}


def _coste_tarea_cliente(tipo: str, params: dict) -> float:
    """Lo que costaria esta tarea para un cliente, antes de ejecutarla.

    Usa la misma tarifa que se paga a los mineros, con tier 1: el cliente paga
    el trabajo que cuesta, sin el multiplicador de potencia de un nodo
    concreto (ese nodo no ha hecho nada todavia).
    """
    try:
        # SIMULAR: consultar el precio no puede gastar el presupuesto del
        # minuto ni mover la bolsa. Antes, pedir precio dejaba la tarea a 0.
        kuki, _ = pago_por_trabajo(tipo, params, 1.0, simular=True)
        # 12 decimales, no 6 (25-10-2026): con el ancla una tarea minima cuesta
        # ~1e-9 KIA y redondear a 6 la dejaba en "cuesta 0", que es mentira y
        # ademas deja al cliente sin saber que tope firmar.
        return round(max(0.0, float(kuki)), 12)
    except Exception:
        return 0.0


def _retener_prepago(tid: str, cliente: str, monto: float) -> None:
    """Mueve el dinero del cliente a la cuenta de escrow. Debe ir en transaccion."""
    _asiento(tid, cliente, -monto, "prepago cliente")
    _asiento(tid, CUENTA_ESCROW, monto, "prepago cliente")


def _pagar_tarea_de_cliente(tid: str, pagos, cliente: str, prepago: float) -> tuple:
    """Paga desde el escrow del cliente a uno o varios mineros y devuelve el sobrante.

    `pagos` es una lista de (nodo_id, kuki). Dos asientos por movimiento,
    siempre compensados: el suministro total NO cambia, porque este dinero ya
    existia (lo puso el cliente). Si lo que hay que pagar supera el deposito,
    se reparte el deposito en proporcion y se avisa; nunca se emite de mas para
    tapar el hueco.
    """
    # 12 decimales y no 6 (25-10-2026, con la definicion 1 KIA = 1 minuto de
    # GPU): una tarea cuesta ~1e-4 KIA y redondear a 6 dejaba 2e-7 KIA de
    # diferencia entre lo retenido y lo pagado. Eso hacia el escrow NEGATIVO:
    # dinero creado de la nada en cada tarea de cliente. Lo cazo
    # tools/prueba_kia_minuto.py.
    pagos = [(n, round(max(0.0, float(k)), 12)) for n, k in pagos if float(k) > 0]
    total = round(sum(k for _, k in pagos), 12)
    deposito = round(max(0.0, float(prepago)), 12)
    if total <= 0 or deposito <= 0:
        if deposito > 0:
            _asiento(tid, CUENTA_ESCROW, -deposito, "devolucion deposito")
            _asiento(tid, cliente, deposito, "devolucion deposito")
            con.execute("UPDATE tareas SET prepago=0 WHERE id=?", (tid,))
        return 0.0, {"pago": 0.0, "sobrante_devuelto": deposito}
    factor = min(1.0, deposito / total)
    pagado = 0.0
    for nodo_id, kuki in pagos:
        parte = round(kuki * factor, 12)
        if parte <= 0:
            continue
        _asiento(tid, CUENTA_ESCROW, -parte, "pago a minero (cliente)")
        _asiento(tid, _cuenta_de(nodo_id), parte, "trabajo verificado")
        pagado = round(pagado + parte, 12)
    sobrante = round(max(0.0, deposito - pagado), 12)
    if sobrante > 0:
        _asiento(tid, CUENTA_ESCROW, -sobrante, "devolucion sobrante")
        _asiento(tid, cliente, sobrante, "devolucion sobrante")
    # El deposito ya se consumio: a 0 para que una segunda pasada no devuelva
    # dos veces. `pagado` guarda lo que cobraron los mineros, para el recibo.
    con.execute("UPDATE tareas SET prepago=0, pagado=? WHERE id=?", (pagado, tid))
    return pagado, {"pago": pagado, "sobrante_devuelto": sobrante,
                    "tope_alcanzado": bool(total > pagado + 1e-9)}


def _devolver_prepago(tid: str, motivo: str = "tarea fallida") -> float:
    """Devuelve al cliente lo retenido de una tarea que NO se llego a pagar.

    Pone `prepago` a 0 en el mismo movimiento: una tarea cerrada dos veces no
    puede devolver el dinero dos veces. Se llama dentro de la transaccion que
    cierra la tarea, igual que `_pagar_tarea_de_cliente` en la que la paga.
    """
    f = con.execute("SELECT cliente,prepago FROM tareas WHERE id=?", (tid,)).fetchone()
    if f is None:
        return 0.0
    cliente = (f["cliente"] or "").strip().upper()
    deposito = round(max(0.0, float(f["prepago"] or 0.0)), 12)
    if not cliente or deposito <= 0:
        return 0.0
    con.execute("UPDATE tareas SET prepago=0 WHERE id=?", (tid,))
    _asiento(tid, CUENTA_ESCROW, -deposito, "devolucion prepago")
    _asiento(tid, cliente, deposito, f"devolucion prepago ({motivo})")
    return deposito


def genesis():
    """Asignacion fundacional: se ejecuta UNA sola vez (idempotente)."""
    ya = con.execute("SELECT COUNT(*) c FROM ledger WHERE motivo='asignacion fundacional'").fetchone()["c"]
    if not ya:
        _asiento("genesis", GENESIS_DIR, GENESIS_MONTO, "asignacion fundacional")
        con.commit()

genesis()

def recalcular_resumen() -> dict:
    """Reconstruye saldos y suministro desde el ledger (fuente de verdad).

    Se ejecuta al arrancar: si alguien edito el ledger, la cadena lo delata y
    los totales se recalculan desde los asientos reales. Tambien sirve como
    reparacion manual via /admin/recalcular_resumen.
    """
    with _pago_lock:
        try:
            con.execute("BEGIN IMMEDIATE")
        except Exception:
            raise HTTPException(503, "Core ocupado, reintentar")
        try:
            con.execute("DELETE FROM saldos_cuenta")
            con.execute("INSERT INTO saldos_cuenta(cuenta,total) "
                        "SELECT nodo_id, COALESCE(SUM(kuki),0) FROM ledger GROUP BY nodo_id")
            fila = con.execute("SELECT COALESCE(SUM(kuki),0) s FROM ledger").fetchone()
            total = float(fila["s"] or 0.0)
            con.execute("INSERT INTO meta(clave,valor) VALUES('supply_emitido',?) "
                        "ON CONFLICT(clave) DO UPDATE SET valor=excluded.valor", (total,))
            cuentas = con.execute("SELECT COUNT(*) c FROM saldos_cuenta").fetchone()["c"]
            con.commit()
            return {"supply": round(total, 6), "cuentas": int(cuentas)}
        except HTTPException:
            try:
                _rollback_seguro()
            except Exception:
                pass
            raise
        except Exception as e:
            try:
                _rollback_seguro()
            except Exception:
                pass
            raise _error_escritura(e, "error reconstruyendo totales")

# CON EL DISCO LLENO EL CORE TIENE QUE ARRANCAR IGUAL (22-10-2026): si aqui
# revienta, no arranca la red entera por no poder reescribir dos totales que ya
# eran correctos. Se sigue con los ultimos buenos y se reconstruyen al
# siguiente arranque con sitio o con /admin/recalcular_resumen.
try:
    _RESUMEN_ARRANQUE = recalcular_resumen()
except HTTPException as _e_resumen:
    if getattr(_e_resumen, "status_code", 0) != 507:
        raise
    _RESUMEN_ARRANQUE = {"supply": None, "cuentas": None,
                         "aviso": "disco lleno: totales sin reconstruir"}
    print("[KUKIA] AVISO: disco lleno al arrancar; se mantienen los ultimos "
          "totales buenos (se reconstruyen cuando haya espacio)", flush=True)

# ============================================================
#  INTEGRIDAD DEL HISTORIAL (sellos firmados)
# ============================================================
# La cadena de hashes del ledger detecta un asiento editado, pero quien edita
# puede reescribir la cadena entera desde ese punto y volver a cuadrarla. Los
# sellos fijan el pasado: cada uno es una foto firmada (asientos, ultimo hash
# y suma) con una clave Ed25519 que NO esta en la base de datos.
# Si al arrancar algo no cuadra, el Core deja de crear dinero hasta que se
# revise: mejor parado que pagando sobre un historial falso.
def _sellos_leidos() -> list:
    if SELLOS is None:
        return []
    try:
        return SELLOS.leer_sellos(SELLOS_FILE)
    except Exception:
        return []

def _n_sellos_meta() -> int:
    try:
        fila = con.execute("SELECT valor FROM meta WHERE clave='sellos_n'").fetchone()
        return int(fila["valor"]) if fila else 0
    except Exception:
        return 0


def _apartar_sellos_ajenos() -> "Path | None":
    """Aparta el sellos.jsonl del repo cuando esta instalacion es nueva.

    POR QUE (22-10-2026): el repo publica los sellos del fundador, firmados con
    SU clave, que no se publica (ni debe). Quien clona el proyecto genera su
    propia clave, no puede verificar esas firmas, y el Core arrancaba con
    "EL HISTORIAL NO CUADRA" y los pagos bloqueados: se clonaba para minar y no
    se cobraba NUNCA. Aqui se renombra el fichero (no se borra: queda como
    evidencia) y esta instalacion empieza su propia cadena de sellos.

    Solo se aparta si (a) la base no tiene sellos propios anotados Y (b) no hay
    ni un sello en el fichero firmado con nuestra clave. En el PC del fundador
    las dos cosas son al reves, asi que no se toca nada, ni siquiera si un dia
    el fichero se corrompe: ahi hay que verlo, no esconderlo.
    """
    if SELLOS is None or not _sello_pub:
        return None
    if _n_sellos_meta() > 0:
        return None
    try:
        sellos = SELLOS.leer_sellos(SELLOS_FILE)
    except Exception:
        return None
    if not sellos or any(SELLOS.verificar_firma(s, _sello_pub)
                         for s in sellos if isinstance(s, dict)):
        return None
    destino = SELLOS_FILE.with_name(SELLOS_FILE.name + ".de_otra_instalacion")
    n = 1
    while destino.exists():
        destino = SELLOS_FILE.with_name(f"{SELLOS_FILE.name}.de_otra_instalacion{n}")
        n += 1
    try:
        SELLOS_FILE.replace(destino)
        return destino
    except Exception as e:
        print(f"[KUKIA] AVISO: no se pudo apartar {SELLOS_FILE.name}: {e}", flush=True)
        return None

def _verificar_integridad() -> dict:
    global LEDGER_ROTO
    problemas = []
    cadena = {}
    sellos = _sellos_leidos()
    if sellos:
        # Clon recien descargado: los sellos del repo son de otra instalacion.
        apartado = _apartar_sellos_ajenos()
        if apartado is not None:
            print(f"[KUKIA] instalacion nueva: los sellos del repo son de otra "
                  f"cadena. Apartados en {apartado.name}; esta empieza la suya.",
                  flush=True)
            sellos = []
    if SELLOS is None:
        problemas.append("modulo de sellos no disponible")
    else:
        try:
            cadena = SELLOS.verificar_cadena_db(con)
            if not cadena["ok"]:
                problemas.append(f"asientos alterados: {cadena['ids_alterados'][:10]}")
            if sellos:
                r = SELLOS.verificar_sellos(con, sellos, _sello_pub)
                for p in r["problemas"]:
                    if p not in problemas:
                        problemas.append(p)
        except Exception as e:
            problemas.append(f"no se pudo verificar: {e}")
    n_meta = _n_sellos_meta()
    if n_meta > len(sellos):
        problemas.append(f"faltan sellos: la base dice {n_meta} y el fichero tiene {len(sellos)}")
    LEDGER_ROTO = bool(problemas) and SELLO_BLOQUEA_PAGOS
    if problemas:
        print("[KUKIA] " + "!" * 62, flush=True)
        print("[KUKIA] LEDGER: EL HISTORIAL NO CUADRA", flush=True)
        for p in problemas[:10]:
            print(f"[KUKIA]   - {p}", flush=True)
        if LEDGER_ROTO:
            print("[KUKIA] PAGOS BLOQUEADOS hasta que se revise (/ledger/integridad)", flush=True)
        print("[KUKIA] " + "!" * 62, flush=True)
    else:
        print(f"[KUKIA] integridad OK: cadena intacta, {len(sellos)} sellos", flush=True)
    return {"ok": not problemas, "problemas": problemas,
            "cadena_ok": bool(cadena.get("ok")), "sellos": len(sellos),
            "pagos_bloqueados": LEDGER_ROTO}

INTEGRIDAD_ARRANQUE = _verificar_integridad()

def _sellar_ahora(espera: float = 10.0) -> dict:
    """Crea un sello firmado del estado actual y lo anade al fichero append-only."""
    if SELLOS is None or not _sello_priv:
        return {}
    if not _pago_lock.acquire(timeout=espera):
        return {}
    try:
        sellos = _sellos_leidos()
        s = SELLOS.nuevo_sello(con, _sello_priv, sellos[-1] if sellos else None)
        if sellos and int(s["hasta"]) <= int(sellos[-1].get("hasta") or 0):
            return {}
        SELLOS.anadir_sello(SELLOS_FILE, s)
        try:
            con.execute("INSERT INTO meta(clave,valor) VALUES('sellos_n',?) "
                        "ON CONFLICT(clave) DO UPDATE SET valor=excluded.valor",
                        (float(len(sellos) + 1),))
            con.commit()
        except Exception:
            pass
        return s
    except Exception as e:
        print(f"[KUKIA] sello error: {e}", flush=True)
        return {}
    finally:
        _pago_lock.release()

def _bucle_sellos():
    """Sella cada SELLO_CADA_SEG segundos (o cada SELLO_CADA_ASIENTOS asientos)."""
    ultimo = 0.0
    while True:
        time.sleep(min(60.0, max(15.0, SELLO_CADA_SEG / 4.0)))
        try:
            if LEDGER_ROTO:
                continue
            sellos = _sellos_leidos()
            ult = sellos[-1] if sellos else None
            est = SELLOS.estado_ledger(con) if SELLOS else {}
            nuevos = int(est.get("asientos", 0)) - int((ult or {}).get("asientos") or 0)
            ahora = time.time()
            toca = (ult is None and int(est.get("asientos", 0)) > 0)
            if not toca:
                toca = nuevos >= SELLO_CADA_ASIENTOS
            if not toca:
                toca = nuevos > 0 and (ahora - ultimo) >= SELLO_CADA_SEG
            if toca:
                s = _sellar_ahora()
                if s:
                    ultimo = ahora
                    print(f"[KUKIA] sello hasta {s['hasta']} ({s['asientos']} asientos, "
                          f"{s['suma']} KIA)", flush=True)
        except Exception as e:
            print(f"[KUKIA] bucle sellos error: {e}", flush=True)

threading.Thread(target=_bucle_sellos, daemon=True).start()
# Primer sello al arrancar: no esperar al bucle para tener una referencia.
if not _sellos_leidos():
    _sellar_ahora()

# ============================================================
#  VERIFICACION REAL (trustless): el Core recalcula la tarea
# ============================================================

def gen_matriz(n: int, seed: int) -> np.ndarray:
    """Matriz determinista de enteros acotados (exacta en float64)."""
    idx = np.arange(n, dtype=np.float64)
    return (idx[:, None] * 31 + idx[None, :] * 17 + seed) % 97 - 48

def computar_tarea(tipo: str, params: dict):
    """Recalcula el resultado esperado en el propio Core (numpy, CPU)."""
    if tipo == "checksum":
        data = str(params.get("data", "kukia"))
        return hashlib.sha256(data.encode()).hexdigest()
    if tipo == "matriz":
        n = int(params.get("n", 256))
        seed = int(params.get("seed", 42))
        a = gen_matriz(n, seed)
        b = gen_matriz(n, seed + 1)
        return str(int(round(float((a @ b).sum()))))
    if tipo == "inferencia":
        n = int(params.get("n", 64))
        seed = int(params.get("seed", 7))
        x = gen_matriz(n, seed)
        k = np.array([[1.0, 2.0, 1.0], [2.0, 4.0, 2.0], [1.0, 2.0, 1.0]])
        out = 0.0
        for i in range(3):
            for j in range(3):
                out += float((x[i:n-2+i, j:n-2+j] * k[i, j]).sum())
        return str(int(round(out)))
    return None

# --- Modelos de referencia para tareas IA (punto 5: distribucion) ---
MODELOS_DIR = Path(__file__).parent / "modelos"
MODELOS_DIR.mkdir(parents=True, exist_ok=True)

def _difusion_ref(n: int, pasos: int, seed: int) -> float:
    """Valor de referencia de la difusion (float64, MISMO calculo que el worker).

    AUDITORIA: en CPU (numpy) una tarea n=2048/pasos=4000 tarda ~7,4 min y la
    cola tenia 97 tareas asi (≈530 min de CPU). Con la GPU disponible baja a
    ~1 s. La rejilla inicial se genera con numpy default_rng (igual que el
    worker) y la iteracion va en float64; la comparacion sigue siendo con
    tolerancia relativa, asi que la verificacion no se debilita.
    """
    if USAR_GPU_VERIF:
        try:
            import torch
            x = torch.from_numpy(np.random.default_rng(seed).random((n, n))).to("cuda", dtype=torch.float64)
            for _ in range(pasos):
                x[1:-1, 1:-1] = x[1:-1, 1:-1] + 0.2 * (
                    x[:-2, 1:-1] + x[2:, 1:-1] + x[1:-1, :-2] + x[1:-1, 2:] - 4 * x[1:-1, 1:-1])
            return float(x.sum().item())
        except Exception:
            pass
    x = np.random.default_rng(seed).random((n, n))
    for _ in range(pasos):
        x[1:-1, 1:-1] = x[1:-1, 1:-1] + 0.2 * (
            x[:-2, 1:-1] + x[2:, 1:-1] + x[1:-1, :-2] + x[1:-1, 2:] - 4 * x[1:-1, 1:-1])
    return float(x.sum())

def _verificar_difusion(params, metrica):
    """Recomputa la difusion y compara con tolerancia relativa (GPU y CPU
    redondean distinto tras cientos de pasos)."""
    try:
        n = int(params.get("n", 256))
        pasos = int(params.get("pasos", 200))
        seed = int(params.get("seed", 42))
        val = float(metrica)
        if not math.isfinite(val):
            return False
        ref = _difusion_ref(n, pasos, seed)
        tol = float(params.get("tolerancia_rel", 1e-9))
        return abs(val - ref) <= tol * max(1.0, abs(ref))
    except MemoryError:
        # AUDITORIA 21-10-2026: sin memoria NO es un resultado incorrecto. Si se
        # tragaba como False, el Core castigaba al nodo por quedarse el sin RAM
        # al recalcular (paso de verdad: no pudo reservar 4 MB de numpy).
        raise
    except Exception:
        return False

def _verificar_embeddings(tipo, params, metrica):
    """Puntos 1+2: hash del modelo + recomputo CPU con tolerancia (lote: spot-check)."""
    try:
        modelo = params["model_path"]
        vecs = json.loads(metrica)
        if not isinstance(vecs, list):
            return False
        h = ONNXCPU.config_hash(modelo, params["input"] if tipo == "embeddings" else params["inputs"])
        if str(params.get("model_hash", h)) != h:
            return False
        tol = float(params.get("tolerancia", 1e-5))
        if tipo == "embeddings":
            pares = [(params["input"], vecs)]
        else:
            entradas = list(params["inputs"])
            if len(vecs) != len(entradas):
                return False
            # AUDITORIA 2026-10-20 (soundness): antes se comprobaban solo 3
            # vectores y los indices salian de params["seed"], que el minero
            # conoce: podia calcular justo esos 3 y falsificar los demas.
            # Ahora se verifican TODOS. El lote se limita en validar_params
            # para que el coste quede acotado.
            pares = list(zip(entradas, vecs))
        for ent, rec in pares:
            ref = ONNXCPU.ejecutar_tarea(modelo, ent)
            if not ONNXCPU.comparar_con_tolerancia(rec, ref, tol):
                return False
        return True
    except MemoryError:
        # Igual que en la difusion: quedarse sin RAM no es trampa del nodo.
        raise
    except Exception:
        return False

app = FastAPI(title="KUKIA Core", version="0.4.4")

# ============================================================
#  HUELLA DEL CODIGO QUE SE ESTA EJECUTANDO (23-10-2026)
#
#  POR QUE EXISTE: una auditoria del 23-10 contesto lo que nadie podia
#  comprobar desde fuera: "una revision de main NO demuestra que codigo se
#  esta ejecutando". Tenia razon: el Core no decia ni que version corria ni
#  desde cuando. Ahora publica en /stats (y en /version) el SHA-256 de los
#  ficheros clave, la fecha de cada uno, la hora de arranque y el commit del
#  repo si esta al lado. Cualquiera puede descargar el fichero de GitHub,
#  calcularle el SHA-256 y compararlo con lo que responde el Core vivo.
#
#  Se calcula UNA vez, al importar: si el fichero cambia en disco despues, la
#  huella sigue siendo la del codigo cargado en memoria, que es justo lo que
#  se quiere demostrar (que no basta con editar el .py para cambiar el Core).
# ============================================================
FICHEROS_HUELLA = (
    "coordinator/kukia_core.py",
    "worker/kukia_worker.py",
    "ledger/kukia_wallet.py",
    "ledger/sellos.py",
    "tools/guardia.py",
)


def _sha256_fichero(ruta: Path) -> str:
    try:
        return hashlib.sha256(ruta.read_bytes()).hexdigest()
    except Exception:
        return ""


def _commit_del_repo(raiz: Path) -> str:
    """Commit de HEAD leido del .git a mano: sin lanzar git ni tocar la red."""
    try:
        git = raiz / ".git"
        cabeza = (git / "HEAD").read_text(encoding="utf-8").strip()
        if cabeza.startswith("ref:"):
            ref = cabeza.split(":", 1)[1].strip()
            fichero = git / ref
            if fichero.exists():
                return fichero.read_text(encoding="utf-8").strip()[:7]
            # ref empaquetada (git pack-refs)
            empaquetadas = git / "packed-refs"
            if empaquetadas.exists():
                for linea in empaquetadas.read_text(encoding="utf-8").splitlines():
                    if linea.endswith(" " + ref):
                        return linea.split(" ", 1)[0][:7]
            return ""
        return cabeza[:7]
    except Exception:
        return ""


def _huella_codigo() -> dict:
    raiz = Path(__file__).resolve().parent.parent
    ficheros = {}
    for rel in FICHEROS_HUELLA:
        f = raiz / rel
        ficheros[rel] = {"sha256": _sha256_fichero(f)[:16],
                         "mtime": (time.strftime("%Y-%m-%d %H:%M:%S",
                                                  time.localtime(f.stat().st_mtime))
                                   if f.exists() else "")}
    return {"version": app.version,
            "commit": _commit_del_repo(raiz),
            "arranque": time.strftime("%Y-%m-%d %H:%M:%S"),
            "arranque_ts": round(time.time(), 3),
            "ficheros": ficheros,
            "nota": "sha256 en 16 hex: se compara con el fichero de GitHub tal cual"}


_HUELLA = _huella_codigo()

# ============================================================
#  RATE LIMIT (anti-spam)  +  AUDITORIA: un cubo POR RUTA
# ============================================================
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request as StarRequest

# AUDITORIA: antes era un unico contador por IP compartido por las 5 rutas:
# un minero que pedia trabajo y entregaba resultados se autobloqueaba (429).
# AUDITORIA 2026-10-28 (A1): las dos rutas de auditoria recorren el ledger
# entero (13-15 s de CPU con 930.000 asientos). Su resultado se reutiliza
# durante AUDITORIA_TTL y solo un hilo lo recalcula; los demas reciben el
# veredicto anterior, siempre marcado con su edad. El techo global impide que
# muchas IPs (o IPs falsificadas) conviertan el limite por IP en decorativo.
AUDITORIA_TTL = max(30.0, float(os.environ.get("KUKIA_AUDITORIA_TTL", "300")))
LIMITE_GLOBAL_FACTOR = max(1, int(os.environ.get("KUKIA_LIMITE_GLOBAL_X", "3")))

LIMITES_POR_RUTA = {
    "/registro": 30,
    "/nodos/registrar": 30,
    # El reto se pide una vez por registro; 60/min deja margen a un worker que
    # reintenta y frena un bucle que quiera llenar la tabla de retos.
    "/nodos/reto": 60,
    # Lista de nodos: lectura acotada, pero tambien se limita para que nadie la
    # martillee en bucle (el panel la pide cada 2 s; esto deja 10x de margen).
    "/nodos": 600,
    # Las dos rutas del minero van firmadas: el limite solo frena un bucle
    # desbocado. Con 2 GPUs el minero hace ~240 entregas/min y con 300 se
    # autofrenaba (429) en los picos.
    "/tareas/pendiente": 1200,
    "/tareas/resultado": 1200,
    "/tareas/resultado_json": 1200,
    # Latido de GPU: 2 GPUs cada 3 s son ~40/min. 600 deja sitio a varios
    # mineros en el mismo Core sin abrir la puerta a un bucle desbocado.
    "/nodos/telemetria": 600,
    "/transferir": 60,
    # Mismo cupo para el camino poscuantico: una transferencia cuesta lo mismo
    # de verificar y no tiene sentido darle mas manga ancha que al v1.
    "/transferir/v2": 60,
    # Un validador independiente manda una atestacion por vuelta (cada 30 s).
    # 240/min deja sitio a varios validadores en el mismo Core sin abrir la
    # puerta a un bucle desbocado.
    "/validador/atestacion": 240,
    # Auditoria publica: con la cache de 300 s, 6/min por IP sobra para
    # comprobar el ledger y ya no permite amasar CPU.
    "/ledger/verificar": 6,
    "/ledger/integridad": 6,
}
RATE_WINDOW = 60.0
_rate_hist = {}
_rate_lock = threading.Lock()

# AUDITORIA 2026-10-20 (minero): anti-replay de /tareas/pendiente.
# Antes la firma solo cubria nodo+ts, asi que una peticion capturada se podia
# repetir durante 5 minutos para robar la cola del nodo (DoS). El worker nuevo
# manda un nonce aleatorio y el Core lo firma; el viejo (sin nonce) sigue
# funcionando para no romper mineros ya descargados.
NONCE_TTL = 600.0

def _nonce_nuevo(nodo_id: str, nonce: str) -> bool:
    """Marca el nonce como usado. Persistente: un reinicio NO lo olvida.

    AUDITORIA 2026-10-20: antes era un dict en RAM (TTL 600 s); reiniciar el
    Core permitia repetir una peticion capturada dentro de su ventana. Ahora
    vive en SQLite con clave primaria (nodo_id, nonce) y se purga al vuelo.
    """
    ahora = time.time()
    with _pago_lock:
        try:
            con.execute("BEGIN IMMEDIATE")
        except Exception:
            raise HTTPException(503, "Core ocupado, reintentar")
        try:
            con.execute("DELETE FROM nonces_pendiente WHERE ts < ?",
                        (ahora - NONCE_TTL,))
            con.execute("INSERT INTO nonces_pendiente(nodo_id,nonce,ts) VALUES(?,?,?)",
                        (nodo_id, nonce, ahora))
            con.commit()
            return True
        except sqlite3.IntegrityError:
            con.rollback()
            return False
        except Exception as e:
            try:
                con.rollback()
            except Exception:
                pass
            raise _error_escritura(e, "error interno guardando el nonce")

class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: StarRequest, call_next):
        limite = LIMITES_POR_RUTA.get(request.url.path)
        if limite:
            # AUDITORIA 2026-10-28 (A2): la IP se saca con _ip_cliente(). Antes
            # se usaba request.client.host, que detras del tunel es 127.0.0.1
            # para TODO el mundo: los cupos eran uno solo para la red entera y
            # un cliente podia dejar a todos los mineros con 429.
            ip = _ip_cliente(request)
            ruta = request.url.path
            clave = (ip, ruta)
            clave_global = ("*", ruta)
            ahora = time.time()
            with _rate_lock:
                if len(_rate_hist) > 5000:      # AUDITORIA: no crecer sin freno
                    for k in [k for k, v in _rate_hist.items() if not v or ahora - v[-1] > RATE_WINDOW]:
                        _rate_hist.pop(k, None)
                glob = [t for t in _rate_hist.get(clave_global, []) if ahora - t < RATE_WINDOW]
                if len(glob) >= limite * LIMITE_GLOBAL_FACTOR:
                    return JSONResponse({"detail": "demasiadas peticiones en la red, espera un minuto"},
                                        status_code=429)
                hist = [t for t in _rate_hist.get(clave, []) if ahora - t < RATE_WINDOW]
                if len(hist) >= limite:
                    return JSONResponse({"detail": "demasiadas peticiones, espera un minuto"}, status_code=429)
                hist.append(ahora)
                glob.append(ahora)
                _rate_hist[clave] = hist
                _rate_hist[clave_global] = glob
        return await call_next(request)

app.add_middleware(RateLimitMiddleware)

class BodyLimitMiddleware(BaseHTTPMiddleware):
    """Corta cuerpos enormes antes de que FastAPI los parsee."""
    async def dispatch(self, request: StarRequest, call_next):
        if request.method in ("POST", "PUT", "PATCH"):
            cab = request.headers.get("content-length")
            if cab:
                try:
                    largo = int(cab)
                except ValueError:
                    return JSONResponse({"detail": "Content-Length invalido"}, status_code=400)
                if largo > MAX_CUERPO_BYTES:
                    return JSONResponse({"detail": f"cuerpo demasiado grande (max {MAX_CUERPO_BYTES} bytes)"},
                                        status_code=413)
        return await call_next(request)

app.add_middleware(BodyLimitMiddleware)

# ============================================================
#  AUDITORIA: guardia de Host/Origin (DNS rebinding)
#  El Core vive en loopback, pero una web abierta en este PC podia
#  alcanzarlo con cualquier Host. Se rechaza lo que no sea local.
# ============================================================
class HostGuardMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: StarRequest, call_next):
        host = (request.headers.get("host") or "").split(":")[0].strip().lower()
        if host and not host_permitido(host):
            return JSONResponse({"detail": "Host no permitido por el Core"}, status_code=403)
        origen = (request.headers.get("origin") or "").strip().lower()
        if origen and not _origen_ok(origen):
            return JSONResponse({"detail": "origen no permitido"}, status_code=403)
        return await call_next(request)

app.add_middleware(HostGuardMiddleware)

# CORS para la billetera web (lectura de saldo/estadisticas). Sin credenciales:
# las escrituras siguen exigiendo firma Ed25519 o token de admin.
app.add_middleware(
    CORSMiddleware,
    allow_origins=sorted(ORIGENES_PERMITIDOS),
    allow_origin_regex=r"^https?://(127\.0\.0\.1|localhost|\[::1\])(:\d+)?$",
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
    max_age=600,
)

class RetoIn(BaseModel):
    clave_publica: str = ""

class NodoIn(BaseModel):
    nombre: str
    vram_gb: int
    ref: str = ""
    tops: float = 0.0    # potencia IA declarada (TOPS, valor oficial de la GPU)
    clave_publica: str = ""  # Ed25519 hex (64 bytes) para autenticar resultados
    prueba_gpu: str = ""  # atestado GPU declarativo (punto 3, no criptografico)
    tops_estimado: bool = True  # punto 1: los TOPS son estimados por VRAM, no medidos
    # ARREGLO 2026-10-19: direccion de la billetera donde COBRAR el trabajo.
    # Antes los pagos iban siempre al id del nodo (uuid efimero): el minero
    # ganaba KIA que no llegaban nunca a su billetera y se quedaban huerfanos
    # en cada reinicio. Vacio = comportamiento antiguo (pagar al nodo).
    direccion_cobro: str = ""
    # Prueba de posesion de la clave privada (reto de un solo uso).
    reto: str = ""
    firma_reto: str = ""
    # Dispositivo con el que mina ('cpu', 'cuda:0'...). Opcional: los workers
    # anteriores no lo mandan y se deduce por VRAM/TOPS (ver _nodo_es_cpu).
    device: str = ""

class TareaIn(BaseModel):
    tipo: str      # 'checksum' | 'matriz' | 'inferencia' | 'embeddings' | 'lote_embeddings' | 'difusion_calor'
    params: dict = {}
    cola: str = "B"       # A=comercial, B=investigacion KUKIA, C=voluntaria/experimental
    campana: str = ""     # identificador de campana (para exclusion y trazabilidad)
    prioridad: float = 0.0  # dentro de la cola, mayor antes
    vram_min: int = 0     # VRAM minima (GB) que exige la tarea


class ClienteAltaIn(BaseModel):
    """Alta de un cliente (solo el administrador del Core)."""
    nombre: str
    direccion: str = ""   # su billetera KUK... (la que firma los prepagos)


class ClienteTareaIn(BaseModel):
    """Trabajo que pide un cliente, ya pagado por adelantado.

    La firma es del cliente sobre
        cliente:<nonce>:<tipo>:<sha256(params ordenados)>:<tope>
    y, si se deposita de mas, sobre la misma cadena con `:<retenido>` al final
    (25-10-2026): el importe que se queda retenido tambien va firmado, para que
    nadie pueda subir la retencion con el token robado.

    `deposito` (25-10-2026): KIA que se dejan RETENIDOS. Si no se manda, se
    retiene el coste justo. Si se manda, tiene que cubrir el coste: el minero
    cobra su tarifa entera y el sobrante vuelve al cliente al cerrar la tarea.
    """
    tipo: str
    params: dict = {}
    nonce: str                     # unico por peticion (anti-repeticion)
    tope: float = 0.0              # KIA maximos que autoriza para esta tarea
    deposito: float = 0.0          # KIA a retener (0 = el coste justo)
    clave_publica: str = ""
    firma: str = ""
    vram_min: int = 0


def tier(vram: int) -> float:
    return TIER_MULT.get(vram, 1.0)

# ============================================================
#  POTENCIA DEMOSTRADA (21-10-2026)
#  El tier se paga por lo que la GPU DEMUESTRA, no por lo que declara.
#  En Bitcoin el hashrate no se declara: se mide resolviendo. Aqui igual, salvo
#  que la medida es trabajo util verificado (FLOPs / segundos).
# ============================================================
# Minimo creible por GB declarado. CALIBRADO CON DATOS REALES (21-10-2026):
# una 5070 de 16 GB midio 12,1 GFLOPS y una de 12 GB 12,7 GFLOPS con el reloj
# del Core (que incluye red y sondeo, asi que es un SUELO, no la potencia
# real). Eso da 0,75-1,06 GFLOPS por GB. El liston se pone en 0,25: cuatro
# veces por debajo de lo medido, para no castigar nunca a una GPU honesta por
# una red lenta, pero muy por encima de lo que da una CPU diciendo 24 GB.
GFLOPS_POR_GB_MIN = 0.25
MIN_MUESTRAS_POTENCIA = 20     # por debajo de esto no se corrige nada
MIN_FLOPS_MUESTRA = 1e9        # tareas de menos de 1 GFLOP no sirven para medir
MIN_SEG_MUESTRA = 0.05         # ni las que duran menos de 50 ms (manda la red)


def tier_verificado(node) -> float:
    """Tier que se PAGA: el declarado, pero solo si la GPU lo respalda.

    POR QUE (fallo economico real): el tier salia de `vram_gb`, que lo manda el
    propio minero en el registro. Una CPU podia declarar 24 GB y cobrar x1,6
    sin demostrar nada. Aqui la VRAM sigue valiendo, pero solo si el nodo ha
    entregado trabajo verificado a la velocidad minima que ese tamano implica.

    Solo se corrige con >= 20 muestras (medir mal a un nodo nuevo seria peor
    que el fraude que evita) y el liston es deliberadamente bajo: 0,25 GFLOPS
    por GB declarado, cuatro veces por debajo de lo que mide una GPU real.
    """
    try:
        declarado = float(node["tier"] or 1.0)
    except Exception:
        declarado = 1.0
    if declarado <= 1.0:
        return declarado          # sin prima que proteger
    try:
        medido = float(node["gflops_medido"] or 0.0)
        muestras = int(node["potencia_muestras"] or 0)
        vram = int(node["vram_gb"] or 0)
    except Exception:
        return declarado
    if muestras < MIN_MUESTRAS_POTENCIA or medido <= 0 or vram <= 0:
        # SIN PRUEBAS NO HAY PRIMA (22-10-2026). Antes, hasta juntar 20 muestras
        # se pagaba el tier declarado "para no castigar", y ahi estaba el
        # agujero: un nodo podia declarar 24 GB, cobrar x1,6 y no entregar
        # nunca trabajo grande. Ahora la prima se GANA con trabajo medido; lo
        # unico que se pierde es la diferencia de los primeros minutos.
        return 1.0
    if medido < GFLOPS_POR_GB_MIN * vram:
        return 1.0
    return declarado


def gflops_de_tarea(tipo: str, params, seg_servidor: float) -> float:
    """GFLOPS medidos por el CORE (reloj del servidor, no el que dice el nodo).

    La duracion la cronometra el Core entre la asignacion y la entrega: el
    minero no puede acortarla. Devuelve 0 si la tarea es demasiado pequena
    para que la medida signifique algo (la red pesaria mas que la GPU).
    """
    try:
        seg = float(seg_servidor)
    except Exception:
        return 0.0
    if seg < MIN_SEG_MUESTRA:
        return 0.0
    flops = _flops_tarea(tipo, params)
    if flops < MIN_FLOPS_MUESTRA:
        return 0.0
    return flops / seg / 1e9

# --- Calendario de emision: la ultima moneda sale el 13-04-2150 ---
# 6 halvings en 124,28 anos -> era de 20,71 anos. Tras esa fecha, emision = 0.
#
# POR QUE 2150 (23-10-2026): decision del dueno. El 22-10 se habia movido al
# 01-01-2152 (126 anos, 6 eras de 21) porque un requisito propio pedia 125 anos
# o mas desde hoy; Kuki lo corrigio: el calendario acaba en 2150, no en 2152.
#
# POR QUE EL 13 DE ABRIL (25-10-2026): Kuki fijo la fecha exacta. El calendario
# ya no acaba el 1 de enero, acaba el 13-04-2150: 45.392 dias desde el
# 01-01-2026, o sea 124,28 anos y 6 eras de 20,71. Eso son 103 dias mas, asi
# que el presupuesto del minuto baja un pelin (de 1,310593 a 1,307607 KIA/min)
# y la red tiene 148.320 minutos mas para emitir los mismos 100M: con el mismo
# trabajo, la ultima moneda ya NO sale antes del 13-04-2150.
# Lo comprueba `python tools/calculo_moneda.py`.
from datetime import date, datetime, timedelta
EMISSION_START = date(2026, 1, 1)
EMISSION_END = date(2150, 4, 13)
HALVING_YEARS = (EMISSION_END - EMISSION_START).days / 365.25 / 6  # 20,71
def emission_mult() -> float:
    """Multiplicador de recompensa por era: 1.0, 0.5, 0.25... 0 tras la fecha final."""
    hoy = date.today()
    if hoy >= EMISSION_END:
        return 0.0
    elapsed = (hoy - EMISSION_START).days / 365.25
    era = max(0, int(elapsed // HALVING_YEARS))
    return 0.5 ** era

def emission_info() -> dict:
    hoy = date.today()
    if hoy >= EMISSION_END:
        return {"era_actual": 6, "multiplicador": 0.0,
                "proximo_halving": None, "fin_emision": EMISSION_END.isoformat(),
                "halving_years": round(HALVING_YEARS, 2)}
    elapsed = (hoy - EMISSION_START).days / 365.25
    era = max(0, int(elapsed // HALVING_YEARS))
    prox = EMISSION_START + timedelta(days=int((era + 1) * HALVING_YEARS * 365.25))
    return {
        "era_actual": era,
        "multiplicador": 0.5 ** era,
        "proximo_halving": prox.isoformat(),
        "fin_emision": EMISSION_END.isoformat(),
        "halving_years": round(HALVING_YEARS, 2),
    }

# ============================================================
#  AUDITORIA: validacion de params (topes duros)
# ============================================================
def validar_params(tipo: str, params: dict) -> dict:
    if not isinstance(params, dict):
        raise HTTPException(400, "params debe ser un objeto")
    if len(json.dumps(params)) > LIMITE_PARAMS_BYTES:
        raise HTTPException(400, f"params demasiado grande (max {LIMITE_PARAMS_BYTES} bytes)")
    for campo, tope in (("n", LIMITE_N), ("pasos", LIMITE_PASOS)):
        if campo in params:
            try:
                v = int(params[campo])
            except Exception:
                raise HTTPException(400, f"{campo} invalido")
            if not (1 <= v <= tope):
                raise HTTPException(400, f"{campo} fuera de rango (1..{tope})")
            params[campo] = v
    for campo in ("seed",):
        if campo in params:
            try:
                params[campo] = int(params[campo])
            except Exception:
                raise HTTPException(400, "seed invalido")
    if tipo == "difusion_calor":
        # AUDITORIA 2026-10-20: recorta el coste real de la tarea. Antes se
        # podia encolar n=4096/pasos=20000 y bloquear minero y verificacion.
        n = int(params.get("n", 256))
        params["n"] = n
        params["pasos"] = min(int(params.get("pasos", 1500)), pasos_max_difusion(n))
    if tipo == "checksum":
        data = str(params.get("data", "kukia"))
        if len(data) > 4096:
            raise HTTPException(400, "data demasiado larga (max 4096)")
        params["data"] = data
    if "model_path" in params and len(str(params["model_path"])) > 256:
        raise HTTPException(400, "model_path demasiado largo")
    if "inputs" in params:
        try:
            tope_lote = 64 if tipo == "lote_embeddings" else 256
            if len(params["inputs"]) > tope_lote:
                raise HTTPException(400, f"demasiadas entradas en el lote (max {tope_lote})")
        except TypeError:
            raise HTTPException(400, "inputs debe ser una lista")
    # AUDITORIA 2026-10-20: la tolerancia la elige la tarea. Sin tope, una
    # tolerancia enorme haria que cualquier resultado "cuadrara".
    for campo, tope in (("tolerancia_rel", 1e-6), ("tolerancia", 1e-3)):
        if campo in params:
            try:
                v = float(params[campo])
            except Exception:
                raise HTTPException(400, f"{campo} invalido")
            if not math.isfinite(v) or v < 0:
                raise HTTPException(400, f"{campo} invalido")
            params[campo] = min(v, tope)
    return params

def tope_default(tipo: str) -> int:
    """Tamano por defecto si la tarea no trae 'n' (para el recalculo)."""
    return 256

@app.post("/nodos/reto")
def nodo_reto(r: RetoIn):
    """Reto de un solo uso para registrar un nodo (prueba de clave).

    El nodo firma "registro:<nonce>:<ts>:<clave_publica>" con su clave privada
    y lo presenta en /nodos/registrar. Asi no se pueden crear identidades con
    claves ajenas ni acumular nodos sin coste.
    """
    pub = (r.clave_publica or "").strip().lower()
    if not pubkey_valida(pub):
        raise HTTPException(400, "clave_publica invalida (Ed25519 hex de 32 bytes)")
    nonce = secrets.token_hex(16)
    ts = int(time.time())
    ahora = time.time()
    with _retos_lock:
        for k, v in list(_retos_registro.items()):
            if ahora - v[0] > RETO_TTL:
                _retos_registro.pop(k, None)
        if len(_retos_registro) > 20_000:
            _retos_registro.clear()
        _retos_registro[nonce] = (ahora, pub, ts)
    return {"nonce": nonce, "ts": ts, "expira_seg": int(RETO_TTL),
            "mensaje": f"registro:{nonce}:{ts}:{pub}"}

def _ip_cliente(req: Request) -> str:
    """IP real del cliente, sin fiarse de cabeceras que pueda inventar Internet.

    Detras del tunel de Cloudflare la conexion llega desde 127.0.0.1 y la IP
    real viene en CF-Connecting-IP. Solo se acepta si el socket es local; una
    peticion directa desde la LAN no puede falsear su IP con esa cabecera.
    """
    host = (req.client.host if req.client else "") or ""
    if CONFIA_PROXY_LOCAL and host in ("127.0.0.1", "::1", "localhost"):
        # AUDITORIA 2026-10-28 (A2): NO se acepta X-Forwarded-For. Cloudflare lo
        # reenvia con la IP real DELANTE de la cadena que mando el cliente, asi
        # que su primer valor lo elige quien llama: con el se saltaba cualquier
        # cupo. CF-Connecting-IP lo escribe el borde y no se hereda del cliente.
        valor = (req.headers.get("cf-connecting-ip") or "").strip()
        if valor:
            return valor[:64]
    return (host or "desconocida")[:64]

def _cupo_registro(ip: str, pub: str) -> None:
    """Cuenta el registro en la misma transaccion del nodo. 429 si se pasa.

    Se purgan las filas de mas de 24 h en cada llamada: la tabla se queda en
    unas pocas decenas de filas y no hace falta un hilo de limpieza.
    """
    ahora = time.time()
    con.execute("DELETE FROM registros_nodo WHERE ts < ?", (ahora - 86400.0,))
    if REGISTRO_CUPO_HORA > 0:
        n = con.execute("SELECT COUNT(*) c FROM registros_nodo WHERE ip=? AND ts>=?",
                        (ip, ahora - 3600.0)).fetchone()["c"]
        if int(n) >= REGISTRO_CUPO_HORA:
            raise HTTPException(429, f"cupo de registros por IP alcanzado "
                                     f"({n}/{REGISTRO_CUPO_HORA} en 1 h); espera o usa otra red")
    if REGISTRO_CUPO_DIA > 0:
        n = con.execute("SELECT COUNT(*) c FROM registros_nodo WHERE ip=? AND ts>=?",
                        (ip, ahora - 86400.0)).fetchone()["c"]
        if int(n) >= REGISTRO_CUPO_DIA:
            raise HTTPException(429, f"cupo diario de registros por IP alcanzado "
                                     f"({n}/{REGISTRO_CUPO_DIA})")
    if REGISTRO_CUPO_CLAVE > 0 and pub:
        n = con.execute("SELECT COUNT(*) c FROM registros_nodo WHERE clave_publica=? AND ts>=?",
                        (pub, ahora - 86400.0)).fetchone()["c"]
        if int(n) >= REGISTRO_CUPO_CLAVE:
            raise HTTPException(429, f"cupo de registros por clave alcanzado "
                                     f"({n}/{REGISTRO_CUPO_CLAVE} en 24 h)")
    con.execute("INSERT INTO registros_nodo(ip, clave_publica, ts) VALUES(?,?,?)",
                (ip, pub, ahora))

@app.post("/nodos/registrar")
def registrar(n: NodoIn, req: Request):
    # AUDITORIA: VRAM y TOPS son autodeclarados; se acotan para que nadie
    # infle la dificultad de la red ni su tier con valores absurdos.
    vram = max(0, min(int(n.vram_gb), 512))
    try:
        tops = float(n.tops)
    except Exception:
        tops = 0.0
    if not math.isfinite(tops):
        tops = 0.0
    tops = max(0.0, min(tops, 100_000.0))
    # AUDITORIA 2026-10-20 (minero): una clave publica con formato invalido
    # dejaba un nodo que no podia firmar nunca (y contaba como activo).
    pub = (n.clave_publica or "").strip().lower()
    if pub and not pubkey_valida(pub):
        raise HTTPException(400, "clave_publica invalida (Ed25519 hex de 32 bytes)")
    if EXIGIR_PRUEBA_REGISTRO:
        if not pub:
            raise HTTPException(400, "falta clave_publica: el registro exige prueba de clave")
        nonce = (n.reto or "").strip()
        if not nonce or len(nonce) > 64:
            raise HTTPException(401, "falta reto: pide uno en /nodos/reto")
        ahora = time.time()
        with _retos_lock:
            dato = _retos_registro.pop(nonce, None)
        if not dato or ahora - dato[0] > RETO_TTL or dato[1] != pub:
            raise HTTPException(401, "reto caducado o ya usado; pide otro")
        mensaje = f"registro:{nonce}:{dato[2]}:{pub}"
        try:
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
            Ed25519PublicKey.from_public_bytes(bytes.fromhex(pub)).verify(
                bytes.fromhex(n.firma_reto or ""), mensaje.encode())
        except Exception:
            raise HTTPException(401, "firma del reto invalida")
    # Sin billetera no se cobra: el pago se abona a la direccion, no al id del
    # nodo. Si se permitia registrarse sin direccion, lo minado quedaba
    # atrapado para siempre al perder la clave (12.394 KIA asi, visto 19-10).
    if not direccion_valida(n.direccion_cobro):
        raise HTTPException(400, "falta direccion_cobro: registra una billetera KUK... "
                                 "para poder cobrar (el pago va a la direccion, no al nodo)")
    # MISMA BILLETERA, MISMA FILA (22-10-2026): si este nombre ya existe con
    # esta misma clave publica, se reutiliza su identidad en vez de crear otra.
    # Antes cada registro insertaba una fila nueva con el mismo nombre y el
    # registro se llenaba de "minero_pc[cuda:0]" repetidos.
    nombre_nodo = n.nombre[:64]
    previo = None
    if pub:
        previo = con.execute(
            "SELECT id FROM nodos WHERE nombre=? AND clave_publica=? "
            "AND COALESCE(retirado,0)=0 ORDER BY COALESCE(tareas_ok,0) DESC LIMIT 1",
            (nombre_nodo, pub)).fetchone()
    nid = previo["id"] if previo else uuid.uuid4().hex[:12]
    ip = _ip_cliente(req)
    with _escritura():   # AUDITORIA: antes escribia sin lock (500 con 2 GPUs)
        _cupo_registro(ip, pub)
        if n.prueba_gpu:
            con.execute("INSERT INTO gpu_pruebas(nodo_id,prueba,creado) VALUES(?,?,?)",
                        (nid, n.prueba_gpu[:2000], time.ctime()))
        dir_cobro = n.direccion_cobro.strip().upper() if direccion_valida(n.direccion_cobro) else ""
        # AUDITORIA 2026-10-20 (minero): visto=0. Antes el solo hecho de
        # registrarse marcaba al nodo como activo 24 h, asi que un script podia
        # inflar los TOPS de la red (y la dificultad) sin minar ni una tarea.
        # Ahora solo cuenta cuando pide trabajo, y para eso hace falta firmar.
        dispositivo = _device_declarado(n.device)
        if previo:
            # Datos de la GPU se actualizan; tareas, reputacion, visto y
            # castigos NO se tocan (eso es historial del nodo).
            con.execute("UPDATE nodos SET vram_gb=?, tier=?, tops=?, tops_estimado=?, "
                        "ref=?, direccion_cobro=?, device=? WHERE id=?",
                        (vram, tier(vram), tops, 1 if n.tops_estimado else 0,
                         n.ref[:64], dir_cobro, dispositivo, nid))
        else:
            con.execute("INSERT INTO nodos(id,nombre,vram_gb,tier,reputacion,ref,creado,tops,clave_publica,tops_estimado,direccion_cobro,visto,device) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (nid, nombre_nodo, vram, tier(vram), 1.0, n.ref[:64], time.ctime(), tops,
                         pub, 1 if n.tops_estimado else 0, dir_cobro, 0.0, dispositivo))
    return {"id": nid, "tier": tier(vram), "max_supply": MAX_SUPPLY, "tops_registrados": tops,
            "autenticado": bool(pub), "direccion_cobro": dir_cobro}

class CobroIn(BaseModel):
    direccion: str
    ts: int = 0
    firma: str = ""

class TelemetriaIn(BaseModel):
    nodo_id: str
    device: str = ""
    metricas: str = ""
    ts: int = 0
    firma: str = ""

class ReinicioIn(BaseModel):
    ts: int = 0
    firma: str = ""


@app.post("/nodos/{nodo_id}/reinicio")
def nodo_reinicio(nodo_id: str, r: ReinicioIn):
    """El nodo avisa de que acaba de arrancar: sus tareas en vuelo son de un
    proceso anterior y no las va a entregar nunca.

    FALLO REAL (21-10-2026): al relanzar el minero, las tareas que tenia
    'ejecutando' el proceso muerto seguian contandole en el cupo de
    concurrencia (4 por nodo). El nodo pedia trabajo, el Core le decia que ya
    tenia demasiadas, y se quedaba sin minar hasta ABANDONO_SEG (30 minutos).
    Medido: 8 tareas colgadas y cero pagos tras reiniciar el minero.

    Firma obligatoria sobre "reinicio:<nodo>:<ts>": solo el dueno del nodo
    puede soltar sus propias tareas, y no sirve para tocar las de otro.
    """
    node = con.execute("SELECT * FROM nodos WHERE id=?", (nodo_id,)).fetchone()
    if not node:
        raise HTTPException(404, "nodo no registrado")
    pub = (node["clave_publica"] or "") if "clave_publica" in node.keys() else ""
    if not pub:
        raise HTTPException(401, "nodo sin clave registrada")
    try:
        ts_f = float(r.ts or 0)
    except Exception:
        raise HTTPException(401, "ts invalido")
    if abs(time.time() - ts_f) > VENTANA_FIRMA:
        raise HTTPException(401, "firma caducada (revisa el reloj)")
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(pub)).verify(
            bytes.fromhex(r.firma or ""), f"reinicio:{nodo_id}:{int(ts_f)}".encode())
    except Exception:
        raise HTTPException(401, "firma invalida")
    liberadas = 0
    with _escritura():
        cur = con.execute("UPDATE tareas SET estado='queued', nodo_id=NULL, asignado=NULL "
                          "WHERE estado='ejecutando' AND nodo_id=?", (nodo_id,))
        liberadas = int(cur.rowcount or 0)
    if liberadas:
        print(f"[KUKIA Core] reinicio de {nodo_id}: {liberadas} tarea(s) devueltas a la cola",
              flush=True)
    return {"ok": True, "liberadas": liberadas}


class ResultadoIn(BaseModel):
    """Resultado por cuerpo JSON (la query string tiene limite de URL)."""
    tid: str
    nodo_id: str
    metrica: str
    hash_res: str = ""
    firma: str = ""
    device: str = ""
    duracion_ms: float = 0.0
    telemetria: str = ""

@app.post("/nodos/{nodo_id}/cobro")
def actualizar_cobro(nodo_id: str, c: CobroIn):
    """Cambia la direccion de cobro de un nodo ya registrado.

    Firma obligatoria con la clave del propio nodo sobre
    "cobro:<nodo>:<direccion>:<ts>" y ventana de 5 minutos: sin la clave no
    se puede desviar el pago de otro minero.
    """
    d = (c.direccion or "").strip().upper()
    if not direccion_valida(d):
        raise HTTPException(400, "direccion invalida (formato KUK + 38 hex)")
    if not c.ts or abs(time.time() - float(c.ts)) > 300:
        raise HTTPException(400, "firma caducada, reintenta")
    node = con.execute("SELECT clave_publica FROM nodos WHERE id=?", (nodo_id,)).fetchone()
    pub = ((node["clave_publica"] if node else "") or "").strip()
    if not pub:
        raise HTTPException(401, "nodo sin clave registrada")
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(pub)).verify(
            bytes.fromhex(c.firma), f"cobro:{nodo_id}:{d}:{c.ts}".encode())
    except Exception:
        raise HTTPException(401, "firma invalida")
    with _escritura():
        con.execute("UPDATE nodos SET direccion_cobro=? WHERE id=?", (d, nodo_id))
    return {"ok": True, "nodo_id": nodo_id, "direccion_cobro": d}

@app.post("/nodos/telemetria")
def nodos_telemetria(t: TelemetriaIn):
    """Latido de GPU firmado por el nodo (utilizacion real, VRAM y ultimo ms).

    No paga ni mueve saldos. La firma cubre el hash del JSON de metricas, para
    que nadie pueda inyectar cifras falsas en el panel de otro nodo. Se guarda
    una sola fila por nodo/GPU (UPSERT): no crece con el tiempo.
    """
    if not (1 <= len(t.nodo_id) <= 64):
        raise HTTPException(400, "nodo_id invalido")
    if not t.metricas or len(t.metricas) > 2048:
        raise HTTPException(400, "telemetria vacia o demasiado grande (max 2 KB)")
    if len(t.device) > 32:
        raise HTTPException(400, "device demasiado largo (max 32)")
    # Sin este filtro un nodo podia inventar miles de nombres de device y
    # llenar gpu_estado (una fila por nombre). Solo CPU o cuda:<0-99>.
    dev = (t.device or "").strip().lower()
    if dev and dev != "cpu" and not (
            dev.startswith("cuda:") and dev[5:].isdigit() and 0 <= int(dev[5:]) <= 99):
        raise HTTPException(400, "device invalido (cpu o cuda:0..99)")
    if not t.ts or abs(time.time() - float(t.ts)) > VENTANA_FIRMA:
        raise HTTPException(400, "telemetria caducada, revisa el reloj")
    node = con.execute("SELECT clave_publica FROM nodos WHERE id=?", (t.nodo_id,)).fetchone()
    pub = ((node["clave_publica"] if node else "") or "").strip().lower()
    if not pub:
        raise HTTPException(401, "nodo sin clave registrada (telemetria firmada)")
    try:
        h = hashlib.sha256(t.metricas.encode()).hexdigest()
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(pub)).verify(
            bytes.fromhex(t.firma),
            f"telemetria:{t.nodo_id}:{int(t.ts)}:{h}".encode())
    except Exception:
        raise HTTPException(401, "firma de telemetria invalida")
    try:
        datos = json.loads(t.metricas)
        if not isinstance(datos, dict) or len(datos) > 64:
            raise ValueError
    except Exception:
        raise HTTPException(400, "metricas debe ser un objeto JSON (max 64 campos)")
    with _escritura():
        con.execute("CREATE TABLE IF NOT EXISTS gpu_estado(nodo_id TEXT, device TEXT, "
                    "metricas TEXT, ts REAL, origen TEXT DEFAULT 'live', "
                    "PRIMARY KEY(nodo_id, device))")
        con.execute("INSERT INTO gpu_estado(nodo_id,device,metricas,ts,origen) "
                    "VALUES(?,?,?,?, 'live') "
                    "ON CONFLICT(nodo_id,device) DO UPDATE SET "
                    "metricas=excluded.metricas, ts=excluded.ts, origen='live'",
                    (t.nodo_id, dev, t.metricas, float(t.ts)))
        # NODO ANTIGUO SIN `device` (23-10-2026): los que se dieron de alta
        # antes de existir la columna se quedaban con device='' y el Core los
        # tomaba por CPU por el plan B (VRAM 0 / TOPS 0), sin mandarles
        # difusiones. El latido trae el dispositivo de verdad: se guarda aqui,
        # sin tocar tareas, reputacion ni pagos.
        if dev:
            fila = con.execute("SELECT device FROM nodos WHERE id=?", (t.nodo_id,)).fetchone()
            if fila is not None and (fila["device"] or "") != dev:
                con.execute("UPDATE nodos SET device=? WHERE id=?", (dev, t.nodo_id))
    return {"ok": True, "nodo_id": t.nodo_id, "device": dev}

@app.get("/nodos")
def nodos(limite: int = 200, activos: int = 0):
    """Lista de nodos ACOTADA (por defecto 200, max 1000).

    AUDITORIA 2026-10-20: antes devolvia todas las filas. Con el registro
    abierto, 43.000 nodos/dia son ~14 MB por consulta y el panel consulta cada
    2 s: un DoS trivial. El total real esta en /stats (campo 'nodos').
    """
    limite = max(1, min(1000, int(limite or 200)))
    # Los retirados (mismo nombre, identidad vieja) no salen: el registro
    # muestra una fila por nodo de verdad.
    sql = "SELECT * FROM nodos WHERE COALESCE(retirado,0)=0"
    args = []
    if activos:
        sql += " AND COALESCE(visto,0)>=?"
        args.append(time.time() - 86400.0)
    sql += " ORDER BY COALESCE(visto,0) DESC, creado DESC LIMIT ?"
    args.append(limite)
    return [dict(r) for r in con.execute(sql, args).fetchall()]

@app.get("/nodos/{nodo_id}")
def nodo_detalle(nodo_id: str):
    """Un nodo concreto: lo usa el worker para reutilizar su identidad sin
    descargar la lista entera."""
    row = con.execute("SELECT * FROM nodos WHERE id=?", (nodo_id,)).fetchone()
    if not row:
        raise HTTPException(404, "nodo no registrado")
    return dict(row)

@app.get("/modelos/{nombre}")
def descargar_modelo(nombre: str):
    """Punto 5: el Core sirve el modelo de referencia; el minero lo baja si le falta."""
    seguro = Path(nombre).name
    ruta = MODELOS_DIR / seguro
    if not ruta.is_file():
        raise HTTPException(404, "modelo no disponible en el Core")
    from fastapi.responses import FileResponse
    return FileResponse(str(ruta), filename=seguro)

@app.post("/tareas/crear")
def crear_tarea(t: TareaIn, _ok: None = Depends(exigir_admin)):
    tid = uuid.uuid4().hex[:12]
    params = validar_params(t.tipo, dict(t.params or {}))
    cola = t.cola if t.cola in ("A", "B", "C") else "B"
    with _escritura():
        con.execute("INSERT INTO tareas(id,tipo,params,estado,creado,cola,campana,prioridad,vram_min) "
                    "VALUES(?,?,?,?,?,?,?,?,?)",
                    (tid, t.tipo, json.dumps(params), "queued", time.ctime(),
                     cola, t.campana[:64], float(t.prioridad), int(t.vram_min)))
    # Se devuelven los params YA validados (con los topes aplicados): asi el
    # cliente sabe exactamente que va a calcular y se puede auditar el recorte.
    return {"tarea_id": tid, "estado": "queued", "tipo": t.tipo, "params": params}

# ============================================================
#  AUDITORIA: recuperacion en hilo propio
#  Antes vivia SOLO dentro de /tareas/pendiente: si nadie pedia trabajo,
#  las tareas quedaban 'ejecutando' para siempre (habia 41 asi, de 50 h).
# ============================================================
def liberar_atascadas(max_abandonos: int = MAX_ABANDONOS, forzar: bool = False):
    """Devuelve a la cola las tareas 'ejecutando' cuyo asignado es viejo
    (o nulo, de versiones anteriores). Devuelve (recuperadas, cerradas)."""
    limite = time.time() - (0 if forzar else ABANDONO_SEG)
    with _pago_lock:
        try:
            con.execute("BEGIN IMMEDIATE")
        except Exception:
            return (0, 0)
        try:
            _a_cerrar = [r["id"] for r in con.execute(
                "SELECT id FROM tareas WHERE estado='ejecutando' AND COALESCE(asignado,0) < ? "
                "AND COALESCE(intentos,0)+1 >= ?", (limite, max_abandonos)).fetchall()]
            cerradas = con.execute(
                "UPDATE tareas SET estado='fallo', fin=? "
                "WHERE estado='ejecutando' AND COALESCE(asignado,0) < ? "
                "AND COALESCE(intentos,0)+1 >= ?", (time.ctime(), limite, max_abandonos))
            # Si esas tareas eran de un cliente, el dinero retenido vuelve. Sin
            # esto, un nodo que abandona se queda el dinero del cliente.
            for _tid in _a_cerrar:
                _devolver_prepago(_tid, "nodo abandonado")
            recuperadas = con.execute(
                "UPDATE tareas SET estado='queued', nodo_id=NULL, asignado=NULL, "
                "intentos=COALESCE(intentos,0)+1 "
                "WHERE estado='ejecutando' AND COALESCE(asignado,0) < ?", (limite,))
            # Nodos sin historial: 2 min, sin contar intento.
            limite_nuevo = time.time() - (0 if forzar else ABANDONO_SEG_NUEVO)
            nuevas = con.execute(
                "UPDATE tareas SET estado='queued', nodo_id=NULL, asignado=NULL "
                "WHERE estado='ejecutando' AND COALESCE(asignado,0) < ? "
                "AND nodo_id IN (SELECT id FROM nodos WHERE COALESCE(tareas_ok,0)=0)",
                (limite_nuevo,))
            con.commit()
            return (recuperadas.rowcount + nuevas.rowcount, cerradas.rowcount)
        except Exception:
            try:
                _rollback_seguro()
            except Exception:
                pass
            return (0, 0)

def _bucle_recuperacion():
    while True:
        try:
            r, c = liberar_atascadas()
            if r or c:
                print(f"[KUKIA Core] recuperacion: {r} a la cola, {c} cerradas como fallo", flush=True)
        except Exception:
            pass
        time.sleep(60)

threading.Thread(target=_bucle_recuperacion, daemon=True).start()

# AUDITORIA: se acepta GET (clientes antiguos) y POST, pero en los dos casos
# hace falta la firma del nodo. Antes bastaba un id inventado.
@app.get("/tareas/pendiente")
@app.post("/tareas/pendiente")
def pendiente(nodo_id: str, excluir: str = "", colas: str = "", ts: float = 0.0, firma: str = "",
              nonce: str = "", solo_primaria: bool = False):
    """Asignacion ATOMICA: SELECT + UPDATE en una transaccion con lock.

    AUDITORIA: exige firma Ed25519 del nodo sobre "pendiente:<nodo_id>:<ts>"
    (ts en segundos, valido VENTANA_FIRMA). Antes cualquiera con un id
    inventado se llevaba tareas de la cola y las dejaba colgadas.

    Nonce opcional (worker nuevo): si viene, la firma es sobre
    "pendiente:<nodo_id>:<ts>:<nonce>" y ese nonce no se puede repetir en
    10 minutos. Sin nonce se mantiene el mensaje antiguo (compatibilidad).

    POLITICA DE PRIORIDAD: cola A > B > C; dentro de cola, mayor prioridad
    antes; a igual, mas antigua antes. Solo tareas con vram_min <= VRAM del
    nodo y que ese nodo no haya fallado ya. 'excluir' = campanas separadas
    por comas. SIN desalojo: lo que ya ejecuta no se interrumpe.

    'solo_primaria=1' pide trabajo PROPIO y no verificacion ajena. Lo usan
    las herramientas que necesitan una tarea concreta (benchmark, pruebas);
    el minero normal no lo manda y participa en la ejecucion redundante.
    """
    node = con.execute("SELECT * FROM nodos WHERE id=?", (nodo_id,)).fetchone()
    if not node:
        raise HTTPException(404, "nodo no registrado")
    if PENDIENTE_EXIGIR_FIRMA:
        pub = (node["clave_publica"] or "") if "clave_publica" in node.keys() else ""
        if not pub:
            raise HTTPException(401, "nodo sin clave registrada (firma obligatoria)")
        if not firma:
            raise HTTPException(401, "falta firma del nodo")
        try:
            ts_f = float(ts)
        except Exception:
            raise HTTPException(401, "ts invalido")
        if abs(time.time() - ts_f) > VENTANA_FIRMA:
            raise HTTPException(401, "firma caducada (revisa el reloj)")
        if nonce:
            if not (8 <= len(nonce) <= 64) or any(
                    not (c.isalnum() or c in "-_") for c in nonce):
                raise HTTPException(400, "nonce invalido")
        mensaje = f"pendiente:{nodo_id}:{int(ts_f)}"
        if nonce:
            mensaje += f":{nonce}"
        try:
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
            Ed25519PublicKey.from_public_bytes(bytes.fromhex(pub)).verify(
                bytes.fromhex(firma), mensaje.encode())
        except Exception:
            raise HTTPException(401, "firma invalida")
        if nonce and not _nonce_nuevo(nodo_id, nonce):
            raise HTTPException(401, "nonce repetido (peticion reutilizada)")
    try:
        castigo = float(node["castigado_hasta"] or 0.0)
    except Exception:
        castigo = 0.0
    if castigo > time.time():
        raise HTTPException(429, f"nodo castigado por resultados incorrectos durante "
                                 f"{int(castigo - time.time())} s")
    # 23-10-2026: una CPU no recibe difusiones. Medido: 17,3 s por difusion en
    # CPU contra 0,8 s en GPU. Matriz e inferencia siguen abiertas para ella.
    es_cpu = _nodo_es_cpu(node)
    # Marca de actividad: solo los nodos vistos en 24 h cuentan para TOPS.
    try:
        with _escritura():
            con.execute("UPDATE nodos SET visto=? WHERE id=?", (time.time(), nodo_id))
    except Exception:
        pass
    with _pago_lock:
        try:
            con.execute("BEGIN IMMEDIATE")
        except Exception:
            raise HTTPException(503, "Core ocupado, reintentar")
        try:
            # Recuperacion oportunista (el hilo lo hace igual cada 60 s)
            con.execute("UPDATE tareas SET estado='queued', nodo_id=NULL, asignado=NULL, "
                        "intentos=COALESCE(intentos,0)+1 "
                        "WHERE estado='ejecutando' AND COALESCE(asignado,0) < ?",
                        (time.time() - ABANDONO_SEG,))
            # Nodos sin trabajo pagado: vencen a los 2 min y no acumulan
            # intentos (no es un fallo, es un nodo lento o un sybil).
            con.execute("UPDATE tareas SET estado='queued', nodo_id=NULL, asignado=NULL "
                        "WHERE estado='ejecutando' AND COALESCE(asignado,0) < ? "
                        "AND nodo_id IN (SELECT id FROM nodos WHERE COALESCE(tareas_ok,0)=0)",
                        (time.time() - ABANDONO_SEG_NUEVO,))
            # Tope de tareas simultaneas por nodo (anti-acaparamiento).
            en_curso = con.execute("SELECT COUNT(*) c FROM tareas WHERE estado='ejecutando' "
                                   "AND nodo_id=?", (nodo_id,)).fetchone()["c"]
            # Las replicas reclamadas tambien ocupan turno: si no, un nodo
            # podria llenarse de replicas sin minar nada propio.
            en_curso += con.execute("SELECT COUNT(*) c FROM tareas_replica WHERE nodo_id=? "
                                    "AND estado='reclamando'", (nodo_id,)).fetchone()["c"]
            cap = MAX_TAREAS_NODO if int(node["tareas_ok"] or 0) > 0 else MAX_TAREAS_NODO_NUEVO
            if int(en_curso or 0) >= cap:
                _rollback_seguro()
                return {"tarea": None, "motivo": "limite de tareas simultaneas por nodo"}
            vram = int(node["vram_gb"] or 0)
            excl = [e.strip()[:64] for e in (excluir or "").split(",") if e.strip()]
            _q = ("SELECT id,tipo,params,cola,campana FROM tareas WHERE estado='queued' "
                  "AND COALESCE(vram_min,0) <= ? "
                  "AND COALESCE(nodos_intento,'') NOT LIKE ? ")
            _args = [vram, f"%{nodo_id}%"]
            if es_cpu:
                _q += "AND COALESCE(tipo,'') <> 'difusion_calor' "
            if excl:
                _q += "AND COALESCE(campana,'') NOT IN (%s) " % ",".join("?" * len(excl))
                _args += excl
            _colas = [c.strip().upper() for c in (colas or "").split(",")
                      if c.strip().upper() in ("A", "B", "C")]
            if _colas:
                _q += "AND COALESCE(cola,'B') IN (%s) " % ",".join("?" * len(_colas))
                _args += _colas
            _q += ("ORDER BY CASE COALESCE(cola,'B') WHEN 'A' THEN 0 WHEN 'B' THEN 1 ELSE 2 END, "
                   "COALESCE(prioridad,0) DESC, creado LIMIT 1")
            if REPLICA_ACTIVA and not solo_primaria:
                rep = _reclamar_replica(nodo_id, vram, sin_difusion=es_cpu)
                if rep is not None:
                    con.commit()
                    return {"tarea": {"id": rep[0], "tipo": rep[1], "params": rep[2]},
                            "replica": True}
            row = con.execute(_q, _args).fetchone()
            if not row:
                _rollback_seguro()
                return {"tarea": None}
            chk = con.execute("SELECT estado FROM tareas WHERE id=?", (row["id"],)).fetchone()
            if not chk or chk["estado"] != "queued":
                _rollback_seguro()
                return {"tarea": None}
            con.execute("UPDATE tareas SET estado='ejecutando', nodo_id=?, asignado=? WHERE id=?",
                        (nodo_id, time.time(), row["id"]))
            try:
                _cola = row["cola"] or "B"
            except Exception:
                _cola = "B"
            con.execute("INSERT INTO planificador_log(tarea_id,nodo_id,cola,motivo,ts) VALUES(?,?,?,?,?)",
                        (row["id"], nodo_id, _cola,
                         f"A>B>C,prioridad,vram_min_ok(vram={vram},excl={len(excl)})", time.ctime()))
            con.commit()
            return {"tarea": {"id": row["id"], "tipo": row["tipo"], "params": json.loads(row["params"])}}
        except HTTPException:
            raise
        except Exception as e:
            try:
                _rollback_seguro()
            except Exception:
                pass
            raise _error_escritura(e, "error interno asignando tarea")

def _castigar_nodo(tid: str, nid: str) -> float:
    """Penalizacion + perdida de reputacion + bloqueo temporal de un nodo.

    Se usa para el primario que miente y para el que verifica y tambien
    miente. Cobra de la MISMA cuenta a la que se le paga (_cuenta_de), que era
    el fallo original: se miraba el saldo del id y nunca habia nada.
    """
    n = con.execute("SELECT * FROM nodos WHERE id=?", (nid,)).fetchone()
    if not n:
        return 0.0
    cuenta = _cuenta_de(nid)
    saldo = float(con.execute("SELECT COALESCE(SUM(kuki),0) s FROM ledger WHERE nodo_id=?",
                              (cuenta,)).fetchone()["s"] or 0.0)
    castigo = min(castigo_por_fallo(float(n["tier"] or 1.0)), max(0.0, saldo))
    if castigo > 0:
        _asiento(tid, cuenta, -castigo, "penalizacion por resultado incorrecto")
    con.execute("UPDATE nodos SET reputacion=max(0,reputacion-0.5), castigado_hasta=?, "
                "fallos=COALESCE(fallos,0)+1 WHERE id=?", (time.time() + CASTIGO_SEG, nid))
    return castigo


@app.post("/tareas/resultado")
def resultado(tid: str, nodo_id: str, metrica: str, hash_res: str = "", firma: str = "",
              device: str = "", duracion_ms: float = 0.0, telemetria: str = ""):
    """El Core recalcula la tarea y compara el RESULTADO EXACTO (trustless).

    AUDITORIA: el orden importa. ANTES se recalculaba el resultado y DESPUES
    se miraba la firma: cualquiera (sin firma) podia quemar la CPU del Core
    con tareas pesadas. Ahora: 1) existe la tarea, 2) firma del nodo,
    3) recien entonces se recalcula, 4) transaccion de pago.

    Un resultado incorrecto ya NO condena la tarea a la primera: se cuenta
    el intento, ese nodo no la vuelve a recibir, se le castiga (reputacion,
    penalizacion en KIA y unos minutos sin tareas) y solo se cierra como
    fallo tras MAX_FALLOS intentos distintos.
    """
    # 0. TOPES DE ENTRADA (antes de tocar la base). Un minero malicioso podia
    # mandar una 'metrica' de megabytes o un duracion_ms=NaN y ensuciar la DB
    # o las medias del panel. Se corta antes de verificar la firma: es barato.
    if not (1 <= len(str(tid)) <= 64) or not (1 <= len(str(nodo_id)) <= 64):
        raise HTTPException(400, "tid o nodo_id invalido")
    if not metrica or len(metrica) > 16384:
        raise HTTPException(400, "metrica vacia o demasiado grande (max 16 KB)")
    if len(hash_res) > 128:
        raise HTTPException(400, "hash_res demasiado largo (max 128)")
    if len(str(device)) > 32:
        raise HTTPException(400, "device demasiado largo (max 32)")
    if len(telemetria) > 8192:
        raise HTTPException(400, "telemetria demasiado grande (max 8 KB)")
    try:
        dur = float(duracion_ms)
    except Exception:
        raise HTTPException(400, "duracion_ms invalida")
    if not math.isfinite(dur) or not (0.0 <= dur <= 86_400_000.0):
        raise HTTPException(400, "duracion_ms fuera de rango")
    duracion_ms = dur

    # 1. Existencia y tipo (lectura sin lock)
    task = con.execute("SELECT * FROM tareas WHERE id=?", (tid,)).fetchone()
    if not task:
        raise HTTPException(404, "tarea no encontrada")
    params = json.loads(task["params"])
    tipo = task["tipo"]

    # 2. AUTENTICACION PRIMERO (vale para correctos E incorrectos)
    node = con.execute("SELECT * FROM nodos WHERE id=?", (nodo_id,)).fetchone()
    if not node:
        raise HTTPException(404, "nodo no registrado")
    pub = (node["clave_publica"] or "") if "clave_publica" in node.keys() else ""
    if EXIGIR_FIRMA_GLOBAL and not pub:
        raise HTTPException(401, "nodo sin clave registrada (firma obligatoria)")
    if pub:
        if not firma:
            raise HTTPException(401, "falta firma del nodo (suplantacion bloqueada)")
        try:
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
            Ed25519PublicKey.from_public_bytes(bytes.fromhex(pub)).verify(
                bytes.fromhex(firma), f"{tid}:{metrica}".encode())
        except Exception:
            raise HTTPException(401, "firma invalida (suplantacion bloqueada)")
    # Rechazo temprano si la tarea no es pagable (evita trabajo inutil)
    if task["estado"] == "ok":
        raise HTTPException(409, "tarea ya pagada (doble pago bloqueado)")
    if task["estado"] == "fallo":
        raise HTTPException(409, "tarea ya cerrada con fallo (no reintentable)")
    if task["estado"] != "ejecutando":
        raise HTTPException(409, f"tarea en estado '{task['estado']}', no pagable")
    if task["nodo_id"] and task["nodo_id"] != nodo_id:
        raise HTTPException(409, "tarea asignada a otro nodo")

    # 3. Verificacion (ya autenticado), con limite de concurrencia.
    # AUDITORIA 2026-10-20: una entrega falsa de 200 bytes obliga a recalcular
    # la tarea entera (~32 s de CPU en difusion si cae a CPU). Sin limite, 40
    # peticiones simultaneas saturan la maquina. Si no hay hueco, la tarea
    # vuelve a la cola y NO se castiga al nodo (no ha hecho nada malo).
    # 3.a EJECUCION REDUNDANTE: si otro nodo ya entrego el mismo resultado y
    # coincide, NO se recalcula. Solo se muestrea 1 de cada AUDITORIA_1_DE
    # para que una colusion entre dos nodos tenga probabilidad de caer.
    replica = None
    try:
        replica = con.execute("SELECT * FROM tareas_replica WHERE tarea_id=?",
                              (tid,)).fetchone()
    except Exception:
        replica = None
    # MARGEN PARA LA REPLICA: si hay una verificacion EN CURSO de esta tarea,
    # se espera unos segundos a que llegue en vez de recalcular. El verificador
    # arranca despues que el primario, asi que su resultado llega unas decimas
    # mas tarde: sin esta espera el Core recalculaba igual y el 30 % del
    # premio que iba a cobrar el verificador se quedaba sin pagar.
    if REPLICA_ESPERA_SEG > 0 and replica is not None \
            and not (replica["metrica"] or "").strip() \
            and (time.time() - float(replica["asignado"] or 0.0)) <= REPLICA_TTL:
        limite = time.time() + REPLICA_ESPERA_SEG
        while time.time() < limite:
            time.sleep(0.1)
            try:
                replica = con.execute("SELECT * FROM tareas_replica WHERE tarea_id=?",
                                      (tid,)).fetchone()
            except Exception:
                break
            if replica is None or (replica["metrica"] or "").strip():
                break
    _rep_ok = False
    esperado = None
    verificado_por = "recalculo"
    # Muestreo de esta entrega: 1 de cada 10 en regimen, 1 de cada 3 si alguno
    # de los dos nodos aun no tiene historial (23-10-2026).
    replica_node = None
    if replica is not None:
        try:
            replica_node = con.execute("SELECT * FROM nodos WHERE id=?",
                                       (replica["nodo_id"],)).fetchone()
        except Exception:
            replica_node = None
    frec_auditoria = _frecuencia_auditoria(node, replica_node)
    if _es_replicable(tipo, params) and replica is not None \
            and (replica["metrica"] or "").strip() \
            and _coinciden(tipo, params, metrica, replica["metrica"]) \
            and not _toca_auditoria(tid, frec_auditoria):
        ok = True
        _rep_ok = True
        verificado_por = "replica"
    else:
        if not _verif_sem.acquire(timeout=VERIF_TIMEOUT):
            try:
                with _escritura():
                    con.execute("UPDATE tareas SET estado='queued', nodo_id=NULL, asignado=NULL "
                                "WHERE id=? AND estado='ejecutando' AND nodo_id=?", (tid, nodo_id))
            except Exception:
                pass
            raise HTTPException(503, "Core ocupado verificando; la tarea vuelve a la cola")
        try:
            try:
                esperado = computar_tarea(tipo, params)
                _tol = (tipo in ("embeddings", "lote_embeddings") and ONNXCPU is not None) or tipo == "difusion_calor"
                if esperado is None and not _tol:
                    raise HTTPException(400, "tipo de tarea no soportado")

                def _cmp(valor):
                    if tipo in ("embeddings", "lote_embeddings") and ONNXCPU is not None:
                        return _verificar_embeddings(tipo, params, valor)
                    if tipo == "difusion_calor":
                        return _verificar_difusion(params, valor)
                    return str(valor).strip() == str(esperado).strip()

                ok = _cmp(metrica)
                if replica is not None and (replica["metrica"] or "").strip():
                    _rep_ok = _cmp(replica["metrica"])
                verificado_por = "auditoria" if (replica is not None and _rep_ok) else "recalculo"
            except MemoryError:
                # AUDITORIA 21-10-2026: el Core se quedo sin RAM al recalcular
                # (numpy no pudo reservar 4 MB) y el nodo cobraba un 500, con la
                # tarea atascada en 'ejecutando'. No es culpa del minero ni se
                # puede pagar sin comprobar: vuelve a la cola y se responde 503.
                try:
                    with _escritura():
                        con.execute("UPDATE tareas SET estado='queued', nodo_id=NULL, asignado=NULL "
                                    "WHERE id=? AND estado='ejecutando' AND nodo_id=?", (tid, nodo_id))
                except Exception:
                    pass
                print(f"[KUKIA] sin memoria para verificar {tid}: vuelve a la cola", flush=True)
                raise HTTPException(503, "sin memoria para verificar ahora; la tarea vuelve a la cola")
        finally:
            _verif_sem.release()

    # 4. TRANSACCION ATOMICA con bloqueo de escritura inmediato.
    with _pago_lock:
        try:
            con.execute("BEGIN IMMEDIATE")
        except Exception:
            raise HTTPException(503, "Core ocupado, reintentar")
        try:
            con.execute("UPDATE nodos SET visto=? WHERE id=?", (time.time(), nodo_id))
            task2 = con.execute("SELECT * FROM tareas WHERE id=?", (tid,)).fetchone()
            if task2 is None:
                # AUDITORIA 21-10-2026 (500 con el nodo existiendo): la tarea
                # puede desaparecer entre la primera lectura y esta transaccion.
                # Pasa cuando la recuperacion la cierra como 'fallo' por abandono
                # (asignado viejo) y el archivador la borra en la misma ventana
                # por ser antigua. Antes `task2["estado"]` lanzaba TypeError y el
                # minero recibia un 500 opaco; ahora es un 409 claro y NO se paga
                # nada (la tarea ya estaba cerrada, no habia premio que dar).
                _rollback_seguro()
                raise HTTPException(
                    409, "la tarea ya no existe (cerrada por abandono y archivada); "
                         "no hay nada que pagar, no reintentes")
            if task2["estado"] == "ok":
                _rollback_seguro()
                raise HTTPException(409, "tarea ya pagada (doble pago bloqueado)")
            if task2["estado"] == "fallo":
                _rollback_seguro()
                raise HTTPException(409, "tarea ya cerrada con fallo (no reintentable)")
            if task2["estado"] != "ejecutando":
                _rollback_seguro()
                raise HTTPException(409, f"tarea en estado '{task2['estado']}', no pagable")
            if task2["nodo_id"] and task2["nodo_id"] != nodo_id:
                _rollback_seguro()
                raise HTTPException(409, "tarea asignada a otro nodo")

            kuki = 0.0
            info_pago = {}
            # POTENCIA DEMOSTRADA: se paga el tier declarado solo si la GPU lo
            # ha respaldado con trabajo verificado (ver tier_verificado).
            tier_pago = tier_verificado(node)
            if ok:
                kuki, info_pago = pago_de_tarea(task2, tier_pago)
                info_pago = dict(info_pago or {})
                info_pago["tier_pagado"] = round(float(tier_pago), 3)
                info_pago["tier_declarado"] = round(float(node["tier"] or 1.0), 3)
                info_pago["verificado_por"] = verificado_por
                # Trazabilidad del muestreo: 1 de cada N, para poder auditar
                # despues cuantas tareas con replica se recalcularon de verdad.
                info_pago["auditoria_1_de"] = frec_auditoria
                # REPARTO DE LA EJECUCION REDUNDANTE: el total NO cambia. El
                # ejecutor cobra el 70 % y el que verifico el 30 %. Pagar el
                # 100 % a cada uno dispararia la emision y la fecha final
                # dejaria de ser cierta.
                kuki_rep = 0.0
                if replica is not None and _rep_ok:
                    # 12 decimales, no 6 (25-10-2026, ancla GPU-minuto): con el
                    # ancla una tarea pequena paga ~1e-6 KIA, y round(...,6)
                    # dejaba al verificador con CERO mientras el ejecutor se
                    # llevaba todo. Con _redondear_kia cada uno cobra su parte
                    # y el total sigue cuadrando al decimal.
                    kuki_rep = _redondear_kia(kuki * PARTE_REPLICA)
                    kuki = _redondear_kia(kuki - kuki_rep)
                    info_pago["parte_replica"] = kuki_rep
                # Muestra de potencia: FLOPs de la tarea / segundos CRONOMETRADOS
                # POR EL CORE (asignado -> ahora). El minero no puede acortarlo.
                _seg = 0.0
                try:
                    _asig = float(task2["asignado"] or 0.0)
                    _seg = time.time() - _asig if _asig > 0 else 0.0
                except Exception:
                    _seg = 0.0
                _g = gflops_de_tarea(task2["tipo"], task2["params"], _seg)
                if _g > 0:
                    _prev = float(node["gflops_medido"] or 0.0)
                    _nuevo = _g if _prev <= 0 else (0.8 * _prev + 0.2 * _g)
                    con.execute("UPDATE nodos SET gflops_medido=?, "
                                "potencia_muestras=COALESCE(potencia_muestras,0)+1 "
                                "WHERE id=?", (round(_nuevo, 4), nodo_id))
                con.execute("UPDATE nodos SET tareas_ok=tareas_ok+1, reputacion=min(5,reputacion+0.05) "
                            "WHERE id=?", (nodo_id,))
                h = hashlib.sha256(f"{tid}:{nodo_id}:{metrica}".encode()).hexdigest()
                con.execute("UPDATE tareas SET estado='ok', hash=?, metrica=?, fin=?, verificado=1, device=?, duracion_ms=? WHERE id=?",
                            (h, metrica[:2000], time.ctime(), device_limpio(device), float(duracion_ms or 0.0), tid))
                try:
                    con.execute("CREATE TABLE IF NOT EXISTS gpu_telemetria(id INTEGER PRIMARY KEY AUTOINCREMENT, "
                                "nodo_id TEXT, device TEXT, metricas TEXT, ts TEXT, origen TEXT)")
                    if telemetria:
                        con.execute("INSERT INTO gpu_telemetria(nodo_id,device,metricas,ts,origen) VALUES(?,?,?,?,?)",
                                    (nodo_id, device_limpio(device), telemetria[:2000], time.ctime(), "worker"))
                except Exception:
                    pass
                # Contador historico: sobrevive al archivado de tareas viejas.
                con.execute("INSERT INTO meta(clave,valor) VALUES('tareas_ok_total',1) "
                            "ON CONFLICT(clave) DO UPDATE SET valor=valor+1")
                # CLIENTE: el dinero sale del deposito del cliente, no de la
                # emision. Un encargo pagado no puede inflar el suministro.
                _de_cliente = (task2["cliente"] or "").strip()
                if _de_cliente:
                    _pagos = [(nodo_id, kuki)]
                    if kuki_rep > 0 and replica is not None:
                        _pagos.append((replica["nodo_id"], kuki_rep))
                    _pagado, _info_c = _pagar_tarea_de_cliente(
                        tid, _pagos, _de_cliente, float(task2["prepago"] or 0.0))
                    info_pago.update(_info_c)
                else:
                    _asiento(tid, _cuenta_de(nodo_id), kuki, "trabajo verificado")
                if kuki_rep > 0 and replica is not None:
                    # El verificador cobra su parte: la red pago el mismo total
                    # por tarea, repartido entre los dos que la resolvieron.
                    if not _de_cliente:
                        _asiento(tid, _cuenta_de(replica["nodo_id"]), kuki_rep,
                                 "verificacion redundante")
                    con.execute("UPDATE nodos SET tareas_ok=tareas_ok+1, "
                                "reputacion=min(5,reputacion+0.05) WHERE id=?",
                                (replica["nodo_id"],))
                    con.execute("UPDATE tareas_replica SET estado='pagada' WHERE tarea_id=?",
                                (tid,))
                elif replica is not None and (replica["metrica"] or "").strip() and not _rep_ok:
                    # Verifico y mintio: mismo castigo que a un primario falso.
                    _castigar_nodo(tid, replica["nodo_id"])
                    con.execute("DELETE FROM tareas_replica WHERE tarea_id=?", (tid,))
                _marcar_ok()
                _anotar_duracion(task2["tipo"], duracion_ms)
            elif replica is not None and _rep_ok:
                # El primario mintio y la REPLICA acerto. Se paga el trabajo
                # entero a quien dijo la verdad, se castiga al mentiroso y la
                # tarea se cierra (no vuelve a la cola).
                rep_node = con.execute("SELECT * FROM nodos WHERE id=?",
                                       (replica["nodo_id"],)).fetchone()
                kuki_r, info_pago = pago_de_tarea(task2, 
                    tier_verificado(rep_node) if rep_node else 1.0)
                info_pago = dict(info_pago or {})
                info_pago["verificado_por"] = "replica"
                info_pago["pago_al_verificador"] = True
                h = hashlib.sha256(f"{tid}:{replica['nodo_id']}:{replica['metrica']}".encode()).hexdigest()
                con.execute("UPDATE tareas SET estado='ok', hash=?, metrica=?, fin=?, "
                            "verificado=1, device=?, duracion_ms=? WHERE id=?",
                            (h, (replica["metrica"] or "")[:2000], time.ctime(),
                             device_limpio(replica["device"]),
                             float(replica["duracion_ms"] or 0.0), tid))
                con.execute("UPDATE nodos SET tareas_ok=tareas_ok+1, "
                            "reputacion=min(5,reputacion+0.05) WHERE id=?",
                            (replica["nodo_id"],))
                con.execute("INSERT INTO meta(clave,valor) VALUES('tareas_ok_total',1) "
                            "ON CONFLICT(clave) DO UPDATE SET valor=valor+1")
                _de_cliente = (task2["cliente"] or "").strip()
                if _de_cliente:
                    _pagado, _info_c = _pagar_tarea_de_cliente(
                        tid, [(replica["nodo_id"], kuki_r)], _de_cliente,
                        float(task2["prepago"] or 0.0))
                    info_pago.update(_info_c)
                else:
                    _asiento(tid, _cuenta_de(replica["nodo_id"]), kuki_r,
                             "trabajo verificado (replica)")
                con.execute("UPDATE tareas_replica SET estado='pagada' WHERE tarea_id=?",
                            (tid,))
                _castigar_nodo(tid, nodo_id)
                _marcar_ok()
            else:
                # AUDITORIA: castigo + reintento en vez de cerrar la tarea
                intentos = int(task2["intentos"] or 0) + 1
                lista = (task2["nodos_intento"] or "")
                if nodo_id not in [x for x in lista.split(",") if x]:
                    lista = (lista + "," + nodo_id).strip(",")
                # Castigo al primario que mintio (sin dejar saldo negativo).
                _castigar_nodo(tid, nodo_id)
                # Si habia replica y TAMBIEN mintio, se va con el primario.
                if replica is not None and (replica["metrica"] or "").strip():
                    _castigar_nodo(tid, replica["nodo_id"])
                # La replica se borra siempre: la tarea vuelve a la cola y no
                # puede heredar un resultado de un intento fallido.
                con.execute("DELETE FROM tareas_replica WHERE tarea_id=?", (tid,))
                if intentos >= MAX_FALLOS:
                    con.execute("UPDATE tareas SET estado='fallo', fin=?, intentos=?, nodos_intento=? WHERE id=?",
                                (time.ctime(), intentos, lista, tid))
                    # El cliente no paga lo que no se pudo verificar.
                    _devolver_prepago(tid, "resultado no verificable")
                else:
                    con.execute("UPDATE tareas SET estado='queued', nodo_id=NULL, asignado=NULL, "
                                "intentos=?, nodos_intento=? WHERE id=?", (intentos, lista, tid))
                _asiento(tid, _cuenta_de(nodo_id), 0.0, "resultado incorrecto")
            con.commit()
        except HTTPException:
            # AUDITORIA 2026-10-19: con el ledger bloqueado, _asiento lanza 503
            # DESPUES de abrir la transaccion. Sin este ROLLBACK la transaccion
            # se quedaba abierta (y el siguiente pago fallaba con 503 en bucle).
            try:
                _rollback_seguro()
            except Exception:
                pass
            raise
        except Exception as e:
            try:
                _rollback_seguro()
            except Exception:
                pass
            # AUDITORIA 2026-10-21: antes esto se comia la excepcion y solo
            # devolvia "error interno en el pago", asi que depurar un fallo
            # real (el de la ejecucion redundante) era a ciegas. Ahora queda en
            # el log con tipo y mensaje, sin datos de nadie.
            print(f"[KUKIA Core] ERROR en el pago de {tid}: {type(e).__name__}: {e}",
                  flush=True)
            raise _error_escritura(e, "error interno en el pago")
    return {"ok": ok, "kuki": kuki, "esperado": esperado, "pago": info_pago}

@app.post("/tareas/resultado_json")
def resultado_json(r: ResultadoIn):
    """Misma verificacion y pago que /tareas/resultado, con cuerpo JSON.

    AUDITORIA 2026-10-20: el resultado viajaba en la query string; con
    embeddings (JSON grande) choca con el limite de URL de los proxies y deja
    firmas en logs. La ruta antigua sigue viva para mineros ya descargados.
    """
    return resultado(r.tid, r.nodo_id, r.metrica, r.hash_res, r.firma,
                     r.device, r.duracion_ms, r.telemetria)

class ReplicaIn(BaseModel):
    """Resultado de un nodo que verifica una tarea ya asignada a otro."""
    tid: str
    nodo_id: str
    metrica: str
    firma: str = ""
    device: str = ""
    duracion_ms: float = 0.0


@app.post("/tareas/replica")
def tarea_replica(r: ReplicaIn):
    """Un nodo entrega el resultado de una REPLICA (verificacion redundante).

    Aqui no se paga: el pago sale cuando llega el primario y se comprueba que
    los dos coinciden (70 % ejecutor / 30 % verificador, total igual). Si el
    primario mintio, cobra el verificador y el mentiroso se lleva el castigo.
    """
    if not (1 <= len(str(r.tid)) <= 64) or not (1 <= len(str(r.nodo_id)) <= 64):
        raise HTTPException(400, "tid o nodo_id invalido")
    if not r.metrica or len(r.metrica) > 16384:
        raise HTTPException(400, "metrica vacia o demasiado grande (max 16 KB)")
    if len(str(r.device)) > 32:
        raise HTTPException(400, "device demasiado largo (max 32)")
    node = con.execute("SELECT * FROM nodos WHERE id=?", (r.nodo_id,)).fetchone()
    if not node:
        raise HTTPException(404, "nodo no registrado")
    pub = (node["clave_publica"] or "") if "clave_publica" in node.keys() else ""
    if pub:
        if not r.firma:
            raise HTTPException(401, "falta firma del nodo")
        try:
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
            Ed25519PublicKey.from_public_bytes(bytes.fromhex(pub)).verify(
                bytes.fromhex(r.firma), f"{r.tid}:{r.metrica}".encode())
        except Exception:
            raise HTTPException(401, "firma invalida")
    rep = con.execute("SELECT * FROM tareas_replica WHERE tarea_id=?", (r.tid,)).fetchone()
    if not rep:
        # AUDITORIA 21-10-2026: la fila de la replica se borra en cuanto la tarea
        # deja de estar 'ejecutando' (el primario entrego antes, o caduco). El
        # nodo verificador que ya habia calculado el trabajo llegaba despues y se
        # llevaba un 404 que en el minero parecia un fallo suyo: era el 65 % de
        # los 404 del Core (344 de 526 en el log). Si la tarea ya no esta en
        # ejecucion no hay nada que guardar y no hay error: se responde 200 con
        # guardada=false. Si sigue en ejecucion y no hay replica pedida, el 404
        # se queda (eso si seria un nodo inventandose trabajo).
        t = con.execute("SELECT estado FROM tareas WHERE id=?", (r.tid,)).fetchone()
        if t is None or t["estado"] != "ejecutando":
            return {"ok": True, "guardada": False,
                    "motivo": "la replica ya no aplica: la tarea se cerro"}
        raise HTTPException(404, "no hay replica pedida para esa tarea")
    if rep["nodo_id"] != r.nodo_id:
        raise HTTPException(403, "esa replica esta asignada a otro nodo")
    if (rep["metrica"] or "").strip():
        raise HTTPException(409, "replica ya entregada")
    tarea = con.execute("SELECT estado FROM tareas WHERE id=?", (r.tid,)).fetchone()
    if not tarea or tarea["estado"] != "ejecutando":
        # Misma razon que arriba: la replica llego tarde. No se guarda nada (la
        # tarea ya se cerro y su premio se repartio o se anulo), pero el nodo no
        # ha hecho nada malo y no tiene que reintentar: 200 con guardada=false.
        return {"ok": True, "guardada": False,
                "motivo": "la tarea ya no esta en ejecucion; la replica no se guarda"}
    try:
        dur = float(r.duracion_ms or 0.0)
    except Exception:
        dur = 0.0
    if not math.isfinite(dur) or not (0.0 <= dur <= 86_400_000.0):
        dur = 0.0
    with _escritura():
        con.execute("UPDATE tareas_replica SET estado='entregada', metrica=?, firma=?, "
                    "device=?, duracion_ms=?, ts=? WHERE tarea_id=? AND nodo_id=?",
                    (r.metrica[:16384], r.firma[:256], device_limpio(r.device), dur,
                     time.time(), r.tid, r.nodo_id))
        con.execute("UPDATE nodos SET visto=? WHERE id=?", (time.time(), r.nodo_id))
    return {"ok": True, "guardada": True}


@app.get("/ledger")
def ledger():
    return [dict(r) for r in con.execute("SELECT * FROM ledger ORDER BY id DESC LIMIT 200").fetchall()]

# AUDITORIA 2026-10-28 (A1): cache con un solo calculo en vuelo.
_auditoria_lock = threading.Lock()
_auditoria_locks = {"verificar": threading.Lock(), "integridad": threading.Lock()}
_auditoria_datos = {}


def _auditoria_cacheada(nombre: str, calcular):
    """Devuelve (datos, ts_del_calculo, cacheado).

    Si el veredicto tiene menos de AUDITORIA_TTL se sirve tal cual. Si toca
    recalcular y ya hay otro hilo haciendolo, no se lanza un segundo recorrido
    del ledger: se sirve el anterior (marcado como viejo por `_con_fecha`). Si
    nunca se calculo, se espera al que esta en curso.
    """
    ahora = time.time()
    with _auditoria_lock:
        guardado = _auditoria_datos.get(nombre)
        if guardado and ahora - guardado["ts"] < AUDITORIA_TTL:
            return guardado["datos"], guardado["ts"], True
        cerrojo = _auditoria_locks[nombre]
    if not cerrojo.acquire(blocking=False):
        with _auditoria_lock:
            guardado = _auditoria_datos.get(nombre)
        if guardado:
            return guardado["datos"], guardado["ts"], True
        cerrojo.acquire()
    try:
        with _auditoria_lock:
            guardado = _auditoria_datos.get(nombre)
            if guardado and time.time() - guardado["ts"] < AUDITORIA_TTL:
                return guardado["datos"], guardado["ts"], True
        datos = calcular()
        ts = time.time()
        with _auditoria_lock:
            _auditoria_datos[nombre] = {"datos": datos, "ts": ts}
        return datos, ts, False
    finally:
        cerrojo.release()


def _con_fecha(datos: dict, ts: float, cacheado: bool) -> dict:
    """Un resultado cacheado nunca se presenta como una verificacion recien hecha."""
    return {**datos, "medido_en": time.ctime(ts),
            "edad_s": round(time.time() - ts, 1), "cacheado": bool(cacheado)}


def _verificar_ledger_ahora():
    """Recorre la cadena entera. Cuesta 13-15 s con 900.000 asientos."""
    prev = ""
    n_hash = 0
    alterados = []
    for r in con.execute("SELECT id,tarea_id,nodo_id,kuki,motivo,ts,hash_prev,hash FROM ledger ORDER BY id").fetchall():
        if not r["hash"]:
            continue
        base = f"{r['tarea_id']}|{r['nodo_id']}|{r['kuki']}|{r['motivo']}|{r['ts']}|{r['hash_prev'] or ''}"
        if hashlib.sha256(base.encode()).hexdigest() != r["hash"] or (r["hash_prev"] or "") != prev:
            alterados.append(r["id"])
        prev = r["hash"]
        n_hash += 1
    total = con.execute("SELECT COUNT(*) c FROM ledger").fetchone()["c"]
    return {"asientos": total, "encadenados": n_hash, "cadena_ok": not alterados,
            "ids_alterados": alterados[:20]}


@app.get("/ledger/verificar")
def verificar_ledger():
    """AUDITORIA: comprueba la cadena de hashes del ledger. Publico y cacheado.

    Devuelve los ids que no cuadran (alterados a mano en el .db). La cadena
    empieza en el primer asiento que tiene hash (los anteriores, de versiones
    sin encadenar, se ignoran).

    AUDITORIA 2026-10-28 (A1): el veredicto se reutiliza durante AUDITORIA_TTL
    (300 s por defecto) y solo un hilo lo recalcula. La respuesta lleva
    `medido_en`, `edad_s` y `cacheado`, para que nadie confunda una foto con una
    verificacion recien hecha."""
    datos, ts, cacheado = _auditoria_cacheada("verificar", _verificar_ledger_ahora)
    return _con_fecha(datos, ts, cacheado)

@app.get("/ledger/sellos")
def ledger_sellos(limite: int = 20):
    """Ultimos sellos firmados (publico). Fijan el historial sin confiar en el Core."""
    sellos = _sellos_leidos()
    limite = max(1, min(200, int(limite or 20)))
    return {"clave_publica": _sello_pub, "total": len(sellos),
            "sellos": sellos[-limite:]}

def _integridad_ahora():
    """Cadena + sellos + si los pagos estan bloqueados (sin cache)."""
    sellos = _sellos_leidos()
    # 27-10-2026 (H-01, hipotesis de concurrencia): las lecturas van dentro
    # de UNA transaccion de solo lectura. En WAL eso les da la MISMA foto del
    # ledger; sin esto, un asiento que entra entre la pasada de la cadena y
    # la de los sellos puede hacer que la respuesta mezcle dos estados. No
    # frena a los escritores (WAL) y no cambia QUE se comprueba.
    _en_tx = True
    try:
        con.execute("BEGIN")
    except Exception:
        _en_tx = False          # ya habia transaccion en este hilo: se sigue sin foto
    try:
        est = SELLOS.estado_ledger(con) if SELLOS else {}
        cadena = (SELLOS.verificar_cadena_db(con) if SELLOS
                  else {"ok": False, "ids_alterados": []})
        # La cadena ya esta recorrida: verificar_sellos la reutiliza en vez
        # de volver a pasar por el ledger entero.
        r = (SELLOS.verificar_sellos(con, sellos, _sello_pub, cadena=cadena)
             if SELLOS else {"ok": False, "problemas": []})
    finally:
        if _en_tx:
            try:
                con.execute("COMMIT")
            except Exception:
                try:
                    con.execute("ROLLBACK")
                except Exception:
                    pass
    return {
        "cadena_ok": bool(cadena.get("ok")),
        "ids_alterados": cadena.get("ids_alterados", [])[:20],
        "asientos_vivos": int(est.get("asientos", 0)),
        "sellos": len(sellos),
        "ultimo_sello": sellos[-1] if sellos else None,
        "sellos_ok": bool(r.get("ok")),
        "problemas": (r.get("problemas") or [])[:20],
        "pagos_bloqueados": LEDGER_ROTO,
        "clave_publica": _sello_pub,
    }


@app.get("/ledger/integridad")
def ledger_integridad():
    """Cadena + sellos + si los pagos estan bloqueados (cacheado 300 s).

    La respuesta lleva `medido_en`, `edad_s` y `cacheado`: un veredicto
    reutilizado nunca se presenta como recien calculado."""
    datos, ts, cacheado = _auditoria_cacheada("integridad", _integridad_ahora)
    return _con_fecha(datos, ts, cacheado)

# ============================================================
#  DIFICULTAD ADAPTATIVA (potencia de LA RED KUKIA, no mundial)
#  + RECOMPENSA DINAMICA (garantiza agotar justo en la fecha final)
# ============================================================
# Tiempo objetivo por tarea (media, sin checksum). Con tareas de milisegundos
# el premio por tarea quedaba en 0,004 KIA; con tareas de ~2 s el ritmo baja y
# cada tarea paga mas, sin tocar el presupuesto de KIA/min ni la fecha final.
OBJETIVO_TAREA_MS = float(os.environ.get("KUKIA_TAREA_MS", "2000"))
ESCALA_MIN, ESCALA_MAX = 0.5, 8.0
_dur_reciente = collections.deque(maxlen=800)   # ms de tareas con FLOPs
_ESCALA_TAREA = 1.0
_escala_pendiente = False
_escala_cargada = False


def _anotar_duracion(tipo: str, ms: float) -> None:
    """Guarda la duracion de una tarea util (checksum no cuenta: no tiene FLOPs)."""
    try:
        ms = float(ms or 0.0)
    except (TypeError, ValueError):
        return
    if tipo == "checksum" or ms <= 0:
        return
    _dur_reciente.append(ms)


def escala_tarea() -> float:
    """Factor de tamano que acerca la duracion media de tarea al objetivo.

    Ajuste suave (+-15 % por vuelta) y solo con >=30 muestras. Sube el tamano
    si las tareas salen cortas, lo baja si se pasan. Se guarda en 'meta' desde
    el bucle de auto-tareas (aqui no se escribe: puede llamarse dentro de una
    transaccion de pago).
    """
    global _ESCALA_TAREA, _escala_pendiente, _escala_cargada
    if not _escala_cargada:
        _escala_cargada = True
        try:
            guardada = _meta_float("escala_tarea", 1.0) or 1.0
            _ESCALA_TAREA = min(ESCALA_MAX, max(ESCALA_MIN, guardada))
        except Exception:
            pass
    if len(_dur_reciente) < 30:
        return _ESCALA_TAREA
    medido = sum(_dur_reciente) / len(_dur_reciente)
    if medido <= 0:
        return _ESCALA_TAREA
    # raiz cuadrada: matriz crece ~n^3 y la difusion ~pasos; asi el ajuste no
    # oscila. Tope de +-15 % por vuelta para no dar saltos bruscos.
    ajuste = (OBJETIVO_TAREA_MS / medido) ** 0.5
    nuevo = _ESCALA_TAREA * min(1.15, max(0.85, ajuste))
    nuevo = min(ESCALA_MAX, max(ESCALA_MIN, nuevo))
    # Freno por cola: si el Core no da abasto (la verificacion cuesta lo mismo
    # que minar), no se siguen agrandando tareas; se retrocede un poco.
    try:
        pend = con.execute("SELECT COUNT(*) c FROM tareas WHERE estado='queued'").fetchone()["c"]
    except Exception:
        pend = 0
    if pend > 400:
        nuevo = min(nuevo, _ESCALA_TAREA * 0.97)
    if abs(nuevo - _ESCALA_TAREA) > 1e-3:
        _ESCALA_TAREA = nuevo
        _escala_pendiente = True
        _dur_reciente.clear()
    return _ESCALA_TAREA

REFERENCIA_TOPS = 1747.0   # red inicial (5070 + 5060 Ti): punto de calibracion
BASE_MATRIX_N = 256
BASE_INFER_N = 64

def tops_red() -> float:
    """TOPS de los nodos ACTIVOS Y CON TRABAJO VERIFICADO (24 h).

    Antes sumaba todos los registros, vivos o no: 34 nodos decian 11.192 TOPS
    cuando los reales eran ~200, y eso inflaba la dificultad 2,5x. Despues se
    filtro por 'visto', pero bastaba pedir trabajo (gratis) para figurar.
    AUDITORIA 2026-10-20: ahora hace falta al menos una tarea PAGADA. Asi un
    nodo recien registrado no cuenta hasta que demuestra trabajo real, y
    registrar nodos fantasma con tops=100.000 ya no agranda las tareas de
    toda la red (era un ataque de dificultad a coste cero).
    """
    desde = time.time() - 86400.0
    row = con.execute("SELECT COALESCE(SUM(tops),0) s FROM nodos "
                      "WHERE visto>=? AND COALESCE(tareas_ok,0)>0",
                      (desde,)).fetchone()
    return float(row["s"] or 0.0)

def dificultad() -> dict:
    """Tamano de tarea segun la potencia de la red (tarea ~constante en tiempo)."""
    tops = tops_red()
    factor = 1.0 if tops <= 0 else min(4.0, max(0.5, (tops / REFERENCIA_TOPS) ** 0.5))
    escala = escala_tarea()
    return {
        "tops_red": round(tops, 1),
        "tops_referencia": REFERENCIA_TOPS,
        "factor": round(factor, 3),
        "escala_tarea": round(escala, 3),
        "objetivo_ms": OBJETIVO_TAREA_MS,
        # La difusion es la que se lleva el tiempo; la matriz crece con techo
        # (3x) para que no explote cuando los pasos ya tocan el limite.
        "matriz_n": min(LIMITE_N, max(64, int(BASE_MATRIX_N * factor * min(escala, 3.0)))),
        "inferencia_n": min(LIMITE_N, max(32, int(BASE_INFER_N * factor * min(escala, 3.0)))),
        "difusion_pasos": min(LIMITE_PASOS, pasos_max_difusion(1024),
                              max(300, int(1500 * escala * escala))),
    }

def supply_emitido() -> float:
    """Suministro desde el total materializado (con respaldo al ledger)."""
    try:
        fila = con.execute("SELECT valor FROM meta WHERE clave='supply_emitido'").fetchone()
        if fila is not None:
            return float(fila["valor"] or 0.0)
    except Exception:
        pass
    return float(con.execute("SELECT COALESCE(SUM(kuki),0) s FROM ledger").fetchone()["s"] or 0.0)

def _epoch(ts: str) -> float:
    try:
        return time.mktime(time.strptime(ts))
    except Exception:
        return time.time()


@app.get("/red/serie_gpu")
def red_serie_gpu(minutos: int = 60):
    """Serie por minuto y GPU de lo medido EN LA PROPIA GPU.

    Sale de `gpu_telemetria`, que rellena el minero en cada tarea con el
    tiempo que tardo de verdad (`tarea_ms`) y los FLOPs de esa tarea. No se
    usa `duracion_ms` del pago ni una media declarada: es lo que la tarjeta
    acaba de hacer, con la mezcla real de trabajo de la red.

    Devuelve, por minuto y por device: GFLOPS medidos (FLOPs / segundos),
    latencia media por tarea y pico de VRAM reservada. Tambien la mezcla de
    tareas ejecutadas en la ventana, que es lo que se puede dibujar sin
    inventar nada.
    """
    minutos = max(5, min(180, int(minutos or 60)))
    ahora = time.time()
    desde = ahora - minutos * 60
    filas = con.execute("SELECT device, metricas, ts FROM gpu_telemetria "
                        "ORDER BY id DESC LIMIT 20000").fetchall()
    devices = []
    puntos = [dict() for _ in range(minutos)]
    tipos = {}
    for r in filas:
        e = _epoch(r["ts"])
        if e < desde:
            continue
        try:
            m = json.loads(r["metricas"] or "{}")
        except Exception:
            continue
        dev = str(r["device"] or "gpu")
        if dev not in devices:
            devices.append(dev)
        i = min(minutos - 1, max(0, int((e - desde) // 60)))
        a = puntos[i].setdefault(dev, {"ms": 0.0, "flops": 0.0, "n": 0, "vram": 0.0,
                                       "nc": 0, "ms_c": 0.0})
        a["ms"] += float(m.get("tarea_ms") or 0.0)
        a["flops"] += float(m.get("flops") or 0.0)
        a["n"] += 1
        # Solo las tareas de COMPUTO miden potencia: un checksum son 0 FLOPs y
        # su tiempo (casi 0) hundiria la media y no dice nada de la GPU.
        if float(m.get("flops") or 0.0) > 0:
            a["nc"] += 1
            a["ms_c"] += float(m.get("tarea_ms") or 0.0)
        a["vram"] = max(a["vram"], float(m.get("vram_usada_mb") or 0.0))
        t = str(m.get("tipo") or "")
        if t:
            tipos[t] = tipos.get(t, 0) + 1
    devices.sort()
    salida = []
    for i in range(minutos):
        e = desde + (i + 1) * 60
        gpus = {}
        for dev, a in puntos[i].items():
            seg = a["ms_c"] / 1000.0
            # Sin tareas de computo en ese minuto el dato es null, NO 0: un
            # cero se dibuja como "la GPU rindio 0" y es mentira, simplemente
            # no hubo nada que medir.
            gpus[dev] = {
                "tareas": a["n"],
                "tareas_computo": a["nc"],
                "ms": round(a["ms_c"] / a["nc"], 1) if a["nc"] else None,
                "gflops": round(a["flops"] / seg / 1e9, 2) if seg > 0 else None,
                "vram_mb": round(a["vram"], 0) if a["vram"] else None,
            }
        salida.append({"t": int(e), "hora": time.strftime("%H:%M", time.localtime(e)),
                       "gpus": gpus})
    return {"minutos": minutos, "devices": devices, "puntos": salida,
            "tipos": dict(sorted(tipos.items(), key=lambda kv: -kv[1])),
            "fuente": "tiempo y FLOPs medidos por el minero en cada tarea"}

def _meta_float(clave: str, defecto: float = 0.0) -> float:
    """Lee un contador historico de 'meta' (0/-1 si no existe o falla)."""
    try:
        row = con.execute("SELECT valor FROM meta WHERE clave=?", (clave,)).fetchone()
        return float(row["valor"]) if row else defecto
    except Exception:
        return defecto

def tasa_tareas_seg() -> float:
    """Ritmo medio real de la red (tareas OK / segundo). Minimo: 1 tarea/dia.

    ARCHIVADO: usa el contador historico de 'meta', no las filas que queden en
    'tareas'. Si contara filas, borrar tareas viejas hundiria el ritmo medido y
    el premio por tarea se dispararia al techo (emitiria demasiado deprisa).
    """
    n = _meta_float("tareas_ok_total", -1.0)
    if n < 0:  # base anterior al archivado: degradar a conteo real
        n = con.execute("SELECT COUNT(*) c FROM tareas WHERE estado='ok'").fetchone()["c"]
    if n <= 0:
        return 1.0 / 86400.0
    primera = _meta_float("primera_ok_epoch", 0.0)
    if primera <= 0:
        m = con.execute("SELECT MIN(fin) m FROM tareas WHERE estado='ok' AND fin IS NOT NULL").fetchone()["m"]
        primera = _epoch(m) if m else time.time()
    elapsed = max(3600.0, time.time() - primera)
    return max(1.0 / 86400.0, n / elapsed)


# Marcas de tiempo de las tareas OK recientes: sirven para medir el ritmo de
# AHORA. La media historica no vale: si la red acelera, la media va por detras
# y el objetivo de calendario sale alto (se emitia 18x de mas).
_ok_reciente = collections.deque(maxlen=20000)


def _marcar_ok() -> None:
    _ok_reciente.append(time.time())


def tasa_reciente(ventana: float = 180.0) -> float:
    """Tareas OK por segundo en los ultimos `ventana` segundos.

    Con menos de 20 muestras cae a la media historica (arranque del Core).
    Ventana corta a proposito: con 10 min el ritmo subia antes de que la
    ventana lo viera y se emitia de mas (1,52 KIA/min medidos frente a 1,31
    de presupuesto).
    """
    ahora = time.time()
    while _ok_reciente and ahora - _ok_reciente[0] > ventana:
        _ok_reciente.popleft()
    if len(_ok_reciente) >= 20:
        span = max(1.0, ahora - _ok_reciente[0])
        return len(_ok_reciente) / span
    return tasa_tareas_seg()

def recompensa_pago(tier: float) -> tuple:
    """Pago que garantiza: (1) nunca pasar de 100M, (2) agotar justo la fecha final.

    - objetivo = restante / tareas_restantes_estimadas (persigue la fecha)
    - techo/suelo = bandas del halving x10 / x0.1 (predecible, anti-spikes)
    - pago final = min(base x tier, restante) -> el ledger JAMAS supera 100M
    """
    emit = supply_emitido()
    restante = MAX_SUPPLY - emit
    if restante <= 0:
        return 0.0, {"agotado": True}
    fin_dt = datetime(EMISSION_END.year, EMISSION_END.month, EMISSION_END.day)
    seg_rest = max(1.0, (fin_dt - datetime.now()).total_seconds())
    tasa = tasa_reciente()
    tareas_rest = max(1.0, tasa * seg_rest)
    objetivo = restante / tareas_rest
    techo = REWARD_BASE * emission_mult()
    # Pago = objetivo de calendario, punto. El techo (10 KIA x era) solo recorta
    # el caso raro de red parada; NO hay suelo. El antiguo suelo de
    # techo*0.1 (=1 KIA/tarea) era lo que agotaba los 100M en meses: con
    # ~300 tareas/min son 430.000 KIA/dia, o sea 197 dias para los 85M que
    # quedan, en vez de llegar a la fecha final.
    base = min(objetivo, techo) if techo > 0 else 0.0
    pago = round(min(base * tier, restante), 6)
    return pago, {"objetivo": round(objetivo, 6), "techo_halving": round(techo, 4),
                  "tasa_dia": round(tasa * 86400, 2), "restante": round(restante, 2),
                  "emision_por_min": round(restante / (seg_rest / 60.0), 4)}


# ============================================================
#  PAGO POR POTENCIA DEMOSTRADA (22-10-2026)
#  ENCENDIDO POR DEFECTO: se apaga con KUKIA_PAGO_POR_FLOP=0.
#
#  POR QUE EXISTE: la tarifa plana paga lo mismo por un checksum (0 FLOPs) que
#  por una difusion de 20 GFLOP, y el tier de VRAM paga por tener memoria, no
#  por calcular. Medido el 22-10-2026: un 32% de las tareas eran checksums, y
#  una misma potencia cobraba un 60% mas solo por declarar 24 GB.
#
#  COMO: pago = FLOPs verificados de la tarea x precio. El precio NO se inventa:
#  sale del presupuesto de emision del minuto (lo que queda / minutos hasta
#  2150) dividido por los GFLOP/min que la red entrega de verdad. Si los nodos
#  calculan el doble, el precio por GFLOP baja a la mitad: la emision total y
#  la fecha final siguen siendo ciertas (techo de 100M intacto).
#
#  SUELO: desde el 25-10-2026 hay un suelo de 0,0001 KIA por tarea
#  (PAGO_MIN_KIA), el minimo que acepta /transferir. Sin el, una tarea de
#  milisegundos paga 1e-9 KIA: trabajo real que no se puede mover nunca.
#  El suelo es en KIA, no en GFLOP, para que siga valiendo lo mismo si un dia
#  cambia el precio. Ojo: con el suelo, un checksum (0 FLOPs) ya no cobra 0,
#  cobra el minimo; por eso los checksums siguen fuera de MEZCLA_AUTO.
#  Se apaga con KUKIA_PAGO_MIN_KIA=0.
#
#  EL TIER NO MULTIPLICA en este modo: pagar dos veces por lo mismo (mas VRAM y
#  mas FLOPs) es justo lo que se quiere quitar de en medio.
# ============================================================
def _gflop_por_minuto(minutos: int = None) -> float:
    """GFLOP FACTURABLES por minuto que ha entregado la red.

    Facturables, no reales: cuenta cada tarea como max(FLOPs, suelo). Asi el
    precio que sale de aqui paga exactamente el presupuesto de emision, sin
    que el suelo de servicios (checksums, inferencia pequena) se lo coma por
    la puerta de atras. Con los FLOPs "reales" la red emitiria de mas y la
    ultima moneda caeria antes de 2150.
    """
    try:
        minutos = int(minutos or GFLOPS_VENTANA_MIN)
    except Exception:
        minutos = 10
    desde = time.time() - minutos * 60.0
    try:
        filas = con.execute("SELECT tipo, params, fin FROM tareas "
                            "WHERE estado='ok' AND fin IS NOT NULL "
                            "ORDER BY rowid DESC LIMIT 20000").fetchall()
    except Exception:
        return 0.0
    gflop = 0.0
    for r in filas:
        try:
            if _epoch(r["fin"]) < desde:
                continue
            gflop += max(_flops_tarea(r["tipo"], r["params"]) / 1e9,
                         GFLOP_MIN_FACTURABLE)
        except Exception:
            continue
    return gflop / minutos


_precio_cache = {"ts": 0.0, "datos": None}


def precio_gflop() -> dict:
    """Precio de 1 GFLOP verificado en KIA (ventana corta, cacheado 20 s)."""
    ahora = time.time()
    if _precio_cache["datos"] is not None and ahora - _precio_cache["ts"] < PRECIO_TTL:
        return _precio_cache["datos"]
    try:
        restante = MAX_SUPPLY - supply_emitido()
        fin_dt = datetime(EMISSION_END.year, EMISSION_END.month, EMISSION_END.day)
        seg_rest = max(1.0, (fin_dt - datetime.now()).total_seconds())
        objetivo_min = max(0.0, restante) / (seg_rest / 60.0)   # KIA/min
    except Exception as e:
        objetivo_min = 0.0
        print(f"[KUKIA Core] precio_gflop: {type(e).__name__}: {e}", flush=True)
    gpm = _gflop_por_minuto()
    if PRECIO_DEFINIDO:
        # Mismo precio que `precio_unico_minuto`: la definicion. Si aqui se
        # publicara el precio por presupuesto, /stats daria DOS precios distintos
        # a la vez y no habria forma de saber cual manda. Con la definicion
        # ademas hay precio siempre, aunque la red no haya medido nada todavia.
        precio_def = KIA_POR_MINUTO_GPU / (GPU_REF_GFLOPS * 60.0)
        datos = {"modo": "potencia", "fuente": "definicion",
                 # 18 decimales: el precio es 1,111111111111e-6 y redondear a 12
                 # lo dejaba en 1,111111e-6 (milesima de millon por GFLOP de
                 # diferencia con el precio que se paga de verdad).
                 "precio_gflop": round(precio_def, 18),
                 "gflop_por_min": round(gpm, 3),
                 "objetivo_kia_min": round(precio_def * gpm, 9),
                 "ventana_min": GFLOPS_VENTANA_MIN}
    elif objetivo_min <= 0 or gpm <= 0:
        datos = {"modo": "plano", "motivo": "sin GFLOP medidos en la ventana"}
    else:
        # SIN DEFINICION: se publica el precio que se PAGA de verdad, que es el
        # del minuto (presupuesto / divisor con suelo de capacidad). Antes aqui
        # se dividia por el trabajo medido sin suelo, asi que /stats daba un
        # precio y se pagaba otro (25-10-2026, al pasar al ancla por defecto).
        pm = precio_unico_minuto()
        precio_pagado = pm.get("precio")
        if not precio_pagado or precio_pagado <= 0:
            datos = {"modo": "plano", "motivo": pm.get("fuente") or "sin precio"}
        else:
            divisor = max(gpm, GPU_REF_GFLOPS * 60.0) if ANCLA_GPU_MIN else gpm
            datos = {"modo": "potencia",
                     "precio_gflop": round(float(precio_pagado), 18),
                     "fuente": pm.get("fuente"),
                     "gflop_divisor": round(divisor, 3),
                     "gflop_por_min": round(gpm, 3),
                     "objetivo_kia_min": round(float(precio_pagado) * divisor, 9),
                     "ventana_min": GFLOPS_VENTANA_MIN}
    _precio_cache["ts"] = ahora
    _precio_cache["datos"] = datos
    return datos


# ============================================================
#  PRECIO UNICO POR MINUTO (23-10-2026)
#  REPARTO PROPORCIONAL AL TRABAJO, NO POR ORDEN DE LLEGADA.
#
#  Antes, en la misma manana, habia una bolsa de KIA que se iba vaciando tarea
#  a tarea, por orden de llegada. Arreglaba el arranque en frio (un Core nuevo
#  pagaba 47 KIA el primer minuto contra 1,31 de presupuesto), pero repartia
#  mal: si el trabajo del minuto valia mas que el presupuesto, las tareas que
#  llegaban tarde cobraban menos o nada, y la misma tarea cobraba distinto
#  segun la hora a la que se entregara.
#
#  Ahora se fija UN precio por GFLOP para el minuto entero, calculado con el
#  trabajo del minuto anterior:
#
#      precio_minuto = presupuesto_por_minuto() / GFLOP del minuto anterior
#
#  Todas las tareas de ese minuto cobran lo mismo por GFLOP, asi que cada nodo
#  cobra en proporcion EXACTA al trabajo que ha entregado, sin importar cuando
#  lo entregue. El total del minuto queda en el presupuesto con UN MINUTO de
#  retraso: si el trabajo se dobla, ese minuto cobra el doble y el siguiente se
#  corrige a la mitad.
#
#  Si el minuto anterior no tuvo trabajo (arranque en frio, red parada) se mira
#  hacia atras: primero la ventana de 10 min y despues lo que haya en las
#  ultimas horas (hasta 6), para que una red que estuvo parada vuelva con un
#  precio sensato en vez de con pago plano. Si no hay NINGUN dato (red nueva,
#  sin historial), modo plano: el pago de calendario, con el minuto cortado en
#  un presupuesto. Ese unico caso, el primero de una red nueva, si reparte por
#  orden de llegada; sin precio no hay otra forma, y se dice aqui.
#
#  AJUSTE FINO: el precio se calcula con el trabajo del minuto anterior, asi que
#  si la red esta cambiando de tamano el minuto puede quedar un poco por encima
#  o por debajo. Se lleva un credito (lo que sobro/falto en el minuto que
#  acaba de cerrar, limitado a un presupuesto) y se aplica al precio del
#  siguiente. Asi el total se queda en el presupuesto, no un 5 % arriba.
#
#  FUSIBLE: red de seguridad para que un minuto no pueda emitir de mas.
#  Desde el 25-10-2026 esta en 1x: el minuto no puede pagar mas que su
#  presupuesto, ni con una rafaga. Antes era 2x, o sea que una rafaga podia
#  emitir el doble de lo que tocaba y adelantar la fecha final de la emision.
#  Con el reparto proporcional el fusible casi nunca muerde: el precio ya deja
#  el minuto cuadrado, y solo corta si alguien entrega de golpe mucho mas
#  trabajo del que la red venia haciendo.
# ============================================================
FUSIBLE_MINUTO_X = max(1.0, float(os.environ.get("KUKIA_FUSIBLE_MINUTO_X", "1")))


def presupuesto_por_minuto() -> float:
    """KIA/min que toca emitir segun el calendario (lo mismo que /stats)."""
    try:
        restante = MAX_SUPPLY - supply_emitido()
        if restante <= 0:
            return 0.0
        fin_dt = datetime(EMISSION_END.year, EMISSION_END.month, EMISSION_END.day)
        seg_rest = max(1.0, (fin_dt - datetime.now()).total_seconds())
        return restante / (seg_rest / 60.0)
    except Exception:
        return 0.0


def _gflop_ventana_larga(horas: float = 6.0) -> float:
    """GFLOP facturables por minuto en lo ultimo que haya cerrado (hasta 6 h).

    Solo se usa cuando el minuto anterior y la ventana de 10 min estan vacios
    (red parada o recien arrancada). Se divide por el tramo que de verdad
    cubren las tareas leidas, no por las 6 h, para no salir con un precio
    inflado si el historial es corto.
    """
    try:
        filas = con.execute("SELECT tipo, params, fin FROM tareas "
                            "WHERE estado='ok' AND fin IS NOT NULL "
                            "ORDER BY rowid DESC LIMIT 20000").fetchall()
    except Exception:
        return 0.0
    ahora = time.time()
    desde = ahora - horas * 3600.0
    gflop, primera = 0.0, None
    for r in filas:
        try:
            t = _epoch(r["fin"])
            if t is None or t < desde:
                continue
            gflop += max(_flops_tarea(r["tipo"], r["params"]) / 1e9,
                         GFLOP_MIN_FACTURABLE)
            if primera is None or t < primera:
                primera = t
        except Exception:
            continue
    if gflop <= 0 or primera is None:
        return 0.0
    minutos = max(1.0, (ahora - primera) / 60.0)
    return gflop / minutos


def _gflop_entre(desde: float, hasta: float) -> float:
    """GFLOP facturables entregados entre dos instantes (segun `fin`).

    Se cuenta igual que se paga: max(FLOPs, suelo). Asi el precio del minuto
    reparte exactamente el presupuesto entre el trabajo que la red factura.
    """
    try:
        filas = con.execute("SELECT tipo, params, fin FROM tareas "
                            "WHERE estado='ok' AND fin IS NOT NULL "
                            "ORDER BY rowid DESC LIMIT 20000").fetchall()
    except Exception:
        return 0.0
    total = 0.0
    for r in filas:
        try:
            t = _epoch(r["fin"])
            if t is None or t < desde or t >= hasta:
                continue
            total += max(_flops_tarea(r["tipo"], r["params"]) / 1e9,
                         GFLOP_MIN_FACTURABLE)
        except Exception:
            continue
    return total


_precio_minuto = {"minuto": -1, "datos": None}

# Contador del minuto en curso + credito (lo que sobro o falto al cerrar el
# anterior). Se GUARDA en `meta` dentro de la transaccion del pago (23-10-2026):
# antes solo vivia en memoria, asi que reiniciar el Core a mitad de minuto
# volvia a poner la bolsa a cero y se podia pagar dos veces el presupuesto del
# mismo minuto. Y si la escritura falla, el pago se aborta: mejor no pagar una
# tarea que emitir por fuera del presupuesto.
_minuto_actual = {"minuto": -1, "pagado": 0.0, "credito": 0.0,
                  "presupuesto": None}


def _guardar_bolsa() -> None:
    """Escribe la bolsa del minuto en `meta`. NO se come los errores.

    Se llama desde `_anotar_pagado`, es decir, DENTRO de la transaccion del
    pago (BEGIN IMMEDIATE). Si el disco falla o `meta` no deja escribir, la
    excepcion sube, el endpoint hace ROLLBACK y no se paga nada: la tarea, el
    asiento y el saldo se quedan como estaban. Silenciar este fallo (el
    `except Exception: pass` de antes) era justo la puerta por la que se
    emitia sin control.
    """
    propio = not con.en_transaccion()
    try:
        for clave, valor in (("bolsa_minuto", float(_minuto_actual["minuto"])),
                             ("bolsa_pagado", float(_minuto_actual["pagado"])),
                             ("bolsa_credito", float(_minuto_actual["credito"]))):
            con.execute("INSERT INTO meta(clave,valor) VALUES(?,?) "
                        "ON CONFLICT(clave) DO UPDATE SET valor=excluded.valor",
                        (clave, valor))
        if propio:
            # Fuera de un pago (pruebas, arranque): la transaccion es de aqui.
            con.commit()
    except Exception as e:
        if propio:
            try:
                con.rollback()
            except Exception:
                pass
        raise _error_escritura(e, "no se pudo anotar la bolsa del minuto; "
                                  "pago abortado")


def _cargar_bolsa() -> None:
    """Recupera la bolsa del minuto tras un reinicio.

    Si el minuto guardado es el de ahora, se sigue con lo ya pagado (no se
    regala otro presupuesto). Si la lectura falla, la bolsa arranca como
    GASTADA: el minuto se cierra sin pagar y a la vuelta del minuto se
    recupera. Es el lado seguro: un minuto sin pagar, no un minuto al doble.
    """
    actual = int(time.time() // 60)
    try:
        fila = con.execute("SELECT clave, valor FROM meta WHERE clave IN "
                           "('bolsa_minuto','bolsa_pagado','bolsa_credito')").fetchall()
        datos = {r["clave"]: float(r["valor"] or 0.0) for r in fila}
        minuto = int(datos.get("bolsa_minuto", -1.0))
        pagado = float(datos.get("bolsa_pagado", 0.0))
        credito = float(datos.get("bolsa_credito", 0.0))
    except Exception as e:
        _minuto_actual["minuto"] = actual
        _minuto_actual["pagado"] = presupuesto_por_minuto()
        _minuto_actual["credito"] = 0.0
        print(f"[KUKIA Core] AVISO: la bolsa guardada no se pudo leer "
              f"({type(e).__name__}); este minuto no paga", flush=True)
        return
    if minuto == actual:
        _minuto_actual["minuto"] = minuto
        _minuto_actual["pagado"] = max(0.0, pagado)
        _minuto_actual["credito"] = credito
        if pagado > 0:
            print(f"[KUKIA Core] bolsa del minuto recuperada: "
                  f"{pagado:.6f} KIA ya pagados", flush=True)


def _cerrar_minuto_si_toca(minuto: int) -> None:
    """Si ha cambiado el minuto, apunta el error del anterior en el credito."""
    c = _minuto_actual
    if c["minuto"] == minuto:
        return
    if c["minuto"] >= 0 and not PRECIO_DEFINIDO:
        # El credito es el sobrante de un minuto flojo para el siguiente. Con el
        # precio DEFINIDO no sirve para nada (el precio no depende del
        # presupuesto) y ademas confunde: se deja en cero.
        presu = float(c.get("presupuesto") or presupuesto_por_minuto())
        c["credito"] += presu - c["pagado"]
        # Nunca mas de un presupuesto de credito: asi una red parada un mes no
        # se pone a pagar el doble durante dias al volver.
        c["credito"] = max(-presu, min(presu, c["credito"]))
    elif PRECIO_DEFINIDO:
        c["credito"] = 0.0
    c["minuto"], c["pagado"] = minuto, 0.0
    # Tope nuevo, congelado para todo el minuto (ver presupuesto_del_minuto).
    c["presupuesto"] = presupuesto_por_minuto()


def precio_unico_minuto() -> dict:
    """Precio por GFLOP fijo para el minuto en curso (se fija al empezar)."""
    minuto = int(time.time() // 60)
    if _precio_minuto["datos"] is not None and _precio_minuto["minuto"] == minuto:
        return _precio_minuto["datos"]
    _cerrar_minuto_si_toca(minuto)
    presu = presupuesto_del_minuto()
    ajuste = max(-presu, min(presu, _minuto_actual["credito"]))
    # El credito (lo que sobro de minutos flojos) puede empujar el objetivo por
    # encima de lo que el minuto puede pagar. Si se deja, el fusible recorta el
    # pago por unas millonésimas y el minero ve un recorte que no es suyo
    # (pasaba el 25-10: 4e-7 por minuto). Se recorta AQUI, en el precio: el
    # objetivo nunca apunta mas alto de lo que el minuto puede soltar.
    objetivo = min(max(0.0, presu + ajuste), presu * FUSIBLE_MINUTO_X)
    gflop = _gflop_entre((minuto - 1) * 60.0, minuto * 60.0)
    fuente = "minuto anterior"
    if gflop <= 0:
        # Red parada: se mira la ventana de 10 min y, si tampoco, lo ultimo que
        # haya (hasta 6 h). Asi el primer minuto tras una parada no sale a
        # precio de oro ni a pago plano.
        gflop = _gflop_entre(minuto * 60.0 - 600.0, minuto * 60.0) / 10.0
        fuente = "ventana 10 min"
    if gflop <= 0:
        gflop = _gflop_ventana_larga()
        fuente = "media de lo ultimo que hay (hasta 6 h)"
    gflop_medido = gflop
    # ANCLA GPU-MINUTO (25-10-2026, revisada el mismo dia): el ancla no es un
    # numero fijo, es un SUELO de capacidad. El divisor es el trabajo medido,
    # pero nunca menos que una GPU de referencia a tope durante un minuto:
    #
    #     divisor = max(trabajo medido, 60 * GPU_REF)
    #
    # Asi se cumplen las tres cosas a la vez:
    #   1) Red parada o con poca faena: manda el suelo y el precio no se dispara
    #      (antes, sin medida, cada GFLOP se cobraba a precio de oro y ningun
    #      cliente podia comprar).
    #   2) Red con mas faena que una GPU: manda lo medido, asi que el
    #      presupuesto se reparte entre TODOS en proporcion a sus GFLOP. Una GPU
    #      el doble de rapida cobra el doble por el mismo tiempo de reloj, que es
    #      lo que se busca: pagar por trabajo entregado, no por modelo.
    #   3) El total del minuto no pasa del presupuesto: el precio ya lo deja
    #      cuadrado y el fusible (1x por defecto) lo garantiza al decimal.
    suelo = GPU_REF_GFLOPS * 60.0
    if ANCLA_GPU_MIN:
        if gflop_medido < suelo:
            gflop = suelo
            fuente = "ancla GPU-minuto (suelo)"
        else:
            gflop = gflop_medido
            fuente = "trabajo medido (reparto proporcional)"
    if PRECIO_DEFINIDO and suelo > 0:
        # DEFINICION (25-10-2026): un minuto de la GPU de referencia vale
        # KIA_POR_MINUTO_GPU KIA, con 1 por defecto. El precio del GFLOP no sale
        # del presupuesto: sale de dividir la definicion entre los GFLOP de un
        # minuto de GPU. Asi cada KIA se puede leer como "un minuto de GPU" sin
        # consultar el calendario, y el presupuesto del minuto queda de TOPE
        # (el fusible), que es su papel.
        precio_def = KIA_POR_MINUTO_GPU / suelo
        datos = {"minuto": minuto, "precio": precio_def,
                 "fuente": "definicion: 1 KIA = 1 minuto de GPU",
                 "objetivo_def_kia": round(precio_def * gflop_medido, 9)}
    elif presu > 0 and gflop > 0:
        datos = {"minuto": minuto, "precio": objetivo / gflop, "fuente": fuente}
    else:
        datos = {"minuto": minuto, "precio": None, "fuente": "plano"}
    # Cuantos minutos de GPU de referencia paga el presupuesto de este minuto a
    # este precio. Es el limite real: por encima de esto el fusible muerde a
    # quien llegue tarde. Hoy son ~1,31 GPUs.
    capacidad = None
    if PRECIO_DEFINIDO and KIA_POR_MINUTO_GPU > 0:
        capacidad = round(presu / KIA_POR_MINUTO_GPU, 6)
    datos.update({"gflop_anterior": round(gflop_medido, 3),
                  "presupuesto_kia_min": round(presu, 6),
                  "credito_kia": round(_minuto_actual["credito"], 6),
                  "objetivo_kia_min": round(objetivo, 6),
                  "modo_precio": ("definido" if PRECIO_DEFINIDO
                                  else ("ancla" if ANCLA_GPU_MIN else "trabajo medido")),
                  "precio_definido": PRECIO_DEFINIDO,
                  "ancla": (ANCLA_GPU_MIN and not PRECIO_DEFINIDO),
                  "gpu_ref_gflops": GPU_REF_GFLOPS,
                  "gflop_divisor": round(gflop, 3),
                  "suelo_gflop_min": round(suelo, 3),
                  "capacidad_gpu_min": capacidad,
                  # Lo que paga un minuto de la GPU de referencia a este precio.
                  "kia_por_minuto_gpu": (KIA_POR_MINUTO_GPU if PRECIO_DEFINIDO else
                                         (round(objetivo / gflop * suelo, 9)
                                          if ANCLA_GPU_MIN else None))})
    _precio_minuto["minuto"], _precio_minuto["datos"] = minuto, datos
    return datos


def pagado_en_minuto() -> float:
    """KIA pagados en el minuto en curso (se reinicia al cambiar de minuto)."""
    _cerrar_minuto_si_toca(int(time.time() // 60))
    return float(_minuto_actual["pagado"])


def _anotar_pagado(kia: float) -> None:
    pagado_en_minuto()                       # refresca el minuto
    anterior = _minuto_actual["pagado"]
    _minuto_actual["pagado"] = anterior + float(kia or 0.0)
    try:
        _guardar_bolsa()
    except Exception:
        # La transaccion se va a revertir: la memoria tiene que volver a lo
        # que dice el disco, o la bolsa quedaria gastada por un pago que no
        # existio.
        _minuto_actual["pagado"] = anterior
        raise


_cargar_bolsa()          # el minuto en curso sigue donde lo dejo el reinicio


def presupuesto_del_minuto() -> float:
    """Presupuesto CONGELADO al empezar el minuto (no se recalcula por tarea).

    POR QUE (auditoria 23-10-2026, hallazgo H1/H4): `presupuesto_por_minuto()`
    sale de lo que queda por emitir dividido por los minutos que faltan hasta
    2150. Ese numero BAJA mientras corre el minuto, asi que cada tarea veia un
    tope un pelo menor que la anterior y, sumando, el minuto podia cerrar unas
    diezmilesimas por encima del presupuesto que se publicaba. Nada grave, pero
    "la bolsa nunca se pasa" tiene que ser cierto al decimal, no al redondeo.
    Congelando el tope al abrir el minuto, la suma de pagos no puede pasarse ni
    un 1e-9, y lo que se publica es exactamente lo que se reparte.
    """
    _cerrar_minuto_si_toca(int(time.time() // 60))
    if _minuto_actual.get("presupuesto") is None:
        _minuto_actual["presupuesto"] = presupuesto_por_minuto()
    return float(_minuto_actual["presupuesto"] or 0.0)


def bolsa_disponible(ahora: float = None) -> float:
    """KIA del presupuesto de este minuto que aun no se han pagado."""
    presu = presupuesto_del_minuto()
    if presu <= 0:
        return 0.0
    minuto = int((time.time() if ahora is None else ahora) // 60)
    if minuto != int(time.time() // 60):
        return presu                         # otro minuto: se enseña el tope
    return max(0.0, presu - pagado_en_minuto())


def _redondear_kia(x: float) -> float:
    """Redondea HACIA ABAJO a 12 decimales.

    POR QUE (23-10-2026): `round(x, 9)` puede subir. Al recortar un pago a lo
    que queda en la bolsa, redondear hacia arriba devolvia un pelin mas que el
    presupuesto: con 25 tareas el minuto cerraba 1e-8 por encima y la promesa
    "la bolsa nunca se pasa" dejaba de ser cierta al decimal. KIA no se regala
    por redondeo: hacia abajo siempre.

    POR QUE 12 Y NO 9 (25-10-2026, ancla GPU-minuto): con el ancla un GFLOP
    cuesta ~1,46e-6 KIA, asi que una tarea pequena (matriz 64 = 0,0005 GFLOP)
    vale 7,3e-10 KIA. A 9 decimales eso se cortaba a CERO y el minero trabajaba
    gratis. A 12 decimales se cobra lo que pesa, sin regalar nada (sigue siendo
    floor: la bolsa no se pasa). Ver tools/prueba_ancla.py.
    """
    return math.floor(float(x) * 1e12) / 1e12


# ============================================================
#  ADAPTADOR DE REPARTO (25-10-2026)
#  QUE COBRA CADA GPU SEGUN LAS QUE SE UNAN A REPARTIR EL MINUTO.
#
#  El pago ya es proporcional al trabajo (precio unico del minuto x GFLOP
#  verificados), pero eso solo se veia en el asiento de cada uno. Este adaptador
#  cierra el minuto y dice, para cada cuenta que entrego trabajo verificado:
#    - cuanto trabajo entrego (GFLOP facturables, contados como se pagan),
#    - que parte del minuto le toca (su GFLOP / el GFLOP de todos),
#    - su cuota de lo que se pago (lo pagado en el minuto x su parte) y
#    - cuanto cobro de verdad (asientos 'trabajo verificado' del mismo minuto).
#  La cifra que BAJA cuando mas GPUs se unen es `cuota_igual_kia`
#  (lo pagado / participantes): si todos entregan lo mismo, cada uno cobra eso.
#
#  OJO CON LOS DOS NUMEROS: `pagado_kia` es lo que se solto de verdad en el
#  minuto y `presupuesto_kia_min` lo que el calendario permitia soltar. Hoy el
#  pago va por el ancla GPU-minuto, que es un SUELO de capacidad (900.000 GFLOP
#  por minuto), asi que con la red casi vacia se paga mucho menos que el tope:
#  el margen no se pierde, se queda en la hucha de lo que falta por emitir.
#
#  NO cambia ningun pago: mide y publica lo que ya se reparte (/stats →
#  `reparto`). Asi el minero puede comprobar que su parte es su trabajo y no el
#  orden de llegada, y que el reparto crece o mengua con los que se unan.
#  Lo comprueba `python tools/prueba_adaptador_reparto.py`.
# ============================================================
_reparto_cache = {"minuto": -1, "datos": None}


def reparto_minuto(minuto: int = None) -> dict:
    """Reparto del minuto ya cerrado entre los que entregaron trabajo.

    `minuto` es indice de minuto (epoch // 60); por defecto, el anterior, que es
    el unico que esta completo. Se cachea por minuto: /stats lo pide cada 2 s y
    el resultado no cambia dentro del mismo minuto.
    """
    minuto = int(minuto if minuto is not None else (time.time() // 60) - 1)
    if _reparto_cache["minuto"] == minuto and _reparto_cache["datos"] is not None:
        return _reparto_cache["datos"]
    desde, hasta = minuto * 60.0, (minuto + 1) * 60.0
    # trabajo[cuenta][(nodo, device)] = GFLOP verificados en el minuto. La
    # cuenta (billetera de cobro) es la unidad de pago: dos GPUs de la misma
    # casa cobran en la misma cuenta, y el asiento es uno solo. Por eso la
    # parte se calcula por cuenta y DESPUES se abre en maquinas: asi la suma de
    # lo que cobra cada GPU es exactamente lo que entro en la billetera.
    trabajo: dict = {}
    tareas_de: dict = {}
    try:
        filas = con.execute("SELECT nodo_id, COALESCE(device,'') AS device, "
                            "tipo, params, fin FROM tareas "
                            "WHERE estado='ok' AND fin IS NOT NULL "
                            "ORDER BY rowid DESC LIMIT 20000").fetchall()
    except Exception:
        try:
            filas = con.execute("SELECT nodo_id, '' AS device, tipo, params, fin "
                                "FROM tareas WHERE estado='ok' AND fin IS NOT NULL "
                                "ORDER BY rowid DESC LIMIT 20000").fetchall()
        except Exception:
            filas = []
    cuenta_de: dict = {}
    for r in filas:
        try:
            t = _epoch(r[4])
            if t is None or t < desde or t >= hasta:
                continue
            # Se cuenta igual que se paga (max(FLOPs, suelo)): si no, la parte
            # de una GPU que solo hizo tareas minusculas saldria a cero y su
            # cuota no cuadraria con el asiento que cobro.
            g = max(_flops_tarea(r[2], r[3]) / 1e9, GFLOP_MIN_FACTURABLE)
        except Exception:
            continue
        nodo = r[0] or "?"
        if nodo not in cuenta_de:
            cuenta_de[nodo] = _cuenta_de(nodo) or nodo
        clave = (nodo, str(r[1] or ""))
        trabajo.setdefault(cuenta_de[nodo], {})
        trabajo[cuenta_de[nodo]][clave] = trabajo[cuenta_de[nodo]].get(clave, 0.0) + g
        tareas_de[clave] = tareas_de.get(clave, 0) + 1
    # Lo cobrado de verdad en ese minuto, por cuenta y solo por trabajo. Se
    # separa lo que pago un cliente (escrow) de lo que pago la emision: el
    # dinero del cliente no sale del presupuesto y no se reparte como emision.
    cobrado: dict = {}
    escrow = 0.0
    try:
        filas = con.execute("SELECT l.nodo_id AS nodo_id, l.kuki AS kuki, l.ts AS ts, "
                            "COALESCE(t.cliente,'') AS cliente "
                            "FROM ledger l LEFT JOIN tareas t ON t.id = l.tarea_id "
                            "WHERE l.motivo=? ORDER BY l.id DESC LIMIT 20000",
                            ("trabajo verificado",)).fetchall()
    except Exception:
        try:
            filas = con.execute("SELECT nodo_id, kuki, ts, '' AS cliente FROM ledger "
                                "WHERE motivo=? ORDER BY id DESC LIMIT 20000",
                                ("trabajo verificado",)).fetchall()
        except Exception:
            filas = []
    for r in filas:
        try:
            t = _epoch(r[2])
            if t is None or t < desde or t >= hasta:
                continue
            kia = float(r[1] or 0.0)
            if str(r[3] or "").strip():
                escrow = round(escrow + kia, 12)
                continue
            cuenta = r[0] or "?"
            cobrado[cuenta] = cobrado.get(cuenta, 0.0) + kia
        except Exception:
            continue
    total = sum(sum(g.values()) for g in trabajo.values())
    gpus = sum(len(g) for g in trabajo.values())
    presu = presupuesto_por_minuto()
    participantes = len(trabajo)
    # El pastel que de verdad se reparte: los asientos de trabajo del minuto.
    # Es lo que cobraron entre todos, y nunca pasa del presupuesto (el fusible
    # del minuto ya lo garantiza antes de llegar aqui).
    pagado = round(sum(cobrado.values()), 12)
    filas_salida = []
    for cuenta in sorted(trabajo, key=lambda c: (-sum(trabajo[c].values()), c)):
        g_cuenta = sum(trabajo[cuenta].values())
        parte = (g_cuenta / total) if total > 0 else 0.0
        cuota = _redondear_kia(pagado * parte)
        cobro = round(cobrado.get(cuenta, 0.0), 12)
        maquinas = []
        for (nodo, device), g in sorted(trabajo[cuenta].items(), key=lambda kv: -kv[1]):
            parte_cuenta = (g / g_cuenta) if g_cuenta > 0 else 0.0
            maquinas.append({"nodo": nodo,
                             "device": device,
                             "tareas": int(tareas_de.get((nodo, device), 0)),
                             "gflop": round(g, 6),
                             "parte_cuenta": round(parte_cuenta, 12),
                             "cuota_kia": _redondear_kia(cobro * parte_cuenta)})
        filas_salida.append({"cuenta": cuenta,
                             "gpus": len(maquinas),
                             "tareas": sum(m["tareas"] for m in maquinas),
                             "gflop": round(g_cuenta, 6),
                             "parte": round(parte, 12),
                             "cuota_kia": cuota,
                             "cobrado_kia": cobro,
                             # Lo que le falta a su asiento para ser exactamente
                             # su parte del pastel (0 = reparto perfecto).
                             "desvio_kia": round(cobro - cuota, 12),
                             "maquinas": maquinas})
    suma_cuotas = round(sum(f["cuota_kia"] for f in filas_salida), 12)
    datos = {"minuto": minuto,
             "participantes": participantes,
             "gpus": gpus,
             "gflop_total": round(total, 6),
             "pagado_kia": pagado,
             "pagado_cliente_kia": escrow,
             "presupuesto_kia_min": round(presu, 9),
             # Lo que cobra cada uno si todos entregan lo mismo: las dos cifras
             # que bajan segun se van uniendo GPUs (por billetera y por GPU).
             "cuota_igual_kia": (_redondear_kia(pagado / participantes)
                                 if participantes else None),
             "cuota_igual_por_gpu_kia": (_redondear_kia(pagado / gpus)
                                         if gpus else None),
             "suma_cuotas_kia": suma_cuotas,
             # El reparto es proporcional al trabajo si cada uno cobro su parte
             # del pastel. Si no, es que el fusible recorto a alguien.
             "proporcional": bool(all(abs(f["desvio_kia"]) <= 1e-9
                                      for f in filas_salida)),
             "regla": ("cada uno cobra lo pagado en el minuto x "
                       "(su GFLOP / GFLOP de todos)"),
             "cuentas": filas_salida}
    _reparto_cache["minuto"], _reparto_cache["datos"] = minuto, datos
    return datos


def _resumen_reparto(max_filas: int = 12) -> dict:
    """Version corta del reparto para /stats (el panel lo pide cada 2 s)."""
    try:
        r = reparto_minuto()
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}
    cuentas = r.get("cuentas") or []
    cortas = []
    for c in cuentas[:max_filas]:
        fila = dict(c)
        fila["maquinas"] = (c.get("maquinas") or [])[:6]
        cortas.append(fila)
    return {"minuto": r["minuto"],
            "participantes": r["participantes"],
            "gpus": r["gpus"],
            "gflop_total": r["gflop_total"],
            "pagado_kia": r["pagado_kia"],
            "pagado_cliente_kia": r["pagado_cliente_kia"],
            "presupuesto_kia_min": r["presupuesto_kia_min"],
            "cuota_igual_kia": r["cuota_igual_kia"],
            "cuota_igual_por_gpu_kia": r["cuota_igual_por_gpu_kia"],
            "suma_cuotas_kia": r["suma_cuotas_kia"],
            "proporcional": r["proporcional"],
            "regla": r["regla"],
            "cuentas_mostradas": len(cortas),
            "top": cortas}


def pago_plano(tier: float = 1.0, simular: bool = False) -> tuple:
    """Pago de calendario (modo plano) DENTRO de la bolsa del minuto.

    POR QUE EXISTE (23-10-2026): el modo plano era la fuga de la bolsa. Con
    `KUKIA_PAGO_POR_FLOP=0` se pagaba `recompensa_pago(tier)` a pelo: sin tope
    por minuto y sin anotarlo en la bolsa, asi que el Core podia emitir en un
    minuto todo lo que le pidieran los nodos (a 10 KIA por tarea, 300 tareas
    son 3.000 KIA/min contra un presupuesto de 1,31). Lo mismo pasaba en el
    primer arranque de una red sin historial, que cae a este mismo pago.

    Ahora todo pago, sea por potencia o plano, pasa por aqui o por
    `pago_por_trabajo`: se corta en lo que le queda al minuto y se anota con
    `_anotar_pagado`. La bolsa deja de tener puertas abiertas.
    """
    presu = presupuesto_del_minuto()
    pago, info = recompensa_pago(tier)
    disp = max(0.0, presu - pagado_en_minuto())
    pagado = _redondear_kia(min(pago, disp))
    # simular=True: valorar sin gastar la bolsa (lo usa la puerta de cliente
    # para dar un precio ANTES de cobrar). Consultar un precio no es pagar.
    if not simular:
        _anotar_pagado(pagado)
    return pagado, dict(info or {}, modo_pago="plano", fuente_precio="plano",
                        bolsa_kia_min=round(presu, 6),
                        pagado_por_bolsa=round(pagado, 9),
                        recortado_por_bolsa=round(max(0.0, pago - pagado), 9))


def pago_por_trabajo(tipo: str, params, tier: float = 1.0,
                     simular: bool = False) -> tuple:
    """KIA que vale una tarea ya verificada, por los FLOPs que ha costado.

    `simular=True` calcula lo que costaria SIN anotarlo en la bolsa del minuto:
    es lo que necesita un presupuesto o un precio al cliente. Pagar de verdad
    es `simular=False` (lo que hace `pago_de_tarea`).
    """
    restante = MAX_SUPPLY - supply_emitido()
    if restante <= 0:
        return 0.0, {"agotado": True, "modo_pago": "potencia"}
    gl = _flops_tarea(tipo, params) / 1e9
    cobrable = max(gl, GFLOP_MIN_FACTURABLE)
    # TECHO POR TAREA: una tarea suelta no puede llevarse mas que la tarifa
    # plana. Con el precio del minuto hace falta muy poco, pero se queda.
    techo = REWARD_BASE * emission_mult()
    pm = precio_unico_minuto()
    if pm["precio"] is None:
        # MODO PLANO: red sin ninguna medida todavia (primer arranque). Pago de
        # calendario, con el minuto cortado en un presupuesto. Aqui si manda el
        # orden de llegada: sin precio hay con que repartir por trabajo. Va por
        # `pago_plano` para no tener dos puertas a la bolsa.
        pagado, info = pago_plano(tier, simular=simular)
        info["modo_pago"] = "plano (sin medida de red)"
        info["pagado_por_fusible"] = round(pagado, 9)
        return pagado, info
    precio = float(pm["precio"])
    # 9 decimales y no 6: el pago por tarea puede ser mucho mas pequeno que con
    # la tarifa plana, y redondear a 6 lo dejaria en cero si la red crece.
    pago = _redondear_kia(min(precio * cobrable, techo, restante))
    # SUELO TRANSFERIBLE (25-10-2026): ninguna tarea paga menos de lo que se
    # puede mover por /transferir. Se aplica aqui, despues del precio y antes
    # del fusible, para que el cliente vea el mismo numero (la puerta de cliente
    # usa esta funcion en modo simular=True) y el escrow lo cubra.
    por_suelo = False
    if PAGO_MIN_KIA > 0 and pago < PAGO_MIN_KIA:
        pago = _redondear_kia(min(PAGO_MIN_KIA, techo, restante))
        por_suelo = True
    # FUSIBLE del minuto: solo actua si algo va muy desmadrado (de golpe, diez
    # veces el trabajo del minuto anterior). En regimen normal no toca nada.
    # El tope se toma EXACTO, sin redondear a 6 decimales: el precio se calcula
    # con el presupuesto exacto, asi que comparandolo con el redondeado un pago
    # de 1 minuto de GPU salia 4e-7 por encima del tope y el fusible recortaba
    # al minero por una millonésima (visto el 25-10-2026 en prueba_minero_mes).
    disp = max(0.0, presupuesto_del_minuto() * FUSIBLE_MINUTO_X - pagado_en_minuto())
    recortado = max(0.0, pago - disp)
    if recortado > 0:
        pago = _redondear_kia(disp)
    if not simular:
        _anotar_pagado(pago)
    return pago, {"modo_pago": "potencia",
                  "gflop_tarea": round(gl, 6),
                  "gflop_cobrados": round(cobrable, 6),
                  "precio_gflop": round(precio, 12),
                  "fuente_precio": pm["fuente"],
                  "gflop_minuto_anterior": pm["gflop_anterior"],
                  "techo_por_tarea": round(techo, 4),
                  "pagado_por_fusible": round(pago, 9),
                  "recortado_por_fusible": round(recortado, 9),
                  "pago_min_kia": round(PAGO_MIN_KIA, 9),
                  "pagado_por_suelo": por_suelo,
                  "suelo_servicios": gl < GFLOP_MIN_FACTURABLE}


def pago_de_tarea(task, tier: float) -> tuple:
    """Pago de una tarea, con el modo que este activo (plano o por potencia).

    El modo plano TAMBIEN pasa por la bolsa (pago_plano): antes devolvia
    `recompensa_pago` a pelo y era la fuga por la que se iba la emision.
    """
    if PAGO_POR_FLOP:
        return pago_por_trabajo(task["tipo"], task["params"], tier)
    return pago_plano(tier)


def castigo_por_fallo(tier: float) -> float:
    """Castigo por resultado incorrecto, proporcional al premio de una tarea.

    100 tareas de castigo, con suelo de 0,1 y tope de 25 KIA. Antes eran 25
    fijos: con el premio actual (0,0037) un solo fallo costaba 6.757 tareas, y
    un nodo nuevo sin saldo no pagaba nada (el castigo se capa al saldo).
    """
    pago, _ = recompensa_pago(tier)
    return min(PENALIZACION, max(PENALIZACION_MIN, PENALIZACION_MULT * pago))

@app.get("/version")
def version():
    """Que codigo esta ejecutandose, para compararlo con el de GitHub."""
    return _HUELLA


@app.get("/stats")
def stats():
    # AUDITORIA 2026-10-20: total materializado; antes era un SUM de todo el
    # ledger en cada peticion (el panel lo pide cada 2 s).
    sum = min(supply_emitido(), MAX_SUPPLY)
    pago1, info1 = recompensa_pago(1.0)
    _pot = precio_gflop() if PAGO_POR_FLOP else {}
    if PAGO_POR_FLOP and _pot.get("modo") == "potencia":
        # En modo potencia no hay "pago base por tarea": el numero que se
        # publica es lo que cobra una tarea minima (servicios), que es lo que
        # de verdad cobra un checksum o una inferencia pequena.
        # Lo que cobra como minimo una tarea: el suelo transferible (25-10-2026),
        # no precio x 0 GFLOP. Si no, /stats publicaba "pago_por_tarea: 0" y no
        # era verdad: una tarea siempre cobra al menos PAGO_MIN_KIA.
        pago1 = round(max(_pot["precio_gflop"] * GFLOP_MIN_FACTURABLE, PAGO_MIN_KIA), 6)
        info1 = dict(info1 or {}, modo_pago="potencia",
                     precio_gflop=_pot["precio_gflop"],
                     gflop_por_min=_pot.get("gflop_por_min"),
                     pago_min_kia=round(PAGO_MIN_KIA, 9))
    total_ok = int(_meta_float("tareas_ok_total", -1.0))
    if total_ok < 0:
        total_ok = con.execute("SELECT COUNT(*) c FROM tareas WHERE estado='ok'").fetchone()["c"]
    en_tabla = con.execute("SELECT COUNT(*) c FROM tareas WHERE estado='ok'").fetchone()["c"]
    _ss = _sellos_leidos()
    return {
        "version": app.version,
        # Que codigo corre de verdad: hash de los ficheros + arranque + commit.
        "codigo": _HUELLA,
        "supply_emitido": sum,
        "max_supply": MAX_SUPPLY,
        # Nodos VIVOS: los retirados (nombres repetidos de identidades viejas)
        # no cuentan. Antes se contaban las filas, no los nodos.
        "nodos": con.execute("SELECT COUNT(*) c FROM nodos "
                             "WHERE COALESCE(retirado,0)=0").fetchone()["c"],
        # Historico real (sobrevive al archivado); 'en_tabla' son las que quedan
        # guardadas por si se quiere auditar el detalle.
        "tareas_ok": total_ok,
        "tareas_ok_en_tabla": en_tabla,
        "tareas_archivadas": int(_meta_float("tareas_archivadas", 0.0)),
        "tareas_pend": con.execute("SELECT COUNT(*) c FROM tareas WHERE estado='queued'").fetchone()["c"],
        "registros": con.execute("SELECT COUNT(*) c FROM registros").fetchone()["c"],
        "emision": emission_info(),
        "dificultad": dificultad(),
        "recompensa_base_actual": pago1,
        "pago_por_tarea": pago1,
        "emision_por_min": info1.get("emision_por_min"),
        "detalle_pago": info1,
        # Pago por potencia (22-10-2026): el interruptor y el precio que se
        # esta aplicando de verdad, para poder auditarlo desde el panel.
        "modo_pago": "potencia" if PAGO_POR_FLOP else "plano",
        "pago_por_potencia": {"activo": PAGO_POR_FLOP,
                              "gflop_min_facturable": GFLOP_MIN_FACTURABLE,
                              "ventana_min": GFLOPS_VENTANA_MIN,
                              "precio_gflop": _pot.get("precio_gflop"),
                              # De donde sale el precio que se paga: definicion,
                              # ancla GPU-minuto (suelo) o trabajo medido.
                              "fuente": _pot.get("fuente"),
                              "gflop_divisor": _pot.get("gflop_divisor"),
                              "gflop_red_por_min": _pot.get("gflop_por_min"),
                              # Suelo por tarea (25-10-2026): ninguna paga menos
                              # de esto, que es el minimo que acepta /transferir.
                              "pago_min_kia": round(PAGO_MIN_KIA, 9),
                              # Definicion del KIA (25-10-2026): 1 KIA = 1 minuto
                              # de GPU de referencia. Se publica con el precio
                              # para que cualquiera pueda comprobarlo.
                              "definicion": ({"kia_por_minuto_gpu": KIA_POR_MINUTO_GPU,
                                              "gflop_por_minuto_gpu": GPU_REF_GFLOPS * 60.0,
                                              "precio_gflop": (KIA_POR_MINUTO_GPU /
                                                               (GPU_REF_GFLOPS * 60.0))}
                                             if PRECIO_DEFINIDO else None)},
        # El precio unico del minuto (23-10-2026): con esto se reparte el
        # presupuesto por trabajo, no por orden de llegada. Se publica el
        # precio, de donde sale y lo que llevamos pagado en el minuto.
        "bolsa_emision": {"kia_disponible": round(bolsa_disponible(), 6),
                          "presupuesto_kia_min": round(presupuesto_del_minuto(), 6),
                          "pagado_en_minuto": round(pagado_en_minuto(), 6),
                          "tope_min": 1,
                          "precio_minuto": (round(float(precio_unico_minuto()["precio"]), 18)
                                            if precio_unico_minuto()["precio"] else None),
                          "fuente_precio": precio_unico_minuto()["fuente"],
                          "gflop_minuto_anterior": precio_unico_minuto()["gflop_anterior"],
                          "credito_kia": precio_unico_minuto()["credito_kia"],
                          "objetivo_kia_min": precio_unico_minuto()["objetivo_kia_min"],
                          "ancla_gpu_min": precio_unico_minuto()["ancla"],
                          "gpu_ref_gflops": precio_unico_minuto()["gpu_ref_gflops"],
                          "gflop_divisor": precio_unico_minuto()["gflop_divisor"],
                          "suelo_gflop_min": precio_unico_minuto()["suelo_gflop_min"],
                          "kia_por_minuto_gpu": precio_unico_minuto()["kia_por_minuto_gpu"],
                          "modo_precio": precio_unico_minuto()["modo_precio"],
                          "precio_definido": PRECIO_DEFINIDO,
                          # Cuantos minutos de GPU de referencia paga la emision
                          # del minuto a la definicion (hasta donde no hay
                          # recorte). Por encima, el fusible reparte el tope.
                          "capacidad_gpu_min": precio_unico_minuto()["capacidad_gpu_min"],
                          "fusible_x": FUSIBLE_MINUTO_X},
        # ADAPTADOR DE REPARTO (25-10-2026): el minuto anterior, repartido entre
        # las GPUs que entregaron trabajo. `cuota_igual_kia` es lo que cobra
        # cada una si entregan lo mismo, y baja segun se unen mas: el total
        # nunca pasa del presupuesto, que es lo que sostiene el 13-04-2150.
        "reparto": _resumen_reparto(),
        # Verificacion redundante (23-10-2026): desde fuera se puede comprobar
        # que reglas estan aplicando de verdad los nodos que se verifican.
        "replicas": {"activa": REPLICA_ACTIVA,
                     "parte_verificador": PARTE_REPLICA,
                     "auditoria_1_de": AUDITORIA_1_DE,
                     "auditoria_1_de_joven": AUDITORIA_1_DE_JOVEN,
                     "tareas_confianza": AUDITORIA_TAREAS_CONFIANZA,
                     "mismo_dueno_bloqueado": True},
        "auto_tareas": {"activo": AUTO_ACTIVO, "objetivo_cola": AUTO_COLA_OBJETIVO,
                        "intervalo_seg": AUTO_INTERVALO},
        "archivado": {"activo": ARCHIVAR_ACTIVO, "retencion": RETENCION_TAREAS,
                      "lote": ARCHIVAR_LOTE, "intervalo_seg": ARCHIVAR_INTERVALO},
        # Resumen barato (sin recorrer la cadena): el detalle completo esta en
        # /ledger/integridad. El panel lo usa para el semaforo de seguridad.
        # DINERO REAL COBRADO (25-10-2026): los euros de la transcripcion y de
        # cualquier otro servicio de fuera de la red. No es emision: son euros.
        "ingresos": _resumen_ingresos(),
        "integridad": {"sellos": len(_ss),
                       "ultimo_sello_hash": (_ss[-1].get("hash") if _ss else None),
                       "pagos_bloqueados": LEDGER_ROTO},
    }

@app.post("/tareas/crear_auto")
def crear_auto(tipo: str = "matriz", _ok: None = Depends(exigir_admin)):
    """Crea una tarea con la dificultad ACTUAL de la red (tamano segun TOPS)."""
    dif = dificultad()
    tid = crear_tarea_auto(tipo)
    return {"tarea_id": tid, "estado": "queued", "dificultad": dif}

@app.post("/admin/liberar_atascadas")
def admin_liberar(_ok: None = Depends(exigir_admin)):
    """AUDITORIA: libera YA las tareas 'ejecutando' colgadas (sin esperar 30 min)."""
    forzar = True
    r, c = liberar_atascadas(forzar=True)
    return {"devueltas_a_la_cola": r, "cerradas_como_fallo": c}

# ============================================================
#  AUDITORIA 2026-10-18: generador automatico de trabajo
#  El minero no inventa tareas: solo pide trabajo. Si la cola se vacia,
#  la red se queda parada (paso de verdad: se quedo a 0 y el minero ocioso).
#  Este bucle mantiene la cola llena con la dificultad actual de la red.
#  Se apaga con KUKIA_AUTO_TAREAS=0 o desde /admin/auto_tareas.
# ============================================================
AUTO_ACTIVO = os.environ.get("KUKIA_AUTO_TAREAS", "1") != "0"
# Un colchon de 120 tareas con lote de 40 cada 10 s: el minero va a ~20
# tareas/min, asi que nunca se queda sin trabajo (con 40/20/15 se vaciaba
# entre pasadas y las GPUs esperaban).
AUTO_COLA_OBJETIVO = max(0, int(os.environ.get("KUKIA_AUTO_COLA", "120")))
AUTO_INTERVALO = max(3.0, float(os.environ.get("KUKIA_AUTO_SEG", "5")))
AUTO_POR_PASADA = max(1, int(os.environ.get("KUKIA_AUTO_LOTE", "40")))

# Trabajo variado: matriz e inferencia (aritmetica determinista) y difusion de
# calor. Los checksums SALEN de la mezcla el 22-10-2026: son 0 FLOPs, con pago
# por potencia cobran 0 y ocuparian cola sin pagar. El tipo sigue soportado
# (una tarea manual se ejecuta y se paga lo que valga su trabajo: 0).
MEZCLA_AUTO = ("matriz", "matriz", "matriz", "matriz",
               "difusion_calor", "difusion_calor", "inferencia")

def params_auto(tipo: str) -> dict:
    """Parametros de una tarea automatica, con el tamano que toca a la red."""
    dif = dificultad()
    rnd = secrets.randbelow
    if tipo == "matriz":
        return {"n": max(64, min(LIMITE_N, int(dif["matriz_n"]))), "seed": rnd(1_000_000)}
    if tipo == "inferencia":
        return {"n": max(32, min(LIMITE_N, int(dif["inferencia_n"]))), "seed": rnd(1_000_000)}
    if tipo == "checksum":
        return {"data": "kukia-" + secrets.token_hex(12)}
    if tipo == "difusion_calor":
        # Los pasos los fija el control de dificultad (objetivo ~2 s por tarea).
        # Antes una tarea n=2048/pasos=4000 costaba 7,4 min de verificacion en
        # CPU; hoy la verificacion va en GPU y el control no deja crecer sin tope.
        return {"n": 1024, "pasos": min(int(dif.get("difusion_pasos") or 1500),
                                         pasos_max_difusion(1024)),
                "seed": rnd(1_000_000), "tolerancia_rel": 1e-9}
    raise HTTPException(400, "tipo no soportado")

def crear_tarea_auto(tipo: str = None, cola: str = "C", prioridad: float = 0.0,
                     permitir_difusion: bool = True) -> str:
    """Crea una tarea de trabajo (misma validacion que /tareas/crear).

    `permitir_difusion=False` deja fuera las difusiones de la mezcla. Lo usa el
    generador cuando no hay ninguna GPU activa: fabricar difusiones que nadie
    puede servir llenaria la cola y dejaria al nodo CPU sin matriz/inferencia.
    """
    if tipo is None:
        mezcla = MEZCLA_AUTO
        if not permitir_difusion:
            mezcla = tuple(t for t in MEZCLA_AUTO if t != "difusion_calor")
        tipo = mezcla[secrets.randbelow(len(mezcla))]
    params = validar_params(tipo, params_auto(tipo))
    tid = uuid.uuid4().hex[:12]
    with _escritura():
        con.execute("INSERT INTO tareas(id,tipo,params,estado,creado,cola,campana,prioridad,vram_min) "
                    "VALUES(?,?,?,?,?,?,?,?,?)",
                    (tid, tipo, json.dumps(params), "queued", time.ctime(), cola, "auto",
                     float(prioridad), 0))
    return tid

def _bucle_auto_tareas():
    """Mantiene la cola con trabajo para que el minero nunca se quede parado."""
    while True:
        try:
            if LEDGER_ROTO:
                # Historial alterado: no se crea trabajo (el minero cobraria 0).
                pass
            elif AUTO_ACTIVO and AUTO_COLA_OBJETIVO > 0:
                # 23-10-2026: sin GPU activa no se fabrican difusiones (nadie
                # las puede servir) y las que ya esten encoladas NO cuentan
                # para el objetivo: si contaran, la cola se llenaria de
                # difusiones muertas y el nodo CPU se quedaria sin trabajo.
                hay_gpu = _hay_gpu_activa()
                dif_cola = con.execute("SELECT COUNT(*) c FROM tareas WHERE estado='queued' "
                                       "AND tipo='difusion_calor'").fetchone()["c"]
                # Tope de difusiones encoladas: aunque haya GPU, no se acumulan
                # mas de media cola. Antes, si las GPU se quedaban paradas un
                # rato, la cola se llenaba de difusiones y la matriz (que si
                # puede hacer una CPU) dejaba de fabricarse. El tope se mira
                # una vez por pasada: se puede pasar de el lo que quepa en esa
                # pasada (40), que es inocuo y evita 40 SELECT por pasada.
                permitir_dif = bool(hay_gpu) and dif_cola < max(10, AUTO_COLA_OBJETIVO // 2)
                if hay_gpu:
                    en_cola = con.execute("SELECT COUNT(*) c FROM tareas WHERE estado='queued'").fetchone()["c"]
                else:
                    en_cola = con.execute("SELECT COUNT(*) c FROM tareas WHERE estado='queued' "
                                          "AND COALESCE(tipo,'') <> 'difusion_calor'").fetchone()["c"]
                faltan = AUTO_COLA_OBJETIVO - en_cola
                if faltan > 0:
                    for _ in range(min(faltan, AUTO_POR_PASADA)):
                        crear_tarea_auto(permitir_difusion=permitir_dif)
                    print(f"[KUKIA Core] auto-tareas: cola {en_cola} -> {en_cola + min(faltan, AUTO_POR_PASADA)}", flush=True)
        except Exception as e:
            print(f"[KUKIA Core] auto-tareas error: {e}", flush=True)
        # Persistir el tamano de tarea que ha aprendido el control de dificultad.
        global _escala_pendiente
        if _escala_pendiente:
            try:
                with _escritura():
                    con.execute("INSERT INTO meta(clave,valor) VALUES('escala_tarea',?) "
                                "ON CONFLICT(clave) DO UPDATE SET valor=excluded.valor",
                                (round(_ESCALA_TAREA, 4),))
                _escala_pendiente = False
            except Exception:
                pass
        time.sleep(AUTO_INTERVALO)

threading.Thread(target=_bucle_auto_tareas, daemon=True).start()

# ============================================================
#  ARCHIVADO DE TAREAS VIEJAS
#  A pleno ritmo (~380 tareas/min) la tabla 'tareas' mete unos 500.000
#  registros al dia (~400 MB/dia). Se borran SOLO las terminadas (ok/fallo)
#  mas antiguas, dejando las ultimas KUKIA_RETENCION_TAREAS para auditar.
#  El ledger (donde estan los saldos) NO se toca, y los contadores de 'meta'
#  conservan el historico. SQLite reutiliza las paginas libres, asi que el
#  fichero deja de crecer en cuanto alcanza el tamano de la ventana.
# ============================================================
ARCHIVAR_ACTIVO = os.environ.get("KUKIA_ARCHIVAR", "1") != "0"
RETENCION_TAREAS = max(1000, int(os.environ.get("KUKIA_RETENCION_TAREAS", "200000")))
ARCHIVAR_LOTE = max(100, int(os.environ.get("KUKIA_ARCHIVAR_LOTE", "20000")))
ARCHIVAR_INTERVALO = max(30.0, float(os.environ.get("KUKIA_ARCHIVAR_SEG", "120")))

def archivar_tareas(retencion: int = None, lote: int = None) -> dict:
    """Borra tareas terminadas antiguas, en lotes, bajo el lock de escritura."""
    ret = max(1000, int(retencion or RETENCION_TAREAS))
    bat = max(100, int(lote or ARCHIVAR_LOTE))
    corte = con.execute("SELECT rowid r FROM tareas WHERE estado IN ('ok','fallo') "
                        "ORDER BY rowid DESC LIMIT 1 OFFSET ?", (ret - 1,)).fetchone()
    if not corte:
        return {"borradas": 0, "retencion": ret, "lote": bat}
    borradas = 0
    with _pago_lock:
        try:
            con.execute("BEGIN IMMEDIATE")
        except Exception:
            return {"borradas": 0, "retencion": ret, "lote": bat,
                    "error": "base ocupada, se reintenta"}
        try:
            cur = con.execute("DELETE FROM tareas WHERE rowid IN (SELECT rowid FROM tareas "
                              "WHERE rowid < ? AND estado IN ('ok','fallo') LIMIT ?)",
                              (corte["r"], bat))
            borradas = cur.rowcount or 0
            if borradas:
                con.execute("INSERT INTO meta(clave,valor) VALUES('tareas_archivadas',?) "
                            "ON CONFLICT(clave) DO UPDATE SET valor=valor+excluded.valor", (float(borradas),))
            con.commit()
        except Exception:
            try:
                _rollback_seguro()
            except Exception:
                pass
            raise
    return {"borradas": borradas, "retencion": ret, "lote": bat,
            "archivadas_total": int(_meta_float("tareas_archivadas", 0.0))}

# ============================================================
#  PODA DE TELEMETRIA (23-10-2026)
#  AUDITORIA 23-10, hallazgo H6: `planificador_log` y `gpu_telemetria` crecian
#  sin tope (medido: 396.165 y 307.503 filas, base de 349 MB) y solo se
#  limpiaban al borrar un nodo entero. Con el tiempo eso llena el disco y hace
#  lento cada INSERT. Ahora se poda lo viejo cada pasada del archivador, y hay
#  un tope duro de filas por si alguien inunda la telemetria en un dia.
#  No se toca NADA del ledger, saldos, tareas ni sellos: son tablas de traza.
# ============================================================
PODA_TELEMETRIA_H = max(1.0, float(os.environ.get("KUKIA_PODA_TELEMETRIA_H", "48")))
PODA_TELEMETRIA_MAX = max(1000, int(os.environ.get("KUKIA_PODA_TELEMETRIA_MAX", "200000")))


def _epoch_de_ts(txt) -> float | None:
    """Las tablas de traza guardan `time.ctime()` (texto), no epoch."""
    try:
        return time.mktime(time.strptime(str(txt), "%a %b %d %H:%M:%S %Y"))
    except Exception:
        return None


def podar_telemetria(horas: float = None, tope: int = None,
                     lote: int = 5000) -> dict:
    """Borra traza vieja de planificador_log y gpu_telemetria.

    Dos limites, en este orden:
      1. Por edad: lo mas viejo que `horas` (48 h por defecto). Se borra a
         trozos desde el principio (los id van en orden de llegada), asi la
         transaccion no se eterniza con 700.000 filas de golpe.
      2. Por tamano: si aun asi quedan mas de `tope` filas (por ejemplo, un
         dia con miles de nodos), se recorta por arriba dejando las ultimas.
    """
    horas = PODA_TELEMETRIA_H if horas is None else max(0.0, float(horas))
    tope = PODA_TELEMETRIA_MAX if tope is None else max(0, int(tope))
    corte = time.time() - horas * 3600.0
    borradas = {}
    with _escritura():
        for tabla in ("planificador_log", "gpu_telemetria"):
            total = 0
            while True:
                filas = con.execute(f"SELECT id, ts FROM {tabla} ORDER BY id ASC LIMIT ?",
                                    (lote,)).fetchall()
                if not filas:
                    break
                viejos = [r["id"] for r in filas if (_epoch_de_ts(r["ts"]) or 0.0) < corte]
                if not viejos:
                    break
                cur = con.execute(f"DELETE FROM {tabla} WHERE id <= ?", (viejos[-1],))
                total += max(0, cur.rowcount or 0)
                if len(viejos) < len(filas):
                    break
            # Tope duro: deja solo las ultimas `tope` filas.
            n = con.execute(f"SELECT COUNT(*) c FROM {tabla}").fetchone()["c"]
            if n > tope:
                cur = con.execute(f"DELETE FROM {tabla} WHERE id NOT IN "
                                  f"(SELECT id FROM {tabla} ORDER BY id DESC LIMIT ?)",
                                  (tope,))
                total += max(0, cur.rowcount or 0)
            borradas[tabla] = total
    return {"horas": horas, "tope": tope, "borradas": borradas,
            "total": sum(borradas.values())}


def _bucle_archivado():
    while True:
        try:
            if ARCHIVAR_ACTIVO:
                for _ in range(20):  # vaciar el atraso sin esperar a la siguiente vuelta
                    r = archivar_tareas()
                    if r.get("borradas"):
                        print(f"[KUKIA Core] archivado: {r['borradas']} tareas viejas "
                              f"(total {r['archivadas_total']})", flush=True)
                    if r.get("borradas", 0) < ARCHIVAR_LOTE or r.get("error"):
                        break
                    time.sleep(1)
        except Exception as e:
            print(f"[KUKIA Core] archivado error: {e}", flush=True)
        try:
            n = podar_nodos()
            if n:
                print(f"[KUKIA Core] podados {n} nodos que nunca trabajaron", flush=True)
        except Exception as e:
            print(f"[KUKIA Core] poda de nodos error: {e}", flush=True)
        try:
            r = podar_telemetria()
            if r.get("total"):
                print(f"[KUKIA Core] telemetria podada: {r['borradas']} "
                      f"(quedan los ultimos {r['horas']:.0f} h)", flush=True)
        except Exception as e:
            print(f"[KUKIA Core] poda de telemetria error: {e}", flush=True)
        time.sleep(ARCHIVAR_INTERVALO)

threading.Thread(target=_bucle_archivado, daemon=True).start()

@app.post("/admin/archivar")
def admin_archivar(retencion: int = -1, lote: int = -1, _ok: None = Depends(exigir_admin)):
    """Fuerza una pasada de archivado (sin esperar al bucle)."""
    return archivar_tareas(None if retencion < 0 else retencion,
                           None if lote < 0 else lote)

@app.post("/admin/podar_nodos")
def admin_podar_nodos(horas: float = -1, _ok: None = Depends(exigir_admin)):
    """Fuerza la poda de nodos que nunca trabajaron (pruebas y mantenimiento)."""
    return {"podados": podar_nodos(None if horas < 0 else horas)}

@app.post("/admin/podar_telemetria")
def admin_podar_telemetria(horas: float = -1, tope: int = -1,
                           _ok: None = Depends(exigir_admin)):
    """Fuerza la poda de traza (planificador_log y gpu_telemetria)."""
    return podar_telemetria(None if horas < 0 else horas,
                            None if tope < 0 else tope)

@app.post("/admin/recalcular_resumen")
def admin_recalcular_resumen(_ok: None = Depends(exigir_admin)):
    """Reconstruye saldos y suministro desde el ledger (reparacion)."""
    return recalcular_resumen()

@app.post("/admin/auto_tareas")
def admin_auto_tareas(activo: int = -1, objetivo: int = -1, _ok: None = Depends(exigir_admin)):
    """Enciende/apaga el generador de trabajo y fija el tamano de la cola.
    activo=1/0 y objetivo=N (tareas en cola que intenta mantener)."""
    global AUTO_ACTIVO, AUTO_COLA_OBJETIVO
    if activo >= 0:
        AUTO_ACTIVO = bool(activo)
    if objetivo >= 0:
        AUTO_COLA_OBJETIVO = int(objetivo)
    return {"activo": AUTO_ACTIVO, "objetivo_cola": AUTO_COLA_OBJETIVO,
            "intervalo_seg": AUTO_INTERVALO, "lote": AUTO_POR_PASADA}

class RegistroIn(BaseModel):
    nombre: str
    email: str
    gpu: str = ""
    tops: str = ""
    ceder_potencia: str = ""
    ref: str = ""

@app.post("/registro")
def registro(r: RegistroIn, request: Request):
    """Registro publico de la lista de espera (desde la web).

    AUDITORIA: antes el 'ref' se guardaba en la columna 'ip' y la IP real no
    se guardaba nunca. Ahora cada dato va a su columna."""
    # AUDITORIA 2026-10-28 (A2): la IP real, no la del socket (que detras del
    # tunel es 127.0.0.1 y dejaba todas las altas con la misma IP).
    ip = _ip_cliente(request)
    with _escritura():
        con.execute("INSERT INTO registros(nombre,email,gpu,tops,ceder,ref,ip,ts) VALUES(?,?,?,?,?,?,?,?)",
                    (r.nombre[:120], r.email[:160], r.gpu[:80], r.tops[:40],
                     r.ceder_potencia[:40], r.ref[:64], ip, time.ctime()))
    return {"ok": True, "mensaje": "Registrado. Te avisaremos al lanzamiento.",
            "posicion": con.execute("SELECT COUNT(*) c FROM registros").fetchone()["c"]}

@app.get("/registros")
def registros(_ok: None = Depends(exigir_admin)):
    return [dict(x) for x in con.execute("SELECT * FROM registros ORDER BY id DESC LIMIT 500").fetchall()]

@app.get("/saldo/{direccion}")
def saldo(direccion: str):
    """Saldo publico de una direccion (cualquiera puede consultarlo)."""
    fila = con.execute("SELECT total FROM saldos_cuenta WHERE cuenta=?",
                       (direccion,)).fetchone()
    s = fila["total"] if fila is not None else 0.0
    # AUDITORIA 2026-10-20: REAL de SQLite deja residuos (-4,6e-13) al sumar
    # muchos asientos. Se redondea SOLO al mostrar (el ledger sigue exacto).
    # CAOS 2026-10-22: ese residuo negativo se pintaba como "-0.0 KIA" y
    # parecia una cuenta en deuda. Polvo (< 1e-9) es cero, no deuda.
    valor = float(s or 0.0)
    if abs(valor) < 1e-9:
        valor = 0.0
    # 12 decimales, no 6 (25-10-2026, definicion 1 KIA = 1 minuto de GPU): una
    # tarea de cliente cuesta ~4e-8 KIA, y redondear a 6 publicaba "0" un saldo
    # que existe. Un cero falso es peor que un numero feo.
    return {"direccion": direccion, "saldo": round(valor, 12)}

@app.get("/saldos")
def saldos():
    """Mapa publico direccion -> saldo (desde los totales materializados)."""
    filas = con.execute("SELECT cuenta,total FROM saldos_cuenta").fetchall()
    return {"saldos": {r["cuenta"]: (0.0 if abs(float(r["total"] or 0.0)) < 1e-9
                                      else round(float(r["total"] or 0.0), 12)) for r in filas},
            "direcciones": len(filas)}

class TransferIn(BaseModel):
    de_nodo: str          # id de nodo O direccion KUK... de una billetera
    a_direccion: str
    monto: float
    nonce: str = ""
    firma: str = ""
    clave_publica: str = ""   # solo si el origen es una billetera (no un nodo)

# ============================================================
#  FIRMAS HIBRIDAS POSCUANTICAS (rama pqc-hibrido, 27-10-2026)
#
#  Camino NUEVO y paralelo: /transferir (v1) queda intacto, byte a byte. Si
#  esta integracion tiene un fallo, no puede llevarse por delante las
#  transferencias que hoy funcionan, porque no las toca.
#
#  Apagado por defecto: sin KUKIA_PQC=1 el Core se comporta como siempre y
#  /transferir/v2 solo contesta "desactivado". Una rama experimental no debe
#  cambiar el comportamiento de un Core que ya esta atendiendo.
#
#  El puente (pqc/puente_core.py) solo VERIFICA. El dinero se mueve aqui, con
#  el MISMO _pago_lock y el MISMO _asiento que el camino v1: una sola cadena
#  de hashes, un solo suministro, ninguna contabilidad paralela.
# ============================================================
try:
    from pqc import puente_core as PQC
except Exception as _e_pqc:          # el Core arranca aunque falte el paquete
    PQC = None
    print(f"[KUKIA] AVISO: firmas poscuanticas no disponibles ({_e_pqc})", flush=True)


class TransferInPQ(BaseModel):
    sobre: dict | str = ""            # sobre hibrido (JSON ya parseado o texto)
    clave_publica_ed25519: str = ""
    clave_publica_mldsa65: str = ""


@app.get("/pqc/estado")
def pqc_estado():
    """Si este Core verifica firmas poscuanticas y con que politica.

    Publico y de solo lectura a proposito: un cliente tiene que poder saber si
    mandar el sobre hibrido o quedarse en el camino v1, sin adivinar.
    """
    if PQC is None:
        return {"activo": False, "motivo": "el paquete pqc no esta instalado"}
    return PQC.estado()


@app.post("/transferir/v2")
def transferir_pq(t: TransferInPQ):
    """Transfiere KIA con sobre hibrido Ed25519 + ML-DSA-65.

    Que aporta frente a /transferir, y es la razon de que exista:
      - La firma cubre un mensaje canonico con dominio, red, version y la
        LISTA de algoritmos. Quitar la firma poscuantica cambia los bytes
        firmados, asi que el downgrade no verifica (no es "vale la que pase").
      - El origen es una direccion KQ1... que compromete las DOS claves
        publicas: presentar una clave y cobrar en otra no cuadra.
      - El importe va firmado como texto decimal y se comprueba que el float
        que llega al ledger es exactamente ese decimal.
      - El nonce se guarda en una tabla con UNIQUE: repetir un sobre es 409,
        tambien despues de reiniciar el Core.
    No crea moneda: debita y acredita lo mismo, asi que supply no cambia.
    """
    if PQC is None:
        raise HTTPException(503, "firmas poscuanticas no instaladas en este Core")
    if not PQC.ACTIVO:
        raise HTTPException(503, PQC.estado().get("motivo") or
                            "firmas poscuanticas desactivadas")
    if LEDGER_ROTO:
        raise HTTPException(503, "ledger alterado: transferencias bloqueadas (ver /ledger/integridad)")
    try:
        mov = PQC.transferencia(t.sobre, t.clave_publica_ed25519, t.clave_publica_mldsa65)
    except PQC.Rechazo as e:
        raise HTTPException(401, f"sobre rechazado: {e}")
    de, a, monto, nonce = mov["de"], mov["a"], mov["monto"], mov["nonce"]
    with _pago_lock:
        try:
            con.execute("BEGIN IMMEDIATE")
        except Exception:
            raise HTTPException(503, "Core ocupado, reintentar")
        try:
            # Tabla PROPIA del camino v2: no se toca la de v1 ni su UNIQUE.
            con.execute("CREATE TABLE IF NOT EXISTS transferencias_pq("
                        "id INTEGER PRIMARY KEY AUTOINCREMENT, de_nodo TEXT, "
                        "a_direccion TEXT, monto REAL, nonce TEXT, ts TEXT, "
                        "modo TEXT, sobre_hash TEXT, UNIQUE(de_nodo, nonce))")
            # El destino solo puede ser una direccion (v1 o v2) o un nodo que
            # exista. Igual que v1: un destino inventado quemaria el saldo.
            if (not direccion_valida(a) and not PQC.es_direccion_v2(a)
                    and not con.execute("SELECT 1 FROM nodos WHERE id=?", (a,)).fetchone()):
                con.execute("ROLLBACK")
                raise HTTPException(400, "destino invalido: usa una direccion "
                                         "KUK.../KQ1... o un nodo registrado")
            saldo = float(con.execute("SELECT COALESCE(SUM(kuki),0) s FROM ledger "
                                      "WHERE nodo_id=?", (de,)).fetchone()["s"] or 0.0)
            if saldo < monto:
                con.execute("ROLLBACK")
                raise HTTPException(409, "saldo insuficiente")
            try:
                con.execute("INSERT INTO transferencias_pq(de_nodo,a_direccion,monto,"
                            "nonce,ts,modo,sobre_hash) VALUES(?,?,?,?,?,?,?)",
                            (de, a, monto, nonce, time.ctime(), mov["modo"],
                             mov["sobre_hash"]))
            except Exception:
                con.execute("ROLLBACK")
                raise HTTPException(409, "nonce ya usado (repeticion bloqueada)")
            # ID propio ('tx-pq-') para que un asiento v2 no pueda confundirse
            # con uno v1 aunque el nonce se llame igual.
            _asiento(f"tx-pq-{nonce}", de, -monto, "transferencia pq salida")
            _asiento(f"tx-pq-{nonce}", a, monto, "transferencia pq entrada")
            con.commit()
            return {"ok": True, "de": de, "a": a, "monto": monto,
                    "monto_firmado": mov["monto_texto"], "modo": mov["modo"],
                    "nonce": nonce, "sobre_hash": mov["sobre_hash"],
                    "supply": round(supply_emitido(), 6)}
        except HTTPException:
            try:
                con.execute("ROLLBACK")
            except Exception:
                pass
            raise
        except Exception as e:
            try:
                con.execute("ROLLBACK")
            except Exception:
                pass
            raise _error_escritura(e, "error interno en transferencia pq")


@app.post("/transferir")
def transferir(t: TransferIn):
    """Transfiere KIA con firma Ed25519 y anti-repeticion.

    Dos origenes validos:
      1) Un NODO registrado con clave publica (worker/minero).
      2) Una BILLETERA KUK... que presenta su clave publica. La direccion se
         deriva de esa clave (KUK + SHA256(pub)[:38]) y tiene que coincidir con
         el origen, asi que nadie puede gastar el saldo de otra billetera. No
         hay que registrar la billetera como nodo: no ensucia la red.

    La firma es sobre "transferir:<de>:<a>:<monto>:<nonce>". El nonce solo
    vale una vez (UNIQUE en transferencias). Todo ocurre en una transaccion:
    sin saldo no hay debito ni credito. No crea moneda: los dos asientos se
    compensan, por eso /stats.supply_emitido no cambia con una transferencia.
    """
    de = (t.de_nodo or "").strip()
    a = (t.a_direccion or "").strip()
    # Las direcciones KUK... se guardan en mayusculas en el ledger; los ids de
    # nodo son hex en minusculas. Normalizar solo lo que es una direccion.
    if direccion_valida(de):
        de = de.upper()
    if direccion_valida(a):
        a = a.upper()
    if not a or len(a) > 128:
        raise HTTPException(400, "destino invalido")
    if a == de:
        raise HTTPException(400, "origen y destino iguales")
    try:
        crudo = float(t.monto)
    except Exception:
        raise HTTPException(400, "monto invalido")
    # AUDITORIA: NaN esquiva "monto <= 0" y "saldo < monto" (NaN no es ni
    # mayor ni menor), y acababa escribiendo asientos NULL y devolviendo 500.
    if not math.isfinite(crudo):
        raise HTTPException(400, "monto debe ser un numero finito")
    if crudo <= 0:
        raise HTTPException(400, "monto debe ser positivo")
    # AUDITORIA 25-10-2026 (hallazgo A2): antes se redondeaba a 4 decimales ANTES
    # de mirar la firma, asi que la firma podia no cubrir el importe movido. El
    # cliente firmaba 1,00005 y el Core verificaba "...:1.0001:..." (401 de
    # "firma invalida" sin explicar nada, o un asiento por un importe distinto
    # del firmado). Ahora el importe que se pide es el que se mueve: con mas de
    # 4 decimales se rechaza con 400 y no se toca el saldo. La billetera web ya
    # pide "maximo 4 decimales" y el suelo transferible son 0,0001, asi que
    # ningun cliente legitimo pierde nada.
    monto = round(crudo, 4)
    if abs(crudo - monto) > 1e-12:
        raise HTTPException(400, "monto con mas de 4 decimales (4 como maximo)")
    if monto > MAX_SUPPLY:
        raise HTTPException(400, "monto mayor que el suministro total")
    if not t.nonce or len(t.nonce) > 64:
        raise HTTPException(400, "nonce invalido")
    if LEDGER_ROTO:
        raise HTTPException(503, "ledger alterado: transferencias bloqueadas (ver /ledger/integridad)")
    with _pago_lock:
        try:
            con.execute("BEGIN IMMEDIATE")
        except Exception:
            raise HTTPException(503, "Core ocupado, reintentar")
        try:
            # P-1 (27-10-2026): el CREATE declaraba 6 columnas y el INSERT de
            # abajo escribe 7 valores con `firma` y `clave_publica`. En una base
            # NUEVA la tabla se creaba sin esas dos y el INSERT reventaba; el
            # except generico de mas abajo lo contaba como "nonce ya usado"
            # (409), asi que la PRIMERA transferencia de una instalacion nueva no
            # se guardaba nunca y el mensaje encima mentia. En un ledger ya
            # migrado el CREATE no se ejecuta (la tabla existe con sus 8
            # columnas), por eso en produccion no saltaba. Se declaran las 8 con
            # el MISMO DEFAULT que la migracion de arriba, para que una base
            # nueva quede igual que una migrada.
            con.execute("CREATE TABLE IF NOT EXISTS transferencias(id INTEGER PRIMARY KEY AUTOINCREMENT, "
                        "de_nodo TEXT, a_direccion TEXT, monto REAL, nonce TEXT, ts TEXT, "
                        "firma TEXT DEFAULT '', clave_publica TEXT DEFAULT '', "
                        "UNIQUE(de_nodo, nonce))")
            # El destino solo puede ser una direccion KUK... o un nodo que
            # exista: antes se aceptaba cualquier cadena y el saldo se quemaba.
            if not direccion_valida(a) and not con.execute(
                    "SELECT 1 FROM nodos WHERE id=?", (a,)).fetchone():
                _rollback_seguro()
                raise HTTPException(400, "destino invalido: usa una direccion KUK... o un nodo registrado")
            node = con.execute("SELECT clave_publica FROM nodos WHERE id=?", (de,)).fetchone()
            pub = ((node["clave_publica"] if node else "") or "").strip().lower()
            if not pub:
                # Camino BILLETERA: origen KUK... + clave publica que deriva
                # exactamente en esa direccion (nada que registrar).
                candidata = (t.clave_publica or "").strip().lower()
                if not pubkey_valida(candidata) or direccion_de_pubkey(candidata) != de:
                    _rollback_seguro()
                    raise HTTPException(401, "emisor sin clave registrada "
                                             "(billetera: envia clave_publica y firma)")
                pub = candidata
            if not t.firma:
                _rollback_seguro()
                raise HTTPException(401, "falta firma")
            try:
                from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
                Ed25519PublicKey.from_public_bytes(bytes.fromhex(pub)).verify(
                    bytes.fromhex(t.firma),
                    f"transferir:{de}:{a}:{monto}:{t.nonce}".encode())
            except Exception:
                _rollback_seguro()
                raise HTTPException(401, "firma invalida")
            saldo = float(con.execute("SELECT COALESCE(SUM(kuki),0) s FROM ledger WHERE nodo_id=?",
                                      (de,)).fetchone()["s"] or 0.0)
            if saldo < monto:
                _rollback_seguro()
                raise HTTPException(409, "saldo insuficiente")
            try:
                con.execute("INSERT INTO transferencias(de_nodo,a_direccion,monto,nonce,ts,"
                            "firma,clave_publica) VALUES(?,?,?,?,?,?,?)",
                            (de, a, monto, t.nonce, time.ctime(), t.firma, pub))
            except sqlite3.IntegrityError as e:
                _rollback_seguro()
                if _nonce_repetido(e):
                    raise HTTPException(409, "nonce ya usado (repeticion bloqueada)")
                # 27-10-2026: esto NO es una repeticion. Un NOT NULL, un CHECK,
                # una clave ajena o el RAISE() de un trigger llegan por el mismo
                # except, y hasta hoy salian como 409 "nonce ya usado": el que
                # reintentaba con otro nonce se comia el mismo 409 para siempre y
                # del fallo real no quedaba ni una linea. Ahora va como fallo de
                # escritura (500/503/507 segun el caso) y queda en el registro.
                print(f"[KUKIA] fallo de escritura guardando la transferencia "
                      f"(no es repeticion): {type(e).__name__}: {e}", flush=True)
                raise _error_escritura(e, "error guardando la transferencia")
            except Exception as e:
                # AUDITORIA 24-10-2026: antes CUALQUIER fallo de este INSERT se
                # contaba como "nonce ya usado". Con la base llena (SQLITE_FULL)
                # la transferencia decia 409 "repeticion" cuando el problema era
                # el espacio: mentia y nadie reintentaba. Ahora FULL -> 507.
                _rollback_seguro()
                raise _error_escritura(e, "error guardando la transferencia")
            # ID COMPLETO (23-10-2026): antes era el nonce cortado a 12 letras y
            # dos transferencias distintas podian caer en el MISMO id de asiento
            # (paso el 19-10 con tres barridos). El importe y las cuentas estaban
            # bien, pero emparejarlas despues era a ciegas. Con el nonce entero,
            # cada transferencia tiene su id y su pareja de asientos.
            _asiento(f"tx-{t.nonce}", de, -monto, "transferencia salida")
            _asiento(f"tx-{t.nonce}", a, monto, "transferencia entrada")
            con.commit()
            return {"ok": True, "de": de, "a": a, "monto": monto,
                    "supply": round(supply_emitido(), 6)}
        except HTTPException:
            try:
                _rollback_seguro()
            except Exception:
                pass
            raise
        except Exception as e:
            try:
                _rollback_seguro()
            except Exception:
                pass
            raise _error_escritura(e, "error interno en transferencia")

# ============================================================
#  SERIE TEMPORAL (panel y web)
#  Se calcula en el Core para que el dashboard NO abra una segunda conexion
#  a la base (lector + escritor a la vez = bloqueos). Cache de 5 s.
# ============================================================
# ============================================================
#  QUIEN ES QUIEN: DOS NODOS DEL MISMO DUENO NO SE VERIFICAN ENTRE SI
#  (23-10-2026)
#
#  El Core paga sin recalcular cuando la replica coincide con el primario y la
#  tarea no cae en la muestra. Eso esta bien contra un nodo suelto, pero el
#  registro permite varias claves desde la misma IP: dos identidades del mismo
#  dueno se dan la razon y cobran un resultado que nadie comprobo. Lo medido:
#  0,918328 KIA por una matriz falsa en `tools/auditoria_replica.py`.
#
#  Aqui se corta por el ladron mas barato: la MISMA CUENTA DE COBRO o la MISMA
#  IP de registro (24 h) son el mismo dueno, y no se le da replica de su propia
#  tarea. La colusion entre dos maquinas distintas sigue siendo posible: para
#  eso esta el muestreo (1/3 en nodos sin historial) y esta apuntado como
#  hueco conocido en el informe de auditoria.
# ============================================================
def _ip_de_nodo(node) -> set:
    """IPs de registro del nodo en las ultimas 24 h (puede ser vacio)."""
    try:
        pub = (node["clave_publica"] or "").strip().lower()
    except Exception:
        return set()
    if not pub:
        return set()
    try:
        filas = con.execute("SELECT ip FROM registros_nodo WHERE clave_publica=? "
                            "AND ts>=?", (pub, time.time() - 86400.0)).fetchall()
    except Exception:
        return set()
    return {str(r["ip"] or "").strip() for r in filas if str(r["ip"] or "").strip()}


def _mismo_dueno(a, b) -> bool:
    """¿Los dos nodos son del mismo dueno? Misma cuenta de cobro o misma IP.

    Por defecto, si no se puede saber, NO se consideran del mismo dueno: la
    replica sigue siendo valida y decide el muestreo. Preferimos no romper la
    red por un dato que falta.
    """
    if a is None or b is None:
        return False
    try:
        if _cuenta_de(a["id"]) == _cuenta_de(b["id"]):
            return True
    except Exception:
        pass
    try:
        return bool(_ip_de_nodo(a) & _ip_de_nodo(b))
    except Exception:
        return False


def _frecuencia_auditoria(*nodos) -> int:
    """1 de cada cuantas tareas con replica se recalculan.

    Normal: AUDITORIA_1_DE (10). Si alguno de los dos nodos tiene menos de
    AUDITORIA_TAREAS_CONFIANZA tareas verificadas: AUDITORIA_1_DE_JOVEN (1 =
    se recalcula siempre).
    """
    if AUDITORIA_1_DE <= 0:
        return 0
    try:
        joven = any(int(n["tareas_ok"] or 0) < AUDITORIA_TAREAS_CONFIANZA
                    for n in nodos if n is not None)
    except Exception:
        joven = True
    return min(AUDITORIA_1_DE, AUDITORIA_1_DE_JOVEN) if joven else AUDITORIA_1_DE


def _es_replicable(tipo: str, params) -> bool:
    """Merece la pena que la resuelva un segundo nodo?

    Solo las tareas CARAS: duplicar una tarea de 3 ms cuesta mas (asignar,
    firmar, red) que recalcularla en el Core. El umbral es el mismo que usa la
    medida de potencia: 1 GFLOP. En la practica esto cubre las difusiones,
    que son las que se comian el 99 % del CPU de verificacion.
    """
    if not REPLICA_ACTIVA:
        return False
    if tipo not in ("matriz", "difusion_calor", "inferencia"):
        return False      # embeddings: comparar dos JSON con tolerancia es delicado
    return _flops_tarea(tipo, params) >= REPLICA_FLOPS_MIN


def _reclamar_replica(nodo_id: str, vram: int, sin_difusion: bool = False):
    """Se lleva una tarea CARA asignada a OTRO nodo para verificarla.

    Se llama dentro de la transaccion de /tareas/pendiente (BEGIN IMMEDIATE),
    asi que dos nodos no pueden quedarse la misma replica: la PRIMARY KEY de
    tareas_replica lo impide y se prueba con INSERT OR IGNORE.

    Devuelve (id, tipo, params) o None.
    """
    ahora = time.time()
    # Replicas caducadas.
    con.execute("DELETE FROM tareas_replica WHERE estado='reclamando' "
                "AND COALESCE(asignado,0) < ?", (ahora - REPLICA_TTL,))
    # Huerfanas (su tarea ya no esta en ejecucion). Con LIMIT y JOIN por clave
    # primaria: un DELETE ... IN (SELECT ... FROM tareas) recorreria las 94.000
    # tareas en CADA peticion de trabajo.
    con.execute("DELETE FROM tareas_replica WHERE rowid IN ("
                "SELECT r.rowid FROM tareas_replica r JOIN tareas t ON t.id=r.tarea_id "
                "WHERE t.estado!='ejecutando' LIMIT 20)")
    # Techo de verificacion simultanea: nunca mas de REPLICA_FRACCION de los
    # nodos activos. El resto ejecuta, que es lo que hace avanzar la cola
    # (sin tareas ejecutandose tampoco habria nada que verificar).
    try:
        activos = int(con.execute("SELECT COUNT(*) c FROM nodos WHERE visto > ?",
                                  (ahora - 300.0,)).fetchone()["c"] or 0)
    except Exception:
        activos = 0
    tope = max(1, int(round(activos * REPLICA_FRACCION)))
    en_curso = int(con.execute("SELECT COUNT(*) c FROM tareas_replica "
                               "WHERE estado='reclamando'").fetchone()["c"] or 0)
    if en_curso >= tope:
        return None
    filas = con.execute(
        "SELECT t.id,t.tipo,t.params,t.nodo_id FROM tareas t "
        "WHERE t.estado='ejecutando' AND t.nodo_id IS NOT NULL AND t.nodo_id!=? "
        "AND COALESCE(t.asignado,0) > ? AND COALESCE(t.vram_min,0) <= ? "
        "AND COALESCE(t.nodos_intento,'') NOT LIKE ? "
        "AND (? = 0 OR t.tipo <> 'difusion_calor') "
        "AND NOT EXISTS (SELECT 1 FROM tareas_replica r WHERE r.tarea_id=t.id) "
        "ORDER BY t.asignado DESC LIMIT 8",
        (nodo_id, ahora - ABANDONO_SEG, vram, f"%{nodo_id}%",
         1 if sin_difusion else 0)).fetchall()
    yo = con.execute("SELECT * FROM nodos WHERE id=?", (nodo_id,)).fetchone()
    for f in filas:
        try:
            p = json.loads(f["params"])
        except Exception:
            continue
        if not _es_replicable(f["tipo"], p):
            continue
        # MISMO DUENO NO SE VERIFICA: ni su propia tarea (ya lo corta el SQL)
        # ni la de otra identidad de la misma cuenta o de la misma IP.
        try:
            dueno = con.execute("SELECT * FROM nodos WHERE id=?", (f["nodo_id"],)).fetchone()
        except Exception:
            dueno = None
        if _mismo_dueno(yo, dueno):
            continue
        cur = con.execute("INSERT OR IGNORE INTO tareas_replica"
                          "(tarea_id,nodo_id,estado,asignado) VALUES(?,?,'reclamando',?)",
                          (f["id"], nodo_id, ahora))
        if cur.rowcount == 1:
            return (f["id"], f["tipo"], p)
    return None


def _toca_auditoria(tid: str, frecuencia: int = None) -> bool:
    """¿Esta tarea se recalcula aunque haya replica? Determinista por el ID.

    Determinista y no aleatorio: el mismo id cae siempre en el mismo lado, asi
    que un nodo no puede "probar suerte" repitiendo la entrega, y una prueba
    puede saber de antemano si esa tarea sera auditada. `frecuencia` permite
    reforzar el muestreo (1 de cada 3) cuando alguno de los dos nodos es nuevo
    (ver `_frecuencia_auditoria`).
    """
    n = AUDITORIA_1_DE if frecuencia is None else int(frecuencia)
    if n <= 0:
        return False
    if n == 1:
        return True
    h = int(hashlib.sha256(str(tid).encode()).hexdigest()[:8], 16)
    return h % n == 0


def _coinciden(tipo: str, params, a: str, b: str) -> bool:
    """Dos resultados de dos nodos distintos: son el mismo resultado?"""
    a = str(a or "").strip()
    b = str(b or "").strip()
    if not a or not b:
        return False
    if tipo == "difusion_calor":
        try:
            fa, fb = float(a), float(b)
            if not (math.isfinite(fa) and math.isfinite(fb)):
                return False
            ref = max(abs(fa), abs(fb), 1e-12)
            return abs(fa - fb) / ref <= 1e-6
        except Exception:
            return False
    return a == b


def _flops_tarea(tipo: str, params) -> float:
    """FLOPs aproximados de una tarea, para medir potencia REAL (no declarada)."""
    try:
        p = json.loads(params) if isinstance(params, str) else (params or {})
    except Exception:
        return 0.0
    try:
        n = int(p.get("n") or 0)
        if tipo == "matriz":
            return 2.0 * n ** 3
        if tipo == "inferencia":
            return 18.0 * n ** 2
        if tipo == "difusion_calor":
            return 10.0 * n ** 2 * int(p.get("pasos") or 0)
    except Exception:
        return 0.0
    return 0.0


_serie_cache = {"ts": 0.0, "minutos": 0, "datos": None}

@app.get("/red/serie")
def red_serie(minutos: int = 60):
    """Serie por minuto: emision acumulada, tareas y GFLOPS medidos.

    GFLOPS = FLOPs de las tareas verificadas / segundos que tardaron de verdad.
    No es lo que declara el nodo al registrarse: es trabajo comprobado.
    """
    minutos = max(5, min(180, int(minutos or 60)))
    ahora = time.time()
    if (_serie_cache["datos"] is not None and _serie_cache["minutos"] == minutos
            and ahora - _serie_cache["ts"] < 5.0):
        return _serie_cache["datos"]
    desde = ahora - minutos * 60
    supply = float(con.execute("SELECT COALESCE(SUM(kuki),0) s FROM ledger").fetchone()["s"] or 0.0)
    add = [0.0] * minutos
    for r in con.execute("SELECT kuki, ts FROM ledger ORDER BY id DESC LIMIT 20000").fetchall():
        e = _epoch(r["ts"])
        if e < desde:
            continue
        i = min(minutos - 1, max(0, int((e - desde) // 60)))
        add[i] += float(r["kuki"] or 0.0)
    tareas = [0] * minutos
    flops = [0.0] * minutos
    ms = [0.0] * minutos
    for r in con.execute("SELECT tipo, params, fin, duracion_ms FROM tareas "
                         "WHERE estado='ok' AND fin IS NOT NULL "
                         "ORDER BY rowid DESC LIMIT 15000").fetchall():
        e = _epoch(r["fin"])
        if e < desde:
            continue
        i = min(minutos - 1, max(0, int((e - desde) // 60)))
        tareas[i] += 1
        ms[i] += float(r["duracion_ms"] or 0.0)
        flops[i] += _flops_tarea(r["tipo"], r["params"])
    sufijo = [0.0] * (minutos + 1)
    for i in range(minutos - 1, -1, -1):
        sufijo[i] = sufijo[i + 1] + add[i]
    puntos = []
    for i in range(minutos):
        e = desde + (i + 1) * 60
        puntos.append({
            "t": int(e),
            "hora": time.strftime("%H:%M", time.localtime(e)),
            "supply": round(supply - sufijo[i + 1], 4),
            "tareas": tareas[i],
            "gflops": round(flops[i] / (ms[i] / 1000.0) / 1e9, 3) if ms[i] > 0 else 0.0,
            "seg_computo": round(ms[i] / 1000.0, 2),
        })
    total_ms = sum(ms)
    datos = {
        "minutos": minutos,
        "bucket_seg": 60,
        "supply_actual": round(supply, 4),
        "tareas_ventana": sum(tareas),
        "gflops_ventana": round(sum(flops) / (total_ms / 1000.0) / 1e9, 3) if total_ms > 0 else 0.0,
        "puntos": puntos,
    }
    _serie_cache.update({"ts": ahora, "minutos": minutos, "datos": datos})
    return datos

class AtestacionIn(BaseModel):
    validador: str
    clave_publica: str
    desde: float = 0.0
    hasta: float = 0.0
    revisadas: int = 0
    ok: bool = True
    discrepancias: list = []
    huella: str = ""
    ts: float = 0.0
    firma: str = ""


@app.get("/red/witness")
def red_witness(desde: float = 0.0, limite: int = 20):
    """Trabajo ya verificado para que un validador independiente lo recalcule.

    Devuelve un manifiesto JSON canonico FIRMADO con la clave del sello del
    Core. El validador comprueba la firma antes de fiarse del contenido: asi
    nadie puede colarle tareas falsas por el camino.

    Solo incluye trabajo verificado (tipo, params, resultado, nodo y hora).
    No hay claves privadas, saldos ni datos personales.
    """
    desde = max(0.0, min(float(desde or 0.0), time.time()))
    limite = max(1, min(100, int(limite or 20)))
    filas = con.execute("SELECT id,tipo,params,metrica,fin,nodo_id FROM tareas "
                        "WHERE estado='ok' AND metrica IS NOT NULL "
                        "ORDER BY rowid DESC LIMIT 1500").fetchall()
    items = []
    for r in filas:
        try:
            params = json.loads(r["params"])
        except Exception:
            continue
        e = _epoch(r["fin"])
        if e < desde:
            continue
        items.append({"id": r["id"], "tipo": r["tipo"], "params": params,
                      "metrica": r["metrica"], "fin": round(e, 3),
                      "nodo": r["nodo_id"]})
    items.sort(key=lambda x: (x["fin"], x["id"]))
    items = items[:limite]
    hasta = max([i["fin"] for i in items], default=desde)
    manifiesto = {"items": items, "desde": round(desde, 3),
                  "hasta": round(hasta, 3), "ts": int(time.time()), "v": 1}
    texto = (SELLOS.canonico(manifiesto) if SELLOS is not None
             else json.dumps(manifiesto, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=False))
    return {"manifiesto": texto, "firma": _firmar_texto(texto),
            "clave_publica": _sello_pub, "items": len(items),
            "hasta": round(hasta, 3)}


@app.post("/validador/atestacion")
def validador_atestacion(a: AtestacionIn):
    """Registra la conformidad (o discrepancia) de un validador independiente.

    Firma Ed25519 del propio validador sobre el JSON canonico de la
    atestacion. Es informativo: NO paga, NO bloquea pagos y NO cambia saldos.
    Su valor es que varias maquinas distintas recalculen lo mismo y lo firmen:
    si una discrepa, queda registrado y se ve en el panel.
    """
    nombre = (a.validador or "").strip()[:64]
    pub = (a.clave_publica or "").strip().lower()
    if not nombre or not pubkey_valida(pub):
        raise HTTPException(400, "validador o clave_publica invalidos")
    if not a.firma:
        raise HTTPException(401, "falta firma del validador")
    try:
        revisadas = max(0, min(1000, int(a.revisadas)))
        desde = round(float(a.desde or 0.0), 3)
        hasta = round(float(a.hasta or 0.0), 3)
        ts = int(float(a.ts or 0.0))
    except Exception:
        raise HTTPException(400, "campos numericos invalidos")
    if ts <= 0 or abs(time.time() - ts) > 3600:
        raise HTTPException(401, "atestacion caducada (revisa el reloj)")
    dis = a.discrepancias if isinstance(a.discrepancias, list) else []
    dis = dis[:50]
    datos = {"validador": nombre, "clave_publica": pub, "desde": desde,
             "hasta": hasta, "revisadas": revisadas, "ok": bool(a.ok),
             "discrepancias": dis, "huella": (a.huella or "").strip()[:64],
             "ts": ts}
    texto = (SELLOS.canonico(datos) if SELLOS is not None
             else json.dumps(datos, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=False))
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(pub)).verify(
            bytes.fromhex(a.firma), texto.encode())
    except Exception:
        raise HTTPException(401, "firma de validador invalida")
    with _escritura():
        con.execute("INSERT INTO validador_atestaciones(validador,clave_publica,desde,hasta,"
                    "revisadas,ok,discrepancias,detalle,huella,ts,firma) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (nombre, pub, desde, hasta, revisadas, 1 if a.ok else 0, len(dis),
                     json.dumps(dis)[:8000], (a.huella or "")[:64], float(ts), a.firma[:256]))
        # Historial acotado: 7 dias bastan para auditar y no crece sin freno.
        con.execute("DELETE FROM validador_atestaciones WHERE ts < ?",
                    (time.time() - 7 * 86400,))
    return {"ok": True, "aceptada": True, "validador": nombre,
            "revisadas": revisadas, "discrepancias": len(dis)}


@app.get("/validador/estado")
def validador_estado():
    """Ultima atestacion de cada validador, actividad 24 h y discrepancias."""
    hace24 = time.time() - 86400
    filas = con.execute("SELECT * FROM validador_atestaciones "
                        "ORDER BY id DESC LIMIT 500").fetchall()
    vistos = {}
    for r in filas:
        if r["validador"] in vistos:
            continue
        vistos[r["validador"]] = {
            "validador": r["validador"],
            "clave_publica": (r["clave_publica"] or "")[:16] + "...",
            "ts": r["ts"], "desde": r["desde"], "hasta": r["hasta"],
            "revisadas": r["revisadas"], "ok": bool(r["ok"]),
            "discrepancias": r["discrepancias"],
        }
    activos = [v for v in vistos.values() if float(v["ts"] or 0) >= hace24]
    dis24 = con.execute("SELECT COUNT(*) c FROM validador_atestaciones "
                        "WHERE ts>=? AND ok=0", (hace24,)).fetchone()["c"]
    rev24 = con.execute("SELECT COALESCE(SUM(revisadas),0) s FROM validador_atestaciones "
                        "WHERE ts>=?", (hace24,)).fetchone()["s"]
    return {"clave_witness": _sello_pub, "activos": len(activos),
            "total": len(vistos), "revisadas_24h": int(rev24 or 0),
            "discrepancias_24h": int(dis24 or 0),
            "validadores": sorted(vistos.values(),
                                  key=lambda x: -float(x["ts"] or 0))}


@app.get("/red/telemetria")
def red_telemetria():
    """Estado EN VIVO por nodo/GPU + historico de tareas (solo lectura).

    El panel elige la primera fila de cada (nodo, device): por eso las filas
    vivas van primero. Solo se muestran latidos de los ultimos 5 minutos; si
    el minero se para, la tarjeta vuelve al ultimo dato historico."""
    vivas = []
    try:
        filas = con.execute("SELECT nodo_id,device,metricas,ts,origen FROM gpu_estado "
                            "WHERE ts>=? ORDER BY ts DESC LIMIT 100",
                            (time.time() - 300.0,)).fetchall()
        vivas = [dict(r) for r in filas]
    except Exception:
        pass
    # Una fila por (nodo, GPU): la ULTIMA de cada una. Con "las 200 mas
    # recientes" una GPU que solo hace tareas largas (difusiones) desaparecia
    # de la lista y el panel sumaba 42 GFLOPS en vez de 84 (visto 20-10-2026).
    ultimas = []
    try:
        ultimas = con.execute(
            "SELECT nodo_id,device,metricas,ts,origen FROM gpu_telemetria "
            "WHERE id IN (SELECT MAX(id) FROM gpu_telemetria GROUP BY nodo_id, device) "
            "ORDER BY id DESC LIMIT 50").fetchall()
    except Exception:
        pass
    try:
        hist = con.execute("SELECT nodo_id,device,metricas,ts,origen FROM gpu_telemetria "
                           "ORDER BY id DESC LIMIT 200").fetchall()
    except Exception:
        hist = []
    # Las vivas primero (el panel se queda con la primera de cada nodo|GPU),
    # luego la ultima de cada GPU, y despues el historico para lo que falte.
    vistas = set()
    salida = []
    for r in vivas + [dict(x) for x in ultimas] + [dict(x) for x in hist]:
        clave = (str(r.get("nodo_id") or ""), str(r.get("device") or ""))
        if clave in vistas:
            continue
        vistas.add(clave)
        salida.append(r)
    return {"telemetria": salida}

# ============================================================
#  PUERTA DE CLIENTE - endpoints
# ============================================================

def exigir_cliente(x_cliente_token: str = Header(default="", alias="X-Cliente-Token")):
    """Dependencia: el token tiene que corresponder a un cliente activo."""
    c = _cliente_por_token(x_cliente_token)
    if c is None:
        raise HTTPException(401, "token de cliente invalido (X-Cliente-Token)")
    return c


@app.post("/admin/clientes")
def admin_alta_cliente(c: ClienteAltaIn, _ok: None = Depends(exigir_admin)):
    """Da de alta un cliente. Devuelve el token UNA sola vez."""
    return _crear_cliente(c.nombre, c.direccion)


@app.get("/admin/clientes")
def admin_lista_clientes(_ok: None = Depends(exigir_admin)):
    """Lista de clientes. Nunca devuelve el token ni su hash."""
    filas = con.execute("SELECT id,nombre,direccion,creado,activo,tareas,gastado "
                        "FROM clientes ORDER BY creado DESC").fetchall()
    return {"clientes": [dict(f) for f in filas]}


class BajaClientesIn(BaseModel):
    """Baja LOGICA de clientes de prueba (25-10-2026). No borra nada."""
    ids: list[str] = []
    nombre_contiene: str = ""
    todos: bool = False
    seco: bool = True        # por defecto: solo dice lo que haria
    forzar: bool = False     # baja tambien a quien tiene tareas en curso


@app.post("/admin/clientes/baja")
def admin_baja_clientes(b: BajaClientesIn, _ok: None = Depends(exigir_admin)):
    """Da de baja clientes en bloque: `activo=0` y NADA mas.

    Por que asi, y no borrando:
      - los asientos del ledger no se tocan (la cadena de hashes y los sellos
        siguen valiendo: borrar una fila romperia las dos cosas);
      - sus tareas, su nombre, su billetera y su saldo quedan como estaban;
      - con `activo=0` el token deja de valer al instante (`exigir_cliente`
        exige `activo=1`), asi que el cliente no puede pedir ni gastar;
      - es reversible: se puede volver a dar de alta con la misma billetera.

    Hay que decir a quien: `ids`, `nombre_contiene` o `todos=True`. Un cliente
    con tareas en cola o ejecutandose se SALTA salvo `forzar`: su prepago
    seguiria retenido en escrow y no podria recoger el resultado.
    """
    if not (b.ids or b.nombre_contiene.strip() or b.todos):
        raise HTTPException(400, "di a quien dar de baja: ids, nombre_contiene o todos")
    asientos_antes = con.execute("SELECT COUNT(*) n, COALESCE(MAX(id),0) m FROM ledger").fetchone()
    supply_antes = supply_emitido()
    quieren = {str(i).strip() for i in (b.ids or []) if str(i).strip()}
    pat = b.nombre_contiene.strip().lower()
    filas = con.execute("SELECT id,nombre,direccion,tareas,gastado,creado FROM clientes "
                        "WHERE activo=1 ORDER BY creado").fetchall()
    bajas, saltados = [], []
    for f in filas:
        if not b.todos:
            if quieren:
                if f["id"] not in quieren:
                    continue
            elif pat:
                if pat not in (f["nombre"] or "").lower():
                    continue
        d = (f["direccion"] or "").strip().upper()
        en_curso, saldo, asientos = 0, 0.0, 0
        if d:
            en_curso = int(con.execute(
                "SELECT COUNT(*) n FROM tareas WHERE cliente=? AND estado IN ('queued','ejecutando')",
                (d,)).fetchone()["n"] or 0)
            s = con.execute("SELECT total FROM saldos_cuenta WHERE cuenta=?", (d,)).fetchone()
            saldo = float(s["total"] if s else 0.0)
            asientos = int(con.execute("SELECT COUNT(*) n FROM ledger WHERE nodo_id=?",
                                       (d,)).fetchone()["n"] or 0)
        info = {"id": f["id"], "nombre": f["nombre"], "direccion": d,
                "tareas": int(f["tareas"] or 0), "gastado": round(float(f["gastado"] or 0.0), 12),
                "saldo": round(saldo, 12), "asientos": asientos, "en_curso": en_curso}
        if en_curso and not b.forzar:
            info["motivo"] = "tareas en curso: usa forzar=True o espera a que acaben"
            saltados.append(info)
            continue
        bajas.append(info)
    if b.seco:
        return {"seco": True, "bajadas": [], "bajarian": bajas, "saltados": saltados,
                "asientos": int(asientos_antes["n"]), "supply": supply_antes,
                "aviso": "nada escrito: repite con seco=false para darlos de baja"}
    with _escritura():
        for x in bajas:
            con.execute("UPDATE clientes SET activo=0 WHERE id=? AND activo=1", (x["id"],))
    asientos_despues = con.execute("SELECT COUNT(*) n, COALESCE(MAX(id),0) m FROM ledger").fetchone()
    return {"seco": False, "bajadas": bajas, "saltados": saltados,
            "asientos_antes": int(asientos_antes["n"]),
            "asientos_despues": int(asientos_despues["n"]),
            "asientos_borrados": int(asientos_antes["n"]) - int(asientos_despues["n"]),
            "supply_antes": supply_antes, "supply_despues": supply_emitido(),
            # Los dos numeros de arriba se leen con el minero trabajando: suben
            # por la emision normal, no por la baja. Lo que importa es que NUNCA
            # bajen (`asientos_borrados` negativo = el ledger crecio).
            "nota": "baja logica: el ledger, las tareas y los saldos quedan intactos"}


# ============================================================
#  INGRESOS REALES EN EUROS (25-10-2026)
#
#  El ledger lleva KIA. El servicio de transcripcion cobra en EUROS. Mezclar
#  las dos cosas (convertir el euro a KIA y abonarlo) seria emitir dinero por
#  vender un servicio: aqui NO se crea ni un KIA.
#
#  Lo que se hace, y basta para lo que importa:
#    - una fila en `ingresos` con el servicio, el trabajo, los minutos y el
#      precio en euros;
#    - un asiento de 0 KIA en el ledger (cuenta INGRESOS_EUR) cuyo motivo lleva
#      el importe y el trabajo: queda en la cadena de hashes y bajo los sellos
#      igual que cualquier pago;
#    - el hash de ese asiento se guarda en la fila, asi que el ingreso y el
#      asiento no se pueden separar sin que se note.
#
#  Asi "el primer euro facturado" es un hecho verificable: sale en /ingresos,
#  en /stats y en /ledger/verificar, sin creerse a nadie.
# ============================================================

class IngresoIn(BaseModel):
    precio_eur: float
    servicio: str = "transcripcion"
    cliente: str = ""
    trabajo: str = ""
    minutos: float = 0.0
    detalle: dict | None = None


def _huella_ingreso(servicio: str, cliente: str, trabajo: str, precio_eur: float) -> str:
    """Identidad del cobro: mismo servicio + trabajo + importe = el mismo euro."""
    base = (f"{(servicio or '').strip().lower()}|{(cliente or '').strip().lower()}|"
            f"{(trabajo or '').strip()}|{round(float(precio_eur), 2):.2f}")
    return hashlib.sha256(base.encode()).hexdigest()[:32]


def _resumen_ingresos() -> dict:
    """Totales de dinero real cobrado. Barato: la tabla es diminuta."""
    try:
        f = con.execute("SELECT COUNT(*) n, COALESCE(SUM(precio_eur),0) t, "
                        "COALESCE(MIN(fecha),'') primera FROM ingresos").fetchone()
        return {"n": int(f["n"] or 0), "total_eur": round(float(f["t"] or 0.0), 2),
                "primera": (f["primera"] or None)}
    except Exception:
        return {"n": 0, "total_eur": 0.0, "primera": None}


@app.post("/admin/ingresos")
def admin_anotar_ingreso(i: IngresoIn, _ok: None = Depends(exigir_admin)):
    """Anota un trabajo COBRADO fuera de la red (transcripcion, etc.).

    No emite KIA: el asiento va a 0. Lo que se guarda es la prueba de que
    alguien pago dinero real por trabajo de esta casa.
    """
    try:
        precio = round(float(i.precio_eur), 2)
        minutos = round(float(i.minutos or 0.0), 2)
    except (TypeError, ValueError):
        raise HTTPException(400, "precio_eur y minutos tienen que ser numeros")
    if not math.isfinite(precio) or precio <= 0:
        raise HTTPException(400, "precio_eur tiene que ser mayor que 0")
    if not math.isfinite(minutos) or minutos < 0:
        raise HTTPException(400, "minutos invalidos")
    if precio > 100_000:
        raise HTTPException(400, "precio_eur fuera de rango (max 100000)")
    servicio = (i.servicio or "transcripcion").strip()[:40]
    cliente = (i.cliente or "").strip()[:80]
    trabajo = (i.trabajo or "").strip()[:200]
    huella = _huella_ingreso(servicio, cliente, trabajo, precio)
    with _escritura():
        ya = con.execute("SELECT id FROM ingresos WHERE huella=?", (huella,)).fetchone()
        if ya is not None:
            # Contar dos veces el mismo euro seria mentir justo en la cifra que
            # se publica como prueba de demanda.
            raise HTTPException(409, f"ese trabajo ya esta anotado (ingreso #{ya['id']})")
        cur = con.execute(
            "INSERT INTO ingresos(huella,servicio,cliente,trabajo,minutos,precio_eur,"
            "detalle,fecha) VALUES(?,?,?,?,?,?,?,?)",
            (huella, servicio, cliente, trabajo, minutos, precio,
             json.dumps(i.detalle or {}, ensure_ascii=False)[:4000],
             time.ctime()))
        iid = int(cur.lastrowid)
        motivo = (f"ingreso real {precio:.2f} EUR · {servicio} · "
                  f"{minutos:.1f} min · {trabajo or '(sin nombre)'}")
        h = _asiento(f"ingreso:{iid}", CUENTA_INGRESOS, 0.0, motivo[:180])
        con.execute("UPDATE ingresos SET asiento_id=?, asiento_hash=? WHERE id=?",
                    (iid, h, iid))
    resumen = _resumen_ingresos()
    return {"id": iid, "precio_eur": precio, "minutos": minutos,
            "asiento_hash": h, "cuenta": CUENTA_INGRESOS,
            "kia_emitidos": 0.0, "total_eur": resumen["total_eur"],
            "ingresos": resumen["n"],
            "nota": "anotado sin emitir KIA: el euro no se convierte en moneda"}


@app.get("/ingresos")
def ingresos_publico():
    """Prueba de demanda, publica: cuanto se ha cobrado y cuando.

    Sin nombre del cliente ni del trabajo: el importe y la fecha bastan para
    verificar que el dinero existe, y el cliente no tiene por que salir en una
    web publica. El detalle con nombres esta en /admin/ingresos.
    """
    filas = con.execute("SELECT servicio,minutos,precio_eur,fecha,asiento_hash "
                        "FROM ingresos ORDER BY id DESC LIMIT 50").fetchall()
    d = _resumen_ingresos()
    d["ultimos"] = [dict(f) for f in filas]
    d["moneda"] = "EUR"
    d["como_se_verifica"] = "cada fila apunta al asiento de 0 KIA del ledger "
    d["ledger"] = "/ledger/verificar"
    return d


@app.get("/admin/ingresos")
def admin_lista_ingresos(_ok: None = Depends(exigir_admin)):
    """Lista completa, con cliente y trabajo (no se publica)."""
    filas = con.execute("SELECT id,servicio,cliente,trabajo,minutos,precio_eur,"
                        "fecha,asiento_id,asiento_hash FROM ingresos "
                        "ORDER BY id DESC LIMIT 200").fetchall()
    d = _resumen_ingresos()
    d["ingresos"] = [dict(f) for f in filas]
    return d


@app.get("/cliente/precio")
def cliente_precio(tipo: str, params: str = "", _c=Depends(exigir_cliente)):
    """Cuanto costaria una tarea de este tipo (para saber que tope firmar).

    Sin `params` da el de una tarea minima del tipo pedido, que es el suelo.
    Con `params` (JSON en la query) da el coste EXACTO de esa tarea, que es lo
    que hace falta para depositar de mas y ver volver el sobrante sin firmar a
    ciegas (25-10-2026).
    """
    if tipo not in TIPOS_CLIENTE:
        raise HTTPException(400, "tipo de tarea no soportado")
    suelo = _coste_tarea_cliente(tipo, {} if tipo != "checksum" else {"n": 10})
    salida = {"tipo": tipo, "coste_minimo": suelo,
              "moneda": "KIA", "nota": "el coste real depende de los params; "
                                        "firma un tope igual o mayor"}
    if params.strip():
        try:
            p = validar_params(tipo, json.loads(params))
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(400, "params no es un JSON valido")
        salida["params"] = p
        salida["coste"] = _coste_tarea_cliente(tipo, p)
    return salida


@app.get("/cliente/saldo")
def cliente_saldo(c=Depends(exigir_cliente)):
    """Saldo disponible y cuanto tiene retenido en tareas en curso."""
    d = (c["direccion"] or "").strip().upper()
    if not d:
        raise HTTPException(400, "este cliente no tiene billetera asociada")
    fila = con.execute("SELECT total FROM saldos_cuenta WHERE cuenta=?", (d,)).fetchone()
    saldo = float((fila["total"] if fila else 0.0) or 0.0)
    ret = con.execute("SELECT COALESCE(SUM(prepago),0) s FROM tareas "
                      "WHERE cliente=? AND estado IN ('queued','ejecutando')", (d,)).fetchone()
    return {"direccion": d, "saldo": round(saldo, 12),
            "retenido": round(float(ret["s"] or 0.0), 12),
            "tareas": int(c["tareas"] or 0)}


@app.post("/cliente/tareas")
def cliente_crear_tarea(t: ClienteTareaIn, c=Depends(exigir_cliente)):
    """El cliente pide trabajo: cobra por adelantado y encola.

    Orden deliberado:
      1) validar params (barato, no toca la base)
      2) calcular el coste con la tarifa de la red
      3) comprobar la FIRMA de la billetera del cliente
      4) comprobar nonce, cupo de cola y saldo
      5) en una sola transaccion: retener el dinero y encolar

    La firma va sobre `cliente:<nonce>:<tipo>:<sha256(params)>:<tope>`; incluye
    el tope para que nadie pueda cambiar el importe autorizado despues.
    """
    if t.tipo not in TIPOS_CLIENTE:
        # Antes de nada: un tipo que ningun minero sabe ejecutar no se cobra.
        raise HTTPException(400, f"tipo de tarea no soportado: {t.tipo}")
    dir_cliente = (c["direccion"] or "").strip().upper()
    if not direccion_valida(dir_cliente):
        raise HTTPException(400, "el cliente no tiene billetera: pide al Core que la asocie")
    params = validar_params(t.tipo, dict(t.params or {}))
    coste = _coste_tarea_cliente(t.tipo, params)
    tope = float(t.tope if t.tope and t.tope > 0 else CLIENTE_TOPE_DEFECTO)
    if not math.isfinite(tope) or tope <= 0:
        raise HTTPException(400, "tope invalido")
    if coste > tope:
        raise HTTPException(402, f"tope insuficiente: esta tarea cuesta {coste} KIA y "
                                 f"autorizaste {tope}. Sube el tope y firma de nuevo")
    if not t.nonce or len(t.nonce) > 64:
        raise HTTPException(400, "nonce invalido")
    # DEPOSITO (25-10-2026): se puede dejar retenido mas que el coste. Se paga
    # la tarifa entera al minero y el sobrante vuelve al cliente. Nunca menos
    # del coste: un deposito corto haria que el minero cobrase de menos.
    deposito = float(t.deposito or 0.0)
    if not math.isfinite(deposito) or deposito < 0:
        raise HTTPException(400, "deposito invalido")
    if 0 < deposito < coste:
        raise HTTPException(402, f"deposito insuficiente: este trabajo cuesta {coste} KIA "
                                 f"y depositaste {deposito}. El minero cobra el trabajo "
                                 f"entero; deposita al menos el coste")
    retenido = round(max(coste, deposito), 12)
    # 3) FIRMA del cliente sobre el tope y los params (y el deposito, si lo hay)
    pub = (t.clave_publica or "").strip().lower()
    if not pubkey_valida(pub) or direccion_de_pubkey(pub) != dir_cliente:
        raise HTTPException(401, "la clave_publica no corresponde a la billetera del cliente")
    if not t.firma:
        raise HTTPException(401, "falta firma del cliente")
    hash_params = hashlib.sha256(json.dumps(params, sort_keys=True).encode()).hexdigest()
    mensaje = f"cliente:{t.nonce}:{t.tipo}:{hash_params}:{tope}"
    if deposito > 0:
        mensaje += f":{retenido}"
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(pub)).verify(
            bytes.fromhex(t.firma), mensaje.encode())
    except Exception:
        raise HTTPException(401, "firma del cliente invalida")
    if LEDGER_ROTO:
        raise HTTPException(503, "ledger alterado: no se aceptan encargos nuevos")
    # 5) transaccion unica: retener y encolar
    with _pago_lock:
        try:
            con.execute("BEGIN IMMEDIATE")
        except Exception:
            raise HTTPException(503, "Core ocupado, reintentar")
        try:
            # nonce ya usado por este cliente (firma reutilizada)
            if con.execute("SELECT 1 FROM tareas WHERE cliente=? AND nonce_cliente=?",
                           (dir_cliente, t.nonce)).fetchone():
                _rollback_seguro()
                raise HTTPException(409, "nonce ya usado por este cliente")
            en_cola = con.execute("SELECT COUNT(*) n FROM tareas WHERE cliente=? AND "
                                  "estado IN ('queued','ejecutando')",
                                  (dir_cliente,)).fetchone()["n"]
            if int(en_cola) >= CLIENTE_MAX_EN_COLA:
                _rollback_seguro()
                raise HTTPException(429, f"ya tienes {en_cola} tareas en curso "
                                         f"(maximo {CLIENTE_MAX_EN_COLA}); espera a que acaben")
            saldo = con.execute("SELECT COALESCE(SUM(kuki),0) s FROM ledger WHERE nodo_id=?",
                                (dir_cliente,)).fetchone()["s"]
            if float(saldo or 0.0) < retenido:
                _rollback_seguro()
                raise HTTPException(402, f"saldo insuficiente: necesitas {retenido} KIA y "
                                         f"tienes {round(float(saldo or 0.0), 6)}")
            tid = uuid.uuid4().hex[:12]
            con.execute("INSERT INTO tareas(id,tipo,params,estado,creado,cola,campana,"
                        "prioridad,vram_min,cliente,prepago,nonce_cliente,precio_cliente) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (tid, t.tipo, json.dumps(params), "queued", time.ctime(),
                         "B", "cliente", 0.0, int(t.vram_min or 0),
                         dir_cliente, retenido, t.nonce, coste))
            _retener_prepago(tid, dir_cliente, retenido)
            con.execute("UPDATE clientes SET tareas=tareas+1, gastado=gastado+? WHERE id=?",
                        (coste, c["id"]))
            con.commit()
        except HTTPException:
            try:
                _rollback_seguro()
            except Exception:
                pass
            raise
        except Exception as e:
            try:
                _rollback_seguro()
            except Exception:
                pass
            raise HTTPException(500, f"no se pudo encolar: {type(e).__name__}")
    return {"tarea_id": tid, "estado": "queued", "tipo": t.tipo,
            "coste": coste, "deposito": retenido,
            "sobrante_previsto": round(retenido - coste, 12), "params": params,
            "recoger_en": f"/cliente/tareas/{tid}"}


@app.get("/cliente/tareas/{tid}")
def cliente_ver_tarea(tid: str, c=Depends(exigir_cliente)):
    """Estado y resultado de una tarea del cliente. Solo de las suyas."""
    dir_cliente = (c["direccion"] or "").strip().upper()
    t = con.execute("SELECT * FROM tareas WHERE id=?", (tid,)).fetchone()
    if t is None:
        raise HTTPException(404, "tarea no encontrada")
    if (t["cliente"] or "").strip().upper() != dir_cliente:
        # No se distingue "no existe" de "no es tuya": no se filtra informacion.
        raise HTTPException(404, "tarea no encontrada")
    estado = t["estado"]
    salida = {"tarea_id": tid, "estado": estado, "tipo": t["tipo"],
              "creado": t["creado"], "coste": float(t["precio_cliente"] or 0.0),
              "prepago": float(t["prepago"] or 0.0),
              "pagado": float(t["pagado"] or 0.0)}
    if estado == "ok":
        salida["resultado"] = t["metrica"]
        salida["hash"] = t["hash"]
        salida["verificado"] = bool(t["verificado"])
        salida["terminado"] = t["fin"]
    elif estado == "fallo":
        salida["error"] = "la tarea no se pudo verificar"
    else:
        salida["espera"] = "todavia en cola o ejecutandose"
    return salida


# el coordinador corre: uvicorn kukia_core:app --port 8300
