"""Testes de regressão dos achados da auditoria de segurança (run-1).

Cada bloco cita o *fingerprint* do achado em ``findings.json``. Só há dados sintéticos: tokens,
chaves, CNPJs e arquivos são inventados para o teste.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text

from buscador import auditoria, relatorio, tipologias
from buscador.api import criar_app
from buscador.conectores import ErroConector
from buscador.conectores.base import RegistroEmpresa, RegistroEstabelecimento, RegistroSocio
from buscador.conectores.cnpj_arquivo import ConectorCnpjArquivo, converter_capital
from buscador.conectores.pncp import ConectorPncp, TransporteSimulado, _para_float
from buscador.config import Configuracao, ErroConfiguracao
from buscador.custodia import ConteudoGrandeDemais, Custodia
from buscador.db import ErroEsquema, criar_engine, criar_esquema
from buscador.modelos import Alerta, Aresta, Auditoria, Captura, Caso, Entidade, Sugestao
from buscador.resolucao import Resolvedor
from buscador.resolucao.normalizacao import normalizar_endereco
from tests.conftest import (
    CHAVE,
    CHAVE_CUSTODIA,
    CHAVE_HMAC,
    DIR_CNPJ,
    FIXTURES_PNCP,
    TOKENS,
    cabecalhos,
    corpo_caso,
    criar_config,
    incluir_equipe,
)

RAIZ = Path(__file__).resolve().parents[1]
PERIODO = {"dataInicial": "20260101", "dataFinal": "20261231"}
TOKENS_PUBLICADOS = (
    "dev-analista:ana.analista:analista,"
    "dev-delegado:carlos.delegado:delegado,"
    "dev-admin:admin.sistema:admin"
)


# ---- Apoio ------------------------------------------------------------------------------------


def _contar(app, modelo, *filtros) -> int:
    with app.state.fabrica_sessoes() as s:
        return s.execute(select(func.count()).select_from(modelo).where(*filtros)).scalar_one()


def _acoes(app, caso_id: str) -> list[str]:
    with app.state.fabrica_sessoes() as s:
        return list(
            s.execute(
                select(Auditoria.acao).where(Auditoria.caso_id == caso_id).order_by(Auditoria.seq)
            ).scalars()
        )


def _coletar(cliente, caso_id: str, fonte: str, parametros: dict, token: str = "tok-analista"):
    return cliente.post(
        f"/casos/{caso_id}/coletas",
        json={"fonte": fonte, "parametros": parametros},
        headers=cabecalhos(token),
    )


def _linha(*campos: str) -> str:
    return ";".join(f'"{c}"' for c in campos) + "\r\n"


def _empresa(basico: str, razao: str, capital: str) -> str:
    return _linha(basico, razao, "2062", "49", capital, "01", "")


def _estabelecimento(basico: str, logradouro: str = "", cep: str = "", municipio: str = "") -> str:
    campos = [""] * 30
    campos[0], campos[1], campos[2], campos[3] = basico, "0001", "00", "1"
    campos[10] = "20200101"
    campos[13] = "RUA" if logradouro else ""
    campos[14], campos[15], campos[18], campos[20] = logradouro, "10" if logradouro else "", cep, ""
    campos[19] = "DF" if municipio else ""
    campos[20] = municipio
    return _linha(*campos)


def _socio(basico: str, nome: str, cpf: str) -> str:
    return _linha(basico, "2", nome, cpf, "49", "20200101", "", "", "", "", "4")


def _registrador(sessao, custodia: Custodia):
    caso = Caso(titulo="c", criado_por="t")
    sessao.add(caso)
    sessao.flush()

    def registrar(fonte, url_ou_arquivo, conteudo, metadados):
        return custodia.registrar(
            sessao,
            caso_id=caso.id,
            fonte=fonte,
            url_ou_arquivo=url_ou_arquivo,
            conteudo=conteudo,
            metadados=metadados,
        )

    registrar.caso_id = caso.id
    return registrar


def _pagina_pncp(**sobrescrita) -> dict:
    """Uma página sintética do PNCP com um contrato da empresa sintética 90000001."""
    item = {
        "numeroControlePNCP": "90100001000109-2-000099/2026",
        "niFornecedor": "90000001000129",
        "tipoPessoa": "PJ",
        "nomeRazaoSocialFornecedor": "ALFA CONSTRUCOES SINTETICA LTDA",
        "orgaoEntidade": {"cnpj": "90100001000109", "razaoSocial": "ORGAO SINTETICO"},
        "objetoContrato": "Objeto sintético",
        "valorGlobal": 200000.0,
        "dataAssinatura": "2026-03-01",
    }
    item.update(sobrescrita)
    return {"data": [item], "paginasRestantes": 0, "empty": False}


def _app_com_pagina(tmp_path: Path, raiz_cnpj: Path, pagina: bytes, **config_extra):
    transporte = TransporteSimulado({1: pagina})
    config = criar_config(tmp_path, raiz_cnpj, **config_extra)
    return criar_app(config, extras_conectores={"pncp": {"transporte": transporte}}), transporte


def _novo_caso(cliente, token: str = "tok-analista", titulo: str = "Caso sintético") -> str:
    r = cliente.post("/casos", json=corpo_caso(titulo), headers=cabecalhos(token))
    assert r.status_code == 201, r.text
    if token == "tok-analista":
        incluir_equipe(cliente, r.json()["id"])
    return r.json()["id"]


# ==== config.py:Configuracao.tokens_dev:default-credentials ===================================


def test_servico_nao_inicia_sem_tokens_ou_com_tokens_publicados(tmp_path, monkeypatch):
    """H23: os tokens fixos só existem com MODO_DEV=1. Fora do modo de desenvolvimento, um
    TOKENS_DEV preenchido (publicado ou não) impede o serviço de iniciar, e sem TOKENS_DEV o
    serviço sobe sem nenhum token fixo: o acesso é por usuário real, com senha, MFA e sessão."""
    for variavel in ("TOKENS_DEV", "AUDITORIA_CHAVE_HMAC", "MODO_DEV", "CUSTODIA_CHAVE"):
        monkeypatch.delenv(variavel, raising=False)
    base = {
        "database_url": f"sqlite:///{tmp_path / 'x.db'}",
        "dados_dir": tmp_path / "d",
        "auditoria_chave_hmac": CHAVE_HMAC,
        "custodia_chave": CHAVE_CUSTODIA,
        "monitoramento_agendador_ativo": False,
    }

    # Sem TOKENS_DEV não há valor padrão nem token fixo: nenhum token publicado autentica.
    sem_tokens = Configuracao(_env_file=None, **base)
    assert sem_tokens.tokens_dev == "" and sem_tokens.modo_dev is False
    app = criar_app(sem_tokens)
    assert app.state.tokens == {}
    with TestClient(app) as producao:
        for token in ("dev-admin", "tok-admin", ""):
            r = producao.get("/me", headers={"Authorization": f"Bearer {token}"})
            assert r.status_code == 401

    # Fora do modo de desenvolvimento, qualquer TOKENS_DEV é recusado (falha fechada).
    for tokens in (TOKENS_PUBLICADOS, TOKENS, TOKENS + ",dev-admin:x:admin"):
        with pytest.raises(ErroConfiguracao, match="MODO_DEV"):
            criar_app(Configuracao(_env_file=None, tokens_dev=tokens, **base))


def test_dev_admin_so_autentica_com_a_flag_explicita(cliente, tmp_path):
    # Aplicação normal: o token publicado nunca dá 200.
    assert cliente.get("/me", headers={"Authorization": "Bearer dev-admin"}).status_code == 401
    r = cliente.get("/auditoria", headers=cabecalhos("dev-delegado"))
    assert r.status_code == 401

    config = criar_config(tmp_path / "dev", tokens_dev=TOKENS_PUBLICADOS, modo_dev=True)
    with TestClient(criar_app(config)) as dev:
        assert dev.get("/me", headers={"Authorization": "Bearer dev-admin"}).json() == {
            "usuario": "admin.sistema",
            "perfil": "admin",
        }


@pytest.mark.parametrize(
    "tokens",
    [
        ":usuario:admin",  # token vazio
        "tok-a:ana:analista,tok-a:outra:admin",  # token repetido
        "tok-a:ana:superusuario",  # perfil desconhecido
        "tok-a:ana",  # formato
        " , ",  # nada
    ],
)
def test_tokens_mal_formados_sao_recusados(tokens):
    config = Configuracao(
        _env_file=None, tokens_dev=tokens, auditoria_chave_hmac=CHAVE_HMAC, modo_dev=True
    )
    if not tokens.replace(",", "").strip():
        # Lista vazia no modo de desenvolvimento: nenhum token fixo (o acesso é por usuário real).
        assert config.tokens() == {}
        return
    with pytest.raises(ErroConfiguracao):
        config.tokens()


def test_tokens_ficam_em_memoria_so_como_resumo(app):
    assert all(len(resumo) == 64 for resumo in app.state.tokens)
    assert "tok-admin" not in app.state.tokens


def test_compose_exige_segredos_e_nao_publica_o_postgres():
    compose = (RAIZ / "docker-compose.yml").read_text(encoding="utf-8")
    for variavel in ("CUSTODIA_CHAVE", "AUDITORIA_CHAVE_HMAC", "POSTGRES_PASSWORD"):
        assert f"${{{variavel}:?" in compose
    # H23: os tokens fixos são opcionais (só valem com MODO_DEV=1) e não têm valor padrão.
    assert "TOKENS_DEV: ${TOKENS_DEV:-}" in compose and "MODO_DEV: ${MODO_DEV:-0}" in compose
    assert "POSTGRES_APP_PASSWORD:?" in compose
    assert "dev-analista" not in compose and "dev-admin" not in compose
    assert "osint:osint@" not in compose and "POSTGRES_PASSWORD: osint" not in compose
    # compose-postgres-published-fixed-credential: a porta só existe no arquivo opcional.
    assert "55432" not in compose
    assert "55432" in (RAIZ / "docker-compose.postgres-porta.yml").read_text(encoding="utf-8")
    # A API usa o papel da aplicação e não cria o esquema.
    assert "osint_app:${POSTGRES_APP_PASSWORD" in compose and 'DB_CRIAR_ESQUEMA: "0"' in compose

    exemplo = (RAIZ / ".env.example").read_text(encoding="utf-8")
    assert "\nTOKENS_DEV=\n" in exemplo and "\nAUDITORIA_CHAVE_HMAC=\n" in exemplo
    assert "\nCUSTODIA_CHAVE=\n" in exemplo and "\nMODO_DEV=0\n" in exemplo
    assert "\nIA_EXTRACAO_ATIVA=0\n" in exemplo
    assert "TOKENS_DEV=dev-" not in exemplo


# ==== auditoria/servico.py:unkeyed-unanchored-hash-chain ======================================


def test_servico_nao_inicia_sem_chave_de_hmac(tmp_path):
    config = criar_config(tmp_path, auditoria_chave_hmac="")
    with pytest.raises(ErroConfiguracao, match="AUDITORIA_CHAVE_HMAC"):
        criar_app(config)
    with pytest.raises(ErroConfiguracao):
        criar_config(tmp_path, auditoria_chave_hmac="curta").chave_auditoria()


def _registrar(sessao, n: int) -> None:
    for i in range(n):
        auditoria.registrar(
            sessao, CHAVE, usuario="ana", acao=f"acao.{i}", caso_id="c1", justificativa="j"
        )
    sessao.commit()


def _reescrever_cadeia(engine, chave: bytes) -> None:
    """O que faz quem tem a credencial do banco: derruba os gatilhos e recalcula os hashes."""
    with engine.begin() as conexao:
        conexao.execute(text("DROP TRIGGER auditoria_sem_update"))
        linhas = conexao.execute(text("SELECT * FROM auditoria ORDER BY seq")).mappings().all()
        anterior = auditoria.HASH_GENESE
        for linha in linhas:
            entrada = dict(linha)
            entrada["detalhes"] = json.loads(entrada["detalhes"])
            entrada["hash_anterior"] = anterior
            anterior = auditoria.calcular_hash(entrada, chave)
            conexao.execute(
                text("UPDATE auditoria SET hash_anterior = :a, hash = :h WHERE seq = :s"),
                {"a": entrada["hash_anterior"], "h": anterior, "s": linha["seq"]},
            )


def test_reescrita_sem_a_chave_do_hmac_e_detectada(sessao, engine):
    _registrar(sessao, 4)
    with engine.begin() as conexao:
        conexao.execute(text("DROP TRIGGER auditoria_sem_delete"))
        conexao.execute(text("DROP TRIGGER IF EXISTS auditoria_sem_update"))
        conexao.execute(text("UPDATE auditoria SET justificativa = 'EDITADO' WHERE seq = 2"))
    with engine.begin() as conexao:
        conexao.execute(
            text(
                "CREATE TRIGGER auditoria_sem_update BEFORE UPDATE ON auditoria "
                "BEGIN SELECT RAISE(ABORT, 'x'); END;"
            )
        )
    # O atacante não tem AUDITORIA_CHAVE_HMAC: recalcula com outra chave (ou sem chave).
    _reescrever_cadeia(engine, b"chave-que-o-atacante-inventou-000000")
    sessao.expire_all()
    resultado = auditoria.verificar(sessao, CHAVE)
    assert resultado.integra is False and resultado.primeira_quebra == 1
    assert "não confere" in resultado.motivo


def test_corte_do_fim_da_cadeia_e_detectado_pela_ancora(sessao, engine, tmp_path):
    ancora = auditoria.Ancora(tmp_path / "dados", CHAVE)
    _registrar(sessao, 6)
    assert ancora.avancar(6, sessao.get(Auditoria, 6).hash) is True
    assert auditoria.verificar(sessao, CHAVE, ancora).integra is True

    with engine.begin() as conexao:
        conexao.execute(text("DROP TRIGGER auditoria_sem_delete"))
        conexao.execute(text("DELETE FROM auditoria WHERE seq > 4"))
    sessao.expire_all()
    # A cadeia que sobrou é internamente consistente...
    assert auditoria.verificar(sessao, CHAVE).integra is True
    # ...mas a âncora externa mostra que o topo foi cortado.
    resultado = auditoria.verificar(sessao, CHAVE, ancora)
    assert resultado.integra is False and resultado.primeira_quebra == 5
    assert "truncada" in resultado.motivo and resultado.ancora_seq == 6


def test_ancora_ausente_adulterada_ou_divergente_quebra_a_verificacao(sessao, tmp_path):
    ancora = auditoria.Ancora(tmp_path / "dados", CHAVE)
    _registrar(sessao, 3)
    resultado = auditoria.verificar(sessao, CHAVE, ancora)
    assert resultado.integra is False and "ausente" in resultado.motivo

    # Hash diferente do que o banco tem na mesma entrada.
    ancora.avancar(3, "f" * 64)
    resultado = auditoria.verificar(sessao, CHAVE, ancora)
    assert resultado.integra is False and resultado.primeira_quebra == 3
    assert "âncora" in resultado.motivo

    # Arquivo editado à mão: o MAC não confere, e a âncora não é sobrescrita em silêncio.
    dados = json.loads(ancora.arquivo.read_text())
    dados["seq"] = 1
    ancora.arquivo.write_text(json.dumps(dados))
    with pytest.raises(auditoria.AncoraInvalida):
        ancora.ler()
    assert ancora.avancar(9, "a" * 64) is False
    assert "inválida" in auditoria.verificar(sessao, CHAVE, ancora).motivo


def test_ancora_so_avanca(tmp_path):
    ancora = auditoria.Ancora(tmp_path, CHAVE)
    assert ancora.ler() is None
    assert ancora.avancar(5, "a" * 64) is True
    assert ancora.avancar(4, "b" * 64) is False and ancora.avancar(5, "c" * 64) is False
    assert ancora.ler().seq == 5 and ancora.ler().hash == "a" * 64
    assert oct(ancora.arquivo.stat().st_mode & 0o777) == "0o600"


def test_api_grava_a_ancora_e_detecta_corte_do_fim(cliente, caso_id, app):
    v = cliente.get("/auditoria/verificar", headers=cabecalhos()).json()
    # caso.criar e as duas inclusões de membros da fixture (H23).
    assert v["integra"] is True and v["ancora_seq"] == 3
    cliente.get(f"/casos/{caso_id}", headers=cabecalhos())
    ponto = app.state.ancora.ler()
    topo = _contar(app, Auditoria)
    assert ponto.seq == topo == 5
    assert app.state.ancora.arquivo.parent == app.state.config.dados_dir / "ancora"

    with app.state.engine.begin() as conexao:
        conexao.execute(text("DROP TRIGGER auditoria_sem_delete"))
        conexao.execute(text("DELETE FROM auditoria WHERE seq >= 2"))
    v = cliente.get("/auditoria/verificar", headers=cabecalhos()).json()
    assert v["integra"] is False and v["primeira_quebra"] == 2 and "truncada" in v["motivo"]
    relatorio_json = cliente.get(f"/casos/{caso_id}/relatorio", headers=cabecalhos()).json()
    assert relatorio_json["auditoria"]["integra"] is False


def test_banco_com_cadeia_versao_1_e_recusado(tmp_path):
    engine = criar_engine(f"sqlite:///{tmp_path / 'v1.db'}")
    with engine.begin() as conexao:
        conexao.execute(
            text(
                "CREATE TABLE auditoria (seq INTEGER PRIMARY KEY, quando VARCHAR(40), "
                "usuario VARCHAR(100), acao VARCHAR(60), caso_id VARCHAR(32), "
                "justificativa TEXT, detalhes JSON, hash_anterior VARCHAR(64), hash VARCHAR(64))"
            )
        )
    with pytest.raises(ErroEsquema, match="versão 1"):
        criar_esquema(engine)


def test_entrada_de_versao_desconhecida_quebra_a_cadeia(sessao, engine):
    _registrar(sessao, 2)
    with engine.begin() as conexao:
        conexao.execute(text("DROP TRIGGER auditoria_sem_update"))
        conexao.execute(text("UPDATE auditoria SET versao = 1 WHERE seq = 2"))
    sessao.expire_all()
    resultado = auditoria.verificar(sessao, CHAVE)
    assert resultado.integra is False and resultado.primeira_quebra == 2
    assert "versão" in resultado.motivo


# ==== auditoria/servico.py:verificar:unbounded-audit-row-size-full-chain-materialization ======


def test_corpo_grande_e_recusado_antes_de_chegar_a_auditoria(cliente, caso_id, app):
    antes = _contar(app, Auditoria)
    grande = {"fonte": "portal_transparencia", "parametros": {"x": "A" * (512 * 1024)}}
    r = cliente.post(f"/casos/{caso_id}/coletas", json=grande, headers=cabecalhos())
    assert r.status_code == 413 and "limite" in r.json()["detail"]

    # Sem Content-Length (corpo em blocos), o limite vale pela soma dos blocos.
    def blocos():
        yield b'{"fonte":"portal_transparencia","parametros":{"x":"'
        for _ in range(40):
            yield b"A" * 16_384
        yield b'"}}'

    r = cliente.post(
        f"/casos/{caso_id}/coletas",
        content=blocos(),
        headers={**cabecalhos(), "Content-Type": "application/json"},
    )
    assert r.status_code == 413
    assert _contar(app, Auditoria) == antes


def test_parametros_grandes_dentro_do_limite_de_corpo_nao_incham_a_auditoria(cliente, caso_id, app):
    antes = _contar(app, Auditoria)
    # Texto acima do limite por valor: recusado no esquema, sem linha de auditoria.
    r = _coletar(cliente, caso_id, "portal_transparencia", {"x": "A" * 5_000})
    assert r.status_code == 422 and _contar(app, Auditoria) == antes
    # Muitos valores pequenos: também recusado no esquema.
    r = _coletar(cliente, caso_id, "portal_transparencia", {"x": [["a"] * 150] * 2})
    assert r.status_code == 422 and _contar(app, Auditoria) == antes

    # Parâmetros aceitos pelo filtro genérico, mas não pela fonte: falha auditada e pequena.
    for _ in range(5):
        r = _coletar(cliente, caso_id, "portal_transparencia", {"x": "A" * 900, "y": "B" * 900})
        assert r.status_code == 422
    with app.state.fabrica_sessoes() as s:
        linhas = s.execute(select(Auditoria).where(Auditoria.caso_id == caso_id)).scalars().all()
    assert len(linhas) == antes + 10  # coleta.iniciar + coleta.falhou por tentativa
    assert (
        max(len(json.dumps(linha.detalhes)) for linha in linhas)
        <= auditoria.servico.MAX_DETALHES_BYTES
    )
    falha = [linha for linha in linhas if linha.acao == "coleta.falhou"][-1]
    assert falha.detalhes["parametros"]["x"]["truncado"] is True
    assert falha.detalhes["parametros"]["x"]["tamanho"] == 900


def test_detalhes_da_auditoria_tem_tamanho_limitado(sessao):
    linha = auditoria.registrar(
        sessao,
        CHAVE,
        usuario="ana",
        acao="teste",
        caso_id=None,
        justificativa="j",
        detalhes={"perfil": "analista", "lista": ["x" * 400] * 190, "texto": "y" * 100_000},
    )
    sessao.commit()
    assert len(json.dumps(linha.detalhes)) <= auditoria.servico.MAX_DETALHES_BYTES
    assert linha.detalhes["truncado"] is True and linha.detalhes["perfil"] == "analista"
    assert auditoria.verificar(sessao, CHAVE).integra is True
    with pytest.raises(ValueError):
        auditoria.registrar(
            sessao, CHAVE, usuario="a", acao="x", caso_id=None, justificativa="j" * 501
        )


def test_verificacao_percorre_a_cadeia_em_lotes(sessao, monkeypatch):
    monkeypatch.setattr(auditoria.servico, "LOTE_VERIFICACAO", 3)
    _registrar(sessao, 10)
    consultas = []
    original = sessao.execute

    def contar_consultas(*a, **k):
        consultas.append(1)
        return original(*a, **k)

    monkeypatch.setattr(sessao, "execute", contar_consultas)
    resultado = auditoria.verificar(sessao, CHAVE)
    assert resultado.integra is True and resultado.entradas == 10
    # 1 contagem + 4 lotes de até 3 linhas + 1 lote vazio: nunca a cadeia inteira de uma vez.
    assert len(consultas) == 6


def test_justificativa_tem_limite_de_tamanho(cliente):
    r = cliente.get("/casos", headers=cabecalhos(justificativa="j" * 501))
    assert r.status_code == 400 and "500" in r.json()["detail"]
    assert cliente.get("/casos", headers=cabecalhos(justificativa="j" * 500)).status_code == 200


def test_listagem_da_auditoria_e_paginada(cliente, caso_id):
    for _ in range(4):
        cliente.get(f"/casos/{caso_id}", headers=cabecalhos())
    h = cabecalhos("tok-delegado")
    pagina = cliente.get("/auditoria", params={"limite": 2, "deslocamento": 1}, headers=h).json()
    assert [e["seq"] for e in pagina] == [2, 3]
    assert cliente.get("/auditoria", params={"limite": 5000}, headers=h).status_code == 422


# ==== relatorio/servico.py:gerar:entradas_do_caso-bypasses-auditoria-role-gate ================


def test_relatorio_do_analista_nao_traz_a_trilha_de_auditoria(cliente):
    h_delegado = cabecalhos("tok-delegado", "JUSTIFICATIVA-SIGILOSA-DO-DELEGADO")
    caso_id = cliente.post("/casos", json=corpo_caso("Caso"), headers=h_delegado).json()["id"]
    cliente.get(f"/casos/{caso_id}", headers=h_delegado)
    # H23: o analista só chega ao relatório por ser membro do caso.
    r = cliente.post(
        f"/casos/{caso_id}/membros", json={"usuario": "ana", "papel": "leitor"}, headers=h_delegado
    )
    assert r.status_code == 200, r.text

    h_analista = cabecalhos("tok-analista")
    assert (
        cliente.get("/auditoria", params={"caso_id": caso_id}, headers=h_analista).status_code
        == 403
    )
    r = cliente.get(f"/casos/{caso_id}/relatorio", headers=h_analista)
    assert r.status_code == 200
    aud = r.json()["auditoria"]
    assert aud["entradas_do_caso"] == [] and aud["trilha_restrita"] is True
    assert aud["integra"] is True and aud["entradas"] >= 2
    assert "JUSTIFICATIVA-SIGILOSA" not in r.text and "carlos" not in json.dumps(aud)

    md = cliente.get(
        f"/casos/{caso_id}/relatorio", params={"formato": "md"}, headers=h_analista
    ).text
    assert "JUSTIFICATIVA-SIGILOSA" not in md and "| Seq |" not in md
    assert "restritas aos perfis delegado e admin" in md

    # Delegado e admin continuam recebendo as entradas.
    r = cliente.post(
        f"/casos/{caso_id}/membros", json={"usuario": "root", "papel": "leitor"}, headers=h_delegado
    )
    assert r.status_code == 200, r.text
    for token in ("tok-delegado", "tok-admin"):
        aud = cliente.get(f"/casos/{caso_id}/relatorio", headers=cabecalhos(token)).json()[
            "auditoria"
        ]
        assert aud["trilha_restrita"] is False
        assert "JUSTIFICATIVA-SIGILOSA-DO-DELEGADO" in [
            e["justificativa"] for e in aud["entradas_do_caso"]
        ]


# ==== tipologias/motor.py:_captura_por_papel:first-fonte-vs-last-writer-wins-capital ==========


def _t3_de_alfa(cliente, caso_id: str) -> list[dict]:
    r = cliente.post(
        f"/casos/{caso_id}/alertas/executar", json={"tipologias": ["T3"]}, headers=cabecalhos()
    )
    assert r.status_code == 200, r.text
    return [a for a in r.json() if "ALFA" in a["motivo"]]


def test_t3_cita_a_captura_que_forneceu_o_capital(cliente, caso_carregado, app, raiz_cnpj):
    assert _t3_de_alfa(cliente, caso_carregado) == []  # capital de 500.000,00: sem alerta

    # Segundo dump sintético de Empresas, com capital de R$ 100,00 para a mesma empresa.
    (raiz_cnpj / "dump2").mkdir()
    (raiz_cnpj / "dump2" / "SINTETICO.txt").write_text("sintético")
    (raiz_cnpj / "dump2" / "B.EMPRECSV").write_bytes(
        _empresa("90000001", "ALFA CONSTRUCOES SINTETICA LTDA", "100,00").encode("latin-1")
    )
    r = _coletar(cliente, caso_carregado, "cnpj_arquivo", {"diretorio": "dump2"})
    assert r.status_code == 200, r.text
    captura_dump2 = r.json()["captura_ids"][0]

    alerta = _t3_de_alfa(cliente, caso_carregado)[0]
    assert "R$ 100,00" in alerta["motivo"]
    evidencia = alerta["evidencias"][0]
    assert evidencia["captura_id"] == captura_dump2
    with app.state.fabrica_sessoes() as s:
        conteudo = app.state.custodia.ler(s.get(Captura, captura_dump2))
    assert b'"100,00"' in conteudo  # os bytes citados contêm o valor do motivo

    # Terceiro dump, com o capital em branco: o valor fica e a citação não muda de captura.
    (raiz_cnpj / "dump3").mkdir()
    (raiz_cnpj / "dump3" / "C.EMPRECSV").write_bytes(
        _empresa("90000001", "ALFA CONSTRUCOES SINTETICA LTDA", "").encode("latin-1")
    )
    assert (
        _coletar(cliente, caso_carregado, "cnpj_arquivo", {"diretorio": "dump3"}).status_code == 200
    )
    alerta = _t3_de_alfa(cliente, caso_carregado)[0]
    assert "R$ 100,00" in alerta["motivo"]
    assert alerta["evidencias"][0]["captura_id"] == captura_dump2


# ==== rotas_casos.py:coletar-unaudited-on-non-ErroConector-exception ==========================


def test_falha_inesperada_na_coleta_fica_auditada_com_as_capturas(
    cliente, caso_id, app, monkeypatch, transporte_pncp
):
    original = Resolvedor.incorporar

    def explodir(self, registros):
        original(self, registros)  # grava entidades e arestas, e só então falha
        raise RuntimeError("falha sintética na resolução")

    monkeypatch.setattr(Resolvedor, "incorporar", explodir)
    r = _coletar(cliente, caso_id, "pncp", PERIODO)
    assert r.status_code == 500 and "auditoria" in r.json()["detail"]
    assert len(transporte_pncp.chamadas) == 2  # as consultas externas aconteceram

    assert _acoes(app, caso_id) == [
        "caso.criar",
        "membro.definir",
        "membro.definir",
        "coleta.iniciar",
        "coleta.falhou",
    ]
    with app.state.fabrica_sessoes() as s:
        falha = s.execute(select(Auditoria).where(Auditoria.acao == "coleta.falhou")).scalar_one()
        capturas = s.execute(select(Captura.id).where(Captura.caso_id == caso_id)).scalars().all()
    assert falha.detalhes["tipo_erro"] == "RuntimeError"
    # As capturas produzidas antes da falha existem e são as citadas na trilha.
    assert len(capturas) == 2 and sorted(falha.detalhes["captura_ids"]) == sorted(capturas)
    assert _contar(app, Entidade) == 0  # o trabalho derivado foi desfeito


def test_tentativa_de_coleta_e_confirmada_antes_do_conector(cliente, caso_id, app, monkeypatch):
    vistos = []

    def espiar(*args, **kwargs):
        vistos.append(_acoes(app, caso_id))  # lido por outra sessão: só enxerga o confirmado
        raise ErroConector("falha sintética")

    monkeypatch.setattr("buscador.api.rotas_casos.executar_coleta", espiar)
    assert _coletar(cliente, caso_id, "pncp", PERIODO).status_code == 422
    assert vistos == [["caso.criar", "membro.definir", "membro.definir", "coleta.iniciar"]]


@pytest.mark.parametrize(
    "parametros",
    [
        {**PERIODO, "max_paginas": "abc"},
        {**PERIODO, "max_paginas": None},
        {**PERIODO, "tamanhoPagina": [1]},
        {**PERIODO, "nota": "parametro desconhecido"},
        {"dataInicial": "20260101"},
    ],
)
def test_parametros_invalidos_dao_422_auditado_sem_consulta_externa(
    cliente, caso_id, app, transporte_pncp, parametros
):
    r = _coletar(cliente, caso_id, "pncp", parametros)
    assert r.status_code == 422 and isinstance(r.json()["detail"], str)
    assert transporte_pncp.chamadas == []
    assert _acoes(app, caso_id)[-2:] == ["coleta.iniciar", "coleta.falhou"]


def test_substituto_isolado_e_recusado_sem_efeito_colateral(cliente, caso_id, app, transporte_pncp):
    antes = _contar(app, Auditoria)
    corpo = (
        b'{"fonte":"pncp","parametros":{"dataInicial":"20260101","dataFinal":"20261231",'
        b'"nota":"\\ud800"}}'
    )
    r = cliente.post(
        f"/casos/{caso_id}/coletas",
        content=corpo,
        headers={**cabecalhos(), "Content-Type": "application/json"},
    )
    assert r.status_code == 422
    assert transporte_pncp.chamadas == [] and _contar(app, Auditoria) == antes
    assert not any((app.state.config.dados_dir / "capturas").glob("*/*"))


# ==== rotas_casos.py:coletar:rollback-discards-custody-captures ===============================


def test_captura_citada_no_erro_503_continua_registrada(tmp_path, raiz_cnpj):
    transporte = TransporteSimulado.de_diretorio(FIXTURES_PNCP, falhas_iniciais=1)
    config = criar_config(tmp_path, raiz_cnpj, pncp_tentativas=1)
    app = criar_app(config, extras_conectores={"pncp": {"transporte": transporte}})
    with TestClient(app) as cliente:
        caso_id = _novo_caso(cliente)
        r = _coletar(cliente, caso_id, "pncp", PERIODO)
        assert r.status_code == 422 and "HTTP 503" in r.json()["detail"]
        captura_id = r.json()["detail"].split("captura ")[1].split(" ")[0]

        captura = cliente.get(f"/capturas/{captura_id}", headers=cabecalhos())
        assert captura.status_code == 200 and captura.json()["metadados"]["http_status"] == 503
        verificacao = cliente.get(f"/capturas/{captura_id}/verificar", headers=cabecalhos())
        assert verificacao.json()["integra"] is True
        detalhe = cliente.get(f"/casos/{caso_id}", headers=cabecalhos()).json()
        assert detalhe["contagens"]["capturas"] == 1

        entradas = cliente.get(
            "/auditoria", params={"caso_id": caso_id}, headers=cabecalhos("tok-admin")
        ).json()
        falha = next(e for e in entradas if e["acao"] == "coleta.falhou")
        assert falha["detalhes"]["captura_ids"] == [captura_id]


# ==== auditoria/detalhes-nonfinite-float-poisons-relatorio-json ===============================


@pytest.mark.parametrize("literal", ["NaN", "Infinity", "-Infinity"])
def test_nan_e_infinity_no_corpo_dao_422_sem_linha_de_auditoria(cliente, caso_id, app, literal):
    h = {**cabecalhos(), "Content-Type": "application/json"}
    antes = _contar(app, Auditoria)
    coleta = f'{{"fonte":"portal_transparencia","parametros":{{"x":{literal}}}}}'.encode()
    assert cliente.post(f"/casos/{caso_id}/coletas", content=coleta, headers=h).status_code == 422
    for nome in ("t1_dias_minimos", "t2_minimo_empresas", "t3_razao_minima"):
        corpo = f'{{"parametros":{{"{nome}":{literal}}}}}'.encode()
        r = cliente.post(f"/casos/{caso_id}/alertas/executar", content=corpo, headers=h)
        assert r.status_code == 422, (nome, r.text)
    assert _contar(app, Auditoria) == antes
    assert cliente.get(f"/casos/{caso_id}/relatorio", headers=cabecalhos()).status_code == 200


@pytest.mark.parametrize(
    "parametros",
    [
        {"t3_razao_minima": "inf"},
        {"t3_razao_minima": 0},
        {"t3_razao_minima": -1},
        {"t2_minimo_empresas": 1},
        {"t1_dias_minimos": -5},
        {"t1_dias_minimos": 1e300},
        {"desconhecido": 1},
    ],
)
def test_limiares_fora_da_faixa_dao_422(cliente, caso_id, parametros):
    r = cliente.post(
        f"/casos/{caso_id}/alertas/executar", json={"parametros": parametros}, headers=cabecalhos()
    )
    assert r.status_code == 422


def test_parametros_efetivos_recusa_nao_finitos():
    padrao = {"t1_dias_minimos": 180, "t2_minimo_empresas": 3, "t3_razao_minima": 10.0}
    for valor in (float("nan"), float("inf"), float("-inf")):
        for nome in padrao:
            with pytest.raises(ValueError):
                tipologias.parametros_efetivos(padrao, {nome: valor})
    with pytest.raises(ValueError):
        tipologias.parametros_efetivos(padrao, {"t2_minimo_empresas": 1})
    assert tipologias.parametros_efetivos(padrao, {"t1_dias_minimos": 90.0}) == {
        **padrao,
        "t1_dias_minimos": 90,
    }


def test_json_canonico_e_registro_recusam_nan(sessao):
    with pytest.raises(ValueError):
        auditoria.json_canonico({"x": float("nan")})
    with pytest.raises(ValueError):
        auditoria.registrar(
            sessao,
            CHAVE,
            usuario="a",
            acao="x",
            caso_id=None,
            justificativa="j",
            detalhes={"x": float("inf")},
        )
    sessao.rollback()
    assert auditoria.contar(sessao) == 0
    # ASCII puro: um substituto isolado não quebra mais a codificação do hash.
    assert auditoria.json_canonico({"t": "\ud800é"}) == '{"t":"\\ud800\\u00e9"}'


def test_relatorio_json_sobrevive_a_valores_nao_finitos_ja_gravados(cliente, caso_carregado, app):
    """Linhas antigas (anteriores à correção) com NaN/Infinity não derrubam mais o relatório."""
    cliente.post(f"/casos/{caso_carregado}/alertas/executar", headers=cabecalhos())
    with app.state.engine.begin() as conexao:
        conexao.execute(
            text("UPDATE alertas SET parametros = :p WHERE caso_id = :c"),
            {"p": '{"t3_razao_minima": NaN, "x": Infinity}', "c": caso_carregado},
        )
        conexao.execute(
            text("UPDATE entidades SET atributos = :a WHERE caso_id = :c AND tipo = 'contrato'"),
            {
                "a": '{"valor_global": Infinity, "data_assinatura": "2026-03-01"}',
                "c": caso_carregado,
            },
        )
    r = cliente.get(f"/casos/{caso_carregado}/relatorio", headers=cabecalhos("tok-delegado"))
    assert r.status_code == 200
    json.loads(r.text, parse_constant=lambda c: pytest.fail(f"constante {c} no relatório"))
    assert r.json()["alertas"][0]["parametros"]["t3_razao_minima"] is None
    for rota in ("alertas", "entidades"):
        assert (
            cliente.get(f"/casos/{caso_carregado}/{rota}", headers=cabecalhos()).status_code == 200
        )
    # As tipologias também seguem rodando, pulando os valores inválidos.
    r = cliente.post(f"/casos/{caso_carregado}/alertas/executar", headers=cabecalhos())
    assert r.status_code == 200


# ==== conectores:nonfinite-collected-number:converter_capital+pncp._para_float ================


def test_conectores_descartam_numeros_nao_finitos():
    for texto in ("NaN", "nan", "inf", "-inf", "Infinity", "1e999", "-1,00", "abc", ""):
        assert converter_capital(texto) is None, texto
    assert converter_capital("1.234,56") == 1234.56 and converter_capital("0,00") == 0.0
    for valor in ("NaN", "inf", "1e999", float("nan"), float("inf"), None, "", "abc", True, [1]):
        assert _para_float(valor) is None, valor
    assert _para_float("10.5") == 10.5 and _para_float(3) == 3.0
    assert tipologias.motor.formatar_moeda(float("inf")) == "valor inválido"
    assert tipologias.motor.formatar_numero(float("nan")) == "valor inválido"


def test_contrato_com_valor_infinito_nao_quebra_alertas_nem_relatorio(tmp_path, raiz_cnpj):
    # 1e999 é JSON válido, mas vira infinito ao ser lido como float.
    pagina = json.dumps(_pagina_pncp()).replace("200000.0", "1e999").encode()
    app, _ = _app_com_pagina(tmp_path, raiz_cnpj, pagina)
    with TestClient(app) as cliente:
        caso_id = _novo_caso(cliente)
        assert (
            _coletar(cliente, caso_id, "cnpj_arquivo", {"diretorio": DIR_CNPJ}).status_code == 200
        )
        r = _coletar(cliente, caso_id, "pncp", PERIODO)
        assert r.status_code == 200 and any("valorGlobal" in a for a in r.json()["avisos"])
        for _ in range(2):
            r = cliente.post(f"/casos/{caso_id}/alertas/executar", json={}, headers=cabecalhos())
            assert r.status_code == 200, r.text
        r = cliente.get(f"/casos/{caso_id}/relatorio", headers=cabecalhos("tok-delegado"))
        assert r.status_code == 200
        contrato = next(e for e in r.json()["entidades"] if e["tipo"] == "contrato")
        assert contrato["atributos"]["valor_global"] is None


def test_pagina_do_pncp_com_nan_literal_e_recusada_mas_fica_na_custodia(tmp_path, raiz_cnpj):
    pagina = json.dumps(_pagina_pncp()).replace("200000.0", "NaN").encode()
    app, _ = _app_com_pagina(tmp_path, raiz_cnpj, pagina)
    with TestClient(app) as cliente:
        caso_id = _novo_caso(cliente)
        r = _coletar(cliente, caso_id, "pncp", PERIODO)
        assert r.status_code == 422 and "JSON válido" in r.json()["detail"]
        assert _contar(app, Captura) == 1 and _contar(app, Entidade) == 0
        assert cliente.get(f"/casos/{caso_id}/relatorio", headers=cabecalhos()).status_code == 200


@pytest.mark.parametrize(
    "corpo",
    [
        b"[1, 2]",
        b'"texto"',
        b'{"data": "nao-e-lista"}',
        b'{"data": [1, "x", null], "paginasRestantes": "muitas"}',
        b'{"data": [{"orgaoEntidade": "x", "tipoContrato": 3, "unidadeOrgao": []}]}',
    ],
)
def test_pncp_com_formato_inesperado_nao_gera_erro_500(sessao, custodia, corpo):
    registrar = _registrador(sessao, custodia)
    conector = ConectorPncp(transporte=TransporteSimulado({1: corpo}))
    try:
        resultado = conector.coletar(PERIODO, registrar)
    except ErroConector as erro:
        assert "formato esperado" in str(erro)
    else:
        assert all(r.numero_controle_pncp == "" for r in resultado.registros)


def test_capital_invalido_no_arquivo_gera_aviso_e_nao_alerta(sessao, custodia, tmp_path):
    (tmp_path / "A.EMPRECSV").write_bytes(
        (
            _empresa("90000001", "ALFA SINTETICA", "NaN") + _empresa("90000002", "BETA", "-1,00")
        ).encode()
    )
    resultado = ConectorCnpjArquivo(dir_base=tmp_path).coletar(
        {"diretorio": "."}, _registrador(sessao, custodia)
    )
    assert [r.capital_social for r in resultado.registros] == [None, None]
    assert any("capital social" in a and "2 valor" in a for a in resultado.avisos)


# ==== relatorio/servico.py:para_markdown:unescaped-interpolation ==============================


def test_quebra_de_linha_e_controle_sao_recusados_na_borda(cliente, caso_id):
    forjada = "ok |\n| 99 | 2026-01-01T00:00:00Z | carlos | caso.encerrar | FORJADO |"
    r = cliente.get(f"/casos/{caso_id}", headers=cabecalhos(justificativa=forjada))
    assert r.status_code == 400 and "quebras de linha" in r.json()["detail"]
    for invisivel in ("a\x00b", "​", "a‮b", "...", "\x7f"):
        r = cliente.get(f"/casos/{caso_id}", headers=cabecalhos(justificativa=invisivel))
        assert r.status_code == 400, repr(invisivel)

    for corpo in (
        {"titulo": "T\n\n## Cadeia de custódia\n\nIntegridade de todas as capturas: sim"},
        {"titulo": "ok", "numero_procedimento": "IP 1\n# x"},
        {"titulo": "ok", "descricao": "a\x00b"},
        {"titulo": "x" * 301},
        {"titulo": "ok", "numero_procedimento": "n" * 101},
    ):
        r = cliente.post("/casos", json=corpo_caso(**corpo), headers=cabecalhos())
        assert r.status_code == 422, corpo
    # Descrição com quebra de linha continua válida (não entra no Markdown).
    r = cliente.post(
        "/casos", json=corpo_caso("ok", descricao="linha 1\nlinha 2"), headers=cabecalhos()
    )
    assert r.status_code == 201


def test_markdown_escapa_valores_ja_gravados(cliente, caso_id, app):
    """Valores antigos ou vindos de fontes externas não forjam linhas, títulos nem seções."""
    forjada = "ok |\n| 99 | 2026-01-01T00:00:00Z | carlos | caso.encerrar | FORJADO | `dead` |"
    titulo = "T\n\n## Cadeia de custódia\n\nIntegridade de todas as capturas: sim"
    with app.state.fabrica_sessoes() as s:
        s.get(Caso, caso_id).titulo = titulo
        captura = Captura(
            caso_id=caso_id,
            fonte="pncp",
            url_ou_arquivo="x` | `forjado\n# Título",
            sha256="0" * 64,
            tamanho_bytes=0,
            caminho_relativo="00/" + "0" * 64,
            metadados={},
        )
        s.add(captura)
        s.flush()
        s.add(
            Entidade(
                caso_id=caso_id,
                tipo="empresa",
                chave="1",
                rotulo="EMPRESA | x |\n| pessoa | FORJADA | 1 | |",
                identificador="<b>1</b>",
                atributos={},
                captura_id=captura.id,
            )
        )
        auditoria.registrar(
            s, CHAVE, usuario="ana", acao="caso.ver", caso_id=caso_id, justificativa=forjada
        )
        s.commit()

    r = cliente.get(
        f"/casos/{caso_id}/relatorio", params={"formato": "md"}, headers=cabecalhos("tok-delegado")
    )
    assert r.status_code == 200
    linhas = r.text.split("\n")
    assert not any(linha.startswith("| 99 |") for linha in linhas)
    assert not any(linha.startswith("| pessoa | FORJADA") for linha in linhas)
    assert [linha for linha in linhas if linha.startswith("#")][0].startswith(
        "# Relatório de análise: T \\#\\# Cadeia de custódia"
    )
    assert linhas.count("## Cadeia de custódia") == 1 and "# Título" not in linhas
    assert "\\| 99 \\|" in r.text and "\\<b\\>1\\</b\\>" in r.text
    linha_forjada = next(linha for linha in linhas if "FORJADO" in linha)
    assert linha_forjada.startswith("| ") and "| ana | `caso.ver` |" in linha_forjada
    # Cada linha de tabela tem o número de colunas do cabeçalho (nenhuma barra sem escape a mais).
    custodia_md = next(linha for linha in linhas if "forjado" in linha)
    assert custodia_md.replace("\\|", "").count("|") == 9


def test_para_markdown_trata_evidencias_e_parametros():
    dados = {
        "caso": {
            "id": "c",
            "titulo": "t",
            "numero_procedimento": "",
            "criado_por": "a",
            "criado_em": "x",
        },
        "gerado_por": "a",
        "gerado_em": "x",
        "resumo": {
            "entidades": 0,
            "arestas": 0,
            "alertas": 1,
            "capturas": 0,
            "sugestoes_resolucao": 0,
        },
        "alertas": [
            {
                "tipologia": "T1",
                "titulo": "x\n## y",
                "severidade": "alta",
                "motivo": "- item\n# titulo forjado",
                "entidades": [{"id": "e`1", "rotulo": "[link](http://x.invalid)"}],
                "evidencias": [
                    {
                        "descricao": "d\n- outra",
                        "fonte": "f",
                        "captura_id": "c",
                        "url_ou_arquivo": "u`\n`",
                        "sha256": "s",
                    }
                ],
                "parametros": {"a": "`\n# z"},
            }
        ],
        "entidades": [],
        "sugestoes_resolucao": [],
        "custodia": [],
        "custodia_integra": True,
        "auditoria": {
            "integra": True,
            "entradas": 0,
            "primeira_quebra": None,
            "motivo": None,
            "entradas_do_caso": [],
        },
    }
    linhas = relatorio.para_markdown(dados).split("\n")
    titulos = [linha for linha in linhas if linha.startswith("#")]
    assert len([t for t in titulos if t.startswith("## ")]) == 7  # só as seções do modelo
    assert "\\- item \\# titulo forjado" in linhas
    assert any("\\[link\\](http://x.invalid)" in linha for linha in linhas)
    assert all(linha.count("`") % 2 == 0 for linha in linhas)


# ==== resolucao/servico.py:Resolvedor:pairwise-group-materialization-unbounded ================


def test_grupos_grandes_nao_sao_expandidos_par_a_par(sessao, custodia, tmp_path):
    k = 12
    registrar = _registrador(sessao, custodia)
    (tmp_path / "A.ESTABELE").write_bytes(
        "".join(
            _estabelecimento(f"{91000000 + i}", "DAS AMOSTRAS", "70000000", "9701")
            for i in range(k)
        ).encode()
    )
    (tmp_path / "A.SOCIOCSV").write_bytes(
        "".join(
            _socio(f"{91000000 + i}", "FULANO HOMONIMO SINTETICO", f"***{100000 + i}**")
            for i in range(k)
        ).encode()
    )
    resultado = ConectorCnpjArquivo(dir_base=tmp_path).coletar({"diretorio": "."}, registrar)
    estatisticas = Resolvedor(sessao, registrar.caso_id, max_grupo=5).incorporar(
        resultado.registros
    )
    sessao.commit()

    def contar(modelo, *filtros):
        return sessao.execute(select(func.count()).select_from(modelo).where(*filtros)).scalar_one()

    # Endereço: estrela com k-1 arestas, e não k(k-1)/2 = 66.
    assert contar(Aresta, Aresta.tipo == "mesmo_endereco") == k - 1
    # Homônimos: no máximo max_grupo*(max_grupo-1) = 20 sugestões, e não k(k-1) = 132.
    assert contar(Sugestao) == 20 and estatisticas.sugestoes_novas == 20
    assert any("RESOLUCAO_MAX_GRUPO" in a and "mesmo_endereco" in a for a in estatisticas.avisos)
    assert any("homônimo" in a for a in estatisticas.avisos)
    # A T2 continua enxergando o grupo inteiro (ela trabalha pela chave do endereço).
    ctx = tipologias.motor.carregar_contexto(sessao, registrar.caso_id)
    grupos = {
        chave
        for e in ctx.entidades.values()
        for chave in (e.atributos or {}).get("enderecos_normalizados", [])
    }
    assert len(grupos) == 1

    # Reincorporar não cria nada de novo.
    de_novo = Resolvedor(sessao, registrar.caso_id, max_grupo=5).incorporar(resultado.registros)
    assert de_novo.arestas_novas == 0 and de_novo.sugestoes_novas == 0


def test_grupo_dentro_do_limite_continua_par_a_par(sessao, custodia, tmp_path):
    registrar = _registrador(sessao, custodia)
    (tmp_path / "A.ESTABELE").write_bytes(
        "".join(
            _estabelecimento(f"{91000000 + i}", "DAS AMOSTRAS", "70000000", "9701")
            for i in range(4)
        ).encode()
    )
    resultado = ConectorCnpjArquivo(dir_base=tmp_path).coletar({"diretorio": "."}, registrar)
    estatisticas = Resolvedor(sessao, registrar.caso_id, max_grupo=5).incorporar(
        resultado.registros
    )
    assert estatisticas.arestas_novas == 6 and estatisticas.avisos == []


def test_pessoas_sem_nome_nao_geram_sugestao(sessao, custodia, tmp_path):
    registrar = _registrador(sessao, custodia)
    (tmp_path / "A.SOCIOCSV").write_bytes(
        (_socio("91000001", "", "***111111**") + _socio("91000002", "", "***222222**")).encode()
    )
    resultado = ConectorCnpjArquivo(dir_base=tmp_path).coletar({"diretorio": "."}, registrar)
    assert (
        Resolvedor(sessao, registrar.caso_id).incorporar(resultado.registros).sugestoes_novas == 0
    )


# ==== cnpj_arquivo:coletar:diretorio-unconfined-server-path ===================================


def test_diretorio_fora_da_raiz_tem_a_mesma_resposta_de_inexistente(cliente, caso_id, tmp_path):
    fora = tmp_path / "fora"
    fora.mkdir()
    (fora / "X.EMPRECSV").write_bytes(_empresa("90000099", "FORA DA RAIZ", "1,00").encode())
    mensagens = set()
    for diretorio in ("/nao/existe", "/etc", str(fora), "../fora", "cnpj/../../fora", "nao-existe"):
        r = _coletar(cliente, caso_id, "cnpj_arquivo", {"diretorio": diretorio})
        assert r.status_code == 422, diretorio
        mensagens.add(r.json()["detail"])
    # Uma só mensagem: não serve de oráculo de existência e não devolve o caminho do servidor.
    assert len(mensagens) == 1
    mensagem = mensagens.pop()
    assert "/etc" not in mensagem and str(tmp_path) not in mensagem and "fora" not in mensagem
    detalhe = cliente.get(f"/casos/{caso_id}", headers=cabecalhos()).json()
    # As seis tentativas recusadas ficam no histórico de coletas; nada mais foi criado.
    assert detalhe["contagens"] == {
        "entidades": 0,
        "alertas": 0,
        "capturas": 0,
        "coletas": 6,
        "sugestoes": 0,
        "monitoramentos": 0,
        "avisos_nao_lidos": 0,
        # Horizonte 3: equipe (o criador e os dois membros da fixture), anotações, tarefas e IA.
        "membros": 3,
        "anotacoes": 0,
        "tarefas_abertas": 0,
        "sugestoes_ia_pendentes": 0,
    }


def test_links_simbolicos_nao_sao_seguidos(cliente, caso_id, raiz_cnpj, tmp_path):
    segredo = tmp_path / "segredo_nao_cnpj.txt"
    segredo.write_text('"DUMMY-SECRET-LINE-1";"valor"\n')
    # Arquivo com nome de tabela do CNPJ apontando para fora da raiz.
    (raiz_cnpj / "links").mkdir()
    os.symlink(segredo, raiz_cnpj / "links" / "qualquer.EMPRECSV")
    r = _coletar(cliente, caso_id, "cnpj_arquivo", {"diretorio": "links"})
    assert r.status_code == 422 and "Nenhum arquivo" in r.json()["detail"]
    # Diretório que é link para fora da raiz.
    fora = tmp_path / "fora"
    fora.mkdir()
    (fora / "X.EMPRECSV").write_bytes(_empresa("90000099", "FORA", "1,00").encode())
    os.symlink(fora, raiz_cnpj / "atalho")
    r = _coletar(cliente, caso_id, "cnpj_arquivo", {"diretorio": "atalho"})
    assert r.status_code == 422 and "raiz de ingestão" in r.json()["detail"]
    entidades = cliente.get(f"/casos/{caso_id}/entidades", headers=cabecalhos()).json()
    assert entidades == [] and "DUMMY-SECRET" not in json.dumps(entidades)


def test_cnpj_arquivo_sem_raiz_configurada_fica_desativado(tmp_path):
    with TestClient(criar_app(criar_config(tmp_path, None))) as cliente:
        caso_id = _novo_caso(cliente)
        r = _coletar(cliente, caso_id, "cnpj_arquivo", {"diretorio": "/etc"})
        assert r.status_code == 422 and "CNPJ_DIR_BASE" in r.json()["detail"]
    # Variável vazia desativa a fonte; não vira o diretório corrente.
    assert Configuracao(_env_file=None, cnpj_dir_base="").cnpj_dir_base is None
    with pytest.raises(ErroConfiguracao):
        criar_config(tmp_path, tmp_path / "nao-existe").validar()


def test_captura_do_cnpj_nao_expoe_o_caminho_do_servidor(cliente, caso_carregado, raiz_cnpj):
    r = cliente.get(f"/casos/{caso_carregado}/relatorio", headers=cabecalhos("tok-delegado"))
    assert str(raiz_cnpj) not in r.text and str(raiz_cnpj.parent) not in r.text
    origens = [c["url_ou_arquivo"] for c in r.json()["custodia"] if c["fonte"] == "cnpj_arquivo"]
    assert len(origens) == 3
    assert all(o.startswith("simulado://cnpj_arquivo/CNPJ_DIR_BASE/cnpj/") for o in origens)


def test_arquivo_ilegivel_vira_erro_do_conector(sessao, custodia, tmp_path):
    if os.geteuid() == 0:
        pytest.skip("root lê qualquer arquivo")
    arquivo = tmp_path / "A.EMPRECSV"
    arquivo.write_bytes(_empresa("90000001", "ALFA", "1,00").encode())
    arquivo.chmod(0)
    try:
        # Erro de E/S é tentado de novo (RNF12) antes de virar erro do conector.
        with pytest.raises(ErroConector, match="abrir ou ler o arquivo .* após 3 tentativas"):
            ConectorCnpjArquivo(dir_base=tmp_path, espera_retentativa_segundos=0).coletar(
                {"diretorio": "."}, _registrador(sessao, custodia)
            )
    finally:
        arquivo.chmod(0o600)


# ==== resolucao/normalizar_endereco:empty-address-key-links-unrelated-companies ===============


def test_endereco_em_branco_ou_so_com_cidade_nao_vira_chave():
    assert normalizar_endereco() == ""
    assert normalizar_endereco(municipio="7107", uf="DF") == ""
    assert (
        normalizar_endereco(tipo_logradouro="RUA", numero="10", bairro="CENTRO", cep="70000000")
        == ""
    )
    assert normalizar_endereco(logradouro="DAS AMOSTRAS") == ""  # sem CEP nem município
    assert normalizar_endereco(logradouro="DAS AMOSTRAS", cep="70000000") != ""
    assert normalizar_endereco(logradouro="DAS AMOSTRAS", municipio="9701") != ""


def test_empresas_sem_endereco_nao_sao_ligadas_nem_geram_t2(tmp_path, raiz_cnpj):
    pasta = raiz_cnpj / "sem_endereco"
    pasta.mkdir()
    basicos = ["90000001", "92000002", "92000003", "92000004"]
    (pasta / "A.EMPRECSV").write_bytes(
        "".join(
            _empresa(b, f"EMPRESA DUMMY {i}", "1000000,00") for i, b in enumerate(basicos)
        ).encode()
    )
    (pasta / "A.ESTABELE").write_bytes(
        (
            _estabelecimento(basicos[0])
            + _estabelecimento(basicos[1])
            + _estabelecimento(basicos[2], municipio="7107")
            + _estabelecimento(basicos[3], municipio="7107")
        ).encode()
    )
    app, _ = _app_com_pagina(tmp_path, raiz_cnpj, json.dumps(_pagina_pncp()).encode())
    with TestClient(app) as cliente:
        caso_id = _novo_caso(cliente)
        assert (
            _coletar(cliente, caso_id, "cnpj_arquivo", {"diretorio": "sem_endereco"}).status_code
            == 200
        )
        assert _coletar(cliente, caso_id, "pncp", PERIODO).status_code == 200
        r = cliente.post(
            f"/casos/{caso_id}/alertas/executar",
            json={"parametros": {"t2_minimo_empresas": 2}},
            headers=cabecalhos(),
        )
        assert r.status_code == 200 and [a for a in r.json() if a["tipologia"] == "T2"] == []
        grafo = cliente.get(f"/casos/{caso_id}/grafo", headers=cabecalhos()).json()
        assert [a for a in grafo["arestas"] if a["tipo"] == "mesmo_endereco"] == []


def test_orgaos_anonimos_de_contratos_diferentes_ficam_separados(sessao, custodia):
    registrar = _registrador(sessao, custodia)
    pagina = _pagina_pncp(orgaoEntidade={})
    outro = dict(pagina["data"][0], numeroControlePNCP="90100001000109-2-000100/2026")
    pagina["data"].append(outro)
    conector = ConectorPncp(transporte=TransporteSimulado({1: json.dumps(pagina).encode()}))
    resultado = conector.coletar(PERIODO, registrar)
    Resolvedor(sessao, registrar.caso_id).incorporar(resultado.registros)
    orgaos = sessao.execute(select(Entidade).where(Entidade.tipo == "orgao")).scalars().all()
    assert len(orgaos) == 2 and all("sem-cnpj:contrato:" in o.chave for o in orgaos)


# ==== rotas_casos.py:gerar_relatorio:global-audit-exclusive-lock-held-across-report ===========


def test_relatorio_so_grava_a_auditoria_depois_de_gerado(cliente, caso_id, app, monkeypatch):
    """A entrada relatorio.gerar (que toma a trava da cadeia no PostgreSQL) vem depois da
    verificação da cadeia e da releitura das capturas, e não antes."""
    original = relatorio.gerar
    pendentes = []

    def espiar(sessao, *args, **kwargs):
        pendentes.append([o.acao for o in sessao.new if isinstance(o, Auditoria)])
        dados = original(sessao, *args, **kwargs)
        pendentes.append([o.acao for o in sessao.new if isinstance(o, Auditoria)])
        return dados

    monkeypatch.setattr("buscador.api.rotas_casos.relatorio.gerar", espiar)
    r = cliente.get(f"/casos/{caso_id}/relatorio", headers=cabecalhos())
    assert r.status_code == 200
    assert pendentes == [[], []]
    assert _acoes(app, caso_id)[-1] == "relatorio.gerar"


def test_escritores_do_postgres_usam_trava_consultiva_e_nao_lock_table():
    fonte = (RAIZ / "buscador" / "auditoria" / "servico.py").read_text(encoding="utf-8")
    assert "pg_advisory_xact_lock(" in fonte and "LOCK TABLE auditoria" not in fonte


# ==== conectores/pncp.py:ConectorPncp.coletar:max_paginas-unbounded ===========================


def _pncp(transporte, **kw) -> ConectorPncp:
    return ConectorPncp(transporte=transporte, dormir=lambda _s: None, **kw)


@pytest.mark.parametrize(
    ("parametros", "trecho"),
    [
        ({**PERIODO, "max_paginas": 21}, "PNCP_MAX_PAGINAS"),
        ({**PERIODO, "max_paginas": 10**9}, "max_paginas"),
        ({"dataInicial": "20200101", "dataFinal": "20261231"}, "PNCP_MAX_DIAS_PERIODO"),
        ({"dataInicial": "20261231", "dataFinal": "20260101"}, "anterior"),
        ({"dataInicial": "20261341", "dataFinal": "20261231"}, "dataInicial"),
        ({**PERIODO, "tamanhoPagina": 5000}, "tamanhoPagina"),
        ({**PERIODO, "cnpjFornecedor": ["1"] * 51}, "cnpjFornecedor"),
    ],
)
def test_pncp_recusa_pedidos_acima_dos_limites_sem_consultar(sessao, custodia, parametros, trecho):
    transporte = TransporteSimulado.de_diretorio(FIXTURES_PNCP)
    with pytest.raises(ErroConector, match=trecho):
        _pncp(transporte).coletar(parametros, _registrador(sessao, custodia))
    assert transporte.chamadas == []


def test_pncp_teto_de_paginas_vem_da_configuracao(tmp_path, raiz_cnpj):
    app, transporte = _app_com_pagina(
        tmp_path, raiz_cnpj, json.dumps(_pagina_pncp()).encode(), pncp_max_paginas=2
    )
    with TestClient(app) as cliente:
        caso_id = _novo_caso(cliente)
        r = _coletar(cliente, caso_id, "pncp", {**PERIODO, "max_paginas": 3})
        assert r.status_code == 422 and "teto de 2" in r.json()["detail"]
        assert transporte.chamadas == []
        assert _coletar(cliente, caso_id, "pncp", {**PERIODO, "max_paginas": 2}).status_code == 200


def test_pncp_limita_bytes_por_pagina(sessao, custodia):
    grande = json.dumps({"data": [], "enchimento": "x" * 5_000}).encode()
    transporte = TransporteSimulado({1: grande})
    with pytest.raises(ErroConector, match="PNCP_MAX_BYTES_PAGINA"):
        _pncp(transporte, max_bytes_pagina=2_048).coletar(PERIODO, _registrador(sessao, custodia))

    class SemTamanhoDeclarado(httpx.BaseTransport):
        def handle_request(self, request):
            return httpx.Response(200, stream=httpx.ByteStream(grande), request=request)

    with pytest.raises(ErroConector, match="PNCP_MAX_BYTES_PAGINA"):
        _pncp(SemTamanhoDeclarado(), max_bytes_pagina=2_048).coletar(
            PERIODO, _registrador(sessao, custodia)
        )
    assert sessao.execute(select(func.count()).select_from(Captura)).scalar_one() == 0


def test_pncp_para_no_prazo_total(sessao, custodia):
    pagina = json.dumps({"data": [], "paginasRestantes": 5}).encode()
    transporte = TransporteSimulado({n: pagina for n in range(1, 11)})
    instantes = iter([0.0, 50.0, 500.0, 900.0])
    conector = _pncp(transporte, prazo_total_segundos=100, relogio=lambda: next(instantes))
    resultado = conector.coletar({**PERIODO, "max_paginas": 10}, _registrador(sessao, custodia))
    assert len(transporte.chamadas) == 2
    assert any("PNCP_PRAZO_TOTAL_SEGUNDOS" in a for a in resultado.avisos)


# ==== cnpj_arquivo:coletar:read_bytes-unbounded-file-into-memory ==============================


def test_cnpj_arquivo_recusa_arquivo_acima_do_limite_de_bytes(sessao, custodia, tmp_path):
    linha = _empresa("90000001", "ALFA SINTETICA", "1,00").encode()
    (tmp_path / "A.EMPRECSV").write_bytes(linha * 100)
    registrar = _registrador(sessao, custodia)
    conector = ConectorCnpjArquivo(dir_base=tmp_path, max_bytes_arquivo=len(linha) * 10)
    with pytest.raises(ErroConector, match="CNPJ_MAX_BYTES_ARQUIVO"):
        conector.coletar({"diretorio": "."}, registrar)
    assert sessao.execute(select(func.count()).select_from(Captura)).scalar_one() == 0
    assert not list(custodia.raiz.glob("*/*")) and not list(custodia.raiz.glob(".parcial-*"))


def test_cnpj_arquivo_limita_o_total_de_registros(sessao, custodia, tmp_path):
    (tmp_path / "A.EMPRECSV").write_bytes(
        "".join(_empresa(f"{90000000 + i}", f"EMPRESA {i}", "1,00") for i in range(30)).encode()
    )
    conector = ConectorCnpjArquivo(dir_base=tmp_path, max_registros=10)
    with pytest.raises(ErroConector, match="CNPJ_MAX_REGISTROS"):
        conector.coletar({"diretorio": "."}, _registrador(sessao, custodia))
    resultado = ConectorCnpjArquivo(dir_base=tmp_path, max_registros=30).coletar(
        {"diretorio": "."}, _registrador(sessao, custodia)
    )
    assert len(resultado.registros) == 30


def test_cnpj_arquivo_le_em_blocos_e_guarda_os_bytes_exatos(
    sessao, custodia, tmp_path, monkeypatch
):
    monkeypatch.setattr("buscador.custodia.servico._BLOCO", 64)
    conteudo = "".join(
        _empresa(f"{90000000 + i}", f"AÇÃO SINTÉTICA {i}", "1.234,56") for i in range(50)
    ).encode("latin-1")
    (tmp_path / "A.EMPRECSV").write_bytes(conteudo)
    lidos = []
    original = Path.read_bytes
    monkeypatch.setattr(Path, "read_bytes", lambda self: lidos.append(self) or original(self))
    resultado = ConectorCnpjArquivo(dir_base=tmp_path).coletar(
        {"diretorio": "."}, _registrador(sessao, custodia)
    )
    assert lidos == []  # o conector não lê mais o arquivo inteiro de uma vez
    monkeypatch.undo()
    captura = resultado.capturas[0]
    assert custodia.ler(captura) == conteudo and captura.tamanho_bytes == len(conteudo)
    assert custodia.verificar(captura).integra is True
    assert len(resultado.registros) == 50
    assert resultado.registros[7].razao_social == "AÇÃO SINTÉTICA 7"
    assert resultado.registros[7].capital_social == 1234.56


def test_campo_gigante_vira_erro_do_conector(sessao, custodia, tmp_path):
    (tmp_path / "A.EMPRECSV").write_bytes(b'"90000001";"' + b"A" * 200_000 + b'";"1"\r\n')
    with pytest.raises(ErroConector, match="fora do layout"):
        ConectorCnpjArquivo(dir_base=tmp_path).coletar(
            {"diretorio": "."}, _registrador(sessao, custodia)
        )


def test_custodia_aceita_fluxo_com_limite_e_nome_temporario_unico(sessao, custodia, tmp_path):
    caso = Caso(titulo="c", criado_por="t")
    sessao.add(caso)
    sessao.flush()
    origem = tmp_path / "origem.bin"
    origem.write_bytes(b"x" * 10_000)
    with open(origem, "rb") as fluxo, pytest.raises(ConteudoGrandeDemais):
        custodia.registrar(
            sessao, caso_id=caso.id, fonte="f", url_ou_arquivo="u", conteudo=fluxo, max_bytes=100
        )
    assert not list(custodia.raiz.glob(".parcial-*"))
    with open(origem, "rb") as fluxo:
        captura = custodia.registrar(
            sessao, caso_id=caso.id, fonte="f", url_ou_arquivo="u", conteudo=fluxo
        )
    assert captura.tamanho_bytes == 10_000 and custodia.verificar(captura).integra is True

    # Cópia em disco alterada: uma nova coleta do mesmo conteúdo restaura o arquivo e preserva
    # a cópia divergente para perícia.
    caminho = custodia.raiz / captura.caminho_relativo
    caminho.write_bytes(b"adulterado")
    assert custodia.verificar(captura).integra is False
    custodia.registrar(
        sessao, caso_id=caso.id, fonte="f", url_ou_arquivo="u", conteudo=b"x" * 10_000
    )
    assert custodia.verificar(captura).integra is True
    assert len(list(caminho.parent.glob("*.divergente-*"))) == 1


# ==== Custódia: capturas sintéticas ficam marcadas (escopo 4 da tarefa) =======================


def test_capturas_de_demonstracao_ficam_marcadas_como_sinteticas(cliente, caso_carregado, app):
    with app.state.fabrica_sessoes() as s:
        capturas = (
            s.execute(select(Captura).where(Captura.caso_id == caso_carregado)).scalars().all()
        )
    assert len(capturas) == 5
    for captura in capturas:
        assert captura.metadados["simulado"] is True
        assert captura.url_ou_arquivo.startswith("simulado://")
        assert "origem_simulada" in captura.metadados
        assert "https://" not in captura.url_ou_arquivo

    cliente.post(f"/casos/{caso_carregado}/alertas/executar", headers=cabecalhos())
    r = cliente.get(f"/casos/{caso_carregado}/relatorio", headers=cabecalhos())
    dados = r.json()
    assert dados["contem_capturas_simuladas"] is True
    assert all(c["simulado"] for c in dados["custodia"])
    evidencias = [ev for al in dados["alertas"] for ev in al["evidencias"]]
    assert evidencias and all(ev["url_ou_arquivo"].startswith("simulado://") for ev in evidencias)
    contrato = next(e for e in dados["entidades"] if e["tipo"] == "contrato")
    assert contrato["atributos"]["url_fonte"].startswith("simulado://")
    assert "https://pncp.gov.br" not in r.text

    md = cliente.get(
        f"/casos/{caso_carregado}/relatorio", params={"formato": "md"}, headers=cabecalhos()
    ).text
    assert "capturas SINTÉTICAS" in md and "https://pncp.gov.br" not in md


def test_marcacao_de_sintetico_e_inequivoca_e_so_vale_para_o_que_e_simulado(
    sessao, custodia, tmp_path
):
    # Qualquer transporte injetado conta como simulado; o conector de rede real, não.
    assert ConectorPncp().simulado is False
    assert ConectorPncp(transporte=TransporteSimulado({})).simulado is True
    assert ConectorPncp(transporte=httpx.MockTransport(lambda r: httpx.Response(204))).simulado

    registrar = _registrador(sessao, custodia)
    # A custódia impõe as duas marcas juntas, venha a indicação pelos metadados ou pela origem.
    a = registrar(
        "pncp", "https://pncp.gov.br/api/consulta/v1/contratos?x=1", b"a", {"simulado": True}
    )
    b = registrar("pncp", "simulado://qualquer", b"b", {})
    c = registrar("pncp", "https://pncp.gov.br/api/consulta/v1/contratos?x=1", b"c", {})
    assert a.url_ou_arquivo == "simulado://pncp.gov.br/api/consulta/v1/contratos?x=1"
    assert a.metadados["simulado"] is True and b.metadados["simulado"] is True
    assert c.metadados["simulado"] is False and c.url_ou_arquivo.startswith("https://")

    # CNPJ: sem o marcador SINTETICO a captura não é marcada; com ele (ou em modo simulado), é.
    (tmp_path / "A.EMPRECSV").write_bytes(_empresa("90000001", "ALFA", "1,00").encode())
    real = ConectorCnpjArquivo(dir_base=tmp_path).coletar({"diretorio": "."}, registrar)
    assert real.capturas[0].metadados["simulado"] is False
    assert real.capturas[0].url_ou_arquivo == "CNPJ_DIR_BASE/A.EMPRECSV"
    forcado = ConectorCnpjArquivo(dir_base=tmp_path, simulado=True).coletar(
        {"diretorio": "."}, registrar
    )
    assert forcado.capturas[0].url_ou_arquivo == "simulado://cnpj_arquivo/CNPJ_DIR_BASE/A.EMPRECSV"
    (tmp_path / "sintetico.TXT").write_text("marcador")
    marcado = ConectorCnpjArquivo(dir_base=tmp_path).coletar({"diretorio": "."}, registrar)
    assert marcado.capturas[0].metadados["simulado"] is True
    assert {type(r) for r in marcado.registros} == {RegistroEmpresa}
    assert RegistroEstabelecimento and RegistroSocio  # tipos usados pelos auxiliares acima


# ==== Hardening do REPORT.md ==================================================================


def test_cors_sem_credenciais_e_com_cabecalhos_restritos(cliente):
    r = cliente.options(
        "/casos",
        headers={
            "Origin": "http://localhost:5190",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,x-justificativa,content-type",
        },
    )
    assert r.status_code == 200
    assert "access-control-allow-credentials" not in r.headers
    permitidos = r.headers["access-control-allow-headers"].lower()
    assert "authorization" in permitidos and "x-justificativa" in permitidos
    r = cliente.options(
        "/casos",
        headers={
            "Origin": "http://localhost:5190",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "x-outro",
        },
    )
    assert r.status_code == 400


def test_entradas_de_consulta_tem_limites(cliente, caso_id):
    h = cabecalhos()
    assert (
        cliente.get(f"/casos/{caso_id}/entidades", params={"q": "a" * 201}, headers=h).status_code
        == 422
    )
    assert (
        cliente.get(f"/casos/{caso_id}/entidades", params={"q": "a\x00b"}, headers=h).status_code
        == 422
    )
    assert (
        cliente.get(f"/casos/{caso_id}/entidades", params={"tipo": "outro"}, headers=h).status_code
        == 422
    )
    assert (
        cliente.get(f"/casos/{caso_id}/grafo", params={"centro": "c" * 65}, headers=h).status_code
        == 422
    )
    assert cliente.get("/casos/" + "c" * 65, headers=h).status_code == 404
    r = cliente.post(
        f"/casos/{caso_id}/alertas/executar",
        json={"tipologias": ["T1"] * 31},
        headers=h,
    )
    assert r.status_code == 422


def test_busca_trata_curingas_do_like_como_texto(cliente, caso_carregado):
    h = cabecalhos()
    todas = cliente.get(f"/casos/{caso_carregado}/entidades", headers=h).json()
    assert len(todas) == 47
    for termo in ("%", "_", "A_FA"):
        r = cliente.get(f"/casos/{caso_carregado}/entidades", params={"q": termo}, headers=h)
        assert r.status_code == 200 and r.json() == [], termo
    r = cliente.get(f"/casos/{caso_carregado}/entidades", params={"q": "alfa"}, headers=h)
    assert any("ALFA" in e["rotulo"] for e in r.json())


def test_execucao_parcial_de_tipologias_nao_apaga_as_outras(cliente, caso_carregado):
    h = cabecalhos()
    # Sete alertas das T, dois da L5 (sócio estrangeiro) e dois da L6 (porte), nas fixtures.
    assert len(cliente.post(f"/casos/{caso_carregado}/alertas/executar", headers=h).json()) == 11
    r = cliente.post(
        f"/casos/{caso_carregado}/alertas/executar", json={"tipologias": ["T2"]}, headers=h
    )
    assert [a["tipologia"] for a in r.json()] == ["T2"]
    assert len(cliente.get(f"/casos/{caso_carregado}/alertas", headers=h).json()) == 11
    assert _contar_alertas(cliente, caso_carregado) == 11


def _contar_alertas(cliente, caso_id: str) -> int:
    return cliente.get(f"/casos/{caso_id}", headers=cabecalhos()).json()["contagens"]["alertas"]


def test_t1_cita_o_cnpj_do_estabelecimento_que_forneceu_a_data(sessao, custodia, tmp_path):
    registrar = _registrador(sessao, custodia)
    (tmp_path / "A.EMPRECSV").write_bytes(_empresa("90000001", "ALFA SINTETICA", "1,00").encode())
    (tmp_path / "A.ESTABELE").write_bytes(
        _estabelecimento("90000001", "DAS AMOSTRAS", "70000000", "9701")
        .replace('"20200101"', '"20260201"')
        .encode()
    )
    cnpj = ConectorCnpjArquivo(dir_base=tmp_path).coletar({"diretorio": "."}, registrar)
    # O contrato vem com o CNPJ de uma filial (ordem 0002), que sobrescreve o atributo "cnpj".
    pagina = _pagina_pncp(niFornecedor="90000001000200")
    pncp = ConectorPncp(transporte=TransporteSimulado({1: json.dumps(pagina).encode()})).coletar(
        PERIODO, registrar
    )
    Resolvedor(sessao, registrar.caso_id).incorporar(cnpj.registros + pncp.registros)
    sessao.commit()
    padrao = {"t1_dias_minimos": 180, "t2_minimo_empresas": 3, "t3_razao_minima": 10.0}
    alerta = tipologias.executar(sessao, registrar.caso_id, padrao, ["T1"])[0]
    assert "Estabelecimento 90000001000100" in alerta.evidencias[0]["descricao"]
    assert isinstance(alerta, Alerta)


# ==== PostgreSQL (rodam só com TEST_POSTGRES_URL e TEST_POSTGRES_URL_APP) =====================
# compose-postgres-published-fixed-credential, unkeyed-unanchored-hash-chain (TRUNCATE) e
# gerar_relatorio:global-audit-exclusive-lock-held-across-report.

URL_DONO = os.environ.get("TEST_POSTGRES_URL", "")
URL_APP = os.environ.get("TEST_POSTGRES_URL_APP", "")
PAPEL_APP = os.environ.get("TEST_POSTGRES_PAPEL_APP", "osint_app")
exige_postgres = pytest.mark.skipif(
    not (URL_DONO and URL_APP), reason="defina TEST_POSTGRES_URL e TEST_POSTGRES_URL_APP"
)


@pytest.fixture
def postgres(tmp_path):
    """Banco descartável: recria o esquema como dono e devolve (engine_dono, engine_app)."""
    from buscador.db import conceder_privilegios
    from buscador.modelos import Base

    dono = criar_engine(URL_DONO)
    Base.metadata.drop_all(dono)
    criar_esquema(dono)
    conceder_privilegios(dono, PAPEL_APP)
    app = criar_engine(URL_APP)
    yield dono, app
    app.dispose()
    Base.metadata.drop_all(dono)
    dono.dispose()


@exige_postgres
def test_postgres_papel_da_aplicacao_nao_altera_nem_trunca_a_auditoria(postgres, tmp_path):
    from sqlalchemy.exc import DBAPIError

    from buscador.db import criar_fabrica_sessoes

    dono, app = postgres
    with criar_fabrica_sessoes(app)() as sessao:
        _registrar(sessao, 3)
        assert auditoria.verificar(sessao, CHAVE).integra is True

    proibidos = [
        "UPDATE auditoria SET usuario = 'x'",
        "DELETE FROM auditoria",
        "TRUNCATE auditoria",
        "DROP TRIGGER auditoria_sem_update ON auditoria",
        "ALTER TABLE auditoria DISABLE TRIGGER ALL",
        "DROP TABLE auditoria",
        "LOCK TABLE auditoria IN EXCLUSIVE MODE",
        "UPDATE capturas SET sha256 = 'x'",
        "DELETE FROM capturas",
        "CREATE TABLE public.intrusa (x int)",
        "CREATE OR REPLACE FUNCTION auditoria_bloquear() RETURNS trigger AS $$ "
        "BEGIN RETURN NEW; END; $$ LANGUAGE plpgsql",
    ]
    for sql in proibidos:
        with pytest.raises(DBAPIError), app.begin() as conexao:
            conexao.execute(text(sql))

    # Até o dono é barrado pelos gatilhos, inclusive no TRUNCATE (gatilho de comando).
    for sql in ("TRUNCATE auditoria", "DELETE FROM auditoria", "UPDATE auditoria SET acao = 'x'"):
        with pytest.raises(DBAPIError, match="somente de acrescimo"), dono.begin() as conexao:
            conexao.execute(text(sql))
    with dono.connect() as conexao:
        dono_da_tabela = conexao.execute(
            text("SELECT tableowner FROM pg_tables WHERE tablename = 'auditoria'")
        ).scalar_one()
        assert dono_da_tabela != PAPEL_APP
        assert conexao.execute(text("SELECT count(*) FROM auditoria")).scalar_one() == 3


@exige_postgres
def test_postgres_fluxo_completo_com_o_papel_da_aplicacao(postgres, tmp_path, raiz_cnpj):
    config = criar_config(tmp_path, raiz_cnpj, database_url=URL_APP, db_criar_esquema=False)
    transporte = TransporteSimulado.de_diretorio(FIXTURES_PNCP)
    app = criar_app(config, extras_conectores={"pncp": {"transporte": transporte}})
    with TestClient(app) as cliente:
        caso_id = _novo_caso(cliente)
        assert (
            _coletar(cliente, caso_id, "cnpj_arquivo", {"diretorio": DIR_CNPJ}).status_code == 200
        )
        assert _coletar(cliente, caso_id, "pncp", PERIODO).status_code == 200
        r = cliente.post(f"/casos/{caso_id}/alertas/executar", json={}, headers=cabecalhos())
        assert r.status_code == 200 and len(r.json()) == 11
        r = cliente.get(f"/casos/{caso_id}/relatorio", headers=cabecalhos("tok-delegado"))
        assert r.status_code == 200 and r.json()["auditoria"]["integra"] is True
        v = cliente.get("/auditoria/verificar", headers=cabecalhos()).json()
        assert v["integra"] is True and v["ancora_seq"] == v["entradas"]
    app.state.engine.dispose()


@exige_postgres
def test_postgres_relatorio_nao_segura_a_trava_da_auditoria(postgres, tmp_path, monkeypatch):
    """Enquanto o relatório é gerado, outra sessão consegue gravar na auditoria na hora."""
    from buscador.db import criar_fabrica_sessoes

    _, engine_app = postgres
    config = criar_config(tmp_path, None, database_url=URL_APP, db_criar_esquema=False)
    app = criar_app(config)
    original = relatorio.gerar
    gravou = []

    def gerar_e_gravar_em_paralelo(sessao, *args, **kwargs):
        dados = original(sessao, *args, **kwargs)
        with criar_fabrica_sessoes(engine_app)() as outra:
            outra.execute(text("SET lock_timeout = '2s'"))
            auditoria.registrar(
                outra, CHAVE, usuario="outra", acao="paralela", caso_id=None, justificativa="j"
            )
            outra.commit()
            gravou.append(True)
        return dados

    monkeypatch.setattr("buscador.api.rotas_casos.relatorio.gerar", gerar_e_gravar_em_paralelo)
    with TestClient(app) as cliente:
        caso_id = _novo_caso(cliente)
        r = cliente.get(f"/casos/{caso_id}/relatorio", headers=cabecalhos())
        assert r.status_code == 200 and gravou == [True]
        assert cliente.get("/auditoria/verificar", headers=cabecalhos()).json()["integra"] is True
    app.state.engine.dispose()
