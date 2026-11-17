"""Cliente HTTP comum dos conectores de API do horizonte 2 (Portal da Transparência e termos do
PNCP): só ``GET`` (coleta passiva), resposta lida em blocos com teto de bytes, retentativa pela
política comum e controle de taxa (RNF12).

Quando recebe um transporte injetado (testes e demonstração), marca-se como simulado: o conector
grava as capturas com ``simulado://`` e ``metadados.simulado = true``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import httpx

from buscador.conectores.base import ControleTaxa, ErroConector, FalhaTransitoria, Retentativa

CABECALHOS_GUARDADOS = ("content-type", "date", "server", "content-length", "cache-control")
STATUS_RETENTAVEIS = {429, 500, 502, 503, 504}


@dataclass(frozen=True)
class Resposta:
    status: int
    cabecalhos: dict[str, str]
    url: str
    conteudo: bytes


class BuscadorHttp:
    def __init__(
        self,
        *,
        nome_fonte: str,
        url_base: str,
        retentativa: Retentativa,
        taxa: ControleTaxa,
        timeout_segundos: float = 30.0,
        max_bytes: int = 8 * 1024 * 1024,
        transporte: httpx.BaseTransport | None = None,
        cabecalhos: dict[str, str] | None = None,
    ) -> None:
        self.nome_fonte = nome_fonte
        self.url_base = url_base.rstrip("/")
        self.retentativa = retentativa
        self.taxa = taxa
        self.max_bytes = max_bytes
        self.simulado = transporte is not None and bool(getattr(transporte, "simulado", True))
        self._cliente = httpx.Client(
            base_url=self.url_base,
            timeout=timeout_segundos,
            transport=transporte,
            follow_redirects=False,
            headers={
                "Accept": "application/json",
                "User-Agent": "buscador-osint-mvp/0.2",
                **(cabecalhos or {}),
            },
        )

    def __enter__(self) -> BuscadorHttp:
        return self

    def __exit__(self, *_exc) -> None:
        self._cliente.close()

    def _requisitar(self, caminho: str, consulta: dict | None) -> Resposta:
        erro_tamanho = ErroConector(
            f"A resposta de {self.nome_fonte} passa do limite de {self.max_bytes} bytes por "
            "requisição."
        )
        with self._cliente.stream("GET", caminho, params=consulta) as resposta:
            declarado = resposta.headers.get("content-length", "")
            if declarado.isdigit() and int(declarado) > self.max_bytes:
                raise erro_tamanho
            partes: list[bytes] = []
            total = 0
            for bloco in resposta.iter_bytes():
                total += len(bloco)
                if total > self.max_bytes:
                    raise erro_tamanho
                partes.append(bloco)
            return Resposta(
                status=resposta.status_code,
                cabecalhos={
                    k.lower(): v
                    for k, v in resposta.headers.items()
                    if k.lower() in CABECALHOS_GUARDADOS
                },
                url=str(resposta.request.url),
                conteudo=b"".join(partes),
            )

    def obter(self, caminho: str, consulta: dict | None = None) -> Resposta:
        """``GET`` com controle de taxa antes de cada tentativa e retentativa em falha transitória
        (rede, HTTP 429 e 5xx). Na última tentativa a resposta de erro é devolvida, para ficar na
        custódia."""

        def tentar(ultima: bool) -> Resposta:
            self.taxa.aguardar()
            try:
                resposta = self._requisitar(caminho, consulta)
            except httpx.HTTPError as erro:
                raise FalhaTransitoria(f"{type(erro).__name__}: {str(erro)[:200]}") from erro
            if resposta.status in STATUS_RETENTAVEIS and not ultima:
                raise FalhaTransitoria(f"{self.nome_fonte} respondeu HTTP {resposta.status}")
            return resposta

        return self.retentativa.executar(tentar, f"Falha ao consultar {self.nome_fonte}")


class TransporteRotas(httpx.BaseTransport):
    """Transporte httpx que responde sem rede, a partir de uma função ``(request) -> (status,
    bytes)``. Usado nos testes e na demonstração; ``simulado`` marca as capturas como sintéticas.
    ``falhas_iniciais`` força respostas 503 nas primeiras chamadas (teste de retentativa)."""

    simulado = True

    def __init__(
        self,
        responder: Callable[[httpx.Request], tuple[int, bytes]],
        falhas_iniciais: int = 0,
    ) -> None:
        self.responder = responder
        self.falhas_restantes = falhas_iniciais
        self.chamadas: list[httpx.Request] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.chamadas.append(request)
        if self.falhas_restantes > 0:
            self.falhas_restantes -= 1
            return httpx.Response(503, content=b"indisponivel", request=request)
        status, conteudo = self.responder(request)
        return httpx.Response(
            status,
            content=conteudo,
            headers={"content-type": "application/json", "server": "simulado"},
            request=request,
        )
