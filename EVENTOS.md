# Eventos RabbitMQ — PortIA

Contrato de mensajería entre `PortIA_Core` y `PortIA_Agentes`, según `CONTEXTO_MAESTRO.md` sección 9 (ADR-006).

**Convenciones:**
- Nombre del evento: `PascalCase` (`ShipmentCreated`, no `shipment_created`).
- Nombre de campos dentro del payload: `snake_case` (única excepción documentada a la convención `camelCase` del resto de la API).
- IDs: `int` (la BD usa `INT IDENTITY`, no UUID).
- Versionado: campo agregado = retrocompatible. Campo eliminado/cambiado de tipo = nuevo evento `V2`, ambas versiones conviven durante la transición.
- Idempotencia: cada evento se identifica por una clave natural (`document_id` / `container_id`) + hash SHA-256 del contenido, verificado contra Redis antes de procesar (ver `idempotency.py`).

Modelos Pydantic: `shared/events.py`.

Estructura del repo: `shared/` tiene lo transversal (eventos, mensajería, idempotencia, logging); cada agente (`agente_lector/`, `agente_inspector/`) tiene su propio `consumer.py` (transporte: traduce mensajes RabbitMQ a llamadas de servicio) y `service.py` (lógica de negocio, sin dependencias de RabbitMQ — se puede probar en aislado).

---

## 1. ShipmentCreated

- **Publica:** Core (`CreateShipmentHandler`)
- **Consume:** Agente Lector
- **Cola:** `shipment_created` (DLQ: `shipment_created.dlq`)

```json
{
  "shipment_id": 101,
  "document_id": 501,
  "document_url": "https://minio.local/portia-documentos/abc123.pdf",
  "document_type": "BL"
}
```

## 2. InspectContainer

- **Publica:** Core (`ProcessExtractedDataHandler`)
- **Consume:** Agente Inspector
- **Cola:** `inspect_container` (DLQ: `inspect_container.dlq`)

```json
{
  "container_id": 301,
  "container_number": "IKSU5203585",
  "terminal_code": "STI",
  "terminal_config": {
    "url": "https://portal.sti.cl",
    "selectors": { "estado": "#estado-contenedor" }
  }
}
```

`terminal_config` alimenta el Strategy Pattern de scraping (`ITerminalScraper`, ADR-002): si el terminal cambia su web, solo se actualiza este JSON, no el código.

## 3. DataExtracted

- **Publica:** Agente Lector
- **Consume:** Core (`DataExtractedConsumer`)
- **Cola:** sin definir todavía del lado Core (la declara MassTransit) — del lado del agente solo se publica con `routing_key="DataExtracted"` hasta coordinar el binding exacto.

```json
{
  "shipment_id": 101,
  "document_id": 501,
  "document_type": "BL",
  "bl_number": "EVGR123456",
  "container_number": "IKSU5203585",
  "shipping_line": "Evergreen",
  "port_of_destination": "San Antonio",
  "eta": "2026-10-15T00:00:00",
  "demurrage_free_days": 7,
  "detention_free_days": 7,
  "forwarder_name": "Agencia XYZ",
  "extracted_data_raw": { "raw_field": "valor crudo del modelo IA" },
  "confidence": 0.95,
  "extraction_status": "OK"
}
```

`extraction_status` ∈ {`OK`, `NEEDS_REVIEW`, `ERROR`}; `NEEDS_REVIEW` si `confidence < 0.7`.

**Nota de contrato:** `extracted_data_raw` viaja siempre, incluso con `extraction_status = "NEEDS_REVIEW"` — permite corregir un campo mal extraído sin perder el resto (CU-COM-05, Ley 21.719).

**Decisión abierta (#4 del maestro):** un `DataExtracted` por contenedor (lo que modela este evento, opción recomendada) vs. uno por BL completo. Confirmar con el equipo antes de dar por cerrado el contrato.

## 4. RealStatusUpdated

- **Publica:** Agente Inspector
- **Consume:** Core (`RealStatusUpdatedConsumer`)
- **Cola:** sin definir todavía del lado Core (la declara MassTransit).

```json
{
  "container_id": 301,
  "container_number": "IKSU5203585",
  "real_arrival_date": "2026-10-14T08:00:00Z",
  "port_status": "En patio",
  "weather_alert": false,
  "weather_data": { "wave_height_m": 1.2, "source": "open-meteo" },
  "scraper_source": "STI",
  "scraped_at": "2026-09-20T10:30:00Z"
}
```

---

## Pendiente de coordinar con el Core (MassTransit)

El Core usa MassTransit, que no publica a una cola nombrada simple: genera sus propios exchanges/bindings por tipo de mensaje. El consumer de agentes (aio-pika, cola declarada manualmente) solo recibirá los eventos de Core si el exchange/routing-key calzan exactamente. Antes de probar extremo a extremo con el Core real, confirmar:
1. Nombre del exchange que MassTransit crea para `ShipmentCreated` e `InspectContainer`.
2. Si MassTransit espera un exchange por tipo (`fanout`/`topic`) en vez de la cola simple que usa este agente hoy.

Mientras tanto, este sprint se prueba publicando manualmente desde la consola de RabbitMQ (`localhost:15672`).
