from fastapi.testclient import TestClient

from buscador.api import criar_app
from tests.conftest import (
    DIR_CNPJ,
    cabecalhos,
    corpo_caso,
    criar_config,
    entidade_por_identificador,
)

# ---- Base, autenticação e justificativa -----------------------------------------------------


def test_health_sem_autenticacao(cliente):
    r = cliente.get("/health")
    assert r.status_code == 200 and r.json() == {"status": "ok", "modo_dev": True}


def test_health_diz_que_nao_ha_token_de_desenvolvimento_fora_do_modo_dev(tmp_path):
    # Piloto de 18/11/2026 (MODO_DEV=0): a tela de entrada usa este campo para não oferecer o
    # acesso por token, que o servidor recusa.
    producao = criar_app(criar_config(tmp_path, modo_dev=False, tokens_dev=""))
    with TestClient(producao) as fora_do_modo_dev:
        r = fora_do_modo_dev.get("/health")
        assert r.status_code == 200 and r.json() == {"status": "ok", "modo_dev": False}
        # Nenhum token fixo autentica: nem os dos testes, nem os publicados, nem vazio.
        for token in ("tok-admin", "tok-analista", "dev-admin", ""):
            r = fora_do_modo_dev.get("/me", headers={"Authorization": f"Bearer {token}"})
            assert r.status_code == 401, token
            r = fora_do_modo_dev.get("/casos", headers=cabecalhos(token))
            assert r.status_code == 401, token


def test_docs_openapi_so_no_modo_dev(cliente, tmp_path):
    # Fora do modo de desenvolvimento, a documentação sem autenticação não existe.
    # (O ``cliente`` dos testes roda no modo de desenvolvimento, por causa dos tokens fixos.)
    producao = criar_app(criar_config(tmp_path / "producao", modo_dev=False, tokens_dev=""))
    with TestClient(producao) as fora_do_modo_dev:
        for rota in ("/docs", "/redoc", "/openapi.json"):
            assert fora_do_modo_dev.get(rota).status_code == 404
    app_dev = criar_app(criar_config(tmp_path / "dev", modo_dev=True))
    with TestClient(app_dev) as dev:
        assert dev.get("/docs").status_code == 200
        assert "/casos/{caso_id}/relatorio" in dev.get("/openapi.json").json()["paths"]


def test_me_devolve_usuario_e_perfil(cliente):
    assert cliente.get("/me", headers=cabecalhos("tok-delegado")).json() == {
        "usuario": "carlos",
        "perfil": "delegado",
    }
    assert cliente.get("/me").status_code == 401
    assert cliente.get("/me", headers={"Authorization": "Bearer errado"}).status_code == 401


def test_rotas_de_caso_exigem_token_e_justificativa(cliente, caso_id):
    assert cliente.get("/casos").status_code == 401
    r = cliente.get("/casos", headers=cabecalhos(justificativa=None))
    assert r.status_code == 400
    assert "X-Justificativa" in r.json()["detail"]
    assert cliente.get("/casos", headers=cabecalhos(justificativa="   ")).status_code == 400
    assert cliente.get("/capturas/x", headers=cabecalhos(justificativa=None)).status_code == 400
    assert (
        cliente.get("/auditoria/verificar", headers=cabecalhos(justificativa=None)).status_code
        == 400
    )
    assert (
        cliente.post(
            "/casos", json={"titulo": "t"}, headers=cabecalhos(justificativa=None)
        ).status_code
        == 400
    )


def test_justificativa_aceita_percent_encoding_e_e_registrada(cliente, caso_id):
    r = cliente.get(
        f"/casos/{caso_id}", headers=cabecalhos("tok-admin", "Verificação de vínculo societário")
    )
    assert r.status_code == 200
    entradas = cliente.get(
        "/auditoria", params={"caso_id": caso_id}, headers=cabecalhos("tok-admin")
    ).json()
    assert entradas[-1]["justificativa"] == "Verificação de vínculo societário"
    assert entradas[-1]["usuario"] == "root" and entradas[-1]["acao"] == "caso.ver"


def test_cors_permite_front_end(cliente):
    r = cliente.options(
        "/casos",
        headers={
            "Origin": "http://localhost:5190",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization,x-justificativa",
        },
    )
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == "http://localhost:5190"
    r = cliente.options(
        "/casos", headers={"Origin": "http://outro.invalid", "Access-Control-Request-Method": "GET"}
    )
    assert "access-control-allow-origin" not in r.headers


# ---- Casos ------------------------------------------------------------------------------------


def test_criar_listar_e_ver_caso(cliente):
    r = cliente.post(
        "/casos",
        json=corpo_caso("Inquérito X", descricao="desc", numero_procedimento="IP 1/2026"),
        headers=cabecalhos("tok-delegado"),
    )
    assert r.status_code == 201
    caso = r.json()
    assert set(caso) == {
        "meu_papel",
        "id",
        "titulo",
        "descricao",
        "numero_procedimento",
        "criado_por",
        "criado_em",
        "finalidade",
        "retencao_ate",
        "somente_leitura",
    }
    assert caso["criado_por"] == "carlos" and caso["criado_em"].endswith("Z")

    # H23: só membro vê o caso. A analista não o enxerga na lista nem o abre.
    assert cliente.get("/casos", headers=cabecalhos()).json() == []
    assert cliente.get(f"/casos/{caso['id']}", headers=cabecalhos()).status_code == 403
    h = cabecalhos("tok-delegado")
    lista = cliente.get("/casos", headers=h).json()
    assert [c["id"] for c in lista] == [caso["id"]] and lista[0]["meu_papel"] == "responsavel"

    detalhe = cliente.get(f"/casos/{caso['id']}", headers=h).json()
    assert detalhe["contagens"] == {
        "entidades": 0,
        "alertas": 0,
        "capturas": 0,
        "coletas": 0,
        "sugestoes": 0,
        "monitoramentos": 0,
        "avisos_nao_lidos": 0,
        "membros": 1,
        "anotacoes": 0,
        "tarefas_abertas": 0,
        "sugestoes_ia_pendentes": 0,
    }
    assert cliente.get("/casos/nao-existe", headers=cabecalhos()).status_code == 404
    assert cliente.post("/casos", json=corpo_caso(""), headers=cabecalhos()).status_code == 422


# ---- Coletas ----------------------------------------------------------------------------------


def test_coleta_cnpj_e_pncp(cliente, caso_id):
    r = cliente.post(
        f"/casos/{caso_id}/coletas",
        json={"fonte": "cnpj_arquivo", "parametros": {"diretorio": DIR_CNPJ}},
        headers=cabecalhos(),
    )
    assert r.status_code == 200, r.text
    corpo = r.json()
    assert len(corpo["captura_ids"]) == 3
    assert corpo["entidades_novas"] == 35 and corpo["sugestoes_novas"] == 4
    r = cliente.post(
        f"/casos/{caso_id}/coletas",
        json={
            "fonte": "pncp",
            "parametros": {"dataInicial": "20260101", "dataFinal": "20261231", "max_paginas": 5},
        },
        headers=cabecalhos(),
    )
    assert r.status_code == 200, r.text
    assert len(r.json()["captura_ids"]) == 2 and r.json()["entidades_novas"] == 12
    detalhe = cliente.get(f"/casos/{caso_id}", headers=cabecalhos()).json()
    assert detalhe["contagens"]["capturas"] == 5 and detalhe["contagens"]["entidades"] == 47


def test_coleta_do_pncp_truncada_avisa_na_resposta_no_historico_e_na_auditoria(cliente, caso_id):
    periodo = {"dataInicial": "20260101", "dataFinal": "20261231"}

    def coletar(max_paginas: int) -> dict:
        r = cliente.post(
            f"/casos/{caso_id}/coletas",
            json={"fonte": "pncp", "parametros": {**periodo, "max_paginas": max_paginas}},
            headers=cabecalhos(),
        )
        assert r.status_code == 200, r.text
        return r.json()

    # A fonte sintética tem 9 contratos em 2 páginas: com 1 página, a coleta fica parcial.
    parcial = coletar(1)
    assert parcial["incompleta"] is True and parcial["paginas_restantes"] == 1
    assert parcial["registros"] == 4 and parcial["situacao"] == "sucesso"
    assert parcial["avisos"][0].startswith("Coleta incompleta:")
    assert "9 contrato(s) em 2 página(s)" in parcial["avisos"][0]
    completa = coletar(2)
    assert completa["incompleta"] is False and completa["paginas_restantes"] is None
    assert not any("incompleta" in a for a in completa["avisos"])

    historico = cliente.get(f"/casos/{caso_id}/coletas", headers=cabecalhos()).json()
    por_id = {c["id"]: c for c in historico}
    assert por_id[parcial["coleta_id"]]["incompleta"] is True
    assert por_id[completa["coleta_id"]]["incompleta"] is False

    trilha = cliente.get(f"/auditoria?caso_id={caso_id}", headers=cabecalhos("tok-admin")).json()
    execucoes = {
        e["detalhes"]["coleta_id"]: e["detalhes"] for e in trilha if e["acao"] == "coleta.executar"
    }
    assert execucoes[parcial["coleta_id"]]["incompleta"] is True
    assert execucoes[parcial["coleta_id"]]["paginas_restantes"] == 1
    assert "incompleta" not in execucoes[completa["coleta_id"]]


def test_coleta_com_erro_devolve_422_e_fica_na_auditoria(cliente, caso_id):
    r = cliente.post(
        f"/casos/{caso_id}/coletas",
        json={"fonte": "cnpj_arquivo", "parametros": {"diretorio": "/nao/existe"}},
        headers=cabecalhos(),
    )
    assert r.status_code == 422 and "não encontrado" in r.json()["detail"]
    r = cliente.post(
        f"/casos/{caso_id}/coletas",
        json={"fonte": "portal_transparencia", "parametros": {}},
        headers=cabecalhos(),
    )
    assert r.status_code == 422 and "recurso" in r.json()["detail"]
    # Sem chave de API a fonte fica "não configurada": não é falha e nada é consultado (H19).
    r = cliente.post(
        f"/casos/{caso_id}/coletas",
        json={
            "fonte": "portal_transparencia",
            "parametros": {"recurso": "ceis", "codigoSancionado": ["90000001000129"]},
        },
        headers=cabecalhos(),
    )
    assert r.status_code == 200 and r.json()["situacao"] == "nao_configurado"
    assert "PORTAL_TRANSPARENCIA_CHAVE" in r.json()["motivo"] and r.json()["captura_ids"] == []
    r = cliente.post(f"/casos/{caso_id}/coletas", json={"fonte": "outra"}, headers=cabecalhos())
    assert r.status_code == 422
    entradas = cliente.get(
        "/auditoria", params={"caso_id": caso_id}, headers=cabecalhos("tok-admin")
    ).json()
    assert [e["acao"] for e in entradas if e["acao"] == "coleta.falhou"]


# ---- Entidades --------------------------------------------------------------------------------


def test_buscar_entidades_por_nome_cnpj_e_cpf_parcial(cliente, caso_carregado):
    h = cabecalhos()
    r = cliente.get(f"/casos/{caso_carregado}/entidades", params={"q": "alfa constru"}, headers=h)
    assert r.status_code == 200
    assert any(
        e["rotulo"] == "ALFA CONSTRUCOES SINTETICA LTDA" and e["tipo"] == "empresa"
        for e in r.json()
    )
    r = cliente.get(
        f"/casos/{caso_carregado}/entidades", params={"q": "90.000.003/0001"}, headers=h
    )
    assert [e["identificador"] for e in r.json() if e["tipo"] == "empresa"] == ["90000003"]
    r = cliente.get(f"/casos/{caso_carregado}/entidades", params={"q": "***666666**"}, headers=h)
    assert [e["rotulo"] for e in r.json()] == ["RICARDO FICTICIO LIMA"]
    r = cliente.get(f"/casos/{caso_carregado}/entidades", params={"q": "666666"}, headers=h)
    assert [e["rotulo"] for e in r.json()] == ["RICARDO FICTICIO LIMA"]
    todos = cliente.get(f"/casos/{caso_carregado}/entidades", headers=h).json()
    assert len(todos) == 47
    assert set(todos[0]) == {"id", "tipo", "rotulo", "identificador", "atributos"}


def test_ver_entidade_com_fontes_e_sugestoes(cliente, caso_carregado):
    carlos = entidade_por_identificador(cliente, caso_carregado, "***222222**", "pessoa")
    r = cliente.get(f"/casos/{caso_carregado}/entidades/{carlos['id']}", headers=cabecalhos())
    assert r.status_code == 200
    d = r.json()
    assert d["fontes"][0]["fonte"] == "cnpj_arquivo" and len(d["fontes"][0]["sha256"]) == 64
    assert len(d["sugestoes"]) == 1
    assert d["sugestoes"][0]["candidata_identificador"] == "***232323**"
    assert d["sugestoes"][0]["score"] == 0.5
    assert (
        cliente.get(f"/casos/{caso_carregado}/entidades/x", headers=cabecalhos()).status_code == 404
    )


# ---- Grafo e caminho --------------------------------------------------------------------------


def test_grafo_e_caminho(cliente, caso_carregado):
    h = cabecalhos()
    ricardo = entidade_por_identificador(cliente, caso_carregado, "***666666**", "pessoa")
    r = cliente.get(
        f"/casos/{caso_carregado}/grafo",
        params={"centro": ricardo["id"], "profundidade": 1},
        headers=h,
    )
    assert r.status_code == 200
    g = r.json()
    assert len(g["nos"]) == 5  # Ricardo + 4 empresas
    assert len([a for a in g["arestas"] if a["tipo"] == "socio_de"]) == 4
    # Subgrafo induzido: as arestas mesmo_endereco entre as empresas vizinhas também aparecem.
    assert {a["tipo"] for a in g["arestas"]} == {"socio_de", "mesmo_endereco"}
    assert set(g["arestas"][0]) == {"id", "origem", "destino", "tipo", "atributos", "captura_id"}
    todo = cliente.get(f"/casos/{caso_carregado}/grafo", headers=h).json()
    assert len(todo["nos"]) == 47
    assert (
        cliente.get(f"/casos/{caso_carregado}/grafo", params={"centro": "x"}, headers=h).status_code
        == 404
    )

    lucia = entidade_por_identificador(cliente, caso_carregado, "***777777**", "pessoa")
    r = cliente.get(
        f"/casos/{caso_carregado}/caminho",
        params={"de": ricardo["id"], "para": lucia["id"]},
        headers=h,
    )
    assert r.status_code == 200
    assert len(r.json()["nos"]) == 3  # Ricardo -> EPSILON -> Lucia
    assert (
        cliente.get(
            f"/casos/{caso_carregado}/caminho", params={"de": ricardo["id"]}, headers=h
        ).status_code
        == 422
    )


# ---- Alertas ----------------------------------------------------------------------------------


def test_executar_e_listar_alertas(cliente, caso_carregado):
    h = cabecalhos()
    r = cliente.post(f"/casos/{caso_carregado}/alertas/executar", headers=h)
    assert r.status_code == 200, r.text
    alertas = r.json()
    # As fixtures têm duas empresas com sócio estrangeiro (L5) e duas microempresas com
    # contratos acima do limite de receita do porte (L6). Três empresas acumulam tipologias
    # distintas e ganham o alerta de combinação (COMB).
    assert sorted(a["tipologia"] for a in alertas) == [
        *["COMB", "COMB", "COMB"],
        *["L5", "L5", "L6", "L6"],
        *["T1", "T1", "T2", "T3", "T3", "T4", "T4"],
    ]
    # A lista vem por severidade: a combinação (alta) abre a fila, seguida da T4 (alta).
    assert [x["tipologia"] for x in alertas[:5]] == ["COMB", "COMB", "COMB", "T4", "T4"]
    alertas_por_tipologia = {x["tipologia"]: x for x in alertas}
    a = alertas[3]
    assert set(a) >= {
        "id",
        "tipologia",
        "titulo",
        "motivo",
        "severidade",
        "entidades",
        "evidencias",
        "parametros",
    }
    assert set(a["evidencias"][0]) == {
        "descricao",
        "fonte",
        "captura_id",
        "url_ou_arquivo",
        "sha256",
    }
    limiares = alertas_por_tipologia["T1"]["parametros"]["limiares"]
    assert {
        k: limiares[k] for k in ("t1_dias_minimos", "t2_minimo_empresas", "t3_razao_minima")
    } == {
        "t1_dias_minimos": 180,
        "t2_minimo_empresas": 3,
        "t3_razao_minima": 10.0,
    }
    # As evidências apontam para capturas verificáveis.
    v = cliente.get(f"/capturas/{a['evidencias'][0]['captura_id']}/verificar", headers=h).json()
    assert v["integra"] is True and v["sha256_registrado"] == a["evidencias"][0]["sha256"]

    listados = cliente.get(f"/casos/{caso_carregado}/alertas", headers=h).json()
    assert {x["id"] for x in listados} == {x["id"] for x in alertas}

    r = cliente.post(
        f"/casos/{caso_carregado}/alertas/executar",
        json={"parametros": {"t1_dias_minimos": 50}, "tipologias": ["T1"]},
        headers=h,
    )
    assert [x["tipologia"] for x in r.json()] == ["T1"] and r.json()[0]["parametros"][
        "t1_dias_minimos"
    ] == 50
    r = cliente.post(
        f"/casos/{caso_carregado}/alertas/executar", json={"parametros": {"x": 1}}, headers=h
    )
    assert r.status_code == 422


# ---- Custódia ---------------------------------------------------------------------------------


def test_capturas_ver_e_verificar(cliente, caso_carregado, app):
    h = cabecalhos()
    caso = cliente.get(f"/casos/{caso_carregado}", headers=h).json()
    assert caso["contagens"]["capturas"] == 5
    entidade = entidade_por_identificador(cliente, caso_carregado, "90000001", "empresa")
    captura_id = cliente.get(
        f"/casos/{caso_carregado}/entidades/{entidade['id']}", headers=h
    ).json()["captura_id"]
    r = cliente.get(f"/capturas/{captura_id}", headers=h)
    assert r.status_code == 200
    c = r.json()
    assert set(c) >= {
        "id",
        "fonte",
        "url_ou_arquivo",
        "coletado_em",
        "sha256",
        "metadados",
        "tamanho_bytes",
    }
    assert c["fonte"] == "cnpj_arquivo" and c["url_ou_arquivo"].endswith("EMPRECSV")
    v = cliente.get(f"/capturas/{captura_id}/verificar", headers=h).json()
    assert v == {
        "integra": True,
        "sha256_registrado": c["sha256"],
        "sha256_recalculado": c["sha256"],
        "arquivo_encontrado": True,
        # H23: a carga fica cifrada no disco; o SHA-256 é o do conteúdo original.
        "cifrada": True,
    }

    # Adultera o arquivo guardado: a verificação passa a falhar.
    from buscador.modelos import Captura

    sessao = app.state.fabrica_sessoes()
    captura = sessao.get(Captura, captura_id)
    (app.state.custodia.raiz / captura.caminho_relativo).write_bytes(b"adulterado")
    sessao.close()
    v = cliente.get(f"/capturas/{captura_id}/verificar", headers=h).json()
    assert v["integra"] is False and v["sha256_recalculado"] != v["sha256_registrado"]
    assert cliente.get("/capturas/nao-existe", headers=h).status_code == 404


# ---- Auditoria --------------------------------------------------------------------------------


def test_auditoria_lista_verifica_e_restringe_perfil(cliente, caso_carregado):
    assert cliente.get("/auditoria", headers=cabecalhos("tok-analista")).status_code == 403
    r = cliente.get(
        "/auditoria", params={"caso_id": caso_carregado}, headers=cabecalhos("tok-delegado")
    )
    assert r.status_code == 200
    entradas = r.json()
    acoes = [e["acao"] for e in entradas]
    assert acoes[:7] == [
        "caso.criar",
        # H23: a fixture inclui o delegado e o admin como membros do caso.
        "membro.definir",
        "membro.definir",
        "coleta.iniciar",
        "coleta.executar",
        "coleta.iniciar",
        "coleta.executar",
    ]
    assert set(entradas[0]) == {
        "seq",
        "quando",
        "usuario",
        "acao",
        "caso_id",
        "justificativa",
        "detalhes",
        "hash_anterior",
        "hash",
    }
    assert entradas[1]["hash_anterior"] == entradas[0]["hash"]
    assert (
        entradas[4]["detalhes"]["fonte"] == "cnpj_arquivo"
        and len(entradas[4]["detalhes"]["captura_ids"]) == 3
    )

    v = cliente.get("/auditoria/verificar", headers=cabecalhos("tok-analista")).json()
    assert v["integra"] is True and v["primeira_quebra"] is None and v["entradas"] >= 4


def test_auditoria_detecta_adulteracao_pela_api(cliente, caso_carregado, app):
    from sqlalchemy import text

    with app.state.engine.begin() as conexao:
        conexao.execute(text("DROP TRIGGER auditoria_sem_update"))
        conexao.execute(text("UPDATE auditoria SET usuario = 'intruso' WHERE seq = 1"))
    v = cliente.get("/auditoria/verificar", headers=cabecalhos()).json()
    assert v["integra"] is False and v["primeira_quebra"] == 1


# ---- Relatório --------------------------------------------------------------------------------


def test_relatorio_json_e_markdown(cliente, caso_carregado):
    h = cabecalhos("tok-delegado", "Relatório para o inquérito")
    cliente.post(f"/casos/{caso_carregado}/alertas/executar", headers=h)
    r = cliente.get(f"/casos/{caso_carregado}/relatorio", headers=h)
    assert r.status_code == 200
    d = r.json()
    assert set(d) >= {
        "caso",
        "resumo",
        "entidades",
        "arestas",
        "alertas",
        "custodia",
        "auditoria",
        "sugestoes_resolucao",
    }
    assert d["resumo"]["alertas"] == 14 and d["resumo"]["capturas"] == 5
    assert d["custodia_integra"] is True and all(
        len(c["sha256_registrado"]) == 64 for c in d["custodia"]
    )
    assert d["auditoria"]["integra"] is True
    # A entrada relatorio.gerar é gravada por último (trava curta): aparece no relatório seguinte.
    acoes = [e["acao"] for e in d["auditoria"]["entradas_do_caso"]]
    assert "alerta.executar" in acoes and "relatorio.gerar" not in acoes
    d2 = cliente.get(f"/casos/{caso_carregado}/relatorio", headers=h).json()
    assert any(e["acao"] == "relatorio.gerar" for e in d2["auditoria"]["entradas_do_caso"])
    assert d["alertas"][0]["evidencias"][0]["sha256"]

    r = cliente.get(f"/casos/{caso_carregado}/relatorio", params={"formato": "md"}, headers=h)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/markdown")
    md = r.text
    assert md.startswith("# Relatório de análise: Caso teste")
    assert "## Cadeia de custódia" in md and "## Trilha de auditoria" in md and "[T1]" in md
    assert "Relatório para o inquérito" in md
    assert (
        cliente.get(
            f"/casos/{caso_carregado}/relatorio", params={"formato": "pdf"}, headers=h
        ).status_code
        == 422
    )


def test_justificativa_crua_em_latin1_tambem_e_aceita(cliente, caso_id):
    # Cabeçalho enviado sem percent-encoding, em UTF-8 cru (o servidor o lê como latin-1 e corrige).
    bruto = "Justificativa com ação".encode()
    r = cliente.get(
        f"/casos/{caso_id}", headers={"Authorization": "Bearer tok-admin", "X-Justificativa": bruto}
    )
    assert r.status_code == 200
    entradas = cliente.get(
        "/auditoria", params={"caso_id": caso_id}, headers=cabecalhos("tok-admin")
    ).json()
    assert "Justificativa com ação" in [e["justificativa"] for e in entradas]
