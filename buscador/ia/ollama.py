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

Soberania: um modelo com ``remote_host`` ou ``remote_model`` roda fora do servidor (o Ollama só
repassa a chamada); este provedor o RECUSA. O cliente HTTP ignora variáveis de proxy do ambiente
(``trust_env=False``) e não segue redirecionamentos, para que o texto não seja desviado.
"""

from __future__ import annotations

import httpx

from buscador.ia.provedor import ErroProvedor, Identidade
from buscador.seguranca import texto_de_linha

MAX_BYTES_RESPOSTA = 4 * 1024 * 1024


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
    ) -> None:
        self.url_base = url_base.rstrip("/")
        self.modelo = modelo
        self.temperatura = temperatura
        self.semente = semente
        self.timeout = timeout_segundos
        self._transporte = transporte
        self._identidade: Identidade | None = None

    def _cliente(self) -> httpx.Client:
        return httpx.Client(
            base_url=self.url_base,
            timeout=self.timeout,
            transport=self._transporte,
            trust_env=False,
            follow_redirects=False,
        )

    def _json(self, cliente: httpx.Client, metodo: str, rota: str, corpo: dict | None = None):
        try:
            resposta = cliente.request(metodo, rota, json=corpo)
        except httpx.HTTPError as erro:
            raise ErroProvedor(
                f"O servidor local de IA não respondeu ({type(erro).__name__})."
            ) from erro
        if resposta.status_code != 200:
            raise ErroProvedor(f"O servidor local de IA respondeu HTTP {resposta.status_code}.")
        if len(resposta.content) > MAX_BYTES_RESPOSTA:
            raise ErroProvedor("A resposta do servidor local de IA passou do tamanho máximo.")
        try:
            return resposta.json()
        except ValueError as erro:
            raise ErroProvedor(
                "O servidor local de IA devolveu uma resposta que não é JSON."
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
                f"O modelo '{self.modelo}' não está instalado no servidor local de IA."
            )
        if achado.get("remote_host") or achado.get("remote_model"):
            raise ErroProvedor(
                f"O modelo '{self.modelo}' é remoto (o servidor local só repassa a chamada): "
                "recusado, porque o texto sairia do servidor."
            )
        self._identidade = Identidade(
            provedor=self.nome,
            modelo=texto_de_linha(achado.get("name") or self.modelo)[:200],
            modelo_versao=texto_de_linha(achado.get("digest") or "")[:200],
            servidor_versao=texto_de_linha(
                versao.get("version") if isinstance(versao, dict) else ""
            )[:50],
        )
        return self._identidade

    def parametros(self) -> dict:
        return {
            "temperature": self.temperatura,
            "seed": self.semente,
            "stream": False,
            "format": "json_schema",
        }

    def gerar(self, instrucoes: str, texto: str, esquema: dict) -> str:
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
        with self._cliente() as cliente:
            resposta = self._json(cliente, "POST", "/api/chat", corpo)
        mensagem = resposta.get("message") if isinstance(resposta, dict) else None
        conteudo = mensagem.get("content") if isinstance(mensagem, dict) else None
        if not isinstance(conteudo, str):
            raise ErroProvedor("A resposta do servidor local de IA veio sem 'message.content'.")
        return conteudo
