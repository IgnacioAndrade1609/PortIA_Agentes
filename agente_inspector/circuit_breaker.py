from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import asdict, dataclass

import redis.asyncio as redis
from dotenv import load_dotenv

from shared.idempotency import ClienteRedis

load_dotenv()

logger = logging.getLogger("portia.agente_inspector")

FALLAS_PARA_ABRIR_DEFAULT = 3
VENTANA_RECUPERACION_SEGUNDOS_DEFAULT = 10 * 60
PREFIJO_CIRCUIT_BREAKER = "circuit_breaker:"
TTL_REGISTRO_SEGUNDOS = 24 * 60 * 60

r = redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379"))


@dataclass
class _EstadoTerminal:
    fallas_consecutivas: int = 0
    abierto_desde: float | None = None


class CircuitBreaker:
    def __init__(
        self,
        fallas_para_abrir: int = FALLAS_PARA_ABRIR_DEFAULT,
        ventana_recuperacion_segundos: int = VENTANA_RECUPERACION_SEGUNDOS_DEFAULT,
        cliente: ClienteRedis | None = None,
    ) -> None:
        self._fallas_para_abrir = fallas_para_abrir
        self._ventana_recuperacion_segundos = ventana_recuperacion_segundos
        self._cliente = cliente if cliente is not None else r

    def _clave(self, terminal_code: str) -> str:
        return f"{PREFIJO_CIRCUIT_BREAKER}{terminal_code}"

    async def _estado_de(self, terminal_code: str) -> _EstadoTerminal:
        crudo = await self._cliente.get(self._clave(terminal_code))
        if crudo is None:
            return _EstadoTerminal()

        if isinstance(crudo, (bytes, bytearray)):
            crudo = crudo.decode("utf-8")

        try:
            datos = json.loads(crudo)
        except (TypeError, ValueError):
            logger.warning(f"Registro de circuit breaker corrupto para terminal={terminal_code}; se ignora")
            return _EstadoTerminal()

        return _EstadoTerminal(
            fallas_consecutivas=int(datos.get("fallas_consecutivas", 0)),
            abierto_desde=datos.get("abierto_desde"),
        )

    async def _guardar(self, terminal_code: str, estado: _EstadoTerminal) -> None:
        await self._cliente.set(self._clave(terminal_code), json.dumps(asdict(estado)), ex=TTL_REGISTRO_SEGUNDOS)

    async def esta_abierto(self, terminal_code: str) -> bool:
        estado = await self._estado_de(terminal_code)
        if estado.abierto_desde is None:
            return False

        if time.time() - estado.abierto_desde >= self._ventana_recuperacion_segundos:
            logger.info(f"Circuit breaker en half-open para terminal={terminal_code}")
            return False

        return True

    async def registrar_exito(self, terminal_code: str) -> None:
        estado = await self._estado_de(terminal_code)
        if estado.abierto_desde is not None:
            logger.info(f"Circuit breaker recuperado para terminal={terminal_code}")

        await self._guardar(terminal_code, _EstadoTerminal())

    async def registrar_falla(self, terminal_code: str) -> None:
        estado = await self._estado_de(terminal_code)
        estado.fallas_consecutivas += 1

        if estado.fallas_consecutivas >= self._fallas_para_abrir:
            if estado.abierto_desde is None:
                minutos_reintento = self._ventana_recuperacion_segundos // 60
                logger.warning(
                    f"Circuit breaker abierto para terminal={terminal_code} "
                    f"tras {estado.fallas_consecutivas} fallas consecutivas; "
                    f"próximo reintento en {minutos_reintento} min"
                )
            estado.abierto_desde = time.time()

        await self._guardar(terminal_code, estado)
