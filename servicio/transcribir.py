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
audio y unos 3 centimos de luz.

Precio de referencia del mercado: 0,10 - 0,30 EUR por minuto de audio.
"""

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

    if not fallos:
        print(f"\nlisto. {len(args.ficheros)} fichero(s) en {salida}")
    return 1 if fallos else 0


if __name__ == "__main__":
    raise SystemExit(main())
