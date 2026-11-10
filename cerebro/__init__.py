"""CEREBRO — memoria semantica local de KUKIA.

Aislado a proposito, como `meta/`: lee el proyecto, no lo escribe; no importa el
Core, no toca el ledger, no paga ni cobra.

Piezas:
    cerebro.config    que se indexa, que NUNCA se indexa, y con que modelo
    cerebro.ingesta   descubrir ficheros y trocearlos con cita de lineas
    cerebro.indice    SQLite (texto) + numpy (vectores) y la busqueda
    cerebro.buscar    `python -m cerebro.buscar "..." -k 8`
    cerebro.evaluar   recall@k medido contra un banco de preguntas conocidas
"""
