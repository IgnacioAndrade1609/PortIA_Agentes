import asyncio
import logging

import aio_pika

from agente_inspector.circuit_breaker import CircuitBreaker
from agente_inspector.scrapers.base import ITerminalScraper, ResultadoScraping, ScraperError
from agente_inspector.scrapers.mock_strategy import MockStrategy
from agente_inspector.scrapers.sti_scraper import StiScraper
from agente_inspector.weather import consultar_clima
from shared.events import InspectContainerEvent, RealStatusUpdatedEvent
from shared.publisher import publish_event

logger = logging.getLogger("portia.agente_inspector")

_mock = MockStrategy()
_circuit_breaker = CircuitBreaker()

_SCRAPERS_REALES: dict[str, ITerminalScraper] = {
    "STI": StiScraper(),
}


async def _consultar_terminal(evento: InspectContainerEvent) -> ResultadoScraping:
    scraper_real = _SCRAPERS_REALES.get(evento.terminal_code)

    if scraper_real is None:
        logger.info(f"Sin scraper real registrado para terminal={evento.terminal_code}, usando MockStrategy")
        return await _mock.consultar_estado(evento.container_number, evento.terminal_config)

    if _circuit_breaker.esta_abierto(evento.terminal_code):
        logger.warning(f"Circuit breaker abierto para terminal={evento.terminal_code}, usando MockStrategy")
        return await _mock.consultar_estado(evento.container_number, evento.terminal_config)

    try:
        resultado = await scraper_real.consultar_estado(evento.container_number, evento.terminal_config)
    except ScraperError:
        logger.warning(f"Scraping real falló para terminal={evento.terminal_code}, usando MockStrategy")
        _circuit_breaker.registrar_falla(evento.terminal_code)
        return await _mock.consultar_estado(evento.container_number, evento.terminal_config)

    logger.info(f"Scraping real exitoso para terminal={evento.terminal_code}")
    _circuit_breaker.registrar_exito(evento.terminal_code)
    return resultado


async def procesar_inspect_container(evento: InspectContainerEvent, channel: aio_pika.abc.AbstractChannel) -> None:
    logger.info(f"InspectContainer recibido: container_id={evento.container_id}")

    resultado, (weather_alert, weather_data) = await asyncio.gather(
        _consultar_terminal(evento),
        consultar_clima(evento.terminal_code),
    )

    evento_salida = RealStatusUpdatedEvent(
        container_id=evento.container_id,
        container_number=evento.container_number,
        real_arrival_date=resultado.real_arrival_date,
        port_status=resultado.port_status,
        weather_alert=weather_alert,
        weather_data=weather_data,
        scraper_source=resultado.scraper_source,
        scraped_at=resultado.scraped_at,
    )

    await publish_event(channel, "real_status_updated", evento_salida)
    logger.info(f"RealStatusUpdated publicado: container_id={evento.container_id}, source={resultado.scraper_source}")
