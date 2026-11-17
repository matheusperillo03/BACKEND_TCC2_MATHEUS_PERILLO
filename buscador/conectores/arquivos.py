"""Leitura confinada de arquivos locais, comum aos conectores de arquivo do horizonte 2 (TSE e
editais). Mesmas garantias do conector do CNPJ: a pasta fica dentro da raiz de ingestão definida
pelo operador, links simbólicos não são seguidos, o caminho do servidor não volta nas mensagens e
cada arquivo tem teto de bytes.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import BinaryIO

from buscador.conectores.base import ErroConector, FalhaTransitoria, Retentativa
from buscador.custodia import PREFIXO_SIMULADO

MARCADOR_SINTETICO = "SINTETICO"


def resolver_pasta(dir_base: Path | None, diretorio: str, variavel: str) -> tuple[Path, Path]:
    """``(raiz, pasta)`` com a pasta confinada à raiz de ingestão ``variavel``."""
    if dir_base is None:
        raise ErroConector(
            f"Esta fonte está desativada: o operador precisa definir a raiz de ingestão na "
            f"variável {variavel}."
        )
    erro = (
        f"Diretório não encontrado dentro da raiz de ingestão ({variavel}). Informe um caminho "
        "relativo a essa raiz."
    )
    try:
        raiz = Path(dir_base).resolve(strict=True)
        pasta = (raiz / diretorio).resolve(strict=True)
    except (OSError, RuntimeError, ValueError) as falha:
        raise ErroConector(erro) from falha
    if not pasta.is_relative_to(raiz) or not pasta.is_dir():
        raise ErroConector(erro)
    return raiz, pasta


def listar_arquivos(pasta: Path, avisos: list[str]) -> tuple[list[str], bool]:
    """Nomes dos arquivos regulares da pasta (em ordem) e se ela tem o marcador ``SINTETICO``."""
    nomes: list[str] = []
    sintetico = False
    try:
        with os.scandir(pasta) as entradas:
            for entrada in sorted(entradas, key=lambda e: e.name):
                if Path(entrada.name).stem.upper() == MARCADOR_SINTETICO:
                    sintetico = True
                    continue
                if entrada.name.startswith("."):
                    continue
                if entrada.is_symlink() or not entrada.is_file(follow_symlinks=False):
                    if not entrada.is_dir(follow_symlinks=False):
                        avisos.append(
                            f"{entrada.name} ignorado: não é um arquivo regular (links "
                            "simbólicos não são seguidos)."
                        )
                    continue
                nomes.append(entrada.name)
    except OSError as erro:
        raise ErroConector("Não foi possível ler o diretório informado.") from erro
    return nomes, sintetico


def ler_arquivo(
    caminho: Path, max_bytes: int, variavel_limite: str, retentativa: Retentativa
) -> bytes:
    """Conteúdo do arquivo regular, com teto de bytes; erro de E/S é transitório (retentativa)."""
    nome = caminho.name

    def ler(_ultima: bool) -> bytes:
        try:
            descritor = os.open(caminho, os.O_RDONLY | os.O_NOFOLLOW)
        except OSError as erro:
            raise FalhaTransitoria(type(erro).__name__) from erro
        with os.fdopen(descritor, "rb") as arquivo:
            try:
                estado = os.fstat(arquivo.fileno())
                if not stat.S_ISREG(estado.st_mode):
                    raise ErroConector(f"{nome} não é um arquivo regular.")
                if estado.st_size > max_bytes:
                    raise ErroConector(
                        f"O arquivo {nome} tem {estado.st_size} bytes e passa do limite de "
                        f"{max_bytes} bytes por arquivo ({variavel_limite}). Extraia só o recorte "
                        "necessário (por exemplo, o CSV de uma UF)."
                    )
                conteudo = arquivo.read(max_bytes + 1)
            except OSError as erro:
                raise FalhaTransitoria(type(erro).__name__) from erro
        if len(conteudo) > max_bytes:
            raise ErroConector(
                f"O arquivo {nome} passa do limite de {max_bytes} bytes ({variavel_limite})."
            )
        return conteudo

    return retentativa.executar(ler, f"Não foi possível abrir ou ler o arquivo {nome}")


def abrir_arquivo(caminho: Path) -> tuple[BinaryIO, int]:
    """Abre o arquivo regular para leitura em fluxo (sem seguir link simbólico) e devolve o
    arquivo aberto e o tamanho em bytes. Erro de E/S é transitório (``FalhaTransitoria``)."""
    try:
        descritor = os.open(caminho, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as erro:
        raise FalhaTransitoria(type(erro).__name__) from erro
    arquivo = os.fdopen(descritor, "rb")
    try:
        estado = os.fstat(arquivo.fileno())
    except OSError as erro:
        arquivo.close()
        raise FalhaTransitoria(type(erro).__name__) from erro
    if not stat.S_ISREG(estado.st_mode):
        arquivo.close()
        raise ErroConector(f"{caminho.name} não é um arquivo regular.")
    return arquivo, estado.st_size


def origem_do_arquivo(
    conector: str, variavel: str, raiz: Path, pasta: Path, nome: str, simulado: bool
) -> str:
    """Origem gravada na custódia: rótulo da raiz + caminho relativo (nunca o caminho real)."""
    relativo = pasta.relative_to(raiz).as_posix()
    partes = (variavel, "" if relativo == "." else relativo, nome)
    origem = "/".join(p for p in partes if p)
    return f"{PREFIXO_SIMULADO}{conector}/{origem}" if simulado else origem


def decodificar(conteudo: bytes) -> tuple[str, str]:
    """Texto e codificação detectada: UTF-8 estrito primeiro; se falhar, Latin-1.

    O leiame do TSE declara Latin-1, mas o arquivo de 2024 veio em UTF-8 (observação registrada em
    docs/sistema/fontes-horizonte2.md). Latin-1 decodifica qualquer sequência de bytes, por isso
    é a alternativa, e não a primeira tentativa."""
    try:
        return conteudo.decode("utf-8-sig"), "utf-8"
    except UnicodeDecodeError:
        return conteudo.decode("latin-1"), "latin-1"
