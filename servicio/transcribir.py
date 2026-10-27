"""Servicio de transcripcion de KUKIA: audio o video -> texto y subtitulos.

Hace dos cosas, y la primera no gasta GPU:

    python servicio/transcribir.py --presupuesto charla.mp3
        Duracion, precio, coste de luz, margen. Sirve para contestar a un
        cliente en cinco segundos, sin abrir el modelo.

    python servicio/transcribir.py charla.mp3
        Transcribe en la GPU y deja tres ficheros: .txt (texto limpio),
        .srt (subtitulos con marcas de tiempo) y .json (los datos del trabajo,
        para la factura).

Motor: faster-whisper (ctranslate2) en la GPU, con whisper de PyTorch como
segundo motor. Los dos ya estan instalados en el venv de la app Whisper de
Pinokio, asi que este script NO instala nada. Si lo lanzas con un Python sin
whisper, se relanza solo con el interprete bueno (KUKIA_WHISPER_PY).

Medido en la 5070 el 25-10-2026 con el modelo small: 5x tiempo real, o sea una
hora de audio en unos 12 minutos de GPU. Con 0,15 EUR/min son 9 EUR por hora de
audio y unos 3 centimos de luz.Precio de referencia del mercado: 0,10 - 0,30 EUR por minuto de audio.

AVISO AL MOVIL
Cuando el trabajo es largo (20 minutos de audio o mas, `--avisar-desde` para
cambiarlo, `--sin-aviso` para callarlo) se manda un mensaje por Telegram al
terminar, con el precio, los minutos y la carpeta de entrega. Usa el bot de
`tools/avisos.py`; si no esta configurado el trabajo sigue igual y solo se
avisa por pantalla de que no se pudo mandar."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent          # ...\KUKIA
SERVICIO = RAIZ / "servicio"
MODELOS = SERVICIO / "modelos"
ENTREGAS = SERVICIO / "entregas"

# Interprete con faster-whisper y whisper ya instalados (venv de Pinokio).
PY_WHISPER = os.environ.get(
    "KUKIA_WHISPER_PY",
    r"I:\pinokio\api\whisper-webui.git\app\env\Scripts\python.exe",
)

PRECIO_MIN = 0.15        # EUR por minuto de audio
MINIMO_MIN = 1.0         # se cobra un minimo de 1 minuto
# A partir de aqui el trabajo es "largo" y se avisa al movil por Telegram al
# terminar. Corto = no molesta: se ve en la propia ventana.
LARGO_MIN = 20.0
WATIOS = 150.0           # consumo real transcribiendo en la 5070
EUR_KWH = 0.22
AUDIO_EXT = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".wma",
             ".mp4", ".mkv", ".mov", ".avi", ".webm", ".ts", ".m4v"}


# ---------------------------------------------------------------- utilidades

def relanzar_con_whisper() -> None:
    """Si a este Python le falta el motor, se relanza con el que si lo tiene."""
    if os.environ.get("KUKIA_YA_RELANZADO") == "1" or not Path(PY_WHISPER).is_file():
        return
    os.environ["KUKIA_YA_RELANZADO"] = "1"
    os.execv(PY_WHISPER, [PY_WHISPER, str(Path(__file__).resolve())] + sys.argv[1:])


def duracion_segundos(ruta: Path) -> float:
    """Duracion real del audio o video, preguntando a ffprobe."""
    exe = shutil.which("ffprobe")
    if not exe:
        raise RuntimeError("no encuentro ffprobe en el PATH")
    salida = subprocess.run(
        [exe, "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(ruta)],
        capture_output=True, text=True, timeout=120,
    )
    try:
        return float(salida.stdout.strip())
    except ValueError:
        raise RuntimeError(f"ffprobe no da duracion de {ruta.name}: {salida.stderr[:200]}")


def precio(minutos: float, precio_min: float, minimo: float) -> dict:
    """Precio del trabajo, coste de la luz y margen."""
    cobrados = max(minutos, minimo)
    eur = cobrados * precio_min
    coste = (minutos / 60.0) * (WATIOS / 1000.0) * EUR_KWH
    return {"minutos": round(minutos, 2), "minutos_cobrados": round(cobrados, 2),
            "precio": round(eur, 2), "coste_luz": round(coste, 4),
            "margen": round(eur - coste, 2),
            "margen_x": round(eur / coste, 1) if coste > 0 else None}


def numero(n: float, decimales: int = 1) -> str:
    """37.42 -> 37,4: en castellano la coma va delante de los decimales."""
    return f"{float(n):.{decimales}f}".replace(".", ",")


def euros(n: float) -> str:
    """5.61 -> 5,61 EUR, que es como se escribe en castellano."""
    return numero(n, 2) + " EUR"


def segundos_a_texto(seg: float) -> str:
    """1 h 12 min / 37 min / 4 min 20 s, como se dice hablando."""
    seg = max(0.0, float(seg))
    h = int(seg // 3600)
    m = int((seg % 3600) // 60)
    s = int(round(seg % 60))
    if s == 60:
        m, s = m + 1, 0
    if h:
        return f"{h} h {m:02d} min"
    if m:
        return f"{m} min"
    return f"{s} s"


def mensaje_aviso(datos: dict, salida: Path, coste_luz: float | None = None) -> str:
    """Texto del aviso de Telegram: precio, minutos y donde esta la entrega.

    Se construye a partir del mismo `.json` que se le entrega al cliente, asi
    que lo que se lee en el movil es lo que se ha cobrado de verdad.
    """
    minutos = float(datos.get("minutos") or 0.0)
    precio = float(datos.get("precio_eur") or 0.0)
    lineas = [
        f"{datos.get('fichero', '?')}",
        f"{numero(minutos)} min · {euros(precio)}",
    ]
    extra = []
    if datos.get("palabras"):
        extra.append(f"{int(datos['palabras']):,} palabras".replace(",", "."))
    if datos.get("veces_tiempo_real"):
        extra.append(f"{numero(datos['veces_tiempo_real'])}x tiempo real")
    if coste_luz is not None:
        extra.append(f"luz {euros(coste_luz)}")
    if extra:
        lineas.append(" · ".join(extra))
    entregados = [f"{Path(datos.get('fichero', 'x')).stem}.txt"]
    if datos.get("segmentos"):
        entregados.append(f"{Path(datos.get('fichero', 'x')).stem}.srt")
    entregados.append(f"{Path(datos.get('fichero', 'x')).stem}.json")
    lineas.append("entrega: " + ", ".join(entregados))
    lineas.append(f"carpeta: {Path(salida).resolve()}")
    return "\n".join(lineas)


def toca_avisar(minutos: float, desde: float, sin_aviso: bool = False) -> bool:
    """¿Este trabajo es lo bastante largo para molestar al movil?"""
    if sin_aviso:
        return False
    return float(minutos) >= float(desde)


def avisar_telegram(titulo: str, texto: str) -> bool:
    """Manda el aviso con tools/avisos.py. Nunca rompe el trabajo.

    Si Telegram no esta configurado (o falla la red), devuelve False y el
    trabajo sigue: el aviso es comodidad, no parte de la entrega.
    """
    herramientas = str(RAIZ / "tools")
    if herramientas not in sys.path:
        sys.path.insert(0, herramientas)
    try:
        import avisos  # noqa: PLC0415  (se importa aqui: no siempre hace falta)
        return bool(avisos.enviar(titulo, texto))
    except Exception as e:
        print(f"[aviso] no se pudo usar Telegram: {type(e).__name__}: {e}",
              file=sys.stderr)
        return False


def _anotar_cobro(datos: dict, salida: Path, cliente: str,
                  importe: float | None = None) -> None:
    """Apunta el trabajo como ingreso real en el ledger de KUKIA.

    No emite KIA: el Core guarda una fila en `ingresos` y un asiento de 0 KIA
    con el importe. Si el Core esta apagado, el trabajo ya esta entregado y no
    se pierde nada: queda el .json con el precio y se puede anotar luego con
    `python servicio/cobros.py --trabajo <json> --cliente "..."`.
    """
    precio = float(datos.get("precio_eur") or 0.0) if importe is None else float(importe)
    if precio <= 0:
        print("  cobro: 0 EUR (prueba gratis), no se anota nada en el ledger")
        return
    try:
        import cobros  # esta en la misma carpeta
        r = cobros.anotar(
            datos.get("fichero") or "transcripcion", cliente,
            datos.get("minutos") or 0.0, precio,
            detalle={"fichero": datos.get("fichero"), "palabras": datos.get("palabras"),
                     "idioma": datos.get("idioma_detectado"), "motor": datos.get("motor"),
                     "carpeta": str(Path(salida).resolve()),
                     "importe_calculado": datos.get("precio_eur")})
        print("  cobro: " + ("[ok] " if r["ok"] else "[aviso] ") + r["mensaje"])
        if not r["ok"] and not r.get("ya_estaba"):
            print(f"         para anotarlo luego: python servicio/cobros.py "
                  f"--trabajo \"{Path(salida).resolve() / (Path(datos.get('fichero', 'x')).stem + '.json')}\" "
                  f"--cliente \"{cliente}\"")
    except Exception as e:
        print(f"  cobro: [aviso] no se pudo anotar ({type(e).__name__}: {e})")


def srt_hora(seg: float) -> str:
    """00:01:23,456 como pide el formato SRT."""
    if seg < 0:
        seg = 0.0
    ms = int(round(seg * 1000))
    h, ms = divmod(ms, 3600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _campos(seg) -> tuple:
    """faster-whisper devuelve objetos, whisper devuelve dicts: se aceptan los dos."""
    if isinstance(seg, dict):
        return float(seg["start"]), float(seg["end"]), str(seg.get("text", ""))
    return float(seg.start), float(seg.end), str(getattr(seg, "text", ""))


def a_srt(segmentos) -> str:
    lineas = []
    for i, seg in enumerate(segmentos, 1):
        ini, fin, texto = _campos(seg)
        texto = " ".join(texto.split())
        if not texto:
            continue
        lineas.append(str(i))
        lineas.append(f"{srt_hora(ini)} --> {srt_hora(fin)}")
        lineas.append(texto)
        lineas.append("")
    return "\n".join(lineas)


# ------------------------------------------------------------------ motores

def cargar_motor(modelo: str, dispositivo: str):
    """Devuelve (motor, manejador, nombre). faster-whisper primero."""
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        WhisperModel = None

    if WhisperModel is not None:
        ruta = MODELOS / f"fw-{modelo}"
        objetivo = str(ruta) if (ruta / "model.bin").is_file() else modelo
        if dispositivo in ("auto", "cuda"):
            try:
                m = WhisperModel(objetivo, device="cuda", compute_type="float16",
                                 download_root=str(MODELOS))
                return "faster-whisper/cuda", m, objetivo
            except Exception as e:
                print(f"[aviso] CUDA no va ({type(e).__name__}: {str(e)[:120]}); "
                      f"sigo en CPU", file=sys.stderr)
        m = WhisperModel(objetivo, device="cpu", compute_type="int8",
                         download_root=str(MODELOS))
        return "faster-whisper/cpu", m, objetivo

    import whisper  # type: ignore
    MODELOS.mkdir(parents=True, exist_ok=True)
    m = whisper.load_model(modelo, download_root=str(MODELOS))
    return "whisper-pytorch", m, modelo


def transcribir_con(motor: str, manejador, ruta: Path, idioma, beam: int,
                    vad: bool) -> dict:
    """Transcribe y devuelve texto, segmentos e idioma detectado."""
    t0 = time.time()
    if motor.startswith("faster-whisper"):
        segs, info = manejador.transcribe(
            str(ruta), language=idioma, beam_size=beam, vad_filter=vad,
            condition_on_previous_text=False, word_timestamps=False,
        )
        segmentos = []
        for s in segs:                      # el generador se consume aqui
            segmentos.append({"start": float(s.start), "end": float(s.end),
                              "text": s.text})
        texto = " ".join(" ".join(s["text"].split()) for s in segmentos)
        return {"texto": texto.strip(), "segmentos": segmentos,
                "idioma": getattr(info, "language", idioma),
                "duracion": float(getattr(info, "duration", 0.0)),
                "proceso": time.time() - t0}

    opciones = {"verbose": False, "fp16": True, "condition_on_previous_text": False,
                "word_timestamps": False}
    if idioma:
        opciones["language"] = idioma
    r = manejador.transcribe(str(ruta), **opciones)
    return {"texto": " ".join(str(r.get("text", "")).split()),
            "segmentos": r.get("segments", []),
            "idioma": r.get("language"), "duracion": 0.0,
            "proceso": time.time() - t0}


# ------------------------------------------------------------------ acciones

def hacer_presupuesto(ficheros, precio_min: float, minimo: float) -> int:
    total = 0.0
    print(f"{'fichero':<44} {'minutos':>8} {'precio':>9} {'coste':>8} {'margen':>9}")
    print("-" * 82)
    for f in ficheros:
        ruta = Path(f)
        if not ruta.is_file():
            print(f"{ruta.name:<44}  (no existe)")
            return 2
        seg = duracion_segundos(ruta)
        d = precio(seg / 60.0, precio_min, minimo)
        total += d["precio"]
        print(f"{ruta.name[:44]:<44} {d['minutos']:>8.2f} {d['precio']:>8.2f} "
              f"{d['coste_luz']:>8.3f} {d['margen']:>8.2f}")
    print("-" * 82)
    print(f"{'TOTAL':<44} {'':>8} {total:>8.2f}")
    print(f"\nA {precio_min:.2f} EUR/min. El mercado cobra 0,10-0,30: esto es barato "
          f"y con margen.")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Transcribe audio/video a texto y subtitulos, y calcula el precio.")
    ap.add_argument("ficheros", nargs="+", help="audio o video a transcribir")
    ap.add_argument("--presupuesto", action="store_true",
                    help="solo calcular duracion y precio, sin GPU")
    ap.add_argument("--modelo", default="small",
                    help="tiny, base, small (por defecto), medium, large-v3")
    ap.add_argument("--idioma", default="es",
                    help="idioma del audio (es, en, ...); vacio = detectar")
    ap.add_argument("--salida", default=None, help="carpeta de entrega")
    ap.add_argument("--precio-min", type=float, default=PRECIO_MIN,
                    help="EUR por minuto de audio")
    ap.add_argument("--minimo", type=float, default=MINIMO_MIN,
                    help="minutos minimos que se cobran")
    ap.add_argument("--formato", choices=("todo", "txt", "srt"), default="todo")
    ap.add_argument("--beam", type=int, default=5,
                    help="5 = calidad normal; 1 = mas rapido y algo peor")
    ap.add_argument("--sin-vad", action="store_true",
                    help="no saltar silencios (por defecto se saltan)")
    ap.add_argument("--dispositivo", choices=("auto", "cuda", "cpu"), default="auto")
    ap.add_argument("--avisar-desde", type=float, default=LARGO_MIN,
                    help="minutos de audio a partir de los cuales se avisa por "
                         "Telegram al terminar (por defecto 20; 0 = avisar siempre)")
    ap.add_argument("--sin-aviso", action="store_true",
                    help="no mandar ningun aviso por Telegram")
    ap.add_argument("--cobrado-a", default=None,
                    help="nombre del cliente que paga: el trabajo se anota en el "
                         "ledger de KUKIA como ingreso real en euros")
    ap.add_argument("--importe", type=float, default=None,
                    help="euros cobrados, si no es el precio calculado "
                         "(descuento, precio pactado o prueba gratis)")
    args = ap.parse_args(argv)

    for f in args.ficheros:
        if Path(f).suffix.lower() not in AUDIO_EXT:
            print(f"[aviso] {f}: extension rara, se intenta igual", file=sys.stderr)

    if args.presupuesto:
        return hacer_presupuesto(args.ficheros, args.precio_min, args.minimo)

    try:
        import faster_whisper  # noqa: F401
    except ImportError:
        try:
            import whisper  # noqa: F401
        except ImportError:
            relanzar_con_whisper()
            print("error: no hay faster-whisper ni whisper en este Python.\n"
                  f"  pon KUKIA_WHISPER_PY a uno que los tenga (probado: {PY_WHISPER})",
                  file=sys.stderr)
            return 3

    motor, manejador, objetivo = cargar_motor(args.modelo, args.dispositivo)
    print(f"motor: {motor}  modelo: {objetivo}", file=sys.stderr)

    salida = Path(args.salida) if args.salida else ENTREGAS / time.strftime("%Y%m%d")
    idioma = args.idioma or None
    fallos = 0
    for f in args.ficheros:
        ruta = Path(f)
        if not ruta.is_file():
            print(f"[error] no existe: {ruta}", file=sys.stderr)
            fallos += 1
            continue
        try:
            seg_total = duracion_segundos(ruta)
            r = transcribir_con(motor, manejador, ruta, idioma, args.beam,
                                not args.sin_vad)
        except Exception as e:
            print(f"[error] {ruta.name}: {type(e).__name__}: {e}", file=sys.stderr)
            fallos += 1
            continue

        salida.mkdir(parents=True, exist_ok=True)
        stem = ruta.stem
        (salida / f"{stem}.txt").write_text(r["texto"] + "\n", encoding="utf-8")
        if args.formato in ("srt", "todo"):
            (salida / f"{stem}.srt").write_text(a_srt(r["segmentos"]), encoding="utf-8")

        d = precio(seg_total / 60.0, args.precio_min, args.minimo)
        datos = {
            "fichero": ruta.name,
            "idioma_detectado": r["idioma"],
            "duracion_s": round(seg_total, 2),
            "minutos": d["minutos"],
            "motor": motor,
            "modelo": objetivo,
            "segmentos": len(r["segmentos"]),
            "segundos_proceso": round(r["proceso"], 1),
            "veces_tiempo_real": (round(seg_total / r["proceso"], 1)
                                  if r["proceso"] > 0 else None),
            "caracteres": len(r["texto"]),
            "palabras": len(r["texto"].split()),
            "precio_eur": d["precio"],
            "coste_luz_eur": d["coste_luz"],
            "margen_eur": d["margen"],
            "fecha": time.strftime("%Y-%m-%d %H:%M"),
        }
        (salida / f"{stem}.json").write_text(
            json.dumps(datos, ensure_ascii=False, indent=2), encoding="utf-8")

        print(f"\n{datos['fichero']}  ({d['minutos']:.2f} min, "
              f"idioma {datos['idioma_detectado']})")
        print(f"  {datos['palabras']} palabras, {datos['segundos_proceso']} s "
              f"({datos['veces_tiempo_real']}x tiempo real)")
        print(f"  precio {datos['precio_eur']:.2f} EUR | luz "
              f"{datos['coste_luz_eur']:.3f} EUR | margen {datos['margen_eur']:.2f} EUR")
        print(f"  entrega en {salida}")

        # Cobrado: queda anotado en el ledger de KUKIA (ingreso real en euros).
        if args.cobrado_a:
            _anotar_cobro(datos, salida, args.cobrado_a, args.importe)

        # Trabajo largo -> aviso al movil, con el precio y donde esta la entrega.
        if toca_avisar(datos["minutos"], args.avisar_desde, args.sin_aviso):
            avisado = avisar_telegram(
                f"KUKIA · transcripción terminada · {euros(datos['precio_eur'])}",
                mensaje_aviso(datos, salida, datos.get("coste_luz_eur")))
            if avisado:
                print("  aviso al movil: enviado")
            else:
                print("  aviso al movil: NO enviado (¿Telegram sin configurar? "
                      "python tools/avisos.py --configurar)")

    if not fallos:
        print(f"\nlisto. {len(args.ficheros)} fichero(s) en {salida}")
    return 1 if fallos else 0


if __name__ == "__main__":
    raise SystemExit(main())
