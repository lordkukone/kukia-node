# Auditoría total KUKIA — 19-10-2026

Auditoría interna (no independiente) del software, la web, el relé público, los datos
y la operación. Todo lo que se afirma abajo se ejecutó contra el sistema real ese día;
las cifras son las del momento de la revisión.

- **Alcance**: `coordinator/kukia_core.py` (1.399 líneas), `worker/kukia_worker.py`,
  `ledger/kukia_wallet.py`, `dashboard/kukia_dash.py`, `publico/relay.js`,
  `publico/publicar_metricas.py`, la web (`docs/`) y la base `coordinator/ledger/kukia.db`.
- **Método**: lectura de código + pruebas en vivo (endpoints, firmas, saldos, cadena del
  ledger), revisión de la web publicada en su origen real y comprobación de permisos y
  secretos en disco.
- **Límite**: es una auditoría interna, no sustituye a una revisión externa.

## Resumen

No hay ningún hallazgo crítico abierto. Los controles importantes (firmas Ed25519, cadena
de hashes, cobro a la billetera, CORS, límites de peticiones) están implementados y se
comprobaron funcionando. Los problemas reales son de **datos y operación**: nodos viejos
que distorsionan la dificultad, KIA atrapados en cuentas sin clave utilizable y la
publicación de métricas que depende de un proceso manual.

| # | Severidad | Hallazgo | Estado |
|---|---|---|---|
| 1 | P1 | 12.494,5449 KIA en ids de nodos viejos sin clave utilizable | Abierto |
| 2 | P1 | 32 nodos fantasma inflan la dificultad (factor 2,53) y bajan el pago por tarea | Abierto |
| 3 | P1 | La billetera del minero no se puede recuperar por la vía documentada (contraseña descartada) | Abierto (documentado) |
| 4 | P2 | La publicación de métricas es manual: sin autoarranque ni vigilante | Mitigado (la web avisa) |
| 5 | P2 | Sin copias de seguridad de `kukia.db` (33 MB, una sola copia) | Abierto |
| 6 | P2 | Clave del relé y token de admin legibles por un grupo local ajeno | **Corregido hoy** |
| 7 | P2 | Relé sin caché de borde: cada lectura iba a R2 (coste/DoS) | **Corregido hoy** |
| 8 | P2 | Claves privadas de billetera en texto plano en disco | Aceptado (con recomendación) |
| 9 | P3 | Origen `"null"` permitido en CORS del Core | Abierto (bajo) |
| 10 | P3 | Sin pruebas automatizadas en el repositorio | Abierto |
| 11 | P3 | `codigo.html` y el zip publicados estaban desfasados | **Corregido hoy** |
| 12 | P3 | `/api/metricas` responde 404 a HEAD (monitorización) | Abierto (bajo) |

## Verificaciones que pasaron

| Control | Resultado | Evidencia |
|---|---|---|
| Firma obligatoria al pedir trabajo | Sí: sin firma o caducada → 401 | `/tareas/pendiente`, `VENTANA_FIRMA=300 s` |
| Firma obligatoria al entregar resultado | Sí, verifica `"{tid}:{metrica}"` con la clave del nodo | `resultado()`, `EXIGIR_FIRMA_GLOBAL=True` |
| Verificación real del trabajo | El Core recalcula (numpy/GPU) y compara; tolerancia relativa solo en difusión | `computar_tarea()` |
| Ledger encadenado | 39.894 asientos, 39.719 encadenados, `cadena_ok: true`, sin ids alterados | `/ledger/verificar` |
| Contabilidad | `supply_emitido` = suma de saldos, diferencia 0,0 | 15.050.933,3888 KIA |
| Cobro a la billetera | Los asientos recientes van a `KUK90BA61…`, no al id del nodo | `/ledger` + saldo de la billetera |
| Core no expuesto | Escucha solo en `127.0.0.1:8300` | `netstat` |
| Guardia Host/Origin | Host ajeno → 403; origen ajeno → 403 | `HostGuardMiddleware` |
| Límite por ruta | Cubos separados por ruta; 429 al pasarse | `LIMITES_POR_RUTA` |
| Token de admin | `compare_digest`; sin token → 403 | `exigir_admin()` |
| SQL | Sin f-strings en consultas; todo parametrizado | grep sobre el Core |
| Relé: escritura protegida | Sin clave o con clave falsa → 401 | probado en producción |
| Relé: solo lectura | No hay endpoint de escritura pública; 404 en rutas raras | probado |
| Web: claves fuera del navegador | `localStorage` guarda solo la dirección; claves en memoria | `wallet.html` |
| Web: HTML siempre fresco | Service worker red-primero para HTML, cache v2 | `sw.js` |
| Secretos en lo publicado | `admin_token.txt`, `clave.txt` no están en `docs/` ni en el zip | revisión del zip y del repo |
| Permisos de la billetera | Solo SYSTEM/Administradores/kuki | ACL NTFS |

## Hallazgos en detalle

### P1-1 · 12.494,5449 KIA atrapados en nodos viejos

34 nodos registrados, **10 sin clave pública** y **14 con saldo**. Los pagos anteriores al
arreglo del 19-10 iban al id del nodo; el barrido recuperó lo firmado con la clave actual,
pero lo que pertenece a claves perdidas sigue ahí: **12.494,5449 KIA** en ids que no son la
billetera del minero.

- **Impacto**: dinero emitido que nadie puede gastar; descuadra la lectura ingenua de
  "saldo de la red" frente a "saldo disponible".
- **Recomendación**: no tocar el ledger (es la verdad contable). Marcar esos nodos como
  `archivado`/`huerfano` y publicar la cifra como "KIA no reclamados". Si apareciera alguna
  de esas claves privadas, el worker ya sabe barrer con `barrer_saldos()`.

### P1-2 · Nodos fantasma y dificultad inflada

`/stats` declara `tops_red: 11.192` frente a la referencia de 1.747 → `factor: 2,531`. El
pago por tarea se calcula con ese factor, así que **se está pagando menos de lo diseñado**
por culpa de nodos de prueba y duplicados (`minero_kuki[cuda:0]` aparece 6 veces,
`minero_kuki[cuda:1]` 7 veces, etc.).

- **Impacto**: economía distorsionada y stat pública "34 nodos" que no refleja 2 GPUs reales.
- **Recomendación**: archivar nodos sin actividad (por ejemplo > 7 días) y calcular
  `tops_red` solo con nodos con clave y actividad reciente; conservar el histórico en una
  tabla aparte, nunca borrar asientos.

### P1-3 · Recuperación de la billetera del minero

`crear_billetera()` genera una contraseña aleatoria y la devuelve, pero el worker la
descarta (`w = w[0]`). El JSON guarda las 20 palabras y la clave privada, pero sin la
contraseña la recuperación web (contraseña + palabras) es imposible. Quien pierda el
fichero no puede rehacer la billetera desde la web.

- **Impacto**: falsa sensación de respaldo con las 20 palabras.
- **Recomendación**: (a) documentar que el respaldo real es el JSON; (b) aceptar
  `KUKIA_WALLET_PASS` al arrancar el worker y guardar una copia cifrada de la contraseña; o
  (c) añadir "importar clave privada" a `wallet.html`. La opción (b)+(c) es la más honesta.

### P2-4 · Publicación de métricas manual

El relé solo tiene datos si `publicar_metricas.py` está corriendo. Tras reiniciar el PC se
queda con el último dato (la web lo dice con punto ámbar). No hay tarea programada ni
vigilante que lo relance.

- **Recomendación**: tarea programada al inicio de sesión + comprobación cada 5 min
  (si el proceso no está, arrancarlo).

### P2-5 · Copias de seguridad

`coordinator/ledger/kukia.db` (33 MB) es la única copia viva. Hay carpetas `_backup_*` del
18-10, pero nada automático.

- **Recomendación**: copia diaria (`.backup` de SQLite, no copiar el fichero en caliente),
  `PRAGMA integrity_check` y `/ledger/verificar` en el mismo script; rotación de 7–30 días.

### P2-6 · Permisos de secretos (corregido)

`publico/clave.txt` (clave de escritura del relé) y `coordinator/admin_token.txt` eran
legibles por `CodexSandboxUsers`. Ahora solo por `kukipcwin11\kuki` (`icacls
/inheritance:r /grant:r`). Verificado con `Get-Acl`.

### P2-7 · Relé sin caché (corregido)

Cada `GET /api/metricas` leía R2, y la página añade `?_=` (que además anulaba cualquier
caché). Ahora hay caché de borde de 3 s con clave fija (ignora el query) y **degradación
segura**: si la caché falla, se sirve desde R2. Un primer intento devolvió 502 en
producción; se corrigió con manejo de errores y se volvió a desplegar (200 verificado).

### P2-8 · Claves privadas en disco

`ledger/wallet/*.json` guarda `clave_privada` y `mnemonico` en claro. El ACL es correcto
(solo el usuario), pero no hay cifrado: un backup sincronizado a la nube o un malware con
permisos de usuario se las lleva.

- **Recomendación**: cifrar con DPAPI (`CryptProtectData`) o mover la carpeta a un volumen
  cifrado; excluirla de cualquier sincronización.

### P3 · Menores

- `ORIGENES_PERMITIDOS` incluye `"null"` (páginas `file://`). Solo afecta a lecturas
  públicas; quitarlo si no se usa.
- 175 asientos anteriores al encadenado se ignoran en la verificación (correcto, pero
  conviene decirlo en la respuesta pública).
- Sin pruebas automatizadas: un `pytest` de humo (registro, firma, pago, transferencia,
  cadena) evitaría regresiones como la del cobro al id del nodo.
- `codigo.html` y `kukia-node.zip` se actualizaban a mano y estaban desfasados: ahora los
  genera `publico/generar_web.py` desde los ficheros reales (verificado por hash).
- El relé responde 404 a `HEAD`; si se monitoriza con `curl -I`, usar `GET`.

## Correcciones aplicadas en esta auditoría

1. **Métricas de salud** en el relé (`salud.gpus`, `tareas_por_min`, `ultima_tarea_hace`)
   y página pública **"Ponte a minar"** con estado real de Core, minero, GPUs y ritmo.
2. **Caché de borde** de 3 s con degradación segura (y arreglo del 502 que provocó la
   primera versión).
3. **Permisos** de `clave.txt` y `admin_token.txt` restringidos al usuario.
4. **`codigo.html` y el zip regenerados** desde disco (incluye el arreglo de cobro, el relé
   y `requirements.txt`, que antes no existía).
5. **Ritmo de tareas** en la portada: ya no marca 0 cuando el dato del relé se repite.

## Recomendaciones priorizadas

1. Limpiar/archivar nodos sin actividad y **recalcular la dificultad** solo con nodos vivos
   (recupera el pago diseñado).
2. Automatizar el publicador y las copias de seguridad de la base.
3. Resolver la recuperación de la billetera del minero (opción b+c del P1-3).
4. Decidir qué hacer con los 12,49 KIA no reclamados (marcarlos, no borrarlos).
5. Añadir una suite de humo y publicar el resultado de `/ledger/verificar` en la web.

## Reproducir

```bash
curl http://127.0.0.1:8300/stats
curl http://127.0.0.1:8300/ledger/verificar
curl http://127.0.0.1:8300/saldos
curl https://site-ef75d1db031c418d80071d2758f3a9e5.freebuff.page/api/metricas
curl -X POST https://site-ef75d1db031c418d80071d2758f3a9e5.freebuff.page/api/publicar -d '{"stats":{}}'
# esperado: 401
```
