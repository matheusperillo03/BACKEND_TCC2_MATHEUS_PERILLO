"""Extração assistida por IA local (RF13, RNF05, H22): execução, sugestões e decisão.

Desligada por padrão (``IA_EXTRACAO_ATIVA=0``): as rotas de execução respondem ``409`` e nada é
enviado a modelo nenhum. Ligada, só fala com o servidor LOCAL configurado.

A execução roda em SEGUNDO PLANO: ``POST .../extracao-ia`` só põe a execução na fila e responde
``202``; o trabalhador (``buscador.ia.trabalhador``) processa bloco a bloco. O cliente acompanha
por ``GET .../extracoes-ia/{id}/progresso`` e pode cancelar e retomar.
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


def _extracao_para_dict(e: ExtracaoIA, sessao, com_sugestoes: bool = False) -> dict:
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
        "progresso": servico.progresso(sessao, e),
        "sugestoes": [_sugestao_para_dict(s, e) for s in e.sugestoes] if com_sugestoes else [],
    }


def _extracao_do_caso(ctx: Contexto, caso_id: str, extracao_id: str) -> ExtracaoIA:
    extracao = (
        ctx.sessao.get(ExtracaoIA, extracao_id) if len(extracao_id) <= MAX_IDENTIFICADOR else None
    )
    if extracao is None or extracao.caso_id != caso_id:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"Execução {extracao_id} não encontrada no caso."
        )
    return extracao


def _provedor_ligado(request: Request):
    provedor = request.app.state.provedor_ia
    if provedor is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "A extração assistida por IA está desligada neste servidor (IA_EXTRACAO_ATIVA=0).",
        )
    return provedor


def _acordar_trabalhador(request: Request) -> None:
    trabalhador = getattr(request.app.state, "trabalhador_ia", None)
    if trabalhador is not None:
        trabalhador.acordar()


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
    "/casos/{caso_id}/capturas/{captura_id}/extracao-ia",
    response_model=esquemas.ExtracaoIaSaida,
    status_code=status.HTTP_202_ACCEPTED,
)
def extrair(caso_id: str, captura_id: str, request: Request, ctx: Contexto = Depends(contexto)):
    """Põe na fila a extração do modelo local sobre o texto da captura e responde ``202`` com a
    execução (``situacao: na_fila``). O processamento roda fora desta requisição, um documento
    por vez; acompanhe por ``GET /casos/{id}/extracoes-ia/{extracao_id}/progresso``.

    Só uma execução ativa por documento: com outra na fila ou em andamento, responde ``409``.
    O que passar nas verificações (esquema, trecho literal, valor amparado no trecho) vira
    sugestão PENDENTE conforme cada bloco termina; nenhuma entidade é criada aqui."""
    ctx.caso_aberto(caso_id, "ia.extrair")
    provedor = _provedor_ligado(request)
    captura = _captura_do_caso(ctx, caso_id, captura_id)
    if not ctx.custodia.verificar(captura).integra:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "A captura não passou na verificação de integridade; a extração foi recusada.",
        )
    try:
        extracao = servico.criar(
            ctx.sessao,
            provedor_nome=provedor.nome,
            captura=captura,
            usuario=ctx.usuario.usuario,
            justificativa=ctx.justificativa,
        )
    except servico.ExecucaoEmConflito as conflito:
        raise HTTPException(status.HTTP_409_CONFLICT, str(conflito)) from conflito
    ctx.auditar(
        "ia.extrair",
        caso_id,
        {
            "extracao_id": extracao.id,
            "captura_id": captura.id,
            "captura_sha256": captura.sha256,
            "provedor": extracao.provedor,
            "prompt_sha256": extracao.prompt_sha256,
            "situacao": extracao.situacao,
        },
    )
    ctx.concluir()
    _acordar_trabalhador(request)
    return _extracao_para_dict(extracao, ctx.sessao)


@roteador.get("/casos/{caso_id}/extracoes-ia", response_model=list[esquemas.ExtracaoIaSaida])
def listar_extracoes(caso_id: str, ctx: Contexto = Depends(contexto)):
    """Execuções da extração no caso (inclusive as que falharam e as que ainda estão na fila ou
    em andamento), da mais recente para a mais antiga, com modelo, versão, parâmetros, o
    andamento e os itens recusados de cada uma."""
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
    return [_extracao_para_dict(e, ctx.sessao) for e in extracoes]


@roteador.get(
    "/casos/{caso_id}/extracoes-ia/{extracao_id}", response_model=esquemas.ExtracaoIaSaida
)
def consultar_extracao(caso_id: str, extracao_id: str, ctx: Contexto = Depends(contexto)):
    """Uma execução, com as sugestões que ela já produziu, as recusas e as métricas."""
    ctx.caso(caso_id)
    extracao = _extracao_do_caso(ctx, caso_id, extracao_id)
    ctx.auditar(
        "ia.consultar_extracao",
        caso_id,
        {"extracao_id": extracao.id, "situacao": extracao.situacao},
    )
    ctx.concluir()
    return _extracao_para_dict(extracao, ctx.sessao, com_sugestoes=True)


@roteador.get(
    "/casos/{caso_id}/extracoes-ia/{extracao_id}/progresso",
    response_model=esquemas.ProgressoExtracaoIa,
)
def progresso_da_extracao(caso_id: str, extracao_id: str, ctx: Contexto = Depends(contexto)):
    """Andamento da execução: situação, blocos concluídos, falhos e pendentes, tempo decorrido
    e estimativa do que falta. Feita para consulta periódica (*polling*).

    Devolve só números e códigos de estado, nenhum conteúdo do documento nem das sugestões; por
    isso, e para a consulta periódica não encher a trilha, esta rota NÃO grava entrada de
    auditoria (decisão do autor). O acesso continua restrito aos membros do caso; a criação, o
    cancelamento, a retomada e o desfecho da execução são auditados."""
    ctx.caso(caso_id)
    return servico.progresso(ctx.sessao, _extracao_do_caso(ctx, caso_id, extracao_id))


@roteador.post(
    "/casos/{caso_id}/extracoes-ia/{extracao_id}/cancelar",
    response_model=esquemas.ProgressoExtracaoIa,
)
def cancelar_extracao(caso_id: str, extracao_id: str, ctx: Contexto = Depends(contexto)):
    """Cancela a execução. Na fila, ela é cancelada na hora; em andamento, o trabalhador para
    no próximo token recebido e fecha a execução como ``cancelada``. Os blocos já concluídos
    (e as sugestões deles) ficam gravados, e a execução pode ser retomada depois."""
    ctx.caso(caso_id)
    extracao = _extracao_do_caso(ctx, caso_id, extracao_id)
    try:
        efeito = servico.pedir_cancelamento(ctx.sessao, extracao, ctx.usuario.usuario)
    except servico.ExecucaoEmConflito as conflito:
        raise HTTPException(status.HTTP_409_CONFLICT, str(conflito)) from conflito
    ctx.auditar(
        "ia.extracao_cancelar",
        caso_id,
        {
            "extracao_id": extracao.id,
            "captura_id": extracao.captura_id,
            "efeito": efeito,
            "situacao": extracao.situacao,
        },
    )
    ctx.concluir()
    return servico.progresso(ctx.sessao, extracao)


@roteador.post(
    "/casos/{caso_id}/extracoes-ia/{extracao_id}/retomar",
    response_model=esquemas.ProgressoExtracaoIa,
    status_code=status.HTTP_202_ACCEPTED,
)
def retomar_extracao(
    caso_id: str, extracao_id: str, request: Request, ctx: Contexto = Depends(contexto)
):
    """Devolve à fila uma execução ``concluida_com_falhas``, ``falha``, ``cancelada`` ou
    ``interrompida``. Só os blocos pendentes e os que falharam são processados; os concluídos
    não são refeitos. O modelo, as instruções e o texto têm de ser os mesmos do início."""
    ctx.caso_aberto(caso_id, "ia.extracao_retomar")
    _provedor_ligado(request)
    extracao = _extracao_do_caso(ctx, caso_id, extracao_id)
    anterior = extracao.situacao
    try:
        servico.retomar(ctx.sessao, extracao, ctx.justificativa)
    except servico.ExecucaoEmConflito as conflito:
        raise HTTPException(status.HTTP_409_CONFLICT, str(conflito)) from conflito
    estado = servico.progresso(ctx.sessao, extracao)
    ctx.auditar(
        "ia.extracao_retomar",
        caso_id,
        {
            "extracao_id": extracao.id,
            "captura_id": extracao.captura_id,
            "situacao_anterior": anterior,
            "blocos_pendentes": estado["blocos_pendentes"],
            "blocos_concluidos": estado["blocos_concluidos"],
            "retomadas": extracao.retomadas,
        },
    )
    ctx.concluir()
    _acordar_trabalhador(request)
    return estado


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
