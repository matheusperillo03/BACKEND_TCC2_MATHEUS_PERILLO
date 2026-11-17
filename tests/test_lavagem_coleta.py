"""Tipologias de lavagem que exigem coleta pequena: L6 (tabela Simples do CNPJ), L10 nas partes
CEAF e CEPIM e L16 (convênios e CEPIM do Portal da Transparência). Cada regra tem caso positivo,
negativo e de limite. Só dados sintéticos e nenhuma chamada de rede (transporte simulado)."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from buscador.api import criar_app
from buscador.conectores import ErroConector
from buscador.conectores.cnpj_arquivo import ConectorCnpjArquivo, classificar_arquivo
from buscador.conectores.portal_transparencia import ConectorPortalTransparencia
from buscador.conectores.simulados import transporte_portal
from buscador.config import ErroConfiguracao
from buscador.tipologias import legislacao
from tests.apoio_lavagem import (
    CNPJ_ORGAO,
    Cenario,
    cnpj,
    convenio,
    expulsao,
    simples,
    verificar_explicacao,
)
from tests.conftest import cabecalhos, corpo_caso, criar_config, incluir_equipe
from tests.sinteticos import cpf_sintetico, mascarado, mascarado_pontuado


@pytest.fixture
def cenario(sessao, custodia, config) -> Cenario:
    return Cenario(sessao, custodia, config)


# ==== L6: porte ou MEI incompatível com o valor contratado ======================================


def test_rf04_l6_limites_por_ano_vem_da_lei_e_a_configuracao_e_validada(tmp_path):
    limites = legislacao.limites_de_receita()
    assert {a: v["anual"] for a, v in limites["mei"].items()} == {
        "2026": 81_000.0,
        "2027": 110_000.0,
        "2028": 140_000.0,
    }
    assert [v["mensal_inicio"] for v in limites["mei"].values()] == [6750.0, 9166.67, 11666.67]
    assert (limites["me"]["anual"], limites["epp"]["anual"]) == (360_000.0, 4_800_000.0)
    assert "LC nº 123/2006" in limites["mei"]["2026"]["fonte"]
    assert "LC nº 238/2026" in limites["mei"]["2027"]["fonte"]
    # Antes de 2026 vale a linha de 2026; depois de 2028, a de 2028.
    assert legislacao.limite_mei_do_ano(limites, 2020)[0] == "2026"
    assert legislacao.limite_mei_do_ano(limites, 2027)[0] == "2027"
    assert legislacao.limite_mei_do_ano(limites, 2035)[0] == "2028"
    extra = legislacao.limites_de_receita('{"2030": {"anual": 150000, "mensal_inicio": 12500}}')
    assert extra["mei"]["2030"]["anual"] == 150_000.0
    for invalido in ("{", "[]", '{"30": {}}', '{"2030": {"anual": -1, "mensal_inicio": 1}}'):
        with pytest.raises(ErroConfiguracao):
            criar_config(tmp_path, l6_limites_mei=invalido).validar()


def _mei(cenario: Cenario, valor: float, ano: str = "2026", inicio: str = "20200101", **kw):
    cenario.empresa("92400001", "MEI SINTETICO 12345678", inicio=inicio, porte="01")
    simples(cenario, "92400001", opcao_mei=inicio, **kw)
    return cenario.contrato("92400001", f"{ano}-06-01", valor)


def test_rf04_l6_dispara_para_mei_com_contratos_acima_do_limite_do_ano(cenario):
    numero = _mei(cenario, 120_000.0)
    (alerta,) = cenario.alertas("L6")
    verificar_explicacao(alerta, cenario.sessao)
    p = alerta.parametros
    assert (p["regime"], p["ano"], p["soma_contratada"]) == ("mei", 2026, 120_000.0)
    # Limite legal de 2026 (R$ 81 mil); com a tolerância, a comparação usa o de 2027 (R$ 110 mil).
    assert (p["limite_legal"], p["limite_aplicado"], p["ano_da_tolerancia"]) == (
        81_000.0,
        110_000.0,
        2027,
    )
    assert "LC nº 123/2006" in p["fonte_limite"]
    assert numero in alerta.motivo and "R$ 120.000,00" in alerta.motivo
    assert "R$ 81.000,00" in alerta.motivo and "tolerância da transição" in alerta.motivo
    assert "não é receita do ano" in alerta.motivo and "não conclusão" in alerta.motivo
    assert {e["fonte"] for e in alerta.evidencias} == {"cnpj_arquivo", "pncp"}


def test_rf04_l6_limite_faixa_de_tolerancia_entre_os_limites_de_anos_vizinhos(cenario):
    # R$ 100 mil em 2026: acima de R$ 81 mil (2026), dentro de R$ 110 mil (2027).
    _mei(cenario, 100_000.0)
    assert cenario.alertas("L6") == []
    (alerta,) = cenario.alertas("L6", l6_tolerancia_transicao=False)
    assert alerta.parametros["limite_aplicado"] == 81_000.0
    assert alerta.parametros["ano_da_tolerancia"] is None


def test_rf04_l6_limite_valor_igual_ao_limite_nao_dispara_e_2028_usa_140_mil(cenario):
    _mei(cenario, 140_000.0, ano="2028")
    assert cenario.alertas("L6") == []
    cenario.contrato("92400001", "2028-07-01", 0.01)
    (alerta,) = cenario.alertas("L6")
    assert alerta.parametros["limite_legal"] == 140_000.0 and alerta.parametros["ano"] == 2028


def test_rf04_l6_no_ano_de_inicio_o_limite_do_mei_e_proporcional_aos_meses(cenario):
    # Início em outubro de 2028: 3 meses x R$ 11.666,67 = R$ 35.000,01.
    cenario.empresa("92400001", "MEI SINTETICO NOVO", inicio="20281015", porte="01")
    simples(cenario, "92400001", opcao_mei="20281015")
    cenario.contrato("92400001", "2028-11-01", 35_000.0)
    assert cenario.alertas("L6") == []
    cenario.contrato("92400001", "2028-12-01", 10.0)
    (alerta,) = cenario.alertas("L6")
    assert alerta.parametros["meses_no_ano_de_inicio"] == 3
    assert alerta.parametros["limite_legal"] == 35_000.01
    assert "proporcional a 3 meses" in alerta.motivo


def test_rf04_l6_so_conta_como_mei_o_contrato_assinado_enquanto_a_opcao_valia(cenario):
    # Excluída do MEI em 01/03/2026; o contrato é de junho: vale o porte (01, microempresa).
    _mei(cenario, 120_000.0, exclusao_mei="20260301")
    assert cenario.alertas("L6") == []
    cenario.contrato("92400001", "2026-07-01", 250_000.0)
    (alerta,) = cenario.alertas("L6")
    assert alerta.parametros["regime"] == "me" and alerta.parametros["limite_legal"] == 360_000.0
    assert "porte ATUAL" in alerta.motivo


def test_rf04_l6_porte_me_e_epp_usam_o_limite_anual_e_porte_demais_nao_dispara(cenario):
    cenario.empresa("92400002", "PEQUENO PORTE SINTETICA LTDA", porte="03")
    cenario.contrato("92400002", "2026-02-01", 4_800_000.0)
    cenario.empresa("92400003", "DEMAIS SINTETICA SA", porte="05", numero="3")
    cenario.contrato("92400003", "2026-02-01", 90_000_000.0)
    assert cenario.alertas("L6") == []
    cenario.contrato("92400002", "2026-03-01", 1.0)
    (alerta,) = cenario.alertas("L6")
    assert alerta.parametros["regime"] == "epp" and "pequeno porte" in alerta.titulo
    # Contratos de anos diferentes não se somam.
    cenario.empresa("92400004", "MICRO SINTETICA LTDA", porte="01", numero="4")
    cenario.contrato("92400004", "2025-12-31", 200_000.0)
    cenario.contrato("92400004", "2026-01-01", 200_000.0)
    assert len(cenario.alertas("L6")) == 1


def test_rf01_cnpj_arquivo_le_a_tabela_simples_no_layout_oficial(tmp_path, cliente, caso_id):
    assert classificar_arquivo("F.K03200$W.SIMPLES.CSV.D61114") == "simples"
    capturas = []

    def registrar(fonte, origem, conteudo, metadados):
        from types import SimpleNamespace

        capturas.append(metadados)
        conteudo.read()
        return SimpleNamespace(id=f"cap{len(capturas)}", url_ou_arquivo=origem)

    pasta = tmp_path / "simples"
    pasta.mkdir()
    (pasta / "SINTETICO.txt").write_text("dados sintéticos", encoding="utf-8")
    (pasta / "TESTE.SIMPLES.CSV").write_bytes(
        b'"92400001";"S";"20200101";"00000000";"S";"20200101";"20260301"\r\n'
    )
    resultado = ConectorCnpjArquivo(dir_base=tmp_path).coletar({"diretorio": "simples"}, registrar)
    (registro,) = resultado.registros
    assert (registro.cnpj_basico, registro.opcao_mei) == ("92400001", "S")
    assert (registro.data_opcao_mei, registro.data_exclusao_mei) == ("20200101", "20260301")
    assert capturas[0]["tabela"] == "simples" and capturas[0]["simulado"] is True
    brutos = ConectorCnpjArquivo.registros_brutos(
        (pasta / "TESTE.SIMPLES.CSV").read_bytes(), {"tabela": "simples"}
    )
    assert brutos["92400001"]["data_exclusao_mei"] == "20260301"


# ==== Portal da Transparência: CEPIM, CEAF e convênios ==========================================

ENTIDADE = "92500001"
CHAVE = "chave-sintetica-do-portal"


def _item_cepim(basico: str = ENTIDADE) -> dict:
    documento = cnpj(basico)
    return {
        "id": 77,
        "dataReferencia": "10/11/2026",
        "motivo": "Omissão sintética no dever de prestar contas",
        "orgaoSuperior": {"nome": "MINISTERIO SINTETICO", "cnpj": CNPJ_ORGAO},
        "pessoaJuridica": {
            "cnpjFormatado": f"{documento[:2]}.{documento[2:5]}.{documento[5:8]}/"
            f"{documento[8:12]}-{documento[12:]}",
            "razaoSocialReceita": "INSTITUTO SINTETICO DE EXEMPLO",
        },
        "convenio": {"numero": "SINT 001/2025"},
    }


def _item_convenio(basico: str = ENTIDADE, identificador: int = 501) -> dict:
    documento = cnpj(basico)
    return {
        "id": identificador,
        "dataInicioVigencia": "2026-03-01",
        "dataFinalVigencia": "2027-12-31",
        "dimConvenio": {
            "codigo": "900001",
            "numero": "SINT 900/2026",
            "objeto": "Objeto sintético",
        },
        "situacao": "EM EXECUÇÃO",
        "convenente": {
            "cnpjFormatado": f"{documento[:2]}.{documento[2:5]}.{documento[5:8]}/"
            f"{documento[8:12]}-{documento[12:]}",
            "nome": "INSTITUTO SINTETICO DE EXEMPLO",
        },
        "orgao": {"nome": "MINISTERIO SINTETICO", "cnpj": CNPJ_ORGAO},
        "valor": 500000.0,
        "valorLiberado": 250000.0,
    }


def _item_ceaf(nome: str, cpf_mascarado: str, identificador: int = 9001) -> dict:
    return {
        "id": identificador,
        "dataPublicacao": "10/05/2024",
        "punicao": {
            "cpfPunidoFormatado": cpf_mascarado,
            "nomePunido": nome,
            "portaria": "123",
            "processo": "00000.000000/2024-00",
        },
        "tipoPunicao": {"descricao": "Demissão"},
        "pessoa": {"nome": nome},
        "orgaoLotacao": {"nome": "ORGAO SINTETICO"},
    }


class _Registrador:
    def __init__(self) -> None:
        self.capturas: list = []

    def __call__(self, fonte, origem, conteudo, metadados):
        from types import SimpleNamespace

        captura = SimpleNamespace(
            id=f"cap{len(self.capturas) + 1}", url_ou_arquivo=origem, metadados=metadados
        )
        self.capturas.append(captura)
        return captura


def _portal(**bases) -> ConectorPortalTransparencia:
    return ConectorPortalTransparencia(
        chave=CHAVE, transporte=transporte_portal(**bases), dormir=lambda _s: None
    )


def test_rf01_portal_coleta_cepim_e_convenios_por_cnpj_e_confere_o_cnpj_de_cada_item():
    alvo = cnpj(ENTIDADE)
    conector = _portal(
        cepim={alvo: [_item_cepim(), _item_cepim("92599999")]},
        convenios={alvo: [_item_convenio(), _item_convenio("92599999", 502)]},
    )
    registrador = _Registrador()
    cepim = conector.coletar({"recurso": "cepim", "codigoSancionado": alvo}, registrador)
    (registro,) = cepim.registros
    assert (registro.cadastro, registro.cnpj_sancionado) == ("CEPIM", alvo)
    # O CEPIM não tem período: só a data de referência.
    assert (registro.data_inicio, registro.data_fim) == ("", "")
    assert registro.data_referencia == "2026-11-10" and "prestar contas" in registro.tipo_sancao
    assert any("CNPJ diferente" in aviso for aviso in cepim.avisos)
    convenios = conector.coletar({"recurso": "convenios", "codigoSancionado": [alvo]}, registrador)
    (convenio_,) = convenios.registros
    assert (convenio_.cnpj_convenente, convenio_.orgao_cnpj) == (alvo, CNPJ_ORGAO)
    assert (convenio_.data_inicio_vigencia, convenio_.valor) == ("2026-03-01", 500000.0)
    # A chave vai só no cabeçalho; a consulta de convênios usa o filtro "convenente".
    consultas = [c.metadados["parametros_consulta"] for c in registrador.capturas]
    assert {"convenente": alvo, "pagina": 1} in consultas
    assert CHAVE not in json.dumps([c.metadados for c in registrador.capturas])
    assert all(c.url_ou_arquivo.startswith("simulado://") for c in registrador.capturas)
    with pytest.raises(ErroConector, match="CNPJ de 14 posições"):
        conector.coletar({"recurso": "convenios"}, registrador)


def test_rnf04_portal_consulta_o_ceaf_por_nome_e_nunca_por_cpf():
    completo = cpf_sintetico()
    conector = _portal(
        ceaf={
            "EXPULSO SINTETICO": [
                _item_ceaf("EXPULSO SINTETICO", mascarado_pontuado(completo)),
                _item_ceaf("EXPULSO SINTETICO JUNIOR", "***.111.222-**", 9002),
            ]
        }
    )
    registrador = _Registrador()
    resultado = conector.coletar(
        {"recurso": "ceaf", "nomeSancionado": "EXPULSO SINTETICO"}, registrador
    )
    (registro,) = resultado.registros  # o nome parecido, que não foi consultado, não entra
    assert registro.nome == "EXPULSO SINTETICO" and registro.data_publicacao == "2024-05-10"
    assert registro.cpf == mascarado_pontuado(completo) and completo not in registro.cpf
    assert any("nome diferente" in aviso for aviso in resultado.avisos)
    consultas = [c.metadados["parametros_consulta"] for c in registrador.capturas]
    assert all(set(c) == {"nomeSancionado", "pagina"} for c in consultas)
    with pytest.raises(ErroConector, match="consulta por CPF não é oferecida"):
        conector.coletar({"recurso": "ceaf"}, registrador)
    with pytest.raises(ErroConector, match="não é aceito"):
        conector.coletar({"recurso": "ceaf", "cpfSancionado": completo}, registrador)


# ==== L10: partes CEPIM e CEAF ==================================================================


def test_rf04_l10_dispara_para_socio_que_possivelmente_coincide_com_pessoa_do_ceaf(cenario):
    completo = cpf_sintetico()
    cenario.empresa("92600001", "CONTRATADA COM EXPULSO SINTETICA LTDA")
    cenario.socio("92600001", "EXPULSO SINTETICO", mascarado(completo), qualificacao="22")
    cenario.contrato("92600001", "2026-06-01")
    expulsao(cenario, "EXPULSO SINTETICO", mascarado_pontuado(completo))
    (alerta,) = cenario.alertas("L10")
    verificar_explicacao(alerta, cenario.sessao)
    assert alerta.parametros["parte"] == "pessoa expulsa (CEAF)"
    assert alerta.parametros["identidade_confirmada"] is False
    assert "POSSÍVEL COINCIDÊNCIA, não uma identificação" in alerta.motivo
    assert mascarado(completo) in alerta.motivo and completo not in alerta.motivo
    assert "CEAF" in alerta.motivo and "Demissão" in alerta.motivo
    # Sócio e pessoa expulsa continuam entidades separadas (RF02): sem fusão.
    assert len({e for e in alerta.entidades}) == 4


def test_rf04_l10_ceaf_limite_so_o_nome_ou_so_os_digitos_nao_disparam(cenario):
    completo = cpf_sintetico()
    outro = cpf_sintetico()
    while outro[3:9] == completo[3:9]:
        outro = cpf_sintetico()
    cenario.empresa("92600002", "CONTRATADA HOMONIMA SINTETICA LTDA")
    cenario.socio("92600002", "EXPULSO SINTETICO", mascarado(completo))
    cenario.socio("92600002", "OUTRO NOME SINTETICO", mascarado(outro))
    cenario.contrato("92600002", "2026-06-01")
    # Mesmo nome, dígitos diferentes; e mesmos dígitos, nome diferente.
    expulsao(cenario, "EXPULSO SINTETICO", mascarado_pontuado(outro), "1")
    expulsao(cenario, "TERCEIRO NOME SINTETICO", mascarado_pontuado(completo), "2")
    assert cenario.alertas("L10") == []
    # Sem contrato nem convênio, o sócio que coincide não gera alerta.
    cenario.empresa("92600003", "SEM CONTRATO SINTETICA LTDA", numero="3")
    cenario.socio("92600003", "TERCEIRO NOME SINTETICO", mascarado(completo))
    assert cenario.alertas("L10") == []


def test_rf04_l10_trata_o_cepim_como_impedimento_sem_periodo(cenario):
    cenario.empresa(
        "92600004", "ENTIDADE IMPEDIDA SINTETICA", natureza="3999", logradouro="DO CEPIM"
    )
    cenario.empresa(
        "92600005", "CONTRATADA VIZINHA SINTETICA LTDA", logradouro="DO CEPIM", complemento="SALA 2"
    )
    cenario.sancao("92600004", inicio="", fim="", cadastro="CEPIM")
    cenario.contrato("92600005", "2026-06-01")
    (alerta,) = cenario.alertas("L10")
    assert "CEPIM" in alerta.motivo and "sem período no cadastro" in alerta.motivo
    assert alerta.parametros["vinculo_por_endereco"] is True
    # O CEPIM não tem período e não é o cadastro da T6: a T6 não dispara para a própria entidade.
    cenario.contrato("92600004", "2026-06-01")
    assert cenario.alertas("T6") == []


# ==== L16: entidade sem fins lucrativos convenente ==============================================


def _entidade(cenario: Cenario, inicio: str = "20100101", natureza: str = "3999") -> None:
    cenario.empresa(
        ENTIDADE,
        "INSTITUTO SINTETICO DE EXEMPLO",
        inicio=inicio,
        natureza=natureza,
        logradouro="DO INSTITUTO",
        cnae="9430800",
    )
    cenario.socio(ENTIDADE, "PRESIDENTE SINTETICO", "***909090**", qualificacao="16")
    convenio(cenario, ENTIDADE)


def test_rf04_l16_sinal_a_entidade_aberta_pouco_antes_da_vigencia(cenario):
    _entidade(cenario, inicio="20251201")  # 90 dias antes de 01/03/2026
    (alerta,) = cenario.alertas("L16")
    verificar_explicacao(alerta, cenario.sessao)
    assert alerta.parametros["sinais"] == ["a"] and alerta.parametros["natureza_juridica"] == "3999"
    assert "(a) iniciou a atividade em 01/12/2025" in alerta.motivo and "90 dias" in alerta.motivo
    assert "dado bancário" in alerta.motivo and "não conclusão" in alerta.motivo


def test_rf04_l16_nao_dispara_sem_sinal_nem_para_empresa_com_fins_lucrativos(cenario):
    _entidade(cenario)
    assert cenario.alertas("L16") == []
    # Mesma situação da entidade recém-aberta, mas natureza de sociedade limitada (2062).
    cenario.empresa("92500002", "LIMITADA CONVENENTE SINTETICA LTDA", inicio="20260101", numero="2")
    convenio(cenario, "92500002", identificador="2")
    assert cenario.alertas("L16") == []


def test_rf04_l16_limite_dias_entre_abertura_e_vigencia(cenario):
    _entidade(cenario, inicio="20250902")  # exatamente 180 dias antes de 01/03/2026
    assert cenario.alertas("L16") == []
    assert len(cenario.alertas("L16", l16_dias_minimos=181)) == 1


def test_rf04_l16_sinal_b_registro_no_cepim(cenario):
    _entidade(cenario)
    cenario.sancao(ENTIDADE, inicio="", fim="", cadastro="CEPIM", identificador="77")
    (alerta,) = cenario.alertas("L16")
    assert alerta.parametros["sinais"] == ["b"] and "CEPIM" in alerta.motivo


def test_rf04_l16_sinal_c_dirigente_possivelmente_servidor_do_concedente(cenario):
    _entidade(cenario)
    cenario.servidor("PRESIDENTE SINTETICO", "***.909.090-**")
    (alerta,) = cenario.alertas("L16")
    assert alerta.parametros["sinais"] == ["c"]
    assert alerta.parametros["identidade_confirmada"] is False
    assert "PESSOA PROVÁVEL, não identificada" in alerta.motivo and "***909090**" in alerta.motivo
    assert "servidor de ORGAO SINTETICO" in alerta.motivo


def test_rf04_l16_sinal_c_limite_homonimo_com_outros_digitos_nao_dispara(cenario):
    _entidade(cenario)
    cenario.servidor("PRESIDENTE SINTETICO", "***.808.080-**")
    assert cenario.alertas("L16") == []


def test_rf04_l16_sinal_d_dirigente_socio_de_empresa_no_mesmo_endereco_ou_ramo(cenario):
    _entidade(cenario)
    cenario.empresa(
        "92500003",
        "FORNECEDORA DO INSTITUTO SINTETICA LTDA",
        logradouro="DO INSTITUTO",
        complemento="FUNDOS",
    )
    cenario.socio("92500003", "PRESIDENTE SINTETICO", "***909090**")
    # Empresa do dirigente sem relação (outro endereço, outro ramo, antiga): não conta.
    cenario.empresa("92500004", "ANTIGA DO DIRIGENTE SINTETICA LTDA", numero="44", cnae="4711301")
    cenario.socio("92500004", "PRESIDENTE SINTETICO", "***909090**")
    (alerta,) = cenario.alertas("L16")
    assert alerta.parametros["sinais"] == ["d"]
    assert "FORNECEDORA DO INSTITUTO SINTETICA LTDA" in alerta.motivo
    assert "mesmo endereço da entidade" in alerta.motivo
    assert "ANTIGA DO DIRIGENTE" not in alerta.motivo


# ==== Pela API: coleta com "do_caso" e execução =================================================


def test_rf01_coleta_do_caso_de_convenios_cepim_e_ceaf_e_alertas_l16_e_l10(tmp_path):
    raiz = tmp_path / "ingestao"
    pasta = raiz / "lavagem"
    pasta.mkdir(parents=True)
    (pasta / "SINTETICO.txt").write_text("dados sintéticos", encoding="utf-8")
    documento = cnpj(ENTIDADE)
    (pasta / "L.EMPRECSV").write_bytes(
        f'"{ENTIDADE}";"INSTITUTO SINTETICO DE EXEMPLO";"3999";"16";"0,00";"05";""\r\n'.encode()
    )
    (pasta / "L.ESTABELE").write_bytes(
        (
            f'"{ENTIDADE}";"0001";"{documento[12:]}";"1";"";"02";"20251201";"00";"";"";'
            '"20251201";"9430800";"";"RUA";"DO INSTITUTO";"1";"";"CENTRO";"70000900";"DF";'
            '"9701";"";"";"";"";"";"";"";"";""\r\n'
        ).encode()
    )
    completo = cpf_sintetico()
    (pasta / "L.SOCIOCSV").write_bytes(
        (
            f'"{ENTIDADE}";"2";"PRESIDENTE SINTETICO";"{mascarado(completo)}";"16";"20251201";"";'
            '"***000000**";"";"00";"5"\r\n'
        ).encode()
    )
    transporte = transporte_portal(
        cepim={documento: [_item_cepim()]},
        convenios={documento: [_item_convenio()]},
        ceaf={
            "PRESIDENTE SINTETICO": [
                _item_ceaf("PRESIDENTE SINTETICO", mascarado_pontuado(completo))
            ]
        },
    )
    config = criar_config(tmp_path, raiz, portal_transparencia_chave=CHAVE)
    extras = {"portal_transparencia": {"transporte": transporte, "dormir": lambda _s: None}}
    with TestClient(criar_app(config, extras_conectores=extras)) as cliente:
        caso = cliente.post("/casos", json=corpo_caso("Caso L16"), headers=cabecalhos()).json()[
            "id"
        ]
        incluir_equipe(cliente, caso)

        def coletar(fonte: str, parametros: dict) -> dict:
            r = cliente.post(
                f"/casos/{caso}/coletas",
                json={"fonte": fonte, "parametros": parametros},
                headers=cabecalhos(),
            )
            assert r.status_code == 200 and r.json()["situacao"] == "sucesso", r.text
            return r.json()

        coletar("cnpj_arquivo", {"diretorio": "lavagem"})
        for recurso in ("convenios", "cepim", "ceaf"):
            coletar("portal_transparencia", {"recurso": recurso, "do_caso": True})
        coletas = cliente.get(f"/casos/{caso}/coletas", headers=cabecalhos()).json()
        gravados = {c["parametros"].get("recurso"): c["parametros"] for c in coletas}
        # "do_caso" foi trocado pela lista explícita (a recoleta repete a mesma consulta).
        assert gravados["convenios"]["codigoSancionado"] == [documento]
        assert gravados["ceaf"]["nomeSancionado"] == ["PRESIDENTE SINTETICO"]
        entidades = cliente.get(f"/casos/{caso}/entidades", headers=cabecalhos()).json()
        assert {e["tipo"] for e in entidades} >= {
            "empresa",
            "pessoa",
            "convenio",
            "sancao",
            "orgao",
        }
        r = cliente.post(
            f"/casos/{caso}/alertas/executar",
            json={"tipologias": ["L16", "L10"]},
            headers=cabecalhos(),
        )
        assert r.status_code == 200, r.text
        por_tipologia = {a["tipologia"]: a for a in r.json()}
        assert por_tipologia["L16"]["parametros"]["sinais"] == ["a", "b"]
        assert por_tipologia["L10"]["parametros"]["parte"] == "pessoa expulsa (CEAF)"
        assert all(a["grupo"] == "lavagem" for a in por_tipologia.values())
        # RNF04: nenhum CPF completo em alertas, entidades ou relatório.
        relatorio = cliente.get(
            f"/casos/{caso}/relatorio", params={"formato": "md"}, headers=cabecalhos()
        ).text
        assert completo not in relatorio + json.dumps(r.json()) + json.dumps(entidades)
        assert "### [L16]" in relatorio and "### [L10]" in relatorio
