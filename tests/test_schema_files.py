"""Verifica que los JSON Schema versionados estén sincronizados con el código.

Si estos tests fallan, no se edita el JSON a mano: se ejecuta

    python -m scripts.export_schemas
"""

from pathlib import Path

import pytest

from agente_lector.extraction.llm_schema import esquema_para_llm
from agente_lector.extraction.schemas import BillOfLadingExtraido
from scripts.export_schemas import EXPORTABLES, serializar

DESTINO = Path(__file__).resolve().parents[1] / "agente_lector" / "extraction" / "schemas"
CAMPOS_METADATA = {"schema_version", "warnings", "review_reasons"}


def _recorrer(nodo):
    """Recorre el schema y devuelve todos los sub-dicts."""
    if isinstance(nodo, dict):
        yield nodo
        for valor in nodo.values():
            yield from _recorrer(valor)
    elif isinstance(nodo, list):
        for elemento in nodo:
            yield from _recorrer(elemento)


@pytest.mark.parametrize("nombre", sorted(EXPORTABLES))
def test_archivo_de_schema_sincronizado(nombre):
    ruta = DESTINO / f"{nombre}.schema.json"
    assert ruta.exists(), f"Falta {ruta.name}. Ejecuta: python -m scripts.export_schemas"
    assert ruta.read_text(encoding="utf-8") == serializar(EXPORTABLES[nombre]()), (
        f"{ruta.name} desactualizado. Ejecuta: python -m scripts.export_schemas"
    )


def test_schema_llm_es_autocontenido_y_sin_campo_extraido():
    """El LLM devuelve solo strings: no debe ver confidence/evidence ni referencias."""
    referencias = [nodo["$ref"] for nodo in _recorrer(esquema_para_llm()) if "$ref" in nodo]
    assert referencias == []


def test_schema_llm_no_expone_metadata():
    propiedades = set(esquema_para_llm()["properties"])
    assert propiedades & CAMPOS_METADATA == set()


def test_schema_llm_cubre_todos_los_campos_del_dominio():
    esperados = set(BillOfLadingExtraido.model_fields) - CAMPOS_METADATA
    assert set(esquema_para_llm()["properties"]) == esperados


def test_schema_llm_esta_endurecido():
    """Formato de structured output estricto: additionalProperties false y required completo."""
    objetos = [nodo for nodo in _recorrer(esquema_para_llm()) if "properties" in nodo]
    assert objetos, "el schema no tiene objetos"
    for objeto in objetos:
        assert objeto["additionalProperties"] is False
        assert objeto["required"] == list(objeto["properties"])


def test_schema_llm_containers_sigue_siendo_lista_de_objetos():
    containers = esquema_para_llm()["properties"]["containers"]
    assert containers["type"] == "array"
    assert set(containers["items"]["properties"]) == {"number", "iso_type", "seal"}


def test_schema_llm_campos_de_texto_son_string_o_null():
    """Ningún campo del BL queda como objeto anidado salvo las partes y los contenedores."""
    anidados = {"shipper", "consignee", "notify_party", "also_notify", "containers"}
    propiedades = esquema_para_llm()["properties"]
    for nombre, definicion in propiedades.items():
        if nombre in anidados:
            continue
        assert definicion["type"] == ["string", "null"], nombre
