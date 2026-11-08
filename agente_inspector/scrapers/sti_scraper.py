import logging
import re
from datetime import datetime, timezone

from playwright.async_api import Page, async_playwright
from tenacity import retry, stop_after_attempt, wait_exponential

from agente_inspector.scrapers.base import ITerminalScraper, ResultadoScraping, ScraperError

logger = logging.getLogger("portia.agente_inspector")

STI_URL = "https://www.stiport.com/movil/sti_en_linead/consulta_cnt/index.php"
TIMEOUT_MS = 15000


def formatear_numero_container(container_number: str) -> str:
    letras = container_number[:4]
    digitos = container_number[4:]
    return f"{letras} {digitos[:6]}-{digitos[6:]}"


def limpiar_mensaje_html(mensaje_html: str) -> str:
    sin_tags = re.sub(r"<[^>]+>", " ", mensaje_html)
    return re.sub(r"\s+", " ", sin_tags).strip()


def detectar_estado_contenedor(
    mensaje_recepcion: str, data_recepcion: dict, mensaje_despacho: str, data_despacho: dict
) -> str | None:
    # Sin un caso real de contenedor retirado/despachado contra el cual validar, se evita adivinar.
    return None


class StiScraper(ITerminalScraper):
    async def consultar_estado(self, container_number: str, terminal_config: dict) -> ResultadoScraping:
        numero_formateado = formatear_numero_container(container_number)

        try:
            data_recepcion, data_despacho = await self._consultar_ambos_modos(numero_formateado)
        except Exception as exc:
            logger.warning(f"StiScraper falló para container={container_number}: {exc}")
            raise ScraperError(str(exc)) from exc

        mensaje_recepcion = limpiar_mensaje_html(data_recepcion.get("message", ""))
        mensaje_despacho = limpiar_mensaje_html(data_despacho.get("message", ""))
        logger.info(
            f"StiScraper OK para container={container_number}: "
            f"recepcion='{mensaje_recepcion}' despacho='{mensaje_despacho}'"
        )

        return ResultadoScraping(
            port_status=mensaje_recepcion,
            real_arrival_date=None,
            scraper_source="SCRAPING",
            scraped_at=datetime.now(timezone.utc),
            estado_contenedor=detectar_estado_contenedor(
                mensaje_recepcion, data_recepcion, mensaje_despacho, data_despacho
            ),
        )

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=2), reraise=True)
    async def _consultar_ambos_modos(self, numero_formateado: str) -> tuple[dict, dict]:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            try:
                page = await browser.new_page()

                data_recepcion = await self._consultar_modo(page, "recepcion", numero_formateado)
                data_despacho = await self._consultar_modo(page, "despacho", numero_formateado)

                return data_recepcion, data_despacho
            finally:
                await browser.close()

    async def _consultar_modo(self, page: Page, tipo_cnt: str, numero_formateado: str) -> dict:
        await page.goto(STI_URL, timeout=TIMEOUT_MS)
        await page.check(f"#tipo_cnt_{tipo_cnt}")
        await page.fill("#contenedor", numero_formateado)

        async with page.expect_response(
            lambda response: "json_consulta_cnt.php" in response.url, timeout=TIMEOUT_MS
        ) as response_info:
            await page.click("#btn_consultar")

        response = await response_info.value
        return await response.json()
