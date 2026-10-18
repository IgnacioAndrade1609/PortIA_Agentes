from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass(frozen=True)
class ResultadoScraping:
    port_status: str
    real_arrival_date: Optional[datetime]
    scraper_source: str
    scraped_at: datetime


class ScraperError(Exception):
    pass


class ITerminalScraper(ABC):
    @abstractmethod
    async def consultar_estado(self, container_number: str, terminal_config: dict) -> ResultadoScraping:
        ...
