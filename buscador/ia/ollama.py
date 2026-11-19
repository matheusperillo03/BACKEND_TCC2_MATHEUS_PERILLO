"""Provedor para um servidor Ollama local.

API do Ollama (documentação oficial, conferida em 16/11/2026):

- ``POST /api/chat`` (https://docs.ollama.com/api/chat): corpo com ``model`` e ``messages``
  (obrigatórios); ``format`` aceita ``json`` ou um esquema JSON ("Format to return a response
  in. Can be `json` or a JSON schema"); ``stream`` é ``true`` por padrão; ``options`` leva
  ``temperature``, ``seed``, ``num_ctx``, ``num_predict`` etc. Com fluxo, a resposta é uma
  sequência de objetos JSON, um por linha, cada um com um pedaço em ``message.content``; o
  último traz ``done: true``, ``done_reason`` e as métricas.
- ``GET /api/tags`` (https://docs.ollama.com/api/tags): lista os modelos, cada um com ``name``,
  ``model``, ``digest`` ("SHA256 digest identifier of the model contents") e, se o modelo for
  remoto, ``remote_model`` e ``remote_host`` ("URL of the upstream Ollama host, if the model is
  remote").
- ``GET /api/version`` (https://docs.ollama.com/api-reference/get-version): ``version``.

Métricas (campos OBSERVADOS nas respostas do Ollama 0.40.16 em 18/11/2026; a definição de cada um
ainda não foi conferida na documentação oficial):

- a resposta de ``/api/chat`` traz ``total_duration``, ``load_duration``,
  ``prompt_eval_duration`` e ``eval_duration`` (nanossegundos), ``prompt_eval_count`` e
  ``eval_count`` (tokens) e ``done_reason`` (``length`` quando a saída foi cortada);
- cada modelo de ``/api/tags`` traz ``size`` (bytes) e ``details.quantization_level`` e
  ``details.parameter_size``; por isso a quantização sai da mesma chamada que já dá o digest,
  sem ``/api/show``;
- ``GET /api/ps`` lista os modelos carregados, cada um com ``context_length`` (a janela de
  contexto em uso).

A geração usa o FLUXO (``stream: true``): é o que permite ver que o servidor travou (nenhum
pedaço dentro de ``sem_token_segundos``), parar um bloco que passou do tempo limite e atender
ao cancelamento no meio da geração (a conexão é fechada, e o servidor para de gerar). O tempo
sem pedaço também cobre a carga do modelo e a leitura do prompt, que vêm antes do primeiro token.

``num_ctx`` e ``num_predict`` são enviados quando têm valor (a aplicação os configura em
``IA_NUM_CTX`` e ``IA_NUM_PREDICT``); com ``None``, o campo não é enviado e vale o padrão do
servidor (registrado como ``None``). O mesmo vale para ``keep_alive``.

Soberania: um modelo com ``remote_host`` ou ``remote_model`` roda fora do servidor (o Ollama só
repassa a chamada); este provedor o RECUSA. O cliente HTTP ignora variáveis de proxy do ambiente
(``trust_env=False``) e não segue redirecionamentos, para que o texto não seja desviado.
"""

from __future__ import annotations

import json
import time

import httpx

from buscador.ia.provedor import Controle, ErroProvedor, Geracao, Identidade
from buscador.seguranca import texto_de_linha

MAX_BYTES_RESPOSTA = 4 * 1024 * 1024
# Campos numéricos da resposta de ``/api/chat`` guardados como métricas (durações em ns).
METRICAS_NUMERICAS = (
    "total_duration",
    "load_duration",
    "prompt_eval_count",
    "prompt_eval_duration",
    "eval_count",
    "eval_duration",
)
# A consulta da janela em uso é só informativa: não pode segurar a execução.
TIMEOUT_CONSULTA_SEGUNDOS = 5.0


def _erro_de_transporte(erro: httpx.HTTPError) -> ErroProvedor:
    """Falha de rede: o servidor não respondeu, recusou a conexão ou a derrubou no meio."""
    esgotou = isinstance(erro, httpx.TimeoutException)
    return ErroProvedor(
        f"O servidor local de IA não respondeu ({type(erro).__name__}).",
        "tempo_esgotado" if esgotou and not isinstance(erro, httpx.ConnectTimeout) else "conexao",
        # Tempo esgotado numa chamada curta (ou na conexão) é o servidor ocupado ou fora do ar.
        transitoria=True,
    )


def _erro_http(status: int) -> ErroProvedor:
    return ErroProvedor(
        f"O servidor local de IA respondeu HTTP {status}.",
        "http",
        transitoria=status >= 500 or status == 429,
    )


def _inteiro(valor: object) -> int | None:
    """``valor`` se for um inteiro não negativo (``bool`` não conta); senão ``None``."""
    if isinstance(valor, bool) or not isinstance(valor, int) or valor < 0:
        return None
    return valor


def metricas_da_resposta(resposta: dict) -> dict:
    """Só os números e o ``done_reason`` da resposta de ``/api/chat``; nenhum texto gerado."""
    metricas: dict = {}
    for campo in METRICAS_NUMERICAS:
        numero = _inteiro(resposta.get(campo))
        if numero is not None:
            metricas[campo] = numero
    motivo = resposta.get("done_reason")
    if isinstance(motivo, str) and motivo:
        metricas["done_reason"] = texto_de_linha(motivo)[:30]
    return metricas


class ProvedorOllama:
    nome = "ollama"

    def __init__(
        self,
        url_base: str,
        modelo: str,
        temperatura: float = 0.0,
        semente: int = 0,
        timeout_segundos: float = 120.0,
        transporte: httpx.BaseTransport | None = None,
        keep_alive: str | None = None,
        num_ctx: int | None = None,
        num_predict: int | None = None,
    ) -> None:
        self.url_base = url_base.rstrip("/")
        self.modelo = modelo
        self.temperatura = temperatura
        self.semente = semente
        self.timeout = timeout_segundos
        # ``None``: o campo não é enviado e vale o padrão do servidor.
        self.keep_alive = keep_alive
        self.num_ctx = num_ctx
        self.num_predict = num_predict
        self._transporte = transporte
        self._identidade: Identidade | None = None

    def _cliente(self, timeout: float | httpx.Timeout | None = None) -> httpx.Client:
        return httpx.Client(
            base_url=self.url_base,
            timeout=self.timeout if timeout is None else timeout,
            transport=self._transporte,
            trust_env=False,
            follow_redirects=False,
        )

    def _json(self, cliente: httpx.Client, metodo: str, rota: str, corpo: dict | None = None):
        try:
            resposta = cliente.request(metodo, rota, json=corpo)
        except httpx.HTTPError as erro:
            raise _erro_de_transporte(erro) from erro
        if resposta.status_code != 200:
            raise _erro_http(resposta.status_code)
        if len(resposta.content) > MAX_BYTES_RESPOSTA:
            raise ErroProvedor(
                "A resposta do servidor local de IA passou do tamanho máximo.", "resposta_invalida"
            )
        try:
            return resposta.json()
        except ValueError as erro:
            raise ErroProvedor(
                "O servidor local de IA devolveu uma resposta que não é JSON.", "resposta_invalida"
            ) from erro

    def identidade(self) -> Identidade:
        if self._identidade is not None:
            return self._identidade
        with self._cliente() as cliente:
            versao = self._json(cliente, "GET", "/api/version")
            modelos = self._json(cliente, "GET", "/api/tags")
        lista = modelos.get("models") if isinstance(modelos, dict) else None
        aceitos = {self.modelo, f"{self.modelo}:latest"}
        achado = next(
            (
                m
                for m in (lista or [])
                if isinstance(m, dict) and (m.get("name") in aceitos or m.get("model") in aceitos)
            ),
            None,
        )
        if achado is None:
            raise ErroProvedor(
                f"O modelo '{self.modelo}' não está instalado no servidor local de IA.",
                "modelo_recusado",
            )
        if achado.get("remote_host") or achado.get("remote_model"):
            raise ErroProvedor(
                f"O modelo '{self.modelo}' é remoto (o servidor local só repassa a chamada): "
                "recusado, porque o texto sairia do servidor.",
                "modelo_recusado",
            )
        detalhes = achado.get("details")
        detalhes = detalhes if isinstance(detalhes, dict) else {}
        self._identidade = Identidade(
            provedor=self.nome,
            modelo=texto_de_linha(achado.get("name") or self.modelo)[:200],
            modelo_versao=texto_de_linha(achado.get("digest") or "")[:200],
            servidor_versao=texto_de_linha(
                versao.get("version") if isinstance(versao, dict) else ""
            )[:50],
            quantizacao=texto_de_linha(detalhes.get("quantization_level") or "")[:50],
            tamanho_parametros=texto_de_linha(detalhes.get("parameter_size") or "")[:50],
            tamanho_bytes=_inteiro(achado.get("size")),
        )
        return self._identidade

    def _keep_alive_enviado(self) -> str | int | None:
        """O ``keep_alive`` como vai no corpo: número de segundos ou duração ("5m")."""
        if self.keep_alive is None:
            return None
        try:
            return int(self.keep_alive)
        except ValueError:
            return self.keep_alive

    def parametros(self) -> dict:
        return {
            "temperature": self.temperatura,
            "seed": self.semente,
            "stream": True,
            "format": "json_schema",
            # ``None``: não enviado; vale o padrão do servidor (ou o do Modelfile).
            "num_ctx": self.num_ctx,
            "num_predict": self.num_predict,
            "keep_alive": self._keep_alive_enviado(),
            "timeout_segundos": self.timeout,
        }

    def contexto_efetivo(self) -> int | None:
        """Janela de contexto com que o modelo está carregado (``/api/ps``), ou ``None``."""
        try:
            with self._cliente(min(self.timeout, TIMEOUT_CONSULTA_SEGUNDOS)) as cliente:
                carregados = self._json(cliente, "GET", "/api/ps")
        except ErroProvedor:
            return None
        lista = carregados.get("models") if isinstance(carregados, dict) else None
        aceitos = {self.modelo, f"{self.modelo}:latest"}
        for m in lista or []:
            if isinstance(m, dict) and (m.get("name") in aceitos or m.get("model") in aceitos):
                return _inteiro(m.get("context_length")) or None
        return None

    def gerar(
        self, instrucoes: str, texto: str, esquema: dict, controle: Controle | None = None
    ) -> Geracao:
        self.identidade()  # confere, antes de enviar qualquer texto, que o modelo é local
        controle = controle or Controle()
        controle.conferir_interrupcao()
        corpo = {
            "model": self.modelo,
            "messages": [
                {"role": "system", "content": instrucoes},
                {"role": "user", "content": texto},
            ],
            "format": esquema,
            "stream": True,
            "options": {"temperature": self.temperatura, "seed": self.semente},
        }
        if self.num_ctx is not None:
            corpo["options"]["num_ctx"] = self.num_ctx
        if self.num_predict is not None:
            corpo["options"]["num_predict"] = self.num_predict
        if self.keep_alive is not None:
            corpo["keep_alive"] = self._keep_alive_enviado()
        # O tempo de leitura é o tempo máximo SEM receber nada: é ele que acusa o travamento.
        sem_token = controle.sem_token_segundos or controle.limite_segundos or self.timeout
        tempos = httpx.Timeout(self.timeout, read=sem_token)
        inicio = time.monotonic()
        partes: list[str] = []
        recebido = 0
        final: dict | None = None
        primeiro_token_ms: int | None = None
        try:
            with (
                self._cliente(tempos) as cliente,
                cliente.stream("POST", "/api/chat", json=corpo) as resposta,
            ):
                if resposta.status_code != 200:
                    raise _erro_http(resposta.status_code)
                for linha in resposta.iter_lines():
                    decorrido = time.monotonic() - inicio
                    limite = controle.limite_segundos
                    if limite is not None and decorrido > limite:
                        raise ErroProvedor(
                            f"A geração do bloco passou do tempo limite de {limite:g} s e foi "
                            "interrompida.",
                            "tempo_esgotado",
                        )
                    controle.conferir_interrupcao()
                    if not linha.strip():
                        continue
                    recebido += len(linha)
                    if recebido > MAX_BYTES_RESPOSTA:
                        raise ErroProvedor(
                            "A resposta do servidor local de IA passou do tamanho máximo.",
                            "resposta_invalida",
                        )
                    try:
                        pedaco = json.loads(linha)
                    except ValueError as erro:
                        raise ErroProvedor(
                            "O servidor local de IA devolveu uma resposta que não é JSON.",
                            "resposta_invalida",
                        ) from erro
                    if not isinstance(pedaco, dict) or pedaco.get("error"):
                        raise ErroProvedor(
                            "O servidor local de IA devolveu um erro durante a geração.",
                            "resposta_invalida",
                        )
                    mensagem = pedaco.get("message")
                    conteudo = mensagem.get("content") if isinstance(mensagem, dict) else None
                    if isinstance(conteudo, str) and conteudo:
                        if primeiro_token_ms is None:
                            primeiro_token_ms = round(decorrido * 1000)
                        partes.append(conteudo)
                    if pedaco.get("done") is True:
                        final = pedaco
                        break
        except httpx.ReadTimeout as erro:
            raise ErroProvedor(
                f"O servidor local de IA ficou {sem_token:g} s sem enviar nenhum token "
                "(travamento).",
                "travamento",
                transitoria=True,
            ) from erro
        except httpx.HTTPError as erro:
            raise _erro_de_transporte(erro) from erro
        if final is None:
            raise ErroProvedor(
                "A resposta do servidor local de IA terminou antes do fim da geração.",
                "conexao",
                transitoria=True,
            )
        if not partes and not isinstance((final.get("message") or {}).get("content"), str):
            raise ErroProvedor(
                "A resposta do servidor local de IA veio sem 'message.content'.",
                "resposta_invalida",
            )
        metricas = metricas_da_resposta(final)
        if primeiro_token_ms is not None:
            metricas["primeiro_token_ms"] = primeiro_token_ms
        return Geracao("".join(partes), metricas)
