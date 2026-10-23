"""Registro de pasos cientificos: como se calcula y como se comprueba.

Cada paso trae SIEMPRE dos caminos distintos:

    motor 0  ->  calcula el resultado
    motor 1+ ->  lo comprueban por otra via (otra ecuacion, otro algoritmo u otra
                 aritmetica), sin mirar el resultado del motor 0.

Esa separacion es lo que hace que un numero valga algo: si los dos caminos
discrepan, el resultado se marca `refutado` y se guarda el fallo. Si coinciden,
como mucho sube a `validado` (nunca a `confirmado` con una sola comprobacion).

`independencia` dice que tan fuerte es la comprobacion, y no se disimula:
    fisica      otra ley o integracion numerica del mismo fenomeno (la mejor)
    algoritmo   misma fisica, distinto algoritmo de resolucion
    aritmetica  misma formula, aritmetica exacta / otra ruta de constantes
    simbolico   resolucion exacta con sympy frente al calculo numerico
    procedimiento  se comprueba el METODO (p. ej. que el intervalo cubra de
                 verdad el nivel pedido) en vez de rehacer la misma cuenta

Los dominios son etiquetas abiertas: `meta.dominios` no tiene lista cerrada,
cualquier rama nueva entra registrando sus pasos aqui.
"""
from __future__ import annotations

import hashlib
import math
from fractions import Fraction

PASOS: dict[str, dict] = {}


def registro(nombre: str, dominio: str, descripcion: str, unidad: str,
             parametros: list[str], motores: list[tuple], tolerancia: float = 1e-6,
             independencia: str = "fisica") -> None:
    """Da de alta un paso con sus dos (o mas) caminos."""
    if len(motores) < 2:
        raise ValueError(f"el paso {nombre} necesita al menos un motor de verificacion")
    PASOS[nombre] = {
        "paso": nombre,
        "dominio": dominio,
        "descripcion": descripcion,
        "unidad": unidad,
        "parametros": parametros,
        "motores": motores,          # [(nombre, fn), ...]: el 0 calcula, el resto comprueba
        "tolerancia": tolerancia,
        "independencia": independencia,
    }


def existe(nombre: str) -> bool:
    return nombre in PASOS


def ficha(nombre: str) -> dict:
    return PASOS[nombre]


def motores(nombre: str) -> list[str]:
    return [m[0] for m in PASOS[nombre]["motores"]]


# ---------------------------------------------------------------------------
#  Fisica
# ---------------------------------------------------------------------------
def _mrua(p):
    return p["v0"] * p["t"] + 0.5 * p["a"] * p["t"] ** 2


def _mrua_edo(p):
    import numpy as np
    from scipy.integrate import solve_ivp

    def f(_t, y):
        return [y[1], p["a"]]

    s = solve_ivp(f, (0.0, p["t"]), [0.0, p["v0"]], rtol=1e-11, atol=1e-12,
                  dense_output=False)
    return float(s.y[0][-1])


registro("cinematica.mrua", "fisica",
         "Posicion de un movil con aceleracion constante", "m",
         ["v0", "a", "t"],
         [("directo", _mrua), ("edo", _mrua_edo)], 1e-6, "fisica")


def _decaimiento(p):
    return p["N0"] * math.exp(-p["lam"] * p["t"])


def _decaimiento_edo(p):
    from scipy.integrate import solve_ivp

    s = solve_ivp(lambda _t, y: [-p["lam"] * y[0]], (0.0, p["t"]),
                  [p["N0"]], rtol=1e-11, atol=1e-12)
    return float(s.y[0][-1])


registro("radiactividad.decaimiento", "fisica",
         "Nucleos que quedan tras un tiempo t (ley exponencial)", "nucleos",
         ["N0", "lam", "t"],
         [("directo", _decaimiento), ("edo", _decaimiento_edo)], 1e-6, "fisica")


def _kepler(p):
    G = 6.67430e-11
    return 2.0 * math.pi * math.sqrt(p["a"] ** 3 / (G * p["masa"]))


def _kepler_orbita(p):
    """Integra la orbita entera y mide el periodo entre dos pasos por el periastro."""
    import numpy as np
    from scipy.integrate import solve_ivp

    G = 6.67430e-11
    mu = G * p["masa"]
    a = p["a"]
    v = math.sqrt(mu / a)
    y0 = [a, 0.0, 0.0, v]

    def f(_t, y):
        r = math.hypot(y[0], y[1])
        return [y[2], y[3], -mu * y[0] / r ** 3, -mu * y[1] / r ** 3]

    def cruza(_t, y):
        return y[1]          # y = 0 (linea del periastro)

    cruza.terminal = False
    t_estimado = 2.0 * math.pi * math.sqrt(a ** 3 / mu)
    s = solve_ivp(f, (0.0, t_estimado * 1.5), y0, events=cruza,
                  rtol=1e-10, atol=1e-6, max_step=t_estimado / 200.0)
    # En una orbita circular centrada en el origen y=0 se cruza dos veces por
    # vuelta: el periodo es el DOBLE de lo que tarda entre dos cruces.
    if not s.t_events or len(s.t_events[0]) < 2:
        raise RuntimeError("la orbita no dio dos cruces en el tiempo maximo")
    t = s.t_events[0]
    return float(2.0 * (t[1] - t[0]))


registro("astronomia.periodo_orbital", "astronomia",
         "Periodo de una orbita circular (tercera ley de Kepler)", "s",
         ["a", "masa"],
         [("kepler", _kepler), ("integracion_orbita", _kepler_orbita)], 1e-4, "fisica")


def _logistica(p):
    K, r, N0, t = p["K"], p["r"], p["N0"], p["t"]
    return K / (1.0 + ((K - N0) / N0) * math.exp(-r * t))


def _logistica_edo(p):
    from scipy.integrate import solve_ivp

    def f(_t, y):
        return [p["r"] * y[0] * (1.0 - y[0] / p["K"])]

    s = solve_ivp(f, (0.0, p["t"]), [p["N0"]], rtol=1e-11, atol=1e-12)
    return float(s.y[0][-1])


registro("biologia.crecimiento_logistico", "biologia",
         "Poblacion con recurso limitado (logistica)", "individuos",
         ["K", "r", "N0", "t"],
         [("directo", _logistica), ("edo", _logistica_edo)], 1e-6, "fisica")


def _gas(p):
    R = 8.31446261815324
    return p["n"] * R * p["T"] / p["V"]


def _gas_exacto(p):
    # Misma ley, aritmetica exacta: R y los datos como fracciones, sin redondeo.
    R = Fraction(831446261815324, 10 ** 14)
    return float(Fraction(str(p["n"])) * R * Fraction(str(p["T"]))
                 / Fraction(str(p["V"])))


registro("termodinamica.gas_ideal", "fisica",
         "Presion de un gas ideal", "Pa",
         ["n", "T", "V"],
         [("directo", _gas), ("aritmetica_exacta", _gas_exacto)], 1e-11, "aritmetica")


def _snell(p):
    n1, th1, n2 = p["n1"], math.radians(p["th1"]), p["n2"]
    s = n1 * math.sin(th1) / n2
    if abs(s) > 1:
        raise ValueError("reflexion total: no hay angulo refractado")
    return math.degrees(math.asin(s))


def _snell_fase(p):
    """Emparejamiento de fase en la superficie: k1 sin(t1) = k2 sin(t2).

    Se plantea como lo que es fisicamente (la componente tangencial del vector de
    onda no cambia al cruzar) y se resuelve por busqueda de raiz, no despejando
    el arcoseno. La independencia es de ALGORITMO, no de ley: la fisica es la
    misma, y la ficha del paso lo dice sin adornos.
    """
    from scipy.optimize import brentq

    k1 = 2.0 * math.pi * p["n1"]
    k2 = 2.0 * math.pi * p["n2"]
    tangencial = k1 * math.sin(math.radians(p["th1"]))
    if abs(tangencial) >= k2:
        raise ValueError("reflexion total: no hay onda transmitida")

    t2 = brentq(lambda t: k2 * math.sin(t) - tangencial,
                -math.pi / 2 + 1e-12, math.pi / 2 - 1e-12, xtol=1e-16, rtol=1e-15)
    return math.degrees(t2)


registro("optica.snell", "fisica",
         "Angulo refractado al pasar de un medio a otro", "grados",
         ["n1", "th1", "n2"],
         [("ley_snell", _snell), ("fase_k", _snell_fase)], 1e-9, "algoritmo")


# ---------------------------------------------------------------------------
#  Quimica, biologia, estadistica
# ---------------------------------------------------------------------------
def _ph(p):
    Ka, c = p["Ka"], p["c"]
    x = (-Ka + math.sqrt(Ka * Ka + 4 * Ka * c)) / 2.0
    return -math.log10(x)


def _ph_brentq(p):
    from scipy.optimize import brentq

    Ka, c = p["Ka"], p["c"]

    def equilibrio(x):            # x^2/(c-x) = Ka
        return x * x / (c - x) - Ka

    x = brentq(equilibrio, 1e-15, c * (1 - 1e-12), xtol=1e-18, rtol=1e-14)
    return -math.log10(x)


registro("quimica.ph_acido_debil", "quimica",
         "pH de un acido debil", "pH",
         ["Ka", "c"],
         [("cuadratica", _ph), ("brentq", _ph_brentq)], 1e-9, "algoritmo")


def _hardy(p):
    q = 1.0 - p["p"]
    return 2.0 * p["p"] * q


def _hardy_simbolico(p):
    import sympy as sp

    P, Q = sp.symbols("P Q")
    expr = sp.expand((P + Q) ** 2)
    het = sp.expand(expr - P ** 2 - Q ** 2)
    return float(het.subs({P: sp.Rational(str(p["p"])),
                           Q: 1 - sp.Rational(str(p["p"]))}))


registro("genetica.hardy_weinberg", "biologia",
         "Frecuencia de heterocigotos en equilibrio", "fraccion",
         ["p"],
         [("formula", _hardy), ("simbolico", _hardy_simbolico)], 1e-12, "simbolico")


def _ic_media(p):
    import numpy as np
    from scipy import stats

    d = np.asarray(p["datos"], dtype=float)
    n = d.size
    s = d.std(ddof=1)
    t = stats.t.ppf(1 - (1 - p["nivel"]) / 2, n - 1)
    return float(t * s / math.sqrt(n))


def _ic_media_cobertura(p):
    """Comprueba el PROCEDIMIENTO: simula muestras y mide cuantas veces cubre."""
    import numpy as np
    from scipy import stats

    rng = np.random.default_rng(12345)
    d = np.asarray(p["datos"], dtype=float)
    mu, sigma = float(d.mean()), float(d.std(ddof=1))
    n = d.size
    reps = 4000
    dentro = 0
    for _ in range(reps):
        m = rng.normal(mu, sigma, n)
        s = m.std(ddof=1)
        t = stats.t.ppf(1 - (1 - p["nivel"]) / 2, n - 1)
        h = t * s / math.sqrt(n)
        if abs(m.mean() - mu) <= h:
            dentro += 1
    cobertura = dentro / reps
    if abs(cobertura - p["nivel"]) > 0.02:
        raise RuntimeError(f"la cobertura simulada ({cobertura:.3f}) no es {p['nivel']}")
    # El numero que se devuelve para comparar es el mismo semiancho del metodo.
    s = d.std(ddof=1)
    t = stats.t.ppf(1 - (1 - p["nivel"]) / 2, n - 1)
    return float(t * s / math.sqrt(n))


registro("estadistica.ic_media", "estadistica",
         "Semiancho del intervalo de confianza de la media", "unidad de los datos",
         ["datos", "nivel"],
         [("t_student", _ic_media), ("cobertura_simulada", _ic_media_cobertura)],
         1e-9, "procedimiento")


# ---------------------------------------------------------------------------
#  Ingenieria, economia, IA, matematicas
# ---------------------------------------------------------------------------
def _interes(p):
    return p["P"] * (1 + p["r"] / p["nper"]) ** (p["nper"] * p["t"])


def _interes_bucle(p):
    saldo = p["P"]
    for _ in range(int(round(p["nper"] * p["t"]))):
        saldo *= (1 + p["r"] / p["nper"])
    return saldo


registro("economia.interes_compuesto", "economia",
         "Capital final con interes compuesto", "dinero",
         ["P", "r", "nper", "t"],
         [("formula", _interes), ("bucle", _interes_bucle)], 1e-9, "algoritmo")


def _regresion(p):
    import numpy as np

    x = np.asarray(p["x"], dtype=float)
    y = np.asarray(p["y"], dtype=float)
    A = np.column_stack([x, np.ones_like(x)])
    m, b = np.linalg.solve(A.T @ A, A.T @ y)
    return float(m)


def _regresion_svd(p):
    import numpy as np

    x = np.asarray(p["x"], dtype=float)
    y = np.asarray(p["y"], dtype=float)
    A = np.column_stack([x, np.ones_like(x)])
    sol, *_ = np.linalg.lstsq(A, y, rcond=None)
    return float(sol[0])


registro("ia.regresion_lineal", "inteligencia_artificial",
         "Pendiente de la recta de minimos cuadrados", "y/x",
         ["x", "y"],
         [("ecuaciones_normales", _regresion), ("svd", _regresion_svd)], 1e-9, "algoritmo")


def _integral(p):
    import sympy as sp

    x = sp.Symbol("x")
    expr = sp.sympify(p["expr"])
    return float(sp.integrate(expr, (x, p["a"], p["b"])).evalf(30))


def _integral_cuad(p):
    import numpy as np
    import sympy as sp
    from scipy.integrate import quad

    x = sp.Symbol("x")
    f = sp.lambdify(x, sp.sympify(p["expr"]), "numpy")
    v, _err = quad(lambda t: float(f(t)), p["a"], p["b"], epsabs=1e-12, epsrel=1e-12)
    return float(v)


registro("matematicas.integral_definida", "matematicas",
         "Integral definida de una funcion", "resultado",
         ["expr", "a", "b"],
         [("simbolico", _integral), ("cuadratura", _integral_cuad)], 1e-9, "simbolico")


def _dilatacion(p):
    return p["L"] * p["alpha"] * p["dT"]


def _dilatacion_exacta(p):
    L = Fraction(str(p["L"]))
    a = Fraction(str(p["alpha"]))
    dT = Fraction(str(p["dT"]))
    return float(L * a * dT)


registro("materiales.dilatacion", "materiales",
         "Alargamiento de una barra al calentarse", "m",
         ["L", "alpha", "dT"],
         [("formula", _dilatacion), ("fraccion_exacta", _dilatacion_exacta)],
         1e-14, "aritmetica")


def huella_pasos() -> str:
    """Hash del registro: identifica con que version de pasos se calculo algo."""
    partes = []
    for nombre in sorted(PASOS):
        for m in PASOS[nombre]["motores"]:
            partes.append(f"{nombre}:{m[0]}:{m[1].__code__.co_code!r}")
    return hashlib.sha256("|".join(partes).encode()).hexdigest()[:16]
