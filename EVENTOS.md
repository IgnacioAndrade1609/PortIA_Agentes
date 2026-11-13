# Eventos RabbitMQ — PortIA

Contrato de mensajería entre `PortIA_Core` y `PortIA_Agentes`, según `CONTEXTO_MAESTRO.md` sección 9 (ADR-006).

**Convenciones:**
- Nombre del evento: `PascalCase` (`ShipmentCreated`, no `shipment_created`).
- Nombre de campos dentro del payload: `snake_case` (única excepción documentada a la convención `camelCase` del resto de la API).
- IDs: `int` (la BD usa `INT IDENTITY`, no UUID).
- Versionado: campo agregado = retrocompatible. Campo eliminado/cambiado de tipo = nuevo evento `V2`, ambas versiones conviven durante la transición.
- Idempotencia: cada evento se identifica por una clave natural (`document_id` / `container_id`) + hash SHA-256 del contenido, verificado contra Redis antes de procesar (ver `idempotency.py`).
  - **Procesamiento:** `reclamar_trabajo()` toma el trabajo con `SET NX` (estado `en_proceso`); un duplicado concurrente no vuelve a ejecutar la extracción.
  - **Respuesta:** `guardar_resultado()` deja los eventos ya calculados en Redis (`IDEMPOTENCY_TTL`, 24 h por defecto); un reintento del mismo mensaje republica esa respuesta sin llamar al LLM.

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
- **Cola:** `data_extracted` (la declara MassTransit con `SnakeCaseEndpointNameFormatter`). El agente publica al *default exchange* con `routing_key="data_extracted"`, que enruta directo a esa cola.

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

`extraction_status` ∈ {`OK`, `NEEDS_REVIEW`, `ERROR`}; `NEEDS_REVIEW` si `confidence < 0.7`,
si el grounding falló, o si algún campo clave quedó sin respaldo verificable tras el reintento
dirigido (`review_reasons` incluye `missing_required_field`).

**Nota de contrato:** `extracted_data_raw` viaja siempre, incluso con `extraction_status = "NEEDS_REVIEW"` — permite corregir un campo mal extraído sin perder el resto (CU-COM-05, Ley 21.719).

**Nota operativa:** si el documento no se puede procesar, el agente **no publica nada**: el
mensaje va a la DLQ y el envío queda para reproceso manual desde RabbitMQ. El Core solo recibe
`DataExtracted` con datos extraídos.

**Decisión abierta (#4 del maestro):** un `DataExtracted` por contenedor (lo que modela este evento, opción recomendada) vs. uno por BL completo. Confirmar con el equipo antes de dar por cerrado el contrato.

## 4. RealStatusUpdated

- **Publica:** Agente Inspector
- **Consume:** Core (`RealStatusUpdatedConsumer`)
- **Cola:** `real_status_updated` (la declara MassTransit). El agente publica al *default exchange* con `routing_key="real_status_updated"`.

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

## Contrato implementado con el Core (MassTransit)

El Core publica los eventos de entrada a los agentes como **exchanges fanout** con el nombre
snake_case del evento; el agente declara el mismo exchange y bindea su cola (`shared/queues.py`):

| Evento | Dirección | Exchange / cola |
|---|---|---|
| `ShipmentCreated` | Core → Lector | exchange fanout `shipment_created`, cola `shipment_created` |
| `DataExtracted` | Lector → Core | cola `data_extracted` (default exchange) |
| `InspectContainer` | Core → Inspector | exchange fanout `inspect_container`, cola `inspect_container` |
| `RealStatusUpdated` | Inspector → Core | cola `real_status_updated` (default exchange) |

MassTransit serializa con `JsonNamingPolicy.SnakeCaseLower` y `UseRawJsonSerializer`, que es
exactamente el formato que producen los modelos Pydantic de `shared/events.py`.

## Flujo end-to-end

```text
POST /api/v1/shipments (Core, multipart "files")
  └─ sube a MinIO y publica ShipmentCreated (una por documento)
       └─ Agente Lector: descarga el PDF/imagen, extrae el BL y publica DataExtracted
            └─ Core DataExtractedConsumer: actualiza Documento, crea Container + Hito
                 └─ publica InspectContainer (requiere terminal de llegada)
                      └─ Agente Inspector: scraping + clima, publica RealStatusUpdated
                           └─ Core RealStatusUpdatedConsumer: actualiza Container, clima y riesgo
                                └─ GET /api/v1/containers alimenta la grilla del front
```

**Requisitos para que la cadena no se corte:**
- El documento debe superar el umbral de confianza; si no, queda `PENDIENTE_REVISION` y no se crea `Container`.
- El contenedor debe tener terminal de llegada (el Core lo infiere desde `port_of_destination`; se siembran terminales de San Antonio, Valparaíso, Arica, Iquique y Antofagasta al iniciar).
- `scraper_source="MOCK"` no corta el flujo: el Core descarta el estado de puerto pero registra el clima.

### Runners

- `python -m workflows.lector` — solo lector.
- `python -m workflows.inspector` — solo inspector.
- `uvicorn main:app` — ambos agentes en un proceso (`main.py`).
