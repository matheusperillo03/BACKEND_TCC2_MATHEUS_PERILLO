"""Conector dos dados abertos do TSE (candidatos e receitas de campanha), lidos de arquivos locais.

Como o conector do CNPJ, não baixa o conjunto inteiro: lê os arquivos CSV (ou ZIP com CSV) que o
investigador colocou numa pasta dentro da raiz de ingestão ``TSE_DIR_BASE``.

Fatos conferidos em fonte oficial (https://dadosabertos.tse.jus.br, conjuntos "candidatos-2024" e
"prestacao-de-contas-eleitorais-2024", e os leiames dentro dos ZIPs; acesso em 16/11/2026; ver
docs/sistema/fontes-horizonte2.md):

- arquivos ``consulta_cand_<ano>_<UF|BRASIL>.csv`` (candidatos) e
  ``receitas_candidatos_<ano>_<UF|BRASIL>.csv`` (receitas); "Os campos estão entre aspas e
  separados por ponto e vírgula, inclusive os campos numéricos"; a primeira linha é o cabeçalho;
- codificação: o leiame diz Latin-1, mas o arquivo de 2024 veio em UTF-8. O conector tenta UTF-8
  e, se falhar, usa Latin-1; a codificação detectada fica nos metadados da captura;
- candidatos: ``SQ_CANDIDATO``, ``NM_CANDIDATO``, ``NM_URNA_CANDIDATO``, ``SG_PARTIDO``, ``SG_UF``,
  ``SG_UE``, ``DS_CARGO``, ``DS_SIT_TOT_TURNO``;
- receitas: ``SQ_CANDIDATO``, ``NM_CANDIDATO``, ``NR_CPF_CNPJ_DOADOR``, ``NM_DOADOR``,
  ``NM_DOADOR_RFB``, ``DS_ORIGEM_RECEITA``, ``VR_RECEITA``, ``DT_RECEITA``, ``SG_UF``, ``SG_UE``,
  ``NM_UE``, ``DS_CARGO``, ``SG_PARTIDO``;
- ``NR_CPF_CANDIDATO`` não é divulgado em 2024 (vem "-4"): o vínculo entre candidato e
  doações é feito por ``SQ_CANDIDATO``. Até a versão 1.1.0 o conector nunca lia a coluna; desde
  a 1.2.0 ele a lê só no arquivo de candidatos e guarda só os seis dígitos centrais (ver
  "Ligação de pessoas", abaixo);
- valores especiais: ``#NULO``, ``#NE``, ``-1``, ``-3``, ``-4`` e "NÃO DIVULGÁVEL" são tratados
  como ausentes.

Cuidados com dados pessoais (RNF04):

- nas receitas de origem "Recursos próprios" o doador é o próprio candidato e o CPF dele aparece
  em ``NR_CPF_CNPJ_DOADOR``; essas linhas são IGNORADAS (contadas num aviso);
- o CPF do doador pessoa física sai daqui como veio, mas a resolução de entidades guarda só os
  seis dígitos centrais (``***NNNNNN**``); o arquivo bruto fica na custódia, como toda captura.

Conferido com arquivos reais em 17/11/2026 (eleições de 2026, recorte do DF; os arquivos reais
ficam fora do git, em ``dados-reais/``):

- endereços oficiais: ``https://cdn.tse.jus.br/estatistica/sead/odsele/consulta_cand/
  consulta_cand_<ano>.zip`` e ``.../prestacao_contas/
  prestacao_de_contas_eleitorais_candidatos_<ano>.zip`` (listados em
  https://dadosabertos.tse.jus.br/dataset/candidatos-<ano>). Tamanhos em 17/11/2026: candidatos
  2024, 64,8 MB; contas 2024, 1,32 GB (o CSV ``receitas_candidatos_2024_BRASIL`` tem 1,4 GB
  descompactado e o de SP, 264 MB); contas 2026, 366 MB. O servidor aceita ``Range``: dá para
  extrair só o CSV de uma UF sem baixar o ZIP inteiro;
- os arquivos de 2026 vieram em Latin-1 (como diz o leiame); os de 2024 tinham vindo em UTF-8;
- o arquivo de receitas chama o ano de ``AA_ELEICAO``; o de candidatos, de ``ANO_ELEICAO``;
- ``VR_RECEITA`` veio com vírgula decimal (``1234,56``) e ``DT_RECEITA`` em ``DD/MM/AAAA``;
- em ``consulta_cand_2026_DF.csv`` a coluna ``NR_CPF_CANDIDATO`` veio PREENCHIDA (11 dígitos),
  ao contrário de 2024; no arquivo de receitas veio ``-4``. Desde a versão 1.2.0 o
  conector guarda dela só os seis dígitos centrais; o arquivo bruto da custódia a contém;
- o CSV de uma UF grande passa do teto padrão ``TSE_MAX_BYTES_ARQUIVO`` (64 MiB): ver "Arquivo
  acima do teto", abaixo.

Arquivo acima do teto (decisão do autor, 17/11/2026). Até ``TSE_MAX_BYTES_ARQUIVO`` o arquivo é
lido inteiro e a captura guarda os bytes do arquivo (como sempre). Acima do teto (ou ZIP com um
CSV acima dele), o conector lê EM FLUXO, linha a linha, sem carregar o arquivo na memória, e
exige ao menos um filtro: ``uf`` (``SG_UF``), ``municipio`` (``NM_UE``) ou ``candidatos``
(``SQ_CANDIDATO``; ``do_caso`` usa os candidatos do caso). A captura passa a ser o RECORTE: o
cabeçalho e as linhas que passam no filtro, com os bytes originais dessas linhas (inclusive as
colunas ``NR_CPF_CANDIDATO`` e ``NR_TITULO_ELEITORAL_CANDIDATO``, como vieram). O SHA-256 da
captura é o do recorte; o
tamanho e o SHA-256 do CSV inteiro, o filtro e a contagem de linhas ficam em
``metadados.recorte``, e a origem leva o sufixo ``#recorte``. Limites que continuam valendo: o
recorte até ``TSE_MAX_BYTES_ARQUIVO``; o arquivo percorrido até ``TSE_MAX_BYTES_FLUXO`` (2 GiB);
cada linha física até 1 MiB; os registros até ``TSE_MAX_REGISTROS``. A codificação é decidida
linha a linha (UTF-8 estrito e, se falhar, Latin-1), o que só difere da detecção sobre o arquivo
inteiro num arquivo Latin-1 com uma linha que seja, por coincidência, UTF-8 válido com acento.
Medição em ``scripts/medir_tse_fluxo.py`` (resultado no README).

Ligação de pessoas (decisão do autor de 23/11/2026, opção (a); substitui a regra anterior, em que
``NR_CPF_CANDIDATO`` nunca era lida):

- ``NR_CPF_CANDIDATO`` é lida só no arquivo de candidatos e reduzida, na própria leitura, aos seis
  dígitos centrais (``***NNNNNN**``, o mesmo padrão do quadro de sócios e do Portal da
  Transparência). O CPF inteiro não chega a registros, entidades, alertas, relatório nem
  exportações; ele existe apenas na carga bruta da custódia, e a rota de texto da captura o
  devolve descaracterizado (``buscador.privacidade``). Nos arquivos de receitas e de despesas a
  coluna (e ``NR_CPF_VICE_CANDIDATO``) continua sem ser lida;
- ``NR_TITULO_ELEITORAL_CANDIDATO`` (12 dígitos) é lida só para calcular um resumo com chave
  (HMAC-SHA256 com a chave própria ``CHAVE_IDENTIFICADORES``), que reconhece o
  mesmo candidato entre eleições, já que ``SQ_CANDIDATO`` muda a cada eleição. O título em claro
  não sai deste módulo, e o resumo nunca é exibido nem exportado. Sem a função de resumo (conector
  criado sem a chave), o título é ignorado e um aviso diz que as candidaturas não serão ligadas.

Bens declarados (``ConectorTseBens``, fonte ``tse_bens``, H50): arquivos
``bem_candidato_<ano>_<UF|BRASIL>.csv`` (de ``bem_candidato_<ano>.zip``), colunas ``ANO_ELEICAO``,
``SG_UF``, ``SG_UE``, ``SQ_CANDIDATO``, ``NR_ORDEM_BEM_CANDIDATO``, ``CD_TIPO_BEM_CANDIDATO``,
``DS_TIPO_BEM_CANDIDATO``, ``DS_BEM_CANDIDATO``, ``VR_BEM_CANDIDATO`` e
``DT_ULT_ATUAL_BEM_CANDIDATO`` (cabeçalho igual em 2018, 2022 e 2026; conferido no leiame e nos
arquivos em 17/11/2026, ``docs/lavagem/fontes.md`` da monografia). O arquivo não tem CPF nem
título; ``VR_BEM_CANDIDATO`` é o "Valor, em reais, do bem patrimonial material declarado pela
candidata ou candidato". O arquivo não tem ``NM_UE``: o filtro ``municipio`` não vale para bens.

Despesas contratadas (H52): arquivos ``despesas_contratadas_candidatos_<ano>_<UF|BRASIL>.csv``, do
mesmo ZIP da prestação de contas. Colunas conferidas em 23/11/2026 no cabeçalho de
``despesas_contratadas_candidatos_2026_DF.csv`` e em ``leiame_despesas-contratadas-candidatos.pdf``
(dentro de ``prestacao_de_contas_eleitorais_candidatos_2026.zip``): ``SQ_CANDIDATO``,
``NM_CANDIDATO``, ``CD_TIPO_FORNECEDOR`` ("0- Pessoa Jurídica; 1- Pessoa Física"),
``CD_CNAE_FORNECEDOR`` e ``DS_CNAE_FORNECEDOR`` ("preenchido apenas para os fornecedores do tipo
0 - Pessoa Jurídica"), ``NR_CPF_CNPJ_FORNECEDOR``, ``NM_FORNECEDOR``, ``NM_FORNECEDOR_RFB``,
``SQ_DESPESA``, ``DT_DESPESA``, ``DS_DESPESA`` e ``VR_DESPESA_CONTRATADA``. Só as despesas com
fornecedor de CNPJ viram registro; as de fornecedor pessoa física são contadas num aviso e
ignoradas (minimização, RNF04). O arquivo ``despesas_pagas_candidatos`` não traz o fornecedor
(liga-se por ``SQ_DESPESA``) e não é lido.

Ainda tratado de forma defensiva: o formato de ``VR_RECEITA`` (aceita
vírgula ou ponto decimal) e de ``DT_RECEITA`` (aceita ``DD/MM/AAAA`` e ``AAAA-MM-DD``); o ano da
eleição vem da coluna ``ANO_ELEICAO``, se existir, ou do nome do arquivo.
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
import time
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import BinaryIO

from buscador.conectores import arquivos
from buscador.conectores.base import (
    Conector,
    ErroConector,
    FalhaTransitoria,
    RegistradorCaptura,
    RegistroBem,
    RegistroCandidato,
    RegistroDespesa,
    RegistroDoacao,
    ResultadoColeta,
    Retentativa,
)
from buscador.conectores.parametros import ParametrosTseArquivo, ParametrosTseBens, validar
from buscador.conectores.portal_transparencia import data_para_iso
from buscador.resolucao.normalizacao import (
    CNPJ_COMPLETO,
    mascarar_cpf,
    normalizar_nome,
    somente_alfanumericos,
    somente_digitos,
)
from buscador.seguranca import numero_finito

VARIAVEL_RAIZ = "TSE_DIR_BASE"
SEPARADOR = ";"
VALORES_AUSENTES = {"#NULO", "#NULO#", "#NE", "#NE#", "-1", "-3", "-4", "NAO DIVULGAVEL"}
ORIGEM_RECURSOS_PROPRIOS = "RECURSOS PROPRIOS"

COLUNAS_CANDIDATOS = ("SQ_CANDIDATO", "NM_CANDIDATO", "DS_CARGO", "SG_UF", "DS_SIT_TOT_TURNO")
COLUNAS_RECEITAS = ("SQ_CANDIDATO", "NR_CPF_CNPJ_DOADOR", "NM_DOADOR", "VR_RECEITA")
COLUNAS_BENS = (
    "SQ_CANDIDATO",
    "NR_ORDEM_BEM_CANDIDATO",
    "DS_TIPO_BEM_CANDIDATO",
    "VR_BEM_CANDIDATO",
)
COLUNAS_DESPESAS = (
    "SQ_CANDIDATO",
    "NR_CPF_CNPJ_FORNECEDOR",
    "NM_FORNECEDOR",
    "DT_DESPESA",
    "VR_DESPESA_CONTRATADA",
)
# Colunas exigidas de cada tabela: a falta de uma delas é mudança de layout (RNF12).
COLUNAS = {
    "candidatos": COLUNAS_CANDIDATOS,
    "receitas": COLUNAS_RECEITAS,
    "bens": COLUNAS_BENS,
    "despesas": COLUNAS_DESPESAS,
}
COLUNA_CPF = "NR_CPF_CANDIDATO"
COLUNA_TITULO = "NR_TITULO_ELEITORAL_CANDIDATO"
TAMANHO_TITULO = 12
CONTEXTO_TITULO = "titulo-eleitoral"
ORIGEM_SIMULADA = "arquivo sintético (fixture de teste ou de demonstração); não veio do TSE"


def classificar(nome: str) -> str | None:
    """``candidatos``, ``receitas``, ``despesas``, ``bens`` ou ``None`` (arquivo que nenhum
    conector do TSE lê). Cada conector lê só as suas tabelas (``Conector.tabelas``)."""
    base = Path(nome).name.lower()
    if not base.endswith(".csv"):
        return None
    if "consulta_cand" in base:
        return "candidatos"
    if "bem_candidato" in base:
        return "bens"
    # ``despesas_pagas_candidatos_*`` não traz o fornecedor e fica de fora.
    if "despesas_contratadas_candidatos" in base:
        return "despesas"
    # ``receitas_candidatos_doador_originario_*`` é outro layout e fica de fora.
    if "receitas_candidatos" in base and "doador_originario" not in base:
        return "receitas"
    return None


def _valor(linha: dict, coluna: str) -> str:
    texto = (linha.get(coluna) or "").strip()
    return "" if normalizar_nome(texto) in VALORES_AUSENTES or texto in VALORES_AUSENTES else texto


def converter_valor(texto: str) -> float | None:
    """``1.234,56`` ou ``1234.56`` -> número finito e não negativo; senão ``None``."""
    texto = (texto or "").strip()
    if not texto:
        return None
    if "," in texto:
        texto = texto.replace(".", "").replace(",", ".")
    valor = numero_finito(texto)
    return valor if valor is not None and valor >= 0 else None


def _ano(linha: dict, nome_arquivo: str, padrao: str) -> str:
    # O arquivo de candidatos traz ``ANO_ELEICAO``; o de receitas, ``AA_ELEICAO``.
    for nome_coluna in ("ANO_ELEICAO", "AA_ELEICAO"):
        coluna = somente_digitos(linha.get(nome_coluna))
        if len(coluna) == 4:
            return coluna
    achado = re.search(r"(?<!\d)(19|20)\d{2}(?!\d)", nome_arquivo)
    return achado.group(0) if achado else padrao


MAX_BYTES_LINHA = 1024 * 1024  # uma linha física do CSV (as reais têm menos de 2 KiB)


def _colunas(cabecalho: list[str]) -> dict[str, int]:
    return {(c or "").strip().upper(): i for i, c in enumerate(cabecalho) if c}


class Filtro:
    """Filtro de linhas comum aos dois modos de leitura: UF (``SG_UF``), município (``NM_UE``) e
    candidatos (``SQ_CANDIDATO``). Vazio aceita tudo."""

    def __init__(self, uf: str = "", municipio: str = "", candidatos: list[str] | None = None):
        self.uf = normalizar_nome(uf)
        self.municipio = normalizar_nome(municipio)
        self.candidatos = frozenset(somente_digitos(c) for c in candidatos or []) - {""}
        self._nomes: dict[str, str] = {}

    def __bool__(self) -> bool:
        return bool(self.uf or self.municipio or self.candidatos)

    def _nome(self, texto: str) -> str:
        # Poucos valores distintos (UFs e municípios): a normalização é feita uma vez por valor.
        normalizado = self._nomes.get(texto)
        if normalizado is None:
            if len(self._nomes) > 100_000:
                self._nomes.clear()
            normalizado = self._nomes[texto] = normalizar_nome(texto)
        return normalizado

    def aceita(self, uf: str, municipio: str, sq_candidato: str) -> bool:
        if self.uf and self._nome(uf) != self.uf:
            return False
        if self.municipio and self._nome(municipio) != self.municipio:
            return False
        return not self.candidatos or somente_digitos(sq_candidato) in self.candidatos

    def descricao(self) -> dict:
        return {
            "uf": self.uf,
            "municipio": self.municipio,
            "candidatos": len(self.candidatos),
        }


class _LinhasBrutas:
    """Entrega ao leitor de CSV as linhas decodificadas de um fluxo binário e guarda os bytes
    originais das linhas consumidas desde o último registro (um registro pode ocupar mais de uma
    linha física quando um campo entre aspas tem quebra de linha). Também calcula o SHA-256 e o
    tamanho do fluxo inteiro e aplica os tetos de bytes por linha e por arquivo."""

    def __init__(self, fluxo: BinaryIO, nome: str, max_bytes: int) -> None:
        self.fluxo = fluxo
        self.nome = nome
        self.max_bytes = max_bytes
        self.pendentes: list[bytes] = []
        self.resumo = hashlib.sha256()
        self.total = 0
        self.utf8 = True

    def __iter__(self):
        primeira = True
        while True:
            bruta = self.fluxo.readline(MAX_BYTES_LINHA + 1)
            if not bruta:
                return
            if len(bruta) > MAX_BYTES_LINHA:
                raise ErroConector(
                    f"O arquivo {self.nome} tem linha acima de {MAX_BYTES_LINHA} bytes: está "
                    "fora do layout do TSE."
                )
            self.total += len(bruta)
            if self.total > self.max_bytes:
                raise ErroConector(
                    f"O arquivo {self.nome} passa do limite de {self.max_bytes} bytes para "
                    "leitura em fluxo (TSE_MAX_BYTES_FLUXO)."
                )
            self.resumo.update(bruta)
            self.pendentes.append(bruta)
            dados = bruta
            if primeira:
                primeira = False
                if dados.startswith(b"\xef\xbb\xbf"):
                    dados = dados[3:]
            # Mesma regra de ``arquivos.decodificar``, aplicada por linha: UTF-8 estrito e, se
            # falhar, Latin-1 (uma linha só com ASCII é igual nas duas).
            try:
                yield dados.decode("utf-8")
            except UnicodeDecodeError:
                self.utf8 = False
                yield dados.decode("latin-1")

    def tomar(self) -> bytes:
        bruto = b"".join(self.pendentes)
        self.pendentes.clear()
        return bruto


class ConectorTseArquivo(Conector):
    nome = "tse_arquivo"
    # 1.0.1: o ano da eleição também é lido de ``AA_ELEICAO`` (arquivo de receitas).
    # 1.1.0: arquivo acima de ``TSE_MAX_BYTES_ARQUIVO`` é lido em fluxo e a captura é o recorte
    # filtrado; filtro por ``candidatos``; CNPJ alfanumérico do doador.
    # 1.2.0: lê o CPF do candidato reduzido aos seis dígitos centrais e o título eleitoral só
    # como resumo com chave (decisão de 23/11/2026); lê as despesas contratadas (H52).
    versao = "1.2.0"
    tabelas = frozenset({"candidatos", "receitas", "despesas"})
    modelo_parametros: type = ParametrosTseArquivo
    esperados = (
        "consulta_cand_*.csv, receitas_candidatos_*.csv, despesas_contratadas_candidatos_*.csv"
    )

    def __init__(
        self,
        dir_base: Path | str | None = None,
        max_bytes_arquivo: int = 64 * 1024 * 1024,
        max_registros: int = 200_000,
        simulado: bool = False,
        tentativas: int = 3,
        espera_retentativa_segundos: float = 1.0,
        dormir: Callable[[float], None] = time.sleep,
        max_bytes_fluxo: int = 2 * 1024 * 1024 * 1024,
        resumir_titulo: Callable[[str], str] | None = None,
    ) -> None:
        super().__init__(Retentativa(tentativas, espera_retentativa_segundos, dormir))
        # Função que troca o título eleitoral pelo resumo com chave (CHAVE_IDENTIFICADORES). Sem
        # ela o título é ignorado: nunca é guardado em claro.
        self.resumir_titulo = resumir_titulo
        self.dir_base = Path(dir_base) if dir_base is not None else None
        self.max_bytes_arquivo = max_bytes_arquivo
        self.max_registros = max_registros
        self.max_bytes_fluxo = max(max_bytes_fluxo, max_bytes_arquivo)
        self.simulado = simulado

    def coletar(self, parametros: dict, registrar_captura: RegistradorCaptura) -> ResultadoColeta:
        validos = validar(self.modelo_parametros, parametros)
        raiz, pasta = arquivos.resolver_pasta(self.dir_base, validos.diretorio, VARIAVEL_RAIZ)
        resultado = ResultadoColeta(fonte=self.nome)
        nomes, sintetico = arquivos.listar_arquivos(pasta, resultado.avisos)
        simulado = self.simulado or sintetico
        lidos = 0
        self._contagem = {
            "proprios": 0,
            "sem_documento": 0,
            "valor_invalido": 0,
            "titulos_sem_resumo": 0,
            "fornecedor_pf": 0,
        }
        filtro = Filtro(validos.uf, getattr(validos, "municipio", ""), validos.candidatos)
        for nome in nomes:
            zipado = nome.lower().endswith(".zip")
            if not zipado and self._tabela(nome) is None:
                continue
            origem = arquivos.origem_do_arquivo(
                self.nome, VARIAVEL_RAIZ, raiz, pasta, nome, simulado
            )
            if self._em_fluxo(pasta / nome, zipado):
                lidos += self._coletar_em_fluxo(
                    pasta / nome,
                    zipado,
                    origem,
                    simulado,
                    validos.ano,
                    filtro,
                    registrar_captura,
                    resultado,
                )
                continue
            conteudo = arquivos.ler_arquivo(
                pasta / nome, self.max_bytes_arquivo, "TSE_MAX_BYTES_ARQUIVO", self.retentativa
            )
            membros = self._membros_zip(conteudo, nome) if zipado else [(nome, conteudo)]
            if not membros:
                resultado.avisos.append(f"{nome}: nenhum CSV esperado no ZIP ({self.esperados}).")
                continue
            codificacoes = {}
            textos = []
            for membro, dados in membros:
                texto, codificacao = arquivos.decodificar(dados)
                codificacoes[membro] = codificacao
                textos.append((membro, texto))
            metadados = {
                "nome_arquivo": nome,
                "formato": "zip" if zipado else "csv",
                "tabelas": {membro: self._tabela(membro) for membro, _ in membros},
                "codificacao_detectada": codificacoes,
                "separador": SEPARADOR,
                "simulado": simulado,
            }
            if simulado:
                metadados["origem_simulada"] = ORIGEM_SIMULADA
            captura = registrar_captura(self.nome, origem, conteudo, metadados)
            resultado.capturas.append(captura)
            for membro, texto in textos:
                self._interpretar(
                    texto, self._tabela(membro), membro, captura.id, validos.ano, filtro, resultado
                )
            lidos += 1
        if not lidos:
            raise ErroConector(
                f"Nenhum arquivo do TSE no diretório informado (esperados {self.esperados} ou "
                "ZIP com esses CSV)."
            )
        if self._contagem["titulos_sem_resumo"]:
            resultado.avisos.append(
                f"{self._contagem['titulos_sem_resumo']} candidato(s) com título eleitoral no "
                "arquivo, mas o conector está sem a chave do resumo: o título não foi guardado e "
                "as candidaturas da mesma pessoa em eleições diferentes não serão ligadas."
            )
        if self._contagem["fornecedor_pf"]:
            resultado.avisos.append(
                f"{self._contagem['fornecedor_pf']} despesa(s) com fornecedor pessoa física ou "
                "sem CNPJ legível foram ignoradas: só o fornecedor pessoa jurídica é lido (RNF04)."
            )
        if self._contagem["proprios"]:
            resultado.avisos.append(
                f"{self._contagem['proprios']} receita(s) de 'Recursos próprios' ignorada(s): "
                "nelas o doador é o próprio candidato e a linha exporia o CPF dele (RNF04)."
            )
        if self._contagem["sem_documento"]:
            resultado.avisos.append(
                f"{self._contagem['sem_documento']} receita(s) sem CPF ou CNPJ do doador "
                "legível foram ignoradas."
            )
        if self._contagem["valor_invalido"]:
            resultado.avisos.append(
                f"{self._contagem['valor_invalido']} receita(s) com VR_RECEITA inválido: o valor "
                "foi desconsiderado."
            )
        return resultado

    # ---- Leitura em fluxo (arquivo acima do teto) ---------------------------------------------

    def _em_fluxo(self, caminho: Path, zipado: bool) -> bool:
        """O arquivo passa do teto de leitura integral? Num ZIP, basta um CSV reconhecido passar
        (o ZIP de uma UF grande tem poucos megabytes compactado e centenas descompactado)."""

        def medir(_ultima: bool) -> bool:
            arquivo, tamanho = arquivos.abrir_arquivo(caminho)
            with arquivo:
                if tamanho > self.max_bytes_arquivo:
                    return True
                if not zipado:
                    return False
                try:
                    with zipfile.ZipFile(arquivo) as pacote:
                        return any(
                            info.file_size > self.max_bytes_arquivo
                            for info in pacote.infolist()
                            if not info.is_dir() and self._tabela(info.filename) is not None
                        )
                except (zipfile.BadZipFile, OSError, RuntimeError, NotImplementedError):
                    return False  # o caminho de leitura integral dá a mensagem de erro

        return self.retentativa.executar(
            medir, f"Não foi possível abrir ou ler o arquivo {caminho.name}"
        )

    def _coletar_em_fluxo(
        self,
        caminho: Path,
        zipado: bool,
        origem: str,
        simulado: bool,
        ano_padrao: str,
        filtro: Filtro,
        registrar_captura: RegistradorCaptura,
        resultado: ResultadoColeta,
    ) -> int:
        """Lê o CSV (ou cada CSV do ZIP) em fluxo e registra como captura só o RECORTE: o
        cabeçalho e as linhas que passam no filtro, com os bytes originais. Devolve quantos CSV
        foram lidos."""
        nome = caminho.name
        if not filtro:
            raise ErroConector(
                f"O arquivo {nome} passa do limite de {self.max_bytes_arquivo} bytes para "
                "leitura integral (TSE_MAX_BYTES_ARQUIVO). Informe ao menos um filtro ('uf', "
                "'municipio', 'candidatos' ou 'do_caso'): o arquivo é então lido em fluxo e só "
                "as linhas do filtro são guardadas."
            )

        def ler(_ultima: bool) -> list[tuple[str, bytes, dict]]:
            arquivo, tamanho = arquivos.abrir_arquivo(caminho)
            with arquivo:
                if tamanho > self.max_bytes_fluxo:
                    raise ErroConector(
                        f"O arquivo {nome} tem {tamanho} bytes e passa do limite de "
                        f"{self.max_bytes_fluxo} bytes para leitura em fluxo "
                        "(TSE_MAX_BYTES_FLUXO)."
                    )
                try:
                    if not zipado:
                        return [(nome, *self._recortar(arquivo, nome, filtro, 0))]
                    recortes: list[tuple[str, bytes, dict]] = []
                    guardado = 0
                    with zipfile.ZipFile(arquivo) as pacote:
                        for info in sorted(pacote.infolist(), key=lambda i: i.filename):
                            if info.is_dir() or self._tabela(info.filename) is None:
                                continue
                            membro = Path(info.filename).name
                            if info.file_size > self.max_bytes_fluxo:
                                raise ErroConector(
                                    f"{membro}, dentro de {nome}, passa do limite de "
                                    f"{self.max_bytes_fluxo} bytes (TSE_MAX_BYTES_FLUXO)."
                                )
                            with pacote.open(info) as fluxo:
                                recorte, dados = self._recortar(fluxo, membro, filtro, guardado)
                            guardado += len(recorte)
                            recortes.append((membro, recorte, dados))
                    return recortes
                except (zipfile.BadZipFile, RuntimeError, NotImplementedError) as erro:
                    raise ErroConector(f"O arquivo {nome} não é um ZIP legível.") from erro
                except OSError as erro:
                    raise FalhaTransitoria(type(erro).__name__) from erro

        recortes = self.retentativa.executar(ler, f"Não foi possível ler o arquivo {nome}")
        if not recortes:
            resultado.avisos.append(f"{nome}: nenhum CSV esperado no ZIP ({self.esperados}).")
            return 0
        for membro, recorte, dados in recortes:
            texto, codificacao = arquivos.decodificar(recorte)
            metadados = {
                "nome_arquivo": nome,
                "formato": "csv",
                "tabelas": {membro: self._tabela(membro)},
                "codificacao_detectada": {membro: codificacao},
                "separador": SEPARADOR,
                "simulado": simulado,
                # A captura NÃO é o arquivo inteiro: é o recorte lido em fluxo. O SHA-256 da
                # captura é o do recorte; o do CSV de origem inteiro fica aqui, para conferência.
                "recorte": {
                    "modo": "fluxo",
                    "arquivo_origem": nome,
                    "membro_do_zip": membro if zipado else None,
                    "filtro": filtro.descricao(),
                    **dados,
                },
            }
            if simulado:
                metadados["origem_simulada"] = ORIGEM_SIMULADA
            captura = registrar_captura(
                self.nome,
                f"{origem}{'!' + membro if zipado else ''}#recorte",
                recorte,
                metadados,
            )
            resultado.capturas.append(captura)
            resultado.avisos.append(
                f"{membro}: arquivo de {dados['origem_bytes']} bytes lido em fluxo; a captura "
                f"guarda só o recorte do filtro ({dados['linhas_mantidas']} de "
                f"{dados['linhas_lidas']} linhas). O SHA-256 do arquivo inteiro está nos "
                "metadados da captura."
            )
            self._interpretar(
                texto, self._tabela(membro), membro, captura.id, ano_padrao, filtro, resultado
            )
        return len(recortes)

    def _recortar(
        self, fluxo: BinaryIO, nome: str, filtro: Filtro, ja_guardado: int
    ) -> tuple[bytes, dict]:
        """Percorre o CSV uma vez, sem carregá-lo na memória, e devolve o recorte (cabeçalho +
        linhas do filtro, bytes originais) e os dados do arquivo inteiro (tamanho, SHA-256 e
        contagem de linhas). O recorte de uma coleta tem o teto ``TSE_MAX_BYTES_ARQUIVO``."""
        linhas = _LinhasBrutas(fluxo, nome, self.max_bytes_fluxo)
        leitor = csv.reader(linhas, delimiter=SEPARADOR, quotechar='"')
        recorte = bytearray()
        lidas = mantidas = 0
        try:
            cabecalho = next(leitor, None)
            if cabecalho is None:
                raise ErroConector(f"O arquivo {nome} está vazio.")
            recorte += linhas.tomar()
            colunas = _colunas(cabecalho)
            exigidas = COLUNAS[self._tabela(nome) or "receitas"]
            faltando = [c for c in exigidas if c not in colunas]
            if faltando:
                raise ErroConector(
                    f"O arquivo {nome} não tem as colunas esperadas do layout do TSE "
                    f"({', '.join(faltando)}): o layout pode ter mudado."
                )
            i_uf, i_ue, i_sq = (colunas.get(c, -1) for c in ("SG_UF", "NM_UE", "SQ_CANDIDATO"))

            def campo(linha: list[str], indice: int) -> str:
                return linha[indice] if 0 <= indice < len(linha) else ""

            for linha in leitor:
                bruta = linhas.tomar()
                lidas += 1
                if not filtro.aceita(campo(linha, i_uf), campo(linha, i_ue), campo(linha, i_sq)):
                    continue
                if ja_guardado + len(recorte) + len(bruta) > self.max_bytes_arquivo:
                    raise ErroConector(
                        f"O recorte de {nome} passa do limite de {self.max_bytes_arquivo} bytes "
                        "(TSE_MAX_BYTES_ARQUIVO): o filtro ainda deixa passar linhas demais. "
                        "Restrinja por 'municipio' ou por 'candidatos'."
                    )
                recorte += bruta
                mantidas += 1
        except csv.Error as erro:
            raise ErroConector(
                f"O arquivo {nome} está fora do layout do TSE (linha {leitor.line_num})."
            ) from erro
        return bytes(recorte), {
            "origem_bytes": linhas.total,
            "origem_sha256": linhas.resumo.hexdigest(),
            "origem_codificacao": "utf-8" if linhas.utf8 else "latin-1",
            "linhas_lidas": lidas,
            "linhas_mantidas": mantidas,
        }

    def _membros_zip(self, conteudo: bytes, nome: str) -> list[tuple[str, bytes]]:
        """CSV reconhecidos dentro do ZIP, cada um com o mesmo teto de bytes (descompactado)."""
        try:
            with zipfile.ZipFile(io.BytesIO(conteudo)) as pacote:
                membros = []
                for info in sorted(pacote.infolist(), key=lambda i: i.filename):
                    if info.is_dir() or self._tabela(info.filename) is None:
                        continue
                    if info.file_size > self.max_bytes_arquivo:
                        raise ErroConector(
                            f"{Path(info.filename).name}, dentro de {nome}, passa do limite de "
                            f"{self.max_bytes_arquivo} bytes (TSE_MAX_BYTES_ARQUIVO)."
                        )
                    with pacote.open(info) as arquivo:
                        dados = arquivo.read(self.max_bytes_arquivo + 1)
                    if len(dados) > self.max_bytes_arquivo:
                        raise ErroConector(
                            f"{Path(info.filename).name}, dentro de {nome}, passa do limite de "
                            f"{self.max_bytes_arquivo} bytes (TSE_MAX_BYTES_ARQUIVO)."
                        )
                    membros.append((Path(info.filename).name, dados))
                return membros
        except (zipfile.BadZipFile, OSError, RuntimeError, NotImplementedError) as erro:
            raise ErroConector(f"O arquivo {nome} não é um ZIP legível.") from erro

    def _tabela(self, nome: str) -> str | None:
        """A tabela do arquivo, se for uma das que este conector lê."""
        tabela = classificar(nome)
        return tabela if tabela in self.tabelas else None

    def _linhas(
        self, texto: str, tabela: str | None, nome: str, filtro: Filtro, resultado: ResultadoColeta
    ):
        """As linhas do CSV que passam no filtro, como dicionário de coluna em maiúsculas. Confere
        as colunas exigidas da tabela (RNF12) e o teto de registros da coleta."""
        leitor = csv.DictReader(io.StringIO(texto, newline=""), delimiter=SEPARADOR, quotechar='"')
        colunas = {c.strip().upper() for c in (leitor.fieldnames or []) if c}
        faltando = [c for c in COLUNAS[tabela or "receitas"] if c not in colunas]
        if faltando:
            # Detecção de mudança de formato (RNF12).
            raise ErroConector(
                f"O arquivo {nome} não tem as colunas esperadas do layout do TSE "
                f"({', '.join(faltando)}): o layout pode ter mudado."
            )
        try:
            for bruta in leitor:
                linha = {(k or "").strip().upper(): (v or "") for k, v in bruta.items() if k}
                if not filtro.aceita(
                    linha.get("SG_UF", ""), linha.get("NM_UE", ""), linha.get("SQ_CANDIDATO", "")
                ):
                    continue
                if len(resultado.registros) >= self.max_registros:
                    raise ErroConector(
                        f"A coleta passa do limite de {self.max_registros} registros "
                        f"(TSE_MAX_REGISTROS), atingido em {nome}. Use os filtros 'uf' e "
                        "'municipio' ou extraia só o recorte necessário."
                    )
                if somente_digitos(_valor(linha, "SQ_CANDIDATO")):
                    yield linha
        except csv.Error as erro:
            raise ErroConector(
                f"O arquivo {nome} está fora do layout do TSE (linha {leitor.line_num})."
            ) from erro

    def _cpf_parcial(self, linha: dict) -> str:
        """Os seis dígitos centrais do CPF do candidato (``***NNNNNN**``) ou vazio. O valor lido
        não sai desta função: só 11 dígitos viram a forma descaracterizada."""
        digitos = somente_digitos(_valor(linha, COLUNA_CPF))
        return mascarar_cpf(digitos) if len(digitos) == 11 else ""

    def _titulo_resumo(self, linha: dict) -> str:
        """O resumo com chave do título eleitoral, ou vazio. O título em claro não sai daqui."""
        digitos = somente_digitos(_valor(linha, COLUNA_TITULO))
        if len(digitos) != TAMANHO_TITULO or len(set(digitos)) == 1:
            return ""
        if self.resumir_titulo is None:
            self._contagem["titulos_sem_resumo"] += 1
            return ""
        return self.resumir_titulo(digitos)

    def _interpretar(
        self,
        texto: str,
        tabela: str | None,
        nome: str,
        captura_id: str,
        ano_padrao: str,
        filtro: Filtro,
        resultado: ResultadoColeta,
    ) -> None:
        if tabela == "bens":
            return self._interpretar_bens(texto, nome, captura_id, ano_padrao, filtro, resultado)
        if tabela == "despesas":
            return self._interpretar_despesas(
                texto, nome, captura_id, ano_padrao, filtro, resultado
            )
        vistos: dict[str, int] = {}
        for linha in self._linhas(texto, tabela, nome, filtro, resultado):
            sq = somente_digitos(_valor(linha, "SQ_CANDIDATO"))
            ano = _ano(linha, nome, ano_padrao)
            candidato = RegistroCandidato(
                captura_id=captura_id,
                ano=ano,
                sq_candidato=sq,
                nome=_valor(linha, "NM_CANDIDATO"),
                nome_urna=_valor(linha, "NM_URNA_CANDIDATO"),
                cargo=_valor(linha, "DS_CARGO"),
                partido=_valor(linha, "SG_PARTIDO"),
                uf=_valor(linha, "SG_UF"),
                sg_ue=_valor(linha, "SG_UE"),
                nm_ue=_valor(linha, "NM_UE"),
                situacao_turno=_valor(linha, "DS_SIT_TOT_TURNO"),
                origem="consulta_cand" if tabela == "candidatos" else "receitas",
            )
            if tabela == "candidatos":
                # CPF e título só são lidos do arquivo de candidatos, e só reduzidos.
                candidato.cpf_parcial = self._cpf_parcial(linha)
                candidato.titulo_resumo = self._titulo_resumo(linha)
                resultado.registros.append(candidato)
                continue
            if normalizar_nome(linha.get("DS_ORIGEM_RECEITA")) == ORIGEM_RECURSOS_PROPRIOS:
                self._contagem["proprios"] += 1
                continue
            # CPF (11 dígitos) ou CNPJ (14 posições; pode ser alfanumérico).
            documento = somente_alfanumericos(_valor(linha, "NR_CPF_CNPJ_DOADOR"))
            if not (
                (len(documento) == 11 and documento.isdigit()) or CNPJ_COMPLETO.fullmatch(documento)
            ):
                self._contagem["sem_documento"] += 1
                continue
            valor = converter_valor(_valor(linha, "VR_RECEITA"))
            if valor is None and _valor(linha, "VR_RECEITA"):
                self._contagem["valor_invalido"] += 1
            data = data_para_iso(_valor(linha, "DT_RECEITA"))
            # Identidade da linha sem expor o documento: resumo dos campos + ocorrência.
            base = "|".join(
                [ano, sq, documento, _valor(linha, "VR_RECEITA"), data, _valor(linha, "NM_DOADOR")]
            )
            resumo = hashlib.sha256(base.encode("utf-8", "surrogatepass")).hexdigest()[:24]
            vistos[resumo] = vistos.get(resumo, 0) + 1
            resultado.registros.append(candidato)
            resultado.registros.append(
                RegistroDoacao(
                    captura_id=captura_id,
                    ano=ano,
                    sq_candidato=sq,
                    nome_candidato=candidato.nome,
                    documento_doador=documento,
                    nome_doador=_valor(linha, "NM_DOADOR"),
                    nome_doador_rfb=_valor(linha, "NM_DOADOR_RFB"),
                    valor=valor,
                    data=data,
                    origem_receita=_valor(linha, "DS_ORIGEM_RECEITA"),
                    chave=f"{resumo}#{vistos[resumo]}",
                )
            )

    def _interpretar_bens(
        self,
        texto: str,
        nome: str,
        captura_id: str,
        ano_padrao: str,
        filtro: Filtro,
        resultado: ResultadoColeta,
    ) -> None:
        for linha in self._linhas(texto, "bens", nome, filtro, resultado):
            ordem = somente_digitos(_valor(linha, "NR_ORDEM_BEM_CANDIDATO"))
            if not ordem:
                continue
            resultado.registros.append(
                RegistroBem(
                    captura_id=captura_id,
                    ano=_ano(linha, nome, ano_padrao),
                    sq_candidato=somente_digitos(_valor(linha, "SQ_CANDIDATO")),
                    ordem=ordem,
                    tipo=_valor(linha, "DS_TIPO_BEM_CANDIDATO"),
                    codigo_tipo=_valor(linha, "CD_TIPO_BEM_CANDIDATO"),
                    descricao=_valor(linha, "DS_BEM_CANDIDATO"),
                    valor=converter_valor(_valor(linha, "VR_BEM_CANDIDATO")),
                    atualizado_em=data_para_iso(_valor(linha, "DT_ULT_ATUAL_BEM_CANDIDATO")),
                )
            )

    def _interpretar_despesas(
        self,
        texto: str,
        nome: str,
        captura_id: str,
        ano_padrao: str,
        filtro: Filtro,
        resultado: ResultadoColeta,
    ) -> None:
        vistos: dict[str, int] = {}
        for linha in self._linhas(texto, "despesas", nome, filtro, resultado):
            # Só o fornecedor pessoa jurídica: ``NR_CPF_CANDIDATO`` e ``NR_CPF_VICE_CANDIDATO``
            # deste arquivo nunca são lidas, e o CPF de fornecedor pessoa física é descartado.
            documento = somente_alfanumericos(_valor(linha, "NR_CPF_CNPJ_FORNECEDOR"))
            if not CNPJ_COMPLETO.fullmatch(documento):
                self._contagem["fornecedor_pf"] += 1
                continue
            sq = somente_digitos(_valor(linha, "SQ_CANDIDATO"))
            ano = _ano(linha, nome, ano_padrao)
            valor = converter_valor(_valor(linha, "VR_DESPESA_CONTRATADA"))
            data = data_para_iso(_valor(linha, "DT_DESPESA"))
            chave = somente_digitos(_valor(linha, "SQ_DESPESA"))
            if not chave:
                base = "|".join([ano, sq, documento, _valor(linha, "VR_DESPESA_CONTRATADA"), data])
                resumo = hashlib.sha256(base.encode("utf-8", "surrogatepass")).hexdigest()[:24]
                vistos[resumo] = vistos.get(resumo, 0) + 1
                chave = f"{resumo}#{vistos[resumo]}"
            resultado.registros.append(
                RegistroDespesa(
                    captura_id=captura_id,
                    ano=ano,
                    sq_candidato=sq,
                    nome_candidato=_valor(linha, "NM_CANDIDATO"),
                    cnpj_fornecedor=documento,
                    nome_fornecedor=_valor(linha, "NM_FORNECEDOR_RFB")
                    or _valor(linha, "NM_FORNECEDOR"),
                    cnae=somente_digitos(_valor(linha, "CD_CNAE_FORNECEDOR")),
                    cnae_descricao=_valor(linha, "DS_CNAE_FORNECEDOR"),
                    valor=valor,
                    data=data,
                    descricao=_valor(linha, "DS_DESPESA"),
                    chave=chave,
                )
            )


class ConectorTseBens(ConectorTseArquivo):
    """Bens declarados ao TSE pelos candidatos (H50): mesmo molde do ``tse_arquivo`` (pasta
    dentro de ``TSE_DIR_BASE``, recorte por UF e por candidatos, leitura em fluxo acima do
    teto), lendo só os arquivos ``bem_candidato_*.csv``."""

    nome = "tse_bens"
    versao = "1.0.0"
    tabelas = frozenset({"bens"})
    modelo_parametros = ParametrosTseBens
    esperados = "bem_candidato_*.csv"
