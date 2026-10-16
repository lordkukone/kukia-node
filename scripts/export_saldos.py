"""Exporta saldos agregados del Core a un JSON estatico para la web (rama).
Solo lectura del Core local. No expone claves ni datos personales (el ledger
solo guarda ids/direcciones y cantidades). Uso: python export_saldos.py
"""
import datetime
import json
import urllib.request
from pathlib import Path

CORE = 'http://127.0.0.1:8300'
OUT = Path(__file__).parent.parent / 'docs' / 'saldos.json'


def main():
    try:
        with urllib.request.urlopen(CORE + '/saldos', timeout=15) as r:
            d = json.load(r)
        snap = {'fecha': datetime.datetime.now().isoformat(timespec='seconds'),
                'fuente': 'Core local (/saldos)', 'testnet': True,
                'direcciones': d.get('direcciones', 0),
                'saldos': d.get('saldos', {})}
        OUT.write_text(json.dumps(snap, indent=1), encoding='utf-8')
        print('OK:', OUT, '| direcciones:', snap['direcciones'])
    except Exception as e:
        print('Core no disponible, no se sobrescribe:', str(e)[:80])


if __name__ == '__main__':
    main()
