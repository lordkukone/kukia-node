"""KUKIAMINER V1.0 BETA — medidor real (rama, sin commit).
Hardware, benchmarks con FLOP contados, ONNX, LLM local, energia.
Cero descargas: usa torch/numpy/onnxruntime instalados. N/D si falta algo.
Uso: python medidor.py --mostrar | --json RUTA | --rapido
"""
import json
import platform
import subprocess
import sys
import time

SALIDA = {}


def _smi():
    try:
        out = subprocess.run(
            ['nvidia-smi', '--query-gpu=index,name,temperature.gpu,'
             'power.draw,power.limit,utilization.gpu,utilization.memory,'
             'memory.used,memory.total,clocks.gr,clocks.mem',
             '--format=csv,noheader,nounits'],
            capture_output=True, text=True, timeout=20)
        gpus = []
        for lin in (out.stdout or '').strip().split('\n'):
            p = [x.strip() for x in lin.split(',')]
            if len(p) == 11:
                gpus.append({'indice': int(p[0]), 'modelo': p[1],
                             'temp_c': p[2], 'potencia_w': p[3],
                             'limite_w': p[4], 'uso_pct': p[5],
                             'uso_mem_pct': p[6], 'vram_usada_mib': p[7],
                             'vram_total_mib': p[8], 'freq_nucleo_mhz': p[9],
                             'freq_mem_mhz': p[10], 'origen': 'nvidia-smi'})
        return gpus
    except Exception as e:
        return [{'error': 'N/D: ' + str(e)[:100]}]


def hardware():
    import shutil
    d = {'sistema': platform.system() + ' ' + platform.release(),
         'cpu_modelo': platform.processor() or 'N/D',
         'gpus': _smi(),
         'ram': 'N/D'}
    try:
        import psutil
        m = psutil.virtual_memory()
        d['ram'] = {'total_gb': round(m.total / 1e9, 1),
                    'usada_gb': round(m.used / 1e9, 1)}
        d['cpu_nucleos'] = psutil.cpu_count(logical=False)
        d['cpu_hilos'] = psutil.cpu_count(logical=True)
    except Exception:
        pass
    try:
        import torch
        d['torch'] = torch.__version__
        d['cuda_disponible'] = torch.cuda.is_available()
    except Exception as e:
        d['torch'] = 'N/D: ' + str(e)[:80]
    return d


def benchmark(dev, n=1024, reps=20, dtype='fp32'):
    """Matmul NxN con FLOP contados (2*N^3 por mult). Devuelve GFLOPS medidos."""
    import torch
    dt = {'fp32': torch.float32, 'fp16': torch.float16, 'bf16': torch.bfloat16,
          'int8': torch.int8}[dtype]
    if dtype == 'int8':
        a = torch.randint(0, 5, (n, n), device=dev, dtype=dt)
        b = torch.randint(0, 5, (n, n), device=dev, dtype=dt)
        op = lambda: (a.to(torch.int32) @ b.to(torch.int32))
        flops = 2 * n ** 3
    else:
        a = torch.randn(n, n, device=dev, dtype=dt)
        b = torch.randn(n, n, device=dev, dtype=dt)
        op = lambda: (a @ b)
        flops = 2 * n ** 3
    try:
        for _ in range(3):
            op()
        if 'cuda' in str(dev):
            torch.cuda.synchronize()
        t0 = time.time()
        for _ in range(reps):
            op()
        if 'cuda' in str(dev):
            torch.cuda.synchronize()
        seg = max(time.time() - t0, 1e-6)
        gflops = flops * reps / seg / 1e9
        c = op()
        ok = bool((c.abs().sum() > 0).item())
        return {'device': str(dev), 'precision': dtype, 'n': n, 'reps': reps,
                'seg': round(seg, 3), 'gflops_medidos': round(gflops, 2),
                'correcto': ok}
    except Exception as e:
        return {'device': str(dev), 'precision': dtype, 'error': str(e)[:120]}


def onnx_info():
    r = {}
    try:
        import onnxruntime as rt
        r['version'] = rt.__version__
        r['proveedores'] = rt.get_available_providers()
    except Exception as e:
        r['error'] = 'N/D: ' + str(e)[:100]
    return r


def llm_local():
    r = {'ollama': 'N/D', 'lmstudio': 'N/D'}
    import urllib.request
    try:
        with urllib.request.urlopen('http://127.0.0.1:11434/api/tags', timeout=5) as x:
            d = json.load(x)
            r['ollama'] = [m.get('name') for m in d.get('models', [])]
    except Exception as e:
        r['ollama'] = 'N/D: ' + str(e)[:60]
    try:
        with urllib.request.urlopen('http://127.0.0.1:1234/v1/models', timeout=5) as x:
            d = json.load(x)
            r['lmstudio'] = [m.get('id') for m in d.get('data', [])]
    except Exception as e:
        r['lmstudio'] = 'N/D: ' + str(e)[:60]
    return r


def main(argv=None):
    a = argv or sys.argv[1:]
    rapido = '--rapido' in a
    out = {'fecha': time.strftime('%Y-%m-%d %H:%M'), 'hardware': hardware(),
           'onnx': onnx_info(), 'llm': llm_local(), 'benchmarks': []}
    import torch
    devs = ['cpu']
    if torch.cuda.is_available():
        devs += ['cuda:%d' % i for i in range(torch.cuda.device_count())]
    reps = 5 if rapido else 20
    n = 512 if rapido else 1024
    for dev in devs:
        for dt in (['fp32'] if dev == 'cpu' else ['fp32', 'fp16']):
            out['benchmarks'].append(benchmark(dev, n=n, reps=reps, dtype=dt))
    if '--json' in a:
        p = a[a.index('--json') + 1]
        open(p, 'w').write(json.dumps(out, indent=1))
        print('json:', p)
    else:
        print(json.dumps(out, indent=1)[:3000])
    return 0


if __name__ == '__main__':
    sys.exit(main())
