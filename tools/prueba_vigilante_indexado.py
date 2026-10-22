#!/usr/bin/env python3
"""Pruebas de la mirada diaria a los buscadores: que diga la verdad y avise una vez.

Lo que se prueba, y por que importa cada cosa:

  1. Que un motor que NO contesta se quede en "sin comprobar" y NUNCA se
     convierta en "no salimos". Ese error es el caro: si el vigilante cree que
     no estamos indexados, deja de empujar avisos y nadie se entera.
  2. Que el aviso salga UNA vez, el dia que la pagina aparece, y no cada manana.
  3. Que se mire una vez al dia y no en cada vuelta de los 10 minutos.
  4. Que la linea escrita en ESTADO_BUSCADORES.txt diga la verdad.
  5. Contra la red de verdad: el control (es.wikipedia.org) TIENE que salir y
     KUKIA todavia no. Si el control no sale, es que nos estan bloqueando: se
     dice y NO se cuenta como fallo, porque no es culpa del codigo.

    python tools/prueba_vigilante_indexado.py
    python tools/prueba_vigilante_indexado.py --sin-red     solo la logica

Todo pasa sobre ficheros temporales: no toca logs/vigilante_web.json ni el
ESTADO_BUSCADORES.txt de verdad, y no manda nada a IndexNow.
"""
import argparse
import json
import shutil
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "tools"))

import vigilante_web as V  # noqa: E402

HOST = "site-1ea296595daa4ba7935f1574ab9a5a13.freebuff.page"
BASE = "https://" + HOST
RESULTADOS: list[tuple[str, bool, str]] = []
SALTOS: list[tuple[str, str]] = []


# --------------------------------------------------------------------------
#  Utilidades
# --------------------------------------------------------------------------
def comprobar(nombre: str, condicion: bool, detalle: str = "") -> bool:
    RESULTADOS.append((nombre, bool(condicion), detalle))
    print(f"  [{'OK  ' if condicion else 'FALLO'}] {nombre}"
          + ("" if condicion or not detalle else f"  -> {detalle}"))
    return bool(condicion)


def salto(nombre: str, detalle: str) -> None:
    """No se pudo comprobar (nos bloquean o no hay red). No es un fallo del codigo."""
    SALTOS.append((nombre, detalle))
    print(f"  [SALTO] {nombre}  -> {detalle}")


class Motor:
    """Sustituye a la consulta real: devuelve lo que se le diga y apunta llamadas."""

    def __init__(self, respuestas: dict):
        self.respuestas = dict(respuestas)
        self.llamadas: list[str] = []

    def __call__(self, motor: str, host: str):
        self.llamadas.append(motor)
        r = self.respuestas.get(motor, ("sin_comprobar", "sin dato", ""))
        if isinstance(r, Exception):
            raise r
        return r


class Avisos:
    def __init__(self):
        self.lista: list[tuple[str, str]] = []

    def __call__(self, titulo: str, texto: str):
        self.lista.append((titulo, texto))


def dia(offset: int = 0) -> str:
    return (datetime.now() + timedelta(days=offset)).strftime("%Y-%m-%d")


# --------------------------------------------------------------------------
#  Pruebas sin red
# --------------------------------------------------------------------------
def pruebas_de_sitio() -> None:
    print("\n== ¿es del mismo sitio? ==")
    comprobar("el mismo host cuenta", V._del_mismo_sitio(BASE + "/minar", HOST))
    comprobar("un subdominio cuenta",
              V._del_mismo_sitio("https://es." + HOST + "/x", HOST))
    comprobar("otro dominio NO cuenta",
              not V._del_mismo_sitio("https://es.wikipedia.org/wiki/X", HOST))
    comprobar("un parecido NO cuenta (evil-host.ejemplo)",
              not V._del_mismo_sitio("https://" + HOST + ".malo.test/", HOST))
    comprobar("vacio no cuenta", not V._del_mismo_sitio("", HOST))


def pruebas_de_linea() -> None:
    print("\n== la linea del ESTADO_BUSCADORES.txt ==")
    comprobar("sin datos no escribe linea", V.linea_indexado({}) == "")

    idx = {"dia": "2026-10-22", "host": HOST, "google": "no", "bing": "no"}
    linea = V.linea_indexado(idx)
    comprobar("'no' se escribe como 'todavia no'", "todavia no" in linea, linea)
    comprobar("'no' nunca se escribe como SALE", "SALE" not in linea, linea)

    idx["bing"] = "sale"
    idx["primera_bing"] = "2026-10-23"
    linea = V.linea_indexado(idx)
    comprobar("'sale' dice SALE y desde cuando",
              "Bing SALE desde el 2026-10-23" in linea, linea)

    idx["google"] = "sin_comprobar"
    linea = V.linea_indexado(idx)
    comprobar("'sin_comprobar' se dice tal cual", "Google sin comprobar" in linea, linea)


def pruebas_de_logica() -> None:
    print("\n== mira y no se cree nada de mas ==")

    # 1. No sale en ninguno: ni aviso ni conclusion equivocada.
    m = Motor({"google": ("no", "", ""), "bing": ("no", "", "")})
    a = Avisos()
    idx = V.revisa_indexado(BASE, {}, forzar=True, consultar=m, avisar=a)
    comprobar("pregunta a los dos motores", sorted(m.llamadas) == ["bing", "google"])
    comprobar("los dos quedan en 'no'", idx.get("google") == "no" and idx.get("bing") == "no")
    comprobar("no avisa si no sale", not a.lista, str(a.lista))
    comprobar("'no' no tiene fecha de primera vez",
              not idx.get("primera_google") and not idx.get("primera_bing"))

    # 2. Sale en Bing: un aviso, con su fecha.
    m = Motor({"google": ("no", "", ""),
               "bing": ("sale", "", f"https://{HOST}/minar")})
    a = Avisos()
    idx = V.revisa_indexado(BASE, {}, forzar=True, consultar=m, avisar=a)
    comprobar("avisa UNA vez", len(a.lista) == 1, str(a.lista))
    comprobar("el aviso nombra a Bing", "Bing" in a.lista[0][0], str(a.lista))
    comprobar("el aviso lleva el enlace que salio",
              f"https://{HOST}/minar" in a.lista[0][1], a.lista[0][1])
    comprobar("guarda el dia de la primera vez", idx.get("primera_bing") == dia())
    comprobar("marca que ya aviso", idx.get("avisado_bing") is True)

    # 3. Sigue saliendo al dia siguiente: NO vuelve a avisar.
    previo = {"indexado": dict(idx, dia=dia(-1))}
    a2 = Avisos()
    m2 = Motor({"google": ("no", "", ""),
                "bing": ("sale", "", f"https://{HOST}/minar")})
    idx2 = V.revisa_indexado(BASE, previo, forzar=True, consultar=m2, avisar=a2)
    comprobar("al dia siguiente no repite el aviso", not a2.lista, str(a2.lista))
    comprobar("conserva el dia de la primera vez",
              idx2.get("primera_bing") == idx.get("primera_bing"),
              f"{idx2.get('primera_bing')} != {idx.get('primera_bing')}")

    # 4. Deja de salir y vuelve: tampoco repite (ya se aviso aquel dia).
    previo = {"indexado": dict(idx, dia=dia(-1), bing="no")}
    a3 = Avisos()
    m3 = Motor({"google": ("no", "", ""), "bing": ("sale", "", f"https://{HOST}/")})
    idx3 = V.revisa_indexado(BASE, previo, forzar=True, consultar=m3, avisar=a3)
    comprobar("sale -> no -> sale no vuelve a avisar", not a3.lista, str(a3.lista))
    comprobar("pero el estado dice que vuelve a salir", idx3.get("bing") == "sale")

    # 5. El motor nos bloquea: "sin comprobar", nunca "no".
    m = Motor({"google": ("sin_comprobar", "Google no sirve resultados sin navegador", ""),
               "bing": ("sin_comprobar", "control es.wikipedia.org: sin resultados", "")})
    a = Avisos()
    idx = V.revisa_indexado(BASE, {}, forzar=True, consultar=m, avisar=a)
    comprobar("bloqueado se queda en 'sin_comprobar'",
              idx.get("google") == "sin_comprobar" and idx.get("bing") == "sin_comprobar")
    comprobar("bloqueado NO se convierte en 'no'",
              idx.get("google") != "no" and idx.get("bing") != "no")
    comprobar("bloqueado no avisa", not a.lista)
    comprobar("la linea lo dice", "sin comprobar" in V.linea_indexado(idx))

    # 6. Un motor revienta (red rota, DNS...): no puede tumbar al vigilante.
    m = Motor({"google": RuntimeError("sin red"), "bing": ("no", "", "")})
    a = Avisos()
    idx = V.revisa_indexado(BASE, {}, forzar=True, consultar=m, avisar=a)
    comprobar("una excepcion deja 'sin_comprobar', no rompe",
              idx.get("google") == "sin_comprobar")
    comprobar("y guarda el motivo", "RuntimeError" in (idx.get("detalle_google") or ""),
              idx.get("detalle_google") or "")
    comprobar("el otro motor se mira igual", idx.get("bing") == "no")

    # 7. Una vez al dia: la segunda vuelta del mismo dia no pregunta.
    m = Motor({"google": ("no", "", ""), "bing": ("no", "", "")})
    a = Avisos()
    primero = V.revisa_indexado(BASE, {}, forzar=True, consultar=m, avisar=a)
    m2 = Motor({})
    segundo = V.revisa_indexado(BASE, {"indexado": primero}, consultar=m2, avisar=a)
    comprobar("no vuelve a preguntar el mismo dia", not m2.llamadas, str(m2.llamadas))
    comprobar("devuelve lo que ya sabia", segundo.get("dia") == primero.get("dia"))

    # 8. Sin host no inventa estado.
    comprobar("una base sin host no devuelve datos",
              not V.revisa_indexado("", {}, forzar=True, consultar=Motor({}), avisar=Avisos()))

    # 9. Si un motor nos bloqueo, se reintenta el mismo dia (pero no en bucle).
    bloqueado = {"dia": dia(), "host": HOST, "google": "no", "bing": "sin_comprobar",
                 "intentos": 1,
                 "cuando": (datetime.now() - timedelta(hours=3)).strftime("%Y-%m-%d %H:%M:%S")}
    m = Motor({"google": ("no", "", ""), "bing": ("no", "", "")})
    idx = V.revisa_indexado(BASE, {"indexado": bloqueado}, consultar=m, avisar=Avisos())
    comprobar("tras 2 h de bloqueo, reintenta", len(m.llamadas) == 2, str(m.llamadas))
    comprobar("cuenta el intento", idx.get("intentos") == 2, str(idx.get("intentos")))

    recien = dict(bloqueado, cuando=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    m2 = Motor({})
    V.revisa_indexado(BASE, {"indexado": recien}, consultar=m2, avisar=Avisos())
    comprobar("recien bloqueado, no machaca al instante", not m2.llamadas, str(m2.llamadas))

    agotado = dict(bloqueado, intentos=3)
    m3 = Motor({})
    V.revisa_indexado(BASE, {"indexado": agotado}, consultar=m3, avisar=Avisos())
    comprobar("a los tres intentos del dia para", not m3.llamadas, str(m3.llamadas))

    # Google sin cuenta de servicio no se reintenta: manana contestara lo mismo.
    sin_clave = dict(bloqueado, bing="no", google="sin_comprobar")
    real_cuenta = V.GOOGLE_CUENTA
    V.GOOGLE_CUENTA = Path(tempfile.gettempdir()) / "no_existe_cuenta_google.json"
    m4 = Motor({})
    try:
        V.revisa_indexado(BASE, {"indexado": sin_clave}, consultar=m4, avisar=Avisos())
    finally:
        V.GOOGLE_CUENTA = real_cuenta
    comprobar("Google sin cuenta de servicio no gasta reintentos",
              not m4.llamadas, str(m4.llamadas))

    # 10. La pausa del 202 de DuckDuckGo no se toma por "no hay resultados".
    class _Respuesta:
        def __init__(self, codigo, cuerpo=""):
            self.status = codigo
            self._c = cuerpo

        def read(self, n=-1):
            return self._c.encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    real_pide, real_sleep = V._pide, V.time.sleep
    intentos = []

    def pide_falso(url, tiempo=25.0):
        intentos.append(url)
        return 202, ""

    V._pide = pide_falso
    V.time.sleep = lambda s: None
    try:
        urls, nota = V._ddg("site:ejemplo.test")
    finally:
        V._pide, V.time.sleep = real_pide, real_sleep
    comprobar("un 202 no devuelve resultados vacios como si no hubiera nada",
              not urls and "202" in nota, f"{urls} {nota}")
    comprobar("un 202 se reintenta (2 intentos, no 20)", len(intentos) == 2, str(len(intentos)))


def _b64(texto: str) -> bytes:
    import base64
    return base64.urlsafe_b64decode(texto + "=" * (-len(texto) % 4))


def pruebas_search_console() -> None:
    """Google por la API de inspeccion de URLs: el JWT y la lectura del veredicto.

    No se puede probar contra Google de verdad sin la clave de Kuki, asi que se
    prueba TODO lo demas: que el JWT se firme bien de verdad (se verifica con la
    clave publica), que se pida el token al sitio correcto y que cada veredicto
    de Google se traduzca al estado que toca, incluidos los errores.
    """
    print("\n== Google por la API de Search Console ==")
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding, rsa

    llave = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = llave.private_bytes(serialization.Encoding.PEM,
                              serialization.PrivateFormat.PKCS8,
                              serialization.NoEncryption()).decode()
    cuenta = {"type": "service_account",
              "client_email": "vigia@kukia.iam.gserviceaccount.com",
              "private_key": pem, "project_id": "kukia"}

    tmp = Path(tempfile.mkdtemp(prefix="kukia_sc_"))
    fichero = tmp / "searchconsole_cuenta.json"
    fichero.write_text(json.dumps(cuenta), encoding="utf-8")

    # --- el JWT (de verdad, verificado con la clave publica) -----------------
    jwt = V._jwt_servicio(cuenta, cuando=1700000000)
    partes = jwt.split(".")
    comprobar("el JWT tiene tres partes", len(partes) == 3, str(len(partes)))
    comprobar("sin relleno '=' (lo que pide el estandar)", "=" not in jwt)
    cabecera = json.loads(_b64(partes[0]))
    comprobar("cabecera RS256/JWT", cabecera == {"alg": "RS256", "typ": "JWT"}, str(cabecera))
    claims = json.loads(_b64(partes[1]))
    comprobar("iss = correo de la cuenta", claims.get("iss") == cuenta["client_email"])
    comprobar("scope = Search Console", claims.get("scope") == V.GOOGLE_SCOPE, str(claims.get("scope")))
    comprobar("aud = endpoint del token", claims.get("aud") == V.API_TOKEN, str(claims.get("aud")))
    comprobar("vale una hora", claims.get("exp", 0) - claims.get("iat", 0) == 3600)
    try:
        llave.public_key().verify(_b64(partes[2]), f"{partes[0]}.{partes[1]}".encode(),
                                  padding.PKCS1v15(), hashes.SHA256())
        firma_ok = True
    except Exception:
        firma_ok = False
    comprobar("la firma se verifica con la clave publica", firma_ok)

    # --- la propiedad de Search Console -------------------------------------
    reales = (V.GOOGLE_CUENTA, V.GOOGLE_SITIO, V._token_google, V._post_json, V._google_web)
    V.GOOGLE_CUENTA = fichero
    V.GOOGLE_SITIO = tmp / "google_site.txt"
    try:
        comprobar("sin google_site.txt la propiedad es el prefijo de URL",
                  V._sitio_de_google(HOST) == f"https://{HOST}/", V._sitio_de_google(HOST))
        V.GOOGLE_SITIO.write_text("sc-domain:" + HOST, encoding="utf-8")
        comprobar("google_site.txt manda (propiedad de dominio)",
                  V._sitio_de_google(HOST) == "sc-domain:" + HOST)
        V.GOOGLE_SITIO.unlink()

        # --- pedir el token -------------------------------------------------
        visto = {}

        def post_token(url, datos, cabeceras=None, tiempo=25.0):
            visto.update(url=url, datos=datos)
            return 200, '{"access_token": "tok-123", "expires_in": 3599}'

        V._post_json = post_token
        try:
            token, nota = V._token_google(cuenta)
        finally:
            V._post_json = reales[3]
        comprobar("pide el token al endpoint de Google", visto.get("url") == V.API_TOKEN,
                  str(visto.get("url")))
        comprobar("grant de cuenta de servicio",
                  (visto.get("datos") or {}).get("grant_type")
                  == "urn:ietf:params:oauth:grant-type:jwt-bearer")
        comprobar("manda un JWT como assertion",
                  str((visto.get("datos") or {}).get("assertion", "")).count(".") == 2)
        comprobar("devuelve el token", token == "tok-123" and not nota, f"{token} {nota}")

        # --- leer el veredicto de Google ------------------------------------
        V._token_google = lambda c: ("tok-123", "")
        enviado = {}

        def inspeccion(respuesta):
            def falso(url, datos, cabeceras=None, tiempo=25.0):
                enviado.update(url=url, datos=datos, cab=cabeceras or {})
                if isinstance(respuesta, tuple):
                    return respuesta
                return 200, json.dumps(respuesta)
            return falso

        def con(respuesta):
            V._post_json = inspeccion(respuesta)
            try:
                return V._google_inspeccion(HOST)
            finally:
                V._post_json = reales[3]

        estado, detalle, url = con({"inspectionResult": {"indexStatusResult": {
            "verdict": "PASS", "coverageState": "Enviada e indexada"}}})
        comprobar("PASS -> sale", estado == "sale", f"{estado} {detalle}")
        comprobar("manda la URL y la propiedad correctas",
                  enviado.get("datos") == {"inspectionUrl": f"https://{HOST}/",
                                          "siteUrl": f"https://{HOST}/",
                                          "languageCode": "es"}, str(enviado.get("datos")))
        comprobar("llama a la API de inspeccion con el token",
                  enviado.get("url") == V.API_INSPECCION
                  and (enviado.get("cab") or {}).get("Authorization") == "Bearer tok-123",
                  str(enviado.get("cab")))

        estado, detalle, _ = con({"inspectionResult": {"indexStatusResult": {
            "verdict": "FAIL", "coverageState": "Descubierta: actualmente sin indexar"}}})
        comprobar("FAIL -> no", estado == "no", f"{estado} {detalle}")
        comprobar("y guarda lo que dice Google",
                  "Descubierta" in detalle, detalle)

        estado, detalle, _ = con({"inspectionResult": {"indexStatusResult": {
            "verdict": "NEUTRAL", "coverageState": ""}}})
        comprobar("NEUTRAL -> sin comprobar (Google no se moja)",
                  estado == "sin_comprobar", f"{estado} {detalle}")

        estado, detalle, _ = con((403, '{"error": {"message": "User does not have sufficient permission"}}'))
        comprobar("403 -> sin comprobar, con el motivo",
                  estado == "sin_comprobar" and "permission" in detalle, f"{estado} {detalle}")
        comprobar("un 403 no se convierte en 'no'", estado != "no")

        estado, detalle, _ = con((0, ""))
        comprobar("sin respuesta -> sin comprobar", estado == "sin_comprobar", detalle)

        estado, detalle, _ = con((200, "esto no es json"))
        comprobar("respuesta rota -> sin comprobar", estado == "sin_comprobar", detalle)

        # --- ficheros que estan mal -----------------------------------------
        fichero.write_text("{}", encoding="utf-8")
        estado, detalle, _ = V._google_inspeccion(HOST)
        comprobar("una clave sin client_email/private_key se canta",
                  estado == "sin_comprobar" and "cuenta de servicio" in detalle, detalle)

        fichero.write_text("no json", encoding="utf-8")
        estado, detalle, _ = V._google_inspeccion(HOST)
        comprobar("un fichero ilegible se canta", estado == "sin_comprobar", detalle)

        # --- sin cuenta de servicio se usa la web --------------------------
        V.GOOGLE_CUENTA = tmp / "no_hay.json"
        usado = {}
        V._google_web = lambda host: (usado.update(host=host), ("no", "", ""))[1]
        estado, _, _ = V.comprueba_google(HOST)
        comprobar("sin cuenta de servicio se intenta la web", usado.get("host") == HOST,
                  str(usado))
        comprobar("y el estado pasa tal cual", estado == "no")
    finally:
        (V.GOOGLE_CUENTA, V.GOOGLE_SITIO, V._token_google, V._post_json,
         V._google_web) = reales
        shutil.rmtree(tmp, ignore_errors=True)


def prueba_de_una_vez() -> None:
    """El camino completo: una_vez() guarda el estado y lo escribe en el parte."""
    print("\n== el vigilante entero (con ficheros temporales) ==")
    tmp = Path(tempfile.mkdtemp(prefix="kukia_vig_"))
    reales = {n: getattr(V, n) for n in ("LOGS", "LOG", "ESTADO", "AVISO",
                                         "revisa", "_consulta", "avisa")}
    avisos = Avisos()
    try:
        V.LOGS = tmp
        V.LOG = tmp / "vigilante_web.log"
        V.ESTADO = tmp / "vigilante_web.json"
        V.AVISO = tmp / "ESTADO_BUSCADORES.txt"
        V.revisa = lambda base: ("listo", "")
        V._consulta = Motor({"google": ("sin_comprobar", "bloquea", ""),
                             "bing": ("sale", "", f"https://{HOST}/whitepaper")})
        V.avisa = avisos

        rc = V.una_vez(BASE, enviar=False)
        estado = json.loads(V.ESTADO.read_text(encoding="utf-8"))
        idx = estado.get("indexado") or {}
        parte = V.AVISO.read_text(encoding="utf-8") if V.AVISO.exists() else ""

        comprobar("una_vez termina bien con el sitio listo", rc == 0, f"rc={rc}")
        comprobar("guarda la mirada del dia en el estado", idx.get("dia") == dia(),
                  str(idx.get("dia")))
        comprobar("guarda que sale en Bing", idx.get("bing") == "sale")
        comprobar("guarda que Google no se pudo comprobar",
                  idx.get("google") == "sin_comprobar")
        # Ojo: aqui tambien salta el aviso de "el sitio ya sirve" (el estado es
        # nuevo), asi que se cuentan solo los avisos de buscadores.
        de_buscadores = [a for a in avisos.lista if "sale en" in a[0]]
        comprobar("avisa una vez de que sale en Bing", len(de_buscadores) == 1,
                  str(avisos.lista))
        comprobar("el parte lleva la linea de buscadores",
                  "Buscadores (" in parte and "Bing SALE desde el" in parte, parte[:200])
        comprobar("el parte no se inventa que Google salga",
                  "Google SALE" not in parte)

        # Segunda vuelta del mismo dia: no vuelve a preguntar ni a avisar.
        m2 = Motor({})
        V._consulta = m2
        V.una_vez(BASE, enviar=False)
        comprobar("dos vueltas el mismo dia = una sola consulta", not m2.llamadas,
                  str(m2.llamadas))
        comprobar("y sigue habiendo un solo aviso de buscadores",
                  len([a for a in avisos.lista if "sale en" in a[0]]) == 1,
                  str(avisos.lista))
    finally:
        for n, v in reales.items():
            setattr(V, n, v)


# --------------------------------------------------------------------------
#  Contra la red (lo unico que dice si funciona de verdad)
# --------------------------------------------------------------------------
def pruebas_de_red() -> None:
    print("\n== contra la red, de verdad ==")
    red_bing()
    red_google()


def red_bing() -> None:
    control, nota, _ = V.comprueba_bing(V.CONTROL)
    if control != "sale":
        salto("el control tiene que salir en Bing",
              f"estado={control} {nota} (nos estan bloqueando: no se concluye nada)")
        return
    comprobar(f"el control ({V.CONTROL}) sale: la consulta vale", True)

    time.sleep(2.0)
    estado, detalle, url = V.comprueba_bing(HOST)
    if estado == "sin_comprobar":
        # DuckDuckGo da 202 cuando le preguntas mucho: no es fallo del codigo.
        salto("KUKIA contra el indice de Bing", detalle)
    else:
        comprobar("KUKIA: se pudo preguntar de verdad", True)
        comprobar("KUKIA: el resultado es coherente con el enlace",
                  estado == "no" or (estado == "sale" and HOST in (url or "")),
                  f"estado={estado} url={url}")
        print(f"         Bing (via DuckDuckGo): {estado} {detalle or url or ''}".rstrip())

def red_google() -> None:
    # Google, de verdad: con una cuenta inventada TIENE que contestar "invalid
    # grant". Si contestara otra cosa, el que esta mal es nuestro JWT.
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    llave = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    inventada = {"client_email": "prueba@kukia-pruebas.iam.gserviceaccount.com",
                 "private_key": llave.private_bytes(
                     serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                     serialization.NoEncryption()).decode()}
    codigo, cuerpo = V._post_json(V.API_TOKEN, {
        "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
        "assertion": V._jwt_servicio(inventada)})
    if codigo == 0:
        salto("Google entiende nuestro JWT", "sin red")
    else:
        try:
            error = (json.loads(cuerpo).get("error") or "")
        except Exception:
            error = ""
        comprobar("Google entiende nuestro JWT (contesta invalid_grant, no otra cosa)",
                  codigo == 400 and error == "invalid_grant", f"HTTP {codigo} {error}")
        print(f"         Google: HTTP {codigo} {error}")

    time.sleep(2.0)
    g_estado, g_detalle, g_url = V.comprueba_google(HOST)
    comprobar("Google responde uno de los tres estados (nunca revienta)",
              g_estado in ("sale", "no", "sin_comprobar"), g_estado)
    comprobar("Google: si dice que sale, el enlace es de KUKIA",
              g_estado != "sale" or HOST in (g_url or ""), g_url or "")
    print(f"         Google: {g_estado} {g_detalle or g_url or ''}".rstrip())


# --------------------------------------------------------------------------
#  Linea de comandos
# --------------------------------------------------------------------------
def prueba_cli() -> None:
    """--indexado --solo-ver no debe tocar el estado ni avisar.

    Se usa un estado de mentira en una carpeta temporal: el de verdad lo escribe
    el vigilante cada 10 minutos y compararlo seria una carrera perdida.
    """
    print("\n== --indexado --dominio <control> --solo-ver ==")
    tmp = Path(tempfile.mkdtemp(prefix="kukia_cli_"))
    estado = tmp / "vigilante_web.json"
    estado.write_text('{"visto": "listo", "indexado": {"dia": "2020-01-01"}}',
                     encoding="utf-8")
    real = V.ESTADO
    V.ESTADO = estado
    try:
        antes = estado.read_bytes()
        codigo, salida = ejecutar(["--indexado", "--dominio", V.CONTROL, "--solo-ver"])
        despues = estado.read_bytes()
    finally:
        V.ESTADO = real
        shutil.rmtree(tmp, ignore_errors=True)
    comprobar("--solo-ver no toca el estado guardado", antes == despues)
    comprobar("--solo-ver lo dice", "modo prueba" in salida, salida[-200:])
    if "sin comprobar" in salida.lower() and codigo != 0:
        salto("la salida del CLI contra el control",
              "no se pudo comprobar (bloqueo o sin red); el codigo hizo lo correcto")
    else:
        comprobar("con un dominio que sale, el CLI sale con 0", codigo == 0,
                  f"codigo={codigo}")
        comprobar("y lo pinta como 'sale'", "sale" in salida.lower(), salida[-300:])


def ejecutar(args: list[str]) -> tuple[int, str]:
    import contextlib
    import io
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        try:
            rc = V.main(args)
        except SystemExit as e:
            rc = int(e.code or 0)
    return int(rc or 0), buf.getvalue()


def main() -> int:
    ap = argparse.ArgumentParser(description="Pruebas del aviso de indexado.")
    ap.add_argument("--sin-red", action="store_true", help="no salir a internet")
    args = ap.parse_args()

    # Las pruebas escriben en SU log: si escribieran en logs/vigilante_web.log,
    # el historial de verdad quedaria lleno de "indexado Bing: no" de mentira y
    # dentro de un mes nadie sabria distinguir que paso de verdad.
    tmp = Path(tempfile.mkdtemp(prefix="kukia_vig_log_"))
    reales = (V.LOGS, V.LOG)
    V.LOGS, V.LOG = tmp, tmp / "pruebas.log"
    try:
        pruebas_de_sitio()
        pruebas_de_linea()
        pruebas_de_logica()
        pruebas_search_console()
        prueba_de_una_vez()
        if not args.sin_red:
            pruebas_de_red()
            prueba_cli()
    finally:
        V.LOGS, V.LOG = reales
        shutil.rmtree(tmp, ignore_errors=True)

    fallos = [r for r in RESULTADOS if not r[1]]
    print(f"\n{len(RESULTADOS) - len(fallos)}/{len(RESULTADOS)} OK"
          + (f" · {len(SALTOS)} sin poder comprobar" if SALTOS else ""))
    for nombre, _ok, detalle in fallos:
        print(f"  FALLO: {nombre}" + (f" -> {detalle}" if detalle else ""))
    for nombre, detalle in SALTOS:
        print(f"  no comprobado: {nombre} -> {detalle}")
    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(main())
