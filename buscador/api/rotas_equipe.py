"""Trabalho em equipe no caso: membros (RNF01, H23), anotações e tarefas (RF08, H37).

Tudo passa por ``ctx.caso``/``ctx.caso_aberto``: só membro vê, só investigador ou responsável
altera, só o responsável gerencia membros, e depois do prazo de retenção (H17) o caso fica só
para leitura (novas anotações e mudanças de tarefa são recusadas e a recusa é auditada).
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, select

from buscador import retencao
from buscador.acesso import servico as acesso
from buscador.api import esquemas_h3 as esquemas
from buscador.api.deps import MAX_IDENTIFICADOR, Contexto, contexto
from buscador.modelos import (
    Alerta,
    Anotacao,
    CasoMembro,
    Entidade,
    Tarefa,
    TarefaEvento,
    iso_utc,
)

roteador = APIRouter(prefix="/casos", tags=["equipe"])

HTTP_422 = 422


# ---- Membros ------------------------------------------------------------------------------------


def _membro_para_dict(m: CasoMembro) -> dict:
    return {
        "usuario": m.usuario,
        "papel": m.papel,
        "adicionado_por": m.adicionado_por,
        "adicionado_em": m.adicionado_em,
    }


def _membros(ctx: Contexto, caso_id: str) -> list[CasoMembro]:
    return list(
        ctx.sessao.execute(
            select(CasoMembro)
            .where(CasoMembro.caso_id == caso_id)
            .order_by(CasoMembro.adicionado_em, CasoMembro.id)
        ).scalars()
    )


def _usuario_conhecido(request: Request, ctx: Contexto, usuario: str) -> bool:
    """Conta real ativa ou, no modo de desenvolvimento, usuário de um token de TOKENS_DEV."""
    conta = acesso.obter_conta(ctx.sessao, usuario)
    if conta is not None:
        return bool(conta.ativo)
    return any(u.usuario == usuario for u in request.app.state.tokens.values())


def _responsaveis(ctx: Contexto, caso_id: str) -> int:
    return ctx.sessao.execute(
        select(func.count())
        .select_from(CasoMembro)
        .where(CasoMembro.caso_id == caso_id, CasoMembro.papel == "responsavel")
    ).scalar_one()


@roteador.get("/{caso_id}/membros", response_model=list[esquemas.MembroSaida])
def listar_membros(caso_id: str, ctx: Contexto = Depends(contexto)):
    ctx.caso(caso_id)
    membros = _membros(ctx, caso_id)
    ctx.auditar("membro.listar", caso_id, {"membros": len(membros)})
    ctx.concluir()
    return [_membro_para_dict(m) for m in membros]


@roteador.post("/{caso_id}/membros", response_model=list[esquemas.MembroSaida])
def definir_membro(
    caso_id: str, corpo: esquemas.MembroEntrada, request: Request, ctx: Contexto = Depends(contexto)
):
    """Acrescenta um membro ao caso ou muda o papel de quem já é membro. Só o responsável."""
    ctx.caso(caso_id, "responsavel")
    usuario = corpo.usuario.lower()
    if not _usuario_conhecido(request, ctx, usuario):
        raise HTTPException(HTTP_422, f"Não há usuário ativo com o login '{usuario}'.")
    membro = (
        ctx.sessao.execute(
            select(CasoMembro)
            .where(CasoMembro.caso_id == caso_id, CasoMembro.usuario == usuario)
            .with_for_update()
        )
        .scalars()
        .first()
    )
    papel_anterior = membro.papel if membro else None
    if membro is None:
        membro = CasoMembro(
            caso_id=caso_id,
            usuario=usuario,
            papel=corpo.papel,
            adicionado_por=ctx.usuario.usuario,
        )
        ctx.sessao.add(membro)
    elif membro.papel != corpo.papel:
        if membro.papel == "responsavel" and _responsaveis(ctx, caso_id) <= 1:
            raise HTTPException(
                HTTP_422,
                "O caso precisa de ao menos um responsável: defina outro antes de mudar este.",
            )
        membro.papel = corpo.papel
    ctx.sessao.flush()
    ctx.auditar(
        "membro.definir",
        caso_id,
        {"membro": usuario, "papel": corpo.papel, "papel_anterior": papel_anterior},
    )
    ctx.concluir()
    return [_membro_para_dict(m) for m in _membros(ctx, caso_id)]


@roteador.post("/{caso_id}/membros/{usuario}/remover", response_model=list[esquemas.MembroSaida])
def remover_membro(caso_id: str, usuario: str, ctx: Contexto = Depends(contexto)):
    """Tira o membro do caso (ele deixa de ver o caso na hora). Só o responsável."""
    ctx.caso(caso_id, "responsavel")
    membro = None
    if len(usuario) <= 100:
        membro = (
            ctx.sessao.execute(
                select(CasoMembro)
                .where(CasoMembro.caso_id == caso_id, CasoMembro.usuario == usuario.lower())
                .with_for_update()
            )
            .scalars()
            .first()
        )
    if membro is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Este usuário não é membro do caso.")
    if membro.papel == "responsavel" and _responsaveis(ctx, caso_id) <= 1:
        raise HTTPException(
            HTTP_422, "O caso precisa de ao menos um responsável: defina outro antes de remover."
        )
    papel = membro.papel
    ctx.sessao.delete(membro)
    ctx.sessao.flush()
    ctx.auditar("membro.remover", caso_id, {"membro": membro.usuario, "papel": papel})
    ctx.concluir()
    return [_membro_para_dict(m) for m in _membros(ctx, caso_id)]


# ---- Anotações ----------------------------------------------------------------------------------


def _rotulo_do_alvo(ctx: Contexto, caso_id: str, alvo_tipo: str, alvo_id: str) -> str | None:
    """Rótulo do alvo, ou ``None`` se ele não existe NESTE caso."""
    if len(alvo_id) > MAX_IDENTIFICADOR:
        return None
    if alvo_tipo == "entidade":
        entidade = ctx.sessao.get(Entidade, alvo_id)
        return entidade.rotulo if entidade is not None and entidade.caso_id == caso_id else None
    alerta = ctx.sessao.get(Alerta, alvo_id)
    if alerta is None or alerta.caso_id != caso_id:
        return None
    return f"[{alerta.tipologia}] {alerta.titulo}"


def _anotacao_para_dict(a: Anotacao, rotulo: str | None) -> dict:
    return {
        "id": a.id,
        "alvo_tipo": a.alvo_tipo,
        "alvo_id": a.alvo_id,
        "alvo_rotulo": rotulo,
        "autor": a.autor,
        "criado_em": a.criado_em,
        "texto": a.texto,
    }


@roteador.get("/{caso_id}/anotacoes", response_model=list[esquemas.AnotacaoSaida])
def listar_anotacoes(
    caso_id: str,
    alvo_tipo: Literal["entidade", "alerta"] | None = Query(default=None),
    alvo_id: str | None = Query(default=None, max_length=MAX_IDENTIFICADOR),
    ctx: Contexto = Depends(contexto),
):
    """Anotações do caso, da mais recente para a mais antiga; com ``alvo_tipo`` e ``alvo_id``,
    só as de uma entidade ou de um alerta."""
    ctx.caso(caso_id)
    consulta = select(Anotacao).where(Anotacao.caso_id == caso_id)
    if alvo_tipo:
        consulta = consulta.where(Anotacao.alvo_tipo == alvo_tipo)
    if alvo_id:
        consulta = consulta.where(Anotacao.alvo_id == alvo_id)
    anotacoes = list(
        ctx.sessao.execute(consulta.order_by(Anotacao.criado_em.desc(), Anotacao.id)).scalars()
    )
    rotulos: dict[tuple[str, str], str | None] = {}
    for a in anotacoes:
        marca = (a.alvo_tipo, a.alvo_id)
        if marca not in rotulos:
            rotulos[marca] = _rotulo_do_alvo(ctx, caso_id, a.alvo_tipo, a.alvo_id)
    ctx.auditar(
        "anotacao.listar",
        caso_id,
        {"alvo_tipo": alvo_tipo, "alvo_id": alvo_id, "anotacoes": len(anotacoes)},
    )
    ctx.concluir()
    return [_anotacao_para_dict(a, rotulos[(a.alvo_tipo, a.alvo_id)]) for a in anotacoes]


@roteador.post(
    "/{caso_id}/anotacoes",
    response_model=esquemas.AnotacaoSaida,
    status_code=status.HTTP_201_CREATED,
)
def anotar(caso_id: str, corpo: esquemas.AnotacaoEntrada, ctx: Contexto = Depends(contexto)):
    """Anota uma entidade ou um alerta do caso. A anotação é só de acréscimo: não se edita nem
    se apaga; para corrigir, anota-se de novo."""
    ctx.caso_aberto(caso_id, "anotacao.criar")
    rotulo = _rotulo_do_alvo(ctx, caso_id, corpo.alvo_tipo, corpo.alvo_id)
    if rotulo is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"{corpo.alvo_tipo.capitalize()} não encontrado(a) no caso."
        )
    anotacao = Anotacao(
        caso_id=caso_id,
        alvo_tipo=corpo.alvo_tipo,
        alvo_id=corpo.alvo_id,
        autor=ctx.usuario.usuario,
        criado_em=iso_utc(),
        texto=corpo.texto,
    )
    ctx.sessao.add(anotacao)
    ctx.sessao.flush()
    ctx.auditar(
        "anotacao.criar",
        caso_id,
        {
            "anotacao_id": anotacao.id,
            "alvo_tipo": anotacao.alvo_tipo,
            "alvo_id": anotacao.alvo_id,
            "caracteres": len(anotacao.texto),
        },
    )
    ctx.concluir()
    return _anotacao_para_dict(anotacao, rotulo)


# ---- Tarefas ------------------------------------------------------------------------------------

SITUACOES_ABERTAS = ("aberta", "em_andamento")


def _tarefa_para_dict(t: Tarefa) -> dict:
    atrasada = bool(
        t.prazo and t.situacao in SITUACOES_ABERTAS and t.prazo < retencao.hoje_utc().isoformat()
    )
    return {
        "id": t.id,
        "titulo": t.titulo,
        "descricao": t.descricao or "",
        "responsavel": t.responsavel,
        "situacao": t.situacao,
        "prazo": t.prazo,
        "atrasada": atrasada,
        "criado_por": t.criado_por,
        "criado_em": t.criado_em,
        "atualizado_em": t.atualizado_em,
        "historico": [
            {"usuario": e.usuario, "quando": e.quando, "alteracoes": e.alteracoes or {}}
            for e in t.eventos
        ],
    }


def _obter_tarefa(ctx: Contexto, caso_id: str, tarefa_id: str, *, travar: bool = False) -> Tarefa:
    tarefa = None
    if len(tarefa_id) <= MAX_IDENTIFICADOR:
        consulta = select(Tarefa).where(Tarefa.id == tarefa_id, Tarefa.caso_id == caso_id)
        if travar:
            consulta = consulta.with_for_update()
        tarefa = ctx.sessao.execute(consulta).scalars().first()
    if tarefa is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"Tarefa {tarefa_id} não encontrada no caso."
        )
    return tarefa


def _validar_responsavel(ctx: Contexto, caso_id: str, responsavel: str | None) -> str | None:
    if responsavel is None or not responsavel.strip():
        return None
    responsavel = responsavel.strip().lower()
    membro = ctx.sessao.execute(
        select(CasoMembro.id).where(
            CasoMembro.caso_id == caso_id, CasoMembro.usuario == responsavel
        )
    ).first()
    if membro is None:
        raise HTTPException(HTTP_422, "O responsável pela tarefa precisa ser membro do caso.")
    return responsavel


@roteador.get("/{caso_id}/tarefas", response_model=list[esquemas.TarefaSaida])
def listar_tarefas(
    caso_id: str,
    situacao: esquemas.SituacaoTarefa | None = Query(default=None),
    ctx: Contexto = Depends(contexto),
):
    ctx.caso(caso_id)
    consulta = select(Tarefa).where(Tarefa.caso_id == caso_id)
    if situacao:
        consulta = consulta.where(Tarefa.situacao == situacao)
    tarefas = list(ctx.sessao.execute(consulta.order_by(Tarefa.criado_em.desc())).scalars())
    # Abertas primeiro; dentro de cada grupo, pelo prazo (sem prazo por último).
    tarefas.sort(key=lambda t: (t.situacao not in SITUACOES_ABERTAS, t.prazo or "9999-12-31"))
    ctx.auditar(
        "tarefa.listar",
        caso_id,
        {"tarefas": len(tarefas), "abertas": sum(t.situacao in SITUACOES_ABERTAS for t in tarefas)},
    )
    ctx.concluir()
    return [_tarefa_para_dict(t) for t in tarefas]


@roteador.post(
    "/{caso_id}/tarefas", response_model=esquemas.TarefaSaida, status_code=status.HTTP_201_CREATED
)
def criar_tarefa(caso_id: str, corpo: esquemas.TarefaEntrada, ctx: Contexto = Depends(contexto)):
    ctx.caso_aberto(caso_id, "tarefa.criar")
    agora = iso_utc()
    tarefa = Tarefa(
        caso_id=caso_id,
        titulo=corpo.titulo,
        descricao=corpo.descricao,
        responsavel=_validar_responsavel(ctx, caso_id, corpo.responsavel),
        situacao="aberta",
        prazo=corpo.prazo.isoformat() if corpo.prazo else None,
        criado_por=ctx.usuario.usuario,
        criado_em=agora,
        atualizado_em=agora,
    )
    ctx.sessao.add(tarefa)
    ctx.sessao.flush()
    inicial = {
        "titulo": tarefa.titulo,
        "responsavel": tarefa.responsavel,
        "situacao": tarefa.situacao,
        "prazo": tarefa.prazo,
    }
    tarefa.eventos.append(
        TarefaEvento(
            caso_id=caso_id,
            usuario=ctx.usuario.usuario,
            quando=agora,
            alteracoes={campo: {"de": None, "para": valor} for campo, valor in inicial.items()},
        )
    )
    ctx.sessao.flush()
    ctx.auditar("tarefa.criar", caso_id, {"tarefa_id": tarefa.id, **inicial})
    ctx.concluir()
    return _tarefa_para_dict(tarefa)


@roteador.get("/{caso_id}/tarefas/{tarefa_id}", response_model=esquemas.TarefaSaida)
def ver_tarefa(caso_id: str, tarefa_id: str, ctx: Contexto = Depends(contexto)):
    ctx.caso(caso_id)
    tarefa = _obter_tarefa(ctx, caso_id, tarefa_id)
    ctx.auditar("tarefa.ver", caso_id, {"tarefa_id": tarefa.id})
    ctx.concluir()
    return _tarefa_para_dict(tarefa)


@roteador.post("/{caso_id}/tarefas/{tarefa_id}", response_model=esquemas.TarefaSaida)
def alterar_tarefa(
    caso_id: str,
    tarefa_id: str,
    corpo: esquemas.TarefaAlteracao,
    ctx: Contexto = Depends(contexto),
):
    """Altera os campos enviados (título, descrição, responsável, situação, prazo). Cada
    alteração acrescenta uma linha ao histórico da tarefa, com o valor anterior e o novo."""
    ctx.caso_aberto(caso_id, "tarefa.alterar")
    tarefa = _obter_tarefa(ctx, caso_id, tarefa_id, travar=True)
    enviados = corpo.model_fields_set
    novos: dict[str, object] = {}
    if "titulo" in enviados and corpo.titulo is not None:
        novos["titulo"] = corpo.titulo
    if "descricao" in enviados and corpo.descricao is not None:
        novos["descricao"] = corpo.descricao
    if "situacao" in enviados and corpo.situacao is not None:
        novos["situacao"] = corpo.situacao
    if "responsavel" in enviados:
        novos["responsavel"] = _validar_responsavel(ctx, caso_id, corpo.responsavel)
    if "prazo" in enviados:
        novos["prazo"] = corpo.prazo.isoformat() if corpo.prazo else None
    alteracoes = {
        campo: {"de": getattr(tarefa, campo), "para": valor}
        for campo, valor in novos.items()
        if getattr(tarefa, campo) != valor
    }
    if not alteracoes:
        raise HTTPException(HTTP_422, "Nenhum campo foi alterado.")
    agora = iso_utc()
    for campo, mudanca in alteracoes.items():
        setattr(tarefa, campo, mudanca["para"])
    tarefa.atualizado_em = agora
    if "descricao" in alteracoes:
        # O histórico registra que a descrição mudou, sem repetir o texto inteiro duas vezes.
        alteracoes["descricao"] = {
            "de": f"({len(alteracoes['descricao']['de'] or '')} caracteres)",
            "para": f"({len(alteracoes['descricao']['para'] or '')} caracteres)",
        }
    tarefa.eventos.append(
        TarefaEvento(
            caso_id=caso_id, usuario=ctx.usuario.usuario, quando=agora, alteracoes=alteracoes
        )
    )
    ctx.sessao.flush()
    ctx.auditar("tarefa.alterar", caso_id, {"tarefa_id": tarefa.id, "alteracoes": alteracoes})
    ctx.concluir()
    return _tarefa_para_dict(tarefa)
