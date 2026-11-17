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
- ``NR_CPF_CANDIDATO`` não é divulgado a partir de 2024 (vem "-4"): o vínculo entre candidato e
  doações é feito por ``SQ_CANDIDATO``. Este conector NUNCA lê ``NR_CPF_CANDIDATO``;
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
  ao contrário de 2024; no arquivo de receitas veio ``-4``. O conector continua sem lê-la, mas
  o arquivo bruto guardado na custódia a contém;
- o CSV de uma UF grande passa do teto padrão ``TSE_MAX_BYTES_ARQUIVO`` (64 MiB): ver "Arquivo
  acima do teto", abaixo.

Arquivo acima do teto (decisão do autor, 17/11/2026). Até ``TSE_MAX_BYTES_ARQUIVO`` o arquivo é
lido inteiro e a captura guarda os bytes do arquivo (como sempre). Acima do teto (ou ZIP com um
CSV acima dele), o conector lê EM FLUXO, linha a linha, sem carregar o arquivo na memória, e
exige ao menos um filtro: ``uf`` (``SG_UF``), ``municipio`` (``NM_UE``) ou ``candidatos``
(``SQ_CANDIDATO``; ``do_caso`` usa os candidatos do caso). A captura passa a ser o RECORTE: o
cabeçalho e as linhas que passam no filtro, com os bytes originais dessas linhas (inclusive a
coluna ``NR_CPF_CANDIDATO``, que o conector não lê). O SHA-256 da captura é o do recorte; o
tamanho e o SHA-256 do CSV inteiro, o filtro e a contagem de linhas ficam em
``metadados.recorte``, e a origem leva o sufixo ``#recorte``. Limites que continuam valendo: o
recorte até ``TSE_MAX_BYTES_ARQUIVO``; o arquivo percorrido até ``TSE_MAX_BYTES_FLUXO`` (2 GiB);
cada linha física até 1 MiB; os registros até ``TSE_MAX_REGISTROS``. A codificação é decidida
linha a linha (UTF-8 estrito e, se falhar, Latin-1), o que só difere da detecção sobre o arquivo
inteiro num arquivo Latin-1 com uma linha que seja, por coincidência, UTF-8 válido com acento.
Medição em ``scripts/medir_tse_fluxo.py`` (resultado no README).

``NR_CPF_CANDIDATO``: o conector nunca lê essa coluna (``_interpretar`` só consulta as colunas
do layout listadas acima), e por isso o CPF do candidato não chega a registros, entidades,
alertas, relatório nem exportações. Ele existe apenas na carga bruta da custódia; a rota de
texto da captura o devolve descaracterizado (``buscador.privacidade``).

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
    RegistroCandidato,
    RegistroDoacao,
    ResultadoColeta,
    Retentativa,
)
from buscador.conectores.parametros import ParametrosTseArquivo, validar
from buscador.conectores.portal_transparencia import data_para_iso
from buscador.resolucao.normalizacao import (
    CNPJ_COMPLETO,
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
ORIGEM_SIMULADA = "arquivo sintético (fixture de teste ou de demonstração); não veio do TSE"


def classificar(nome: str) -> str | None:
    """``candidatos``, ``receitas`` ou ``None`` (arquivo que o conector não lê)."""
    base = Path(nome).name.lower()
    if not base.endswith(".csv"):
        return None
    if "consulta_cand" in base:
        return "candidatos"
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
    versao = "1.1.0"

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
    ) -> None:
        super().__init__(Retentativa(tentativas, espera_retentativa_segundos, dormir))
        self.dir_base = Path(dir_base) if dir_base is not None else None
        self.max_bytes_arquivo = max_bytes_arquivo
        self.max_registros = max_registros
        self.max_bytes_fluxo = max(max_bytes_fluxo, max_bytes_arquivo)
        self.simulado = simulado

    def coletar(self, parametros: dict, registrar_captura: RegistradorCaptura) -> ResultadoColeta:
        validos = validar(ParametrosTseArquivo, parametros)
        raiz, pasta = arquivos.resolver_pasta(self.dir_base, validos.diretorio, VARIAVEL_RAIZ)
        resultado = ResultadoColeta(fonte=self.nome)
        nomes, sintetico = arquivos.listar_arquivos(pasta, resultado.avisos)
        simulado = self.simulado or sintetico
        lidos = 0
        self._contagem = {"proprios": 0, "sem_documento": 0, "valor_invalido": 0}
        filtro = Filtro(validos.uf, validos.municipio, validos.candidatos)
        for nome in nomes:
            zipado = nome.lower().endswith(".zip")
            if not zipado and classificar(nome) is None:
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
                resultado.avisos.append(f"{nome}: nenhum CSV de candidatos ou de receitas no ZIP.")
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
                "tabelas": {membro: classificar(membro) for membro, _ in membros},
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
                    texto, classificar(membro), membro, captura.id, validos.ano, filtro, resultado
                )
            lidos += 1
        if not lidos:
            raise ErroConector(
                "Nenhum arquivo do TSE no diretório informado (esperados consulta_cand_*.csv, "
                "receitas_candidatos_*.csv ou ZIP com esses CSV)."
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
                            if not info.is_dir() and classificar(info.filename) is not None
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
                            if info.is_dir() or classificar(info.filename) is None:
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
            resultado.avisos.append(f"{nome}: nenhum CSV de candidatos ou de receitas no ZIP.")
            return 0
        for membro, recorte, dados in recortes:
            texto, codificacao = arquivos.decodificar(recorte)
            metadados = {
                "nome_arquivo": nome,
                "formato": "csv",
                "tabelas": {membro: classificar(membro)},
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
                texto, classificar(membro), membro, captura.id, ano_padrao, filtro, resultado
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
            exigidas = COLUNAS_CANDIDATOS if classificar(nome) == "candidatos" else COLUNAS_RECEITAS
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
                    if info.is_dir() or classificar(info.filename) is None:
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
        leitor = csv.DictReader(io.StringIO(texto, newline=""), delimiter=SEPARADOR, quotechar='"')
        colunas = {c.strip().upper() for c in (leitor.fieldnames or []) if c}
        exigidas = COLUNAS_CANDIDATOS if tabela == "candidatos" else COLUNAS_RECEITAS
        faltando = [c for c in exigidas if c not in colunas]
        if faltando:
            # Detecção de mudança de formato (RNF12).
            raise ErroConector(
                f"O arquivo {nome} não tem as colunas esperadas do layout do TSE "
                f"({', '.join(faltando)}): o layout pode ter mudado."
            )
        vistos: dict[str, int] = {}
        try:
            for bruta in leitor:
                # Só as colunas do layout são lidas; ``NR_CPF_CANDIDATO`` nunca é consultada.
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
                sq = somente_digitos(_valor(linha, "SQ_CANDIDATO"))
                if not sq:
                    continue
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
                    resultado.registros.append(candidato)
                    continue
                if normalizar_nome(linha.get("DS_ORIGEM_RECEITA")) == ORIGEM_RECURSOS_PROPRIOS:
                    self._contagem["proprios"] += 1
                    continue
                # CPF (11 dígitos) ou CNPJ (14 posições; pode ser alfanumérico).
                documento = somente_alfanumericos(_valor(linha, "NR_CPF_CNPJ_DOADOR"))
                if not (
                    (len(documento) == 11 and documento.isdigit())
                    or CNPJ_COMPLETO.fullmatch(documento)
                ):
                    self._contagem["sem_documento"] += 1
                    continue
                valor = converter_valor(_valor(linha, "VR_RECEITA"))
                if valor is None and _valor(linha, "VR_RECEITA"):
                    self._contagem["valor_invalido"] += 1
                data = data_para_iso(_valor(linha, "DT_RECEITA"))
                # Identidade da linha sem expor o documento: resumo dos campos + ocorrência.
                base = "|".join(
                    [
                        ano,
                        sq,
                        documento,
                        _valor(linha, "VR_RECEITA"),
                        data,
                        _valor(linha, "NM_DOADOR"),
                    ]
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
        except csv.Error as erro:
            raise ErroConector(
                f"O arquivo {nome} está fora do layout do TSE (linha {leitor.line_num})."
            ) from erro
