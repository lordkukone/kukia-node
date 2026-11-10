# CEREBRO KUKIA — Fase 1 (memoria semantica local)

Paquete aislado, como `meta/`: **lee** el proyecto, no lo escribe. No importa el
Core, no toca el ledger ni las claves, no habla con ninguna nube. Los embeddings
salen de Ollama en `127.0.0.1:11434`.

## Uso

```bash
python -m cerebro.indice --indexar      # incremental: solo lo que cambio
python -m cerebro.indice --estado       # cuantas fuentes y fragmentos hay
python -m cerebro.buscar "como se ancla el ledger" -k 8
python -m cerebro.evaluar --detalle     # recall@8 contra el banco
python cerebro/pruebas/test_cerebro.py  # pruebas sin Ollama ni red
```

## Como funciona

| Pieza | Que hace |
|---|---|
| `ingesta.py` | Recorre las raices, aplica la lista negra, deduplica por SHA-256 y trocea en fragmentos de ~400 tokens con 60 de solape |
| `indice.py` | Guarda texto y vectores en `estado/cerebro.db` (SQLite). Indexacion incremental por hash de fichero y cache por hash de fragmento |
| `buscar.py` | Coseno por fuerza bruta en numpy, con cita `ruta:linea_ini-linea_fin` |
| `evaluar.py` | recall@8 y MRR sobre `bancos/preguntas.json`; falla si baja del umbral |

## Que NO entra nunca

La lista negra esta en `config.py` y se comprueba en un solo sitio:

- nombres exactos: `clave.txt`, `admin_token.txt`, `sello_key.txt`, `id_ed25519`, `.env`, `.dev.vars`;
- patrones: `recuperacion`, `wallet`, `mnemonic`, `secret`, `*.key`, `*.pem`, `*.db`, `*.zip`, `*.pdf`, imagenes, modelos;
- carpetas: `backups`, `medidas`, `_mezcla_*`, `informes_mainnet_20261029`, `anclas`, `logs`, `modelos`, `.git`.

`clave.txt` esta por nombre exacto a proposito: son 64 bytes en `publico/` que
ningun patron general atrapaba (Fase 0, hallazgo F0-1).

## Medido (10-11-2026)

- `nomic-embed-text`: 768 dimensiones, 38 ms por fragmento en caliente.
- Corpus del proyecto: ~1.000 ficheros, 6,6 MB, ~2,7 M tokens, ~7.900 fragmentos.
- Indice resultante: ~24 MB de vectores + el texto.

## Criterios de aceptacion de la Fase 1

1. `cerebro.buscar` devuelve 8 fragmentos con cita.
2. recall@8 >= 0,80 sobre 20 preguntas con respuesta conocida.
3. 0 ficheros secretos indexados (probado en `pruebas/test_cerebro.py`).
4. Reindexar sin cambios no vuelve a embeber nada.
5. Nada escrito fuera de `cerebro/`.

## Siguiente (Fase 2)

Memoria episodica y de errores: sintoma -> causa -> arreglo -> prueba, importando
`TRASPASO`, `SESION`, `AUDITORIA_*` y `DIARIO` como fuente citada, sin reescribirlos.
