"""Segundo fator por TOTP (RFC 6238), só com a biblioteca padrão.

RFC 6238 (https://www.rfc-editor.org/rfc/rfc6238), conferida em 16/11/2026:

- "TOTP = HOTP(K, T)", com "T = (Current Unix time - T0) / X", passo padrão X = 30 segundos e
  T0 = 0 (seção 4);
- o HOTP é o da RFC 4226: HMAC-SHA-1 da contagem em 8 bytes (big-endian) e truncamento dinâmico;
- "The verifier MUST NOT accept the second attempt of the OTP" (seção 5.2): por isso quem chama
  ``verificar`` guarda o último passo aceito e o devolve em ``ultimo_passo``;
- a seção 5.2 recomenda aceitar no máximo um passo de atraso de transmissão; aqui a janela é de um
  passo para trás e um para a frente (relógio do telefone adiantado), configurável.

Parâmetros: HMAC-SHA-1, 6 dígitos e passo de 30 s, os valores padrão das RFCs e os que os
aplicativos autenticadores aceitam sem configuração. A implementação é conferida nos testes com os
vetores do Apêndice B da RFC 6238 (SHA-1, 8 dígitos). Decisão: implementar (cerca de 30 linhas)
em vez de acrescentar uma dependência, porque o algoritmo é curto, fixo e tem vetores de teste
oficiais.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote, urlencode

PASSO_SEGUNDOS = 30
DIGITOS = 6
# 160 bits: o tamanho da saída do HMAC-SHA-1, recomendado pela RFC 4226 (seção 4, R6).
TAMANHO_SEGREDO = 20


def gerar_segredo() -> bytes:
    return secrets.token_bytes(TAMANHO_SEGREDO)


def segredo_base32(segredo: bytes) -> str:
    """Forma que o usuário digita no aplicativo autenticador (Base32 sem preenchimento)."""
    return base64.b32encode(segredo).decode("ascii").rstrip("=")


def passo_de(instante: float, passo_segundos: int = PASSO_SEGUNDOS) -> int:
    return int(instante // passo_segundos)


def hotp(segredo: bytes, contador: int, digitos: int = DIGITOS) -> str:
    """HOTP da RFC 4226: HMAC-SHA-1 do contador e truncamento dinâmico."""
    # SHA-1 aqui é o algoritmo fixado pela RFC para o HMAC do HOTP, não um resumo de integridade.
    mac = hmac.new(segredo, struct.pack(">Q", contador), hashlib.sha1).digest()
    deslocamento = mac[-1] & 0x0F
    valor = struct.unpack(">I", mac[deslocamento : deslocamento + 4])[0] & 0x7FFFFFFF
    return str(valor % (10**digitos)).zfill(digitos)


def codigo(segredo: bytes, instante: float | None = None, digitos: int = DIGITOS) -> str:
    return hotp(segredo, passo_de(time.time() if instante is None else instante), digitos)


def verificar(
    segredo: bytes,
    informado: str,
    *,
    instante: float | None = None,
    ultimo_passo: int | None = None,
    janela: int = 1,
) -> int | None:
    """Devolve o passo de tempo do código aceito, ou ``None`` se o código não vale.

    Um passo menor ou igual a ``ultimo_passo`` nunca é aceito (uso único). Todos os candidatos
    da janela são comparados, em tempo constante, para não revelar qual deles quase acertou.
    """
    informado = informado.strip()
    if len(informado) != DIGITOS or not informado.isascii() or not informado.isdigit():
        return None
    atual = passo_de(time.time() if instante is None else instante)
    aceito = None
    for passo in range(atual - janela, atual + janela + 1):
        confere = hmac.compare_digest(hotp(segredo, passo), informado)
        if confere and (ultimo_passo is None or passo > ultimo_passo) and aceito is None:
            aceito = passo
    return aceito


def uri_de_cadastro(segredo: bytes, usuario: str, emissor: str) -> str:
    """URI ``otpauth://`` para cadastrar o segredo num aplicativo autenticador (por QR code).

    Formato "Key Uri Format" do Google Authenticator
    (https://github.com/google/google-authenticator/wiki/Key-Uri-Format, conferido em
    16/11/2026): ``otpauth://TYPE/LABEL?PARAMETERS``, rótulo ``issuer:accountname`` (os dois
    pontos podem vir codificados como ``%3A``) e parâmetros ``secret`` (Base32 sem
    preenchimento), ``issuer``, ``algorithm``, ``digits`` e ``period``. A página avisa que o
    aplicativo do Google ignora ``algorithm`` e ``period``: por isso os valores usados são os
    padrões (SHA1, 6 dígitos, 30 s). A tela mostra também o segredo em Base32, para digitar à mão.
    """
    rotulo = quote(f"{emissor}:{usuario}", safe="")
    consulta = urlencode(
        {
            "secret": segredo_base32(segredo),
            "issuer": emissor,
            "algorithm": "SHA1",
            "digits": DIGITOS,
            "period": PASSO_SEGUNDOS,
        }
    )
    return f"otpauth://totp/{rotulo}?{consulta}"
