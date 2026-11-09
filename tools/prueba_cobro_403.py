#!/usr/bin/env python3
"""Prueba del registro de cobros y del diagnostico de 403 (NO toca el Core).

Levanta un servidor falso en 127.0.0.1:<puerto libre> que imita a
/admin/ingresos, y comprueba:

  S1  403 con token: el mensaje lleva sha8 y origen, y el registro apunta
     estado=403, motivo=token_rechazado y el MISMO token que recibio el servidor.
  S2  403 sin token (fichero inexistente): motivo=sin_token y el mensaje dice
     que no se envio token, en vez de culpar al Core.
  S3  200: se apunta estado=200 y ok=True.
  S4  409: se apunta motivo=ya_estaba (no es un fallo, es "ya contado").
  S5  el token no aparece NUNCA en el registro; el sha8 si.
  S6  si el registro no se puede escribir, anotar() no lanza y sigue devolviendo.
  S7  el token puede venir de KUKIA_ADMIN_TOKEN_TEXTO, sin perder el error.
  S8  --pendientes lista SOLO lo que no entro (200 y 409 no cuentan).
  S9  --reintentar lo manda otra vez, lo apunta y deja la lista vacia.
  S10 si el Core no responde, --reintentar sale 1 y el pendiente NO se pierde.

Uso:  python tools/prueba_cobro_403.py
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
fallos = []


def check(nombre: str, condicion: bool, detalle: str = "") -> None:
    print(("  [PASA] " if condicion else "  [FALLA] ") + nombre
          + (f"  ({detalle})" if detalle and not condicion else ""))
    if not condicion:
        fallos.append(nombre)


class Falso(BaseHTTPRequestHandler):
    """Imita /admin/ingresos sin arrancar el Core."""

    modo = "403"
    vistos: list = []

    def do_POST(self):  # noqa: N802 (nombre que impone http.server)
        n = int(self.headers.get("Content-Length") or 0)
        crudo = self.rfile.read(n)
        try:
            cuerpo = json.loads(crudo.decode("utf-8") or "{}")
        except Exception:
            cuerpo = {}
        Falso.vistos.append({"ruta": self.path,
                             "token": self.headers.get("X-Admin-Token"),
                             "cuerpo": cuerpo})
        if Falso.modo == "403":
            self._responder(403, {"detail": "solo el administrador del Core"})
        elif Falso.modo == "409":
            self._responder(409, {"detail": "ese trabajo ya esta anotado (ingreso #7)"})
        else:
            self._responder(200, {"id": 7, "precio_eur": 3.75, "minutos": 12.5,
                                  "asiento_hash": "a" * 64, "total_eur": 3.75,
                                  "ingresos": 1, "kia_emitidos": 0.0})

    def _responder(self, codigo: int, obj: dict) -> None:
        datos = json.dumps(obj).encode("utf-8")
        self.send_response(codigo)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(datos)))
        self.end_headers()
        self.wfile.write(datos)

    def log_message(self, *args):  # silencio: la prueba habla por check()
        pass


def lineas(ruta: Path) -> list:
    if not ruta.is_file():
        return []
    return [json.loads(l) for l in ruta.read_text(encoding="utf-8").splitlines() if l.strip()]


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="prueba_cobro_"))
    registro = tmp / "cobros_registro.jsonl"
    fichero_token = tmp / "admin_token.txt"
    token = "token-de-prueba-" + "9" * 40
    sha = hashlib.sha256(token.encode("utf-8")).hexdigest()[:8]
    fichero_token.write_text(token, encoding="utf-8")

    # El entorno se fija ANTES de importar: cobros lee el token en cada llamada,
    # pero registro_cobros() tambien se puede cambiar despues (S6).
    os.environ["KUKIA_ADMIN_TOKEN"] = str(fichero_token)
    os.environ["KUKIA_COBROS_REGISTRO"] = str(registro)
    os.environ.pop("KUKIA_ADMIN_TOKEN_TEXTO", None)
    sys.path.insert(0, str(RAIZ / "servicio"))
    import cobros  # noqa: PLC0415 (a proposito: ya estan las variables puestas)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Falso)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    core = f"http://127.0.0.1:{srv.server_address[1]}"
    print(f"servidor falso en {core}\nregistro de prueba: {registro}\n")

    try:
        print("S0  diagnostico del token")
        d = cobros.token_diagnostico()
        check("origen=fichero", d["origen"] == "fichero", str(d["origen"]))
        check("sha8 correcto (8 hex) y token disponible para enviar",
              d["sha8"] == sha and len(d["sha8"]) == 8 and d["token"] == token,
              f"sha8={d['sha8']} esperado={sha}")

        print("\nS1  403 con token")
        Falso.modo, Falso.vistos = "403", []
        r = cobros.anotar("charla.json", "Cliente X", 12.5, 3.75, core=core)
        check("no ok y estado 403", r["ok"] is False and r["estado"] == 403, str(r.get("estado")))
        check("motivo=token_rechazado", r.get("motivo") == "token_rechazado", str(r.get("motivo")))
        check("mensaje con sha8 y origen",
              f"sha8={sha}" in r["mensaje"] and "origen=fichero" in r["mensaje"], r["mensaje"])
        check("el servidor recibio ese token",
              bool(Falso.vistos) and Falso.vistos[-1]["token"] == token)
        reg = lineas(registro)
        check("una linea en el registro", len(reg) == 1, str(len(reg)))
        if reg:
            u = reg[-1]
            check("registro: estado 403 y motivo",
                  u["estado"] == 403 and u["motivo"] == "token_rechazado", json.dumps(u)[:160])
            check("registro: sha8 y origen",
                  u["token_sha8"] == sha and u["token_origen"] == "fichero")
            check("registro: trabajo, cliente y precio",
                  (u["trabajo"], u["cliente"], u["precio_eur"]) == ("charla.json", "Cliente X", 3.75),
                  json.dumps(u)[:160])

        print("\nS2  403 sin token (fichero inexistente)")
        os.environ["KUKIA_ADMIN_TOKEN"] = str(tmp / "no_existe.txt")
        Falso.vistos = []
        r = cobros.anotar("otra.json", "Cliente Y", 5.0, 2.0, core=core)
        check("estado 403 y motivo=sin_token",
              r["estado"] == 403 and r.get("motivo") == "sin_token", str(r.get("motivo")))
        check("el mensaje dice que no se envio token",
              "no se envio token" in r["mensaje"], r["mensaje"])
        check("el servidor no recibio cabecera",
              bool(Falso.vistos) and Falso.vistos[-1]["token"] in (None, ""))
        reg = lineas(registro)
        check("registro: motivo sin_token y sha8 vacio",
              reg[-1]["motivo"] == "sin_token" and reg[-1]["token_sha8"] == "", json.dumps(reg[-1])[:160])

        print("\nS3  200 (el cobro entra)")
        os.environ["KUKIA_ADMIN_TOKEN"] = str(fichero_token)
        Falso.modo = "200"
        r = cobros.anotar("buena.json", "Cliente Z", 20.0, 6.5, core=core)
        check("ok, estado 200, id 7", r["ok"] and r["estado"] == 200 and r.get("id") == 7, str(r.get("mensaje")))
        reg = lineas(registro)
        check("registro: ok=True estado=200",
              reg[-1]["ok"] is True and reg[-1]["estado"] == 200, json.dumps(reg[-1])[:160])

        print("\nS4  409 (ya estaba anotado)")
        Falso.modo = "409"
        r = cobros.anotar("repe.json", "Cliente Z", 20.0, 6.5, core=core)
        check("ya_estaba y motivo", r.get("ya_estaba") is True and r.get("motivo") == "ya_estaba",
              str(r.get("motivo")))
        reg = lineas(registro)
        check("registro: estado 409", reg[-1]["estado"] == 409, json.dumps(reg[-1])[:160])

        print("\nS5  el token no aparece en el registro")
        texto = registro.read_text(encoding="utf-8")
        check("token ausente", token not in texto)
        check("sha8 presente", sha in texto)
        check("el token no esta en ninguna linea",
              all(token not in json.dumps(u) for u in lineas(registro)))

        print("\nS6  registro imposible (ruta a un directorio)")
        antes = len(lineas(registro))
        os.environ["KUKIA_COBROS_REGISTRO"] = str(tmp)
        Falso.modo = "200"
        try:
            r = cobros.anotar("sin_registro.json", "Cliente W", 3.0, 1.25, core=core)
            check("anotar no lanza y devuelve ok", r["ok"] is True)
        except Exception as e:
            check("anotar no lanza y devuelve ok", False, f"{type(e).__name__}: {e}")
        os.environ["KUKIA_COBROS_REGISTRO"] = str(registro)
        check("el registro bueno no gana lineas si no se puede escribir",
              len(lineas(registro)) == antes, f"{antes} -> {len(lineas(registro))}")

        print("\nS7  token del entorno (KUKIA_ADMIN_TOKEN_TEXTO)")
        os.environ["KUKIA_ADMIN_TOKEN"] = str(tmp / "no_existe.txt")
        os.environ["KUKIA_ADMIN_TOKEN_TEXTO"] = "token-en-variable"
        d = cobros.token_diagnostico()
        check("origen=env", d["origen"] == "env" and d["token"] == "token-en-variable", d["origen"])
        check("el error del fichero no se pierde",
              "no existe" in d["error"], d["error"])

        print("\nS8  --pendientes: solo lo que no entro")
        os.environ["KUKIA_ADMIN_TOKEN"] = str(fichero_token)
        os.environ.pop("KUKIA_ADMIN_TOKEN_TEXTO", None)
        os.environ["KUKIA_COBROS_REGISTRO"] = str(registro)
        # Se parte de un registro limpio: los 403 de S1 y S2 son intentos que
        # nunca entraron y tambien saldrian como pendientes (es correcto).
        registro.unlink(missing_ok=True)
        Falso.modo = "403"
        cobros.anotar("pendiente_a.mp3", "Cliente P", 8.0, 2.0, core=core)
        Falso.modo = "200"
        cobros.anotar("entrado_b.mp3", "Cliente Q", 9.0, 2.5, core=core)
        Falso.modo = "409"
        cobros.anotar("repetido_c.mp3", "Cliente R", 10.0, 3.0, core=core)
        cola = cobros.pendientes()
        check("solo queda el que no entro (ni 200 ni 409 cuentan)",
              [u["trabajo"] for u in cola] == ["pendiente_a.mp3"],
              str([u["trabajo"] for u in cola]))
        check("el pendiente conserva importe y motivo",
              bool(cola) and cola[0]["precio_eur"] == 2.0 and
              cola[0]["motivo"] == "token_rechazado",
              json.dumps(cola[0])[:200] if cola else "vacio")
        salida = io.StringIO()
        with contextlib.redirect_stdout(salida):
            rc_p = cobros.main(["--pendientes"])
        texto_p = salida.getvalue()
        check("--pendientes sale 0 y nombra el trabajo",
              rc_p == 0 and "pendiente_a.mp3" in texto_p, f"rc={rc_p} {texto_p[:200]}")

        print("\nS9  --reintentar: lo manda, lo apunta y vacia la lista")
        Falso.modo = "200"
        salida = io.StringIO()
        with contextlib.redirect_stdout(salida):
            rc_r = cobros.main(["--reintentar", "--core", core])
        check("exit 0 al resolverse", rc_r == 0, f"rc={rc_r} {salida.getvalue()[:200]}")
        check("ya no queda ningun pendiente", cobros.pendientes() == [],
              str(cobros.pendientes())[:200])
        u = lineas(registro)[-1]
        check("el reintento quedo apuntado con estado 200",
              u["trabajo"] == "pendiente_a.mp3" and u["estado"] == 200,
              json.dumps(u)[:200])

        print("\nS10  --reintentar con el Core caido no pierde el pendiente")
        Falso.modo = "403"
        cobros.anotar("pendiente_d.mp3", "Cliente S", 5.0, 1.5, core=core)
        salida = io.StringIO()
        with contextlib.redirect_stdout(salida):
            rc_d = cobros.main(["--reintentar", "--core", "http://127.0.0.1:9"])
        check("exit 1 si algo sigue fuera", rc_d == 1, f"rc={rc_d}")
        check("el pendiente sigue ahi tras el fallo",
              any(u["trabajo"] == "pendiente_d.mp3" for u in cobros.pendientes()),
              str([u["trabajo"] for u in cobros.pendientes()])[:200])
    finally:
        srv.shutdown()
        srv.server_close()

    print()
    if fallos:
        print(f"FALLAN {len(fallos)}: " + ", ".join(fallos))
        return 1
    print("TODO PASA")
    return 0


if __name__ == "__main__":
    for _f in (sys.stdout, sys.stderr):
        try:
            _f.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    raise SystemExit(main())
