import hashlib
import os

import redis.asyncio as redis

r = redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379"))


async def intentar_marcar_procesado(clave: str, ttl: int = 86400) -> bool:
    """Marca la clave como procesada de forma atómica (SET NX).

    Retorna True si esta llamada fue la que marcó la clave (evento nuevo),
    False si ya estaba marcada (evento duplicado). Un GET+SET separado
    tiene una carrera bajo consumo concurrente: dos mensajes idénticos
    pueden pasar ambos el chequeo antes de que cualquiera marque.
    """
    return bool(await r.set(f"procesado:{clave}", "1", ex=ttl, nx=True))


def hash_evento(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()
