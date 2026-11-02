"""Workflows independientes de los agentes.

Cada carpeta contiene un runner autónomo que conecta RabbitMQ, declara sus colas
y arranca únicamente el consumidor que le corresponde. Así se puede levantar el
lector sin arrastrar al inspector, y replicar el mismo patrón para el inspector.
"""
