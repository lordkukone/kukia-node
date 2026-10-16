"""KUKIA — verificación determinista con ONNX en CPU (referencia del Core).

El MINERO trabaja en GPU. El CORE usa este módulo como REFERENCIA determinista
en CPU para contrastar resultados. Autotest aislado: no toca red, BD ni billeteras.

Uso:  python ia_onnx_cpu.py --probar-onnx   (sale 0 si la CPU es determinista)
"""
import argparse
import os
import sys
import tempfile


def ejecutar_cpu(modelo_path, entradas):
    """Ejecuta un modelo ONNX solo con CPUExecutionProvider."""
    import onnxruntime as rt
    sesion = rt.InferenceSession(modelo_path, providers=["CPUExecutionProvider"])
    return sesion.run(None, entradas)


def verificar_determinismo(modelo_path, entradas, repeticiones=2):
    """Ejecuta N veces y exige salidas bit-idénticas. Devuelve True si pasa."""
    import numpy as np
    primera = None
    for _ in range(repeticiones):
        salidas = ejecutar_cpu(modelo_path, entradas)
        planas = [np.asarray(s) for s in salidas]
        if primera is None:
            primera = planas
        else:
            for a, b in zip(primera, planas):
                if a.shape != b.shape or not bool((a == b).all()):
                    return False
    return True


def comparar_con_tolerancia(resultado, esperado, tolerancia=1e-5):
    """Compara un resultado contra el esperado con tolerancia (ruido float GPU)."""
    import numpy as np
    r = np.asarray(resultado, dtype=np.float64)
    e = np.asarray(esperado, dtype=np.float64)
    if r.shape != e.shape:
        return False
    return bool((abs(r - e) <= tolerancia).all())


def config_hash(modelo_path, entrada_json):
    """DNI de la tarea: hash de (bytes del modelo + entrada)."""
    import hashlib
    import json
    h = hashlib.sha256()
    with open(modelo_path, "rb") as f:
        for bloque in iter(lambda: f.read(65536), b""):
            h.update(bloque)
    h.update(json.dumps(entrada_json, sort_keys=True).encode())
    return h.hexdigest()


def ejecutar_tarea(modelo_path, entrada_lista):
    """Ejecuta la tarea embeddings en CPU y devuelve el vector resultado."""
    import numpy as np
    x = np.asarray(entrada_lista, dtype=np.float32)
    salidas = ejecutar_cpu(modelo_path, {"X": x})
    return [float(v) for v in np.asarray(salidas[0]).flatten().tolist()]


def _construir_mini_onnx(destino):
    """Modelo mínimo Gemm con pesos fijos (semilla 42) para el autotest."""
    import numpy as np
    from onnx import TensorProto, helper
    import onnx
    rng = np.random.default_rng(42)
    W = rng.standard_normal((8, 4), dtype=np.float32)
    B = rng.standard_normal((4,), dtype=np.float32)
    nodo = helper.make_node("Gemm", inputs=["X", "W", "B"], outputs=["Y"])
    g = helper.make_graph(
        [nodo], "mini",
        [helper.make_tensor_value_info("X", TensorProto.FLOAT, [1, 8])],
        [helper.make_tensor_value_info("Y", TensorProto.FLOAT, [1, 4])],
        [helper.make_tensor("W", TensorProto.FLOAT, [8, 4], W.flatten().tolist()),
         helper.make_tensor("B", TensorProto.FLOAT, [4], B.flatten().tolist())])
    m = helper.make_model(g, producer_name="kukia-test")
    m.opset_import[0].version = 13
    m.ir_version = 8
    onnx.save(m, destino)
    return destino


def probar_onnx():
    import numpy as np
    with tempfile.TemporaryDirectory(prefix="kukia_onnx_") as d:
        modelo = _construir_mini_onnx(os.path.join(d, "mini.onnx"))
        x = np.ones((1, 8), dtype=np.float32)
        ok = verificar_determinismo(modelo, {"X": x}, repeticiones=2)
        print("probar-onnx:", "OK determinista en CPU" if ok else "FALLO")
        return ok


def main(argv=None):
    p = argparse.ArgumentParser(description="Autotest ONNX CPU (referencia KUKIA).")
    p.add_argument("--probar-onnx", action="store_true",
                   help="Ejecuta el autotest determinista en CPU.")
    args = p.parse_args(argv)
    if args.probar_onnx:
        return 0 if probar_onnx() else 1
    p.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
