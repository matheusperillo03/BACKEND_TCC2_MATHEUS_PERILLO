"""Teste de fumaça das FONTES REAIS: confere se cada fonte oficial ainda responde no formato que
os conectores esperam. É um teste MANUAL (usa a rede); a suíte automatizada (``pytest``) continua
só com dados sintéticos e nunca chama este script.

O que ele faz, fonte a fonte (só coleta passiva: ``GET``, ``HEAD`` e a listagem ``PROPFIND`` do
compartilhamento público da Receita; nada é escrito em nenhuma fonte):

- ``pncp``: roda o conector de contratos com uma janela pequena (poucos dias, 2 páginas de 10) e
  depois filtrando por um órgão; confere campos, paginação e o filtro;
- ``pncp_termos``: consulta os termos de um contrato público que tinha termos aditivos em
  17/11/2026 (``--contrato-termos`` troca o contrato);
- ``portal``: CEIS, CNEP e servidores do Portal da Transparência. Precisa da chave do operador em
  ``PORTAL_TRANSPARENCIA_CHAVE`` (ambiente ou ``.env``). Sem chave, a fonte é PULADA com aviso.
  A chave só vai no cabeçalho ``chave-api-dados``: este script nunca a imprime nem a grava;
- ``cnpj``: lista (sem baixar) os arquivos do mês mais recente dos dados abertos do CNPJ, com os
  tamanhos; se houver um recorte local em ``dados-reais/cnpj/`` roda o conector nele;
- ``tse``: mede por ``HEAD`` os ZIPs oficiais de candidatos e de prestação de contas; se houver
  arquivos locais em ``dados-reais/tse/`` roda o conector neles;
- ``edital``: extrai o texto dos PDFs que estiverem em ``dados-reais/editais/`` (não chama a IA).
  ``--baixar-edital CNPJ/ANO/SEQUENCIAL`` baixa antes o primeiro documento em PDF daquela
  contratação do PNCP para essa pasta.

Dados reais ficam FORA do git: a pasta ``dados-reais/`` está no ``.gitignore`` e no
``.dockerignore``. Nada é gravado no banco nem na custódia: as capturas ficam só em memória, para
conferir o formato. A saída traz só contagens e formatos, não nomes nem documentos de pessoas.

Limites respeitados: no máximo uma requisição por segundo em cada fonte (bem abaixo dos 400 por
minuto do Portal da Transparência; o PNCP, o TSE e a Receita não documentam limite), poucas
páginas por fonte e nenhum download de base completa.

Uso:
  uv run python scripts/testar_fontes_reais.py                 # todas as fontes
  uv run python scripts/testar_fontes_reais.py --fontes pncp,portal
  uv run python scripts/testar_fontes_reais.py --fontes edital \\
      --baixar-edital 00394460005887/2026/72

Código de saída: 0 se nenhuma fonte falhou (fonte pulada não é falha); 1 se alguma falhou.
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import io
import os
import re
import sys
import time
import urllib.parse
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

import httpx

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

from buscador.conectores.base import (  # noqa: E402
    ControleTaxa,
    ErroConector,
    FonteNaoConfigurada,
    RegistroCandidato,
    RegistroDoacao,
    RegistroEmpresa,
    RegistroEstabelecimento,
    RegistroSocio,
    Retentativa,
)
from buscador.conectores.cnpj_arquivo import ConectorCnpjArquivo, classificar_arquivo  # noqa: E402
from buscador.conectores.http_util import BuscadorHttp  # noqa: E402
from buscador.conectores.pncp import ConectorPncp  # noqa: E402
from buscador.conectores.pncp_termos import ConectorPncpTermos  # noqa: E402
from buscador.conectores.portal_transparencia import (  # noqa: E402
    CABECALHO_CHAVE,
    URL_BASE,
    VARIAVEL_CHAVE,
    ConectorPortalTransparencia,
)
from buscador.conectores.tse_arquivo import ConectorTseArquivo  # noqa: E402
from buscador.conectores.tse_arquivo import classificar as classificar_tse  # noqa: E402
from buscador.documentos import DocumentoRecusado, LimitesPdf, extrair_texto  # noqa: E402

AGENTE = "buscador-osint-mvp/0.2"
FONTES = ("pncp", "pncp_termos", "portal", "cnpj", "tse", "edital")
REQUISICOES_POR_MINUTO = 60

# Repositório "Arquivos da Receita Federal" (Nextcloud do Serpro). O endereço
# https://arquivos.receitafederal.gov.br/ redireciona para este compartilhamento público; a
# listagem usa o WebDAV público do próprio compartilhamento (conferido em 17/11/2026).
RECEITA_RAIZ = "https://arquivos.receitafederal.gov.br/"
RECEITA_DAV = "https://arquivos.receitafederal.gov.br/public.php/dav/files/{token}/"
RECEITA_PASTA_CNPJ = "Dados/Cadastros/CNPJ/"
TSE_CDN = "https://cdn.tse.jus.br/estatistica/sead/odsele"
# Contrato público com três termos aditivos em 17/11/2026 (conselho profissional federal).
CONTRATO_COM_TERMOS = "32533415000117/2023/2"
# Código SIAPE com servidores em 17/11/2026 (Controladoria-Geral da União).
ORGAO_SIAPE = "59000"


@dataclass
class Resultado:
    fonte: str
    situacao: str = "OK"  # OK | FALHOU | PULADO
    linhas: list[str] = field(default_factory=list)

    def nota(self, texto: str) -> None:
        self.linhas.append(texto)

    def falha(self, texto: str) -> None:
        self.situacao = "FALHOU"
        self.linhas.append(f"FALHA: {texto}")

    def exigir(self, condicao: bool, texto: str) -> None:
        if not condicao:
            self.falha(texto)


class Memoria:
    """Registrador de capturas em memória: nada vai para o banco nem para a custódia."""

    def __init__(self) -> None:
        self.capturas: list[SimpleNamespace] = []

    def __call__(self, fonte, origem, conteudo, metadados) -> SimpleNamespace:
        if not isinstance(conteudo, bytes | bytearray):
            conteudo = conteudo.read()
        captura = SimpleNamespace(
            id=f"mem-{len(self.capturas) + 1}",
            fonte=fonte,
            url_ou_arquivo=origem,
            sha256=hashlib.sha256(conteudo).hexdigest(),
            metadados=metadados,
            tamanho_bytes=len(conteudo),
        )
        self.capturas.append(captura)
        return captura

    def status(self) -> list[int]:
        return [c.metadados.get("http_status") for c in self.capturas]


def _forma(texto: object) -> str:
    """Formato de um valor sem mostrar o valor: dígitos viram 9 e letras viram a."""
    return re.sub(r"[A-Za-zÀ-ÿ]", "a", re.sub(r"\d", "9", str(texto or "")))


def _tamanho(n: int | None) -> str:
    if n is None:
        return "?"
    for unidade, divisor in (("GB", 1e9), ("MB", 1e6), ("kB", 1e3)):
        if n >= divisor:
            return f"{n / divisor:.2f} {unidade}".replace(".", ",")
    return f"{n} B"


def _cliente(**extras) -> httpx.Client:
    return httpx.Client(
        timeout=60, follow_redirects=False, headers={"User-Agent": AGENTE}, **extras
    )


# ---- PNCP ---------------------------------------------------------------------------------------


def testar_pncp(args) -> Resultado:
    r = Resultado("pncp")
    fim = date.today() - timedelta(days=1)
    inicio = fim - timedelta(days=max(1, args.dias) - 1)
    janela = {"dataInicial": inicio.strftime("%Y%m%d"), "dataFinal": fim.strftime("%Y%m%d")}
    memoria = Memoria()
    resultado = ConectorPncp().coletar({**janela, "max_paginas": 2, "tamanhoPagina": 10}, memoria)
    registros = resultado.registros
    r.nota(
        f"contratos publicados de {inicio} a {fim}: {len(registros)} registro(s) em "
        f"{len(memoria.capturas)} página(s), HTTP {memoria.status()}"
    )
    r.exigir(bool(registros), "nenhum contrato no período (esperado ao menos um)")
    r.exigir(
        len({x.numero_controle_pncp for x in registros}) == len(registros),
        "contratos repetidos entre as páginas (paginação)",
    )
    for campo in ("numero_controle_pncp", "ni_fornecedor", "orgao_cnpj", "data_assinatura"):
        vazios = sum(1 for x in registros if not getattr(x, campo))
        r.exigir(vazios == 0, f"{vazios} contrato(s) sem '{campo}': o layout pode ter mudado")
    r.exigir(
        all(re.fullmatch(r"\d{4}-\d{2}-\d{2}", x.data_assinatura) for x in registros),
        "dataAssinatura fora do formato AAAA-MM-DD",
    )
    r.exigir(
        all(x.extras.get("sequencialContrato") for x in registros),
        "contrato sem 'sequencialContrato' (usado na consulta de termos)",
    )
    tipos = collections.Counter(x.tipo_pessoa_fornecedor for x in registros)
    r.nota(
        f"tipoPessoa: {dict(tipos)}; valorGlobal ausente em "
        f"{sum(x.valor_global is None for x in registros)}"
    )
    if not registros:
        return r
    time.sleep(1)
    orgao = args.cnpj_orgao or registros[0].orgao_cnpj
    memoria = Memoria()
    inicio_orgao = fim - timedelta(days=29)
    filtrado = ConectorPncp().coletar(
        {
            "dataInicial": inicio_orgao.strftime("%Y%m%d"),
            "dataFinal": janela["dataFinal"],
            "cnpjOrgao": orgao,
            "max_paginas": 2,
            "tamanhoPagina": 10,
        },
        memoria,
    )
    r.nota(
        f"filtro por órgão ({orgao}, 30 dias): {len(filtrado.registros)} registro(s), "
        f"HTTP {memoria.status()}"
    )
    r.exigir(
        all(x.orgao_cnpj == re.sub(r"\D", "", orgao) for x in filtrado.registros),
        "a API devolveu contratos de outro órgão com 'cnpjOrgao' informado",
    )
    return r


def testar_pncp_termos(args) -> Resultado:
    r = Resultado("pncp_termos")
    try:
        cnpj, ano, sequencial = args.contrato_termos.split("/")
        contrato = {"cnpj": cnpj, "ano": int(ano), "sequencial": int(sequencial)}
    except ValueError:
        r.falha("--contrato-termos deve ter o formato CNPJ/ANO/SEQUENCIAL")
        return r
    memoria = Memoria()
    resultado = ConectorPncpTermos(requisicoes_por_minuto=REQUISICOES_POR_MINUTO).coletar(
        {"contratos": [contrato]}, memoria
    )
    r.nota(
        f"termos do contrato {args.contrato_termos}: {len(resultado.registros)} termo(s), "
        f"HTTP {memoria.status()} (sem autenticação)"
    )
    r.exigir(
        bool(resultado.registros),
        "nenhum termo (o contrato de referência pode ter mudado; use --contrato-termos)",
    )
    r.exigir(
        all(t.sequencial_termo and t.tipo_termo for t in resultado.registros),
        "termo sem 'sequencialTermoContrato' ou sem 'tipoTermoContratoNome'",
    )
    r.nota(f"tipos: {dict(collections.Counter(t.tipo_termo for t in resultado.registros))}")
    return r


# ---- Portal da Transparência --------------------------------------------------------------------


def _chave_do_portal() -> str:
    """Chave do operador: variável de ambiente ou ``.env`` (lido em tempo de execução). O valor
    nunca é impresso, registrado nem gravado."""
    chave = os.environ.get(VARIAVEL_CHAVE, "").strip()
    if chave:
        return chave
    from dotenv import dotenv_values

    return (dotenv_values(RAIZ / ".env").get(VARIAVEL_CHAVE) or "").strip()


def _cnpj_de_amostra(chave: str, recurso: str) -> str:
    """Um CNPJ que está no cadastro (primeira página, sem filtro), para consultar por CNPJ."""
    with BuscadorHttp(
        nome_fonte="o Portal da Transparência",
        url_base=URL_BASE,
        retentativa=Retentativa(2, 2.0),
        taxa=ControleTaxa(REQUISICOES_POR_MINUTO),
        cabecalhos={CABECALHO_CHAVE: chave},
    ) as http:
        resposta = http.obter(f"/api-de-dados/{recurso}", {"pagina": 1})
    if resposta.status in (401, 403):
        raise ErroConector(
            f"o Portal recusou a chave (HTTP {resposta.status}); confira {VARIAVEL_CHAVE}"
        )
    if resposta.status != 200:
        raise ErroConector(f"HTTP {resposta.status} em /api-de-dados/{recurso}")
    import json

    for item in json.loads(resposta.conteudo.decode("utf-8")):
        cnpj = re.sub(r"\D", "", ((item or {}).get("pessoa") or {}).get("cnpjFormatado") or "")
        if len(cnpj) == 14:
            return cnpj
    return ""


def testar_portal(args) -> Resultado:
    r = Resultado("portal")
    chave = _chave_do_portal()
    if not chave:
        r.situacao = "PULADO"
        r.nota(
            f"sem chave: defina {VARIAVEL_CHAVE} no ambiente ou no .env (cadastro em "
            "https://portaldatransparencia.gov.br/api-de-dados/cadastrar-email). Nenhuma "
            "requisição foi feita."
        )
        return r
    r.nota(f"chave encontrada ({VARIAVEL_CHAVE}); o valor não é exibido")

    def conector() -> ConectorPortalTransparencia:
        return ConectorPortalTransparencia(
            chave=chave,
            requisicoes_por_minuto=REQUISICOES_POR_MINUTO,
            retentativa=Retentativa(2, 2.0),
        )

    try:
        for recurso in ("ceis", "cnep"):
            cnpj = _cnpj_de_amostra(chave, recurso)
            if not cnpj:
                r.nota(f"{recurso}: primeira página sem pessoa jurídica; consulta por CNPJ pulada")
                continue
            memoria = Memoria()
            resultado = conector().coletar(
                {"recurso": recurso, "codigoSancionado": [cnpj]}, memoria
            )
            sancoes = resultado.registros
            r.nota(
                f"{recurso}: {len(sancoes)} sanção(ões) para um CNPJ do cadastro, "
                f"HTTP {memoria.status()}"
            )
            r.exigir(bool(sancoes), f"{recurso}: a consulta por CNPJ não devolveu o registro")
            r.exigir(
                all(s.cnpj_sancionado == cnpj and s.id_registro for s in sancoes),
                f"{recurso}: registro sem id ou de outro CNPJ",
            )
            r.exigir(
                all(re.fullmatch(r"\d{4}-\d{2}-\d{2}", s.data_inicio) for s in sancoes),
                f"{recurso}: dataInicioSancao fora de DD/MM/AAAA (o layout pode ter mudado)",
            )
            r.exigir(
                not any("formato não reconhecido" in a for a in resultado.avisos),
                f"{recurso}: datas em formato não reconhecido",
            )
            _sem_chave_nas_capturas(r, memoria, chave)
        memoria = Memoria()
        resultado = conector().coletar(
            {
                "recurso": "servidores",
                "orgaoServidorLotacao": args.orgao_siape,
                # A correspondência código SIAPE -> CNPJ é declarada pelo investigador; aqui
                # só o formato da resposta interessa, por isso um CNPJ de preenchimento.
                "cnpjOrgao": "00000000000000",
                "max_paginas": 1,
            },
            memoria,
        )
        servidores = resultado.registros
        r.nota(
            f"servidores (órgão SIAPE {args.orgao_siape}, 1 página): {len(servidores)} "
            f"registro(s), HTTP {memoria.status()}; formato do CPF: "
            f"{dict(collections.Counter(_forma(s.cpf) for s in servidores))}"
        )
        r.exigir(bool(servidores), "servidores: nenhum registro (troque --orgao-siape)")
        r.exigir(
            all(re.fullmatch(r"\*{3}\.\d{3}\.\d{3}-\*{2}", s.cpf) for s in servidores),
            "servidores: CPF fora do formato descaracterizado ***.NNN.NNN-**",
        )
        _sem_chave_nas_capturas(r, memoria, chave)
    except FonteNaoConfigurada as erro:
        r.situacao = "PULADO"
        r.nota(str(erro))
    return r


def _sem_chave_nas_capturas(r: Resultado, memoria: Memoria, chave: str) -> None:
    vazou = any(chave in c.url_ou_arquivo or chave in repr(c.metadados) for c in memoria.capturas)
    r.exigir(not vazou, "a chave apareceu na URL ou nos metadados de uma captura")


# ---- CNPJ (Receita Federal) ---------------------------------------------------------------------


_ITEM_DAV = re.compile(rb"<d:response>(.*?)</d:response>", re.S)
_HREF_DAV = re.compile(rb"<d:href>([^<]*)</d:href>")
_TAMANHO_DAV = re.compile(rb"<d:getcontentlength>(\d+)</d:getcontentlength>")


def _listar_webdav(cliente: httpx.Client, url: str) -> list[tuple[str, int | None]]:
    """``(nome, tamanho)`` dos itens de uma pasta do compartilhamento público (``PROPFIND``,
    profundidade 1; pasta tem tamanho ``None``). É a mesma listagem que a página faz.

    A resposta (XML do WebDAV) NÃO passa por um interpretador de XML: só interessam o endereço e
    o tamanho de cada item, lidos por expressão regular. Assim não há entidade a expandir."""
    resposta = cliente.request("PROPFIND", url, headers={"Depth": "1"})
    if resposta.status_code != 207:
        raise ErroConector(f"listagem respondeu HTTP {resposta.status_code}")
    if len(resposta.content) > 2 * 1024 * 1024:
        raise ErroConector("listagem maior que o esperado")
    base = urllib.parse.unquote(urllib.parse.urlparse(url).path).rstrip("/")
    itens = []
    for bloco in _ITEM_DAV.findall(resposta.content):
        href = _HREF_DAV.search(bloco)
        if not href:
            continue
        caminho = urllib.parse.unquote(href.group(1).decode("utf-8", "replace")).rstrip("/")
        if caminho == base:
            continue
        tamanho = _TAMANHO_DAV.search(bloco)
        itens.append((caminho.rsplit("/", 1)[-1], int(tamanho.group(1)) if tamanho else None))
    return itens


def tabela_cnpj(cliente: httpx.Client) -> tuple[str, str, list[tuple[str, int | None]]]:
    """``(url da pasta, mês, arquivos)`` do mês mais recente dos dados abertos do CNPJ."""
    resposta = cliente.get(RECEITA_RAIZ)
    destino = resposta.headers.get("location", "")
    achado = re.search(r"/index\.php/s/([A-Za-z0-9]+)", destino + resposta.text[:5000])
    if not achado:
        raise ErroConector("não foi possível descobrir o compartilhamento público da Receita")
    pasta = RECEITA_DAV.format(token=achado.group(1)) + RECEITA_PASTA_CNPJ
    time.sleep(1)
    meses = sorted(n for n, t in _listar_webdav(cliente, pasta) if re.fullmatch(r"\d{4}-\d{2}", n))
    if not meses:
        raise ErroConector("nenhuma pasta AAAA-MM em Dados/Cadastros/CNPJ")
    time.sleep(1)
    url = f"{pasta}{meses[-1]}/"
    return url, meses[-1], sorted(_listar_webdav(cliente, url))


def testar_cnpj(args) -> Resultado:
    r = Resultado("cnpj")
    with _cliente() as cliente:
        url, mes, arquivos = tabela_cnpj(cliente)
    r.nota(f"pasta oficial do mês mais recente ({mes}): {url}")
    grupos: dict[str, list[int]] = collections.defaultdict(list)
    for nome, tamanho in arquivos:
        grupos[re.sub(r"\d*\.zip$", "", nome)].append(tamanho or 0)
        if args.detalhes:
            r.nota(f"  {nome}: {_tamanho(tamanho)}")
    for grupo, tamanhos in grupos.items():
        r.nota(f"  {grupo}: {len(tamanhos)} arquivo(s), {_tamanho(sum(tamanhos))} compactado(s)")
    r.nota(f"  TOTAL: {_tamanho(sum(sum(t) for t in grupos.values()))} compactados")
    for esperado in ("Empresas", "Estabelecimentos", "Socios"):
        r.exigir(len(grupos.get(esperado, [])) == 10, f"esperados 10 arquivos {esperado}*.zip")
    raiz = args.dados_reais / "cnpj"
    pastas = (
        [
            p
            for p in sorted(raiz.iterdir())
            if p.is_dir() and any(classificar_arquivo(f.name) for f in p.iterdir() if f.is_file())
        ]
        if raiz.is_dir()
        else []
    )
    if not pastas:
        r.nota(
            f"sem recorte local em {raiz}/ (gere um com scripts/recortar_cnpj.py): "
            "conector não testado"
        )
        return r
    for pasta in pastas:
        memoria = Memoria()
        resultado = ConectorCnpjArquivo(dir_base=raiz).coletar({"diretorio": pasta.name}, memoria)
        contagem = collections.Counter(type(x).__name__ for x in resultado.registros)
        r.nota(f"recorte local '{pasta.name}': {dict(contagem)}; avisos: {len(resultado.avisos)}")
        socios = [x for x in resultado.registros if isinstance(x, RegistroSocio)]
        formas = collections.Counter(
            (x.identificador_socio, _forma(x.cnpj_cpf_socio)) for x in socios
        )
        r.nota(f"  formato do documento do sócio por tipo: {dict(formas)}")
        r.exigir(
            all(
                re.fullmatch(r"\*{3}\d{6}\*{2}", x.cnpj_cpf_socio)
                for x in socios
                if x.identificador_socio == "2"
            ),
            "CPF de sócio pessoa física fora do formato descaracterizado ***NNNNNN**",
        )
        r.exigir(
            all(len(x.cnpj_basico) == 8 for x in resultado.registros),
            "CNPJ básico com tamanho diferente de 8",
        )
        estabelecimentos = [
            x for x in resultado.registros if isinstance(x, RegistroEstabelecimento)
        ]
        r.exigir(
            all(re.fullmatch(r"\d{8}", x.data_inicio_atividade) for x in estabelecimentos),
            "data de início de atividade fora de AAAAMMDD",
        )
        empresas = [x for x in resultado.registros if isinstance(x, RegistroEmpresa)]
        r.exigir(all(x.capital_social is not None for x in empresas), "capital social não numérico")
        r.exigir(
            all(c.metadados["simulado"] is False for c in memoria.capturas),
            "captura real marcada como sintética",
        )
    return r


# ---- TSE ----------------------------------------------------------------------------------------


def testar_tse(args) -> Resultado:
    r = Resultado("tse")
    with _cliente() as cliente:
        for ano in args.anos_tse:
            for nome, url in (
                ("candidatos", f"{TSE_CDN}/consulta_cand/consulta_cand_{ano}.zip"),
                (
                    "prestação de contas (receitas e despesas)",
                    f"{TSE_CDN}/prestacao_contas/prestacao_de_contas_eleitorais_candidatos_{ano}.zip",
                ),
            ):
                resposta = cliente.head(url)
                tamanho = resposta.headers.get("content-length", "")
                r.nota(
                    f"{ano}, {nome}: HTTP {resposta.status_code}, "
                    f"{_tamanho(int(tamanho)) if tamanho.isdigit() else '?'}, aceita Range: "
                    f"{resposta.headers.get('accept-ranges', 'não informado')} — {url}"
                )
                r.exigir(
                    resposta.status_code == 200, f"{url} respondeu HTTP {resposta.status_code}"
                )
                time.sleep(1)
    raiz = args.dados_reais / "tse"
    pastas = (
        [
            p
            for p in sorted(raiz.iterdir())
            if p.is_dir() and any(classificar_tse(f.name) for f in p.iterdir() if f.is_file())
        ]
        if raiz.is_dir()
        else []
    )
    if not pastas:
        r.nota(f"sem arquivos locais em {raiz}/: conector não testado")
        return r
    for pasta in pastas:
        memoria = Memoria()
        resultado = ConectorTseArquivo(dir_base=raiz).coletar({"diretorio": pasta.name}, memoria)
        candidatos = [
            x
            for x in resultado.registros
            if isinstance(x, RegistroCandidato) and x.origem == "consulta_cand"
        ]
        doacoes = [x for x in resultado.registros if isinstance(x, RegistroDoacao)]
        codificacoes = {
            v for c in memoria.capturas for v in c.metadados["codificacao_detectada"].values()
        }
        r.nota(
            f"local '{pasta.name}': {len(candidatos)} candidato(s), {len(doacoes)} doação(ões), "
            f"anos {sorted({x.ano for x in resultado.registros})}, "
            f"codificação {sorted(codificacoes)}"
        )
        for aviso in resultado.avisos:
            r.nota(f"  aviso do conector: {aviso}")
        r.exigir(all(len(x.ano) == 4 for x in resultado.registros), "registro sem ano da eleição")
        r.exigir(
            all(x.data and x.valor is not None for x in doacoes), "doação sem data ou sem valor"
        )
        r.exigir(
            all(len(x.documento_doador) in (11, 14) for x in doacoes), "doador sem CPF/CNPJ legível"
        )
        if candidatos and doacoes:
            conhecidos = {x.sq_candidato for x in candidatos}
            ligados = sum(1 for x in doacoes if x.sq_candidato in conhecidos)
            r.nota(
                "  doações ligadas a um candidato do arquivo de candidatos: "
                f"{ligados} de {len(doacoes)}"
            )
    return r


# ---- Editais em PDF -----------------------------------------------------------------------------


def baixar_edital(args, r: Resultado) -> None:
    """Baixa o primeiro PDF listado para a contratação (API de integração do PNCP, ``GET`` sem
    autenticação) para ``dados-reais/editais/pncp-<cnpj>-<ano>-<seq>/``. O nome do arquivo é
    montado aqui; o nome sugerido pelo servidor (``content-disposition``) não é usado."""
    try:
        cnpj, ano, sequencial = args.baixar_edital.split("/")
        cnpj, ano, sequencial = re.sub(r"\D", "", cnpj), int(ano), int(sequencial)
    except ValueError:
        r.falha("--baixar-edital deve ter o formato CNPJ/ANO/SEQUENCIAL")
        return
    base = f"https://pncp.gov.br/api/pncp/v1/orgaos/{cnpj}/compras/{ano}/{sequencial}/arquivos"
    limite = LimitesPdf().max_bytes
    with _cliente() as cliente:
        lista = cliente.get(base, headers={"Accept": "application/json"})
        if lista.status_code != 200:
            r.falha(f"lista de documentos respondeu HTTP {lista.status_code}")
            return
        documentos = [d for d in lista.json() if isinstance(d, dict)]
        r.nota(f"contratação {cnpj}/{ano}/{sequencial}: {len(documentos)} documento(s) listado(s)")
        for documento in documentos:
            numero = documento.get("sequencialDocumento")
            if not isinstance(numero, int):
                continue
            time.sleep(1)
            with cliente.stream("GET", f"{base}/{numero}") as resposta:
                declarado = resposta.headers.get("content-length", "")
                if resposta.status_code != 200 or (declarado.isdigit() and int(declarado) > limite):
                    continue
                blocos, total = [], 0
                for bloco in resposta.iter_bytes():
                    total += len(bloco)
                    if total > limite:
                        break
                    blocos.append(bloco)
                conteudo = b"".join(blocos)
            if total > limite or not conteudo.startswith(b"%PDF-"):
                continue
            pasta = args.dados_reais / "editais" / f"pncp-{cnpj}-{ano}-{sequencial}"
            pasta.mkdir(parents=True, exist_ok=True)
            destino = pasta / f"edital-{cnpj}-{ano}-{sequencial}-doc{numero}.pdf"
            destino.write_bytes(conteudo)
            r.nota(
                f"baixado o documento {numero} ({documento.get('tipoDocumentoNome')}): "
                f"{_tamanho(len(conteudo))}, SHA-256 {hashlib.sha256(conteudo).hexdigest()}"
            )
            return
    r.falha("nenhum documento em PDF dentro do limite de tamanho nessa contratação")


def testar_edital(args) -> Resultado:
    r = Resultado("edital")
    if args.baixar_edital:
        baixar_edital(args, r)
    raiz = args.dados_reais / "editais"
    pdfs = sorted(raiz.rglob("*.pdf")) if raiz.is_dir() else []
    if not pdfs:
        if r.situacao == "OK":
            r.situacao = "PULADO"
        r.nota(f"nenhum PDF em {raiz}/ (use --baixar-edital CNPJ/ANO/SEQUENCIAL)")
        return r
    for pdf in pdfs[:10]:
        conteudo = pdf.read_bytes()
        inicio = time.monotonic()
        try:
            documento = extrair_texto(conteudo)
        except DocumentoRecusado as recusa:
            r.nota(f"{pdf.name}: sem texto — {recusa}")
            continue
        r.nota(
            f"{pdf.name}: {_tamanho(len(conteudo))}, {documento.paginas} página(s) "
            f"({documento.paginas_com_texto} com texto), {len(documento.texto)} caracteres em "
            f"{time.monotonic() - inicio:.2f} s com {documento.extrator}; SHA-256 do arquivo "
            f"{hashlib.sha256(conteudo).hexdigest()[:16]}…, do texto {documento.sha256[:16]}…"
        )
        if args.trecho:
            trecho = " ".join(documento.texto.split())[: args.trecho]
            r.nota(f"  início do texto: {trecho}")
    return r


TESTES = {
    "pncp": testar_pncp,
    "pncp_termos": testar_pncp_termos,
    "portal": testar_portal,
    "cnpj": testar_cnpj,
    "tse": testar_tse,
    "edital": testar_edital,
}


def principal(argumentos: list[str] | None = None) -> int:
    analisador = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    analisador.add_argument("--fontes", default=",".join(FONTES), help="lista separada por vírgula")
    analisador.add_argument("--dados-reais", type=Path, default=RAIZ / "dados-reais")
    analisador.add_argument("--dias", type=int, default=3, help="janela do PNCP, em dias")
    analisador.add_argument("--cnpj-orgao", default="", help="órgão do teste de filtro do PNCP")
    analisador.add_argument("--contrato-termos", default=CONTRATO_COM_TERMOS)
    analisador.add_argument("--orgao-siape", default=ORGAO_SIAPE)
    analisador.add_argument("--anos-tse", type=int, nargs="+", default=[2024, 2026])
    analisador.add_argument("--baixar-edital", default="", metavar="CNPJ/ANO/SEQUENCIAL")
    analisador.add_argument("--trecho", type=int, default=0, help="mostra N caracteres do edital")
    analisador.add_argument("--detalhes", action="store_true", help="lista cada arquivo do CNPJ")
    args = analisador.parse_args(argumentos)
    pedidas = [f.strip() for f in args.fontes.split(",") if f.strip()]
    desconhecidas = [f for f in pedidas if f not in TESTES]
    if desconhecidas:
        analisador.error(
            f"fonte desconhecida: {', '.join(desconhecidas)} (use {', '.join(FONTES)})"
        )
    resultados: list[Resultado] = []
    for fonte in pedidas:
        try:
            resultado = TESTES[fonte](args)
        except (ErroConector, httpx.HTTPError, OSError, ValueError) as erro:
            resultado = Resultado(fonte)
            # A mensagem dos conectores não leva a chave (ela só existe no cabeçalho).
            resultado.falha(f"{type(erro).__name__}: {str(erro)[:300]}")
        resultados.append(resultado)
        print(f"\n[{resultado.situacao}] {fonte}")
        for linha in resultado.linhas:
            print(f"  {linha}")
    print("\nResumo: " + ", ".join(f"{x.fonte}={x.situacao}" for x in resultados))
    return 1 if any(x.situacao == "FALHOU" for x in resultados) else 0


if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    raise SystemExit(principal())
