"""Gera as fixtures SINTÉTICAS do horizonte 2 em tests/fixtures/h2.

Nenhum dado aqui é real. Os CNPJs são os das fixtures do CNPJ (bases fictícias 900000xx e
901000xx, com DV válido); os CPFs completos usados nos arquivos do TSE e do Portal da
Transparência têm dígitos verificadores INVÁLIDOS de propósito, de modo que não podem pertencer a
ninguém; nomes e órgãos são rotulados como sintéticos. O cenário se apoia nas empresas e nos
sócios de tests/fixtures/cnpj e foi desenhado para disparar e para não disparar cada tipologia do
horizonte 2 (T5 a T10); a tabela está no README.

Uso: uv run python scripts/gerar_fixtures_h2.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

from buscador.resolucao.normalizacao import calcular_dv_cnpj, formatar_cnpj  # noqa: E402

DESTINO = RAIZ / "tests" / "fixtures" / "h2"
MARCADOR = "Arquivos sintéticos (fixtures de teste e de demonstração). Nenhum dado é real.\n"


def cnpj(basico: str, ordem: str = "0001") -> str:
    return f"{basico}{ordem}{calcular_dv_cnpj(basico + ordem)}"


def _dv_cpf(nove: str) -> str:
    def dv(parcial: str) -> str:
        soma = sum(int(d) * p for d, p in zip(parcial, range(len(parcial) + 1, 1, -1), strict=True))
        resto = soma * 10 % 11
        return "0" if resto == 10 else str(resto)

    primeiro = dv(nove)
    return primeiro + dv(nove + primeiro)


def cpf_sintetico(miolo: str) -> str:
    """CPF de 11 dígitos com o miolo pedido (os seis dígitos que ficam visíveis depois de
    descaracterizado) e DV propositalmente INVÁLIDO."""
    nove = f"000{miolo}"
    valido = _dv_cpf(nove)
    invalido = f"{(int(valido) + 11) % 100:02d}"
    if invalido == valido:  # impossível (soma 11 em módulo 100), mas fica explícito
        raise ValueError("o CPF sintético ficou com DV válido")
    return nove + invalido


ORGAO = {
    "cnpj": cnpj("90100003"),
    "razaoSocial": "PREFEITURA SINTETICA DE EXEMPLOPOLIS",
    "poderId": "E",
    "esferaId": "M",
}
UNIDADE = {
    "ufNome": "Goiás",
    "ufSigla": "GO",
    "municipioNome": "Exemplopolis",
    "codigoUnidade": "910001",
    "nomeUnidade": "SECRETARIA SINTETICA DE ADMINISTRACAO",
    "codigoIbge": "0000000",
}
SIAPE_ORGAO = "99001"
CATEGORIAS = {2: "Compras", 7: "Obras", 8: "Serviços"}

EMPRESAS = {
    "ALFA": ("90000001", "ALFA CONSTRUCOES SINTETICA LTDA"),
    "DELTA": ("90000004", "DELTA SERVICOS SINTETICA LTDA"),
    "EPSILON": ("90000005", "EPSILON COMERCIO SINTETICA LTDA"),
    "ZETA": ("90000006", "ZETA CONSULTORIA SINTETICA LTDA"),
    "ETA": ("90000007", "ETA TRANSPORTES SINTETICA LTDA"),
    "MU": ("90000012", "MU EVENTOS SINTETICA LTDA"),
}

# (sequencial, empresa, categoria, valor inicial, assinatura, objeto)
CONTRATOS = [
    # T5 dispara: três serviços do mesmo fornecedor, cada um abaixo de R$ 65.492,11; soma 90 mil.
    (101, "DELTA", 8, 30000.0, "2026-02-10", "Serviço sintético de jardinagem, lote A"),
    (102, "DELTA", 8, 30000.0, "2026-04-10", "Serviço sintético de jardinagem, lote B"),
    (103, "DELTA", 8, 30000.0, "2026-06-10", "Serviço sintético de jardinagem, lote C"),
    # T5 não dispara: a soma (40 mil) fica abaixo do limite do inciso II.
    (104, "EPSILON", 2, 20000.0, "2026-03-05", "Aquisição sintética de material de expediente"),
    (105, "EPSILON", 2, 20000.0, "2026-07-05", "Aquisição sintética de material de limpeza"),
    # T5 não dispara: obras usam o limite do inciso I (R$ 130.984,20) e a soma é 100 mil.
    (106, "ALFA", 7, 50000.0, "2026-02-20", "Obra sintética de calçada, trecho 1"),
    (107, "ALFA", 7, 50000.0, "2026-05-20", "Obra sintética de calçada, trecho 2"),
    # T8 dispara (aditivos de 30% > 25%) e T6 dispara (sanção vigente no CEIS).
    (108, "ZETA", 8, 100000.0, "2026-03-01", "Consultoria sintética em gestão"),
    # T8 não dispara: reforma de edifício tem limite de 50% e os aditivos somam 30%.
    # T6 não dispara: a sanção no CNEP terminou em 2021.
    (109, "ETA", 7, 200000.0, "2026-04-15", "Reforma de edifício sintético da sede"),
    # T8 não dispara: aditivo de 20%; o termo de reajuste não é acréscimo.
    (110, "MU", 8, 100000.0, "2026-05-10", "Organização sintética de eventos"),
]

# sequencial do contrato -> termos (sequencial, tipo, valor acrescido, acréscimo/supressão, data)
TERMOS = {
    108: [
        (1, "Termo Aditivo", 20000.0, True, "2026-05-01"),
        (2, "Termo Aditivo", 10000.0, True, "2026-08-01"),
    ],
    109: [(1, "Termo Aditivo", 60000.0, True, "2026-07-01")],
    110: [
        (1, "Termo Aditivo", 20000.0, True, "2026-07-10"),
        (2, "Termo de Apostilamento", 50000.0, False, "2026-09-10"),
    ],
}


def numero_controle(sequencial: int, tipo: int = 2) -> str:
    return f"{ORGAO['cnpj']}-{tipo}-{sequencial:06d}/2026"


def contrato(seq: int, empresa: str, categoria: int, valor: float, data: str, objeto: str) -> dict:
    basico, nome = EMPRESAS[empresa]
    return {
        "numeroControlePncpCompra": numero_controle(seq, 1),
        "orgaoEntidade": ORGAO,
        "anoContrato": 2026,
        "sequencialContrato": seq,
        "dataAtualizacao": f"{data}T10:00:00",
        "processo": f"00000.{seq:06d}/2026-00",
        "dataPublicacaoPncp": f"{data}T10:00:00",
        "dataAtualizacaoGlobal": f"{data}T10:00:00",
        "unidadeOrgao": UNIDADE,
        "numeroParcelas": 1,
        "numeroRetificacao": 0,
        "tipoContrato": {"id": 1, "nome": "Contrato (termo inicial)"},
        "numeroContratoEmpenho": f"{seq}/2026",
        "categoriaProcesso": {"id": categoria, "nome": CATEGORIAS[categoria]},
        "tipoPessoa": "PJ",
        "niFornecedor": cnpj(basico),
        "nomeRazaoSocialFornecedor": nome,
        "numeroControlePNCP": numero_controle(seq),
        "objetoContrato": objeto,
        "valorInicial": valor,
        "valorParcela": valor,
        "valorGlobal": valor,
        "valorAcumulado": None,
        "dataAssinatura": data,
        "dataVigenciaInicio": data,
        "dataVigenciaFim": "2027-12-31",
        "usuarioNome": "Fixture sintética",
    }


def termo(seq_contrato: int, seq: int, tipo: str, valor: float, acrescimo: bool, data: str) -> dict:
    return {
        "sequencialTermoContrato": seq,
        "numeroTermoContrato": f"{seq}/2026",
        "tipoTermoContratoNome": tipo,
        "objetoTermoContrato": f"{tipo} sintético nº {seq} do contrato {seq_contrato}",
        "valorAcrescido": valor,
        "qualificacaoAcrescimoSupressao": acrescimo,
        "qualificacaoReajuste": not acrescimo,
        "qualificacaoVigencia": False,
        "qualificacaoFornecedor": False,
        "dataAssinatura": data,
        "fundamentoLegal": "Fundamento sintético",
        "orgaoEntidade": ORGAO,
    }


def sancao(identificador: int, empresa: str, inicio: str, fim: str, tipo: str) -> dict:
    basico, nome = EMPRESAS[empresa]
    formatado = formatar_cnpj(cnpj(basico))
    return {
        "id": identificador,
        "dataInicioSancao": inicio,
        "dataFimSancao": fim,
        "tipoSancao": {"descricaoResumida": tipo, "descricaoPortal": tipo},
        "orgaoSancionador": {"nome": "ORGAO SANCIONADOR SINTETICO", "siglaUf": "GO"},
        "sancionado": {"nome": nome, "codigoFormatado": formatado},
        "pessoa": {"cnpjFormatado": formatado, "razaoSocialReceita": nome, "tipo": "J"},
        "numeroProcesso": f"SINT-{identificador}/2025",
    }


def servidor(identificador: int, nome: str, cpf: str) -> dict:
    orgao = {"codigo": SIAPE_ORGAO, "nome": "ORGAO SIAPE SINTETICO", "sigla": "OSS"}
    return {
        "servidor": {
            "id": identificador,
            "idServidorAposentadoPensionista": identificador,
            "pessoa": {"nome": nome, "cpfFormatado": cpf, "tipo": "F"},
            "situacao": "ATIVO PERMANENTE",
            "tipoServidor": "Civil",
            "orgaoServidorLotacao": orgao,
            "orgaoServidorExercicio": orgao,
        }
    }


def _cpf_formatado(cpf: str) -> str:
    return f"{cpf[:3]}.{cpf[3:6]}.{cpf[6:9]}-{cpf[9:]}"


def linha_csv(valores: list[str]) -> str:
    return ";".join('"' + v.replace('"', '""') + '"' for v in valores)


def gravar(caminho: Path, conteudo: str | bytes) -> None:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(conteudo, bytes):
        caminho.write_bytes(conteudo)
    else:
        caminho.write_text(conteudo, encoding="utf-8")


def gerar() -> None:
    # ---- PNCP: contratos e termos --------------------------------------------------------------
    pagina = {
        "data": [contrato(*c) for c in CONTRATOS],
        "totalRegistros": len(CONTRATOS),
        "totalPaginas": 1,
        "numeroPagina": 1,
        "paginasRestantes": 0,
        "empty": False,
    }
    gravar(
        DESTINO / "pncp" / "contratos_pagina1.json",
        json.dumps(pagina, ensure_ascii=False, indent=2) + "\n",
    )
    termos = {
        f"{ORGAO['cnpj']}/2026/{seq}": [termo(seq, *t) for t in lista]
        for seq, lista in TERMOS.items()
    }
    gravar(
        DESTINO / "termos" / "termos.json", json.dumps(termos, ensure_ascii=False, indent=2) + "\n"
    )

    # ---- Portal da Transparência: CEIS, CNEP e servidores --------------------------------------
    ceis = {
        cnpj(EMPRESAS["ZETA"][0]): [
            sancao(900001, "ZETA", "01/01/2025", "01/01/2027", "Impedimento sintético de contratar")
        ]
    }
    cnep = {
        cnpj(EMPRESAS["ETA"][0]): [
            sancao(900002, "ETA", "01/01/2020", "01/01/2021", "Multa sintética (Lei 12.846)")
        ]
    }
    servidores = {
        SIAPE_ORGAO: [
            # T7 dispara: mesmo nome e mesmos seis dígitos de um sócio de DELTA, EPSILON e ZETA.
            # O CPF vem completo (DV inválido) para mostrar que só o miolo é guardado.
            servidor(1, "RICARDO FICTICIO LIMA", _cpf_formatado(cpf_sintetico("666666"))),
            # T7 não dispara: mesmo nome de um sócio da DELTA, dígitos diferentes (homônimo).
            servidor(2, "PEDRO EXEMPLO ALVES", "***.404.040-**"),
            # T7 não dispara: ninguém com este nome é sócio.
            servidor(3, "SERVIDORA SEM VINCULO SINTETICA", "***.505.050-**"),
        ]
    }
    for nome, dados in (("ceis", ceis), ("cnep", cnep), ("servidores", servidores)):
        gravar(
            DESTINO / "portal" / f"{nome}.json",
            json.dumps(dados, ensure_ascii=False, indent=2) + "\n",
        )

    # ---- TSE: candidatos e receitas (UTF-8, como o arquivo de 2024) ----------------------------
    candidatos = [
        ["ANO_ELEICAO", "SG_UF", "SG_UE", "DS_CARGO", "SQ_CANDIDATO", "NR_CANDIDATO"]
        + ["NM_CANDIDATO", "NM_URNA_CANDIDATO", "NR_CPF_CANDIDATO", "SG_PARTIDO"]
        + ["DS_SIT_TOT_TURNO"],
        ["2024", "GO", "99001", "PREFEITO", "990000000001", "99", "CANDIDATO SINTETICO UM"]
        + ["SINTETICO UM", "-4", "PSIN", "ELEITO"],
        ["2024", "GO", "99001", "PREFEITO", "990000000002", "98", "CANDIDATA SINTETICA DOIS"]
        + ["SINTETICA DOIS", "-4", "PFIC", "NÃO ELEITO"],
        ["2024", "GO", "99002", "PREFEITO", "990000000003", "97", "CANDIDATO SINTETICO TRES"]
        + ["SINTETICO TRES", "-4", "PSIN", "ELEITO"],
    ]
    gravar(
        DESTINO / "tse" / "consulta_cand_2024_GO.csv",
        "\n".join(linha_csv(linha) for linha in candidatos) + "\n",
    )
    cabecalho = ["ANO_ELEICAO", "SG_UF", "SG_UE", "NM_UE", "DS_CARGO", "SQ_CANDIDATO"]
    cabecalho += ["NM_CANDIDATO", "NR_CPF_CANDIDATO", "SG_PARTIDO", "DS_ORIGEM_RECEITA"]
    cabecalho += ["NR_CPF_CNPJ_DOADOR", "NM_DOADOR", "NM_DOADOR_RFB", "DT_RECEITA", "VR_RECEITA"]

    def receita(sq, ue, municipio, candidato, origem, documento, doador, data, valor):
        return ["2024", "GO", ue, municipio, "PREFEITO", sq, candidato, "-4", "PSIN", origem] + [
            documento,
            doador,
            doador,
            data,
            valor,
        ]

    um = ("990000000001", "99001", "EXEMPLOPOLIS", "CANDIDATO SINTETICO UM")
    dois = ("990000000002", "99001", "EXEMPLOPOLIS", "CANDIDATA SINTETICA DOIS")
    tres = ("990000000003", "99002", "OUTRA CIDADE SINTETICA", "CANDIDATO SINTETICO TRES")
    pf = "Recursos de pessoas físicas"
    receitas = [
        cabecalho,
        # T9 dispara (pessoa física): possível coincidência com sócio de DELTA, EPSILON e ZETA.
        receita(*um, pf, cpf_sintetico("666666"), "RICARDO FICTICIO LIMA", "15/08/2024", "5000,00"),
        # T9 dispara (pessoa jurídica): a doadora ETA é contratada pela prefeitura.
        receita(
            *um,
            "Recursos de pessoas jurídicas (sintético)",
            cnpj(EMPRESAS["ETA"][0]),
            "ETA TRANSPORTES SINTETICA LTDA",
            "20/08/2024",
            "10000,00",
        ),
        # T9 não dispara: a candidata não foi eleita.
        receita(
            *dois, pf, cpf_sintetico("777777"), "LUCIA SINTETICA ROCHA", "16/08/2024", "800,00"
        ),
        # T9 não dispara: o candidato eleito é de outro município.
        receita(*tres, pf, cpf_sintetico("888888"), "MARCOS EXEMPLO NUNES", "17/08/2024", "900,00"),
        # Ignorada pelo conector: em "Recursos próprios" o doador é o próprio candidato.
        receita(
            *um,
            "Recursos próprios",
            cpf_sintetico("123123"),
            "CANDIDATO SINTETICO UM",
            "18/08/2024",
            "700,00",
        ),
        # T9 não dispara: mesmo nome de uma sócia da MU, dígitos do CPF diferentes (homônima).
        receita(*um, pf, cpf_sintetico("999999"), "TATIANA TESTE VIEIRA", "19/08/2024", "600,00"),
    ]
    gravar(
        DESTINO / "tse" / "receitas_candidatos_2024_GO.csv",
        "\n".join(linha_csv(linha) for linha in receitas) + "\n",
    )
    gravar(DESTINO / "tse" / "SINTETICO.txt", MARCADOR)

    # ---- Editais -------------------------------------------------------------------------------
    restritivo = (
        "EDITAL SINTÉTICO DE PREGÃO Nº 1/2026 - PREFEITURA SINTETICA DE EXEMPLOPOLIS\n"
        "\n"
        "1. DO OBJETO\n"
        "1.1. Contratação sintética de serviços de jardinagem.\n"
        "\n"
        "7. DA HABILITAÇÃO\n"
        "7.1. A licitante deverá apresentar certidão negativa de quitação de tributos "
        "municipais.\n"
        "7.2. Será exigida, ainda, Certidão de\n"
        "Quitação junto ao conselho profissional sintético.\n"
        "7.3. Demais documentos conforme o termo de referência.\n"
    )
    regular = (
        "EDITAL SINTÉTICO DE PREGÃO Nº 2/2026 - PREFEITURA SINTETICA DE EXEMPLOPOLIS\n"
        "\n"
        "7. DA HABILITAÇÃO\n"
        "7.1. A licitante deverá apresentar prova de regularidade fiscal e trabalhista, na forma "
        "da lei.\n"
        "7.2. A quitação de eventuais multas contratuais será tratada na execução.\n"
    )
    gravar(DESTINO / "editais" / "edital_restritivo.txt", restritivo)
    gravar(DESTINO / "editais" / "edital_regular.txt", regular)
    gravar(DESTINO / "editais" / "SINTETICO.txt", MARCADOR)
    gravar(DESTINO / "SINTETICO.txt", MARCADOR)


if __name__ == "__main__":
    gerar()
    print(f"Fixtures sintéticas do horizonte 2 geradas em {DESTINO.relative_to(RAIZ)}")
