"""Rotas da trilha de auditoria (RNF02)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from buscador import auditoria
from buscador.api import esquemas
from buscador.api.deps import MAX_IDENTIFICADOR, Contexto, contexto, exigir_perfil

roteador = APIRouter(prefix="/auditoria", tags=["auditoria"])


@roteador.get(
    "",
    response_model=list[esquemas.AuditoriaSaida],
    dependencies=[Depends(exigir_perfil("delegado", "admin"))],
)
def listar_auditoria(
    caso_id: str | None = Query(default=None, max_length=MAX_IDENTIFICADOR),
    limite: int = Query(default=500, ge=1, le=1000),
    deslocamento: int = Query(default=0, ge=0),
    ctx: Contexto = Depends(contexto),
):
    entradas = auditoria.listar(ctx.sessao, caso_id, limite=limite, deslocamento=deslocamento)
    ctx.auditar(
        "auditoria.listar",
        caso_id,
        {"entradas": len(entradas), "limite": limite, "deslocamento": deslocamento},
    )
    ctx.concluir()
    return entradas


@roteador.get("/verificar", response_model=esquemas.VerificacaoAuditoria)
def verificar_auditoria(ctx: Contexto = Depends(contexto)):
    resultado = auditoria.verificar(ctx.sessao, ctx.chave_auditoria, ctx.ancora)
    ctx.auditar(
        "auditoria.verificar",
        None,
        {
            "integra": resultado.integra,
            "entradas": resultado.entradas,
            "primeira_quebra": resultado.primeira_quebra,
        },
    )
    ctx.concluir()
    return {
        "integra": resultado.integra,
        "entradas": resultado.entradas,
        "primeira_quebra": resultado.primeira_quebra,
        "motivo": resultado.motivo,
        "ancora_seq": resultado.ancora_seq,
    }
