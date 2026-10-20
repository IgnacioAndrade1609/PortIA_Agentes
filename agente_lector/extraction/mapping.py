"""Mapeo capa 3 → contrato de eventos (``DataExtractedEvent``).

Este módulo **traduce, no valida** (decisión 2026-10-20: sin validaciones en Fase 1).
La corrección de datos (ISO 6346, fechas, unidades, cross-checks) es Fase 5 y el cálculo de
``confidence`` / ``extraction_status`` confiables es Fase 7.

Reglas de traducción
--------------------
- Si un valor no se puede convertir al tipo que exige el evento (p. ej. ``eta`` no parseable
  como fecha ISO), se envía ``None`` en vez de fallar.
- Un BL con *N* contenedores produce *N* eventos (la decisión #4 de ``EVENTOS.md`` sigue
  abierta). Un BL sin contenedores produce **un** evento con ``container_number = ""``,
  que Fase 7 marcará como ``NEEDS_REVIEW``.
- ``extracted_data_raw`` lleva el BL completo serializado (campos string + warnings), nunca
  el ``DoclingDocument``.
"""

import re
from datetime import datetime

from agente_lector.extraction.schemas import BillOfLadingExtraido, CampoExtraido
from shared.events import DataExtractedEvent, ExtractionStatus

__all__ = ["a_evento", "a_eventos"]

_CLAVES_CAMPO = {"value", "confidence", "evidence"}


def _texto(campo: CampoExtraido | None) -> str | None:
    """Valor limpio del campo, o ``None`` si está vacío."""
    if campo is None or campo.value is None:
        return None
    valor = campo.value.strip()
    return valor or None


def _entero(campo: CampoExtraido | None) -> int | None:
    """Primer entero del valor (``"7 días"`` → ``7``), o ``None``."""
    texto = _texto(campo)
    if texto is None:
        return None
    coincidencia = re.search(r"-?\d+", texto)
    return int(coincidencia.group()) if coincidencia else None


def _fecha(campo: CampoExtraido | None) -> datetime | None:
    """Fecha ISO 8601 del valor, o ``None`` si no es parseable."""
    texto = _texto(campo)
    if texto is None:
        return None
    try:
        return datetime.fromisoformat(texto)
    except ValueError:
        return None


def _confianzas(dato) -> list[float]:
    """Junta las confianzas de todos los ``CampoExtraido`` con valor presente."""
    if isinstance(dato, dict):
        if set(dato) == _CLAVES_CAMPO:
            return [float(dato["confidence"])] if dato.get("value") else []
        return [c for valor in dato.values() for c in _confianzas(valor)]
    if isinstance(dato, list):
        return [c for elemento in dato for c in _confianzas(elemento)]
    return []


def _confianza_agregada(bl: BillOfLadingExtraido) -> float:
    """Media simple, **provisional**: Fase 7 la reemplaza por un mínimo ponderado."""
    valores = _confianzas(bl.model_dump(mode="python"))
    if not valores:
        return 0.0
    return round(sum(valores) / len(valores), 2)


def a_eventos(
    bl: BillOfLadingExtraido,
    *,
    shipment_id: int,
    document_id: int,
    document_type: str | None = None,
    confidence: float | None = None,
    extraction_status: ExtractionStatus = ExtractionStatus.OK,
) -> list[DataExtractedEvent]:
    """Convierte un BL extraído en uno o más ``DataExtractedEvent`` (uno por contenedor)."""
    base: dict = {
        "shipment_id": shipment_id,
        "document_id": document_id,
        "document_type": document_type or _texto(bl.document_type) or "BL",
        "bl_number": _texto(bl.bl_number),
        "shipping_line": _texto(bl.shipping_line),
        "port_of_destination": _texto(bl.port_of_discharge),
        "eta": _fecha(bl.eta),
        "demurrage_free_days": _entero(bl.demurrage_free_days),
        "detention_free_days": _entero(bl.detention_free_days),
        "forwarder_name": _texto(bl.forwarder_name),
        "extracted_data_raw": bl.model_dump(mode="json"),
        "confidence": confidence if confidence is not None else _confianza_agregada(bl),
        "extraction_status": extraction_status,
    }

    numeros = [numero for numero in (_texto(contenedor.number) for contenedor in bl.containers) if numero]
    if not numeros:
        return [DataExtractedEvent(container_number="", **base)]
    return [DataExtractedEvent(container_number=numero, **base) for numero in numeros]


def a_evento(
    bl: BillOfLadingExtraido,
    *,
    shipment_id: int,
    document_id: int,
    document_type: str | None = None,
    confidence: float | None = None,
    extraction_status: ExtractionStatus = ExtractionStatus.OK,
) -> DataExtractedEvent:
    """Atajo para el caso de un solo contenedor: devuelve el primer evento."""
    return a_eventos(
        bl,
        shipment_id=shipment_id,
        document_id=document_id,
        document_type=document_type,
        confidence=confidence,
        extraction_status=extraction_status,
    )[0]
