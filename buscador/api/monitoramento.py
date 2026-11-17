"""Execução do monitoramento contínuo (RF06, H18): refaz as coletas de origem da entidade pelo
mesmo caminho das rotas (linha em ``coletas``, custódia e auditoria), compara com a coleta
anterior (H14) e grava os avisos de novidade.

Usado pela rota de execução manual e pelo agendador interno. Cada execução fica em
``monitoramento_execucoes`` e na trilha de auditoria (``monitoramento.executar``). Caso com o
prazo de retenção vencido não é monitorado (H17): a execução é recusada, o monitoramento é
desativado e a recusa também vai para a auditoria.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException

from buscador import monitoramento, retencao
from buscador.api.deps import Contexto
from buscador.api.rotas_casos import _coletar_registrando
from buscador.conectores import CONECTORES
from buscador.modelos import (
    Aviso,
    Caso,
    Coleta,
    Entidade,
    Monitoramento,
    MonitoramentoExecucao,
    iso_utc,
)
from buscador.seguranca import texto_de_linha

registro = logging.getLogger(__name__)

MOTIVO_RETENCAO = "Prazo de retenção do caso vencido: o monitoramento foi encerrado."


def proxima_execucao(periodicidade_horas: int, agora: datetime | None = None) -> str:
    return iso_utc((agora or datetime.now(UTC)) + timedelta(hours=periodicidade_horas))


def executar(
    ctx: Contexto, monitorado: Monitoramento, origem: str
) -> tuple[MonitoramentoExecucao, list[Aviso]]:
    """Roda uma execução do monitoramento e devolve a execução e os avisos criados."""
    sessao = ctx.sessao
    caso = sessao.get(Caso, monitorado.caso_id)
    entidade = sessao.get(Entidade, monitorado.entidade_id)
    execucao = MonitoramentoExecucao(
        monitoramento_id=monitorado.id,
        caso_id=monitorado.caso_id,
        origem=origem,
        usuario=ctx.usuario.usuario,
    )
    sessao.add(execucao)
    sessao.flush()
    base = {
        "monitoramento_id": monitorado.id,
        "execucao_id": execucao.id,
        "entidade_id": monitorado.entidade_id,
        "origem": origem,
        "periodicidade_horas": monitorado.periodicidade_horas,
    }

    if caso is None or entidade is None or retencao.vencida(caso):
        vencido = caso is not None and retencao.vencida(caso)
        motivo = MOTIVO_RETENCAO if vencido else "A entidade monitorada não existe mais no caso."
        execucao.situacao = "recusada_retencao" if vencido else "falha"
        execucao.motivo = motivo
        execucao.concluido_em = iso_utc()
        monitorado.ativo = False
        monitorado.motivo_inativo = motivo
        monitorado.ultima_execucao_em = execucao.concluido_em
        ctx.auditar(
            "monitoramento.retencao_recusa" if vencido else "monitoramento.falhou",
            monitorado.caso_id,
            {**base, "motivo": motivo, "retencao_ate": caso.retencao_ate if caso else None},
        )
        ctx.concluir()
        return execucao, []

    antes = monitoramento.fotografia(sessao, entidade)
    origens = monitoramento.coletas_de_origem(sessao, entidade)
    origem_ids = [(c.id, c.fonte, dict(c.parametros or {})) for c in origens]
    caso_id, entidade_id, execucao_id = caso.id, entidade.id, execucao.id
    # A próxima execução já avança aqui: se o processo cair no meio, o monitoramento não fica
    # preso num laço de novas tentativas, e outro processo não o pega de novo neste ciclo.
    monitorado.proxima_execucao_em = proxima_execucao(monitorado.periodicidade_horas)
    # A execução fica confirmada antes das recoletas (que confirmam a transação várias vezes).
    ctx.auditar("monitoramento.iniciar", caso_id, {**base, "coletas_de_origem": len(origens)})
    ctx.concluir()

    linhas: list[dict] = []
    remocoes: list[monitoramento.Novidade] = []
    for coleta_id, fonte, parametros in origem_ids:
        anterior = sessao.get(Coleta, coleta_id)
        linha = {"coleta_id": None, "recoleta_de": coleta_id, "fonte": fonte, "motivo": ""}
        if fonte not in CONECTORES or anterior is None:
            linhas.append({**linha, "situacao": "falha", "motivo": "Fonte indisponível."})
            continue
        try:
            nova, _resultado, diferenca = _coletar_registrando(
                ctx, sessao.get(Caso, caso_id), fonte, parametros, recoleta_de=anterior
            )
        except HTTPException as erro:
            # A falha já foi registrada em ``coletas`` e na auditoria por _coletar_registrando.
            linhas.append(
                {**linha, "situacao": "falha", "motivo": texto_de_linha(erro.detail)[:300]}
            )
            continue
        linha.update(
            coleta_id=nova.id,
            situacao=nova.situacao,
            motivo=nova.motivo or "",
            mesmo_conteudo=diferenca["mesmo_conteudo"] if diferenca else None,
        )
        linhas.append(linha)
        if diferenca:
            remocoes += monitoramento.remocoes_de_socios(
                sessao.get(Entidade, entidade_id), diferenca
            )

    entidade = sessao.get(Entidade, entidade_id)
    execucao = sessao.get(MonitoramentoExecucao, execucao_id)
    monitorado = sessao.get(Monitoramento, execucao.monitoramento_id)
    sessao.refresh(entidade)
    depois = monitoramento.fotografia(sessao, entidade)
    achadas = (monitoramento.novidades(entidade, antes, depois) + remocoes)[
        : monitoramento.MAX_AVISOS_POR_EXECUCAO
    ]
    avisos = [
        Aviso(
            caso_id=caso_id,
            monitoramento_id=monitorado.id,
            execucao_id=execucao.id,
            entidade_id=entidade_id,
            tipo=n.tipo,
            titulo=n.titulo,
            descricao=n.descricao,
            detalhes=n.detalhes,
            captura_ids=n.captura_ids,
        )
        for n in achadas
    ]
    sessao.add_all(avisos)

    feitas = [linha for linha in linhas if linha["situacao"] == "sucesso"]
    if not linhas:
        situacao, motivo = (
            "sem_coletas",
            "A entidade não tem coleta de origem bem-sucedida para refazer.",
        )
    elif len(feitas) == len(linhas):
        situacao, motivo = "sucesso", ""
    elif feitas:
        situacao, motivo = "parcial", "Parte das recoletas falhou ou a fonte não está configurada."
    else:
        situacao, motivo = "falha", "Nenhuma recoleta foi concluída."
    execucao.situacao = situacao
    execucao.motivo = motivo
    execucao.coletas = linhas
    execucao.avisos_gerados = len(avisos)
    execucao.concluido_em = iso_utc()
    monitorado.ultima_execucao_em = execucao.concluido_em
    monitorado.proxima_execucao_em = proxima_execucao(monitorado.periodicidade_horas)
    sessao.flush()
    ctx.auditar(
        "monitoramento.executar",
        caso_id,
        {
            **base,
            "situacao": situacao,
            "recoletas": [
                {k: linha.get(k) for k in ("coleta_id", "recoleta_de", "fonte", "situacao")}
                for linha in linhas
            ],
            "avisos": len(avisos),
            "tipos_de_aviso": sorted({a.tipo for a in avisos}),
            "proxima_execucao_em": monitorado.proxima_execucao_em,
        },
    )
    ctx.concluir()
    return execucao, avisos
