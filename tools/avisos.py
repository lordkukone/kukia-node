#!/usr/bin/env python3
"""Avisos al movil por Telegram, para cuando algo se cae.

POR QUE TELEGRAM Y NO WHATSAPP
  Telegram se usa con un bot propio: se crea en @BotFather en dos minutos, es
  gratis y no necesita cuenta de empresa ni verificacion. WhatsApp solo deja
  mandar mensajes por su API de empresa (Meta) o pagando a un intermediario
  tipo Twilio, con numero verificado y plantillas aprobadas: para avisarse a si
  mismo de que una web no responde, no compensa. Si algun dia hace falta
  WhatsApp, lo de aqui es una sola funcion `enviar()` y se cambia sin tocar el
  vigilante.

COMO SE CONFIGURA (una vez)
  1. En Telegram, habla con @BotFather, manda /newbot y apunta el token
     (algo como 8123456789:AAH...).
  2. Manda un mensaje cualquiera a tu bot nuevo (o pulsa /start).
  3. python tools/avisos.py --configurar
     Pega el token, el script busca solo tu chat y manda un mensaje de prueba.

  El token queda en `coordinator/telegram_aviso.json`, solo para tu usuario de
  Windows, y fuera de git. No se imprime nunca por pantalla.

USO
    python tools/avisos.py --configurar    alta (token + chat + prueba)
    python tools/avisos.py --probar        mandar un mensaje de prueba
    python tools/avisos.py --estado        si esta configurado y con que bot

DARLO DE ALTA SIN QUE EL TOKEN PASE POR NINGUN SITIO
    En una terminal, pega el token sin que se vea lo que escribes y deja que el
    script lo recoja de ahi (el fichero se borra al leerlo):

        read -rsp "token del bot: " TG; printf '%s' "$TG" > .telegram_token; unset TG; echo
        python tools/avisos.py --configurar --token-de .telegram_token

    Asi el token no queda en el historial del shell (no va en la orden), ni en la
    salida de la terminal, ni en ningun chat: solo en
    `coordinator/telegram_aviso.json`, que esta fuera de git.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
CONFIG = RAIZ / "coordinator" / "telegram_aviso.json"
API = "https://api.telegram.org/bot{token}/{metodo}"


def leer() -> dict:
    try:
        d = json.loads(CONFIG.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(d, dict):
        return {}
    return d


def configurado() -> bool:
    d = leer()
    return bool(d.get("token") and d.get("chat_id"))


def guardar(token: str, chat_id: str) -> None:
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    CONFIG.write_text(json.dumps({"token": token, "chat_id": str(chat_id)},
                                 indent=2) + "\n", encoding="utf-8")
    try:
        os.chmod(CONFIG, 0o600)
    except Exception:
        pass


def pide(token: str, metodo: str, datos: dict | None = None,
         tiempo: float = 20.0) -> dict:
    """Llama a la API del bot. Devuelve su JSON (o {ok: False, ...})."""
    cuerpo = None if datos is None else json.dumps(datos).encode()
    pet = urllib.request.Request(API.format(token=token, metodo=metodo),
                                 data=cuerpo,
                                 headers={"Content-Type": "application/json"},
                                 method="POST" if datos is not None else "GET")
    try:
        with urllib.request.urlopen(pet, timeout=tiempo) as r:
            return json.loads(r.read(65536).decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read(65536).decode("utf-8", "replace"))
        except Exception:
            return {"ok": False, "descripcion": f"HTTP {e.code}"}
    except Exception as e:
        return {"ok": False, "descripcion": f"{type(e).__name__}: {e}"}


def enviar(titulo: str, texto: str = "") -> bool:
    """Manda el aviso al movil. Nunca lanza: si no hay Telegram, devuelve False."""
    d = leer()
    token, chat = d.get("token"), d.get("chat_id")
    if not token or not chat:
        return False
    mensaje = f"{titulo}\n{texto}".strip()
    r = pide(token, "sendMessage", {"chat_id": chat, "text": mensaje[:4000],
                                    "disable_web_page_preview": True})
    return bool(r.get("ok"))


def token_de_fichero(ruta: str) -> str:
    """Lee el token de un fichero y lo BORRA en el acto.

    Sirve para pegarlo en una terminal con `read -s` (que no enseña lo que se
    escribe): el token no aparece en la orden, ni en la salida, ni en el
    historial. El fichero temporal desaparece al usarlo; queda solo en
    `coordinator/telegram_aviso.json` (permisos del dueno, fuera de git).
    """
    p = Path(ruta)
    try:
        token = p.read_text(encoding="utf-8").strip()
    except Exception as e:
        print(f"[ERROR] no se pudo leer {ruta}: {type(e).__name__}")
        return ""
    try:
        p.unlink()
    except Exception:
        print(f"[aviso] no se pudo borrar {ruta}; borralo a mano")
    if not token or ":" not in token or len(token) < 20:
        print("[ERROR] eso no parece un token de bot (formato 123456789:AA...)")
        return ""
    return token


def configurar(token: str = "", interactivo: bool = True) -> int:
    if not token:
        print("Telegram: habla con @BotFather, /newbot, y copia el token que te da.")
        import getpass
        token = getpass.getpass("token del bot (no se muestra): ").strip()
    else:
        print("Telegram: token leido del fichero (no se imprime y el fichero ya no existe).")
    if not token:
        print("[ERROR] sin token no hay bot")
        return 2
    yo = pide(token, "getMe")
    if not yo.get("ok"):
        print(f"[ERROR] Telegram no acepta ese token: {yo.get('description', yo)}")
        return 2
    usuario = yo.get("result", {}).get("username", "?")
    print(f"bot valido: @{usuario}")

    chat = ""
    intentos = 6 if interactivo else 12
    for intento in range(intentos):
        upd = pide(token, "getUpdates", {"limit": 10, "timeout": 0})
        if upd.get("ok"):
            for u in reversed(upd.get("result") or []):
                m = u.get("message") or u.get("edited_message") or {}
                if m.get("chat", {}).get("id"):
                    chat = str(m["chat"]["id"])
                    nombre = m["chat"].get("first_name") or m["chat"].get("title") or ""
                    print(f"chat encontrado: {chat} ({nombre})")
                    break
        if chat:
            break
        if interactivo:
            print(f"todavia no veo tu mensaje ({intento + 1}/{intentos}). Abre Telegram, "
                  f"busca @{usuario} y mandale /start.")
            try:
                input("pulsa Enter cuando lo hayas mandado...")
            except EOFError:
                break
        else:
            # Sin terminal interactiva (token pasado por fichero): se espera solo.
            print(f"esperando tu /start en @{usuario} ... ({intento + 1}/{intentos})",
                  flush=True)
            time.sleep(5.0)
    if not chat:
        print("[ERROR] no encontre tu chat. Manda /start al bot y repite.")
        return 2

    prueba = pide(token, "sendMessage",
                  {"chat_id": chat, "text": "KUKIA: aviso de prueba. Si lees esto, "
                                            "los avisos al movil funcionan."})
    if not prueba.get("ok"):
        print(f"[ERROR] el bot no pudo escribirte: {prueba.get('description', prueba)}")
        print("        Abre el chat con el bot y pulsa /start, y repite.")
        return 2
    guardar(token, chat)
    print(f"guardado en {CONFIG} (token fuera de git, solo tu usuario lo lee)")
    print("prueba enviada. A partir de ahora el vigilante avisa tambien aqui.")
    print("\npara ver una caida y una vuelta de verdad, con aviso a los dos sitios:")
    print("  python tools/prueba_avisos_reales.py")
    return 0


def estado() -> int:
    d = leer()
    if not d.get("token"):
        print("sin configurar: no hay token en " + str(CONFIG))
        print("  python tools/avisos.py --configurar")
        return 1
    yo = pide(d["token"], "getMe")
    if yo.get("ok"):
        print(f"configurado con @{yo.get('result', {}).get('username', '?')}, "
              f"chat {d.get('chat_id')}")
        return 0
    print(f"hay token pero Telegram no lo acepta: {yo.get('description', yo)}")
    return 1


def probar() -> int:
    if not configurado():
        print("[ERROR] falta configurar: python tools/avisos.py --configurar")
        return 2
    ok = enviar("KUKIA: aviso de prueba",
                "Si ves esto en el movil, los avisos funcionan.")
    print("enviado" if ok else "no se pudo enviar (mira --estado)")
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="Avisos de KUKIA por Telegram")
    ap.add_argument("--configurar", action="store_true", help="alta del bot y del chat")
    ap.add_argument("--probar", action="store_true", help="mensaje de prueba")
    ap.add_argument("--estado", action="store_true", help="como esta la configuracion")
    ap.add_argument("--token-de", metavar="FICHERO", default="",
                    help="leer el token de un fichero (para pegarlo sin que se vea); "
                         "el fichero se borra al leerlo")
    args = ap.parse_args()
    if args.configurar:
        token = ""
        if args.token_de:
            token = token_de_fichero(args.token_de)
            if not token:
                return 2
        return configurar(token=token, interactivo=not bool(args.token_de))
    if args.probar:
        return probar()
    return estado()


if __name__ == "__main__":
    for _f in (sys.stdout, sys.stderr):
        try:
            _f.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    raise SystemExit(main())
