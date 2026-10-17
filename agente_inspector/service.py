"""Lógica de negocio del Agente Inspector: separada del transporte (consumer.py)."""

import logging

from shared.events import InspectContainerEvent

logger = logging.getLogger("portia.agente_inspector")


async def procesar_inspect_container(evento: InspectContainerEvent) -> None:
    logger.info(f"InspectContainer recibido: container_id={evento.container_id}")
    # TODO: scraping del terminal (evento.terminal_config) vía Strategy Pattern
    # (ITerminalScraper por terminal + MockStrategy), consulta de clima, y
    # publicación de RealStatusUpdatedEvent vía shared.publisher.publish_event.
