"""Demo de extracción (2 PDF + 2 imágenes) con reporte de cumplimiento del esquema.

Corre el pipeline **real** de punta a punta — texto nativo o Docling+OCR → LLM →
grounding con evidencia citada → gate de campos clave → mapeo a eventos — y deja un JSON
por artefacto en ``output/``.

Uso (desde la raíz del repo)::

    python -m scripts.demo_extraccion
    python -m scripts.demo_extraccion --pdf temp/pdf/bl_document_5.pdf --imagen temp/drivea/6.webp
    python -m scripts.demo_extraccion --salida output_prueba

Requiere ``LLM_API_KEY`` (o ``OPENROUTER_API_KEY`` / ``API_KEY``) en el entorno o en ``.env``.

Estructura de salida (un solo archivo por documento)::

    output/
      01_pdf_bl_document_1/
        respuesta_llm.json   # respuesta cruda del modelo (la última, tras el reintento)
      02_pdf_bl_document_2/
        respuesta_llm.json
      03_imagen_2/
        respuesta_llm.json

El cumplimiento del esquema y del contrato se evalua igual, pero solo se informa por consola.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path
from typing import Any

from agente_lector.extraction.llm_extractor import (
    OpenRouterClient,
    _campo_por_clave,
    extraer_bl_async,
    verificar_contrato,
)
from agente_lector.extraction.llm_schema import CAMPOS_CLAVE, esquema_para_llm
from agente_lector.extraction.mapping import a_eventos
from agente_lector.pipeline import (
    SUFIJOS_IMAGEN,
    UMBRAL_TEXTO_NATIVO,
    _estado_extraccion,
    _texto_nativo_pdf,
    texto_documento,
)

RAIZ = Path(__file__).resolve().parents[1]
SALIDA_POR_DEFECTO = RAIZ / "output"

PDFS_POR_DEFECTO = ("temp/pdf/bl_document_1.pdf", "temp/pdf/bl_document_2.pdf")
IMAGENES_POR_DEFECTO = ("temp/drivea/2.png", "temp/drivea/4.jpg")


class ClienteEspia:
    """Envuelve el cliente real y guarda cada respuesta cruda del LLM.

    Permite ver el JSON del modelo sin duplicar la lógica de ``extraer_bl_async`` (que
    incluye verificación del contrato y reintento dirigido).
    """

    def __init__(self, cliente: Any) -> None:
        self._cliente = cliente
        self.modelo = cliente.modelo
        self.respuestas: list[dict] = []

    def extraer(self, markdown: str) -> dict:
        datos = self._cliente.extraer(markdown)
        self.respuestas.append(datos)
        return datos


# ---------------------------------------------------------------------------
# Revisión del esquema
# ---------------------------------------------------------------------------
def _revisar_forma(respuesta: dict, esquema: dict) -> dict:
    """Comprueba que la respuesta cruda respete la forma del schema estricto."""
    propiedades = set(esquema["properties"])
    requeridas = set(esquema.get("required") or [])
    claves = set(respuesta)

    evidencia = respuesta.get("evidencia")
    evidencia_esquema = esquema["properties"]["evidencia"]
    evidencia_ok = isinstance(evidencia, dict)
    faltan_evidencia: list[str] = []
    extra_evidencia: list[str] = []
    if evidencia_ok:
        faltan_evidencia = sorted(set(evidencia_esquema["required"]) - set(evidencia))
        extra_evidencia = sorted(set(evidencia) - set(evidencia_esquema["properties"]))

    faltan = sorted(requeridas - claves)
    extra = sorted(claves - propiedades)
    return {
        "cumple": not faltan and not extra and evidencia_ok and not faltan_evidencia and not extra_evidencia,
        "propiedades_faltantes": faltan,
        "propiedades_extra": extra,
        "evidencia_presente": evidencia_ok,
        "evidencia_faltante": faltan_evidencia,
        "evidencia_extra": extra_evidencia,
    }


def _revisar_campos_clave(bl) -> list[dict]:
    """Estado de cada campo clave: con valor respaldado, o declarado ausente."""
    declarados = set(bl.campos_no_encontrados)
    filas: list[dict] = []
    for clave in CAMPOS_CLAVE:
        campo = _campo_por_clave(bl, clave)
        valor = (campo.value or "").strip() if campo else ""
        if valor:
            estado = "ok" if campo.evidence and campo.confidence >= 0.9 else "sin_respaldo"
        else:
            estado = "declarado_ausente" if clave in declarados else "sin_declarar"
        filas.append(
            {
                "campo": clave,
                "estado": estado,
                "valor": valor or None,
                "confidence": campo.confidence if campo else 0.0,
                "cita": campo.evidence[0].text if campo and campo.evidence else None,
            }
        )
    return filas


def _metodo_lectura(datos: bytes, ruta: Path) -> str:
    """Cómo se obtuvo el texto: capa nativa del PDF o Docling+OCR."""
    if ruta.suffix.lower() in SUFIJOS_IMAGEN:
        return "docling_ocr"
    paginas, nativo = _texto_nativo_pdf(datos)
    umbral = UMBRAL_TEXTO_NATIVO * max(1, paginas)
    return "texto_nativo" if len(nativo) >= umbral else "docling_ocr"


# ---------------------------------------------------------------------------
# Procesamiento de un documento
# ---------------------------------------------------------------------------
def _escribir(ruta: Path, contenido: dict | str) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(contenido, str):
        ruta.write_text(contenido, encoding="utf-8")
        return
    ruta.write_text(json.dumps(contenido, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


async def procesar(ruta: Path, indice: int, salida: Path) -> dict:
    """Extrae un documento y escribe sus cuatro JSON. Nunca lanza: devuelve el resumen."""
    nombre = f"{indice:02d}_{'imagen' if ruta.suffix.lower() in SUFIJOS_IMAGEN else 'pdf'}_{ruta.stem}"
    destino = salida / nombre
    base = {
        "orden": indice,
        "documento": ruta.name,
        "ruta": str(ruta.relative_to(RAIZ)),
        "tipo": "imagen" if ruta.suffix.lower() in SUFIJOS_IMAGEN else "pdf",
    }
    inicio = time.perf_counter()

    try:
        datos = ruta.read_bytes()
        texto = await asyncio.to_thread(texto_documento, datos, ruta.name)
        metodo = _metodo_lectura(datos, ruta)
        if not texto.strip():
            raise ValueError("el documento no tiene texto extraíble")

        espia = ClienteEspia(OpenRouterClient())
        bl = await extraer_bl_async(texto, espia)
        errores = verificar_contrato(bl)
        estado = _estado_extraccion(bl)
        eventos = a_eventos(
            bl,
            shipment_id=9000 + indice,
            document_id=9100 + indice,
            document_type="BL",
            extraction_status=estado,
        )
    except Exception as exc:  # noqa: BLE001 - la demo reporta, no revienta
        return {
            **base,
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "ms": int((time.perf_counter() - inicio) * 1000),
        }

    despliegue_eventos = [evento.model_dump(mode="json") for evento in eventos]
    campos = _revisar_campos_clave(bl)
    forma = _revisar_forma(espia.respuestas[-1], esquema_para_llm()) if espia.respuestas else {
        "cumple": False,
        "propiedades_faltantes": ["(sin respuesta)"],
        "propiedades_extra": [],
        "evidencia_presente": False,
        "evidencia_faltante": [],
        "evidencia_extra": [],
    }

    # Único artefacto en disco: la respuesta cruda del modelo.
    _escribir(destino / "respuesta_llm.json", espia.respuestas[-1] if espia.respuestas else {})

    return {
        **base,
        "ok": True,
        "metodo_lectura": metodo,
        "caracteres_texto": len(texto),
        "llamadas_llm": len(espia.respuestas),
        "estado": estado.value,
        "cumple_esquema": forma["cumple"],
        "cumple_contrato": not errores,
        "errores_contrato": len(errores),
        "campos": _contar_estados(campos),
        "eventos": len(despliegue_eventos),
        "contenedores": [evento["container_number"] for evento in despliegue_eventos],
        "confidence": despliegue_eventos[0]["confidence"] if despliegue_eventos else 0.0,
        "ms": int((time.perf_counter() - inicio) * 1000),
    }


def _contar_estados(campos: list[dict]) -> dict:
    conteo = {"ok": 0, "declarado_ausente": 0, "sin_respaldo": 0, "sin_declarar": 0}
    for fila in campos:
        conteo[fila["estado"]] += 1
    conteo["total"] = len(campos)
    return conteo


# ---------------------------------------------------------------------------
# Informe agregado
# ---------------------------------------------------------------------------
def _tabla_md(resumenes: list[dict]) -> str:
    cabecera = (
        "| # | Documento | Tipo | Lectura | chars | LLM | Estado | Esquema | Contrato "
        "| Clave ok | Ausentes | Sin respaldo | Contenedores |\n"
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|\n"
    )
    filas = []
    for r in resumenes:
        if not r.get("ok"):
            filas.append(
                f"| {r['orden']} | `{r['documento']}` | {r['tipo']} | — | — | — | "
                f"**ERROR** | — | — | — | — | — | `{r.get('error', '')}` |"
            )
            continue
        c = r["campos"]
        filas.append(
            f"| {r['orden']} | `{r['documento']}` | {r['tipo']} | {r['metodo_lectura']} "
            f"| {r['caracteres_texto']} | {r['llamadas_llm']} | {r['estado']} "
            f"| {'sí' if r['cumple_esquema'] else 'NO'} "
            f"| {'sí' if r['cumple_contrato'] else 'NO'} "
            f"| {c['ok']}/{c['total']} | {c['declarado_ausente']} | {c['sin_respaldo']} "
            f"| {', '.join(r['contenedores']) or '—'} |"
        )
    return cabecera + "\n".join(filas) + "\n"


async def ejecutar(entradas: list[Path], salida: Path) -> list[dict]:
    salida.mkdir(parents=True, exist_ok=True)
    resumenes: list[dict] = []

    for indice, ruta in enumerate(entradas, start=1):
        print(f"[{indice}/{len(entradas)}] {ruta.name} ...", flush=True)
        resumen = await procesar(ruta, indice, salida)
        if resumen.get("ok"):
            print(
                f"    → estado={resumen['estado']} esquema={'ok' if resumen['cumple_esquema'] else 'NO'} "
                f"contrato={'ok' if resumen['cumple_contrato'] else 'NO'} "
                f"clave={resumen['campos']['ok']}/{resumen['campos']['total']} "
                f"({resumen['ms']} ms)",
                flush=True,
            )
        else:
            print(f"    → ERROR: {resumen['error']}", flush=True)
        resumenes.append(resumen)

    print("\n" + _tabla_md(resumenes), flush=True)
    return resumenes


def main() -> int:
    parser = argparse.ArgumentParser(description="Extrae 2 PDF y 2 imágenes y guarda los JSON.")
    parser.add_argument("--pdf", nargs="*", default=list(PDFS_POR_DEFECTO))
    parser.add_argument("--imagen", nargs="*", default=list(IMAGENES_POR_DEFECTO))
    parser.add_argument("--salida", default=str(SALIDA_POR_DEFECTO))
    args = parser.parse_args()

    rutas = [RAIZ / p for p in (*args.pdf, *args.imagen)]
    faltantes = [ruta for ruta in rutas if not ruta.is_file()]
    if faltantes:
        for ruta in faltantes:
            print(f"no existe: {ruta}")
        return 2

    resumenes = asyncio.run(ejecutar(rutas, Path(args.salida)))
    return 0 if all(r.get("ok") for r in resumenes) else 1


if __name__ == "__main__":
    raise SystemExit(main())
