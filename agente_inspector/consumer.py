import aio_pika

from agente_inspector.service import procesar_inspect_container
from shared.events import InspectContainerEvent
from shared.idempotency import hash_evento, intentar_marcar_procesado


def _crear_on_message(channel: aio_pika.abc.AbstractChannel):
    async def on_message(message: aio_pika.abc.AbstractIncomingMessage) -> None:
        async with message.process(requeue=False):
            clave = hash_evento(message.body)
            if not await intentar_marcar_procesado(clave):
                return

            evento = InspectContainerEvent.model_validate_json(message.body)
            await procesar_inspect_container(evento, channel)

    return on_message


async def start(cola_inspect_container: aio_pika.abc.AbstractQueue, channel: aio_pika.abc.AbstractChannel) -> None:
    await cola_inspect_container.consume(_crear_on_message(channel))
