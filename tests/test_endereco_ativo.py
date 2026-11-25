"""Critério único de "estabelecimento que conta no endereço" (decisão do autor): só o ATIVO
(código 02) e o que veio sem situação informada; nulo, suspenso, inapto e baixado ficam de fora.
Vale para a T2, a L1, a aresta ``mesmo_endereco`` e o sinal (d) da L16. A L10 fica de fora de
propósito (ver o teste dela aqui).

Só dados sintéticos (``tests/apoio_lavagem.py``).
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from buscador.modelos import Aresta, Entidade
from buscador.situacao_cadastral import (
    conta_no_endereco,
    enderecos_em_uso,
    situacao_nao_informada,
)
from buscador.tipologias.catalogo import catalogo
from buscador.tipologias.politicas import POLITICAS
from tests.apoio_lavagem import Cenario, convenio


@pytest.fixture
def cenario(sessao, custodia, config) -> Cenario:
    return Cenario(sessao, custodia, config)


# ==== Função única de filtro ====================================================================


@pytest.mark.parametrize("situacao", ["02", "2", " 2 "])
def test_filtro_conta_o_estabelecimento_ativo(situacao):
    est = {"situacao_cadastral": situacao}
    assert conta_no_endereco(est) and not situacao_nao_informada(est)


@pytest.mark.parametrize("situacao", ["01", "1", "3", "04", "08", "8"])
def test_filtro_nao_conta_nulo_suspenso_inapto_nem_baixado(situacao):
    est = {"situacao_cadastral": situacao}
    assert not conta_no_endereco(est) and not situacao_nao_informada(est)


@pytest.mark.parametrize("situacao", ["", None, "  "])
def test_filtro_conta_o_sem_situacao_e_marca_que_nao_foi_informada(situacao):
    est = {"situacao_cadastral": situacao}
    assert conta_no_endereco(est) and situacao_nao_informada(est)


def test_enderecos_em_uso_agrupa_so_os_estabelecimentos_que_contam():
    ativo = {"endereco_normalizado": "RUA A", "situacao_cadastral": "02"}
    sem = {"endereco_normalizado": "RUA A", "situacao_cadastral": ""}
    baixado = {"endereco_normalizado": "RUA B", "situacao_cadastral": "08"}
    em_branco = {"endereco_normalizado": "", "situacao_cadastral": "02"}
    atributos = {"estabelecimentos": [ativo, sem, baixado, em_branco]}
    assert enderecos_em_uso(atributos) == {"RUA A": [ativo, sem]}
    assert enderecos_em_uso(None) == {} and enderecos_em_uso({}) == {}


# ==== Aresta mesmo_endereco =====================================================================


def _arestas_de_endereco(cenario: Cenario) -> list[tuple[set[str], dict]]:
    cenario.incorporar()
    rotulo = {
        e.id: e.rotulo
        for e in cenario.sessao.execute(
            select(Entidade).where(Entidade.caso_id == cenario.caso_id)
        ).scalars()
    }
    return [
        ({rotulo[a.origem_id], rotulo[a.destino_id]}, a.atributos)
        for a in cenario.sessao.execute(
            select(Aresta).where(Aresta.caso_id == cenario.caso_id, Aresta.tipo == "mesmo_endereco")
        ).scalars()
    ]


def _duas_no_ponto_comum(cenario: Cenario, situacao_da_filial: str) -> None:
    """A UM tem a matriz no ponto comum; a DOIS só tem lá uma filial (matriz em outra rua)."""
    cenario.empresa("93000001", "UM SINTETICA LTDA", logradouro="DO PONTO COMUM")
    cenario.empresa("93000002", "DOIS SINTETICA LTDA", logradouro="DA MATRIZ DOIS")
    cenario.filial("93000002", logradouro="DO PONTO COMUM", situacao=situacao_da_filial)


@pytest.mark.parametrize("situacao", ["01", "3", "04", "08"])
def test_aresta_mesmo_endereco_nao_liga_por_estabelecimento_nao_ativo(cenario, situacao):
    _duas_no_ponto_comum(cenario, situacao)
    assert _arestas_de_endereco(cenario) == []


def test_aresta_mesmo_endereco_liga_por_filial_ativa(cenario):
    _duas_no_ponto_comum(cenario, "02")
    ((par, atributos),) = _arestas_de_endereco(cenario)
    assert par == {"UM SINTETICA LTDA", "DOIS SINTETICA LTDA"}
    assert atributos["estabelecimentos_sem_situacao"] == 0
    assert "PONTO COMUM" in atributos["endereco"] and atributos["derivada"] is True


def test_aresta_mesmo_endereco_liga_por_filial_sem_situacao_e_registra_no_atributo(cenario):
    _duas_no_ponto_comum(cenario, "")
    ((par, atributos),) = _arestas_de_endereco(cenario)
    assert par == {"UM SINTETICA LTDA", "DOIS SINTETICA LTDA"}
    assert atributos["estabelecimentos_sem_situacao"] == 1


def test_aresta_mesmo_endereco_ignora_a_baixada_e_mantem_as_ativas_do_grupo(cenario):
    _duas_no_ponto_comum(cenario, "02")
    cenario.empresa(
        "93000003", "TRES BAIXADA SINTETICA LTDA", logradouro="DO PONTO COMUM", situacao="08"
    )
    ((par, _),) = _arestas_de_endereco(cenario)
    assert par == {"UM SINTETICA LTDA", "DOIS SINTETICA LTDA"}


def test_aresta_mesmo_endereco_usa_a_captura_e_o_endereco_do_estabelecimento_que_conta(cenario):
    # A UM tem no ponto comum uma filial baixada (lida primeiro) e a matriz ativa.
    cenario.empresa("93000001", "UM SINTETICA LTDA", logradouro="DO PONTO COMUM")
    cenario.empresa("93000002", "DOIS SINTETICA LTDA", logradouro="DO PONTO COMUM")
    cenario.filial("93000001", logradouro="DO PONTO COMUM", situacao="08")
    cenario.incorporar()
    (aresta,) = cenario.sessao.execute(
        select(Aresta).where(Aresta.caso_id == cenario.caso_id, Aresta.tipo == "mesmo_endereco")
    ).scalars()
    assert aresta.atributos["estabelecimentos_sem_situacao"] == 0
    assert "PONTO COMUM" in aresta.atributos["endereco"]
    assert aresta.captura_id == cenario.captura("cnpj_arquivo")


# ==== L1: sem situação informada conta, com aviso (mesmo critério da T2) ========================


def _massa(cenario: Cenario, situacao_da_quinta: str) -> None:
    for n in range(1, 5):
        cenario.empresa(f"9310000{n}", f"MASSA {n} SINTETICA LTDA", complemento="SALA 1")
    cenario.empresa(
        "93100005", "MASSA 5 SINTETICA LTDA", complemento="SALA 1", situacao=situacao_da_quinta
    )


def test_l1_conta_estabelecimento_sem_situacao_informada_e_avisa(cenario):
    _massa(cenario, "")
    (alerta,) = cenario.alertas("L1")
    assert len(alerta.entidades) == 5
    assert alerta.parametros["empresas_ativas_no_endereco"] == 5
    assert alerta.parametros["estabelecimentos_sem_situacao"] == 1
    assert "situação cadastral não informada" in alerta.motivo
    assert "baixados, inaptos, suspensos e nulos ficam de fora" in alerta.motivo
    da_quinta = [e for e in alerta.evidencias if "MASSA 5" in e["descricao"]]
    assert len(da_quinta) == 1 and "situação não informada" in da_quinta[0]["descricao"]
    das_outras = [e for e in alerta.evidencias if "MASSA 5" not in e["descricao"]]
    assert len(das_outras) == 4
    assert all("situação ATIVA" in e["descricao"] for e in das_outras)
    # O título só fala em "ativas" quando todas têm a situação ATIVA informada.
    assert "ativas" not in alerta.titulo


def test_l1_sem_estabelecimento_sem_situacao_nao_traz_o_aviso(cenario):
    _massa(cenario, "02")
    (alerta,) = cenario.alertas("L1")
    assert alerta.parametros["estabelecimentos_sem_situacao"] == 0
    assert "não informada" not in alerta.motivo
    assert "5 empresas ativas" in alerta.titulo


@pytest.mark.parametrize("situacao", ["01", "3", "04", "08"])
def test_l1_continua_sem_contar_nulo_suspenso_inapto_e_baixado(cenario, situacao):
    _massa(cenario, situacao)
    assert cenario.alertas("L1") == []


def test_l1_prefere_o_estabelecimento_ativo_da_mesma_empresa_no_endereco(cenario):
    # A quinta empresa tem, na mesma sala, a matriz sem situação e uma filial ATIVA: conta uma
    # vez, pelo estabelecimento ativo, e o alerta não avisa de situação não informada.
    _massa(cenario, "")
    cenario.filial("93100005", complemento="SALA 1", situacao="02")
    (alerta,) = cenario.alertas("L1")
    assert len(alerta.entidades) == 5
    assert alerta.parametros["estabelecimentos_sem_situacao"] == 0


def test_l1_catalogo_e_politica_de_erro_dizem_o_criterio(cenario):
    (item,) = [t for t in catalogo(cenario.padrao) if t["codigo"] == "L1"]
    assert "ATIVO" in item["regra"] and "sem situação informada" in item["regra"]
    politica = POLITICAS["L1"].politica_erro
    assert "sem situação informada" in politica and "baixado" in politica


# ==== L16, sinal (d): empresa do dirigente no mesmo endereço ====================================

ENTIDADE = "93200001"


def _instituto_e_empresa_do_dirigente(cenario: Cenario, situacao_da_empresa: str) -> None:
    cenario.empresa(
        ENTIDADE,
        "INSTITUTO SINTETICO DE EXEMPLO",
        inicio="20100101",
        natureza="3999",
        logradouro="DO INSTITUTO",
        cnae="9430800",
    )
    cenario.socio(ENTIDADE, "PRESIDENTE SINTETICO", "***909090**", qualificacao="16")
    convenio(cenario, ENTIDADE)
    # Empresa antiga e de outro ramo: só o endereço a liga à entidade.
    cenario.empresa(
        "93200002",
        "FORNECEDORA DO INSTITUTO SINTETICA LTDA",
        inicio="20100101",
        logradouro="DO INSTITUTO",
        complemento="FUNDOS",
        cnae="4711301",
        situacao=situacao_da_empresa,
    )
    cenario.socio("93200002", "PRESIDENTE SINTETICO", "***909090**")


def test_l16_sinal_d_liga_pelo_endereco_de_estabelecimento_ativo(cenario):
    _instituto_e_empresa_do_dirigente(cenario, "02")
    (alerta,) = cenario.alertas("L16")
    assert alerta.parametros["sinais"] == ["d"]
    assert "mesmo endereço da entidade" in alerta.motivo
    assert "não informada" not in alerta.motivo


@pytest.mark.parametrize("situacao", ["01", "3", "04", "08"])
def test_l16_sinal_d_nao_liga_pelo_endereco_de_estabelecimento_nao_ativo(cenario, situacao):
    _instituto_e_empresa_do_dirigente(cenario, situacao)
    assert cenario.alertas("L16") == []


def test_l16_sinal_d_liga_pelo_endereco_sem_situacao_informada_e_avisa(cenario):
    _instituto_e_empresa_do_dirigente(cenario, "")
    (alerta,) = cenario.alertas("L16")
    assert alerta.parametros["sinais"] == ["d"]
    assert "mesmo endereço da entidade" in alerta.motivo
    assert "situação cadastral não informada" in alerta.motivo


# ==== L10: o vínculo por endereço com a sancionada NÃO segue o filtro ===========================


def test_l10_continua_ligando_a_sancionada_baixada_pelo_endereco(cenario):
    # A L10 procura a empresa usada no lugar da sancionada. A sancionada pode estar baixada ou
    # inapta: tirá-la da comparação esconderia justamente a sucessora.
    cenario.empresa(
        "93300001", "SANCIONADA SINTETICA LTDA", logradouro="DA SANCAO", numero="1", situacao="08"
    )
    cenario.empresa("93300002", "SUCESSORA SINTETICA LTDA", logradouro="DA SANCAO", numero="1")
    cenario.sancao("93300001", inicio="2025-01-01")
    cenario.contrato("93300002", "2026-06-01")
    (alerta,) = cenario.alertas("L10")
    assert alerta.parametros["vinculo_por_endereco"] is True
