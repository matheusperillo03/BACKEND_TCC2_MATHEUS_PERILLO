import pytest

from buscador import tipologias
from buscador.tipologias.motor import formatar_data, formatar_moeda, formatar_numero


@pytest.fixture
def sessao_caso(app, caso_carregado):
    s = app.state.fabrica_sessoes()
    try:
        yield s, caso_carregado
    finally:
        s.close()


PADRAO = {"t1_dias_minimos": 180, "t2_minimo_empresas": 3, "t3_razao_minima": 10.0}


def _rotulos(sessao, alerta):
    from buscador.modelos import Entidade

    return {sessao.get(Entidade, eid).rotulo for eid in alerta.entidades}


def test_formatacao_em_portugues():
    assert formatar_moeda(1234567.5) == "R$ 1.234.567,50"
    assert formatar_moeda(None) == "valor não informado"
    assert formatar_data("2026-07-01") == "01/07/2026"
    assert formatar_numero(50.0) == "50,0"
    assert formatar_numero(1234.56) == "1.234,6"


def test_t1_dispara_para_empresa_recente_e_nao_para_antiga(sessao_caso):
    sessao, caso_id = sessao_caso
    alertas = tipologias.executar(sessao, caso_id, PADRAO, ["T1"])
    assert len(alertas) == 2
    por_empresa = {
        next(
            iter(
                _rotulos(sessao, a)
                & {"BETA ENGENHARIA SINTETICA LTDA", "THETA TECNOLOGIA SINTETICA LTDA"}
            )
        ): a
        for a in alertas
    }
    beta = por_empresa["BETA ENGENHARIA SINTETICA LTDA"]
    assert "52 dias depois" in beta.motivo and "limiar configurado é de 180 dias" in beta.motivo
    assert "10/05/2026" in beta.motivo and "01/07/2026" in beta.motivo
    # A severidade é da tipologia (tabela única de políticas), não do valor observado.
    assert beta.severidade == tipologias.POLITICAS["T1"].severidade == "media"
    assert beta.parametros["dias_observados"] == 52
    assert beta.parametros["limiares"] == PADRAO
    fontes = {e["fonte"] for e in beta.evidencias}
    assert fontes == {"cnpj_arquivo", "pncp"}
    assert all(
        len(e["sha256"]) == 64 and e["captura_id"] and e["url_ou_arquivo"] for e in beta.evidencias
    )
    # ALFA (2015) e LAMBDA (2021) não disparam.
    assert not any("ALFA" in r or "LAMBDA" in r for a in alertas for r in _rotulos(sessao, a))


def test_t1_respeita_o_limiar(sessao_caso):
    sessao, caso_id = sessao_caso
    assert tipologias.executar(sessao, caso_id, {**PADRAO, "t1_dias_minimos": 50}, ["T1"]) != []
    assert (
        len(tipologias.executar(sessao, caso_id, {**PADRAO, "t1_dias_minimos": 50}, ["T1"])) == 1
    )  # só THETA (45)
    assert tipologias.executar(sessao, caso_id, {**PADRAO, "t1_dias_minimos": 10}, ["T1"]) == []


def test_t2_dispara_so_com_empresa_contratada(sessao_caso):
    sessao, caso_id = sessao_caso
    alertas = tipologias.executar(sessao, caso_id, PADRAO, ["T2"])
    assert len(alertas) == 1  # grupo D; grupo J tem 3 empresas mas nenhuma contratada
    a = alertas[0]
    assert _rotulos(sessao, a) == {
        "DELTA SERVICOS SINTETICA LTDA",
        "EPSILON COMERCIO SINTETICA LTDA",
        "ZETA CONSULTORIA SINTETICA LTDA",
    }
    assert (
        "compartilhado por 3 empresas" in a.motivo and "DELTA SERVICOS SINTETICA LTDA" in a.motivo
    )
    assert a.parametros["empresas_contratadas"] == 1
    assert len([e for e in a.evidencias if e["fonte"] == "cnpj_arquivo"]) == 3
    assert len([e for e in a.evidencias if e["fonte"] == "pncp"]) == 1


def test_t2_respeita_o_limiar(sessao_caso):
    sessao, caso_id = sessao_caso
    assert tipologias.executar(sessao, caso_id, {**PADRAO, "t2_minimo_empresas": 4}, ["T2"]) == []
    assert (
        len(tipologias.executar(sessao, caso_id, {**PADRAO, "t2_minimo_empresas": 2}, ["T2"])) == 1
    )


def test_t3_dispara_para_capital_incompativel(sessao_caso):
    sessao, caso_id = sessao_caso
    alertas = tipologias.executar(sessao, caso_id, PADRAO, ["T3"])
    rotulos = {r for a in alertas for r in _rotulos(sessao, a)}
    assert (
        "GAMA LIMPEZA SINTETICA EIRELI" in rotulos and "THETA TECNOLOGIA SINTETICA LTDA" in rotulos
    )
    assert "ALFA CONSTRUCOES SINTETICA LTDA" not in rotulos  # 200000/500000 = 0,4
    gama = next(a for a in alertas if "GAMA" in a.motivo)
    assert (
        "R$ 10.000,00" in gama.motivo
        and "R$ 500.000,00" in gama.motivo
        and "50,0 vezes" in gama.motivo
    )
    assert gama.parametros["razao_observada"] == 50.0
    assert {e["fonte"] for e in gama.evidencias} == {"cnpj_arquivo", "pncp"}
    assert any("capital social" in e["descricao"] for e in gama.evidencias)


def test_t3_respeita_o_limiar_e_ignora_empresa_sem_capital(sessao_caso):
    sessao, caso_id = sessao_caso
    assert tipologias.executar(sessao, caso_id, {**PADRAO, "t3_razao_minima": 100}, ["T3"]) == []
    alertas = tipologias.executar(sessao, caso_id, {**PADRAO, "t3_razao_minima": 1.0}, ["T3"])
    rotulos = {r for a in alertas for r in _rotulos(sessao, a)}
    assert (
        "PI FORNECEDOR DESCONHECIDO SINTETICO LTDA" not in rotulos
    )  # sem capital social conhecido


def test_executar_substitui_alertas_anteriores(sessao_caso):
    sessao, caso_id = sessao_caso
    # Nas fixtures: sete alertas das T, dois da L5, dois da L6 e três de combinação.
    assert len(tipologias.executar(sessao, caso_id, PADRAO)) == 14
    assert len(tipologias.executar(sessao, caso_id, PADRAO, ["T2"])) == 1
    from sqlalchemy import select

    from buscador.modelos import Alerta

    # A execução parcial substitui só os alertas das tipologias pedidas; os demais ficam.
    alertas = list(sessao.execute(select(Alerta).where(Alerta.caso_id == caso_id)).scalars())
    assert len(alertas) == 14 and sum(a.tipologia == "T2" for a in alertas) == 1
    assert len(tipologias.executar(sessao, caso_id, PADRAO, ["T2", "T2"])) == 1


def test_parametros_efetivos_valida():
    assert tipologias.parametros_efetivos(PADRAO, {"t1_dias_minimos": 30}) == {
        **PADRAO,
        "t1_dias_minimos": 30,
    }
    with pytest.raises(ValueError):
        tipologias.parametros_efetivos(PADRAO, {"inexistente": 1})
    with pytest.raises(ValueError):
        tipologias.parametros_efetivos(PADRAO, {"t3_razao_minima": -1})


# ==== T2: só conta estabelecimento ATIVO (achado do piloto de 24/11/2026) =======================


@pytest.fixture
def cenario(sessao, custodia, config):
    from tests.apoio_lavagem import Cenario

    return Cenario(sessao, custodia, config)


def _tres_no_mesmo_endereco_por_filiais(cenario, situacao_da_terceira: str) -> str:
    """Três empresas sintéticas dividem um endereço: a contratada pela matriz e as outras duas por
    filial (as matrizes delas ficam em outros endereços). Devolve o CNPJ da filial da terceira."""
    cenario.empresa("91000001", "UM CONTRATADA SINTETICA LTDA", logradouro="DO PONTO COMUM")
    cenario.empresa("91000002", "DOIS FILIAL ATIVA SINTETICA LTDA", logradouro="DA MATRIZ DOIS")
    cenario.empresa("91000003", "TRES FILIAL SINTETICA LTDA", logradouro="DA MATRIZ TRES")
    cenario.filial("91000002", logradouro="DO PONTO COMUM")
    filial = cenario.filial("91000003", logradouro="DO PONTO COMUM", situacao=situacao_da_terceira)
    cenario.contrato("91000001", "2026-03-01")
    return filial


def test_t2_nao_conta_filial_baixada(cenario):
    # Sem a filial baixada sobram duas empresas ativas no endereço, abaixo do mínimo de três.
    _tres_no_mesmo_endereco_por_filiais(cenario, situacao_da_terceira="08")
    assert cenario.alertas("T2") == []
    # Com o mínimo em duas, o alerta aparece só com as duas empresas de estabelecimento ativo.
    (alerta,) = cenario.alertas("T2", t2_minimo_empresas=2)
    assert cenario.rotulos(alerta) == {
        "UM CONTRATADA SINTETICA LTDA",
        "DOIS FILIAL ATIVA SINTETICA LTDA",
    }
    assert alerta.parametros["empresas_no_endereco"] == 2
    assert not any("TRES FILIAL" in e["descricao"] for e in alerta.evidencias)


@pytest.mark.parametrize("situacao", ["01", "3", "04", "08"])
def test_t2_nao_conta_estabelecimento_em_situacao_diferente_de_ativa(cenario, situacao):
    _tres_no_mesmo_endereco_por_filiais(cenario, situacao_da_terceira=situacao)
    assert cenario.alertas("T2") == []


def test_t2_conta_filial_ativa_e_diz_que_so_contam_ativos(cenario):
    filial = _tres_no_mesmo_endereco_por_filiais(cenario, situacao_da_terceira="2")
    (alerta,) = cenario.alertas("T2")
    assert len(cenario.rotulos(alerta)) == 3
    assert alerta.parametros["empresas_no_endereco"] == 3
    assert alerta.parametros["estabelecimentos_sem_situacao"] == 0
    assert "situação cadastral ATIVA" in alerta.motivo
    da_filial = [e for e in alerta.evidencias if filial in e["descricao"]]
    assert len(da_filial) == 1 and "situação ATIVA" in da_filial[0]["descricao"]
    from buscador.tipologias.catalogo import catalogo

    (item,) = [t for t in catalogo(cenario.padrao) if t["codigo"] == "T2"]
    assert "ATIVA" in item["regra"]


def test_t2_conta_estabelecimento_sem_situacao_informada_e_avisa(cenario):
    # Política de erro da T2: prefere evitar falso negativo. Sem o código não se sabe se o
    # estabelecimento foi baixado, então ele continua contando, e o alerta deixa isso à vista.
    filial = _tres_no_mesmo_endereco_por_filiais(cenario, situacao_da_terceira="")
    (alerta,) = cenario.alertas("T2")
    assert len(cenario.rotulos(alerta)) == 3
    assert alerta.parametros["estabelecimentos_sem_situacao"] == 1
    assert "situação cadastral não informada" in alerta.motivo
    da_filial = [e for e in alerta.evidencias if filial in e["descricao"]]
    assert len(da_filial) == 1 and "situação não informada" in da_filial[0]["descricao"]
