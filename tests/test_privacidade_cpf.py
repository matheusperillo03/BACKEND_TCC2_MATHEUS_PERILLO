"""RNF04, risco 1 do piloto: o CPF completo que existe nas cargas brutas (PNCP, CEIS, TSE) não
sai da custódia por nenhum caminho da API. Todos os CPFs daqui são sintéticos e sorteados."""

from __future__ import annotations

import io
import json
import logging
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from buscador import privacidade
from buscador.api import criar_app
from buscador.conectores.pncp import TransporteSimulado
from buscador.conectores.simulados import transporte_portal
from buscador.conectores.tse_arquivo import ConectorTseArquivo
from buscador.modelos import Aresta, Captura, Coleta, Entidade
from buscador.privacidade import descaracterizar, descaracterizar_texto
from tests.conftest import cabecalhos, corpo_caso, criar_config, incluir_equipe
from tests.sinteticos import (
    cnpj_alfanumerico,
    cpf_com_dv_errado,
    cpf_sintetico,
    mascarado,
    mascarado_pontuado,
    pontuado,
)

ORGAO = "90100001000109"
EMPRESA = "90000001000129"
PERIODO = {"dataInicial": "20260101", "dataFinal": "20261231"}


# ==== A função de descaracterização ==============================================================


def test_descaracteriza_cpf_pontuado_solto_e_em_campo_de_documento():
    cpf = cpf_sintetico()
    assert descaracterizar_texto(f"CPF {pontuado(cpf)}.") == f"CPF {mascarado_pontuado(cpf)}."
    assert descaracterizar_texto(f'"niFornecedor":"{cpf}"') == f'"niFornecedor":"{mascarado(cpf)}"'
    assert descaracterizar_texto(f"JOAO SINTETICO {cpf}") == f"JOAO SINTETICO {mascarado(cpf)}"
    assert descaracterizar_texto(f"C.P.F.{pontuado(cpf)}") == f"C.P.F.{mascarado_pontuado(cpf)}"
    assert descaracterizar_texto(f"fim.{cpf};{cpf}") == f"fim.{mascarado(cpf)};{mascarado(cpf)}"
    # Nove dígitos, hífen e dois: forma própria de CPF, mesmo com DV errado.
    errado = cpf_com_dv_errado(cpf)
    assert descaracterizar_texto(f"{errado[:9]}-{errado[9:]}") == f"***{errado[3:9]}-**"
    # Sem DV válido e sem contexto, 11 dígitos não são CPF; com contexto, são.
    assert descaracterizar_texto(f"protocolo {errado}") == f"protocolo {errado}"
    assert descaracterizar_texto(f"CPF nº {errado}") == f"CPF nº {mascarado(errado)}"
    assert descaracterizar({"niFornecedor": errado}) == {"niFornecedor": mascarado(errado)}
    assert descaracterizar({"cpfDoador": [errado]}) == {"cpfDoador": [mascarado(errado)]}
    assert descaracterizar({"sq_candidato": cpf, "candidatos": [cpf]}) == {
        "sq_candidato": cpf,
        "candidatos": [cpf],
    }


def test_descaracterizacao_preserva_comprimento_e_nao_mexe_em_cnpj_nem_em_numero():
    cpf = cpf_sintetico()
    texto = f"a {pontuado(cpf)} b {cpf} c"
    assert len(descaracterizar_texto(texto)) == len(texto)
    alfanumerico = cnpj_alfanumerico()
    for intacto in (
        EMPRESA,
        "90.000.001/0001-29",
        alfanumerico,
        f"{alfanumerico[:2]}.{alfanumerico[2:5]}.{alfanumerico[5:8]}/{alfanumerico[8:12]}-"
        f"{alfanumerico[12:]}",
        f"X{cpf}",  # colado numa letra: parte de um identificador
        f"{cpf}7",  # 12 dígitos
        f"0.{cpf}",  # parte decimal de um número
        "90100001000109-2-000001/2026",
        "2026-11-17T10:00:00.123456+00:00",
        "***.123.456-**",
        "***123456**",
        "R$ 123.456.789,00",
        "",
    ):
        assert descaracterizar_texto(intacto) == intacto
    assert descaracterizar(12345678909) == 12345678909 and descaracterizar(None) is None


# ==== Apoio: caso com PNCP (fornecedor pessoa física) ============================================


def _contrato(numero: int, tipo: str, ni: str, nome: str, objeto: str) -> dict:
    return {
        "numeroControlePNCP": f"{ORGAO}-2-{numero:06d}/2026",
        "numeroControlePncpCompra": f"{ORGAO}-1-{numero:06d}/2026",
        "orgaoEntidade": {
            "cnpj": ORGAO,
            "razaoSocial": "PREFEITURA SINTETICA",
            "poderId": "E",
            "esferaId": "M",
        },
        "unidadeOrgao": {"ufSigla": "DF", "municipioNome": "Exemplo", "codigoUnidade": "900001"},
        "anoContrato": 2026,
        "sequencialContrato": numero,
        "tipoPessoa": tipo,
        "niFornecedor": ni,
        "nomeRazaoSocialFornecedor": nome,
        "objetoContrato": objeto,
        "valorInicial": 1000.0,
        "valorGlobal": 1000.0,
        "dataAssinatura": "2026-03-15",
        "dataPublicacaoPncp": "2026-03-15T10:00:00",
    }


def _pagina(*contratos: dict) -> bytes:
    corpo = {
        "data": list(contratos),
        "totalRegistros": len(contratos),
        "totalPaginas": 1,
        "numeroPagina": 1,
        "paginasRestantes": 0,
        "empty": False,
    }
    return json.dumps(corpo, ensure_ascii=False).encode("utf-8")


@pytest.fixture
def cpfs() -> dict[str, str]:
    """CPFs sintéticos distintos: fornecedor, citado no objeto, no nome do MEI e sancionado."""
    achados: set[str] = set()
    while len(achados) < 5:
        achados.add(cpf_sintetico())
    fornecedor, objeto, mei, sancionado, candidato = sorted(achados)
    return {
        "fornecedor": fornecedor,
        "objeto": objeto,
        "mei": mei,
        "sancionado": sancionado,
        "candidato": candidato,
    }


def _paginas(cpfs: dict[str, str], objeto_pf: str) -> dict[int, bytes]:
    return {
        1: _pagina(
            _contrato(1, "PF", cpfs["fornecedor"], "FULANO SINTETICO DE TAL", objeto_pf),
            _contrato(
                2,
                "PJ",
                EMPRESA,
                f"BELTRANO SINTETICO {cpfs['mei']}",
                f"Locação; fiador CPF {pontuado(cpfs['objeto'])}",
            ),
        )
    }


@pytest.fixture
def ambiente(tmp_path: Path, cpfs):
    """Aplicação com PNCP e Portal simulados e a raiz do TSE; devolve (cliente, bases)."""
    transporte = TransporteSimulado(_paginas(cpfs, "Serviço sintético de pintura"))
    # Não vazio: ``transporte_portal`` troca um dicionário vazio por outro (``ceis or {}``).
    ceis: dict[str, list] = {"00000000000000": []}
    raiz_tse = tmp_path / "ingestao_tse"
    (raiz_tse / "t").mkdir(parents=True)
    (raiz_tse / "t" / "SINTETICO.txt").write_text("dados sintéticos", encoding="utf-8")
    config = criar_config(
        tmp_path,
        None,
        tse_dir_base=raiz_tse,
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
        assert r.status_code == 201, r.text
        caso_id = r.json()["id"]
        incluir_equipe(cliente, caso_id)
        yield {
            "cliente": cliente,
            "app": app,
            "caso_id": caso_id,
            "pncp": transporte,
            "ceis": ceis,
            "tse": raiz_tse / "t",
        }


def _coletar(amb, fonte: str, parametros: dict) -> dict:
    r = amb["cliente"].post(
        f"/casos/{amb['caso_id']}/coletas",
        json={"fonte": fonte, "parametros": parametros},
        headers=cabecalhos(),
    )
    assert r.status_code == 200, r.text
    assert r.json()["situacao"] == "sucesso", r.text
    return r.json()


def _sem_cpf(texto: str, cpfs: dict[str, str]) -> None:
    for papel, cpf in cpfs.items():
        assert cpf not in texto, f"CPF completo ({papel}) vazou"
        assert pontuado(cpf) not in texto, f"CPF pontuado ({papel}) vazou"


# ==== Caminhos da API ============================================================================


def test_custodia_guarda_o_original_e_as_entidades_so_o_descaracterizado(ambiente, cpfs):
    coleta = _coletar(ambiente, "pncp", PERIODO)
    with ambiente["app"].state.fabrica_sessoes() as s:
        captura = s.get(Captura, coleta["captura_ids"][0])
        bruto = ambiente["app"].state.custodia.ler(captura)
        # A prova não muda: os bytes guardados são os da fonte, com o CPF completo.
        assert cpfs["fornecedor"].encode() in bruto
        entidades = list(s.execute(select(Entidade)).scalars())
        arestas = list(s.execute(select(Aresta)).scalars())
        gravado = json.dumps(
            [[e.chave, e.identificador, e.rotulo, e.atributos] for e in entidades]
            + [a.atributos for a in arestas]
            + [c.parametros for c in s.execute(select(Coleta)).scalars()],
            default=str,
            ensure_ascii=False,
        )
    _sem_cpf(gravado, cpfs)
    # Texto livre: o CPF citado no objeto e o do nome empresarial ficam descaracterizados.
    assert mascarado_pontuado(cpfs["objeto"]) in gravado
    assert f"BELTRANO SINTETICO {mascarado(cpfs['mei'])}" in gravado
    assert mascarado(cpfs["fornecedor"]) in gravado
    r = ambiente["cliente"].get(f"/capturas/{captura.id}/verificar", headers=cabecalhos())
    assert r.json()["integra"] is True


def test_entidades_grafo_alertas_relatorio_e_exportacoes_sem_cpf_completo(ambiente, cpfs):
    _coletar(ambiente, "pncp", PERIODO)
    cliente, caso_id = ambiente["cliente"], ambiente["caso_id"]
    h = cabecalhos("tok-delegado")
    cliente.post(f"/casos/{caso_id}/alertas/executar", json={}, headers=cabecalhos())
    textos = [
        cliente.get(f"/casos/{caso_id}/entidades", headers=h).text,
        cliente.get(f"/casos/{caso_id}/grafo", headers=h).text,
        cliente.get(f"/casos/{caso_id}/alertas", headers=h).text,
        cliente.get(f"/casos/{caso_id}/coletas", headers=h).text,
        cliente.get(f"/casos/{caso_id}/relatorio", headers=h).text,
        cliente.get(f"/casos/{caso_id}/relatorio", params={"formato": "md"}, headers=h).text,
    ]
    for entidade in cliente.get(f"/casos/{caso_id}/entidades", headers=h).json():
        textos.append(cliente.get(f"/casos/{caso_id}/entidades/{entidade['id']}", headers=h).text)
    for formato in ("graphml", "csv_entidades", "csv_vinculos"):
        r = cliente.get(f"/casos/{caso_id}/exportacao", params={"formato": formato}, headers=h)
        assert r.status_code == 200, r.text
        textos.append(r.content.decode("utf-8"))
    r = cliente.get(f"/casos/{caso_id}/exportacao", params={"formato": "zip"}, headers=h)
    with zipfile.ZipFile(io.BytesIO(r.content)) as pacote:
        textos.extend(pacote.read(nome).decode("utf-8") for nome in pacote.namelist())
    for texto in textos:
        _sem_cpf(texto, cpfs)
    assert mascarado(cpfs["fornecedor"]) in textos[0]
    assert any(mascarado(cpfs["fornecedor"]) in t for t in textos[-5:])  # no pacote exportado


def test_comparacao_de_capturas_campo_a_campo_sem_cpf_completo(ambiente, cpfs):
    cliente, caso_id = ambiente["cliente"], ambiente["caso_id"]
    primeira = _coletar(ambiente, "pncp", PERIODO)
    # Entre as duas coletas muda o fornecedor PF (outro CPF), o objeto e o nome com CPF.
    outro = cpf_sintetico()
    novos = {**cpfs, "fornecedor": outro}
    ambiente["pncp"].paginas = _paginas(novos, f"Pintura; preposto {cpfs['fornecedor']}")
    r = cliente.post(
        f"/casos/{caso_id}/coletas/{primeira['coleta_id']}/recoletar", headers=cabecalhos()
    )
    assert r.status_code == 200, r.text
    resposta = r.json()
    par = resposta["comparacao"]["capturas"][0]
    assert par["mesmo_hash"] is False and "niFornecedor" in par["campos_alterados"]
    campos = {c["campo"]: c for d in par["diferencas"] for c in d["campos"]}
    assert campos["niFornecedor"] == {
        "campo": "niFornecedor",
        "antes": mascarado(cpfs["fornecedor"]),
        "depois": mascarado(outro),
    }
    assert (
        campos["objetoContrato"]["depois"] == f"Pintura; preposto {mascarado(cpfs['fornecedor'])}"
    )
    saidas = [
        r.text,
        cliente.get(f"/capturas/{par['captura_id']}/comparar", headers=cabecalhos()).text,
        cliente.get(
            f"/casos/{caso_id}/coletas/{resposta['coleta_id']}/comparacao", headers=cabecalhos()
        ).text,
        cliente.get(
            "/auditoria", params={"caso_id": caso_id}, headers=cabecalhos("tok-delegado")
        ).text,
    ]
    for saida in saidas:
        _sem_cpf(saida, {**cpfs, "outro": outro})
    assert mascarado(outro) in saidas[1] and mascarado(outro) in saidas[2]


def test_comparacao_com_cpf_de_dv_invalido_em_campo_de_documento(ambiente, cpfs):
    """Num campo de documento, 11 dígitos são ocultados mesmo sem DV válido."""
    cliente, caso_id = ambiente["cliente"], ambiente["caso_id"]
    errado = cpf_com_dv_errado(cpfs["fornecedor"])
    primeira = _coletar(ambiente, "pncp", PERIODO)
    ambiente["pncp"].paginas = _paginas({**cpfs, "fornecedor": errado}, "Serviço sintético")
    r = cliente.post(
        f"/casos/{caso_id}/coletas/{primeira['coleta_id']}/recoletar", headers=cabecalhos()
    )
    assert errado not in r.text and mascarado(errado) in r.text


def test_rota_de_texto_da_captura_devolve_cpf_descaracterizado(ambiente, cpfs):
    cliente, caso_id = ambiente["cliente"], ambiente["caso_id"]
    coleta = _coletar(ambiente, "pncp", PERIODO)
    captura_id = coleta["captura_ids"][0]
    r = cliente.get(f"/casos/{caso_id}/capturas/{captura_id}/texto", headers=cabecalhos())
    assert r.status_code == 200, r.text
    corpo = r.json()
    _sem_cpf(r.text, cpfs)
    assert corpo["cpfs_descaracterizados"] is True
    assert f'"niFornecedor": "{mascarado(cpfs["fornecedor"])}"' in corpo["texto"]
    assert mascarado_pontuado(cpfs["objeto"]) in corpo["texto"]
    assert EMPRESA in corpo["texto"] and ORGAO in corpo["texto"]  # CNPJ continua inteiro
    with ambiente["app"].state.fabrica_sessoes() as s:
        original = ambiente["app"].state.custodia.ler(s.get(Captura, captura_id)).decode("utf-8")
    # Mesmo comprimento do original: as posições dos trechos continuam valendo.
    assert len(corpo["texto"]) == len(original) and corpo["texto"] != original
    # Não há rota que devolva os bytes originais da captura.
    caminhos = set(ambiente["app"].openapi()["paths"])
    assert not any(
        p.startswith("/capturas") and p.rsplit("/", 1)[-1] in ("original", "download", "bruto")
        for p in caminhos
    )
    assert {p for p in caminhos if p.startswith("/capturas")} == {
        "/capturas/{captura_id}",
        "/capturas/{captura_id}/comparar",
        "/capturas/{captura_id}/verificar",
    }


def test_auditoria_nao_guarda_cpf_do_termo_de_busca_nem_de_parametros(ambiente, cpfs):
    cliente, caso_id = ambiente["cliente"], ambiente["caso_id"]
    _coletar(ambiente, "pncp", PERIODO)
    cpf = cpfs["fornecedor"]
    for termo in (cpf, pontuado(cpf)):
        r = cliente.get(f"/casos/{caso_id}/entidades", params={"q": termo}, headers=cabecalhos())
        assert r.status_code == 200
    # Filtro de fornecedor por CPF é recusado (ficaria nos parâmetros da coleta).
    r = cliente.post(
        f"/casos/{caso_id}/coletas",
        json={"fonte": "pncp", "parametros": {**PERIODO, "cnpjFornecedor": cpf}},
        headers=cabecalhos(),
    )
    assert r.status_code == 422 and "CPF" in r.text
    h = cabecalhos("tok-delegado")
    trilha = cliente.get("/auditoria", params={"caso_id": caso_id}, headers=h)
    _sem_cpf(trilha.text, cpfs)
    buscas = [e for e in trilha.json() if e["acao"] == "entidade.buscar"]
    assert {e["detalhes"]["q"] for e in buscas} == {mascarado(cpf), mascarado_pontuado(cpf)}
    _sem_cpf(cliente.get(f"/casos/{caso_id}/coletas", headers=h).text, cpfs)
    assert cliente.get("/auditoria/verificar", headers=h).json()["integra"] is True


def test_registros_de_log_saem_com_cpf_descaracterizado(caplog):
    privacidade.instalar_filtro_de_registros()
    privacidade.instalar_filtro_de_registros()  # idempotente
    cpf = cpf_sintetico()
    registro = logging.getLogger("buscador.teste_privacidade")
    with caplog.at_level(logging.INFO):
        registro.info("fornecedor %s (%s)", cpf, pontuado(cpf))
        registro.info(f"GET /casos/x/entidades?q={cpf} 200")
        try:
            raise ValueError(f"documento {pontuado(cpf)} inválido")
        except ValueError:
            registro.exception("falha com %s", {"ni": cpf})
    assert len(caplog.records) == 3
    saida = "\n".join(logging.Formatter().format(r) for r in caplog.records) + caplog.text
    assert cpf not in saida and pontuado(cpf) not in saida
    assert mascarado(cpf) in saida and mascarado_pontuado(cpf) in saida
    assert "ValueError" in saida


def test_ceis_com_sancionado_pessoa_fisica_nao_vira_entidade_nem_aparece_na_comparacao(
    ambiente, cpfs
):
    cliente, caso_id = ambiente["cliente"], ambiente["caso_id"]
    cpf = cpfs["sancionado"]

    def sancao(ident: int, codigo: str, tipo: str, nome: str) -> dict:
        pessoa = {"tipo": tipo, "nome": nome}
        if tipo == "J":
            pessoa["cnpjFormatado"] = codigo
        else:
            pessoa["cpfFormatado"] = codigo
        return {
            "id": ident,
            "dataInicioSancao": "01/01/2025",
            "dataFimSancao": "01/01/2027",
            "tipoSancao": {"descricaoResumida": "Impedimento sintético"},
            "orgaoSancionador": {"nome": "ORGAO SINTETICO"},
            "sancionado": {"nome": nome, "codigoFormatado": codigo},
            "pessoa": pessoa,
            "numeroProcesso": "SINT-1/2025",
        }

    ambiente["ceis"][EMPRESA] = [
        sancao(1, "90.000.001/0001-29", "J", "ALFA SINTETICA LTDA"),
        sancao(2, pontuado(cpf), "F", "SANCIONADO SINTETICO PF"),
    ]
    parametros = {"recurso": "ceis", "codigoSancionado": [EMPRESA]}
    primeira = _coletar(ambiente, "portal_transparencia", parametros)
    assert any("pessoa física" in a for a in primeira["avisos"]), primeira
    # Na recoleta o registro da pessoa física muda (CPF sem pontuação e outro processo).
    ambiente["ceis"][EMPRESA][1] = {
        **sancao(2, cpf, "F", "SANCIONADO SINTETICO PF"),
        "numeroProcesso": "SINT-2/2025",
    }
    r = cliente.post(
        f"/casos/{caso_id}/coletas/{primeira['coleta_id']}/recoletar", headers=cabecalhos()
    )
    assert r.status_code == 200, r.text
    par = r.json()["comparacao"]["capturas"][0]
    assert par["registros"]["alterados"] == 1
    campos = {c["campo"]: c for c in par["diferencas"][0]["campos"]}
    assert campos["sancionado.codigoFormatado"]["antes"] == mascarado_pontuado(cpf)
    assert campos["sancionado.codigoFormatado"]["depois"] == mascarado(cpf)
    h = cabecalhos("tok-delegado")
    saidas = [
        r.text,
        cliente.get(f"/capturas/{par['captura_id']}/comparar", headers=h).text,
        cliente.get(f"/casos/{caso_id}/capturas/{par['captura_id']}/texto", headers=h).text,
        cliente.get(f"/casos/{caso_id}/entidades", headers=h).text,
        cliente.get(f"/casos/{caso_id}/relatorio", headers=h).text,
        cliente.get("/auditoria", params={"caso_id": caso_id}, headers=h).text,
    ]
    for saida in saidas:
        _sem_cpf(saida, cpfs)
    assert "SANCIONADO SINTETICO PF" not in saidas[3]  # pessoa física não vira entidade


# ==== TSE: NR_CPF_CANDIDATO ======================================================================

_CAND = (
    '"ANO_ELEICAO";"SG_UF";"SG_UE";"NM_UE";"DS_CARGO";"SQ_CANDIDATO";"NM_CANDIDATO";'
    '"NM_URNA_CANDIDATO";"NR_CPF_CANDIDATO";"SG_PARTIDO";"DS_SIT_TOT_TURNO"\n'
    '"2026";"DF";"DF";"DISTRITO FEDERAL";"DEPUTADO DISTRITAL";"990000000009";'
    '"CANDIDATO SINTETICO NOVE";"SINTETICO NOVE";"{cpf}";"PSIN";"ELEITO"\n'
)


def test_tse_nr_cpf_candidato_nunca_e_lido_e_o_texto_da_captura_o_oculta(ambiente, cpfs):
    cliente, caso_id = ambiente["cliente"], ambiente["caso_id"]
    cpf = cpfs["candidato"]
    arquivo = ambiente["tse"] / "consulta_cand_2026_DF.csv"
    arquivo.write_bytes(_CAND.format(cpf=cpf).encode("latin-1"))
    coleta = _coletar(ambiente, "tse_arquivo", {"diretorio": "t"})
    h = cabecalhos("tok-delegado")
    saidas = [
        json.dumps(coleta),
        cliente.get(f"/casos/{caso_id}/entidades", headers=h).text,
        cliente.get(f"/casos/{caso_id}/relatorio", headers=h).text,
        cliente.get(f"/casos/{caso_id}/exportacao", params={"formato": "graphml"}, headers=h).text,
        cliente.get("/auditoria", params={"caso_id": caso_id}, headers=h).text,
    ]
    texto = cliente.get(
        f"/casos/{caso_id}/capturas/{coleta['captura_ids'][0]}/texto", headers=h
    ).json()
    saidas.append(json.dumps(texto, ensure_ascii=False))
    for saida in saidas:
        _sem_cpf(saida, cpfs)
    assert f'"{mascarado(cpf)}";"PSIN"' in texto["texto"] and texto["cpfs_descaracterizados"]
    assert "CANDIDATO SINTETICO NOVE" in saidas[1]
    # A carga bruta (a prova) continua com a coluna como veio.
    with ambiente["app"].state.fabrica_sessoes() as s:
        bruto = ambiente["app"].state.custodia.ler(s.get(Captura, coleta["captura_ids"][0]))
    assert cpf.encode() in bruto


def test_tse_registros_nao_dependem_da_coluna_nr_cpf_candidato(tmp_path):
    """O conector não lê ``NR_CPF_CANDIDATO``: com a coluna preenchida, vazia ou ausente, os
    registros são idênticos e nenhum campo deles contém o CPF."""
    cpf = cpf_sintetico()

    def registros(conteudo: str) -> list:
        pasta = tmp_path / f"t{len(list(tmp_path.iterdir()))}"
        pasta.mkdir()
        (pasta / "consulta_cand_2026_DF.csv").write_bytes(conteudo.encode("latin-1"))
        capturas: list[dict] = []

        def registrar(fonte, origem, conteudo, metadados):
            capturas.append(metadados)
            return type("C", (), {"id": "cap", "url_ou_arquivo": origem})()

        resultado = ConectorTseArquivo(dir_base=tmp_path).coletar(
            {"diretorio": pasta.name}, registrar
        )
        return resultado.registros

    com = registros(_CAND.format(cpf=cpf))
    sem = registros(_CAND.format(cpf="-4"))
    ausente = registros(
        _CAND.format(cpf="x").replace('"NR_CPF_CANDIDATO";', "").replace('"x";', "")
    )
    assert com == sem == ausente and len(com) == 1
    assert cpf not in repr(com)
