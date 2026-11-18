"""Extração assistida por IA local (RF13, RNF05, H22): execução, sugestões e decisão.

Desligada por padrão (``IA_EXTRACAO_ATIVA=0``): as rotas de execução respondem ``409`` e nada é
enviado a modelo nenhum. Ligada, só fala com o servidor LOCAL configurado.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select

from buscador.api import esquemas_h3 as esquemas
from buscador.api.deps import MAX_IDENTIFICADOR, Contexto, contexto, usuario_atual
from buscador.config import Usuario
from buscador.ia import servico
from buscador.modelos import Captura, ExtracaoIA, SugestaoIA, iso_utc

roteador = APIRouter(tags=["ia"])

HTTP_422 = 422


@roteador.get("/ia", response_model=esquemas.IaEstado)
def estado_da_ia(request: Request, _usuario: Usuario = Depends(usuario_atual)):
    """Diz se a extração por IA está ligada e com qual provedor e modelo (não é dado de caso)."""
    config = request.app.state.config
    provedor = request.app.state.provedor_ia
    return {
        "ativa": provedor is not None,
        "provedor": provedor.nome if provedor is not None else None,
        "modelo": (config.ia_modelo.strip() or None) if provedor is not None else None,
        "local": True,
        "tipos": list(servico.TIPOS),
    }


def _captura_do_caso(ctx: Contexto, caso_id: str, captura_id: str) -> Captura:
    captura = ctx.sessao.get(Captura, captura_id) if len(captura_id) <= MAX_IDENTIFICADOR else None
    if captura is None or captura.caso_id != caso_id:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"Captura {captura_id} não encontrada no caso."
        )
    return captura


def _sugestao_para_dict(s: SugestaoIA, extracao: ExtracaoIA) -> dict:
    return {
        "id": s.id,
        "extracao_id": s.extracao_id,
        "captura_id": s.captura_id,
        "captura_sha256": extracao.captura_sha256,
        "tipo": s.tipo,
        "valor": s.valor,
        "campos": s.campos or {},
        "trecho": s.trecho,
        "posicao_inicio": s.posicao_inicio,
        "posicao_fim": s.posicao_fim,
        "confianca": s.confianca,
        "situacao": s.situacao,
        "decidido_por": s.decidido_por,
        "decidido_em": s.decidido_em,
        "justificativa_decisao": s.justificativa_decisao,
        "entidade_id": s.entidade_id,
        "modelo": extracao.modelo,
        "provedor": extracao.provedor,
    }


def _extracao_para_dict(e: ExtracaoIA, com_sugestoes: bool = False) -> dict:
    return {
        "id": e.id,
        "captura_id": e.captura_id,
        "captura_sha256": e.captura_sha256,
        "provedor": e.provedor,
        "modelo": e.modelo,
        "modelo_versao": e.modelo_versao or "",
        "servidor_versao": e.servidor_versao or "",
        "parametros": e.parametros or {},
        "prompt_sha256": e.prompt_sha256 or "",
        "situacao": e.situacao,
        "motivo": e.motivo or "",
        "blocos": e.blocos,
        "itens_recebidos": e.itens_recebidos,
        "itens_aceitos": e.itens_aceitos,
        "itens_recusados": e.itens_recusados,
        "recusas": e.recusas or [],
        "metricas": e.metricas,
        "usuario": e.usuario,
        "iniciado_em": e.iniciado_em,
        "concluido_em": e.concluido_em,
        "sugestoes": [_sugestao_para_dict(s, e) for s in e.sugestoes] if com_sugestoes else [],
    }


@roteador.get("/casos/{caso_id}/capturas/{captura_id}/texto", response_model=esquemas.TextoCaptura)
def texto_da_captura(caso_id: str, captura_id: str, ctx: Contexto = Depends(contexto)):
    """O texto do documento capturado, para conferir o trecho citado ao lado do original.

    Todo CPF sai descaracterizado (``***.NNN.NNN-**`` ou ``***NNNNNN**``, com o mesmo
    comprimento), mesmo que a carga guardada na custódia o traga completo (RNF04);
    ``cpfs_descaracterizados`` diz se houve troca. ``texto_sha256`` é o resumo do texto original
    extraído, não o do texto devolvido. A API não tem rota que devolva os bytes originais."""
    ctx.caso_aberto(caso_id, "captura.texto")
    captura = _captura_do_caso(ctx, caso_id, captura_id)
    try:
        texto = servico.texto_da_captura(
            ctx.custodia, captura, ctx.config.ia_max_caracteres, ctx.config.limites_pdf()
        )
    except servico.ErroExtracao as erro:
        raise HTTPException(erro.status, str(erro)) from erro
    except Exception as erro:  # arquivo ausente ou cifra que não confere
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "O conteúdo da captura não pôde ser lido (verifique a integridade da captura).",
        ) from erro
    ctx.auditar(
        "captura.texto", caso_id, {"captura_id": captura.id, "caracteres": len(texto.conteudo)}
    )
    ctx.concluir()
    return {
        "captura_id": captura.id,
        "sha256": captura.sha256,
        "codificacao": texto.codificacao,
        "caracteres": len(texto.conteudo),
        "truncado": texto.truncado,
        "formato": texto.formato,
        "paginas": texto.paginas,
        "extrator": texto.extrator,
        "texto_sha256": texto.texto_sha256,
        "confere_com_coleta": texto.confere_com_coleta,
        "cpfs_descaracterizados": texto.cpfs_descaracterizados,
        "texto": texto.conteudo,
    }


@roteador.post(
    "/casos/{caso_id}/capturas/{captura_id}/extracao-ia", response_model=esquemas.ExtracaoIaSaida
)
def extrair(caso_id: str, captura_id: str, request: Request, ctx: Contexto = Depends(contexto)):
    """Roda o modelo local sobre o texto da captura. O que passa nas verificações (esquema,
    trecho literal, valor amparado no trecho) vira sugestão PENDENTE; o que não passa fica
    listado em ``recusas`` com o motivo. Nenhuma entidade é criada aqui."""
    ctx.caso_aberto(caso_id, "ia.extrair")
    provedor = request.app.state.provedor_ia
    if provedor is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "A extração assistida por IA está desligada neste servidor (IA_EXTRACAO_ATIVA=0).",
        )
    captura = _captura_do_caso(ctx, caso_id, captura_id)
    if not ctx.custodia.verificar(captura).integra:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "A captura não passou na verificação de integridade; a extração foi recusada.",
        )
    extracao = servico.executar(
        ctx.sessao,
        provedor=provedor,
        custodia=ctx.custodia,
        captura=captura,
        usuario=ctx.usuario.usuario,
        max_caracteres=ctx.config.ia_max_caracteres,
        tamanho_bloco=ctx.config.ia_tamanho_bloco,
        max_itens=ctx.config.ia_max_itens,
        limites_pdf=ctx.config.limites_pdf(),
    )
    ctx.auditar(
        "ia.extrair",
        caso_id,
        {
            "extracao_id": extracao.id,
            "captura_id": captura.id,
            "captura_sha256": captura.sha256,
            "provedor": extracao.provedor,
            "modelo": extracao.modelo,
            "modelo_versao": extracao.modelo_versao,
            "servidor_versao": extracao.servidor_versao,
            "parametros": extracao.parametros,
            "prompt_sha256": extracao.prompt_sha256,
            "situacao": extracao.situacao,
            "motivo": extracao.motivo,
            "blocos": extracao.blocos,
            "itens_recebidos": extracao.itens_recebidos,
            "itens_aceitos": extracao.itens_aceitos,
            "itens_recusados": extracao.itens_recusados,
            "motivos_de_recusa": sorted({r["motivo"] for r in extracao.recusas or []}),
            "metricas": extracao.metricas,
        },
    )
    ctx.concluir()
    if extracao.situacao != "sucesso":
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            f"A extração falhou e foi registrada ({extracao.id}): {extracao.motivo}",
        )
    return _extracao_para_dict(extracao, com_sugestoes=True)


@roteador.get("/casos/{caso_id}/extracoes-ia", response_model=list[esquemas.ExtracaoIaSaida])
def listar_extracoes(caso_id: str, ctx: Contexto = Depends(contexto)):
    """Execuções da extração no caso (inclusive as que falharam), da mais recente para a mais
    antiga, com modelo, versão, parâmetros e os itens recusados de cada uma."""
    ctx.caso(caso_id)
    extracoes = list(
        ctx.sessao.execute(
            select(ExtracaoIA)
            .where(ExtracaoIA.caso_id == caso_id)
            .order_by(ExtracaoIA.iniciado_em.desc())
        ).scalars()
    )
    ctx.auditar("ia.listar_extracoes", caso_id, {"extracoes": len(extracoes)})
    ctx.concluir()
    return [_extracao_para_dict(e) for e in extracoes]


@roteador.get("/casos/{caso_id}/sugestoes-ia", response_model=list[esquemas.SugestaoIaSaida])
def listar_sugestoes(
    caso_id: str,
    situacao: Literal["pendente", "aceita", "recusada"] | None = Query(default=None),
    captura_id: str | None = Query(default=None, max_length=MAX_IDENTIFICADOR),
    ctx: Contexto = Depends(contexto),
):
    ctx.caso(caso_id)
    sugestoes = servico.sugestoes_do_caso(ctx.sessao, caso_id, situacao, captura_id)
    extracoes = {
        e.id: e
        for e in ctx.sessao.execute(
            select(ExtracaoIA).where(ExtracaoIA.caso_id == caso_id)
        ).scalars()
    }
    ctx.auditar(
        "ia.listar_sugestoes",
        caso_id,
        {"situacao": situacao, "captura_id": captura_id, "sugestoes": len(sugestoes)},
    )
    ctx.concluir()
    return [_sugestao_para_dict(s, extracoes[s.extracao_id]) for s in sugestoes]


@roteador.post(
    "/casos/{caso_id}/sugestoes-ia/{sugestao_id}/decisao", response_model=esquemas.SugestaoIaSaida
)
def decidir_sugestao(
    caso_id: str,
    sugestao_id: str,
    corpo: esquemas.DecisaoSugestaoIa,
    ctx: Contexto = Depends(contexto),
):
    """O investigador aceita ou recusa a sugestão, com justificativa. Só a aceitação pode criar
    (ou completar) uma entidade, e a decisão fica na trilha de auditoria. A decisão é única:
    uma sugestão já decidida não muda."""
    ctx.caso_aberto(caso_id, "ia.sugestao_decidir")
    sugestao = None
    if len(sugestao_id) <= MAX_IDENTIFICADOR:
        sugestao = (
            ctx.sessao.execute(
                select(SugestaoIA)
                .where(SugestaoIA.id == sugestao_id, SugestaoIA.caso_id == caso_id)
                .with_for_update()
            )
            .scalars()
            .first()
        )
    if sugestao is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"Sugestão {sugestao_id} não encontrada no caso."
        )
    if sugestao.situacao != "pendente":
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"Esta sugestão já foi decidida ({sugestao.situacao})."
        )
    entidade = servico.aceitar(ctx.sessao, sugestao) if corpo.decisao == "aceita" else None
    sugestao.situacao = corpo.decisao
    sugestao.decidido_por = ctx.usuario.usuario
    sugestao.decidido_em = iso_utc()
    sugestao.justificativa_decisao = corpo.justificativa
    sugestao.entidade_id = entidade.id if entidade is not None else None
    ctx.sessao.flush()
    extracao = sugestao.extracao
    ctx.auditar(
        "ia.sugestao_decidir",
        caso_id,
        {
            "sugestao_id": sugestao.id,
            "extracao_id": sugestao.extracao_id,
            "captura_id": sugestao.captura_id,
            "tipo": sugestao.tipo,
            "valor": sugestao.valor,
            "decisao": corpo.decisao,
            "justificativa_decisao": corpo.justificativa,
            "entidade_id": sugestao.entidade_id,
            "modelo": extracao.modelo,
            "modelo_versao": extracao.modelo_versao,
        },
    )
    ctx.concluir()
    return _sugestao_para_dict(sugestao, extracao)
