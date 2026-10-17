"""Transporte: traduce mensajes crudos de RabbitMQ a llamadas de servicio.

No contiene lógica de negocio — eso vive en service.py.
"""

import aio_pika

from agente_inspector.service import procesar_inspect_container
from shared.events import InspectContainerEvent
from shared.idempotency import hash_evento, intentar_marcar_procesado


async def on_message(message: aio_pika.abc.AbstractIncomingMessage) -> None:
    async with message.process(requeue=False):
        clave = hash_evento(message.body)
        if not await intentar_marcar_procesado(clave):
            return

        evento = InspectContainerEvent.model_validate_json(message.body)
        await procesar_inspect_container(evento)


async def start(cola_inspect_container: aio_pika.abc.AbstractQueue) -> None:
    await cola_inspect_container.consume(on_message)
