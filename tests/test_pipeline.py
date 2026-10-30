"""Tests del pipeline: lectura del documento y gate de estado (sin LLM ni Docling)."""

from pathlib import Path

import pytest

from agente_lector import pipeline
from agente_lector.extraction.schemas import (
    BillOfLadingExtraido,
    CampoExtraido,
    ReviewReason,
)
from shared.events import ExtractionStatus


def _bl(*campos: tuple[str, str], confianza: float = 0.9) -> BillOfLadingExtraido:
    bl = BillOfLadingExtraido()
    for nombre, valor in campos:
        setattr(bl, nombre, CampoExtraido(value=valor, confidence=confianza))
    return bl


def test_bl_vacio_es_error():
    assert pipeline._estado_extraccion(BillOfLadingExtraido()) is ExtractionStatus.ERROR


def test_bl_con_datos_es_ok():
    bl = _bl(("bl_number", "vO-2005-50037"), ("port_of_discharge", "Guayaquil"))

    assert pipeline._estado_extraccion(bl) is ExtractionStatus.OK


def test_grounding_fallido_va_a_revision():
    bl = _bl(("bl_number", "vO-2005-50037"))
    bl.review_reasons.append(ReviewReason.GROUNDING_FAILED)

    assert pipeline._estado_extraccion(bl) is ExtractionStatus.NEEDS_REVIEW


def test_confianza_baja_va_a_revision():
    bl = _bl(("bl_number", "vO-2005-50037"), confianza=0.5)

    assert pipeline._estado_extraccion(bl) is ExtractionStatus.NEEDS_REVIEW


def test_confianza_media_solo_cuenta_campos_con_valor():
    bl = _bl(("bl_number", "x"), confianza=0.9)
    bl.shipping_line = CampoExtraido(value=None, confidence=0.0)

    assert pipeline._confianza_media(bl) == pytest.approx(0.9)


def test_formato_no_soportado_es_error_permanente():
    with pytest.raises(pipeline.ErrorDocumento) as excinfo:
        pipeline.texto_documento(b"contenido", "bl.docx")

    assert excinfo.value.transitorio is False


def test_archivo_inexistente_es_error_permanente(tmp_path: Path):
    with pytest.raises(pipeline.ErrorDocumento) as excinfo:
        pipeline._leer_documento(str(tmp_path / "no-existe.pdf"))

    assert excinfo.value.transitorio is False


def test_archivo_local_vacio_es_error(tmp_path: Path):
    ruta = tmp_path / "vacio.pdf"
    ruta.write_bytes(b"")

    with pytest.raises(pipeline.ErrorDocumento):
        pipeline._leer_documento(str(ruta))


def test_archivo_local_se_lee(tmp_path: Path):
    ruta = tmp_path / "bl.pdf"
    ruta.write_bytes(b"%PDF-1.4 contenido")

    datos, nombre = pipeline._leer_documento(str(ruta))

    assert datos.startswith(b"%PDF")
    assert nombre == "bl.pdf"


def test_archivo_demasiado_grande(tmp_path: Path, monkeypatch):
    ruta = tmp_path / "grande.pdf"
    ruta.write_bytes(b"x" * 2048)
    monkeypatch.setattr(pipeline, "MAX_FILE_MB", 0.001)

    with pytest.raises(pipeline.ErrorDocumento) as excinfo:
        pipeline._leer_documento(str(ruta))

    assert "MB" in str(excinfo.value)


def test_idiomas_ocr(monkeypatch):
    monkeypatch.setenv("OCR_LANG", "es, en")
    assert pipeline._idiomas_ocr() == ["es", "en"]

    monkeypatch.setenv("OCR_LANG", "")
    assert pipeline._idiomas_ocr() == ["es", "en"]
