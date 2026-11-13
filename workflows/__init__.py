"""Workflows independientes de los agentes.

Cada carpeta contiene un runner autónomo que conecta RabbitMQ, declara sus colas
y arranca únicamente el consumidor que le corresponde:

- ``workflows.lector``    → consume ``shipment_created`` y publica ``DataExtracted``.
- ``workflows.inspector`` → consume ``inspect_container`` y publica ``RealStatusUpdated``.

Así se puede levantar un agente sin arrastrar al otro; ``main.py`` arranca ambos en
un mismo proceso.
"""
