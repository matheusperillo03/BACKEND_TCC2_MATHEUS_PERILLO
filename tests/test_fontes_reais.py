"""Regressões das divergências achadas no teste com as fontes reais (17/11/2026).

Só dados sintéticos: cada teste reproduz, com valores inventados, o FORMATO observado na fonte
real (sócio pessoa jurídica com CNPJ básico de 8 dígitos; fornecedor pessoa física com CPF
completo no PNCP; ``AA_ELEICAO`` no arquivo de receitas do TSE) e os scripts de apoio ao piloto
(``scripts/recortar_cnpj.py`` e ``scripts/testar_fontes_reais.py``), sem rede.
"""

from __future__ import annotations

import importlib.util
import io
import json
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from buscador.conectores.base import RegistroContrato, RegistroDoacao, RegistroSocio
from buscador.conectores.cnpj_arquivo import ConectorCnpjArquivo
from buscador.conectores.tse_arquivo import ConectorTseArquivo
from buscador.modelos import Aresta, Caso, Entidade
from buscador.resolucao import Resolvedor
from tests import sinteticos

RAIZ = Path(__file__).resolve().parents[1]


def _script(nome: str):
    especificacao = importlib.util.spec_from_file_location(nome, RAIZ / "scripts" / f"{nome}.py")
    modulo = importlib.util.module_from_spec(especificacao)
    sys.modules[nome] = modulo  # as dataclasses do script procuram o módulo pelo nome
    especificacao.loader.exec_module(modulo)
    return modulo


@pytest.fixture
def caso(sessao):
    caso = Caso(titulo="c", criado_por="t")
    sessao.add(caso)
    sessao.flush()
    return caso


def _captura(sessao, custodia, caso) -> str:
    return custodia.registrar(
        sessao,
        caso_id=caso.id,
        fonte="cnpj_arquivo",
        url_ou_arquivo="simulado://teste",
        conteudo=b"sintetico",
        metadados={"simulado": True},
    ).id


def test_socio_pessoa_juridica_com_cnpj_basico_de_8_digitos_vira_empresa(sessao, custodia, caso):
    """Nos arquivos de 11/2026 o sócio PJ veio com o CNPJ básico (8 dígitos), não com 14."""
    captura_id = _captura(sessao, custodia, caso)
    registros = [
        RegistroSocio(captura_id, "90000001", "1", "HOLDING SINTETICA SA", "90000077"),
        RegistroSocio(captura_id, "90000002", "1", "HOLDING SINTETICA SA", "90000077000155"),
        RegistroSocio(captura_id, "90000001", "2", "FULANO SINTETICO", "***123456**"),
    ]
    Resolvedor(sessao, caso.id).incorporar(registros)
    sessao.commit()
    entidades = {e.id: e for e in sessao.execute(select(Entidade)).scalars()}
    por_chave = {(e.tipo, e.identificador): e for e in entidades.values()}
    holding = por_chave[("empresa", "90000077")]
    assert holding.rotulo == "HOLDING SINTETICA SA"
    # Os dois formatos (8 e 14 dígitos) caem na MESMA empresa, e ela não vira "pessoa".
    assert [e.rotulo for e in entidades.values() if e.tipo == "pessoa"] == ["FULANO SINTETICO"]
    socias = [
        entidades[a.destino_id].identificador
        for a in sessao.execute(select(Aresta).where(Aresta.tipo == "socio_de")).scalars()
        if a.origem_id == holding.id
    ]
    assert sorted(socias) == ["90000001", "90000002"]


def test_cpf_de_fornecedor_pessoa_fisica_nao_fica_completo_no_contrato(sessao, custodia, caso):
    """O PNCP traz o CPF completo do fornecedor PF; o contrato guarda só o descaracterizado."""
    captura_id = _captura(sessao, custodia, caso)
    cpf_sintetico = sinteticos.cpf_sintetico()  # sorteado: nenhum CPF de DV válido fica no git

    def contrato(numero: str, tipo: str, ni: str) -> RegistroContrato:
        return RegistroContrato(
            captura_id=captura_id,
            numero_controle_pncp=numero,
            ni_fornecedor=ni,
            tipo_pessoa_fornecedor=tipo,
            nome_fornecedor="FORNECEDOR SINTETICO",
            orgao_cnpj="90100001000109",
            orgao_razao_social="ORGAO SINTETICO",
            objeto="Objeto sintético",
            valor_global=1000.0,
            data_assinatura="2026-03-01",
            url_fonte="simulado://teste",
        )

    Resolvedor(sessao, caso.id).incorporar(
        [
            contrato("90100001000109-2-000001/2026", "PF", cpf_sintetico),
            contrato("90100001000109-2-000002/2026", "PJ", "90000001000129"),
        ]
    )
    sessao.commit()
    entidades = list(sessao.execute(select(Entidade)).scalars())
    arestas = list(sessao.execute(select(Aresta)).scalars())
    tudo = json.dumps(
        [[e.identificador, e.rotulo, e.atributos] for e in entidades]
        + [a.atributos for a in arestas],
        default=str,
    )
    assert cpf_sintetico not in tudo
    contratos = {e.identificador: e.atributos for e in entidades if e.tipo == "contrato"}
    assert contratos["90100001000109-2-000001/2026"]["ni_fornecedor"] == sinteticos.mascarado(
        cpf_sintetico
    )
    # O CNPJ continua inteiro (a T4 e a consulta de sanções precisam dele).
    assert contratos["90100001000109-2-000002/2026"]["ni_fornecedor"] == "90000001000129"
    pessoa = next(e for e in entidades if e.tipo == "pessoa")
    assert pessoa.identificador == sinteticos.mascarado(cpf_sintetico)


def test_tse_le_o_ano_de_aa_eleicao_no_arquivo_de_receitas(tmp_path):
    """O arquivo real de receitas chama o ano de ``AA_ELEICAO`` (o de candidatos, ANO_ELEICAO);
    o nome do arquivo abaixo não traz o ano, de propósito."""
    pasta = tmp_path / "t"
    pasta.mkdir()
    (pasta / "SINTETICO.txt").write_text("sintetico\n", encoding="utf-8")
    colunas = [
        "AA_ELEICAO",
        "SG_UF",
        "SG_UE",
        "NM_UE",
        "DS_CARGO",
        "SQ_CANDIDATO",
        "NM_CANDIDATO",
        "NR_CPF_CANDIDATO",
        "SG_PARTIDO",
        "DS_ORIGEM_RECEITA",
        "NR_CPF_CNPJ_DOADOR",
        "NM_DOADOR",
        "NM_DOADOR_RFB",
        "DT_RECEITA",
        "VR_RECEITA",
    ]
    linha = [
        "2026",
        "ZZ",
        "ZZ",
        "UNIDADE SINTETICA",
        "DEPUTADO DISTRITAL",
        "990000000001",
        "CANDIDATA SINTETICA",
        "-4",
        "PSX",
        "Recursos de pessoas jurídicas",
        "90000001000129",
        "ALFA SINTETICA LTDA",
        "ALFA SINTETICA LTDA",
        "05/09/2026",
        "1234,56",
    ]
    texto = "\n".join(";".join(f'"{c}"' for c in x) for x in (colunas, linha)) + "\n"
    (pasta / "receitas_candidatos_recorte.csv").write_bytes(texto.encode("latin-1"))
    capturas = []

    def registrar(fonte, origem, conteudo, metadados):
        capturas.append(metadados)
        return SimpleNamespace(id="cap1", url_ou_arquivo=origem)

    resultado = ConectorTseArquivo(dir_base=tmp_path).coletar({"diretorio": "t"}, registrar)
    doacao = next(r for r in resultado.registros if isinstance(r, RegistroDoacao))
    assert (doacao.ano, doacao.valor, doacao.data) == ("2026", 1234.56, "2026-09-05")
    assert ConectorTseArquivo.versao == "1.2.0"


# ---- scripts/recortar_cnpj.py -------------------------------------------------------------------


def _linha(*campos: str) -> bytes:
    return (";".join(f'"{c}"' for c in campos) + "\r\n").encode("latin-1")


def _zip(caminho: Path, membro: str, conteudo: bytes) -> None:
    with zipfile.ZipFile(caminho, "w", zipfile.ZIP_DEFLATED) as pacote:
        pacote.writestr(membro, conteudo)


def test_recorte_do_cnpj_le_os_zips_em_fluxo_e_o_conector_le_o_recorte(tmp_path):
    recortar = _script("recortar_cnpj")
    zips = tmp_path / "zips"
    zips.mkdir()
    empresa = lambda b, nome: _linha(b, nome, "2062", "49", "1000,00", "01", "")  # noqa: E731
    estab = lambda b, uf: _linha(  # noqa: E731
        b, "0001", "29", "1", "FANTASIA", "02", "20200101", "00", "", "", "20200101", "4120400",
        "", "RUA", "SINTETICA", "10", "", "CENTRO", "70000000", uf, "9701", "", "", "", "", "", "",
        "contato@exemplo.invalid", "", "",
    )  # fmt: skip
    socio = lambda b, nome, doc, ident: _linha(  # noqa: E731
        b, ident, nome, doc, "49", "20200101", "", "***000000**", "", "00", "5"
    )
    # A empresa 90000001 é a PRIMEIRA linha de um arquivo e a ÚLTIMA (sem quebra final) de outro.
    _zip(
        zips / "Empresas0.zip",
        "K3241.K03200Y0.D61114.EMPRECSV",
        empresa("90000001", "ALFA SINTÉTICA LTDA") + empresa("90000002", "BETA SINTETICA LTDA"),
    )
    _zip(
        zips / "Empresas1.zip",
        "K3241.K03200Y1.D61114.EMPRECSV",
        empresa("90000003", "GAMA SINTETICA LTDA") + empresa("90000009", "OUTRA LTDA"),
    )
    _zip(
        zips / "Estabelecimentos0.zip",
        "K3241.K03200Y0.D61114.ESTABELE",
        estab("90000002", "DF") + estab("90000001", "DF").rstrip(b"\r\n"),
    )
    _zip(
        zips / "Socios0.zip",
        "K3241.K03200Y0.D61114.SOCIOCSV",
        socio("90000001", "FULANO SINTETICO", "***123456**", "2")
        + socio("90000009", "ALFA SINTETICA", "90000001", "1")  # 90000001 fora do 1º campo
        + socio("90000003", "HOLDING SINTETICA", "90000077", "1"),
    )
    # Tabela Simples (opção pelo Simples e pelo MEI): um ZIP só, lido como os demais.
    _zip(
        zips / "Simples.zip",
        "F.K03200$W.SIMPLES.CSV.D61114",
        _linha("90000001", "S", "20200101", "00000000", "S", "20200101", "00000000")
        + _linha("90000009", "N", "00000000", "00000000", "N", "00000000", "00000000"),
    )
    (zips / "Cnaes.zip").write_bytes(b"nao e lido")
    saida = tmp_path / "ingestao" / "recorte"
    lista = tmp_path / "lista.txt"
    lista.write_text("# comentário\n90.000.001/0001-29\n90000003\n", encoding="utf-8")
    assert (
        recortar.principal(["--zips", str(zips), "--saida", str(saida), "--lista", str(lista)]) == 0
    )
    manifesto = json.loads((saida / "MANIFESTO.json").read_text(encoding="utf-8"))
    assert manifesto["cnpjs_basicos"] == ["90000001", "90000003"]
    assert {t: d["linhas"] for t, d in manifesto["tabelas"].items()} == {
        "empresas": 2,
        "estabelecimentos": 1,
        "socios": 2,
        "simples": 1,
    }
    assert all(len(o["sha256"]) == 64 for o in manifesto["origem"])
    # As linhas do recorte têm os mesmos bytes das linhas de origem (acento em Latin-1).
    assert (saida / "K3241.RECORTE.EMPRECSV").read_bytes() == empresa(
        "90000001", "ALFA SINTÉTICA LTDA"
    ) + empresa("90000003", "GAMA SINTETICA LTDA")
    # O conector lê o recorte (o manifesto é ignorado) e as capturas não são sintéticas.
    capturas = []

    def registrar(fonte, origem, conteudo, metadados):
        capturas.append((origem, metadados))
        conteudo.read()
        return SimpleNamespace(id=f"cap{len(capturas)}", url_ou_arquivo=origem)

    resultado = ConectorCnpjArquivo(dir_base=tmp_path / "ingestao").coletar(
        {"diretorio": "recorte"}, registrar
    )
    assert len(resultado.registros) == 6 and len(capturas) == 4
    assert all(m["simulado"] is False for _, m in capturas)


def test_recorte_do_cnpj_filtra_por_blocos_pequenos_e_recusa_entrada_invalida(tmp_path):
    recortar = _script("recortar_cnpj")
    linhas = [_linha(f"{n:08d}", f"EMPRESA {n}", "x" * (n % 50)) for n in range(1, 400)]
    for bloco in (7, 64, 1000, 10**6):
        achadas = list(
            recortar.filtrar_linhas(io.BytesIO(b"".join(linhas)), ["00000001", "00000399"], bloco)
        )
        assert achadas == [linhas[0], linhas[-1]]
    assert recortar.cnpjs_basicos(["90.000.001/0001-29", "90000001", "90000002"]) == [
        "90000001",
        "90000002",
    ]
    with pytest.raises(ValueError, match="8 ou 14"):
        recortar.cnpjs_basicos(["123"])
    assert (
        recortar.principal(
            [
                "--zips",
                str(tmp_path / "nao-existe"),
                "--saida",
                str(tmp_path / "s"),
                "--cnpj",
                "90000001",
            ]
        )
        == 1
    )


# ---- scripts/testar_fontes_reais.py (sem rede) --------------------------------------------------


def test_teste_de_fumaca_pula_o_portal_sem_chave_e_nao_faz_requisicao(monkeypatch, capsys):
    fumaca = _script("testar_fontes_reais")
    monkeypatch.setattr(fumaca, "_chave_do_portal", lambda: "")

    def proibido(*_a, **_k):
        raise AssertionError("sem chave, nenhuma requisição pode ser feita")

    monkeypatch.setattr(fumaca, "BuscadorHttp", proibido)
    monkeypatch.setattr(fumaca, "ConectorPortalTransparencia", proibido)
    assert fumaca.principal(["--fontes", "portal"]) == 0
    saida = capsys.readouterr().out
    assert "[PULADO] portal" in saida and "PORTAL_TRANSPARENCIA_CHAVE" in saida


def test_teste_de_fumaca_nao_exibe_a_chave_e_edital_sem_pdf_e_pulado(monkeypatch, capsys, tmp_path):
    fumaca = _script("testar_fontes_reais")
    chave = "chave-sintetica-0123456789abcdef"
    monkeypatch.setattr(fumaca, "_chave_do_portal", lambda: chave)

    def recusada(*_a, **_k):
        raise fumaca.ErroConector("o Portal recusou a chave (HTTP 401)")

    monkeypatch.setattr(fumaca, "_cnpj_de_amostra", recusada)
    codigo = fumaca.principal(["--fontes", "portal,edital", "--dados-reais", str(tmp_path)])
    saida = capsys.readouterr().out
    assert codigo == 1 and "[FALHOU] portal" in saida and "[PULADO] edital" in saida
    assert chave not in saida
