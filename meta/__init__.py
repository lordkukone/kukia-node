"""KUKIA META - prototipo aislado del cerebro cientifico.

Regla unica: **un numero no vale por venir de un modelo, vale porque otro camino
lo comprueba**. Aqui se guarda que se pregunto, como se calculo, quien lo
comprobo por otra via, con que limitaciones y cuanto se acerco a la respuesta
conocida.

Aislado de la moneda: `meta/estado/meta.db` es otra base, no toca el ledger, no
paga ni cobra. Modulos:

    esquema       tablas y escalones epistemicos
    memoria       rastro y estado deducido de las pruebas
    pasos         registro cientifico: cada paso con dos caminos independientes
    ingesta       problema -> pregunta con criterio de exito
    planificador  valida paso y parametros (no ejecuta texto libre)
    ejecutor      calcula dos veces y anota el entorno
    verificador   recalcula por otra via y compara
    proceso       el pipeline completo
    evaluacion    banco con respuestas conocidas + deteccion de errores
"""
__version__ = "0.1.0"
