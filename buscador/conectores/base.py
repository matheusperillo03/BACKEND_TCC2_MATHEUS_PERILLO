"""Interface comum dos conectores (RNF10: conectores modulares).

Cada conector recebe parâmetros, registra toda carga bruta na cadeia de custódia por meio de
``RegistradorCaptura`` e devolve registros normalizados, cada um com o ``captura_id`` de origem.
A resolução de entidades (RF02) transforma os registros em entidades e arestas.

Todo conector tem nome e versão (RF07, H14: a captura registra quem a produziu) e uma política de
retentativa comum (RNF12, H15): a operação de E/S sinaliza ``FalhaTransitoria`` e ``Retentativa``
tenta de novo com espera crescente, contando as tentativas para o registro da coleta. Os
conectores de API com limite documentado usam também ``ControleTaxa`` (RNF12).
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import BinaryIO

from buscador.modelos import Captura

# Função fornecida pelo serviço de coleta: (fonte, url_ou_arquivo, conteudo, metadados) -> Captura.
# ``conteudo`` são os bytes brutos ou um arquivo binário aberto, copiado em blocos pela custódia.
RegistradorCaptura = Callable[[str, str, bytes | BinaryIO, dict], Captura]


class ErroConector(Exception):
    """Erro de configuração ou de coleta com mensagem apresentável ao usuário."""


class FonteNaoConfigurada(ErroConector):
    """A fonte depende de configuração do operador que não foi feita (por exemplo, a chave de API).

    Não é falha de coleta: a coleta é fechada como ``nao_configurado``, sem capturas, e as demais
    fontes continuam funcionando (H19)."""


class FalhaTransitoria(Exception):
    """Falha que vale tentar de novo (rede, HTTP 429/5xx, erro de leitura). A mensagem entra no
    motivo da falha se as tentativas se esgotarem."""


class Retentativa:
    """Política de retentativa comum a todos os conectores (RNF12).

    ``executar`` chama a operação até ``tentativas`` vezes, esperando ``espera * n`` segundos
    depois da n-ésima falha transitória. A operação recebe ``ultima=True`` na última tentativa,
    para poder devolver o que tiver (por exemplo, a resposta de erro, que vai para a custódia).
    ``maior`` é o maior número de tentativas que uma operação precisou; ``total`` soma todas;
    ``ultima_falha`` guarda a mensagem da falha transitória mais recente.
    """

    def __init__(
        self,
        tentativas: int = 3,
        espera_segundos: float = 1.0,
        dormir: Callable[[float], None] = time.sleep,
    ) -> None:
        self.tentativas = max(1, tentativas)
        self.espera = max(0.0, espera_segundos)
        self.dormir = dormir
        self.maior = 0
        self.total = 0
        self.ultima_falha = ""

    def executar[T](self, operacao: Callable[[bool], T], falha: str) -> T:
        ultimo_erro = ""
        for tentativa in range(1, self.tentativas + 1):
            self.total += 1
            self.maior = max(self.maior, tentativa)
            try:
                return operacao(tentativa == self.tentativas)
            except FalhaTransitoria as erro:
                ultimo_erro = self.ultima_falha = str(erro)
            if tentativa < self.tentativas:
                self.dormir(self.espera * tentativa)
        raise ErroConector(f"{falha} após {self.tentativas} tentativas: {ultimo_erro}")


class ControleTaxa:
    """Controle de taxa de requisições (RNF12): no máximo ``por_minuto`` chamadas por minuto.

    Espaça as chamadas pelo intervalo mínimo ``60 / por_minuto``: antes de cada requisição,
    ``aguardar`` dorme o que faltar desde a anterior. ``por_minuto <= 0`` desliga o controle.
    ``esperas`` conta quantas vezes foi preciso esperar e ``total_esperado`` soma os segundos.
    """

    def __init__(
        self,
        por_minuto: float = 0,
        dormir: Callable[[float], None] = time.sleep,
        relogio: Callable[[], float] = time.monotonic,
    ) -> None:
        self.por_minuto = max(0.0, float(por_minuto))
        self.intervalo = 60.0 / self.por_minuto if self.por_minuto > 0 else 0.0
        self.dormir = dormir
        self.relogio = relogio
        self.esperas = 0
        self.total_esperado = 0.0
        self._ultima: float | None = None

    def aguardar(self) -> None:
        if self.intervalo <= 0:
            return
        agora = self.relogio()
        if self._ultima is not None:
            falta = self.intervalo - (agora - self._ultima)
            if falta > 0:
                self.esperas += 1
                self.total_esperado += falta
                self.dormir(falta)
                agora += falta
        self._ultima = agora


@dataclass
class RegistroEmpresa:
    captura_id: str
    cnpj_basico: str
    razao_social: str
    natureza_juridica: str = ""
    qualificacao_responsavel: str = ""
    capital_social: float | None = None
    porte: str = ""
    ente_federativo: str = ""


@dataclass
class RegistroEstabelecimento:
    captura_id: str
    cnpj_basico: str
    cnpj_ordem: str
    cnpj_dv: str
    matriz_filial: str
    nome_fantasia: str = ""
    situacao_cadastral: str = ""
    data_situacao_cadastral: str = ""
    motivo_situacao_cadastral: str = ""
    nome_cidade_exterior: str = ""
    pais: str = ""
    data_inicio_atividade: str = ""
    cnae_fiscal_principal: str = ""
    cnae_fiscal_secundaria: str = ""
    tipo_logradouro: str = ""
    logradouro: str = ""
    numero: str = ""
    complemento: str = ""
    bairro: str = ""
    cep: str = ""
    uf: str = ""
    municipio: str = ""
    correio_eletronico: str = ""
    situacao_especial: str = ""
    data_situacao_especial: str = ""

    @property
    def cnpj_completo(self) -> str:
        return f"{self.cnpj_basico}{self.cnpj_ordem}{self.cnpj_dv}"


@dataclass
class RegistroSocio:
    captura_id: str
    cnpj_basico: str
    identificador_socio: str  # 1 PJ, 2 PF, 3 estrangeiro
    nome_socio: str
    cnpj_cpf_socio: str  # CPF descaracterizado (***NNNNNN**) ou CNPJ (8 ou 14 posições)
    qualificacao_socio: str = ""
    data_entrada_sociedade: str = ""
    pais: str = ""
    representante_legal: str = ""
    nome_representante: str = ""
    qualificacao_representante: str = ""
    faixa_etaria: str = ""


@dataclass
class RegistroContrato:
    captura_id: str
    numero_controle_pncp: str
    ni_fornecedor: str
    tipo_pessoa_fornecedor: str
    nome_fornecedor: str
    orgao_cnpj: str
    orgao_razao_social: str
    objeto: str
    valor_global: float | None
    data_assinatura: str  # AAAA-MM-DD
    url_fonte: str
    extras: dict = field(default_factory=dict)


@dataclass
class RegistroTermo:
    """Termo de contrato do PNCP (aditivo, apostilamento ou rescisão), ligado ao contrato."""

    captura_id: str
    numero_controle_pncp: str  # do contrato a que o termo pertence
    sequencial_termo: int | None
    numero_termo: str = ""
    tipo_termo: str = ""
    objeto: str = ""
    valor_acrescido: float | None = None
    # ``qualificacaoAcrescimoSupressao`` (booleano da fonte; ``None`` quando ausente).
    acrescimo_supressao: bool | None = None
    reajuste: bool | None = None
    data_assinatura: str = ""
    fundamento_legal: str = ""
    url_fonte: str = ""


@dataclass
class RegistroSancao:
    """Registro do CEIS ou do CNEP (Portal da Transparência) de uma pessoa jurídica."""

    captura_id: str
    cadastro: str  # CEIS | CNEP
    id_registro: str
    cnpj_sancionado: str  # 14 posições (numérico ou alfanumérico)
    nome_sancionado: str = ""
    data_inicio: str = ""  # AAAA-MM-DD
    data_fim: str = ""  # AAAA-MM-DD; vazio = sem data final informada
    tipo_sancao: str = ""
    orgao_sancionador: str = ""
    numero_processo: str = ""
    url_fonte: str = ""


@dataclass
class RegistroServidor:
    """Servidor do Executivo Federal (Portal da Transparência). O CPF é descaracterizado na
    entrada da resolução; o CPF completo, se a fonte o trouxer, nunca vira atributo (RNF04)."""

    captura_id: str
    nome: str
    cpf: str
    codigo_orgao: str  # código SIAPE consultado
    nome_orgao: str = ""
    # CNPJ do órgão contratante a que o investigador declarou corresponder o código SIAPE.
    cnpj_orgao: str = ""
    vinculo: str = ""  # lotacao | exercicio
    situacao: str = ""
    tipo_servidor: str = ""
    url_fonte: str = ""


@dataclass
class RegistroCandidato:
    """Candidato (TSE). O CPF do candidato não é lido nem guardado: o vínculo com as doações é
    feito por ``SQ_CANDIDATO`` (o CPF não é divulgado a partir de 2024)."""

    captura_id: str
    ano: str
    sq_candidato: str
    nome: str
    nome_urna: str = ""
    cargo: str = ""
    partido: str = ""
    uf: str = ""
    sg_ue: str = ""
    nm_ue: str = ""
    situacao_turno: str = ""
    # Só o arquivo de candidatos traz a situação da totalização; o de receitas não.
    origem: str = "consulta_cand"


@dataclass
class RegistroDoacao:
    """Receita de campanha (TSE): doador -> candidato."""

    captura_id: str
    ano: str
    sq_candidato: str
    nome_candidato: str
    documento_doador: str  # CPF (11) ou CNPJ (14), só dígitos; o CPF é descaracterizado adiante
    nome_doador: str
    nome_doador_rfb: str = ""
    valor: float | None = None
    data: str = ""  # AAAA-MM-DD
    origem_receita: str = ""
    chave: str = ""  # identidade estável da linha dentro do arquivo


@dataclass
class RegistroEdital:
    """Edital (texto ou PDF) guardado na custódia; a análise (T10) relê a captura e, no PDF,
    extrai o texto de novo."""

    captura_id: str
    nome_arquivo: str
    codificacao: str
    caracteres: int
    numero_controle_pncp_compra: str = ""
    cnpj_orgao: str = ""
    formato: str = "texto"  # texto | pdf
    paginas: int | None = None
    # Falso no PDF sem camada de texto (escaneado): a captura existe, mas não há texto a analisar.
    texto_extraivel: bool = True


Registro = (
    RegistroEmpresa
    | RegistroEstabelecimento
    | RegistroSocio
    | RegistroContrato
    | RegistroTermo
    | RegistroSancao
    | RegistroServidor
    | RegistroCandidato
    | RegistroDoacao
    | RegistroEdital
)


@dataclass
class ResultadoColeta:
    fonte: str
    capturas: list[Captura] = field(default_factory=list)
    registros: list[Registro] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)


class Conector(ABC):
    """Contrato que todo conector implementa."""

    nome: str = ""
    # Versão do conector (semântica): muda quando muda o que ele pede à fonte ou como interpreta
    # a resposta. Fica gravada em cada captura e em cada coleta.
    versao: str = "0.0.0"

    def __init__(self, retentativa: Retentativa | None = None) -> None:
        self.retentativa = retentativa or Retentativa()

    @property
    def tentativas(self) -> int:
        """Maior número de tentativas que uma operação desta coleta precisou (0 = nenhuma)."""
        return self.retentativa.maior

    @abstractmethod
    def coletar(
        self, parametros: dict, registrar_captura: RegistradorCaptura
    ) -> ResultadoColeta: ...

    @classmethod
    def registros_brutos(cls, conteudo: bytes, metadados: dict) -> dict[str, dict[str, object]]:
        """Registros da carga bruta por chave estável, para comparar duas capturas campo a campo
        (RF07, H14). O padrão é não saber interpretar: a comparação fica só no hash."""
        return {}
