"""Texto de um documento guardado na custódia: texto puro ou PDF com camada de texto.

Regra de custódia (RF07): a captura guarda os BYTES ORIGINAIS do arquivo (o PDF como veio) e o
SHA-256 registrado é o desses bytes. O texto é DERIVADO: é extraído de novo, da captura, toda vez
que alguém precisa dele (rota de texto, tipologia T10 e extração por IA, H22). Por isso as
posições de um trecho (``posicao_inicio``/``posicao_fim`` das sugestões da IA; linha, coluna e
posição da T10) referem-se ao TEXTO EXTRAÍDO, e não a deslocamentos dentro do arquivo PDF.

Para que essa referência seja reproduzível, a coleta grava nos metadados da captura o extrator
e a versão (``extrator``, por exemplo ``pypdf 7.1.0``) e o SHA-256 do texto extraído
(``texto_sha256``, em UTF-8). ``extrair_texto`` devolve o resumo do texto atual; quem lê compara
com o da coleta e sabe se uma troca de versão da biblioteca mudou o texto.

Limites defensivos do PDF (``LimitesPdf``): tamanho do arquivo, número de páginas, total de
caracteres e tempo. A extração roda num processo separado (``buscador/pdf_extrator.py``), morto
se passar do prazo. PDF criptografado, corrompido, acima dos limites ou sem camada de texto
levanta ``DocumentoRecusado`` com o motivo. OCR está fora do escopo: PDF escaneado é relatado
como "sem camada de texto".

Biblioteca: ``pypdf`` (https://pypdf.readthedocs.io/, licença BSD-3-Clause, compatível com a
licença MIT do projeto; conferida nos metadados do pacote instalado em 17/11/2026).
"""

from __future__ import annotations

import hashlib
import json
import subprocess  # nosec B404 - só para rodar o extrator de PDF do próprio projeto, sem shell
import sys
from dataclasses import dataclass
from pathlib import Path

MAGICA_PDF = b"%PDF-"
EXTRATOR = Path(__file__).with_name("pdf_extrator.py")

SEM_CAMADA_DE_TEXTO = "sem_camada_de_texto"
MENSAGENS = {
    "tamanho": "o PDF passa do tamanho máximo de {detalhe} bytes (EDITAIS_PDF_MAX_BYTES)",
    "paginas": "o PDF tem {detalhe} páginas e passa do máximo permitido (EDITAIS_PDF_MAX_PAGINAS)",
    "caracteres": (
        "o texto do PDF passa do máximo de {detalhe} caracteres (EDITAIS_PDF_MAX_CARACTERES)"
    ),
    "tempo": (
        "a extração do texto do PDF passou do prazo de {detalhe} s (EDITAIS_PDF_TEMPO_SEGUNDOS)"
    ),
    "criptografado": "o PDF é criptografado (protegido por senha ou por permissões)",
    "corrompido": "o PDF está corrompido ou não é um PDF válido",
    SEM_CAMADA_DE_TEXTO: (
        "o PDF está sem camada de texto (provavelmente escaneado); OCR está fora do escopo"
    ),
    "binario": "a captura não é um documento de texto (conteúdo binário)",
}


@dataclass(frozen=True)
class LimitesPdf:
    max_bytes: int = 50 * 1024 * 1024
    max_paginas: int = 300
    max_caracteres: int = 5_000_000
    tempo_segundos: float = 60.0


class DocumentoRecusado(Exception):
    """O documento não pôde virar texto. ``motivo`` é um código estável (chave de ``MENSAGENS``)
    e a mensagem é apresentável ao usuário."""

    def __init__(self, motivo: str, detalhe: str = "") -> None:
        self.motivo = motivo
        self.detalhe = detalhe
        modelo = MENSAGENS.get(motivo, "o documento não pôde ser lido")
        super().__init__(modelo.format(detalhe=detalhe))


@dataclass(frozen=True)
class TextoDocumento:
    texto: str
    formato: str  # "texto" | "pdf"
    # Texto puro: a codificação detectada. PDF: "pdf" (o texto extraído é Unicode).
    codificacao: str
    paginas: int | None = None
    paginas_com_texto: int | None = None
    extrator: str = ""

    @property
    def sha256(self) -> str:
        """SHA-256 do texto extraído (UTF-8): identifica a que texto as posições se referem."""
        return hashlib.sha256(self.texto.encode("utf-8", "surrogatepass")).hexdigest()


def eh_pdf(conteudo: bytes) -> bool:
    """A assinatura ``%PDF-`` pode vir depois de alguns bytes de lixo (toleram-se 1024)."""
    return MAGICA_PDF in conteudo[:1024]


def _extrair_pdf(conteudo: bytes, limites: LimitesPdf) -> TextoDocumento:
    if len(conteudo) > limites.max_bytes:
        raise DocumentoRecusado("tamanho", str(limites.max_bytes))
    tempo = max(1, int(limites.tempo_segundos))
    try:
        # O extrator roda isolado (-I), com ambiente vazio: não herda segredos da API.
        processo = subprocess.run(  # nosec B603 - executável e script fixos, sem shell
            [
                sys.executable,
                "-I",
                str(EXTRATOR),
                str(limites.max_paginas),
                str(limites.max_caracteres),
                str(tempo),
            ],
            input=conteudo,
            capture_output=True,
            timeout=limites.tempo_segundos,
            env={},
            check=False,
        )
    except subprocess.TimeoutExpired as erro:
        raise DocumentoRecusado("tempo", f"{limites.tempo_segundos:g}") from erro
    except OSError as erro:
        raise DocumentoRecusado("corrompido", type(erro).__name__) from erro
    try:
        resposta = json.loads(processo.stdout.decode("utf-8"))
        if not isinstance(resposta, dict):
            raise ValueError("resposta fora do formato")
    except (ValueError, UnicodeDecodeError) as erro:
        # Sem resposta: o processo morreu (teto de CPU, falta de memória ou falha do leitor).
        motivo = "tempo" if processo.returncode < 0 else "corrompido"
        detalhe = f"{limites.tempo_segundos:g}" if motivo == "tempo" else ""
        raise DocumentoRecusado(motivo, detalhe) from erro
    if not resposta.get("ok"):
        raise DocumentoRecusado(
            str(resposta.get("motivo") or "corrompido"), str(resposta.get("detalhe") or "")
        )
    texto = str(resposta.get("texto") or "")
    if not texto.strip():
        raise DocumentoRecusado(SEM_CAMADA_DE_TEXTO, str(resposta.get("paginas") or ""))
    return TextoDocumento(
        texto=texto,
        formato="pdf",
        codificacao="pdf",
        paginas=int(resposta.get("paginas") or 0),
        paginas_com_texto=int(resposta.get("paginas_com_texto") or 0),
        extrator=str(resposta.get("extrator") or "pypdf"),
    )


def extrair_texto(conteudo: bytes, limites: LimitesPdf | None = None) -> TextoDocumento:
    """Texto do documento. PDF (pela assinatura do conteúdo, não pelo nome): extração com os
    limites; senão, texto em UTF-8 ou Latin-1. Levanta ``DocumentoRecusado``."""
    if eh_pdf(conteudo):
        return _extrair_pdf(conteudo, limites or LimitesPdf())
    if b"\x00" in conteudo[:65_536]:
        raise DocumentoRecusado("binario")
    # Importação tardia: ``buscador.conectores`` importa este módulo (conector de editais).
    from buscador.conectores.arquivos import decodificar

    texto, codificacao = decodificar(conteudo)
    return TextoDocumento(texto=texto, formato="texto", codificacao=codificacao)
