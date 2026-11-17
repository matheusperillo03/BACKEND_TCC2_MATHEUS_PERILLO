from __future__ import annotations

import hashlib
import os
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from sqlalchemy.orm import Session

from buscador.cripto import ALGORITMO, Cofre, ErroCripto
from buscador.modelos import Captura, iso_utc

PREFIXO_SIMULADO = "simulado://"
_BLOCO = 1024 * 1024
# Arquivos cifrados (AES-256-GCM) têm esta extensão; os sem extensão são cargas em claro,
# gravadas antes da criptografia em repouso (H23) ou por uma custódia sem cofre.
EXTENSAO_CIFRADA = ".gcm"
CONTEXTO_CAPTURAS = "capturas"


class ConteudoGrandeDemais(Exception):
    """A carga passou do limite de bytes informado; nada foi guardado."""


def sha256_bytes(conteudo: bytes) -> str:
    return hashlib.sha256(conteudo).hexdigest()


def marcar_simulado(url_ou_arquivo: str) -> str:
    """Troca o esquema da origem por ``simulado://`` (ou o acrescenta), sem duplicar."""
    if url_ou_arquivo.startswith(PREFIXO_SIMULADO):
        return url_ou_arquivo
    _, separador, resto = url_ou_arquivo.partition("://")
    return PREFIXO_SIMULADO + (resto if separador else url_ou_arquivo.lstrip("/"))


@dataclass(frozen=True)
class ResultadoVerificacao:
    integra: bool
    sha256_registrado: str
    sha256_recalculado: str | None
    arquivo_encontrado: bool
    # Verdadeiro quando a carga está cifrada no disco (AES-256-GCM).
    cifrada: bool = False


class Custodia:
    """Guarda cargas brutas endereçadas pelo conteúdo e verifica a integridade depois.

    O arquivo fica em ``<dados_dir>/capturas/<aa>/<sha256>``; o mesmo conteúdo coletado duas
    vezes gera duas capturas (carimbos de tempo diferentes) apontando para o mesmo arquivo.

    Toda captura diz se é sintética: ``metadados["simulado"]`` é sempre gravado e, quando
    verdadeiro, ``url_ou_arquivo`` começa com ``simulado://``. Assim uma carga de demonstração ou
    de teste nunca se confunde com uma resposta de fonte oficial.

    Criptografia em repouso (RNF01, H23): com um ``cofre``, a carga é gravada cifrada com
    AES-256-GCM em ``<aa>/<sha256>.gcm``. O SHA-256 da custódia continua sendo o do conteúdo
    ORIGINAL (calculado antes de cifrar) e entra nos dados associados da cifra: o arquivo de uma
    captura não decifra no lugar de outra. A verificação decifra e recalcula o SHA-256. Cargas em
    claro de versões anteriores continuam legíveis e verificáveis (o caminho de cada captura diz
    em que forma ela está). Sem cofre (só em testes de unidade), grava em claro.

    Limitação: a carga cifrada passa inteira pela memória (o AES-GCM autentica a mensagem toda de
    uma vez); os conectores já limitam o tamanho de cada carga (no máximo 64 MiB, o arquivo do TSE).
    """

    def __init__(self, dados_dir: Path, cofre: Cofre | None = None) -> None:
        self.raiz = Path(dados_dir) / "capturas"
        self.raiz.mkdir(parents=True, exist_ok=True)
        self.cofre = cofre

    def _caminho(self, sha256: str) -> Path:
        nome = sha256 + (EXTENSAO_CIFRADA if self.cofre is not None else "")
        return self.raiz / sha256[:2] / nome

    def _decifrar(self, caminho: Path, sha256: str) -> bytes:
        if self.cofre is None:
            raise ErroCripto("A captura está cifrada e a chave da custódia não foi configurada.")
        return self.cofre.decifrar(CONTEXTO_CAPTURAS, caminho.read_bytes(), sha256.encode("ascii"))

    def _guardar_cifrado(
        self, conteudo: bytes | BinaryIO, max_bytes: int | None
    ) -> tuple[str, int]:
        """Lê a carga em blocos (respeitando o limite), calcula o SHA-256 do conteúdo original e
        grava o arquivo cifrado por troca atômica de nome."""
        if self.cofre is None:
            raise ErroCripto("A custódia não tem cofre configurado.")
        acumulado = bytearray()
        blocos = (
            [conteudo]
            if isinstance(conteudo, bytes | bytearray | memoryview)
            else iter(lambda: conteudo.read(_BLOCO), b"")
        )
        for bloco in blocos:
            acumulado += bloco
            if max_bytes is not None and len(acumulado) > max_bytes:
                raise ConteudoGrandeDemais(f"conteúdo acima do limite de {max_bytes} bytes")
        digest = hashlib.sha256(acumulado).hexdigest()
        destino = self._caminho(digest)
        if destino.exists():
            try:
                integro = hashlib.sha256(self._decifrar(destino, digest)).hexdigest() == digest
            except (ErroCripto, OSError):
                integro = False
            if integro:
                return digest, len(acumulado)
            # A cópia em disco foi alterada: preserva-a à parte e restaura o conteúdo certo.
            destino.replace(destino.with_name(f"{destino.name}.divergente-{uuid.uuid4().hex}"))
        cifrado = self.cofre.cifrar(CONTEXTO_CAPTURAS, bytes(acumulado), digest.encode("ascii"))
        descritor, temporario = tempfile.mkstemp(dir=self.raiz, prefix=".parcial-")
        try:
            with os.fdopen(descritor, "wb") as saida:
                saida.write(cifrado)
            destino.parent.mkdir(parents=True, exist_ok=True)
            os.replace(temporario, destino)
        except BaseException:
            Path(temporario).unlink(missing_ok=True)
            raise
        return digest, len(acumulado)

    def _sha256_arquivo(self, caminho: Path) -> str:
        with open(caminho, "rb") as arquivo:
            return hashlib.file_digest(arquivo, "sha256").hexdigest()

    def _guardar(self, conteudo: bytes | BinaryIO, max_bytes: int | None) -> tuple[str, int]:
        """Copia a carga em blocos para um temporário de nome único, calculando o SHA-256."""
        if self.cofre is not None:
            return self._guardar_cifrado(conteudo, max_bytes)
        resumo = hashlib.sha256()
        tamanho = 0
        descritor, temporario = tempfile.mkstemp(dir=self.raiz, prefix=".parcial-")
        try:
            with os.fdopen(descritor, "wb") as saida:
                blocos = (
                    [conteudo]
                    if isinstance(conteudo, bytes | bytearray | memoryview)
                    else iter(lambda: conteudo.read(_BLOCO), b"")
                )
                for bloco in blocos:
                    tamanho += len(bloco)
                    if max_bytes is not None and tamanho > max_bytes:
                        raise ConteudoGrandeDemais(f"conteúdo acima do limite de {max_bytes} bytes")
                    resumo.update(bloco)
                    saida.write(bloco)
            digest = resumo.hexdigest()
            destino = self._caminho(digest)
            if destino.exists() and self._sha256_arquivo(destino) != digest:
                # A cópia em disco foi alterada: preserva-a à parte e restaura o conteúdo certo.
                destino.replace(destino.with_name(f"{digest}.divergente-{uuid.uuid4().hex}"))
            if destino.exists():
                Path(temporario).unlink()
            else:
                destino.parent.mkdir(parents=True, exist_ok=True)
                os.replace(temporario, destino)
        except BaseException:
            Path(temporario).unlink(missing_ok=True)
            raise
        return digest, tamanho

    def registrar(
        self,
        sessao: Session,
        *,
        caso_id: str,
        fonte: str,
        url_ou_arquivo: str,
        conteudo: bytes | BinaryIO,
        metadados: dict | None = None,
        max_bytes: int | None = None,
        conector_nome: str = "",
        conector_versao: str = "",
        coleta_id: str | None = None,
    ) -> Captura:
        """Guarda a carga (bytes ou arquivo aberto em modo binário, lido em blocos).

        ``conector_nome`` e ``conector_versao`` dizem quem produziu a captura (RF07, H14) e
        ``coleta_id``, a que coleta ela pertence."""
        metadados = dict(metadados or {})
        simulado = bool(metadados.get("simulado")) or url_ou_arquivo.startswith(PREFIXO_SIMULADO)
        metadados["simulado"] = simulado
        if simulado:
            url_ou_arquivo = marcar_simulado(url_ou_arquivo)
        digest, tamanho = self._guardar(conteudo, max_bytes)
        if self.cofre is not None:
            # Como a carga está guardada (o SHA-256 acima é sempre o do conteúdo original).
            metadados["cifra_em_repouso"] = {
                "algoritmo": ALGORITMO,
                "chave_id": self.cofre.id_corrente,
            }
        captura = Captura(
            caso_id=caso_id,
            fonte=fonte,
            url_ou_arquivo=url_ou_arquivo,
            coletado_em=iso_utc(),
            sha256=digest,
            tamanho_bytes=tamanho,
            caminho_relativo=str(self._caminho(digest).relative_to(self.raiz)),
            metadados=metadados,
            conector_nome=conector_nome,
            conector_versao=conector_versao,
            coleta_id=coleta_id,
        )
        sessao.add(captura)
        sessao.flush()
        return captura

    @staticmethod
    def cifrada(captura: Captura) -> bool:
        return captura.caminho_relativo.endswith(EXTENSAO_CIFRADA)

    def ler(self, captura: Captura) -> bytes:
        """O conteúdo ORIGINAL da captura (decifrado, se estiver cifrado no disco)."""
        caminho = self.raiz / captura.caminho_relativo
        if self.cifrada(captura):
            return self._decifrar(caminho, captura.sha256)
        return caminho.read_bytes()

    def verificar(self, captura: Captura) -> ResultadoVerificacao:
        """Recalcula o SHA-256 do conteúdo original. Numa captura cifrada, um arquivo alterado
        (ou cifrado com outra chave) não passa na autenticação do AES-GCM: a captura não está
        íntegra e não há SHA-256 recalculado."""
        caminho = self.raiz / captura.caminho_relativo
        cifrada = self.cifrada(captura)
        try:
            if cifrada:
                if not caminho.is_file():
                    raise FileNotFoundError(caminho)
                try:
                    recalculado = hashlib.sha256(
                        self._decifrar(caminho, captura.sha256)
                    ).hexdigest()
                except ErroCripto:
                    return ResultadoVerificacao(
                        integra=False,
                        sha256_registrado=captura.sha256,
                        sha256_recalculado=None,
                        arquivo_encontrado=True,
                        cifrada=True,
                    )
            else:
                recalculado = self._sha256_arquivo(caminho)
        except OSError:
            return ResultadoVerificacao(
                integra=False,
                sha256_registrado=captura.sha256,
                sha256_recalculado=None,
                arquivo_encontrado=False,
                cifrada=cifrada,
            )
        return ResultadoVerificacao(
            integra=recalculado == captura.sha256,
            sha256_registrado=captura.sha256,
            sha256_recalculado=recalculado,
            arquivo_encontrado=True,
            cifrada=cifrada,
        )
