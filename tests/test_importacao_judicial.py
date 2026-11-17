"""RF10: importação sob ordem judicial. Módulo separado, desligado por padrão, que exige a decisão
judicial antes de importar, valida pelo leiaute da IN BCB nº 636/2025, segrega o dado e audita cada
acesso. Só dados SINTÉTICOS: os arquivos são gerados aqui, com CPF sorteado a cada execução."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from buscador.api import criar_app
from buscador.config import ErroConfiguracao
from buscador.importacao_judicial import leiaute
from tests.conftest import (
    CHAVE_CUSTODIA,
    cabecalhos,
    corpo_caso,
    criar_config,
    incluir_equipe,
)
from tests.sinteticos import cpf_sintetico, mascarado

CHAVE_SIGILO = "ffeeddccbbaa99887766554433221100" * 2
BANCO, AGENCIA, CONTA = "999", "00001", "445566"
PASTA = "caso-sintetico"
DELEGADO = "tok-delegado"


def _extrato(chave: int, data: str, conta: str = CONTA, valor: str = "10000", **extras) -> bytes:
    campos = {
        "CODIGO_CHAVE_EXTRATO": str(chave),
        "COD_COMP_INSTITUIÇÃO": BANCO,
        "NUMERO_AGÊNCIA": AGENCIA,
        "NUMERO_CONTA": conta,
        "TIPO_CONTA": "1",
        "DATA_LANCAMENTO": data,
        "DESCRICAO_LANCAMENTO": "TRANSFERENCIA SINTETICA",
        "VALOR_LANCAMENTO": valor,
        "NATUREZA_LANCAMENTO": "C",
        "HORARIO_LANCAMENTO": "152032",
        **extras,
    }
    return leiaute.montar_linha("EXTRATO", **campos)


@pytest.fixture
def cpf_titular() -> str:
    return cpf_sintetico()


@pytest.fixture
def raiz_judicial(tmp_path: Path, cpf_titular: str) -> Path:
    """Arquivos sintéticos no leiaute, com linhas válidas, fora do escopo e fora do leiaute."""
    raiz = tmp_path / "judicial"
    pasta = raiz / PASTA
    pasta.mkdir(parents=True)
    (pasta / "SINTETICO.txt").write_text("dados sintéticos", encoding="utf-8")
    instituicao = {"COD_COMP_INSTITUIÇÃO": BANCO, "NUMERO_AGÊNCIA": AGENCIA}
    (pasta / "AGENCIAS.txt").write_bytes(
        leiaute.montar_linha(
            "AGENCIAS", **instituicao, **{"NOME_AGÊNCIA": "AGENCIA SINTETICA"}, CODIGO_UF="53"
        )
    )
    (pasta / "CONTAS.txt").write_bytes(
        leiaute.montar_linha(
            "CONTAS",
            **instituicao,
            NUMERO_CONTA=CONTA,
            TIPO_CONTA="1",
            DATA_ABERTURA_CONTA="01022020",
            DATA_ENCERRAMENTO_CONTA="31129999",
            MOVIMENTACAO_CONTA="1",
            SALDO_INICIAL_CONTA="1002303",
        )
        # Contraparte declarada pelo leiaute (código 3): aceita, sem quarentena.
        + leiaute.montar_linha(
            "CONTAS", **instituicao, NUMERO_CONTA="777777", MOVIMENTACAO_CONTA="3"
        )
        # Conta que a decisão não autorizou: quarentena.
        + leiaute.montar_linha(
            "CONTAS", **instituicao, NUMERO_CONTA="888888", MOVIMENTACAO_CONTA="1"
        )
    )
    (pasta / "TITULARES.txt").write_bytes(
        leiaute.montar_linha(
            "TITULARES",
            **instituicao,
            NUMERO_CONTA=CONTA,
            TIPO_TITULAR="T",
            PESSOA_INVESTIGADA="1",
            TIPO_PESSOA_TITULAR="1",
            CPF_CNPJ_TITULAR=cpf_titular,
            NOME_TITULAR="TITULAR SINTETICO DE EXEMPLO",
        )
    )
    (pasta / "EXTRATO.txt").write_bytes(
        _extrato(1, "10022026", CHAVE_PIX_TRANSACAO=cpf_titular, TIPO_CHAVE_PIX_TRANSACAO="1")
        + _extrato(2, "30062026")  # último dia do período: dentro
        + _extrato(3, "01072026")  # um dia depois do período: quarentena
        + _extrato(4, "10022026", conta="888888")  # conta não autorizada: quarentena
        + _extrato(5, "31022026")  # data que não existe: rejeitada
        + _extrato(6, "10022026", valor="100,00")  # valor com vírgula: rejeitada
        + b"linha\tcom\tpoucos\tcampos\r\n"  # número de campos errado: rejeitada
        + _extrato(7, "10022026").replace(b"SINTETICA", "SINTÉTICA".encode("latin-1"))  # não ASCII
    )
    (pasta / "ORIGEM-DESTINO.txt").write_bytes(
        leiaute.montar_linha(
            "ORIGEM_DESTINO",
            CODIGO_CHAVE_OD="1",
            CODIGO_CHAVE_EXTRATO="1",
            VALOR_TRANSACAO="10000",
            TIPO_PESSOA_OD="1",
            CPF_CNPJ_OD=cpf_titular,
            NOME_PESSOA_OD="ORDENANTE SINTETICO",
        )
        + leiaute.montar_linha(
            "ORIGEM_DESTINO", CODIGO_CHAVE_OD="2", CODIGO_CHAVE_EXTRATO="3", VALOR_TRANSACAO="1"
        )
        + leiaute.montar_linha(
            "ORIGEM_DESTINO", CODIGO_CHAVE_OD="3", CODIGO_CHAVE_EXTRATO="99", VALOR_TRANSACAO="1"
        )
    )
    return raiz


def _app(tmp_path: Path, raiz: Path | None, **extras):
    return criar_app(
        criar_config(
            tmp_path,
            importacao_judicial_ativa=True,
            importacao_judicial_chave=CHAVE_SIGILO,
            importacao_judicial_dir_base=raiz,
            **extras,
        )
    )


@pytest.fixture
def cliente_rf10(tmp_path: Path, raiz_judicial: Path):
    with TestClient(_app(tmp_path, raiz_judicial)) as cliente:
        yield cliente


def _caso(cliente: TestClient) -> str:
    r = cliente.post("/casos", json=corpo_caso("Caso RF10"), headers=cabecalhos())
    assert r.status_code == 201, r.text
    incluir_equipe(cliente, r.json()["id"])
    return r.json()["id"]


def _corpo_decisao(**campos) -> dict:
    return {
        "numero_processo": "0000000-00.2026.8.07.0001",
        "numero_decisao": "Decisão sintética ID 123456",
        "juizo": "1ª Vara Criminal de Exemplópolis",
        "data_decisao": "2026-10-01",
        "periodo_inicio": "2026-01-01",
        "periodo_fim": "2026-06-30",
        "contas": [{"banco": BANCO, "agencia": AGENCIA, "conta": CONTA}],
        "descricao_escopo": "Afastamento do sigilo bancário da conta sintética do caso",
        **campos,
    }


def _decisao(cliente: TestClient, caso: str, **campos) -> dict:
    r = cliente.post(
        f"/casos/{caso}/importacao-judicial/decisoes",
        json=_corpo_decisao(**campos),
        headers=cabecalhos(DELEGADO),
    )
    assert r.status_code == 201, r.text
    return r.json()


def _importar(cliente: TestClient, caso: str, decisao_id: str, diretorio: str = PASTA):
    return cliente.post(
        f"/casos/{caso}/importacao-judicial/decisoes/{decisao_id}/importacoes",
        json={"diretorio": diretorio, "origem": "midia", "descricao_origem": "Mídia sintética"},
        headers=cabecalhos(DELEGADO),
    )


def _auditoria(cliente: TestClient, caso: str) -> list[dict]:
    r = cliente.get("/auditoria", params={"caso_id": caso}, headers=cabecalhos("tok-admin"))
    assert r.status_code == 200, r.text
    return [e for e in r.json() if e["acao"].startswith("importacao_judicial.")]


# ==== Desligado por padrão ======================================================================


def test_rf10_modulo_desligado_por_padrao_so_responde_o_estado(cliente, caso_id, config):
    assert config.importacao_judicial_ativa is False
    r = cliente.get("/importacao-judicial/estado", headers={"Authorization": "Bearer tok-analista"})
    assert r.status_code == 200 and r.json() == {
        "ativa": False,
        "perfis_autorizados": ["delegado"],
        "leiaute": "Instrução Normativa BCB nº 636/2025, Anexo I",
        "arquivos": ["AGENCIAS", "CONTAS", "TITULARES", "EXTRATO", "ORIGEM_DESTINO"],
        "origens": ["simba", "oficio", "midia"],
        "somente_sintetico": True,
    }
    assert cliente.get("/importacao-judicial/estado").status_code == 401
    base = f"/casos/{caso_id}/importacao-judicial"
    for metodo, rota, corpo in (
        ("post", f"{base}/decisoes", _corpo_decisao()),
        ("get", f"{base}/decisoes", None),
        ("post", f"{base}/decisoes/x/importacoes", {"diretorio": PASTA, "origem": "midia"}),
        ("get", f"{base}/importacoes", None),
        ("get", f"{base}/registros", None),
    ):
        r = cliente.request(metodo, rota, json=corpo, headers=cabecalhos(DELEGADO))
        assert r.status_code == 404 and "desligado" in r.json()["detail"], rota
    # Desligado, nada do módulo existe em disco.
    assert not (Path(config.dados_dir) / "sigiloso").exists()


def test_rf10_configuracao_exige_chave_propria_e_base_separada(tmp_path):
    ligado = {"importacao_judicial_ativa": True}
    for extras in (
        {},  # sem chave
        {"importacao_judicial_chave": "curta"},
        {"importacao_judicial_chave": CHAVE_CUSTODIA},  # a mesma chave da custódia
        {"importacao_judicial_chave": CHAVE_SIGILO, "importacao_judicial_perfis": "chefe"},
        {
            "importacao_judicial_chave": CHAVE_SIGILO,
            "importacao_judicial_database_url": f"sqlite:///{tmp_path / 'teste.db'}",
        },  # a mesma base do dado aberto
    ):
        with pytest.raises(ErroConfiguracao):
            criar_config(tmp_path, **ligado, **extras).validar()
    criar_config(tmp_path, **ligado, importacao_judicial_chave=CHAVE_SIGILO).validar()
    # Desligado, a chave não é exigida.
    criar_config(tmp_path).validar()


# ==== Decisão judicial antes de importar ========================================================


def test_rf10_decisao_judicial_e_obrigatoria_antes_de_importar(cliente_rf10):
    caso = _caso(cliente_rf10)
    estado = cliente_rf10.get(
        "/importacao-judicial/estado", headers={"Authorization": "Bearer tok-analista"}
    ).json()
    assert estado["ativa"] is True
    # Sem decisão registrada não há o que importar: a rota só existe sob uma decisão.
    assert _importar(cliente_rf10, caso, "inexistente").status_code == 404
    lista = cliente_rf10.get(
        f"/casos/{caso}/importacao-judicial/decisoes", headers=cabecalhos(DELEGADO)
    )
    assert lista.status_code == 200 and lista.json() == []
    # Número, juízo, data e escopo são obrigatórios.
    for falta in ("numero_processo", "numero_decisao", "juizo", "data_decisao", "contas"):
        corpo = _corpo_decisao()
        del corpo[falta]
        r = cliente_rf10.post(
            f"/casos/{caso}/importacao-judicial/decisoes", json=corpo, headers=cabecalhos(DELEGADO)
        )
        assert r.status_code == 422, falta
    for invalido in (
        {"contas": []},
        {"descricao_escopo": "   "},
        {"data_decisao": "01/10/2026"},
        {"periodo_inicio": "2026-07-01"},  # depois do fim
        {"investigados": ["123"]},
    ):
        r = cliente_rf10.post(
            f"/casos/{caso}/importacao-judicial/decisoes",
            json=_corpo_decisao(**invalido),
            headers=cabecalhos(DELEGADO),
        )
        assert r.status_code == 422, invalido
    decisao = _decisao(cliente_rf10, caso)
    assert decisao["registrado_por"] == "carlos" and decisao["importacoes"] == 0
    assert decisao["marca"] == (
        "sigiloso: ordem judicial nº Decisão sintética ID 123456 "
        "(processo 0000000-00.2026.8.07.0001)"
    )
    assert decisao["contas"] == [{"banco": BANCO, "agencia": AGENCIA, "conta": CONTA}]
    r = _importar(cliente_rf10, caso, decisao["id"])
    assert r.status_code == 200, r.text
    # A decisão de um caso não serve para importar em outro.
    outro = _caso(cliente_rf10)
    assert _importar(cliente_rf10, outro, decisao["id"]).status_code == 404


def test_rf10_so_perfil_autorizado_registra_a_decisao_e_o_cpf_sai_descaracterizado(
    cliente_rf10, cpf_titular
):
    caso = _caso(cliente_rf10)
    # O analista é responsável pelo caso, mas o perfil não é autorizado: 403, com auditoria.
    r = cliente_rf10.post(
        f"/casos/{caso}/importacao-judicial/decisoes",
        json=_corpo_decisao(),
        headers=cabecalhos("tok-analista"),
    )
    assert r.status_code == 403 and "delegado" in r.json()["detail"]
    assert [e["acao"] for e in _auditoria(cliente_rf10, caso)] == [
        "importacao_judicial.perfil_negado"
    ]
    # Quem não é membro do caso não registra nem lê.
    alheio = cliente_rf10.post("/casos", json=corpo_caso("Alheio"), headers=cabecalhos()).json()
    r = cliente_rf10.get(
        f"/casos/{alheio['id']}/importacao-judicial/registros", headers=cabecalhos(DELEGADO)
    )
    assert r.status_code == 403
    # O admin é só leitor no caso: não lê o dado sigiloso (exige investigador).
    r = cliente_rf10.get(
        f"/casos/{caso}/importacao-judicial/registros", headers=cabecalhos("tok-admin")
    )
    assert r.status_code == 403
    decisao = _decisao(cliente_rf10, caso, investigados=[cpf_titular, "90.000.001/0001-29"])
    assert decisao["investigados"] == [mascarado(cpf_titular), "90000001000129"]
    assert cpf_titular not in json.dumps(decisao)


# ==== Validação pelo leiaute e escopo ===========================================================


def test_rf10_leiaute_e_o_do_anexo_i_da_in_bcb_636():
    assert {t: len(c) for t, c in leiaute.CAMPOS.items()} == {
        "AGENCIAS": 14,
        "CONTAS": 12,
        "TITULARES": 28,
        "EXTRATO": 27,
        "ORIGEM_DESTINO": 28,
    }
    assert leiaute.CAMPOS["EXTRATO"][5] == ("DATA_LANCAMENTO", "D", 8)
    assert leiaute.CAMPOS["TITULARES"][7] == ("CPF_CNPJ_TITULAR", "T", 14)
    assert leiaute.CAMPOS["ORIGEM_DESTINO"][9] == ("CPF_CNPJ_OD", "T", 14)
    assert "636/2025" in leiaute.NORMA
    assert leiaute.classificar_arquivo("ORIGEM-DESTINO.txt") == "ORIGEM_DESTINO"
    assert leiaute.classificar_arquivo("origem_destino.TXT") == "ORIGEM_DESTINO"
    assert leiaute.classificar_arquivo("EXTRATO.csv") is None
    assert leiaute.classificar_arquivo("POSICAO.txt") is None
    # Art. 3º: data ddmmaaaa, valor em centavos sem separador, TAB entre colunas.
    lido = leiaute.interpretar_linha(
        "EXTRATO", 1, _extrato(1, "05022008", valor="1002303").strip(b"\r\n")
    )
    assert lido["DATA_LANCAMENTO"] == "2008-02-05" and lido["VALOR_LANCAMENTO"] == 10023.03
    assert (
        leiaute.interpretar_linha(
            "EXTRATO", 1, _extrato(1, "05022008", valor="000").strip(b"\r\n")
        )["VALOR_LANCAMENTO"]
        == 0.0
    )
    for linha, campo in (
        (_extrato(1, "05/02/08"), "DATA_LANCAMENTO"),
        (_extrato(1, "05022008", NATUREZA_SALDO="X"), "NATUREZA_SALDO"),
        (_extrato(1, "05022008", HORARIO_LANCAMENTO="256000"), "HORARIO_LANCAMENTO"),
        (_extrato(1, "05022008", conta="1" * 21), "NUMERO_CONTA"),  # acima do tamanho
        (_extrato(1, "05022008", TIPO_CONTA="A"), "TIPO_CONTA"),  # numérico com letra
        (_extrato(1, "05022008", valor=""), "VALOR_LANCAMENTO"),  # obrigatório vazio
    ):
        rejeicao = leiaute.interpretar_linha("EXTRATO", 9, linha.strip(b"\r\n"))
        assert isinstance(rejeicao, leiaute.Rejeicao) and rejeicao.campo == campo, campo
        assert rejeicao.linha == 9
    with pytest.raises(ValueError):
        leiaute.montar_linha("EXTRATO", CAMPO_INEXISTENTE="1")


def test_rf10_importacao_valida_rejeita_e_poe_em_quarentena_o_que_esta_fora_do_escopo(
    cliente_rf10, cpf_titular
):
    caso = _caso(cliente_rf10)
    decisao = _decisao(cliente_rf10, caso)
    r = _importar(cliente_rf10, caso, decisao["id"])
    assert r.status_code == 200, r.text
    importacao = r.json()
    assert importacao["marca"] == decisao["marca"] and importacao["origem"] == "midia"
    assert importacao["importado_por"] == "carlos"
    por_tipo = {a["tipo"]: a for a in importacao["arquivos"]}
    assert list(por_tipo) == ["AGENCIAS", "CONTAS", "TITULARES", "EXTRATO", "ORIGEM_DESTINO"]
    contagens = {
        t: (a["linhas"], a["aceitas"], a["rejeitadas"], a["em_quarentena"])
        for t, a in por_tipo.items()
    }
    assert contagens == {
        "AGENCIAS": (1, 1, 0, 0),
        "CONTAS": (3, 3, 0, 1),
        "TITULARES": (1, 1, 0, 0),
        # 8 linhas: 4 aceitas (2 delas em quarentena: fora do período e conta não autorizada)
        # e 4 rejeitadas (data, valor, número de campos e caractere não ASCII).
        "EXTRATO": (8, 4, 4, 2),
        # 3 linhas: uma ligada a lançamento válido, uma a lançamento em quarentena e uma órfã.
        "ORIGEM_DESTINO": (3, 3, 0, 2),
    }
    assert importacao["totais"] == {
        "linhas": 16,
        "aceitas": 12,
        "rejeitadas": 4,
        "em_quarentena": 5,
    }
    rejeicoes = por_tipo["EXTRATO"]["rejeicoes"]
    assert [(x["linha"], x["campo"]) for x in rejeicoes] == [
        (5, "DATA_LANCAMENTO"),
        (6, "VALOR_LANCAMENTO"),
        (7, ""),
        (8, ""),
    ]
    assert "ddmmaaaa" in rejeicoes[0]["motivo"] and "ASCII" in rejeicoes[3]["motivo"]
    assert "campo(s) separados por TAB" in rejeicoes[2]["motivo"]
    # Custódia do arquivo recebido: SHA-256 dos bytes originais.
    assert all(len(a["sha256"]) == 64 and a["tamanho_bytes"] > 0 for a in por_tipo.values())
    assert cpf_titular not in json.dumps(importacao)

    def registros(**filtros) -> dict:
        r = cliente_rf10.get(
            f"/casos/{caso}/importacao-judicial/registros",
            params=filtros,
            headers=cabecalhos(DELEGADO),
        )
        assert r.status_code == 200, r.text
        return r.json()

    todos = registros(limite=200)
    assert todos["total"] == 12 and len(todos["itens"]) == 12
    assert all(i["marca"] == decisao["marca"] for i in todos["itens"])
    quarentena = registros(quarentena="true")
    assert quarentena["total"] == 5
    assert sorted((i["tipo"], i["linha"], i["motivo_quarentena"]) for i in quarentena["itens"]) == [
        ("CONTAS", 3, "conta fora das autorizadas na decisão"),
        ("EXTRATO", 3, "lançamento fora do período autorizado na decisão"),
        ("EXTRATO", 4, "conta fora das autorizadas na decisão"),
        ("ORIGEM_DESTINO", 2, "o lançamento de origem está em quarentena"),
        ("ORIGEM_DESTINO", 3, "lançamento de origem não encontrado no EXTRATO desta importação"),
    ]
    extrato = registros(tipo="EXTRATO", quarentena="false")
    assert extrato["total"] == 2
    primeiro = extrato["itens"][0]["campos"]
    assert primeiro["DATA_LANCAMENTO"] == "2026-02-10" and primeiro["VALOR_LANCAMENTO"] == 100.0
    assert primeiro["NATUREZA_LANCAMENTO"] == "C" and primeiro["NUMERO_CONTA"] == CONTA
    # Limite do período: 30/06/2026 (último dia autorizado) está dentro.
    assert extrato["itens"][1]["campos"]["DATA_LANCAMENTO"] == "2026-06-30"
    # CPF sempre descaracterizado na leitura: titular, origem-destino e chave Pix.
    tudo = json.dumps(todos)
    assert cpf_titular not in tudo and tudo.count(mascarado(cpf_titular)) == 3
    titular = registros(tipo="TITULARES")["itens"][0]["campos"]
    assert titular["CPF_CNPJ_TITULAR"] == mascarado(cpf_titular)
    assert registros(limite=1, deslocamento=11)["itens"][0]["tipo"] == "TITULARES"
    r = cliente_rf10.get(
        f"/casos/{caso}/importacao-judicial/registros",
        params={"tipo": "POSICAO"},
        headers=cabecalhos(DELEGADO),
    )
    assert r.status_code == 422
    lista = cliente_rf10.get(
        f"/casos/{caso}/importacao-judicial/importacoes", headers=cabecalhos(DELEGADO)
    ).json()
    assert [i["id"] for i in lista] == [importacao["id"]]
    decisoes = cliente_rf10.get(
        f"/casos/{caso}/importacao-judicial/decisoes", headers=cabecalhos(DELEGADO)
    ).json()
    assert decisoes[0]["importacoes"] == 1


def test_rf10_so_dados_sinteticos_e_pasta_confinada_a_raiz(cliente_rf10, raiz_judicial, tmp_path):
    caso = _caso(cliente_rf10)
    decisao = _decisao(cliente_rf10, caso)
    real = raiz_judicial / "sem-marcador"
    real.mkdir()
    (real / "EXTRATO.txt").write_bytes(_extrato(1, "10022026"))
    r = _importar(cliente_rf10, caso, decisao["id"], "sem-marcador")
    assert r.status_code == 422 and "SINTETICO" in r.json()["detail"]
    vazia = raiz_judicial / "vazia"
    vazia.mkdir()
    (vazia / "SINTETICO").write_text("", encoding="utf-8")
    (vazia / "POSICAO.txt").write_text("x", encoding="utf-8")
    r = _importar(cliente_rf10, caso, decisao["id"], "vazia")
    assert r.status_code == 422 and "Nenhum arquivo do leiaute" in r.json()["detail"]
    fora = tmp_path / "fora"
    fora.mkdir()
    for caminho in ("../fora", str(fora), "nao-existe"):
        r = _importar(cliente_rf10, caso, decisao["id"], caminho)
        assert r.status_code == 422 and str(tmp_path) not in r.json()["detail"], caminho
    r = cliente_rf10.post(
        f"/casos/{caso}/importacao-judicial/decisoes/{decisao['id']}/importacoes",
        json={"diretorio": PASTA, "origem": "pendrive"},
        headers=cabecalhos(DELEGADO),
    )
    assert r.status_code == 422
    # Nenhuma importação recusada deixou registro; as recusas estão na auditoria.
    lidos = cliente_rf10.get(
        f"/casos/{caso}/importacao-judicial/registros", headers=cabecalhos(DELEGADO)
    ).json()
    assert lidos == {"total": 0, "itens": []}
    recusas = [e for e in _auditoria(cliente_rf10, caso) if e["acao"].endswith("recusada")]
    assert len(recusas) == 5
    # Sem a raiz configurada, nada é importado.
    with TestClient(_app(tmp_path / "sem-raiz", None)) as sem_raiz:
        caso2 = _caso(sem_raiz)
        d2 = _decisao(sem_raiz, caso2)
        r = _importar(sem_raiz, caso2, d2["id"])
        assert r.status_code == 422 and "IMPORTACAO_JUDICIAL_DIR_BASE" in r.json()["detail"]


# ==== Segregação e auditoria ====================================================================


def test_rf10_dado_fica_em_base_e_chave_separadas_e_fora_do_dado_aberto(
    cliente_rf10, tmp_path, cpf_titular
):
    caso = _caso(cliente_rf10)
    decisao = _decisao(cliente_rf10, caso, investigados=[cpf_titular])
    assert _importar(cliente_rf10, caso, decisao["id"]).status_code == 200
    aberta = tmp_path / "teste.db"
    sigilosa = tmp_path / "dados" / "sigiloso" / "importacao_judicial.db"
    assert sigilosa.is_file()

    def tabelas(caminho: Path) -> set[str]:
        with sqlite3.connect(caminho) as conexao:
            return {
                n for (n,) in conexao.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }

    assert tabelas(sigilosa) == {"ij_decisoes", "ij_importacoes", "ij_arquivos", "ij_registros"}
    assert not {t for t in tabelas(aberta) if t.startswith("ij_")}
    # Nada vira entidade, vínculo, alerta, captura, relatório ou exportação do dado aberto.
    h = cabecalhos(DELEGADO)
    assert cliente_rf10.get(f"/casos/{caso}/entidades", headers=h).json() == []
    detalhe = cliente_rf10.get(f"/casos/{caso}", headers=h).json()
    assert detalhe["contagens"] == {**detalhe["contagens"], "entidades": 0, "capturas": 0}
    relatorio = cliente_rf10.get(f"/casos/{caso}/relatorio", params={"formato": "md"}, headers=h)
    assert relatorio.status_code == 200
    for texto in ("TITULAR SINTETICO", "TRANSFERENCIA SINTETICA", CONTA, "0000000-00.2026"):
        assert texto not in relatorio.text
    # Em disco, nada em claro: nem o banco sigiloso nem os arquivos recebidos trazem o CPF, o
    # nome do titular, a conta ou o histórico; e a base do dado aberto também não.
    cifrados = list((tmp_path / "dados" / "sigiloso" / "arquivos").glob("*.gcm"))
    assert len(cifrados) == 5
    em_disco = (
        sigilosa.read_bytes() + aberta.read_bytes() + b"".join(a.read_bytes() for a in cifrados)
    )
    for segredo in (cpf_titular, "TITULAR SINTETICO", "TRANSFERENCIA SINTETICA", "ORDENANTE"):
        assert segredo.encode() not in em_disco, segredo
    assert not list((tmp_path / "dados" / "capturas").rglob("*.gcm"))
    # A chave é própria: com a chave da custódia, o conteúdo não decifra.
    from buscador import cripto

    blob = cifrados[0].read_bytes()
    digest = cifrados[0].stem.encode("ascii")
    custodia = cripto.Cofre(bytes.fromhex(CHAVE_CUSTODIA))
    with pytest.raises(cripto.ErroCripto):
        custodia.decifrar("importacao-judicial/arquivo", blob, digest)
    original = cripto.Cofre(bytes.fromhex(CHAVE_SIGILO)).decifrar(
        "importacao-judicial/arquivo", blob, digest
    )
    import hashlib

    assert hashlib.sha256(original).hexdigest() == cifrados[0].stem


def test_rnf02_cada_acesso_ao_dado_sigiloso_entra_na_auditoria(cliente_rf10, cpf_titular):
    caso = _caso(cliente_rf10)
    decisao = _decisao(cliente_rf10, caso, investigados=[cpf_titular])
    importacao = _importar(cliente_rf10, caso, decisao["id"]).json()
    base = f"/casos/{caso}/importacao-judicial"
    h = cabecalhos(DELEGADO, "Conferir lançamentos do inquérito sintético")
    cliente_rf10.get(f"{base}/decisoes", headers=h)
    cliente_rf10.get(f"{base}/importacoes", headers=h)
    cliente_rf10.get(f"{base}/registros", params={"tipo": "EXTRATO"}, headers=h)
    cliente_rf10.get(f"{base}/registros", params={"quarentena": "true"}, headers=h)
    entradas = _auditoria(cliente_rf10, caso)
    assert [e["acao"].removeprefix("importacao_judicial.") for e in entradas] == [
        "decisao.registrar",
        "importar",
        "decisao.listar",
        "importacao.listar",
        "registro.ler",
        "registro.ler",
    ]
    assert all(e["usuario"] == "carlos" and e["justificativa"] for e in entradas)
    registro = entradas[0]["detalhes"]
    assert registro["numero_decisao"] == "Decisão sintética ID 123456"
    assert registro["juizo"] == "1ª Vara Criminal de Exemplópolis"
    assert (registro["contas_autorizadas"], registro["investigados"]) == (1, 1)
    assert entradas[1]["detalhes"]["importacao_id"] == importacao["id"]
    assert entradas[1]["detalhes"]["totais"] == importacao["totais"]
    assert len(entradas[1]["detalhes"]["arquivos"]) == 5
    leitura = entradas[4]
    assert leitura["justificativa"] == "Conferir lançamentos do inquérito sintético"
    assert leitura["detalhes"]["tipo"] == "EXTRATO" and leitura["detalhes"]["registros_lidos"] == 4
    assert leitura["detalhes"]["decisoes_lidas"] == [decisao["id"]]
    assert entradas[5]["detalhes"]["registros_lidos"] == 5
    # A trilha não leva conta, documento nem conteúdo de registro; e a cadeia continua íntegra.
    trilha = json.dumps(entradas)
    for segredo in (cpf_titular, mascarado(cpf_titular), CONTA, "TITULAR SINTETICO"):
        assert segredo not in trilha
    verificacao = cliente_rf10.get("/auditoria/verificar", headers=cabecalhos("tok-admin")).json()
    assert verificacao["integra"] is True
    # Sem justificativa, não há leitura (nem entrada nova).
    r = cliente_rf10.get(f"{base}/registros", headers=cabecalhos(DELEGADO, None))
    assert r.status_code == 400 and len(_auditoria(cliente_rf10, caso)) == 6


def test_rf10_caso_com_retencao_vencida_nao_le_nem_importa(tmp_path, raiz_judicial, monkeypatch):
    from buscador import retencao

    with TestClient(_app(tmp_path, raiz_judicial)) as cliente:
        caso = _caso(cliente)
        decisao = _decisao(cliente, caso)
        monkeypatch.setattr(retencao, "vencida", lambda _caso: True)
        assert _importar(cliente, caso, decisao["id"]).status_code == 403
        r = cliente.get(
            f"/casos/{caso}/importacao-judicial/registros", headers=cabecalhos(DELEGADO)
        )
        assert r.status_code == 403
