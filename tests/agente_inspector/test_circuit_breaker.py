from agente_inspector import circuit_breaker as circuit_breaker_module
from agente_inspector.circuit_breaker import CircuitBreaker


def test_circuito_cerrado_al_inicio():
    cb = CircuitBreaker(fallas_para_abrir=3, ventana_recuperacion_segundos=60)
    assert cb.esta_abierto("STI") is False


def test_se_abre_tras_n_fallas_consecutivas():
    cb = CircuitBreaker(fallas_para_abrir=3, ventana_recuperacion_segundos=60)

    cb.registrar_falla("STI")
    cb.registrar_falla("STI")
    assert cb.esta_abierto("STI") is False

    cb.registrar_falla("STI")
    assert cb.esta_abierto("STI") is True


def test_un_exito_resetea_el_contador_de_fallas():
    cb = CircuitBreaker(fallas_para_abrir=3, ventana_recuperacion_segundos=60)

    cb.registrar_falla("STI")
    cb.registrar_falla("STI")
    cb.registrar_exito("STI")
    cb.registrar_falla("STI")

    assert cb.esta_abierto("STI") is False


def test_terminales_son_independientes():
    cb = CircuitBreaker(fallas_para_abrir=3, ventana_recuperacion_segundos=60)

    cb.registrar_falla("STI")
    cb.registrar_falla("STI")
    cb.registrar_falla("STI")

    assert cb.esta_abierto("STI") is True
    assert cb.esta_abierto("TPS") is False


def test_recupera_a_half_open_tras_la_ventana(monkeypatch):
    tiempo_actual = [1000.0]
    monkeypatch.setattr(circuit_breaker_module.time, "monotonic", lambda: tiempo_actual[0])

    cb = CircuitBreaker(fallas_para_abrir=1, ventana_recuperacion_segundos=60)

    cb.registrar_falla("STI")
    assert cb.esta_abierto("STI") is True

    tiempo_actual[0] += 60
    assert cb.esta_abierto("STI") is False
