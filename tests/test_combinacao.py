"""Alerta de combinação (COMB): vários sinais na mesma empresa ou pessoa, explicado. Casos
positivo, negativo e de limite, com dados sintéticos (``tests/apoio_lavagem.py``)."""

from __future__ import annotations

import pytest

from buscador import tipologias
from buscador.config import ErroConfiguracao
from buscador.modelos import Alerta, AlertaDecisao
from buscador.tipologias import combinacao
from buscador.tipologias.politicas import POLITICAS
from tests.apoio_lavagem import Cenario, verificar_explicacao
from tests.conftest import cabecalhos, criar_config


@pytest.fixture
def cenario(sessao, custodia, config) -> Cenario:
    return Cenario(sessao, custodia, config)


def _recente_e_subcapitalizada(cenario: Cenario, capital: float = 1_000.0) -> None:
    """Empresa que dispara a T1 (aberta 60 dias antes) e a T3 (contrato de 50 vezes o capital)."""
    cenario.empresa("92700001", "ACUMULADORA SINTETICA LTDA", inicio="20260402", capital=capital)
    cenario.contrato("92700001", "2026-06-01", 50_000.0)


def _combinacoes(alertas) -> list:
    return [a for a in alertas if a.tipologia == "COMB"]


def test_rf14_combinacao_dispara_com_duas_tipologias_distintas_na_mesma_empresa(cenario):
    _recente_e_subcapitalizada(cenario)
    alertas = cenario.alertas("T1", "T3", "COMB")
    (comb,) = _combinacoes(alertas)
    verificar_explicacao(comb, cenario.sessao)
    p = comb.parametros
    assert (p["entidade_rotulo"], p["entidade_tipo"]) == ("ACUMULADORA SINTETICA LTDA", "empresa")
    assert (p["tipologias_distintas"], p["soma_pesos"]) == (2, 2.0)
    assert (p["comb_minimo_tipologias"], p["comb_peso_minimo"]) == (2, 2.0)
    assert [(c["tipologia"], c["peso"], c["conta"]) for c in p["componentes"]] == [
        ("T1", 1.0, True),
        ("T3", 1.0, True),
    ]
    # Cada componente aponta para o alerta de origem e repete o que o explica.
    ids = {a.id for a in alertas if a.tipologia != "COMB"}
    assert {c["alerta_id"] for c in p["componentes"]} == ids
    assert all(c["titulo"] and c["tipologia_nome"] for c in p["componentes"])
    assert "Não é escore de culpa" in comb.motivo and "pista" in comb.motivo
    assert "[T1]" in comb.motivo and "[T3]" in comb.motivo
    assert comb.severidade == "alta" and POLITICAS["COMB"].grupo == "combinacao"
    assert {e["descricao"][:4] for e in comb.evidencias} == {"[T1]", "[T3]"}
    assert comb.entidades == [p["entidade_id"]]  # o contrato não ganha combinação


def test_rf14_combinacao_nao_dispara_com_uma_tipologia_nem_repete_a_mesma(cenario):
    _recente_e_subcapitalizada(cenario, capital=1_000_000.0)  # só a T1
    cenario.contrato("92700001", "2026-06-15", 60_000.0)  # segundo alerta da MESMA tipologia
    alertas = cenario.alertas("T1", "T3", "COMB")
    assert [a.tipologia for a in alertas] == ["T1", "T1"]


def test_rf14_combinacao_limite_minimos_de_tipologias_e_de_peso_sao_configuraveis(cenario):
    _recente_e_subcapitalizada(cenario)
    assert _combinacoes(cenario.alertas("T1", "T3", "COMB", comb_minimo_tipologias=3)) == []
    assert _combinacoes(cenario.alertas("T1", "T3", "COMB", comb_peso_minimo=2.01)) == []
    assert len(_combinacoes(cenario.alertas("T1", "T3", "COMB", comb_peso_minimo=2.0))) == 1


def test_rf14_sinais_fracos_pesam_menos_e_dois_deles_sozinhos_nao_disparam(cenario):
    # L14 (faixa etária atípica) e L15 (20 CNAEs): dois sinais fracos, soma de pesos 1,0.
    secundarias = ",".join(f"47{n:05d}" for n in range(19))
    cenario.empresa("92700002", "SO SINAIS FRACOS SINTETICA LTDA", secundarias=secundarias)
    cenario.socio("92700002", "ADMIN IDOSO SINTETICO", "***818181**", faixa="9")
    cenario.contrato("92700002", "2026-06-01", 150_000.0)
    # H49: os sinais fracos não geram alerta próprio, e os dois juntos não chegam ao peso mínimo.
    assert cenario.alertas("L14", "L15", "COMB") == []
    assert sorted(d.tipologia for d in cenario.sinais("L14", "L15")) == ["L14", "L15"]
    pesos = cenario.padrao["comb_pesos"]
    assert (pesos["L14"], pesos["L15"], pesos["T1"], pesos["L8"]) == (0.5, 0.5, 1.0, 0.0)
    # Com mais um sinal comum (L5, sócio estrangeiro), a soma chega a 2,0 e dispara.
    cenario.socio(
        "92700002", "FOREIGN SAMPLE HOLDINGS", "", identificador="3", pais="249", qualificacao="37"
    )
    alertas = cenario.alertas("L5", "COMB")
    assert sorted(a.tipologia for a in alertas) == ["COMB", "L5"]
    (comb,) = _combinacoes(alertas)
    assert (comb.parametros["tipologias_distintas"], comb.parametros["soma_pesos"]) == (3, 2.0)
    fracos = {c["tipologia"] for c in comb.parametros["componentes"] if c["sinal_fraco"]}
    assert fracos == {"L14", "L15"}
    # Só a empresa: nenhum dos alertas reúne duas tipologias numa pessoa.
    assert comb.parametros["entidade_tipo"] == "empresa"


def test_rf14_composicao_e_mesmo_fato_nao_contam_duas_vezes(cenario):
    # T1 + T7 na mesma contratada geram a L8, que é a composição das duas: não soma de novo.
    cenario.empresa("92700003", "RECEM ABERTA SINTETICA LTDA", inicio="20260301")
    cenario.socio("92700003", "SERVIDOR E SOCIO SINTETICO", "***444444**", entrada="20260301")
    cenario.contrato("92700003", "2026-06-01", 10_000.0)
    cenario.servidor("SERVIDOR E SOCIO SINTETICO", "***.444.444-**")
    combinacoes = _combinacoes(cenario.alertas("T1", "T7", "L8", "COMB"))
    empresa = next(c for c in combinacoes if c.parametros["entidade_tipo"] == "empresa")
    assert (empresa.parametros["tipologias_distintas"], empresa.parametros["soma_pesos"]) == (
        2,
        2.0,
    )
    l8 = next(c for c in empresa.parametros["componentes"] if c["tipologia"] == "L8")
    assert (l8["conta"], l8["peso"]) == (False, 0.0)
    assert l8["observacao"] == "Composição de T1 e T7, já contadas separadamente."
    assert "não soma" in empresa.motivo
    # L8 sozinha com outra tipologia não vira "duas": T7 + L8 no sócio conta uma.
    socio = [c for c in combinacoes if c.parametros["entidade_tipo"] == "pessoa"]
    assert socio == []


def test_rf14_l1_e_t2_descrevem_o_mesmo_endereco_e_contam_uma_vez(cenario):
    for n in range(5):
        cenario.empresa(f"9271000{n}", f"MESMO PREDIO {n} SINTETICA LTDA", complemento="SALA 1")
    cenario.contrato("92710000", "2026-06-01", 10.0)
    alertas = cenario.alertas("T2", "L1", "COMB")
    assert sorted(a.tipologia for a in alertas) == ["L1", "T2"]
    # Forçando o mínimo de peso a 1, o componente repetido aparece como "mesmo fato".
    (comb, *_) = _combinacoes(
        cenario.alertas("T2", "L1", "T3", "COMB", t3_razao_minima=0.00001, comb_peso_minimo=1.5)
    )
    por_codigo = {c["tipologia"]: c for c in comb.parametros["componentes"]}
    contam = sorted(c for c, v in por_codigo.items() if v["conta"])
    assert len(contam) == 2 and "T3" in contam and ("L1" in contam) != ("T2" in contam)
    repetido = por_codigo["T2" if "L1" in contam else "L1"]
    assert "Mesmo fato" in repetido["observacao"] and repetido["peso"] == 0.0


def test_rf12_alerta_descartado_sai_da_combinacao_na_execucao_seguinte(cenario):
    _recente_e_subcapitalizada(cenario)
    alertas = cenario.alertas("T1", "T3", "COMB")
    (comb,) = _combinacoes(alertas)
    t3 = next(a for a in alertas if a.tipologia == "T3")
    t3.situacao = "descartado"
    cenario.sessao.add(
        AlertaDecisao(
            alerta_id=t3.id,
            caso_id=cenario.caso_id,
            decisao="descartado",
            justificativa="Capital conferido em fonte sintética",
            usuario="teste",
        )
    )
    cenario.sessao.flush()
    assert _combinacoes(cenario.alertas("COMB")) == []
    # A combinação não tinha decisão: foi apagada, e não mantida como inativa.
    assert cenario.sessao.get(Alerta, comb.id) is None


def test_rf14_combinacao_pela_api_no_catalogo_e_na_fila(cliente, caso_carregado):
    catalogo = cliente.get("/tipologias", headers={"Authorization": "Bearer tok-analista"}).json()
    codigos = [t["codigo"] for t in catalogo]
    assert codigos[-1] == "COMB" and codigos == tipologias.ordem_tipologias()
    por_codigo = {t["codigo"]: t for t in catalogo}
    assert (por_codigo["COMB"]["grupo"], por_codigo["COMB"]["peso"]) == ("combinacao", 0.0)
    assert (por_codigo["T1"]["peso"], por_codigo["L14"]["peso"], por_codigo["L8"]["peso"]) == (
        1.0,
        0.5,
        0.0,
    )
    assert "Velasco" in por_codigo["COMB"]["base"]
    pesos = next(p for p in por_codigo["COMB"]["parametros"] if p["nome"] == "comb_pesos")
    assert pesos["valor"]["L15"] == 0.5 and pesos["por_execucao"] is False
    r = cliente.post(f"/casos/{caso_carregado}/alertas/executar", json={}, headers=cabecalhos())
    assert r.status_code == 200, r.text
    alertas = r.json()
    combinacoes = [a for a in alertas if a["tipologia"] == "COMB"]
    assert len(combinacoes) == 3 and alertas[0]["tipologia"] == "COMB"
    ids = {a["id"] for a in alertas}
    for comb in combinacoes:
        assert comb["grupo"] == "combinacao" and comb["situacao"] == "pendente"
        assert comb["politica_erro"].startswith("Prefere evitar falso positivo")
        componentes = comb["parametros"]["componentes"]
        # H49: o sinal fraco não tem alerta próprio (``alerta_id`` nulo); os demais apontam para
        # um alerta da fila.
        assert {c["alerta_id"] for c in componentes if not c["sinal_fraco"]} <= ids
        assert all(c["alerta_id"] is None for c in componentes if c["sinal_fraco"])
        assert comb["parametros"]["aviso"].startswith("Não é escore de culpa")
    # Executar só a combinação não apaga os demais; pedir um limiar fora da faixa dá 422.
    r = cliente.post(
        f"/casos/{caso_carregado}/alertas/executar",
        json={"tipologias": ["COMB"], "parametros": {"comb_minimo_tipologias": 3}},
        headers=cabecalhos(),
    )
    assert r.status_code == 200 and all(a["tipologia"] == "COMB" for a in r.json())
    listados = cliente.get(f"/casos/{caso_carregado}/alertas", headers=cabecalhos()).json()
    assert sum(a["tipologia"] != "COMB" for a in listados) == 11
    r = cliente.post(
        f"/casos/{caso_carregado}/alertas/executar",
        json={"parametros": {"comb_minimo_tipologias": 1}},
        headers=cabecalhos(),
    )
    assert r.status_code == 422


def test_rf14_pesos_configurados_sao_validados(tmp_path):
    assert combinacao.pesos('{"L5": 0.5}')["L5"] == 0.5
    for invalido in ("{", "[]", '{"X1": 1}', '{"T1": -1}', '{"T1": 11}', '{"COMB": 1}'):
        with pytest.raises(ErroConfiguracao):
            criar_config(tmp_path, comb_pesos=invalido).validar()
    assert set(combinacao.pesos_padrao()) == set(POLITICAS) - {"COMB"}
