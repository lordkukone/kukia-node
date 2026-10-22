# FASE 1 — PROPUESTA DE REDISEÑO VISUAL + HONESTIDAD (KUKIA Network)
Fecha: 15/10/2026 · Estado: PROPUESTA (sin tocar código; pendiente autorización para ejecutar)
Regla de esta fase: solo presentación, estilos, composición y textos. Cero cambios funcionales.

## 1. INVENTARIO (qué existe)
Web estática en `Desktop\KUKIA\docs\` = `index.html` (41KB) + `codigo.html` (45,9KB, muestra el fuente) + `whitepaper.html` + `wallet.html` (PWA, genera/recupera billetera en local + pestaña "Mi saldo") + assets (moneda.jpg, promo_web.mp4, kukia-node.zip con core+worker+wallet+dashboard, whitepaper PDF).
Backend real verificado: `coordinator/kukia_core.py` (tiers, reputación, anti-doble-pago, recompensa dinámica), `worker/kukia_worker.py`, `ledger/kukia_wallet.py` (Ed25519), `dashboard/kukia_dash.py` (panel LOCAL que lee el SQLite del propio equipo).
Comparativa repo vs vivo: **idénticos** (misma portada, mismos textos). Lo que se edite aquí hay que republicarlo igual en ambos.

## 2. PROPUESTA VISUAL (paleta del plan: azul noche + cian eléctrico + violeta)
Hoy la web mezcla tema oro (CSS) con verde/cian (estilos inline del hero) = inconsistente y con estética "cripto-moneda genérica".
- Unificar todo a: fondo `#050b1e/#071230`, primario cian `#00e5ff`, acento violeta `#7b5cff`, texto `#c3d1ea`. El dorado queda SOLO para la moneda KIA (logo/moneda), no para botones/títulos.
- Hero: mantener canvas de nodos + video, titular EXACTO del plan («Computación GPU distribuida para IA, verificable y abierta» — ver punto 3 sobre "verificable"), 2 botones con destinos actuales (#registro, #como).
- Tarjetas/secciones: mismas, reestilizadas (bordes finos cian, sin brillos dorados agresivos).
- Añadir secciones que pide el plan y faltan: **Red/estado** (con etiqueta TESTNET + fecha de los datos), **Para mineros** (hardware 8GB+, software pip, costes eléctricos, límites, riesgos, sin admin), **Documentación** (enlaces whitepaper/código), **Comunidad** (GitHub + 2 posts vivos de Reddit, sin inventar más).
- Responsive: el menú desaparece en móvil sin alternativa → añadir menú hamburguesa simple (solo CSS/JS visual).

## 3. TEXTOS A HONESTIZAR (obligatorio en esta fase, con reemplazo exacto)
3.1. **Quantum/QPU**: NO existe computación cuántica. Quitar en: `index.html:6` (title), `:8` (keywords: quantum compute, QPU), `:9-10` (og:), `:109` (tagline CYBER/QUANTUM), `:122` (hashtags #GPUtoQPU #QPU #FuturoCuantico), `:325` (footer #QPU #FuturoCuantico). Nuevo tagline: `RED GPU DISTRIBUIDA // IA VERIFICABLE EN PRUEBAS`.
3.2. **"Sin trampas posibles"** (`index.html:130`, tarjeta Verifica) → "Diseñado para detectarlas: el Core recalcula cada tarea y rechaza lo falso."
3.3. **"staking" / "su depósito"** (`index.html:130` "reputacion y staking"; `:237` "pierden reputacion y su deposito") → NO IMPLEMENTADO (core solo tiene tiers+reputación). Quitar ambas menciones.
3.4. **"15M+ KIA emitidos (verificados)"** (`index.html:145`) → "15M génesis (fundador)". Es asignación fundacional, no minería.
3.5. **"Garantía total"** (`index.html:142`) → "Protección". El cuerpo ya es cuidadoso; el titular no.
3.6. **"Seguridad probada... funciona"** (`index.html:221-224`) → "Seguridad probada en pruebas (resultado real)". Mantener la recomendación honesta del equipo secundario.
3.7. **"El ledger público lo demuestra"** (`index.html:142`) → "El ledger local lo registra". El ledger es un SQLite local, no público.
3.8. **"Dashboard: estado de la red en vivo"** (`index.html:182`) → "Dashboard: panel local (corre en tu propio equipo)". Existe `kukia_dash.py`, es local.
3.9. **"Reparte tu carga entre decenas de GPUs"** (`index.html:140`) → "Reparte tu carga entre los nodos disponibles". Hoy hay ~13 nodos de prueba.
3.10. **Stats estáticas** (`index.html:145-148` + contador 100M en `:155`) → añadir bajo el bloque: "Datos de la red de pruebas a fecha de publicación. No son métricas en vivo." (Cablear datos reales = fase 3+, documentado como pendiente.)
3.11. **`whitepaper.html:34`** "KUKIA es una red descentralizada" → "KUKIA es una red distribuida en pruebas con coordinador central (testnet)". El resto del whitepaper (caja de transparencia §7, aviso de recompensas §4) YA es honesto: no tocar.
3.12. **Registro (formsubmit a Hotmail personal)** (`index.html:263`): añadir micro-línea de privacidad: "Tus datos van al correo del fundador vía FormSubmit solo para avisarte del lanzamiento. Sin cuenta, sin spam." (Auditoría de privacidad = fase 2.)

## 4. ARCHIVOS A MODIFICAR (solo estos, nada funcional)
- `docs/index.html` (paleta, hero, secciones nuevas, textos 3.1–3.10, menú móvil, línea privacidad 3.12)
- `docs/whitepaper.html` (texto 3.11)
- `docs/wallet.html` — SIN cambios de lógica; solo estilos para igualar paleta.
- `docs/codigo.html` — SIN cambios (verificar que el código mostrado == repo antes de republicar).
- Procedimiento: ① zip de backup de `docs/` con fecha; ② rama `fase-1-rediseno` (clonar de GitHub); ③ editar; ④ abrir las 4 páginas en Chrome+modo móvil y comprobar; ⑤ diff + commit; ⑥ republicar igual en Surge y Pages; ⑦ rollback = restaurar el zip.

## 5. PRUEBAS DE REGRESIÓN (checklist antes de publicar)
- [ ] Las 4 páginas abren sin errores de consola en Chrome y Firefox.
- [ ] Móvil 360px: menú hamburguesa funciona, hero/video legibles, formulario usable.
- [ ] Todos los enlaces internos y externos responden (GitHub, Reddit vivos, zip, PDF).
- [ ] Formulario sigue enviando a FormSubmit (probar envío real).
- [ ] Video promo reproduce con sonido opcional.
- [ ] `codigo.html` muestra el mismo código del repo (diff).
- [ ] Buscar "quantum/QPU/staking/garantía total/trampas imposibles" → 0 resultados.

## 6. PENDIENTES DOCUMENTADOS (NO se tocan en fase 1, van a fases 2-3)
- P0-honestidad: pestaña "Mi saldo" de `wallet.html` apunta a `http://192.168.1.11:8300` (solo funciona en la LAN de Kuki). Propuesta: campo "URL de tu Core" editable + nota de que requiere Core propio. Requiere mini-cambio funcional → con autorización.
- Stats en vivo: hoy son números fijos. Cableado real = fase 3.
- Privacidad del registro, rate-limit, admin token, sandbox del worker = fase 2.

## 7. DECISIÓN SOLICITADA
Autorizar la ejecución de los cambios del punto 4 con el procedimiento del punto 4. Fases 2-5 NO empezadas.
