import json
from pathlib import Path

import pytest

from buscador.conectores import ErroConector, FonteNaoConfigurada, criar_conector
from buscador.conectores.base import (
    RegistroContrato,
    RegistroEmpresa,
    RegistroEstabelecimento,
    RegistroSocio,
)
from buscador.conectores.cnpj_arquivo import ConectorCnpjArquivo, converter_capital
from buscador.conectores.pncp import ConectorPncp, TransporteSimulado
from buscador.conectores.portal_transparencia import ConectorPortalTransparencia
from buscador.modelos import Caso
from tests.conftest import FIXTURES_CNPJ, FIXTURES_PNCP


@pytest.fixture
def registrador(sessao, custodia):
    caso = Caso(titulo="c", criado_por="t")
    sessao.add(caso)
    sessao.flush()
    capturas = []

    def registrar(fonte, url_ou_arquivo, conteudo, metadados):
        c = custodia.registrar(
            sessao,
            caso_id=caso.id,
            fonte=fonte,
            url_ou_arquivo=url_ou_arquivo,
            conteudo=conteudo,
            metadados=metadados,
        )
        capturas.append(c)
        return c

    registrar.capturas = capturas
    return registrar


# ---- CNPJ -----------------------------------------------------------------------------------


def test_cnpj_arquivo_le_as_tres_tabelas_no_layout_oficial(registrador):
    resultado = ConectorCnpjArquivo(dir_base=FIXTURES_CNPJ.parent).coletar(
        {"diretorio": FIXTURES_CNPJ.name}, registrador
    )
    assert len(resultado.capturas) == 3
    empresas = [r for r in resultado.registros if isinstance(r, RegistroEmpresa)]
    estab = [r for r in resultado.registros if isinstance(r, RegistroEstabelecimento)]
    socios = [r for r in resultado.registros if isinstance(r, RegistroSocio)]
    assert len(empresas) == 15
    assert len(estab) == 16  # 15 matrizes + 1 filial
    assert len(socios) == 27

    alfa = next(e for e in empresas if e.cnpj_basico == "90000001")
    assert alfa.razao_social == "ALFA CONSTRUCOES SINTETICA LTDA"
    assert alfa.capital_social == 500000.0
    assert alfa.captura_id in {c.id for c in resultado.capturas}

    matriz = next(e for e in estab if e.cnpj_basico == "90000001")
    assert matriz.cnpj_completo == "90000001000129"
    assert matriz.data_inicio_atividade == "20150310"
    assert (matriz.tipo_logradouro, matriz.logradouro, matriz.numero) == (
        "RUA",
        "DAS AMOSTRAS",
        "100",
    )
    assert matriz.correio_eletronico == "contato@exemplo.invalid"

    socio = next(s for s in socios if s.nome_socio == "JOAO DA SILVA EXEMPLO")
    assert socio.cnpj_cpf_socio == "***111111**"
    assert socio.identificador_socio == "2"
    estrangeiro = next(s for s in socios if s.identificador_socio == "3")
    assert estrangeiro.cnpj_cpf_socio == ""
    assert estrangeiro.pais == "249"

    metadados = registrador.capturas[0].metadados
    assert metadados["codificacao"] == "latin-1" and metadados["separador"] == ";"


def test_cnpj_arquivo_decodifica_latin1(tmp_path: Path, registrador):
    linha = '"90000050";"AÇÃO E ÇÃO SINTÉTICA LTDA";"2062";"49";"1.234,56";"01";""\r\n'
    (tmp_path / "x.EMPRECSV").write_bytes(linha.encode("latin-1"))
    resultado = ConectorCnpjArquivo(dir_base=tmp_path).coletar({"diretorio": "."}, registrador)
    empresa = resultado.registros[0]
    assert empresa.razao_social == "AÇÃO E ÇÃO SINTÉTICA LTDA"
    assert empresa.capital_social == 1234.56


def test_converter_capital():
    assert converter_capital("1000,00") == 1000.0
    assert converter_capital("") is None
    assert converter_capital("abc") is None


def test_cnpj_arquivo_erros(tmp_path: Path, registrador):
    with pytest.raises(ErroConector):
        ConectorCnpjArquivo(dir_base=tmp_path).coletar({}, registrador)
    with pytest.raises(ErroConector):
        ConectorCnpjArquivo(dir_base=tmp_path).coletar({"diretorio": "nao-existe"}, registrador)
    with pytest.raises(ErroConector):
        ConectorCnpjArquivo(dir_base=tmp_path).coletar({"diretorio": "."}, registrador)


# ---- PNCP -----------------------------------------------------------------------------------


def _conector(transporte, **kw) -> ConectorPncp:
    return ConectorPncp(
        transporte=transporte, espera_retentativa_segundos=0, dormir=lambda _s: None, **kw
    )


def test_pncp_pagina_e_registra_cada_resposta(registrador, transporte_pncp):
    resultado = _conector(transporte_pncp).coletar(
        {"dataInicial": "20260101", "dataFinal": "20261231", "max_paginas": 5}, registrador
    )
    assert len(resultado.capturas) == 2
    assert len(resultado.registros) == 9
    assert all(isinstance(r, RegistroContrato) for r in resultado.registros)
    assert resultado.avisos == []
    captura = resultado.capturas[0]
    assert captura.metadados["http_status"] == 200
    assert captura.metadados["parametros_consulta"]["pagina"] == 1
    assert captura.metadados["cabecalhos"]["content-type"] == "application/json"
    # Transporte simulado: a origem nunca aparece como https://pncp.gov.br.
    assert captura.url_ou_arquivo.startswith("simulado://pncp.gov.br/api/consulta/v1/contratos?")
    assert captura.metadados["simulado"] is True
    assert "dataInicial=20260101" in captura.url_ou_arquivo
    # Bytes guardados são exatamente os da fixture.
    assert json.loads(open(FIXTURES_PNCP / "contratos_pagina1.json", "rb").read()) == json.loads(
        transporte_pncp.paginas[1]
    )
    contrato = resultado.registros[0]
    assert contrato.ni_fornecedor == "90000001000129"
    assert contrato.orgao_cnpj == "90100001000109"
    assert contrato.valor_global == 200000.0
    assert contrato.data_assinatura == "2026-03-15"


def test_pncp_respeita_max_paginas(registrador, transporte_pncp):
    resultado = _conector(transporte_pncp).coletar(
        {"dataInicial": "20260101", "dataFinal": "20261231", "max_paginas": 1}, registrador
    )
    assert len(resultado.capturas) == 1
    assert len(resultado.registros) == 4
    assert any("max_paginas" in a for a in resultado.avisos)


def test_pncp_filtra_fornecedor_depois_da_coleta(registrador, transporte_pncp):
    resultado = _conector(transporte_pncp).coletar(
        {"dataInicial": "20260101", "dataFinal": "20261231", "cnpjFornecedor": "90000002"},
        registrador,
    )
    assert len(resultado.capturas) == 2  # as páginas inteiras continuam na custódia
    assert [r.ni_fornecedor[:8] for r in resultado.registros] == ["90000002"]


def test_pncp_retenta_em_503(registrador):
    transporte = TransporteSimulado.de_diretorio(FIXTURES_PNCP, falhas_iniciais=2)
    resultado = _conector(transporte, tentativas=3).coletar(
        {"dataInicial": "20260101", "dataFinal": "20261231", "max_paginas": 1}, registrador
    )
    assert len(resultado.registros) == 4
    assert len(transporte.chamadas) == 3


def test_pncp_desiste_apos_tentativas_e_guarda_a_resposta_com_erro(registrador):
    transporte = TransporteSimulado.de_diretorio(FIXTURES_PNCP, falhas_iniciais=5)
    with pytest.raises(ErroConector, match="HTTP 503"):
        _conector(transporte, tentativas=3).coletar(
            {"dataInicial": "20260101", "dataFinal": "20261231"}, registrador
        )
    assert len(transporte.chamadas) == 3
    # A última resposta (com erro) fica na custódia, com o status registrado.
    assert registrador.capturas[-1].metadados["http_status"] == 503


def test_pncp_erro_de_transporte_apos_tentativas(registrador):
    import httpx

    class TransporteQueFalha(httpx.BaseTransport):
        def handle_request(self, request):
            raise httpx.ConnectError("sem rede", request=request)

    with pytest.raises(ErroConector, match="3 tentativas"):
        _conector(TransporteQueFalha(), tentativas=3).coletar(
            {"dataInicial": "20260101", "dataFinal": "20261231"}, registrador
        )


def test_pncp_sem_dados_responde_204(registrador):
    transporte = TransporteSimulado(paginas={})
    resultado = _conector(transporte).coletar(
        {"dataInicial": "20260101", "dataFinal": "20261231"}, registrador
    )
    assert resultado.registros == []
    assert len(resultado.capturas) == 1
    assert resultado.capturas[0].metadados["http_status"] == 204


def test_pncp_valida_datas_e_orgao(registrador, transporte_pncp):
    with pytest.raises(ErroConector, match="dataInicial"):
        _conector(transporte_pncp).coletar({"dataFinal": "20261231"}, registrador)
    with pytest.raises(ErroConector, match="dataFinal"):
        _conector(transporte_pncp).coletar(
            {"dataInicial": "20260101", "dataFinal": "2026"}, registrador
        )
    _conector(transporte_pncp).coletar(
        {
            "dataInicial": "20260101",
            "dataFinal": "20261231",
            "cnpjOrgao": "90.100.001/0001-09",
            "max_paginas": 1,
        },
        registrador,
    )
    assert transporte_pncp.chamadas[-1].url.params["cnpjOrgao"] == "90100001000109"


# ---- Portal da Transparência (stub) e fábrica -------------------------------------------------


def test_portal_transparencia_exige_chave(monkeypatch, registrador):
    monkeypatch.delenv("PORTAL_TRANSPARENCIA_CHAVE", raising=False)
    parametros = {"recurso": "ceis", "codigoSancionado": ["90000001000129"]}
    # Sem chave: "não configurado" (um ErroConector específico), sem nenhuma requisição.
    with pytest.raises(FonteNaoConfigurada, match="PORTAL_TRANSPARENCIA_CHAVE"):
        ConectorPortalTransparencia().coletar(parametros, registrador)
    # Parâmetros inválidos são recusados antes de olhar a chave.
    with pytest.raises(ErroConector, match="recurso"):
        ConectorPortalTransparencia().coletar({}, registrador)


def test_fabrica_de_conectores(config):
    assert isinstance(criar_conector("cnpj_arquivo", config), ConectorCnpjArquivo)
    assert isinstance(criar_conector("pncp", config), ConectorPncp)
    assert isinstance(criar_conector("portal_transparencia", config), ConectorPortalTransparencia)
    with pytest.raises(ErroConector):
        criar_conector("inexistente", config)
