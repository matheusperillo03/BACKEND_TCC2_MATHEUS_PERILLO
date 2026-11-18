"""Esquemas do horizonte 3: autenticação, membros, anotações, tarefas, exportação e IA."""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

from buscador.api.esquemas import TextoLinha, TextoLivre, _com_conteudo

TextoComConteudo = Annotated[TextoLivre, AfterValidator(_com_conteudo)]
PapelCaso = Literal["responsavel", "investigador", "leitor"]
Perfil = Literal["analista", "delegado", "admin"]


# ---- Autenticação (H23) ---------------------------------------------------------------------


class LoginEntrada(BaseModel):
    usuario: str = Field(min_length=1, max_length=100)
    senha: str = Field(min_length=1, max_length=256)


class CodigoEntrada(BaseModel):
    codigo: str = Field(min_length=6, max_length=6, pattern=r"^[0-9]{6}$")


class SessaoSaida(BaseModel):
    # mfa: falta o código; cadastro_mfa: falta cadastrar o segundo fator; ativa: pode usar a API.
    etapa: Literal["mfa", "cadastro_mfa", "ativa"]
    token: str
    expira_em: str
    usuario: str
    perfil: str
    # Só em "cadastro_mfa": o segredo (Base32) e a URI otpauth:// para o aplicativo autenticador.
    segredo: str | None = None
    otpauth_uri: str | None = None


class SessaoAtual(BaseModel):
    usuario: str
    perfil: str
    autenticacao: Literal["sessao", "token_dev"]
    mfa_ativo: bool
    expira_em: str | None
    inatividade_minutos: int | None


class TrocaSenhaEntrada(BaseModel):
    senha_atual: str = Field(min_length=1, max_length=256)
    senha_nova: str = Field(min_length=1, max_length=256)


class UsuarioEntrada(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    usuario: TextoLinha = Field(min_length=3, max_length=64)
    nome: TextoLinha = Field(default="", max_length=200)
    perfil: Perfil
    senha: str = Field(min_length=1, max_length=256)


class UsuarioAlteracao(BaseModel):
    ativo: bool | None = None
    perfil: Perfil | None = None
    senha: str | None = Field(default=None, min_length=1, max_length=256)
    redefinir_mfa: bool = False


class UsuarioAdminSaida(BaseModel):
    usuario: str
    nome: str
    perfil: str
    ativo: bool
    mfa_ativo: bool
    mfa_obrigatorio: bool
    bloqueado_ate: str | None
    criado_por: str
    criado_em: str


# ---- Membros do caso (H23) ------------------------------------------------------------------


class MembroEntrada(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    usuario: TextoLinha = Field(min_length=1, max_length=100)
    papel: PapelCaso


class MembroSaida(BaseModel):
    usuario: str
    papel: str
    adicionado_por: str
    adicionado_em: str


# ---- Anotações e tarefas (H37) --------------------------------------------------------------


class AnotacaoEntrada(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    alvo_tipo: Literal["entidade", "alerta"]
    alvo_id: str = Field(min_length=1, max_length=64)
    texto: TextoComConteudo = Field(min_length=1, max_length=5_000)


class AnotacaoSaida(BaseModel):
    id: str
    alvo_tipo: str
    alvo_id: str
    alvo_rotulo: str | None = None
    autor: str
    criado_em: str
    texto: str


SituacaoTarefa = Literal["aberta", "em_andamento", "concluida", "cancelada"]


class TarefaEntrada(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    titulo: Annotated[TextoLinha, AfterValidator(_com_conteudo)] = Field(
        min_length=1, max_length=300
    )
    descricao: TextoLivre = Field(default="", max_length=5_000)
    responsavel: TextoLinha | None = Field(default=None, max_length=100)
    prazo: date | None = None


class TarefaAlteracao(BaseModel):
    """Só os campos enviados mudam. ``responsavel`` e ``prazo`` aceitam ``null`` (limpar)."""

    model_config = ConfigDict(str_strip_whitespace=True)

    titulo: Annotated[TextoLinha, AfterValidator(_com_conteudo)] | None = Field(
        default=None, min_length=1, max_length=300
    )
    descricao: TextoLivre | None = Field(default=None, max_length=5_000)
    responsavel: TextoLinha | None = Field(default=None, max_length=100)
    situacao: SituacaoTarefa | None = None
    prazo: date | None = None


class TarefaEventoSaida(BaseModel):
    usuario: str
    quando: str
    alteracoes: dict[str, Any]


class TarefaSaida(BaseModel):
    id: str
    titulo: str
    descricao: str
    responsavel: str | None
    situacao: str
    prazo: str | None
    atrasada: bool
    criado_por: str
    criado_em: str
    atualizado_em: str
    historico: list[TarefaEventoSaida]


# ---- Extração assistida por IA (H22) --------------------------------------------------------


class IaEstado(BaseModel):
    ativa: bool
    provedor: str | None
    modelo: str | None
    local: bool = True
    tipos: list[str]


class TextoCaptura(BaseModel):
    captura_id: str
    sha256: str
    codificacao: str
    caracteres: int
    truncado: bool
    # ``sha256`` é o do arquivo original na custódia (no PDF, os bytes do PDF). O texto é
    # derivado: ``formato`` diz de onde veio (texto | pdf) e ``texto_sha256`` identifica o texto
    # extraído, ao qual se referem as posições dos trechos. ``confere_com_coleta`` compara esse
    # resumo com o gravado na coleta (``None`` se a captura não o tem).
    formato: str = "texto"
    paginas: int | None = None
    extrator: str = ""
    texto_sha256: str = ""
    confere_com_coleta: bool | None = None
    # RNF04: o texto devolvido leva todo CPF descaracterizado (mesmo comprimento); este campo diz
    # se o texto original tinha CPF completo. ``texto_sha256`` é o resumo do texto original.
    cpfs_descaracterizados: bool = False
    texto: str


class SugestaoIaSaida(BaseModel):
    id: str
    extracao_id: str
    captura_id: str
    captura_sha256: str
    tipo: str
    valor: str
    campos: dict[str, str]
    trecho: str
    posicao_inicio: int
    posicao_fim: int
    confianca: float | None
    situacao: str
    decidido_por: str | None
    decidido_em: str | None
    justificativa_decisao: str | None
    entidade_id: str | None
    modelo: str
    provedor: str


class RecusaIa(BaseModel):
    motivo: str
    tipo: str
    valor: str
    trecho: str


class BlocoExtracaoIa(BaseModel):
    numero: int
    # pendente | em_andamento | concluido | falho
    situacao: str
    caracteres: int
    tentativas: int
    causa: str = ""
    duracao_ms: int = 0
    # Bloco que foi dividido ao meio e deu origem a este (saída cortada ou tempo esgotado).
    pai_numero: int | None = None


class ProgressoExtracaoIa(BaseModel):
    """Andamento de uma execução: só números e códigos, nenhum texto do documento."""

    id: str
    captura_id: str
    # na_fila | em_andamento | sucesso | concluida_com_falhas | falha | cancelada | interrompida
    situacao: str
    motivo: str = ""
    ativa: bool
    retomavel: bool
    cancelamento_pedido: bool = False
    posicao_na_fila: int | None = None
    blocos_total: int
    blocos_concluidos: int
    blocos_falhos: int
    blocos_pendentes: int
    bloco_atual: int | None = None
    itens_recebidos: int
    itens_aceitos: int
    itens_recusados: int
    tempo_decorrido_ms: int
    # ``None`` antes do primeiro bloco concluído e depois do fim.
    estimativa_restante_ms: int | None = None
    retomadas: int = 0
    iniciado_em: str
    concluido_em: str | None = None
    blocos: list[BlocoExtracaoIa] = []


class ExtracaoIaSaida(BaseModel):
    id: str
    captura_id: str
    captura_sha256: str
    provedor: str
    modelo: str
    modelo_versao: str
    servidor_versao: str
    parametros: dict[str, Any]
    prompt_sha256: str
    situacao: str
    motivo: str
    blocos: int
    itens_recebidos: int
    itens_aceitos: int
    itens_recusados: int
    recusas: list[RecusaIa]
    # Tempos e tokens medidos (por bloco e no total); ``None`` em execução antiga.
    metricas: dict[str, Any] | None = None
    usuario: str
    iniciado_em: str
    concluido_em: str | None
    progresso: ProgressoExtracaoIa | None = None
    sugestoes: list[SugestaoIaSaida] = []


class DecisaoSugestaoIa(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    decisao: Literal["aceita", "recusada"]
    justificativa: TextoComConteudo = Field(min_length=1, max_length=2_000)
