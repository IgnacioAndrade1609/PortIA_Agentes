import hashlib
from datetime import datetime, timedelta, timezone

from agente_inspector.scrapers.base import ITerminalScraper, ResultadoScraping

_ESTADOS_PUERTO = ["En patio", "Descargado", "Disponible para retiro"]


class MockStrategy(ITerminalScraper):
    async def consultar_estado(self, container_number: str, terminal_config: dict) -> ResultadoScraping:
        semilla = int(hashlib.sha256(container_number.encode()).hexdigest(), 16)
        ahora = datetime.now(timezone.utc)

        return ResultadoScraping(
            port_status=_ESTADOS_PUERTO[semilla % len(_ESTADOS_PUERTO)],
            real_arrival_date=ahora - timedelta(hours=semilla % 48),
            scraper_source="MOCK",
            scraped_at=ahora,
        )
