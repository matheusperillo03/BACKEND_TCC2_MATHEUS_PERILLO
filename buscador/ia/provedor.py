"""Interface do provedor de modelo de linguagem (plugável) e a fábrica a partir da configuração.

Um provedor só faz uma coisa: recebe as instruções, um bloco de texto e o esquema JSON da
resposta, e devolve o texto que o modelo gerou, com as métricas que o servidor informou sobre
essa geração (tempos e contagens de tokens; nunca texto). Toda a verificação (JSON estrito,
esquema, trecho literal) é do serviço (``buscador.ia.servico``), igual para qualquer provedor:
nenhum provedor é confiável por construção.

Soberania dos dados: os provedores aceitos falam com um servidor LOCAL. Não há provedor de API
externa, e a configuração recusa endereço que não seja de loopback, de rede privada ou um nome
declarado em ``IA_HOSTS_LOCAIS`` (``Configuracao.validar_ia``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from buscador.config import Configuracao


class ErroProvedor(Exception):
    """O provedor não respondeu, respondeu com erro ou não pode ser usado (ex.: modelo remoto).

    ``causa`` é um código estável, gravado nas métricas da execução que falhou:
    ``tempo_esgotado``, ``conexao``, ``http``, ``resposta_invalida`` ou ``modelo_recusado``."""

    def __init__(self, mensagem: str, causa: str = "provedor") -> None:
        super().__init__(mensagem)
        self.causa = causa


@dataclass(frozen=True)
class Identidade:
    """O que identifica o modelo numa execução (vai para a auditoria e para os metadados)."""

    provedor: str
    modelo: str
    # Resumo (digest) do conteúdo do modelo, quando o servidor informa: distingue duas versões
    # publicadas com o mesmo nome.
    modelo_versao: str
    servidor_versao: str
    # Quantização e tamanho do modelo, quando o servidor informa (vazio ou ``None`` se não).
    quantizacao: str = ""
    tamanho_parametros: str = ""
    tamanho_bytes: int | None = None


@dataclass(frozen=True)
class Geracao:
    """O texto gerado para um bloco e as métricas dessa geração.

    ``metricas`` traz só números e códigos curtos informados pelo servidor (no Ollama:
    ``total_duration``, ``load_duration``, ``prompt_eval_count``, ``prompt_eval_duration``,
    ``eval_count``, ``eval_duration`` e ``done_reason``). Fica vazio se o provedor não mede."""

    texto: str
    metricas: dict = field(default_factory=dict)


class Provedor(Protocol):
    nome: str

    def identidade(self) -> Identidade: ...

    def parametros(self) -> dict:
        """Parâmetros de geração efetivamente enviados (temperatura, semente etc.)."""
        ...

    def gerar(self, instrucoes: str, texto: str, esquema: dict) -> Geracao:
        """Texto gerado pelo modelo para um bloco (espera-se JSON no ``esquema``) e as métricas."""
        ...

    def contexto_efetivo(self) -> int | None:
        """Janela de contexto (em tokens) com que o modelo está carregado, se o servidor informa."""
        ...


def criar_provedor(config: Configuracao, transporte=None) -> Provedor | None:
    """Provedor configurado, ou ``None`` com a extração desligada (o padrão)."""
    if not config.ia_extracao_ativa:
        return None
    config.validar_ia()
    if config.ia_provedor == "falso":
        from buscador.ia.falso import ProvedorFalso

        return ProvedorFalso()
    from buscador.ia.ollama import ProvedorOllama

    return ProvedorOllama(
        url_base=config.ia_ollama_url,
        modelo=config.ia_modelo.strip(),
        temperatura=config.ia_temperatura,
        semente=config.ia_semente,
        timeout_segundos=config.ia_timeout_segundos,
        keep_alive=config.ia_keep_alive.strip() or None,
        transporte=transporte,
    )
