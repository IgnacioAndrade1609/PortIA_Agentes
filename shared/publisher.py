import aio_pika
from pydantic import BaseModel


async def publish_event(channel: aio_pika.abc.AbstractChannel, routing_key: str, event: BaseModel) -> None:
    message = aio_pika.Message(
        body=event.model_dump_json().encode(),
        content_type="application/json",
        delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
    )
    await channel.default_exchange.publish(message, routing_key=routing_key)
