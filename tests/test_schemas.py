"""Tests de construcción y validación de los esquemas (Fase 1)."""

import json

from agente_lector.extraction.schemas import (
    SCHEMA_VERSION,
    BillOfLadingExtraido,
    Block,
    BlockType,
    BoundingBox,
    CampoExtraido,
    ContainerData,
    ContentLayer,
    DocumentoNormalizado,
    EngineInfo,
    Evidence,
    PageInfo,
    ParteComercial,
    ResultadoExtraccion,
    SourceInfo,
    TableCell,
    TableData,
    WarningCode,
)
from shared.events import ExtractionStatus


def test_schema_version_es_semver():
    assert isinstance(SCHEMA_VERSION, str)
    mayor, menor, parche = SCHEMA_VERSION.split(".")
    assert all(parte.isdigit() for parte in (mayor, menor, parche))


def test_extraction_status_se_reutiliza_del_contrato():
    """El estado no se duplica: es el mismo enum de ``shared/events.py``."""
    from agente_lector.extraction import schemas

    assert schemas.ExtractionStatus is ExtractionStatus


def test_campo_extraido_vacio_por_defecto():
    campo = CampoExtraido()
    assert campo.value is None
    assert campo.confidence == 0.0
    assert campo.evidence == []
    assert campo.presente is False


def test_campo_extraido_presente_solo_con_texto():
    assert CampoExtraido(value="TGCU5129073").presente is True
    assert CampoExtraido(value="   ").presente is False


def test_campo_extraido_guarda_evidencia():
    campo = CampoExtraido(
        value="TGCU5129073",
        confidence=0.91,
        evidence=[Evidence(page=1, text="TGCU 512907.3", bbox=BoundingBox(x0=0.1, y0=0.2, x1=0.3, y1=0.25))],
    )
    assert campo.evidence[0].page == 1
    assert campo.evidence[0].bbox.x1 == 0.3


def test_confianza_fuera_de_rango_es_invalida():
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        CampoExtraido(value="x", confidence=1.5)


def test_bl_minimo_esta_vacio_pero_es_usable():
    bl = BillOfLadingExtraido()
    assert bl.schema_version == SCHEMA_VERSION
    assert bl.bl_number.value is None
    assert bl.shipper.nombre.value is None  # anidado con default_factory: no es None
    assert bl.containers == []
    assert bl.warnings == []


def test_bl_serializa_a_json_sin_perder_tipos():
    bl = BillOfLadingExtraido(
        bl_number=CampoExtraido(value="140021277"),
        consignee=ParteComercial(nombre=CampoExtraido(value="MALL URUBO LOCAL 27")),
        containers=[ContainerData(number=CampoExtraido(value="TGCU5129073"))],
        warnings=[WarningCode.NO_HEADER_DETECTED],
    )
    crudo = bl.model_dump(mode="json")
    texto = json.dumps(crudo, ensure_ascii=False)
    assert json.loads(texto)["bl_number"]["value"] == "140021277"
    assert crudo["warnings"] == ["no_header_detected"]


def test_warnings_acepta_el_valor_del_enum_y_rechaza_libre():
    import pytest
    from pydantic import ValidationError

    assert BillOfLadingExtraido(warnings=["empty_cells"]).warnings == [WarningCode.EMPTY_CELLS]
    with pytest.raises(ValidationError):
        BillOfLadingExtraido(warnings=["warning inventado"])


def test_documento_normalizado_minimo():
    documento = DocumentoNormalizado(
        extractor_version="docling@2.140.0+norm@1",
        source=SourceInfo(filename="1.webp", mime="image/webp", sha256="a" * 64),
    )
    assert documento.engine == EngineInfo()
    assert documento.warnings == []
    assert documento.markdown == ""


def test_documento_normalizado_con_tabla_deduplicada():
    documento = DocumentoNormalizado(
        extractor_version="docling@2.140.0+norm@1",
        source=SourceInfo(filename="1.webp", mime="image/webp", sha256="a" * 64),
        pages=[PageInfo(page_no=1, width=768, height=1024, text_chars=1240)],
        blocks=[
            Block(order=1, type=BlockType.TABLE, page=1, text="carga"),
            Block(order=2, type=BlockType.FOOTER, page=1, text="FOLIO 140021277", layer=ContentLayer.FURNITURE),
        ],
        tables=[
            TableData(
                id="t1",
                page=1,
                rows=[["#", "Peso"], ["1", "25541.25"]],
                cells=[TableCell(r=0, c=1, text="Peso")],
                header_row_idx=0,
                merged_from=["t0", "t1", "t2"],
            )
        ],
    )
    assert documento.tables[0].merged_from == ["t0", "t1", "t2"]
    assert documento.blocks[1].layer is ContentLayer.FURNITURE
    assert documento.model_dump(mode="json")["blocks"][0]["type"] == "table"


def test_resultado_extraccion_acepta_lista_vacia():
    resultado = ResultadoExtraccion(status=ExtractionStatus.ERROR)
    assert resultado.documentos == []
    assert resultado.confidence == 0.0
    assert resultado.metadata == {}
