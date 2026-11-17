"""Risco 3 do piloto: arquivo do TSE acima do teto (UF grande) é lido em fluxo, com filtro, e a
captura guarda só o recorte. Arquivos sintéticos gerados no teste (nenhum CPF)."""

from __future__ import annotations

import hashlib
import importlib.util
import tracemalloc
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from buscador.api import criar_app
from buscador.conectores.base import ErroConector, RegistroCandidato, RegistroDoacao
from buscador.conectores.tse_arquivo import MAX_BYTES_LINHA, ConectorTseArquivo
from tests.conftest import cabecalhos, corpo_caso, criar_config, incluir_equipe

RAIZ = Path(__file__).resolve().parent.parent
TETO = 256 * 1024  # teto de leitura integral usado nos testes (o padrão é 64 MiB)
COLUNAS = (
    "AA_ELEICAO", "SG_UF", "SG_UE", "NM_UE", "DS_CARGO", "SQ_CANDIDATO", "NM_CANDIDATO",
    "NR_CPF_CANDIDATO", "SG_PARTIDO", "DS_ORIGEM_RECEITA", "NR_CPF_CNPJ_DOADOR", "NM_DOADOR",
    "NM_DOADOR_RFB", "DT_RECEITA", "DS_RECEITA", "VR_RECEITA",
)  # fmt: skip
MUNICIPIOS = ("SÃO SINTÉTICO", "EXEMPLÓPOLIS", "VILA FICTÍCIA", "PORTO IMAGINÁRIO")


def _linha(*campos: str) -> str:
    return ";".join('"' + c.replace('"', '""') + '"' for c in campos) + "\r\n"


def _receita(n: int, descricao: str = "DOAÇÃO SINTÉTICA") -> str:
    municipio = MUNICIPIOS[n % len(MUNICIPIOS)]
    return _linha(
        "2024", "ZZ", f"9900{n % 4}", municipio, "VEREADOR", str(990000000000 + n % 40),
        f"CANDIDATO SINTÉTICO {n % 40:02d}", "-4", "PSIN",
        "Recursos de pessoas jurídicas (sintético)", f"9{n % 1000:07d}000100",
        f"DOADORA SINTÉTICA {n % 1000:04d} LTDA", f"DOADORA SINTÉTICA {n % 1000:04d} LTDA",
        "15/08/2024", descricao, f"{100 + n},50",
    )  # fmt: skip


def _csv(linhas: int, codificacao: str = "latin-1") -> bytes:
    return (_linha(*COLUNAS) + "".join(_receita(n) for n in range(linhas))).encode(codificacao)


def _coletar(raiz: Path, parametros: dict, **opcoes):
    capturas: list[SimpleNamespace] = []

    def registrar(fonte, origem, conteudo, metadados):
        captura = SimpleNamespace(
            id=f"cap{len(capturas) + 1}", url_ou_arquivo=origem, conteudo=conteudo, meta=metadados
        )
        capturas.append(captura)
        return captura

    opcoes.setdefault("max_bytes_arquivo", TETO)
    conector = ConectorTseArquivo(dir_base=raiz, **opcoes)
    resultado = conector.coletar({"diretorio": "t", **parametros}, registrar)
    return resultado, capturas


def _essencia(registros: list) -> list[tuple]:
    """Os registros sem o ID da captura (que muda entre as duas formas de leitura)."""
    saida = []
    for r in registros:
        if isinstance(r, RegistroDoacao):
            saida.append(("doacao", r.sq_candidato, r.documento_doador, r.valor, r.data, r.chave))
        elif isinstance(r, RegistroCandidato):
            saida.append(("candidato", r.ano, r.sq_candidato, r.nome, r.nm_ue, r.uf))
    return saida


@pytest.fixture
def pasta(tmp_path: Path) -> Path:
    destino = tmp_path / "t"
    destino.mkdir()
    (destino / "SINTETICO.txt").write_text("dados sintéticos", encoding="utf-8")
    return destino


def test_arquivo_acima_do_teto_sem_filtro_e_recusado_com_orientacao(tmp_path, pasta):
    (pasta / "receitas_candidatos_2024_ZZ.csv").write_bytes(_csv(1000))
    with pytest.raises(ErroConector, match="Informe ao menos um filtro"):
        _coletar(tmp_path, {})
    # Abaixo do teto nada muda: leitura integral, captura com os bytes do arquivo.
    conteudo = _csv(50)
    assert len(conteudo) < TETO
    (pasta / "receitas_candidatos_2024_ZZ.csv").write_bytes(conteudo)
    resultado, capturas = _coletar(tmp_path, {})
    assert capturas[0].conteudo == conteudo and "recorte" not in capturas[0].meta
    assert not capturas[0].url_ou_arquivo.endswith("#recorte")
    assert sum(isinstance(r, RegistroDoacao) for r in resultado.registros) == 50


@pytest.mark.parametrize("codificacao", ["latin-1", "utf-8"])
def test_leitura_em_fluxo_guarda_o_recorte_e_da_os_mesmos_registros(tmp_path, pasta, codificacao):
    conteudo = _csv(2000, codificacao)
    assert len(conteudo) > TETO
    (pasta / "receitas_candidatos_2024_ZZ.csv").write_bytes(conteudo)
    filtro = {"uf": "zz", "municipio": "são sintético"}
    resultado, capturas = _coletar(tmp_path, filtro)
    # Mesmos registros da leitura integral (teto alto) com o mesmo filtro.
    integral, capturas_integrais = _coletar(tmp_path, filtro, max_bytes_arquivo=10**9)
    assert capturas_integrais[0].conteudo == conteudo
    assert _essencia(resultado.registros) == _essencia(integral.registros)
    assert sum(isinstance(r, RegistroDoacao) for r in resultado.registros) == 500
    # A captura é o recorte: cabeçalho + as linhas do município, com os bytes originais.
    (captura,) = capturas
    linhas = conteudo.split(b"\r\n")
    esperado = b"\r\n".join(
        [linhas[0]] + [li for li in linhas[1:] if MUNICIPIOS[0].encode(codificacao) in li] + [b""]
    )
    assert captura.conteudo == esperado and len(captura.conteudo) <= TETO
    assert captura.url_ou_arquivo.endswith("t/receitas_candidatos_2024_ZZ.csv#recorte")
    recorte = captura.meta["recorte"]
    assert recorte["modo"] == "fluxo" and recorte["membro_do_zip"] is None
    assert recorte["origem_bytes"] == len(conteudo)
    assert recorte["origem_sha256"] == hashlib.sha256(conteudo).hexdigest()
    assert recorte["origem_codificacao"] == codificacao
    assert (recorte["linhas_lidas"], recorte["linhas_mantidas"]) == (2000, 500)
    assert recorte["filtro"] == {"uf": "ZZ", "municipio": "SAO SINTETICO", "candidatos": 0}
    assert captura.meta["codificacao_detectada"] == {"receitas_candidatos_2024_ZZ.csv": codificacao}
    assert any("lido em fluxo" in a and "500 de 2000" in a for a in resultado.avisos)


def test_filtro_por_candidatos_e_campo_com_quebra_de_linha(tmp_path, pasta):
    # Uma linha com quebra de linha dentro de um campo entre aspas: é UM registro do CSV.
    especial = _receita(7, "DOAÇÃO COM\r\nQUEBRA; e ponto e vírgula")
    conteudo = (_linha(*COLUNAS) + "".join(_receita(n) for n in range(1500)) + especial).encode(
        "latin-1"
    )
    (pasta / "receitas_candidatos_2024_ZZ.csv").write_bytes(conteudo)
    sq = "990000000007"
    resultado, capturas = _coletar(tmp_path, {"candidatos": [sq]})
    doacoes = [r for r in resultado.registros if isinstance(r, RegistroDoacao)]
    assert {d.sq_candidato for d in doacoes} == {sq} and len(doacoes) == 38 + 1
    assert capturas[0].conteudo.endswith(especial.encode("latin-1"))
    assert capturas[0].meta["recorte"]["linhas_lidas"] == 1501
    assert capturas[0].meta["recorte"]["filtro"]["candidatos"] == 1
    with pytest.raises(ErroConector, match="candidatos"):
        _coletar(tmp_path, {"candidatos": ["99A"]})


def test_zip_com_csv_grande_e_lido_em_fluxo_membro_a_membro(tmp_path, pasta):
    receitas = _csv(2000)
    candidatos = (
        _linha(
            "ANO_ELEICAO",
            "SG_UF",
            "NM_UE",
            "DS_CARGO",
            "SQ_CANDIDATO",
            "NM_CANDIDATO",
            "DS_SIT_TOT_TURNO",
        )  # fmt: skip
        + "".join(
            _linha(
                "2024",
                "ZZ",
                MUNICIPIOS[n % 4],
                "VEREADOR",
                str(990000000000 + n),
                f"CANDIDATO SINTÉTICO {n:02d}",
                "ELEITO",
            )  # fmt: skip
            for n in range(40)
        )
    ).encode("latin-1")
    with zipfile.ZipFile(pasta / "contas_ZZ.zip", "w", zipfile.ZIP_DEFLATED) as pacote:
        pacote.writestr("pasta/receitas_candidatos_2024_ZZ.csv", receitas)
        pacote.writestr("consulta_cand_2024_ZZ.csv", candidatos)
        pacote.writestr("leiame.pdf", b"nao e csv")
    assert (pasta / "contas_ZZ.zip").stat().st_size < TETO < len(receitas)
    resultado, capturas = _coletar(tmp_path, {"municipio": "Exemplópolis"})
    origens = [c.url_ou_arquivo for c in capturas]
    assert origens == [
        "simulado://tse_arquivo/TSE_DIR_BASE/t/contas_ZZ.zip!consulta_cand_2024_ZZ.csv#recorte",
        "simulado://tse_arquivo/TSE_DIR_BASE/t/contas_ZZ.zip!receitas_candidatos_2024_ZZ.csv"
        "#recorte",
    ]
    por_membro = {c.meta["recorte"]["membro_do_zip"]: c.meta["recorte"] for c in capturas}
    assert (
        por_membro["receitas_candidatos_2024_ZZ.csv"]["origem_sha256"]
        == hashlib.sha256(receitas).hexdigest()
    )
    assert por_membro["consulta_cand_2024_ZZ.csv"]["linhas_mantidas"] == 10
    assert sum(isinstance(r, RegistroDoacao) for r in resultado.registros) == 500
    assert all(c.meta["simulado"] and c.meta["formato"] == "csv" for c in capturas)


def test_limites_defensivos_do_modo_fluxo(tmp_path, pasta):
    arquivo = pasta / "receitas_candidatos_2024_ZZ.csv"
    arquivo.write_bytes(_csv(2000))
    # 1. O recorte também tem teto: filtro que deixa passar tudo é recusado.
    with pytest.raises(ErroConector, match="O recorte de .* passa do limite"):
        _coletar(tmp_path, {"uf": "ZZ"})
    # 2. Teto do arquivo percorrido em fluxo.
    with pytest.raises(ErroConector, match="TSE_MAX_BYTES_FLUXO"):
        _coletar(tmp_path, {"municipio": "EXEMPLÓPOLIS"}, max_bytes_fluxo=TETO + 1)
    # 3. Teto de registros.
    with pytest.raises(ErroConector, match="TSE_MAX_REGISTROS"):
        _coletar(tmp_path, {"municipio": "EXEMPLÓPOLIS"}, max_registros=100)
    # 4. Linha física gigante (arquivo sem quebras de linha): fora do layout.
    arquivo.write_bytes(_linha(*COLUNAS).encode() + b'"' + b"x" * (MAX_BYTES_LINHA + 10))
    with pytest.raises(ErroConector, match="linha acima de"):
        _coletar(tmp_path, {"municipio": "EXEMPLÓPOLIS"}, max_bytes_arquivo=1024)
    # 5. Aspas que nunca fecham: o leitor de CSV recusa o campo gigante.
    aberto = _linha(*COLUNAS) + '"2024;' + "".join(_receita(n).replace('"', "") for n in range(900))
    arquivo.write_bytes(aberto.encode("latin-1"))
    with pytest.raises(ErroConector, match="fora do layout do TSE"):
        _coletar(tmp_path, {"municipio": "EXEMPLÓPOLIS"})
    # 6. Layout sem as colunas obrigatórias e arquivo vazio.
    arquivo.write_bytes(_csv(2000).replace(b"NR_CPF_CNPJ_DOADOR", b"OUTRA_COLUNA_QUALQUER"))
    with pytest.raises(ErroConector, match="o layout pode ter mudado"):
        _coletar(tmp_path, {"municipio": "EXEMPLÓPOLIS"})
    # 7. Link simbólico não é seguido.
    arquivo.unlink()
    alvo = tmp_path / "fora.csv"
    alvo.write_bytes(_csv(2000))
    (pasta / "receitas_candidatos_2024_ZZ.csv").symlink_to(alvo)
    with pytest.raises(ErroConector, match="Nenhum arquivo do TSE"):
        _coletar(tmp_path, {"municipio": "EXEMPLÓPOLIS"})


def test_memoria_da_leitura_em_fluxo_nao_cresce_com_o_arquivo(tmp_path, pasta):
    """Arquivo sintético de ~24 MB com teto de leitura integral de 1 MiB: o pico de memória
    alocada pelo Python durante a coleta fica muito abaixo do tamanho do arquivo."""
    medir = importlib.util.spec_from_file_location(
        "medir_tse_fluxo", RAIZ / "scripts" / "medir_tse_fluxo.py"
    )
    modulo = importlib.util.module_from_spec(medir)
    medir.loader.exec_module(modulo)
    tamanho, linhas = modulo.gerar(pasta / "receitas_candidatos_2024_ZZ.csv", 24)
    assert tamanho > 24 * 1024 * 1024
    tracemalloc.start()
    try:
        resultado, capturas = _coletar(
            tmp_path,
            {"uf": "ZZ", "municipio": "MUNICÍPIO SINTÉTICO 007"},
            max_bytes_arquivo=1024 * 1024,
        )
        _, pico = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    recorte = capturas[0].meta["recorte"]
    assert recorte["origem_bytes"] == tamanho and recorte["linhas_lidas"] == linhas
    assert recorte["linhas_mantidas"] == linhas // modulo.MUNICIPIOS_SINTETICOS
    assert len(capturas[0].conteudo) < 1024 * 1024
    assert sum(isinstance(r, RegistroDoacao) for r in resultado.registros) == linhas // 500
    assert pico < 4 * 1024 * 1024, f"pico de {pico} bytes para um arquivo de {tamanho} bytes"


def test_do_caso_filtra_as_receitas_pelos_candidatos_do_caso(tmp_path):
    raiz = tmp_path / "ingestao_tse"
    for nome in ("cand", "receitas"):
        (raiz / nome).mkdir(parents=True)
        (raiz / nome / "SINTETICO.txt").write_text("dados sintéticos", encoding="utf-8")
    (raiz / "cand" / "consulta_cand_2024_ZZ.csv").write_bytes(
        (
            _linha(
                "ANO_ELEICAO",
                "SG_UF",
                "NM_UE",
                "DS_CARGO",
                "SQ_CANDIDATO",
                "NM_CANDIDATO",
                "DS_SIT_TOT_TURNO",
            )  # fmt: skip
            + _linha("2024", "ZZ", "EXEMPLÓPOLIS", "VEREADOR", "990000000001", "UM", "ELEITO")
            + _linha("2024", "ZZ", "EXEMPLÓPOLIS", "VEREADOR", "990000000005", "CINCO", "ELEITO")
        ).encode("latin-1")
    )
    (raiz / "receitas" / "receitas_candidatos_2024_ZZ.csv").write_bytes(_csv(2000))
    config = criar_config(tmp_path, None, tse_dir_base=raiz, tse_max_bytes_arquivo=TETO)
    with TestClient(criar_app(config)) as cliente:
        caso_id = cliente.post("/casos", json=corpo_caso(), headers=cabecalhos()).json()["id"]
        incluir_equipe(cliente, caso_id)

        def coletar(parametros: dict):
            return cliente.post(
                f"/casos/{caso_id}/coletas",
                json={"fonte": "tse_arquivo", "parametros": parametros},
                headers=cabecalhos(),
            )

        # Sem candidato no caso, "do_caso" não tem o que filtrar; sem filtro, o arquivo grande
        # é recusado com a orientação.
        r = coletar({"diretorio": "receitas", "do_caso": True})
        assert r.status_code == 422 and "não tem candidato" in r.text
        r = coletar({"diretorio": "receitas"})
        assert r.status_code == 422 and "Informe ao menos um filtro" in r.text
        assert coletar({"diretorio": "cand"}).json()["situacao"] == "sucesso"
        r = coletar({"diretorio": "receitas", "do_caso": True})
        assert r.status_code == 200 and r.json()["situacao"] == "sucesso", r.text
        coleta = r.json()
        assert any("lido em fluxo" in a for a in coleta["avisos"])
        registro = next(
            c
            for c in cliente.get(f"/casos/{caso_id}/coletas", headers=cabecalhos()).json()
            if c["id"] == coleta["coleta_id"]
        )
        # A lista explícita fica gravada: a recoleta repete a mesma consulta.
        assert registro["parametros"] == {
            "diretorio": "receitas",
            "candidatos": ["990000000001", "990000000005"],
        }
        captura = cliente.get(f"/capturas/{coleta['captura_ids'][0]}", headers=cabecalhos()).json()
        assert captura["url_ou_arquivo"].endswith("#recorte")
        assert captura["metadados"]["recorte"]["linhas_mantidas"] == 100
        assert captura["conector_versao"] == "1.1.0"
        verificacao = cliente.get(
            f"/capturas/{captura['id']}/verificar", headers=cabecalhos()
        ).json()
        assert verificacao["integra"] is True
        candidatos = cliente.get(
            f"/casos/{caso_id}/entidades", params={"tipo": "candidato"}, headers=cabecalhos()
        ).json()
        assert {c["identificador"] for c in candidatos} == {"990000000001", "990000000005"}
        r = cliente.post(
            f"/casos/{caso_id}/coletas/{coleta['coleta_id']}/recoletar", headers=cabecalhos()
        )
        assert r.status_code == 200 and r.json()["comparacao"]["mesmo_conteudo"] is True
