"""Conector da API de consulta do PNCP (Serpro), rota GET /v1/contratos, sem autenticação.

A rota filtra por período (``dataInicial``/``dataFinal`` em AAAAMMDD) e, opcionalmente, por órgão
(``cnpjOrgao``). Ela não filtra por fornecedor: o filtro por ``cnpjFornecedor`` é aplicado depois
da coleta, sobre os registros já capturados. Cada página HTTP é uma captura (RF07).

Limites do lado do servidor (o cliente não os ultrapassa): teto de páginas por coleta, largura
máxima do período, bytes por página e prazo total. Números não finitos (``NaN``, ``Infinity``,
``1e999``) vindos da fonte são recusados ou desconsiderados com aviso.

Quando o conector recebe um transporte injetado (testes e demonstração), toda captura sai marcada
como sintética: ``url_ou_arquivo`` com ``simulado://`` e ``metadados.simulado = true``.

Conferido contra a API real em 17/11/2026 (OpenAPI: https://pncp.gov.br/api/consulta/v3/api-docs;
``scripts/testar_fontes_reais.py`` repete o teste):

- a operação chama-se "Consultar Contratos por Data de Publicação": ``dataInicial``/``dataFinal``
  filtram pela data de PUBLICAÇÃO no PNCP, não pela de assinatura (um contrato assinado antes
  do período aparece se foi publicado nele);
- ``pagina`` é obrigatório (mínimo 1) e ``tamanhoPagina`` vai de 10 a 500 (padrão do servidor:
  500); fora disso a API responde 400 "Tamanho de página inválido". Página além da última
  responde 400 "Página N inexistente."; a coleta para antes, por ``paginasRestantes``;
- o envelope é ``{data, totalRegistros, totalPaginas, numeroPagina, paginasRestantes, empty}``;
  período sem contratos (ou órgão sem contratos no período) responde 204, sem corpo;
- período de mais de 365 dias entre as datas responde 422 "Período maior que 365 dias." (o
  padrão ``PNCP_MAX_DIAS_PERIODO = 366`` conta os dois extremos e é o mesmo limite). Um ano
  inteiro sem ``cnpjOrgao`` levou 22 s na primeira página: prefira períodos curtos ou um órgão;
- ``cnpjOrgao`` sem pontuação (com pontuação: 422 "CNPJ do Órgão/Entidade inválido."); o
  conector já envia só as 14 posições (dígitos e, no CNPJ alfanumérico, letras maiúsculas). Não
  foi conferido em 17/11/2026 se a API já devolve ou aceita CNPJ alfanumérico: o conector apenas
  não descarta as letras;
- a OpenAPI não declara limite de requisições; o conector faz uma requisição por vez;
- ``tipoPessoa`` assume ``PJ``, ``PF`` e ``PE`` (estrangeiro). Com ``PF``, ``niFornecedor`` traz
  o CPF COMPLETO (11 dígitos): ele fica só na carga bruta da custódia; a resolução de entidades
  guarda apenas o CPF descaracterizado (RNF04).
"""

from __future__ import annotations

import os
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime

import httpx

from buscador.conectores.base import (
    Conector,
    ErroConector,
    FalhaTransitoria,
    RegistradorCaptura,
    RegistroContrato,
    ResultadoColeta,
    Retentativa,
)
from buscador.conectores.parametros import ParametrosPncp, validar
from buscador.custodia import marcar_simulado
from buscador.resolucao.normalizacao import CNPJ_BASICO, CNPJ_COMPLETO, somente_alfanumericos
from buscador.seguranca import carregar_json_estrito, numero_finito, sanitizar_json

CABECALHOS_GUARDADOS = ("content-type", "date", "server", "content-length", "cache-control")
TAMANHO_PAGINA_PADRAO = 50
MAX_PAGINAS_PADRAO = 5
STATUS_RETENTAVEIS = {429, 500, 502, 503, 504}


def _documento(texto: object) -> str:
    """CNPJ ou CPF sem pontuação. O CNPJ pode ser alfanumérico (letras de A a Z nas 12 primeiras
    posições; ver ``buscador.resolucao.normalizacao``), então as letras são mantidas, em
    maiúsculas."""
    return somente_alfanumericos(texto if isinstance(texto, str) else "")


def _data(valor: str, nome: str) -> date:
    try:
        return datetime.strptime(valor, "%Y%m%d").date()
    except ValueError as erro:
        raise ErroConector(
            f"O parâmetro '{nome}' não é uma data válida no formato AAAAMMDD."
        ) from erro


def _texto(valor: object) -> str:
    return valor if isinstance(valor, str) else ("" if valor is None else str(valor))


def _objeto(valor: object) -> dict:
    return valor if isinstance(valor, dict) else {}


@dataclass(frozen=True)
class _Resposta:
    status: int
    cabecalhos: dict[str, str]
    url: str
    conteudo: bytes


class ConectorPncp(Conector):
    nome = "pncp"
    # 1.1.0: retentativa pela política comum e registros brutos para a comparação de capturas.
    # 1.2.0: guarda também valorInicial, sequencialContrato, categoriaProcesso, código da unidade
    # e esfera/poder do órgão, usados pelas tipologias T5, T8 e T9 e pela coleta de termos.
    # 1.3.0: aceita o CNPJ alfanumérico (letras de A a Z não são mais descartadas);
    # ``cnpjFornecedor`` só com 8 ou 14 posições.
    versao = "1.3.0"

    def __init__(
        self,
        url_base: str = "https://pncp.gov.br/api/consulta",
        timeout_segundos: float = 30.0,
        tentativas: int = 3,
        espera_retentativa_segundos: float = 1.0,
        transporte: httpx.BaseTransport | None = None,
        dormir: Callable[[float], None] = time.sleep,
        max_paginas: int = 20,
        max_dias_periodo: int = 366,
        max_bytes_pagina: int = 8 * 1024 * 1024,
        prazo_total_segundos: float = 120.0,
        relogio: Callable[[], float] = time.monotonic,
    ) -> None:
        super().__init__(Retentativa(tentativas, espera_retentativa_segundos, dormir))
        self.url_base = url_base.rstrip("/")
        self.timeout_segundos = timeout_segundos
        self.transporte = transporte
        self.max_paginas = max_paginas
        self.max_dias_periodo = max_dias_periodo
        self.max_bytes_pagina = max_bytes_pagina
        self.prazo_total_segundos = prazo_total_segundos
        self.relogio = relogio
        # Transporte injetado = sem rede real. Só deixa de ser sintético se o transporte disser.
        self.simulado = transporte is not None and bool(getattr(transporte, "simulado", True))

    # ---- HTTP -------------------------------------------------------------------------------

    def _cliente(self) -> httpx.Client:
        return httpx.Client(
            base_url=self.url_base,
            timeout=self.timeout_segundos,
            transport=self.transporte,
            follow_redirects=False,
            headers={"Accept": "application/json", "User-Agent": "buscador-osint-mvp/0.1"},
        )

    def _requisitar(self, cliente: httpx.Client, consulta: dict) -> _Resposta:
        """Lê a resposta em blocos e desiste se ela passar do limite de bytes por página."""
        limite = self.max_bytes_pagina
        erro_tamanho = ErroConector(
            f"A resposta do PNCP passa do limite de {limite} bytes por página "
            "(PNCP_MAX_BYTES_PAGINA); reduza 'tamanhoPagina'."
        )
        with cliente.stream("GET", "/v1/contratos", params=consulta) as resposta:
            declarado = resposta.headers.get("content-length", "")
            if declarado.isdigit() and int(declarado) > limite:
                raise erro_tamanho
            partes: list[bytes] = []
            total = 0
            for bloco in resposta.iter_bytes():
                total += len(bloco)
                if total > limite:
                    raise erro_tamanho
                partes.append(bloco)
            return _Resposta(
                status=resposta.status_code,
                cabecalhos={
                    k.lower(): v
                    for k, v in resposta.headers.items()
                    if k.lower() in CABECALHOS_GUARDADOS
                },
                url=str(resposta.request.url),
                conteudo=b"".join(partes),
            )

    def _buscar_pagina(self, cliente: httpx.Client, consulta: dict) -> _Resposta:
        def tentar(ultima: bool) -> _Resposta:
            try:
                resposta = self._requisitar(cliente, consulta)
            except httpx.HTTPError as erro:
                raise FalhaTransitoria(f"{type(erro).__name__}: {str(erro)[:200]}") from erro
            # Na última tentativa a resposta de erro é devolvida, para ficar na custódia.
            if resposta.status in STATUS_RETENTAVEIS and not ultima:
                raise FalhaTransitoria(f"PNCP respondeu HTTP {resposta.status}")
            return resposta

        return self.retentativa.executar(tentar, "Falha ao consultar o PNCP")

    # ---- Coleta -----------------------------------------------------------------------------

    def _validar(self, parametros: dict) -> ParametrosPncp:
        validos = validar(ParametrosPncp, parametros)
        inicio = _data(validos.dataInicial, "dataInicial")
        fim = _data(validos.dataFinal, "dataFinal")
        if fim < inicio:
            raise ErroConector("'dataFinal' não pode ser anterior a 'dataInicial'.")
        if (fim - inicio).days + 1 > self.max_dias_periodo:
            raise ErroConector(
                f"O período pedido passa do máximo de {self.max_dias_periodo} dias por coleta "
                "(PNCP_MAX_DIAS_PERIODO). Divida a consulta em períodos menores."
            )
        if validos.max_paginas > self.max_paginas:
            raise ErroConector(
                f"'max_paginas' passa do teto de {self.max_paginas} páginas por coleta "
                "(PNCP_MAX_PAGINAS). Reduza o período ou filtre por 'cnpjOrgao'."
            )
        return validos

    def coletar(self, parametros: dict, registrar_captura: RegistradorCaptura) -> ResultadoColeta:
        validos = self._validar(parametros)
        max_paginas = validos.max_paginas
        cnpj_orgao = _documento(validos.cnpjOrgao) or None
        fornecedores = [d for d in map(_documento, validos.cnpjFornecedor) if d]
        if any(not (CNPJ_BASICO.fullmatch(f) or CNPJ_COMPLETO.fullmatch(f)) for f in fornecedores):
            # Um CPF (11 dígitos) não é aceito como filtro: ficaria gravado nos parâmetros da
            # coleta e na auditoria (RNF04).
            raise ErroConector(
                "'cnpjFornecedor' aceita o CNPJ completo (14 posições) ou a raiz (8 posições), "
                "numéricos ou alfanuméricos. O filtro por CPF não é oferecido (RNF04)."
            )

        resultado = ResultadoColeta(fonte=self.nome)
        valores_invalidos = 0
        pagina = 1
        inicio = self.relogio()
        with self._cliente() as cliente:
            while pagina <= max_paginas:
                if pagina > 1 and self.relogio() - inicio > self.prazo_total_segundos:
                    resultado.avisos.append(
                        f"Coleta interrompida antes da página {pagina}: prazo total de "
                        f"{self.prazo_total_segundos:g} s esgotado (PNCP_PRAZO_TOTAL_SEGUNDOS); "
                        "pode haver mais páginas no período."
                    )
                    break
                consulta = {
                    "dataInicial": validos.dataInicial,
                    "dataFinal": validos.dataFinal,
                    "pagina": pagina,
                    "tamanhoPagina": validos.tamanhoPagina,
                }
                if cnpj_orgao:
                    consulta["cnpjOrgao"] = cnpj_orgao
                resposta = self._buscar_pagina(cliente, consulta)
                metadados = {
                    "http_status": resposta.status,
                    "cabecalhos": resposta.cabecalhos,
                    "parametros_consulta": consulta,
                    "metodo": "GET",
                    "simulado": self.simulado,
                }
                if self.simulado:
                    metadados["origem_simulada"] = (
                        "transporte HTTP simulado (teste ou demonstração); a resposta não veio "
                        "do PNCP"
                    )
                captura = registrar_captura(
                    self.nome,
                    marcar_simulado(resposta.url) if self.simulado else resposta.url,
                    resposta.conteudo,
                    metadados,
                )
                resultado.capturas.append(captura)
                guardada = f"(captura {captura.id} guardada para custódia)"

                if resposta.status == 204:
                    resultado.avisos.append("PNCP devolveu 204: nenhum contrato no período.")
                    break
                if resposta.status != 200:
                    raise ErroConector(
                        f"PNCP respondeu HTTP {resposta.status} na página {pagina} {guardada}."
                    )
                try:
                    corpo = carregar_json_estrito(resposta.conteudo.decode("utf-8"))
                except (ValueError, UnicodeDecodeError, RecursionError) as erro:
                    raise ErroConector(
                        f"A resposta do PNCP na página {pagina} não é JSON válido {guardada}."
                    ) from erro
                itens = corpo.get("data") if isinstance(corpo, dict) else None
                if not isinstance(corpo, dict) or not isinstance(itens or [], list):
                    raise ErroConector(
                        f"A resposta do PNCP na página {pagina} não tem o formato esperado "
                        f"{guardada}."
                    )

                for item in itens or []:
                    if not isinstance(item, dict):
                        resultado.avisos.append(
                            f"Página {pagina}: item fora do formato esperado ignorado."
                        )
                        continue
                    registro = self._para_registro(item, captura.id, captura.url_ou_arquivo)
                    if registro.valor_global is None and item.get("valorGlobal") not in (None, ""):
                        valores_invalidos += 1
                    if fornecedores and not any(
                        registro.ni_fornecedor.startswith(f) for f in fornecedores
                    ):
                        continue
                    resultado.registros.append(registro)

                restantes = numero_finito(corpo.get("paginasRestantes")) or 0
                if restantes <= 0 or corpo.get("empty"):
                    break
                pagina += 1
            else:
                resultado.avisos.append(
                    f"Coleta interrompida no limite de max_paginas={max_paginas}; "
                    "pode haver mais páginas no período."
                )
        if valores_invalidos:
            resultado.avisos.append(
                f"{valores_invalidos} contrato(s) com 'valorGlobal' não numérico ou não finito: "
                "o valor foi desconsiderado."
            )
        return resultado

    @classmethod
    def registros_brutos(cls, conteudo: bytes, metadados: dict) -> dict[str, dict[str, object]]:
        """Contratos da página por ``numeroControlePNCP`` (carga que não é JSON: sem registros)."""
        try:
            corpo = carregar_json_estrito(conteudo.decode("utf-8"))
        except (ValueError, UnicodeDecodeError, RecursionError):
            return {}
        itens = corpo.get("data") if isinstance(corpo, dict) else None
        registros: dict[str, dict[str, object]] = {}
        for posicao, item in enumerate(itens if isinstance(itens, list) else []):
            if isinstance(item, dict):
                chave = _texto(item.get("numeroControlePNCP")) or f"item {posicao + 1}"
                registros[chave] = _achatar(item)
        return registros

    @staticmethod
    def _para_registro(item: dict, captura_id: str, url: str) -> RegistroContrato:
        orgao = _objeto(item.get("orgaoEntidade"))
        unidade = _objeto(item.get("unidadeOrgao"))
        extras = {
            "anoContrato": item.get("anoContrato"),
            "numeroContratoEmpenho": item.get("numeroContratoEmpenho"),
            "tipoContrato": _objeto(item.get("tipoContrato")).get("nome"),
            "dataVigenciaInicio": item.get("dataVigenciaInicio"),
            "dataVigenciaFim": item.get("dataVigenciaFim"),
            "dataPublicacaoPncp": item.get("dataPublicacaoPncp"),
            "unidadeOrgao": unidade.get("nomeUnidade"),
            "municipioOrgao": unidade.get("municipioNome"),
            "ufOrgao": unidade.get("ufSigla"),
            "processo": item.get("processo"),
            # Campos de ``RecuperarContratoDTO``, ``RecuperarUnidadeOrgaoDTO`` e
            # ``RecuperarOrgaoEntidadeDTO`` (OpenAPI de consulta do PNCP, conferida em 16/11/2026).
            "valorInicial": _para_float(item.get("valorInicial")),
            "sequencialContrato": item.get("sequencialContrato"),
            "numeroControlePncpCompra": item.get("numeroControlePncpCompra"),
            "categoriaProcessoId": _objeto(item.get("categoriaProcesso")).get("id"),
            "categoriaProcessoNome": _objeto(item.get("categoriaProcesso")).get("nome"),
            "codigoUnidade": unidade.get("codigoUnidade"),
            "esferaId": orgao.get("esferaId"),
            "poderId": orgao.get("poderId"),
        }
        return RegistroContrato(
            captura_id=captura_id,
            numero_controle_pncp=_texto(item.get("numeroControlePNCP")),
            ni_fornecedor=_documento(item.get("niFornecedor")),
            tipo_pessoa_fornecedor=_texto(item.get("tipoPessoa")),
            nome_fornecedor=_texto(item.get("nomeRazaoSocialFornecedor")),
            orgao_cnpj=_documento(orgao.get("cnpj")),
            orgao_razao_social=_texto(orgao.get("razaoSocial")),
            objeto=_texto(item.get("objetoContrato")),
            valor_global=_para_float(item.get("valorGlobal")),
            data_assinatura=_texto(item.get("dataAssinatura"))[:10],
            url_fonte=url,
            # Só escalares finitos: objetos aninhados e NaN/Infinity não entram nos atributos.
            extras={
                k: v
                for k, v in sanitizar_json(extras).items()  # type: ignore[union-attr]
                if not isinstance(v, dict | list)
            },
        )


def _achatar(item: dict) -> dict[str, object]:
    """Campos do item num só nível (``orgaoEntidade.cnpj``), para a comparação campo a campo."""
    plano: dict[str, object] = {}
    for chave, valor in item.items():
        if isinstance(valor, dict):
            for sub, v in valor.items():
                plano[f"{chave}.{sub}"] = v if not isinstance(v, dict | list) else str(v)
        else:
            plano[str(chave)] = valor if not isinstance(valor, list) else str(valor)
    return plano


def _para_float(valor: object) -> float | None:
    """Número finito ou ``None`` (vazio, não numérico, ``NaN``, ``Infinity``, ``1e999``)."""
    if valor == "":
        return None
    return numero_finito(valor)


class TransporteSimulado(httpx.BaseTransport):
    """Transporte httpx que serve páginas do PNCP a partir de arquivos locais (sem rede).

    Usado nos testes e na demonstração: ``paginas`` mapeia o número da página para o conteúdo
    JSON (bytes). Páginas inexistentes respondem 204, como faz a API quando não há dados.
    ``falhas_iniciais`` força respostas 503 nas primeiras chamadas (teste de retentativa).
    O atributo ``simulado`` faz o conector marcar as capturas como sintéticas.
    """

    simulado = True

    def __init__(self, paginas: dict[int, bytes], falhas_iniciais: int = 0) -> None:
        self.paginas = paginas
        self.falhas_restantes = falhas_iniciais
        self.chamadas: list[httpx.Request] = []

    @classmethod
    def de_diretorio(cls, diretorio: str | os.PathLike, **kw) -> TransporteSimulado:
        from pathlib import Path

        paginas: dict[int, bytes] = {}
        for arquivo in sorted(Path(diretorio).glob("contratos_pagina*.json")):
            numero = int(re.sub(r"\D", "", arquivo.stem) or 0)
            paginas[numero] = arquivo.read_bytes()
        return cls(paginas, **kw)

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.chamadas.append(request)
        if self.falhas_restantes > 0:
            self.falhas_restantes -= 1
            return httpx.Response(503, content=b"indisponivel", request=request)
        try:
            pagina = int(request.url.params.get("pagina", "1"))
        except ValueError:
            pagina = 0
        if pagina not in self.paginas:
            return httpx.Response(204, request=request)
        return httpx.Response(
            200,
            content=self.paginas[pagina],
            headers={"content-type": "application/json", "server": "simulado"},
            request=request,
        )
