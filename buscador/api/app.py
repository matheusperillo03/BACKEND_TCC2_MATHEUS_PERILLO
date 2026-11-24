"""Fábrica da aplicação FastAPI."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from buscador import __version__, auditoria
from buscador.agendador import Agendador
from buscador.api import (
    esquemas,
    rotas_auditoria,
    rotas_auth,
    rotas_capturas,
    rotas_casos,
    rotas_equipe,
    rotas_exportacao,
    rotas_ia,
    rotas_importacao_judicial,
    rotas_monitoramento,
)
from buscador.api.deps import usuario_atual
from buscador.config import Configuracao, Usuario, host_do_cabecalho, host_e_local
from buscador.custodia import Custodia
from buscador.db import criar_engine, criar_esquema, criar_fabrica_sessoes, verificar_esquema
from buscador.ia.provedor import Provedor, criar_provedor
from buscador.ia.trabalhador import Trabalhador
from buscador.privacidade import instalar_filtro_de_registros
from buscador.tipologias.catalogo import catalogo

DESCRICAO = (
    "MVP do buscador OSINT (TCC2, UnB/FCTE): coleta passiva de dados abertos (CNPJ, PNCP, Portal "
    "da Transparência, TSE e editais), resolução de entidades, grafo de vínculos, alertas de "
    "tipologia explicados (T1 a T10 e L), monitoramento contínuo, cadeia de custódia e trilha de "
    "auditoria encadeada por HMAC. Acesso por usuário real (senha, MFA por TOTP e sessão em "
    "`/auth`) e por caso (membros com papel). As rotas de caso exigem `Authorization: Bearer "
    "<token da sessão>` e `X-Justificativa`."
)


class LimiteDeCorpo:
    """Recusa com 413 corpos acima de ``max_bytes``, pelo Content-Length ou pela soma dos blocos.

    O uvicorn não tem teto de corpo; sem isto, a porta direta da API aceitaria qualquer tamanho.
    """

    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        mensagem = f"Corpo da requisição acima do limite de {self.max_bytes} bytes."
        declarado = dict(scope["headers"]).get(b"content-length")
        if declarado is not None:
            if not declarado.isdigit():
                resposta = JSONResponse({"detail": "Content-Length inválido."}, status_code=400)
                await resposta(scope, receive, send)
                return
            if int(declarado) > self.max_bytes:
                resposta = JSONResponse({"detail": mensagem}, status_code=413)
                await resposta(scope, receive, send)
                return
        recebidos = 0

        async def receber() -> Message:
            nonlocal recebidos
            evento = await receive()
            if evento["type"] == "http.request":
                recebidos += len(evento.get("body", b""))
                if recebidos > self.max_bytes:
                    raise HTTPException(413, mensagem)
            return evento

        await self.app(scope, receber, send)


class HostPermitido:
    """Recusa com 421 a requisição cujo ``Host`` não é a própria máquina nem o ``URL_PUBLICA``.

    Sem ``URL_PUBLICA`` o serviço só atende em localhost. Assim, um ambiente publicado por proxy
    ou VPN sem a declaração (que exige https e ``MODO_DEV=0``) não responde por esse endereço: os
    tokens de desenvolvimento não ficam alcançáveis de fora por descuido. O ``Host`` é o que o
    nginx do front-end repassa (``proxy_set_header Host $host``). A resposta não ecoa o valor.
    """

    def __init__(self, app: ASGIApp, config: Configuracao) -> None:
        self.app = app
        self.host_publico = config.host_publico()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            host = dict(scope["headers"]).get(b"host", b"").decode("latin-1")
            nome = host_do_cabecalho(host)
            if nome is None or not (host_e_local(nome) or nome == self.host_publico):
                resposta = JSONResponse(
                    {
                        "detail": "Endereço não autorizado: este serviço só atende na própria "
                        "máquina ou no endereço declarado em URL_PUBLICA (https)."
                    },
                    status_code=421,
                )
                await resposta(scope, receive, send)
                return
        await self.app(scope, receive, send)


def _texto_seguro(valor: object) -> str:
    """Texto que sempre codifica em UTF-8 (substitutos isolados viram '?') e de tamanho limitado."""
    return str(valor).encode("utf-8", "replace").decode("utf-8")[:300]


async def _erro_de_validacao(_request: Request, erro: RequestValidationError) -> JSONResponse:
    """422 sem devolver a entrada recusada: o padrão do FastAPI ecoa o valor em ``input``, o
    que repete corpos grandes e falha (500) quando o valor tem um substituto isolado."""
    detalhes = [
        {
            "type": _texto_seguro(e.get("type", "")),
            "loc": [_texto_seguro(parte) for parte in e.get("loc", ())][:8],
            "msg": _texto_seguro(e.get("msg", "")),
        }
        for e in erro.errors()[:20]
    ]
    return JSONResponse({"detail": detalhes}, status_code=422)


def criar_app(
    config: Configuracao | None = None,
    extras_conectores: dict | None = None,
    provedor_ia: Provedor | None = None,
) -> FastAPI:
    """``provedor_ia`` substitui o provedor da configuração (testes e demonstração)."""
    config = config or Configuracao()
    # Falha fechada: sem tokens próprios e sem a chave da auditoria, o serviço não sobe.
    config.validar()
    engine = criar_engine(config.database_url)
    if config.db_criar_esquema:
        criar_esquema(engine)
    else:
        verificar_esquema(engine)

    # RNF04: nenhum registro (``log``) do processo sai com CPF completo.
    instalar_filtro_de_registros()

    @asynccontextmanager
    async def ciclo_de_vida(app: FastAPI):
        # Agendador interno do monitoramento contínuo (H18): começa e termina com o serviço.
        agendador = Agendador(app.state, config.monitoramento_intervalo_segundos)
        app.state.agendador = agendador
        if config.monitoramento_agendador_ativo:
            agendador.iniciar()
        # Trabalhador da extração de editais: consome a fila fora das requisições.
        trabalhador = Trabalhador(app.state, config.ia_trabalhador_intervalo_segundos)
        app.state.trabalhador_ia = trabalhador
        # Sobe também com a extração desligada: a partida fecha as execuções que um processo
        # anterior deixou no meio (e libera a trava do documento).
        if config.ia_trabalhador_ativo:
            trabalhador.iniciar()
        try:
            yield
        finally:
            trabalhador.parar()
            agendador.parar()

    # /docs, /redoc e /openapi.json não exigem token: só existem no modo de desenvolvimento.
    app = FastAPI(
        lifespan=ciclo_de_vida,
        title="Buscador OSINT (MVP)",
        version=__version__,
        description=DESCRICAO,
        docs_url="/docs" if config.modo_dev else None,
        redoc_url="/redoc" if config.modo_dev else None,
        openapi_url="/openapi.json" if config.modo_dev else None,
    )
    chave = config.chave_auditoria()
    app.state.config = config
    app.state.engine = engine
    app.state.fabrica_sessoes = criar_fabrica_sessoes(engine)
    # Criptografia em repouso (H23): as capturas são cifradas no disco e os segredos de TOTP, no
    # banco, com subchaves da chave mestra que fica fora do banco.
    app.state.cofre = config.cofre()
    app.state.custodia = Custodia(config.dados_dir, app.state.cofre)
    # Tokens fixos de desenvolvimento: mapa vazio fora de MODO_DEV=1.
    app.state.tokens = config.tokens_por_resumo()
    # Extração por IA local (H22): ``None`` quando desligada (o padrão).
    app.state.provedor_ia = provedor_ia if provedor_ia is not None else criar_provedor(config)
    app.state.chave_auditoria = chave
    app.state.ancora = auditoria.Ancora(config.dados_dir, chave)
    # Importação sob ordem judicial (RF10): ``None`` com o módulo desligado (o padrão). Ligado,
    # tem base e chave de cifra próprias, separadas das do dado aberto.
    app.state.importacao_judicial = None
    if config.importacao_judicial_ativa:
        from buscador.importacao_judicial import Modulo

        app.state.importacao_judicial = Modulo(
            config.url_importacao_judicial(),
            config.chave_importacao_judicial(),
            config.dados_dir,
            config.importacao_judicial_dir_base,
            config.importacao_judicial_max_bytes_arquivo,
            config.importacao_judicial_max_linhas,
        )
    # Extras injetados nos conectores (ex.: transporte httpx simulado nos testes e na demo).
    app.state.extras_conectores = extras_conectores or {}

    app.add_middleware(LimiteDeCorpo, max_bytes=config.max_corpo_bytes)
    # A autenticação é por cabeçalho Bearer, não por cookie: sem credenciais no CORS.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.origens_cors(),
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Authorization", "X-Justificativa", "Content-Type"],
        expose_headers=["Content-Disposition", "X-Conteudo-SHA256"],
    )

    # Adicionado por último: é a camada de fora e roda antes de qualquer outra.
    app.add_middleware(HostPermitido, config=config)

    app.add_exception_handler(RequestValidationError, _erro_de_validacao)

    @app.get("/health", response_model=esquemas.Saude, tags=["base"])
    def health():
        return {"status": "ok", "modo_dev": bool(config.modo_dev)}

    @app.get("/me", response_model=esquemas.UsuarioSaida, tags=["base"])
    def me(usuario: Usuario = Depends(usuario_atual)):
        return {"usuario": usuario.usuario, "perfil": usuario.perfil}

    @app.get("/tipologias", response_model=list[esquemas.TipologiaSaida], tags=["base"])
    def tipologias(_usuario: Usuario = Depends(usuario_atual)):
        """Catálogo das tipologias (T1 a T10 e as de lavagem, L): regra, base, política de erro e
        a configuração em vigor. Não é dado de caso, por isso exige só o token."""
        return catalogo(config.parametros_tipologias())

    app.include_router(rotas_auth.roteador)
    app.include_router(rotas_auth.roteador_usuarios)
    app.include_router(rotas_casos.roteador)
    app.include_router(rotas_equipe.roteador)
    app.include_router(rotas_exportacao.roteador)
    app.include_router(rotas_ia.roteador)
    app.include_router(rotas_monitoramento.roteador)
    app.include_router(rotas_importacao_judicial.roteador_estado)
    app.include_router(rotas_importacao_judicial.roteador)
    app.include_router(rotas_capturas.roteador)
    app.include_router(rotas_auditoria.roteador)
    return app
