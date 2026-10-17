import aio_pika

DLX_NAME = "dlx"

# Colas que consumen los agentes, con su DLQ asociada.
COLAS_CONSUMIDAS = ["shipment_created", "inspect_container"]


async def declarar_colas(channel: aio_pika.abc.AbstractChannel) -> dict[str, aio_pika.abc.AbstractQueue]:
    dlx = await channel.declare_exchange(DLX_NAME, aio_pika.ExchangeType.DIRECT, durable=True)

    colas: dict[str, aio_pika.abc.AbstractQueue] = {}
    for nombre in COLAS_CONSUMIDAS:
        dlq_routing_key = f"{nombre}.dlq"
        dlq = await channel.declare_queue(dlq_routing_key, durable=True)
        await dlq.bind(dlx, routing_key=dlq_routing_key)

        cola = await channel.declare_queue(
            nombre,
            durable=True,
            arguments={
                "x-dead-letter-exchange": DLX_NAME,
                "x-dead-letter-routing-key": dlq_routing_key,
            },
        )
        colas[nombre] = cola

    return colas
