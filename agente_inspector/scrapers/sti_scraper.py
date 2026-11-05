import logging
import re
from datetime import datetime, timezone

from playwright.async_api import async_playwright

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


class StiScraper(ITerminalScraper):
    async def consultar_estado(self, container_number: str, terminal_config: dict) -> ResultadoScraping:
        tipo_cnt = terminal_config.get("tipo_cnt", "RECEPCION")
        numero_formateado = formatear_numero_container(container_number)

        try:
            data = await self._consultar_en_navegador(tipo_cnt, numero_formateado)
        except Exception as exc:
            logger.warning(f"StiScraper falló para container={container_number}: {exc}")
            raise ScraperError(str(exc)) from exc

        mensaje = limpiar_mensaje_html(data.get("message", ""))
        logger.info(f"StiScraper OK para container={container_number}: {mensaje}")

        return ResultadoScraping(
            port_status=mensaje,
            real_arrival_date=None,
            scraper_source="SCRAPING",
            scraped_at=datetime.now(timezone.utc),
        )

    async def _consultar_en_navegador(self, tipo_cnt: str, numero_formateado: str) -> dict:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            try:
                page = await browser.new_page()
                await page.goto(STI_URL, timeout=TIMEOUT_MS)

                await page.check(f"#tipo_cnt_{tipo_cnt.lower()}")
                await page.fill("#contenedor", numero_formateado)

                async with page.expect_response(
                    lambda response: "json_consulta_cnt.php" in response.url, timeout=TIMEOUT_MS
                ) as response_info:
                    await page.click("#btn_consultar")

                response = await response_info.value
                return await response.json()
            finally:
                await browser.close()
