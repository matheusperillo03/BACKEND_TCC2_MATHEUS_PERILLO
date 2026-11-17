"""Rotas de casos: criação, coleta, entidades, grafo, alertas e relatório."""

from __future__ import annotations

import logging
import re
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import JSONResponse, PlainTextResponse
from sqlalchemy import func, or_, select

from buscador import comparacao, grafo, relatorio, retencao, tipologias
from buscador.api import esquemas
from buscador.api.deps import MAX_IDENTIFICADOR, Contexto, contexto
from buscador.coleta import AndamentoColeta, ResultadoColetaCaso, executar_coleta
from buscador.conectores import CONECTORES, ErroConector, FonteNaoConfigurada
from buscador.conectores.parametros import validar_parametros
from buscador.modelos import (
    Alerta,
    AlertaDecisao,
    Anotacao,
    Aresta,
    Aviso,
    Captura,
    Caso,
    CasoMembro,
    Coleta,
    Entidade,
    Monitoramento,
    Sugestao,
    SugestaoIA,
    Tarefa,
    iso_utc,
)
from buscador.privacidade import campo_de_documento, descaracterizar
from buscador.resolucao import TIPOS_ENTIDADE
from buscador.resolucao.normalizacao import (
    limpar_documento,
    normalizar_cnpj,
    normalizar_nome,
    somente_digitos,
)
from buscador.seguranca import caractere_proibido, sanitizar_json, texto_de_linha

registro = logging.getLogger(__name__)

roteador = APIRouter(prefix="/casos", tags=["casos"])

# Literal: o nome da constante do Starlette para o 422 mudou entre versões.
HTTP_422 = 422


def _padrao_like(termo: str) -> str:
    """Termo do usuário como texto literal dentro de um LIKE (com ``ESCAPE '\\'``)."""
    escapado = termo.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escapado}%"


def _caso_para_dict(caso: Caso, meu_papel: str | None = None) -> dict:
    return {
        "meu_papel": meu_papel,
        "id": caso.id,
        "titulo": caso.titulo,
        "descricao": caso.descricao,
        "numero_procedimento": caso.numero_procedimento,
        "criado_por": caso.criado_por,
        "criado_em": caso.criado_em,
        "finalidade": caso.finalidade or "",
        "retencao_ate": caso.retencao_ate,
        "somente_leitura": retencao.vencida(caso),
    }


def _entidade_resumo(e: Entidade) -> dict:
    return {
        "id": e.id,
        "tipo": e.tipo,
        "rotulo": e.rotulo,
        "identificador": e.identificador or "",
        "atributos": sanitizar_json(e.atributos or {}),
    }


def _decisao_para_dict(d: AlertaDecisao) -> dict:
    return {
        "decisao": d.decisao,
        "justificativa": d.justificativa,
        "usuario": d.usuario,
        "perfil": d.perfil,
        "decidido_em": d.decidido_em,
    }


def _alerta_para_dict(a: Alerta) -> dict:
    politica = tipologias.politica(a.tipologia)
    decisoes = [_decisao_para_dict(d) for d in a.decisoes]
    return {
        "id": a.id,
        "tipologia": a.tipologia,
        "titulo": a.titulo,
        "motivo": a.motivo,
        "severidade": a.severidade,
        "entidades": a.entidades or [],
        "evidencias": a.evidencias or [],
        "parametros": sanitizar_json(a.parametros or {}),
        "criado_em": a.criado_em,
        "tipologia_nome": politica.nome if politica else a.tipologia,
        "prefere_evitar": politica.prefere_evitar if politica else "",
        "politica_erro": politica.politica_erro if politica else "",
        "grupo": politica.grupo if politica else "contratacao",
        "sinal_fraco": politica.sinal_fraco if politica else False,
        "situacao": a.situacao or "pendente",
        # A decisão em vigor é a última do histórico; as anteriores continuam em ``decisoes``.
        "decisao": decisoes[-1] if decisoes else None,
        "decisoes": decisoes,
        "ativo": bool(a.ativo),
    }


def _coleta_para_dict(c: Coleta, captura_ids: list[str]) -> dict:
    return {
        "id": c.id,
        "fonte": c.fonte,
        "parametros": sanitizar_json(c.parametros or {}),
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
        "captura_ids": captura_ids,
    }


def _obter_alerta(ctx: Contexto, caso_id: str, alerta_id: str, *, travar: bool = False) -> Alerta:
    alerta = None
    if len(alerta_id) <= MAX_IDENTIFICADOR:
        consulta = select(Alerta).where(Alerta.id == alerta_id, Alerta.caso_id == caso_id)
        if travar:
            # Serializa decisões concorrentes sobre o mesmo alerta (ignorado no SQLite).
            consulta = consulta.with_for_update()
        alerta = ctx.sessao.execute(consulta).scalars().first()
    if alerta is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"Alerta {alerta_id} não encontrado no caso."
        )
    return alerta


def _obter_coleta(ctx: Contexto, caso_id: str, coleta_id: str) -> Coleta:
    coleta = ctx.sessao.get(Coleta, coleta_id) if len(coleta_id) <= MAX_IDENTIFICADOR else None
    if coleta is None or coleta.caso_id != caso_id:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"Coleta {coleta_id} não encontrada no caso."
        )
    return coleta


@roteador.post("", response_model=esquemas.CasoSaida, status_code=status.HTTP_201_CREATED)
def criar_caso(corpo: esquemas.CasoEntrada, ctx: Contexto = Depends(contexto)):
    caso = Caso(
        titulo=corpo.titulo,
        descricao=corpo.descricao,
        numero_procedimento=corpo.numero_procedimento,
        criado_por=ctx.usuario.usuario,
        finalidade=corpo.finalidade,
        retencao_ate=corpo.retencao_ate.isoformat(),
    )
    ctx.sessao.add(caso)
    ctx.sessao.flush()
    # Acesso por caso (H23): quem cria o caso é o primeiro membro, como responsável.
    ctx.sessao.add(
        CasoMembro(
            caso_id=caso.id,
            usuario=ctx.usuario.usuario,
            papel="responsavel",
            adicionado_por=ctx.usuario.usuario,
        )
    )
    ctx.auditar(
        "caso.criar",
        caso.id,
        {"titulo": caso.titulo, "finalidade": caso.finalidade, "retencao_ate": caso.retencao_ate},
    )
    ctx.concluir()
    return _caso_para_dict(caso, "responsavel")


@roteador.get("", response_model=list[esquemas.CasoSaida])
def listar_casos(ctx: Contexto = Depends(contexto)):
    """Só os casos de que o usuário é membro (H23)."""
    linhas = ctx.sessao.execute(
        select(Caso, CasoMembro.papel)
        .join(CasoMembro, CasoMembro.caso_id == Caso.id)
        .where(CasoMembro.usuario == ctx.usuario.usuario)
        .order_by(Caso.criado_em.desc())
    ).all()
    ctx.auditar("caso.listar", None, {"total": len(linhas)})
    ctx.concluir()
    return [_caso_para_dict(caso, papel) for caso, papel in linhas]


@roteador.get("/{caso_id}", response_model=esquemas.CasoDetalhe)
def ver_caso(caso_id: str, ctx: Contexto = Depends(contexto)):
    caso = ctx.caso(caso_id)

    def contar(modelo):
        return ctx.sessao.execute(
            select(func.count()).select_from(modelo).where(modelo.caso_id == caso_id)
        ).scalar_one()

    contagens = {
        "entidades": contar(Entidade),
        "alertas": contar(Alerta),
        "capturas": contar(Captura),
        "coletas": contar(Coleta),
        "sugestoes": contar(Sugestao),
        "membros": contar(CasoMembro),
        "anotacoes": contar(Anotacao),
        "tarefas_abertas": ctx.sessao.execute(
            select(func.count())
            .select_from(Tarefa)
            .where(Tarefa.caso_id == caso_id, Tarefa.situacao.in_(("aberta", "em_andamento")))
        ).scalar_one(),
        "sugestoes_ia_pendentes": ctx.sessao.execute(
            select(func.count())
            .select_from(SugestaoIA)
            .where(SugestaoIA.caso_id == caso_id, SugestaoIA.situacao == "pendente")
        ).scalar_one(),
        "monitoramentos": ctx.sessao.execute(
            select(func.count())
            .select_from(Monitoramento)
            .where(Monitoramento.caso_id == caso_id, Monitoramento.ativo.is_(True))
        ).scalar_one(),
        "avisos_nao_lidos": ctx.sessao.execute(
            select(func.count())
            .select_from(Aviso)
            .where(Aviso.caso_id == caso_id, Aviso.lido_em.is_(None))
        ).scalar_one(),
    }
    ctx.auditar("caso.ver", caso_id)
    ctx.concluir()
    return {**_caso_para_dict(caso, ctx.papel_no_caso(caso_id)), "contagens": contagens}


MAX_AVISOS_COLETA = 50


def _fechar_coleta(
    ctx: Contexto, coleta_id: str, situacao: str, andamento: AndamentoColeta, motivo: str, **campos
) -> None:
    """Fecha a linha da coleta (RNF12): situação, tentativas, motivo e conector efetivo."""
    coleta = ctx.sessao.get(Coleta, coleta_id)
    if coleta is None:  # pragma: no cover - a linha é confirmada antes da coleta começar
        return
    coleta.situacao = situacao
    coleta.tentativas = andamento.tentativas
    coleta.motivo = motivo
    coleta.concluido_em = iso_utc()
    if andamento.conector_nome:
        coleta.conector_nome = andamento.conector_nome
        coleta.conector_versao = andamento.conector_versao
    for nome, valor in campos.items():
        setattr(coleta, nome, valor)


def _motivo_da_falha(erro: Exception, andamento: AndamentoColeta) -> str:
    """Motivo apresentável: a mensagem do conector, ou um texto genérico para erro inesperado
    (o detalhe técnico fica só na auditoria, restrita a delegado e admin)."""
    if isinstance(erro, ErroConector):
        return texto_de_linha(erro)[:400]
    return (
        f"Falha inesperada ({type(erro).__name__}) depois de {andamento.tentativas} "
        "tentativa(s); o detalhe está na auditoria."
    )


def _registrar_falha_de_coleta(
    ctx: Contexto,
    caso_id: str,
    coleta_id: str,
    fonte: str,
    parametros: dict,
    erro: Exception,
    andamento: AndamentoColeta,
) -> None:
    """Grava ``coleta.falhou``, fecha a coleta como falha e confirma junto as capturas já
    registradas (RF07 + RNF02 + RNF12).

    Se nem isso for possível (sessão inutilizada), desfaz tudo e grava a falha dizendo quais
    capturas ficaram sem registro no banco.
    """
    motivo = _motivo_da_falha(erro, andamento)
    detalhes = {
        "coleta_id": coleta_id,
        "fonte": fonte,
        "parametros": parametros,
        "conector": {"nome": andamento.conector_nome, "versao": andamento.conector_versao},
        "tentativas": andamento.tentativas,
        "tipo_erro": type(erro).__name__,
        "erro": texto_de_linha(erro)[:400],
        "captura_ids": list(andamento.captura_ids),
    }
    try:
        _fechar_coleta(ctx, coleta_id, "falha", andamento, motivo)
        ctx.auditar("coleta.falhou", caso_id, detalhes)
        ctx.concluir()
    except Exception:
        registro.exception("Não foi possível confirmar as capturas da coleta que falhou.")
        ctx.desfazer()
        detalhes["captura_ids"] = []
        detalhes["capturas_nao_registradas"] = list(andamento.captura_ids)
        _fechar_coleta(ctx, coleta_id, "falha", andamento, motivo)
        ctx.auditar("coleta.falhou", caso_id, detalhes)
        ctx.concluir()


def _parametros_sem_cpf(parametros: object) -> object:
    """Parâmetros como ficam gravados na coleta (e voltam em ``GET /coletas``): nos campos de
    documento, um CPF digitado no lugar do CNPJ fica descaracterizado (RNF04). As fontes só
    aceitam CNPJ nesses campos, então a consulta válida não muda."""
    if not isinstance(parametros, dict):
        return parametros
    return {
        k: descaracterizar(v, campo=k) if campo_de_documento(k) or k == "contratos" else v
        for k, v in parametros.items()
    }


MAX_CODIGOS_DO_CASO = 100
MAX_CANDIDATOS_DO_CASO = 2000


def _expandir_do_caso(ctx: Contexto, caso_id: str, fonte: str, parametros: dict) -> dict:
    """Troca ``"do_caso": true`` pela lista explícita do que será consultado (H19): as contratadas
    do caso (CEIS e CNEP) ou os contratos do caso (termos do PNCP). A lista fica gravada nos
    parâmetros da coleta, de modo que a recoleta repete exatamente a mesma consulta."""
    if not parametros.get("do_caso") or fonte not in (
        "portal_transparencia",
        "pncp_termos",
        "tse_arquivo",
    ):
        return parametros
    expandido = {k: v for k, v in parametros.items() if k != "do_caso"}
    if fonte == "tse_arquivo":
        # Candidatos que já estão no caso (colete antes o arquivo de candidatos, com 'uf' e
        # 'municipio'): só as linhas desses SQ_CANDIDATO são lidas do arquivo de receitas.
        sqs = sorted(
            {
                str(sq)
                for sq in ctx.sessao.execute(
                    select(Entidade.identificador).where(
                        Entidade.caso_id == caso_id, Entidade.tipo == "candidato"
                    )
                ).scalars()
                if sq and str(sq).isdigit()
            }
        )
        if not sqs:
            raise ErroConector(
                "O caso não tem candidato para filtrar (colete antes o arquivo de candidatos do "
                "TSE, com 'uf' e 'municipio')."
            )
        if len(sqs) > MAX_CANDIDATOS_DO_CASO:
            raise ErroConector(
                f"O caso tem {len(sqs)} candidatos, acima de {MAX_CANDIDATOS_DO_CASO} por "
                "coleta: use 'uf' e 'municipio' ou informe os SQ_CANDIDATO em 'candidatos'."
            )
        expandido["candidatos"] = sqs
        return expandido
    contratos = (
        ctx.sessao.execute(
            select(Entidade)
            .where(Entidade.caso_id == caso_id, Entidade.tipo == "contrato")
            .order_by(Entidade.identificador)
        )
        .scalars()
        .all()
    )
    if fonte == "portal_transparencia":
        if parametros.get("recurso") == "servidores":
            raise ErroConector(
                "'do_caso' não vale para 'servidores': informe o código SIAPE e o CNPJ do órgão."
            )
        if parametros.get("recurso") in ("cepim", "convenios", "ceaf"):
            return _expandir_lavagem(ctx, caso_id, parametros["recurso"], contratos, expandido)
        cnpjs = sorted(
            {
                ni
                for c in contratos
                # CNPJ numérico ou alfanumérico; o CPF descaracterizado do fornecedor pessoa
                # física não tem a forma de CNPJ e fica de fora.
                if (ni := normalizar_cnpj(str((c.atributos or {}).get("ni_fornecedor") or "")))
            }
        )
        if not cnpjs:
            raise ErroConector("O caso não tem empresa contratada (CNPJ) para consultar.")
        if len(cnpjs) > MAX_CODIGOS_DO_CASO:
            raise ErroConector(
                f"O caso tem {len(cnpjs)} contratadas, acima de {MAX_CODIGOS_DO_CASO} por coleta: "
                "informe os CNPJs em 'codigoSancionado', em lotes."
            )
        expandido["codigoSancionado"] = cnpjs
        return expandido
    itens = []
    for c in contratos:
        a = c.atributos or {}
        if a.get("orgao_cnpj") and a.get("anoContrato") and a.get("sequencialContrato"):
            itens.append(
                {
                    "cnpj": a["orgao_cnpj"],
                    "ano": int(a["anoContrato"]),
                    "sequencial": int(a["sequencialContrato"]),
                    "numero_controle_pncp": c.identificador,
                }
            )
    if not itens:
        raise ErroConector(
            "O caso não tem contrato com órgão, ano e sequencial (colete os contratos do PNCP "
            "antes dos termos)."
        )
    expandido["contratos"] = itens
    return expandido


def _expandir_lavagem(
    ctx: Contexto, caso_id: str, recurso: str, contratos: list[Entidade], expandido: dict
) -> dict:
    """``do_caso`` das coletas das tipologias L10 e L16: CEPIM e convênios pelas entidades sem
    fins lucrativos e convenentes do caso; CEAF pelos NOMES dos sócios pessoa física das
    contratadas e das convenentes (nunca por CPF, RNF04)."""
    empresas = {
        e.id: e
        for e in ctx.sessao.execute(
            select(Entidade).where(Entidade.caso_id == caso_id, Entidade.tipo == "empresa")
        ).scalars()
    }
    arestas = list(
        ctx.sessao.execute(
            select(Aresta).where(
                Aresta.caso_id == caso_id,
                Aresta.tipo.in_(("convenente_de", "fornecedor_de", "socio_de")),
            )
        ).scalars()
    )
    convenentes = {a.origem_id for a in arestas if a.tipo == "convenente_de"}
    if recurso in ("cepim", "convenios"):
        naturezas = set(ctx.config.parametros_tipologias()["l16_naturezas_sem_fins_lucrativos"])
        cnpjs = sorted(
            {
                cnpj
                for e in empresas.values()
                if e.id in convenentes
                or str((e.atributos or {}).get("natureza_juridica") or "") in naturezas
                if (cnpj := normalizar_cnpj(str((e.atributos or {}).get("cnpj") or "")))
            }
        )
        if not cnpjs:
            raise ErroConector(
                "O caso não tem entidade sem fins lucrativos (pela natureza jurídica do CNPJ) "
                "nem convenente com CNPJ para consultar."
            )
        if len(cnpjs) > MAX_CODIGOS_DO_CASO:
            raise ErroConector(
                f"O caso tem {len(cnpjs)} entidades, acima de {MAX_CODIGOS_DO_CASO} por coleta: "
                "informe os CNPJs em 'codigoSancionado', em lotes."
            )
        expandido["codigoSancionado"] = cnpjs
        return expandido
    alvos = convenentes | {a.origem_id for a in arestas if a.tipo == "fornecedor_de"}
    socios = {a.origem_id for a in arestas if a.tipo == "socio_de" and a.destino_id in alvos}
    nomes = sorted(
        {
            nome
            for p in ctx.sessao.execute(
                select(Entidade).where(Entidade.caso_id == caso_id, Entidade.tipo == "pessoa")
            ).scalars()
            if p.id in socios and len(nome := str((p.atributos or {}).get("nome") or "")) >= 3
        }
    )
    if not nomes:
        raise ErroConector(
            "O caso não tem sócio pessoa física de contratada ou de convenente para consultar "
            "(colete antes o CNPJ e os contratos ou convênios)."
        )
    if len(nomes) > MAX_CODIGOS_DO_CASO:
        raise ErroConector(
            f"O caso tem {len(nomes)} sócios, acima de {MAX_CODIGOS_DO_CASO} por coleta: informe "
            "os nomes em 'nomeSancionado', em lotes."
        )
    expandido["nomeSancionado"] = nomes
    return expandido


def _coletar_registrando(
    ctx: Contexto, caso: Caso, fonte: str, parametros: dict, recoleta_de: Coleta | None = None
) -> tuple[Coleta, ResultadoColetaCaso, dict | None]:
    """Roda a coleta com registro completo: linha em ``coletas``, custódia e auditoria.

    Devolve a coleta, o resultado e, numa recoleta, a comparação com a coleta anterior. Em caso
    de falha, registra-a (situação, tentativas e motivo) e levanta ``HTTPException``.
    """
    extras = ctx.extras_conectores.get(fonte, {})
    classe = CONECTORES[fonte]
    coleta = Coleta(
        caso_id=caso.id,
        fonte=fonte,
        parametros=_parametros_sem_cpf(parametros),
        conector_nome=classe.nome,
        conector_versao=classe.versao,
        usuario=ctx.usuario.usuario,
        recoleta_de=recoleta_de.id if recoleta_de else None,
    )
    ctx.sessao.add(coleta)
    ctx.sessao.flush()
    coleta_id = coleta.id
    caso_id = caso.id
    conector = {"nome": classe.nome, "versao": classe.versao}
    base = {"coleta_id": coleta_id, "fonte": fonte, "parametros": parametros, "conector": conector}
    if recoleta_de is not None:
        base["recoleta_de"] = recoleta_de.id
    # A tentativa fica confirmada na trilha antes de qualquer consulta externa ou leitura de
    # arquivo: nem uma queda do processo no meio da coleta a deixa sem registro.
    ctx.auditar("coleta.iniciar", caso_id, base)
    ctx.concluir()

    andamento = AndamentoColeta()
    try:
        validar_parametros(fonte, parametros)
        expandidos = _expandir_do_caso(ctx, caso_id, fonte, parametros)
        if expandidos is not parametros:
            parametros = expandidos
            validar_parametros(fonte, parametros)
            coleta.parametros = _parametros_sem_cpf(parametros)
            base["parametros"] = parametros
        resultado = executar_coleta(
            ctx.sessao,
            config=ctx.config,
            custodia=ctx.custodia,
            caso=caso,
            fonte=fonte,
            parametros=parametros,
            extras_conector=extras,
            andamento=andamento,
            coleta_id=coleta_id,
        )
        motivo = ""
        if andamento.tentativas > 1:
            motivo = (
                f"Sucesso depois de {andamento.tentativas} tentativas; última falha "
                f"transitória: {texto_de_linha(andamento.ultima_falha_transitoria)[:300]}"
            )
        _fechar_coleta(
            ctx,
            coleta_id,
            "sucesso",
            andamento,
            motivo,
            registros=resultado.registros,
            avisos=[texto_de_linha(a)[:300] for a in resultado.avisos[:MAX_AVISOS_COLETA]],
        )
        ctx.sessao.flush()
        diferenca = None
        detalhes = {
            **base,
            "situacao": "sucesso",
            "tentativas": andamento.tentativas,
            "captura_ids": resultado.captura_ids,
            "entidades_novas": resultado.entidades_novas,
            "arestas_novas": resultado.arestas_novas,
        }
        if recoleta_de is not None:
            diferenca = comparacao.comparar_coletas(ctx.sessao, ctx.custodia, coleta, recoleta_de)
            detalhes["comparacao"] = {
                "mesmo_conteudo": diferenca["mesmo_conteudo"],
                "capturas_iguais": diferenca["capturas_iguais"],
                "capturas_diferentes": diferenca["capturas_diferentes"],
            }
        ctx.auditar("coleta.executar", caso_id, detalhes)
        ctx.concluir()
    except FonteNaoConfigurada as erro:
        # Fonte sem configuração (ex.: sem chave de API) não é falha: nada foi consultado. A
        # coleta fica registrada como "não configurado" e as demais fontes seguem (H19).
        motivo = texto_de_linha(erro)[:400]
        _fechar_coleta(ctx, coleta_id, "nao_configurado", andamento, motivo, avisos=[motivo])
        ctx.auditar("coleta.nao_configurada", caso_id, {**base, "situacao": "nao_configurado"})
        ctx.concluir()
        vazio = ResultadoColetaCaso(
            fonte=fonte,
            captura_ids=[],
            entidades_novas=0,
            entidades_atualizadas=0,
            arestas_novas=0,
            sugestoes_novas=0,
            registros=0,
            avisos=[motivo],
        )
        return coleta, vazio, None
    except Exception as erro:
        # Qualquer falha (do conector, da resolução ou da própria gravação) fica auditada, e as
        # capturas já guardadas continuam registradas: o que a mensagem de erro cita existe.
        _registrar_falha_de_coleta(ctx, caso_id, coleta_id, fonte, parametros, erro, andamento)
        if isinstance(erro, ErroConector):
            raise HTTPException(HTTP_422, str(erro)) from erro
        registro.exception("Falha inesperada na coleta do caso %s.", caso_id)
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "Falha inesperada na coleta. A tentativa foi registrada na auditoria.",
        ) from erro
    return coleta, resultado, diferenca


def _coleta_saida(coleta: Coleta, resultado: ResultadoColetaCaso) -> dict:
    return {
        **resultado.para_dict(),
        "coleta_id": coleta.id,
        "situacao": coleta.situacao,
        "tentativas": coleta.tentativas,
        "motivo": coleta.motivo or "",
        "conector_nome": coleta.conector_nome,
        "conector_versao": coleta.conector_versao,
        "recoleta_de": coleta.recoleta_de,
    }


@roteador.post("/{caso_id}/coletas", response_model=esquemas.ColetaSaida)
def coletar(caso_id: str, corpo: esquemas.ColetaEntrada, ctx: Contexto = Depends(contexto)):
    caso = ctx.caso_aberto(caso_id, "coleta.iniciar")
    coleta, resultado, _ = _coletar_registrando(ctx, caso, corpo.fonte, corpo.parametros)
    return _coleta_saida(coleta, resultado)


@roteador.get("/{caso_id}/coletas", response_model=list[esquemas.ColetaRegistro])
def listar_coletas(caso_id: str, ctx: Contexto = Depends(contexto)):
    """Histórico das coletas do caso, da mais recente para a mais antiga (RNF12, H15)."""
    ctx.caso(caso_id)
    coletas = (
        ctx.sessao.execute(
            select(Coleta).where(Coleta.caso_id == caso_id).order_by(Coleta.iniciado_em.desc())
        )
        .scalars()
        .all()
    )
    por_coleta: dict[str, list[str]] = {}
    for captura_id, coleta_id in ctx.sessao.execute(
        select(Captura.id, Captura.coleta_id)
        .where(Captura.caso_id == caso_id, Captura.coleta_id.is_not(None))
        .order_by(Captura.coletado_em, Captura.id)
    ):
        por_coleta.setdefault(coleta_id, []).append(captura_id)
    ctx.auditar(
        "coleta.listar",
        caso_id,
        {"coletas": len(coletas), "falhas": sum(c.situacao == "falha" for c in coletas)},
    )
    ctx.concluir()
    return [_coleta_para_dict(c, por_coleta.get(c.id, [])) for c in coletas]


@roteador.post("/{caso_id}/coletas/{coleta_id}/recoletar", response_model=esquemas.RecoletaSaida)
def recoletar(caso_id: str, coleta_id: str, ctx: Contexto = Depends(contexto)):
    """Repete uma coleta do caso (mesma fonte e mesmos parâmetros) e compara as capturas novas
    com as da coleta original (RF07, H14). Nada da coleta original é alterado nem apagado."""
    caso = ctx.caso_aberto(caso_id, "coleta.recoletar")
    original = _obter_coleta(ctx, caso_id, coleta_id)
    if original.situacao == "em_andamento":
        raise HTTPException(status.HTTP_409_CONFLICT, "A coleta original ainda está em andamento.")
    if original.fonte not in CONECTORES:
        raise HTTPException(HTTP_422, "A fonte da coleta original não está mais disponível.")
    coleta, resultado, diferenca = _coletar_registrando(
        ctx, caso, original.fonte, dict(original.parametros or {}), recoleta_de=original
    )
    if diferenca is None:
        # A fonte deixou de estar configurada: não houve o que comparar.
        diferenca = comparacao.comparar_coletas(ctx.sessao, ctx.custodia, coleta, original)
    return {**_coleta_saida(coleta, resultado), "comparacao": diferenca}


@roteador.get("/{caso_id}/coletas/{coleta_id}/comparacao", response_model=esquemas.ComparacaoColeta)
def comparar_coleta(
    caso_id: str,
    coleta_id: str,
    com: str | None = Query(default=None, max_length=MAX_IDENTIFICADOR),
    ctx: Contexto = Depends(contexto),
):
    """Compara as capturas de uma coleta com as de outra do mesmo caso e da mesma fonte. Sem
    ``com``, a outra é a coleta que esta repetiu (``recoleta_de``)."""
    ctx.caso(caso_id)
    coleta = _obter_coleta(ctx, caso_id, coleta_id)
    outra_id = com or coleta.recoleta_de
    if not outra_id:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "Esta coleta não é uma recoleta; informe em 'com' a coleta a comparar.",
        )
    outra = _obter_coleta(ctx, caso_id, outra_id)
    if outra.id == coleta.id or outra.fonte != coleta.fonte:
        raise HTTPException(HTTP_422, "Só é possível comparar coletas diferentes da mesma fonte.")
    diferenca = comparacao.comparar_coletas(ctx.sessao, ctx.custodia, coleta, outra)
    ctx.auditar(
        "coleta.comparar",
        caso_id,
        {
            "coleta_id": coleta.id,
            "anterior_id": outra.id,
            "mesmo_conteudo": diferenca["mesmo_conteudo"],
            "capturas_diferentes": diferenca["capturas_diferentes"],
        },
    )
    ctx.concluir()
    return diferenca


@roteador.get("/{caso_id}/entidades", response_model=list[esquemas.EntidadeResumo])
def buscar_entidades(
    caso_id: str,
    q: str = Query(default="", max_length=200, description="Nome, CNPJ ou CPF parcial"),
    tipo: Literal[TIPOS_ENTIDADE] | None = Query(default=None),  # type: ignore[valid-type]
    ctx: Contexto = Depends(contexto),
):
    ctx.caso_aberto(caso_id, "entidade.buscar")
    consulta = select(Entidade).where(Entidade.caso_id == caso_id)
    if tipo:
        consulta = consulta.where(Entidade.tipo == tipo)
    termo = q.strip()
    if caractere_proibido(termo) is not None:
        raise HTTPException(
            HTTP_422,
            "O termo de busca não pode conter caracteres de controle.",
        )
    if termo:
        digitos = somente_digitos(termo)
        rotulo = func.upper(Entidade.rotulo)
        condicoes = [rotulo.like(_padrao_like(termo.upper()), escape="\\")]
        nome = normalizar_nome(termo)
        if nome:
            condicoes.append(rotulo.like(_padrao_like(nome), escape="\\"))
        # CNPJ alfanumérico (raiz, parcial ou completo, com ou sem pontuação): só vale como
        # documento o termo que tem letra E dígito; um nome sem dígitos segue pelo rótulo.
        documento = limpar_documento(termo)
        alfanumerico = bool(
            digitos and documento != digitos and re.fullmatch(r"[0-9A-Z]{8,14}", documento)
        )
        if alfanumerico:
            condicoes.append(Entidade.identificador == documento[:8])
            condicoes.append(Entidade.identificador.like(f"%{documento}%"))
            condicoes.append(Entidade.chave.like(f"%{documento}%"))
        elif digitos:
            condicoes.append(Entidade.identificador.like(f"%{digitos}%"))
            condicoes.append(Entidade.chave.like(f"%{digitos}%"))
            if len(digitos) >= 8:
                # CNPJ completo ou parcial: casa com o CNPJ básico da empresa.
                condicoes.append(Entidade.identificador == digitos[:8])
        if "*" in termo:
            condicoes.append(Entidade.identificador == termo)
        consulta = consulta.where(or_(*condicoes))
    entidades = (
        ctx.sessao.execute(consulta.order_by(Entidade.tipo, Entidade.rotulo)).scalars().all()
    )
    ctx.auditar(
        "entidade.buscar", caso_id, {"q": termo, "tipo": tipo, "resultados": len(entidades)}
    )
    ctx.concluir()
    return [_entidade_resumo(e) for e in entidades]


@roteador.get("/{caso_id}/entidades/{entidade_id}", response_model=esquemas.EntidadeDetalhe)
def ver_entidade(caso_id: str, entidade_id: str, ctx: Contexto = Depends(contexto)):
    ctx.caso_aberto(caso_id, "entidade.ver")
    entidade = (
        ctx.sessao.get(Entidade, entidade_id) if len(entidade_id) <= MAX_IDENTIFICADOR else None
    )
    if entidade is None or entidade.caso_id != caso_id:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"Entidade {entidade_id} não encontrada no caso."
        )
    fontes = []
    for f in entidade.fontes:
        captura = ctx.sessao.get(Captura, f.captura_id)
        if captura is None or captura.caso_id != caso_id:
            continue
        fontes.append(
            {
                "captura_id": f.captura_id,
                "papel": f.papel,
                "fonte": captura.fonte,
                "url_ou_arquivo": captura.url_ou_arquivo,
                "sha256": captura.sha256,
            }
        )
    sugestoes = []
    for s in ctx.sessao.execute(
        select(Sugestao).where(Sugestao.entidade_id == entidade.id, Sugestao.caso_id == caso_id)
    ).scalars():
        candidata = ctx.sessao.get(Entidade, s.candidata_id)
        if candidata is not None and candidata.caso_id != caso_id:
            candidata = None
        sugestoes.append(
            {
                "id": s.id,
                "candidata_id": s.candidata_id,
                "candidata_rotulo": candidata.rotulo if candidata else None,
                "candidata_identificador": candidata.identificador if candidata else None,
                "score": s.score,
                "motivo": s.motivo,
                "criado_em": s.criado_em,
            }
        )
    ctx.auditar("entidade.ver", caso_id, {"entidade_id": entidade_id, "tipo": entidade.tipo})
    ctx.concluir()
    return {
        **_entidade_resumo(entidade),
        "captura_id": entidade.captura_id,
        "fontes": fontes,
        "sugestoes": sugestoes,
    }


@roteador.get("/{caso_id}/grafo", response_model=esquemas.GrafoSaida)
def ver_grafo(
    caso_id: str,
    centro: str | None = Query(default=None, max_length=MAX_IDENTIFICADOR),
    profundidade: int = Query(default=2, ge=0, le=6),
    ctx: Contexto = Depends(contexto),
):
    ctx.caso_aberto(caso_id, "grafo.ver")
    # RNF06 (H29): a vizinhança é consultada no banco, carregando só o subgrafo pedido.
    try:
        saida = grafo.sql.vizinhanca(
            ctx.sessao, caso_id, centro, profundidade, ctx.config.grafo_max_nos
        )
    except grafo.LimiteDoGrafo as erro:
        raise HTTPException(HTTP_422, str(erro)) from erro
    except grafo.ErroGrafo as erro:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(erro)) from erro
    ctx.auditar(
        "grafo.ver",
        caso_id,
        {
            "centro": centro,
            "profundidade": profundidade,
            "nos": len(saida["nos"]),
            "arestas": len(saida["arestas"]),
        },
    )
    ctx.concluir()
    return saida


@roteador.get("/{caso_id}/caminho", response_model=esquemas.GrafoSaida)
def ver_caminho(
    caso_id: str,
    de: str = Query(..., max_length=MAX_IDENTIFICADOR),
    para: str = Query(..., max_length=MAX_IDENTIFICADOR),
    ctx: Contexto = Depends(contexto),
):
    ctx.caso_aberto(caso_id, "caminho.ver")
    try:
        sub = grafo.sql.caminho_mais_curto(
            ctx.sessao, caso_id, de, para, ctx.config.grafo_max_nos_visitados
        )
    except grafo.LimiteDoGrafo as erro:
        raise HTTPException(HTTP_422, str(erro)) from erro
    except grafo.ErroGrafo as erro:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(erro)) from erro
    saida = sub if sub is not None else {"nos": [], "arestas": []}
    ctx.auditar("caminho.ver", caso_id, {"de": de, "para": para, "encontrado": sub is not None})
    ctx.concluir()
    return saida


@roteador.post("/{caso_id}/alertas/executar", response_model=list[esquemas.AlertaSaida])
def executar_alertas(
    caso_id: str,
    corpo: esquemas.ExecutarAlertasEntrada | None = None,
    ctx: Contexto = Depends(contexto),
):
    ctx.caso_aberto(caso_id, "alerta.executar")
    corpo = corpo or esquemas.ExecutarAlertasEntrada()
    try:
        parametros = tipologias.parametros_efetivos(
            ctx.config.parametros_tipologias(), corpo.parametros.model_dump(exclude_none=True)
        )
        alertas = tipologias.executar(
            ctx.sessao,
            caso_id,
            parametros,
            corpo.tipologias,
            ler_captura=ctx.custodia.ler,
            limites_pdf=ctx.config.limites_pdf(),
        )
    except (ValueError, OverflowError) as erro:
        raise HTTPException(HTTP_422, str(erro)) from erro
    ctx.auditar(
        "alerta.executar",
        caso_id,
        {
            "parametros": parametros,
            "tipologias": list(dict.fromkeys(corpo.tipologias or tipologias.ordem_tipologias())),
            "alertas": len(alertas),
        },
    )
    ctx.concluir()
    return [_alerta_para_dict(a) for a in alertas]


@roteador.get("/{caso_id}/alertas", response_model=list[esquemas.AlertaSaida])
def listar_alertas(caso_id: str, ctx: Contexto = Depends(contexto)):
    """Alertas do caso por severidade (alta primeiro), com a política de erro da tipologia
    (RF14) e a situação de cada um (RF12)."""
    ctx.caso(caso_id)
    alertas = tipologias.ordenar(
        list(ctx.sessao.execute(select(Alerta).where(Alerta.caso_id == caso_id)).scalars())
    )
    ctx.auditar(
        "alerta.listar",
        caso_id,
        {"alertas": len(alertas), "pendentes": sum(a.situacao == "pendente" for a in alertas)},
    )
    ctx.concluir()
    return [_alerta_para_dict(a) for a in alertas]


@roteador.post("/{caso_id}/alertas/{alerta_id}/decisao", response_model=esquemas.AlertaSaida)
def decidir_alerta(
    caso_id: str,
    alerta_id: str,
    corpo: esquemas.DecisaoEntrada,
    ctx: Contexto = Depends(contexto),
):
    """Confirma ou descarta o alerta, com justificativa (RF12, H12).

    O alerta é pista: a decisão é do investigador. Cada decisão acrescenta uma linha ao histórico
    do alerta e uma entrada à trilha de auditoria; rever um alerta já decidido é uma nova
    decisão, e as anteriores continuam no histórico."""
    ctx.caso_aberto(caso_id, "alerta.decidir")
    alerta = _obter_alerta(ctx, caso_id, alerta_id, travar=True)
    situacao_anterior = alerta.situacao or "pendente"
    decisao = AlertaDecisao(
        alerta_id=alerta.id,
        caso_id=caso_id,
        decisao=corpo.decisao,
        justificativa=corpo.justificativa,
        usuario=ctx.usuario.usuario,
        perfil=ctx.usuario.perfil,
        decidido_em=iso_utc(),
    )
    alerta.decisoes.append(decisao)
    alerta.situacao = decisao.decisao
    alerta.decidido_por = decisao.usuario
    alerta.decidido_em = decisao.decidido_em
    alerta.justificativa_decisao = decisao.justificativa
    ctx.sessao.flush()
    ctx.auditar(
        "alerta.decidir",
        caso_id,
        {
            "alerta_id": alerta.id,
            "tipologia": alerta.tipologia,
            "decisao": decisao.decisao,
            "justificativa_decisao": decisao.justificativa,
            "situacao_anterior": situacao_anterior,
            "revisao": situacao_anterior != "pendente",
            "decisao_id": decisao.id,
            "decidido_em": decisao.decidido_em,
        },
    )
    ctx.concluir()
    return _alerta_para_dict(alerta)


@roteador.get(
    "/{caso_id}/relatorio",
    responses={200: {"content": {"application/json": {}, "text/markdown": {}}}},
)
def gerar_relatorio(
    caso_id: str,
    formato: str = Query(default="json", pattern="^(json|md)$"),
    ctx: Contexto = Depends(contexto),
):
    caso = ctx.caso(caso_id)
    # Primeiro o trabalho pesado (verificação da cadeia e releitura das capturas); a entrada de
    # auditoria vem por último, para que a trava da cadeia dure só o instante da gravação.
    dados = relatorio.gerar(
        ctx.sessao,
        caso,
        ctx.custodia,
        gerado_por=ctx.usuario.usuario,
        perfil=ctx.usuario.perfil,
        chave_auditoria=ctx.chave_auditoria,
        ancora=ctx.ancora,
    )
    ctx.auditar("relatorio.gerar", caso_id, {"formato": formato})
    ctx.concluir()
    if formato == "md":
        return PlainTextResponse(
            relatorio.para_markdown(dados), media_type="text/markdown; charset=utf-8"
        )
    # ``gerar`` já devolve JSON estrito (sem NaN/Infinity), então a serialização não falha.
    return JSONResponse(dados)
