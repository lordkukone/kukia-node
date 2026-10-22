# Auditoría de seguridad — minero y moneda KIA

**Fecha**: 19-10-2026 · **Tipo**: interna (no independiente) · **Alcance**:
`worker/kukia_worker.py`, `ledger/kukia_wallet.py`, la parte monetaria de
`coordinator/kukia_core.py` (emisión, pagos, penalizaciones, transferencias, ledger) y su
exposición pública a través del relé.

Todo lo que se afirma se comprobó contra el sistema real y la base `coordinator/ledger/kukia.db`
en ese momento. Reproducible con `python tools/verificar_moneda.py`.

## Resumen

La parte criptográfica está bien resuelta: firmas Ed25519 obligatorias, nonces contra
repetición, pagos atómicos bajo lock, tope de 100M respetado, cadena de hashes intacta y
saldos que cuadran al céntimo con el suministro. **El problema grave no es un ataque: es que
la moneda no se puede gastar.** Solo existe un endpoint de transferencia y exige que el emisor
sea un **nodo**; las direcciones de billetera (`KUK...`) no son nodos, así que todo lo que
llega a una billetera queda congelado. Hoy son **15.045.568,84 KIA**, el 10 % del suministro
total.

| # | Severidad | Hallazgo | Estado |
|---|---|---|---|
| 1 | **P0** | Las billeteras no pueden enviar KIA: 15.045.568,84 KIA sin ruta de gasto | Abierto |
| 2 | P1 | Tier de pago y TOPS autodeclarados sin atestación (hasta 1,6× por tarea) | Abierto |
| 3 | P1 | La contraseña de la billetera del minero se descarta: recuperación web imposible | Abierto |
| 4 | P1 | Génesis y migración de 15M sin encadenar (hash vacío) | Abierto |
| 5 | P2 | La emisión se acelera con tiers inflados y sybil (el tope de 100M sí se respeta) | Abierto |
| 6 | P2 | Se anuncia “quema de comisiones 10-15 %” y no existe ninguna comisión | Abierto (web) |
| 7 | P2 | DoS local con tareas de difusión en el límite (n=4096, pasos=20000) | Abierto |
| 8 | P3 | Residuo de coma flotante en saldos (−4,6e-13) | Aceptado |
| 9 | P3 | Destino de transferencia sin validar: se puede quemar enviando a basura | Abierto |
| 10 | P3 | El relé publica todos los saldos e ids de nodo | Aceptado (por diseño) |

## P0 · La moneda no tiene salida

- Único endpoint de gasto: `POST /transferir`.
- Exige `de_nodo` registrado en `nodos` con `clave_publica` y firma Ed25519
  (`kukia_core.py`, `transferir()`).
- Las direcciones de billetera (`KUK + SHA256(pubkey)[:38]`) **no están en `nodos`**.

Prueba en vivo:

```bash
curl -X POST http://127.0.0.1:8300/transferir -H "content-type: application/json" \
  -d '{"de_nodo":"KUK77D72508BD940C9BA969B0402A3CF1F9679BE3",
       "a_direccion":"KUK90BA61EDA315C341434AF93CC4D82D80D9DD21",
       "monto":1,"nonce":"audit-test-1","firma":"00"}'
# HTTP 401 {"detail":"emisor sin clave registrada"}
```

- Fundador: `KUK9E8B44CD7…` = **15.000.005,5613 KIA** (migrado del génesis el 14-10).
- Minero: `KUK90BA61…` = **45.563,2826 KIA**.
- Total atrapado: **15.045.568,84 KIA** (10 % del suministro). El minero no lo nota porque
  solo cobra, pero el fundador no puede mover ni un KIA.

**Arreglo recomendado** (sin custodio, sin tabla nueva): aceptar origen billetera en
`/transferir` cuando venga `clave_publica` y la dirección derivada de esa clave coincida con
`de_nodo`; verificar la misma firma `transferir:de:a:monto:nonce`. La billetera web ya tiene
`nacl` y la clave en memoria, así que el siguiente paso es una pestaña “Enviar”.
Alternativa peor: registrar cada billetera como nodo (ensucia la red y mezcla conceptos).

## P1 · Tier y TOPS autodeclarados

`registrar()` acepta `vram_gb` y `tops` del cliente, solo acotados (512 GB, 100.000 TOPS):

- `tier(vram)` paga **1,6×** por tarea con `vram_gb=24`, aunque la GPU sea de 4 GB y el
  resultado se verifique igual (la verificación no mira hardware).
- `tops` entra en `dificultad()`: `factor = sqrt(tops_red/1747)` con techo 4. Inflar `tops`
  agranda las tareas de toda la red (`n` hasta 4096) → más CPU de verificación en el Core.
- El registro es abierto y sin prueba de trabajo (30/min por IP y todo llega desde 127.0.0.1
  en local): sybil trivial cuando la red se abra.

**Recomendación**: en fase de pruebas, tier plano (1,0) para todos y `tops_red` calculado con
una referencia fija; para abrir la red, atestación mínima (benchmark verificado por el Core) o
un tier que no multiplique el pago.

## P1 · Recuperación de la billetera del minero

`crear_billetera()` genera contraseña aleatoria y la devuelve; el worker la descarta
(`w = w[0]`). El JSON guarda palabras y clave privada, pero sin contraseña la recuperación web
(contraseña + 20 palabras) es imposible. Si se pierde el JSON, la billetera no se rehace.

**Recomendación**: aceptar `KUKIA_WALLET_PASS`, guardar una copia cifrada, o añadir
“importar clave privada” a la web. Y documentar que el respaldo real es el JSON.

## P1 · Génesis y migración fuera de la cadena

La cadena de hashes empieza en el asiento **176**. Los asientos **5** (génesis 15M), **12** y
**13** (migración −15M/+15M) tienen `hash` vacío: son justo los que más importan y son los
únicos que se podrían alterar sin que `/ledger/verificar` lo note.

**Recomendación**: publicar un ancla firmada (hash del asiento 176 + fecha) y re-encadenar
hacia atrás con un asiento de “anclaje”; o firmar un snapshot del ledger y publicarlo en la web.

## P2 · Emisión acelerable

`recompensa_pago(tier)` calcula `objetivo = restante / tareas_restantes` y paga
`min(base × tier, restante)`. El calendario a 2150 asume tier 1,0. Con nodos que declaren 24 GB
(1,6×) o muchos nodos sybil, la emisión va más rápido de lo previsto. El **tope de 100M no se
puede superar** (comprobado: `supply + pago <= MAX_SUPPLY` bajo lock), pero la fecha de fin se
adelanta.

## P2 · “Quema de comisiones 10-15 %”

La portada lo anuncia en tokenomics; el Core **no cobra comisión** en ninguna ruta
(`transferir()` mueve el importe íntegro). Es una promesa que el código no cumple: o se
implementa (quemar un % en cada transferencia, con asiento negativo a una cuenta de quema) o se
quita de la web.

## P2 · DoS local con tareas de difusión

`LIMITE_PASOS = 20000` y `LIMITE_N = 4096`: una tarea `difusion_calor` en el límite son
~3,3·10¹¹ operaciones en float64, tanto en el minero como en la verificación del Core. Los
generadores automáticos usan 1024/1500, pero el admin (o un Core comprometido) puede encolar el
máximo y bloquear ambos procesos. Además, cada resultado incorrecto autenticado obliga a
recalcular entero antes de rechazarlo.

**Recomendación**: bajar el límite de `pasos` para difusión (p. ej. 3000) y añadir un
presupuesto de CPU por nodo/minuto para resultados fallidos.

## P3 · Menores

- **Coma flotante**: saldos con residuo `-4,616e-13` por usar `REAL`. No afecta al total
  (suma exacta con el suministro), pero conviene redondear a 1e-6 al leer.
- **Destino sin validar**: `a_direccion` solo se limita a 128 caracteres; se puede enviar a una
  cadena basura y quemar el saldo. Validar `KUK…` o id de nodo.
- **Relé**: publica saldos e ids de nodo (por diseño, ya eran públicos en `/saldos`); conviene
  decirlo en la web para que nadie espere privacidad.
- **Minero**: la entrega usa `timeout=3600`; si el Core se cuelga, el hilo queda bloqueado hasta
  una hora. Bajar a 120 s y reintentar.
- **Billetera en disco**: claves privadas en texto plano (ACL correcta: solo el usuario). Ver
  informe total del 19-10.

## Lo que está bien (verificado)

| Control | Resultado |
|---|---|
| Firma obligatoria al pedir trabajo y al entregar | 401 sin firma / firma inválida |
| Autenticación ANTES de recalcular | sí (`resultado()` documenta el orden) |
| Pago dentro de lock + `BEGIN IMMEDIATE` | sin doble pago; estado revalidado |
| Tope de suministro | `min(base×tier, restante)`; suma == supply |
| Penalización | acotada al saldo (`min(PENALIZACION, max(0, saldo))`), sin negativos |
| Replay de transferencias | `UNIQUE(de_nodo, nonce)` + firma con importe |
| Cadena de hashes | 46.846 asientos encadenados, `cadena_ok: true`, sin alterados |
| Contabilidad | supply 15.058.063,3888 = suma de saldos; 0 saldos negativos |
| Claves en el navegador | no se guardan; solo la dirección |
| Secretos locales | `clave.txt` y `admin_token.txt` solo los lee el usuario |
| Relé | solo lectura; escribir exige clave (401 comprobado) |

## Herramienta entregada

`tools/verificar_moneda.py` (solo lectura, sale 1 si algo falla):

```
1) cadena de hashes        6) nodos duplicados/sin clave
2) supply == suma ledger   7) direcciones con saldo sin ruta de gasto
3) saldos negativos        8) nonces repetidos
```

Resultado de hoy: **OK con 3 avisos** (asientos fundacionales sin encadenar, 15.045.568,84 KIA
en billeteras sin ruta de gasto y nodos duplicados de pruebas).

## Prioridad recomendada

1. **Abrir la ruta de gasto desde billetera** (P0) y una pestaña “Enviar” en la web.
2. Anclar la cadena y cubrir génesis/migración.
3. Tier plano y `tops_red` con referencia fija mientras la red esté en pruebas.
4. Resolver el respaldo de la billetera del minero.
5. Quitar o implementar la “quema de comisiones”.
