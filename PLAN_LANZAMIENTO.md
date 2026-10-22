# KUKIA — qué falta para lanzarla sin fallos

**Fecha:** 19-10-2026
**Estado medido:** Core vivo, 9 sellos, cadena y firmas OK, emisión 1,3075 KIA/min,
85.996 asientos, base de 73,6 MB, 2 GPUs minando, disco al 10,2 % libre.

## Veredicto

**No está lista para un lanzamiento público. Sí está lista para seguir funcionando
en privado y para que un auditor la revise.**

"Sin fallos" no existe: lo que se puede garantizar es que **los fallos se detecten
y se recuperen**. Hoy falta sobre todo lo segundo (disponibilidad y copias) y lo
primero en un punto concreto: nadie de fuera puede conectarse.

Los fallos se ordenan por lo que impide lanzar (P0), lo que duele al lanzar (P1)
y lo que puede esperar (P2).

---

## P0 — Sin esto no hay lanzamiento

### P0.1 Nadie de fuera puede minar ni usar la billetera

El Core escucha solo en `127.0.0.1:8300`. Un minero en otra casa no puede pedir
trabajo, y una billetera en otro navegador no puede consultar saldo ni transferir.

Opciones (elegir una):

| Opción | Ventaja | Coste / riesgo |
|---|---|---|
| **Túnel a este PC** (Cloudflare Tunnel, ngrok…) | Rápido, sin migrar nada | Depende de tu PC y tu luz; expone el Core a Internet |
| **Core en un VPS** | 24/7, IP fija, HTTPS | Migrar a Linux, pagar, mover la base y las claves |
| **Relé público + Core local** | El Core no se expone | Solo sirve para métricas; minar exigiría abrir la API |

**Decidido: túnel a este PC.** Hecho y probado el 19-10:

- `cloudflared` instalado en `tools/bin/cloudflared.exe` (53 MB, sin admin).
- [ARRANCAR_TUNEL.cmd](ARRANCAR_TUNEL.cmd) levanta el túnel con
  `--http-host-header 127.0.0.1`, asi el Core sigue viendo un Host local y su
  guardia anti-DNS-rebinding no se debilita.
- Verificado por la URL publica: `/stats` 200, `/ledger/integridad` 200,
  CORS correcto para `https://lordkukone.github.io` y `/admin` 403 sin token.

Lo que **falta** para que el túnel sirva de verdad:

- [ ] **URL fija**: el túnel rápido da una URL aleatoria que cambia al reiniciar.
      Para una direccion estable hace falta un dominio en Cloudflare + túnel con
      nombre, o Tailscale/ngrok con cuenta. Sin esto, ningun minero puede
      configurar el Core una sola vez.
- [ ] **Cerrar `/admin` fuera del túnel** (Cloudflare Access o regla de ruta).
- [ ] **Límite por IP** (hoy el límite es por ruta, no por origen).
- [ ] Aviso claro: si el PC se apaga o se corta internet, la moneda cae.

### P0.2 Disponibilidad: si se apaga el PC, se para la moneda

No hay supervisor: si el Core o el minero mueren, nadie los reinicia. Tampoco hay
aviso: te enteras cuando miras.

- [ ] Arranque automático del Core y del minero al iniciar sesión (script de
      inicio o tarea programada).
- [ ] Reinicio automático si el proceso muere.
- [ ] Aviso si `tools/salud.py` falla (correo, Telegram o lo que uses).

### P0.3 Copias de seguridad (ya hay herramienta, falta rutina)

`tools/backup_ledger.py` copia el `.db` con la API de SQLite (sin parar el Core),
verifica la copia contra los sellos y escribe un `manifest.json` con hashes.

- [ ] Elegir destino **fuera de este disco** (otro disco, NAS o nube).
- [ ] Programarla (cada 6-12 h) y probar una **restauración real** una vez.
- [ ] Decidir si se copian las claves (`--con-claves`): sin ellas se audita pero
      no se recupera el dinero; con ellas, la copia es oro y hay que protegerla.
- [ ] Guardar una copia de `sello_key.txt` y `admin_token.txt` en un sitio
      separado (papel o gestor de contraseñas).

### P0.4 Exposición segura (solo si se abre a Internet)

- [ ] HTTPS obligatorio (nunca HTTP en claro fuera de loopback).
- [ ] `/admin/*` cerrado por IP o fuera del túnel; el token no viaja por la web.
- [ ] Límites por IP además de por ruta (hoy hay límite por ruta).
- [ ] Firewall: solo 443 público; 8300 nunca directo a Internet.
- [ ] Revisar CORS y `HostGuard` con el dominio real (`KUKIA_HOSTS`, `KUKIA_ORIGENES`).

### P0.5 Reglas claras antes de que entre nadie

- [ ] Qué pasa si se pierde una clave (hoy: el saldo queda congelado).
- [ ] Unidad mínima: 0,0001 KIA (las transferencias redondean a 4 decimales).
- [ ] Aviso legal: no es una inversión, no hay mercado, no hay promesa de valor.
- [x] Guía de minero: [GUIA_MINERO.md](GUIA_MINERO.md) (requisitos, arranque de un clic,
      recuperación de la billetera, qué se gana y qué no).

---

## P1 — Duele al lanzar, pero se puede vivir con ello unos días

- [ ] **Prueba de restauración**: borrar una copia y levantar el Core con ella.
- [ ] **Corte de luz** con el Core escribiendo: comprobar que arranca y que la
      cadena sigue cuadrando (SQLite es transaccional, pero hay que verlo).
- [ ] **Varios mineros a la vez**: hoy solo se han probado 2 GPUs en una máquina.
- [ ] **Reloj movido** ±10 min: las firmas caducan a los 5 min; comprobar el
      mensaje de error y la recuperación.
- [ ] **Disco lleno**: el `tools/salud.py` avisa al 20 %, pero no hay prueba de
      escritura con el disco al 100 %.
- [ ] **Monitorización continua**: `tools/salud.py --backups <carpeta> --json`
      cada 5 min desde un script, con aviso si devuelve 1.
- [ ] **Rotación de logs**: `core_sellos.out.log` y `minero_ok.out.log` crecen
      sin límite.

---

## P2 — Mejoras que no bloquean el lanzamiento

- [ ] Anclar los sellos fuera de la máquina (web, git u OpenTimestamps → Bitcoin).
- [ ] Segundo Core que recalcule y compare (validación independiente).
- [ ] Multisig 2-de-3 para la clave del sello.
- [ ] Precisión de 6 decimales o unidad mínima explícita (polvo actual: 0,000071 KIA).
- [ ] Panel público con el sello y el estado de integridad.
- [x] Empaquetar el minero como instalador de un clic: `ARRANCAR_MINERO.cmd` +
      `VER_SALDO_MINERO.cmd`, dentro de `docs/kukia-node.zip` (probado contra el Core
      real: nodo registrado, tarea entregada y cobrada).

---

## Checklist de "go"

Se puede decir "lanzada" cuando **todas** estas son verdad:

1. Un minero en otra máquina, fuera de tu red, pide trabajo y cobra.
2. Una billetera en otro navegador consulta saldo y transfiere.
3. El Core se reinicia solo tras un corte y la cadena sigue `cadena_ok`.
4. Existe una copia verificada de las últimas 24 h en un disco distinto.
5. `tools/salud.py --backups ...` devuelve 0 durante 24 h seguidas.
6. Hay una guía de minero y un aviso legal publicados.
7. Sabes qué harás si mañana se cae el PC (y cuánto tarda en volver).

Hoy: **0 de 7**. Hay piezas (integridad verificada, copia manual que funciona),
pero ninguna de las siete condiciones se cumple todavía.

---

## Herramientas listas

```bash
ARRANCAR_TUNEL.cmd                     # URL publica temporal (cambia cada vez)
python tools/salud.py                  # veredicto: OK / AVISO / FALLO
python tools/salud.py --backups D:/KUKIA_backups --json
python tools/backup_ledger.py --destino D:/KUKIA_backups --con-claves --mantener 14
python publico/verificar_sellos.py --core http://127.0.0.1:8300
```

Ninguna de las tres necesita privilegios de administrador.
