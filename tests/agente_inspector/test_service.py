from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from agente_inspector import service
from agente_inspector.scrapers.base import ITerminalScraper, ResultadoScraping, ScraperError
from shared.events import InspectContainerEvent

EVENTO = InspectContainerEvent(
    container_id=1,
    container_number="MSCU1234567",
    terminal_code="STI",
    terminal_config={"url": "https://sti.cl"},
)


@pytest.fixture(autouse=True)
def mock_consultar_clima():
    with patch("agente_inspector.service.consultar_clima", new=AsyncMock(return_value=(False, None))):
        yield


class _ScraperFalso(ITerminalScraper):
    def __init__(self, resultado=None, error=None):
        self._resultado = resultado
        self._error = error

    async def consultar_estado(self, container_number: str, terminal_config: dict) -> ResultadoScraping:
        if self._error is not None:
            raise self._error
        return self._resultado


async def test_usa_mock_si_no_hay_scraper_real_registrado(monkeypatch):
    monkeypatch.setitem(service._SCRAPERS_REALES, "STI", None)
    service._SCRAPERS_REALES.pop("STI", None)

    canal_falso = AsyncMock()
    with patch("agente_inspector.service.publish_event", new=AsyncMock()) as publish:
        await service.procesar_inspect_container(EVENTO, canal_falso)

    evento_publicado = publish.call_args.args[2]
    assert evento_publicado.scraper_source == "MOCK"


async def test_usa_scraper_real_cuando_esta_registrado_y_circuito_cerrado():
    resultado_real = ResultadoScraping(
        port_status="Descargado",
        real_arrival_date=datetime.now(timezone.utc),
        scraper_source="SCRAPING",
        scraped_at=datetime.now(timezone.utc),
    )
    service._SCRAPERS_REALES["STI"] = _ScraperFalso(resultado=resultado_real)
    service._circuit_breaker.registrar_exito("STI")

    canal_falso = AsyncMock()
    try:
        with patch("agente_inspector.service.publish_event", new=AsyncMock()) as publish:
            await service.procesar_inspect_container(EVENTO, canal_falso)

        evento_publicado = publish.call_args.args[2]
        assert evento_publicado.scraper_source == "SCRAPING"
    finally:
        service._SCRAPERS_REALES.pop("STI", None)


async def test_cae_a_mock_y_abre_circuito_tras_3_fallas_del_scraper_real():
    service._SCRAPERS_REALES["STI"] = _ScraperFalso(error=ScraperError("timeout"))

    canal_falso = AsyncMock()
    try:
        with patch("agente_inspector.service.publish_event", new=AsyncMock()):
            for _ in range(3):
                await service.procesar_inspect_container(EVENTO, canal_falso)

        assert service._circuit_breaker.esta_abierto("STI") is True

        with patch("agente_inspector.service.publish_event", new=AsyncMock()) as publish:
            await service.procesar_inspect_container(EVENTO, canal_falso)

        evento_publicado = publish.call_args.args[2]
        assert evento_publicado.scraper_source == "MOCK"
    finally:
        service._SCRAPERS_REALES.pop("STI", None)
        service._circuit_breaker.registrar_exito("STI")
