"""Conector dos termos de contrato do PNCP (aditivos, apostilamentos e rescisões).

Fatos conferidos em fonte oficial (acesso em 16/11/2026; docs/sistema/fontes-horizonte2.md):

- a API de Consulta do PNCP não tem termos; eles estão na "API PNCP" (integração), servidor
  ``/api/pncp`` (OpenAPI: https://pncp.gov.br/api/pncp/v3/api-docs), em
  ``GET /v1/orgaos/{cnpj}/contratos/{ano}/{sequencial}/termos`` ("Consultar Termos de Contrato"),
  com ``pagina`` e ``tamanhoPagina`` opcionais; a resposta 200 é um array de
  ``RecuperarTermoContratoDTO``;
- a OpenAPI não declara ``security`` para esse ``GET`` (os de escrita declaram ``bearerAuth``);
- campos usados: ``sequencialTermoContrato``, ``numeroTermoContrato``, ``tipoTermoContratoNome``,
  ``objetoTermoContrato``, ``valorAcrescido`` ("Valor acrescido ou suprimido do contrato
  original", Manual de Integração do PNCP, https://pncp.gov.br/manual/pt-br/latest/singlehtml/),
  ``qualificacaoAcrescimoSupressao``, ``qualificacaoReajuste``, ``dataAssinatura`` e
  ``fundamentoLegal``.

A consulta é contrato a contrato, só para os contratos pedidos (os do caso): não existe endpoint
de termos por período.

Testado contra a API real em 17/11/2026: o endpoint responde SEM autenticação; contrato sem
termos responde 204 (sem corpo); contrato com termos responde 200 com um array JSON no layout
acima (conferido num contrato com três termos aditivos); ``pagina=2`` além do fim responde 204.
Se um dia responder 401/403, o conector guarda a resposta na custódia e explica.

Só coleta passiva (``GET``). Com transporte injetado, as capturas saem marcadas como sintéticas.
"""

from __future__ import annotations

import time
from collections.abc import Callable

import httpx

from buscador.conectores.base import (
    Conector,
    ControleTaxa,
    ErroConector,
    RegistradorCaptura,
    RegistroTermo,
    ResultadoColeta,
    Retentativa,
)
from buscador.conectores.http_util import BuscadorHttp
from buscador.conectores.parametros import ParametrosPncpTermos, validar
from buscador.custodia import marcar_simulado
from buscador.resolucao.normalizacao import somente_alfanumericos
from buscador.seguranca import carregar_json_estrito, numero_finito

URL_BASE = "https://pncp.gov.br/api/pncp"
# Teto de páginas de termos por contrato (um contrato raramente tem dezenas de termos).
MAX_PAGINAS_POR_CONTRATO = 10


def _texto(valor: object) -> str:
    return valor.strip() if isinstance(valor, str) else ("" if valor is None else str(valor))


def _booleano(valor: object) -> bool | None:
    return valor if isinstance(valor, bool) else None


def _inteiro(valor: object) -> int | None:
    numero = numero_finito(valor)
    return int(numero) if numero is not None and numero == int(numero) else None


class ConectorPncpTermos(Conector):
    nome = "pncp_termos"
    # 1.0.1: aceita o CNPJ alfanumérico (letras de A a Z não são mais descartadas).
    versao = "1.0.1"

    def __init__(
        self,
        url_base: str = URL_BASE,
        timeout_segundos: float = 30.0,
        tentativas: int = 3,
        espera_retentativa_segundos: float = 1.0,
        requisicoes_por_minuto: float = 60,
        max_contratos: int = 100,
        max_bytes_pagina: int = 8 * 1024 * 1024,
        transporte: httpx.BaseTransport | None = None,
        dormir: Callable[[float], None] = time.sleep,
        relogio: Callable[[], float] = time.monotonic,
    ) -> None:
        super().__init__(Retentativa(tentativas, espera_retentativa_segundos, dormir))
        self.url_base = url_base
        self.timeout_segundos = timeout_segundos
        self.max_contratos = max_contratos
        self.max_bytes_pagina = max_bytes_pagina
        self.transporte = transporte
        self.taxa = ControleTaxa(requisicoes_por_minuto, dormir=dormir, relogio=relogio)

    def coletar(self, parametros: dict, registrar_captura: RegistradorCaptura) -> ResultadoColeta:
        validos = validar(ParametrosPncpTermos, parametros)
        if not validos.contratos:
            raise ErroConector(
                "Informe em 'contratos' os contratos a consultar (cnpj, ano e sequencial) ou use "
                "'do_caso': true para consultar os contratos do caso."
            )
        if len(validos.contratos) > self.max_contratos:
            raise ErroConector(
                f"A coleta pede termos de {len(validos.contratos)} contratos e passa do teto de "
                f"{self.max_contratos} por coleta (PNCP_TERMOS_MAX_CONTRATOS)."
            )
        resultado = ResultadoColeta(fonte=self.nome)
        valores_invalidos = 0
        with BuscadorHttp(
            nome_fonte="o PNCP (termos de contrato)",
            url_base=self.url_base,
            retentativa=self.retentativa,
            taxa=self.taxa,
            timeout_segundos=self.timeout_segundos,
            max_bytes=self.max_bytes_pagina,
            transporte=self.transporte,
        ) as http:
            for contrato in validos.contratos:
                cnpj = somente_alfanumericos(contrato.cnpj)
                caminho = f"/v1/orgaos/{cnpj}/contratos/{contrato.ano}/{contrato.sequencial}/termos"
                anterior: bytes | None = None
                for pagina in range(1, MAX_PAGINAS_POR_CONTRATO + 1):
                    consulta = {"pagina": pagina}
                    resposta = http.obter(caminho, consulta)
                    metadados = {
                        "recurso": "termos",
                        "contrato": {
                            "cnpj": cnpj,
                            "ano": contrato.ano,
                            "sequencial": contrato.sequencial,
                            "numero_controle_pncp": contrato.numero_controle_pncp,
                        },
                        "http_status": resposta.status,
                        "cabecalhos": resposta.cabecalhos,
                        "parametros_consulta": consulta,
                        "metodo": "GET",
                        "simulado": http.simulado,
                    }
                    if http.simulado:
                        metadados["origem_simulada"] = (
                            "transporte HTTP simulado (teste ou demonstração); a resposta não "
                            "veio do PNCP"
                        )
                    captura = registrar_captura(
                        self.nome,
                        marcar_simulado(resposta.url) if http.simulado else resposta.url,
                        resposta.conteudo,
                        metadados,
                    )
                    resultado.capturas.append(captura)
                    guardada = f"(captura {captura.id} guardada para custódia)"
                    if resposta.status in (204, 404):
                        break
                    if resposta.status in (401, 403):
                        raise ErroConector(
                            f"O PNCP respondeu HTTP {resposta.status} na consulta de termos "
                            f"{guardada}. A OpenAPI não declara autenticação para este endpoint "
                            "e ele respondia sem credencial em 17/11/2026: a fonte pode ter "
                            "mudado; a coleta de termos fica pendente."
                        )
                    if resposta.status != 200:
                        raise ErroConector(
                            f"O PNCP respondeu HTTP {resposta.status} na consulta de termos do "
                            f"contrato {contrato.numero_controle_pncp or caminho} {guardada}."
                        )
                    try:
                        corpo = carregar_json_estrito(resposta.conteudo.decode("utf-8"))
                    except (ValueError, UnicodeDecodeError, RecursionError) as erro:
                        raise ErroConector(
                            f"A resposta de termos do PNCP não é JSON válido {guardada}."
                        ) from erro
                    # A OpenAPI declara um array; aceita-se também o envelope paginado (``data``).
                    itens = corpo.get("data") if isinstance(corpo, dict) else corpo
                    if not isinstance(itens, list):
                        raise ErroConector(
                            "A resposta de termos do PNCP não tem o formato esperado (array "
                            f"JSON) {guardada}: a fonte pode ter mudado de formato."
                        )
                    # Página vazia ou igual à anterior (servidor ignorou ``pagina``): fim.
                    if not itens or resposta.conteudo == anterior:
                        break
                    anterior = resposta.conteudo
                    for item in itens:
                        if not isinstance(item, dict):
                            continue
                        valor = item.get("valorAcrescido")
                        acrescido = None if valor in (None, "") else numero_finito(valor)
                        if acrescido is None and valor not in (None, ""):
                            valores_invalidos += 1
                        resultado.registros.append(
                            RegistroTermo(
                                captura_id=captura.id,
                                numero_controle_pncp=contrato.numero_controle_pncp,
                                sequencial_termo=_inteiro(item.get("sequencialTermoContrato")),
                                numero_termo=_texto(item.get("numeroTermoContrato")),
                                tipo_termo=_texto(item.get("tipoTermoContratoNome")),
                                objeto=_texto(item.get("objetoTermoContrato")),
                                valor_acrescido=acrescido,
                                acrescimo_supressao=_booleano(
                                    item.get("qualificacaoAcrescimoSupressao")
                                ),
                                reajuste=_booleano(item.get("qualificacaoReajuste")),
                                data_assinatura=_texto(item.get("dataAssinatura"))[:10],
                                fundamento_legal=_texto(item.get("fundamentoLegal")),
                                url_fonte=captura.url_ou_arquivo,
                            )
                        )
        if valores_invalidos:
            resultado.avisos.append(
                f"{valores_invalidos} termo(s) com 'valorAcrescido' não numérico ou não finito: "
                "o valor foi desconsiderado."
            )
        if self.taxa.esperas:
            resultado.avisos.append(
                f"Controle de taxa: {self.taxa.esperas} espera(s), "
                f"{self.taxa.total_esperado:.1f} s no total."
            )
        return resultado

    @classmethod
    def registros_brutos(cls, conteudo: bytes, metadados: dict) -> dict[str, dict[str, object]]:
        """Termos da página por ``sequencialTermoContrato``."""
        from buscador.conectores.pncp import _achatar

        try:
            corpo = carregar_json_estrito(conteudo.decode("utf-8"))
        except (ValueError, UnicodeDecodeError, RecursionError):
            return {}
        itens = corpo.get("data") if isinstance(corpo, dict) else corpo
        registros: dict[str, dict[str, object]] = {}
        for posicao, item in enumerate(itens if isinstance(itens, list) else []):
            if isinstance(item, dict):
                chave = _texto(item.get("sequencialTermoContrato")) or f"item {posicao + 1}"
                registros[f"termo {chave}"] = _achatar(item)
        return registros
