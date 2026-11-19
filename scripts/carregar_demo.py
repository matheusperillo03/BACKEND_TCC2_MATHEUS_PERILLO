"""Cria um caso de demonstração com as fixtures SINTÉTICAS e roda o fluxo completo.

Passa pelas mesmas rotas HTTP do contrato (em processo, com TestClient), de modo que a cadeia de
custódia e a trilha de auditoria ficam preenchidas como numa sessão real. Usa o banco apontado
por DATABASE_URL (SQLite por padrão; PostgreSQL no Docker Compose). A coleta do PNCP é feita com
um transporte simulado que serve as páginas de tests/fixtures/pncp, sem acessar a rede.

Todas as capturas da demonstração saem marcadas como sintéticas (``simulado://`` na origem e
``metadados.simulado = true``): o PNCP por causa do transporte simulado, e o CNPJ por causa do
arquivo-marcador ``SINTETICO.txt`` da pasta de fixtures. A raiz de ingestão do CNPJ é fixada em
tests/fixtures só para este script. Exige as mesmas variáveis do serviço
(``AUDITORIA_CHAVE_HMAC`` e ``CUSTODIA_CHAVE``).

Quem "faz" a demonstração (H23): no modo de desenvolvimento (``MODO_DEV=1``), o primeiro token de
``TOKENS_DEV``. Fora dele, um usuário real que já exista, indicado em ``DEMO_USUARIO`` (crie-o com
``python -m buscador.usuarios criar``): o script abre para ele uma sessão curta direto no banco
(sem senha nem código, porque roda no servidor com a credencial do banco), registra isso na
auditoria (``auth.sessao_script``) e a encerra no fim. Esse usuário fica como responsável pelo
caso de demonstração e o vê na interface.

Horizonte 2: a demonstração coleta também os termos de contrato do PNCP, o CEIS, o CNEP e os
servidores do Portal da Transparência (todos por transporte simulado; a chave usada é fictícia),
os arquivos sintéticos do TSE e os editais sintéticos de tests/fixtures/h2, roda as tipologias T1
a T10 e marca uma empresa para monitoramento contínuo.

Horizonte 3: inclui os demais usuários de desenvolvimento como membros do caso (quando existem),
cria uma anotação e uma tarefa, exporta o grafo (ZIP com GraphML e CSV) e roda a extração
assistida por IA sobre uma ata sintética com o provedor FALSO (expressões regulares, sem modelo
nenhum): uma sugestão é aceita e a outra fica pendente, para a revisão na interface.

Uso:
  uv run python scripts/carregar_demo.py
  docker compose exec -e DEMO_USUARIO=<login> api uv run --no-sync python scripts/carregar_demo.py
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import quote

from fastapi.testclient import TestClient

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

from buscador import auditoria  # noqa: E402
from buscador.acesso import servico as acesso  # noqa: E402
from buscador.api import criar_app  # noqa: E402
from buscador.conectores.pncp import TransporteSimulado  # noqa: E402
from buscador.conectores.simulados import extras_de_diretorio  # noqa: E402
from buscador.config import Configuracao  # noqa: E402
from buscador.ia.falso import ProvedorFalso  # noqa: E402

FIXTURES_CNPJ = RAIZ / "tests" / "fixtures" / "cnpj"
FIXTURES_PNCP = RAIZ / "tests" / "fixtures" / "pncp"
FIXTURES_H2 = RAIZ / "tests" / "fixtures" / "h2"
FIXTURES_H3 = RAIZ / "tests" / "fixtures" / "h3"
CNPJ_ORGAO_H2 = "90100003000190"  # PREFEITURA SINTETICA DE EXEMPLOPOLIS
SIAPE_H2 = "99001"


def _paginas_pncp() -> dict[int, bytes]:
    """As páginas sintéticas dos dois horizontes numa só sequência (1, 2 e 3)."""
    arquivos = [
        *sorted(FIXTURES_PNCP.glob("contratos_pagina*.json")),
        FIXTURES_H2 / "pncp" / "contratos_pagina1.json",
    ]
    paginas = [json.loads(a.read_text(encoding="utf-8")) for a in arquivos]
    for posicao, pagina in enumerate(paginas, start=1):
        pagina.update(
            numeroPagina=posicao,
            totalPaginas=len(paginas),
            paginasRestantes=len(paginas) - posicao,
        )
    return {
        posicao: json.dumps(pagina, ensure_ascii=False).encode("utf-8")
        for posicao, pagina in enumerate(paginas, start=1)
    }


def _sessao_do_script(app, config: Configuracao) -> tuple[str, str] | None:
    """Fora do modo de desenvolvimento: sessão curta para o usuário de ``DEMO_USUARIO``."""
    usuario = os.environ.get("DEMO_USUARIO", "").strip().lower()
    if not usuario:
        print(
            "Sem tokens de desenvolvimento (MODO_DEV=0): informe em DEMO_USUARIO o login de um "
            "usuário existente (crie com `python -m buscador.usuarios criar <login> <perfil>`).",
            file=sys.stderr,
        )
        return None
    with app.state.fabrica_sessoes() as sessao:
        conta = acesso.obter_conta(sessao, usuario)
        if conta is None or not conta.ativo:
            print(f"Usuário '{usuario}' não existe ou está inativo.", file=sys.stderr)
            return None
        token, linha = acesso._abrir_sessao(sessao, conta, "ativa", 15)
        entrada = auditoria.registrar(
            sessao,
            app.state.chave_auditoria,
            usuario="cli",
            acao="auth.sessao_script",
            caso_id=None,
            justificativa="Carga de demonstração com dados sintéticos",
            detalhes={"usuario_da_sessao": conta.usuario, "sessao_id": linha.id, "minutos": 15},
        )
        sessao.commit()
        app.state.ancora.avancar(entrada.seq, entrada.hash)
    return token, usuario


def main() -> int:
    config = Configuracao().model_copy(
        update={
            "cnpj_dir_base": FIXTURES_CNPJ.parent,
            "tse_dir_base": FIXTURES_H2,
            # Raiz comum aos editais e atas sintéticos (h2/editais e h3/editais).
            "editais_dir_base": FIXTURES_H2.parent,
            # Chave fictícia: o Portal é simulado e nenhuma requisição sai desta máquina.
            "portal_transparencia_chave": "chave-ficticia-da-demonstracao",
            # O agendador não roda em segundo plano durante o script.
            "monitoramento_agendador_ativo": False,
        }
    )
    extras = extras_de_diretorio(FIXTURES_H2)
    extras["pncp"] = {"transporte": TransporteSimulado(_paginas_pncp())}
    # Provedor FALSO da extração por IA: só neste processo; o serviço em execução não muda.
    app = criar_app(config, extras_conectores=extras, provedor_ia=ProvedorFalso())
    tokens = config.tokens()
    sessao_do_script = False
    if tokens:
        token = next(iter(tokens))
        usuario_demo = tokens[token].usuario
    else:
        aberta = _sessao_do_script(app, config)
        if aberta is None:
            return 2
        token, usuario_demo = aberta
        sessao_do_script = True
    outros_usuarios = [u.usuario for u in tokens.values() if u.usuario != usuario_demo]
    cabecalhos = {
        "Authorization": f"Bearer {token}",
        "X-Justificativa": quote("Carga de demonstração com dados sintéticos"),
    }
    with TestClient(app) as cliente:
        r = cliente.post(
            "/casos",
            json={
                "titulo": "Caso de demonstração (dados sintéticos)",
                "descricao": "Fixtures sintéticas no layout do CNPJ e do PNCP; nenhum dado real.",
                "numero_procedimento": "DEMO-0001/2026",
                # RNF04 (H17): todo caso declara a finalidade e o prazo de retenção.
                "finalidade": "Demonstração do protótipo com dados sintéticos.",
                "retencao_ate": (datetime.now(UTC).date() + timedelta(days=365)).isoformat(),
            },
            headers=cabecalhos,
        )
        r.raise_for_status()
        caso = r.json()
        caso_id = caso["id"]
        print(f"Caso criado: {caso_id} ({caso['titulo']}), retenção até {caso['retencao_ate']}")

        r = cliente.post(
            f"/casos/{caso_id}/coletas",
            json={"fonte": "cnpj_arquivo", "parametros": {"diretorio": FIXTURES_CNPJ.name}},
            headers=cabecalhos,
        )
        r.raise_for_status()
        c1 = r.json()
        print(
            f"Coleta cnpj_arquivo: {len(c1['captura_ids'])} capturas, "
            f"{c1['entidades_novas']} entidades novas, "
            f"{c1['arestas_novas']} arestas, {c1['sugestoes_novas']} sugestões "
            f"[conector {c1['conector_nome']} {c1['conector_versao']}, {c1['situacao']}, "
            f"{c1['tentativas']} tentativa(s)]"
        )

        r = cliente.post(
            f"/casos/{caso_id}/coletas",
            json={
                "fonte": "pncp",
                "parametros": {
                    "dataInicial": "20260101",
                    "dataFinal": "20261231",
                    "max_paginas": 5,
                },
            },
            headers=cabecalhos,
        )
        r.raise_for_status()
        c2 = r.json()
        print(
            f"Coleta pncp (simulada): {len(c2['captura_ids'])} capturas, "
            f"{c2['entidades_novas']} entidades novas, "
            f"{c2['arestas_novas']} arestas "
            f"[conector {c2['conector_nome']} {c2['conector_versao']}, {c2['situacao']}, "
            f"{c2['tentativas']} tentativa(s)]"
        )

        # ---- Horizonte 2: novas fontes (H19) ----------------------------------------------
        coletas_h2 = [
            ("pncp_termos", {"do_caso": True}),
            ("portal_transparencia", {"recurso": "ceis", "do_caso": True}),
            ("portal_transparencia", {"recurso": "cnep", "do_caso": True}),
            (
                "portal_transparencia",
                {
                    "recurso": "servidores",
                    "orgaoServidorLotacao": SIAPE_H2,
                    "cnpjOrgao": CNPJ_ORGAO_H2,
                },
            ),
            ("tse_arquivo", {"diretorio": "tse"}),
            ("edital_arquivo", {"diretorio": "h2/editais", "cnpjOrgao": CNPJ_ORGAO_H2}),
        ]
        for fonte, parametros in coletas_h2:
            r = cliente.post(
                f"/casos/{caso_id}/coletas",
                json={"fonte": fonte, "parametros": parametros},
                headers=cabecalhos,
            )
            r.raise_for_status()
            c = r.json()
            print(
                f"Coleta {fonte} {parametros.get('recurso', '')} (simulada): "
                f"{len(c['captura_ids'])} capturas, {c['registros']} registros "
                f"[conector {c['conector_nome']} {c['conector_versao']}, {c['situacao']}]"
            )

        r = cliente.post(f"/casos/{caso_id}/alertas/executar", json={}, headers=cabecalhos)
        r.raise_for_status()
        alertas = r.json()
        print(
            f"Alertas: {len(alertas)} (por severidade; todos pendentes de confirmação ou descarte)"
        )
        for a in alertas:
            print(f"  [{a['tipologia']}] ({a['severidade']}, {a['situacao']}) {a['motivo']}")

        r = cliente.get(
            f"/casos/{caso_id}/entidades", params={"q": "CARLOS HOMONIMO"}, headers=cabecalhos
        )
        for e in r.json():
            det = cliente.get(f"/casos/{caso_id}/entidades/{e['id']}", headers=cabecalhos).json()
            for s in det["sugestoes"]:
                print(
                    f"  Sugestão: {e['rotulo']} ({e['identificador']}) ~ {s['candidata_rotulo']} "
                    f"({s['candidata_identificador']}), score {s['score']}"
                )

        # ---- Horizonte 2: monitoramento contínuo (H18) -------------------------------------
        r = cliente.get(
            f"/casos/{caso_id}/entidades",
            params={"q": "90000006", "tipo": "empresa"},
            headers=cabecalhos,
        )
        r.raise_for_status()
        zeta = next(e for e in r.json() if e["identificador"] == "90000006")
        r = cliente.post(
            f"/casos/{caso_id}/monitoramentos",
            json={"entidade_id": zeta["id"], "periodicidade_horas": 24},
            headers=cabecalhos,
        )
        r.raise_for_status()
        monitorado = r.json()
        r = cliente.post(
            f"/casos/{caso_id}/monitoramentos/{monitorado['id']}/executar", headers=cabecalhos
        )
        r.raise_for_status()
        execucao = r.json()
        print(
            f"Monitoramento de {monitorado['entidade_rotulo']} a cada "
            f"{monitorado['periodicidade_horas']} h: {len(execucao['coletas'])} recoleta(s), "
            f"situação {execucao['situacao']}, {execucao['avisos_gerados']} aviso(s) de novidade"
        )

        # ---- Horizonte 3: equipe, anotações, tarefas, exportação e IA ----------------------
        for usuario, papel in zip(outros_usuarios, ("investigador", "leitor"), strict=False):
            r = cliente.post(
                f"/casos/{caso_id}/membros",
                json={"usuario": usuario, "papel": papel},
                headers=cabecalhos,
            )
            r.raise_for_status()
        membros = cliente.get(f"/casos/{caso_id}/membros", headers=cabecalhos).json()
        print("Membros do caso: " + ", ".join(f"{m['usuario']} ({m['papel']})" for m in membros))

        r = cliente.post(
            f"/casos/{caso_id}/anotacoes",
            json={
                "alvo_tipo": "entidade",
                "alvo_id": zeta["id"],
                "texto": "Anotação sintética: conferir o quadro societário com a junta comercial.",
            },
            headers=cabecalhos,
        )
        r.raise_for_status()
        if alertas:
            r = cliente.post(
                f"/casos/{caso_id}/anotacoes",
                json={
                    "alvo_tipo": "alerta",
                    "alvo_id": alertas[0]["id"],
                    "texto": "Anotação sintética: alerta a discutir na reunião da equipe.",
                },
                headers=cabecalhos,
            )
            r.raise_for_status()
        r = cliente.post(
            f"/casos/{caso_id}/tarefas",
            json={
                "titulo": "Solicitar o processo de contratação (tarefa sintética)",
                "descricao": "Tarefa de demonstração; nenhum ofício real.",
                "responsavel": usuario_demo,
                "prazo": (datetime.now(UTC).date() + timedelta(days=15)).isoformat(),
            },
            headers=cabecalhos,
        )
        r.raise_for_status()
        tarefa = r.json()
        r = cliente.post(
            f"/casos/{caso_id}/tarefas/{tarefa['id']}",
            json={"situacao": "em_andamento"},
            headers=cabecalhos,
        )
        r.raise_for_status()
        print(
            f"Anotações: 2; tarefa '{tarefa['titulo']}' em andamento "
            f"({len(r.json()['historico'])} entradas no histórico)"
        )

        r = cliente.get(
            f"/casos/{caso_id}/exportacao", params={"formato": "zip"}, headers=cabecalhos
        )
        r.raise_for_status()
        print(
            f"Exportação do grafo (ZIP com GraphML e CSV): {len(r.content)} bytes, "
            f"SHA-256 {hashlib.sha256(r.content).hexdigest()}"
        )

        r = cliente.post(
            f"/casos/{caso_id}/coletas",
            json={
                "fonte": "edital_arquivo",
                "parametros": {"diretorio": "h3/editais", "cnpjOrgao": CNPJ_ORGAO_H2},
            },
            headers=cabecalhos,
        )
        r.raise_for_status()
        captura_ata = r.json()["captura_ids"][0]
        # A extração roda em segundo plano: o pedido responde 202 e o trabalhador processa.
        r = cliente.post(f"/casos/{caso_id}/capturas/{captura_ata}/extracao-ia", headers=cabecalhos)
        r.raise_for_status()
        rota_extracao = f"/casos/{caso_id}/extracoes-ia/{r.json()['id']}"
        limite = time.monotonic() + 60
        while cliente.get(f"{rota_extracao}/progresso", headers=cabecalhos).json()["ativa"]:
            if time.monotonic() > limite:
                raise SystemExit("A extração da demonstração não terminou em 60 s.")
            time.sleep(0.05)
        r = cliente.get(rota_extracao, headers=cabecalhos)
        r.raise_for_status()
        extracao = r.json()
        print(
            f"Extração por IA (provedor FALSO, sem modelo): {extracao['itens_aceitos']} "
            f"sugestão(ões), {extracao['itens_recusados']} item(ns) recusado(s) "
            f"[{extracao['provedor']} / {extracao['modelo']}]"
        )
        for sugestao in extracao["sugestoes"]:
            print(f'  Sugestão ({sugestao["tipo"]}): {sugestao["valor"]} — "{sugestao["trecho"]}"')
        empresa = next((s for s in extracao["sugestoes"] if s["tipo"] == "empresa"), None)
        if empresa is not None:
            r = cliente.post(
                f"/casos/{caso_id}/sugestoes-ia/{empresa['id']}/decisao",
                json={
                    "decisao": "aceita",
                    "justificativa": "CNPJ conferido no trecho citado da ata sintética.",
                },
                headers=cabecalhos,
            )
            r.raise_for_status()
            print(
                f"  Sugestão de empresa aceita: entidade {r.json()['entidade_id']} ligada à ata; "
                "as demais ficam pendentes para a revisão na interface."
            )

        r = cliente.get("/auditoria/verificar", headers=cabecalhos)
        print(f"Auditoria: {json.dumps(r.json(), ensure_ascii=False)}")
        print()
        print("Exemplos:")
        print(
            "  curl -H 'Authorization: Bearer <token>' -H 'X-Justificativa: demo' "
            f"http://127.0.0.1:8100/casos/{caso_id}"
        )
        print(
            "  curl -H 'Authorization: Bearer <token>' -H 'X-Justificativa: demo' "
            f"'http://127.0.0.1:8100/casos/{caso_id}/relatorio?formato=md'"
        )
        print(
            "  curl -OJ -H 'Authorization: Bearer <token>' -H 'X-Justificativa: demo' "
            f"'http://127.0.0.1:8100/casos/{caso_id}/exportacao?formato=zip'"
        )
        if sessao_do_script:
            cliente.post("/auth/logout", headers={"Authorization": f"Bearer {token}"})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
