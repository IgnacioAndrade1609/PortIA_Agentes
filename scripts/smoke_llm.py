"""Diagnóstico del LLM: verifica credenciales, modelo y formato sin exponer datos reales.

Uso::

    python -m scripts.smoke_llm                 # texto sintético, no imprime valores
    python -m scripts.smoke_llm --mostrar-valores

Por defecto **no** imprime los valores extraídos (pueden ser PII): solo los nombres de los
campos que el modelo devolvió con contenido.
"""

import argparse
import time
from dataclasses import replace

import requests

from agente_lector.extraction.llm_extractor import (
    ConfigLLM,
    ErrorLLM,
    OpenRouterClient,
    construir_bl,
)
from agente_lector.extraction.schemas import BillOfLadingExtraido

#: BL ficticio: ningún dato corresponde a un documento real.
TEXTO_SINTETICO = """# BILL OF LADING

#BL 140021277 · FOLIO 140021277
Embarcador: ACME EXPORTS SPA · RUT 76.543.210-8
Consignatario: IMPORTADORA DEMO LTDA · RUT 77.111.222-3
Notificar: IMPORTADORA DEMO LTDA
Naviera: MSC / Buque MSC AINO · Viaje 123E
Puerto de embarque: VALENCIA (ESVLC)
Puerto de descarga: SANTA CRUZ (BOSCS)
Contenedor: TGCU 512907.3 - 45G0 - SELLO 000574
Bultos: 78 CONTENEDOR NO ESPECIFICADO
Peso bruto: 25541.25 KGM
Volumen: 0.00 MTQ
Flete: PREPAID

FOLIO 140021277 · CODIGO DE VERIFICACION A1B2C3D4
"""


def listar_modelos_gratuitos(base_url: str) -> int:
    """Muestra los modelos ``:free`` disponibles hoy (el catálogo cambia seguido)."""
    try:
        respuesta = requests.get(f"{base_url.rstrip('/')}/models", timeout=30)
        respuesta.raise_for_status()
    except requests.RequestException as error:
        print(f"[FALLO] no se pudo listar modelos: {error}")
        return 1

    modelos = respuesta.json().get("data", [])
    libres = [modelo for modelo in modelos if str(modelo.get("id", "")).endswith(":free")]
    print(f"modelos disponibles: {len(modelos)} · gratuitos: {len(libres)}")
    for modelo in libres:
        contexto = modelo.get("context_length")
        print(f"  {modelo['id']:<60} ctx={contexto}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mostrar-valores", action="store_true", help="imprime los valores extraídos")
    parser.add_argument("--listar-modelos", action="store_true", help="lista los modelos gratuitos")
    parser.add_argument("--modelo", default=None, help="sobrescribe LLM_MODEL para esta prueba")
    argumentos = parser.parse_args()

    try:
        config = ConfigLLM.desde_entorno()
    except ErrorLLM as error:
        print(f"[FALLO] configuración: {error}")
        return 2

    if argumentos.listar_modelos:
        return listar_modelos_gratuitos(config.base_url)

    if argumentos.modelo:
        config = replace(config, modelo=argumentos.modelo)

    print(f"base_url: {config.base_url}")
    print(f"modelo:   {config.modelo}")
    print(f"formato:  {config.modo_formato} | timeout: {config.timeout}s")

    cliente = OpenRouterClient(config)
    inicio = time.perf_counter()
    try:
        datos = cliente.extraer(TEXTO_SINTETICO)
    except ErrorLLM as error:
        print(f"[FALLO] {error} (transitorio={error.transitorio})")
        return 1

    ms = int((time.perf_counter() - inicio) * 1000)
    bl: BillOfLadingExtraido = construir_bl(datos, TEXTO_SINTETICO)

    campos = {nombre: campo for nombre, campo in datos.items() if campo not in (None, "", [], {})}
    print(f"[OK] {ms} ms · {len(campos)} campos con valor")
    print("campos:", ", ".join(sorted(campos)))
    if argumentos.mostrar_valores:
        for nombre in sorted(campos):
            print(f"  {nombre} = {campos[nombre]}")
    if bl.review_reasons:
        print("revisión:", ", ".join(motivo.value for motivo in bl.review_reasons))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
