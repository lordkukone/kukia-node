"""Pruebas del prototipo poscuantico: positivas, negativas y coste.

Se ejecuta con:  python -m pqc.pruebas_pqc
Devuelve 0 solo si TODO pasa. Ninguna prueba toca el ledger real ni el Core:
las billeteras, los sellos y los importes son ficticios y viven en memoria.
"""

from __future__ import annotations

import copy
import json
import tempfile
import threading
import time
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric import ed25519, mldsa

from . import firma_hibrida as fh
from . import llaves, mensaje, sellos_pq, vinculacion

FALLOS: list[str] = []
PASAN = 0

PALABRAS = ("abrir alma ardilla arena artista barco bosque brazo cabeza caja "
            "campo canto carne carta casa cazador centro cerebro cielo cima").split()


def check(nombre: str, fn):
    """Ejecuta una prueba. `fn` devuelve (ok, detalle) o lanza."""
    global PASAN
    try:
        ok, detalle = fn()
    except Exception as e:              # una excepcion es un FALLO, no un aviso
        ok, detalle = False, f"excepcion {type(e).__name__}: {e}"
    if ok:
        PASAN += 1
        print(f"  [OK ] {nombre}" + (f"  · {detalle}" if detalle else ""))
    else:
        FALLOS.append(f"{nombre}: {detalle}")
        print(f"  [MAL] {nombre}  · {detalle}")


def billetera(n=1) -> llaves.Billetera:
    b, _, _ = llaves.crear(PALABRAS, f"prueba-{n}")
    return b


# =====================================================================
#  POSITIVAS
# =====================================================================

def positivo_sobre():
    b = billetera()
    sobre = fh.firmar("transferir", b, de=b.direccion, a="KQ1" + "A" * 32,
                      monto="1.5", nonce="n-1")
    r = fh.verificar(json.dumps(sobre), {"ed25519": b.pub_ed, "mldsa65": b.pub_pq},
                     direccion_origen=b.direccion)
    return r.ok and r.modo == "hibrida", r.motivo or f"modo {r.modo}"


def positivo_tamano_sobre():
    b = billetera()
    sobre = fh.firmar("transferir", b, de=b.direccion, a="KQ1" + "A" * 32,
                      monto="1.5", nonce="n-1")
    crudo = json.dumps(sobre).encode()
    return True, f"{len(crudo)} B de sobre (Ed25519 solo serian ~330 B)"


def positivo_recuperacion():
    """Las 20 palabras + contrasena recuperan EXACTAMENTE la misma billetera."""
    b1 = billetera(7)
    b2 = billetera(7)
    igual = (b1.pub_ed == b2.pub_ed and b1.pub_pq == b2.pub_pq
             and b1.direccion == b2.direccion)
    distinta = billetera(8).direccion != b1.direccion
    return igual and distinta, "determinista y distinta con otra contrasena"


def positivo_semillas_separadas():
    m = llaves.material(PALABRAS, "x")
    a, b = llaves.semilla(m, "ed25519"), llaves.semilla(m, "mldsa65")
    return a != b and len(a) == len(b) == 32, "32 B y distintas"


def positivo_firma_aleatoria():
    """FIPS 204 en modo hedged: dos firmas distintas del mismo mensaje, ambas validas."""
    b = billetera()
    s1, s2 = b.firmar_pq(b"hola"), b.firmar_pq(b"hola")
    ok = s1 != s2
    for s in (s1, s2):
        mldsa.MLDSA65PublicKey.from_public_bytes(bytes.fromhex(b.pub_pq)).verify(
            s, b"hola", context=llaves.CONTEXTO_PQ)
    return ok, "hedged (firmas distintas) y las dos verifican"


def positivo_sellos_encadenados():
    b = billetera()
    e1 = {"hasta": 10, "hash": "a" * 64, "asientos": 10, "suma": "100.5", "ts": 1000}
    s1 = sellos_pq.sellar(e1, b)
    ok1, m1 = sellos_pq.verificar(s1, {"ed25519": b.pub_ed, "mldsa65": b.pub_pq})
    e2 = {"hasta": 20, "hash": "b" * 64, "asientos": 20, "suma": "200.5", "ts": 2000}
    s2 = sellos_pq.sellar(e2, b, anterior=s1)
    ok2, m2 = sellos_pq.verificar(s2, {"ed25519": b.pub_ed, "mldsa65": b.pub_pq},
                                  anterior=s1)
    return ok1 and ok2, m1 or m2 or "dos sellos, el segundo encadenado"


def positivo_sello_v1_intacto():
    """Un sello del formato viejo se verifica igual que ayer."""
    k = ed25519.Ed25519PrivateKey.generate()
    pub = k.public_key().public_bytes_raw().hex()
    cuerpo = {"v": 1, "hasta": 5, "hash": "c" * 64, "asientos": 5, "suma": 5.0,
              "ts": 1500, "prev": ""}
    sello = dict(cuerpo)
    sello["firma"] = k.sign(json.dumps(cuerpo, sort_keys=True, separators=(",", ":"),
                                       ensure_ascii=False).encode()).hex()
    ok, _ = sellos_pq.verificar_v1(sello, pub)
    malo = dict(sello, suma=5.5)
    ok_malo, _ = sellos_pq.verificar_v1(malo, pub)
    return ok and not ok_malo, "v1 valido; con la suma cambiada, rechazado"


def positivo_vinculo():
    v1 = ed25519.Ed25519PrivateKey.generate()
    nueva = billetera()
    v = vinculacion.crear(v1, nueva)
    ok, motivo = vinculacion.verificar(json.dumps(v))
    return ok, motivo or f"{v['origen_v1']} -> {v['destino_v2']}"


def positivo_slhdsa():
    """Tercera firma SLH-DSA (FIPS 205) con el binario openssl."""
    try:
        import shutil
        if not shutil.which("openssl"):
            return True, "omitida: no hay openssl"
        b = billetera()
        with tempfile.TemporaryDirectory() as tmp:
            priv, pub = sellos_pq.crear_clave_slhdsa(Path(tmp))
            sello = sellos_pq.sellar({"hasta": 1, "hash": "d" * 64, "asientos": 1,
                                      "suma": "1", "ts": 10}, b, con_slhdsa=str(priv))
            ok, motivo = sellos_pq.verificar(
                sello, {"ed25519": b.pub_ed, "mldsa65": b.pub_pq,
                        "slhdsa_pub": str(pub)})
            firma_b = len(sello["firmas"]["slhdsa_sha2_128s"]) // 2
            return ok, motivo or f"firma SLH-DSA de {firma_b} B verificada"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


# =====================================================================
#  NEGATIVAS
# =====================================================================

def _sobre_valido():
    b = billetera()
    s = fh.firmar("transferir", b, de=b.direccion, a="KQ1" + "B" * 32,
                  monto="10", nonce="n-9")
    return b, s


def negativo_importe_alterado():
    b, s = _sobre_valido()
    s["cuerpo"]["monto"] = "10000"
    r = fh.verificar(s, {"ed25519": b.pub_ed, "mldsa65": b.pub_pq},
                     direccion_origen=b.direccion)
    return (not r.ok) and "firma" in r.motivo, r.motivo


def negativo_quitar_firma_pq():
    b, s = _sobre_valido()
    del s["firmas"]["mldsa65"]
    r = fh.verificar(s, {"ed25519": b.pub_ed, "mldsa65": b.pub_pq},
                     direccion_origen=b.direccion)
    return not r.ok, r.motivo


def negativo_downgrade_declarado():
    """El atacante re-firma solo con Ed25519 y declara alg=['ed25519']."""
    b, s = _sobre_valido()
    s["alg"] = ["ed25519"]
    s["cuerpo"]["alg"] = ["ed25519"]
    del s["firmas"]["mldsa65"]
    datos = fh._bytes_del_sobre(s)
    s["firmas"]["ed25519"] = b.firmar_ed(datos).hex()
    r = fh.verificar(s, {"ed25519": b.pub_ed, "mldsa65": b.pub_pq},
                     direccion_origen=b.direccion)
    return (not r.ok) and "algoritmos" in r.motivo, r.motivo


def negativo_heredada_se_marca():
    """Telemetria de un nodo viejo: se acepta, pero queda marcada como heredada."""
    b = billetera()
    cuerpo = mensaje.cuerpo("telemetria", ["ed25519"], mensaje.RED,
                            nodo="nodo-1", huella="h" * 64, ts="1000")
    sobre = {"v": 2, "red": mensaje.RED, "tipo": "telemetria", "alg": ["ed25519"],
             "cuerpo": cuerpo, "firmas": {}}
    datos = fh._bytes_del_sobre(sobre)
    sobre["firmas"]["ed25519"] = b.firmar_ed(datos).hex()
    r = fh.verificar(sobre, {"ed25519": b.pub_ed, "mldsa65": b.pub_pq})
    ok = r.ok and r.modo == "heredada"
    # Y si la politica exige hibrida en todo, ni eso:
    r2 = fh.verificar(sobre, {"ed25519": b.pub_ed, "mldsa65": b.pub_pq},
                      politica={"exigir_hibrida_en_todo": True})
    return ok and not r2.ok, f"heredada aceptada ({r.modo}), con exigencia hibrida rechazada"


def negativo_sustitucion_algoritmo():
    """Firma de ML-DSA-44 metida donde va la de 65: se corta por longitud."""
    b, s = _sobre_valido()
    k44 = mldsa.MLDSA44PrivateKey.generate()
    s["firmas"]["mldsa65"] = k44.sign(b"lo que sea").hex()
    r = fh.verificar(s, {"ed25519": b.pub_ed, "mldsa65": b.pub_pq},
                     direccion_origen=b.direccion)
    return (not r.ok) and "2420" in r.motivo, r.motivo


def negativo_red_distinta():
    """Mismo mensaje, otra red: la firma no vale."""
    b, s = _sobre_valido()
    s["red"] = "kukia-testnet"
    s["cuerpo"]["red"] = "kukia-testnet"
    r = fh.verificar(s, {"ed25519": b.pub_ed, "mldsa65": b.pub_pq},
                     direccion_origen=b.direccion)
    return not r.ok, r.motivo


def negativo_replay_otra_operacion():
    """Un sobre de 'transferir' presentado como 'sello' no cuela."""
    b, s = _sobre_valido()
    s["tipo"] = "sello"
    r = fh.verificar(s, {"ed25519": b.pub_ed, "mldsa65": b.pub_pq})
    return (not r.ok) and "coincide" in r.motivo, r.motivo


def negativo_direccion_ajena():
    """Claves de otra billetera con el origen de la victima."""
    b, s = _sobre_valido()
    otra = billetera(99)
    r = fh.verificar(s, {"ed25519": otra.pub_ed, "mldsa65": otra.pub_pq},
                     direccion_origen=b.direccion)
    return (not r.ok) and "duenas" in r.motivo, r.motivo


def negativo_contexto_pq():
    """Firma ML-DSA hecha con otro contexto (otro protocolo): no verifica."""
    b, s = _sobre_valido()
    datos = fh._bytes_del_sobre(s)
    s["firmas"]["mldsa65"] = b.firmar_pq(datos, b"otro-protocolo").hex()
    r = fh.verificar(s, {"ed25519": b.pub_ed, "mldsa65": b.pub_pq},
                     direccion_origen=b.direccion)
    return (not r.ok) and "ML-DSA" in r.motivo, r.motivo


def negativo_nonce_repetido():
    b, s = _sobre_valido()
    almacen = fh.AlmacenNonces()
    primera = almacen.consumir(s["cuerpo"]["de"], s["cuerpo"]["nonce"])
    segunda = almacen.consumir(s["cuerpo"]["de"], s["cuerpo"]["nonce"])
    return primera and not segunda, "el segundo intento con el mismo nonce se rechaza"


def negativo_nonce_concurrente():
    """40 hilos con el mismo nonce: tiene que pasar exactamente uno."""
    almacen = fh.AlmacenNonces()
    pasan = []
    barrera = threading.Barrier(40)

    def golpe():
        barrera.wait()
        if almacen.consumir("KQ1" + "C" * 32, "n-42"):
            pasan.append(1)

    hilos = [threading.Thread(target=golpe) for _ in range(40)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join()
    return len(pasan) == 1, f"pasaron {len(pasan)} de 40"


def negativo_importes():
    malos = ["0", "-1", "nan", "inf", "1.00001", "100000001"]
    rechazados = 0
    detalles = []
    for v in malos:
        try:
            mensaje.importe(v)
            detalles.append(f"ACEPTO {v!r}")
        except mensaje.MensajeInvalido:
            rechazados += 1
    # el float 0.1+0.2 tiene 17 decimales: tiene que caer
    try:
        mensaje.importe(0.1 + 0.2)
        detalles.append("ACEPTO 0.1+0.2")
    except mensaje.MensajeInvalido:
        rechazados += 1
    # y los validos siguen valiendo
    ok_validos = all(mensaje.importe(v) in ("0.0001", "1.5", "100000000", "10")
                     for v in ("0.0001", 1.5, "100000000", 10))
    return rechazados == len(malos) + 1 and ok_validos, \
        f"{rechazados} rechazados, validos OK" if not detalles else "; ".join(detalles)


def negativo_basura_y_tamanos():
    b, s = _sobre_valido()
    casos = {
        "firma truncada": dict(s, firmas=dict(s["firmas"], mldsa65=s["firmas"]["mldsa65"][:200])),
        "hex invalido": dict(s, firmas=dict(s["firmas"], ed25519="zz" * 32)),
        "clave corta": None,
        "sobre no JSON": "{esto no es json",
        "sobre gigante": "x" * 1_000_000,
        "sin campo": {"v": 2, "tipo": "transferir"},
    }
    malos = 0
    for nombre, caso in casos.items():
        if nombre == "clave corta":
            r = fh.verificar(s, {"ed25519": "ab" * 8, "mldsa65": b.pub_pq},
                             direccion_origen=b.direccion)
        else:
            r = fh.verificar(caso, {"ed25519": b.pub_ed, "mldsa65": b.pub_pq},
                             direccion_origen=b.direccion)
        if not r.ok and r.motivo:
            malos += 1
    return malos == len(casos), f"{malos}/{len(casos)} rechazados con motivo escrito"


def negativo_dos_grandes_rapido():
    """Un sobre de 5 MB se corta ANTES de parsear: sin coste apreciable."""
    t0 = time.perf_counter()
    r = fh.verificar("{" + "a" * 5_000_000, {"ed25519": "", "mldsa65": ""})
    ms = (time.perf_counter() - t0) * 1000
    return (not r.ok) and ms < 100, f"rechazado en {ms:.1f} ms"


def negativo_sello_manipulado():
    b = billetera()
    s = sellos_pq.sellar({"hasta": 10, "hash": "a" * 64, "asientos": 10,
                          "suma": "100.5", "ts": 1}, b)
    roto = copy.deepcopy(s)
    roto["suma"] = "999999"
    ok1, _ = sellos_pq.verificar(roto, {"ed25519": b.pub_ed, "mldsa65": b.pub_pq})
    # cadena rota: prev que no corresponde
    e2 = {"hasta": 20, "hash": "b" * 64, "asientos": 20, "suma": "2", "ts": 2}
    s2 = sellos_pq.sellar(e2, b, anterior=s)
    otro_anterior = sellos_pq.sellar({"hasta": 15, "hash": "e" * 64, "asientos": 15,
                                      "suma": "1", "ts": 1}, b)
    ok2, _ = sellos_pq.verificar(s2, {"ed25519": b.pub_ed, "mldsa65": b.pub_pq},
                                 anterior=otro_anterior)
    return (not ok1) and (not ok2), "suma cambiada y cadena rota, las dos rechazadas"


def negativo_vinculo_sin_clave_vieja():
    v1 = ed25519.Ed25519PrivateKey.generate()
    nueva = billetera(5)
    v = vinculacion.crear(v1, nueva)
    del v["firmas"]["v1_ed25519"]
    ok1, m1 = vinculacion.verificar(v)
    # v1 ya vinculada a otra v2 distinta
    v2 = vinculacion.crear(v1, nueva)
    ok2, _ = vinculacion.verificar(v2, ya_vinculadas={v2["origen_v1"]: "KQ1" + "Z" * 32})
    return (not ok1) and (not ok2), f"{m1}; y re-vincular se rechaza"


def negativo_clave_vieja_no_gasta():
    """La clave v1 no puede firmar una transferencia desde la direccion v2."""
    v1 = ed25519.Ed25519PrivateKey.generate()
    nueva = billetera(6)
    sobre = fh.firmar("transferir", nueva, de=nueva.direccion, a="KQ1" + "D" * 32,
                      monto="1", nonce="n-77")
    pub_v1 = v1.public_key().public_bytes_raw().hex()
    r = fh.verificar(sobre, {"ed25519": pub_v1, "mldsa65": nueva.pub_pq},
                     direccion_origen=nueva.direccion)
    return (not r.ok) and "duenas" in r.motivo, r.motivo


def negativo_importe_no_canonico():
    """'1.50' firmado es valido criptograficamente pero no se acepta:
    el mismo dinero no puede tener dos textos."""
    b, s = _sobre_valido()
    s["cuerpo"]["monto"] = "10.00"
    r = fh.verificar(s, {"ed25519": b.pub_ed, "mldsa65": b.pub_pq},
                     direccion_origen=b.direccion)
    return (not r.ok) and "canonico" in r.motivo, r.motivo


def negativo_direcciones_no_se_confunden():
    """La direccion v2 no pasa por el validador v1 del Core, ni al reves."""
    b = billetera()
    v1 = "KUK" + "A" * 38

    def direccion_valida_core(d):          # copia exacta de la regla del Core
        d = (d or "").strip().upper()
        return (len(d) == 41 and d.startswith("KUK")
                and all(c in "0123456789ABCDEF" for c in d[3:]))

    ok = (llaves.familia(b.direccion) == "v2" and llaves.familia(v1) == "v1"
          and not direccion_valida_core(b.direccion)
          and not llaves.es_direccion_v2(v1)
          and not llaves.es_direccion_v1(b.direccion))
    return ok, f"{b.direccion} (v2, {len(b.direccion)} chars) vs {v1} (v1)"


def negativo_sin_red():
    """Sin clave publica poscuantica no se puede verificar una operacion de dinero."""
    b, s = _sobre_valido()
    r = fh.verificar(s, {"ed25519": b.pub_ed}, direccion_origen=b.direccion)
    return (not r.ok) and "falta clave publica" in r.motivo, r.motivo


# =====================================================================
#  COSTE MEDIDO
# =====================================================================

def coste():
    b = billetera()
    n = 200
    msg = b"KUKIA-SIG-v2|kukia-mainnet|transferir|ed25519,mldsa65|" + b'{"a":1}' * 8
    t0 = time.perf_counter()
    for _ in range(n):
        b.firmar_ed(msg)
    t1 = time.perf_counter()
    for _ in range(n):
        ed25519.Ed25519PublicKey.from_public_bytes(bytes.fromhex(b.pub_ed)).verify(
            b.firmar_ed(msg), msg)
    t2 = time.perf_counter()
    t3 = time.perf_counter()
    for _ in range(n):
        s = b.firmar_pq(msg)
    t4 = time.perf_counter()
    pub = mldsa.MLDSA65PublicKey.from_public_bytes(bytes.fromhex(b.pub_pq))
    for _ in range(n):
        pub.verify(s, msg, context=llaves.CONTEXTO_PQ)
    t5 = time.perf_counter()
    print(f"\n  {'operacion':<26}{'ms':>9}")
    print(f"  {'Ed25519 firmar':<26}{(t1 - t0) / n * 1000:>9.3f}")
    print(f"  {'Ed25519 verificar':<26}{(t2 - t1) / n * 1000:>9.3f}")
    print(f"  {'ML-DSA-65 firmar':<26}{(t4 - t3) / n * 1000:>9.3f}")
    print(f"  {'ML-DSA-65 verificar':<26}{(t5 - t4) / n * 1000:>9.3f}")
    sobre = fh.firmar("transferir", b, de=b.direccion, a="KQ1" + "E" * 32,
                      monto="1", nonce="n-1")
    print(f"  sobre hibrido completo: {len(json.dumps(sobre))} B "
          f"(firma ML-DSA {len(sobre['firmas']['mldsa65']) // 2} B, "
          f"clave publica {len(b.pub_pq) // 2} B)")


def main() -> int:
    print("=" * 72)
    print(" PRUEBAS DEL PROTOTIPO POSCUANTICO KUKIA  (todo ficticio, sin ledger)")
    print("=" * 72)
    print("\nPositivas:")
    check("sobre hibrido valido verifica", positivo_sobre)
    check("tamano del sobre", positivo_tamano_sobre)
    check("20 palabras recuperan la misma billetera", positivo_recuperacion)
    check("semillas separadas por algoritmo", positivo_semillas_separadas)
    check("ML-DSA en modo hedged", positivo_firma_aleatoria)
    check("sellos v2 encadenados", positivo_sellos_encadenados)
    check("sello v1 sigue valiendo", positivo_sello_v1_intacto)
    check("vinculo v1 -> v2", positivo_vinculo)
    check("tercera firma SLH-DSA (openssl)", positivo_slhdsa)

    print("\nNegativas:")
    check("importe alterado", negativo_importe_alterado)
    check("firma poscuantica quitada", negativo_quitar_firma_pq)
    check("downgrade declarado a Ed25519", negativo_downgrade_declarado)
    check("telemetria heredada se marca", negativo_heredada_se_marca)
    check("sustitucion de algoritmo", negativo_sustitucion_algoritmo)
    check("replay en otra red", negativo_red_distinta)
    check("replay de otra operacion", negativo_replay_otra_operacion)
    check("direccion ajena", negativo_direccion_ajena)
    check("contexto poscuantico distinto", negativo_contexto_pq)
    check("nonce repetido", negativo_nonce_repetido)
    check("nonce concurrente (40 hilos)", negativo_nonce_concurrente)
    check("importes invalidos", negativo_importes)
    check("basura, hex invalido y tamanos", negativo_basura_y_tamanos)
    check("sobre de 5 MB cortado antes de parsear", negativo_dos_grandes_rapido)
    check("sello manipulado y cadena rota", negativo_sello_manipulado)
    check("vinculo sin la clave vieja", negativo_vinculo_sin_clave_vieja)
    check("la clave vieja no gasta desde la v2", negativo_clave_vieja_no_gasta)
    check("importe no canonico ('10.00')", negativo_importe_no_canonico)
    check("direcciones v1 y v2 no se confunden", negativo_direcciones_no_se_confunden)
    check("operacion de dinero sin clave poscuantica", negativo_sin_red)

    coste()

    total = PASAN + len(FALLOS)
    print("\n" + "=" * 72)
    if FALLOS:
        print(f" RESULTADO: {PASAN}/{total} OK — {len(FALLOS)} FALLOS")
        for f in FALLOS:
            print(f"   - {f}")
        return 1
    print(f" RESULTADO: {PASAN}/{total} OK — ninguna falla")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
