"""Tests del consumidor del Agente Lector: idempotencia de procesamiento y respuesta.

Sin RabbitMQ ni Redis reales: se inyectan un mensaje, un canal y un cliente Redis falsos.
"""

import asyncio
import contextlib
import json

import pytest

from agente_lector import consumer
from agente_lector.pipeline import ErrorDocumento
from shared import idempotency as idem
from shared.events import DataExtractedEvent

EVENTO = {
    "shipment_id": 101,
    "document_id": 501,
    "document_url": "temp/pdf/bl_document_2.pdf",
    "document_type": "BL",
}


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


class MensajeFalso:
    """Imita ``aio_pika.IncomingMessage``: ``body`` + ``process()`` como context manager."""

    def __init__(self, payload: dict) -> None:
        self.body = json.dumps(payload).encode()
        self.requeue: bool | None = None

    @contextlib.asynccontextmanager
    async def process(self, requeue: bool = True):
        self.requeue = requeue
        yield self


class ExchangeFalso:
    def __init__(self) -> None:
        self.publicados: list[tuple[str, dict]] = []

    async def publish(self, message, routing_key: str) -> None:
        self.publicados.append((routing_key, json.loads(message.body)))


class CanalFalso:
    def __init__(self) -> None:
        self.default_exchange = ExchangeFalso()


class ServicioFalso:
    def __init__(self, eventos=None, error: Exception | None = None) -> None:
        self.eventos = eventos if eventos is not None else [_evento_extraido()]
        self.error = error
        self.llamadas: list = []

    async def __call__(self, evento):
        self.llamadas.append(evento)
        if self.error is not None:
            raise self.error
        return self.eventos


def _evento_extraido(**cambios) -> DataExtractedEvent:
    datos = {
        "shipment_id": 101,
        "document_id": 501,
        "document_type": "BL",
        "bl_number": "vO-2005-50037",
        "container_number": "UNYU1681810",
        "confidence": 0.9,
        "extraction_status": "OK",
        "extracted_data_raw": {"bl_number": "vO-2005-50037"},
    }
    datos.update(cambios)
    return DataExtractedEvent(**datos)


@pytest.fixture
def redis_falso(monkeypatch) -> RedisFalso:
    falso = RedisFalso()
    monkeypatch.setattr(idem, "r", falso)
    return falso


@pytest.fixture
def canal() -> CanalFalso:
    return CanalFalso()


def _procesar(servicio: ServicioFalso, canal: CanalFalso, monkeypatch):
    monkeypatch.setattr(consumer, "procesar_shipment_created", servicio)
    mensaje = MensajeFalso(EVENTO)
    asyncio.run(consumer.on_message(mensaje, canal))
    return mensaje


def test_primera_entrega_procesa_publica_y_cachea(redis_falso, canal, monkeypatch):
    servicio = ServicioFalso()

    mensaje = _procesar(servicio, canal, monkeypatch)

    assert len(servicio.llamadas) == 1
    assert mensaje.requeue is False
    assert len(canal.default_exchange.publicados) == 1
    routing_key, cuerpo = canal.default_exchange.publicados[0]
    assert routing_key == consumer.ROUTING_KEY_DATA_EXTRACTED
    assert cuerpo["container_number"] == "UNYU1681810"
    assert asyncio.run(idem.obtener_resultado(idem.hash_evento(mensaje.body))) == [cuerpo]


def test_duplicado_reutiliza_la_respuesta_sin_reprocesar(redis_falso, canal, monkeypatch):
    servicio = ServicioFalso()
    _procesar(servicio, canal, monkeypatch)

    mensaje = _procesar(servicio, canal, monkeypatch)

    assert len(servicio.llamadas) == 1  # no se volvió a llamar al LLM
    assert len(canal.default_exchange.publicados) == 2  # pero sí se republicó
    primero = canal.default_exchange.publicados[0][1]
    segundo = canal.default_exchange.publicados[1][1]
    assert primero == segundo
    assert mensaje.body == MensajeFalso(EVENTO).body


def test_evento_en_proceso_se_ignora(redis_falso, canal, monkeypatch):
    servicio = ServicioFalso()
    asyncio.run(idem.reclamar_trabajo(idem.hash_evento(MensajeFalso(EVENTO).body)))

    _procesar(servicio, canal, monkeypatch)

    assert servicio.llamadas == []
    assert canal.default_exchange.publicados == []


def test_error_transitorio_libera_el_trabajo(redis_falso, canal, monkeypatch):
    error = ErrorDocumento("almacenamiento caído", transitorio=True)
    servicio = ServicioFalso(error=error)
    monkeypatch.setattr(consumer, "procesar_shipment_created", servicio)
    mensaje = MensajeFalso(EVENTO)

    with pytest.raises(ErrorDocumento):
        asyncio.run(consumer.on_message(mensaje, canal))

    clave = idem.hash_evento(mensaje.body)
    assert idem.clave_trabajo(clave) not in redis_falso.datos
    assert canal.default_exchange.publicados == []


def test_error_permanente_recuerda_el_fallo(redis_falso, canal, monkeypatch):
    error = ErrorDocumento("formato no soportado", transitorio=False)
    servicio = ServicioFalso(error=error)
    monkeypatch.setattr(consumer, "procesar_shipment_created", servicio)
    mensaje = MensajeFalso(EVENTO)

    with pytest.raises(ErrorDocumento):
        asyncio.run(consumer.on_message(mensaje, canal))

    clave = idem.hash_evento(mensaje.body)
    registro = json.loads(redis_falso.datos[idem.clave_trabajo(clave)])
    assert registro["estado"] == idem.ESTADO_FALLIDO
    assert "formato" in registro["error"]


def test_fallo_previo_no_se_reprocesa(redis_falso, canal, monkeypatch):
    servicio = ServicioFalso()
    clave = idem.hash_evento(MensajeFalso(EVENTO).body)
    asyncio.run(idem.marcar_fallido(clave, "documento corrupto"))

    _procesar(servicio, canal, monkeypatch)

    assert servicio.llamadas == []
    assert canal.default_exchange.publicados == []
