"""Tests del pipeline: lectura del documento y gate de estado (sin LLM ni Docling)."""

from pathlib import Path

import pytest

from agente_lector import pipeline
from agente_lector.extraction.llm_schema import CAMPOS_CLAVE
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


# ---------------------------------------------------------------------------
# Gate de campos clave
# ---------------------------------------------------------------------------
def test_bl_sin_campos_clave_va_a_revision():
    """El contrato puede "cumplir" con los 16 declarados ausentes y aun así no servir."""
    bl = _bl(("observations", "sin novedad"))
    bl.campos_no_encontrados = list(CAMPOS_CLAVE)

    assert pipeline._estado_extraccion(bl) is ExtractionStatus.NEEDS_REVIEW


def test_bl_con_un_campo_clave_es_ok():
    assert pipeline._estado_extraccion(_bl(("bl_number", "vO-2005-50037"))) is ExtractionStatus.OK


# ---------------------------------------------------------------------------
# Ruteo por contenido (magic bytes), no por extensión
# ---------------------------------------------------------------------------
PDF = b"%PDF-1.7\n" + b"\x00" * 20
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 20


@pytest.mark.parametrize(
    ("datos", "esperado"),
    [
        (PDF, ".pdf"),
        (PNG, ".png"),
        (b"\xff\xd8\xff\xe0" + b"\x00" * 8, ".jpg"),
        (b"RIFF\x00\x00\x00\x00WEBPVP8 ", ".webp"),
        (b"II*\x00resto", ".tiff"),
        (b"MM\x00*resto", ".tiff"),
        (b"BM" + b"\x00" * 4 + b"\x00" * 4 + b"\x00" * 6, ".bmp"),
        (b"GIF89a......", None),
        (b"contenido cualquiera", None),
    ],
)
def test_tipo_por_contenido(datos, esperado):
    assert pipeline._tipo_por_contenido(datos) == esperado


def test_imagen_renombrada_como_pdf_se_rutea_como_imagen(monkeypatch):
    llamadas: list[tuple[str, bool, bool]] = []

    def falso_docling(datos, nombre, *, usar_ocr, permitir_imagen):
        llamadas.append((nombre, usar_ocr, permitir_imagen))
        return "markdown"

    monkeypatch.setattr(pipeline, "_markdown_docling", falso_docling)

    assert pipeline.texto_documento(PNG, "trampa.pdf") == "markdown"
    assert llamadas == [("trampa.png", True, True)]  # nombre corregido para Docling


def test_pdf_renombrado_como_imagen_usa_texto_nativo(monkeypatch):
    monkeypatch.setattr(pipeline, "_texto_nativo_pdf", lambda datos: (1, "y" * 500))
    monkeypatch.setattr(
        pipeline, "_markdown_docling", lambda *a, **k: pytest.fail("no debe usar Docling")
    )

    assert pipeline.texto_documento(PDF, "foto.png") == "y" * 500


def test_pdf_sin_extension_usa_texto_nativo(monkeypatch):
    monkeypatch.setattr(pipeline, "_texto_nativo_pdf", lambda datos: (1, "x" * 500))
    monkeypatch.setattr(
        pipeline, "_markdown_docling", lambda *a, **k: pytest.fail("no debe usar Docling")
    )

    assert pipeline.texto_documento(PDF, "documento") == "x" * 500
