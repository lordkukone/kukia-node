# KUKIA Core en un servidor Linux.
#
# El Core NO necesita GPU: verifica recalculando en CPU. Por eso se instala
# torch CPU-only (~200 MB) en vez de la rueda con CUDA (~2,5 GB).
#
# La base del ledger y las claves NO van dentro de la imagen: se montan como
# volumen (ver docker-compose.yml). Si se hornea la base dentro, cada despliegue
# pisaria el ledger vivo.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Solo lo imprescindible para compilar si alguna rueda no viene precompilada.
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential \
 && rm -rf /var/lib/apt/lists/*

# torch desde el indice CPU: la rueda normal arrastra CUDA y multiplica la imagen
# por diez sin ninguna ventaja, porque el Core no usa la GPU.
COPY requirements.txt .
RUN pip install --index-url https://download.pytorch.org/whl/cpu -r requirements.txt

COPY coordinator/ coordinator/
COPY ledger/ ledger/
COPY tools/ tools/
COPY worker/ worker/
COPY validator/ validator/
COPY publico/ publico/

# El Core se inserta la raiz en sys.path el solo (kukia_core.py, linea 25),
# asi que arranca con el modulo por nombre sin tocar PYTHONPATH.
ENV PYTHONPATH=/app

# Se ejecuta sin privilegios: si alguien entra por un fallo del Core, no es root.
RUN useradd --create-home --uid 10001 kukia \
 && mkdir -p /app/coordinator/ledger \
 && chown -R kukia:kukia /app
USER kukia

EXPOSE 8300

# /version es barato y no toca la base de datos.
HEALTHCHECK --interval=30s --timeout=6s --start-period=120s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8300/version', timeout=5).status==200 else 1)"

# --proxy-headers + forwarded-allow-ips: detras de Caddy el Core tiene que ver
# la IP real del que llama, o todos los nodos parecen el mismo.
CMD ["uvicorn", "kukia_core:app", \
     "--app-dir", "/app/coordinator", \
     "--host", "0.0.0.0", \
     "--port", "8300", \
     "--proxy-headers", \
     "--forwarded-allow-ips", "*", \
     "--no-server-header"]
