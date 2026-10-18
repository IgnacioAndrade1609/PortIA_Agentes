import httpx
import pytest

from agente_inspector.open_meteo_client import OpenMeteoClient


def _transport_con_respuesta(payload: dict, status_code: int = 200) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json=payload)

    return httpx.MockTransport(handler)


async def test_consultar_altura_ola_devuelve_el_valor_de_wave_height():
    transport = _transport_con_respuesta({"current": {"wave_height": 1.32}})
    client = OpenMeteoClient(transport=transport)

    resultado = await client.consultar_altura_ola(-33.593, -71.622)

    assert resultado == 1.32


async def test_consultar_altura_ola_retorna_none_si_falta_el_dato():
    transport = _transport_con_respuesta({"current": {}})
    client = OpenMeteoClient(transport=transport)

    resultado = await client.consultar_altura_ola(-33.593, -71.622)

    assert resultado is None


async def test_consultar_altura_ola_reintenta_y_se_recupera():
    intentos = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        intentos["n"] += 1
        if intentos["n"] < 3:
            raise httpx.ConnectError("timeout", request=request)
        return httpx.Response(200, json={"current": {"wave_height": 2.5}})

    client = OpenMeteoClient(transport=httpx.MockTransport(handler))

    resultado = await client.consultar_altura_ola(-33.593, -71.622)

    assert resultado == 2.5
    assert intentos["n"] == 3


async def test_consultar_altura_ola_lanza_tras_agotar_reintentos():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("timeout", request=request)

    client = OpenMeteoClient(transport=httpx.MockTransport(handler))

    with pytest.raises(httpx.ConnectError):
        await client.consultar_altura_ola(-33.593, -71.622)
