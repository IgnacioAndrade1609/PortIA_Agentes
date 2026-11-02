from unittest.mock import AsyncMock, patch

import pytest

from agente_inspector.scrapers.base import ScraperError
from agente_inspector.scrapers.sti_scraper import (
    StiScraper,
    formatear_numero_container,
    limpiar_mensaje_html,
)


def test_formatear_numero_container():
    assert formatear_numero_container("IKSU5203585") == "IKSU 520358-5"


def test_limpiar_mensaje_html():
    mensaje = "<strong>RECEPCION<br>IKSU 520358-5</strong><br>(26) Fuera de Plazo para esta carga. "
    assert limpiar_mensaje_html(mensaje) == "RECEPCION IKSU 520358-5 (26) Fuera de Plazo para esta carga."


class _FakeResponse:
    def __init__(self, payload: dict):
        self._payload = payload

    async def json(self) -> dict:
        return self._payload


class _FakeResponseInfo:
    def __init__(self, response: _FakeResponse):
        self._response = response

    @property
    async def value(self) -> _FakeResponse:
        return self._response


class _FakeExpectResponseCtx:
    def __init__(self, response: _FakeResponse):
        self._info = _FakeResponseInfo(response)

    async def __aenter__(self) -> _FakeResponseInfo:
        return self._info

    async def __aexit__(self, *args):
        return False


class _FakePage:
    def __init__(self, response: _FakeResponse):
        self._response = response
        self.goto = AsyncMock()
        self.check = AsyncMock()
        self.fill = AsyncMock()
        self.click = AsyncMock()

    def expect_response(self, predicate, timeout):
        return _FakeExpectResponseCtx(self._response)


class _FakeBrowser:
    def __init__(self, page: _FakePage):
        self._page = page
        self.close = AsyncMock()

    async def new_page(self) -> _FakePage:
        return self._page


class _FakeChromium:
    def __init__(self, browser: _FakeBrowser):
        self._browser = browser

    async def launch(self, headless: bool) -> _FakeBrowser:
        return self._browser


class _FakePlaywright:
    def __init__(self, browser: _FakeBrowser):
        self.chromium = _FakeChromium(browser)


class _FakeAsyncPlaywrightCtx:
    def __init__(self, response: _FakeResponse):
        page = _FakePage(response)
        browser = _FakeBrowser(page)
        self._playwright = _FakePlaywright(browser)
        self.page = page
        self.browser = browser

    async def __aenter__(self) -> _FakePlaywright:
        return self._playwright

    async def __aexit__(self, *args):
        return False


async def test_consultar_estado_devuelve_mensaje_limpio():
    payload = {
        "success": "",
        "message": "<strong>RECEPCION<br>IKSU 520358-5</strong><br> (26) Fuera de Plazo para esta carga. ",
        "codigo": "-1",
    }
    fake_ctx = _FakeAsyncPlaywrightCtx(_FakeResponse(payload))

    with patch("agente_inspector.scrapers.sti_scraper.async_playwright", return_value=fake_ctx):
        resultado = await StiScraper().consultar_estado("IKSU5203585", {})

    assert resultado.scraper_source == "SCRAPING"
    assert resultado.port_status == "RECEPCION IKSU 520358-5 (26) Fuera de Plazo para esta carga."
    assert resultado.real_arrival_date is None
    fake_ctx.page.check.assert_awaited_once_with("#tipo_cnt_recepcion")
    fake_ctx.page.fill.assert_awaited_once_with("#contenedor", "IKSU 520358-5")
    fake_ctx.browser.close.assert_awaited_once()


async def test_consultar_estado_usa_tipo_cnt_de_terminal_config():
    fake_ctx = _FakeAsyncPlaywrightCtx(_FakeResponse({"message": "ok"}))

    with patch("agente_inspector.scrapers.sti_scraper.async_playwright", return_value=fake_ctx):
        await StiScraper().consultar_estado("IKSU5203585", {"tipo_cnt": "DESPACHO"})

    fake_ctx.page.check.assert_awaited_once_with("#tipo_cnt_despacho")


async def test_consultar_estado_cuando_falla_navegacion_lanza_scraper_error():
    with patch("agente_inspector.scrapers.sti_scraper.async_playwright", side_effect=RuntimeError("timeout")):
        with pytest.raises(ScraperError):
            await StiScraper().consultar_estado("IKSU5203585", {})
