"""Rotas da cadeia de custódia (RF07)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status

from buscador import comparacao
from buscador.api import esquemas
from buscador.api.deps import MAX_IDENTIFICADOR, Contexto, contexto
from buscador.modelos import Captura

roteador = APIRouter(prefix="/capturas", tags=["custodia"])


def _obter(ctx: Contexto, captura_id: str) -> Captura:
    captura = ctx.sessao.get(Captura, captura_id) if len(captura_id) <= MAX_IDENTIFICADOR else None
    if captura is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Captura {captura_id} não encontrada.")
    # Acesso por caso (H23): a captura só é vista por membro do caso a que pertence.
    ctx.caso(captura.caso_id)
    return captura


@roteador.get("/{captura_id}", response_model=esquemas.CapturaSaida)
def ver_captura(captura_id: str, ctx: Contexto = Depends(contexto)):
    captura = _obter(ctx, captura_id)
    ctx.auditar("captura.ver", captura.caso_id, {"captura_id": captura_id})
    ctx.concluir()
    return {
        "id": captura.id,
        "caso_id": captura.caso_id,
        "fonte": captura.fonte,
        "url_ou_arquivo": captura.url_ou_arquivo,
        "coletado_em": captura.coletado_em,
        "sha256": captura.sha256,
        "metadados": captura.metadados or {},
        "tamanho_bytes": captura.tamanho_bytes,
        "conector_nome": captura.conector_nome or "",
        "conector_versao": captura.conector_versao or "",
        "coleta_id": captura.coleta_id,
    }


@roteador.get("/{captura_id}/comparar", response_model=esquemas.ComparacaoCaptura)
def comparar_captura(
    captura_id: str,
    com: str | None = Query(default=None, max_length=MAX_IDENTIFICADOR),
    ctx: Contexto = Depends(contexto),
):
    """Compara a captura com outra da mesma origem (RF07, H14). Sem ``com``, a anterior é a
    captura mais recente do mesmo caso, fonte e origem, feita antes desta."""
    captura = _obter(ctx, captura_id)
    if com is None:
        anterior = comparacao.captura_anterior(ctx.sessao, captura)
        if anterior is None:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                "Não há captura anterior da mesma origem neste caso para comparar.",
            )
    else:
        anterior = ctx.sessao.get(Captura, com)
        # Captura de outro caso responde como inexistente: não confirma que ela existe.
        if anterior is None or anterior.caso_id != captura.caso_id or anterior.id == captura.id:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, f"Captura {com} não encontrada neste caso."
            )
        if anterior.fonte != captura.fonte:
            raise HTTPException(422, "Só é possível comparar capturas da mesma fonte.")
    resultado = comparacao.comparar_capturas(ctx.custodia, captura, anterior)
    ctx.auditar(
        "captura.comparar",
        captura.caso_id,
        {
            "captura_id": captura.id,
            "anterior_id": anterior.id,
            "mesmo_hash": resultado["mesmo_hash"],
            "campos_alterados": resultado["campos_alterados"],
        },
    )
    ctx.concluir()
    return resultado


@roteador.get("/{captura_id}/verificar", response_model=esquemas.VerificacaoCaptura)
def verificar_captura(captura_id: str, ctx: Contexto = Depends(contexto)):
    captura = _obter(ctx, captura_id)
    resultado = ctx.custodia.verificar(captura)
    ctx.auditar(
        "captura.verificar",
        captura.caso_id,
        {"captura_id": captura_id, "integra": resultado.integra},
    )
    ctx.concluir()
    return {
        "integra": resultado.integra,
        "sha256_registrado": resultado.sha256_registrado,
        "sha256_recalculado": resultado.sha256_recalculado,
        "arquivo_encontrado": resultado.arquivo_encontrado,
        "cifrada": resultado.cifrada,
    }
