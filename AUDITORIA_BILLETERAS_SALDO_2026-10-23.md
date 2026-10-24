# Billeteras con saldo: ¿se puede mover cada una firmando? — 23-10-2026, 21:30

**Método**: solo lectura sobre la base (`mode=ro`), sin enviar nada a producción.
La prueba de que una billetera «se puede mover» se hizo **en una copia del ledger**
con un Core aislado: 0,01 KIA de cada una a una dirección de usar y tirar, y al
final se comprueba que el ledger real no cambia de tamaño ni de fecha. Ningún KIA
real se ha movido. No se imprime ninguna clave privada ni palabra de recuperación
(solo prefijos de la clave pública).

Reproducible: `python tools/auditoria_billeteras.py --probar`

---

## Resumen

| | KIA |
|---|---|
| Total con saldo (16 cuentas) | **15.086.700,778254** |
| Movible firmando desde este PC | **15.074.583,246156** (99,92 %) |
| Sin clave en este PC | **12.117,532098** |

El total coincide **al micro-KIA con el supply emitido**, así que aquí está
contabilizado todo el KIA que existe: no hay cuentas escondidas.

## Las 16 cuentas, una por una

| Cuenta | Saldo KIA | Tipo | Clave | Estado |
|---|---|---|---|---|
| `KUK9E8B44…EEC0CE` | 15.000.382,975700 | billetera | `kuki_fundador_nueva.json` | **OK** (probada) |
| `KUK90BA61…D9DD21` | 70.951,099075 | billetera | `worker_kuki_minero.json` | **OK** (probada) |
| `07e46a1fb829` | 11.697,849900 | nodo | — | sin clave aquí |
| `KUKE1A0EB…E3E96F` | 3.227,625596 | billetera | `worker_minero_pc.json` | **OK** (probada) |
| `cdad7794abba` | 158,250600 | nodo | — | sin clave aquí |
| `27ad4b110f2e` | 100,000000 | nodo sin clave pública | — | **no gastable** |
| `af5edff051d8` | 100,000000 | nodo sin clave pública | — | **no gastable** |
| `520c99c56cf8` | 30,000000 | nodo sin clave pública | — | **no gastable** |
| `KUK46D7EC…3A5E6` | 13,395575 | billetera | `worker_minero_kukipcwin11.json` | **OK** (probada) |
| `0be9be21ba15` | 10,000000 | nodo sin clave pública | — | **no gastable** |
| `67a2cf16e5bd` | 10,000000 | nodo sin clave pública | — | **no gastable** |
| `6f7756aabc78` | 10,000000 | nodo sin clave pública | — | **no gastable** |
| `KUKA4F98C…6B977` | 8,150210 | billetera | `worker_prueba_pipeline.json` | **OK** (probada) |
| `KUKA994AE…10CD4BB` | 0,863284 | billetera | — | sin clave aquí |
| `KUK4494E1…A24934` | 0,530779 | billetera | — | sin clave aquí |
| `KUK666B9E…AC335` | 0,037535 | billetera | — | sin clave aquí |

## Prueba real (sobre la copia, 0,01 KIA cada una)

```
KUK9E8B44… kuki_fundador_nueva.json:      MUEVE OK
KUK90BA61… worker_kuki_minero.json:       MUEVE OK
KUKE1A0EB… worker_minero_pc.json:         MUEVE OK
KUK46D7EC… worker_minero_kukipcwin11.json: MUEVE OK
KUKA4F98C… worker_prueba_pipeline.json:   MUEVE OK
transferencias en la copia: 24 (19 + estas 5)
ledger real intacto
```

Antes de firmar se comprueban **las mismas condiciones que exige el Core**: que la
clave pública derive exactamente la dirección (`KUK + SHA256(pub)[:38]`) y que la
firma Ed25519 de un mensaje `transferir:…` verifique (y que **no** valga para un
mensaje manipulado).

## Lo que NO se puede mover desde este PC, y por qué

**1) Nodos con clave pública registrada, pero sin la privada aquí — 11.856,1005 KIA**

`07e46a1fb829` (11.697,85) y `cdad7794abba` (158,25). Se comprobó en la copia que
el Core **rechaza** una firma con otra clave (`401 firma invalida`), que es lo
correcto. Su clave privada no está en este PC: hay que moverlas desde el equipo
donde se creó ese nodo, o con su copia de seguridad.

**2) Nodos registrados sin clave pública — 260 KIA (no gastables por nadie)**

`27ad4b110f2e` (100), `af5edff051d8` (100), `520c99c56cf8` (30), `0be9be21ba15`
(10), `67a2cf16e5bd` (10), `6f7756aabc78` (10). Son de los primeros días (14-16
de octubre), antes de que el registro pidiera clave. Comprobado en la copia:

```
nodo 27ad4b110f2e: HTTP 401 {"detail":"emisor sin clave registrada ..."}
```

No hay firma posible: el Core no tiene ninguna clave contra la que verificarla, y
«adivinar» una clave para ese id sería dar a cualquiera el saldo de cualquier
nodo. Son **260 KIA de pruebas viejas** que se quedan donde están. No se ha
borrado ni tocado nada.

**3) Billeteras sin clave en este PC — 1,4316 KIA**

`KUKA994AE…` (0,86), `KUK4494E1…` (0,53), `KUK666B9E…` (0,04). Ninguna clave local
deriva esas direcciones: son de la billetera web del navegador o de otro equipo.
Por importe, da igual; si alguna vez importa, se recuperan con sus 20 palabras.

## Dos observaciones (sin tocar nada)

- **Tres ficheros de billetera viejos guardan la clave privada EN CLARO** en el
  disco (`ledger/wallet/nodo_5070.json`, `nodo_5060ti.json`, `nodo_fundador.json`).
  Los tres tienen saldo 0, así que no hay dinero en riesgo, pero conviene
  cifrarlos con DPAPI como el resto (no lo he hecho para no romper herramientas
  que los lean).
- La billetera del fundador y las cuatro de nodos que sí se mueven están cifradas
  con DPAPI (atadas al usuario de Windows de este PC). Eso protege el fichero,
  pero **no** protege si alguien entra en tu sesión de Windows: para mover dinero
  de verdad hace falta la frase de recuperación, no solo el PC.

## Conclusión

- El 99,92 % del KIA que existe se puede mover hoy mismo firmando desde este PC, y
  está **demostrado moviéndolo** (en una copia).
- Lo único atrapado de verdad son **260 KIA** de nodos de los primeros días sin
  clave pública, y **11.856 KIA** de dos nodos cuya clave vive en otro equipo.
- El ledger no se ha tocado: ni una fila, ni un saldo, ni una transferencia real.
