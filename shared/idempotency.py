"""Idempotencia y caché de trabajos en Redis.

Se cubren **dos** niveles, que es lo que necesita un consumidor de RabbitMQ:

1. **Procesamiento** — :func:`reclamar_trabajo` hace un ``SET NX`` atómico con estado
   ``en_proceso``. Si dos entregas del mismo evento llegan a la vez (o dos réplicas del
   agente), solo una gana el reclamo; la otra no vuelve a ejecutar la extracción.
2. **Respuesta** — :func:`guardar_resultado` deja el resultado ya calculado en Redis durante
   ``IDEMPOTENCY_TTL``. Un reintento posterior (mensaje reentregado desde la DLQ o el mismo
   documento procesado de nuevo) devuelve exactamente la misma respuesta sin volver a llamar
   al LLM, que es la parte cara y no determinista.

Cada clave guarda **un único** JSON::

    {
      "estado": "en_proceso" | "listo" | "fallido",
      "resultado": [ ...eventos... ],   # solo cuando estado == "listo"
      "error": "detalle",               # solo cuando estado == "fallido"
      "actualizado": "2026-10-30T12:00:00+00:00"
    }

``GET``+``SET`` separados tienen carrera bajo consumo concurrente: dos mensajes idénticos
pueden pasar ambos el chequeo antes de que cualquiera marque la clave. Por eso el reclamo
es un ``SET NX``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import UTC, datetime
from typing import Any, NamedTuple, Protocol

import redis.asyncio as redis
from dotenv import load_dotenv

# Carga .env antes de crear el cliente: REDIS_URL debe estar disponible al importar.
load_dotenv()

logger = logging.getLogger("portia.idempotencia")

__all__ = [
    "ESTADO_EN_PROCESO",
    "ESTADO_FALLIDO",
    "ESTADO_LISTO",
    "ESTADO_NUEVO",
    "PREFIJO_TRABAJO",
    "Reclamo",
    "clave_trabajo",
    "guardar_resultado",
    "hash_evento",
    "intentar_marcar_procesado",
    "liberar_trabajo",
    "marcar_fallido",
    "obtener_resultado",
    "reclamar_trabajo",
]

#: Prefijo de las claves de trabajo (permite `SCAN trabajo:*` para inspección).
PREFIJO_TRABAJO = "trabajo:"

ESTADO_NUEVO = "nuevo"
ESTADO_EN_PROCESO = "en_proceso"
ESTADO_LISTO = "listo"
ESTADO_FALLIDO = "fallido"

#: Cuánto se conserva el resultado ya calculado (24 h por defecto).
TTL_TRABAJO = int(os.getenv("IDEMPOTENCY_TTL", "86400"))

#: TTL del lock mientras se procesa; si el worker muere, el trabajo se puede reintentar.
TTL_LOCK = int(os.getenv("IDEMPOTENCY_LOCK_TTL", "900"))

r = redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379"))


class ClienteRedis(Protocol):
    """Subconjunto de ``redis.asyncio.Redis`` que usa este módulo (facilita los tests)."""

    async def get(self, clave: str) -> Any: ...
    async def set(self, clave: str, valor: str, *, ex: int | None = None, nx: bool = False) -> Any: ...
    async def delete(self, clave: str) -> Any: ...


class Reclamo(NamedTuple):
    """Resultado de intentar tomar un trabajo.

    ``estado`` es uno de ``nuevo``, ``en_proceso``, ``listo`` o ``fallido``. Cuando es
    ``listo``, ``resultado`` trae la respuesta cacheada.
    """

    estado: str
    resultado: list[dict] | None = None


# ---------------------------------------------------------------------------
# Claves
# ---------------------------------------------------------------------------
def hash_evento(payload: bytes) -> str:
    """SHA-256 del mensaje crudo: identifica la entrega, no el contenido lógico."""
    return hashlib.sha256(payload).hexdigest()


def clave_trabajo(clave: str) -> str:
    return f"{PREFIJO_TRABAJO}{clave}"


def _cliente(cliente: ClienteRedis | None) -> ClienteRedis:
    return cliente if cliente is not None else r


def _ahora() -> str:
    return datetime.now(tz=UTC).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Lectura / escritura del registro
# ---------------------------------------------------------------------------
async def _leer(cliente: ClienteRedis, clave: str) -> dict[str, Any] | None:
    crudo = await cliente.get(clave)
    if crudo is None:
        return None
    if isinstance(crudo, (bytes, bytearray)):
        crudo = crudo.decode("utf-8")
    try:
        datos = json.loads(crudo)
    except (TypeError, ValueError):
        logger.warning("registro de idempotencia corrupto en %s; se ignora", clave)
        return None
    return datos if isinstance(datos, dict) else None


async def _escribir(cliente: ClienteRedis, clave: str, registro: dict[str, Any], ttl: int) -> None:
    await cliente.set(clave, json.dumps(registro, ensure_ascii=False), ex=ttl)


# ---------------------------------------------------------------------------
# API de trabajos
# ---------------------------------------------------------------------------
async def reclamar_trabajo(
    clave: str,
    *,
    ttl: int | None = None,
    ttl_lock: int | None = None,
    cliente: ClienteRedis | None = None,
) -> Reclamo:
    """Intenta tomar el trabajo ``clave``.

    Devuelve ``Reclamo("nuevo")`` solo si esta llamada ganó el ``SET NX`` (hay que procesar).
    En cualquier otro caso informa el estado guardado, para que el consumidor decida sin
    volver a ejecutar la extracción.
    """
    c = _cliente(cliente)
    k = clave_trabajo(clave)
    marca = {"estado": ESTADO_EN_PROCESO, "actualizado": _ahora()}

    if await c.set(k, json.dumps(marca), ex=ttl_lock or TTL_LOCK, nx=True):
        return Reclamo(ESTADO_NUEVO)

    datos = await _leer(c, k)
    if datos is None:  # carrera: la clave expiró entre el SET NX y el GET
        return Reclamo(ESTADO_EN_PROCESO)

    estado = str(datos.get("estado", ESTADO_EN_PROCESO))
    resultado = datos.get("resultado") if estado == ESTADO_LISTO else None
    return Reclamo(estado, resultado if isinstance(resultado, list) else None)


async def guardar_resultado(
    clave: str,
    resultado: list[dict],
    *,
    ttl: int | None = None,
    cliente: ClienteRedis | None = None,
) -> None:
    """Guarda la respuesta definitiva del trabajo (la que se publica en RabbitMQ)."""
    c = _cliente(cliente)
    registro = {"estado": ESTADO_LISTO, "resultado": resultado, "actualizado": _ahora()}
    await _escribir(c, clave_trabajo(clave), registro, ttl or TTL_TRABAJO)


async def marcar_fallido(
    clave: str,
    error: str,
    *,
    ttl: int | None = None,
    cliente: ClienteRedis | None = None,
) -> None:
    """Deja constancia del fallo para no repetir indefinidamente el mismo documento."""
    c = _cliente(cliente)
    registro = {
        "estado": ESTADO_FALLIDO,
        "error": (error or "")[:500],
        "actualizado": _ahora(),
    }
    await _escribir(c, clave_trabajo(clave), registro, ttl or TTL_TRABAJO)


async def liberar_trabajo(clave: str, *, cliente: ClienteRedis | None = None) -> None:
    """Suelta el lock para permitir un reintento (se usa ante errores transitorios)."""
    await _cliente(cliente).delete(clave_trabajo(clave))


async def obtener_resultado(
    clave: str,
    *,
    cliente: ClienteRedis | None = None,
) -> list[dict] | None:
    """Respuesta cacheada del trabajo, o ``None`` si aún no está lista."""
    datos = await _leer(_cliente(cliente), clave_trabajo(clave))
    if not datos or datos.get("estado") != ESTADO_LISTO:
        return None
    resultado = datos.get("resultado")
    return resultado if isinstance(resultado, list) else None


# ---------------------------------------------------------------------------
# Compatibilidad: marcador simple usado por el Agente Inspector
# ---------------------------------------------------------------------------
async def intentar_marcar_procesado(
    clave: str,
    ttl: int = 86400,
    *,
    cliente: ClienteRedis | None = None,
) -> bool:
    """Marca la clave como procesada de forma atómica (``SET NX``).

    Retorna True si esta llamada fue la que marcó la clave (evento nuevo),
    False si ya estaba marcada (evento duplicado). Se mantiene para los consumidores que
    solo necesitan «procesado / no procesado», sin caché de respuesta.
    """
    return bool(
        await _cliente(cliente).set(f"procesado:{clave}", "1", ex=ttl, nx=True)
    )

