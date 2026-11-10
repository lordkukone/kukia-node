"""Configuracion del cerebro: que se indexa, que no se indexa NUNCA y con que modelo.

La lista negra vive en un solo sitio. Si algo no esta permitido aqui, la ingesta
no lo toca: no hay filtros repartidos por el codigo.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

PAQUETE = Path(__file__).resolve().parent
RAIZ = PAQUETE.parent
ESTADO = PAQUETE / "estado"
DB = Path(os.environ.get("CEREBRO_DB", str(ESTADO / "cerebro.db")))

# Raices que se recorren. Si una no existe (por ejemplo I: desconectada), se salta.
RAICES = [RAIZ, Path("I:/CEREBRO")]

EXTENSIONES = {".md", ".txt", ".py", ".json"}

# Carpetas que no se recorren nunca: peso muerto, copias o datos, no conocimiento.
DIRS_PROHIBIDOS = {
    ".git", "__pycache__", "node_modules", ".wrangler", ".venv", "venv",
    "backups", "medidas", "capturas", "capturas_local", "sitio_publicado",
    "migracion_entera", "anclas", "logs", "modelos", "entregas",
    "pruebas_regresion", "informes_mainnet_20261029", "respaldos_backup_20261031",
    "estado", "cerebro",
}
PREFIJOS_PROHIBIDOS = ("_backup", "_mezcla", "_auditoria", "_autostart", "_dash")

# Nombres exactos (en minusculas) que jamas entran.
# `clave.txt` esta a proposito: 64 bytes en publico/ que ningun patron general
# atrapaba (Fase 0, hallazgo F0-1).
NOMBRES_PROHIBIDOS = {
    "clave.txt", "admin_token.txt", "sello_key.txt", "id_ed25519",
    ".env", ".dev.vars", "tokenizer.json",
}

# Patrones sobre el nombre del fichero.
PATRONES_PROHIBIDOS = [re.compile(p, re.IGNORECASE) for p in (
    r"recuperacion",
    r"mnemonic|mnemonico",
    r"wallet",
    r"secret",
    r"^\.env",
    r"\.pem$",
    r"\.key$",
    r"\.db($|-)",
    r"\.sqlite",
    r"\.zip$",
    r"\.pdf$",
    r"\.(png|jpe?g|gif|webp|mp4|pt|gguf|safetensors|onnx|ots)$",
)]

# Segmentos de ruta (carpeta o fichero) que prohiben el fichero entero.
# `ledger/wallet/*.json` guarda la clave privada y el nombre del fichero no lo
# dice: lo dice la carpeta. Sin esto se indexaron 41 billeteras el 10-11-2026.
SEGMENTOS_PROHIBIDOS = {"wallet", "wallets", "recuperacion", "recuperaciones"}

# Patrones sobre cualquier segmento de la ruta relativa.
PATRONES_RUTA = [re.compile(p, re.IGNORECASE) for p in (
    r"wallet", r"recuperacion", r"mnemonic|mnemonico", r"clave_privada",
)]

MAX_BYTES = 3_000_000

# Ollama y modelo: medidos en la Fase 0 (768 dims, 38 ms por fragmento).
OLLAMA = os.environ.get("CEREBRO_OLLAMA", "http://127.0.0.1:11434")
MODELO = os.environ.get("CEREBRO_MODELO", "nomic-embed-text")
LOTE = 8

# Troceado: 400 tokens con 60 de solape (el modelo admite 2.048 de contexto).
TOKENS_FRAGMENTO = 400
TOKENS_SOLAPE = 60
MAX_CHARS_LINEA = 2000

# Umbral del banco de evaluacion (criterio de aceptacion de la Fase 1).
UMBRAL_RECALL = 0.80
