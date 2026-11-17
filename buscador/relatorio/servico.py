from __future__ import annotations

import json
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from buscador import auditoria, retencao, tipologias
from buscador.custodia import PREFIXO_SIMULADO, Custodia
from buscador.modelos import (
    Alerta,
    Aresta,
    Aviso,
    Captura,
    Caso,
    Coleta,
    Entidade,
    Monitoramento,
    Sugestao,
    iso_utc,
)
from buscador.seguranca import sanitizar_json, texto_de_linha

NOTA_DADOS_PESSOAIS = (
    "CPF sempre descaracterizado (***NNNNNN**): só os seis dígitos centrais são guardados, mesmo "
    "quando a fonte traz o CPF completo. Coincidências entre pessoas de fontes diferentes "
    "(sócio, servidor, doador) são apresentadas como possível coincidência, nunca como "
    "identificação, e os registros não são fundidos."
)

# Só estes perfis leem as entradas da trilha (a mesma regra de GET /auditoria).
PERFIS_TRILHA = ("delegado", "admin")
MAX_ENTRADAS_TRILHA = 500


def gerar(
    sessao: Session,
    caso: Caso,
    custodia: Custodia,
    *,
    gerado_por: str,
    perfil: str,
    chave_auditoria: bytes,
    ancora: auditoria.Ancora | None = None,
) -> dict:
    """Relatório do caso em JSON estrito (``NaN``/``Infinity`` antigos saem como ``null``).

    As entradas da trilha de auditoria só entram para ``delegado`` e ``admin``; os demais perfis
    recebem apenas o resumo da integridade da cadeia.
    """
    entidades = list(
        sessao.execute(
            select(Entidade)
            .where(Entidade.caso_id == caso.id)
            .order_by(Entidade.tipo, Entidade.rotulo)
        ).scalars()
    )
    arestas = list(sessao.execute(select(Aresta).where(Aresta.caso_id == caso.id)).scalars())
    # Mesma ordem da lista de alertas: severidade (alta primeiro), tipologia e título (RF14).
    alertas = tipologias.ordenar(
        list(sessao.execute(select(Alerta).where(Alerta.caso_id == caso.id)).scalars())
    )
    coletas = list(
        sessao.execute(
            select(Coleta).where(Coleta.caso_id == caso.id).order_by(Coleta.iniciado_em)
        ).scalars()
    )
    capturas = list(
        sessao.execute(
            select(Captura).where(Captura.caso_id == caso.id).order_by(Captura.coletado_em)
        ).scalars()
    )
    sugestoes = list(sessao.execute(select(Sugestao).where(Sugestao.caso_id == caso.id)).scalars())
    monitoramentos = list(
        sessao.execute(
            select(Monitoramento)
            .where(Monitoramento.caso_id == caso.id)
            .order_by(Monitoramento.criado_em)
        ).scalars()
    )
    avisos = list(
        sessao.execute(
            select(Aviso).where(Aviso.caso_id == caso.id).order_by(Aviso.criado_em)
        ).scalars()
    )
    cadeia = auditoria.verificar(sessao, chave_auditoria, ancora)
    rotulos = {e.id: e.rotulo for e in entidades}

    custodia_linhas = []
    for c in capturas:
        verificacao = custodia.verificar(c)
        custodia_linhas.append(
            {
                "captura_id": c.id,
                "fonte": c.fonte,
                "url_ou_arquivo": c.url_ou_arquivo,
                "coletado_em": c.coletado_em,
                "sha256_registrado": verificacao.sha256_registrado,
                "sha256_recalculado": verificacao.sha256_recalculado,
                "tamanho_bytes": c.tamanho_bytes,
                "conector_nome": c.conector_nome or "",
                "conector_versao": c.conector_versao or "",
                "coleta_id": c.coleta_id,
                "integra": verificacao.integra,
                "simulado": bool((c.metadados or {}).get("simulado"))
                or c.url_ou_arquivo.startswith(PREFIXO_SIMULADO),
            }
        )

    ve_trilha = perfil in PERFIS_TRILHA
    total_do_caso = auditoria.contar(sessao, caso.id)
    entradas_do_caso = (
        auditoria.listar(sessao, caso.id, limite=MAX_ENTRADAS_TRILHA, ultimas=True)
        if ve_trilha
        else []
    )

    dados = {
        "gerado_em": iso_utc(),
        "gerado_por": gerado_por,
        "caso": {
            "id": caso.id,
            "titulo": caso.titulo,
            "descricao": caso.descricao,
            "numero_procedimento": caso.numero_procedimento,
            "criado_por": caso.criado_por,
            "criado_em": caso.criado_em,
            "finalidade": caso.finalidade or "",
            "retencao_ate": caso.retencao_ate,
            "somente_leitura": retencao.vencida(caso),
        },
        "resumo": {
            "entidades": len(entidades),
            "arestas": len(arestas),
            "alertas": len(alertas),
            "capturas": len(capturas),
            "sugestoes_resolucao": len(sugestoes),
            "coletas": len(coletas),
            "coletas_com_falha": sum(c.situacao == "falha" for c in coletas),
            "coletas_nao_configuradas": sum(c.situacao == "nao_configurado" for c in coletas),
            "monitoramentos_ativos": sum(bool(m.ativo) for m in monitoramentos),
            "avisos": len(avisos),
            "avisos_nao_lidos": sum(a.lido_em is None for a in avisos),
            "alertas_pendentes": sum(al.situacao == "pendente" for al in alertas),
            "alertas_confirmados": sum(al.situacao == "confirmado" for al in alertas),
            "alertas_descartados": sum(al.situacao == "descartado" for al in alertas),
        },
        # RNF12 (H15): toda coleta do caso, inclusive as que falharam, com tentativas e motivo.
        "coletas": [
            {
                "id": c.id,
                "fonte": c.fonte,
                "conector_nome": c.conector_nome,
                "conector_versao": c.conector_versao,
                "situacao": c.situacao,
                "tentativas": c.tentativas,
                "motivo": c.motivo or "",
                "registros": c.registros,
                "avisos": c.avisos or [],
                "usuario": c.usuario,
                "iniciado_em": c.iniciado_em,
                "concluido_em": c.concluido_em,
                "recoleta_de": c.recoleta_de,
            }
            for c in coletas
        ],
        "entidades": [
            {
                "id": e.id,
                "tipo": e.tipo,
                "rotulo": e.rotulo,
                "identificador": e.identificador,
                "atributos": e.atributos or {},
                "capturas": sorted({f.captura_id for f in e.fontes} | {e.captura_id}),
            }
            for e in entidades
        ],
        "arestas": [
            {
                "id": a.id,
                "origem": a.origem_id,
                "origem_rotulo": rotulos.get(a.origem_id),
                "destino": a.destino_id,
                "destino_rotulo": rotulos.get(a.destino_id),
                "tipo": a.tipo,
                "atributos": a.atributos or {},
                "captura_id": a.captura_id,
            }
            for a in arestas
        ],
        "sugestoes_resolucao": [
            {
                "entidade_id": s.entidade_id,
                "entidade_rotulo": rotulos.get(s.entidade_id),
                "candidata_id": s.candidata_id,
                "candidata_rotulo": rotulos.get(s.candidata_id),
                "score": s.score,
                "motivo": s.motivo,
            }
            for s in sugestoes
        ],
        "alertas": [
            {
                "id": al.id,
                "tipologia": al.tipologia,
                "titulo": al.titulo,
                "motivo": al.motivo,
                "severidade": al.severidade,
                "entidades": [
                    {"id": eid, "rotulo": rotulos.get(eid)} for eid in (al.entidades or [])
                ],
                "evidencias": al.evidencias or [],
                "parametros": al.parametros or {},
                "politica_erro": _politica_erro(al.tipologia),
                "situacao": al.situacao or "pendente",
                "ativo": bool(al.ativo),
                "decisoes": [
                    {
                        "decisao": d.decisao,
                        "justificativa": d.justificativa,
                        "usuario": d.usuario,
                        "perfil": d.perfil,
                        "decidido_em": d.decidido_em,
                    }
                    for d in al.decisoes
                ],
            }
            for al in alertas
        ],
        # RF06 (H18): entidades acompanhadas, execuções e avisos de novidade.
        "monitoramentos": [
            {
                "id": m.id,
                "entidade_id": m.entidade_id,
                "entidade_rotulo": rotulos.get(m.entidade_id),
                "periodicidade_horas": m.periodicidade_horas,
                "ativo": bool(m.ativo),
                "motivo_inativo": m.motivo_inativo or "",
                "criado_por": m.criado_por,
                "criado_em": m.criado_em,
                "ultima_execucao_em": m.ultima_execucao_em,
                "proxima_execucao_em": m.proxima_execucao_em,
                "execucoes": [
                    {
                        "id": e.id,
                        "origem": e.origem,
                        "usuario": e.usuario,
                        "iniciado_em": e.iniciado_em,
                        "concluido_em": e.concluido_em,
                        "situacao": e.situacao,
                        "coletas": e.coletas or [],
                        "avisos_gerados": e.avisos_gerados,
                        "motivo": e.motivo or "",
                    }
                    for e in m.execucoes
                ],
            }
            for m in monitoramentos
        ],
        "avisos": [
            {
                "id": a.id,
                "tipo": a.tipo,
                "entidade_id": a.entidade_id,
                "entidade_rotulo": rotulos.get(a.entidade_id),
                "titulo": a.titulo,
                "descricao": a.descricao,
                "detalhes": a.detalhes or {},
                "captura_ids": a.captura_ids or [],
                "criado_em": a.criado_em,
                "lido_em": a.lido_em,
                "lido_por": a.lido_por,
            }
            for a in avisos
        ],
        # RNF04: nenhum CPF completo é guardado em entidades, alertas ou avisos.
        "dados_pessoais": NOTA_DADOS_PESSOAIS,
        "custodia": custodia_linhas,
        "custodia_integra": all(linha["integra"] for linha in custodia_linhas),
        "contem_capturas_simuladas": any(linha["simulado"] for linha in custodia_linhas),
        "auditoria": {
            "integra": cadeia.integra,
            "entradas": cadeia.entradas,
            "primeira_quebra": cadeia.primeira_quebra,
            "motivo": cadeia.motivo,
            "ancora_seq": cadeia.ancora_seq,
            "trilha_restrita": not ve_trilha,
            "entradas_do_caso_total": total_do_caso,
            "entradas_do_caso": entradas_do_caso,
        },
    }
    return sanitizar_json(dados)  # type: ignore[return-value]


def _politica_erro(tipologia: str) -> str:
    politica = tipologias.politica(tipologia)
    return politica.politica_erro if politica else ""


# ---- Markdown ---------------------------------------------------------------------------------

_ESPECIAIS_MD = re.compile(r"([\\`*_\[\]<>#|~])")
_INICIO_DE_BLOCO = re.compile(r"^(\d+[.)]|[-+=])")
_ESPACOS = re.compile(r"\s+")


def _linha(valor: object) -> str:
    """Uma linha só: quebras, tabulações e caracteres de controle ou invisíveis viram espaço."""
    return _ESPACOS.sub(" ", texto_de_linha("" if valor is None else valor)).strip()


def _md(valor: object) -> str:
    """Texto externo como texto literal de Markdown: sem quebra de linha e com os caracteres de
    marcação escapados, para não abrir título, linha de tabela, link, ênfase nem HTML."""
    texto = _ESPECIAIS_MD.sub(r"\\\1", _linha(valor))
    # "-", "+", "=" ou "1." no começo abririam lista ou título quando o valor inicia a linha.
    return "\\" + texto if _INICIO_DE_BLOCO.match(texto) else texto


def _cod(valor: object) -> str:
    """Valor dentro de crases: sem crase e sem quebra; a barra vertical vai escapada (tabelas)."""
    return "`" + _linha(valor).replace("`", "'").replace("|", "\\|") + "`"


_SITUACOES_ALERTA = {
    "pendente": "pendente de análise",
    "confirmado": "confirmado",
    "descartado": "descartado",
}
_SITUACOES_COLETA = {
    "sucesso": "sucesso",
    "falha": "FALHA",
    "em_andamento": "em andamento",
    "nao_configurado": "fonte não configurada",
}
_TIPOS_AVISO = {
    "quadro_societario": "quadro societário",
    "contrato_novo": "contrato novo",
    "sancao_nova": "sanção nova",
    "termo_novo": "termo de contrato novo",
}


def _sim_nao(valor: bool) -> str:
    return "sim" if valor else "NÃO"


def para_markdown(dados: dict) -> str:
    caso = dados["caso"]
    linhas = [
        f"# Relatório de análise: {_md(caso['titulo'])}",
        "",
        f"- Caso: {_cod(caso['id'])}",
        f"- Procedimento: {_md(caso['numero_procedimento']) or '(não informado)'}",
        f"- Criado por {_md(caso['criado_por'])} em {_md(caso['criado_em'])}",
        f"- Gerado por {_md(dados['gerado_por'])} em {_md(dados['gerado_em'])}",
        f"- Finalidade: {_md(caso.get('finalidade')) or '(não informada)'}",
        f"- Prazo de retenção: {_md(caso.get('retencao_ate')) or '(não definido)'}"
        + (" — **vencido: caso só para leitura**" if caso.get("somente_leitura") else ""),
        "",
    ]
    if dados.get("contem_capturas_simuladas"):
        linhas += [
            "> **Atenção: este caso contém capturas SINTÉTICAS** (origem `simulado://`), geradas "
            "para teste ou demonstração. Elas não vieram de fonte oficial e não servem de prova.",
            "",
        ]
    linhas += [
        "## Resumo",
        "",
        "| Entidades | Arestas | Alertas | Capturas | Coletas | Sugestões de resolução |",
        "|---|---|---|---|---|---|",
        "| {entidades} | {arestas} | {alertas} | {capturas} | {coletas} | "
        "{sugestoes_resolucao} |".format(
            **{"coletas": 0, **{k: int(v or 0) for k, v in dados["resumo"].items()}}
        ),
        "",
        "## Alertas de tipologia",
        "",
    ]
    if not dados["alertas"]:
        linhas.append("Nenhum alerta gerado (ou as tipologias ainda não foram executadas).")
    else:
        resumo = dados["resumo"]
        linhas += [
            "Todo alerta é uma pista, não uma conclusão: a confirmação ou o descarte é do "
            "investigador. Situação: "
            f"{int(resumo.get('alertas_pendentes') or 0)} pendente(s), "
            f"{int(resumo.get('alertas_confirmados') or 0)} confirmado(s) e "
            f"{int(resumo.get('alertas_descartados') or 0)} descartado(s). "
            "Ordem: severidade (alta primeiro).",
            "",
        ]
    for al in dados["alertas"]:
        decisoes = al.get("decisoes") or []
        linhas += [
            f"### [{_md(al['tipologia'])}] {_md(al['titulo'])} "
            f"(severidade {_md(al['severidade'])})",
            "",
            f"Situação: **{_md(_SITUACOES_ALERTA.get(al.get('situacao'), al.get('situacao')))}**"
            + (
                ""
                if al.get("ativo", True)
                else " (não detectado na última execução da tipologia; mantido pela decisão)"
            ),
            "",
        ]
        for posicao, d in enumerate(decisoes, start=1):
            linhas.append(
                f"- Decisão {posicao}{' (em vigor)' if posicao == len(decisoes) else ''}: "
                f"{_md(d.get('decisao'))} por {_md(d.get('usuario'))} em "
                f"{_md(d.get('decidido_em'))} — justificativa: {_md(d.get('justificativa'))}"
            )
        if decisoes:
            linhas.append("")
        if al.get("politica_erro"):
            linhas += [f"Política de erro da tipologia: {_md(al['politica_erro'])}", ""]
        linhas += [
            _md(al["motivo"]),
            "",
            "Entidades: "
            + ", ".join(f"{_md(e['rotulo'])} ({_cod(e['id'])})" for e in al["entidades"]),
            "",
            "Evidências:",
            "",
        ]
        for ev in al["evidencias"]:
            linhas.append(
                f"- {_md(ev.get('descricao'))} — fonte {_cod(ev.get('fonte'))}, "
                f"captura {_cod(ev.get('captura_id'))}, {_cod(ev.get('url_ou_arquivo'))}, "
                f"SHA-256 {_cod(ev.get('sha256'))}"
            )
        parametros = json.dumps(al["parametros"], ensure_ascii=False, sort_keys=True)
        linhas += ["", f"Parâmetros: {_cod(parametros)}", ""]

    linhas += [
        "## Entidades",
        "",
        "| Tipo | Rótulo | Identificador | Capturas |",
        "|---|---|---|---|",
    ]
    for e in dados["entidades"]:
        linhas.append(
            f"| {_md(e['tipo'])} | {_md(e['rotulo'])} | {_md(e['identificador'] or '')} | "
            + ", ".join(_cod(c) for c in e["capturas"])
            + " |"
        )

    if dados["sugestoes_resolucao"]:
        linhas += ["", "## Sugestões de resolução (não fundidas)", ""]
        for s in dados["sugestoes_resolucao"]:
            linhas.append(
                f"- {_md(s['entidade_rotulo'])} ↔ {_md(s['candidata_rotulo'])} "
                f"(score {float(s['score'] or 0):.2f}): {_md(s['motivo'])}"
            )

    linhas += [
        "",
        "## Coletas",
        "",
    ]
    coletas = dados.get("coletas") or []
    if not coletas:
        linhas.append("Nenhuma coleta registrada.")
    else:
        falhas = int(dados["resumo"].get("coletas_com_falha") or 0)
        if falhas:
            linhas += [
                f"> **Atenção: {falhas} coleta(s) deste caso falharam.** Os dados podem estar "
                "incompletos; veja o motivo de cada falha na tabela.",
                "",
            ]
        linhas += [
            "| Coleta | Fonte | Conector | Situação | Tentativas | Motivo | Iniciada em | "
            "Recoleta de |",
            "|---|---|---|---|---|---|---|---|",
        ]
        for c in coletas:
            linhas.append(
                f"| {_cod(c['id'])} | {_cod(c['fonte'])} | "
                f"{_md(c['conector_nome'])} {_md(c['conector_versao'])} | "
                f"{_md(_SITUACOES_COLETA.get(c['situacao'], c['situacao']))} | "
                f"{int(c['tentativas'] or 0)} | {_md(c['motivo'] or '')} | "
                f"{_md(c['iniciado_em'])} | {_cod(c['recoleta_de']) if c['recoleta_de'] else ''} |"
            )

    linhas += ["", "## Monitoramento contínuo e avisos", ""]
    monitoramentos = dados.get("monitoramentos") or []
    if not monitoramentos:
        linhas.append("Nenhuma entidade marcada para acompanhamento.")
    else:
        linhas += [
            "| Entidade | Periodicidade (h) | Ativo | Execuções | Última execução | Próxima |",
            "|---|---|---|---|---|---|",
        ]
        for m in monitoramentos:
            linhas.append(
                f"| {_md(m['entidade_rotulo'] or m['entidade_id'])} | "
                f"{int(m['periodicidade_horas'] or 0)} | "
                f"{'sim' if m['ativo'] else 'não (' + _md(m['motivo_inativo']) + ')'} | "
                f"{len(m['execucoes'])} | {_md(m['ultima_execucao_em'] or '')} | "
                f"{_md(m['proxima_execucao_em']) if m['ativo'] else ''} |"
            )
    avisos = dados.get("avisos") or []
    if avisos:
        linhas += ["", f"Avisos de novidade ({len(avisos)}):", ""]
        for a in avisos:
            capturas = ", ".join(_cod(c) for c in a["captura_ids"])
            linhas.append(
                f"- [{_md(_TIPOS_AVISO.get(a['tipo'], a['tipo']))}] {_md(a['criado_em'])}: "
                f"{_md(a['descricao'])}"
                + (f" Capturas: {capturas}." if capturas else "")
                + (f" Lido por {_md(a['lido_por'])}." if a.get("lido_em") else " Não lido.")
            )
    if dados.get("dados_pessoais"):
        linhas += ["", "## Dados pessoais", "", _md(dados["dados_pessoais"])]

    linhas += [
        "",
        "## Cadeia de custódia",
        "",
        f"Integridade de todas as capturas: {_sim_nao(dados['custodia_integra'])}",
        "",
        "| Captura | Fonte | Conector | Origem | Coletado em | SHA-256 registrado | Íntegra | "
        "Sintética |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for c in dados["custodia"]:
        conector = f"{c.get('conector_nome') or ''} {c.get('conector_versao') or ''}".strip()
        linhas.append(
            f"| {_cod(c['captura_id'])} | {_cod(c['fonte'])} | "
            f"{_md(conector) or '(não registrado)'} | {_cod(c['url_ou_arquivo'])} | "
            f"{_md(c['coletado_em'])} | {_cod(c['sha256_registrado'])} | "
            f"{_sim_nao(c['integra'])} | {'SIM' if c.get('simulado') else 'não'} |"
        )

    aud = dados["auditoria"]
    linhas += [
        "",
        "## Trilha de auditoria",
        "",
        f"Cadeia íntegra: {_sim_nao(aud['integra'])} ({int(aud['entradas'])} entradas no total"
        + (
            f"; primeira quebra na entrada {_md(aud['primeira_quebra'])}: {_md(aud['motivo'])}"
            if not aud["integra"]
            else ""
        )
        + ")",
        "",
    ]
    if aud.get("trilha_restrita"):
        linhas.append(
            "As entradas da trilha deste caso são restritas aos perfis delegado e admin "
            f"({int(aud.get('entradas_do_caso_total') or 0)} entradas registradas)."
        )
    else:
        entradas = aud["entradas_do_caso"]
        total = int(aud.get("entradas_do_caso_total") or len(entradas))
        if total > len(entradas):
            linhas += [f"Mostrando as {len(entradas)} entradas mais recentes de {total}.", ""]
        linhas += [
            "| Seq | Quando | Usuário | Ação | Justificativa | Hash |",
            "|---|---|---|---|---|---|",
        ]
        for ent in entradas:
            linhas.append(
                f"| {int(ent['seq'])} | {_md(ent['quando'])} | {_md(ent['usuario'])} | "
                f"{_cod(ent['acao'])} | {_md(ent['justificativa'] or '')} | "
                f"{_cod(str(ent['hash'])[:16] + '…')} |"
            )
    linhas.append("")
    return "\n".join(linhas)
