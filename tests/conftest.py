from __future__ import annotations

import shutil
from pathlib import Path
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from buscador.api import criar_app
from buscador.conectores.pncp import TransporteSimulado
from buscador.config import Configuracao
from buscador.custodia import Custodia
from buscador.db import criar_engine, criar_esquema, criar_fabrica_sessoes

FIXTURES = Path(__file__).parent / "fixtures"
FIXTURES_CNPJ = FIXTURES / "cnpj"
FIXTURES_PNCP = FIXTURES / "pncp"

TOKENS = "tok-analista:ana:analista,tok-delegado:carlos:delegado,tok-admin:root:admin"
# Chave sintética, só para os testes (a cadeia de auditoria exige uma chave de HMAC).
CHAVE_HMAC = "chave-sintetica-de-teste-0123456789abcdef"
CHAVE = CHAVE_HMAC.encode()
# Chave mestra sintética da criptografia em repouso (32 bytes em hexadecimal), só para os testes.
CHAVE_CUSTODIA = "00112233445566778899aabbccddeeff" * 2
# Nome da pasta das fixtures do CNPJ dentro da raiz de ingestão dos testes.
DIR_CNPJ = "cnpj"


@pytest.fixture
def raiz_cnpj(tmp_path: Path) -> Path:
    """Raiz de ingestão (CNPJ_DIR_BASE) dos testes, com uma cópia das fixtures em ``cnpj/``."""
    raiz = tmp_path / "ingestao"
    shutil.copytree(FIXTURES_CNPJ, raiz / DIR_CNPJ)
    return raiz


def criar_config(tmp_path: Path, raiz_cnpj: Path | None = None, **extras) -> Configuracao:
    valores = {
        "database_url": f"sqlite:///{tmp_path / 'teste.db'}",
        "dados_dir": tmp_path / "dados",
        # Os tokens fixos só existem no modo de desenvolvimento (H23); os testes de login, MFA
        # e sessão de usuários reais estão em test_horizonte3.py.
        "tokens_dev": TOKENS,
        "modo_dev": True,
        "auditoria_chave_hmac": CHAVE_HMAC,
        "custodia_chave": CHAVE_CUSTODIA,
        "cnpj_dir_base": raiz_cnpj,
        "pncp_espera_retentativa_segundos": 0,
        "coleta_espera_retentativa_segundos": 0,
        # O agendador do monitoramento não roda em segundo plano nos testes: eles chamam
        # ``Agendador.ciclo`` diretamente.
        "monitoramento_agendador_ativo": False,
        "_env_file": None,
    }
    valores.update(extras)
    return Configuracao(**valores)


@pytest.fixture
def config(tmp_path: Path, raiz_cnpj: Path) -> Configuracao:
    return criar_config(tmp_path, raiz_cnpj)


@pytest.fixture
def engine(config: Configuracao):
    engine = criar_engine(config.database_url)
    criar_esquema(engine)
    return engine


@pytest.fixture
def sessao(engine):
    fabrica = criar_fabrica_sessoes(engine)
    s = fabrica()
    yield s
    s.close()


@pytest.fixture
def custodia(config: Configuracao) -> Custodia:
    return Custodia(config.dados_dir)


@pytest.fixture
def transporte_pncp() -> TransporteSimulado:
    return TransporteSimulado.de_diretorio(FIXTURES_PNCP)


@pytest.fixture
def app(config: Configuracao, transporte_pncp: TransporteSimulado):
    return criar_app(config, extras_conectores={"pncp": {"transporte": transporte_pncp}})


@pytest.fixture
def cliente(app) -> TestClient:
    with TestClient(app) as c:
        yield c


def cabecalhos(
    token: str = "tok-analista", justificativa: str | None = "Teste automatizado"
) -> dict:
    h = {"Authorization": f"Bearer {token}"}
    if justificativa is not None:
        h["X-Justificativa"] = quote(justificativa)
    return h


# Finalidade e prazo de retenção sintéticos, obrigatórios em todo caso (RNF04, H17).
FINALIDADE = "Teste automatizado com dados sintéticos"
RETENCAO = "2099-12-31"


def corpo_caso(titulo: str = "Caso teste", **campos) -> dict:
    """Corpo válido de ``POST /casos``; ``campos`` sobrescreve ou acrescenta."""
    return {"titulo": titulo, "finalidade": FINALIDADE, "retencao_ate": RETENCAO, **campos}


def incluir_equipe(cliente: TestClient, caso_id: str, token: str = "tok-analista") -> None:
    """Acesso por caso (H23): quem cria o caso é o responsável; os demais usuários dos testes
    entram como membros (o delegado como investigador e o admin como leitor)."""
    for usuario, papel in (("carlos", "investigador"), ("root", "leitor")):
        r = cliente.post(
            f"/casos/{caso_id}/membros",
            json={"usuario": usuario, "papel": papel},
            headers=cabecalhos(token),
        )
        assert r.status_code == 200, r.text


@pytest.fixture
def caso_id(cliente: TestClient) -> str:
    r = cliente.post(
        "/casos",
        json=corpo_caso(descricao="d", numero_procedimento="P-1"),
        headers=cabecalhos(),
    )
    assert r.status_code == 201, r.text
    incluir_equipe(cliente, r.json()["id"])
    return r.json()["id"]


@pytest.fixture
def caso_carregado(cliente: TestClient, caso_id: str) -> str:
    """Caso com as duas coletas (CNPJ e PNCP) feitas."""
    r = cliente.post(
        f"/casos/{caso_id}/coletas",
        json={"fonte": "cnpj_arquivo", "parametros": {"diretorio": DIR_CNPJ}},
        headers=cabecalhos(),
    )
    assert r.status_code == 200, r.text
    r = cliente.post(
        f"/casos/{caso_id}/coletas",
        json={"fonte": "pncp", "parametros": {"dataInicial": "20260101", "dataFinal": "20261231"}},
        headers=cabecalhos(),
    )
    assert r.status_code == 200, r.text
    return caso_id


def entidade_por_identificador(
    cliente: TestClient, caso_id: str, identificador: str, tipo: str
) -> dict:
    r = cliente.get(
        f"/casos/{caso_id}/entidades",
        params={"q": identificador, "tipo": tipo},
        headers=cabecalhos(),
    )
    assert r.status_code == 200, r.text
    candidatos = [e for e in r.json() if e["identificador"] == identificador]
    assert candidatos, f"{tipo} {identificador} não encontrado"
    return candidatos[0]
