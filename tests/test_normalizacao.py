import pytest

from buscador.resolucao.normalizacao import (
    calcular_dv_cnpj,
    cnpj_valido,
    data_layout_para_iso,
    formatar_cnpj,
    mascarar_cpf,
    normalizar_endereco,
    normalizar_nome,
)


def test_normalizar_nome_remove_acentos_e_colapsa_espacos():
    assert normalizar_nome("  João   da  Silva-Ávila ") == "JOAO DA SILVA AVILA"
    assert normalizar_nome(None) == ""


def test_mascarar_cpf_segue_o_layout_da_receita():
    assert mascarar_cpf("***123456**") == "***123456**"
    assert mascarar_cpf("12345678901") == "***456789**"
    assert mascarar_cpf("123.456.789-01") == "***456789**"
    assert mascarar_cpf("") == ""
    assert mascarar_cpf("abc") == ""


def test_data_layout_para_iso():
    assert data_layout_para_iso("20260510") == "2026-05-10"
    assert data_layout_para_iso("00000000") == ""
    assert data_layout_para_iso("") == ""


def test_endereco_normalizado_ignora_complemento_e_caixa():
    a = normalizar_endereco(
        "Rua", "das Amostras", "100", "Sala 1", "Centro", "70.000-001", "9701", "df"
    )
    b = normalizar_endereco(
        "RUA", "DAS AMOSTRAS", "100", "SALA 2", "CENTRO", "70000001", "9701", "DF"
    )
    assert a == b
    assert normalizar_endereco("RUA", "X", "", "", "B", "1", "M", "UF").split("|")[2] == "SN"


@pytest.mark.parametrize("basico", ["90000001", "90000002", "90100001", "11222333"])
def test_dv_cnpj_e_valido(basico):
    dv = calcular_dv_cnpj(basico + "0001")
    assert cnpj_valido(basico + "0001" + dv)
    assert not cnpj_valido(basico + "0001" + ("00" if dv != "00" else "11"))


def test_dv_cnpj_caso_conhecido():
    # 11.222.333/0001-81 é o exemplo clássico de CNPJ com DV válido.
    assert calcular_dv_cnpj("112223330001") == "81"
    assert formatar_cnpj("11222333000181") == "11.222.333/0001-81"
