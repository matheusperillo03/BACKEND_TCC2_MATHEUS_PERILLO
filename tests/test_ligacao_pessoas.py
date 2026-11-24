"""Ligação de registros de pessoas (H46, H50, H51 e H52), decisão do autor de 23/11/2026, opção (a):

- entre eleições, o candidato é reconhecido pelo título eleitoral, guardado só como resumo (HMAC
  com a chave própria CHAVE_IDENTIFICADORES), nunca exibido nem exportado;
- o CPF do candidato é reduzido aos seis dígitos centrais na leitura; o CPF inteiro nunca é
  guardado fora da carga bruta;
- candidato e sócio ou servidor com os mesmos seis dígitos e o mesmo nome geram SUGESTÃO, que o
  investigador confirma ou descarta, sem fusão.

Todos os CPFs, títulos e nomes daqui são sintéticos e sorteados a cada execução."""

from __future__ import annotations

import json
import secrets
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from buscador.api import criar_app
from buscador.conectores.base import (
    ErroConector,
    RegistroBem,
    RegistroCandidato,
    RegistroDespesa,
)
from buscador.conectores.pncp import TransporteSimulado
from buscador.conectores.simulados import transporte_portal
from buscador.conectores.tse_arquivo import ConectorTseArquivo, ConectorTseBens
from buscador.cripto import resumo_identificador
from buscador.modelos import Captura, Entidade, Sugestao
from tests.conftest import (
    CHAVE_IDENTIFICADORES,
    FIXTURES_CNPJ,
    FIXTURES_PNCP,
    cabecalhos,
    corpo_caso,
    criar_config,
    incluir_equipe,
)
from tests.sinteticos import cpf_sintetico, mascarado, pontuado

SIAPE = "99001"
CNPJ_ORGAO = "90100001000109"
NOME = "FULANO SINTETICO DA LIGACAO"
OUTRO_NOME = "BELTRANA SINTETICA SEM LIGACAO"

CABECALHO_CAND = (
    '"ANO_ELEICAO";"SG_UF";"SG_UE";"NM_UE";"DS_CARGO";"SQ_CANDIDATO";"NM_CANDIDATO";'
    '"NM_URNA_CANDIDATO";"NR_CPF_CANDIDATO";"NR_TITULO_ELEITORAL_CANDIDATO";"SG_PARTIDO";'
    '"DS_SIT_TOT_TURNO"\n'
)
CABECALHO_BENS = (
    '"ANO_ELEICAO";"SG_UF";"SG_UE";"SQ_CANDIDATO";"NR_ORDEM_BEM_CANDIDATO";'
    '"CD_TIPO_BEM_CANDIDATO";"DS_TIPO_BEM_CANDIDATO";"DS_BEM_CANDIDATO";"VR_BEM_CANDIDATO";'
    '"DT_ULT_ATUAL_BEM_CANDIDATO"\n'
)


CHAVE_ID = bytes.fromhex(CHAVE_IDENTIFICADORES)


def titulo_sintetico() -> str:
    return "".join(str(secrets.randbelow(10)) for _ in range(12))


def linha_cand(ano, sq, nome, cpf, titulo, situacao="ELEITO", cargo="DEPUTADO DISTRITAL") -> str:
    return (
        f'"{ano}";"DF";"DF";"DISTRITO FEDERAL";"{cargo}";"{sq}";"{nome}";"SINTETICO";"{cpf}";'
        f'"{titulo}";"PSIN";"{situacao}"\n'
    )


def linha_bem(ano, sq, ordem, tipo, descricao, valor, codigo="12") -> str:
    return (
        f'"{ano}";"DF";"DF";"{sq}";"{ordem}";"{codigo}";"{tipo}";"{descricao}";"{valor}";'
        '"15/08/2026"\n'
    )


def resumo_esperado(titulo: str) -> str:
    return resumo_identificador(CHAVE_ID, "titulo-eleitoral", titulo.encode())


class _Registrador:
    def __init__(self) -> None:
        self.capturas: list[dict] = []

    def __call__(self, fonte, origem, conteudo, metadados):
        self.capturas.append({"fonte": fonte, "origem": origem, "conteudo": conteudo})
        return type("C", (), {"id": f"cap{len(self.capturas)}", "url_ou_arquivo": origem})()


@pytest.fixture
def pessoa() -> dict:
    """Uma pessoa sintética: CPF de DV válido e título de 12 dígitos, sorteados."""
    return {"cpf": cpf_sintetico(), "titulo": titulo_sintetico(), "outro_cpf": cpf_sintetico()}


def _servidor(nome: str, cpf: str, ident: int = 1) -> dict:
    orgao = {"codigo": SIAPE, "nome": "ORGAO SIAPE SINTETICO", "sigla": "OSS"}
    return {
        "servidor": {
            "id": ident,
            "idServidorAposentadoPensionista": ident,
            # O Portal devolve o CPF já descaracterizado.
            "pessoa": {"nome": nome, "cpfFormatado": f"***.{cpf[3:6]}.{cpf[6:9]}-**", "tipo": "F"},
            "situacao": "ATIVO PERMANENTE",
            "tipoServidor": "Civil",
            "orgaoServidorLotacao": orgao,
            "orgaoServidorExercicio": orgao,
        }
    }


@pytest.fixture
def amb(tmp_path: Path, pessoa):
    """Aplicação com as raízes do CNPJ e do TSE e o Portal simulado. O quadro de sócios sintético
    ganha um sócio com o nome e os seis dígitos da pessoa do teste."""
    raiz_cnpj = tmp_path / "ingestao_cnpj"
    shutil.copytree(FIXTURES_CNPJ, raiz_cnpj / "cnpj")
    socios = next((raiz_cnpj / "cnpj").glob("*SOCIOCSV"))
    with socios.open("a", encoding="latin-1") as arquivo:
        arquivo.write(
            f'"90000001";"2";"{NOME}";"{mascarado(pessoa["cpf"])}";"49";"20150310";"";'
            '"***000000**";"";"00";"5"\n'
        )
    raiz_tse = tmp_path / "ingestao_tse"
    for pasta in ("e2022", "e2026", "bens"):
        (raiz_tse / pasta).mkdir(parents=True)
        (raiz_tse / pasta / "SINTETICO.txt").write_text("dados sintéticos", encoding="utf-8")
    servidores = {SIAPE: [_servidor(NOME, pessoa["cpf"])]}
    config = criar_config(
        tmp_path,
        raiz_cnpj,
        tse_dir_base=raiz_tse,
        portal_transparencia_chave="chave-sintetica",
        portal_transparencia_requisicoes_por_minuto=700,
    )
    app = criar_app(
        config,
        extras_conectores={
            "pncp": {"transporte": TransporteSimulado.de_diretorio(FIXTURES_PNCP)},
            "portal_transparencia": {
                "transporte": transporte_portal({"0": []}, None, servidores),
                "dormir": lambda _s: None,
            },
        },
    )
    with TestClient(app) as cliente:
        r = cliente.post("/casos", json=corpo_caso(), headers=cabecalhos())
        assert r.status_code == 201, r.text
        caso_id = r.json()["id"]
        incluir_equipe(cliente, caso_id)
        yield {"cliente": cliente, "app": app, "caso_id": caso_id, "tse": raiz_tse}


def coletar(amb, fonte: str, parametros: dict, esperado: int = 200) -> dict:
    r = amb["cliente"].post(
        f"/casos/{amb['caso_id']}/coletas",
        json={"fonte": fonte, "parametros": parametros},
        headers=cabecalhos(),
    )
    assert r.status_code == esperado, r.text
    if esperado == 200:
        assert r.json()["situacao"] == "sucesso", r.text
    return r.json()


def candidaturas(amb, pessoa, anos=(("2022", "990000000022"), ("2026", "990000000026"))) -> None:
    """Uma candidatura da pessoa por eleição (SQ_CANDIDATO diferente a cada uma) e, em 2026, a
    de outra pessoa, com outro título."""
    for ano, sq in anos:
        texto = CABECALHO_CAND + linha_cand(ano, sq, NOME, pessoa["cpf"], pessoa["titulo"])
        if ano == "2026":
            texto += linha_cand(
                ano, "990000000099", OUTRO_NOME, pessoa["outro_cpf"], titulo_sintetico()
            )
        (amb["tse"] / f"e{ano}" / f"consulta_cand_{ano}_DF.csv").write_bytes(
            texto.encode("latin-1")
        )
        coletar(amb, "tse_arquivo", {"diretorio": f"e{ano}"})


def entidades(amb, tipo: str) -> list[dict]:
    r = amb["cliente"].get(
        f"/casos/{amb['caso_id']}/entidades", params={"tipo": tipo}, headers=cabecalhos()
    )
    assert r.status_code == 200, r.text
    return r.json()


def detalhe(amb, entidade_id: str) -> dict:
    r = amb["cliente"].get(f"/casos/{amb['caso_id']}/entidades/{entidade_id}", headers=cabecalhos())
    assert r.status_code == 200, r.text
    return r.json()


def candidato(amb, ano: str, nome: str = NOME) -> dict:
    return next(
        e
        for e in entidades(amb, "candidato")
        if e["atributos"]["ano"] == ano and e["rotulo"] == nome
    )


def saidas_da_api(amb) -> list[str]:
    """Tudo o que sai do caso pela API (listas, detalhes, grafo, relatório, exportações,
    auditoria), como texto."""
    cliente, caso_id, h = amb["cliente"], amb["caso_id"], cabecalhos("tok-delegado")
    textos = [
        cliente.get(f"/casos/{caso_id}", headers=h).text,
        cliente.get(f"/casos/{caso_id}/entidades", headers=h).text,
        cliente.get(f"/casos/{caso_id}/alertas", headers=h).text,
        cliente.get(f"/casos/{caso_id}/coletas", headers=h).text,
        cliente.get(f"/casos/{caso_id}/relatorio", headers=h).text,
        cliente.get(f"/casos/{caso_id}/relatorio", params={"formato": "md"}, headers=h).text,
        cliente.get("/auditoria", params={"caso_id": caso_id}, headers=h).text,
    ]
    for formato in ("graphml", "csv_entidades", "csv_vinculos", "zip"):
        r = cliente.get(f"/casos/{caso_id}/exportacao", params={"formato": formato}, headers=h)
        textos.append(r.content.decode("latin-1"))
    for e in json.loads(textos[1]):
        textos.append(cliente.get(f"/casos/{caso_id}/entidades/{e['id']}", headers=h).text)
        if e["tipo"] == "candidato":
            textos.append(
                cliente.get(f"/casos/{caso_id}/entidades/{e['id']}/patrimonio", headers=h).text
            )
            textos.append(
                cliente.get(
                    f"/casos/{caso_id}/grafo",
                    params={"centro": e["id"], "profundidade": 2},
                    headers=h,
                ).text
            )
    return textos


# ==== Resumo do título eleitoral (decisão de 23/11/2026) =========================================


def test_h50_resumo_do_titulo_depende_da_chave_e_do_contexto_e_nao_e_sha256_puro():
    import hashlib

    titulo = titulo_sintetico().encode()
    resumo = resumo_identificador(CHAVE_ID, "titulo-eleitoral", titulo)
    assert len(resumo) == 64 and int(resumo, 16) >= 0
    assert resumo == resumo_identificador(CHAVE_ID, "titulo-eleitoral", titulo)
    # Sem a chave não se refaz o resumo (um título tem 12 dígitos: SHA-256 puro seria reversível
    # por força bruta).
    assert resumo != hashlib.sha256(titulo).hexdigest()
    assert resumo != resumo_identificador(bytes.fromhex("ab" * 32), "titulo-eleitoral", titulo)
    assert resumo != resumo_identificador(CHAVE_ID, "outro-contexto", titulo)


def test_h50_conector_le_titulo_so_como_resumo_e_cpf_so_com_seis_digitos(tmp_path, pessoa):
    pasta = tmp_path / "t"
    pasta.mkdir()
    (pasta / "consulta_cand_2026_DF.csv").write_bytes(
        (
            CABECALHO_CAND
            + linha_cand("2026", "990000000026", NOME, pessoa["cpf"], pessoa["titulo"])
            + linha_cand("2026", "990000000027", OUTRO_NOME, "-4", "#NULO")
        ).encode("latin-1")
    )
    registrador = _Registrador()
    resultado = ConectorTseArquivo(dir_base=tmp_path, resumir_titulo=resumo_esperado).coletar(
        {"diretorio": "t"}, registrador
    )
    com, sem = (r for r in resultado.registros if isinstance(r, RegistroCandidato))
    assert com.cpf_parcial == mascarado(pessoa["cpf"])
    assert com.titulo_resumo == resumo_esperado(pessoa["titulo"])
    # Valores não divulgáveis ("-4", "#NULO") ficam vazios: não há o que resumir.
    assert (sem.cpf_parcial, sem.titulo_resumo) == ("", "")
    # Nem o CPF inteiro nem o título em claro existem nos registros que saem do conector.
    assert pessoa["cpf"] not in repr(resultado.registros)
    assert pessoa["titulo"] not in repr(resultado.registros)
    assert pessoa["cpf"][:3] not in com.cpf_parcial[:3] and com.cpf_parcial.endswith("**")
    # A carga bruta fica como veio (é a prova).
    assert pessoa["titulo"].encode() in registrador.capturas[0]["conteudo"]
    assert pessoa["cpf"].encode() in registrador.capturas[0]["conteudo"]


def test_h50_conector_sem_chave_configurada_nao_guarda_resumo_nem_titulo(tmp_path, pessoa):
    pasta = tmp_path / "t"
    pasta.mkdir()
    (pasta / "consulta_cand_2026_DF.csv").write_bytes(
        (
            CABECALHO_CAND
            + linha_cand("2026", "990000000026", NOME, pessoa["cpf"], pessoa["titulo"])
        ).encode("latin-1")
    )
    resultado = ConectorTseArquivo(dir_base=tmp_path).coletar({"diretorio": "t"}, _Registrador())
    assert resultado.registros[0].titulo_resumo == ""
    assert pessoa["titulo"] not in repr(resultado.registros)
    assert any("título eleitoral" in a for a in resultado.avisos)


def test_h50_cpf_com_tamanho_errado_ou_pontuado_nunca_vira_cpf_inteiro(tmp_path, pessoa):
    pasta = tmp_path / "t"
    pasta.mkdir()
    (pasta / "consulta_cand_2026_DF.csv").write_bytes(
        (
            CABECALHO_CAND
            + linha_cand("2026", "1", NOME, pontuado(pessoa["cpf"]), pessoa["titulo"])
            + linha_cand("2026", "2", OUTRO_NOME, pessoa["outro_cpf"][:9], "12")
        ).encode("latin-1")
    )
    resultado = ConectorTseArquivo(dir_base=tmp_path, resumir_titulo=lambda t: "r" * 64).coletar(
        {"diretorio": "t"}, _Registrador()
    )
    um, dois = resultado.registros
    assert um.cpf_parcial == mascarado(pessoa["cpf"])
    # CPF com menos de 11 dígitos e título fora do tamanho não são usados.
    assert (dois.cpf_parcial, dois.titulo_resumo) == ("", "")


# ==== Mesmo candidato entre eleições, pelo resumo do título ======================================


def test_h50_mesmo_titulo_liga_as_candidaturas_sem_fundir_e_o_resumo_nunca_sai(amb, pessoa):
    candidaturas(amb, pessoa)
    de_2022, de_2026 = candidato(amb, "2022"), candidato(amb, "2026")
    outro = candidato(amb, "2026", OUTRO_NOME)
    # Não há fusão: cada candidatura continua uma entidade, com o seu SQ_CANDIDATO.
    assert de_2022["id"] != de_2026["id"]
    assert (de_2022["identificador"], de_2026["identificador"]) == ("990000000022", "990000000026")
    assert [c["entidade_id"] for c in detalhe(amb, de_2022["id"])["outras_candidaturas"]] == [
        de_2026["id"]
    ]
    ligada = detalhe(amb, de_2026["id"])["outras_candidaturas"]
    assert [(c["entidade_id"], c["ano"], c["cargo"]) for c in ligada] == [
        (de_2022["id"], "2022", "DEPUTADO DISTRITAL")
    ]
    assert detalhe(amb, outro["id"])["outras_candidaturas"] == []
    # O atributo visível do CPF é só o descaracterizado.
    assert de_2026["atributos"]["cpf_mascarado"] == mascarado(pessoa["cpf"])
    # O resumo fica numa coluna que nenhuma rota devolve; o título em claro, só na carga bruta.
    resumo = resumo_esperado(pessoa["titulo"])
    with amb["app"].state.fabrica_sessoes() as s:
        guardados = {
            e.atributos["ano"]: e.chave_pessoa
            for e in s.execute(select(Entidade).where(Entidade.tipo == "candidato")).scalars()
            if e.rotulo == NOME
        }
        captura = s.execute(select(Captura).order_by(Captura.coletado_em)).scalars().first()
        bruto = amb["app"].state.custodia.ler(captura)
    assert guardados == {"2022": resumo, "2026": resumo}
    assert pessoa["titulo"].encode() in bruto
    for texto in saidas_da_api(amb):
        assert resumo not in texto, "o resumo do título saiu pela API"
        assert pessoa["titulo"] not in texto, "o título eleitoral saiu pela API"
        assert pessoa["cpf"] not in texto and pontuado(pessoa["cpf"]) not in texto
        assert "chave_pessoa" not in texto and "titulo_resumo" not in texto


def test_h50_texto_da_captura_oculta_o_titulo_e_o_cpf_do_candidato(amb, pessoa):
    candidaturas(amb, pessoa, anos=(("2026", "990000000026"),))
    with amb["app"].state.fabrica_sessoes() as s:
        captura_id = s.execute(select(Captura.id)).scalars().first()
    r = amb["cliente"].get(
        f"/casos/{amb['caso_id']}/capturas/{captura_id}/texto", headers=cabecalhos()
    )
    assert r.status_code == 200, r.text
    assert pessoa["cpf"] not in r.text and pessoa["titulo"] not in r.text
    assert mascarado(pessoa["cpf"]) in r.json()["texto"]
    assert NOME in r.json()["texto"]
    # A troca preserva o comprimento (as posições dos trechos continuam valendo).
    assert '"' + "*" * 12 + '";"PSIN"' in r.json()["texto"]


def test_h50_comparacao_de_capturas_nao_mostra_o_titulo():
    from buscador.privacidade import descaracterizar, ocultar_titulo_em_csv

    titulo = titulo_sintetico()
    campos = {"NR_TITULO_ELEITORAL_CANDIDATO": titulo, "SQ_CANDIDATO": "990000000026"}
    assert descaracterizar(campos) == {
        "NR_TITULO_ELEITORAL_CANDIDATO": "*" * 12,
        "SQ_CANDIDATO": "990000000026",
    }
    # Texto que não é CSV do TSE fica como está.
    assert ocultar_titulo_em_csv(f"edital {titulo}") == f"edital {titulo}"


# ==== Candidato x sócio ou servidor: sugestão que o investigador confirma ou descarta ============


def _sugestoes(amb, entidade_id: str) -> list[dict]:
    return detalhe(amb, entidade_id)["sugestoes"]


def test_h46_candidato_e_socio_com_seis_digitos_e_nome_geram_sugestao_sem_fusao(amb, pessoa):
    coletar(amb, "cnpj_arquivo", {"diretorio": "cnpj"})
    candidaturas(amb, pessoa, anos=(("2026", "990000000026"),))
    cand = candidato(amb, "2026")
    socio = next(e for e in entidades(amb, "pessoa") if e["rotulo"] == NOME)
    assert socio["identificador"] == mascarado(pessoa["cpf"])
    (sugestao,) = _sugestoes(amb, cand["id"])
    assert sugestao["candidata_id"] == socio["id"] and sugestao["candidata_tipo"] == "pessoa"
    assert sugestao["score"] == 0.9 and sugestao["situacao"] == "pendente"
    assert "seis dígitos" in sugestao["motivo"] and "candidato" in sugestao["motivo"].lower()
    assert (
        mascarado(pessoa["cpf"]) in sugestao["motivo"] and pessoa["cpf"] not in sugestao["motivo"]
    )
    # A sugestão aparece dos dois lados e ninguém foi fundido.
    (volta,) = [s for s in _sugestoes(amb, socio["id"]) if s["candidata_id"] == cand["id"]]
    assert volta["candidata_tipo"] == "candidato" and volta["situacao"] == "pendente"
    assert {cand["tipo"], socio["tipo"]} == {"candidato", "pessoa"}
    # Outra pessoa (outro CPF, outro nome) não recebe sugestão.
    assert _sugestoes(amb, candidato(amb, "2026", OUTRO_NOME)["id"]) == []


def test_h46_a_ordem_das_coletas_nao_muda_a_sugestao(amb, pessoa):
    candidaturas(amb, pessoa, anos=(("2026", "990000000026"),))
    cand = candidato(amb, "2026")
    assert _sugestoes(amb, cand["id"]) == []
    coletar(amb, "cnpj_arquivo", {"diretorio": "cnpj"})
    socio = next(e for e in entidades(amb, "pessoa") if e["rotulo"] == NOME)
    assert [s["candidata_id"] for s in _sugestoes(amb, cand["id"])] == [socio["id"]]
    # Recoletar não duplica a sugestão.
    coletar(amb, "cnpj_arquivo", {"diretorio": "cnpj"})
    coletar(amb, "tse_arquivo", {"diretorio": "e2026"})
    assert len(_sugestoes(amb, cand["id"])) == 1


def test_h51_candidato_e_servidor_com_seis_digitos_e_nome_geram_sugestao(amb, pessoa):
    candidaturas(amb, pessoa, anos=(("2026", "990000000026"),))
    coletar(
        amb,
        "portal_transparencia",
        {"recurso": "servidores", "orgaoServidorLotacao": SIAPE, "cnpjOrgao": CNPJ_ORGAO},
    )
    cand = candidato(amb, "2026")
    servidor = next(e for e in entidades(amb, "pessoa") if e["rotulo"] == NOME)
    (sugestao,) = _sugestoes(amb, cand["id"])
    assert (sugestao["candidata_id"], sugestao["score"]) == (servidor["id"], 0.9)
    assert "servidor" in sugestao["motivo"]


def test_h46_so_nome_ou_so_seis_digitos_nao_geram_sugestao_de_candidato(amb, pessoa):
    """A decisão exige os seis dígitos E o nome: mesmo nome com outros dígitos (homônimo) ou
    mesmos dígitos com outro nome não ligam o candidato a ninguém."""
    coletar(amb, "cnpj_arquivo", {"diretorio": "cnpj"})
    texto = (
        CABECALHO_CAND
        + linha_cand("2026", "1", NOME, pessoa["outro_cpf"], titulo_sintetico())
        + linha_cand("2026", "2", OUTRO_NOME, pessoa["cpf"], titulo_sintetico())
        + linha_cand("2026", "3", NOME, "-4", titulo_sintetico())
    )
    (amb["tse"] / "e2026" / "consulta_cand_2026_DF.csv").write_bytes(texto.encode("latin-1"))
    coletar(amb, "tse_arquivo", {"diretorio": "e2026"})
    for cand in entidades(amb, "candidato"):
        assert _sugestoes(amb, cand["id"]) == []


def _decidir(amb, sugestao_id: str, corpo: dict, token: str = "tok-analista"):
    return amb["cliente"].post(
        f"/casos/{amb['caso_id']}/sugestoes/{sugestao_id}/decisao",
        json=corpo,
        headers=cabecalhos(token),
    )


def test_h46_investigador_confirma_ou_descarta_a_sugestao_com_justificativa_e_auditoria(
    amb, pessoa
):
    coletar(amb, "cnpj_arquivo", {"diretorio": "cnpj"})
    candidaturas(amb, pessoa, anos=(("2026", "990000000026"),))
    cand = candidato(amb, "2026")
    (sugestao,) = _sugestoes(amb, cand["id"])
    # Sem justificativa, decisão inválida ou papel de leitor: recusado.
    assert _decidir(amb, sugestao["id"], {"decisao": "confirmado"}).status_code == 422
    assert (
        _decidir(amb, sugestao["id"], {"decisao": "fundir", "justificativa": "x"}).status_code
        == 422
    )
    corpo = {"decisao": "confirmado", "justificativa": "Conferido no registro de candidatura"}
    assert _decidir(amb, sugestao["id"], corpo, "tok-admin").status_code == 403
    assert _decidir(amb, "inexistente", corpo).status_code == 404
    r = _decidir(amb, sugestao["id"], corpo)
    assert r.status_code == 200, r.text
    assert r.json()["situacao"] == "confirmado" and r.json()["decidido_por"] == "ana"
    # A decisão vale para o par (os dois sentidos) e não funde as entidades.
    socio_id = sugestao["candidata_id"]
    (ida,) = _sugestoes(amb, cand["id"])
    (volta,) = [s for s in _sugestoes(amb, socio_id) if s["candidata_id"] == cand["id"]]
    for s in (ida, volta):
        assert s["situacao"] == "confirmado"
        assert s["justificativa_decisao"] == corpo["justificativa"] and s["decidido_em"]
    assert {e["id"] for e in entidades(amb, "candidato")} >= {cand["id"]}
    assert any(e["id"] == socio_id for e in entidades(amb, "pessoa"))
    # Rever a decisão é permitido e fica registrado.
    r = _decidir(amb, volta["id"], {"decisao": "descartado", "justificativa": "Homônimo"})
    assert r.status_code == 200 and r.json()["situacao"] == "descartado"
    assert _sugestoes(amb, cand["id"])[0]["situacao"] == "descartado"
    trilha = amb["cliente"].get(
        "/auditoria", params={"caso_id": amb["caso_id"]}, headers=cabecalhos("tok-delegado")
    )
    decisoes = [e for e in trilha.json() if e["acao"] == "sugestao.decidir"]
    assert [d["detalhes"]["decisao"] for d in decisoes] == ["confirmado", "descartado"]
    assert decisoes[1]["detalhes"]["situacao_anterior"] == "confirmado"
    assert decisoes[0]["usuario"] == "ana" and decisoes[0]["detalhes"]["entidade_id"]
    assert pessoa["cpf"] not in trilha.text
    with amb["app"].state.fabrica_sessoes() as s:
        do_par = s.execute(
            select(Sugestao).where(Sugestao.entidade_id.in_((cand["id"], socio_id)))
        ).scalars()
        assert [x.situacao for x in do_par if x.candidata_id in (cand["id"], socio_id)] == [
            "descartado",
            "descartado",
        ]


# ==== H50: bens declarados ao TSE (conector tse_bens) ===========================================


def _arquivo_de_bens(amb, pessoa, ano: str, sq: str, bens: list[tuple], extra: str = "") -> None:
    texto = CABECALHO_BENS + "".join(linha_bem(ano, sq, *bem) for bem in bens) + extra
    (amb["tse"] / "bens" / f"bem_candidato_{ano}_DF.csv").write_bytes(texto.encode("latin-1"))


BENS_2022 = [
    ("1", "Apartamento", "APARTAMENTO SINTETICO NA QUADRA 1", "300000,00"),
    (
        "2",
        "Veículo automotor terrestre: caminhão, automóvel, moto, etc.",
        "CARRO SINTETICO",
        "50000,00",
    ),
]
BENS_2026 = [
    ("1", "Apartamento", "APARTAMENTO SINTETICO NA QUADRA 1", "350000,00"),
    ("2", "Apartamento", "SEGUNDO APARTAMENTO SINTETICO", "900000,00"),
    ("3", "Quotas ou quinhões de capital", "QUOTAS DE EMPRESA SINTETICA", "250000,50"),
]


def test_h50_conector_tse_bens_le_o_arquivo_de_bens_e_so_ele(tmp_path):
    pasta = tmp_path / "b"
    pasta.mkdir()
    (pasta / "bem_candidato_2026_DF.csv").write_bytes(
        (
            CABECALHO_BENS
            + linha_bem("2026", "990000000026", *BENS_2026[0])
            + linha_bem("2026", "990000000099", "1", "Terreno", "TERRENO SINTETICO", "#NULO")
        ).encode("latin-1")
    )
    # Arquivo de candidatos na mesma pasta: o conector de bens não o lê.
    (pasta / "consulta_cand_2026_DF.csv").write_bytes(
        (CABECALHO_CAND + linha_cand("2026", "1", NOME, "-4", "-4")).encode("latin-1")
    )
    registrador = _Registrador()
    conector = ConectorTseBens(dir_base=tmp_path)
    resultado = conector.coletar({"diretorio": "b"}, registrador)
    assert conector.nome == "tse_bens" and len(registrador.capturas) == 1
    assert registrador.capturas[0]["fonte"] == "tse_bens"
    assert all(isinstance(r, RegistroBem) for r in resultado.registros)
    primeiro, segundo = resultado.registros
    assert (primeiro.ano, primeiro.sq_candidato, primeiro.ordem) == ("2026", "990000000026", "1")
    assert (primeiro.tipo, primeiro.valor) == ("Apartamento", 350000.0)
    assert primeiro.descricao == "APARTAMENTO SINTETICO NA QUADRA 1"
    assert primeiro.atualizado_em == "2026-08-15"
    assert segundo.valor is None
    # Filtro pelos candidatos pedidos.
    so_um = ConectorTseBens(dir_base=tmp_path).coletar(
        {"diretorio": "b", "candidatos": ["990000000099"]}, _Registrador()
    )
    assert [r.sq_candidato for r in so_um.registros] == ["990000000099"]
    # O conector de candidatos e receitas não lê o arquivo de bens.
    outros = ConectorTseArquivo(dir_base=tmp_path).coletar({"diretorio": "b"}, _Registrador())
    assert all(isinstance(r, RegistroCandidato) for r in outros.registros)


def test_h50_layout_de_bens_alterado_e_recusado(tmp_path):
    pasta = tmp_path / "b"
    pasta.mkdir()
    (pasta / "bem_candidato_2026_DF.csv").write_text(
        '"SQ_CANDIDATO";"OUTRA"\n"1";"x"\n', encoding="latin-1"
    )
    with pytest.raises(ErroConector, match="layout pode ter mudado"):
        ConectorTseBens(dir_base=tmp_path).coletar({"diretorio": "b"}, _Registrador())


def test_h50_bens_ficam_ligados_ao_candidato_da_eleicao_com_a_carga_na_custodia(amb, pessoa):
    candidaturas(amb, pessoa)
    _arquivo_de_bens(
        amb,
        pessoa,
        "2026",
        "990000000026",
        BENS_2026,
        # Bem de candidato que NÃO está no caso: com ``do_caso`` não entra.
        extra=linha_bem("2026", "990000000777", "1", "Terreno", "TERRENO DE FORA DO CASO", "1,00"),
    )
    coleta = coletar(amb, "tse_bens", {"diretorio": "bens", "do_caso": True})
    cand = detalhe(amb, candidato(amb, "2026")["id"])
    atributos = cand["atributos"]
    assert atributos["bens_total"] == 1500000.5 and atributos["bens_quantidade"] == 3
    assert atributos["bens_por_tipo"] == {
        "Apartamento": 1250000.0,
        "Quotas ou quinhões de capital": 250000.5,
    }
    assert [b["descricao"] for b in atributos["bens"]] == [b[2] for b in BENS_2026]
    # Custódia: a captura do arquivo de bens é fonte da entidade, com SHA-256.
    (captura_id,) = coleta["captura_ids"]
    fonte = next(f for f in cand["fontes"] if f["captura_id"] == captura_id)
    assert fonte["fonte"] == "tse_bens" and len(fonte["sha256"]) == 64
    assert atributos["origem_atributos"]["bens_total"] == captura_id
    verificacao = amb["cliente"].get(f"/capturas/{captura_id}/verificar", headers=cabecalhos())
    assert verificacao.status_code == 200 and verificacao.json()["integra"] is True
    # O candidato de fora do caso não entrou, e o de 2022 (sem arquivo) não ganhou bens.
    assert not any(e["identificador"] == "990000000777" for e in entidades(amb, "candidato"))
    assert "bens_total" not in candidato(amb, "2022")["atributos"]
    # Recoletar o mesmo arquivo não duplica os bens.
    coletar(amb, "tse_bens", {"diretorio": "bens", "do_caso": True})
    assert detalhe(amb, cand["id"])["atributos"]["bens_quantidade"] == 3


def test_h50_do_caso_sem_candidato_no_caso_e_recusado(amb, pessoa):
    _arquivo_de_bens(amb, pessoa, "2026", "990000000026", BENS_2026)
    r = coletar(amb, "tse_bens", {"diretorio": "bens", "do_caso": True}, esperado=422)
    assert "candidato" in json.dumps(r, ensure_ascii=False)


def test_h50_descricao_do_bem_com_cpf_e_descaracterizada(amb, pessoa):
    candidaturas(amb, pessoa, anos=(("2026", "990000000026"),))
    bens = [("1", "Outros bens", f"CREDITO COM CPF {pontuado(pessoa['outro_cpf'])}", "10,00")]
    _arquivo_de_bens(amb, pessoa, "2026", "990000000026", bens)
    coletar(amb, "tse_bens", {"diretorio": "bens", "do_caso": True})
    for texto in saidas_da_api(amb):
        assert pessoa["outro_cpf"] not in texto and pontuado(pessoa["outro_cpf"]) not in texto


# ==== H51: linha do tempo patrimonial ===========================================================


def _patrimonio(amb, entidade_id: str, esperado: int = 200) -> dict:
    r = amb["cliente"].get(
        f"/casos/{amb['caso_id']}/entidades/{entidade_id}/patrimonio", headers=cabecalhos()
    )
    assert r.status_code == esperado, r.text
    return r.json()


def test_h51_linha_do_tempo_soma_os_bens_por_eleicao_e_por_tipo_com_a_captura_de_origem(
    amb, pessoa
):
    candidaturas(amb, pessoa)
    _arquivo_de_bens(amb, pessoa, "2022", "990000000022", BENS_2022)
    _arquivo_de_bens(amb, pessoa, "2026", "990000000026", BENS_2026)
    coleta = coletar(amb, "tse_bens", {"diretorio": "bens", "do_caso": True})
    de_2022, de_2026 = candidato(amb, "2022"), candidato(amb, "2026")
    # A linha é a mesma a partir de qualquer candidatura da pessoa (ligadas pelo título).
    linha = _patrimonio(amb, de_2026["id"])
    assert _patrimonio(amb, de_2022["id"])["pontos"] == linha["pontos"]
    assert linha["ligacao"] == "titulo_eleitoral"
    assert "valor informado pelo candidato" in linha["nota"]
    assert [(p["ano"], p["entidade_id"], p["total"]) for p in linha["pontos"]] == [
        ("2022", de_2022["id"], 350000.0),
        ("2026", de_2026["id"], 1500000.5),
    ]
    ultimo = linha["pontos"][1]
    assert ultimo["quantidade"] == 3 and ultimo["cargo"] == "DEPUTADO DISTRITAL"
    assert ultimo["por_tipo"] == [
        {"tipo": "Apartamento", "total": 1250000.0, "quantidade": 2},
        {"tipo": "Quotas ou quinhões de capital", "total": 250000.5, "quantidade": 1},
    ]
    assert [b["descricao"] for b in ultimo["bens"]] == [b[2] for b in BENS_2026]
    # Cada ponto cita a captura e o SHA-256 de origem (RNF05).
    assert {p["captura_id"] for p in linha["pontos"]} <= set(coleta["captura_ids"])
    assert all(len(p["sha256"]) == 64 and p["fonte"] == "tse_bens" for p in linha["pontos"])
    assert linha["variacoes"] == [
        {"de_ano": "2022", "para_ano": "2026", "diferenca": 1150000.5, "percentual": 328.57}
    ]
    # Sem fonte de renda pública no sistema, a linha diz isso e não há alerta L11.
    assert linha["renda_publica"]["conhecida"] is False and linha["renda_publica"]["motivo"]
    # Consultar a linha do tempo fica na trilha de auditoria.
    trilha = (
        amb["cliente"]
        .get("/auditoria", params={"caso_id": amb["caso_id"]}, headers=cabecalhos("tok-delegado"))
        .json()
    )
    assert any(e["acao"] == "patrimonio.ver" for e in trilha)


def test_h51_candidatura_unica_sem_bens_ou_entidade_que_nao_e_candidato(amb, pessoa):
    coletar(amb, "cnpj_arquivo", {"diretorio": "cnpj"})
    candidaturas(amb, pessoa, anos=(("2026", "990000000026"),))
    linha = _patrimonio(amb, candidato(amb, "2026")["id"])
    assert linha["ligacao"] == "candidatura_unica" and linha["variacoes"] == []
    (ponto,) = linha["pontos"]
    assert (ponto["total"], ponto["quantidade"], ponto["bens"], ponto["captura_id"]) == (
        None,
        0,
        [],
        None,
    )
    empresa = entidades(amb, "empresa")[0]
    assert "candidato" in _patrimonio(amb, empresa["id"], esperado=422)["detail"]
    _patrimonio(amb, "inexistente", esperado=404)


def test_h51_ponto_sem_valor_declarado_nao_entra_na_variacao(amb, pessoa):
    candidaturas(amb, pessoa)
    _arquivo_de_bens(amb, pessoa, "2026", "990000000026", BENS_2026)
    coletar(amb, "tse_bens", {"diretorio": "bens", "do_caso": True})
    linha = _patrimonio(amb, candidato(amb, "2026")["id"])
    assert [p["total"] for p in linha["pontos"]] == [None, 1500000.5]
    # Só há um ponto com bens: não há variação a calcular.
    assert linha["variacoes"] == []


# ==== H52: despesas contratadas de campanha (conector tse_arquivo e aresta) ======================

# Colunas do leiame de despesas contratadas (ver a docstring de ``tse_arquivo``); só as que o
# conector usa e as duas de CPF, que ele nunca lê.
CABECALHO_DESPESAS = (
    '"ANO_ELEICAO";"SG_UF";"SG_UE";"NM_UE";"SQ_CANDIDATO";"NM_CANDIDATO";"NR_CPF_CANDIDATO";'
    '"NR_CPF_VICE_CANDIDATO";"CD_TIPO_FORNECEDOR";"CD_CNAE_FORNECEDOR";"DS_CNAE_FORNECEDOR";'
    '"NR_CPF_CNPJ_FORNECEDOR";"NM_FORNECEDOR";"NM_FORNECEDOR_RFB";"SQ_DESPESA";"DT_DESPESA";'
    '"DS_DESPESA";"VR_DESPESA_CONTRATADA"\n'
)
# Matriz da ALFA nas fixtures sintéticas do CNPJ (básico 90000001).
CNPJ_ALFA = "90000001000129"
CNPJ_FORA = "90000077000100"


def linha_despesa(
    sq_despesa,
    documento,
    valor,
    *,
    sq="990000000026",
    nome=NOME,
    cpf="-4",
    cpf_vice="-4",
    tipo="0",
    cnae="1813001",
    ds_cnae="Impressão de material para uso publicitário",
    fornecedor="FORNECEDOR SINTETICO",
    data="20/08/2026",
    descricao="MATERIAL SINTETICO DE CAMPANHA",
    ano="2026",
) -> str:
    return (
        f'"{ano}";"DF";"DF";"DISTRITO FEDERAL";"{sq}";"{nome}";"{cpf}";"{cpf_vice}";"{tipo}";'
        f'"{cnae}";"{ds_cnae}";"{documento}";"{fornecedor}";"{fornecedor} RFB";"{sq_despesa}";'
        f'"{data}";"{descricao}";"{valor}"\n'
    )


def _pasta_de_despesas(tmp_path: Path, texto: str, nome="despesas_contratadas_candidatos_2026_DF"):
    pasta = tmp_path / "d"
    pasta.mkdir(exist_ok=True)
    (pasta / f"{nome}.csv").write_bytes(texto.encode("latin-1"))
    return pasta


def test_h52_conector_le_despesa_com_fornecedor_pj_e_ignora_fornecedor_pf_com_aviso(
    tmp_path, pessoa
):
    _pasta_de_despesas(
        tmp_path,
        CABECALHO_DESPESAS
        + linha_despesa("7001", CNPJ_ALFA, "12345,67")
        # Fornecedor pessoa física (CPF) e fornecedor sem documento legível: ignorados.
        + linha_despesa("7002", pessoa["outro_cpf"], "500,00", tipo="1", cnae="-1", ds_cnae="#NULO")
        + linha_despesa("7003", "-4", "10,00"),
    )
    resultado = ConectorTseArquivo(dir_base=tmp_path).coletar({"diretorio": "d"}, _Registrador())
    (despesa,) = resultado.registros
    assert isinstance(despesa, RegistroDespesa)
    assert (despesa.ano, despesa.sq_candidato, despesa.nome_candidato) == (
        "2026",
        "990000000026",
        NOME,
    )
    assert (despesa.cnpj_fornecedor, despesa.valor, despesa.data) == (
        CNPJ_ALFA,
        12345.67,
        "2026-08-20",
    )
    assert (despesa.cnae, despesa.cnae_descricao) == (
        "1813001",
        "Impressão de material para uso publicitário",
    )
    # O nome da Receita tem preferência; a chave é o SQ_DESPESA.
    assert despesa.nome_fornecedor == "FORNECEDOR SINTETICO RFB" and despesa.chave == "7001"
    assert despesa.descricao == "MATERIAL SINTETICO DE CAMPANHA"
    (aviso,) = [a for a in resultado.avisos if "pessoa física" in a]
    assert aviso.startswith("2 despesa(s)") and "ignoradas" in aviso
    # O CPF do fornecedor pessoa física não chega a registro nem a aviso.
    assert pessoa["outro_cpf"] not in repr(resultado.registros) + repr(resultado.avisos)


def test_h52_cpf_do_candidato_e_do_vice_no_arquivo_de_despesas_nunca_aparecem(tmp_path, pessoa):
    vice = cpf_sintetico()
    _pasta_de_despesas(
        tmp_path,
        CABECALHO_DESPESAS
        + linha_despesa("7001", CNPJ_ALFA, "100,00", cpf=pessoa["cpf"], cpf_vice=vice),
    )
    registrador = _Registrador()
    resultado = ConectorTseArquivo(dir_base=tmp_path, resumir_titulo=lambda t: "r" * 64).coletar(
        {"diretorio": "d"}, registrador
    )
    texto = repr(resultado.registros) + repr(resultado.avisos)
    for cpf in (pessoa["cpf"], vice):
        # Nem inteiro nem reduzido: as duas colunas não são lidas neste arquivo.
        assert cpf not in texto and mascarado(cpf) not in texto
    assert all(isinstance(r, RegistroDespesa) for r in resultado.registros)
    # A carga bruta fica como veio (é a prova).
    assert pessoa["cpf"].encode() in registrador.capturas[0]["conteudo"]


def test_h52_layout_de_despesas_alterado_e_recusado(tmp_path):
    _pasta_de_despesas(tmp_path, '"SQ_CANDIDATO";"NM_FORNECEDOR";"VALOR_NOVO"\n"1";"x";"1,00"\n')
    with pytest.raises(ErroConector, match="layout pode ter mudado"):
        ConectorTseArquivo(dir_base=tmp_path).coletar({"diretorio": "d"}, _Registrador())


def test_h52_arquivo_de_despesas_pagas_nao_e_lido(tmp_path):
    _pasta_de_despesas(
        tmp_path,
        CABECALHO_DESPESAS + linha_despesa("7001", CNPJ_ALFA, "100,00"),
        nome="despesas_pagas_candidatos_2026_DF",
    )
    registrador = _Registrador()
    with pytest.raises(ErroConector, match="Nenhum arquivo do TSE"):
        ConectorTseArquivo(dir_base=tmp_path).coletar({"diretorio": "d"}, registrador)
    assert registrador.capturas == []
    # Com o arquivo de contratadas ao lado, só ele vira captura e registro.
    _pasta_de_despesas(tmp_path, CABECALHO_DESPESAS + linha_despesa("7009", CNPJ_ALFA, "1,00"))
    resultado = ConectorTseArquivo(dir_base=tmp_path).coletar({"diretorio": "d"}, registrador)
    assert [r.chave for r in resultado.registros] == ["7009"]
    assert len(registrador.capturas) == 1
    assert "despesas_contratadas" in registrador.capturas[0]["origem"]


def test_h52_sq_despesa_e_a_chave_e_sem_ele_a_chave_nao_expoe_o_documento(tmp_path):
    _pasta_de_despesas(
        tmp_path,
        CABECALHO_DESPESAS
        + linha_despesa("7001", CNPJ_ALFA, "100,00")
        + linha_despesa("7002", CNPJ_ALFA, "100,00")
        # Sem SQ_DESPESA: duas linhas iguais continuam duas despesas (resumo + ocorrência).
        + linha_despesa("#NULO", CNPJ_ALFA, "30,00")
        + linha_despesa("#NULO", CNPJ_ALFA, "30,00"),
    )
    resultado = ConectorTseArquivo(dir_base=tmp_path).coletar({"diretorio": "d"}, _Registrador())
    chaves = [r.chave for r in resultado.registros]
    assert chaves[:2] == ["7001", "7002"]
    assert len(set(chaves)) == 4 and chaves[2].endswith("#1") and chaves[3].endswith("#2")
    assert CNPJ_ALFA not in chaves[2]


def _despesas_no_caso(amb, linhas: str) -> dict:
    (amb["tse"] / "e2026" / "despesas_contratadas_candidatos_2026_DF.csv").write_bytes(
        (CABECALHO_DESPESAS + linhas).encode("latin-1")
    )
    return coletar(amb, "tse_arquivo", {"diretorio": "e2026"})


def _grafo(amb, centro: str) -> dict:
    r = amb["cliente"].get(
        f"/casos/{amb['caso_id']}/grafo",
        params={"centro": centro, "profundidade": 1},
        headers=cabecalhos(),
    )
    assert r.status_code == 200, r.text
    return r.json()


def test_h52_despesa_vira_aresta_do_candidato_para_a_empresa_no_grafo_e_na_exportacao(amb, pessoa):
    coletar(amb, "cnpj_arquivo", {"diretorio": "cnpj"})
    candidaturas(amb, pessoa, anos=(("2026", "990000000026"),))
    coleta = _despesas_no_caso(
        amb,
        linha_despesa("7001", CNPJ_ALFA, "12345,67", cpf=pessoa["cpf"])
        + linha_despesa("7002", CNPJ_ALFA, "1000,00", data="01/09/2026")
        + linha_despesa("7003", CNPJ_FORA, "50,00", fornecedor="FORNECEDOR DE FORA SINTETICO")
        + linha_despesa("7004", pessoa["outro_cpf"], "70,00", tipo="1"),
    )
    cand = candidato(amb, "2026")
    alfa = next(e for e in entidades(amb, "empresa") if e["identificador"] == "90000001")
    # A empresa já coletada no CNPJ é a mesma entidade (CNPJ básico); a de fora é criada.
    assert alfa["rotulo"] == "ALFA CONSTRUCOES SINTETICA LTDA"
    fora = next(e for e in entidades(amb, "empresa") if e["identificador"] == "90000077")
    assert fora["rotulo"] == "FORNECEDOR DE FORA SINTETICO RFB"
    grafo = _grafo(amb, cand["id"])
    despesas = [a for a in grafo["arestas"] if a["tipo"] == "despesa_de_campanha"]
    assert len(despesas) == 3
    assert {(a["origem"], a["destino"]) for a in despesas} == {
        (cand["id"], alfa["id"]),
        (cand["id"], fora["id"]),
    }
    primeira = next(a for a in despesas if a["atributos"]["valor"] == 12345.67)
    assert primeira["atributos"] == {
        "valor": 12345.67,
        "data": "2026-08-20",
        "ano": "2026",
        "descricao": "MATERIAL SINTETICO DE CAMPANHA",
        "cnae_fornecedor": "1813001",
        "cnae_fornecedor_descricao": "Impressão de material para uso publicitário",
        "cnpj_fornecedor": CNPJ_ALFA,
    }
    # Custódia: a aresta aponta para a captura do arquivo de despesas.
    assert primeira["captura_id"] in coleta["captura_ids"]
    # Recoletar o mesmo arquivo não duplica as arestas (a chave é o SQ_DESPESA).
    coletar(amb, "tse_arquivo", {"diretorio": "e2026"})
    de_novo = [a for a in _grafo(amb, cand["id"])["arestas"] if a["tipo"] == "despesa_de_campanha"]
    assert sorted(a["id"] for a in de_novo) == sorted(a["id"] for a in despesas)
    # Exportação: o vínculo sai no CSV de vínculos e no GraphML.
    h = cabecalhos("tok-delegado")
    for formato in ("csv_vinculos", "graphml"):
        r = amb["cliente"].get(
            f"/casos/{amb['caso_id']}/exportacao", params={"formato": formato}, headers=h
        )
        assert r.status_code == 200, r.text
        assert r.text.count("despesa_de_campanha") == 3, formato
    # Nenhum CPF sai: nem o do candidato (coluna não lida neste arquivo) nem o do fornecedor PF.
    for texto in saidas_da_api(amb):
        assert pessoa["cpf"] not in texto and pessoa["outro_cpf"] not in texto


# ==== H46 (L9) e H52 (L13) pela API: da coleta ao alerta =========================================


def _executar(amb, *codigos: str, **parametros) -> list[dict]:
    r = amb["cliente"].post(
        f"/casos/{amb['caso_id']}/alertas/executar",
        json={"tipologias": list(codigos), "parametros": parametros},
        headers=cabecalhos(),
    )
    assert r.status_code == 200, r.text
    return r.json()


def test_h46_l9_pela_api_segue_a_decisao_do_investigador_sobre_a_sugestao(amb, pessoa):
    # O mesmo sócio sintético entra também na BETA, contratada por órgão estadual do DF em
    # 01/07/2026 (fixtures do PNCP); a ALFA só tem contrato com órgão municipal.
    socios = next((amb["tse"].parent / "ingestao_cnpj" / "cnpj").glob("*SOCIOCSV"))
    with socios.open("a", encoding="latin-1") as arquivo:
        arquivo.write(
            f'"90000002";"2";"{NOME}";"{mascarado(pessoa["cpf"])}";"49";"20200310";"";'
            '"***000000**";"";"00";"5"\n'
        )
    coletar(amb, "cnpj_arquivo", {"diretorio": "cnpj"})
    coletar(amb, "pncp", {"dataInicial": "20260101", "dataFinal": "20261231", "max_paginas": 5})
    # Eleito em 2022 (deputado distrital, DF): mandato tratado de 01/01/2023 a 31/12/2026.
    candidaturas(amb, pessoa, anos=(("2022", "990000000022"),))
    cand = candidato(amb, "2022")
    (sugestao,) = _sugestoes(amb, cand["id"])
    (alerta,) = _executar(amb, "L9")
    assert alerta["tipologia"] == "L9" and alerta["severidade"] == "alta"
    assert alerta["parametros"]["sugestao_situacao"] == "pendente"
    assert alerta["parametros"]["sugestao_id"] == sugestao["id"]
    assert "BETA ENGENHARIA SINTETICA LTDA" in alerta["motivo"]
    assert "SECRETARIA ESTADUAL SINTETICA DE OBRAS" in alerta["motivo"]
    assert "ALFA" not in alerta["motivo"]
    assert {e["fonte"] for e in alerta["evidencias"]} == {"tse_arquivo", "cnpj_arquivo", "pncp"}
    assert all(len(e["sha256"]) == 64 for e in alerta["evidencias"])
    corpo = {"decisao": "confirmado", "justificativa": "Conferido no registro de candidatura"}
    assert _decidir(amb, sugestao["id"], corpo).status_code == 200
    (confirmado,) = _executar(amb, "L9")
    assert confirmado["id"] == alerta["id"]
    assert confirmado["parametros"]["sugestao_situacao"] == "confirmado"
    assert "confirmada pelo investigador" in confirmado["motivo"]
    corpo = {"decisao": "descartado", "justificativa": "Homônimo"}
    assert _decidir(amb, sugestao["id"], corpo).status_code == 200
    assert _executar(amb, "L9") == []
    # Nem o CPF inteiro, nem o título, nem o seu resumo saem em alerta, relatório ou exportação.
    assert _decidir(amb, sugestao["id"], {**corpo, "decisao": "confirmado"}).status_code == 200
    # Mandato de 3 anos (até 31/12/2025) deixa o contrato de 2026 de fora.
    assert _executar(amb, "L9", l9_anos_mandato=3) == []
    assert len(_executar(amb, "L9")) == 1
    resumo = resumo_esperado(pessoa["titulo"])
    for texto in saidas_da_api(amb):
        assert pessoa["cpf"] not in texto and pessoa["titulo"] not in texto
        assert resumo not in texto


def test_h52_l13_pela_api_alerta_o_fornecedor_recem_aberto_e_avisa_do_cnae_desligado(amb, pessoa):
    coletar(amb, "cnpj_arquivo", {"diretorio": "cnpj"})
    candidaturas(amb, pessoa, anos=(("2026", "990000000026"),))
    # BETA iniciou a atividade em 10/05/2026 (fixtures do CNPJ); a ALFA, em 2015.
    _despesas_no_caso(
        amb,
        linha_despesa("7001", CNPJ_ALFA, "9000,00")
        + linha_despesa("7002", "90000002000173", "4000,00")
        + linha_despesa("7003", "90000002000173", "1500,50", data="01/09/2026"),
    )
    (alerta,) = _executar(amb, "L13")
    assert alerta["tipologia"] == "L13" and alerta["severidade"] == "media"
    assert alerta["parametros"]["sinais"] == ["recem_aberta"]
    assert alerta["parametros"]["total_despesas"] == 5500.5
    assert alerta["parametros"]["menor_intervalo_dias"] == 102
    assert "BETA ENGENHARIA SINTETICA LTDA" in alerta["motivo"] and NOME in alerta["motivo"]
    assert [e["fonte"] for e in alerta["evidencias"]] == [
        "cnpj_arquivo",
        "tse_arquivo",
        "tse_arquivo",
    ]
    caso = amb["cliente"].get(f"/casos/{amb['caso_id']}", headers=cabecalhos()).text
    assert "critério do CNAE está desligado" in caso
    # Limiar por execução; parâmetro estruturado (a lista de CNAE) não é aceito na chamada.
    assert _executar(amb, "L13", l13_dias_abertura=100) == []
    r = amb["cliente"].post(
        f"/casos/{amb['caso_id']}/alertas/executar",
        json={"tipologias": ["L13"], "parametros": {"l13_cnaes_campanha": ["18"]}},
        headers=cabecalhos(),
    )
    assert r.status_code == 422
    # As duas tipologias aparecem no catálogo da interface.
    r = amb["cliente"].get("/tipologias", headers=cabecalhos())
    assert r.status_code == 200, r.text
    assert {"L9", "L13"} <= {t["codigo"] for t in r.json()}
