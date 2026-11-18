"""Provedor para um servidor Ollama local.

API do Ollama (documentação oficial, conferida em 16/11/2026):

- ``POST /api/chat`` (https://docs.ollama.com/api/chat): corpo com ``model`` e ``messages``
  (obrigatórios); ``format`` aceita ``json`` ou um esquema JSON ("Format to return a response
  in. Can be `json` or a JSON schema"); ``stream`` é ``true`` por padrão, por isso vai
  ``false``; ``options`` leva ``temperature``, ``seed`` etc. A resposta sem fluxo traz ``model``,
  ``message.content``, ``done`` e ``done_reason``.
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

Este provedor só REGISTRA ``num_ctx``, ``num_predict`` e ``keep_alive``: sem valor configurado,
o campo não é enviado e vale o padrão do servidor (registrado como ``None``).

Soberania: um modelo com ``remote_host`` ou ``remote_model`` roda fora do servidor (o Ollama só
repassa a chamada); este provedor o RECUSA. O cliente HTTP ignora variáveis de proxy do ambiente
(``trust_env=False``) e não segue redirecionamentos, para que o texto não seja desviado.
"""

from __future__ import annotations

import httpx

from buscador.ia.provedor import ErroProvedor, Geracao, Identidade
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
        # ``None``: o campo não é enviado e vale o padrão do servidor. A aplicação não define
        # ``num_ctx`` nem ``num_predict``; só o script de benchmark os passa.
        self.keep_alive = keep_alive
        self.num_ctx = num_ctx
        self.num_predict = num_predict
        self._transporte = transporte
        self._identidade: Identidade | None = None

    def _cliente(self, timeout: float | None = None) -> httpx.Client:
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
            esgotou = isinstance(erro, httpx.TimeoutException)
            raise ErroProvedor(
                f"O servidor local de IA não respondeu ({type(erro).__name__}).",
                "tempo_esgotado" if esgotou else "conexao",
            ) from erro
        if resposta.status_code != 200:
            raise ErroProvedor(
                f"O servidor local de IA respondeu HTTP {resposta.status_code}.", "http"
            )
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
            "stream": False,
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

    def gerar(self, instrucoes: str, texto: str, esquema: dict) -> Geracao:
        self.identidade()  # confere, antes de enviar qualquer texto, que o modelo é local
        corpo = {
            "model": self.modelo,
            "messages": [
                {"role": "system", "content": instrucoes},
                {"role": "user", "content": texto},
            ],
            "format": esquema,
            "stream": False,
            "options": {"temperature": self.temperatura, "seed": self.semente},
        }
        if self.num_ctx is not None:
            corpo["options"]["num_ctx"] = self.num_ctx
        if self.num_predict is not None:
            corpo["options"]["num_predict"] = self.num_predict
        if self.keep_alive is not None:
            corpo["keep_alive"] = self._keep_alive_enviado()
        with self._cliente() as cliente:
            resposta = self._json(cliente, "POST", "/api/chat", corpo)
        mensagem = resposta.get("message") if isinstance(resposta, dict) else None
        conteudo = mensagem.get("content") if isinstance(mensagem, dict) else None
        if not isinstance(conteudo, str):
            raise ErroProvedor(
                "A resposta do servidor local de IA veio sem 'message.content'.",
                "resposta_invalida",
            )
        return Geracao(conteudo, metricas_da_resposta(resposta))
