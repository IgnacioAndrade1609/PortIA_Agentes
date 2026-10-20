"""Extracción del BL con un LLM vía OpenRouter (structured output + grounding).

Flujo
-----
1. Se envía el **Markdown** del documento (nunca el ``DoclingDocument``) a
   ``POST {base_url}/chat/completions`` con el schema plano de
   :func:`agente_lector.extraction.llm_schema.esquema_para_llm`.
2. El modelo devuelve **solo strings** (``null`` si el dato no aparece). No puede emitir
   ``confidence`` ni ``evidence``: esas señales las calcula el pipeline.
3. :func:`construir_bl` arma ``BillOfLadingExtraido`` y aplica **grounding**: cada valor debe
   existir en el texto del documento. Lo que no existe se marca ``grounding_failed`` y queda
   con confianza 0 para que un humano lo revise.

Diseño de robustez
------------------
- ``response_format`` degrada solo: ``json_schema`` → ``json_object`` → sin formato, cuando el
  proveedor responde que no soporta el formato pedido (los modelos gratuitos suelen no soportarlo).
- Reintentos con backoff **solo** en errores transitorios (429/5xx). Un 401/403/404 no se reintenta.
- El modelo de respaldo (``LLM_MODEL_FALLBACK``) se intenta después del principal.
- ``requests`` es bloqueante: en código async usar :meth:`OpenRouterClient.extraer_async`
  (envuelve la llamada en ``asyncio.to_thread``) para no bloquear el event loop del consumer.

Nunca se loguea el contenido del documento ni los valores extraídos (PII, Ley 21.719): solo
nombres de campos, modelo, tokens y latencia.
"""

import asyncio
import json
import logging
import os
import re
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path
from typing import Any, Protocol

import requests

from agente_lector.extraction.llm_schema import esquema_para_llm
from agente_lector.extraction.schemas import (
    BillOfLadingExtraido,
    CampoExtraido,
    ContainerData,
    Evidence,
    EvidenceMethod,
    ParteComercial,
    ReviewReason,
)

__all__ = [
    "RUTA_PROMPT",
    "VERSION_PROMPT",
    "ConfigLLM",
    "ErrorLLM",
    "LLMClient",
    "OpenRouterClient",
    "aplicar_grounding",
    "buscar_en_texto",
    "cargar_prompt",
    "construir_bl",
    "es_extraccion_vacia",
    "extraer_bl",
    "extraer_bl_async",
]

logger = logging.getLogger("portia.agente_lector")

RUTA_PROMPT = Path(__file__).resolve().parent / "prompts" / "bl_v1.md"

#: Versión del prompt: viaja en la metadata del resultado.
VERSION_PROMPT = "bl_v1"

#: Umbral de grounding: por debajo de esto el valor no está respaldado por el documento.
UMBRAL_GROUNDING = 0.9

#: Códigos HTTP que sí valen un reintento.
_CODIGOS_TRANSITORIOS = frozenset({408, 409, 425, 429})

#: Señales de que el problema es el ``response_format`` y no el documento.
#: Incluye el caso real de MiniMax: rechaza ``"type": ["string", "null"]``.
_PALABRAS_FORMATO = (
    "response_format",
    "json_schema",
    "structured",
    "schema",
    "not supported",
    "unsupported",
    "mismatch type",
    "invalid params",
)

_PARTES = ("shipper", "consignee", "notify_party", "also_notify")
_SUBCAMPOS_PARTE = ("nombre", "direccion", "identificador", "contacto")
_SUBCAMPOS_CONTENEDOR = ("number", "iso_type", "seal")


class ErrorLLM(RuntimeError):
    """Fallo del proveedor de LLM.

    ``transitorio`` indica si vale la pena reintentar; ``formato_no_soportado`` permite
    degradar el ``response_format`` en vez de rendirse.
    """

    def __init__(
        self,
        mensaje: str,
        *,
        transitorio: bool,
        formato_no_soportado: bool = False,
    ) -> None:
        super().__init__(mensaje)
        self.transitorio = transitorio
        self.formato_no_soportado = formato_no_soportado


@dataclass(frozen=True)
class ConfigLLM:
    """Configuración del proveedor. Se construye con :meth:`desde_entorno`."""

    api_key: str
    base_url: str = "https://openrouter.ai/api/v1"
    modelo: str = "google/gemma-4-31b-it:free"
    modelos_respaldo: tuple[str, ...] = ()
    temperatura: float = 0.0
    max_tokens: int = 4096
    timeout: float = 90.0
    intentos: int = 3
    #: ``auto`` | ``json_schema`` | ``json_object`` | ``ninguno``
    modo_formato: str = "auto"
    razonamiento: bool = False
    #: Restringe el enrutamiento de OpenRouter a estos proveedores.
    proveedor_solo: tuple[str, ...] = ()
    permitir_fallbacks: bool = False
    #: Cabeceras de atribución de OpenRouter (opcionales).
    referer: str = ""
    titulo: str = ""

    @property
    def url_completions(self) -> str:
        return f"{self.base_url.rstrip('/')}/chat/completions"

    @classmethod
    def desde_entorno(cls) -> "ConfigLLM":
        """Lee la configuración de variables de entorno (y del ``.env`` si existe)."""
        try:  # el ``.env`` no se carga en el arranque del servicio (ver main.py)
            from dotenv import load_dotenv

            load_dotenv()
        except ImportError:  # pragma: no cover - python-dotenv es dependencia del proyecto
            pass

        api_key = (
            os.getenv("LLM_API_KEY")
            or os.getenv("OPENROUTER_API_KEY")
            or os.getenv("API_KEY")
            or ""
        ).strip()
        if not api_key:
            raise ErrorLLM(
                "falta la API key del LLM: define LLM_API_KEY (o OPENROUTER_API_KEY / API_KEY)",
                transitorio=False,
            )

        return cls(
            api_key=api_key,
            base_url=os.getenv("LLM_BASE_URL", "https://openrouter.ai/api/v1"),
            modelo=os.getenv("LLM_MODEL", "google/gemma-4-31b-it:free"),
            modelos_respaldo=_lista(os.getenv("LLM_MODEL_FALLBACK", "")),
            temperatura=float(os.getenv("LLM_TEMPERATURE", "0")),
            max_tokens=int(os.getenv("LLM_MAX_TOKENS", "4096")),
            timeout=float(os.getenv("LLM_TIMEOUT", "90")),
            intentos=max(1, int(os.getenv("LLM_MAX_RETRIES", "3"))),
            modo_formato=os.getenv("LLM_RESPONSE_FORMAT", "auto"),
            razonamiento=_booleano(os.getenv("LLM_REASONING", "false")),
            proveedor_solo=_lista(os.getenv("LLM_PROVIDER_ONLY", "")),
            permitir_fallbacks=_booleano(os.getenv("LLM_ALLOW_FALLBACKS", "false")),
            referer=os.getenv("LLM_REFERER", ""),
            titulo=os.getenv("LLM_TITLE", ""),
        )


class LLMClient(Protocol):
    """Contrato mínimo para poder sustituir el proveedor (o simularlo en tests)."""

    modelo: str

    def extraer(self, markdown: str) -> dict: ...


@dataclass
class OpenRouterClient:
    """Cliente de ``/chat/completions`` compatible con OpenRouter y con vLLM/Ollama."""

    config: ConfigLLM = field(default_factory=ConfigLLM.desde_entorno)
    #: Inyectable para los tests (evita esperas reales).
    dormir: Callable[[float], None] = time.sleep

    @property
    def modelo(self) -> str:
        return self.config.modelo

    # -- construcción del payload -------------------------------------------
    def construir_payload(
        self,
        markdown: str,
        *,
        modelo: str | None = None,
        modo_formato: str | None = None,
    ) -> dict:
        """Cuerpo de la petición, con la forma que documenta OpenRouter."""
        modo = modo_formato or self.config.modo_formato
        payload: dict[str, Any] = {
            "model": modelo or self.config.modelo,
            "messages": [
                {"role": "system", "content": cargar_prompt()},
                {"role": "user", "content": markdown},
            ],
            "temperature": self.config.temperatura,
            "max_tokens": self.config.max_tokens,
        }

        if self.config.razonamiento:
            payload["reasoning"] = {"enabled": True}

        if self.config.proveedor_solo:
            payload["provider"] = {
                "only": list(self.config.proveedor_solo),
                "allow_fallbacks": self.config.permitir_fallbacks,
            }

        formato = self._response_format("json_schema" if modo == "auto" else modo)
        if formato is not None:
            payload["response_format"] = formato
        return payload

    @staticmethod
    def _response_format(modo: str) -> dict | None:
        if modo == "json_schema":
            return {
                "type": "json_schema",
                "json_schema": {
                    "name": "bill_of_lading",
                    "strict": True,
                    "schema": esquema_para_llm(),
                },
            }
        if modo == "json_object":
            return {"type": "json_object"}
        return None

    def _cabeceras(self) -> dict[str, str]:
        cabeceras = {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json",
        }
        if self.config.referer:
            cabeceras["HTTP-Referer"] = self.config.referer
        if self.config.titulo:
            cabeceras["X-Title"] = self.config.titulo
        return cabeceras

    # -- transporte ----------------------------------------------------------
    def _post(self, payload: dict) -> dict:
        """Una llamada HTTP. Aislada para poder simularla en tests."""
        try:
            respuesta = requests.post(
                self.config.url_completions,
                headers=self._cabeceras(),
                data=json.dumps(payload),
                timeout=self.config.timeout,
            )
        except requests.RequestException as exc:
            raise ErrorLLM(f"fallo de red: {type(exc).__name__}", transitorio=True) from exc

        if respuesta.status_code >= 400:
            detalle = (respuesta.text or "")[:400]
            transitorio = respuesta.status_code in _CODIGOS_TRANSITORIOS or respuesta.status_code >= 500
            raise ErrorLLM(
                f"openrouter {respuesta.status_code}: {detalle}",
                transitorio=transitorio,
                formato_no_soportado=_es_problema_de_formato(detalle, payload),
            )

        try:
            cuerpo = respuesta.json()
        except ValueError as exc:
            raise ErrorLLM("la respuesta del proveedor no es JSON", transitorio=True) from exc

        # OpenRouter también puede devolver ``200`` con un ``error`` dentro del cuerpo.
        if isinstance(cuerpo, dict) and cuerpo.get("error"):
            error = cuerpo["error"]
            mensaje = error.get("message", "") if isinstance(error, dict) else str(error)
            codigo = error.get("code") if isinstance(error, dict) else None
            raise ErrorLLM(
                f"openrouter: {mensaje}",
                transitorio=codigo in _CODIGOS_TRANSITORIOS or (isinstance(codigo, int) and codigo >= 500),
                formato_no_soportado=_es_problema_de_formato(mensaje, payload),
            )

        return cuerpo

    # -- API pública ---------------------------------------------------------
    def extraer(self, markdown: str) -> dict:
        """Extrae los campos del BL. Reintenta, degrada formato y prueba el respaldo."""
        if not markdown or not markdown.strip():
            raise ErrorLLM("documento vacío: no hay texto para extraer", transitorio=False)

        modelos = (self.config.modelo, *self.config.modelos_respaldo)
        modo = self.config.modo_formato
        ultimo: ErrorLLM | None = None

        for modelo in modelos:
            intento = 0
            while intento < self.config.intentos:
                payload = self.construir_payload(markdown, modelo=modelo, modo_formato=modo)
                try:
                    return _extraer_json(self._post(payload))
                except ErrorLLM as error:
                    ultimo = error
                    # Degradar el formato no consume intento: se reintenta el mismo modelo.
                    if error.formato_no_soportado and modo != "ninguno":
                        modo = "json_object" if modo in ("auto", "json_schema") else "ninguno"
                        logger.warning(
                            "el proveedor rechazó el formato pedido; degradando a %s", modo
                        )
                        continue
                    if not error.transitorio:
                        break  # este modelo no sirve: probar el de respaldo
                    espera = min(2**intento, 8)
                    intento += 1
                    logger.warning("error transitorio del LLM (%s); reintento en %ss", error, espera)
                    self.dormir(espera)

        raise ultimo or ErrorLLM("no se pudo extraer el documento", transitorio=False)

    async def extraer_async(self, markdown: str) -> dict:
        """Versión no bloqueante: ``requests`` es síncrono y el consumer es async."""
        return await asyncio.to_thread(self.extraer, markdown)


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------
@lru_cache(maxsize=4)
def cargar_prompt(ruta: Path | None = None) -> str:
    """Texto del prompt versionado (``prompts/bl_v1.md``)."""
    destino = ruta or RUTA_PROMPT
    return destino.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Respuesta → esquema de dominio
# ---------------------------------------------------------------------------
def extraer_bl(markdown: str, cliente: LLMClient) -> BillOfLadingExtraido:
    """Llama al LLM y devuelve el BL con evidencia y confianza por campo."""
    inicio = time.perf_counter()
    datos = cliente.extraer(markdown)
    bl = construir_bl(datos, markdown)
    logger.info(
        "extraccion LLM completada: modelo=%s campos=%d sin_respaldo=%d ms=%d",
        getattr(cliente, "modelo", "?"),
        _contar_presentes(bl),
        len(bl.review_reasons),
        int((time.perf_counter() - inicio) * 1000),
    )
    return bl


async def extraer_bl_async(markdown: str, cliente: OpenRouterClient) -> BillOfLadingExtraido:
    """Variante async para el consumer de RabbitMQ."""
    inicio = time.perf_counter()
    datos = await cliente.extraer_async(markdown)
    bl = construir_bl(datos, markdown)
    logger.info(
        "extraccion LLM completada: modelo=%s campos=%d sin_respaldo=%d ms=%d",
        cliente.modelo,
        _contar_presentes(bl),
        len(bl.review_reasons),
        int((time.perf_counter() - inicio) * 1000),
    )
    return bl


def construir_bl(datos: dict, texto: str) -> BillOfLadingExtraido:
    """Convierte el JSON plano del LLM en ``BillOfLadingExtraido`` y aplica grounding."""
    if not isinstance(datos, dict):
        raise ErrorLLM(f"se esperaba un objeto JSON, llegó {type(datos).__name__}", transitorio=False)

    bl = BillOfLadingExtraido()

    for nombre in _nombres_campo_texto():
        valor = _a_texto(datos.get(nombre))
        if valor:
            setattr(bl, nombre, CampoExtraido(value=valor))

    for nombre in _nombres_parte():
        recibida = datos.get(nombre)
        if not isinstance(recibida, dict):
            continue
        parte: ParteComercial = getattr(bl, nombre)
        for subcampo in _SUBCAMPOS_PARTE:
            valor = _a_texto(recibida.get(subcampo))
            if valor:
                setattr(parte, subcampo, CampoExtraido(value=valor))

    for recibido in datos.get("containers") or []:
        if not isinstance(recibido, dict):
            continue
        campos = {sub: _a_texto(recibido.get(sub)) for sub in _SUBCAMPOS_CONTENEDOR}
        if not any(campos.values()):
            continue
        bl.containers.append(
            ContainerData(**{sub: CampoExtraido(value=v) for sub, v in campos.items() if v})
        )

    aplicar_grounding(bl, texto)
    return bl


def aplicar_grounding(bl: BillOfLadingExtraido, texto: str) -> list[str]:
    """Marca con confianza y evidencia los campos respaldados por el documento.

    Devuelve los nombres de los campos que **no** se pudieron respaldar (se gregan a
    ``bl.review_reasons``). Nunca modifica el valor: la corrección es de un humano.
    """
    sin_respaldo: list[str] = []
    for nombre, campo in _iterar_campos(bl):
        if not campo.presente:
            continue
        puntaje, fragmento, exacto = buscar_en_texto(campo.value or "", texto)
        if puntaje >= UMBRAL_GROUNDING:
            campo.confidence = 0.9 if exacto else 0.85
            campo.evidence = [
                Evidence(page=None, text=fragmento, method=EvidenceMethod.LLM)
            ]
        else:
            campo.confidence = 0.0
            campo.evidence = []
            sin_respaldo.append(nombre)

    if sin_respaldo and ReviewReason.GROUNDING_FAILED not in bl.review_reasons:
        bl.review_reasons.append(ReviewReason.GROUNDING_FAILED)
    return sin_respaldo


def buscar_en_texto(valor: str, texto: str) -> tuple[float, str, bool]:
    """Busca ``valor`` en ``texto``. Devuelve ``(puntaje, fragmento, exacto)``.

    Exacto: coincidencia literal sobre el texto normalizado (espacios y mayúsculas).
    Fuzzy: mejor ventana de tokens cercana al largo del valor, con anclaje por las 3
    primeras letras del primer token (evita comparar contra todo el documento).
    """
    normalizado = _normalizar(valor)
    if not normalizado:
        return 0.0, "", False

    cuerpo = _normalizar(texto)
    if normalizado in cuerpo:
        return 1.0, valor, True

    tokens = cuerpo.split()
    if not tokens:
        return 0.0, "", False

    primero = normalizado.split()[0]
    ancla = primero if len(primero) < 3 else primero[:3]
    candidatos = [indice for indice, token in enumerate(tokens) if token.startswith(ancla)]
    if not candidatos:
        return 0.0, "", False

    tamano_base = max(1, len(normalizado.split()))
    mejor, fragmento = 0.0, ""
    for indice in candidatos[:200]:
        for tamano in {tamano_base, tamano_base + 1}:
            ventana = " ".join(tokens[indice : indice + tamano])
            if not ventana:
                continue
            puntaje = SequenceMatcher(None, normalizado, ventana).ratio()
            if puntaje > mejor:
                mejor, fragmento = puntaje, ventana
                if puntaje >= 0.999:
                    return mejor, fragmento, False
    return mejor, fragmento, False


def es_extraccion_vacia(bl: BillOfLadingExtraido) -> bool:
    """``True`` si el modelo no trajo ningún campo con valor."""
    return not any(campo.presente for _, campo in _iterar_campos(bl))


def _contar_presentes(bl: BillOfLadingExtraido) -> int:
    return sum(1 for _, campo in _iterar_campos(bl) if campo.presente)


# ---------------------------------------------------------------------------
# Utilidades internas
# ---------------------------------------------------------------------------
@lru_cache(maxsize=1)
def _nombres_campo_texto() -> tuple[str, ...]:
    """Campos de primer nivel que son ``CampoExtraido``."""
    return tuple(
        nombre
        for nombre, campo in BillOfLadingExtraido.model_fields.items()
        if campo.annotation is CampoExtraido
    )


@lru_cache(maxsize=1)
def _nombres_parte() -> tuple[str, ...]:
    """Campos de primer nivel que son ``ParteComercial``."""
    return tuple(
        nombre
        for nombre, campo in BillOfLadingExtraido.model_fields.items()
        if campo.annotation is ParteComercial
    )


def _iterar_campos(bl: BillOfLadingExtraido) -> Iterator[tuple[str, CampoExtraido]]:
    for nombre in _nombres_campo_texto():
        yield nombre, getattr(bl, nombre)
    for nombre in _nombres_parte():
        parte: ParteComercial = getattr(bl, nombre)
        for subcampo in _SUBCAMPOS_PARTE:
            yield f"{nombre}.{subcampo}", getattr(parte, subcampo)
    for indice, contenedor in enumerate(bl.containers):
        for subcampo in _SUBCAMPOS_CONTENEDOR:
            yield f"containers[{indice}].{subcampo}", getattr(contenedor, subcampo)


def _normalizar(texto: str) -> str:
    return re.sub(r"\s+", " ", texto).strip().casefold()


def _a_texto(valor: Any) -> str | None:
    """Normaliza lo que devuelve el modelo a ``str`` (el contrato es todo string)."""
    if valor is None:
        return None
    if isinstance(valor, bool):
        return "true" if valor else "false"
    if isinstance(valor, (int, float)):
        return str(valor)
    if isinstance(valor, str):
        limpio = valor.strip()
        return limpio or None
    if isinstance(valor, list):  # algunos modelos devuelven listas de strings
        partes = [_a_texto(elemento) for elemento in valor]
        unido = " ".join(p for p in partes if p)
        return unido or None
    return None


def _lista(valor: str) -> tuple[str, ...]:
    return tuple(elemento.strip() for elemento in valor.split(",") if elemento.strip())


def _booleano(valor: str) -> bool:
    return valor.strip().lower() in {"1", "true", "si", "sí", "yes", "on"}


def _es_problema_de_formato(detalle: str, payload: dict) -> bool:
    """¿El error es porque el proveedor no acepta el ``response_format`` enviado?

    Caso real (MiniMax vía OpenRouter): rechaza ``"type": ["string", "null"]`` con
    ``invalid params ... Mismatch type string with value array``. Si además el payload no
    pedía ``json_schema``, el formato no es el culpable.
    """
    if (payload.get("response_format") or {}).get("type") != "json_schema":
        return False
    minusculas = detalle.casefold()
    return any(senal in minusculas for senal in _PALABRAS_FORMATO)


def _limpiar_cercas(contenido: str) -> str:
    """Quita ```json ... ``` si el modelo insistió en envolver el JSON."""
    texto = contenido.strip()
    if not texto.startswith("```"):
        return texto
    texto = re.sub(r"^```[a-zA-Z]*\s*", "", texto)
    return re.sub(r"\s*```$", "", texto).strip()


def _recortar_objeto(texto: str) -> str | None:
    """Extrae el objeto ``{...}`` más externo de una respuesta con prosa alrededor.

    Los modelos chicos suelen contestar "Aquí tienes el JSON: {...} Espero que sirva".
    Recortar es más barato y fiable que gastar otro intento.
    """
    inicio = texto.find("{")
    fin = texto.rfind("}")
    if inicio < 0 or fin <= inicio:
        return None
    return texto[inicio : fin + 1]


def _extraer_json(respuesta: dict) -> dict:
    try:
        mensaje = respuesta["choices"][0]["message"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ErrorLLM("la respuesta no trae 'choices[0].message'", transitorio=True) from exc

    contenido = mensaje.get("content")
    if isinstance(contenido, list):  # bloques de contenido de algunos proveedores
        contenido = "".join(
            parte.get("text", "") for parte in contenido if isinstance(parte, dict)
        )

    if not isinstance(contenido, str) or not contenido.strip():
        raise ErrorLLM("el modelo devolvió contenido vacío", transitorio=True)

    datos = _cargar_json(_limpiar_cercas(contenido))
    if datos is None:
        recortado = _recortar_objeto(contenido)
        datos = _cargar_json(recortado) if recortado else None
    if datos is None:
        raise ErrorLLM("el modelo no devolvió JSON válido", transitorio=True)

    if not isinstance(datos, dict):
        raise ErrorLLM("el modelo devolvió un JSON que no es un objeto", transitorio=False)

    uso = respuesta.get("usage") or {}
    if uso:
        logger.debug(
            "tokens: prompt=%s completion=%s",
            uso.get("prompt_tokens"),
            uso.get("completion_tokens"),
        )
    return datos


def _cargar_json(texto: str) -> dict | None:
    try:
        return json.loads(texto)
    except json.JSONDecodeError:
        return None
