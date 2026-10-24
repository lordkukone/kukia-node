#!/usr/bin/env bash
# Sube el ledger y las claves del Core a un servidor, verificando el hash.
#
#   ./despliegue/subir_al_servidor.sh usuario@servidor /ruta/kukia
#
# No borra nada en origen. Si el hash remoto no coincide, avisa y no sigue.
#
# ANTES:  python despliegue/copiar_ledger_seguro.py
set -euo pipefail

DESTINO_HOST="${1:?falta el destino: usuario@servidor}"
DESTINO_RUTA="${2:-/srv/kukia}"
LOCAL="$(cd "$(dirname "$0")" && pwd)"

LEDGER="$LOCAL/datos/kukia.db"
SELLO="$LOCAL/../coordinator/sello_key.txt"
ADMIN="$LOCAL/../coordinator/admin_token.txt"

for f in "$LEDGER" "$SELLO" "$ADMIN"; do
  if [ ! -f "$f" ]; then
    echo "[ERROR] falta $f"
    [ "$f" = "$LEDGER" ] && echo "        ejecuta antes: python despliegue/copiar_ledger_seguro.py"
    exit 1
  fi
done

echo "== 1. copia de seguridad del ledger en el servidor (si ya habia uno) =="
ssh "$DESTINO_HOST" "mkdir -p '$DESTINO_RUTA/datos' '$DESTINO_RUTA/claves' && \
  if [ -f '$DESTINO_RUTA/datos/kukia.db' ]; then \
     cp '$DESTINO_RUTA/datos/kukia.db' '$DESTINO_RUTA/datos/kukia.db.bak.\$(date +%Y%m%d_%H%M%S)' && \
     echo '   guardada copia del ledger anterior'; \
  else echo '   no habia ledger previo'; fi"

echo "== 2. subiendo ledger ($(du -h "$LEDGER" | cut -f1)) =="
scp "$LEDGER" "$DESTINO_HOST:$DESTINO_RUTA/datos/kukia.db"

echo "== 3. subiendo claves (permisos 600 en el servidor) =="
scp "$SELLO" "$DESTINO_HOST:$DESTINO_RUTA/claves/sello_key.txt"
scp "$ADMIN" "$DESTINO_HOST:$DESTINO_RUTA/claves/admin_token.txt"
ssh "$DESTINO_HOST" "chmod 600 '$DESTINO_RUTA/claves/'*.txt"

echo "== 4. verificando el hash del ledger EN EL SERVIDOR =="
LOCAL_HASH=$(sha256sum "$LEDGER" | cut -d' ' -f1)
REMOTO_HASH=$(ssh "$DESTINO_HOST" "sha256sum '$DESTINO_RUTA/datos/kukia.db' | cut -d' ' -f1")
echo "   local : $LOCAL_HASH"
echo "   remoto: $REMOTO_HASH"
if [ "$LOCAL_HASH" != "$REMOTO_HASH" ]; then
  echo "[ERROR] el hash NO coincide. NO arranques el Core en el servidor: el ledger llegó corrupto."
  exit 1
fi
echo "   OK: el ledger del servidor es identico al de tu PC."

cat <<FIN

== Hecho ==
En el servidor:
  cd $DESTINO_RUTA
  nano .env                 # pon el dominio y KUKIA_ORIGENES
  nano Caddyfile            # cambia TU_DOMINIO
  docker compose up -d
  docker compose logs -f core

Y cuando el Core del servidor responda, apaga el de tu PC para que no haya dos
Cores escribiendo el mismo historial (cada uno con su base, pero dos historiales
distintos = dos redes distintas).
FIN
