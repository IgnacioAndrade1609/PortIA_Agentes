from unittest.mock import AsyncMock, patch

from agente_inspector import weather


async def test_consultar_clima_sin_alerta_bajo_el_umbral():
    with patch.object(weather._client, "consultar_altura_ola", new=AsyncMock(return_value=1.5)):
        weather_alert, weather_data = await weather.consultar_clima("STI")

    assert weather_alert is False
    assert weather_data["wave_height_m"] == 1.5


async def test_consultar_clima_con_alerta_sobre_el_umbral():
    with patch.object(weather._client, "consultar_altura_ola", new=AsyncMock(return_value=3.4)):
        weather_alert, weather_data = await weather.consultar_clima("TPS")

    assert weather_alert is True
    assert weather_data["wave_height_m"] == 3.4


async def test_consultar_clima_terminal_sin_coordenadas_retorna_sin_alerta():
    weather_alert, weather_data = await weather.consultar_clima("LQN")

    assert weather_alert is False
    assert weather_data is None


async def test_consultar_clima_cuando_falla_la_consulta_retorna_sin_alerta():
    with patch.object(weather._client, "consultar_altura_ola", new=AsyncMock(side_effect=Exception("timeout"))):
        weather_alert, weather_data = await weather.consultar_clima("STI")

    assert weather_alert is False
    assert weather_data is None
