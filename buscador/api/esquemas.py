"""Esquemas Pydantic do contrato v0 (docs/mvp-escopo.md, seção 5)."""

from __future__ import annotations

import math
from datetime import date
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_validator

from buscador import retencao
from buscador.seguranca import validar_texto

# Texto de uma linha (sem controle nem invisíveis) e texto livre (aceita quebra de linha e tab).
TextoLinha = Annotated[str, AfterValidator(validar_texto)]
TextoLivre = Annotated[str, AfterValidator(lambda t: validar_texto(t, permitir_quebras=True))]

MAX_NOS_PARAMETROS = 200
MAX_PROFUNDIDADE_PARAMETROS = 4
MAX_TEXTO_PARAMETRO = 1_000


def validar_json_limitado(valor: object) -> object:
    """JSON genérico vindo do cliente: só números finitos, textos sem caracteres de controle e
    tamanho limitado (nós, profundidade e comprimento de texto)."""
    restantes = MAX_NOS_PARAMETROS

    def visitar(no: object, profundidade: int) -> None:
        nonlocal restantes
        restantes -= 1
        if restantes < 0:
            raise ValueError(f"parâmetros com mais de {MAX_NOS_PARAMETROS} valores")
        if isinstance(no, float) and not math.isfinite(no):
            raise ValueError("números NaN ou Infinity não são aceitos")
        if isinstance(no, str):
            if len(no) > MAX_TEXTO_PARAMETRO:
                raise ValueError(f"texto com mais de {MAX_TEXTO_PARAMETRO} caracteres")
            validar_texto(no)
        elif isinstance(no, dict | list):
            if profundidade >= MAX_PROFUNDIDADE_PARAMETROS:
                raise ValueError("parâmetros aninhados demais")
            if isinstance(no, dict):
                for chave in no:
                    if not isinstance(chave, str) or len(chave) > 64:
                        raise ValueError("nome de parâmetro inválido")
                    validar_texto(chave)
            for filho in no.values() if isinstance(no, dict) else no:
                visitar(filho, profundidade + 1)

    visitar(valor, 0)
    return valor


class Saude(BaseModel):
    status: str = "ok"


class UsuarioSaida(BaseModel):
    usuario: str
    perfil: str


def _com_conteudo(texto: str) -> str:
    if not any(c.isalnum() for c in texto):
        raise ValueError("o texto precisa conter letras ou números")
    return texto


class CasoEntrada(BaseModel):
    """RNF04 (H17): todo caso nasce com finalidade declarada e prazo de retenção."""

    model_config = ConfigDict(str_strip_whitespace=True)

    titulo: TextoLinha = Field(min_length=1, max_length=300)
    descricao: TextoLivre = Field(default="", max_length=5_000)
    numero_procedimento: TextoLinha = Field(default="", max_length=100)
    finalidade: Annotated[TextoLivre, AfterValidator(_com_conteudo)] = Field(
        min_length=1, max_length=2_000
    )
    # Data (AAAA-MM-DD) até a qual o caso aceita coletas e consultas; o próprio dia ainda vale.
    retencao_ate: date

    @field_validator("retencao_ate")
    @classmethod
    def _prazo_nao_vencido(cls, valor: date) -> date:
        if valor < retencao.hoje_utc():
            raise ValueError("o prazo de retenção não pode ser uma data passada")
        return valor


class CasoSaida(BaseModel):
    id: str
    titulo: str
    descricao: str
    numero_procedimento: str
    criado_por: str
    criado_em: str
    finalidade: str
    retencao_ate: str | None
    # Verdadeiro depois do prazo de retenção: novas coletas e consultas são recusadas.
    somente_leitura: bool
    # Papel de quem consulta neste caso (H23): responsavel | investigador | leitor.
    meu_papel: str | None = None


class CasoDetalhe(CasoSaida):
    contagens: dict[str, int]


class ColetaEntrada(BaseModel):
    """Os parâmetros passam aqui só pelo filtro genérico (JSON finito e limitado). A validação
    por fonte, com pydantic, fica em ``buscador.conectores.parametros`` e roda na rota antes de
    qualquer efeito colateral, para que a tentativa recusada também fique na auditoria."""

    fonte: Literal[
        "cnpj_arquivo",
        "pncp",
        "portal_transparencia",
        "pncp_termos",
        "tse_arquivo",
        "edital_arquivo",
    ]
    parametros: dict[str, Any] = Field(default_factory=dict, max_length=20)

    @field_validator("parametros")
    @classmethod
    def _parametros_limitados(cls, valor: dict) -> dict:
        validar_json_limitado(valor)
        return valor


class ColetaSaida(BaseModel):
    fonte: str
    captura_ids: list[str]
    entidades_novas: int
    entidades_atualizadas: int
    arestas_novas: int
    sugestoes_novas: int
    registros: int
    avisos: list[str]
    # A coleta parou antes de ler todas as páginas que a fonte informou (dados parciais).
    incompleta: bool = False
    paginas_restantes: int | None = None
    # RNF12 (H15) e RF07 (H14): registro da coleta, resultado por fonte e conector usado.
    coleta_id: str
    situacao: Literal["sucesso", "falha", "em_andamento", "nao_configurado"]
    tentativas: int
    motivo: str
    conector_nome: str
    conector_versao: str
    recoleta_de: str | None = None


class ColetaRegistro(BaseModel):
    """Uma coleta do caso (bem-sucedida ou não), como fica no histórico."""

    id: str
    fonte: str
    parametros: dict[str, Any]
    conector_nome: str
    conector_versao: str
    situacao: Literal["sucesso", "falha", "em_andamento", "nao_configurado"]
    tentativas: int
    motivo: str
    registros: int
    avisos: list[str]
    # Derivado dos avisos gravados: há um que começa com "Coleta incompleta:".
    incompleta: bool = False
    usuario: str
    iniciado_em: str
    concluido_em: str | None
    recoleta_de: str | None
    captura_ids: list[str]


class ConectorSaida(BaseModel):
    nome: str
    versao: str


class CampoAlterado(BaseModel):
    campo: str
    antes: str | None
    depois: str | None


class DiferencaRegistro(BaseModel):
    registro: str | None
    tipo: Literal["alterado", "adicionado", "removido"]
    campos: list[CampoAlterado]


class ContagemRegistros(BaseModel):
    adicionados: int
    removidos: int
    alterados: int
    iguais: int | None


class ComparacaoCaptura(BaseModel):
    captura_id: str
    anterior_id: str
    fonte: str
    url_ou_arquivo: str
    coletado_em: str
    anterior_coletado_em: str
    sha256: str
    anterior_sha256: str
    mesmo_hash: bool
    tamanho_bytes: int
    anterior_tamanho_bytes: int
    conector: ConectorSaida
    anterior_conector: ConectorSaida
    mesma_versao_conector: bool
    registros_comparados: bool
    registros: ContagemRegistros
    campos_alterados: list[str]
    diferencas: list[DiferencaRegistro]
    diferencas_omitidas: int
    observacao: str | None


class ComparacaoColeta(BaseModel):
    coleta_id: str
    anterior_id: str
    fonte: str
    conector: ConectorSaida
    anterior_conector: ConectorSaida
    mesmo_conteudo: bool
    capturas_iguais: int
    capturas_diferentes: int
    capturas: list[ComparacaoCaptura]
    capturas_sem_anterior: list[str]
    capturas_anteriores_sem_correspondente: list[str]


class RecoletaSaida(ColetaSaida):
    comparacao: ComparacaoColeta


class ParametroTipologia(BaseModel):
    nome: str
    valor: Any
    descricao: str
    # Verdadeiro: pode ser sobrescrito em POST /casos/{id}/alertas/executar.
    por_execucao: bool


class TipologiaSaida(BaseModel):
    """Entrada do catálogo de tipologias: regra, base, política de erro e configuração em vigor."""

    codigo: str
    nome: str
    historia: str
    severidade: str
    prefere_evitar: str
    politica_erro: str
    regra: str
    base: str
    fontes: list[str]
    parametros: list[ParametroTipologia]
    # "contratacao" (T), "lavagem" (L) ou "combinacao".
    grupo: str = "contratacao"
    # Sinal fraco: sozinho não indica nada; só pesa no alerta de combinação.
    sinal_fraco: bool = False
    # Peso da tipologia no alerta de combinação.
    peso: float = 1.0


class MonitoramentoEntrada(BaseModel):
    """RF06 (H18): marca uma entidade do caso para acompanhamento."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    entidade_id: TextoLinha = Field(min_length=1, max_length=64)
    # Periodicidade em horas (24 = diária, 168 = semanal, 720 = mensal).
    periodicidade_horas: int = Field(default=24, ge=1, le=8760)


class MonitoramentoSituacaoEntrada(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ativo: bool
    periodicidade_horas: int | None = Field(default=None, ge=1, le=8760)


class RecoletaMonitorada(BaseModel):
    coleta_id: str | None
    recoleta_de: str
    fonte: str
    situacao: str
    mesmo_conteudo: bool | None = None
    motivo: str = ""


class ExecucaoMonitoramento(BaseModel):
    id: str
    monitoramento_id: str
    origem: str
    usuario: str
    iniciado_em: str
    concluido_em: str | None
    situacao: str
    coletas: list[RecoletaMonitorada]
    avisos_gerados: int
    motivo: str


class MonitoramentoSaida(BaseModel):
    id: str
    caso_id: str
    entidade_id: str
    entidade_rotulo: str | None
    entidade_tipo: str | None
    entidade_identificador: str | None
    periodicidade_horas: int
    ativo: bool
    motivo_inativo: str
    justificativa: str
    criado_por: str
    criado_em: str
    proxima_execucao_em: str
    ultima_execucao_em: str | None
    # Coletas que serão refeitas (fonte e coleta mais recente de cada uma).
    coletas_de_origem: list[dict[str, Any]]
    ultima_execucao: ExecucaoMonitoramento | None


class AvisoSaida(BaseModel):
    id: str
    caso_id: str
    monitoramento_id: str
    execucao_id: str
    entidade_id: str
    entidade_rotulo: str | None
    tipo: Literal["quadro_societario", "contrato_novo", "sancao_nova", "termo_novo"]
    titulo: str
    descricao: str
    detalhes: dict[str, Any]
    captura_ids: list[str]
    criado_em: str
    lido_em: str | None
    lido_por: str | None


class ExecucaoMonitoramentoSaida(ExecucaoMonitoramento):
    avisos: list[AvisoSaida]


class EntidadeResumo(BaseModel):
    id: str
    tipo: str
    rotulo: str
    identificador: str
    atributos: dict[str, Any]


class SugestaoSaida(BaseModel):
    id: str
    candidata_id: str
    candidata_rotulo: str | None
    candidata_identificador: str | None
    score: float
    motivo: str
    criado_em: str


class FonteSaida(BaseModel):
    captura_id: str
    papel: str
    fonte: str
    url_ou_arquivo: str
    sha256: str


class EntidadeDetalhe(EntidadeResumo):
    captura_id: str
    fontes: list[FonteSaida]
    sugestoes: list[SugestaoSaida]


class NoSaida(BaseModel):
    id: str
    tipo: str | None
    rotulo: str | None
    identificador: str | None = None


class ArestaSaida(BaseModel):
    id: str
    origem: str
    destino: str
    tipo: str | None
    atributos: dict[str, Any]
    captura_id: str | None


class GrafoSaida(BaseModel):
    nos: list[NoSaida]
    arestas: list[ArestaSaida]


class ParametrosTipologias(BaseModel):
    """Limiares por execução, nas mesmas faixas da configuração. NaN e Infinity são recusados."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    t1_dias_minimos: int | None = Field(default=None, ge=0, le=36_500)
    t2_minimo_empresas: int | None = Field(default=None, ge=2, le=10_000)
    t3_razao_minima: float | None = Field(default=None, gt=0, le=1e9)
    t5_minimo_contratacoes: int | None = Field(default=None, ge=2, le=1_000)
    t8_percentual_limite: float | None = Field(default=None, gt=0, le=1_000)
    t8_percentual_limite_reforma: float | None = Field(default=None, gt=0, le=1_000)
    # Tipologias de lavagem (L).
    l1_minimo_estabelecimentos: int | None = Field(default=None, ge=2, le=10_000)
    l1_janela_abertura_dias: int | None = Field(default=None, ge=1, le=36_500)
    l1_minimo_abertas_na_janela: int | None = Field(default=None, ge=2, le=10_000)
    l3_minimo_empresas: int | None = Field(default=None, ge=2, le=10_000)
    l3_minimo_abertas_na_janela: int | None = Field(default=None, ge=1, le=10_000)
    l3_janela_meses: int | None = Field(default=None, ge=1, le=1_200)
    l4_minimo_niveis: int | None = Field(default=None, ge=2, le=20)
    l7_dias_apos_abertura: int | None = Field(default=None, ge=0, le=36_500)
    l7_meses_antes_contrato: int | None = Field(default=None, ge=1, le=1_200)
    l14_valor_minimo: float | None = Field(default=None, ge=0, le=1e12)
    l15_minimo_cnaes: int | None = Field(default=None, ge=2, le=1_000)
    l15_minimo_categorias: int | None = Field(default=None, ge=2, le=100)
    l16_dias_minimos: int | None = Field(default=None, ge=0, le=36_500)
    # Alerta de combinação.
    comb_minimo_tipologias: int | None = Field(default=None, ge=2, le=30)
    comb_peso_minimo: float | None = Field(default=None, gt=0, le=100)


CodigoTipologia = Literal[
    "T1", "T2", "T3", "T4", "T5", "T6", "T7", "T8", "T9", "T10",
    "L1", "L3", "L4", "L5", "L6", "L7", "L8", "L10", "L14", "L15", "L16",
    "COMB",
]  # fmt: skip


class ExecutarAlertasEntrada(BaseModel):
    model_config = ConfigDict(extra="forbid")

    parametros: ParametrosTipologias = Field(default_factory=ParametrosTipologias)
    tipologias: list[CodigoTipologia] | None = Field(default=None, max_length=30)


class DecisaoEntrada(BaseModel):
    """RF12 (H12): o investigador confirma ou descarta o alerta, sempre com justificativa."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    decisao: Literal["confirmado", "descartado"]
    justificativa: Annotated[TextoLinha, AfterValidator(_com_conteudo)] = Field(
        min_length=1, max_length=500
    )


class DecisaoSaida(BaseModel):
    decisao: Literal["confirmado", "descartado"]
    justificativa: str
    usuario: str
    perfil: str
    decidido_em: str


class EvidenciaSaida(BaseModel):
    descricao: str
    fonte: str
    captura_id: str
    url_ou_arquivo: str
    sha256: str


class AlertaSaida(BaseModel):
    id: str
    tipologia: str
    titulo: str
    motivo: str
    severidade: str
    entidades: list[str]
    evidencias: list[EvidenciaSaida]
    parametros: dict[str, Any]
    criado_em: str
    # RF14 (H13): nome, severidade e política de erro vêm da tabela única das tipologias.
    tipologia_nome: str
    prefere_evitar: str
    politica_erro: str
    grupo: str = "contratacao"
    sinal_fraco: bool = False
    peso: float = 1.0
    # RF12 (H12): situação, decisão em vigor e histórico completo das decisões.
    situacao: Literal["pendente", "confirmado", "descartado"]
    decisao: DecisaoSaida | None
    decisoes: list[DecisaoSaida]
    # Falso: a última execução da tipologia não detectou mais o alerta (mantido pela decisão).
    ativo: bool


class CapturaSaida(BaseModel):
    id: str
    caso_id: str
    fonte: str
    url_ou_arquivo: str
    coletado_em: str
    sha256: str
    metadados: dict[str, Any]
    tamanho_bytes: int
    conector_nome: str
    conector_versao: str
    coleta_id: str | None


class VerificacaoCaptura(BaseModel):
    integra: bool
    sha256_registrado: str
    sha256_recalculado: str | None
    arquivo_encontrado: bool
    # Verdadeiro quando a carga está cifrada no disco (AES-256-GCM); o SHA-256 é sempre o do
    # conteúdo original.
    cifrada: bool = False


class AuditoriaSaida(BaseModel):
    seq: int
    quando: str
    usuario: str
    acao: str
    caso_id: str | None
    justificativa: str | None
    detalhes: dict[str, Any]
    hash_anterior: str
    hash: str


class VerificacaoAuditoria(BaseModel):
    integra: bool
    entradas: int
    primeira_quebra: int | None
    motivo: str | None = None
    # Entrada mais recente copiada para a âncora externa (fora do banco); None se não houver.
    ancora_seq: int | None = None


class Erro(BaseModel):
    detail: str
