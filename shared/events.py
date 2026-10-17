"""Modelos de los eventos que circulan por RabbitMQ entre Core y Agentes.

Contrato completo (quién publica, quién consume, JSON de ejemplo): ver EVENTOS.md.
"""

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator


class ExtractionStatus(str, Enum):
    OK = "OK"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    ERROR = "ERROR"


class ShipmentCreatedEvent(BaseModel):
    """Publica: Core. Consume: Agente Lector."""

    shipment_id: int
    document_id: int
    document_url: str
    document_type: str


class InspectContainerEvent(BaseModel):
    """Publica: Core. Consume: Agente Inspector."""

    container_id: int
    container_number: str
    terminal_code: str
    terminal_config: dict


class DataExtractedEvent(BaseModel):
    """Publica: Agente Lector. Consume: Core (DataExtractedConsumer)."""

    shipment_id: int
    document_id: int
    document_type: str
    bl_number: Optional[str] = None
    container_number: str
    shipping_line: Optional[str] = None
    port_of_destination: Optional[str] = None
    eta: Optional[datetime] = None
    demurrage_free_days: Optional[int] = None
    detention_free_days: Optional[int] = None
    forwarder_name: Optional[str] = None
    extracted_data_raw: dict
    confidence: float = Field(ge=0.0, le=1.0)
    extraction_status: ExtractionStatus

    @field_validator("confidence")
    @classmethod
    def round_confidence(cls, v):
        return round(v, 2)


class RealStatusUpdatedEvent(BaseModel):
    """Publica: Agente Inspector. Consume: Core (RealStatusUpdatedConsumer)."""

    container_id: int
    container_number: str
    real_arrival_date: Optional[datetime] = None
    port_status: str
    weather_alert: bool
    weather_data: Optional[dict] = None
    scraper_source: str
    scraped_at: datetime
