# Auditoría de caos — 22/10/2026

Objetivo de la sesión: *«ponerlo a funcionar perfecto como Bitcoin, sin un fallo
ni nada vulnerable»*. Esto es lo comprobado, lo arreglado y —importante— lo que
sigue sin poder afirmarse.

---

## 1. Lo que ya estaba bien (comprobado, no supuesto)

Antes de tocar nada se pasó la batería existente sobre una **copia** del ledger
y un Core aislado: **101/101 OK**. Los puntos duros que ya cubría y siguen
cubriendo:

| Propiedad | Cómo se comprueba |
|---|---|
| Nadie gasta el saldo de otra billetera | firma Ed25519 sobre `transferir:<de>:<a>:<monto>:<nonce>`; la dirección se deriva de la clave pública |
| No hay doble gasto por repetición | `UNIQUE(de_nodo, nonce)` + 409 |
| El Core no confía en el minero | recalcula la tarea y compara (o verificación redundante 70/30) |
| Un resultado falso no cobra | castigo + reintento, tarea devuelta a la cola |
| El historial no se reescribe a mano | cadena SHA-256 + sellos firmados con clave fuera del `.db` |
| Un nodo no acapara la cola | cupo por nodo (1 si no tiene historial, 4 con él) |
| La red no se infla registrando nodos | reto firmado + cupos por IP y por clave |
| No hay inyección SQL | todas las consultas van parametrizadas (revisado: cero `execute(f"...")`) |
| Nadie administra sin token | `X-Admin-Token` con `secrets.compare_digest` |
| Basura no peta (secuencial) | cubierto en parte por la suite |

---

## 2. Lo que faltaba probar de verdad

Toda la batería era **secuencial**. Los tres fallos que solo aparecen cuando algo
pasa *a la vez* o *se corta la luz* no estaban cubiertos:

1. dos transferencias simultáneas del mismo saldo (doble gasto),
2. el mismo resultado entregado varias veces a la vez (doble pago),
3. un apagón a mitad de escritura (medio asiento).

Y nadie había tirado basura sistemática contra las rutas públicas buscando un 500.

Nueva batería: **`tools/pruebas_caos.py`** — 33 casos, sobre copia del ledger y
Core aislado en puerto libre. Nunca toca la red real.

---

## 3. Resultados (33/33 OK)

```
== A. DOBLE GASTO: 8 transferencias a la vez del mismo saldo ==
  [OK] solo UNA de las 8 cobra (doble gasto imposible)
  [OK] el origen se queda con el resto (ni negativo ni duplicado)
  [OK] el suministro no cambia
  [OK] la cadena sigue intacta tras el acoso

== B. DOBLE PAGO: el mismo resultado entregado 6 veces a la vez ==
  [OK] nada devuelve 500 (200/409)      codigos=[409,409,200,409,409,409]
  [OK] solo una entrega se paga | un solo asiento | tarea en 'ok'

== C. CORTE DE LUZ: kill -9 con 40 transferencias en vuelo ==
  [OK] el Core rearranca solo tras un corte seco
  [OK] cadena intacta | sellos bien | pagos no bloqueados
  [OK] suministro = suma de saldos (no hay dinero suelto)
  [OK] cero medios asientos (cada debito tiene su credito)
  [OK] nonce cobrado sigue bloqueado; el que no entro se puede reintentar
   (corte a mitad de faena de verdad: 12 de 40 entraron)

== D. BASURA: cuerpos malformados contra las rutas publicas ==
  [OK] ni una sola basura provoca un 500 (27 casos)
  [OK] no se puede bajar el token de admin por /modelos (404)
  [OK] cuerpo gigante cortado (413); sin token de admin no se crea nada (403)
```

El punto C es el que más vale: el `kill -9` cayó con 40 peticiones dentro y 12
ya comprometidas. Tras rearrancar, ninguna cuenta perdió ni duplicó un céntimo y
no quedó ni un débito sin su crédito.

---

## 4. Fallos encontrados y arreglados

### 4.1 Tres cuentas con saldo `-0.0` (arreglado)

`saldos_cuenta` tenía **3 filas con `-4,6e-13`** (`96df5ffad36c`,
`d4040a724742`, `8657020a6670`). No es deuda: es residuo de sumar y restar en
coma flotante. La billetera las pintaba como `-0.0 KIA` y en un ledger eso
parece una cuenta embargada.

- Escritura: `_asiento` deja el total en `0.0` cuando queda en ±1e-9 (no afecta
  a ningún saldo real).
- Lectura: `/saldo` y `/saldos` muestran `0.0`, no `-0.0`, también en las filas
  históricas.
- Comprobado: **0 cuentas** con deuda real (`< -1e-6`) en el ledger.

*Se aplica al reiniciar el Core.* El Core en marcha (PID del 8300) sigue con el
código anterior; no se ha reiniciado para no cortar la red sin avisar.

### 4.2 Documentación desfasada (arreglado)

- `README.md` decía «93 pruebas»; son 101.
- `SEGURIDAD.md` repetía el punto «5.» dos veces en la lista de pendientes.
- Añadida la batería de caos en `README.md` y `SEGURIDAD.md`.

---

## 5. Lo que sigue sin ser «como Bitcoin» (y no se arregla hoy)

Esto no es código: es arquitectura y operación. Decirlo es parte del trabajo.

| Pieza | Bitcoin | KUKIA hoy |
|---|---|---|
| Nodos que validan el historial | miles, independientes | **1** Core en tu PC |
| Reescritura del historial | coste energético de una red | una clave en el disco de tu PC |
| Apagón del operador | la red sigue | **la red desaparece** |
| Acceso exterior | red P2P | túnel gratuito de Cloudflare que cambia de URL al caerse |
| Precio de mercado | exchange, liquidez | no existe (99,44 % del suministro en tu génesis) |
| Código auditable | repositorio público con historial | zip publicado, **sin git** |

Lo que **sí** comparte con Bitcoin, y ahora está probado bajo concurrencia y
apagón: SHA-256 encadenado, Ed25519 para gastar, verificación sin confiar en el
minero, y ni doble gasto ni doble pago.

Para acercarse de verdad, por orden de impacto:

1. **Réplica del ledger** en otro PC/disco con la misma cadena y arranque
   manual si el principal muere (es el «ni un fallo» de verdad).
2. **Dominio fijo** en vez del túnel rápido.
3. **Multisig 2-de-3** para la clave del sello (diseño ya escrito en `MULTISIG.md`).
4. **Git público** con el hash sellado en Bitcoin, para que el código publicado
   sea el que corre.
5. Un **minero externo real** cobrando: hoy los 32 nodos registrados son de esta
   máquina.

---

## 6. Cómo repetirlo

```bash
python tools/pruebas_seguridad.py   # 101 casos, secuenciales
python tools/pruebas_caos.py        # 33 casos: concurrencia, kill -9, basura
```

Ambas copian el ledger con la API de backup de SQLite, arrancan un Core aislado
en un puerto libre y salen con código 0 solo si todo pasa. Ninguna escribe en la
base real.
