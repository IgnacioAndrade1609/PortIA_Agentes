"""Exporta los JSON Schema de las capas 2 y 3 al repositorio.

Los archivos generados se versionan para que el contrato sea auditable y para que el
``schema`` que se envía al LLM sea byte-a-byte el mismo que se revisa en el PR.

Uso (desde la raíz del repo)::

    python -m scripts.export_schemas
"""

import json
from pathlib import Path

from agente_lector.extraction.llm_schema import esquema_para_llm
from agente_lector.extraction.schemas import (
    BillOfLadingExtraido,
    DocumentoNormalizado,
)

RAIZ = Path(__file__).resolve().parents[1]
DESTINO = RAIZ / "agente_lector" / "extraction" / "schemas"

#: ``nombre de archivo`` → ``callable`` que devuelve el JSON Schema.
EXPORTABLES = {
    "documento_normalizado": lambda: DocumentoNormalizado.model_json_schema(),
    "bill_of_lading": lambda: BillOfLadingExtraido.model_json_schema(),
    "bill_of_lading.llm": esquema_para_llm,
}


def serializar(esquema: dict) -> str:
    """Serialización canónica (misma en el script y en los tests)."""
    return json.dumps(esquema, indent=2, ensure_ascii=False, sort_keys=True) + "\n"


def main() -> int:
    DESTINO.mkdir(parents=True, exist_ok=True)
    for nombre, generar in EXPORTABLES.items():
        ruta = DESTINO / f"{nombre}.schema.json"
        ruta.write_text(serializar(generar()), encoding="utf-8")
        print(f"escrito {ruta.relative_to(RAIZ)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
