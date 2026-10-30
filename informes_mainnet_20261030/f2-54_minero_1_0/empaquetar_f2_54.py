"""Construye el paquete distribuible del minero 1.0 de forma reproducible.

- Solo entra lo necesario para minar: worker, billetera, utilidades y guiones.
- NUNCA entra `ledger/wallet/` (clave privada), `identidad_minero.json`, logs,
  bases de datos ni respaldos.
- El ZIP es reproducible: entradas ordenadas, fecha fija y permisos fijos. Dos
  ejecuciones del mismo codigo dan el mismo SHA-256.
- Deja `dist/SHA256SUMS.txt` con el hash de cada archivo y del propio ZIP.

    python empaquetar_f2_54.py
"""
import hashlib
import json
import zipfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
AQUI = Path(__file__).resolve().parent
DIST = AQUI / "dist"
VERSION = "1.0"
FECHA_ZIP = (2026, 10, 30, 0, 0, 0)

INCLUIR = [
    ("worker/kukia_worker.py", "worker/kukia_worker.py"),
    ("ledger/kukia_wallet.py", "ledger/kukia_wallet.py"),
    ("ledger/sellos.py", "ledger/sellos.py"),
    ("ledger/wordlist_es.txt", "ledger/wordlist_es.txt"),
    ("tools/core_destino.py", "tools/core_destino.py"),
    ("MINERO_1.0_LEEME.md", "LEEME.md"),
    ("GUIA_MINERO.md", "GUIA_MINERO.md"),
    ("CHANGELOG_MINERO.md", "CHANGELOG_MINERO.md"),
]

PROHIBIDO = ("wallet/", "identidad_minero.json", ".log", ".db", "recuperacion",
             "sello_key", "admin_token", ".pyc", "__pycache__")

ARRANCAR = """@echo off
rem Arranca SOLO el minero. Cerrar esta ventana lo detiene.
chcp 65001 >nul 2>&1
setlocal enabledelayedexpansion
title KUKIA - minero 1.0
cd /d "%~dp0"

if exist "minero.env" (
  for /f "usebackq eol=# tokens=1,* delims==" %%A in ("minero.env") do (
    if not "%%A"=="" set "%%A=%%B"
  )
)
if not defined KUKIA_CORE set "KUKIA_CORE=http://127.0.0.1:8300"

python --version >nul 2>&1
if errorlevel 1 (
  echo [ERROR] No hay Python en el PATH. Instala Python 3.10 o superior.
  pause
  exit /b 1
)
python -c "import requests" >nul 2>&1
if errorlevel 1 (
  echo [..] Instalando requests...
  python -m pip install --quiet requests
)
python -c "import torch" >nul 2>&1
if errorlevel 1 (
  echo [AVISO] Sin PyTorch no se puede calcular ninguna tarea.
  echo         GPU NVIDIA: python -m pip install torch --index-url https://download.pytorch.org/whl/cu121
  echo         Solo CPU:    python -m pip install torch
)

echo Core: %KUKIA_CORE%
cd worker
python -u kukia_worker.py %1
set "CODIGO=%ERRORLEVEL%"
cd ..
echo.
echo [minero detenido] codigo %CODIGO%
pause
"""

ENV_EJEMPLO = """# Copia este fichero como minero.env y ajusta lo que necesites.
# No pongas credenciales: el minero no las usa para minar.
KUKIA_CORE=http://127.0.0.1:8300
# KUKIA_EN_VUELO=4
# KUKIA_HILOS_GPU=1
# KUKIA_COLA_ESPERA=30
# KUKIA_MINERO_LOG_BYTES=5242880
# KUKIA_ESTERIL_SEG=180
# KUKIA_USAR_CPU=1
"""


def sha256(ruta: Path) -> str:
    h = hashlib.sha256()
    with ruta.open("rb") as f:
        for trozo in iter(lambda: f.read(1 << 20), b""):
            h.update(trozo)
    return h.hexdigest()


def prohibido(nombre: str) -> bool:
    n = nombre.replace("\\", "/").lower()
    # `.env` de verdad si; `minero.env.example` es una plantilla y va en el paquete.
    if n.endswith(".env") or "/.env" in n:
        return True
    return any(p in n for p in PROHIBIDO)


def main() -> int:
    DIST.mkdir(parents=True, exist_ok=True)
    # Limpieza: solo los artefactos de este script.
    for viejo in list(DIST.glob("kukia-miner-*.zip")) + [DIST / "SHA256SUMS.txt"]:
        if viejo.exists():
            viejo.unlink()

    faltan = [o for o, _ in INCLUIR if not (RAIZ / o).exists()]
    if faltan:
        print(f"FALTA en el repo: {faltan}")
        return 1

    extra = {
        "ARRANCAR_MINERO.cmd": ARRANCAR.replace("\n", "\r\n"),
        "minero.env.example": ENV_EJEMPLO.replace("\n", "\r\n"),
    }
    version = {
        "nombre": "KUKIA Miner",
        "version": VERSION,
        "fecha": "2026-10-30",
        "rama": "f2-53-minero-estable",
        "worker_sha256": sha256(RAIZ / "worker" / "kukia_worker.py"),
        "estado": "TESTNET",
        "requisitos": ["Windows 10/11", "Python 3.10+", "requests",
                       "PyTorch (CUDA opcional)"],
        "no_incluye": ["clave privada", "identidad del nodo", "logs", "bases de datos"],
    }
    # VERSION.json y SHA256SUMS se generan despues de conocer el contenido; se
    # calculan sobre los ficheros que entran y se meten dentro del ZIP.
    entradas = []
    for origen, destino in INCLUIR:
        entradas.append((origen, destino, (RAIZ / origen).read_bytes()))
    for nombre, texto in extra.items():
        entradas.append((f"(generado) {nombre}", nombre, texto.encode("utf-8")))
    entradas.append(("(generado) VERSION.json", "VERSION.json",
                     (json.dumps(version, indent=2, ensure_ascii=False) + "\n").encode("utf-8")))

    sumas = "".join(f"{hashlib.sha256(datos).hexdigest()}  {nombre}\n"
                    for _, nombre, datos in sorted(entradas, key=lambda e: e[1]))
    entradas.append(("(generado) SHA256SUMS.txt", "SHA256SUMS.txt", sumas.encode("utf-8")))

    for _, nombre, _ in entradas:
        if prohibido(nombre):
            print(f"ABORTADO: '{nombre}' no debe ir en el paquete")
            return 1

    zip_path = DIST / f"kukia-miner-{VERSION}.zip"
    # Reproducible: nombre ordenado, fecha fija, permisos fijos, sin metadatos
    # del sistema de ficheros.
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for _, nombre, datos in sorted(entradas, key=lambda e: e[1]):
            info = zipfile.ZipInfo(f"kukia-miner-{VERSION}/{nombre}", date_time=FECHA_ZIP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            info.create_system = 3
            z.writestr(info, datos)

    resumen = sumas + f"{sha256(zip_path)}  {zip_path.name}\n"
    (DIST / "SHA256SUMS.txt").write_text(resumen, encoding="utf-8")
    print(f"{zip_path.name}: {zip_path.stat().st_size} bytes")
    print(f"sha256 {sha256(zip_path)}")
    print(f"{len(entradas)} ficheros dentro")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
