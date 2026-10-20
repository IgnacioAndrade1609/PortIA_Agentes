"""Tests del preprocesador de imagen (OpenCV)."""

import cv2
import numpy as np
import pytest

from agente_lector.extraction.preprocesor.image_cleaner import (
    NINGUNA,
    OCR,
    SUAVE,
    OpcionesLimpieza,
    limpiar_bytes,
    limpiar_imagen,
)

TEXTO = "BILL OF LADING 140021277 TGCU5129073"


def _documento(angulo: float = 0.0, ancho: int = 700, alto: int = 900, lineas: int = 14) -> np.ndarray:
    """Imagen sintética tipo BL: líneas de texto negro sobre blanco."""
    imagen = np.full((alto, ancho, 3), 255, np.uint8)
    for indice in range(lineas):
        cv2.putText(
            imagen,
            TEXTO,
            (40, 60 + indice * (alto // (lineas + 2))),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.9,
            (0, 0, 0),
            2,
            cv2.LINE_AA,
        )
    if angulo:
        centro = (ancho / 2, alto / 2)
        matriz = cv2.getRotationMatrix2D(centro, angulo, 1.0)
        imagen = cv2.warpAffine(
            imagen,
            matriz,
            (ancho, alto),
            flags=cv2.INTER_CUBIC,
            borderMode=cv2.BORDER_CONSTANT,
            borderValue=(255, 255, 255),
        )
    return imagen


def test_sin_opciones_no_toca_nada():
    original = _documento()
    resultado = limpiar_imagen(original, NINGUNA)
    assert resultado.operaciones == []
    assert resultado.angulo_corregido == 0.0
    assert resultado.imagen.shape == original.shape
    assert np.array_equal(resultado.imagen, original)


def test_imagen_recta_no_se_rota():
    resultado = limpiar_imagen(_documento(), SUAVE)
    assert resultado.angulo_corregido == 0.0
    assert not any(operacion.startswith("deskew") for operacion in resultado.operaciones)


@pytest.mark.parametrize("inclinacion", [3.0, -6.0, 11.0])
def test_endereza_texto_inclinado(inclinacion):
    """Si el documento está girado +a, la corrección debe ser -a (y viceversa)."""
    resultado = limpiar_imagen(_documento(inclinacion), SUAVE)
    assert resultado.angulo_corregido == pytest.approx(-inclinacion, abs=1.0)
    assert any(operacion.startswith("deskew") for operacion in resultado.operaciones)


def test_preset_suave_nunca_binariza():
    """Binarizar destruye bordes de tabla: el preset de Docling no debe hacerlo."""
    resultado = limpiar_imagen(_documento(), SUAVE)
    assert "binarizada" not in resultado.operaciones


def test_preset_ocr_binariza_y_amplia():
    resultado = limpiar_imagen(_documento(ancho=400, alto=520, lineas=8), OCR)
    assert "binarizada" in resultado.operaciones
    # Se amplía a 1600 px de ancho y luego el recorte quita el marco vacío.
    assert resultado.escala == pytest.approx(4.0, abs=0.01)
    assert resultado.ancho >= 1400
    grises = np.unique(cv2.cvtColor(resultado.imagen, cv2.COLOR_BGR2GRAY))
    assert set(grises.tolist()) <= {0, 255}


def test_amplia_solo_si_hace_falta():
    opciones = OpcionesLimpieza(
        enderezar=False,
        quitar_sombras=False,
        normalizar_iluminacion=False,
        reducir_ruido=False,
        ancho_minimo_px=800,
    )
    pequena = limpiar_imagen(_documento(ancho=400, alto=520), opciones)
    assert pequena.escala > 1.0
    assert pequena.ancho == 800

    grande = limpiar_imagen(_documento(ancho=900, alto=1100), opciones)
    assert grande.escala == 1.0
    assert grande.ancho == 900


def test_quitar_sombras_aclara_la_zona_oscura():
    base = _documento()
    alto, ancho = base.shape[:2]
    gradiente = np.tile(np.linspace(255, 90, ancho, dtype=np.uint8), (alto, 1))
    sombreada = np.minimum(base, cv2.merge([gradiente] * 3))

    opciones = OpcionesLimpieza(
        enderezar=False,
        quitar_sombras=True,
        normalizar_iluminacion=False,
        reducir_ruido=False,
    )
    antes = cv2.cvtColor(sombreada[:, int(ancho * 0.85) :], cv2.COLOR_BGR2GRAY).mean()
    despues = cv2.cvtColor(
        limpiar_imagen(sombreada, opciones).imagen[:, int(ancho * 0.85) :], cv2.COLOR_BGR2GRAY
    ).mean()
    assert despues > antes + 20


def test_acepta_escala_de_grises_y_bgra():
    gris = cv2.cvtColor(_documento(), cv2.COLOR_BGR2GRAY)
    assert limpiar_imagen(gris, NINGUNA).imagen.shape[2] == 3

    bgra = cv2.cvtColor(_documento(), cv2.COLOR_BGR2BGRA)
    assert limpiar_imagen(bgra, NINGUNA).imagen.shape[2] == 3


def test_bytes_ida_y_vuelta_png():
    original = _documento()
    ok, buffer = cv2.imencode(".png", original)
    assert ok

    resultado = limpiar_bytes(buffer.tobytes(), NINGUNA)
    assert resultado.imagen.shape == original.shape

    recodificada = cv2.imdecode(np.frombuffer(resultado.a_bytes(".png"), np.uint8), cv2.IMREAD_COLOR)
    assert recodificada is not None
    assert recodificada.shape == original.shape


def test_jpeg_acepta_calidad():
    limpia = limpiar_imagen(_documento(), NINGUNA)
    assert limpia.a_bytes(".jpg", calidad=70)


@pytest.mark.parametrize("datos", [b"", b"no soy una imagen"])
def test_bytes_invalidos_lanzan_error(datos):
    with pytest.raises(ValueError):
        limpiar_bytes(datos)


def test_imagen_vacia_lanza_error():
    with pytest.raises(ValueError):
        limpiar_imagen(np.zeros((0, 0, 3), np.uint8))


def test_imagen_blanca_no_explota_en_el_enderezado():
    """Sin tinta no hay ángulo que estimar: debe devolver 0, no fallar."""
    resultado = limpiar_imagen(np.full((400, 600, 3), 255, np.uint8), SUAVE)
    assert resultado.angulo_corregido == 0.0


def test_recorte_elimina_marco_vacio():
    con_marco = cv2.copyMakeBorder(_documento(ancho=600, alto=800), 120, 120, 120, 120, cv2.BORDER_CONSTANT, value=(255, 255, 255))
    opciones = OpcionesLimpieza(
        enderezar=False,
        quitar_sombras=False,
        normalizar_iluminacion=False,
        reducir_ruido=False,
        recortar_bordes=True,
    )
    resultado = limpiar_imagen(con_marco, opciones)
    assert resultado.alto < con_marco.shape[0]
    assert resultado.ancho < con_marco.shape[1]
