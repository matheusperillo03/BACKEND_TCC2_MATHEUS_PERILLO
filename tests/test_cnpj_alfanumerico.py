"""Risco 2 do piloto: CNPJ alfanumérico (IN RFB nº 2.229/2024) em validação, normalização,
conectores, resolução de entidades, busca, tipologias e exportação, sem quebrar o numérico.

Os CNPJs alfanuméricos são SINTÉTICOS, sorteados a cada execução com o DV calculado pela regra
oficial. O único valor fixo é o exemplo do manual da Receita/Serpro (12.ABC.345/01DE-35)."""

from __future__ import annotations

import importlib.util
import io
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from buscador.api import criar_app
from buscador.conectores.base import ErroConector
from buscador.conectores.pncp import ConectorPncp, TransporteSimulado
from buscador.conectores.simulados import transporte_portal
from buscador.ia import servico as ia
from buscador.ia.falso import ProvedorFalso
from buscador.resolucao.normalizacao import (
    calcular_dv_cnpj,
    cnpj_valido,
    formatar_cnpj,
    normalizar_cnpj,
    normalizar_cnpj_basico,
)
from tests.conftest import cabecalhos, corpo_caso, criar_config, incluir_equipe
from tests.sinteticos import cnpj_alfanumerico, cnpj_formatado, raiz_alfanumerica

RAIZ = Path(__file__).resolve().parent.parent
ORGAO = "90100001000109"
NUMERICA = "90000001000129"  # empresa sintética numérica das fixtures


# ==== Validação e normalização ===================================================================


def test_dv_do_exemplo_oficial_do_manual_da_receita():
    # Manual "Cálculo dos dígitos verificadores de CNPJ alfanumérico": 12.ABC.345/01DE-35.
    assert calcular_dv_cnpj("12ABC34501DE") == "35"
    assert cnpj_valido("12.ABC.345/01DE-35") and cnpj_valido("12ABC34501DE35")
    assert formatar_cnpj("12abc34501de35") == "12.ABC.345/01DE-35"
    assert normalizar_cnpj(" 12.abc.345/01de-35 ") == "12ABC34501DE35"
    assert normalizar_cnpj_basico("12.ABC.345/01DE-35") == "12ABC345"


def test_cnpj_numerico_continua_valendo():
    assert calcular_dv_cnpj("112223330001") == "81"
    assert cnpj_valido("11.222.333/0001-81") and cnpj_valido(NUMERICA)
    assert formatar_cnpj("11222333000181") == "11.222.333/0001-81"
    assert normalizar_cnpj_basico("123") == "00000123"
    assert normalizar_cnpj_basico(NUMERICA) == "90000001"


@pytest.mark.parametrize("_", range(20))
def test_cnpj_alfanumerico_sintetico_valido_e_variantes_invalidas(_):
    cnpj = cnpj_alfanumerico()
    assert cnpj_valido(cnpj) and cnpj_valido(cnpj.lower()) and cnpj_valido(cnpj_formatado(cnpj))
    assert normalizar_cnpj(cnpj_formatado(cnpj).lower()) == cnpj
    assert formatar_cnpj(cnpj) == cnpj_formatado(cnpj)
    # DV errado (cada dígito verificador trocado).
    for posicao in (12, 13):
        trocado = cnpj[:posicao] + str((int(cnpj[posicao]) + 1) % 10) + cnpj[posicao + 1 :]
        assert not cnpj_valido(trocado)
    # (Trocar uma posição da raiz nem sempre invalida o CNPJ: restos 0 e 1 dão o mesmo DV 0,
    # então o módulo 11 não detecta toda troca; por isso esse caso não é afirmado aqui.)
    invalidos = (
        cnpj[:12] + "A" + cnpj[13],  # letra no dígito verificador
        cnpj[:13],  # 13 posições
        cnpj + "0",  # 15 posições
        cnpj[:5] + "_" + cnpj[6:],  # símbolo fora da máscara
        cnpj[:5] + "Ç" + cnpj[6:],  # letra fora de A-Z
        "",
    )
    for invalido in invalidos:
        assert not cnpj_valido(invalido) and normalizar_cnpj(invalido) == ""
    assert formatar_cnpj(cnpj[:13]) == cnpj[:13]  # o que não é CNPJ volta como veio


def test_calcular_dv_recusa_entrada_fora_do_formato():
    for errado in ("12ABC34501D", "12ABC34501DE3", "12ABC34501D*", ""):
        with pytest.raises(ValueError):
            calcular_dv_cnpj(errado)


# ==== Da coleta à exportação =====================================================================


def _linha(*campos: str) -> str:
    return ";".join(f'"{c}"' for c in campos) + "\n"


def _estabelecimento(raiz: str, ordem: str, matriz: str, inicio: str, numero: str) -> str:
    dv = calcular_dv_cnpj(raiz + ordem)
    return _linha(
        raiz, ordem, dv, matriz, "FANTASIA SINTETICA", "02", inicio, "00", "", "105", inicio,
        "4120400", "", "RUA", "DAS LETRAS", numero, "", "CENTRO", "70000001", "DF", "9701",
        "61", "30000000", "", "", "", "", "contato@exemplo.invalid", "", "",
    )  # fmt: skip


def _contrato(numero: int, ni: str, nome: str) -> dict:
    return {
        "numeroControlePNCP": f"{ORGAO}-2-{numero:06d}/2026",
        "orgaoEntidade": {"cnpj": ORGAO, "razaoSocial": "PREFEITURA SINTETICA", "esferaId": "M"},
        "unidadeOrgao": {"ufSigla": "DF", "municipioNome": "Exemplo", "codigoUnidade": "900001"},
        "anoContrato": 2026,
        "sequencialContrato": numero,
        "tipoPessoa": "PJ",
        "niFornecedor": ni,
        "nomeRazaoSocialFornecedor": nome,
        "objetoContrato": "Obra sintética",
        "valorInicial": 900000.0,
        "valorGlobal": 900000.0,
        "dataAssinatura": "2026-06-01",
    }


@pytest.fixture
def cenario(tmp_path: Path):
    """Caso com uma empresa de CNPJ alfanumérico (matriz e filial), uma sócia pessoa jurídica
    também alfanumérica e uma empresa numérica, contratadas pelo mesmo órgão."""
    raiz, raiz_socia = raiz_alfanumerica(), raiz_alfanumerica()
    while raiz_socia == raiz:
        raiz_socia = raiz_alfanumerica()
    matriz = cnpj_alfanumerico(raiz, "0001")
    filial = cnpj_alfanumerico(raiz, "00B2")
    pasta = tmp_path / "ingestao" / "cnpj"
    pasta.mkdir(parents=True)
    (pasta / "SINTETICO.txt").write_text("dados sintéticos", encoding="utf-8")
    (pasta / "K0.EMPRECSV").write_text(
        # Raiz em minúsculas no arquivo: o conector normaliza para maiúsculas.
        _linha(raiz.lower(), "LETRAS SINTETICA LTDA", "2062", "49", "1000,00", "01", "")
        + _linha("90000001", "ALFA NUMERICA SINTETICA LTDA", "2062", "49", "500000,00", "05", ""),
        encoding="latin-1",
    )
    (pasta / "K0.ESTABELE").write_text(
        _estabelecimento(raiz, "0001", "1", "20260510", "10")
        + _estabelecimento(raiz, "00B2", "2", "20260520", "20")
        + _estabelecimento("90000001", "0001", "1", "20150310", "30"),
        encoding="latin-1",
    )
    (pasta / "K0.SOCIOCSV").write_text(
        _linha(raiz, "1", "HOLDING SINTETICA SA", raiz_socia, "22", "20260510", "", "", "", "", "")
        + _linha(
            raiz, "2", "SOCIA SINTETICA PF", "***123456**", "49", "20260510", "", "", "", "", "5"
        ),
        encoding="latin-1",
    )
    pagina = {
        "data": [
            _contrato(1, matriz, "LETRAS SINTETICA LTDA"),
            _contrato(2, NUMERICA, "ALFA NUMERICA SINTETICA LTDA"),
        ],
        "paginasRestantes": 0,
        "empty": False,
    }
    ceis = {
        matriz: [
            {
                "id": 77,
                "dataInicioSancao": "01/01/2026",
                "dataFimSancao": "01/01/2028",
                "tipoSancao": {"descricaoResumida": "Impedimento sintético"},
                "orgaoSancionador": {"nome": "ORGAO SINTETICO"},
                "sancionado": {"nome": "LETRAS", "codigoFormatado": cnpj_formatado(matriz)},
                "pessoa": {"cnpjFormatado": cnpj_formatado(matriz), "tipo": "J"},
                "numeroProcesso": "SINT-77/2026",
            }
        ]
    }
    tse = tmp_path / "ingestao_tse" / "t"
    tse.mkdir(parents=True)
    (tse / "SINTETICO.txt").write_text("dados sintéticos", encoding="utf-8")
    (tse / "receitas_candidatos_2024_DF.csv").write_text(
        _linha(
            "AA_ELEICAO",
            "SG_UF",
            "NM_UE",
            "DS_CARGO",
            "SQ_CANDIDATO",
            "NM_CANDIDATO",
            "DS_ORIGEM_RECEITA",
            "NR_CPF_CNPJ_DOADOR",
            "NM_DOADOR",
            "DT_RECEITA",
            "VR_RECEITA",
        )  # fmt: skip
        + _linha(
            "2024",
            "DF",
            "EXEMPLO",
            "PREFEITO",
            "990000000001",
            "CANDIDATO SINTETICO",
            "Recursos de pessoas jurídicas (sintético)",
            filial,
            "LETRAS SINTETICA LTDA",
            "20/08/2024",
            "10000,00",
        ),  # fmt: skip
        encoding="utf-8",
    )
    transporte = TransporteSimulado({1: json.dumps(pagina).encode()})
    config = criar_config(
        tmp_path,
        tmp_path / "ingestao",
        tse_dir_base=tmp_path / "ingestao_tse",
        portal_transparencia_chave="chave-sintetica",
        portal_transparencia_requisicoes_por_minuto=700,
    )
    app = criar_app(
        config,
        extras_conectores={
            "pncp": {"transporte": transporte},
            "portal_transparencia": {
                "transporte": transporte_portal(ceis),
                "dormir": lambda _s: None,
            },
        },
    )
    with TestClient(app) as cliente:
        r = cliente.post("/casos", json=corpo_caso(), headers=cabecalhos())
        caso_id = r.json()["id"]
        incluir_equipe(cliente, caso_id)
        for fonte, parametros in (
            ("cnpj_arquivo", {"diretorio": "cnpj"}),
            ("pncp", {"dataInicial": "20260101", "dataFinal": "20261231"}),
            ("portal_transparencia", {"recurso": "ceis", "do_caso": True}),
            ("tse_arquivo", {"diretorio": "t"}),
        ):
            r = cliente.post(
                f"/casos/{caso_id}/coletas",
                json={"fonte": fonte, "parametros": parametros},
                headers=cabecalhos(),
            )
            assert r.status_code == 200 and r.json()["situacao"] == "sucesso", r.text
        yield {
            "cliente": cliente,
            "caso_id": caso_id,
            "raiz": raiz,
            "raiz_socia": raiz_socia,
            "matriz": matriz,
            "filial": filial,
            "transporte": transporte,
        }


def _buscar(c, q: str, tipo: str = "empresa") -> list[dict]:
    r = c["cliente"].get(
        f"/casos/{c['caso_id']}/entidades", params={"q": q, "tipo": tipo}, headers=cabecalhos()
    )
    assert r.status_code == 200, r.text
    return r.json()


def test_resolucao_une_as_fontes_pela_raiz_alfanumerica(cenario):
    c = cenario
    empresas = _buscar(c, "")
    por_raiz = {e["identificador"]: e for e in empresas}
    # Três empresas: a alfanumérica, a sócia PJ (só a raiz) e a numérica.
    assert set(por_raiz) == {c["raiz"], c["raiz_socia"], "90000001"}
    letras = (
        c["cliente"]
        .get(f"/casos/{c['caso_id']}/entidades/{por_raiz[c['raiz']]['id']}", headers=cabecalhos())
        .json()
    )
    atributos = letras["atributos"]
    assert atributos["cnpj_basico"] == c["raiz"] and atributos["cnpj"] == c["matriz"]
    assert {e["cnpj"] for e in atributos["estabelecimentos"]} == {c["matriz"], c["filial"]}
    assert letras["rotulo"] == "LETRAS SINTETICA LTDA"
    # Arquivo do CNPJ, PNCP (fornecedor), CEIS (sancionado) e TSE (doador): a mesma entidade.
    papeis = {f["papel"] for f in letras["fontes"]}
    assert {
        "empresas",
        "estabelecimentos",
        "socios",
        "fornecedor",
        "sancionado",
        "doador",
    } <= papeis
    grafo = (
        c["cliente"]
        .get(
            f"/casos/{c['caso_id']}/grafo",
            params={"centro": letras["id"], "profundidade": 1},
            headers=cabecalhos(),
        )
        .json()
    )
    tipos = {a["tipo"] for a in grafo["arestas"]}
    assert {"socio_de", "contratado_por", "sancionada_em", "doou_para"} <= tipos
    socia = por_raiz[c["raiz_socia"]]
    assert any(
        a["tipo"] == "socio_de" and a["origem"] == socia["id"] and a["destino"] == letras["id"]
        for a in grafo["arestas"]
    )
    # "do_caso" expandiu para os CNPJs das contratadas, inclusive o alfanumérico.
    coletas = c["cliente"].get(f"/casos/{c['caso_id']}/coletas", headers=cabecalhos()).json()
    portal = next(x for x in coletas if x["fonte"] == "portal_transparencia")
    assert set(portal["parametros"]["codigoSancionado"]) == {c["matriz"], NUMERICA}


def test_busca_por_cnpj_alfanumerico_em_varias_formas(cenario):
    c = cenario
    for termo in (
        c["raiz"],
        c["raiz"].lower(),
        c["matriz"],
        cnpj_formatado(c["matriz"]),
        cnpj_formatado(c["filial"]).lower(),
    ):
        achadas = _buscar(c, termo)
        assert [e["identificador"] for e in achadas] == [c["raiz"]], termo
    # O numérico segue como antes, e um nome sem dígitos não é tratado como documento.
    assert [e["identificador"] for e in _buscar(c, "90.000.001/0001-29")] == ["90000001"]
    assert [e["identificador"] for e in _buscar(c, "HOLDING SINTETICA")] == [c["raiz_socia"]]


def test_tipologias_e_exportacao_com_cnpj_alfanumerico(cenario):
    c = cenario
    r = c["cliente"].post(f"/casos/{c['caso_id']}/alertas/executar", json={}, headers=cabecalhos())
    assert r.status_code == 200, r.text
    alertas = r.json()
    por_tipologia = {a["tipologia"]: a for a in alertas if c["matriz"] in json.dumps(a)}
    # T1: aberta 22 dias antes do contrato; T3: capital de R$ 1 mil para contrato de R$ 900 mil;
    # T6: sanção vigente no CEIS na data da assinatura.
    assert {"T1", "T3", "T6"} <= set(por_tipologia), sorted({a["tipologia"] for a in alertas})
    assert f"(CNPJ {c['matriz']})" in por_tipologia["T1"]["motivo"]
    relatorio = (
        c["cliente"]
        .get(f"/casos/{c['caso_id']}/relatorio", params={"formato": "md"}, headers=cabecalhos())
        .text
    )
    assert c["matriz"] in relatorio
    for formato in ("csv_entidades", "csv_vinculos", "graphml"):
        r = c["cliente"].get(
            f"/casos/{c['caso_id']}/exportacao", params={"formato": formato}, headers=cabecalhos()
        )
        assert r.status_code == 200, r.text
        texto = r.content.decode("utf-8")
        assert c["raiz"] in texto and c["raiz_socia"] in texto and "90000001" in texto
        if formato != "csv_vinculos":
            assert c["matriz"] in texto and c["filial"] in texto


def test_pncp_filtra_fornecedor_pela_raiz_alfanumerica_e_recusa_formato_estranho(cenario):
    c = cenario

    def coletar(filtro) -> list:
        conector = ConectorPncp(transporte=c["transporte"])
        resultado = conector.coletar(
            {"dataInicial": "20260101", "dataFinal": "20261231", "cnpjFornecedor": filtro},
            lambda fonte, origem, conteudo, metadados: type(
                "C", (), {"id": "cap", "url_ou_arquivo": origem}
            )(),
        )
        return [r.ni_fornecedor for r in resultado.registros]

    assert coletar(c["raiz"].lower()) == [c["matriz"]]
    assert coletar(cnpj_formatado(c["matriz"])) == [c["matriz"]]
    assert coletar("90000001") == [NUMERICA]
    assert coletar([c["raiz"], NUMERICA]) == [c["matriz"], NUMERICA]
    for errado in ("123", c["matriz"][:13], "ABCDEFGHIJK"):
        with pytest.raises(ErroConector, match="8 posições"):
            coletar(errado)


def test_portal_recusa_codigo_que_nao_e_cnpj(cenario):
    c = cenario
    r = c["cliente"].post(
        f"/casos/{c['caso_id']}/coletas",
        json={
            "fonte": "portal_transparencia",
            "parametros": {"recurso": "ceis", "codigoSancionado": [c["matriz"][:13], "x"]},
        },
        headers=cabecalhos(),
    )
    assert r.status_code == 422 and "14 posições" in r.text


# ==== Recorte dos arquivos da Receita e extração por IA ==========================================


def test_recorte_dos_arquivos_da_receita_aceita_raiz_alfanumerica():
    especificacao = importlib.util.spec_from_file_location(
        "recortar_cnpj", RAIZ / "scripts" / "recortar_cnpj.py"
    )
    recortar = importlib.util.module_from_spec(especificacao)
    especificacao.loader.exec_module(recortar)
    raiz = raiz_alfanumerica()
    cnpj = cnpj_alfanumerico(raiz)
    assert recortar.cnpjs_basicos([cnpj_formatado(cnpj).lower(), "90000001", NUMERICA]) == sorted(
        {raiz, "90000001"}
    )
    for errado in ("1234567", cnpj[:13], "12ABC34*"):
        with pytest.raises(ValueError):
            recortar.cnpjs_basicos([errado])
    arquivo = _linha("90000001", "NUMERICA") + _linha(raiz, "LETRAS") + _linha("90000002", "OUTRA")
    linhas = list(recortar.filtrar_linhas(io.BytesIO(arquivo.encode("latin-1")), [raiz]))
    assert linhas == [_linha(raiz, "LETRAS").encode("latin-1")]


def test_extracao_por_ia_reconhece_e_ampara_cnpj_alfanumerico():
    cnpj = cnpj_alfanumerico()
    texto = (
        f"Contratada: LETRAS SINTETICA LTDA, CNPJ {cnpj_formatado(cnpj)}, com sede em Exemplo.\n"
    )
    itens = json.loads(ProvedorFalso().gerar("", texto, {}))["itens"]
    assert [i["campos"]["cnpj"] for i in itens if i["tipo"] == "empresa"] == [cnpj_formatado(cnpj)]
    verificacao = ia.Verificacao()
    ia.verificar_itens(texto, itens, verificacao, 10)
    assert [i.tipo for i, _, _ in verificacao.aceitos] == ["empresa"] and not verificacao.recusas
    # CNPJ que não está no trecho (uma posição trocada) é recusado.
    outro = ("A" if cnpj[0] != "A" else "B") + cnpj[1:]
    falso = {**itens[0], "campos": {"cnpj": outro}}
    verificacao = ia.Verificacao()
    ia.verificar_itens(texto, [falso], verificacao, 10)
    assert [r["motivo"] for r in verificacao.recusas] == ["campo_fora_do_trecho"]
