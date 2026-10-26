# Migracion del dinero a unidades enteras — informe de conciliacion

Generado: 2026-10-25 21:19:45
Copia migrada: `C:\Users\kuki\Desktop\KUKIA\migracion_entera\kukia_entera.db`
Origen leido (solo lectura): `C:\Users\kuki\Desktop\KUKIA\coordinator\ledger\kukia.db`
Escala: 1 KIA = 10.000.000.000 unidades (1e-10 KIA por unidad)

## Resumen

| Que | Coma flotante (antes) | Enteros (despues) |
|---|---|---|
| Suministro emitido | `15088160.871962598` | `15.088.160.8719721777` |
| Suma de asientos | `15088160.87197193` | `15.088.160.8719721777` |
| Suma de saldos | `15088160.87197194` | `15.088.160.8719721777` |

- Desvio que YA existia con coma flotante (saldos - suministro): **0.0000093430 KIA** (93430 unidades).
- Desvio despues de convertir (saldos - suministro): **0 unidades**.
- Suministro: el float decia `15088160.871962598`; el ledger, en unidades, dice `15.088.160.8719721777`. Correccion de la coma flotante: **95797 unidades**.
- Lo que mueve el redondeo de la conversion, en total (media unidad por asiento como maximo): 4666.894652839487614453754458 unidades = 0.0000004666 KIA.
- Cuentas recalculadas desde el ledger: 1 (el resto ya cuadraba).
- Tope del suministro: 100.000.000 KIA = 1.000.000.000.000.000.000 unidades; lo emitido ocupa 1/6.6 del margen del entero.

## Partida doble

- Grupos de cargo/abono revisados: 41
- Grupos que NO suman cero en enteros: **0**
- Grupos que no sumaban cero ni con coma flotante (|desvio| > 1e-12): 0 (desvio maximo 0.000e+00 KIA)
- Emision sin pareja: 150881608719721777 unidades; coincide con el suministro
- Resultado: OK

| Grupo | Asientos | Suma en enteros | Suma en flotante |
|---|---|---|---|
| `2a48fd5d24b2` | 6 | 0 | `0.0` |
| `643e42ad6158` | 4 | 0 | `0.0` |
| `806fae27ba4f` | 6 | 0 | `0.0` |
| `84274510fad2` | 4 | 0 | `0.0` |
| `9533722671b9` | 6 | 0 | `0.0` |

## Correccion de la coma flotante (1 cuentas)

Estas son las cuentas cuyo saldo materializado no era exactamente la suma de
sus asientos. La migracion las iguala al ledger (la fuente de verdad) y aqui
queda apuntado cuanto se movio y por que.

| Cuenta | Asientos | Antes (float) | Ahora (enteros) | Correccion (u) |
|---|---|---|---|---|
| `KUKE1A0EB09FD9BF49FF41B884EDBDF6DC0E3E96F` | 687401 | `4686.831005857656` | 4.686.8310060949 | +2372 |

## La cadena de hashes

- Asientos anteriores a la cadena (sin hash, no verificables): 175
- Asientos sellados verificados: 779407
- Ancla (primer asiento sellado): `{'id': 176, 'hash_prev': ''}`
- Cabeza de la cadena: `6ea8bf0dbb2f2afd732d229bae41e30c`
- Resultado: OK, la cadena encadena y cada hash corresponde a su asiento

## Doble escritura

- Cuentas escritas en las dos representaciones: 45
- Cuentas donde las dos no dicen lo mismo: 0

## Conversion por tabla

| Tabla | Columna | Filas | Residuo total (unidades) |
|---|---|---|---|
| ledger | kuki | 779582 | 4664.410000000000 |
| saldos_cuenta | total | 45 | 0.9546528394876144537544580000 |
| transferencias | monto | 32 | 0.0000 |
| tareas | prepago | 200181 | 0 |
| tareas | precio_cliente | 200181 | 0.510000000000 |
| tareas | pagado | 200181 | 0.510000000000 |
| clientes | gastado | 16 | 0.510000000000 |

## Conciliacion cuenta por cuenta (45 cuentas)

`delta_u` = saldo entero - suma de asientos enteros. El margen es media
unidad por asiento convertido (redondeo) mas una. Si `delta_u` sale del
margen, esa cuenta tiene un problema de verdad, no de redondeo.

| Cuenta | Asientos | Suma de asientos (KIA) | Saldo (KIA) | delta_u | Correccion_u | Estado |
|---|---|---|---|---|---|---|
| `07e46a1fb829` |    6468 |      11.697.8499000000 |      11.697.8499000000 |   +0 |     +0 | OK |
| `0be9be21ba15` |       2 |          10.0000000000 |          10.0000000000 |   +0 |     +0 | OK |
| `1390e2621e7a` |       3 |           0.0000000000 |           0.0000000000 |   +0 |     +0 | OK |
| `27ad4b110f2e` |       2 |         100.0000000000 |         100.0000000000 |   +0 |     +0 | OK |
| `4c488fa71d61` |       3 |           0.0000000000 |           0.0000000000 |   +0 |     +0 | OK |
| `520c99c56cf8` |       4 |          30.0000000000 |          30.0000000000 |   +0 |     +0 | OK |
| `5b72627ac027` |    2619 |           0.0000000000 |           0.0000000000 |   +0 |     +0 | OK |
| `67a2cf16e5bd` |       1 |          10.0000000000 |          10.0000000000 |   +0 |     +0 | OK |
| `6f7756aabc78` |       1 |          10.0000000000 |          10.0000000000 |   +0 |     +0 | OK |
| `76c9038d1698` |       1 |           0.0000000000 |           0.0000000000 |   +0 |     +0 | OK |
| `7f203c4add7d` |       3 |           0.0000000000 |           0.0000000000 |   +0 |     +0 | OK |
| `8657020a6670` |      24 |           0.0000000000 |           0.0000000000 |   +0 |     +0 | OK |
| `96df5ffad36c` |    6377 |           0.0000000000 |           0.0000000000 |   +0 |     +0 | OK |
| `9a1c4380d3c2` |       2 |           0.0000000000 |           0.0000000000 |   +0 |     +0 | OK |
| `9f9c273bc748` |       2 |           0.0000000000 |           0.0000000000 |   +0 |     +0 | OK |
| `KUK080DF3C598B5285730CCFDAB6B9A50AD126661` |       3 |           0.0498641178 |           0.0498641178 |   +0 |     +0 | OK |
| `KUK14F3F9E126406CC60327BB1D5D1FA0830807EC` |       3 |           0.0399860000 |           0.0399860000 |   +0 |     +0 | OK |
| `KUK1F6345D5CBDA23C6BD830B72B1D67B2BA60234` |       3 |           0.0498963200 |           0.0498963200 |   +0 |     +0 | OK |
| `KUK269B067A2DA4C1F974ED5D6F5B661371DFF4AE` |       3 |           0.0499000000 |           0.0499000000 |   +0 |     +0 | OK |
| `KUK33773B881B1FF2CBBF81C2656D6AAB2CFBDBD9` |       3 |           0.0499000000 |           0.0499000000 |   +0 |     +0 | OK |
| `KUK4494E1F4F573C8EAB3BCA36C0B16EA8CA24934` |      66 |           0.5307790000 |           0.5307790000 |   +0 |     +0 | OK |
| `KUK46D7ECA2EED95884D6FF97A203432CADE3A5E6` |    2349 |          13.3955749380 |          13.3955749380 |   +0 |     +0 | OK |
| `KUK4E53E98FC82211048E4B714AC94F2E57688D6D` |       2 |           0.0499450000 |           0.0499450000 |   +0 |     +0 | OK |
| `KUK5305845DCC82BFC11AF1F7D2BE1C4453827EBC` |       3 |           0.0097963200 |           0.0097963200 |   +0 |     +0 | OK |
| `KUK545D9183B65C658839CF39FC3F146E9D7CE083` |       3 |           0.0499000000 |           0.0499000000 |   +0 |     +0 | OK |
| `KUK666B9EF7AE674DD544427B2AF53BEBB78AC335` |       1 |           0.0375350000 |           0.0375350000 |   +0 |     +0 | OK |
| `KUK77D72508BD940C9BA969B0402A3CF1F9679BE3` |       2 |           0.0000000000 |           0.0000000000 |   +0 |     +0 | OK |
| `KUK7B9929F9F56D130059D28E70B4476D02634B41` |      42 |           0.5098178170 |           0.5098178170 |   +0 |     +0 | OK |
| `KUK90BA61EDA315C341434AF93CC4D82D80D9DD21` |   69864 |      70.951.0990750000 |      70.951.0990750000 |   +0 |     +0 | OK |
| `KUK9E8B44CD7FE3AF178039453D3EEDF391EEC0CE` |      25 |  15.000.382.3757000000 |  15.000.382.3757000000 |   +0 |     +0 | OK |
| `KUKA15980892BE783A91DB0EC88996AEA0F7CCAF5` |       3 |           0.0497998595 |           0.0497998595 |   +0 |     +0 | OK |
| `KUKA4F98CE947608D5EE35CDDF3ACBDE5E7A6B977` |    1488 |           8.5301128510 |           8.5301128510 |   +0 |     +0 | OK |
| `KUKA6626021B10A2E83F9AED44D8125FB6CDE0F9A` |       1 |           0.0500000000 |           0.0500000000 |   +0 |     +0 | OK |
| `KUKA994AE86B32510B7B8212C68548FA9010CD4BB` |      13 |           0.8632840000 |           0.8632840000 |   +0 |     +0 | OK |
| `KUKBABE2A062AA6481F06218341063410B65C3D87` |       3 |           0.0497998595 |           0.0497998595 |   +0 |     +0 | OK |
| `KUKC093289C00A862E7CDC306ABD58342DC3F7AF0` |       2 |           0.0499000000 |           0.0499000000 |   +0 |     +0 | OK |
| `KUKD27A0F4E0B6B6B5A3E4B68A91BBA048FEAE663` |       3 |           0.0499000000 |           0.0499000000 |   +0 |     +0 | OK |
| `KUKE1A0EB09FD9BF49FF41B884EDBDF6DC0E3E96F` |  687401 |       4.686.8310060949 |       4.686.8310060949 |   +0 |  +2372 | OK |
| `KUKIA_ESCROW` |      34 |           0.0000000000 |           0.0000000000 |   +0 |     +0 | OK |
| `aed08a45b087` |      45 |           0.0000000000 |           0.0000000000 |   +0 |     +0 | OK |
| `af5edff051d8` |       1 |         100.0000000000 |         100.0000000000 |   +0 |     +0 | OK |
| `cdad7794abba` |      22 |         158.2506000000 |         158.2506000000 |   +0 |     +0 | OK |
| `d4040a724742` |      57 |           0.0000000000 |           0.0000000000 |   +0 |     +0 | OK |
| `f8d7728b8953` |    2622 |           0.0000000000 |           0.0000000000 |   +0 |     +0 | OK |
| `fff50dd0a224` |       3 |           0.0000000000 |           0.0000000000 |   +0 |     +0 | OK |

## Lo que este informe NO dice

- No dice que el ledger real se haya migrado: se ha migrado una copia.
- No dice que el Core ya escriba en enteros: eso es el paso siguiente.
- No hay auditoria independiente de la conversion: es una prueba propia,
  reproducible con `python tools/prueba_migracion_entera.py`.
