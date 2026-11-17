"""Gera as fixtures SINTÉTICAS de tests/fixtures (layout oficial do CNPJ e respostas do PNCP).

Nenhum dado aqui é real: CNPJs são gerados com DV válido a partir de bases fictícias (900000xx),
nomes são rotulados como sintéticos e os CPFs já nascem descaracterizados (***NNNNNN**), como no
layout da Receita. O cenário foi desenhado para disparar T1, T2, T3 e T4 ao menos uma vez e
produzir uma sugestão de homônimo.

Uso: uv run python scripts/gerar_fixtures.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

from buscador.resolucao.normalizacao import calcular_dv_cnpj  # noqa: E402

DESTINO_CNPJ = RAIZ / "tests" / "fixtures" / "cnpj"
DESTINO_PNCP = RAIZ / "tests" / "fixtures" / "pncp"


def cnpj_completo(basico: str, ordem: str = "0001") -> str:
    return f"{basico}{ordem}{calcular_dv_cnpj(basico + ordem)}"


# ---- Endereços sintéticos (tipo, logradouro, numero, complemento, bairro, cep, uf, municipio) --
ENDERECOS = {
    "A": ("RUA", "DAS AMOSTRAS", "100", "", "CENTRO", "70000001", "DF", "9701"),
    "B": ("AVENIDA", "DOS TESTES", "2500", "SALA 3", "ASA NORTE", "70000002", "DF", "9701"),
    "C": ("RUA", "DO EXEMPLO", "45", "", "TAGUATINGA", "70000003", "DF", "9701"),
    "D": ("QUADRA", "SINTETICA", "12", "LOJA 1", "CEILANDIA", "70000004", "DF", "9701"),
    "E": ("RUA", "FICTICIA", "77", "", "GAMA", "70000005", "DF", "9701"),
    "E2": ("RUA", "FICTICIA FILIAL", "78", "", "GAMA", "70000006", "DF", "9701"),
    "F": ("AVENIDA", "DE DEMONSTRACAO", "900", "BLOCO B", "GUARA", "70000007", "DF", "9701"),
    "G": ("RUA", "DOS HOMONIMOS", "10", "", "SOBRADINHO", "70000008", "DF", "9701"),
    "H": ("SETOR", "COMERCIAL SINTETICO", "1", "COBERTURA", "ASA SUL", "70000009", "DF", "9701"),
    "I": ("RUA", "DA SEGURANCA FICTICIA", "33", "", "PLANALTINA", "70000010", "DF", "9701"),
    "J": ("AVENIDA", "COMPARTILHADA", "500", "SALA 1", "AGUAS CLARAS", "70000011", "DF", "9701"),
    "K": ("RUA", "DA SAUDE SINTETICA", "60", "", "CRUZEIRO", "70000012", "DF", "9701"),
}

# ---- Empresas: (básico, razão social, natureza, capital, porte, início, situação, endereço) ----
EMPRESAS = [
    (
        "90000001",
        "ALFA CONSTRUCOES SINTETICA LTDA",
        "2062",
        "500000,00",
        "05",
        "20150310",
        "02",
        "A",
    ),
    (
        "90000002",
        "BETA ENGENHARIA SINTETICA LTDA",
        "2062",
        "100000,00",
        "01",
        "20260510",
        "02",
        "B",
    ),
    ("90000003", "GAMA LIMPEZA SINTETICA EIRELI", "2305", "10000,00", "01", "20100805", "02", "C"),
    ("90000004", "DELTA SERVICOS SINTETICA LTDA", "2062", "80000,00", "03", "20180101", "02", "D"),
    (
        "90000005",
        "EPSILON COMERCIO SINTETICA LTDA",
        "2062",
        "50000,00",
        "01",
        "20190115",
        "02",
        "D",
    ),
    (
        "90000006",
        "ZETA CONSULTORIA SINTETICA LTDA",
        "2062",
        "30000,00",
        "01",
        "20200220",
        "02",
        "D",
    ),
    (
        "90000007",
        "ETA TRANSPORTES SINTETICA LTDA",
        "2062",
        "250000,00",
        "05",
        "20120505",
        "02",
        "E",
    ),
    (
        "90000008",
        "THETA TECNOLOGIA SINTETICA LTDA",
        "2062",
        "20000,00",
        "01",
        "20260801",
        "02",
        "F",
    ),
    ("90000009", "IOTA ALIMENTOS SINTETICA LTDA", "2062", "150000,00", "03", "20160612", "03", "G"),
    ("90000010", "KAPPA HOLDING SINTETICA SA", "2046", "2000000,00", "05", "20080101", "02", "H"),
    (
        "90000011",
        "LAMBDA SEGURANCA SINTETICA LTDA",
        "2062",
        "90000,00",
        "03",
        "20210303",
        "02",
        "I",
    ),
    ("90000012", "MU EVENTOS SINTETICA LTDA", "2062", "40000,00", "01", "20220410", "02", "J"),
    ("90000013", "NU PUBLICIDADE SINTETICA LTDA", "2062", "35000,00", "01", "20230505", "02", "J"),
    ("90000014", "XI MANUTENCAO SINTETICA LTDA", "2062", "60000,00", "01", "20230606", "02", "J"),
    ("90000015", "OMICRON SAUDE SINTETICA LTDA", "2062", "300000,00", "05", "20140707", "08", "K"),
]

# Data do evento da situação cadastral das empresas que não estão ativas (T4, H31). Códigos do
# layout oficial: 01 NULA, 2 ATIVA, 3 SUSPENSA, 4 INAPTA, 08 BAIXADA. OMICRON foi baixada antes
# do contrato 8; IOTA foi suspensa depois do contrato 9. Nas demais a data é a do início da
# atividade. O código do motivo da situação não é usado pelo MVP e fica "00" em todas.
DATAS_SITUACAO = {"90000015": "20250630", "90000009": "20260901"}

# Filial adicional (básico, ordem, endereço, início)
FILIAIS = [("90000007", "0002", "E2", "20150909")]

# ---- Sócios: (básico, identificador, nome, cpf/cnpj, qualificação, entrada, país, faixa) -------
SOCIOS = [
    ("90000001", "2", "JOAO DA SILVA EXEMPLO", "***111111**", "49", "20150310", "", "5"),
    ("90000001", "2", "CARLOS HOMONIMO SINTETICO", "***222222**", "22", "20150310", "", "4"),
    ("90000002", "2", "MARIA SINTETICA SANTOS", "***333333**", "49", "20260510", "", "3"),
    ("90000002", "2", "PEDRO EXEMPLO ALVES", "***444444**", "22", "20260510", "", "4"),
    ("90000003", "2", "MARIA SINTETICA SANTOS", "***333333**", "65", "20100805", "", "3"),
    ("90000003", "2", "ANA TESTE PEREIRA", "***555555**", "22", "20110101", "", "6"),
    ("90000004", "2", "RICARDO FICTICIO LIMA", "***666666**", "49", "20180101", "", "5"),
    ("90000004", "2", "PEDRO EXEMPLO ALVES", "***444444**", "22", "20180101", "", "4"),
    ("90000005", "2", "RICARDO FICTICIO LIMA", "***666666**", "49", "20190115", "", "5"),
    ("90000005", "2", "LUCIA SINTETICA ROCHA", "***777777**", "22", "20190115", "", "4"),
    ("90000006", "2", "RICARDO FICTICIO LIMA", "***666666**", "49", "20200220", "", "5"),
    ("90000006", "2", "MARCOS EXEMPLO NUNES", "***888888**", "22", "20200220", "", "3"),
    ("90000007", "2", "FERNANDA TESTE DUARTE", "***999999**", "49", "20120505", "", "6"),
    ("90000007", "2", "PAULO SINTETICO BRAGA", "***101010**", "22", "20120505", "", "7"),
    ("90000008", "2", "RICARDO FICTICIO LIMA", "***666666**", "49", "20260801", "", "5"),
    ("90000008", "2", "JULIA EXEMPLO CASTRO", "***121212**", "22", "20260801", "", "3"),
    ("90000009", "2", "CARLOS HOMONIMO SINTETICO", "***232323**", "49", "20160612", "", "6"),
    ("90000009", "2", "BRUNO TESTE MOURA", "***131313**", "22", "20160612", "", "4"),
    (
        "90000010",
        "1",
        "ALFA CONSTRUCOES SINTETICA LTDA",
        cnpj_completo("90000001"),
        "22",
        "20080101",
        "",
        "0",
    ),
    ("90000010", "2", "HELENA SINTETICA PRADO", "***141414**", "10", "20080101", "", "7"),
    ("90000011", "2", "GUSTAVO EXEMPLO REIS", "***151515**", "49", "20210303", "", "4"),
    ("90000011", "3", "JOHN FOREIGN SAMPLE", "", "22", "20210303", "249", "0"),
    ("90000012", "2", "TATIANA TESTE VIEIRA", "***161616**", "49", "20220410", "", "4"),
    ("90000013", "2", "TATIANA TESTE VIEIRA", "***161616**", "49", "20230505", "", "4"),
    ("90000014", "2", "RENATO SINTETICO FARIA", "***171717**", "49", "20230606", "", "5"),
    ("90000015", "2", "SILVIA EXEMPLO MELO", "***181818**", "49", "20140707", "", "6"),
    ("90000015", "3", "JOHN FOREIGN SAMPLE", "", "22", "20140707", "249", "0"),
]

# ---- Órgãos e contratos sintéticos --------------------------------------------------------------
ORGAOS = {
    "O1": ("90100001", "PREFEITURA MUNICIPAL SINTETICA DE EXEMPLO", "M", "Exemplo", "DF"),
    "O2": ("90100002", "SECRETARIA ESTADUAL SINTETICA DE OBRAS", "E", "Brasilia", "DF"),
}

# (sequencial, fornecedor básico, nome fornecedor, órgão, objeto, valor, assinatura)
CONTRATOS = [
    (
        1,
        "90000001",
        "ALFA CONSTRUCOES SINTETICA LTDA",
        "O1",
        "Reforma sintética de escola municipal",
        200000.0,
        "2026-03-15",
    ),
    (
        2,
        "90000002",
        "BETA ENGENHARIA SINTETICA LTDA",
        "O2",
        "Projeto executivo sintético de ponte",
        150000.0,
        "2026-07-01",
    ),
    (
        3,
        "90000003",
        "GAMA LIMPEZA SINTETICA EIRELI",
        "O1",
        "Serviços sintéticos de limpeza predial",
        500000.0,
        "2026-06-20",
    ),
    (
        4,
        "90000004",
        "DELTA SERVICOS SINTETICA LTDA",
        "O1",
        "Manutenção sintética de praças",
        120000.0,
        "2026-08-10",
    ),
    (
        5,
        "90000008",
        "THETA TECNOLOGIA SINTETICA LTDA",
        "O2",
        "Fornecimento sintético de software de gestão",
        400000.0,
        "2026-09-15",
    ),
    (
        6,
        "90000011",
        "LAMBDA SEGURANCA SINTETICA LTDA",
        "O2",
        "Vigilância sintética de almoxarifado",
        95000.0,
        "2026-05-05",
    ),
    (
        7,
        "90000099",
        "PI FORNECEDOR DESCONHECIDO SINTETICO LTDA",
        "O1",
        "Aquisição sintética de mobiliário",
        70000.0,
        "2026-04-02",
    ),
    # Contratos 8 e 9: fornecedores sem situação cadastral ativa (T4). Os valores ficam abaixo
    # do capital social e as empresas são antigas, para não disparar T1 nem T3.
    (
        8,
        "90000015",
        "OMICRON SAUDE SINTETICA LTDA",
        "O1",
        "Fornecimento sintético de insumos de saúde",
        60000.0,
        "2026-02-10",
    ),
    (
        9,
        "90000009",
        "IOTA ALIMENTOS SINTETICA LTDA",
        "O2",
        "Fornecimento sintético de merenda escolar",
        90000.0,
        "2026-05-20",
    ),
]


def campo(valor: str) -> str:
    return '"' + str(valor).replace('"', "'") + '"'


def linha(*valores: str) -> str:
    return ";".join(campo(v) for v in valores) + "\r\n"


MARCADOR_SINTETICO = (
    "Dados SINTETICOS gerados por scripts/gerar_fixtures.py para testes e demonstracao.\n"
    "Nenhum registro desta pasta veio da Receita Federal. A presenca deste arquivo faz o\n"
    "conector cnpj_arquivo marcar as capturas como simuladas (simulado://).\n"
)


def gerar_cnpj() -> None:
    DESTINO_CNPJ.mkdir(parents=True, exist_ok=True)
    empresas = ""
    estabelecimentos = ""
    socios = ""
    for basico, razao, natureza, capital, porte, inicio, situacao, chave_end in EMPRESAS:
        empresas += linha(basico, razao, natureza, "49", capital, porte, "")
        estabelecimentos += _estabelecimento(
            basico,
            "0001",
            "1",
            razao.split(" ")[0],
            situacao,
            inicio,
            chave_end,
            data_situacao=DATAS_SITUACAO.get(basico),
        )
    for basico, ordem, chave_end, inicio in FILIAIS:
        razao = next(e[1] for e in EMPRESAS if e[0] == basico)
        estabelecimentos += _estabelecimento(
            basico, ordem, "2", razao.split(" ")[0] + " FILIAL", "02", inicio, chave_end
        )
    for basico, ident, nome, doc, qualif, entrada, pais, faixa in SOCIOS:
        socios += linha(
            basico, ident, nome, doc, qualif, entrada, pais, "***000000**", "", "00", faixa
        )
    (DESTINO_CNPJ / "K3241.K03200Y0.D61111.EMPRECSV").write_bytes(empresas.encode("latin-1"))
    (DESTINO_CNPJ / "K3241.K03200Y0.D61111.ESTABELE").write_bytes(
        estabelecimentos.encode("latin-1")
    )
    (DESTINO_CNPJ / "K3241.K03200Y0.D61111.SOCIOCSV").write_bytes(socios.encode("latin-1"))
    # Marcador lido pelo conector cnpj_arquivo: as capturas desta pasta saem como sintéticas.
    (DESTINO_CNPJ / "SINTETICO.txt").write_text(MARCADOR_SINTETICO, encoding="utf-8")


def _estabelecimento(
    basico, ordem, matriz_filial, fantasia, situacao, inicio, chave_end, data_situacao=None
) -> str:
    tipo, logradouro, numero, complemento, bairro, cep, uf, municipio = ENDERECOS[chave_end]
    return linha(
        basico,
        ordem,
        calcular_dv_cnpj(basico + ordem),
        matriz_filial,
        f"{fantasia} SINTETICA",
        situacao,
        data_situacao or inicio,
        "00",
        "",
        "105",
        inicio,
        "4120400",
        "4321500,4399103",
        tipo,
        logradouro,
        numero,
        complemento,
        bairro,
        cep,
        uf,
        municipio,
        "61",
        "30000000",
        "",
        "",
        "",
        "",
        "contato@exemplo.invalid",
        "",
        "",
    )


def _contrato(seq, basico, nome, orgao_chave, objeto, valor, assinatura) -> dict:
    orgao_basico, orgao_nome, esfera, municipio, uf = ORGAOS[orgao_chave]
    orgao_cnpj = cnpj_completo(orgao_basico)
    return {
        "numeroControlePncpAta": None,
        "codigoPaisFornecedor": None,
        "numeroControlePncpCompra": f"{orgao_cnpj}-1-{seq:06d}/2026",
        "orgaoEntidade": {
            "cnpj": orgao_cnpj,
            "razaoSocial": orgao_nome,
            "poderId": "E",
            "esferaId": esfera,
        },
        "anoContrato": 2026,
        "sequencialContrato": seq,
        "dataAtualizacao": f"{assinatura}T10:00:00",
        "emendaParlamentar": None,
        "processo": f"00000.{seq:06d}/2026-00",
        "informacaoComplementar": None,
        "dataPublicacaoPncp": f"{assinatura}T10:00:00",
        "dataAtualizacaoGlobal": f"{assinatura}T10:00:00",
        "unidadeOrgao": {
            "ufNome": "Distrito Federal",
            "ufSigla": uf,
            "municipioNome": municipio,
            "codigoUnidade": f"9{seq:05d}",
            "nomeUnidade": f"UNIDADE SINTETICA {orgao_chave}",
            "codigoIbge": "5300108",
        },
        "orgaoSubRogado": None,
        "unidadeSubRogada": None,
        "receita": False,
        "numeroParcelas": 1,
        "numeroRetificacao": 0,
        "temRemanejamento": False,
        "frutoAdesao": False,
        "tipoContrato": {"id": 1, "nome": "Contrato (termo inicial)"},
        "numeroContratoEmpenho": f"{seq}/2026",
        "categoriaProcesso": {"id": 2, "nome": "Compras"},
        "tipoPessoa": "PJ",
        "niFornecedor": cnpj_completo(basico),
        "nomeRazaoSocialFornecedor": nome,
        "numeroControlePNCP": f"{orgao_cnpj}-2-{seq:06d}/2026",
        "tipoPessoaSubContratada": None,
        "niFornecedorSubContratado": None,
        "nomeFornecedorSubContratado": None,
        "objetoContrato": objeto,
        "valorInicial": valor,
        "valorParcela": valor,
        "valorGlobal": valor,
        "valorAcumulado": None,
        "dataAssinatura": assinatura,
        "dataVigenciaInicio": assinatura,
        "dataVigenciaFim": "2027-12-31",
        "identificadorCipi": None,
        "urlCipi": None,
        "usuarioNome": "Fixture sintética",
    }


def gerar_pncp() -> None:
    DESTINO_PNCP.mkdir(parents=True, exist_ok=True)
    contratos = [_contrato(*c) for c in CONTRATOS]
    paginas = [contratos[:4], contratos[4:]]
    for numero, dados in enumerate(paginas, start=1):
        corpo = {
            "data": dados,
            "totalRegistros": len(contratos),
            "totalPaginas": len(paginas),
            "numeroPagina": numero,
            "paginasRestantes": len(paginas) - numero,
            "empty": False,
        }
        (DESTINO_PNCP / f"contratos_pagina{numero}.json").write_text(
            json.dumps(corpo, ensure_ascii=False, indent=1), encoding="utf-8"
        )


if __name__ == "__main__":
    gerar_cnpj()
    gerar_pncp()
    print(f"Fixtures sintéticas geradas em {DESTINO_CNPJ} e {DESTINO_PNCP}")
