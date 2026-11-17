"""Conector da API do Portal da Transparência (CGU): CEIS, CNEP, CEPIM, CEAF, convênios e
servidores do Executivo Federal.

Fatos conferidos na OpenAPI oficial (https://api.portaldatransparencia.gov.br/v3/api-docs, acesso
em 16/11/2026; ver docs/sistema/fontes-horizonte2.md na monografia):

- a chave é obrigatória e vai no cabeçalho ``chave-api-dados``; aqui ela vem só da variável de
  ambiente ``PORTAL_TRANSPARENCIA_CHAVE``. Sem chave o conector levanta ``FonteNaoConfigurada``:
  a coleta é fechada como "não configurado", sem falhar as demais fontes;
- ``GET /api-de-dados/ceis`` e ``/api-de-dados/cnep``: ``pagina`` (obrigatório) e
  ``codigoSancionado`` ("CNPJ ou CPF Sancionado"); a resposta é um array de ``CeisDTO``/``CnepDTO``
  (``id``, ``dataInicioSancao``, ``dataFimSancao``, ``tipoSancao``, ``orgaoSancionador``,
  ``sancionado.codigoFormatado``, ``pessoa.cnpjFormatado``, ``numeroProcesso``);
- ``GET /api-de-dados/servidores``: ``pagina`` e ao menos um de ``orgaoServidorLotacao``,
  ``orgaoServidorExercicio`` (código SIAPE) ou CPF; a resposta é um array de
  ``CadastroServidorDTO`` (``servidor.pessoa.nome``, ``servidor.pessoa.cpfFormatado``,
  ``servidor.orgaoServidorLotacao``, ``servidor.orgaoServidorExercicio``, ``servidor.situacao``);
- limite de requisições (https://portaldatransparencia.gov.br/api-de-dados, conferido de novo
  em 17/11/2026): "No período de 6:00 às 23:59, o Portal aceita 400 requisições por minuto. Já
  no período das 00:00 às 5:59, são aceitas 700 requisições por minuto." A página não lista
  limite diferente para CEIS, CNEP ou servidores. O controle de taxa fica em
  ``PORTAL_TRANSPARENCIA_REQUISICOES_POR_MINUTO`` (padrão 300, abaixo do limite diurno);
- termos de uso (mesma página): a API "é de livre utilização, conforme previsto no Decreto
  nº 8.777, de 11 de maio de 2016"; para o conjunto completo o Portal sugere as planilhas de
  dados abertos, e a API para "consultas pontuais" (é o uso deste conector).

Conferido contra a API real em 17/11/2026, com a chave do operador (``scripts/
testar_fontes_reais.py`` repete o teste e pula a fonte, com aviso, se não houver chave):

- a paginação não tem envelope: cada página é um array com até 15 itens e a página além do fim
  é um array vazio (``[]``); a coleta para na primeira página vazia ou em ``max_paginas``;
- datas das sanções em ``DD/MM/AAAA``; ``dataFimSancao`` pode vir como "Sem informação" (vira
  data ausente: sanção sem data final). ``AAAA-MM-DD`` continua aceito por precaução;
- CEIS sem filtro traz também pessoas físicas, com ``codigoFormatado`` no formato de CPF; o
  conector só consulta por CNPJ e sanção de pessoa física não vira registro (RNF04);
- em ``servidores``, ``cpfFormatado`` já vem descaracterizado (``***.NNN.NNN-**``); a resolução
  de entidades guarda só os seis dígitos centrais de qualquer forma. O servidor só é consultado
  por órgão, nunca por CPF. Sem órgão nem CPF a API responde 400 ("Filtros mínimos"); uma
  página de servidores levou cerca de 6 s;
- os códigos SIAPE vêm de ``GET /api-de-dados/orgaos-siape`` (``descricao`` filtra pelo nome).
  Um mesmo órgão pode ter mais de um código, e nem todo código tem servidores (a
  Controladoria-Geral da União aparece como 20125, sem servidores, e 59000, com servidores);
- chave inválida: HTTP 401, corpo ``{"Erro na API": "Chave de API inválida!"}``.

CEPIM, CEAF e convênios (tipologias L10 e L16). OpenAPI e API real conferidas em 17/11/2026, com
uma página de cada rota e a chave do operador (só a estrutura e o formato dos campos foram lidos):

- ``GET /api-de-dados/cepim``: ``cnpjSancionado`` e ``pagina``; array de ``CepimDTO`` (``id``,
  ``dataReferencia`` em ``DD/MM/AAAA``, ``motivo``, ``orgaoSuperior.nome``,
  ``pessoaJuridica.cnpjFormatado`` e ``convenio.numero``). O cadastro não traz período;
- ``GET /api-de-dados/ceaf``: ``nomeSancionado`` e ``pagina`` (a rota aceita ``cpfSancionado``,
  que este conector NÃO usa, RNF04); array de ``CeafDTO`` (``id``, ``dataPublicacao`` em
  ``DD/MM/AAAA``, ``punicao.nomePunido``, ``punicao.cpfPunidoFormatado``, que já vem
  descaracterizado como ``***.NNN.NNN-**``, ``punicao.portaria``, ``punicao.processo``,
  ``tipoPunicao.descricao`` e ``orgaoLotacao.nome``);
- ``GET /api-de-dados/convenios``: sem filtro responde 400 ("escolha um período de até 1 mês ou
  um convenente ou um órgão/entidade ou uma localidade [...] ou um número de convênio"); com
  ``convenente`` (CNPJ de 14 dígitos) responde 200. Array de ``ConvenioDTO`` (``id``,
  ``dataInicioVigencia`` e ``dataFinalVigencia`` em ``AAAA-MM-DD``, ``dimConvenio.numero`` e
  ``.objeto``, ``situacao``, ``convenente.cnpjFormatado``, ``orgao.nome`` e ``orgao.cnpj``,
  ``valor`` e ``valorLiberado``). PENDENTE: a consulta de teste por ``convenente`` voltou vazia
  (o CNPJ usado não tinha convênio), então o conector confere o CNPJ de cada item recebido.

Só coleta passiva: o conector faz apenas ``GET`` nesses endpoints de dados abertos.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from datetime import datetime

import httpx

from buscador.conectores.base import (
    Conector,
    ControleTaxa,
    ErroConector,
    FonteNaoConfigurada,
    RegistradorCaptura,
    RegistroConvenio,
    RegistroExpulsao,
    RegistroSancao,
    RegistroServidor,
    ResultadoColeta,
    Retentativa,
)
from buscador.conectores.http_util import BuscadorHttp, Resposta
from buscador.conectores.parametros import ParametrosPortalTransparencia, validar
from buscador.custodia import marcar_simulado
from buscador.resolucao.normalizacao import normalizar_cnpj, normalizar_nome
from buscador.seguranca import carregar_json_estrito, numero_finito

VARIAVEL_CHAVE = "PORTAL_TRANSPARENCIA_CHAVE"
CABECALHO_CHAVE = "chave-api-dados"
URL_BASE = "https://api.portaldatransparencia.gov.br"
CAMINHOS = {
    "ceis": "/api-de-dados/ceis",
    "cnep": "/api-de-dados/cnep",
    "servidores": "/api-de-dados/servidores",
    "cepim": "/api-de-dados/cepim",
    "ceaf": "/api-de-dados/ceaf",
    "convenios": "/api-de-dados/convenios",
}
# Limite documentado no período diurno (o mais restritivo dos dois não restritos).
LIMITE_DOCUMENTADO_POR_MINUTO = 400

MENSAGEM_SEM_CHAVE = (
    "Portal da Transparência não configurado: defina a chave de API na variável de ambiente "
    f"{VARIAVEL_CHAVE} (cadastro em https://portaldatransparencia.gov.br/api-de-dados/"
    f"cadastrar-email; a chave vai no cabeçalho '{CABECALHO_CHAVE}'). Nenhuma consulta foi feita."
)


def _texto(valor: object) -> str:
    return valor.strip() if isinstance(valor, str) else ("" if valor is None else str(valor))


def _objeto(valor: object) -> dict:
    return valor if isinstance(valor, dict) else {}


def data_para_iso(valor: object) -> str:
    """``DD/MM/AAAA`` ou ``AAAA-MM-DD[...]`` -> ``AAAA-MM-DD``; qualquer outro texto -> ``""``."""
    texto = _texto(valor)
    for formato in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(texto[:10], formato).date().isoformat()
        except ValueError:
            continue
    return ""


class ConectorPortalTransparencia(Conector):
    nome = "portal_transparencia"
    # 1.0.0: coleta real de CEIS, CNEP e servidores (antes era um stub).
    # 1.1.0: aceita o CNPJ alfanumérico (letras de A a Z não são mais descartadas).
    # 1.2.0: CEPIM, CEAF e convênios (tipologias L10 e L16).
    versao = "1.2.0"

    def __init__(
        self,
        chave: str | None = None,
        retentativa: Retentativa | None = None,
        url_base: str = URL_BASE,
        timeout_segundos: float = 30.0,
        requisicoes_por_minuto: float = 300,
        max_paginas: int = 200,
        max_bytes_pagina: int = 8 * 1024 * 1024,
        transporte: httpx.BaseTransport | None = None,
        dormir: Callable[[float], None] = time.sleep,
        relogio: Callable[[], float] = time.monotonic,
    ) -> None:
        super().__init__(retentativa)
        self.chave = chave if chave is not None else os.environ.get(VARIAVEL_CHAVE, "")
        self.url_base = url_base
        self.timeout_segundos = timeout_segundos
        self.max_paginas = max_paginas
        self.max_bytes_pagina = max_bytes_pagina
        self.transporte = transporte
        self.taxa = ControleTaxa(requisicoes_por_minuto, dormir=dormir, relogio=relogio)

    def coletar(self, parametros: dict, registrar_captura: RegistradorCaptura) -> ResultadoColeta:
        validos = validar(ParametrosPortalTransparencia, parametros)
        if not self.chave.strip():
            raise FonteNaoConfigurada(MENSAGEM_SEM_CHAVE)
        if validos.max_paginas > self.max_paginas:
            raise ErroConector(
                f"'max_paginas' passa do teto de {self.max_paginas} páginas por consulta "
                "(PORTAL_TRANSPARENCIA_MAX_PAGINAS)."
            )
        resultado = ResultadoColeta(fonte=self.nome)
        with BuscadorHttp(
            nome_fonte="o Portal da Transparência",
            url_base=self.url_base,
            retentativa=self.retentativa,
            taxa=self.taxa,
            timeout_segundos=self.timeout_segundos,
            max_bytes=self.max_bytes_pagina,
            transporte=self.transporte,
            cabecalhos={CABECALHO_CHAVE: self.chave.strip()},
        ) as http:
            if validos.recurso == "servidores":
                self._servidores(http, validos, registrar_captura, resultado)
            elif validos.recurso == "cepim":
                self._cepim(http, validos, registrar_captura, resultado)
            elif validos.recurso == "ceaf":
                self._ceaf(http, validos, registrar_captura, resultado)
            elif validos.recurso == "convenios":
                self._convenios(http, validos, registrar_captura, resultado)
            else:
                self._sancoes(http, validos, registrar_captura, resultado)
        if self.taxa.esperas:
            resultado.avisos.append(
                f"Controle de taxa: {self.taxa.esperas} espera(s), "
                f"{self.taxa.total_esperado:.1f} s no total, para respeitar "
                f"{self.taxa.por_minuto:g} requisições por minuto."
            )
        return resultado

    # ---- Páginas ------------------------------------------------------------------------------

    def _paginas(
        self,
        http: BuscadorHttp,
        recurso: str,
        filtro: dict,
        max_paginas: int,
        registrar_captura: RegistradorCaptura,
        resultado: ResultadoColeta,
    ):
        """Gera ``(captura, itens)`` de cada página até a primeira vazia ou até ``max_paginas``."""
        for pagina in range(1, max_paginas + 1):
            consulta = {**filtro, "pagina": pagina}
            resposta = http.obter(CAMINHOS[recurso], consulta)
            captura = self._guardar(http, recurso, consulta, resposta, registrar_captura)
            resultado.capturas.append(captura)
            guardada = f"(captura {captura.id} guardada para custódia)"
            if resposta.status in (401, 403):
                raise ErroConector(
                    f"O Portal da Transparência recusou a chave de API (HTTP {resposta.status}) "
                    f"{guardada}. Confira {VARIAVEL_CHAVE}."
                )
            if resposta.status != 200:
                raise ErroConector(
                    f"O Portal da Transparência respondeu HTTP {resposta.status} em "
                    f"{CAMINHOS[recurso]}, página {pagina} {guardada}."
                )
            try:
                corpo = carregar_json_estrito(resposta.conteudo.decode("utf-8"))
            except (ValueError, UnicodeDecodeError, RecursionError) as erro:
                raise ErroConector(
                    f"A resposta do Portal da Transparência não é JSON válido {guardada}."
                ) from erro
            if not isinstance(corpo, list):
                # Detecção de mudança de formato (RNF12): a OpenAPI declara um array.
                raise ErroConector(
                    "A resposta do Portal da Transparência não tem o formato esperado (array "
                    f"JSON) {guardada}: a fonte pode ter mudado de formato."
                )
            if not corpo:
                return
            yield captura, corpo
        resultado.avisos.append(
            f"Consulta de {recurso} interrompida no limite de max_paginas={max_paginas}; pode "
            "haver mais registros."
        )

    def _guardar(
        self,
        http: BuscadorHttp,
        recurso: str,
        consulta: dict,
        resposta: Resposta,
        registrar_captura: RegistradorCaptura,
    ):
        # A chave vai só no cabeçalho da requisição: não entra na URL nem nos metadados.
        metadados = {
            "recurso": recurso,
            "http_status": resposta.status,
            "cabecalhos": resposta.cabecalhos,
            "parametros_consulta": consulta,
            "metodo": "GET",
            "simulado": http.simulado,
        }
        if http.simulado:
            metadados["origem_simulada"] = (
                "transporte HTTP simulado (teste ou demonstração); a resposta não veio do "
                "Portal da Transparência"
            )
        return registrar_captura(
            self.nome,
            marcar_simulado(resposta.url) if http.simulado else resposta.url,
            resposta.conteudo,
            metadados,
        )

    # ---- CEIS e CNEP --------------------------------------------------------------------------

    def _sancoes(self, http, validos, registrar_captura, resultado: ResultadoColeta) -> None:
        cadastro = validos.recurso.upper()
        consultados = [d for d in map(normalizar_cnpj, validos.codigoSancionado) if d]
        if not consultados:
            raise ErroConector(
                "Informe em 'codigoSancionado' ao menos um CNPJ de 14 posições (ou use "
                "'do_caso': true para consultar as contratadas do caso)."
            )
        ignorados_pf = fora_do_filtro = datas_invalidas = 0
        for cnpj in dict.fromkeys(consultados):
            for captura, itens in self._paginas(
                http,
                validos.recurso,
                {"codigoSancionado": cnpj},
                validos.max_paginas,
                registrar_captura,
                resultado,
            ):
                for item in itens:
                    if not isinstance(item, dict):
                        continue
                    sancionado = _objeto(item.get("sancionado"))
                    pessoa = _objeto(item.get("pessoa"))
                    # CNPJ numérico ou alfanumérico (14 posições); CPF (11 dígitos, completo ou
                    # descaracterizado) não tem essa forma e cai no ramo abaixo.
                    documento = normalizar_cnpj(pessoa.get("cnpjFormatado")) or normalizar_cnpj(
                        sancionado.get("codigoFormatado")
                    )
                    if not documento:
                        # Pessoa física (CPF): não vira registro (minimização, RNF04).
                        ignorados_pf += 1
                        continue
                    if documento[:8] != cnpj[:8]:
                        fora_do_filtro += 1
                        continue
                    inicio = data_para_iso(item.get("dataInicioSancao"))
                    fim = data_para_iso(item.get("dataFimSancao"))
                    if _texto(item.get("dataInicioSancao")) and not inicio:
                        datas_invalidas += 1
                    tipo = _objeto(item.get("tipoSancao"))
                    resultado.registros.append(
                        RegistroSancao(
                            captura_id=captura.id,
                            cadastro=cadastro,
                            id_registro=_texto(item.get("id")),
                            cnpj_sancionado=documento,
                            nome_sancionado=_texto(sancionado.get("nome"))
                            or _texto(pessoa.get("razaoSocialReceita"))
                            or _texto(pessoa.get("nome")),
                            data_inicio=inicio,
                            data_fim=fim,
                            tipo_sancao=_texto(tipo.get("descricaoResumida"))
                            or _texto(tipo.get("descricaoPortal")),
                            orgao_sancionador=_texto(
                                _objeto(item.get("orgaoSancionador")).get("nome")
                            ),
                            numero_processo=_texto(item.get("numeroProcesso")),
                            url_fonte=captura.url_ou_arquivo,
                        )
                    )
        if ignorados_pf:
            resultado.avisos.append(
                f"{ignorados_pf} registro(s) do {cadastro} sem CNPJ de 14 posições (pessoa física "
                "ou documento ilegível) não foram incorporados."
            )
        if fora_do_filtro:
            resultado.avisos.append(
                f"{fora_do_filtro} registro(s) do {cadastro} de CNPJ diferente do consultado "
                "foram ignorados."
            )
        if datas_invalidas:
            resultado.avisos.append(
                f"{datas_invalidas} registro(s) do {cadastro} com data de início em formato não "
                "reconhecido: a data ficou vazia e a tipologia T6 não os considera vigentes."
            )

    # ---- CEPIM, CEAF e convênios --------------------------------------------------------------

    def _cnpjs_consultados(self, validos, para_que: str) -> list[str]:
        consultados = [d for d in map(normalizar_cnpj, validos.codigoSancionado) if d]
        if not consultados:
            raise ErroConector(
                f"Informe em 'codigoSancionado' ao menos um CNPJ de 14 posições {para_que} (ou "
                "use 'do_caso': true)."
            )
        return list(dict.fromkeys(consultados))

    def _cepim(self, http, validos, registrar_captura, resultado: ResultadoColeta) -> None:
        fora_do_filtro = 0
        for cnpj in self._cnpjs_consultados(validos, "da entidade"):
            for captura, itens in self._paginas(
                http,
                "cepim",
                {"cnpjSancionado": cnpj},
                validos.max_paginas,
                registrar_captura,
                resultado,
            ):
                for item in itens:
                    if not isinstance(item, dict):
                        continue
                    pessoa = _objeto(item.get("pessoaJuridica"))
                    documento = normalizar_cnpj(pessoa.get("cnpjFormatado"))
                    if not documento or documento[:8] != cnpj[:8]:
                        fora_do_filtro += 1
                        continue
                    resultado.registros.append(
                        RegistroSancao(
                            captura_id=captura.id,
                            cadastro="CEPIM",
                            id_registro=_texto(item.get("id")),
                            cnpj_sancionado=documento,
                            nome_sancionado=_texto(pessoa.get("razaoSocialReceita"))
                            or _texto(pessoa.get("nome")),
                            # O CEPIM não tem período: as datas de início e de fim ficam vazias.
                            tipo_sancao=_texto(item.get("motivo")),
                            orgao_sancionador=_texto(
                                _objeto(item.get("orgaoSuperior")).get("nome")
                            ),
                            numero_processo=_texto(_objeto(item.get("convenio")).get("numero")),
                            url_fonte=captura.url_ou_arquivo,
                            data_referencia=data_para_iso(item.get("dataReferencia")),
                        )
                    )
        if fora_do_filtro:
            resultado.avisos.append(
                f"{fora_do_filtro} registro(s) do CEPIM sem CNPJ ou de CNPJ diferente do "
                "consultado foram ignorados."
            )

    def _ceaf(self, http, validos, registrar_captura, resultado: ResultadoColeta) -> None:
        nomes = list(dict.fromkeys(n for n in validos.nomeSancionado if normalizar_nome(n)))
        if not nomes:
            raise ErroConector(
                "Para 'ceaf', informe em 'nomeSancionado' ao menos um nome completo (ou use "
                "'do_caso': true). A consulta por CPF não é oferecida (RNF04)."
            )
        outros_nomes = 0
        for nome in nomes:
            procurado = normalizar_nome(nome)
            for captura, itens in self._paginas(
                http,
                "ceaf",
                {"nomeSancionado": nome},
                validos.max_paginas,
                registrar_captura,
                resultado,
            ):
                for item in itens:
                    punicao = _objeto(_objeto(item).get("punicao"))
                    pessoa = _objeto(_objeto(item).get("pessoa"))
                    punido = _texto(punicao.get("nomePunido")) or _texto(pessoa.get("nome"))
                    # A busca da fonte pode ser por parte do nome: só entra o nome idêntico
                    # (minimização: quem não foi consultado não vira registro).
                    if normalizar_nome(punido) != procurado:
                        outros_nomes += 1
                        continue
                    resultado.registros.append(
                        RegistroExpulsao(
                            captura_id=captura.id,
                            id_registro=_texto(item.get("id")),
                            nome=punido,
                            cpf=_texto(punicao.get("cpfPunidoFormatado"))
                            or _texto(pessoa.get("cpfFormatado")),
                            tipo_punicao=_texto(_objeto(item.get("tipoPunicao")).get("descricao")),
                            data_publicacao=data_para_iso(item.get("dataPublicacao")),
                            orgao_lotacao=_texto(_objeto(item.get("orgaoLotacao")).get("nome")),
                            portaria=_texto(punicao.get("portaria")),
                            processo=_texto(punicao.get("processo")),
                            url_fonte=captura.url_ou_arquivo,
                        )
                    )
        if outros_nomes:
            resultado.avisos.append(
                f"{outros_nomes} registro(s) do CEAF de nome diferente do consultado não foram "
                "incorporados."
            )

    def _convenios(self, http, validos, registrar_captura, resultado: ResultadoColeta) -> None:
        fora_do_filtro = 0
        for cnpj in self._cnpjs_consultados(validos, "do convenente"):
            for captura, itens in self._paginas(
                http,
                "convenios",
                {"convenente": cnpj},
                validos.max_paginas,
                registrar_captura,
                resultado,
            ):
                for item in itens:
                    if not isinstance(item, dict):
                        continue
                    convenente = _objeto(item.get("convenente"))
                    documento = normalizar_cnpj(convenente.get("cnpjFormatado"))
                    if not documento or documento[:8] != cnpj[:8]:
                        fora_do_filtro += 1
                        continue
                    dimensao = _objeto(item.get("dimConvenio"))
                    orgao = _objeto(item.get("orgao"))
                    resultado.registros.append(
                        RegistroConvenio(
                            captura_id=captura.id,
                            id_registro=_texto(item.get("id")),
                            cnpj_convenente=documento,
                            nome_convenente=_texto(convenente.get("razaoSocialReceita"))
                            or _texto(convenente.get("nome")),
                            numero=_texto(dimensao.get("numero")) or _texto(dimensao.get("codigo")),
                            objeto=_texto(dimensao.get("objeto")),
                            data_inicio_vigencia=data_para_iso(item.get("dataInicioVigencia")),
                            data_fim_vigencia=data_para_iso(item.get("dataFinalVigencia")),
                            valor=numero_finito(item.get("valor")),
                            valor_liberado=numero_finito(item.get("valorLiberado")),
                            situacao=_texto(item.get("situacao")),
                            orgao_nome=_texto(orgao.get("nome")),
                            orgao_cnpj=normalizar_cnpj(orgao.get("cnpj")) or "",
                            url_fonte=captura.url_ou_arquivo,
                        )
                    )
        if fora_do_filtro:
            resultado.avisos.append(
                f"{fora_do_filtro} convênio(s) sem CNPJ do convenente ou de CNPJ diferente do "
                "consultado foram ignorados."
            )

    # ---- Servidores ---------------------------------------------------------------------------

    def _servidores(self, http, validos, registrar_captura, resultado: ResultadoColeta) -> None:
        if validos.orgaoServidorLotacao:
            vinculo, parametro, codigo = (
                "lotacao",
                "orgaoServidorLotacao",
                validos.orgaoServidorLotacao,
            )
        elif validos.orgaoServidorExercicio:
            vinculo, parametro, codigo = (
                "exercicio",
                "orgaoServidorExercicio",
                validos.orgaoServidorExercicio,
            )
        else:
            raise ErroConector(
                "Para 'servidores', informe 'orgaoServidorLotacao' ou 'orgaoServidorExercicio' "
                "(código SIAPE do órgão). A consulta por CPF não é oferecida (RNF04)."
            )
        cnpj_orgao = normalizar_cnpj(validos.cnpjOrgao)
        if not cnpj_orgao:
            raise ErroConector(
                "Para 'servidores', informe em 'cnpjOrgao' o CNPJ (14 posições) do órgão "
                "contratante do caso a que o código SIAPE corresponde: a correspondência é "
                "declarada pelo investigador e fica registrada nos parâmetros da coleta."
            )
        sem_nome = 0
        for captura, itens in self._paginas(
            http,
            "servidores",
            {parametro: codigo},
            validos.max_paginas,
            registrar_captura,
            resultado,
        ):
            for item in itens:
                servidor = _objeto(_objeto(item).get("servidor"))
                pessoa = _objeto(servidor.get("pessoa"))
                nome = _texto(pessoa.get("nome"))
                if not nome:
                    sem_nome += 1
                    continue
                orgao = _objeto(
                    servidor.get(
                        "orgaoServidorLotacao" if vinculo == "lotacao" else "orgaoServidorExercicio"
                    )
                )
                resultado.registros.append(
                    RegistroServidor(
                        captura_id=captura.id,
                        nome=nome,
                        cpf=_texto(pessoa.get("cpfFormatado")),
                        codigo_orgao=codigo,
                        nome_orgao=_texto(orgao.get("nome")),
                        cnpj_orgao=cnpj_orgao,
                        vinculo=vinculo,
                        situacao=_texto(servidor.get("situacao")),
                        tipo_servidor=_texto(servidor.get("tipoServidor")),
                        url_fonte=captura.url_ou_arquivo,
                    )
                )
        if sem_nome:
            resultado.avisos.append(f"{sem_nome} servidor(es) sem nome foram ignorados.")

    @classmethod
    def registros_brutos(cls, conteudo: bytes, metadados: dict) -> dict[str, dict[str, object]]:
        """Itens da página por ``id`` (sanções) ou ``servidor.id`` (servidores)."""
        from buscador.conectores.pncp import _achatar

        try:
            corpo = carregar_json_estrito(conteudo.decode("utf-8"))
        except (ValueError, UnicodeDecodeError, RecursionError):
            return {}
        registros: dict[str, dict[str, object]] = {}
        for posicao, item in enumerate(corpo if isinstance(corpo, list) else []):
            if not isinstance(item, dict):
                continue
            identidade = item.get("id") or _objeto(item.get("servidor")).get("id")
            registros[_texto(identidade) or f"item {posicao + 1}"] = _achatar(item)
        return registros
