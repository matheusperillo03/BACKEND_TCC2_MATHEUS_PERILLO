"""Horizonte 2 do backlog: H19 (novas fontes), H32 a H36 e H39 (tipologias T5 a T10) e H18
(monitoramento contínuo). Só dados sintéticos (tests/fixtures/h2, gerados por
scripts/gerar_fixtures_h2.py) e nenhuma chamada de rede: os conectores de API usam transportes
simulados."""

from __future__ import annotations

import json
import os
import shutil
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect, select, text

from buscador import retencao, tipologias
from buscador.agendador import Agendador
from buscador.api import criar_app
from buscador.conectores import (
    ControleTaxa,
    ErroConector,
    FonteNaoConfigurada,
    Retentativa,
    criar_conector,
)
from buscador.conectores.edital_arquivo import ConectorEditalArquivo
from buscador.conectores.http_util import TransporteRotas
from buscador.conectores.pncp_termos import ConectorPncpTermos
from buscador.conectores.portal_transparencia import ConectorPortalTransparencia
from buscador.conectores.simulados import extras_de_diretorio, transporte_portal, transporte_termos
from buscador.conectores.tse_arquivo import ConectorTseArquivo
from buscador.db import criar_engine, criar_esquema, verificar_esquema
from buscador.modelos import (
    Aresta,
    Aviso,
    Base,
    Caso,
    Entidade,
    Monitoramento,
    MonitoramentoExecucao,
    Sugestao,
)
from buscador.tipologias import legislacao
from buscador.tipologias.politicas import POLITICAS
from tests.conftest import (
    DIR_CNPJ,
    FIXTURES,
    cabecalhos,
    corpo_caso,
    criar_config,
    incluir_equipe,
)

H2 = FIXTURES / "h2"
PERIODO = {"dataInicial": "20260101", "dataFinal": "20261231"}
CNPJ_ORGAO = "90100003000190"
CNPJ_ZETA = "90000006000151"
CNPJ_ETA = "90000007000104"
SIAPE = "99001"
CHAVE_PORTAL = "chave-sintetica-do-portal"
# CPFs completos (DV inválido) que existem só nas cargas brutas das fixtures.
CPFS_COMPLETOS = ("00066666646", "000.666.666-46", "00077777705", "00088888854", "00012312366")


def test_fixtures_h2_sao_sinteticas_e_o_cnpj_do_orgao_confere():
    assert "sintéticos" in (H2 / "SINTETICO.txt").read_text(encoding="utf-8")
    pagina = json.loads((H2 / "pncp" / "contratos_pagina1.json").read_text(encoding="utf-8"))
    assert {c["orgaoEntidade"]["cnpj"] for c in pagina["data"]} == {CNPJ_ORGAO}
    assert CNPJ_ZETA in json.loads((H2 / "portal" / "ceis.json").read_text(encoding="utf-8"))


# ==== Apoio =====================================================================================


@pytest.fixture
def raizes(tmp_path: Path, raiz_cnpj: Path) -> dict[str, Path]:
    """Raízes de ingestão dos testes, com cópias das fixtures (os testes podem alterá-las)."""
    tse = tmp_path / "ingestao_tse"
    editais = tmp_path / "ingestao_editais"
    shutil.copytree(H2 / "tse", tse / "eleicao2024")
    shutil.copytree(H2 / "editais", editais / "pregao")
    return {"cnpj": raiz_cnpj, "tse": tse, "editais": editais}


@pytest.fixture
def extras() -> dict:
    """Extras dos conectores (transportes simulados). As bases do CEIS e dos termos ficam
    acessíveis em ``_ceis`` e ``_termos``, para o teste alterá-las entre duas coletas."""
    ceis = json.loads((H2 / "portal" / "ceis.json").read_text(encoding="utf-8"))
    cnep = json.loads((H2 / "portal" / "cnep.json").read_text(encoding="utf-8"))
    servidores = json.loads((H2 / "portal" / "servidores.json").read_text(encoding="utf-8"))
    termos = json.loads((H2 / "termos" / "termos.json").read_text(encoding="utf-8"))
    base = extras_de_diretorio(H2)
    # ``dormir`` sem efeito: o controle de taxa é exercitado, mas os testes não esperam de fato.
    base["portal_transparencia"] = {
        "transporte": transporte_portal(ceis, cnep, servidores),
        "dormir": lambda _segundos: None,
    }
    base["pncp_termos"] = {"transporte": transporte_termos(termos)}
    base["_ceis"] = ceis
    base["_termos"] = termos
    return base


@pytest.fixture
def app_h2(tmp_path: Path, raizes, extras):
    config = criar_config(
        tmp_path,
        raizes["cnpj"],
        tse_dir_base=raizes["tse"],
        editais_dir_base=raizes["editais"],
        portal_transparencia_chave=CHAVE_PORTAL,
        portal_transparencia_requisicoes_por_minuto=700,
        pncp_termos_requisicoes_por_minuto=0,
    )
    return criar_app(config, extras_conectores=extras)


@pytest.fixture
def cliente_h2(app_h2):
    with TestClient(app_h2) as c:
        yield c


def _coletar(cliente, caso_id: str, fonte: str, parametros: dict):
    return cliente.post(
        f"/casos/{caso_id}/coletas",
        json={"fonte": fonte, "parametros": parametros},
        headers=cabecalhos(),
    )


COLETAS_H2 = [
    ("cnpj_arquivo", {"diretorio": DIR_CNPJ}),
    ("pncp", PERIODO),
    ("pncp_termos", {"do_caso": True}),
    ("portal_transparencia", {"recurso": "ceis", "do_caso": True}),
    ("portal_transparencia", {"recurso": "cnep", "do_caso": True}),
    (
        "portal_transparencia",
        {"recurso": "servidores", "orgaoServidorLotacao": SIAPE, "cnpjOrgao": CNPJ_ORGAO},
    ),
    ("tse_arquivo", {"diretorio": "eleicao2024"}),
    ("edital_arquivo", {"diretorio": "pregao", "cnpjOrgao": CNPJ_ORGAO}),
]


@pytest.fixture
def caso_h2(cliente_h2) -> str:
    """Caso com todas as fontes do horizonte 2 coletadas (dados sintéticos)."""
    r = cliente_h2.post("/casos", json=corpo_caso("Caso H2"), headers=cabecalhos())
    assert r.status_code == 201, r.text
    caso_id = r.json()["id"]
    incluir_equipe(cliente_h2, caso_id)
    for fonte, parametros in COLETAS_H2:
        r = _coletar(cliente_h2, caso_id, fonte, parametros)
        assert r.status_code == 200 and r.json()["situacao"] == "sucesso", (fonte, r.text)
    return caso_id


def _alertas(cliente, caso_id: str, tipologia: str, **parametros) -> list[dict]:
    corpo = {"tipologias": [tipologia]}
    if parametros:
        corpo["parametros"] = parametros
    r = cliente.post(f"/casos/{caso_id}/alertas/executar", json=corpo, headers=cabecalhos())
    assert r.status_code == 200, r.text
    return r.json()


def _entidades(cliente, caso_id: str, **filtros) -> list[dict]:
    r = cliente.get(f"/casos/{caso_id}/entidades", params=filtros, headers=cabecalhos())
    assert r.status_code == 200, r.text
    return r.json()


def _auditoria(cliente, caso_id: str, acao: str) -> list[dict]:
    r = cliente.get("/auditoria", params={"caso_id": caso_id}, headers=cabecalhos("tok-admin"))
    assert r.status_code == 200, r.text
    return [e for e in r.json() if e["acao"] == acao]


class _Registrador:
    """Registrador de capturas em memória para testar um conector isolado."""

    def __init__(self) -> None:
        self.capturas: list[dict] = []

    def __call__(self, fonte, origem, conteudo, metadados):
        from types import SimpleNamespace

        dados = conteudo if isinstance(conteudo, bytes) else conteudo.read()
        captura = SimpleNamespace(
            id=f"cap{len(self.capturas) + 1}",
            fonte=fonte,
            url_ou_arquivo=origem,
            conteudo=dados,
            metadados=metadados,
        )
        self.capturas.append(captura)
        return captura


def _verificar_explicacao(alerta: dict, cliente) -> None:
    """Todo alerta: regra e política da tabela única, motivo e evidências ligadas a capturas."""
    politica = POLITICAS[alerta["tipologia"]]
    assert alerta["severidade"] == politica.severidade
    assert alerta["tipologia_nome"] == politica.nome
    assert alerta["politica_erro"] == politica.politica_erro
    assert alerta["situacao"] == "pendente" and len(alerta["motivo"]) > 80
    assert alerta["evidencias"]
    for evidencia in alerta["evidencias"]:
        captura = cliente.get(f"/capturas/{evidencia['captura_id']}", headers=cabecalhos()).json()
        assert captura["sha256"] == evidencia["sha256"]
        assert captura["url_ou_arquivo"].startswith("simulado://")


# ==== H19: novas fontes ==========================================================================


def test_h19_todas_as_coletas_ficam_registradas_com_conector_e_versao(cliente_h2, caso_h2):
    coletas = cliente_h2.get(f"/casos/{caso_h2}/coletas", headers=cabecalhos()).json()
    assert len(coletas) == len(COLETAS_H2) and {c["situacao"] for c in coletas} == {"sucesso"}
    conectores = {(c["conector_nome"], c["conector_versao"]) for c in coletas}
    assert conectores == {
        ("cnpj_arquivo", "1.4.0"),
        ("pncp", "1.4.0"),
        ("pncp_termos", "1.0.1"),
        ("portal_transparencia", "1.2.0"),
        ("tse_arquivo", "1.2.0"),
        ("edital_arquivo", "1.1.1"),
    }
    assert all(c["captura_ids"] and c["tentativas"] >= 1 for c in coletas)
    # "do_caso" foi trocado pela lista explícita: a recoleta repete a mesma consulta.
    termos = next(c for c in coletas if c["fonte"] == "pncp_termos")
    assert "do_caso" not in termos["parametros"] and len(termos["parametros"]["contratos"]) == 10
    assert termos["parametros"]["contratos"][0] == {
        "cnpj": CNPJ_ORGAO,
        "ano": 2026,
        "sequencial": 101,
        "numero_controle_pncp": f"{CNPJ_ORGAO}-2-000101/2026",
    }
    ceis = next(
        c
        for c in coletas
        if c["fonte"] == "portal_transparencia" and c["parametros"]["recurso"] == "ceis"
    )
    assert CNPJ_ZETA in ceis["parametros"]["codigoSancionado"]
    assert len(ceis["parametros"]["codigoSancionado"]) == 6


def test_h19_portal_sem_chave_fica_nao_configurado_e_nao_falha_as_outras_fontes(
    tmp_path, raiz_cnpj, extras, monkeypatch
):
    monkeypatch.delenv("PORTAL_TRANSPARENCIA_CHAVE", raising=False)
    app = criar_app(criar_config(tmp_path, raiz_cnpj), extras_conectores=extras)
    with TestClient(app) as cliente:
        caso_id = cliente.post("/casos", json=corpo_caso(), headers=cabecalhos()).json()["id"]
        assert (
            _coletar(cliente, caso_id, "cnpj_arquivo", {"diretorio": DIR_CNPJ}).status_code == 200
        )
        assert _coletar(cliente, caso_id, "pncp", PERIODO).status_code == 200
        r = _coletar(cliente, caso_id, "portal_transparencia", {"recurso": "ceis", "do_caso": True})
        assert r.status_code == 200, r.text
        corpo = r.json()
        assert corpo["situacao"] == "nao_configurado" and corpo["captura_ids"] == []
        assert corpo["tentativas"] == 0 and "PORTAL_TRANSPARENCIA_CHAVE" in corpo["motivo"]
        # Nenhuma requisição saiu, e a coleta ficou no histórico e na auditoria.
        assert extras["portal_transparencia"]["transporte"].chamadas == []
        assert len(_auditoria(cliente, caso_id, "coleta.nao_configurada")) == 1
        assert _auditoria(cliente, caso_id, "coleta.falhou") == []
        # As demais fontes continuam funcionando depois disso.
        assert _coletar(cliente, caso_id, "pncp_termos", {"do_caso": True}).status_code == 200
        md = cliente.get(
            f"/casos/{caso_id}/relatorio", params={"formato": "md"}, headers=cabecalhos()
        ).text
        assert "fonte não configurada" in md and "coleta(s) deste caso falharam" not in md


def test_h19_portal_envia_a_chave_so_no_cabecalho(cliente_h2, caso_h2, extras):
    transporte = extras["portal_transparencia"]["transporte"]
    assert transporte.chamadas
    assert all(c.headers["chave-api-dados"] == CHAVE_PORTAL for c in transporte.chamadas)
    assert all(c.method == "GET" for c in transporte.chamadas)  # só coleta passiva
    assert all(CHAVE_PORTAL not in str(c.url) for c in transporte.chamadas)
    coletas = cliente_h2.get(f"/casos/{caso_h2}/coletas", headers=cabecalhos()).json()
    for coleta in (c for c in coletas if c["fonte"] == "portal_transparencia"):
        for captura_id in coleta["captura_ids"]:
            captura = cliente_h2.get(f"/capturas/{captura_id}", headers=cabecalhos()).json()
            assert CHAVE_PORTAL not in json.dumps(captura)
            assert captura["metadados"]["simulado"] is True
    trilha = cliente_h2.get(
        "/auditoria", params={"caso_id": caso_h2}, headers=cabecalhos("tok-admin")
    ).text
    assert CHAVE_PORTAL not in trilha


def test_h19_controle_de_taxa_espaca_as_requisicoes():
    agora = [0.0]
    esperas: list[float] = []

    def dormir(segundos: float) -> None:
        esperas.append(segundos)
        agora[0] += segundos

    taxa = ControleTaxa(120, dormir=dormir, relogio=lambda: agora[0])  # 2 por segundo
    for _ in range(4):
        taxa.aguardar()
    assert esperas == pytest.approx([0.5, 0.5, 0.5]) and taxa.esperas == 3
    agora[0] += 10  # passou tempo de sobra: a próxima não espera
    taxa.aguardar()
    assert len(esperas) == 3
    # Zero desliga o controle.
    livre = ControleTaxa(0, dormir=dormir, relogio=lambda: agora[0])
    livre.aguardar()
    livre.aguardar()
    assert livre.esperas == 0


def test_h19_portal_respeita_a_taxa_configurada_e_faz_retentativa():
    agora = [0.0]
    esperas: list[float] = []

    def dormir(segundos: float) -> None:
        esperas.append(segundos)
        agora[0] += segundos

    transporte = transporte_portal(
        ceis=json.loads((H2 / "portal" / "ceis.json").read_text(encoding="utf-8")),
        falhas_iniciais=2,
    )
    conector = ConectorPortalTransparencia(
        chave=CHAVE_PORTAL,
        retentativa=Retentativa(3, 0, dormir=lambda _s: None),
        requisicoes_por_minuto=60,
        transporte=transporte,
        dormir=dormir,
        relogio=lambda: agora[0],
    )
    registrador = _Registrador()
    resultado = conector.coletar(
        {"recurso": "ceis", "codigoSancionado": [CNPJ_ZETA, CNPJ_ETA]}, registrador
    )
    # Duas falhas 503, depois ZETA (página 1 com registro, página 2 vazia) e ETA (vazia).
    assert len(transporte.chamadas) == 5 and conector.tentativas == 3
    assert esperas == pytest.approx([1.0] * 4)  # 60 por minuto = uma requisição por segundo
    assert any("Controle de taxa: 4 espera(s)" in a for a in resultado.avisos)
    (sancao,) = resultado.registros
    assert (sancao.cadastro, sancao.cnpj_sancionado) == ("CEIS", CNPJ_ZETA)
    assert (sancao.data_inicio, sancao.data_fim) == ("2025-01-01", "2027-01-01")


def test_h19_portal_trata_chave_recusada_formato_inesperado_e_pessoa_fisica():
    def conector(responder):
        return ConectorPortalTransparencia(
            chave=CHAVE_PORTAL,
            retentativa=Retentativa(1, 0),
            requisicoes_por_minuto=700,
            transporte=TransporteRotas(responder),
            dormir=lambda _s: None,
        )

    parametros = {"recurso": "cnep", "codigoSancionado": [CNPJ_ZETA]}
    registrador = _Registrador()
    with pytest.raises(ErroConector, match="recusou a chave"):
        conector(lambda _r: (401, b"{}")).coletar(parametros, registrador)
    assert len(registrador.capturas) == 1  # a resposta de erro foi para a custódia
    with pytest.raises(ErroConector, match="mudado de formato"):
        conector(lambda _r: (200, b'{"data": []}')).coletar(parametros, _Registrador())
    with pytest.raises(FonteNaoConfigurada):
        ConectorPortalTransparencia(chave="").coletar(parametros, _Registrador())

    # Sanção de pessoa física (CPF) e data ilegível: não viram registro / ficam sem data.
    itens = [
        {"id": 1, "sancionado": {"codigoFormatado": "***.666.666-**"}, "pessoa": {}},
        {
            "id": 2,
            "dataInicioSancao": "Sem informação",
            "sancionado": {"codigoFormatado": "90.000.006/0001-51"},
        },
    ]
    paginas = iter([json.dumps(itens).encode(), b"[]"])
    resultado = conector(lambda _r: (200, next(paginas))).coletar(parametros, _Registrador())
    assert [r.id_registro for r in resultado.registros] == ["2"]
    assert resultado.registros[0].data_inicio == ""
    assert any("pessoa física" in a for a in resultado.avisos)
    assert any("formato não reconhecido" in a for a in resultado.avisos)


def test_h19_servidores_exigem_orgao_e_cnpj_e_nunca_consultam_por_cpf(cliente_h2, caso_h2):
    r = _coletar(cliente_h2, caso_h2, "portal_transparencia", {"recurso": "servidores"})
    assert r.status_code == 422 and "orgaoServidorLotacao" in r.json()["detail"]
    r = _coletar(
        cliente_h2,
        caso_h2,
        "portal_transparencia",
        {"recurso": "servidores", "orgaoServidorLotacao": SIAPE},
    )
    assert r.status_code == 422 and "cnpjOrgao" in r.json()["detail"]
    r = _coletar(cliente_h2, caso_h2, "portal_transparencia", {"recurso": "servidores", "cpf": "1"})
    assert r.status_code == 422 and "não é aceito" in r.json()["detail"]


def test_h19_tse_le_utf8_latin1_e_zip_com_o_mesmo_resultado(tmp_path):
    def coletar(pasta: Path):
        conector = ConectorTseArquivo(dir_base=tmp_path, simulado=True)
        registrador = _Registrador()
        return conector.coletar({"diretorio": pasta.name}, registrador), registrador

    utf8 = tmp_path / "utf8"
    shutil.copytree(H2 / "tse", utf8)
    latin1 = tmp_path / "latin1"
    latin1.mkdir()
    pacote = tmp_path / "zipado"
    pacote.mkdir()
    with zipfile.ZipFile(pacote / "prestacao_de_contas_eleitorais_candidatos_2024.zip", "w") as z:
        for arquivo in sorted((H2 / "tse").glob("*.csv")):
            texto = arquivo.read_text(encoding="utf-8")
            (latin1 / arquivo.name).write_bytes(texto.encode("latin-1"))
            z.writestr(arquivo.name, texto.encode("latin-1"))
        z.writestr("leiame_receitas-candidatos.pdf", b"%PDF sintetico")

    def resumo(resultado):
        return [
            {k: v for k, v in vars(r).items() if k != "captura_id"} for r in resultado.registros
        ]

    (r_utf8, cap_utf8), (r_latin1, cap_latin1), (r_zip, cap_zip) = map(
        coletar, (utf8, latin1, pacote)
    )
    assert resumo(r_utf8) == resumo(r_latin1) == resumo(r_zip)
    codificacoes = [list(c.metadados["codificacao_detectada"].values()) for c in cap_utf8.capturas]
    assert codificacoes == [["utf-8"], ["utf-8"]]
    assert all(
        v == "latin-1"
        for c in cap_latin1.capturas
        for v in c.metadados["codificacao_detectada"].values()
    )
    assert len(cap_zip.capturas) == 1 and cap_zip.capturas[0].metadados["formato"] == "zip"
    # O acento sobreviveu às duas codificações.
    origens = {r.origem_receita for r in r_latin1.registros if hasattr(r, "origem_receita")}
    assert "Recursos de pessoas físicas" in origens
    situacoes = {getattr(r, "situacao_turno", "") for r in r_latin1.registros}
    assert "NÃO ELEITO" in situacoes


def test_h19_tse_ignora_recursos_proprios_e_nunca_guarda_o_cpf_inteiro_do_candidato(tmp_path):
    shutil.copytree(H2 / "tse", tmp_path / "t")
    resultado = ConectorTseArquivo(dir_base=tmp_path).coletar({"diretorio": "t"}, _Registrador())
    doacoes = [r for r in resultado.registros if hasattr(r, "documento_doador")]
    assert len(doacoes) == 5 and all(d.origem_receita != "Recursos próprios" for d in doacoes)
    assert any("Recursos próprios" in a and "1 receita" in a for a in resultado.avisos)
    candidatos = [r for r in resultado.registros if hasattr(r, "situacao_turno")]
    # Decisão de 23/11/2026: do CPF do candidato só existem os seis dígitos centrais (aqui o
    # arquivo de 2024 traz "-4", então nem isso).
    assert all(not hasattr(c, "cpf") and c.cpf_parcial == "" for c in candidatos)
    # Valores e datas no formato brasileiro.
    ricardo = next(d for d in doacoes if d.nome_doador == "RICARDO FICTICIO LIMA")
    assert (ricardo.valor, ricardo.data, ricardo.sq_candidato) == (
        5000.0,
        "2024-08-15",
        "990000000001",
    )
    # Filtro por município e detecção de mudança de layout.
    filtrado = ConectorTseArquivo(dir_base=tmp_path).coletar(
        {"diretorio": "t", "municipio": "Outra Cidade Sintética"}, _Registrador()
    )
    assert {r.sq_candidato for r in filtrado.registros} == {"990000000003"}
    (tmp_path / "t" / "receitas_candidatos_2024_GO.csv").write_text(
        '"SQ_CANDIDATO";"OUTRA"\n"1";"x"\n', encoding="utf-8"
    )
    with pytest.raises(ErroConector, match="layout pode ter mudado"):
        ConectorTseArquivo(dir_base=tmp_path).coletar({"diretorio": "t"}, _Registrador())
    with pytest.raises(ErroConector, match="TSE_DIR_BASE"):
        ConectorTseArquivo(dir_base=None).coletar({"diretorio": "t"}, _Registrador())
    with pytest.raises(ErroConector, match="não encontrado"):
        ConectorTseArquivo(dir_base=tmp_path).coletar({"diretorio": "../.."}, _Registrador())


def test_h19_candidato_e_ligado_as_doacoes_por_sq_candidato_sem_cpf(cliente_h2, caso_h2):
    candidatos = _entidades(cliente_h2, caso_h2, tipo="candidato")
    assert {c["identificador"] for c in candidatos} == {
        "990000000001",
        "990000000002",
        "990000000003",
    }
    um = next(c for c in candidatos if c["identificador"] == "990000000001")
    # A situação vem do arquivo de candidatos; o município, do de receitas (mesmo SQ_CANDIDATO).
    assert um["atributos"]["situacao_turno"] == "ELEITO"
    assert um["atributos"]["nm_ue"] == "EXEMPLOPOLIS"
    assert "cpf" not in json.dumps(um).lower()
    grafo = cliente_h2.get(
        f"/casos/{caso_h2}/grafo",
        params={"centro": um["id"], "profundidade": 1},
        headers=cabecalhos(),
    ).json()
    doacoes = [a for a in grafo["arestas"] if a["tipo"] == "doou_para"]
    assert len(doacoes) == 3  # Ricardo, ETA e Tatiana; "recursos próprios" ficou de fora
    assert {a["atributos"]["valor"] for a in doacoes} == {5000.0, 10000.0, 600.0}


def test_h19_termos_do_pncp_entram_no_contrato_e_so_para_os_contratos_do_caso(
    cliente_h2, caso_h2, extras
):
    chamadas = extras["pncp_termos"]["transporte"].chamadas
    caminhos = {c.url.path for c in chamadas}
    assert len(caminhos) == 10 and all(
        p.startswith(f"/api/pncp/v1/orgaos/{CNPJ_ORGAO}/contratos/2026/") for p in caminhos
    )
    (contrato,) = _entidades(cliente_h2, caso_h2, q="000108/2026", tipo="contrato")
    termos = contrato["atributos"]["termos"]
    assert [(t["sequencial"], t["valor_acrescido"], t["acrescimo_supressao"]) for t in termos] == [
        (1, 20000.0, True),
        (2, 10000.0, True),
    ]
    assert contrato["atributos"]["valorInicial"] == 100000.0
    assert contrato["atributos"]["categoriaProcessoId"] == 8


def test_h19_termos_explicam_resposta_401_e_exigem_contratos():
    registrador = _Registrador()
    conector = ConectorPncpTermos(
        transporte=TransporteRotas(lambda _r: (401, b"{}")),
        tentativas=1,
        requisicoes_por_minuto=0,
    )
    contratos = {"contratos": [{"cnpj": CNPJ_ORGAO, "ano": 2026, "sequencial": 1}]}
    with pytest.raises(ErroConector, match="não declara autenticação"):
        conector.coletar(contratos, registrador)
    assert len(registrador.capturas) == 1
    with pytest.raises(ErroConector, match="do_caso"):
        ConectorPncpTermos(transporte=transporte_termos({})).coletar({}, _Registrador())


def test_h19_edital_le_txt_da_raiz_configurada_e_recusa_pdf_invalido(tmp_path):
    shutil.copytree(H2 / "editais", tmp_path / "e")
    # PDF válido é lido (tests/test_editais_pdf.py); um arquivo .pdf que não é PDF é recusado.
    (tmp_path / "e" / "edital.pdf").write_bytes(b"%PDF sintetico")
    (tmp_path / "e" / "planilha.xlsx").write_bytes(b"sintetico")
    registrador = _Registrador()
    resultado = ConectorEditalArquivo(dir_base=tmp_path).coletar({"diretorio": "e"}, registrador)
    assert [r.nome_arquivo for r in resultado.registros] == [
        "edital_regular.txt",
        "edital_restritivo.txt",
    ]
    assert any("não serem .txt nem .pdf" in a for a in resultado.avisos)
    assert any("Recusado, edital.pdf: o PDF está corrompido" in a for a in resultado.avisos)
    assert all(
        c.url_ou_arquivo.startswith("simulado://edital_arquivo/") for c in registrador.capturas
    )
    assert all(str(tmp_path) not in c.url_ou_arquivo for c in registrador.capturas)
    with pytest.raises(ErroConector, match="EDITAIS_DIR_BASE"):
        ConectorEditalArquivo(dir_base=None).coletar({"diretorio": "e"}, _Registrador())


def test_h19_fabrica_conhece_os_novos_conectores(config):
    for fonte in ("pncp_termos", "tse_arquivo", "edital_arquivo", "portal_transparencia"):
        conector = criar_conector(fonte, config)
        assert conector.nome == fonte and conector.versao.count(".") == 2
    assert criar_conector("portal_transparencia", config).taxa.por_minuto == 300


# ==== RNF04: dados pessoais minimizados e CPF sempre descaracterizado ===========================


def test_rnf04_nenhum_cpf_completo_chega_a_entidades_alertas_avisos_ou_relatorio(
    cliente_h2, caso_h2
):
    # Os CPFs completos existem nas cargas brutas (custódia), como vieram da "fonte".
    bruto = (H2 / "tse" / "receitas_candidatos_2024_GO.csv").read_text(encoding="utf-8")
    assert "00066666646" in bruto
    cliente_h2.post(f"/casos/{caso_h2}/alertas/executar", json={}, headers=cabecalhos())
    h = cabecalhos("tok-delegado")
    saidas = [
        cliente_h2.get(f"/casos/{caso_h2}/entidades", headers=h).text,
        cliente_h2.get(f"/casos/{caso_h2}/alertas", headers=h).text,
        cliente_h2.get(f"/casos/{caso_h2}/grafo", headers=h).text,
        cliente_h2.get(f"/casos/{caso_h2}/relatorio", headers=h).text,
        cliente_h2.get(f"/casos/{caso_h2}/relatorio", params={"formato": "md"}, headers=h).text,
        cliente_h2.get("/auditoria", params={"caso_id": caso_h2}, headers=h).text,
    ]
    for saida in saidas:
        for cpf in CPFS_COMPLETOS:
            assert cpf not in saida
    assert "***666666**" in saidas[0] and "CPF sempre descaracterizado" in saidas[4]
    # Do servidor fica só o necessário: nome, dígitos visíveis, órgão e situação funcional.
    servidor = next(
        e
        for e in _entidades(cliente_h2, caso_h2, q="RICARDO FICTICIO LIMA", tipo="pessoa")
        if e["atributos"].get("papel_fonte") == "servidor"
    )
    assert set(servidor["atributos"]) == {
        "nome",
        "nome_normalizado",
        "cpf_mascarado",
        "papel_fonte",
        "codigo_orgao_siape",
        "nome_orgao_siape",
        "situacao",
        "tipo_servidor",
        "origem_atributos",
    }


def test_rf02_socio_servidor_e_doador_nao_sao_fundidos_e_geram_sugestao(
    app_h2, cliente_h2, caso_h2
):
    ricardos = _entidades(cliente_h2, caso_h2, q="RICARDO FICTICIO LIMA", tipo="pessoa")
    papeis = sorted(e["atributos"].get("papel_fonte", "socio") for e in ricardos)
    assert papeis == ["doador", "servidor", "socio"]
    assert {e["identificador"] for e in ricardos} == {"***666666**"}
    with app_h2.state.fabrica_sessoes() as s:
        ids = {e["id"] for e in ricardos}
        sugestoes = list(s.execute(select(Sugestao).where(Sugestao.entidade_id.in_(ids))).scalars())
        assert len(sugestoes) == 6  # três registros, sugeridos dois a dois, nos dois sentidos
        assert all(x.score == 0.9 and "Possível coincidência" in x.motivo for x in sugestoes)
        assert all("não identificam uma pessoa" in x.motivo for x in sugestoes)


# ==== H32: T5 fracionamento ======================================================================


def test_h32_t5_dispara_para_contratacoes_que_somam_o_limite_e_cita_a_lei(cliente_h2, caso_h2):
    (alerta,) = _alertas(cliente_h2, caso_h2, "T5")
    _verificar_explicacao(alerta, cliente_h2)
    p = alerta["parametros"]
    assert (p["exercicio"], p["inciso"], p["limite_dispensa"]) == ("2026", "II", 65492.11)
    assert (p["soma"], p["contratacoes"]) == (90000.0, 3)
    assert (p["orgao_cnpj"], p["codigo_unidade"]) == (CNPJ_ORGAO, "910001")
    assert "Decreto nº 12.807" in p["fonte_limite"] and "d12807.htm" in p["fonte_limite"]
    assert p["criterio_natureza"] == "fornecedor_e_categoria"
    motivo = alerta["motivo"]
    assert "DELTA SERVICOS SINTETICA LTDA" in motivo and "R$ 90.000,00" in motivo
    assert "R$ 65.492,11" in motivo and "art. 75, II" in motivo and "§ 1º" in motivo
    assert "indício a verificar" in motivo and "mesmo fornecedor e mesma categoria" in motivo
    assert len(alerta["evidencias"]) == 3 and all(
        e["fonte"] == "pncp" for e in alerta["evidencias"]
    )
    assert len(alerta["entidades"]) == 4  # três contratos e o fornecedor


def test_h32_t5_nao_dispara_abaixo_do_limite_nem_para_obras_no_inciso_i(app_h2, caso_h2):
    padrao = app_h2.state.config.parametros_tipologias()
    with app_h2.state.fabrica_sessoes() as s:
        # EPSILON (2 x 20 mil, compras) e ALFA (2 x 50 mil, obras no inciso I) não disparam.
        (unico,) = tipologias.executar(s, caso_h2, padrao, ["T5"])
        assert "DELTA" in unico.motivo and "EPSILON" not in unico.motivo
        # Com o limite do inciso I menor, as obras da ALFA passam a disparar: o inciso conta.
        limites = legislacao.limites_dispensa(
            '{"2026": {"inciso_i": 90000, "inciso_ii": 65492.11, "fonte": "teste"}}'
        )
        alertas = tipologias.executar(
            s, caso_h2, {**padrao, "t5_limites_dispensa": limites}, ["T5"]
        )
        assert sorted(a.parametros["inciso"] for a in alertas) == ["I", "II"]
        # Exercício sem limite configurado não é avaliado.
        sem_2026 = {"2025": limites["2026"]}
        assert (
            tipologias.executar(s, caso_h2, {**padrao, "t5_limites_dispensa": sem_2026}, ["T5"])
            == []
        )
        # Mínimo de contratações maior que o grupo: não dispara.
        assert (
            tipologias.executar(s, caso_h2, {**padrao, "t5_minimo_contratacoes": 4}, ["T5"]) == []
        )
        # Critério só por fornecedor continua achando o mesmo grupo.
        por_fornecedor = tipologias.executar(
            s, caso_h2, {**padrao, "t5_criterio_natureza": "fornecedor"}, ["T5"]
        )
        assert len(por_fornecedor) == 1
        s.rollback()


def test_h32_limites_de_2026_vem_do_decreto_e_a_configuracao_e_validada(tmp_path):
    limites = legislacao.limites_dispensa()
    assert limites["2026"]["inciso_i"] == 130984.20 and limites["2026"]["inciso_ii"] == 65492.11
    assert "12.807" in limites["2026"]["fonte"]
    assert legislacao.CATEGORIAS_INCISO_I == {7: "Obras", 9: "Serviços de Engenharia"}
    from buscador.config import ErroConfiguracao

    for invalido in ("{", "[]", '{"26": {}}', '{"2027": {"inciso_i": -1, "inciso_ii": 1}}'):
        with pytest.raises(ErroConfiguracao):
            criar_config(tmp_path, t5_limites_dispensa=invalido).validar()
    with pytest.raises(ErroConfiguracao):
        criar_config(tmp_path, t5_criterio_natureza="qualquer").validar()
    with pytest.raises(ErroConfiguracao):
        criar_config(tmp_path, t10_expressoes_extras="(sem fechar").validar()


# ==== H33: T6 empresa sancionada =================================================================


def test_h33_t6_dispara_so_para_sancao_vigente_na_data_do_contrato(cliente_h2, caso_h2):
    (alerta,) = _alertas(cliente_h2, caso_h2, "T6")
    _verificar_explicacao(alerta, cliente_h2)
    assert "ZETA CONSULTORIA SINTETICA LTDA" in alerta["motivo"] and "CEIS" in alerta["motivo"]
    assert "01/01/2025" in alerta["motivo"] and "01/03/2026" in alerta["motivo"]
    assert alerta["parametros"] == {
        **alerta["parametros"],
        "cadastro": "CEIS",
        "id_registro": "900001",
        "data_inicio_sancao": "2025-01-01",
        "data_fim_sancao": "2027-01-01",
        "data_assinatura": "2026-03-01",
    }
    assert [e["fonte"] for e in alerta["evidencias"]] == ["portal_transparencia", "pncp"]
    # A ETA tem registro no CNEP, mas ele terminou em 2021: está no caso e não dispara.
    sancoes = _entidades(cliente_h2, caso_h2, tipo="sancao")
    assert {s["atributos"]["cadastro"] for s in sancoes} == {"CEIS", "CNEP"}
    assert "ETA TRANSPORTES" not in alerta["motivo"]


# ==== H34: T7 sócio servidor =====================================================================


def test_h34_t7_e_possivel_coincidencia_por_digitos_visiveis_e_nome(cliente_h2, caso_h2):
    alertas = _alertas(cliente_h2, caso_h2, "T7")
    assert len(alertas) == 3  # o sócio Ricardo em DELTA, EPSILON e ZETA, contratadas do órgão
    empresas = sorted(a["titulo"].split("sócio de ")[1].split(" e servidor")[0] for a in alertas)
    assert [e.split()[0] for e in empresas] == ["DELTA", "EPSILON", "ZETA"]
    for alerta in alertas:
        _verificar_explicacao(alerta, cliente_h2)
        assert alerta["titulo"].startswith("Possível coincidência")
        motivo = alerta["motivo"]
        assert "POSSÍVEL COINCIDÊNCIA, não uma identificação" in motivo
        assert "***666666**" in motivo and "RICARDO FICTICIO LIMA" in motivo
        assert "seis dígitos visíveis e o nome normalizado" in motivo
        assert "não foram" in motivo and f"SIAPE {SIAPE}" in motivo
        assert alerta["parametros"]["identidade_confirmada"] is False
        fontes = [e["fonte"] for e in alerta["evidencias"]]
        assert fontes[:2] == ["cnpj_arquivo", "portal_transparencia"] and "pncp" in fontes
    # Pedro (mesmo nome de um sócio da DELTA, dígitos diferentes) e a servidora sem vínculo
    # não disparam.
    textos = " ".join(a["motivo"] for a in alertas)
    assert "PEDRO EXEMPLO ALVES" not in textos and "SEM VINCULO" not in textos


# ==== H35: T8 aditivo acima do limite ============================================================


def test_h35_t8_soma_valor_acrescido_contra_o_valor_inicial(cliente_h2, caso_h2):
    (alerta,) = _alertas(cliente_h2, caso_h2, "T8")
    _verificar_explicacao(alerta, cliente_h2)
    p = alerta["parametros"]
    assert (p["valor_inicial"], p["soma_valor_acrescido"]) == (100000.0, 30000.0)
    assert (p["percentual_observado"], p["percentual_limite"]) == (30.0, 25.0)
    assert p["reforma_identificada"] is False and p["termos_considerados"] == 2
    motivo = alerta["motivo"]
    assert "000108/2026" in motivo and "30,0%" in motivo and "art. 125" in motivo
    assert "valorGlobal/valorAcumulado não foram usados" in motivo
    assert "acréscimo ou supressão sem distinguir" in motivo
    assert [e["fonte"] for e in alerta["evidencias"]] == ["pncp", "pncp_termos", "pncp_termos"]


def test_h35_t8_usa_50_por_cento_na_reforma_e_ignora_termo_sem_acrescimo(cliente_h2, caso_h2):
    # Limite menor: o aditivo de 20% da MU dispara; a reforma da ETA (30%) continua abaixo de 50%.
    alertas = _alertas(cliente_h2, caso_h2, "T8", t8_percentual_limite=15)
    assert sorted(a["motivo"].split(" de ")[1].split(" com ")[0] for a in alertas) == [
        "MU EVENTOS SINTETICA LTDA",
        "ZETA CONSULTORIA SINTETICA LTDA",
    ]
    mu = next(a for a in alertas if "MU EVENTOS" in a["motivo"])
    # O apostilamento de R$ 50 mil (qualificacaoAcrescimoSupressao = false) não entrou na soma.
    assert mu["parametros"]["soma_valor_acrescido"] == 20000.0
    assert (mu["parametros"]["termos_considerados"], mu["parametros"]["termos_total"]) == (1, 2)
    # Com o limite de reforma menor, a reforma de edifício da ETA dispara e é identificada.
    alertas = _alertas(cliente_h2, caso_h2, "T8", t8_percentual_limite_reforma=20)
    eta = next(a for a in alertas if "ETA TRANSPORTES" in a["motivo"])
    assert eta["parametros"]["reforma_identificada"] is True
    assert eta["parametros"]["percentual_limite"] == 20.0
    assert "reforma de edifício ou de equipamento, identificada pelo objeto" in eta["motivo"]


def test_h35_t8_nunca_usa_valor_global_como_base(app_h2, caso_h2):
    padrao = app_h2.state.config.parametros_tipologias()
    with app_h2.state.fabrica_sessoes() as s:
        contrato = s.execute(
            select(Entidade).where(
                Entidade.caso_id == caso_h2, Entidade.identificador.like("%000108/2026")
            )
        ).scalar_one()
        atributos = dict(contrato.atributos)
        del atributos["valorInicial"]
        atributos["valor_global"] = 1.0  # se fosse usado, o percentual explodiria
        contrato.atributos = atributos
        s.flush()
        assert tipologias.executar(s, caso_h2, padrao, ["T8"]) == []
        s.rollback()


# ==== H39: T9 doador contratado ==================================================================


def test_h39_t9_dispara_para_doador_pj_e_para_possivel_coincidencia_de_socio(cliente_h2, caso_h2):
    alertas = _alertas(cliente_h2, caso_h2, "T9")
    assert len(alertas) == 4
    por_tipo = {
        t: [a for a in alertas if a["parametros"]["tipo_doador"] == t]
        for t in ("pessoa_juridica", "pessoa_fisica")
    }
    (pj,) = por_tipo["pessoa_juridica"]
    _verificar_explicacao(pj, cliente_h2)
    assert pj["titulo"].startswith("Doadora contratada")
    assert pj["parametros"]["identidade_confirmada"] is True
    assert "ETA TRANSPORTES SINTETICA LTDA" in pj["motivo"] and "R$ 10.000,00" in pj["motivo"]
    assert "CANDIDATO SINTETICO UM" in pj["motivo"] and "situação 'ELEITO'" in pj["motivo"]
    assert (
        "município de EXEMPLOPOLIS/GO" in pj["motivo"]
        and "indício a verificar" in pj["motivo"].lower()
    )
    assert [e["fonte"] for e in pj["evidencias"]] == ["tse_arquivo", "tse_arquivo", "pncp"]

    pf = por_tipo["pessoa_fisica"]
    assert sorted(a["motivo"].split("; e ")[1].split()[0] for a in pf) == [
        "DELTA",
        "EPSILON",
        "ZETA",
    ]
    for alerta in pf:
        _verificar_explicacao(alerta, cliente_h2)
        assert alerta["titulo"].startswith("Possível coincidência")
        assert alerta["parametros"]["identidade_confirmada"] is False
        assert "POSSÍVEL COINCIDÊNCIA, não uma identificação" in alerta["motivo"]
        assert "***666666**" in alerta["motivo"] and "R$ 5.000,00" in alerta["motivo"]
        assert {"tse_arquivo", "cnpj_arquivo", "pncp"} <= {e["fonte"] for e in alerta["evidencias"]}
    textos = " ".join(a["motivo"] for a in alertas)
    # Candidata não eleita, candidato de outro município e homônima com outros dígitos: nada.
    assert "LUCIA" not in textos and "MARCOS" not in textos and "TATIANA" not in textos


def test_h39_t9_exige_eleito_ente_e_contrato_posterior_a_doacao(app_h2, caso_h2):
    padrao = app_h2.state.config.parametros_tipologias()
    with app_h2.state.fabrica_sessoes() as s:
        # Nenhuma situação conta como "eleito": nada dispara.
        assert tipologias.executar(s, caso_h2, {**padrao, "t9_situacoes_eleito": []}, ["T9"]) == []
        # "NÃO ELEITO" passa a contar: a doação da Lucia (sócia da EPSILON) também dispara.
        mais = tipologias.executar(
            s, caso_h2, {**padrao, "t9_situacoes_eleito": ["ELEITO", "NÃO ELEITO"]}, ["T9"]
        )
        assert len(mais) == 5 and any("LUCIA SINTETICA ROCHA" in a.motivo for a in mais)
        # Doação posterior a todos os contratos: não é "doador que depois vira contratado".
        for aresta in s.execute(
            select(Aresta).where(Aresta.caso_id == caso_h2, Aresta.tipo == "doou_para")
        ).scalars():
            aresta.atributos = {**aresta.atributos, "data": "2027-01-01"}
        s.flush()
        assert tipologias.executar(s, caso_h2, padrao, ["T9"]) == []
        s.rollback()
    with app_h2.state.fabrica_sessoes() as s:
        # Órgão de outra esfera (estadual) no mesmo município: o ente não coincide.
        orgao = s.execute(
            select(Entidade).where(Entidade.caso_id == caso_h2, Entidade.tipo == "orgao")
        ).scalar_one()
        orgao.atributos = {**orgao.atributos, "esfera": "E"}
        s.flush()
        assert tipologias.executar(s, caso_h2, padrao, ["T9"]) == []
        s.rollback()


# ==== H36: T10 cláusula restritiva em edital =====================================================


def test_h36_t10_cita_o_trecho_literal_e_a_posicao(cliente_h2, caso_h2):
    alertas = _alertas(cliente_h2, caso_h2, "T10")
    assert sorted(a["parametros"]["regra"] for a in alertas) == [
        "certidao_negativa_quitacao",
        "certidao_quitacao",
    ]
    texto = (H2 / "editais" / "edital_restritivo.txt").read_text(encoding="utf-8")
    for alerta in alertas:
        _verificar_explicacao(alerta, cliente_h2)
        assert "edital_restritivo.txt" in alerta["titulo"]
        assert (
            "sem modelo de linguagem" in alerta["motivo"] and "Manual da Alice" in alerta["motivo"]
        )
        (ocorrencia,) = alerta["parametros"]["ocorrencias"]
        inicio = ocorrencia["posicao"]
        # O trecho citado é literalmente o que está no arquivo, na posição informada.
        assert texto[inicio : inicio + len(ocorrencia["trecho"])] == ocorrencia["trecho"]
        assert ocorrencia["contexto"] in texto
        linha = texto.splitlines()[ocorrencia["linha"] - 1]
        assert linha[ocorrencia["coluna"] - 1 :].startswith(ocorrencia["trecho"].split("\n")[0])
        (evidencia,) = alerta["evidencias"]
        assert evidencia["fonte"] == "edital_arquivo"
        assert f"linha {ocorrencia['linha']}" in evidencia["descricao"]
        assert f"linha {ocorrencia['linha']}, coluna {ocorrencia['coluna']}" in alerta["motivo"]
    negativa = next(a for a in alertas if a["parametros"]["regra"] == "certidao_negativa_quitacao")
    assert negativa["parametros"]["ocorrencias"][0]["trecho"] == "certidão negativa de quitação"
    assert negativa["parametros"]["ocorrencias"][0]["linha"] == 7
    # A expressão atravessa a quebra de linha do edital.
    quitacao = next(a for a in alertas if a["parametros"]["regra"] == "certidao_quitacao")
    assert quitacao["parametros"]["ocorrencias"][0]["trecho"] == "Certidão de\nQuitação"
    # O edital regular ("regularidade fiscal", "quitação de multas") não dispara.
    assert all("edital_regular" not in a["titulo"] for a in alertas)


def test_h36_t10_aceita_expressoes_extras_da_configuracao(app_h2, caso_h2):
    padrao = app_h2.state.config.parametros_tipologias()
    extras_t10 = legislacao.expressoes_extras(r"regularidade\s+fiscal;;termo de referência")
    with app_h2.state.fabrica_sessoes() as s:
        alertas = tipologias.executar(
            s,
            caso_h2,
            {**padrao, "t10_expressoes_extras": extras_t10},
            ["T10"],
            ler_captura=app_h2.state.custodia.ler,
        )
        assert sorted(a.parametros["regra"] for a in alertas) == [
            "certidao_negativa_quitacao",
            "certidao_quitacao",
            "extra_1",
            "extra_2",
        ]
        # Sem acesso à custódia não há texto: nenhuma detecção, nenhum erro.
        assert tipologias.executar(s, caso_h2, padrao, ["T10"]) == []
        s.rollback()


# ==== Tabela única, catálogo e relatório ==========================================================


def test_tipologias_t5_a_t10_estao_na_tabela_unica_e_no_catalogo(cliente_h2):
    r = cliente_h2.get("/tipologias", headers={"Authorization": "Bearer tok-analista"})
    assert r.status_code == 200, r.text
    catalogo = r.json()
    assert [t["codigo"] for t in catalogo][:10] == [f"T{n}" for n in range(1, 11)]
    for item in catalogo:
        politica = POLITICAS[item["codigo"]]
        assert (item["nome"], item["severidade"]) == (politica.nome, politica.severidade)
        assert item["politica_erro"] == politica.politica_erro and item["regra"] and item["base"]
    t5 = next(t for t in catalogo if t["codigo"] == "T5")
    limites = next(p for p in t5["parametros"] if p["nome"] == "t5_limites_dispensa")
    assert limites["valor"]["2026"]["inciso_ii"] == 65492.11 and limites["por_execucao"] is False
    minimo = next(p for p in t5["parametros"] if p["nome"] == "t5_minimo_contratacoes")
    assert minimo["valor"] == 2 and minimo["por_execucao"] is True
    assert "Decreto nº 12.807" in t5["base"] and "art. 75" in t5["base"]
    assert cliente_h2.get("/tipologias").status_code == 401
    assert {POLITICAS[f"T{n}"].severidade for n in (6, 7)} == {"alta"}
    assert POLITICAS["T10"].severidade == "baixa"


def test_execucao_completa_ordena_por_severidade_e_entra_no_relatorio(cliente_h2, caso_h2):
    r = cliente_h2.post(f"/casos/{caso_h2}/alertas/executar", json={}, headers=cabecalhos())
    assert r.status_code == 200, r.text
    alertas = r.json()
    contagem = {
        t: sum(a["tipologia"] == t for a in alertas) for t in ("T5", "T6", "T7", "T8", "T9", "T10")
    }
    assert contagem == {"T5": 1, "T6": 1, "T7": 3, "T8": 1, "T9": 4, "T10": 2}
    ordem = [{"alta": 0, "media": 1, "baixa": 2}[a["severidade"]] for a in alertas]
    assert ordem == sorted(ordem)
    # A decisão do investigador (RF12) vale também para as tipologias novas.
    t7 = next(a for a in alertas if a["tipologia"] == "T7")
    r = cliente_h2.post(
        f"/casos/{caso_h2}/alertas/{t7['id']}/decisao",
        json={"decisao": "descartado", "justificativa": "Homônimo conferido em fonte completa"},
        headers=cabecalhos(),
    )
    assert r.status_code == 200 and r.json()["situacao"] == "descartado"
    md = cliente_h2.get(
        f"/casos/{caso_h2}/relatorio", params={"formato": "md"}, headers=cabecalhos()
    ).text
    for marca in ("### [T5]", "### [T6]", "### [T7]", "### [T8]", "### [T9]", "### [T10]"):
        assert marca in md
    assert "## Dados pessoais" in md and "## Monitoramento contínuo e avisos" in md


# ==== H18: monitoramento contínuo ================================================================


def _entidade(cliente, caso_id: str, identificador: str, tipo: str) -> dict:
    return next(
        e
        for e in _entidades(cliente, caso_id, q=identificador, tipo=tipo)
        if e["identificador"] == identificador
    )


def _monitorar(cliente, caso_id: str, entidade_id: str, horas: int = 24, justificativa=None):
    return cliente.post(
        f"/casos/{caso_id}/monitoramentos",
        json={"entidade_id": entidade_id, "periodicidade_horas": horas},
        headers=cabecalhos(justificativa=justificativa or "Acompanhar alvo do inquérito sintético"),
    )


def _provocar_novidades(raizes, extras) -> None:
    """Muda as "fontes" entre duas coletas: sanção nova, termo novo, contrato novo e mudança no
    quadro societário da ZETA (um sócio entra, outro sai)."""
    from scripts.gerar_fixtures_h2 import contrato, sancao, termo

    # Os transportes simulados leem estas bases a cada requisição.
    extras["_ceis"][CNPJ_ZETA].append(
        sancao(900003, "ZETA", "01/06/2026", "01/06/2028", "Suspensão sintética")
    )
    extras["_termos"][f"{CNPJ_ORGAO}/2026/108"].append(
        termo(108, 3, "Termo Aditivo", 5000.0, True, "2026-10-01")
    )
    pncp = extras["pncp"]["transporte"]
    pagina = json.loads(pncp.paginas[1])
    pagina["data"].append(
        contrato(111, "ZETA", 8, 40000.0, "2026-10-20", "Consultoria sintética complementar")
    )
    pncp.paginas[1] = json.dumps(pagina, ensure_ascii=False).encode("utf-8")
    socios = next((raizes["cnpj"] / DIR_CNPJ).glob("*SOCIOCSV"))
    linhas = socios.read_text(encoding="latin-1").splitlines()
    linhas = [linha for linha in linhas if "MARCOS EXEMPLO NUNES" not in linha]
    linhas.append(
        '"90000006";"2";"NOVA SOCIA SINTETICA";"***202020**";"22";"20261001";"";'
        '"***000000**";"";"00";"4"'
    )
    socios.write_text("\n".join(linhas) + "\n", encoding="latin-1")


def test_h18_marcar_entidade_lista_as_coletas_de_origem_e_audita(cliente_h2, caso_h2):
    zeta = _entidade(cliente_h2, caso_h2, "90000006", "empresa")
    r = _monitorar(cliente_h2, caso_h2, zeta["id"], horas=168)
    assert r.status_code == 201, r.text
    m = r.json()
    assert (m["entidade_rotulo"], m["periodicidade_horas"], m["ativo"]) == (
        "ZETA CONSULTORIA SINTETICA LTDA",
        168,
        True,
    )
    assert (
        m["justificativa"] == "Acompanhar alvo do inquérito sintético" and m["criado_por"] == "ana"
    )
    # Refaz o que alimenta a entidade: CNPJ, PNCP, termos, CEIS e CNEP (estes dois pelos
    # parâmetros, mesmo o CNEP não tendo achado nada para a ZETA). Servidores, TSE e edital não.
    fontes = sorted(c["fonte"] for c in m["coletas_de_origem"])
    assert fontes == [
        "cnpj_arquivo",
        "pncp",
        "pncp_termos",
        "portal_transparencia",
        "portal_transparencia",
    ]
    proxima = datetime.fromisoformat(m["proxima_execucao_em"].replace("Z", "+00:00"))
    assert timedelta(hours=167) < proxima - datetime.now(UTC) < timedelta(hours=169)
    assert len(_auditoria(cliente_h2, caso_h2, "monitoramento.criar")) == 1
    # Marcar de novo atualiza a periodicidade em vez de duplicar.
    assert _monitorar(cliente_h2, caso_h2, zeta["id"], horas=24).json()["id"] == m["id"]
    lista = cliente_h2.get(f"/casos/{caso_h2}/monitoramentos", headers=cabecalhos()).json()
    assert len(lista) == 1 and lista[0]["periodicidade_horas"] == 24
    detalhe = cliente_h2.get(f"/casos/{caso_h2}", headers=cabecalhos()).json()
    assert detalhe["contagens"]["monitoramentos"] == 1
    # Entidade de outro caso ou inexistente, e periodicidade inválida.
    assert _monitorar(cliente_h2, caso_h2, "nao-existe").status_code == 404
    assert _monitorar(cliente_h2, caso_h2, zeta["id"], horas=0).status_code == 422


def test_h18_execucao_sem_mudanca_nao_gera_aviso_e_fica_na_auditoria(cliente_h2, caso_h2):
    zeta = _entidade(cliente_h2, caso_h2, "90000006", "empresa")
    m = _monitorar(cliente_h2, caso_h2, zeta["id"]).json()
    r = cliente_h2.post(f"/casos/{caso_h2}/monitoramentos/{m['id']}/executar", headers=cabecalhos())
    assert r.status_code == 200, r.text
    execucao = r.json()
    assert (execucao["situacao"], execucao["origem"], execucao["avisos"]) == (
        "sucesso",
        "manual",
        [],
    )
    assert len(execucao["coletas"]) == 5
    assert all(
        c["situacao"] == "sucesso" and c["mesmo_conteudo"] is True for c in execucao["coletas"]
    )
    # Cada recoleta é uma coleta comum (H14): linha em `coletas`, capturas e comparação.
    coletas = cliente_h2.get(f"/casos/{caso_h2}/coletas", headers=cabecalhos()).json()
    recoletas = [c for c in coletas if c["recoleta_de"]]
    assert {c["id"] for c in recoletas} == {c["coleta_id"] for c in execucao["coletas"]}
    (entrada,) = _auditoria(cliente_h2, caso_h2, "monitoramento.executar")
    assert entrada["detalhes"]["situacao"] == "sucesso" and entrada["detalhes"]["avisos"] == 0
    assert len(entrada["detalhes"]["recoletas"]) == 5
    assert len(_auditoria(cliente_h2, caso_h2, "monitoramento.iniciar")) == 1


def test_h18_avisa_socio_contrato_sancao_e_termo_novos(cliente_h2, caso_h2, raizes, extras):
    zeta = _entidade(cliente_h2, caso_h2, "90000006", "empresa")
    m = _monitorar(cliente_h2, caso_h2, zeta["id"]).json()
    _provocar_novidades(raizes, extras)
    r = cliente_h2.post(f"/casos/{caso_h2}/monitoramentos/{m['id']}/executar", headers=cabecalhos())
    assert r.status_code == 200, r.text
    execucao = r.json()
    assert execucao["situacao"] == "sucesso" and execucao["avisos_gerados"] == 5
    assert sum(c["mesmo_conteudo"] is False for c in execucao["coletas"]) == 4  # CNEP não mudou
    por_tipo: dict[str, list[dict]] = {}
    for aviso in execucao["avisos"]:
        por_tipo.setdefault(aviso["tipo"], []).append(aviso)
    assert {t: len(v) for t, v in por_tipo.items()} == {
        "quadro_societario": 2,
        "contrato_novo": 1,
        "sancao_nova": 1,
        "termo_novo": 1,
    }
    societario = " ".join(a["descricao"] for a in por_tipo["quadro_societario"])
    assert "NOVA SOCIA SINTETICA" in societario and "***202020**" in societario
    assert "MARCOS EXEMPLO NUNES" in societario and "não consta mais" in societario
    assert "000111/2026" in por_tipo["contrato_novo"][0]["descricao"]
    assert "Suspensão sintética" in por_tipo["sancao_nova"][0]["descricao"]
    assert "nº 3/2026 do contrato" in por_tipo["termo_novo"][0]["descricao"]
    # Todo aviso aponta para capturas verificáveis da recoleta.
    for aviso in execucao["avisos"]:
        assert aviso["captura_ids"] and aviso["entidade_rotulo"].startswith("ZETA")
        v = cliente_h2.get(
            f"/capturas/{aviso['captura_ids'][0]}/verificar", headers=cabecalhos()
        ).json()
        assert v["integra"] is True
    # Lista, contagem e marcação de lido (auditada).
    assert (
        cliente_h2.get(f"/casos/{caso_h2}", headers=cabecalhos()).json()["contagens"][
            "avisos_nao_lidos"
        ]
        == 5
    )
    aviso_id = execucao["avisos"][0]["id"]
    r = cliente_h2.post(f"/casos/{caso_h2}/avisos/{aviso_id}/lido", headers=cabecalhos())
    assert r.status_code == 200 and r.json()["lido_por"] == "ana" and r.json()["lido_em"]
    nao_lidos = cliente_h2.get(
        f"/casos/{caso_h2}/avisos", params={"nao_lidos": True}, headers=cabecalhos()
    ).json()
    assert len(nao_lidos) == 4
    assert len(cliente_h2.get(f"/casos/{caso_h2}/avisos", headers=cabecalhos()).json()) == 5
    assert len(_auditoria(cliente_h2, caso_h2, "aviso.marcar_lido")) == 1
    # A sanção nova (vigente só a partir de junho) não muda a T6 do contrato de março, mas o
    # termo novo aumenta o percentual da T8: o monitoramento alimenta as tipologias.
    (t8,) = _alertas(cliente_h2, caso_h2, "T8")
    assert t8["parametros"]["percentual_observado"] == 35.0
    # Uma segunda execução, sem mudança nova, não repete os avisos.
    r = cliente_h2.post(f"/casos/{caso_h2}/monitoramentos/{m['id']}/executar", headers=cabecalhos())
    assert r.json()["avisos"] == [] and r.json()["situacao"] == "sucesso"
    md = cliente_h2.get(
        f"/casos/{caso_h2}/relatorio", params={"formato": "md"}, headers=cabecalhos()
    ).text
    assert "Avisos de novidade (5)" in md and "[sanção nova]" in md


def test_h18_agendador_executa_so_o_que_venceu_e_audita_como_sistema(
    app_h2, cliente_h2, caso_h2, raizes, extras
):
    zeta = _entidade(cliente_h2, caso_h2, "90000006", "empresa")
    m = _monitorar(cliente_h2, caso_h2, zeta["id"], horas=24).json()
    agendador = Agendador(app_h2.state)
    # Ainda não venceu: nada roda.
    assert agendador.ciclo() == 0
    assert agendador.ciclo(agora=datetime.now(UTC) + timedelta(hours=23)) == 0
    _provocar_novidades(raizes, extras)
    depois = datetime.now(UTC) + timedelta(hours=25)
    assert agendador.ciclo(agora=depois) == 1
    with app_h2.state.fabrica_sessoes() as s:
        (execucao,) = s.execute(select(MonitoramentoExecucao)).scalars().all()
        assert (execucao.origem, execucao.usuario, execucao.situacao) == (
            "agendador",
            "agendador",
            "sucesso",
        )
        assert execucao.avisos_gerados == 5
        assert s.execute(select(Aviso)).scalars().all()
        monitorado = s.get(Monitoramento, m["id"])
        # A próxima execução foi reagendada: o mesmo instante não dispara de novo.
        assert monitorado.proxima_execucao_em > m["proxima_execucao_em"]
    assert agendador.ciclo(agora=datetime.now(UTC) + timedelta(hours=23)) == 0
    (entrada,) = _auditoria(cliente_h2, caso_h2, "monitoramento.executar")
    assert entrada["usuario"] == "agendador" and entrada["detalhes"]["perfil"] == "sistema"
    assert entrada["detalhes"]["origem"] == "agendador"
    assert entrada["justificativa"] == (
        "Monitoramento contínuo (automático): Acompanhar alvo do inquérito sintético"
    )
    # As recoletas automáticas também ficam em `coletas`, com o usuário de sistema.
    coletas = cliente_h2.get(f"/casos/{caso_h2}/coletas", headers=cabecalhos()).json()
    assert sum(c["usuario"] == "agendador" and bool(c["recoleta_de"]) for c in coletas) == 5
    assert cliente_h2.get("/auditoria/verificar", headers=cabecalhos("tok-admin")).json()["integra"]
    # Desativado: o agendador não o executa mais, e a execução manual é recusada.
    r = cliente_h2.post(
        f"/casos/{caso_h2}/monitoramentos/{m['id']}/situacao",
        json={"ativo": False},
        headers=cabecalhos(),
    )
    assert r.status_code == 200 and r.json()["ativo"] is False
    assert agendador.ciclo(agora=datetime.now(UTC) + timedelta(days=30)) == 0
    r = cliente_h2.post(f"/casos/{caso_h2}/monitoramentos/{m['id']}/executar", headers=cabecalhos())
    assert r.status_code == 409


def test_h18_agendador_inicia_e_para_com_o_servico(tmp_path, raiz_cnpj):
    config = criar_config(
        tmp_path,
        raiz_cnpj,
        monitoramento_agendador_ativo=True,
        monitoramento_intervalo_segundos=3600,
    )
    app = criar_app(config)
    with TestClient(app):
        thread = app.state.agendador._thread
        assert thread is not None and thread.is_alive() and thread.daemon
    assert app.state.agendador._thread is None and not thread.is_alive()


def test_h18_caso_com_retencao_vencida_nao_e_monitorado(app_h2, cliente_h2, caso_h2, extras):
    zeta = _entidade(cliente_h2, caso_h2, "90000006", "empresa")
    m = _monitorar(cliente_h2, caso_h2, zeta["id"], horas=1).json()
    ontem = (retencao.hoje_utc() - timedelta(days=1)).isoformat()
    with app_h2.state.fabrica_sessoes() as s:
        s.get(Caso, caso_h2).retencao_ate = ontem
        s.commit()
    chamadas_antes = len(extras["portal_transparencia"]["transporte"].chamadas)
    coletas_antes = len(cliente_h2.get(f"/casos/{caso_h2}/coletas", headers=cabecalhos()).json())
    assert Agendador(app_h2.state).ciclo(agora=datetime.now(UTC) + timedelta(hours=2)) == 1
    # Nenhuma coleta foi refeita e nenhuma requisição saiu.
    assert len(extras["portal_transparencia"]["transporte"].chamadas) == chamadas_antes
    assert (
        len(cliente_h2.get(f"/casos/{caso_h2}/coletas", headers=cabecalhos()).json())
        == coletas_antes
    )
    (monitorado,) = cliente_h2.get(f"/casos/{caso_h2}/monitoramentos", headers=cabecalhos()).json()
    assert monitorado["ativo"] is False and "retenção" in monitorado["motivo_inativo"]
    assert monitorado["ultima_execucao"]["situacao"] == "recusada_retencao"
    (recusa,) = _auditoria(cliente_h2, caso_h2, "monitoramento.retencao_recusa")
    assert recusa["usuario"] == "agendador" and recusa["detalhes"]["retencao_ate"] == ontem
    # Desativado, não volta a ser executado; e a API recusa executar, criar e reativar.
    assert Agendador(app_h2.state).ciclo(agora=datetime.now(UTC) + timedelta(days=9)) == 0
    for rota, corpo in (
        (f"/casos/{caso_h2}/monitoramentos/{m['id']}/executar", None),
        (f"/casos/{caso_h2}/monitoramentos/{m['id']}/situacao", {"ativo": True}),
        (f"/casos/{caso_h2}/monitoramentos", {"entidade_id": zeta["id"]}),
    ):
        assert cliente_h2.post(rota, json=corpo, headers=cabecalhos()).status_code == 403
    # Ler os avisos e desativar continuam permitidos (o caso está só para leitura).
    assert cliente_h2.get(f"/casos/{caso_h2}/avisos", headers=cabecalhos()).status_code == 200


def test_h18_fonte_sem_chave_deixa_a_execucao_parcial(tmp_path, raizes, extras, monkeypatch):
    """Recoleta de fonte que deixou de estar configurada não derruba o monitoramento."""
    banco = {"database_url": f"sqlite:///{tmp_path / 'parcial.db'}"}
    com_chave = criar_config(
        tmp_path, raizes["cnpj"], portal_transparencia_chave=CHAVE_PORTAL, **banco
    )
    with TestClient(criar_app(com_chave, extras_conectores=extras)) as cliente:
        caso_id = cliente.post("/casos", json=corpo_caso(), headers=cabecalhos()).json()["id"]
        for fonte, parametros in COLETAS_H2[:5]:
            assert _coletar(cliente, caso_id, fonte, parametros).status_code == 200
        zeta = _entidade(cliente, caso_id, "90000006", "empresa")
        m = _monitorar(cliente, caso_id, zeta["id"]).json()
    monkeypatch.delenv("PORTAL_TRANSPARENCIA_CHAVE", raising=False)
    sem_chave = criar_config(tmp_path, raizes["cnpj"], **banco)
    with TestClient(criar_app(sem_chave, extras_conectores=extras)) as cliente:
        r = cliente.post(
            f"/casos/{caso_id}/monitoramentos/{m['id']}/executar", headers=cabecalhos()
        )
        assert r.status_code == 200, r.text
        situacoes = sorted(c["situacao"] for c in r.json()["coletas"])
        assert situacoes == ["nao_configurado", "nao_configurado", "sucesso", "sucesso", "sucesso"]
        assert r.json()["situacao"] == "parcial"


# ==== Migração idempotente (SQLite e PostgreSQL) =================================================

TABELAS_H2 = ("avisos", "monitoramento_execucoes", "monitoramentos")
GATILHOS = {
    "auditoria_sem_update",
    "auditoria_sem_delete",
    "alerta_decisoes_sem_update",
    "alerta_decisoes_sem_delete",
}


def _voltar_ao_horizonte_1(engine) -> None:
    """Deixa o banco como o do horizonte 1: sem as tabelas novas (as colunas não mudaram)."""
    with engine.begin() as conexao:
        for tabela in TABELAS_H2:
            conexao.execute(text(f"DROP TABLE {tabela}"))


def test_migracao_do_horizonte_1_cria_as_tabelas_novas_e_mantem_os_gatilhos(tmp_path, monkeypatch):
    from buscador import migrar
    from buscador.db import ErroEsquema

    url = f"sqlite:///{tmp_path / 'h1.db'}"
    engine = criar_engine(url)
    criar_esquema(engine)
    with engine.begin() as conexao:
        conexao.execute(
            text(
                "INSERT INTO casos (id, titulo, descricao, numero_procedimento, criado_por, "
                "criado_em, finalidade) VALUES ('c1', 'Caso do horizonte 1', '', '', 'ana', "
                "'2026-11-16T00:00:00.000000Z', 'teste')"
            )
        )
        conexao.execute(
            text(
                "INSERT INTO auditoria (versao, quando, usuario, acao, detalhes, hash_anterior, "
                "hash) VALUES (2, 'q', 'ana', 'caso.criar', '{}', 'h0', 'h1')"
            )
        )
    _voltar_ao_horizonte_1(engine)
    with pytest.raises(ErroEsquema, match="monitoramentos"):
        verificar_esquema(engine)
    engine.dispose()

    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.delenv("DB_PAPEL_APP", raising=False)
    assert migrar.main() == 0
    assert migrar.main() == 0  # idempotente

    engine = criar_engine(url)
    verificar_esquema(engine)
    assert set(TABELAS_H2) <= set(inspect(engine).get_table_names())
    assert criar_esquema(engine) == []
    with engine.connect() as conexao:
        gatilhos = {
            linha[0]
            for linha in conexao.execute(
                text("SELECT name FROM sqlite_master WHERE type = 'trigger'")
            )
        }
        assert GATILHOS <= gatilhos
        assert conexao.execute(text("SELECT titulo FROM casos")).scalar_one() == (
            "Caso do horizonte 1"
        )
    for sql in ("UPDATE auditoria SET usuario = 'x'", "DELETE FROM auditoria"):
        with pytest.raises(Exception, match="somente de acrescimo"), engine.begin() as conexao:
            conexao.execute(text(sql))
    # As tabelas novas têm as relações esperadas com casos, entidades e entre si.
    chaves = {
        tabela: {(fk["constrained_columns"][0], fk["referred_table"]) for fk in fks}
        for tabela in TABELAS_H2
        if (fks := inspect(engine).get_foreign_keys(tabela))
    }
    assert chaves["monitoramentos"] == {("caso_id", "casos"), ("entidade_id", "entidades")}
    assert chaves["monitoramento_execucoes"] == {
        ("monitoramento_id", "monitoramentos"),
        ("caso_id", "casos"),
    }
    assert chaves["avisos"] == {
        ("caso_id", "casos"),
        ("monitoramento_id", "monitoramentos"),
        ("execucao_id", "monitoramento_execucoes"),
        ("entidade_id", "entidades"),
    }


URL_DONO = os.environ.get("TEST_POSTGRES_URL", "")
URL_APP = os.environ.get("TEST_POSTGRES_URL_APP", "")
PAPEL_APP = os.environ.get("TEST_POSTGRES_PAPEL_APP", "osint_app")
exige_postgres = pytest.mark.skipif(
    not (URL_DONO and URL_APP), reason="defina TEST_POSTGRES_URL e TEST_POSTGRES_URL_APP"
)


@pytest.fixture
def postgres_vazio():
    dono = criar_engine(URL_DONO)
    Base.metadata.drop_all(dono)
    yield dono
    Base.metadata.drop_all(dono)
    dono.dispose()


@exige_postgres
def test_postgres_migracao_do_horizonte_1_e_fluxo_do_horizonte_2(
    postgres_vazio, tmp_path, raizes, extras, monkeypatch
):
    """No PostgreSQL: o banco do horizonte 1 ganha as tabelas novas pelo `migrar` (duas vezes),
    os gatilhos da auditoria continuam, e a API, com o papel da aplicação, faz o fluxo do
    horizonte 2 (coletas, tipologias, monitoramento e agendador)."""
    from buscador import migrar

    dono = postgres_vazio
    criar_esquema(dono)
    _voltar_ao_horizonte_1(dono)
    monkeypatch.setenv("DATABASE_URL", URL_DONO)
    monkeypatch.setenv("DB_PAPEL_APP", PAPEL_APP)
    assert migrar.main() == 0
    assert migrar.main() == 0
    assert set(TABELAS_H2) <= set(inspect(dono).get_table_names())
    with dono.connect() as conexao:
        gatilhos = {
            linha[0]
            for linha in conexao.execute(
                text("SELECT tgname FROM pg_trigger WHERE NOT tgisinternal")
            )
        }
    assert GATILHOS | {"auditoria_sem_truncate", "alerta_decisoes_sem_truncate"} <= gatilhos

    config = criar_config(
        tmp_path,
        raizes["cnpj"],
        database_url=URL_APP,
        db_criar_esquema=False,
        tse_dir_base=raizes["tse"],
        editais_dir_base=raizes["editais"],
        portal_transparencia_chave=CHAVE_PORTAL,
        portal_transparencia_requisicoes_por_minuto=700,
        pncp_termos_requisicoes_por_minuto=0,
    )
    app = criar_app(config, extras_conectores=extras)
    with TestClient(app) as cliente:
        caso_id = cliente.post("/casos", json=corpo_caso(), headers=cabecalhos()).json()["id"]
        for fonte, parametros in COLETAS_H2:
            r = _coletar(cliente, caso_id, fonte, parametros)
            assert r.status_code == 200 and r.json()["situacao"] == "sucesso", (fonte, r.text)
        r = cliente.post(f"/casos/{caso_id}/alertas/executar", json={}, headers=cabecalhos())
        assert r.status_code == 200, r.text
        assert {a["tipologia"] for a in r.json()} >= {"T5", "T6", "T7", "T8", "T9", "T10"}
        zeta = _entidade(cliente, caso_id, "90000006", "empresa")
        m = _monitorar(cliente, caso_id, zeta["id"]).json()
        _provocar_novidades(raizes, extras)
        assert Agendador(app.state).ciclo(agora=datetime.now(UTC) + timedelta(hours=25)) == 1
        avisos = cliente.get(f"/casos/{caso_id}/avisos", headers=cabecalhos()).json()
        assert len(avisos) == 5 and {a["monitoramento_id"] for a in avisos} == {m["id"]}
        assert cliente.get("/auditoria/verificar", headers=cabecalhos("tok-admin")).json()[
            "integra"
        ]
    app.state.engine.dispose()
    # O papel da aplicação continua sem poder alterar a auditoria.
    aplicacao = criar_engine(URL_APP)
    with pytest.raises(Exception), aplicacao.begin() as conexao:  # noqa: B017
        conexao.execute(text("UPDATE auditoria SET usuario = 'x'"))
    aplicacao.dispose()


def test_fixtures_h2_sao_texto_simples():
    """As fixtures do horizonte 2 são texto legível e versionável (sem binários)."""
    binarios = [
        p for p in H2.rglob("*") if p.is_file() and p.suffix not in (".json", ".csv", ".txt")
    ]
    assert binarios == []
