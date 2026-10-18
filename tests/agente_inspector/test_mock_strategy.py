from agente_inspector.scrapers.mock_strategy import MockStrategy


async def test_mock_es_determinista_para_el_mismo_container():
    mock = MockStrategy()

    resultado_1 = await mock.consultar_estado("MSCU1234567", {})
    resultado_2 = await mock.consultar_estado("MSCU1234567", {})

    assert resultado_1.port_status == resultado_2.port_status
    assert resultado_1.scraper_source == "MOCK"


async def test_mock_puede_variar_entre_containers_distintos():
    mock = MockStrategy()

    resultado_a = await mock.consultar_estado("MSCU1234567", {})
    resultado_b = await mock.consultar_estado("CMAU4455667", {})

    assert resultado_a.scraper_source == "MOCK"
    assert resultado_b.scraper_source == "MOCK"
