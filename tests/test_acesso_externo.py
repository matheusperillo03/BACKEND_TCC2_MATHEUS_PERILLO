"""Acesso de fora da máquina: TLS obrigatório e modo de desenvolvimento só local (RNF01, H10).

Depois do piloto de 18/11/2026: o sistema foi alcançado por HTTPS em um nome ``*.ts.net``. Estes
testes fixam que (a) os tokens de desenvolvimento não valem com ``MODO_DEV=0``; (b) o serviço não
inicia com ``MODO_DEV=1`` e endereço público fora da máquina; (c) endereço público fora da máquina
só com ``https``; (d) a API recusa requisição cujo ``Host`` não é local nem o endereço declarado.

Só há dados sintéticos: os nomes de máquina, tokens e chaves são inventados para o teste.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from buscador.api import criar_app
from buscador.config import ErroConfiguracao
from tests.conftest import TOKENS, cabecalhos, criar_config

RAIZ = Path(__file__).resolve().parent.parent
# Nome sintético, no formato dos nomes de máquina de uma VPN privada.
PUBLICO = "piloto-sintetico.exemplo.ts.net"
TOKENS_PUBLICADOS = "dev-analista:ana:analista,dev-delegado:carlos:delegado,dev-admin:root:admin"


def _producao(tmp_path, **extras):
    return criar_config(tmp_path, modo_dev=False, tokens_dev="", **extras)


# ---- (a) token de desenvolvimento com MODO_DEV=0 ---------------------------------------------


def test_h10_token_de_desenvolvimento_e_recusado_com_modo_dev_desligado(tmp_path):
    """Com MODO_DEV=0 nenhum token fixo autentica, nem os publicados nem os dos testes."""
    app = criar_app(_producao(tmp_path))
    assert app.state.tokens == {}
    with TestClient(app, base_url="http://localhost") as cliente:
        assert cliente.get("/health").json() == {"status": "ok", "modo_dev": False}
        fixos = [item.split(":")[0] for item in (TOKENS + "," + TOKENS_PUBLICADOS).split(",")]
        for token in fixos:
            r = cliente.get("/me", headers={"Authorization": f"Bearer {token}"})
            assert r.status_code == 401, token
            assert cliente.get("/casos", headers=cabecalhos(token)).status_code == 401, token
            assert cliente.get("/auth/sessao", headers=cabecalhos(token)).status_code == 401


# ---- (b) MODO_DEV=1 só na própria máquina ------------------------------------------------------


@pytest.mark.parametrize(
    "url", [f"https://{PUBLICO}", f"https://{PUBLICO}:8443", "https://192.0.2.10"]
)
def test_h10_servico_nao_inicia_com_modo_dev_e_endereco_publico_fora_da_maquina(tmp_path, url):
    config = criar_config(tmp_path, modo_dev=True, url_publica=url)
    with pytest.raises(ErroConfiguracao, match="MODO_DEV"):
        config.validar()
    with pytest.raises(ErroConfiguracao, match="MODO_DEV"):
        criar_app(config)


@pytest.mark.parametrize(
    "url", ["", "http://localhost:8090", "http://127.0.0.1:8090", "https://[::1]:8443"]
)
def test_h10_modo_dev_continua_valendo_com_endereco_local(tmp_path, url):
    app = criar_app(criar_config(tmp_path, modo_dev=True, url_publica=url))
    with TestClient(app, base_url="http://127.0.0.1:8100") as cliente:
        r = cliente.get("/me", headers={"Authorization": "Bearer tok-analista"})
        assert r.status_code == 200 and r.json()["usuario"] == "ana"


def test_h10_token_de_desenvolvimento_nao_vale_por_endereco_fora_da_maquina(tmp_path):
    """MODO_DEV=1 exposto por engano (proxy ou VPN apontando para a porta local): a requisição
    chega com o Host de fora e é recusada antes da autenticação."""
    app = criar_app(criar_config(tmp_path, modo_dev=True))
    with TestClient(app, base_url=f"https://{PUBLICO}") as de_fora:
        r = de_fora.get("/me", headers={"Authorization": "Bearer tok-admin"})
        assert r.status_code == 421
        assert "URL_PUBLICA" in r.json()["detail"]
        assert de_fora.get("/health").status_code == 421
        assert de_fora.get("/docs").status_code == 421
    with TestClient(app, base_url="http://localhost:8090") as local:
        assert local.get("/me", headers={"Authorization": "Bearer tok-admin"}).status_code == 200


# ---- (c) fora da máquina, só com TLS -----------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        f"http://{PUBLICO}",  # sem TLS fora da máquina
        "http://192.0.2.10:8090",
        f"ftp://{PUBLICO}",
        PUBLICO,  # sem esquema
        f"https://usuario:senha@{PUBLICO}",
        f"https://{PUBLICO}/caminho",
        f"https://{PUBLICO}?x=1",
        "https://",
    ],
)
def test_rnf01_servico_nao_inicia_com_endereco_publico_sem_tls_ou_mal_formado(tmp_path, url):
    config = _producao(tmp_path, url_publica=url)
    with pytest.raises(ErroConfiguracao, match="URL_PUBLICA"):
        config.validar()
    with pytest.raises(ErroConfiguracao, match="URL_PUBLICA"):
        criar_app(config)


def test_rnf01_endereco_publico_com_https_e_aceito_fora_do_modo_dev(tmp_path):
    app = criar_app(_producao(tmp_path, url_publica=f"https://{PUBLICO.upper()}/"))
    with TestClient(app, base_url=f"https://{PUBLICO}") as cliente:
        assert cliente.get("/health").json() == {"status": "ok", "modo_dev": False}
        assert cliente.get("/docs").status_code == 404


# ---- (d) Host da requisição --------------------------------------------------------------------


def test_rnf01_api_recusa_host_que_nao_e_local_nem_o_endereco_publico(tmp_path):
    # Sem URL_PUBLICA, só a própria máquina.
    app = criar_app(_producao(tmp_path / "local"))
    for base in ("http://localhost", "http://127.0.0.1:8100", "http://[::1]:8100"):
        with TestClient(app, base_url=base) as cliente:
            assert cliente.get("/health").status_code == 200, base
    for base in (f"https://{PUBLICO}", "http://192.0.2.10:8090", "http://localhost.exemplo.net"):
        with TestClient(app, base_url=base) as cliente:
            r = cliente.get("/health")
            assert r.status_code == 421, base
            # A resposta não ecoa o Host recebido.
            assert PUBLICO not in r.text and "192.0.2.10" not in r.text

    # Com URL_PUBLICA, a própria máquina e o nome declarado; qualquer outro nome, não.
    app = criar_app(_producao(tmp_path / "publico", url_publica=f"https://{PUBLICO}"))
    for base, esperado in (
        (f"https://{PUBLICO}", 200),
        (f"https://{PUBLICO}:443", 200),
        ("http://127.0.0.1:8090", 200),
        ("https://outro.exemplo.ts.net", 421),
        (f"https://x.{PUBLICO}", 421),
    ):
        with TestClient(app, base_url=base) as cliente:
            assert cliente.get("/health").status_code == esperado, base


def test_rnf01_api_recusa_requisicao_sem_host_ou_com_host_invalido(tmp_path):
    app = criar_app(_producao(tmp_path))
    with TestClient(app, base_url="http://localhost") as cliente:
        for host in ("", "localhost:abc", "[::1", "localhost, exemplo.net", "a b"):
            r = cliente.get("/health", headers={"Host": host})
            assert r.status_code == 421, host


# ---- Compose e exemplo de configuração ---------------------------------------------------------


def test_rnf01_compose_repassa_o_endereco_publico_e_o_tls_fica_em_arquivo_opcional():
    compose = (RAIZ / "docker-compose.yml").read_text(encoding="utf-8")
    assert "URL_PUBLICA: ${URL_PUBLICA:-}" in compose
    # O arquivo principal continua só em localhost e sem TLS próprio.
    assert '"127.0.0.1:8090:8080"' in compose and "8443" not in compose

    tls = (RAIZ / "docker-compose.tls.yml").read_text(encoding="utf-8")
    # O certificado vem de fora do repositório, por volume só de leitura, e é obrigatório.
    assert "${TLS_DIR_CERTIFICADO:?" in tls and ":/etc/nginx/tls:ro" in tls
    assert "nginx-tls.conf" in tls and ":8443" in tls
    # Com TLS no nginx, a API exige o endereço público declarado.
    assert "${URL_PUBLICA:?" in tls
    assert "BEGIN" not in tls and "PRIVATE KEY" not in tls

    exemplo = (RAIZ / ".env.example").read_text(encoding="utf-8")
    assert "\nURL_PUBLICA=\n" in exemplo and "\nMODO_DEV=0\n" in exemplo

    ignorados = (RAIZ / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "tls/" in ignorados
    assert "tls" in (RAIZ / ".dockerignore").read_text(encoding="utf-8").splitlines()
