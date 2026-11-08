import asyncio

import pytest

from agente_inspector import circuit_breaker as circuit_breaker_module
from agente_inspector.circuit_breaker import CircuitBreaker


class RedisFalso:
    def __init__(self) -> None:
        self.datos: dict[str, str] = {}

    async def get(self, clave: str):
        return self.datos.get(clave)

    async def set(self, clave: str, valor: str, *, ex: int | None = None, nx: bool = False):
        if nx and clave in self.datos:
            return None
        self.datos[clave] = valor
        return True

    async def delete(self, clave: str):
        self.datos.pop(clave, None)
        return 1


@pytest.fixture
def redis_falso() -> RedisFalso:
    return RedisFalso()


def test_circuito_cerrado_al_inicio(redis_falso):
    cb = CircuitBreaker(fallas_para_abrir=3, ventana_recuperacion_segundos=60, cliente=redis_falso)
    assert asyncio.run(cb.esta_abierto("STI")) is False


def test_se_abre_tras_n_fallas_consecutivas(redis_falso):
    cb = CircuitBreaker(fallas_para_abrir=3, ventana_recuperacion_segundos=60, cliente=redis_falso)

    asyncio.run(cb.registrar_falla("STI"))
    asyncio.run(cb.registrar_falla("STI"))
    assert asyncio.run(cb.esta_abierto("STI")) is False

    asyncio.run(cb.registrar_falla("STI"))
    assert asyncio.run(cb.esta_abierto("STI")) is True


def test_un_exito_resetea_el_contador_de_fallas(redis_falso):
    cb = CircuitBreaker(fallas_para_abrir=3, ventana_recuperacion_segundos=60, cliente=redis_falso)

    asyncio.run(cb.registrar_falla("STI"))
    asyncio.run(cb.registrar_falla("STI"))
    asyncio.run(cb.registrar_exito("STI"))
    asyncio.run(cb.registrar_falla("STI"))

    assert asyncio.run(cb.esta_abierto("STI")) is False


def test_terminales_son_independientes(redis_falso):
    cb = CircuitBreaker(fallas_para_abrir=3, ventana_recuperacion_segundos=60, cliente=redis_falso)

    asyncio.run(cb.registrar_falla("STI"))
    asyncio.run(cb.registrar_falla("STI"))
    asyncio.run(cb.registrar_falla("STI"))

    assert asyncio.run(cb.esta_abierto("STI")) is True
    assert asyncio.run(cb.esta_abierto("TPS")) is False


def test_recupera_a_half_open_tras_la_ventana(monkeypatch, redis_falso):
    tiempo_actual = [1000.0]
    monkeypatch.setattr(circuit_breaker_module.time, "time", lambda: tiempo_actual[0])

    cb = CircuitBreaker(fallas_para_abrir=1, ventana_recuperacion_segundos=60, cliente=redis_falso)

    asyncio.run(cb.registrar_falla("STI"))
    assert asyncio.run(cb.esta_abierto("STI")) is True

    tiempo_actual[0] += 60
    assert asyncio.run(cb.esta_abierto("STI")) is False


def test_estado_persiste_entre_instancias_distintas(redis_falso):
    cb1 = CircuitBreaker(fallas_para_abrir=2, ventana_recuperacion_segundos=60, cliente=redis_falso)
    asyncio.run(cb1.registrar_falla("STI"))
    asyncio.run(cb1.registrar_falla("STI"))

    cb2 = CircuitBreaker(fallas_para_abrir=2, ventana_recuperacion_segundos=60, cliente=redis_falso)
    assert asyncio.run(cb2.esta_abierto("STI")) is True


def test_registro_corrupto_en_redis_se_trata_como_circuito_cerrado(redis_falso):
    redis_falso.datos[f"{circuit_breaker_module.PREFIJO_CIRCUIT_BREAKER}STI"] = "no-es-json"

    cb = CircuitBreaker(fallas_para_abrir=3, ventana_recuperacion_segundos=60, cliente=redis_falso)
    assert asyncio.run(cb.esta_abierto("STI")) is False
