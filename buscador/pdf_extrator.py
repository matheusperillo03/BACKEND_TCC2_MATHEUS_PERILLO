"""Processo auxiliar que extrai o texto de um PDF (chamado por ``buscador.documentos``).

Roda SEMPRE num processo separado (``python -I pdf_extrator.py <max_paginas> <max_caracteres>
<tempo>``): o PDF chega pela entrada padrão e a resposta sai como uma linha de JSON. O processo
pai impõe o prazo e mata este processo se ele estourar; um PDF malicioso ou defeituoso não
trava nem derruba a API.

O que este módulo faz e o que NÃO faz:

- só chama ``PdfReader`` e ``page.extract_text()`` da biblioteca ``pypdf`` (licença BSD-3-Clause),
  que é Python puro: não renderiza, não executa JavaScript nem ações do documento e não abre
  anexos nem arquivos embutidos (este código nunca os lê);
- PDF criptografado é recusado, mesmo com senha de usuário vazia: nada é decifrado;
- o número de páginas e o total de caracteres têm teto;
- não importa nada do pacote ``buscador`` (por isso é executado pelo caminho do arquivo, com
  ``-I``: sem variáveis de ambiente do Python, sem o diretório corrente e sem o do script no
  ``sys.path``).

Texto devolvido: o de cada página, na ordem, separado por uma linha em branco. Caracteres de
controle (menos ``\\n`` e ``\\t``) e substitutos isolados são removidos, para o texto caber em JSON
e passar pelas mesmas validações do texto puro. OCR não é feito: página sem camada de texto
devolve texto vazio.
"""

from __future__ import annotations

import io
import json
import logging
import sys
import unicodedata

SEPARADOR_PAGINAS = "\n\n"
MOTIVOS = ("criptografado", "corrompido", "paginas", "caracteres")


def _limpar(texto: str) -> str:
    return "".join(
        c
        for c in texto.replace("\r\n", "\n").replace("\r", "\n")
        if c in "\n\t" or unicodedata.category(c) not in ("Cc", "Cs", "Co", "Cn")
    )


def extrair(conteudo: bytes, max_paginas: int, max_caracteres: int) -> dict:
    """``{"ok": True, "paginas", "paginas_com_texto", "texto", "extrator"}`` ou
    ``{"ok": False, "motivo", "detalhe"}``."""
    import pypdf
    from pypdf.errors import PyPdfError

    extrator = f"pypdf {pypdf.__version__}"
    try:
        leitor = pypdf.PdfReader(io.BytesIO(conteudo), strict=False)
        if leitor.is_encrypted:
            return {"ok": False, "motivo": "criptografado", "detalhe": "", "extrator": extrator}
        total = len(leitor.pages)
        if total > max_paginas:
            return {"ok": False, "motivo": "paginas", "detalhe": str(total), "extrator": extrator}
        partes: list[str] = []
        caracteres = 0
        com_texto = 0
        for pagina in leitor.pages:
            texto = _limpar(pagina.extract_text() or "")
            if texto.strip():
                com_texto += 1
            caracteres += len(texto) + len(SEPARADOR_PAGINAS)
            if caracteres > max_caracteres:
                return {
                    "ok": False,
                    "motivo": "caracteres",
                    "detalhe": str(max_caracteres),
                    "extrator": extrator,
                }
            partes.append(texto)
    except (PyPdfError, ValueError, TypeError, KeyError, IndexError, AttributeError) as erro:
        return {
            "ok": False,
            "motivo": "corrompido",
            "detalhe": type(erro).__name__,
            "extrator": extrator,
        }
    except (RecursionError, MemoryError, OverflowError, AssertionError, OSError) as erro:
        return {
            "ok": False,
            "motivo": "corrompido",
            "detalhe": type(erro).__name__,
            "extrator": extrator,
        }
    return {
        "ok": True,
        "paginas": total,
        "paginas_com_texto": com_texto,
        "texto": SEPARADOR_PAGINAS.join(partes),
        "extrator": extrator,
    }


def _limitar_recursos(tempo_segundos: int) -> None:
    """Segunda barreira, além do prazo imposto pelo processo pai: teto de CPU deste processo."""
    try:
        import resource

        resource.setrlimit(resource.RLIMIT_CPU, (tempo_segundos + 2, tempo_segundos + 5))
    except (ImportError, ValueError, OSError):
        pass


def principal(argumentos: list[str]) -> int:
    max_paginas, max_caracteres, tempo = (int(a) for a in argumentos[:3])
    _limitar_recursos(tempo)
    # Os avisos da pypdf sobre PDFs fora do padrão não interessam à resposta.
    logging.disable(logging.CRITICAL)
    conteudo = sys.stdin.buffer.read()
    resposta = extrair(conteudo, max_paginas, max_caracteres)
    sys.stdout.write(json.dumps(resposta, ensure_ascii=True))
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(principal(sys.argv[1:]))
