import logging
import time
from dataclasses import dataclass

logger = logging.getLogger("portia.agente_inspector")

FALLAS_PARA_ABRIR_DEFAULT = 3
VENTANA_RECUPERACION_SEGUNDOS_DEFAULT = 10 * 60


@dataclass
class _EstadoTerminal:
    fallas_consecutivas: int = 0
    abierto_desde: float | None = None


class CircuitBreaker:
    def __init__(
        self,
        fallas_para_abrir: int = FALLAS_PARA_ABRIR_DEFAULT,
        ventana_recuperacion_segundos: int = VENTANA_RECUPERACION_SEGUNDOS_DEFAULT,
    ) -> None:
        self._fallas_para_abrir = fallas_para_abrir
        self._ventana_recuperacion_segundos = ventana_recuperacion_segundos
        self._estados: dict[str, _EstadoTerminal] = {}

    def _estado_de(self, terminal_code: str) -> _EstadoTerminal:
        return self._estados.setdefault(terminal_code, _EstadoTerminal())

    def esta_abierto(self, terminal_code: str) -> bool:
        estado = self._estado_de(terminal_code)
        if estado.abierto_desde is None:
            return False

        if time.monotonic() - estado.abierto_desde >= self._ventana_recuperacion_segundos:
            logger.info(f"Circuit breaker en half-open para terminal={terminal_code}")
            return False

        return True

    def registrar_exito(self, terminal_code: str) -> None:
        estado = self._estado_de(terminal_code)
        if estado.abierto_desde is not None:
            logger.info(f"Circuit breaker recuperado para terminal={terminal_code}")
        estado.fallas_consecutivas = 0
        estado.abierto_desde = None

    def registrar_falla(self, terminal_code: str) -> None:
        estado = self._estado_de(terminal_code)
        estado.fallas_consecutivas += 1

        if estado.fallas_consecutivas >= self._fallas_para_abrir:
            if estado.abierto_desde is None:
                logger.warning(
                    f"Circuit breaker abierto para terminal={terminal_code} "
                    f"tras {estado.fallas_consecutivas} fallas consecutivas"
                )
            estado.abierto_desde = time.monotonic()
