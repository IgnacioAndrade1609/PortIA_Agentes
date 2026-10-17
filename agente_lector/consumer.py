"""Transporte: traduce mensajes crudos de RabbitMQ a llamadas de servicio.

No contiene lógica de negocio — eso vive en service.py.
"""

import aio_pika

from agente_lector.service import procesar_shipment_created
from shared.events import ShipmentCreatedEvent
from shared.idempotency import hash_evento, intentar_marcar_procesado


async def on_message(message: aio_pika.abc.AbstractIncomingMessage) -> None:
    # requeue=False: si la excepción sube (JSON inválido, validación Pydantic, etc.)
    # aio-pika la manda directo a la DLQ declarada en shared/queues.py.
    async with message.process(requeue=False):
        clave = hash_evento(message.body)
        if not await intentar_marcar_procesado(clave):
            return

        evento = ShipmentCreatedEvent.model_validate_json(message.body)
        await procesar_shipment_created(evento)


async def start(cola_shipment_created: aio_pika.abc.AbstractQueue) -> None:
    await cola_shipment_created.consume(on_message)
