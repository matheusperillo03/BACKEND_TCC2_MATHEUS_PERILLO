"""Chave própria do resumo de identificadores (``CHAVE_IDENTIFICADORES``).

O resumo do título eleitoral, que reconhece o mesmo candidato entre eleições, usa uma chave só
dele: não deriva da chave da custódia. Assim, trocar a chave da custódia não desfaz a ligação, e
quem tem uma chave não refaz o que depende da outra. A chave é obrigatória quando a ligação está
em uso (fonte do TSE ligada, ``TSE_DIR_BASE``); sem ela o serviço não inicia.

Chaves e título daqui são sintéticos."""

from __future__ import annotations

import hashlib
import secrets

import pytest

from buscador import cripto
from buscador.api import criar_app
from buscador.conectores import criar_conector
from buscador.conectores.tse_arquivo import CONTEXTO_TITULO
from buscador.config import ErroConfiguracao
from tests.conftest import CHAVE_CUSTODIA, CHAVE_HMAC, CHAVE_IDENTIFICADORES, criar_config

OUTRA_CHAVE = "ab" * 32


def _titulo() -> str:
    return "".join(str(secrets.randbelow(10)) for _ in range(12))


def _raiz_tse(tmp_path):
    raiz = tmp_path / "tse"
    raiz.mkdir()
    return raiz


def test_sem_a_chave_e_com_a_ligacao_em_uso_o_servico_nao_inicia(tmp_path):
    config = criar_config(
        tmp_path, None, tse_dir_base=_raiz_tse(tmp_path), chave_identificadores=""
    )
    with pytest.raises(ErroConfiguracao, match="CHAVE_IDENTIFICADORES é obrigatória"):
        config.validar()
    with pytest.raises(ErroConfiguracao, match="CHAVE_IDENTIFICADORES"):
        criar_app(config)
    # O conector do TSE também não é criado sem a chave (nada de resumo com outra chave).
    with pytest.raises(ErroConfiguracao, match="CHAVE_IDENTIFICADORES"):
        criar_conector("tse_arquivo", config)


def test_sem_a_chave_e_sem_a_ligacao_em_uso_o_servico_inicia(tmp_path):
    config = criar_config(tmp_path, None, tse_dir_base=None, chave_identificadores="")
    config.validar()


def test_chave_malformada_ou_repetida_impede_a_partida(tmp_path):
    raiz = _raiz_tse(tmp_path)
    for valor in ("curta", "zz" * 32, "ab" * 31):
        config = criar_config(tmp_path, None, tse_dir_base=raiz, chave_identificadores=valor)
        with pytest.raises(ErroConfiguracao, match="CHAVE_IDENTIFICADORES"):
            config.validar()
    # Mesmo com a fonte do TSE desligada, uma chave informada precisa ser válida.
    with pytest.raises(ErroConfiguracao, match="CHAVE_IDENTIFICADORES"):
        criar_config(tmp_path, None, chave_identificadores="curta").validar()
    # Chave própria: não pode repetir a da custódia nem a da auditoria.
    for extras in (
        {"chave_identificadores": CHAVE_CUSTODIA},
        {"chave_identificadores": OUTRA_CHAVE, "custodia_chaves_anteriores": OUTRA_CHAVE},
        {"chave_identificadores": OUTRA_CHAVE, "auditoria_chave_hmac": OUTRA_CHAVE},
    ):
        config = criar_config(tmp_path, None, tse_dir_base=raiz, **extras)
        with pytest.raises(ErroConfiguracao, match="CHAVE_IDENTIFICADORES não pode ser igual"):
            config.validar()
    assert CHAVE_HMAC != CHAVE_IDENTIFICADORES != CHAVE_CUSTODIA


def test_resumo_estavel_com_a_mesma_chave_e_diferente_com_outra(tmp_path):
    raiz = _raiz_tse(tmp_path)
    titulo = _titulo()
    um = criar_config(tmp_path, None, tse_dir_base=raiz).resumidor_de_titulo()
    dois = criar_config(tmp_path, None, tse_dir_base=raiz).resumidor_de_titulo()
    outro = criar_config(
        tmp_path, None, tse_dir_base=raiz, chave_identificadores=OUTRA_CHAVE
    ).resumidor_de_titulo()
    resumo = um(titulo)
    assert len(resumo) == 64 and int(resumo, 16) >= 0
    assert resumo == um(titulo) == dois(titulo)
    assert resumo != outro(titulo)
    assert resumo != hashlib.sha256(titulo.encode()).hexdigest()
    assert resumo == cripto.resumo_identificador(
        bytes.fromhex(CHAVE_IDENTIFICADORES), CONTEXTO_TITULO, titulo.encode()
    )


def test_resumo_nao_depende_da_chave_da_custodia(tmp_path):
    raiz = _raiz_tse(tmp_path)
    titulo = _titulo()
    antes = criar_config(tmp_path, None, tse_dir_base=raiz).resumidor_de_titulo()(titulo)
    # Troca da chave da custódia (a anterior fica só para leitura): o resumo não muda.
    depois = criar_config(
        tmp_path,
        None,
        tse_dir_base=raiz,
        custodia_chave="cd" * 32,
        custodia_chaves_anteriores=CHAVE_CUSTODIA,
    ).resumidor_de_titulo()(titulo)
    assert antes == depois
    # E a chave da custódia sozinha não refaz o resumo.
    assert antes != cripto.resumo_identificador(
        bytes.fromhex(CHAVE_CUSTODIA), CONTEXTO_TITULO, titulo.encode()
    )
    assert not hasattr(cripto.Cofre, "resumo")
