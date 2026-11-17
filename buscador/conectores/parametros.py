"""Parâmetros aceitos por cada fonte, validados com pydantic antes de qualquer efeito colateral.

Chaves desconhecidas são recusadas, textos não levam caracteres de controle, números têm faixa e
``NaN``/``Infinity`` não entram. ``validar_parametros`` converte o erro do pydantic em
``ErroConector`` com mensagem em português.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, BeforeValidator, ConfigDict, Field, ValidationError

from buscador.conectores.base import ErroConector
from buscador.seguranca import validar_texto

TextoLinha = Annotated[str, AfterValidator(validar_texto)]
Documento = Annotated[str, Field(min_length=1, max_length=20), AfterValidator(validar_texto)]


def _lista(valor: object) -> object:
    if valor is None:
        return []
    return [valor] if isinstance(valor, str) else valor


class _Parametros(BaseModel):
    model_config = ConfigDict(
        extra="forbid", allow_inf_nan=False, str_strip_whitespace=True, coerce_numbers_to_str=True
    )


class ParametrosCnpjArquivo(_Parametros):
    """``diretorio`` é relativo à raiz de ingestão ``CNPJ_DIR_BASE`` (ou absoluto dentro dela)."""

    diretorio: TextoLinha = Field(min_length=1, max_length=500)


class ParametrosPncp(_Parametros):
    dataInicial: str = Field(pattern=r"^\d{8}$")
    dataFinal: str = Field(pattern=r"^\d{8}$")
    cnpjOrgao: Documento | None = None
    # O teto efetivo é PNCP_MAX_PAGINAS, conferido pelo conector.
    max_paginas: int = Field(default=5, ge=1, le=1000)
    tamanhoPagina: int = Field(default=50, ge=10, le=500)
    cnpjFornecedor: Annotated[list[Documento], BeforeValidator(_lista)] = Field(
        default_factory=list, max_length=50
    )


CodigoOrgao = Annotated[str, Field(min_length=1, max_length=10, pattern=r"^\d+$")]
NomePessoa = Annotated[str, Field(min_length=3, max_length=150), AfterValidator(validar_texto)]


class ParametrosPortalTransparencia(_Parametros):
    """``recurso`` escolhe o endpoint. CEIS, CNEP e CEPIM consultam por CNPJ
    (``codigoSancionado``, ou ``do_caso`` para as contratadas e convenentes do caso).
    ``convenios`` consulta pelo CNPJ do convenente (``codigoSancionado``, ou ``do_caso`` para as
    entidades sem fins lucrativos do caso). ``ceaf`` consulta por nome (``nomeSancionado``, ou
    ``do_caso`` para os sócios pessoa física das contratadas e convenentes), nunca por CPF.
    ``servidores`` consulta por órgão (código SIAPE) e exige ``cnpjOrgao``: o órgão contratante
    do caso a que o código corresponde."""

    recurso: Literal["ceis", "cnep", "servidores", "cepim", "ceaf", "convenios"]
    # CEIS, CNEP e CEPIM: CNPJ sancionado. Convênios: CNPJ do convenente.
    codigoSancionado: Annotated[list[Documento], BeforeValidator(_lista)] = Field(
        default_factory=list, max_length=100
    )
    # CEAF: nome completo da pessoa (a consulta por CPF não é oferecida, RNF04).
    nomeSancionado: Annotated[list[NomePessoa], BeforeValidator(_lista)] = Field(
        default_factory=list, max_length=100
    )
    orgaoServidorLotacao: CodigoOrgao | None = None
    orgaoServidorExercicio: CodigoOrgao | None = None
    cnpjOrgao: Documento | None = None
    # O teto efetivo é PORTAL_TRANSPARENCIA_MAX_PAGINAS, conferido pelo conector.
    max_paginas: int = Field(default=20, ge=1, le=1000)
    # Preenchido pela rota de coleta: troca-se pela lista de contratadas do caso.
    do_caso: bool = False


class ContratoPncp(_Parametros):
    cnpj: Documento
    ano: int = Field(ge=1990, le=2100)
    sequencial: int = Field(ge=1, le=10**9)
    numero_controle_pncp: TextoLinha = Field(default="", max_length=100)


class ParametrosPncpTermos(_Parametros):
    """Contratos cujos termos serão consultados (``do_caso`` = todos os contratos do caso)."""

    contratos: list[ContratoPncp] = Field(default_factory=list, max_length=500)
    do_caso: bool = False


SqCandidato = Annotated[str, Field(min_length=1, max_length=20, pattern=r"^\d+$")]


class ParametrosTseArquivo(_Parametros):
    """``diretorio`` é relativo à raiz ``TSE_DIR_BASE``. ``uf``, ``municipio`` (nome da unidade
    eleitoral) e ``candidatos`` (lista de ``SQ_CANDIDATO``) restringem as linhas lidas; ``ano``
    vale quando nem o arquivo nem o nome o dizem. ``do_caso`` é trocado, pela rota de coleta,
    pela lista dos candidatos que já estão no caso. Arquivo acima de ``TSE_MAX_BYTES_ARQUIVO`` só
    é lido (em fluxo) com ao menos um desses filtros."""

    diretorio: TextoLinha = Field(min_length=1, max_length=500)
    ano: str = Field(default="", pattern=r"^(\d{4})?$")
    uf: str = Field(default="", pattern=r"^([A-Za-z]{2})?$")
    municipio: TextoLinha = Field(default="", max_length=100)
    candidatos: Annotated[list[SqCandidato], BeforeValidator(_lista)] = Field(
        default_factory=list, max_length=2000
    )
    do_caso: bool = False


class ParametrosEditalArquivo(_Parametros):
    """``diretorio`` é relativo à raiz ``EDITAIS_DIR_BASE``."""

    diretorio: TextoLinha = Field(min_length=1, max_length=500)
    numero_controle_pncp_compra: TextoLinha = Field(default="", max_length=100)
    cnpjOrgao: Documento | None = None


MODELOS = {
    "cnpj_arquivo": ParametrosCnpjArquivo,
    "pncp": ParametrosPncp,
    "portal_transparencia": ParametrosPortalTransparencia,
    "pncp_termos": ParametrosPncpTermos,
    "tse_arquivo": ParametrosTseArquivo,
    "edital_arquivo": ParametrosEditalArquivo,
}

_DICAS = {
    "dataInicial": "formato AAAAMMDD",
    "dataFinal": "formato AAAAMMDD",
    "max_paginas": "inteiro maior ou igual a 1",
    "tamanhoPagina": "inteiro de 10 a 500",
    "diretorio": "texto de 1 a 500 caracteres, sem caracteres de controle",
    "cnpjOrgao": "CNPJ com até 20 caracteres",
    "cnpjFornecedor": "CNPJ ou lista de até 50 CNPJs",
    "recurso": "ceis, cnep, cepim, ceaf, convenios ou servidores",
    "codigoSancionado": "CNPJ ou lista de até 100 CNPJs",
    "nomeSancionado": "nome completo ou lista de até 100 nomes, de 3 a 150 caracteres",
    "orgaoServidorLotacao": "código SIAPE do órgão, só dígitos",
    "orgaoServidorExercicio": "código SIAPE do órgão, só dígitos",
    "contratos": "lista de objetos com cnpj, ano e sequencial",
    "ano": "quatro dígitos",
    "uf": "sigla de duas letras",
    "candidatos": "SQ_CANDIDATO (só dígitos) ou lista de até 2000",
}


def _mensagem(erro: ValidationError) -> str:
    partes = []
    for detalhe in erro.errors()[:5]:
        nome = str(detalhe["loc"][0]) if detalhe["loc"] else "parametros"
        if detalhe["type"] == "missing":
            texto = f"O parâmetro '{nome}' é obrigatório"
        elif detalhe["type"] == "extra_forbidden":
            partes.append(f"O parâmetro '{nome[:60]}' não é aceito por esta fonte.")
            continue
        else:
            texto = f"O parâmetro '{nome}' é inválido"
        dica = _DICAS.get(nome)
        partes.append(f"{texto} ({dica})." if dica else f"{texto}.")
    return " ".join(partes) or "Parâmetros inválidos."


def validar[M: BaseModel](modelo: type[M], parametros: object) -> M:
    if not isinstance(parametros, dict):
        raise ErroConector("Os parâmetros da coleta devem ser um objeto JSON.")
    try:
        return modelo.model_validate(parametros)
    except ValidationError as erro:
        raise ErroConector(_mensagem(erro)) from erro


def validar_parametros(fonte: str, parametros: object) -> BaseModel:
    if fonte not in MODELOS:
        raise ErroConector(f"Fonte desconhecida: '{fonte[:60]}'.")
    return validar(MODELOS[fonte], parametros)
