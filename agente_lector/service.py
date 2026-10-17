"""Lógica de negocio del Agente Lector: separada del transporte (consumer.py)
para que se pueda probar sin necesitar una conexión a RabbitMQ.
"""

import logging

from shared.events import ShipmentCreatedEvent

logger = logging.getLogger("portia.agente_lector")


async def procesar_shipment_created(evento: ShipmentCreatedEvent) -> None:
    logger.info(f"ShipmentCreated recibido: shipment_id={evento.shipment_id}")
    # TODO: descargar el documento (evento.document_url), extraer datos con IA,
    # normalizar y publicar DataExtractedEvent vía shared.publisher.publish_event.
