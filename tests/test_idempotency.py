"""Tests de la caché de trabajos en Redis (idempotencia de procesamiento y respuesta).

No requieren un Redis real: ``shared.idempotency`` acepta un cliente inyectado.
"""

import asyncio

import pytest

from shared import idempotency as idem


class RedisFalso:
    """Implementación mínima de ``GET`` / ``SET`` (con ``NX`` y ``EX``) / ``DELETE``."""

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


def test_hash_evento_es_estable():
    assert idem.hash_evento(b'{"a":1}') == idem.hash_evento(b'{"a":1}')
    assert idem.hash_evento(b'{"a":1}') != idem.hash_evento(b'{"a":2}')
    assert len(idem.hash_evento(b"x")) == 64


def test_clave_trabajo_usa_prefijo():
    assert idem.clave_trabajo("abc") == f"{idem.PREFIJO_TRABAJO}abc"


def test_primer_reclamo_es_nuevo_y_deja_en_proceso(redis_falso):
    reclamo = asyncio.run(idem.reclamar_trabajo("evt", cliente=redis_falso))

    assert reclamo.estado == idem.ESTADO_NUEVO
    assert reclamo.resultado is None
    assert '"en_proceso"' in redis_falso.datos[idem.clave_trabajo("evt")]


def test_segundo_reclamo_no_reprocesa(redis_falso):
    asyncio.run(idem.reclamar_trabajo("evt", cliente=redis_falso))

    segundo = asyncio.run(idem.reclamar_trabajo("evt", cliente=redis_falso))

    assert segundo.estado == idem.ESTADO_EN_PROCESO
    assert segundo.resultado is None


def test_reclamo_devuelve_la_respuesta_cacheada(redis_falso):
    asyncio.run(idem.reclamar_trabajo("evt", cliente=redis_falso))
    eventos = [{"shipment_id": 1, "container_number": "TGCU5129073"}]
    asyncio.run(idem.guardar_resultado("evt", eventos, cliente=redis_falso))

    reclamo = asyncio.run(idem.reclamar_trabajo("evt", cliente=redis_falso))

    assert reclamo.estado == idem.ESTADO_LISTO
    assert reclamo.resultado == eventos


def test_obtener_resultado_antes_y_despues(redis_falso):
    assert asyncio.run(idem.obtener_resultado("evt", cliente=redis_falso)) is None

    asyncio.run(idem.reclamar_trabajo("evt", cliente=redis_falso))
    asyncio.run(idem.guardar_resultado("evt", [{"a": 1}], cliente=redis_falso))

    assert asyncio.run(idem.obtener_resultado("evt", cliente=redis_falso)) == [{"a": 1}]


def test_obtener_resultado_ignora_trabajo_en_proceso(redis_falso):
    asyncio.run(idem.reclamar_trabajo("evt", cliente=redis_falso))

    assert asyncio.run(idem.obtener_resultado("evt", cliente=redis_falso)) is None


def test_fallo_se_recuerda_sin_resultado(redis_falso):
    asyncio.run(idem.reclamar_trabajo("evt", cliente=redis_falso))
    asyncio.run(idem.marcar_fallido("evt", "documento corrupto", cliente=redis_falso))

    reclamo = asyncio.run(idem.reclamar_trabajo("evt", cliente=redis_falso))

    assert reclamo.estado == idem.ESTADO_FALLIDO
    assert reclamo.resultado is None


def test_liberar_trabajo_permite_reintento(redis_falso):
    asyncio.run(idem.reclamar_trabajo("evt", cliente=redis_falso))
    asyncio.run(idem.liberar_trabajo("evt", cliente=redis_falso))

    reclamo = asyncio.run(idem.reclamar_trabajo("evt", cliente=redis_falso))

    assert reclamo.estado == idem.ESTADO_NUEVO


def test_lee_valores_en_bytes(redis_falso):
    asyncio.run(idem.reclamar_trabajo("evt", cliente=redis_falso))
    asyncio.run(idem.guardar_resultado("evt", [{"a": 1}], cliente=redis_falso))
    clave = idem.clave_trabajo("evt")
    redis_falso.datos[clave] = redis_falso.datos[clave].encode()

    assert asyncio.run(idem.obtener_resultado("evt", cliente=redis_falso)) == [{"a": 1}]


def test_registro_corrupto_se_ignora(redis_falso):
    redis_falso.datos[idem.clave_trabajo("evt")] = "no es json"

    reclamo = asyncio.run(idem.reclamar_trabajo("evt", cliente=redis_falso))

    # No se puede interpretar: se trata como "en proceso" para no reprocesar a ciegas.
    assert reclamo.estado == idem.ESTADO_EN_PROCESO


def test_intentar_marcar_procesado_sigue_funcionando(redis_falso):
    assert asyncio.run(idem.intentar_marcar_procesado("evt", cliente=redis_falso)) is True
    assert asyncio.run(idem.intentar_marcar_procesado("evt", cliente=redis_falso)) is False
