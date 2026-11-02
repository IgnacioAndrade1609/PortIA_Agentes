"""Tests del mapeo ``BillOfLadingExtraido`` → ``DataExtractedEvent`` (Fase 1).

Valores alineados con el fixture real (``resultado_extraccion1.json``, Anexo A del diseño).
"""

import json

import pytest

from agente_lector.extraction.mapping import a_evento, a_eventos
from agente_lector.extraction.schemas import (
    BillOfLadingExtraido,
    CampoExtraido,
    ContainerData,
)
from shared.events import ExtractionStatus


def _campo(valor: str, confianza: float = 0.9) -> CampoExtraido:
    return CampoExtraido(value=valor, confidence=confianza)


@pytest.fixture
def bl() -> BillOfLadingExtraido:
    """BL equivalente al del fixture ``1.webp``."""
    return BillOfLadingExtraido(
        document_type=_campo("BL"),
        bl_number=_campo("140021277"),
        bl_madre=_campo("MEDUV4881458"),
        folio=_campo("140021277"),
        shipping_line=_campo("MSC"),
        vessel=_campo("MSC AINO"),
        port_of_loading=_campo("VALENCIA"),
        port_of_discharge=_campo("SANTA CRUZ"),
        eta=_campo("2021-12-01"),
        gross_weight=_campo("25541.25"),
        gross_weight_unit=_campo("KGM"),
        demurrage_free_days=_campo("7 días"),
        containers=[ContainerData(number=_campo("TGCU5129073"), iso_type=_campo("45G0"), seal=_campo("000574"))],
    )


def test_un_contenedor_produce_un_evento(bl):
    eventos = a_eventos(bl, shipment_id=101, document_id=501)
    assert len(eventos) == 1
    assert eventos[0].container_number == "TGCU5129073"


def test_campos_del_contrato_se_mapean(bl):
    evento = a_evento(bl, shipment_id=101, document_id=501)
    assert evento.shipment_id == 101
    assert evento.document_id == 501
    assert evento.document_type == "BL"
    assert evento.bl_number == "140021277"
    assert evento.shipping_line == "MSC"
    assert evento.port_of_destination == "SANTA CRUZ"
    assert evento.eta.isoformat() == "2021-12-01T00:00:00"
    assert evento.demurrage_free_days == 7
    assert evento.detention_free_days is None
    assert evento.extraction_status is ExtractionStatus.OK


def test_n_contenedores_produce_n_eventos(bl):
    bl.containers.append(ContainerData(number=_campo("MSCU1234565")))
    eventos = a_eventos(bl, shipment_id=101, document_id=501)
    assert [e.container_number for e in eventos] == ["TGCU5129073", "MSCU1234565"]
    # El raw es el mismo BL completo para todos los eventos.
    assert eventos[0].extracted_data_raw == eventos[1].extracted_data_raw


def test_bl_sin_contenedor_produce_evento_con_numero_vacio(bl):
    """Sin validaciones en Fase 1: se traduce con ``""`` y Fase 7 lo marcará NEEDS_REVIEW."""
    bl.containers = []
    eventos = a_eventos(bl, shipment_id=101, document_id=501)
    assert len(eventos) == 1
    assert eventos[0].container_number == ""


def test_contenedor_sin_numero_se_ignora(bl):
    bl.containers = [ContainerData()]
    eventos = a_eventos(bl, shipment_id=101, document_id=501)
    assert [e.container_number for e in eventos] == [""]


def test_eta_no_parseable_no_explota(bl):
    bl.eta = _campo("fecha ilegible")
    assert a_evento(bl, shipment_id=101, document_id=501).eta is None


def test_bl_sin_document_type_cae_al_valor_por_defecto():
    evento = a_evento(BillOfLadingExtraido(), shipment_id=1, document_id=2)
    assert evento.document_type == "BL"
    assert evento.bl_number is None
    assert evento.confidence == 0.0


def test_document_type_del_evento_tiene_prioridad(bl):
    evento = a_evento(bl, shipment_id=101, document_id=501, document_type="BL_HOUSE")
    assert evento.document_type == "BL_HOUSE"


def test_extraction_status_e_confidence_son_del_llamador(bl):
    evento = a_evento(
        bl,
        shipment_id=101,
        document_id=501,
        confidence=0.42,
        extraction_status=ExtractionStatus.NEEDS_REVIEW,
    )
    assert evento.confidence == 0.42
    assert evento.extraction_status is ExtractionStatus.NEEDS_REVIEW


def test_extracted_data_raw_es_el_bl_y_no_el_docling_document(bl):
    evento = a_evento(bl, shipment_id=101, document_id=501)
    raw = evento.extracted_data_raw
    assert raw["bl_number"]["value"] == "140021277"
    assert raw["schema_version"]
    assert "schema_name" not in raw  # nunca el DoclingDocument crudo
    assert "tables" not in raw
    json.dumps(raw)  # debe ser serializable tal cual llega al mensaje


def test_confianza_provisional_es_media_de_los_campos_presentes():
    bl = BillOfLadingExtraido(
        bl_number=_campo("140021277", 0.9),
        eta=_campo("2021-12-01", 0.6),
        containers=[ContainerData(number=_campo("TGCU5129073", 0.9))],
    )
    evento = a_evento(bl, shipment_id=1, document_id=2)
    assert evento.confidence == round((0.9 + 0.6 + 0.9) / 3, 2)


def test_campos_solo_espacios_se_tratan_como_ausentes():
    bl = BillOfLadingExtraido(bl_number=_campo("   "), containers=[ContainerData(number=_campo(" "))])
    evento = a_evento(bl, shipment_id=1, document_id=2)
    assert evento.bl_number is None
    assert evento.container_number == ""
