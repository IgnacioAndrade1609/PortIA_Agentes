"""Transporte: traduce mensajes crudos de RabbitMQ a llamadas de servicio.

No contiene lógica de negocio — eso vive en service.py. Aquí solo se resuelve la
**idempotencia** (procesamiento + respuesta) contra Redis antes de publicar.
"""

import logging

import aio_pika

from agente_lector.service import procesar_shipment_created
from shared.events import DataExtractedEvent, ShipmentCreatedEvent
from shared.idempotency import (
    ESTADO_LISTO,
    ESTADO_NUEVO,
    guardar_resultado,
    hash_evento,
    liberar_trabajo,
    marcar_fallido,
    reclamar_trabajo,
)
from shared.publisher import publish_event

logger = logging.getLogger("portia.agente_lector")

#: MassTransit declara la cola del consumer como ``data_extracted``. Publicando al
#: default exchange con esa routing key, el evento llega directo a la cola del Core.
ROUTING_KEY_DATA_EXTRACTED = "data_extracted"


async def on_message(
    message: aio_pika.abc.AbstractIncomingMessage,
    channel: aio_pika.abc.AbstractChannel,
) -> None:
    # requeue=False: si la excepción sube (JSON inválido, validación Pydantic, fallo del
    # documento, etc.) aio-pika la manda directo a la DLQ declarada en shared/queues.py.
    async with message.process(requeue=False):
        evento = ShipmentCreatedEvent.model_validate_json(message.body)
        clave = hash_evento(message.body)

        reclamo = await reclamar_trabajo(clave)
        if reclamo.estado == ESTADO_LISTO:
            # Misma entrega repetida: se reutiliza la respuesta ya calculada y no se
            # vuelve a llamar al LLM (evita coste y resultados distintos).
            logger.info(
                "evento duplicado: se reutiliza la respuesta en caché (document_id=%s, eventos=%d)",
                evento.document_id,
                len(reclamo.resultado or []),
            )
            await _publicar(channel, reclamo.resultado or [])
            return

        if reclamo.estado != ESTADO_NUEVO:
            # Otro worker lo tiene tomado (en_proceso) o ya falló antes (fallido).
            logger.info(
                "evento ignorado: trabajo en estado '%s' (document_id=%s)",
                reclamo.estado,
                evento.document_id,
            )
            return

        try:
            eventos = await procesar_shipment_created(evento)
        except Exception as exc:
            # Transitorio (red, 5xx, LLM saturado) → se suelta el lock para reintentar.
            # Permanente (documento inexistente, formato no soportado) → se recuerda el fallo.
            if getattr(exc, "transitorio", True):
                await liberar_trabajo(clave)
            else:
                await marcar_fallido(clave, str(exc))
            logger.error("fallo procesando document_id=%s: %s", evento.document_id, exc)
            raise

        datos = [evento_extraido.model_dump(mode="json") for evento_extraido in eventos]
        await guardar_resultado(clave, datos)
        await _publicar(channel, datos)


async def _publicar(channel: aio_pika.abc.AbstractChannel, datos: list[dict]) -> None:
    """Publica los eventos (revalidados: lo cacheado se serializa como JSON)."""
    for datos_evento in datos:
        event = DataExtractedEvent.model_validate(datos_evento)
        await publish_event(channel, ROUTING_KEY_DATA_EXTRACTED, event)


async def start(cola_shipment_created: aio_pika.abc.AbstractQueue) -> None:
    canal = cola_shipment_created.channel

    async def _on_message(message: aio_pika.abc.AbstractIncomingMessage) -> None:
        await on_message(message, canal)

    await cola_shipment_created.consume(_on_message)
