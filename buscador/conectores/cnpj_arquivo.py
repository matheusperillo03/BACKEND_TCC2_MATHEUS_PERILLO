"""Conector dos dados abertos do CNPJ (Receita Federal), lidos de arquivos locais no layout oficial.

Layout (metadados do CNPJ, Receita Federal): arquivos separados por ``;``, codificação latin-1, sem
cabeçalho, campos entre aspas. Tabelas usadas: Empresas (7 campos), Estabelecimentos (30 campos),
Sócios (11 campos) e Simples (7 campos: opção pelo Simples e pelo MEI, com as datas; usada pela
tipologia L6). O CPF do sócio já vem descaracterizado (``***NNNNNN**``). O MVP não baixa o
dump completo: lê o que houver na pasta informada.

Conferido com arquivos reais em 17/11/2026 (mês 2026-11; um arquivo de cada tabela, lido em
fluxo; os dados reais ficam fora do git, em ``dados-reais/``):

- endereço oficial: o repositório "Arquivos da Receita Federal"
  (https://arquivos.receitafederal.gov.br/, pasta ``Dados/Cadastros/CNPJ/<AAAA-MM>/``), para onde
  o conjunto do dados.gov.br aponta; o antigo ``dadosabertos.rfb.gov.br`` não respondeu;
- o mês tem 37 ZIPs, 7,87 GB compactados: 10 ``Empresas`` (1,40 GB), 10 ``Estabelecimentos``
  (5,46 GB), 10 ``Socios`` (0,70 GB), ``Simples`` (0,31 GB) e as tabelas de domínio. Descompactada,
  a base passa de 26 GB (taxa medida: 3,2 a 4,2 vezes);
- o layout confere: 7, 30 e 11 campos em todas as linhas (4,5 milhões de empresas, 4,8 milhões
  de estabelecimentos e 2,0 milhões de sócios lidos), Latin-1, ``;``, campos entre aspas, CNPJ
  básico numérico de 8 dígitos, datas ``AAAAMMDD``, capital com vírgula decimal;
- o CPF do sócio e o do representante vêm descaracterizados (``***NNNNNN**``);
- o sócio PESSOA JURÍDICA veio com o CNPJ BÁSICO (8 dígitos), e não com o CNPJ de 14 dígitos; a
  resolução de entidades aceita os dois formatos;
- ``municipio`` é o código da tabela ``Municipios`` (4 dígitos), não o nome;
- os arquivos de mesmo número NÃO cobrem a mesma faixa de CNPJ (``Empresas1`` ia de 00000000 a
  04631960; ``Socios3`` e ``Estabelecimentos2`` cobriam a faixa inteira): os dados de uma
  empresa podem estar em qualquer um dos dez arquivos de cada tabela.

Base completa: este conector NÃO serve para carregá-la. Ele copia cada arquivo para a custódia e
transforma toda linha em registro, com teto de bytes por arquivo (``CNPJ_MAX_BYTES_ARQUIVO``,
16 MiB) e de registros (``CNPJ_MAX_REGISTROS``, 100 mil): não há filtro por CNPJ. O caminho leve
é recortar antes: ``scripts/recortar_cnpj.py`` percorre os ZIPs em fluxo, sem descompactar em
disco, e grava só as linhas dos CNPJs do caso, que este conector então lê.

Segurança da leitura:
- a pasta fica confinada à raiz de ingestão configurada pelo operador (``CNPJ_DIR_BASE``); links
  simbólicos não são seguidos e o caminho do servidor não volta nas mensagens nem na custódia;
- cada arquivo é lido em blocos, com limite de bytes, e interpretado linha a linha, com limite de
  registros por coleta;
- uma pasta com o arquivo-marcador ``SINTETICO`` (ou o conector em modo simulado) gera capturas
  marcadas como sintéticas (``simulado://`` e ``metadados.simulado``).
"""

from __future__ import annotations

import csv
import io
import os
import stat
import time
from collections.abc import Callable
from pathlib import Path

from buscador.conectores.base import (
    Conector,
    ErroConector,
    FalhaTransitoria,
    RegistradorCaptura,
    Registro,
    RegistroEmpresa,
    RegistroEstabelecimento,
    RegistroSimples,
    RegistroSocio,
    ResultadoColeta,
    Retentativa,
)
from buscador.conectores.parametros import ParametrosCnpjArquivo, validar
from buscador.custodia import PREFIXO_SIMULADO
from buscador.seguranca import numero_finito

CODIFICACAO = "latin-1"
SEPARADOR = ";"
MARCADOR_SINTETICO = "SINTETICO"
ROTULO_RAIZ = "CNPJ_DIR_BASE"

# Sufixos dos arquivos oficiais (ex.: K3241.K03200Y0.D51111.EMPRECSV) e nomes alternativos aceitos.
PADROES = {
    "empresas": ("EMPRECSV", "EMPRESAS"),
    "estabelecimentos": ("ESTABELE", "ESTABELECIMENTOS"),
    "socios": ("SOCIOCSV", "SOCIOS"),
    # Nome oficial conferido na listagem de 11/2026: ``Simples.zip`` (o arquivo de dentro traz
    # ``SIMPLES.CSV`` no nome). A leitura do arquivo real de 0,31 GB NÃO foi testada: como nas
    # outras tabelas, o caminho é recortar antes (``scripts/recortar_cnpj.py``).
    "simples": ("SIMPLES",),
}

CAMPOS_EMPRESAS = 7
CAMPOS_ESTABELECIMENTOS = 30
CAMPOS_SOCIOS = 11
CAMPOS_SIMPLES = 7

# Nome de cada posição do layout, usado na comparação de capturas campo a campo (RF07, H14).
NOMES_CAMPOS = {
    "empresas": (
        "cnpj_basico",
        "razao_social",
        "natureza_juridica",
        "qualificacao_responsavel",
        "capital_social",
        "porte",
        "ente_federativo",
    ),
    "estabelecimentos": (
        "cnpj_basico",
        "cnpj_ordem",
        "cnpj_dv",
        "matriz_filial",
        "nome_fantasia",
        "situacao_cadastral",
        "data_situacao_cadastral",
        "motivo_situacao_cadastral",
        "nome_cidade_exterior",
        "pais",
        "data_inicio_atividade",
        "cnae_fiscal_principal",
        "cnae_fiscal_secundaria",
        "tipo_logradouro",
        "logradouro",
        "numero",
        "complemento",
        "bairro",
        "cep",
        "uf",
        "municipio",
        "ddd_1",
        "telefone_1",
        "ddd_2",
        "telefone_2",
        "ddd_fax",
        "fax",
        "correio_eletronico",
        "situacao_especial",
        "data_situacao_especial",
    ),
    "socios": (
        "cnpj_basico",
        "identificador_socio",
        "nome_socio",
        "cnpj_cpf_socio",
        "qualificacao_socio",
        "data_entrada_sociedade",
        "pais",
        "representante_legal",
        "nome_representante",
        "qualificacao_representante",
        "faixa_etaria",
    ),
    # Metadados do CNPJ, "Dados do Simples" (conferido em 17/11/2026): CNPJ básico; opção pelo
    # Simples (S, N ou em branco); data de opção e de exclusão do Simples; opção pelo MEI; data
    # de opção e de exclusão do MEI.
    "simples": (
        "cnpj_basico",
        "opcao_simples",
        "data_opcao_simples",
        "data_exclusao_simples",
        "opcao_mei",
        "data_opcao_mei",
        "data_exclusao_mei",
    ),
}
# Posições que identificam o registro em cada tabela (chave da comparação).
POSICOES_CHAVE = {
    "empresas": (0,),
    "estabelecimentos": (0, 1, 2),
    "socios": (0, 3, 2),
    "simples": (0,),
}

_ERRO_DIRETORIO = (
    "Diretório não encontrado dentro da raiz de ingestão do CNPJ (CNPJ_DIR_BASE). Informe um "
    "caminho relativo a essa raiz."
)


def classificar_arquivo(caminho: Path | str) -> str | None:
    nome = Path(caminho).name.upper()
    for tabela, marcadores in PADROES.items():
        if any(m in nome for m in marcadores):
            return tabela
    return None


def converter_capital(texto: str) -> float | None:
    """Capital social vem como '1000,00' (vírgula decimal) nos arquivos oficiais.

    Devolve ``None`` para vazio, não numérico, não finito (``NaN``, ``inf``, ``1e999``) e negativo.
    """
    texto = (texto or "").strip()
    if not texto:
        return None
    valor = numero_finito(texto.replace(".", "").replace(",", "."))
    if valor is None or valor < 0:
        return None
    return valor


def _completar(linha: list[str], total: int) -> list[str]:
    linha = [c.strip() for c in linha]
    if len(linha) < total:
        linha = linha + [""] * (total - len(linha))
    return linha


def _raiz(campo: str) -> str:
    """CNPJ básico (raiz) de 8 posições. Com o CNPJ alfanumérico (IN RFB nº 2.229/2024; ver
    ``buscador.resolucao.normalizacao``) a raiz e a ordem podem ter letras de A a Z: o campo é só
    posto em maiúsculas e completado com zeros à esquerda, sem descartar caractere nenhum. O
    layout publicado (metadados do CNPJ) ainda descreve o campo como número; não foi conferido em
    17/11/2026 um arquivo real com raiz alfanumérica."""
    return campo.upper().zfill(8)


def _empresa(linha: list[str], captura_id: str) -> RegistroEmpresa:
    c = _completar(linha, CAMPOS_EMPRESAS)
    return RegistroEmpresa(
        captura_id=captura_id,
        cnpj_basico=_raiz(c[0]),
        razao_social=c[1],
        natureza_juridica=c[2],
        qualificacao_responsavel=c[3],
        capital_social=converter_capital(c[4]),
        porte=c[5],
        ente_federativo=c[6],
    )


def _estabelecimento(linha: list[str], captura_id: str) -> RegistroEstabelecimento:
    c = _completar(linha, CAMPOS_ESTABELECIMENTOS)
    return RegistroEstabelecimento(
        captura_id=captura_id,
        cnpj_basico=_raiz(c[0]),
        cnpj_ordem=c[1].upper().zfill(4),
        cnpj_dv=c[2].zfill(2),
        matriz_filial=c[3],
        nome_fantasia=c[4],
        situacao_cadastral=c[5],
        data_situacao_cadastral=c[6],
        motivo_situacao_cadastral=c[7],
        nome_cidade_exterior=c[8],
        pais=c[9],
        data_inicio_atividade=c[10],
        cnae_fiscal_principal=c[11],
        cnae_fiscal_secundaria=c[12],
        tipo_logradouro=c[13],
        logradouro=c[14],
        numero=c[15],
        complemento=c[16],
        bairro=c[17],
        cep=c[18],
        uf=c[19],
        municipio=c[20],
        # c[21..26]: DDD/telefones/fax, não usados no MVP
        correio_eletronico=c[27],
        situacao_especial=c[28],
        data_situacao_especial=c[29],
    )


def _socio(linha: list[str], captura_id: str) -> RegistroSocio:
    c = _completar(linha, CAMPOS_SOCIOS)
    return RegistroSocio(
        captura_id=captura_id,
        cnpj_basico=_raiz(c[0]),
        identificador_socio=c[1],
        nome_socio=c[2],
        cnpj_cpf_socio=c[3],
        qualificacao_socio=c[4],
        data_entrada_sociedade=c[5],
        pais=c[6],
        representante_legal=c[7],
        nome_representante=c[8],
        qualificacao_representante=c[9],
        faixa_etaria=c[10],
    )


def _simples(linha: list[str], captura_id: str) -> RegistroSimples:
    c = _completar(linha, CAMPOS_SIMPLES)
    return RegistroSimples(
        captura_id=captura_id,
        cnpj_basico=_raiz(c[0]),
        opcao_simples=c[1].upper(),
        data_opcao_simples=c[2],
        data_exclusao_simples=c[3],
        opcao_mei=c[4].upper(),
        data_opcao_mei=c[5],
        data_exclusao_mei=c[6],
    )


INTERPRETADORES: dict[str, Callable[[list[str], str], Registro]] = {
    "empresas": _empresa,
    "estabelecimentos": _estabelecimento,
    "socios": _socio,
    "simples": _simples,
}


class _LeitorLimitado:
    """Leitura binária que falha se o arquivo passar do limite, mesmo que cresça na cópia."""

    def __init__(self, arquivo, limite: int, nome: str) -> None:
        self._arquivo = arquivo
        self._restante = limite
        self._limite = limite
        self._nome = nome

    def read(self, tamanho: int = -1) -> bytes:
        bloco = self._arquivo.read(tamanho)
        self._restante -= len(bloco)
        if self._restante < 0:
            raise ErroConector(
                f"O arquivo {self._nome} passa do limite de {self._limite} bytes por arquivo "
                "(CNPJ_MAX_BYTES_ARQUIVO). Divida o arquivo ou extraia só o recorte necessário."
            )
        return bloco


class ConectorCnpjArquivo(Conector):
    nome = "cnpj_arquivo"
    # 1.1.0: retentativa na abertura e na cópia do arquivo e registros brutos para a comparação.
    # 1.2.0: aceita o CNPJ alfanumérico (letras de A a Z não são mais descartadas) na raiz e
    # na ordem.
    # 1.3.0: lê também a tabela Simples (opção pelo Simples e pelo MEI).
    versao = "1.3.0"

    def __init__(
        self,
        dir_base: Path | str | None = None,
        max_bytes_arquivo: int = 16 * 1024 * 1024,
        max_registros: int = 100_000,
        simulado: bool = False,
        tentativas: int = 3,
        espera_retentativa_segundos: float = 1.0,
        dormir: Callable[[float], None] = time.sleep,
    ) -> None:
        super().__init__(Retentativa(tentativas, espera_retentativa_segundos, dormir))
        self.dir_base = Path(dir_base) if dir_base is not None else None
        self.max_bytes_arquivo = max_bytes_arquivo
        self.max_registros = max_registros
        self.simulado = simulado

    def _resolver_pasta(self, diretorio: str) -> tuple[Path, Path]:
        if self.dir_base is None:
            raise ErroConector(
                "A fonte cnpj_arquivo está desativada: o operador precisa definir a raiz de "
                "ingestão na variável CNPJ_DIR_BASE."
            )
        try:
            raiz = self.dir_base.resolve(strict=True)
            pasta = (raiz / diretorio).resolve(strict=True)
        except (OSError, RuntimeError, ValueError) as erro:
            raise ErroConector(_ERRO_DIRETORIO) from erro
        if not pasta.is_relative_to(raiz) or not pasta.is_dir():
            raise ErroConector(_ERRO_DIRETORIO)
        return raiz, pasta

    def coletar(self, parametros: dict, registrar_captura: RegistradorCaptura) -> ResultadoColeta:
        validos = validar(ParametrosCnpjArquivo, parametros)
        raiz, pasta = self._resolver_pasta(validos.diretorio)

        resultado = ResultadoColeta(fonte=self.nome)
        simulado = self.simulado
        reconhecidos: list[tuple[str, str]] = []
        try:
            with os.scandir(pasta) as entradas:
                for entrada in sorted(entradas, key=lambda e: e.name):
                    if Path(entrada.name).stem.upper() == MARCADOR_SINTETICO:
                        simulado = True
                        continue
                    tabela = classificar_arquivo(entrada.name)
                    if not tabela:
                        continue
                    if entrada.is_symlink() or not entrada.is_file(follow_symlinks=False):
                        resultado.avisos.append(
                            f"{entrada.name} ignorado: não é um arquivo regular "
                            "(links simbólicos não são seguidos)."
                        )
                        continue
                    reconhecidos.append((entrada.name, tabela))
        except OSError as erro:
            raise ErroConector("Não foi possível ler o diretório informado.") from erro
        if not reconhecidos:
            raise ErroConector(
                "Nenhum arquivo no layout do CNPJ no diretório informado "
                "(esperados sufixos EMPRECSV, ESTABELE, SOCIOCSV e SIMPLES)."
            )

        relativo = pasta.relative_to(raiz).as_posix()
        for nome, tabela in reconhecidos:
            partes = (ROTULO_RAIZ, "" if relativo == "." else relativo, nome)
            origem = "/".join(p for p in partes if p)
            metadados = {
                "tabela": tabela,
                "nome_arquivo": nome,
                "codificacao": CODIFICACAO,
                "separador": SEPARADOR,
                "simulado": simulado,
            }
            if simulado:
                origem = f"{PREFIXO_SIMULADO}{self.nome}/{origem}"
                metadados["origem_simulada"] = (
                    "arquivo sintético (fixture de teste ou de demonstração); não veio da "
                    "Receita Federal"
                )
            self._coletar_arquivo(
                pasta / nome, tabela, origem, metadados, registrar_captura, resultado
            )
        return resultado

    def _coletar_arquivo(
        self,
        caminho: Path,
        tabela: str,
        origem: str,
        metadados: dict,
        registrar_captura: RegistradorCaptura,
        resultado: ResultadoColeta,
    ) -> None:
        nome = caminho.name

        def abrir_e_guardar(_ultima: bool):
            """Abre o arquivo e copia a carga para a custódia. Erro de E/S nesta etapa é
            transitório (a cópia parcial é descartada pela custódia) e vale nova tentativa."""
            try:
                descritor = os.open(caminho, os.O_RDONLY | os.O_NOFOLLOW)
            except OSError as erro:
                raise FalhaTransitoria(type(erro).__name__) from erro
            arquivo = os.fdopen(descritor, "rb")
            try:
                estado = os.fstat(arquivo.fileno())
                if not stat.S_ISREG(estado.st_mode):
                    arquivo.close()
                    return None, None
                if estado.st_size > self.max_bytes_arquivo:
                    raise ErroConector(
                        f"O arquivo {nome} tem {estado.st_size} bytes e passa do limite de "
                        f"{self.max_bytes_arquivo} bytes por arquivo (CNPJ_MAX_BYTES_ARQUIVO). "
                        "Divida o arquivo ou extraia só o recorte necessário."
                    )
                captura = registrar_captura(
                    self.nome,
                    origem,
                    _LeitorLimitado(arquivo, self.max_bytes_arquivo, nome),
                    metadados,
                )
            except OSError as erro:
                arquivo.close()
                raise FalhaTransitoria(type(erro).__name__) from erro
            except BaseException:
                arquivo.close()
                raise
            return arquivo, captura

        arquivo, captura = self.retentativa.executar(
            abrir_e_guardar, f"Não foi possível abrir ou ler o arquivo {nome}"
        )
        if arquivo is None:
            resultado.avisos.append(f"{nome} ignorado: não é um arquivo regular.")
            return
        # Com a captura já registrada não há nova tentativa: repetir duplicaria a captura.
        with arquivo:
            resultado.capturas.append(captura)
            try:
                arquivo.seek(0)
                texto = io.TextIOWrapper(arquivo, encoding=CODIFICACAO, newline="")
                try:
                    self._interpretar(texto, tabela, captura.id, nome, resultado)
                finally:
                    texto.detach()
            except OSError as erro:
                raise ErroConector(f"Falha ao ler o arquivo {nome}.") from erro

    @classmethod
    def registros_brutos(cls, conteudo: bytes, metadados: dict) -> dict[str, dict[str, object]]:
        """Linhas do arquivo por chave do registro, com os nomes de campo do layout."""
        tabela = metadados.get("tabela")
        if tabela not in NOMES_CAMPOS:
            return {}
        nomes = NOMES_CAMPOS[tabela]
        registros: dict[str, dict[str, object]] = {}
        texto = io.StringIO(conteudo.decode(CODIFICACAO), newline="")
        try:
            for linha in csv.reader(texto, delimiter=SEPARADOR, quotechar='"'):
                if not linha:
                    continue
                campos = _completar(linha, len(nomes))
                chave = "|".join(campos[p] for p in POSICOES_CHAVE[tabela])
                # Chave repetida no mesmo arquivo: as linhas seguintes ganham um sufixo.
                base, n = chave, 1
                while chave in registros:
                    n += 1
                    chave = f"{base}#{n}"
                registros[chave] = dict(zip(nomes, campos, strict=False))
        except csv.Error:
            return {}
        return registros

    def _interpretar(
        self, texto, tabela: str, captura_id: str, nome: str, resultado: ResultadoColeta
    ) -> None:
        interpretar = INTERPRETADORES[tabela]
        capitais_invalidos = 0
        leitor = csv.reader(texto, delimiter=SEPARADOR, quotechar='"')
        try:
            for linha in leitor:
                if not linha:
                    continue
                if len(resultado.registros) >= self.max_registros:
                    raise ErroConector(
                        f"A coleta passa do limite de {self.max_registros} registros "
                        f"(CNPJ_MAX_REGISTROS), atingido em {nome}. Divida os arquivos ou "
                        "extraia só o recorte necessário."
                    )
                registro = interpretar(linha, captura_id)
                if (
                    isinstance(registro, RegistroEmpresa)
                    and registro.capital_social is None
                    and len(linha) > 4
                    and linha[4].strip()
                ):
                    capitais_invalidos += 1
                resultado.registros.append(registro)
        except csv.Error as erro:
            raise ErroConector(
                f"O arquivo {nome} está fora do layout do CNPJ (linha {leitor.line_num})."
            ) from erro
        if capitais_invalidos:
            resultado.avisos.append(
                f"{nome}: {capitais_invalidos} valor(es) de capital social inválido(s) (não "
                "numérico, não finito ou negativo) foram desconsiderados."
            )
