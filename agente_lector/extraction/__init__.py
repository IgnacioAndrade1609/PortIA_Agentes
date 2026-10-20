"""Paquete de extracción del Agente Lector.

Solo este paquete conoce Docling, y únicamente a través de ``docling_runner`` (Fase 3).
El resto del sistema —validadores, LLM, publisher— trabaja con los esquemas de aquí.

Capas (ver ``temp/EXTRACTOR_DOCLING.md``):

1. ``DoclingDocument`` crudo → se archiva, no se interpreta.
2. ``DocumentoNormalizado`` → contrato interno versionado (``schemas``).
3. ``BillOfLadingExtraido`` → dominio + evidencia + confianza (``schemas``).
4. ``DataExtractedEvent`` → contrato con el Core (``mapping``).
"""

from agente_lector.extraction.mapping import a_evento, a_eventos
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
    EvidenceMethod,
    PageInfo,
    ParteComercial,
    ResultadoExtraccion,
    ReviewReason,
    SourceInfo,
    TableCell,
    TableData,
    WarningCode,
)

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
    "PageInfo",
    "ParteComercial",
    "ResultadoExtraccion",
    "ReviewReason",
    "SourceInfo",
    "TableCell",
    "TableData",
    "WarningCode",
    "a_evento",
    "a_eventos",
]
