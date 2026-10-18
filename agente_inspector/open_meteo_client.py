import logging
from typing import Optional

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger("portia.agente_inspector")

MARINE_API_URL = "https://marine-api.open-meteo.com/v1/marine"


class OpenMeteoClient:
    def __init__(
        self,
        base_url: str = MARINE_API_URL,
        timeout_seconds: float = 10.0,
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ) -> None:
        self._base_url = base_url
        self._timeout_seconds = timeout_seconds
        self._transport = transport

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=0.1, min=0.1, max=1), reraise=True)
    async def consultar_altura_ola(self, latitude: float, longitude: float) -> Optional[float]:
        async with httpx.AsyncClient(timeout=self._timeout_seconds, transport=self._transport) as client:
            response = await client.get(
                self._base_url,
                params={"latitude": latitude, "longitude": longitude, "current": "wave_height"},
            )
            response.raise_for_status()
            data = response.json()
            return data.get("current", {}).get("wave_height")
