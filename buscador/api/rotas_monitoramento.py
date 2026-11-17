"""Rotas do monitoramento contínuo (RF06, H18) e dos avisos de novidade."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select

from buscador import monitoramento
from buscador.api import esquemas
from buscador.api import monitoramento as servico
from buscador.api.deps import MAX_IDENTIFICADOR, Contexto, contexto
from buscador.modelos import Aviso, Entidade, Monitoramento, MonitoramentoExecucao, iso_utc
from buscador.seguranca import sanitizar_json

roteador = APIRouter(prefix="/casos", tags=["monitoramento"])


def _execucao_para_dict(e: MonitoramentoExecucao) -> dict:
    return {
        "id": e.id,
        "monitoramento_id": e.monitoramento_id,
        "origem": e.origem,
        "usuario": e.usuario,
        "iniciado_em": e.iniciado_em,
        "concluido_em": e.concluido_em,
        "situacao": e.situacao,
        "coletas": e.coletas or [],
        "avisos_gerados": e.avisos_gerados,
        "motivo": e.motivo or "",
    }


def _monitoramento_para_dict(ctx: Contexto, m: Monitoramento) -> dict:
    entidade = ctx.sessao.get(Entidade, m.entidade_id)
    origens = monitoramento.coletas_de_origem(ctx.sessao, entidade) if entidade else []
    ultima = m.execucoes[-1] if m.execucoes else None
    return {
        "id": m.id,
        "caso_id": m.caso_id,
        "entidade_id": m.entidade_id,
        "entidade_rotulo": entidade.rotulo if entidade else None,
        "entidade_tipo": entidade.tipo if entidade else None,
        "entidade_identificador": entidade.identificador if entidade else None,
        "periodicidade_horas": m.periodicidade_horas,
        "ativo": bool(m.ativo),
        "motivo_inativo": m.motivo_inativo or "",
        "justificativa": m.justificativa or "",
        "criado_por": m.criado_por,
        "criado_em": m.criado_em,
        "proxima_execucao_em": m.proxima_execucao_em,
        "ultima_execucao_em": m.ultima_execucao_em,
        "coletas_de_origem": [
            {"coleta_id": c.id, "fonte": c.fonte, "iniciado_em": c.iniciado_em} for c in origens
        ],
        "ultima_execucao": _execucao_para_dict(ultima) if ultima else None,
    }


def _aviso_para_dict(a: Aviso, rotulos: dict[str, str]) -> dict:
    return {
        "id": a.id,
        "caso_id": a.caso_id,
        "monitoramento_id": a.monitoramento_id,
        "execucao_id": a.execucao_id,
        "entidade_id": a.entidade_id,
        "entidade_rotulo": rotulos.get(a.entidade_id),
        "tipo": a.tipo,
        "titulo": a.titulo,
        "descricao": a.descricao,
        "detalhes": sanitizar_json(a.detalhes or {}),
        "captura_ids": a.captura_ids or [],
        "criado_em": a.criado_em,
        "lido_em": a.lido_em,
        "lido_por": a.lido_por,
    }


def _rotulos(ctx: Contexto, avisos: list[Aviso]) -> dict[str, str]:
    ids = {a.entidade_id for a in avisos}
    if not ids:
        return {}
    linhas = ctx.sessao.execute(select(Entidade.id, Entidade.rotulo).where(Entidade.id.in_(ids)))
    return {entidade_id: rotulo for entidade_id, rotulo in linhas}


def _obter(ctx: Contexto, caso_id: str, monitoramento_id: str) -> Monitoramento:
    m = (
        ctx.sessao.get(Monitoramento, monitoramento_id)
        if len(monitoramento_id) <= MAX_IDENTIFICADOR
        else None
    )
    if m is None or m.caso_id != caso_id:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"Monitoramento {monitoramento_id} não encontrado no caso."
        )
    return m


@roteador.get("/{caso_id}/monitoramentos", response_model=list[esquemas.MonitoramentoSaida])
def listar_monitoramentos(caso_id: str, ctx: Contexto = Depends(contexto)):
    ctx.caso(caso_id)
    itens = (
        ctx.sessao.execute(
            select(Monitoramento)
            .where(Monitoramento.caso_id == caso_id)
            .order_by(Monitoramento.criado_em)
        )
        .scalars()
        .all()
    )
    saida = [_monitoramento_para_dict(ctx, m) for m in itens]
    ctx.auditar(
        "monitoramento.listar",
        caso_id,
        {"total": len(itens), "ativos": sum(m.ativo for m in itens)},
    )
    ctx.concluir()
    return saida


@roteador.post(
    "/{caso_id}/monitoramentos",
    response_model=esquemas.MonitoramentoSaida,
    status_code=status.HTTP_201_CREATED,
)
def criar_monitoramento(
    caso_id: str, corpo: esquemas.MonitoramentoEntrada, ctx: Contexto = Depends(contexto)
):
    """Marca uma entidade do caso para acompanhamento. A justificativa da requisição fica no
    monitoramento e acompanha cada execução automática na auditoria. Marcar de novo a mesma
    entidade reativa o monitoramento e atualiza a periodicidade."""
    ctx.caso_aberto(caso_id, "monitoramento.criar")
    minimo = ctx.config.monitoramento_periodicidade_minima_horas
    if corpo.periodicidade_horas < minimo:
        raise HTTPException(422, f"A periodicidade mínima é de {minimo} hora(s).")
    entidade = ctx.sessao.get(Entidade, corpo.entidade_id)
    if entidade is None or entidade.caso_id != caso_id:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"Entidade {corpo.entidade_id} não encontrada no caso."
        )
    m = (
        ctx.sessao.execute(
            select(Monitoramento).where(
                Monitoramento.caso_id == caso_id, Monitoramento.entidade_id == entidade.id
            )
        )
        .scalars()
        .first()
    )
    novo = m is None
    if m is None:
        m = Monitoramento(
            caso_id=caso_id,
            entidade_id=entidade.id,
            criado_por=ctx.usuario.usuario,
            periodicidade_horas=corpo.periodicidade_horas,
            proxima_execucao_em="",
        )
        ctx.sessao.add(m)
    m.periodicidade_horas = corpo.periodicidade_horas
    m.ativo = True
    m.motivo_inativo = ""
    m.justificativa = ctx.justificativa
    m.proxima_execucao_em = servico.proxima_execucao(corpo.periodicidade_horas)
    ctx.sessao.flush()
    saida = _monitoramento_para_dict(ctx, m)
    ctx.auditar(
        "monitoramento.criar" if novo else "monitoramento.reativar",
        caso_id,
        {
            "monitoramento_id": m.id,
            "entidade_id": entidade.id,
            "entidade_tipo": entidade.tipo,
            "periodicidade_horas": m.periodicidade_horas,
            "coletas_de_origem": len(saida["coletas_de_origem"]),
        },
    )
    ctx.concluir()
    return saida


@roteador.post(
    "/{caso_id}/monitoramentos/{monitoramento_id}/situacao",
    response_model=esquemas.MonitoramentoSaida,
)
def alterar_monitoramento(
    caso_id: str,
    monitoramento_id: str,
    corpo: esquemas.MonitoramentoSituacaoEntrada,
    ctx: Contexto = Depends(contexto),
):
    """Ativa ou desativa o monitoramento e, opcionalmente, muda a periodicidade."""
    # Desativar é permitido mesmo com o caso vencido; reativar, não.
    if corpo.ativo:
        ctx.caso_aberto(caso_id, "monitoramento.alterar")
    else:
        ctx.caso(caso_id)
    m = _obter(ctx, caso_id, monitoramento_id)
    if corpo.periodicidade_horas is not None:
        minimo = ctx.config.monitoramento_periodicidade_minima_horas
        if corpo.periodicidade_horas < minimo:
            raise HTTPException(422, f"A periodicidade mínima é de {minimo} hora(s).")
        m.periodicidade_horas = corpo.periodicidade_horas
    m.ativo = corpo.ativo
    m.motivo_inativo = "" if corpo.ativo else f"Desativado por {ctx.usuario.usuario}."
    if corpo.ativo:
        m.proxima_execucao_em = servico.proxima_execucao(m.periodicidade_horas)
    ctx.sessao.flush()
    saida = _monitoramento_para_dict(ctx, m)
    ctx.auditar(
        "monitoramento.alterar",
        caso_id,
        {
            "monitoramento_id": m.id,
            "ativo": m.ativo,
            "periodicidade_horas": m.periodicidade_horas,
        },
    )
    ctx.concluir()
    return saida


@roteador.post(
    "/{caso_id}/monitoramentos/{monitoramento_id}/executar",
    response_model=esquemas.ExecucaoMonitoramentoSaida,
)
def executar_monitoramento(caso_id: str, monitoramento_id: str, ctx: Contexto = Depends(contexto)):
    """Executa o monitoramento agora (o agendador faz o mesmo na periodicidade escolhida)."""
    ctx.caso_aberto(caso_id, "monitoramento.executar")
    m = _obter(ctx, caso_id, monitoramento_id)
    if not m.ativo:
        raise HTTPException(status.HTTP_409_CONFLICT, "O monitoramento está desativado.")
    execucao, avisos = servico.executar(ctx, m, "manual")
    rotulos = _rotulos(ctx, avisos)
    return {
        **_execucao_para_dict(execucao),
        "avisos": [_aviso_para_dict(a, rotulos) for a in avisos],
    }


@roteador.get("/{caso_id}/avisos", response_model=list[esquemas.AvisoSaida])
def listar_avisos(
    caso_id: str,
    nao_lidos: bool = Query(default=False),
    ctx: Contexto = Depends(contexto),
):
    """Avisos de novidade do caso, do mais recente para o mais antigo."""
    ctx.caso(caso_id)
    consulta = select(Aviso).where(Aviso.caso_id == caso_id)
    if nao_lidos:
        consulta = consulta.where(Aviso.lido_em.is_(None))
    avisos = list(ctx.sessao.execute(consulta.order_by(Aviso.criado_em.desc())).scalars())
    rotulos = _rotulos(ctx, avisos)
    ctx.auditar(
        "aviso.listar",
        caso_id,
        {"avisos": len(avisos), "nao_lidos": sum(a.lido_em is None for a in avisos)},
    )
    ctx.concluir()
    return [_aviso_para_dict(a, rotulos) for a in avisos]


@roteador.post("/{caso_id}/avisos/{aviso_id}/lido", response_model=esquemas.AvisoSaida)
def marcar_aviso_lido(caso_id: str, aviso_id: str, ctx: Contexto = Depends(contexto)):
    ctx.caso(caso_id)
    aviso = ctx.sessao.get(Aviso, aviso_id) if len(aviso_id) <= MAX_IDENTIFICADOR else None
    if aviso is None or aviso.caso_id != caso_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Aviso {aviso_id} não encontrado no caso.")
    if aviso.lido_em is None:
        aviso.lido_em = iso_utc()
        aviso.lido_por = ctx.usuario.usuario
    ctx.auditar("aviso.marcar_lido", caso_id, {"aviso_id": aviso.id, "tipo": aviso.tipo})
    ctx.concluir()
    return _aviso_para_dict(aviso, _rotulos(ctx, [aviso]))
