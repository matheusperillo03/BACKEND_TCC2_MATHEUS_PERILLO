"""Rotas da importação sob ordem judicial (RF10). Módulo desligado por padrão: com
``IMPORTACAO_JUDICIAL_ATIVA=0`` só a rota de estado responde; as demais dão 404.

Toda chamada às rotas de caso, inclusive as de leitura, gera uma entrada na trilha de auditoria
(RNF02) com quem, quando, a justificativa e o que foi pedido. Os detalhes da auditoria nunca levam
número de conta, documento nem conteúdo de registro: só identificadores e contagens.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

from buscador.api.deps import MAX_IDENTIFICADOR, Contexto, contexto, usuario_atual
from buscador.api.esquemas import TextoLinha, _com_conteudo
from buscador.config import Usuario
from buscador.importacao_judicial import ErroImportacao, Modulo, leiaute
from buscador.importacao_judicial.servico import ORIGENS
from buscador.resolucao.normalizacao import normalizar_cnpj, somente_digitos

HTTP_422 = 422
MENSAGEM_DESLIGADO = (
    "O módulo de importação sob ordem judicial está desligado (IMPORTACAO_JUDICIAL_ATIVA)."
)

roteador_estado = APIRouter(prefix="/importacao-judicial", tags=["importação judicial"])
roteador = APIRouter(prefix="/casos/{caso_id}/importacao-judicial", tags=["importação judicial"])

Texto = Annotated[TextoLinha, AfterValidator(_com_conteudo)]


def _data_iso(texto: str) -> str:
    try:
        return date.fromisoformat(texto).isoformat()
    except ValueError as erro:
        raise ValueError("data inválida (esperado AAAA-MM-DD)") from erro


Data = Annotated[str, Field(min_length=10, max_length=10), AfterValidator(_data_iso)]


def _documento(texto: str) -> str:
    """CPF (11 dígitos) ou CNPJ (14 posições), com ou sem pontuação."""
    digitos = somente_digitos(texto)
    if len(digitos) == 11 and digitos == "".join(c for c in texto if c.isalnum()):
        return digitos
    cnpj = normalizar_cnpj(texto)
    if not cnpj:
        raise ValueError("documento inválido (esperado CPF ou CNPJ)")
    return cnpj


class ContaAutorizada(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    banco: str = Field(pattern=r"^\d{1,3}$")
    agencia: str = Field(pattern=r"^\d{1,5}$")
    conta: str = Field(pattern=r"^[0-9A-Za-z-]{1,20}$")


class DecisaoEntrada(BaseModel):
    """Ato de entrada: sem a decisão judicial registrada, a importação não abre."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    numero_processo: Texto = Field(min_length=1, max_length=100)
    numero_decisao: Texto = Field(min_length=1, max_length=100)
    juizo: Texto = Field(min_length=1, max_length=200)
    data_decisao: Data
    periodo_inicio: Data
    periodo_fim: Data
    contas: list[ContaAutorizada] = Field(min_length=1, max_length=200)
    investigados: list[Annotated[str, Field(max_length=20), AfterValidator(_documento)]] = Field(
        default_factory=list, max_length=200
    )
    descricao_escopo: Texto = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def _periodo_coerente(self) -> DecisaoEntrada:
        if self.periodo_inicio > self.periodo_fim:
            raise ValueError("o início do período autorizado é posterior ao fim")
        return self


class ImportacaoEntrada(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    diretorio: TextoLinha = Field(min_length=1, max_length=500)
    origem: Literal["simba", "oficio", "midia"]
    descricao_origem: TextoLinha = Field(default="", max_length=500)


def _modulo(request: Request) -> Modulo:
    modulo = getattr(request.app.state, "importacao_judicial", None)
    if modulo is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, MENSAGEM_DESLIGADO)
    return modulo


@roteador_estado.get("/estado")
def estado(request: Request, _usuario: Usuario = Depends(usuario_atual)) -> dict:
    """Diz se o módulo está ligado. Não é dado de caso: exige só o token."""
    config = request.app.state.config
    return {
        "ativa": getattr(request.app.state, "importacao_judicial", None) is not None,
        "perfis_autorizados": sorted(config.perfis_importacao_judicial()),
        "leiaute": leiaute.NORMA,
        "arquivos": list(leiaute.TIPOS),
        "origens": list(ORIGENS),
        "somente_sintetico": True,
    }


def _concluir(ctx: Contexto, sigilosa, acao: str, caso_id: str, detalhes: dict) -> None:
    """Auditoria primeiro: se a entrada da trilha não for gravada, nada do dado sigiloso é
    confirmado (nenhum acesso fica sem registro)."""
    try:
        ctx.auditar(f"importacao_judicial.{acao}", caso_id, detalhes)
        ctx.concluir()
    except BaseException:
        sigilosa.rollback()
        raise
    sigilosa.commit()


@roteador.post("/decisoes", status_code=status.HTTP_201_CREATED)
def registrar_decisao(
    caso_id: str,
    corpo: DecisaoEntrada,
    ctx: Contexto = Depends(contexto),
    modulo: Modulo = Depends(_modulo),
) -> dict:
    ctx.caso_aberto(caso_id, "importacao_judicial.decisao.registrar", "investigador")
    autorizados = ctx.config.perfis_importacao_judicial()
    if ctx.usuario.perfil not in autorizados:
        ctx.auditar(
            "importacao_judicial.perfil_negado",
            caso_id,
            {"perfis_exigidos": sorted(autorizados)},
        )
        ctx.concluir()
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            f"Registrar decisão judicial é restrito aos perfis: {', '.join(sorted(autorizados))}.",
        )
    with modulo.sessao() as sigilosa:
        decisao = modulo.registrar_decisao(
            sigilosa, caso_id=caso_id, usuario=ctx.usuario.usuario, dados=corpo.model_dump()
        )
        saida = modulo.decisao_para_dict(sigilosa, decisao)
        _concluir(
            ctx,
            sigilosa,
            "decisao.registrar",
            caso_id,
            {
                "decisao_id": decisao.id,
                "numero_processo": decisao.numero_processo,
                "numero_decisao": decisao.numero_decisao,
                "juizo": decisao.juizo,
                "data_decisao": decisao.data_decisao,
                "periodo": [decisao.periodo_inicio, decisao.periodo_fim],
                "contas_autorizadas": len(corpo.contas),
                "investigados": len(corpo.investigados),
            },
        )
    return saida


@roteador.get("/decisoes")
def listar_decisoes(
    caso_id: str, ctx: Contexto = Depends(contexto), modulo: Modulo = Depends(_modulo)
) -> list[dict]:
    ctx.caso_aberto(caso_id, "importacao_judicial.decisao.listar", "investigador")
    with modulo.sessao() as sigilosa:
        saida = [
            modulo.decisao_para_dict(sigilosa, d) for d in modulo.listar_decisoes(sigilosa, caso_id)
        ]
        _concluir(ctx, sigilosa, "decisao.listar", caso_id, {"decisoes": len(saida)})
    return saida


@roteador.post("/decisoes/{decisao_id}/importacoes")
def importar(
    caso_id: str,
    decisao_id: str,
    corpo: ImportacaoEntrada,
    ctx: Contexto = Depends(contexto),
    modulo: Modulo = Depends(_modulo),
) -> dict:
    ctx.caso_aberto(caso_id, "importacao_judicial.importar", "investigador")
    with modulo.sessao() as sigilosa:
        try:
            decisao = modulo.obter_decisao(sigilosa, caso_id, decisao_id)
        except LookupError as erro:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(erro)) from erro
        try:
            importacao = modulo.importar(
                sigilosa,
                decisao=decisao,
                usuario=ctx.usuario.usuario,
                diretorio=corpo.diretorio,
                origem=corpo.origem,
                descricao_origem=corpo.descricao_origem,
            )
        except ErroImportacao as erro:
            sigilosa.rollback()
            ctx.auditar(
                "importacao_judicial.importar.recusada",
                caso_id,
                {"decisao_id": decisao_id, "motivo": str(erro)[:300]},
            )
            ctx.concluir()
            raise HTTPException(HTTP_422, str(erro)) from erro
        saida = modulo.importacao_para_dict(sigilosa, importacao)
        _concluir(
            ctx,
            sigilosa,
            "importar",
            caso_id,
            {
                "decisao_id": decisao_id,
                "importacao_id": importacao.id,
                "origem": corpo.origem,
                "arquivos": [
                    {"tipo": a["tipo"], "sha256": a["sha256"], "tamanho_bytes": a["tamanho_bytes"]}
                    for a in saida["arquivos"]
                ],
                "totais": saida["totais"],
            },
        )
    return saida


@roteador.get("/importacoes")
def listar_importacoes(
    caso_id: str, ctx: Contexto = Depends(contexto), modulo: Modulo = Depends(_modulo)
) -> list[dict]:
    ctx.caso_aberto(caso_id, "importacao_judicial.importacao.listar", "investigador")
    with modulo.sessao() as sigilosa:
        saida = [
            modulo.importacao_para_dict(sigilosa, i)
            for i in modulo.listar_importacoes(sigilosa, caso_id)
        ]
        _concluir(ctx, sigilosa, "importacao.listar", caso_id, {"importacoes": len(saida)})
    return saida


@roteador.get("/registros")
def listar_registros(
    caso_id: str,
    decisao_id: str | None = Query(default=None, max_length=MAX_IDENTIFICADOR),
    tipo: Literal["AGENCIAS", "CONTAS", "TITULARES", "EXTRATO", "ORIGEM_DESTINO"] | None = None,
    quarentena: bool | None = None,
    limite: int = Query(default=50, ge=1, le=200),
    deslocamento: int = Query(default=0, ge=0),
    ctx: Contexto = Depends(contexto),
    modulo: Modulo = Depends(_modulo),
) -> dict:
    """Leitura do dado sigiloso: cada chamada fica na auditoria, com os filtros e a contagem."""
    ctx.caso_aberto(caso_id, "importacao_judicial.registro.ler", "investigador")
    with modulo.sessao() as sigilosa:
        saida = modulo.listar_registros(
            sigilosa,
            caso_id,
            decisao_id=decisao_id,
            tipo=tipo,
            quarentena=quarentena,
            limite=limite,
            deslocamento=deslocamento,
        )
        _concluir(
            ctx,
            sigilosa,
            "registro.ler",
            caso_id,
            {
                "decisao_id": decisao_id,
                "tipo": tipo,
                "quarentena": quarentena,
                "limite": limite,
                "deslocamento": deslocamento,
                "registros_lidos": len(saida["itens"]),
                "decisoes_lidas": sorted({i["decisao_id"] for i in saida["itens"]}),
            },
        )
    return saida
