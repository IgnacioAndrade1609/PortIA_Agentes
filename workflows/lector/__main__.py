"""Runner independiente del Agente Lector.

Uso:
    python -m workflows.lector

Requiere:
    - RabbitMQ en RABBITMQ_URL
    - Redis en REDIS_URL (idempotencia de trabajos activos y resultados)

Solo consume ``shipment_created``. El inspector queda fuera de este proceso.
"""

from __future__ import annotations

import asyncio
import logging
import os

import aio_pika

import agente_lector.consumer as agente_lector
from shared.idempotency import TTL_LOCK, r as redis_client
from shared.logging_config import setup_logging
from shared.queues import declarar_colas

logger = logging.getLogger("portia.workflow.lector")


async def run() -> None:
    setup_logging()

    rabbit_url = os.getenv("RABBITMQ_URL", "amqp://guest:guest@localhost:5672/")
    connection = await aio_pika.connect_robust(rabbit_url)

    try:
        channel = await connection.channel()
        # Un mensaje a la vez por worker: menos concurrencia sobre Docling/LLM y
        # más predecible para la demo.
        await channel.set_qos(prefetch_count=1)

        await redis_client.ping()
        logger.info("Redis OK (idempotencia activa, lock TTL=%ss)", TTL_LOCK)

        colas = await declarar_colas(channel)
        await agente_lector.start(colas["shipment_created"])

        logger.info("Workflow Lector iniciado; esperando ShipmentCreated...")
        await asyncio.Event().wait()
    finally:
        await connection.close()


def main() -> None:
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        logger.info("Workflow Lector detenido por el usuario")


if __name__ == "__main__":
    main()
