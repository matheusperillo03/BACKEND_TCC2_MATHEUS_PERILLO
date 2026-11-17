"""Exportação do grafo do caso em formatos abertos (RNF09, H38)."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response

from buscador import exportacao
from buscador.api.deps import Contexto, contexto

roteador = APIRouter(prefix="/casos", tags=["exportacao"])


@roteador.get(
    "/{caso_id}/exportacao",
    responses={
        200: {
            "content": {
                "application/graphml+xml": {},
                "text/csv": {},
                "application/zip": {},
            }
        }
    },
)
def exportar_grafo(
    caso_id: str,
    formato: Literal["graphml", "csv_entidades", "csv_vinculos", "zip"] = Query(default="zip"),
    ctx: Contexto = Depends(contexto),
):
    """Grafo do caso em GraphML, CSV de entidades, CSV de vínculos ou ZIP com os três. Cada item
    leva o ID e o SHA-256 da captura de origem. A exportação é uma consulta aos vínculos do caso:
    é recusada depois do prazo de retenção e fica na trilha de auditoria, com o SHA-256 do
    arquivo entregue."""
    caso = ctx.caso_aberto(caso_id, "exportacao.gerar")
    arquivo, dados = exportacao.gerar(ctx.sessao, caso, formato)
    ctx.auditar(
        "exportacao.gerar",
        caso_id,
        {
            "formato": formato,
            "arquivo": arquivo.nome,
            "sha256": arquivo.sha256,
            "tamanho_bytes": len(arquivo.conteudo),
            "entidades": len(dados.entidades),
            "vinculos": len(dados.vinculos),
        },
    )
    ctx.concluir()
    nome = arquivo.nome if formato == "zip" else f"caso-{caso.id}-{arquivo.nome}"
    return Response(
        arquivo.conteudo,
        media_type=arquivo.tipo_mime,
        headers={
            "Content-Disposition": f'attachment; filename="{nome}"',
            "X-Conteudo-SHA256": arquivo.sha256,
            "Cache-Control": "no-store",
        },
    )
