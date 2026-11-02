"""Esquemas de datos del extractor (capas 2 y 3 de ``EXTRACTOR_DOCLING.md``).

Capa 2 — ``DocumentoNormalizado``
    Representación propia, plana y estable, derivada del ``DoclingDocument``. Es lo que
    consumen los validadores, el LLM y los tests. **Nadie aguas abajo importa ``docling``.**

Capa 3 — ``BillOfLadingExtraido``
    Esquema de dominio con evidencia y confianza por campo. Es lo único que produce el LLM
    y lo único que se mapea al contrato ``DataExtractedEvent`` (``shared/events.py``).

Decisiones registradas (2026-10-20)
-----------------------------------
1. **Todo valor extraído es ``str``** (``CampoExtraido.value``). La conversión a tipos
   (fecha, entero, decimal) ocurre en el mapeo o en los validadores, nunca en el esquema.
2. ``schema_version`` usa **SemVer en texto** (``"1.0.0"``); ``extractor_version`` conserva
   el formato ``<librería>@<versión>+<componente>@<versión>``.
3. Los modelos son **permisivos** (config Pydantic por defecto): un campo desconocido se
   ignora y los objetos son mutables, igual que en ``shared/events.py``.
4. El mapeo al evento **no valida**; ``confidence`` y ``extraction_status`` los fija el
   llamador (Fase 7).
"""

from enum import Enum

from pydantic import BaseModel, Field

from shared.events import ExtractionStatus

__all__ = [
    "SCHEMA_VERSION",
    "BillOfLadingExtraido",
    "Block",
    "BlockType",
    "BoundingBox",
    "CampoExtraido",
    "ContainerData",
    "ContentLayer",
    "DocumentoNormalizado",
    "EngineInfo",
    "Evidence",
    "EvidenceMethod",
    "ExtractionStatus",
    "PageInfo",
    "ParteComercial",
    "ResultadoExtraccion",
    "ReviewReason",
    "SourceInfo",
    "TableCell",
    "TableData",
    "WarningCode",
]

#: Versión del contrato de las capas 2 y 3. Sube al cambiar de forma incompatible.
SCHEMA_VERSION = "1.0.0"


# ---------------------------------------------------------------------------
# Enums cerrados
# ---------------------------------------------------------------------------
class BlockType(str, Enum):
    """Tipo de bloque en el orden de lectura del documento."""

    HEADER = "header"
    SECTION_HEADER = "section_header"
    TEXT = "text"
    TABLE = "table"
    PICTURE = "picture"
    FOOTER = "footer"
    KEY_VALUE = "key_value"


class ContentLayer(str, Enum):
    """Capa de contenido de Docling (``body`` vs ``furniture``)."""

    BODY = "body"
    FURNITURE = "furniture"


class EvidenceMethod(str, Enum):
    """Cómo se obtuvo el valor citado en la evidencia."""

    TEXT_LAYER = "text_layer"
    OCR = "ocr"
    LLM = "llm"
    INFERRED = "inferred"


class WarningCode(str, Enum):
    """Avisos no bloqueantes del pipeline (enum cerrado para poder medirlos)."""

    TABLE_SPAN_DUPLICATED = "table_span_duplicated"
    MULTIPLE_TABLES_MERGED = "multiple_tables_merged"
    NO_HEADER_DETECTED = "no_header_detected"
    EMPTY_CELLS = "empty_cells"
    PICTURE_TEXT_FILTERED = "picture_text_filtered"
    FURNITURE_KEPT = "furniture_kept"
    OCR_LOW_CONF = "ocr_low_conf"
    OCR_SKIPPED = "ocr_skipped"
    EMPTY_DOCUMENT = "empty_document"
    UNUSUAL_COORD_ORIGIN = "unusual_coord_origin"


class ReviewReason(str, Enum):
    """Motivos por los que un documento debe ir a revisión humana."""

    GROUNDING_FAILED = "grounding_failed"
    LOW_OCR = "low_ocr"
    LOW_CONFIDENCE = "low_confidence"
    INVALID_CONTAINER_CHECK_DIGIT = "invalid_container_check_digit"
    DATE_AMBIGUOUS = "date_ambiguous"
    PORT_AMBIGUOUS = "port_ambiguous"
    MISSING_REQUIRED_FIELD = "missing_required_field"
    CROSS_CHECK_MISMATCH = "cross_check_mismatch"
    MULTIPLE_DOCUMENTS = "multiple_documents"
    EMPTY_EXTRACTION = "empty_extraction"


# ---------------------------------------------------------------------------
# Capa 2 — DocumentoNormalizado
# ---------------------------------------------------------------------------
class BoundingBox(BaseModel):
    """Rectángulo normalizado a ``0..1`` respecto del tamaño de la página."""

    x0: float
    y0: float
    x1: float
    y1: float


class SourceInfo(BaseModel):
    """Origen del archivo. ``sha256`` es la clave de idempotencia (no el ``binary_hash``)."""

    filename: str
    mime: str
    sha256: str
    size_bytes: int = 0


class EngineInfo(BaseModel):
    """Motor usado para producir el documento. Se guarda para poder comparar corridas."""

    ocr: str = ""
    lang: list[str] = Field(default_factory=list)
    ocr_used: bool = False
    docling_version: str = ""
    elapsed_ms: int = 0


class PageInfo(BaseModel):
    """Geometría y densidad de texto de una página."""

    page_no: int
    width: float
    height: float
    text_chars: int = 0
    #: ``True`` si Docling aplicó OCR en esta página (Fase 6 lo usa para el fallback).
    ocr_used: bool = False


class Block(BaseModel):
    """Unidad de contenido en orden de lectura."""

    order: int
    type: BlockType
    page: int
    text: str = ""
    bbox: BoundingBox | None = None
    layer: ContentLayer = ContentLayer.BODY


class TableCell(BaseModel):
    """Celda reconstruida desde ``table_cells`` (nunca desde ``grid``)."""

    r: int
    c: int
    rowspan: int = 1
    colspan: int = 1
    text: str = ""
    page: int | None = None
    bbox: BoundingBox | None = None


class TableData(BaseModel):
    """Tabla reconstruida, deduplicada y con encabezado inferido."""

    id: str
    page: int | None = None
    rows: list[list[str]] = Field(default_factory=list)
    cells: list[TableCell] = Field(default_factory=list)
    header_row_idx: int | None = None
    #: Ids de las tablas de origen si esta tabla se unió desde varias (BL partido en 3).
    merged_from: list[str] = Field(default_factory=list)


class DocumentoNormalizado(BaseModel):
    """Capa 2: contrato interno versionado. Es la entrada del LLM y de los validadores."""

    schema_version: str = Field(default=SCHEMA_VERSION, description="Versión SemVer del esquema.")
    extractor_version: str = Field(description="p. ej. 'docling@2.140.0+norm@1'.")
    source: SourceInfo
    engine: EngineInfo = Field(default_factory=EngineInfo)
    pages: list[PageInfo] = Field(default_factory=list)
    blocks: list[Block] = Field(default_factory=list)
    tables: list[TableData] = Field(default_factory=list)
    markdown: str = Field(default="", description="Documento completo en Markdown (entrada del LLM).")
    warnings: list[WarningCode] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Capa 3 — BillOfLadingExtraido
# ---------------------------------------------------------------------------
class Evidence(BaseModel):
    """Trazabilidad de un valor: dónde y cómo aparece en el documento."""

    page: int | None = None
    text: str = Field(default="", description="Fragmento literal del documento.")
    bbox: BoundingBox | None = None
    method: EvidenceMethod = EvidenceMethod.LLM


class CampoExtraido(BaseModel):
    """Valor crudo de un campo, siempre ``str``, con su confianza y su evidencia."""

    value: str | None = Field(default=None, description="Valor tal como aparece en el documento.")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    evidence: list[Evidence] = Field(default_factory=list)

    @property
    def presente(self) -> bool:
        """``True`` si hay un valor no vacío (no valida su corrección)."""
        return bool(self.value and self.value.strip())


class ParteComercial(BaseModel):
    """Embarcador, consignatario, notificar o cualquier parte nombrada del BL."""

    nombre: CampoExtraido = Field(default_factory=CampoExtraido)
    direccion: CampoExtraido = Field(default_factory=CampoExtraido)
    #: RUT (Chile), tax id, NIT u otro identificador tributario de la parte.
    identificador: CampoExtraido = Field(default_factory=CampoExtraido)
    #: Teléfono o correo, cuando el documento lo trae.
    contacto: CampoExtraido = Field(default_factory=CampoExtraido)


class ContainerData(BaseModel):
    """Contenedor declarado en el BL."""

    number: CampoExtraido = Field(default_factory=CampoExtraido, description="Número de contenedor (ISO 6346).")
    iso_type: CampoExtraido = Field(default_factory=CampoExtraido, description="Tipo ISO, p. ej. '45G0'.")
    seal: CampoExtraido = Field(default_factory=CampoExtraido, description="Precinto / sello.")


class BillOfLadingExtraido(BaseModel):
    """Capa 3: BL extraído. Todos los campos son opcionales; ``null`` = no aparece.

    El LLM **no** rellena ``confidence`` ni ``evidence``: las calcula el pipeline
    (grounding + validadores + OCR). Ver ``llm_schema.esquema_para_llm()``.
    """

    schema_version: str = Field(default=SCHEMA_VERSION, description="Versión SemVer del esquema.")

    # --- Identificación del documento ---
    document_type: CampoExtraido = Field(default_factory=CampoExtraido, description="Tipo de documento, p. ej. 'BL'.")
    bl_number: CampoExtraido = Field(default_factory=CampoExtraido, description="Número del conocimiento de embarque (#BL / B/L No.).")
    bl_madre: CampoExtraido = Field(default_factory=CampoExtraido, description="Número del BL madre (master B/L).")
    folio: CampoExtraido = Field(default_factory=CampoExtraido, description="Folio del documento (Chile: folio de Aduanas).")
    codigo_verificacion: CampoExtraido = Field(default_factory=CampoExtraido, description="Código de verificación impreso junto al folio.")
    booking_number: CampoExtraido = Field(default_factory=CampoExtraido, description="Número de reserva (booking).")
    bl_type: CampoExtraido = Field(default_factory=CampoExtraido, description="Modalidad: original, telex release, straight, order, to bearer.")
    numero_originales: CampoExtraido = Field(default_factory=CampoExtraido, description="Cantidad de originales emitidos.")
    operacion: CampoExtraido = Field(default_factory=CampoExtraido, description="Sentido de la operación: importación, exportación o tránsito.")

    # --- Partes ---
    shipper: ParteComercial = Field(default_factory=ParteComercial, description="Embarcador / exportador.")
    consignee: ParteComercial = Field(default_factory=ParteComercial, description="Consignatario / importador.")
    notify_party: ParteComercial = Field(default_factory=ParteComercial, description="Parte a notificar.")
    also_notify: ParteComercial = Field(default_factory=ParteComercial, description="Segunda parte a notificar, si existe.")
    forwarder_name: CampoExtraido = Field(default_factory=CampoExtraido, description="Forwarder / agente de carga.")
    agent: CampoExtraido = Field(default_factory=CampoExtraido, description="Agente de naves o agente marítimo emisor.")
    signed_by: CampoExtraido = Field(default_factory=CampoExtraido, description="Nombre de quien firma el BL.")

    # --- Transporte ---
    shipping_line: CampoExtraido = Field(default_factory=CampoExtraido, description="Naviera / transportista marítimo.")
    carrier_scac: CampoExtraido = Field(default_factory=CampoExtraido, description="Código SCAC del transportista.")
    vessel: CampoExtraido = Field(default_factory=CampoExtraido, description="Nombre del buque.")
    voyage_number: CampoExtraido = Field(default_factory=CampoExtraido, description="Número de viaje.")
    service_type: CampoExtraido = Field(default_factory=CampoExtraido, description="Servicio o ruta, p. ej. 'LINER'.")

    # --- Puertos y lugares ---
    port_of_loading: CampoExtraido = Field(default_factory=CampoExtraido, description="Puerto de embarque.")
    port_of_discharge: CampoExtraido = Field(default_factory=CampoExtraido, description="Puerto de descarga.")
    port_of_transshipment: CampoExtraido = Field(default_factory=CampoExtraido, description="Puerto de transbordo.")
    place_of_receipt: CampoExtraido = Field(default_factory=CampoExtraido, description="Lugar de recepción de la carga.")
    place_of_delivery: CampoExtraido = Field(default_factory=CampoExtraido, description="Lugar de entrega.")
    final_destination: CampoExtraido = Field(default_factory=CampoExtraido, description="Destino final de la mercancía.")
    country_of_origin: CampoExtraido = Field(default_factory=CampoExtraido, description="País de origen de la mercancía.")

    # --- Fechas ---
    place_of_issue: CampoExtraido = Field(default_factory=CampoExtraido, description="Lugar de emisión del BL.")
    date_of_issue: CampoExtraido = Field(default_factory=CampoExtraido, description="Fecha de emisión (ISO 8601 si es posible).")
    shipped_on_board_date: CampoExtraido = Field(default_factory=CampoExtraido, description="Fecha de embarque a bordo.")
    eta: CampoExtraido = Field(default_factory=CampoExtraido, description="Fecha estimada de arribo (ISO 8601 si es posible).")

    # --- Carga ---
    goods_description: CampoExtraido = Field(default_factory=CampoExtraido, description="Descripción de la mercancía.")
    marks_and_numbers: CampoExtraido = Field(default_factory=CampoExtraido, description="Marcas y números.")
    packages_count: CampoExtraido = Field(default_factory=CampoExtraido, description="Cantidad de bultos.")
    packages_type: CampoExtraido = Field(default_factory=CampoExtraido, description="Tipo de bulto.")
    gross_weight: CampoExtraido = Field(default_factory=CampoExtraido, description="Peso bruto.")
    gross_weight_unit: CampoExtraido = Field(default_factory=CampoExtraido, description="Unidad del peso bruto (KGM).")
    volume: CampoExtraido = Field(default_factory=CampoExtraido, description="Volumen.")
    volume_unit: CampoExtraido = Field(default_factory=CampoExtraido, description="Unidad de volumen (MTQ).")
    containers: list[ContainerData] = Field(default_factory=list, description="Contenedores declarados.")

    # --- Flete y condiciones ---
    freight_terms: CampoExtraido = Field(default_factory=CampoExtraido, description="Condición del flete: prepaid / collect.")
    freight_amount: CampoExtraido = Field(default_factory=CampoExtraido, description="Monto del flete.")
    freight_currency: CampoExtraido = Field(default_factory=CampoExtraido, description="Moneda del flete.")
    demurrage_free_days: CampoExtraido = Field(default_factory=CampoExtraido, description="Días libres de demora (demurrage).")
    detention_free_days: CampoExtraido = Field(default_factory=CampoExtraido, description="Días libres de detención (detention).")

    # --- Otros ---
    observations: CampoExtraido = Field(default_factory=CampoExtraido, description="Observaciones o instrucciones especiales.")
    warnings: list[WarningCode] = Field(default_factory=list)
    review_reasons: list[ReviewReason] = Field(default_factory=list)


class ResultadoExtraccion(BaseModel):
    """Salida del pipeline completo: puede contener varios BLs (PDF multi-documento)."""

    status: ExtractionStatus
    documentos: list[BillOfLadingExtraido] = Field(default_factory=list)
    motivos: list[ReviewReason] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    warnings: list[WarningCode] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)
