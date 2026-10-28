# Auditoría completa de billeteras — 28-10-2026, 16:58

Solo lectura. No se ha movido dinero, ni creado billeteras, ni escrito en el ledger.
Cada cifra de este informe sale de abrir la base con `mode=ro`, descifrar las claves
con DPAPI en memoria y recalcular la dirección desde la clave pública.

## Resumen

| Dato | Valor |
|---|---|
| Ficheros en `ledger/wallet/` | 41 `.json` (40 son billeteras con dirección) |
| Billeteras con saldo | **18** |
| Saldo en billeteras | **15.076.068,025456 KIA** |
| Suministro total (suma de asientos) | 15.088.186,107972 KIA |
| Cuentas con saldo | 30 de 45 |
| Clave que abre y da su dirección | 36 de 40 |
| Clave que firma Ed25519 | **40 de 40** |
| Con las 20 palabras dentro (cifradas) | 35 de 40 |
| Con `RECUPERACION_*.txt` | 15 de 40 |
| Saldo que cuadra con sus asientos | **18 de 18** |

La suma de `saldos_cuenta` y la suma de los asientos de `ledger` coinciden al microKIA
(15.088.186,107972 las dos), así que no hay saldo inventado ni asiento huérfano.

## Billeteras con saldo

| Dirección | KIA | Fichero | kdf | Asientos | Recuperación |
|---|---:|---|---:|---:|---|
| `KUK9E8B44CD7FE3AF178039453D3EEDF391EEC0CE` | 15.000.382,375700 | `kuki_fundador_nueva` | v1 | 25 | **NO** |
| `KUK90BA61EDA315C341434AF93CC4D82D80D9DD21` | 70.951,099075 | `worker_kuki_minero` | v2 | 69.864 | **NO** |
| `KUKE1A0EB09FD9BF49FF41B884EDBDF6DC0E3E96F` | 4.712,026406 | `worker_minero_pc` | v2 | 939.355 | sí |
| `KUK46D7ECA2EED95884D6FF97A203432CADE3A5E6` | 13,395575 | `worker_minero_kukipcwin11` | v2 | 2.349 | sí |
| `KUKA4F98CE947608D5EE35CDDF3ACBDE5E7A6B977` | 8,530113 | `worker_prueba_pipeline` | v2 | 1.488 | sí |
| 13 × `cliente_prueba_*` / `cliente_wal_verif` | 0,598587 en total | `cliente_*` | v2 | 1–3 | NO |

Las 13 de cliente suman 0,598587 KIA: son restos de las pruebas del 25-10. Con las 5
anteriores dan las 18 billeteras con saldo.

## Lo que está bien (comprobado, no supuesto)

1. **Ninguna clave privada en claro** en `ledger/wallet`: las 40 se guardan como
   `clave_privada_cifrada` con `cifrado: dpapi-usuario-windows`.
2. **Todas las claves firman**: se firmó un mensaje con cada una y se verificó con su
   clave pública.
3. **36 de 40 claves reproducen su dirección** (`KUK` + `SHA256(pub)[:38]`). Las 4 que
   no, están abajo y tienen saldo 0.
4. **Los 15 ficheros `RECUPERACION_*.txt` se verificaron derivando**: con su contraseña
   y sus 20 palabras sale la misma dirección **y la misma clave privada** que hay en el
   JSON. 15 de 15.
5. **Cada saldo cuadra con sus asientos** en las 18 billeteras con dinero.
6. **git no versiona ninguna billetera**: `git ls-files ledger/wallet` → 0.
7. **Permisos de la carpeta**: `SYSTEM`, `Administradores` y `kukipcwin11\kuki`. No hay
   `Usuarios` ni `Todos`.
8. **El papel de N: tiene las palabras correctas** de las dos billeteras grandes:
   se compararon palabra por palabra con las que guarda el JSON (cifradas) y coinciden.

## Hallazgos

### A1 — 15 billeteras con saldo no tienen fichero de recuperación (15.071.334,073362 KIA)

Son el fundador y el minero (15.000.382 + 70.951) más 13 pequeñas. Su JSON solo abre en
este usuario de Windows; la copia de N: es el **mismo** blob DPAPI (`sha256 83565db6…`,
byte a byte igual), así que tampoco sirve en otro PC. Lo único que viaja son las 20
palabras del papel… **y la contraseña, que en el papel son 46 guiones bajos**: se generó
para rellenar a mano y nunca se rellenó.

Es el riesgo real de esta auditoría: no es que falte la copia, es que falta el dato que
convierte las palabras en la clave.

### A2 — El flag `tiene_recuperacion` miente (15 billeteras con saldo)

Dicen `tiene_recuperacion: true` y no existe el fichero. Es lo que hace que nadie note
A1: quien mire el JSON verá que la billetera "tiene recuperación".

### A3 — 11.856,10 KIA en dos nodos cuya clave no está en ningún sitio (media)

| Nodo | KIA | Estado | Clave |
|---|---:|---|---|
| `07e46a1fb829` `kuki_minero[cuda:0]` | 11.697,85 | retirado | pública registrada, **privada no encontrada** |
| `cdad7794abba` `minero_kuki[cuda:0]` | 158,25 | retirado | pública registrada, **privada no encontrada** |

Se buscaron sus claves públicas en todos los `.json` del proyecto: **0 apariciones**. No
están en `validator/`, ni en `ledger/wallet/`, ni en los respaldos de este PC. Si viven
en otro disco o en otro equipo, este informe no puede verlo.

### A4 — 4 billeteras cuyo `direccion` no sale de su clave (baja, saldo 0)

| Fichero | Dirección guardada | Su clave da | Esquema |
|---|---|---|---|
| `kuki_fundador.json` | `KUK77D72508…` | `KUK5E4245A6…` | `ed25519` |
| `nodo_5060ti.json` | `KUK8D5B53C6…` | `KUK77EF125A…` | legado 13-10-2026 |
| `nodo_5070.json` | `KUK746A7A92…` | `KUK221683FA…` | legado 13-10-2026 |
| `nodo_fundador.json` | `KUKB77214B3…` | `KUK1934CB34…` | legado 13-10-2026 |

Son restos del esquema de direcciones anterior (`sha256-clave-hex`). Ninguna tiene
saldo: las cuatro cuentas que nombran no existen en `saldos_cuenta`. **No usarlas**: con
ellas se firmaría por una cuenta distinta de la que dicen.

### A5 — 260 KIA en 6 nodos de prueba sin clave pública (baja, por diseño)

`test_conc` 100, `test_doble` 100, `nodo_fundador_5070` 30, `test_verificador` 10,
`test_h2` 10, `kuki_nodo[cuda:1]` 10. Nunca registraron `clave_publica`: no hay clave que
buscar. Es dinero de pruebas que quedó en cuentas sin dueño.

### A6 — 11 ficheros no declaran versión del KDF (baja)

Son los de octubre (13–16). Hoy `recuperar_billetera()` asume v1 cuando falta el campo, y
la herramienta nueva `tools/recuperacion_billetera.py` prueba v1 y v2 antes de escribir,
así que no bloquea nada. Pero conviene saberlo: **si se recuperan a mano, hay que usar
v1**, no la versión por defecto de las nuevas.

### A7 — 30 copias de `RECUPERACION_*.txt` en claro dentro de `_mezcla_fase1` (media, higiene)

Contraseña + 20 palabras de 15 billeteras, en texto plano en un borrador del disco. git
lo ignora (`.gitignore:125 _mezcla_*/`, 0 ficheros versionados), pero no deja de ser el
mismo secreto duplicado fuera de `ledger/wallet`.

### A8 — `kuki_fundador_nueva_hist.json` no es una billetera (baja)

Es una lista de 108 B, un resto de una migración. Cualquier script que recorra
`ledger/wallet/*.json` suponiendo objetos tropieza con él (esta auditoría lo hizo).

## Prueba de gasto real sobre una copia (28-10-2026)

Ejecutada después del inventario, con `python tools/auditoria_billeteras.py --probar`:
copia del ledger con `VACUUM INTO`, Core aislado en un puerto libre y una transferencia
firmada de verdad desde cada billetera con saldo.

**18 de 18 mueven.** El Core aceptó el gasto de las 18 (17 con 0,01 KIA y
`cliente_wal_verif` con 0,0049, la mitad de su saldo). Que el Core acepte significa que
pasaron las cuatro comprobaciones de un pago: la dirección derivada de la clave pública,
la firma Ed25519, el saldo y el nonce de un solo uso.

Producción no se tocó: el fundador sigue con 15.000.382,375700 y las transferencias
reales siguen siendo 32 (las 18 de la prueba fueron a la copia, que se borró al acabar).

Dos fallos del propio arnés, corregidos al ejecutarlo:

1. **Sellaba en el fichero real.** El Core aislado recibía `KUKIA_DB` de la copia pero no
   `KUKIA_SELLOS_FILE`, así que al arrancar habría añadido un sello a
   `coordinator/sellos.jsonl` de producción. Ahora se copia el fichero de sellos al
   directorio temporal y la prueba comprueba su sha256 antes y después.
2. **Mandaba 0,01 KIA a todas.** A la que tiene 0,009796 la rechazaba por saldo (409) y
   parecía que su clave fallaba. Ahora el importe es la mitad del saldo redondeada a 4
   decimales, que es el máximo que acepta el Core (más decimales dan 400).

## Lo que NO se ha comprobado

- **Si las claves de los dos nodos retirados viven en otro equipo o disco.** Se buscaron
  solo en este PC.
- **Que el papel esté escrito a mano.** El fichero de N: está en blanco en la contraseña;
  lo que haya en el papel físico no lo puede leer este programa.
- **La recuperación del fundador con su contraseña real**: sigue sin ejecutarse.

## Cómo se reproduce

```bash
python tools/auditoria_billeteras.py          # cuentas con saldo, firma real (solo lectura)
python tools/auditar_claves.py                # fugas de claves fuera de ledger/wallet
python tools/auditoria_billeteras.py --probar  # opcional: gasto real sobre una copia
```

La verificación de los ficheros de recuperación (derivar y comparar con la dirección) y
las tablas de arriba se hicieron con consultas directas a `kukia.db` en `mode=ro`; no hay
script que las reproduzca todavía porque se hicieron a mano en esta revisión.
