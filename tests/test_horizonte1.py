"""Horizonte 1 do backlog: H12 (RF12), H13 (RF14), H14 (RF07), H15 (RNF12), H17 (RNF04) e H31
(RF04), mais a evolução do esquema do banco. Só dados sintéticos (``scripts/gerar_fixtures.py``).
"""

from __future__ import annotations

import os
from datetime import date, timedelta
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import DBAPIError

from buscador import retencao, tipologias
from buscador.api import criar_app
from buscador.conectores import CONECTORES, ErroConector, FalhaTransitoria, Retentativa
from buscador.conectores.cnpj_arquivo import ConectorCnpjArquivo
from buscador.conectores.pncp import TransporteSimulado
from buscador.db import ErroEsquema, criar_engine, criar_esquema, verificar_esquema
from buscador.modelos import Alerta, AlertaDecisao, Captura, Caso, Coleta, Entidade
from buscador.tipologias.politicas import ORDEM_SEVERIDADE, POLITICAS, SEVERIDADES
from tests.conftest import (
    DIR_CNPJ,
    FIXTURES_PNCP,
    cabecalhos,
    corpo_caso,
    criar_config,
    entidade_por_identificador,
)

PERIODO = {"dataInicial": "20260101", "dataFinal": "20261231"}
H_DELEGADO = cabecalhos("tok-delegado")


def _novo_caso(cliente, **campos) -> str:
    r = cliente.post("/casos", json=corpo_caso(**campos), headers=cabecalhos())
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _coletar(cliente, caso_id: str, fonte: str, parametros: dict):
    return cliente.post(
        f"/casos/{caso_id}/coletas",
        json={"fonte": fonte, "parametros": parametros},
        headers=cabecalhos(),
    )


def _executar(cliente, caso_id: str, **corpo) -> list[dict]:
    r = cliente.post(f"/casos/{caso_id}/alertas/executar", json=corpo, headers=cabecalhos())
    assert r.status_code == 200, r.text
    return r.json()


def _decidir(
    cliente, caso_id: str, alerta_id: str, decisao: str, justificativa, token="tok-analista"
):
    return cliente.post(
        f"/casos/{caso_id}/alertas/{alerta_id}/decisao",
        json={"decisao": decisao, "justificativa": justificativa},
        headers=cabecalhos(token),
    )


def _auditoria(cliente, caso_id: str, acao: str) -> list[dict]:
    r = cliente.get("/auditoria", params={"caso_id": caso_id, "limite": 1000}, headers=H_DELEGADO)
    assert r.status_code == 200, r.text
    return [e for e in r.json() if e["acao"] == acao]


def _vencer(app, caso_id: str, dias: int = 1) -> str:
    """Põe o prazo de retenção no passado (direto no banco: a API não aceita data passada)."""
    ontem = (retencao.hoje_utc() - timedelta(days=dias)).isoformat()
    with app.state.fabrica_sessoes() as s:
        s.get(Caso, caso_id).retencao_ate = ontem
        s.commit()
    return ontem


# ==== H12 (RF12): alerta como pista ============================================================


def test_h12_alerta_nasce_pendente_e_a_decisao_fica_no_alerta_e_na_auditoria(
    cliente, caso_carregado
):
    alertas = _executar(cliente, caso_carregado)
    assert all(a["situacao"] == "pendente" and a["decisao"] is None for a in alertas)
    alvo = alertas[0]

    r = _decidir(cliente, caso_carregado, alvo["id"], "confirmado", "Conferido no cadastro oficial")
    assert r.status_code == 200, r.text
    decidido = r.json()
    assert decidido["id"] == alvo["id"] and decidido["situacao"] == "confirmado"
    assert decidido["decisao"]["decisao"] == "confirmado"
    assert decidido["decisao"]["usuario"] == "ana" and decidido["decisao"]["perfil"] == "analista"
    assert decidido["decisao"]["justificativa"] == "Conferido no cadastro oficial"
    assert decidido["decisao"]["decidido_em"].endswith("Z")
    assert len(decidido["decisoes"]) == 1

    listados = cliente.get(f"/casos/{caso_carregado}/alertas", headers=cabecalhos()).json()
    por_id = {a["id"]: a for a in listados}
    assert por_id[alvo["id"]]["situacao"] == "confirmado"
    assert sum(a["situacao"] == "pendente" for a in listados) == len(alertas) - 1

    eventos = _auditoria(cliente, caso_carregado, "alerta.decidir")
    assert len(eventos) == 1
    detalhes = eventos[0]["detalhes"]
    assert eventos[0]["usuario"] == "ana"
    assert detalhes["alerta_id"] == alvo["id"] and detalhes["decisao"] == "confirmado"
    assert detalhes["justificativa_decisao"] == "Conferido no cadastro oficial"
    assert detalhes["situacao_anterior"] == "pendente" and detalhes["revisao"] is False
    assert cliente.get("/auditoria/verificar", headers=cabecalhos()).json()["integra"] is True


def test_h12_justificativa_da_decisao_e_obrigatoria_e_validada(cliente, caso_carregado):
    alvo = _executar(cliente, caso_carregado)[0]
    url = f"/casos/{caso_carregado}/alertas/{alvo['id']}/decisao"
    invalidos = [
        {"decisao": "confirmado"},
        {"decisao": "confirmado", "justificativa": ""},
        {"decisao": "confirmado", "justificativa": "   "},
        {"decisao": "confirmado", "justificativa": "..."},
        {"decisao": "confirmado", "justificativa": "linha 1\nlinha 2"},
        {"decisao": "confirmado", "justificativa": "a\x00b"},
        {"decisao": "confirmado", "justificativa": "x" * 501},
        {"decisao": "pendente", "justificativa": "voltar atrás"},
        {"decisao": "apagado", "justificativa": "texto"},
        {"justificativa": "texto"},
        {"decisao": "descartado", "justificativa": "texto", "usuario": "outro"},
    ]
    for corpo in invalidos:
        assert cliente.post(url, json=corpo, headers=cabecalhos()).status_code == 422, corpo
    # Nenhuma tentativa recusada mudou o alerta nem entrou como decisão.
    atual = cliente.get(f"/casos/{caso_carregado}/alertas", headers=cabecalhos()).json()
    assert all(a["situacao"] == "pendente" and a["decisoes"] == [] for a in atual)
    assert _auditoria(cliente, caso_carregado, "alerta.decidir") == []
    # A justificativa do acesso (cabeçalho) continua exigida, como em toda rota de caso.
    r = cliente.post(
        url,
        json={"decisao": "descartado", "justificativa": "texto"},
        headers=cabecalhos(justificativa=None),
    )
    assert r.status_code == 400


def test_h12_revisao_gera_novo_evento_e_mantem_o_historico(cliente, caso_carregado, app):
    alvo = _executar(cliente, caso_carregado)[0]
    assert (
        _decidir(cliente, caso_carregado, alvo["id"], "confirmado", "Primeira análise").status_code
        == 200
    )
    r = _decidir(
        cliente, caso_carregado, alvo["id"], "descartado", "Revisto: documento novo", "tok-delegado"
    )
    assert r.status_code == 200, r.text
    revisto = r.json()
    assert revisto["situacao"] == "descartado" and revisto["decisao"]["usuario"] == "carlos"
    assert [d["decisao"] for d in revisto["decisoes"]] == ["confirmado", "descartado"]
    assert [d["justificativa"] for d in revisto["decisoes"]] == [
        "Primeira análise",
        "Revisto: documento novo",
    ]

    eventos = _auditoria(cliente, caso_carregado, "alerta.decidir")
    assert [e["detalhes"]["decisao"] for e in eventos] == ["confirmado", "descartado"]
    assert eventos[1]["detalhes"]["revisao"] is True
    assert eventos[1]["detalhes"]["situacao_anterior"] == "confirmado"

    # O histórico é só de acréscimo também no banco (gatilhos).
    for sql in ("UPDATE alerta_decisoes SET decisao = 'x'", "DELETE FROM alerta_decisoes"):
        with pytest.raises(DBAPIError, match="somente de acrescimo"):
            with app.state.engine.begin() as conexao:
                conexao.execute(text(sql))
    with app.state.fabrica_sessoes() as s:
        assert len(list(s.execute(select(AlertaDecisao)).scalars())) == 2


def test_h12_reexecucao_das_tipologias_preserva_a_decisao(cliente, caso_carregado):
    alertas = _executar(cliente, caso_carregado)
    t1 = [a for a in alertas if a["tipologia"] == "T1"]
    assert len(t1) == 2
    beta = next(a for a in t1 if a["parametros"]["dias_observados"] == 52)
    theta = next(a for a in t1 if a["parametros"]["dias_observados"] == 45)
    assert (
        _decidir(cliente, caso_carregado, beta["id"], "descartado", "Empresa regular").status_code
        == 200
    )

    # Mesma execução de novo: os mesmos alertas (mesmo id), com a decisão mantida.
    de_novo = {a["id"]: a for a in _executar(cliente, caso_carregado)}
    assert set(de_novo) == {a["id"] for a in alertas}
    assert (
        de_novo[beta["id"]]["situacao"] == "descartado"
        and len(de_novo[beta["id"]]["decisoes"]) == 1
    )

    # Limiar que deixa de detectar os dois T1: o pendente some; o decidido fica, inativo.
    assert (
        _executar(cliente, caso_carregado, parametros={"t1_dias_minimos": 10}, tipologias=["T1"])
        == []
    )
    listados = {
        a["id"]: a
        for a in cliente.get(f"/casos/{caso_carregado}/alertas", headers=cabecalhos()).json()
    }
    assert theta["id"] not in listados
    assert listados[beta["id"]]["ativo"] is False
    assert (
        listados[beta["id"]]["situacao"] == "descartado"
        and len(listados[beta["id"]]["decisoes"]) == 1
    )
    # Os inativos vão para o fim da lista.
    assert list(listados)[-1] == beta["id"]

    # Volta a ser detectado: é o mesmo alerta, reativado, com o histórico.
    reativados = {a["id"]: a for a in _executar(cliente, caso_carregado, tipologias=["T1"])}
    assert (
        reativados[beta["id"]]["ativo"] is True
        and reativados[beta["id"]]["situacao"] == "descartado"
    )


def test_h12_alerta_de_outro_caso_nao_e_decidido(cliente, caso_carregado):
    """Regressão de segurança: o alerta só é alcançado pelo caso a que pertence."""
    alvo = _executar(cliente, caso_carregado)[0]
    outro = _novo_caso(cliente, titulo="Outro caso")
    r = _decidir(cliente, outro, alvo["id"], "descartado", "tentativa por outro caso")
    assert r.status_code == 404
    assert _decidir(cliente, caso_carregado, "x" * 65, "descartado", "id longo").status_code == 404
    assert _decidir(cliente, caso_carregado, "inexistente", "descartado", "id").status_code == 404
    atual = cliente.get(f"/casos/{caso_carregado}/alertas", headers=cabecalhos()).json()
    assert all(a["situacao"] == "pendente" for a in atual)
    assert (
        _decidir(cliente, caso_carregado, alvo["id"], "descartado", "sem token").status_code == 200
    )
    sem_token = cliente.post(
        f"/casos/{caso_carregado}/alertas/{alvo['id']}/decisao",
        json={"decisao": "confirmado", "justificativa": "sem token"},
        headers={"X-Justificativa": "teste"},
    )
    assert sem_token.status_code == 401


def test_h12_relatorio_mostra_a_situacao_de_cada_alerta(cliente, caso_carregado):
    alertas = _executar(cliente, caso_carregado)
    _decidir(cliente, caso_carregado, alertas[0]["id"], "confirmado", "Confirmado | na análise")
    _decidir(cliente, caso_carregado, alertas[1]["id"], "descartado", "Sem relevância")
    d = cliente.get(f"/casos/{caso_carregado}/relatorio", headers=cabecalhos()).json()
    assert d["resumo"]["alertas_confirmados"] == 1 and d["resumo"]["alertas_descartados"] == 1
    assert d["resumo"]["alertas_pendentes"] == len(alertas) - 2
    por_id = {a["id"]: a for a in d["alertas"]}
    assert por_id[alertas[0]["id"]]["situacao"] == "confirmado"
    assert por_id[alertas[0]["id"]]["decisoes"][0]["usuario"] == "ana"
    assert all(a["situacao"] in ("pendente", "confirmado", "descartado") for a in d["alertas"])

    md = cliente.get(
        f"/casos/{caso_carregado}/relatorio", params={"formato": "md"}, headers=cabecalhos()
    ).text
    assert "Situação: **confirmado**" in md and "Situação: **descartado**" in md
    assert md.count("Situação: **pendente de análise**") == len(alertas) - 2
    # A justificativa entra escapada: a barra não abre coluna de tabela.
    assert "justificativa: Confirmado \\| na análise" in md
    assert "é uma pista, não uma conclusão" in md


# ==== H13 (RF14): severidade e política de erro ================================================


def test_h13_tabela_unica_cobre_todas_as_tipologias():
    assert set(POLITICAS) == set(tipologias.TIPOLOGIAS) > {f"T{n}" for n in range(1, 11)}
    for politica in POLITICAS.values():
        assert politica.severidade in SEVERIDADES
        assert politica.prefere_evitar in ("falso_negativo", "falso_positivo")
        assert politica.nome and len(politica.politica_erro) > 40
        assert politica.politica_erro.startswith("Prefere evitar falso ")
    assert {p.severidade for p in POLITICAS.values()} == set(SEVERIDADES)


def test_h13_lista_vem_por_severidade_com_a_politica(cliente, caso_carregado):
    executados = _executar(cliente, caso_carregado)
    listados = cliente.get(f"/casos/{caso_carregado}/alertas", headers=cabecalhos()).json()
    for alertas in (executados, listados):
        ordens = [ORDEM_SEVERIDADE[a["severidade"]] for a in alertas]
        assert ordens == sorted(ordens)
        assert [a["severidade"] for a in alertas][0] == "alta"
        assert [a["severidade"] for a in alertas][-1] == "baixa"
        for a in alertas:
            politica = POLITICAS[a["tipologia"]]
            assert a["severidade"] == politica.severidade
            assert a["politica_erro"] == politica.politica_erro
            assert a["prefere_evitar"] == politica.prefere_evitar
            assert a["tipologia_nome"] == politica.nome
    assert [a["id"] for a in executados] == [a["id"] for a in listados]

    md = cliente.get(
        f"/casos/{caso_carregado}/relatorio", params={"formato": "md"}, headers=cabecalhos()
    ).text
    assert "Política de erro da tipologia: Prefere evitar falso negativo" in md
    posicoes = [md.index(f"### [{t}]") for t in ("T4", "T1", "T3", "T2")]
    assert posicoes == sorted(posicoes)


# ==== H14 (RF07): versão do conector, recoleta e comparação ====================================


def test_h14_captura_registra_nome_e_versao_do_conector(cliente, caso_carregado):
    coletas = cliente.get(f"/casos/{caso_carregado}/coletas", headers=cabecalhos()).json()
    assert {c["fonte"] for c in coletas} == {"cnpj_arquivo", "pncp"}
    for coleta in coletas:
        classe = CONECTORES[coleta["fonte"]]
        assert (coleta["conector_nome"], coleta["conector_versao"]) == (classe.nome, classe.versao)
        assert coleta["captura_ids"]
        for captura_id in coleta["captura_ids"]:
            captura = cliente.get(f"/capturas/{captura_id}", headers=cabecalhos()).json()
            assert captura["conector_nome"] == classe.nome
            assert captura["conector_versao"] == classe.versao
            assert captura["coleta_id"] == coleta["id"]
    relatorio_json = cliente.get(f"/casos/{caso_carregado}/relatorio", headers=cabecalhos()).json()
    assert all(c["conector_nome"] and c["conector_versao"] for c in relatorio_json["custodia"])
    assert all(v.count(".") == 2 for v in {c.versao for c in CONECTORES.values()})


def test_h14_recoleta_sem_mudanca_tem_o_mesmo_hash_e_nao_apaga_nada(cliente, caso_id):
    primeira = _coletar(cliente, caso_id, "cnpj_arquivo", {"diretorio": DIR_CNPJ}).json()
    r = cliente.post(
        f"/casos/{caso_id}/coletas/{primeira['coleta_id']}/recoletar", headers=cabecalhos()
    )
    assert r.status_code == 200, r.text
    segunda = r.json()
    assert segunda["recoleta_de"] == primeira["coleta_id"]
    assert segunda["coleta_id"] != primeira["coleta_id"] and segunda["situacao"] == "sucesso"
    comparacao = segunda["comparacao"]
    assert comparacao["mesmo_conteudo"] is True
    assert comparacao["capturas_iguais"] == 3 and comparacao["capturas_diferentes"] == 0
    assert all(c["mesmo_hash"] and c["campos_alterados"] == [] for c in comparacao["capturas"])
    assert all(c["mesma_versao_conector"] for c in comparacao["capturas"])
    # Nada foi apagado: as capturas da primeira coleta continuam lá e íntegras.
    assert set(primeira["captura_ids"]).isdisjoint(segunda["captura_ids"])
    assert (
        cliente.get(f"/casos/{caso_id}", headers=cabecalhos()).json()["contagens"]["capturas"] == 6
    )
    for captura_id in primeira["captura_ids"]:
        v = cliente.get(f"/capturas/{captura_id}/verificar", headers=cabecalhos()).json()
        assert v["integra"] is True
    evento = _auditoria(cliente, caso_id, "coleta.executar")[-1]["detalhes"]
    assert evento["recoleta_de"] == primeira["coleta_id"]
    assert evento["comparacao"]["mesmo_conteudo"] is True


def test_h14_recoleta_com_mudanca_diz_quais_campos_mudaram(cliente, caso_id, raiz_cnpj):
    primeira = _coletar(cliente, caso_id, "cnpj_arquivo", {"diretorio": DIR_CNPJ}).json()
    arquivo = raiz_cnpj / DIR_CNPJ / "K3241.K03200Y0.D61111.EMPRECSV"
    original = arquivo.read_bytes()
    # A fonte mudou entre as coletas: capital da BETA, uma empresa a mais e uma a menos.
    linhas = original.replace(b'"100000,00"', b'"999000,00"').split(b"\r\n")
    linhas = [linha for linha in linhas if linha and not linha.startswith(b'"90000014"')]
    linhas.append(b'"90000077";"NOVA SINTETICA LTDA";"2062";"49";"1000,00";"01";""')
    arquivo.write_bytes(b"\r\n".join(linhas) + b"\r\n")

    r = cliente.post(
        f"/casos/{caso_id}/coletas/{primeira['coleta_id']}/recoletar", headers=cabecalhos()
    )
    assert r.status_code == 200, r.text
    comparacao = r.json()["comparacao"]
    assert comparacao["mesmo_conteudo"] is False
    assert comparacao["capturas_iguais"] == 2 and comparacao["capturas_diferentes"] == 1
    diferente = next(c for c in comparacao["capturas"] if not c["mesmo_hash"])
    assert diferente["url_ou_arquivo"].endswith("EMPRECSV")
    assert diferente["sha256"] != diferente["anterior_sha256"]
    assert diferente["registros_comparados"] is True
    assert diferente["campos_alterados"] == ["capital_social"]
    assert diferente["registros"] == {
        "adicionados": 1,
        "removidos": 1,
        "alterados": 1,
        "iguais": 13,
    }
    por_registro = {d["registro"]: d for d in diferente["diferencas"]}
    assert por_registro["90000002"]["tipo"] == "alterado"
    assert por_registro["90000002"]["campos"] == [
        {"campo": "capital_social", "antes": "100000,00", "depois": "999000,00"}
    ]
    assert por_registro["90000077"]["tipo"] == "adicionado"
    assert por_registro["90000014"]["tipo"] == "removido"

    # A carga antiga continua guardada e verificável (nada é sobrescrito).
    antiga = diferente["anterior_id"]
    assert (
        cliente.get(f"/capturas/{antiga}/verificar", headers=cabecalhos()).json()["integra"] is True
    )
    # A mesma comparação por captura (com a anterior da mesma origem) e pela coleta.
    r = cliente.get(f"/capturas/{diferente['captura_id']}/comparar", headers=cabecalhos())
    assert r.status_code == 200 and r.json()["anterior_id"] == antiga
    assert r.json()["campos_alterados"] == ["capital_social"]
    r = cliente.get(
        f"/casos/{caso_id}/coletas/{comparacao['coleta_id']}/comparacao", headers=cabecalhos()
    )
    assert r.status_code == 200 and r.json()["capturas_diferentes"] == 1
    assert _auditoria(cliente, caso_id, "captura.comparar")[0]["detalhes"]["mesmo_hash"] is False


def test_h14_recoleta_do_pncp_compara_por_contrato(tmp_path, raiz_cnpj):
    import json

    paginas = {n: (FIXTURES_PNCP / f"contratos_pagina{n}.json").read_bytes() for n in (1, 2)}
    transporte = TransporteSimulado(dict(paginas))
    app = criar_app(
        criar_config(tmp_path, raiz_cnpj), extras_conectores={"pncp": {"transporte": transporte}}
    )
    with TestClient(app) as cliente:
        caso_id = _novo_caso(cliente)
        primeira = _coletar(cliente, caso_id, "pncp", PERIODO).json()
        corpo = json.loads(paginas[1])
        corpo["data"][0]["valorGlobal"] = 250000.0
        corpo["data"][0]["orgaoEntidade"]["razaoSocial"] = "PREFEITURA SINTETICA RENOMEADA"
        transporte.paginas[1] = json.dumps(corpo).encode()
        r = cliente.post(
            f"/casos/{caso_id}/coletas/{primeira['coleta_id']}/recoletar", headers=cabecalhos()
        )
        assert r.status_code == 200, r.text
        comparacao = r.json()["comparacao"]
        assert comparacao["capturas_iguais"] == 1 and comparacao["capturas_diferentes"] == 1
        diferente = next(c for c in comparacao["capturas"] if not c["mesmo_hash"])
        assert diferente["campos_alterados"] == ["orgaoEntidade.razaoSocial", "valorGlobal"]
        assert diferente["registros"]["alterados"] == 1 and diferente["registros"]["iguais"] == 3
        assert diferente["diferencas"][0]["registro"] == "90100001000109-2-000001/2026"
        assert diferente["url_ou_arquivo"].startswith("simulado://")


def test_h14_comparacao_e_recoleta_nao_cruzam_casos(cliente, caso_id):
    """Regressão de segurança: coleta e captura de outro caso respondem como inexistentes."""
    primeira = _coletar(cliente, caso_id, "cnpj_arquivo", {"diretorio": DIR_CNPJ}).json()
    outro = _novo_caso(cliente, titulo="Outro caso")
    do_outro = _coletar(cliente, outro, "cnpj_arquivo", {"diretorio": DIR_CNPJ}).json()
    h = cabecalhos()
    assert (
        cliente.post(
            f"/casos/{outro}/coletas/{primeira['coleta_id']}/recoletar", headers=h
        ).status_code
        == 404
    )
    assert (
        cliente.get(
            f"/casos/{outro}/coletas/{primeira['coleta_id']}/comparacao", headers=h
        ).status_code
        == 404
    )
    r = cliente.get(
        f"/casos/{caso_id}/coletas/{primeira['coleta_id']}/comparacao",
        params={"com": do_outro["coleta_id"]},
        headers=h,
    )
    assert r.status_code == 404
    r = cliente.get(
        f"/capturas/{primeira['captura_ids'][0]}/comparar",
        params={"com": do_outro["captura_ids"][0]},
        headers=h,
    )
    assert r.status_code == 404
    # Sem captura anterior da mesma origem no caso, não há o que comparar.
    assert (
        cliente.get(f"/capturas/{primeira['captura_ids'][0]}/comparar", headers=h).status_code
        == 404
    )
    # Uma coleta que não é recoleta precisa dizer com qual comparar.
    assert (
        cliente.get(
            f"/casos/{caso_id}/coletas/{primeira['coleta_id']}/comparacao", headers=h
        ).status_code
        == 404
    )
    assert (
        cliente.post(f"/casos/{caso_id}/coletas/{'x' * 65}/recoletar", headers=h).status_code == 404
    )


# ==== H15 (RNF12): falhas de coleta por fonte e retentativa ====================================


def test_h15_retentativa_comum_conta_tentativas_e_desiste():
    esperas: list[float] = []
    politica = Retentativa(tentativas=3, espera_segundos=2.0, dormir=esperas.append)
    chamadas = []

    def falha_duas_vezes(ultima: bool) -> str:
        chamadas.append(ultima)
        if len(chamadas) < 3:
            raise FalhaTransitoria("fonte fora do ar")
        return "ok"

    assert politica.executar(falha_duas_vezes, "Falha") == "ok"
    assert chamadas == [False, False, True] and esperas == [2.0, 4.0]
    assert (
        politica.maior == 3 and politica.total == 3 and politica.ultima_falha == "fonte fora do ar"
    )

    def sempre_falha(_ultima: bool) -> str:
        raise FalhaTransitoria("sem resposta")

    with pytest.raises(ErroConector, match="Falha ao ler após 3 tentativas: sem resposta"):
        politica.executar(sempre_falha, "Falha ao ler")
    # Erro que não é transitório não é tentado de novo.
    unica = Retentativa(tentativas=5, dormir=esperas.append)
    with pytest.raises(ErroConector, match="definitivo"):
        unica.executar(lambda _u: (_ for _ in ()).throw(ErroConector("definitivo")), "Falha")
    assert unica.maior == 1
    # Todo conector tem nome, versão e a política de retentativa.
    for classe in CONECTORES.values():
        assert classe.nome and classe.versao != "0.0.0"


def test_h15_cnpj_arquivo_tenta_de_novo_em_erro_de_leitura(
    sessao, custodia, raiz_cnpj, monkeypatch
):
    caso = Caso(titulo="c", criado_por="t")
    sessao.add(caso)
    sessao.flush()

    def registrar(fonte, origem, conteudo, metadados):
        return custodia.registrar(
            sessao,
            caso_id=caso.id,
            fonte=fonte,
            url_ou_arquivo=origem,
            conteudo=conteudo,
            metadados=metadados,
        )

    abrir = os.open
    falhas = {"restantes": 2}

    def abrir_instavel(caminho, *args, **kwargs):
        if str(caminho).endswith("EMPRECSV") and falhas["restantes"] > 0:
            falhas["restantes"] -= 1
            raise OSError("leitura interrompida")
        return abrir(caminho, *args, **kwargs)

    monkeypatch.setattr("buscador.conectores.cnpj_arquivo.os.open", abrir_instavel)
    conector = ConectorCnpjArquivo(dir_base=raiz_cnpj, tentativas=3, espera_retentativa_segundos=0)
    resultado = conector.coletar({"diretorio": DIR_CNPJ}, registrar)
    assert len(resultado.capturas) == 3 and conector.tentativas == 3
    # Cada arquivo gerou uma só captura, mesmo com as tentativas repetidas.
    assert (
        len(list(sessao.execute(select(Captura).where(Captura.caso_id == caso.id)).scalars())) == 3
    )

    falhas["restantes"] = 99
    conector = ConectorCnpjArquivo(dir_base=raiz_cnpj, tentativas=2, espera_retentativa_segundos=0)
    with pytest.raises(ErroConector, match="EMPRECSV após 2 tentativas: OSError"):
        conector.coletar({"diretorio": DIR_CNPJ}, registrar)
    assert conector.tentativas == 2


def test_h15_coleta_informa_sucesso_falha_tentativas_e_motivo_por_fonte(tmp_path, raiz_cnpj):
    transporte = TransporteSimulado.de_diretorio(FIXTURES_PNCP, falhas_iniciais=2)
    app = criar_app(
        criar_config(tmp_path, raiz_cnpj), extras_conectores={"pncp": {"transporte": transporte}}
    )
    with TestClient(app) as cliente:
        caso_id = _novo_caso(cliente)
        r = _coletar(cliente, caso_id, "cnpj_arquivo", {"diretorio": DIR_CNPJ})
        assert r.status_code == 200
        assert (r.json()["situacao"], r.json()["tentativas"], r.json()["motivo"]) == (
            "sucesso",
            1,
            "",
        )

        # PNCP: duas respostas 503 e a terceira dá certo.
        r = _coletar(cliente, caso_id, "pncp", PERIODO)
        assert r.status_code == 200, r.text
        assert r.json()["situacao"] == "sucesso" and r.json()["tentativas"] == 3
        assert "3 tentativas" in r.json()["motivo"] and "HTTP 503" in r.json()["motivo"]

        # PNCP fora do ar de vez: falha depois de esgotar as tentativas.
        transporte.falhas_restantes = 99
        r = _coletar(cliente, caso_id, "pncp", PERIODO)
        assert r.status_code == 422 and "HTTP 503" in r.json()["detail"]
        # Fonte sem chave configurada: "não configurado" (H19), sem nenhuma tentativa e sem
        # falhar. Parâmetro inválido: falha sem nenhuma tentativa.
        r = _coletar(
            cliente,
            caso_id,
            "portal_transparencia",
            {"recurso": "ceis", "codigoSancionado": ["90000001000129"]},
        )
        assert r.status_code == 200 and r.json()["situacao"] == "nao_configurado"
        assert (
            _coletar(cliente, caso_id, "cnpj_arquivo", {"diretorio": "nao-existe"}).status_code
            == 422
        )

        coletas = cliente.get(f"/casos/{caso_id}/coletas", headers=cabecalhos()).json()
        assert [c["fonte"] for c in coletas] == [
            "cnpj_arquivo",
            "portal_transparencia",
            "pncp",
            "pncp",
            "cnpj_arquivo",
        ]
        assert [c["situacao"] for c in coletas] == [
            "falha",
            "nao_configurado",
            "falha",
            "sucesso",
            "sucesso",
        ]
        assert [c["tentativas"] for c in coletas] == [0, 0, 3, 3, 1]
        falha_pncp = coletas[2]
        assert "HTTP 503" in falha_pncp["motivo"] and falha_pncp["concluido_em"]
        # A resposta de erro do PNCP ficou na custódia e está ligada à coleta que falhou.
        assert len(falha_pncp["captura_ids"]) == 1
        assert "PORTAL_TRANSPARENCIA_CHAVE" in coletas[1]["motivo"]
        assert "CNPJ_DIR_BASE" in coletas[0]["motivo"] and str(tmp_path) not in coletas[0]["motivo"]
        assert all(c["usuario"] == "ana" and c["conector_versao"] for c in coletas)

        falhou = _auditoria(cliente, caso_id, "coleta.falhou")
        assert len(_auditoria(cliente, caso_id, "coleta.nao_configurada")) == 1
        assert len(falhou) == 2
        assert {e["detalhes"]["tentativas"] for e in falhou} == {0, 3}
        assert all(e["detalhes"]["coleta_id"] for e in falhou)

        d = cliente.get(f"/casos/{caso_id}/relatorio", headers=cabecalhos()).json()
        assert d["resumo"]["coletas"] == 5 and d["resumo"]["coletas_com_falha"] == 2
        assert [c["situacao"] for c in d["coletas"]].count("falha") == 2
        md = cliente.get(
            f"/casos/{caso_id}/relatorio", params={"formato": "md"}, headers=cabecalhos()
        ).text
        assert "## Coletas" in md and "2 coleta(s) deste caso falharam" in md
        assert "| FALHA | 3 |" in md and "| sucesso | 1 |" in md


def test_h15_falha_inesperada_nao_expoe_detalhe_interno_no_historico(cliente, caso_id, monkeypatch):
    """Regressão de segurança: o motivo visível a todos os perfis não traz o erro interno."""

    def explodir(*_args, **_kwargs):
        raise RuntimeError("segredo interno /caminho/do/servidor")

    monkeypatch.setattr("buscador.resolucao.Resolvedor.incorporar", explodir)
    r = _coletar(cliente, caso_id, "cnpj_arquivo", {"diretorio": DIR_CNPJ})
    assert r.status_code == 500 and "segredo" not in r.text
    coleta = cliente.get(f"/casos/{caso_id}/coletas", headers=cabecalhos()).json()[0]
    assert coleta["situacao"] == "falha" and coleta["tentativas"] == 1
    assert "segredo" not in coleta["motivo"] and "RuntimeError" in coleta["motivo"]
    # As capturas já guardadas continuam registradas e ligadas à coleta que falhou.
    assert len(coleta["captura_ids"]) == 3


# ==== H17 (RNF04): finalidade e prazo de retenção ==============================================


def test_h17_caso_exige_finalidade_e_prazo_de_retencao(cliente):
    hoje = retencao.hoje_utc()
    invalidos = [
        {"titulo": "sem finalidade", "retencao_ate": "2099-12-31"},
        {"titulo": "sem prazo", "finalidade": "Apurar contrato"},
        corpo_caso(finalidade=""),
        corpo_caso(finalidade="   "),
        corpo_caso(finalidade="???"),
        corpo_caso(finalidade="a\x00b"),
        corpo_caso(finalidade="x" * 2001),
        corpo_caso(retencao_ate="31/12/2099"),
        corpo_caso(retencao_ate="2099-02-30"),
        corpo_caso(retencao_ate=""),
        corpo_caso(retencao_ate=(hoje - timedelta(days=1)).isoformat()),
    ]
    for corpo in invalidos:
        assert cliente.post("/casos", json=corpo, headers=cabecalhos()).status_code == 422, corpo

    r = cliente.post(
        "/casos",
        json=corpo_caso(finalidade="Apurar o contrato 1/2026", retencao_ate=hoje.isoformat()),
        headers=cabecalhos(),
    )
    assert r.status_code == 201, r.text
    caso = r.json()
    assert caso["finalidade"] == "Apurar o contrato 1/2026"
    assert caso["retencao_ate"] == hoje.isoformat() and caso["somente_leitura"] is False
    evento = _auditoria(cliente, caso["id"], "caso.criar")[0]["detalhes"]
    assert evento["finalidade"] == "Apurar o contrato 1/2026"
    assert evento["retencao_ate"] == hoje.isoformat()
    d = cliente.get(f"/casos/{caso['id']}/relatorio", headers=cabecalhos()).json()
    assert d["caso"]["finalidade"] == "Apurar o contrato 1/2026"
    md = cliente.get(
        f"/casos/{caso['id']}/relatorio", params={"formato": "md"}, headers=cabecalhos()
    ).text
    assert "- Finalidade: Apurar o contrato 1/2026" in md
    assert f"- Prazo de retenção: {hoje.isoformat()}" in md and "vencido" not in md


def test_h17_o_ultimo_dia_do_prazo_ainda_vale(cliente, caso_id, monkeypatch):
    monkeypatch.setattr(retencao, "hoje_utc", lambda: date(2099, 12, 31))
    assert _coletar(cliente, caso_id, "cnpj_arquivo", {"diretorio": DIR_CNPJ}).status_code == 200
    monkeypatch.setattr(retencao, "hoje_utc", lambda: date(2100, 1, 1))
    assert _coletar(cliente, caso_id, "cnpj_arquivo", {"diretorio": DIR_CNPJ}).status_code == 403
    assert cliente.get(f"/casos/{caso_id}", headers=cabecalhos()).json()["somente_leitura"] is True


def test_h17_caso_vencido_fica_so_leitura_e_a_recusa_entra_na_auditoria(
    cliente, caso_carregado, app
):
    h = cabecalhos()
    alertas = _executar(cliente, caso_carregado)
    empresa = entidade_por_identificador(cliente, caso_carregado, "90000001", "empresa")
    coletas = cliente.get(f"/casos/{caso_carregado}/coletas", headers=h).json()
    antes = cliente.get(f"/casos/{caso_carregado}", headers=h).json()["contagens"]
    vencido_em = _vencer(app, caso_carregado)

    recusadas = {
        "coleta.iniciar": lambda: _coletar(
            cliente, caso_carregado, "cnpj_arquivo", {"diretorio": DIR_CNPJ}
        ),
        "coleta.recoletar": lambda: cliente.post(
            f"/casos/{caso_carregado}/coletas/{coletas[0]['id']}/recoletar", headers=h
        ),
        "entidade.buscar": lambda: cliente.get(f"/casos/{caso_carregado}/entidades", headers=h),
        "entidade.ver": lambda: cliente.get(
            f"/casos/{caso_carregado}/entidades/{empresa['id']}", headers=h
        ),
        "grafo.ver": lambda: cliente.get(f"/casos/{caso_carregado}/grafo", headers=h),
        "caminho.ver": lambda: cliente.get(
            f"/casos/{caso_carregado}/caminho",
            params={"de": empresa["id"], "para": empresa["id"]},
            headers=h,
        ),
        "alerta.executar": lambda: cliente.post(
            f"/casos/{caso_carregado}/alertas/executar", json={}, headers=h
        ),
        "alerta.decidir": lambda: _decidir(
            cliente, caso_carregado, alertas[0]["id"], "confirmado", "depois do prazo"
        ),
    }
    for acao, chamar in recusadas.items():
        r = chamar()
        assert r.status_code == 403, (acao, r.text)
        assert vencido_em in r.json()["detail"] and "só para leitura" in r.json()["detail"]

    eventos = _auditoria(cliente, caso_carregado, "caso.retencao_recusa")
    assert [e["detalhes"]["acao_recusada"] for e in eventos] == list(recusadas)
    assert all(
        e["detalhes"]["retencao_ate"] == vencido_em and e["usuario"] == "ana" for e in eventos
    )

    # O que já existe continua legível, e nada foi criado, alterado nem apagado.
    detalhe = cliente.get(f"/casos/{caso_carregado}", headers=h).json()
    assert detalhe["somente_leitura"] is True and detalhe["contagens"] == antes
    listados = cliente.get(f"/casos/{caso_carregado}/alertas", headers=h).json()
    assert len(listados) == len(alertas) and all(a["situacao"] == "pendente" for a in listados)
    assert len(cliente.get(f"/casos/{caso_carregado}/coletas", headers=h).json()) == len(coletas)
    captura_id = coletas[0]["captura_ids"][0]
    assert cliente.get(f"/capturas/{captura_id}/verificar", headers=h).json()["integra"] is True
    d = cliente.get(f"/casos/{caso_carregado}/relatorio", headers=h).json()
    assert d["caso"]["somente_leitura"] is True and d["custodia_integra"] is True
    assert d["auditoria"]["integra"] is True and d["resumo"]["capturas"] == antes["capturas"]
    md = cliente.get(f"/casos/{caso_carregado}/relatorio", params={"formato": "md"}, headers=h).text
    assert "vencido: caso só para leitura" in md
    assert cliente.get("/auditoria/verificar", headers=h).json()["integra"] is True


def test_h17_caso_sem_prazo_de_versao_anterior_continua_aberto(cliente, caso_id, app):
    with app.state.fabrica_sessoes() as s:
        caso = s.get(Caso, caso_id)
        caso.retencao_ate, caso.finalidade = None, ""
        s.commit()
    detalhe = cliente.get(f"/casos/{caso_id}", headers=cabecalhos()).json()
    assert detalhe["retencao_ate"] is None and detalhe["somente_leitura"] is False
    assert _coletar(cliente, caso_id, "cnpj_arquivo", {"diretorio": DIR_CNPJ}).status_code == 200
    md = cliente.get(
        f"/casos/{caso_id}/relatorio", params={"formato": "md"}, headers=cabecalhos()
    ).text
    assert "- Finalidade: (não informada)" in md and "- Prazo de retenção: (não definido)" in md


# ==== H31 (RF04): T4, empresa contratada sem situação cadastral ativa ==========================


@pytest.fixture
def sessao_caso(app, caso_carregado):
    s = app.state.fabrica_sessoes()
    try:
        yield s, caso_carregado
    finally:
        s.close()


PADRAO = {"t1_dias_minimos": 180, "t2_minimo_empresas": 3, "t3_razao_minima": 10.0}


def test_h31_t4_dispara_para_baixada_e_suspensa_com_regra_motivo_e_evidencias(sessao_caso):
    sessao, caso_id = sessao_caso
    alertas = tipologias.executar(sessao, caso_id, PADRAO, ["T4"])
    assert len(alertas) == 2 and all(
        a.tipologia == "T4" and a.severidade == "alta" for a in alertas
    )
    por_situacao = {a.parametros["situacao_cadastral"]: a for a in alertas}

    baixada = por_situacao["BAIXADA"]
    rotulos = {sessao.get(Entidade, eid).rotulo for eid in baixada.entidades}
    assert "OMICRON SAUDE SINTETICA LTDA" in rotulos
    assert baixada.titulo.startswith("Empresa com situação cadastral BAIXADA no contrato")
    assert "BAIXADA (código 08)" in baixada.motivo and "90000015000142" in baixada.motivo
    assert "desde 30/06/2025" in baixada.motivo and "10/02/2026" in baixada.motivo
    assert "225 dias antes da assinatura" in baixada.motivo
    assert "qualquer situação diferente de ATIVA (código 02)" in baixada.motivo
    assert baixada.parametros["situacao_cadastral_codigo"] == "08"
    assert baixada.parametros["situacao_anterior_ao_contrato"] is True
    assert baixada.parametros["dias_entre_situacao_e_assinatura"] == 225
    assert baixada.parametros["limiares"] == PADRAO
    assert [e["fonte"] for e in baixada.evidencias] == ["cnpj_arquivo", "pncp"]
    assert "Estabelecimentos" in baixada.evidencias[0]["descricao"]
    assert baixada.evidencias[0]["url_ou_arquivo"].endswith("ESTABELE")
    assert all(len(e["sha256"]) == 64 and e["captura_id"] for e in baixada.evidencias)

    # Suspensa depois da assinatura: dispara (a política evita falso negativo) e diz a ordem.
    suspensa = por_situacao["SUSPENSA"]
    assert "SUSPENSA (código 03)" in suspensa.motivo
    assert "104 dias depois da assinatura" in suspensa.motivo
    assert suspensa.parametros["situacao_anterior_ao_contrato"] is False
    assert suspensa.parametros["dias_entre_situacao_e_assinatura"] == -104


def test_h31_t4_nao_dispara_para_ativa_nem_para_empresa_sem_dado_do_cnpj(sessao_caso):
    sessao, caso_id = sessao_caso
    alertas = tipologias.executar(sessao, caso_id, PADRAO, ["T4"])
    rotulos = {sessao.get(Entidade, eid).rotulo for a in alertas for eid in a.entidades}
    # ALFA (ativa, com contrato) e PI (só no PNCP, sem situação conhecida) ficam de fora.
    assert not any("ALFA" in r or "PI FORNECEDOR" in r for r in rotulos)
    empresas = {r for r in rotulos if "SINTETICA LTDA" in r}
    assert empresas == {"OMICRON SAUDE SINTETICA LTDA", "IOTA ALIMENTOS SINTETICA LTDA"}


def test_h31_t4_le_o_codigo_com_ou_sem_zero_e_sinaliza_codigo_fora_do_layout(sessao_caso):
    from sqlalchemy.orm.attributes import flag_modified

    sessao, caso_id = sessao_caso
    alfa = sessao.execute(
        select(Entidade).where(Entidade.caso_id == caso_id, Entidade.identificador == "90000001")
    ).scalar_one()

    def com_situacao(codigo: str, data: str | None = "2026-01-01") -> list[Alerta]:
        atributos = dict(alfa.atributos)
        atributos["estabelecimentos"] = [
            {**est, "situacao_cadastral": codigo, "data_situacao_cadastral": data}
            for est in atributos["estabelecimentos"]
        ]
        alfa.atributos = atributos
        flag_modified(alfa, "atributos")
        sessao.flush()
        return [
            a
            for a in tipologias.executar(sessao, caso_id, PADRAO, ["T4"])
            if alfa.id in a.entidades
        ]

    # "2" e "02" são a mesma situação ATIVA no layout: não disparam; vazio também não.
    for ativa_ou_vazia in ("2", "02", "", "  "):
        assert com_situacao(ativa_ou_vazia) == []
    for codigo, nome in (("4", "INAPTA"), ("04", "INAPTA"), ("01", "NULA"), ("3", "SUSPENSA")):
        (alerta,) = com_situacao(codigo)
        assert alerta.parametros["situacao_cadastral"] == nome
    (sem_data,) = com_situacao("08", None)
    assert "a data dessa situação não foi informada" in sem_data.motivo
    assert sem_data.parametros["situacao_anterior_ao_contrato"] is None
    (desconhecido,) = com_situacao("99")
    assert "código 99, que não consta do layout oficial" in desconhecido.motivo
    assert desconhecido.parametros["situacao_cadastral"] == "não reconhecida"
    assert set(tipologias.SITUACOES_CADASTRAIS.values()) == {
        "NULA",
        "ATIVA",
        "SUSPENSA",
        "INAPTA",
        "BAIXADA",
    }


def test_h31_t4_pela_api_e_no_relatorio(cliente, caso_carregado):
    r = cliente.post(
        f"/casos/{caso_carregado}/alertas/executar",
        json={"tipologias": ["T4"]},
        headers=cabecalhos(),
    )
    assert r.status_code == 200 and [a["tipologia"] for a in r.json()] == ["T4", "T4"]
    assert all(a["tipologia_nome"] == POLITICAS["T4"].nome for a in r.json())
    r = cliente.post(
        f"/casos/{caso_carregado}/alertas/executar",
        json={"tipologias": ["T11"]},
        headers=cabecalhos(),
    )
    assert r.status_code == 422
    md = cliente.get(
        f"/casos/{caso_carregado}/relatorio", params={"formato": "md"}, headers=cabecalhos()
    ).text
    assert "### [T4] Empresa com situação cadastral BAIXADA" in md


# ==== Evolução do esquema (SQLite; o PostgreSQL fica em test_seguranca.py, com banco real) =====

ESQUEMA_ANTERIOR = [
    """CREATE TABLE casos (id VARCHAR(32) PRIMARY KEY, titulo VARCHAR(300) NOT NULL,
       descricao TEXT NOT NULL, numero_procedimento VARCHAR(100) NOT NULL,
       criado_por VARCHAR(100) NOT NULL, criado_em VARCHAR(40) NOT NULL)""",
    """CREATE TABLE capturas (id VARCHAR(32) PRIMARY KEY,
       caso_id VARCHAR(32) NOT NULL REFERENCES casos (id), fonte VARCHAR(50) NOT NULL,
       url_ou_arquivo TEXT NOT NULL, coletado_em VARCHAR(40) NOT NULL, sha256 VARCHAR(64) NOT NULL,
       tamanho_bytes INTEGER NOT NULL, caminho_relativo VARCHAR(200) NOT NULL,
       metadados JSON NOT NULL)""",
    """CREATE TABLE alertas (id VARCHAR(32) PRIMARY KEY,
       caso_id VARCHAR(32) NOT NULL REFERENCES casos (id), tipologia VARCHAR(5) NOT NULL,
       titulo VARCHAR(300) NOT NULL, motivo TEXT NOT NULL, severidade VARCHAR(10) NOT NULL,
       entidades JSON NOT NULL, evidencias JSON NOT NULL, parametros JSON NOT NULL,
       criado_em VARCHAR(40) NOT NULL)""",
    """INSERT INTO casos VALUES ('c1', 'Caso antigo', '', 'P-1', 'ana', '2026-11-11T00:00:00Z')""",
    """INSERT INTO capturas VALUES ('k1', 'c1', 'pncp', 'simulado://x', '2026-11-11T00:00:00Z',
       '0000000000000000000000000000000000000000000000000000000000000000', 0, '00/0', '{}')""",
    """INSERT INTO alertas VALUES ('a1', 'c1', 'T1', 't', 'm', 'alta', '[]', '[]', '{}',
       '2026-11-11T00:00:00Z')""",
]
COLUNAS_NOVAS = {
    "casos": {"finalidade", "retencao_ate"},
    "capturas": {"conector_nome", "conector_versao", "coleta_id"},
    "alertas": {
        "chave",
        "ativo",
        "situacao",
        "decidido_por",
        "decidido_em",
        "justificativa_decisao",
    },
}


def _banco_da_versao_anterior(url: str):
    engine = criar_engine(url)
    with engine.begin() as conexao:
        for sql in ESQUEMA_ANTERIOR:
            conexao.execute(text(sql))
    return engine


def test_migracao_acrescenta_colunas_e_tabelas_sem_perder_dados(tmp_path):
    engine = _banco_da_versao_anterior(f"sqlite:///{tmp_path / 'antigo.db'}")
    # Sem migrar, o serviço recusa o banco e diz o que falta (DB_CRIAR_ESQUEMA=0).
    with pytest.raises(ErroEsquema, match="Tabelas ausentes"):
        verificar_esquema(engine)

    feitos = criar_esquema(engine)
    for tabela, colunas in COLUNAS_NOVAS.items():
        assert {f"{tabela}.{c}" for c in colunas} <= set(feitos)
        assert colunas <= {c["name"] for c in inspect(engine).get_columns(tabela)}
    assert {"coletas", "alerta_decisoes", "auditoria"} <= set(inspect(engine).get_table_names())
    assert "ix_capturas_coleta_id" in {i["name"] for i in inspect(engine).get_indexes("capturas")}
    verificar_esquema(engine)
    # É idempotente: rodar de novo não muda nada.
    assert criar_esquema(engine) == []

    from buscador.db import criar_fabrica_sessoes

    with criar_fabrica_sessoes(engine)() as s:
        caso = s.get(Caso, "c1")
        assert (caso.titulo, caso.finalidade, caso.retencao_ate) == ("Caso antigo", "", None)
        assert retencao.vencida(caso) is False
        captura = s.get(Captura, "k1")
        assert (captura.conector_nome, captura.conector_versao, captura.coleta_id) == ("", "", None)
        alerta = s.get(Alerta, "a1")
        assert (alerta.situacao, alerta.ativo, alerta.chave, alerta.decisoes) == (
            "pendente",
            True,
            "",
            [],
        )
        assert list(s.execute(select(Coleta)).scalars()) == []
    # Os gatilhos da auditoria continuam lá depois da migração.
    gatilhos = {
        linha[0]
        for linha in engine.connect().execute(
            text("SELECT name FROM sqlite_master WHERE type = 'trigger'")
        )
    }
    assert {
        "auditoria_sem_update",
        "auditoria_sem_delete",
        "alerta_decisoes_sem_update",
    } <= gatilhos
    engine.dispose()


def test_migracao_pelo_script_e_servico_sobe_com_o_banco_antigo(
    tmp_path, raiz_cnpj, monkeypatch, capsys
):
    from buscador import migrar

    url = f"sqlite:///{tmp_path / 'antigo.db'}"
    _banco_da_versao_anterior(url).dispose()
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.delenv("DB_PAPEL_APP", raising=False)
    assert migrar.main() == 0
    assert "casos.finalidade" in capsys.readouterr().out

    config = criar_config(tmp_path, raiz_cnpj, database_url=url, db_criar_esquema=False)
    app = criar_app(config)
    with TestClient(app) as cliente:
        # O caso antigo aparece, sem finalidade e sem prazo, e aceita coleta e alertas novos.
        (antigo,) = cliente.get("/casos", headers=cabecalhos()).json()
        assert antigo["finalidade"] == "" and antigo["retencao_ate"] is None
        assert _coletar(cliente, "c1", "cnpj_arquivo", {"diretorio": DIR_CNPJ}).status_code == 200
        # O alerta antigo (sem chave) é substituído na reexecução; não havia decisão a preservar.
        assert _executar(cliente, "c1", tipologias=["T1"]) == []
        assert cliente.get("/casos/c1/alertas", headers=cabecalhos()).json() == []
    app.state.engine.dispose()


def test_servico_recusa_banco_sem_as_colunas_novas(tmp_path):
    """Com ``DB_CRIAR_ESQUEMA=0`` o serviço não migra sozinho: diz quais colunas faltam."""
    engine = criar_engine(f"sqlite:///{tmp_path / 'parcial.db'}")
    criar_esquema(engine)
    with engine.begin() as conexao:
        conexao.execute(text("ALTER TABLE casos DROP COLUMN finalidade"))
    with pytest.raises(ErroEsquema, match=r"casos\.finalidade.*buscador\.migrar"):
        verificar_esquema(engine)
    assert criar_esquema(engine) == ["casos.finalidade"]
    engine.dispose()


def test_transporte_que_falha_conta_tentativas_na_coleta(tmp_path, raiz_cnpj):
    class SemRede(httpx.BaseTransport):
        simulado = True

        def handle_request(self, request):
            raise httpx.ConnectError("sem rede", request=request)

    app = criar_app(
        criar_config(tmp_path, raiz_cnpj, pncp_tentativas=2),
        extras_conectores={"pncp": {"transporte": SemRede()}},
    )
    with TestClient(app) as cliente:
        caso_id = _novo_caso(cliente)
        r = _coletar(cliente, caso_id, "pncp", PERIODO)
        assert r.status_code == 422 and "2 tentativas" in r.json()["detail"]
        (coleta,) = cliente.get(f"/casos/{caso_id}/coletas", headers=cabecalhos()).json()
        assert (coleta["situacao"], coleta["tentativas"]) == ("falha", 2)
        assert "ConnectError" in coleta["motivo"] and coleta["captura_ids"] == []


def test_caminho_da_fixture_e_sintetico():
    marcador = Path(__file__).parent / "fixtures" / "cnpj" / "SINTETICO.txt"
    assert marcador.exists() and "SINTETICOS" in marcador.read_text(encoding="utf-8")


# ==== PostgreSQL (rodam só com TEST_POSTGRES_URL e TEST_POSTGRES_URL_APP; no CI, sempre) ======

URL_DONO = os.environ.get("TEST_POSTGRES_URL", "")
URL_APP = os.environ.get("TEST_POSTGRES_URL_APP", "")
PAPEL_APP = os.environ.get("TEST_POSTGRES_PAPEL_APP", "osint_app")
exige_postgres = pytest.mark.skipif(
    not (URL_DONO and URL_APP), reason="defina TEST_POSTGRES_URL e TEST_POSTGRES_URL_APP"
)


@pytest.fixture
def postgres_vazio():
    """Banco descartável sem nenhuma tabela do serviço; limpa tudo ao final."""
    from buscador.modelos import Base

    dono = criar_engine(URL_DONO)
    Base.metadata.drop_all(dono)
    yield dono
    Base.metadata.drop_all(dono)
    dono.dispose()


@exige_postgres
def test_postgres_migracao_acrescenta_colunas_e_mantem_gatilhos_e_privilegios(postgres_vazio):
    from buscador.db import conceder_privilegios, criar_fabrica_sessoes

    dono = postgres_vazio
    with dono.begin() as conexao:
        for sql in ESQUEMA_ANTERIOR:
            conexao.execute(text(sql))
    feitos = criar_esquema(dono)
    conceder_privilegios(dono, PAPEL_APP)
    for tabela, colunas in COLUNAS_NOVAS.items():
        assert {f"{tabela}.{c}" for c in colunas} <= set(feitos)
        assert colunas <= {c["name"] for c in inspect(dono).get_columns(tabela)}
    assert "ix_capturas_coleta_id" in {i["name"] for i in inspect(dono).get_indexes("capturas")}
    assert criar_esquema(dono) == []
    verificar_esquema(dono)

    app = criar_engine(URL_APP)
    with criar_fabrica_sessoes(app)() as s:
        caso = s.get(Caso, "c1")
        assert (caso.finalidade, caso.retencao_ate) == ("", None)
        alerta = s.get(Alerta, "a1")
        assert (alerta.situacao, alerta.ativo, alerta.chave) == ("pendente", True, "")
        assert s.get(Captura, "k1").conector_versao == ""
        # O papel da aplicação acrescenta decisões, mas não as altera nem apaga.
        s.add(
            AlertaDecisao(
                alerta_id="a1", caso_id="c1", decisao="confirmado", justificativa="j", usuario="ana"
            )
        )
        s.commit()
    proibidos = [
        "UPDATE alerta_decisoes SET decisao = 'x'",
        "DELETE FROM alerta_decisoes",
        "TRUNCATE alerta_decisoes",
        "UPDATE capturas SET conector_versao = '9.9.9'",
        "UPDATE auditoria SET usuario = 'x'",
        "DROP TRIGGER alerta_decisoes_sem_update ON alerta_decisoes",
        "ALTER TABLE casos DROP COLUMN finalidade",
    ]
    for sql in proibidos:
        with pytest.raises(DBAPIError), app.begin() as conexao:
            conexao.execute(text(sql))
    # Até o dono é barrado pelos gatilhos.
    for sql in ("UPDATE alerta_decisoes SET decisao = 'x'", "TRUNCATE alerta_decisoes"):
        with pytest.raises(DBAPIError, match="somente de acrescimo"), dono.begin() as conexao:
            conexao.execute(text(sql))
    app.dispose()


@exige_postgres
def test_postgres_horizonte1_com_o_papel_da_aplicacao(postgres_vazio, tmp_path, raiz_cnpj):
    from buscador.db import conceder_privilegios

    dono = postgres_vazio
    criar_esquema(dono)
    conceder_privilegios(dono, PAPEL_APP)
    config = criar_config(tmp_path, raiz_cnpj, database_url=URL_APP, db_criar_esquema=False)
    transporte = TransporteSimulado.de_diretorio(FIXTURES_PNCP)
    app = criar_app(config, extras_conectores={"pncp": {"transporte": transporte}})
    with TestClient(app) as cliente:
        caso_id = _novo_caso(cliente)
        primeira = _coletar(cliente, caso_id, "cnpj_arquivo", {"diretorio": DIR_CNPJ})
        assert primeira.status_code == 200, primeira.text
        assert _coletar(cliente, caso_id, "pncp", PERIODO).status_code == 200
        assert _coletar(cliente, caso_id, "portal_transparencia", {}).status_code == 422
        r = cliente.post(
            f"/casos/{caso_id}/coletas/{primeira.json()['coleta_id']}/recoletar",
            headers=cabecalhos(),
        )
        assert r.status_code == 200 and r.json()["comparacao"]["mesmo_conteudo"] is True
        alertas = _executar(cliente, caso_id)
        # Sete alertas das T, dois da L5 e dois da L6; a T4 (alta) abre a fila.
        assert [a["tipologia"] for a in alertas][:2] == ["T4", "T4"] and len(alertas) == 11
        assert _decidir(cliente, caso_id, alertas[0]["id"], "confirmado", "ok").status_code == 200
        assert (
            _decidir(cliente, caso_id, alertas[0]["id"], "descartado", "revisto").status_code == 200
        )
        de_novo = {a["id"]: a for a in _executar(cliente, caso_id)}
        assert len(de_novo[alertas[0]["id"]]["decisoes"]) == 2
        coletas = cliente.get(f"/casos/{caso_id}/coletas", headers=cabecalhos()).json()
        assert [c["situacao"] for c in coletas].count("falha") == 1
        _vencer(app, caso_id)
        assert _coletar(cliente, caso_id, "pncp", PERIODO).status_code == 403
        assert len(_auditoria(cliente, caso_id, "caso.retencao_recusa")) == 1
        assert cliente.get("/auditoria/verificar", headers=cabecalhos()).json()["integra"] is True
    app.state.engine.dispose()
