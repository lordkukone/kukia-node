"""Prototipo poscuantico de KUKIA (aislado: no toca el Core ni el ledger real).

Modulos:
    mensaje       - bytes canonicos que se firman (dominio, version, importes)
    llaves        - semillas separadas por algoritmo y direcciones v2
    firma_hibrida - Ed25519 + ML-DSA-65 con politica anti-downgrade
    sellos_pq     - sellos del ledger v2 sin invalidar los v1
    vinculacion   - migracion firmada de una billetera v1 a una v2
"""
