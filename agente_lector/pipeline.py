"""Pipeline de extracción del Agente Lector (Fase 3 + 8 del checklist).

``documento`` → texto → BL (LLM + grounding) → ``DataExtractedEvent``.

Decisiones de esta implementación
---------------------------------
- **Texto nativo primero.** Un PDF con capa de texto se lee con ``pypdfium2`` (milisegundos y
  sin pérdidas). Docling solo entra cuando el PDF es escaneado o el archivo es una imagen.
  Se midió que Docling descarta el cuerpo del BL en una parte del corpus, así que no se usa
  como camino principal.
- **OCR de página completa** (``force_full_page_ocr=True``). Sin esto, Docling detecta la
  página como imagen y devuelve solo el encabezado: un fallo silencioso.
- **Nada de I/O bloqueante en el event loop**: descarga, conversión y normalización corren en
  ``asyncio.to_thread``.
- **Errores clasificados**: :class:`ErrorDocumento` distingue transitorio (red, 5xx) de
  permanente (404, archivo corrupto, formato no soportado) para que el consumidor decida si
  reintentar o mandar a la DLQ.
"""

from __future__ import annotations

import asyncio
import io
import logging
import os
import threading
from functools import lru_cache
from pathlib import Path
from typing import Any

import requests

from agente_lector.extraction.llm_extractor import (
    LLMClient,
    OpenRouterClient,
    contar_campos_clave_con_valor,
    es_extraccion_vacia,
    extraer_bl_async,
)
from agente_lector.extraction.mapping import a_eventos
from agente_lector.extraction.schemas import BillOfLadingExtraido, ReviewReason
from shared.events import DataExtractedEvent, ExtractionStatus

__all__ = ["ErrorDocumento", "extraer_documento", "texto_documento"]

logger = logging.getLogger("portia.agente_lector")

#: Extensiones tratadas como imagen (siempre necesitan OCR).
SUFIJOS_IMAGEN = frozenset({".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"})

#: Formatos que el pipeline acepta.
SUFIJOS_SOPORTADOS = SUFIJOS_IMAGEN | {".pdf"}

#: Firmas de archivo (magic bytes) → extensión canónica.
#: La extensión del nombre miente; el contenido no. ``BM`` se valida además contra los
#: campos reservados en cero del header BMP, porque es una firma de dos bytes muy débil.
_FIRMAS: tuple[tuple[bytes, str], ...] = (
    (b"%PDF-", ".pdf"),
    (b"\x89PNG\r\n\x1a\n", ".png"),
    (b"\xff\xd8\xff", ".jpg"),
    (b"II*\x00", ".tiff"),
    (b"MM\x00*", ".tiff"),
)

#: Campos clave que deben tener al menos un valor para que el resultado sea utilizable.
#: Con 0, el BL no sirve para nada aguas abajo aunque el contrato "cumpla" (el modelo
#: puede declarar los 16 campos como ausentes y pasar el gate de cobertura).
MIN_CAMPOS_CLAVE_CON_VALOR = 1

MAX_FILE_MB = float(os.getenv("MAX_FILE_MB", "50"))
MAX_PAGES = int(os.getenv("MAX_PAGES", "50"))
TIMEOUT_DESCARGA = float(os.getenv("DOCUMENT_TIMEOUT", "120"))
TIMEOUT_DOCUMENTO = float(os.getenv("DOCUMENT_TIMEOUT", "120"))

#: Caracteres mínimos de texto nativo por página para confiar en el PDF digital.
UMBRAL_TEXTO_NATIVO = int(os.getenv("NATIVE_TEXT_THRESHOLD", "100"))

#: Por debajo de esta confianza media el resultado queda en revisión.
UMBRAL_CONFIANZA = float(os.getenv("CONFIDENCE_THRESHOLD", "0.7"))

#: Docling no garantiza uso concurrente del mismo convertidor.
_LOCK_CONVERTIDOR = threading.Lock()


class ErrorDocumento(RuntimeError):
    """Fallo al obtener o interpretar el documento.

    ``transitorio`` indica si conviene reintentar (red, almacenamiento caído) o si el
    documento es irrecuperable (no existe, corrupto, formato no soportado).
    """

    def __init__(self, mensaje: str, *, transitorio: bool) -> None:
        super().__init__(mensaje)
        self.transitorio = transitorio


# ---------------------------------------------------------------------------
# API pública
# ---------------------------------------------------------------------------
async def extraer_documento(
    *,
    url: str,
    shipment_id: int,
    document_id: int,
    document_type: str | None = None,
    cliente: LLMClient | None = None,
) -> list[DataExtractedEvent]:
    """Descarga/lee el documento, lo extrae y devuelve los eventos a publicar.

    Lanza :class:`ErrorDocumento` si el documento no se puede procesar y
    ``ErrorLLM`` si el proveedor falla tras agotar reintentos.
    """
    datos, nombre = await asyncio.to_thread(_leer_documento, url)
    texto = await asyncio.to_thread(texto_documento, datos, nombre)
    if not texto.strip():
        raise ErrorDocumento(
            "el documento no tiene texto extraíble (¿escaneo ilegible?)", transitorio=False
        )

    cliente_llm = cliente or OpenRouterClient()
    bl = await extraer_bl_async(texto, cliente_llm)  # type: ignore[arg-type]
    estado = _estado_extraccion(bl)

    eventos = a_eventos(
        bl,
        shipment_id=shipment_id,
        document_id=document_id,
        document_type=document_type,
        extraction_status=estado,
    )
    logger.info(
        "documento extraído: shipment_id=%s document_id=%s chars=%d eventos=%d estado=%s",
        shipment_id,
        document_id,
        len(texto),
        len(eventos),
        estado.value,
    )
    return eventos


def _tipo_por_contenido(datos: bytes) -> str | None:
    """Extensión deducida de los primeros bytes, o ``None`` si no se reconoce."""
    for firma, sufijo in _FIRMAS:
        if datos.startswith(firma):
            return sufijo
    if datos[:4] == b"RIFF" and datos[8:12] == b"WEBP":
        return ".webp"
    if datos[:2] == b"BM" and len(datos) > 14 and datos[6:10] == b"\x00\x00\x00\x00":
        return ".bmp"
    return None


def texto_documento(datos: bytes, nombre: str) -> str:
    """Texto/Markdown del documento (bloqueante: llamar desde ``to_thread``).

    El tipo se decide por el **contenido** (magic bytes); la extensión solo se usa cuando la
    firma no es reconocible. Con la extensión como única fuente, un PDF subido sin sufijo se
    rechazaba y una imagen renombrada a ``.pdf`` entraba por la rama equivocada.
    """
    sufijo = _tipo_por_contenido(datos) or Path(nombre).suffix.lower()
    if sufijo not in SUFIJOS_SOPORTADOS:
        raise ErrorDocumento(f"formato no soportado: {sufijo or '(sin extensión)'}", transitorio=False)

    # Docling recibe el nombre corregido: si el archivo decía ``.pdf`` pero es una imagen, el
    # nombre original lo haría fallar al inferir el formato.
    nombre_efectivo = f"{Path(nombre).stem or 'documento'}{sufijo}"

    if sufijo in SUFIJOS_IMAGEN:
        return _markdown_docling(datos, nombre_efectivo, usar_ocr=True, permitir_imagen=True)

    paginas, nativo = _texto_nativo_pdf(datos)
    if paginas > MAX_PAGES:
        raise ErrorDocumento(f"el PDF tiene {paginas} páginas (máximo {MAX_PAGES})", transitorio=False)
    if len(nativo) >= UMBRAL_TEXTO_NATIVO * max(1, paginas):
        return nativo
    logger.info("PDF sin texto nativo suficiente (%d chars); se usa Docling + OCR", len(nativo))
    return _markdown_docling(datos, nombre_efectivo, usar_ocr=True, permitir_imagen=False)


# ---------------------------------------------------------------------------
# Lectura del documento
# ---------------------------------------------------------------------------
def _leer_documento(url: str) -> tuple[bytes, str]:
    """Devuelve ``(bytes, nombre_archivo)`` desde una URL http(s) o una ruta local."""
    if url.startswith(("http://", "https://")):
        try:
            respuesta = requests.get(url, timeout=TIMEOUT_DESCARGA)
        except requests.RequestException as exc:
            raise ErrorDocumento(
                f"no se pudo descargar el documento ({type(exc).__name__})", transitorio=True
            ) from exc
        if respuesta.status_code >= 500:
            raise ErrorDocumento(
                f"almacenamiento no disponible ({respuesta.status_code})", transitorio=True
            )
        if respuesta.status_code >= 400:
            raise ErrorDocumento(
                f"documento no encontrado ({respuesta.status_code})", transitorio=False
            )
        datos = respuesta.content
        nombre = Path(url.split("?")[0]).name or "documento"
    else:
        ruta = Path(url[7:]) if url.startswith("file://") else Path(url)
        if not ruta.is_file():
            raise ErrorDocumento(f"no existe el archivo {ruta}", transitorio=False)
        datos = ruta.read_bytes()
        nombre = ruta.name

    if not datos:
        raise ErrorDocumento("el documento está vacío", transitorio=False)
    if len(datos) > MAX_FILE_MB * 1024 * 1024:
        raise ErrorDocumento(
            f"el documento supera {MAX_FILE_MB:g} MB", transitorio=False
        )
    return datos, nombre


# ---------------------------------------------------------------------------
# Texto nativo (PDF digital)
# ---------------------------------------------------------------------------
def _texto_nativo_pdf(datos: bytes) -> tuple[int, str]:
    """``(número_de_páginas, texto)`` usando pypdfium2. ``(0, "")`` si no se puede abrir."""
    try:
        import pypdfium2 as pdfium

        pdf = pdfium.PdfDocument(datos)
    except Exception as exc:  # noqa: BLE001 - pdfium lanza tipos variados
        logger.warning("no se pudo abrir el PDF: %s", type(exc).__name__)
        return 0, ""

    partes: list[str] = []
    try:
        for indice in range(len(pdf)):
            pagina = pdf.get_page(indice)
            partes.append(pagina.get_textpage().get_text_bounded())
            pagina.close()
    finally:
        pdf.close()
    return len(partes), "\n".join(partes)


# ---------------------------------------------------------------------------
# Docling (PDF escaneado e imágenes)
# ---------------------------------------------------------------------------
def _markdown_docling(
    datos: bytes, nombre: str, *, usar_ocr: bool, permitir_imagen: bool
) -> str:
    from docling.datamodel.base_models import DocumentStream

    convertidor = _convertidor(usar_ocr, permitir_imagen)
    flujo = DocumentStream(name=nombre, stream=io.BytesIO(datos))
    # Un ``DocumentConverter`` comparte estado interno: se serializa el uso entre los
    # hilos del executor para que los mensajes concurrentes no lo corrompan.
    with _LOCK_CONVERTIDOR:
        resultado = convertidor.convert(flujo)
    return resultado.document.export_to_markdown()


@lru_cache(maxsize=4)
def _convertidor(usar_ocr: bool, permitir_imagen: bool) -> Any:
    """``DocumentConverter`` reutilizado por proceso (cargar los modelos es caro)."""
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import (
        EasyOcrOptions,
        PdfPipelineOptions,
        TableFormerMode,
    )
    from docling.document_converter import DocumentConverter, PdfFormatOption

    opciones = PdfPipelineOptions()
    opciones.do_ocr = usar_ocr
    opciones.do_table_structure = True
    opciones.table_structure_options.mode = TableFormerMode.ACCURATE
    opciones.table_structure_options.do_cell_matching = True
    opciones.do_picture_description = False
    opciones.generate_page_images = False
    opciones.enable_remote_services = False
    opciones.document_timeout = TIMEOUT_DOCUMENTO
    if usar_ocr:
        opciones.ocr_options = EasyOcrOptions(
            lang=_idiomas_ocr(),
            force_full_page_ocr=True,  # sin esto las imágenes devuelven solo el encabezado
        )

    formatos: dict[Any, Any] = {InputFormat.PDF: PdfFormatOption(pipeline_options=opciones)}
    permitidos = [InputFormat.PDF]
    if permitir_imagen:
        try:
            from docling.document_converter import ImageFormatOption

            formatos[InputFormat.IMAGE] = ImageFormatOption(pipeline_options=opciones)
            permitidos.append(InputFormat.IMAGE)
        except ImportError:  # pragma: no cover - depende de la versión de docling
            logger.warning("esta versión de docling no expone ImageFormatOption")

    return DocumentConverter(format_options=formatos, allowed_formats=permitidos)


def _idiomas_ocr() -> list[str]:
    crudo = os.getenv("OCR_LANG", "es,en")
    return [codigo.strip() for codigo in crudo.split(",") if codigo.strip()] or ["es", "en"]


# ---------------------------------------------------------------------------
# Estado del resultado
# ---------------------------------------------------------------------------
def _estado_extraccion(bl: BillOfLadingExtraido) -> ExtractionStatus:
    """Gate mínimo: sin datos no se publica como OK, y un dato alucinado va a revisión.

    ``MISSING_REQUIRED_FIELD`` lo agrega ``llm_extractor._extraer_con_contrato`` cuando, tras
    el reintento dirigido, algún campo clave quedó sin valor citado ni declaración de ausencia.
    """
    if es_extraccion_vacia(bl):
        return ExtractionStatus.ERROR
    if contar_campos_clave_con_valor(bl) < MIN_CAMPOS_CLAVE_CON_VALOR:
        # Hay texto suelto pero ningún campo clave: el contrato puede "cumplir" (el modelo
        # declaró todo como ausente) y aun así el resultado no sirve para el Core.
        logger.warning(
            "extracción sin campos clave: %s declarados ausentes",
            ", ".join(bl.campos_no_encontrados) or "(sin declarar)",
        )
        return ExtractionStatus.NEEDS_REVIEW
    if ReviewReason.GROUNDING_FAILED in bl.review_reasons:
        return ExtractionStatus.NEEDS_REVIEW
    if ReviewReason.MISSING_REQUIRED_FIELD in bl.review_reasons:
        return ExtractionStatus.NEEDS_REVIEW
    if _confianza_media(bl) < UMBRAL_CONFIANZA:
        return ExtractionStatus.NEEDS_REVIEW
    return ExtractionStatus.OK


def _confianza_media(bl: BillOfLadingExtraido) -> float:
    valores: list[float] = []
    _recolectar_confianzas(bl.model_dump(mode="python"), valores)
    if not valores:
        return 0.0
    return sum(valores) / len(valores)


def _recolectar_confianzas(dato: Any, acumulador: list[float]) -> None:
    if isinstance(dato, dict):
        if set(dato) == {"value", "confidence", "evidence"}:
            if dato.get("value"):
                acumulador.append(float(dato["confidence"]))
            return
        for valor in dato.values():
            _recolectar_confianzas(valor, acumulador)
    elif isinstance(dato, list):
        for elemento in dato:
            _recolectar_confianzas(elemento, acumulador)
