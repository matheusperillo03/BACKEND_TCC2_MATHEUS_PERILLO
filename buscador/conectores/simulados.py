"""Transportes HTTP simulados dos conectores do horizonte 2, para testes e demonstração (sem rede).

Servem respostas no formato das APIs oficiais a partir de dicionários (ou dos arquivos JSON de
``tests/fixtures/h2``). Toda captura feita por eles sai marcada como sintética.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import httpx

from buscador.conectores.http_util import TransporteRotas

_TERMOS = re.compile(r"/v1/orgaos/(\d+)/contratos/(\d+)/(\d+)/termos$")


def _json(dados: object) -> bytes:
    return json.dumps(dados, ensure_ascii=False).encode("utf-8")


def transporte_portal(
    ceis: dict[str, list] | None = None,
    cnep: dict[str, list] | None = None,
    servidores: dict[str, list] | None = None,
    falhas_iniciais: int = 0,
    tamanho_pagina: int = 15,
) -> TransporteRotas:
    """Portal da Transparência: ``ceis``/``cnep`` por CNPJ e ``servidores`` por código SIAPE.

    Os dicionários são lidos a cada requisição: o teste pode alterá-los entre duas coletas."""
    bases = {"ceis": ceis or {}, "cnep": cnep or {}, "servidores": servidores or {}}

    def responder(request: httpx.Request) -> tuple[int, bytes]:
        if not request.headers.get("chave-api-dados"):
            return 401, _json({"mensagem": "chave ausente (simulado)"})
        recurso = request.url.path.rsplit("/", 1)[-1]
        if recurso not in bases:
            return 404, b"{}"
        parametros = request.url.params
        chave = (
            parametros.get("codigoSancionado")
            or parametros.get("orgaoServidorLotacao")
            or parametros.get("orgaoServidorExercicio")
            or ""
        )
        itens = bases[recurso].get(chave, [])
        pagina = int(parametros.get("pagina", "1") or 1)
        return 200, _json(itens[(pagina - 1) * tamanho_pagina : pagina * tamanho_pagina])

    return TransporteRotas(responder, falhas_iniciais=falhas_iniciais)


def transporte_termos(termos: dict[str, list] | None = None, **kw) -> TransporteRotas:
    """Termos de contrato do PNCP por ``"<cnpj>/<ano>/<sequencial>"``; sem termos responde 204,
    e a segunda página é sempre vazia."""
    base = termos if termos is not None else {}

    def responder(request: httpx.Request) -> tuple[int, bytes]:
        achado = _TERMOS.search(request.url.path)
        if not achado:
            return 404, b"{}"
        itens = base.get("/".join(achado.groups()), [])
        if not itens or request.url.params.get("pagina", "1") != "1":
            return 204, b""
        return 200, _json(itens)

    return TransporteRotas(responder, **kw)


def extras_de_diretorio(diretorio: str | Path) -> dict[str, dict]:
    """Extras dos conectores (``criar_app(extras_conectores=...)``) a partir de ``tests/fixtures/
    h2``: PNCP, termos e Portal da Transparência simulados."""
    from buscador.conectores.pncp import TransporteSimulado

    raiz = Path(diretorio)

    def ler(nome: str) -> dict:
        return json.loads((raiz / "portal" / nome).read_text(encoding="utf-8"))

    return {
        "pncp": {"transporte": TransporteSimulado.de_diretorio(raiz / "pncp")},
        "pncp_termos": {
            "transporte": transporte_termos(
                json.loads((raiz / "termos" / "termos.json").read_text(encoding="utf-8"))
            )
        },
        "portal_transparencia": {
            "transporte": transporte_portal(
                ceis=ler("ceis.json"), cnep=ler("cnep.json"), servidores=ler("servidores.json")
            )
        },
    }
