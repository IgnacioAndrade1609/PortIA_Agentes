import logging
from typing import Optional

from agente_inspector.open_meteo_client import OpenMeteoClient

logger = logging.getLogger("portia.agente_inspector")

UMBRAL_MAREJADA_METROS = 3.0

COORDENADAS_TERMINAL: dict[str, tuple[float, float]] = {
    "STI": (-33.593, -71.622),
    "DPW": (-33.593, -71.622),
    "TPS": (-33.046, -71.620),
}

_client = OpenMeteoClient()


async def consultar_clima(terminal_code: str) -> tuple[bool, Optional[dict]]:
    coordenadas = COORDENADAS_TERMINAL.get(terminal_code)
    if coordenadas is None:
        logger.warning(f"Sin coordenadas registradas para terminal={terminal_code}")
        return False, None

    latitude, longitude = coordenadas

    try:
        altura_ola = await _client.consultar_altura_ola(latitude, longitude)
    except Exception as exc:
        logger.warning(f"Consulta a Open-Meteo falló para terminal={terminal_code}: {exc}")
        return False, None

    if altura_ola is None:
        logger.warning(f"Open-Meteo no devolvió wave_height para terminal={terminal_code}")
        return False, None

    weather_alert = altura_ola >= UMBRAL_MAREJADA_METROS
    weather_data = {"wave_height_m": altura_ola, "umbral_marejada_m": UMBRAL_MAREJADA_METROS}

    if weather_alert:
        logger.warning(f"Marejada detectada en terminal={terminal_code}: wave_height={altura_ola}m")
    else:
        logger.info(f"Clima sin alerta en terminal={terminal_code}: wave_height={altura_ola}m")

    return weather_alert, weather_data
