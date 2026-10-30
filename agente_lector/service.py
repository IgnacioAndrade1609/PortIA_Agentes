"""Lógica de negocio del Agente Lector: separada del transporte (consumer.py)
para que se pueda probar sin necesitar una conexión a RabbitMQ.
"""

import logging

from agente_lector.pipeline import extraer_documento
from shared.events import DataExtractedEvent, ShipmentCreatedEvent

logger = logging.getLogger("portia.agente_lector")


async def procesar_shipment_created(evento: ShipmentCreatedEvent) -> list[DataExtractedEvent]:
    """Extrae el BL del documento y devuelve los eventos a publicar (uno por contenedor)."""
    logger.info(
        "ShipmentCreated recibido: shipment_id=%s document_id=%s",
        evento.shipment_id,
        evento.document_id,
    )
    return await extraer_documento(
        url=evento.document_url,
        shipment_id=evento.shipment_id,
        document_id=evento.document_id,
        document_type=evento.document_type,
    )
