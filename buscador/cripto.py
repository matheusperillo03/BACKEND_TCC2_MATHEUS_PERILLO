"""Criptografia em repouso (RNF01, H23): AES-256-GCM com chave fora do banco.

Uma chave mestra de 32 bytes (``CUSTODIA_CHAVE`` ou o arquivo ``CUSTODIA_CHAVE_ARQUIVO``) nunca
cifra nada diretamente: cada uso tem uma subchave própria, derivada com HKDF-SHA256 e um rótulo de
contexto ("capturas", "totp"). Assim, o vazamento de um texto cifrado de um contexto não serve
para o outro, e a mesma chave mestra não é reutilizada com dois propósitos.

Formato de cada blob cifrado::

    MAGICA (8 bytes) | versão (1) | id da chave (8) | nonce (12) | texto cifrado + etiqueta GCM (16)

O id da chave são os 8 primeiros bytes do SHA-256 da chave mestra: diz com qual chave o blob foi
cifrado sem revelar a chave. É o que permite ler blobs de uma chave anterior
(``CUSTODIA_CHAVES_ANTERIORES``) depois de trocar a chave corrente. Os dados associados (AAD)
ligam o blob ao contexto e, nas capturas, ao SHA-256 do conteúdo original: um arquivo cifrado
copiado para o lugar de outro não decifra.

Biblioteca: ``cryptography`` (pyca), que expõe o AES-GCM e o HKDF do OpenSSL. O nonce é aleatório
de 96 bits, gerado a cada cifragem.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

MAGICA = b"OSINTGCM"
VERSAO = 1
TAMANHO_CHAVE = 32
_TAMANHO_ID = 8
_TAMANHO_NONCE = 12
_CABECALHO = len(MAGICA) + 1 + _TAMANHO_ID + _TAMANHO_NONCE
ALGORITMO = "AES-256-GCM"


class ErroCripto(Exception):
    """Chave ausente ou inválida, ou blob que não decifra (adulterado ou de outra chave)."""


def interpretar_chave(texto: str, nome: str = "CUSTODIA_CHAVE") -> bytes:
    """Chave de 32 bytes escrita em hexadecimal (64 caracteres, ``openssl rand -hex 32``)."""
    texto = texto.strip()
    try:
        chave = bytes.fromhex(texto)
    except ValueError as erro:
        raise ErroCripto(
            f"{nome} deve ser uma chave de 32 bytes em hexadecimal (64 caracteres; gere com "
            "`openssl rand -hex 32`)."
        ) from erro
    if len(chave) != TAMANHO_CHAVE:
        raise ErroCripto(
            f"{nome} deve ter 32 bytes (64 caracteres hexadecimais); recebidos {len(chave)} bytes."
        )
    return chave


def ler_chave_de_arquivo(caminho: Path, nome: str = "CUSTODIA_CHAVE_ARQUIVO") -> bytes:
    try:
        texto = Path(caminho).read_text(encoding="ascii")
    except (OSError, UnicodeDecodeError) as erro:
        raise ErroCripto(f"{nome} não aponta para um arquivo de chave legível.") from erro
    return interpretar_chave(texto, nome)


def id_da_chave(chave: bytes) -> bytes:
    return hashlib.sha256(b"buscador-osint/id-da-chave/" + chave).digest()[:_TAMANHO_ID]


class Cofre:
    """Cifra e decifra com subchaves derivadas da chave mestra corrente (e lê as anteriores)."""

    def __init__(self, chave: bytes, anteriores: tuple[bytes, ...] | list[bytes] = ()) -> None:
        for candidata in (chave, *anteriores):
            if len(candidata) != TAMANHO_CHAVE:
                raise ErroCripto("A chave mestra deve ter 32 bytes.")
        self._corrente = id_da_chave(chave)
        self._chaves = {id_da_chave(c): c for c in (*anteriores, chave)}
        self._subchaves: dict[tuple[bytes, str], AESGCM] = {}

    @property
    def id_corrente(self) -> str:
        return self._corrente.hex()

    def _aead(self, id_chave: bytes, contexto: str) -> AESGCM:
        marca = (id_chave, contexto)
        if marca not in self._subchaves:
            mestra = self._chaves.get(id_chave)
            if mestra is None:
                raise ErroCripto(
                    f"O conteúdo foi cifrado com uma chave que não está configurada (id "
                    f"{id_chave.hex()}); inclua-a em CUSTODIA_CHAVES_ANTERIORES."
                )
            subchave = HKDF(
                algorithm=hashes.SHA256(),
                length=TAMANHO_CHAVE,
                salt=None,
                info=b"buscador-osint/v1/" + contexto.encode("ascii"),
            ).derive(mestra)
            self._subchaves[marca] = AESGCM(subchave)
        return self._subchaves[marca]

    @staticmethod
    def _aad(contexto: str, associado: bytes) -> bytes:
        return contexto.encode("ascii") + b"\x00" + associado

    def cifrar(self, contexto: str, dados: bytes, associado: bytes = b"") -> bytes:
        nonce = os.urandom(_TAMANHO_NONCE)
        cifrado = self._aead(self._corrente, contexto).encrypt(
            nonce, bytes(dados), self._aad(contexto, associado)
        )
        return MAGICA + bytes([VERSAO]) + self._corrente + nonce + cifrado

    def decifrar(self, contexto: str, blob: bytes, associado: bytes = b"") -> bytes:
        if len(blob) < _CABECALHO + 16 or not blob.startswith(MAGICA):
            raise ErroCripto("O conteúdo não está no formato cifrado esperado.")
        if blob[len(MAGICA)] != VERSAO:
            raise ErroCripto("Versão desconhecida do formato cifrado.")
        inicio = len(MAGICA) + 1
        id_chave = blob[inicio : inicio + _TAMANHO_ID]
        nonce = blob[inicio + _TAMANHO_ID : _CABECALHO]
        try:
            return self._aead(id_chave, contexto).decrypt(
                nonce, blob[_CABECALHO:], self._aad(contexto, associado)
            )
        except InvalidTag as erro:
            raise ErroCripto(
                "O conteúdo cifrado não confere com a etiqueta de autenticação: foi alterado ou "
                "pertence a outro registro."
            ) from erro
