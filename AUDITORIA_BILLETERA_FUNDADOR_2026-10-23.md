# Auditoría de la billetera del fundador — 23-10-2026, 21:15

**Billetera**: `KUK9E8B44CD7FE3AF178039453D3EEDF391EEC0CE`
**Saldo comprobado**: **15.000.382,9757 KIA**
**Método**: solo lectura. La base se abre con `mode=ro`, el Core se consulta por GET y
la prueba que mueve dinero se hace contra una **copia** del ledger en una carpeta
temporal. No se ha impreso, copiado ni compartido ninguna clave privada ni frase de
recuperación.

Reproducible: `python tools/auditoria_billetera.py --direccion KUK9E8B44CD7FE3AF178039453D3EEDF391EEC0CE`

---

## 1. El saldo coincide con el ledger

| Comprobación | Resultado |
|---|---|
| Saldo materializado (`saldos_cuenta`) | 15.000.382,975700 KIA |
| Suma de sus 13 asientos del ledger | 15.000.382,975700 KIA |
| Diferencia | **0,000000** (cuadra al micro-KIA) |

Los 13 asientos, uno por uno:

| id | KIA | motivo | cuándo |
|---|---|---|---|
| 13 | +15.000.000,000000 | migración a billetera con recuperación | 14-10 11:12 |
| 101 | +2,805300 | transferencia entrada | 16-10 14:39 |
| 103 | +2,756000 | transferencia entrada | 16-10 14:39 |
| 85606 | +71,034200 | transferencia entrada (rescate) | 19-10 14:44 |
| 85608 | +58,462600 | transferencia entrada (rescate) | 19-10 14:44 |
| 85610 | +45,546400 | transferencia entrada (rescate) | 19-10 14:44 |
| 85612 | +3,401200 | transferencia entrada (rescate) | 19-10 14:44 |
| 85614 | +100,000000 | transferencia entrada (rescate) | 19-10 14:44 |
| 85621 | +100,000000 | transferencia entrada (rescate) | 19-10 14:44 |
| 295486 | −0,010000 | transferencia salida | 22-10 16:54 |
| 295665 | −0,010000 | transferencia salida | 22-10 16:55 |
| 295956 | −0,010000 | transferencia salida | 22-10 16:57 |
| 298221 | −1,000000 | transferencia salida | 22-10 17:13 |

15.000.000 + 384,0057 recibidos − 1,03 enviados = **15.000.382,9757**. Ni un céntimo
fuera de sitio.

## 2. Cada movimiento, contabilizado una sola vez

- **Ningún asiento repetido** en esta billetera (se buscó por tarea + cuenta + importe + motivo + fecha).
- **Las 19 transferencias de la red cuadran** con sus asientos: cada una tiene su
  salida (emisor, signo negativo) y su entrada (destino, mismo importe), y las cuentas
  coinciden con las de la tabla de transferencias.
- **Nonces sin repetir** (lo exige la propia base con `UNIQUE(de_nodo, nonce)` y lo
  comprueba además `tools/verificar_moneda.py`).
- **Hallazgo real (corregido hoy)**: el id del asiento era `tx-` + el nonce cortado a
  12 letras. El 19-10 hubo tres barridos distintos (`5b72627ac027-…-410941`,
  `…-242765`, `…-338402`) que caían **en el mismo id** `tx-barrido-5b72`. El dinero
  estaba bien (6 asientos, 3 parejas correctas), pero emparejarlos después era a
  ciegas. Desde hoy el id lleva el **nonce completo**.

## 3. Nada por encima de las reglas de emisión

Desglose del saldo por origen:

| Origen | KIA |
|---|---|
| Fundacional / migración (una sola vez, bloque génesis) | 15.000.000,000000 |
| Trabajo verificado cobrado por esta dirección | 0,000000 (esta billetera no mina) |
| Transferencias recibidas − enviadas | +382,975700 |
| **Total** | **15.000.382,975700** |

Y la identidad contable de toda la red:

```
génesis 15.000.000,000000 + trabajo pagado 86.700,778254 = 15.086.700,778254
supply_emitido = 15.086.700,778254   ·   suma del ledger = 15.086.700,778254
```

O sea: **no hay KIA de otra fuente** (una transferencia no crea moneda) y el trabajo
pagado está muy por debajo del tope de 100 millones (quedan 84.913.299,22 KIA por
emitir hasta 2150). La asignación fundacional **no se puede repetir**: hay un solo
asiento de 15M y su pareja de migración se compensa (−15M en la dirección antigua).

## 4. La web dice lo mismo que el Core y que la base

| Fuente | Saldo |
|---|---|
| Base de datos (`saldos_cuenta` + ledger) | 15.000.382,975700 |
| Core vivo (`/saldo/...`) | 15.000.382,975700 |
| Relé público que pinta la web (`/api/saldo/...`) | 15.000.382,975700 |
| Instantánea de la web (`docs/metricas_snapshot.json`) | supply 15.086.700,778253937 (idéntico) |

Los cuatro coinciden al micro-KIA, en la misma pasada.

## 5. Firmas y registro

- **Cadena de hashes intacta**: 444.779 asientos encadenados, sin saltos.
- **Sellos**: cadena de sellos verificada al arrancar el Core (261 sellos).
- **Aviso honesto y ya corregido**: hasta hoy la firma Ed25519 se comprobaba al
  entrar y **no se guardaba**. Las 19 transferencias existentes solo conservan nonce,
  importe, cuentas y asientos: su firma original ya no se puede re-verificar (se
  validó en su momento, no queda el original).
- **Desde hoy sí se guarda**: `transferencias.firma` y `transferencias.clave_publica`.
  Probado en una copia del ledger con una transferencia real firmada:

```
[ OK ] el Core acepta la transferencia (200)
[ OK ] la firma esta guardada tal cual
[ OK ] la clave publica del emisor esta guardada
[ OK ] la firma guardada verifica contra la clave publica
[ OK ] un mensaje manipulado NO verifica (no se puede reutilizar)
[ OK ] asiento con id completo (tx-9e1485188a374504b7b167f21c3821bb)
[ OK ] los dos asientos se compensan (no se crea moneda)
[ OK ] se rechaza la repeticion (409 esperado 409)
[ OK ] el ledger real no ha cambiado (349290496 bytes)
```

Reproducible: `python tools/prueba_firma_guardada.py`

## Resumen

| Punto pedido | Resultado |
|---|---|
| 1. Saldo = transacciones del ledger | **OK** (0,000000 de diferencia, 13 asientos) |
| 2. Cada movimiento una sola vez | **OK** (sin duplicados; 19 transferencias emparejadas) |
| 3. Sin KIA por encima de las reglas | **OK** (15M fundacional + 382,9757 de transferencias; red = génesis + trabajo) |
| 4. Web = Core = base | **OK** (las cuatro fuentes al micro-KIA) |
| 5. Firmas y registro correctos | **OK en lo que hay**; a partir de hoy la firma queda guardada y re-verificable |

**Avisos que no son fallo pero hay que saber**: 175 asientos antiguos (previos a la
cadena de hashes) no están encadenados — 3 son los fundacionales/migración; las 19
transferencias anteriores a hoy no tienen firma guardada; y la billetera del fundador
no es un nodo, así que solo se mueve firmando con su clave (el Core la comprueba).

**Mejoras hechas hoy a raíz de esto**: firma + clave pública guardadas en cada
transferencia, id del asiento con el nonce completo, y esta auditoría repetible con un
comando (`tools/auditoria_billetera.py`).

**Nunca se ha tocado**: ni una fila del ledger, ni un saldo, ni una transferencia. La
prueba que mueve dinero usa una copia y lo comprueba al final (mismo tamaño y fecha
del ledger real antes y después).
