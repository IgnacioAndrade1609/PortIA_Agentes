"""Runner independiente del Agente Inspector.

Uso:
    python -m workflows.inspector

Requiere:
    - RabbitMQ en RABBITMQ_URL
    - Redis en REDIS_URL (idempotencia de eventos ya procesados)

Solo consume ``inspect_container``. El lector queda fuera de este proceso.
"""

from __future__ import annotations

import asyncio
import logging
import os

import aio_pika

import agente_inspector.consumer as agente_inspector
from shared.idempotency import r as redis_client
from shared.logging_config import setup_logging
from shared.queues import declarar_colas

logger = logging.getLogger("portia.workflow.inspector")


async def run() -> None:
    setup_logging()

    rabbit_url = os.getenv("RABBITMQ_URL", "amqp://guest:guest@localhost:5672/")
    connection = await aio_pika.connect_robust(rabbit_url)

    try:
        channel = await connection.channel()
        # Una inspección a la vez por worker: el scraping bloquea y así el
        # reparto entre réplicas queda predecible.
        await channel.set_qos(prefetch_count=1)

        await redis_client.ping()
        logger.info("Redis OK (idempotencia de inspecciones activa)")

        colas = await declarar_colas(channel)
        await agente_inspector.start(colas["inspect_container"], channel)

        logger.info("Workflow Inspector iniciado; esperando InspectContainer...")
        await asyncio.Event().wait()
    finally:
        await connection.close()


def main() -> None:
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        logger.info("Workflow Inspector detenido por el usuario")


if __name__ == "__main__":
    main()
