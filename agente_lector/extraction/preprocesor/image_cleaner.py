"""Limpieza de imágenes antes de Docling / OCR, con OpenCV.

Objetivo: subir la tasa de acierto del OCR en fotos de celular y escaneos pobres
(inclinación, sombras, poco contraste, ruido, resolución baja).

Regla de oro
------------
Docling detecta la estructura de tablas **sobre la imagen** (TableFormer). Una binarización
agresiva borra líneas tenues y bordes de celda, y empeora justo lo que más nos importa: la
tabla de carga del BL. Por eso hay dos presets y no uno:

- :data:`SUAVE` (por defecto): enderezado + sombras + iluminación + ruido leve. Conserva
  grises y bordes ⇒ **apto para la ruta Docling** (layout + tablas).
- :data:`OCR`: agrega binarización adaptativa, nitidez y ampliación ⇒ **solo para una segunda
  pasada de OCR** (fallback por página de la Fase 6), nunca para el layout.

Todo es determinista: mismos bytes + mismas opciones ⇒ misma imagen de salida. El resultado
incluye qué se aplicó y cuánto, para poder comparar corridas y medir si ayuda o estorba.

Uso::

    from agente_lector.extraction.preprocesor.image_cleaner import limpiar_bytes, SUAVE

    limpia = limpiar_bytes(datos, SUAVE)
    datos_para_docling = limpia.a_bytes(".png")
    print(limpia.operaciones, limpia.angulo_corregido)
"""

import logging
from dataclasses import dataclass, field

import cv2
import numpy as np

__all__ = [
    "NINGUNA",
    "OCR",
    "SUAVE",
    "ImagenLimpiada",
    "OpcionesLimpieza",
    "limpiar_bytes",
    "limpiar_imagen",
]

logger = logging.getLogger("portia.agente_lector")

#: Rango de enderezado aceptado (grados). Más que esto no es una foto torcida, es otra cosa.
_ANGULO_MAXIMO = 15.0

#: Solo se considera corregido si supera este umbral (evita rotaciones inútiles).
_ANGULO_MINIMO = 0.4

#: Ancho al que se reduce la muestra para estimar el ángulo (velocidad, no calidad).
_ANCHO_ANALISIS = 1000

_PASO_GRUESO = 1.0
_PASO_FINO = 0.25

#: Fondo considerado "blanco" al recortar bordes.
_UMBRAL_FONDO = 240


@dataclass(frozen=True)
class OpcionesLimpieza:
    """Qué mejoras aplicar. Cada una es independiente y se puede apagar."""

    enderezar: bool = True
    quitar_sombras: bool = True
    normalizar_iluminacion: bool = True
    reducir_ruido: bool = True
    #: ``True`` usa ``fastNlMeansDenoisingColored`` (mejor en fotos, ~10x más lento).
    ruido_fuerte: bool = False
    realzar_nitidez: bool = False
    #: Binariza la salida. **Apagado por defecto: destruye bordes de tabla.**
    binarizar: bool = False
    #: Escala la imagen si su ancho es menor (útil para fotos de baja resolución).
    ancho_minimo_px: int | None = None
    recortar_bordes: bool = False


#: Preset por defecto: seguro para la ruta Docling (layout + TableFormer).
SUAVE = OpcionesLimpieza()

#: Preset agresivo para OCR puro (segunda pasada). No usar en la ruta de Docling.
OCR = OpcionesLimpieza(
    realzar_nitidez=True,
    binarizar=True,
    ancho_minimo_px=1600,
    recortar_bordes=True,
)

#: Preset de diagnóstico: no toca nada (sirve para comparar A/B contra ``SUAVE``).
NINGUNA = OpcionesLimpieza(
    enderezar=False,
    quitar_sombras=False,
    normalizar_iluminacion=False,
    reducir_ruido=False,
)


@dataclass
class ImagenLimpiada:
    """Resultado del preprocesado: imagen + trazabilidad de lo aplicado."""

    imagen: np.ndarray
    operaciones: list[str] = field(default_factory=list)
    angulo_corregido: float = 0.0
    escala: float = 1.0
    ancho: int = 0
    alto: int = 0

    def a_bytes(self, formato: str = ".png", calidad: int = 92) -> bytes:
        """Serializa la imagen (PNG por defecto: sin pérdida, importa para el OCR)."""
        parametros = [int(cv2.IMWRITE_JPEG_QUALITY), int(calidad)] if formato in (".jpg", ".jpeg") else []
        ok, buffer = cv2.imencode(formato, self.imagen, parametros)
        if not ok:
            raise ValueError(f"no se pudo codificar la imagen como {formato!r}")
        return buffer.tobytes()


# ---------------------------------------------------------------------------
# API pública
# ---------------------------------------------------------------------------
def limpiar_bytes(datos: bytes, opciones: OpcionesLimpieza = SUAVE) -> ImagenLimpiada:
    """Decodifica ``datos`` (PNG/JPEG/WEBP/TIFF/BMP) y aplica la limpieza."""
    if not datos:
        raise ValueError("no hay bytes de imagen")
    arreglo = cv2.imdecode(np.frombuffer(datos, dtype=np.uint8), cv2.IMREAD_UNCHANGED)
    if arreglo is None:
        raise ValueError("los bytes no corresponden a una imagen decodificable")
    return limpiar_imagen(arreglo, opciones)


def limpiar_imagen(imagen: np.ndarray, opciones: OpcionesLimpieza = SUAVE) -> ImagenLimpiada:
    """Aplica la limpieza a una imagen en memoria (BGR, gris o BGRA)."""
    if imagen is None or getattr(imagen, "size", 0) == 0:
        raise ValueError("imagen vacía")

    actual = _a_bgr(imagen)
    operaciones: list[str] = []
    angulo = 0.0
    escala = 1.0

    if opciones.enderezar:
        angulo = _estimar_angulo(cv2.cvtColor(actual, cv2.COLOR_BGR2GRAY))
        if abs(angulo) >= _ANGULO_MINIMO:
            actual = _rotar(actual, angulo, borde=(255, 255, 255), bandera=cv2.INTER_CUBIC)
            operaciones.append(f"deskew:{angulo:+.2f}deg")
        else:
            angulo = 0.0

    if opciones.quitar_sombras:
        actual = _quitar_sombras(actual)
        operaciones.append("sombras")

    if opciones.normalizar_iluminacion:
        actual = _normalizar_iluminacion(actual)
        operaciones.append("clahe")

    if opciones.reducir_ruido:
        if opciones.ruido_fuerte:
            actual = cv2.fastNlMeansDenoisingColored(actual, None, 3, 3, 7, 21)
            operaciones.append("nlm")
        else:
            actual = cv2.medianBlur(actual, 3)
            operaciones.append("mediana")

    if opciones.realzar_nitidez:
        actual = _realzar_nitidez(actual)
        operaciones.append("nitidez")

    if opciones.ancho_minimo_px and actual.shape[1] < opciones.ancho_minimo_px:
        escala = opciones.ancho_minimo_px / float(actual.shape[1])
        actual = cv2.resize(
            actual,
            (round(actual.shape[1] * escala), round(actual.shape[0] * escala)),
            interpolation=cv2.INTER_CUBIC,
        )
        operaciones.append(f"escala:{escala:.2f}")

    if opciones.binarizar:
        actual = _binarizar(actual)
        operaciones.append("binarizada")

    if opciones.recortar_bordes:
        actual = _recortar_bordes(actual)
        operaciones.append("recorte")

    alto, ancho = actual.shape[:2]
    logger.debug(
        "imagen limpiada: %dx%d -> %dx%d (angulo=%.2f escala=%.2f ops=%s)",
        imagen.shape[1],
        imagen.shape[0],
        ancho,
        alto,
        angulo,
        escala,
        operaciones,
    )
    return ImagenLimpiada(
        imagen=actual,
        operaciones=operaciones,
        angulo_corregido=angulo,
        escala=escala,
        ancho=ancho,
        alto=alto,
    )


# ---------------------------------------------------------------------------
# Pasos internos
# ---------------------------------------------------------------------------
def _a_bgr(imagen: np.ndarray) -> np.ndarray:
    """Normaliza cualquier entrada a BGR de 8 bits."""
    if imagen.dtype != np.uint8:
        if imagen.dtype == np.uint16:
            imagen = (imagen / 257.0).round().astype(np.uint8)
        else:
            imagen = np.clip(imagen, 0, 255).astype(np.uint8)

    if imagen.ndim == 2:
        return cv2.cvtColor(imagen, cv2.COLOR_GRAY2BGR)
    if imagen.shape[2] == 4:
        return cv2.cvtColor(imagen, cv2.COLOR_BGRA2BGR)
    if imagen.shape[2] == 3:
        return imagen
    raise ValueError(f"forma de imagen no soportada: {imagen.shape}")


def _rotar(
    imagen: np.ndarray,
    angulo: float,
    borde: tuple[int, int, int] | int = 255,
    bandera: int = cv2.INTER_CUBIC,
) -> np.ndarray:
    """Rota sin recortar contenido, rellenando con ``borde``."""
    alto, ancho = imagen.shape[:2]
    centro = (ancho / 2.0, alto / 2.0)
    matriz = cv2.getRotationMatrix2D(centro, angulo, 1.0)
    coseno, seno = abs(matriz[0, 0]), abs(matriz[0, 1])
    nuevo_ancho = round(alto * seno + ancho * coseno)
    nuevo_alto = round(alto * coseno + ancho * seno)
    matriz[0, 2] += nuevo_ancho / 2.0 - centro[0]
    matriz[1, 2] += nuevo_alto / 2.0 - centro[1]
    return cv2.warpAffine(
        imagen,
        matriz,
        (nuevo_ancho, nuevo_alto),
        flags=bandera,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=borde,
    )


def _estimar_angulo(gris: np.ndarray) -> float:
    """Ángulo de inclinación en grados, maximizando el contraste del perfil horizontal.

    El texto alineado produce picos nítidos al proyectar la tinta por filas; la inclinación
    los difumina. Se busca por fuerza bruta sobre una muestra reducida (grueso + fino).
    """
    escala = min(1.0, _ANCHO_ANALISIS / float(max(gris.shape[1], 1)))
    muestra = (
        cv2.resize(gris, None, fx=escala, fy=escala, interpolation=cv2.INTER_AREA)
        if escala < 1.0
        else gris
    )
    _, tinta = cv2.threshold(muestra, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    if cv2.countNonZero(tinta) < 200:
        return 0.0

    def puntaje(angulo: float) -> float:
        rotada = _rotar(tinta, angulo, borde=0, bandera=cv2.INTER_NEAREST)
        filas = np.where(rotada.sum(axis=1) > 0)[0]  # descarta el relleno añadido
        if filas.size < 2:
            return 0.0
        perfil = rotada[filas[0] : filas[-1] + 1].sum(axis=1, dtype=np.float64)
        return float(np.var(perfil))

    gruesos = np.arange(-_ANGULO_MAXIMO, _ANGULO_MAXIMO + 1e-9, _PASO_GRUESO)
    mejor = float(max(gruesos, key=puntaje))
    finos = np.arange(mejor - _PASO_GRUESO, mejor + _PASO_GRUESO + 1e-9, _PASO_FINO)
    return round(float(max(finos, key=puntaje)), 2)


def _quitar_sombras(imagen: np.ndarray) -> np.ndarray:
    """Divide por el fondo estimado: aplana sombras sin quemar los blancos."""
    alto, ancho = imagen.shape[:2]
    lado = max(15, int(min(alto, ancho) / 40) | 1)  # impar, proporcional al tamaño
    nucleo = cv2.getStructuringElement(cv2.MORPH_RECT, (lado, lado))
    canales = []
    for canal in cv2.split(imagen):
        fondo = cv2.morphologyEx(canal, cv2.MORPH_CLOSE, nucleo)
        canales.append(cv2.divide(canal, np.maximum(fondo, 1), scale=255))
    return cv2.merge(canales)


def _normalizar_iluminacion(imagen: np.ndarray) -> np.ndarray:
    """CLAHE sobre el canal L: sube el contraste local sin reventar los extremos."""
    laboratorio = cv2.cvtColor(imagen, cv2.COLOR_BGR2LAB)
    luminancia, a, b = cv2.split(laboratorio)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    return cv2.cvtColor(cv2.merge((clahe.apply(luminancia), a, b)), cv2.COLOR_LAB2BGR)


def _realzar_nitidez(imagen: np.ndarray) -> np.ndarray:
    """Unsharp mask suave (máscara de enfoque) para trazos desenfocados."""
    difusa = cv2.GaussianBlur(imagen, (0, 0), sigmaX=1.5)
    return cv2.addWeighted(imagen, 1.5, difusa, -0.5, 0)


def _binarizar(imagen: np.ndarray) -> np.ndarray:
    """Binarización adaptativa gaussiana (soporta iluminación desigual)."""
    gris = cv2.cvtColor(imagen, cv2.COLOR_BGR2GRAY)
    menor = min(gris.shape[:2])
    bloque = max(15, (menor // 20) * 2 + 1)
    binaria = cv2.adaptiveThreshold(
        gris, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, bloque, 10
    )
    return cv2.cvtColor(binaria, cv2.COLOR_GRAY2BGR)


def _recortar_bordes(imagen: np.ndarray, margen: int = 8) -> np.ndarray:
    """Recorta el marco uniforme alrededor del contenido."""
    gris = cv2.cvtColor(imagen, cv2.COLOR_BGR2GRAY)
    filas, columnas = np.where(gris < _UMBRAL_FONDO)
    if filas.size == 0:
        return imagen
    alto, ancho = gris.shape[:2]
    y0 = max(0, filas.min() - margen)
    y1 = min(alto, filas.max() + margen + 1)
    x0 = max(0, columnas.min() - margen)
    x1 = min(ancho, columnas.max() + margen + 1)
    if (y1 - y0) < alto * 0.2 or (x1 - x0) < ancho * 0.2:
        return imagen  # recorte sospechoso: mejor no tocar
    return imagen[y0:y1, x0:x1]
