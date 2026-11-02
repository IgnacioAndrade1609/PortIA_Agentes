"""Tests del extractor LLM (OpenRouter). Ninguna llamada real: ``_post`` se simula."""

import asyncio
import json

import dotenv
import pytest

from agente_lector.extraction.llm_extractor import (
    RUTA_PROMPT,
    UMBRAL_GROUNDING,
    VERSION_PROMPT,
    ConfigLLM,
    ErrorLLM,
    OpenRouterClient,
    aplicar_grounding,
    buscar_en_texto,
    cargar_prompt,
    construir_bl,
    es_extraccion_vacia,
    extraer_bl,
    extraer_bl_async,
)
from agente_lector.extraction.schemas import BillOfLadingExtraido, ReviewReason

TEXTO_BL = """# BILL OF LADING

#BL 140021277 · FOLIO 140021277
Embarcador: SEPAL INVERSIONES GLOBALES SL
Consignatario: MALL URUBO LOCAL 27
Notificar: MALL URUBO LOCAL 27
Puerto de embarque: VALENCIA (ESVLC)
Puerto de descarga: SANTA CRUZ (BOSCS)
Naviera: MSC AINO / servicio LINER
Contenedor: TGCU 512907.3 - 45G0 - SELLO 000574
Peso bruto: 25541.25 KGM
Bultos: 78 CONTENEDOR NO ESPECIFICADO
"""


def _config(**cambios) -> ConfigLLM:
    base = {"api_key": "clave-de-prueba", "modelo": "google/gemma-4-31b-it:free", "intentos": 1}
    base.update(cambios)
    return ConfigLLM(**base)


def _respuesta(datos: dict) -> dict:
    return {"choices": [{"message": {"content": json.dumps(datos)}}], "usage": {"prompt_tokens": 10}}


# ---------------------------------------------------------------------------
# Payload: debe seguir el formato documentado por OpenRouter
# ---------------------------------------------------------------------------
def test_payload_sigue_el_formato_documentado():
    config = _config(
        razonamiento=True,
        proveedor_solo=("google-ai-studio",),
        permitir_fallbacks=False,
    )
    payload = OpenRouterClient(config).construir_payload("# BL\nTGCU5129073")

    assert payload["model"] == "google/gemma-4-31b-it:free"
    assert payload["messages"][0]["role"] == "system"
    assert payload["messages"][1] == {"role": "user", "content": "# BL\nTGCU5129073"}
    assert payload["reasoning"] == {"enabled": True}
    assert payload["provider"] == {"only": ["google-ai-studio"], "allow_fallbacks": False}
    assert payload["temperature"] == 0.0


def test_payload_incluye_schema_estricto():
    payload = OpenRouterClient(_config()).construir_payload("texto")
    formato = payload["response_format"]
    assert formato["type"] == "json_schema"
    assert formato["json_schema"]["name"] == "bill_of_lading"
    assert formato["json_schema"]["strict"] is True
    assert "bl_number" in formato["json_schema"]["schema"]["properties"]


def test_payload_omite_provider_si_no_se_define():
    payload = OpenRouterClient(_config()).construir_payload("texto")
    assert "provider" not in payload
    assert "reasoning" not in payload


@pytest.mark.parametrize(("modo", "esperado"), [("ninguno", None), ("json_object", "json_object")])
def test_modo_formato_configurable(modo, esperado):
    payload = OpenRouterClient(_config(modo_formato=modo)).construir_payload("texto")
    if esperado is None:
        assert "response_format" not in payload
    else:
        assert payload["response_format"] == {"type": esperado}


def test_cabeceras_llevan_bearer_y_atribucion():
    cliente = OpenRouterClient(_config(referer="https://portia.local", titulo="PortIA"))
    cabeceras = cliente._cabeceras()
    assert cabeceras["Authorization"] == "Bearer clave-de-prueba"
    assert cabeceras["Content-Type"] == "application/json"
    assert cabeceras["HTTP-Referer"] == "https://portia.local"
    assert cabeceras["X-Title"] == "PortIA"
    assert cliente.config.url_completions == "https://openrouter.ai/api/v1/chat/completions"


# ---------------------------------------------------------------------------
# Reintentos, degradación de formato y modelo de respaldo
# ---------------------------------------------------------------------------
def test_degrada_response_format_si_el_proveedor_no_lo_soporta():
    cliente = OpenRouterClient(_config(modo_formato="auto", intentos=2))
    modos: list[str | None] = []

    def falso_post(payload):
        modos.append((payload.get("response_format") or {}).get("type"))
        if len(modos) == 1:
            raise ErrorLLM("400", transitorio=False, formato_no_soportado=True)
        return _respuesta({"bl_number": "140021277"})

    cliente._post = falso_post
    assert cliente.extraer("texto") == {"bl_number": "140021277"}
    assert modos == ["json_schema", "json_object"]


def test_reintenta_en_error_transitorio_con_backoff():
    esperas: list[float] = []
    cliente = OpenRouterClient(_config(intentos=3), dormir=esperas.append)
    llamadas: list[int] = []

    def falso_post(payload):
        llamadas.append(1)
        if len(llamadas) < 3:
            raise ErrorLLM("429", transitorio=True)
        return _respuesta({"bl_number": "140021277"})

    cliente._post = falso_post
    assert cliente.extraer("texto") == {"bl_number": "140021277"}
    assert len(llamadas) == 3
    assert esperas == [1, 2]


def test_error_permanente_no_se_reintenta():
    cliente = OpenRouterClient(_config(intentos=3))
    llamadas: list[int] = []

    def falso_post(payload):
        llamadas.append(1)
        raise ErrorLLM("401", transitorio=False)

    cliente._post = falso_post
    with pytest.raises(ErrorLLM):
        cliente.extraer("texto")
    assert len(llamadas) == 1


def test_usa_el_modelo_de_respaldo():
    config = _config(modelo="modelo/principal", modelos_respaldo=("modelo/respaldo",), intentos=1)
    cliente = OpenRouterClient(config)
    modelos: list[str] = []

    def falso_post(payload):
        modelos.append(payload["model"])
        if payload["model"] == "modelo/principal":
            raise ErrorLLM("404", transitorio=False)
        return _respuesta({"bl_number": "140021277"})

    cliente._post = falso_post
    assert cliente.extraer("texto") == {"bl_number": "140021277"}
    assert modelos == ["modelo/principal", "modelo/respaldo"]


def test_documento_vacio_no_llama_a_la_api():
    cliente = OpenRouterClient(_config())
    cliente._post = lambda payload: pytest.fail("no debería llamarse")

    with pytest.raises(ErrorLLM):
        cliente.extraer("   \n  ")


# ---------------------------------------------------------------------------
# Parseo de la respuesta
# ---------------------------------------------------------------------------
def test_parsea_json_envuelto_en_cercas():
    cliente = OpenRouterClient(_config())
    cliente._post = lambda payload: {
        "choices": [{"message": {"content": '```json\n{"bl_number": "140021277"}\n```'}}]
    }
    assert cliente.extraer("texto") == {"bl_number": "140021277"}


def test_parsea_json_envuelto_en_prosa():
    """Los modelos chicos contestan "Aquí tienes el JSON: {...} Espero que sirva"."""
    cliente = OpenRouterClient(_config())
    cliente._post = lambda payload: {
        "choices": [
            {
                "message": {
                    "content": 'Aquí tienes el JSON:\n{"bl_number": "140021277"}\nEspero que sirva.'
                }
            }
        ]
    }
    assert cliente.extraer("texto") == {"bl_number": "140021277"}


def test_parsea_contenido_por_bloques():
    cliente = OpenRouterClient(_config())
    cliente._post = lambda payload: {
        "choices": [{"message": {"content": [{"type": "text", "text": '{"bl_number": "1"}'}]}}]
    }
    assert cliente.extraer("texto") == {"bl_number": "1"}


@pytest.mark.parametrize(
    "respuesta",
    [
        {},
        {"choices": []},
        {"choices": [{"message": {"content": ""}}]},
        {"choices": [{"message": {"content": "no soy json"}}]},
        {"choices": [{"message": {"content": "[1, 2]"}}]},
    ],
)
def test_respuestas_invalidas_terminan_en_error(respuesta):
    cliente = OpenRouterClient(_config(intentos=1))
    cliente._post = lambda payload: respuesta
    with pytest.raises(ErrorLLM):
        cliente.extraer("texto")


# ---------------------------------------------------------------------------
# Grounding
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("valor", "texto", "exacto", "minimo"),
    [
        ("140021277", TEXTO_BL, True, 1.0),
        ("SANTA CRUZ", "puerto de descarga: santa  cruz", True, 1.0),
        ("MALL URUBO LOCAL 27", "MALL URUBO LOCAL 2", False, UMBRAL_GROUNDING),
        ("INVENTADO123", TEXTO_BL, False, 0.0),
    ],
)
def test_buscar_en_texto(valor, texto, exacto, minimo):
    puntaje, _fragmento, es_exacto = buscar_en_texto(valor, texto)
    assert es_exacto is exacto
    if exacto:
        assert puntaje == 1.0
    elif minimo == 0.0:
        assert puntaje < UMBRAL_GROUNDING
    else:
        assert puntaje >= minimo


def test_construir_bl_marca_evidencia_de_campos_respaldados():
    bl = construir_bl({"bl_number": "140021277", "port_of_discharge": "SANTA CRUZ"}, TEXTO_BL)
    assert bl.bl_number.value == "140021277"
    assert bl.bl_number.confidence == 0.9
    assert bl.bl_number.evidence[0].text == "140021277"
    assert bl.port_of_discharge.confidence == 0.9
    assert bl.review_reasons == []


def test_valor_inventado_queda_sin_confianza_y_en_revision():
    bl = construir_bl({"bl_number": "999999999", "eta": "2021-12-01"}, TEXTO_BL)
    assert bl.bl_number.value == "999999999"  # se conserva para el humano
    assert bl.bl_number.confidence == 0.0
    assert bl.bl_number.evidence == []
    assert ReviewReason.GROUNDING_FAILED in bl.review_reasons


def test_contenedor_inventado_se_detecta():
    bl = construir_bl({"containers": [{"number": "MSCU1234565", "iso_type": "45G0"}]}, TEXTO_BL)
    assert bl.containers[0].number.confidence == 0.0
    assert bl.containers[0].iso_type.confidence > 0  # 45G0 sí está en el texto
    assert ReviewReason.GROUNDING_FAILED in bl.review_reasons


def test_campos_ausentes_no_generan_revision():
    bl = construir_bl({"bl_number": None, "eta": None, "containers": []}, TEXTO_BL)
    assert bl.review_reasons == []
    assert es_extraccion_vacia(bl)


def test_numeros_y_listas_se_convierten_a_string():
    bl = construir_bl({"gross_weight": 25541.25, "packages_count": 78, "observations": ["a", "b"]}, "25541.25 78 a b")
    assert bl.gross_weight.value == "25541.25"
    assert bl.packages_count.value == "78"
    assert bl.observations.value == "a b"


def test_partes_y_contenedores_se_mapean():
    datos = {
        "shipper": {"nombre": "SEPAL INVERSIONES GLOBALES SL", "identificador": "76.123.456-7"},
        "consignee": {"nombre": "MALL URUBO LOCAL 27"},
        "containers": [
            {"number": "TGCU 512907.3", "iso_type": "45G0", "seal": "000574"},
            {"iso_type": None, "number": None, "seal": None},
        ],
    }
    bl = construir_bl(datos, TEXTO_BL + "\nRUT 76.123.456-7")
    assert bl.shipper.nombre.value == "SEPAL INVERSIONES GLOBALES SL"
    assert bl.consignee.nombre.value == "MALL URUBO LOCAL 27"
    assert len(bl.containers) == 1  # el contenedor vacío se descarta
    assert bl.containers[0].seal.value == "000574"


def test_aplicar_grounding_es_idempotente():
    bl = BillOfLadingExtraido()
    aplicar_grounding(bl, TEXTO_BL)
    assert bl.review_reasons == []

    datos = {"bl_number": "140021277", "eta": "1999-01-01"}
    bl = construir_bl(datos, TEXTO_BL)
    aplicar_grounding(bl, TEXTO_BL)
    assert bl.review_reasons.count(ReviewReason.GROUNDING_FAILED) == 1


def test_resultado_no_es_un_objeto_json():
    with pytest.raises(ErrorLLM):
        construir_bl(["no", "es", "un", "dict"], TEXTO_BL)


# ---------------------------------------------------------------------------
# Integración con el cliente y prompt
# ---------------------------------------------------------------------------
def test_extraer_bl_completo():
    cliente = OpenRouterClient(_config())
    cliente._post = lambda payload: _respuesta(
        {
            "document_type": "BL",
            "bl_number": "140021277",
            "shipping_line": "MSC",
            "port_of_discharge": "SANTA CRUZ",
            "containers": [{"number": "TGCU 512907.3", "iso_type": "45G0", "seal": "000574"}],
        }
    )
    bl = extraer_bl(TEXTO_BL, cliente)
    assert bl.bl_number.value == "140021277"
    assert bl.containers[0].seal.value == "000574"
    assert bl.review_reasons == []


def test_extraer_bl_async_no_bloquea():
    cliente = OpenRouterClient(_config())
    cliente._post = lambda payload: _respuesta({"bl_number": "140021277"})
    bl = asyncio.run(extraer_bl_async(TEXTO_BL, cliente))
    assert bl.bl_number.value == "140021277"


def test_prompt_versionado_existe():
    assert RUTA_PROMPT.exists()
    assert VERSION_PROMPT == "bl_v1"
    prompt = cargar_prompt()
    assert "Bill of Lading" in prompt
    assert "null" in prompt
    assert "confidence" in prompt  # le decimos explícitamente que no lo emita


def test_config_desde_entorno(monkeypatch):
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *a, **k: None)
    monkeypatch.setenv("LLM_API_KEY", "clave")
    monkeypatch.setenv("LLM_MODEL", "google/gemma-4-31b-it:free")
    monkeypatch.setenv("LLM_MODEL_FALLBACK", "otro/modelo, tercero/modelo")
    monkeypatch.setenv("LLM_PROVIDER_ONLY", "google-ai-studio")
    monkeypatch.setenv("LLM_ALLOW_FALLBACKS", "true")
    monkeypatch.setenv("LLM_REASONING", "true")
    monkeypatch.setenv("LLM_MAX_RETRIES", "5")

    config = ConfigLLM.desde_entorno()
    assert config.api_key == "clave"
    assert config.modelos_respaldo == ("otro/modelo", "tercero/modelo")
    assert config.proveedor_solo == ("google-ai-studio",)
    assert config.permitir_fallbacks is True
    assert config.razonamiento is True
    assert config.intentos == 5


def test_config_sin_api_key_falla_claro(monkeypatch):
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *a, **k: None)
    for variable in ("LLM_API_KEY", "OPENROUTER_API_KEY", "API_KEY"):
        monkeypatch.delenv(variable, raising=False)
    with pytest.raises(ErrorLLM) as error:
        ConfigLLM.desde_entorno()
    assert "API key" in str(error.value)
