"""Deriva el contrato que se envía al LLM a partir del esquema de dominio.

Problema
--------
``BillOfLadingExtraido.model_json_schema()`` tiene ~50 campos y cada uno es un objeto
``CampoExtraido`` (``value`` / ``confidence`` / ``evidence``). Eso infla el schema, encarece
el prompt y hace que el modelo rellene confianzas y evidencia **inventadas**.

Solución
--------
1. Aplanar cada ``CampoExtraido`` a ``string | null``. El LLM devuelve **solo valores**; la
   confianza y la evidencia las calcula el pipeline (grounding + validadores, Fases 6 y 7).
2. Inlinear los ``$ref`` restantes (``ParteComercial``, ``ContainerData``) para que el schema
   sea autocontenido: no todos los proveedores aceptan ``$defs``.
3. Endurecerlo al formato que exigen los *structured outputs* estrictos:
   ``additionalProperties: false`` y ``required`` con todas las propiedades (como todo es
   ``null``-able, el modelo puede devolver ``null`` sin violar el contrato).

Uso::

    from agente_lector.extraction.llm_schema import esquema_para_llm
    schema = esquema_para_llm()   # listo para response_format / responseSchema / tool
"""

from typing import Any

from agente_lector.extraction.schemas import BillOfLadingExtraido

__all__ = ["esquema_para_llm"]

#: Campos de metadata que el LLM no debe emitir (los pone el pipeline).
_CAMPOS_METADATA = ("schema_version", "warnings", "review_reasons")

_DESCRIPCION_VALOR = "Valor literal del documento; null si no aparece."

#: Instrucción global que acompaña al schema (regla de oro: no inventar).
_DESCRIPCION_RAIZ = (
    "Datos de un Bill of Lading (conocimiento de embarque). Devuelve null en todo campo que "
    "no aparezca literalmente en el documento. No infieras, no corrijas y no completes: otro "
    "componente normaliza y valida los valores. Devuelve un objeto por BL."
)


def _aplanar(nodo: Any) -> Any:
    """Reemplaza cada referencia a ``CampoExtraido`` por ``string | null``."""
    if isinstance(nodo, dict):
        referencia = nodo.get("$ref")
        if isinstance(referencia, str) and referencia.rsplit("/", 1)[-1] == "CampoExtraido":
            aplanado: dict[str, Any] = {"type": ["string", "null"], "description": _DESCRIPCION_VALOR}
            if "title" in nodo:
                aplanado["title"] = nodo["title"]
            return aplanado
        return {clave: _aplanar(valor) for clave, valor in nodo.items()}
    if isinstance(nodo, list):
        return [_aplanar(elemento) for elemento in nodo]
    return nodo


def _inlinear(nodo: Any, defs: dict) -> Any:
    """Sustituye los ``$ref`` por el contenido de la definición (sin recursión)."""
    if isinstance(nodo, dict):
        referencia = nodo.get("$ref")
        if isinstance(referencia, str) and referencia.startswith("#/$defs/"):
            nombre = referencia.rsplit("/", 1)[-1]
            resuelto = _inlinear(defs.get(nombre, {}), defs)
            hermanas = {clave: valor for clave, valor in nodo.items() if clave != "$ref"}
            return {**resuelto, **_inlinear(hermanas, defs)}
        return {clave: _inlinear(valor, defs) for clave, valor in nodo.items()}
    if isinstance(nodo, list):
        return [_inlinear(elemento, defs) for elemento in nodo]
    return nodo


def _endurecer(nodo: Any) -> Any:
    """Aplica el formato estricto de structured output a cada objeto del schema."""
    if isinstance(nodo, dict):
        endurecido = {clave: _endurecer(valor) for clave, valor in nodo.items()}
        propiedades = endurecido.get("properties")
        if isinstance(propiedades, dict):
            endurecido["additionalProperties"] = False
            endurecido["required"] = list(propiedades.keys())
        return endurecido
    if isinstance(nodo, list):
        return [_endurecer(elemento) for elemento in nodo]
    return nodo


def esquema_para_llm() -> dict:
    """JSON Schema plano y autocontenido del BL, listo para structured output."""
    plano = _aplanar(BillOfLadingExtraido.model_json_schema())
    defs = plano.get("$defs") or {}
    esquema = _inlinear({clave: valor for clave, valor in plano.items() if clave != "$defs"}, defs)

    propiedades = esquema.get("properties", {})
    for nombre in _CAMPOS_METADATA:
        propiedades.pop(nombre, None)
    esquema["properties"] = propiedades
    esquema["description"] = _DESCRIPCION_RAIZ

    return _endurecer(esquema)
