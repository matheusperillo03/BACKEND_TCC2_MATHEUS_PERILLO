import pytest
from sqlalchemy import select

from buscador.conectores.cnpj_arquivo import ConectorCnpjArquivo
from buscador.conectores.pncp import ConectorPncp
from buscador.modelos import Aresta, Caso, Entidade, Sugestao
from buscador.resolucao import Resolvedor
from tests.conftest import FIXTURES_CNPJ


@pytest.fixture
def caso(sessao):
    caso = Caso(titulo="c", criado_por="t")
    sessao.add(caso)
    sessao.flush()
    return caso


@pytest.fixture
def registrador(sessao, custodia, caso):
    def registrar(fonte, url_ou_arquivo, conteudo, metadados):
        return custodia.registrar(
            sessao,
            caso_id=caso.id,
            fonte=fonte,
            url_ou_arquivo=url_ou_arquivo,
            conteudo=conteudo,
            metadados=metadados,
        )

    return registrar


@pytest.fixture
def resolvido(sessao, caso, registrador, transporte_pncp):
    cnpj = ConectorCnpjArquivo(dir_base=FIXTURES_CNPJ.parent).coletar(
        {"diretorio": FIXTURES_CNPJ.name}, registrador
    )
    pncp = ConectorPncp(transporte=transporte_pncp).coletar(
        {"dataInicial": "20260101", "dataFinal": "20261231"}, registrador
    )
    resolvedor = Resolvedor(sessao, caso.id)
    e1 = resolvedor.incorporar(cnpj.registros)
    e2 = Resolvedor(sessao, caso.id).incorporar(pncp.registros)
    sessao.commit()
    return caso, e1, e2


def _entidades(sessao, caso_id, tipo):
    return list(
        sessao.execute(
            select(Entidade).where(Entidade.caso_id == caso_id, Entidade.tipo == tipo)
        ).scalars()
    )


def _arestas(sessao, caso_id, tipo):
    return list(
        sessao.execute(
            select(Aresta).where(Aresta.caso_id == caso_id, Aresta.tipo == tipo)
        ).scalars()
    )


def test_empresa_e_chaveada_pelo_cnpj_basico_e_funde_estabelecimentos(sessao, resolvido):
    caso, _, _ = resolvido
    empresas = {e.identificador: e for e in _entidades(sessao, caso.id, "empresa")}
    # 15 das fixtures + 1 que só aparece no PNCP (fornecedor desconhecido)
    assert len(empresas) == 16
    eta = empresas["90000007"]
    assert len(eta.atributos["estabelecimentos"]) == 2
    assert eta.atributos["cnpj"] == "90000007000170"[:12] + eta.atributos["cnpj"][12:]
    assert eta.atributos["data_inicio_atividade"] == "2012-05-05"
    assert eta.atributos["capital_social"] == 250000.0
    assert len(eta.atributos["enderecos_normalizados"]) == 2
    desconhecida = empresas["90000099"]
    assert desconhecida.rotulo == "PI FORNECEDOR DESCONHECIDO SINTETICO LTDA"
    assert "capital_social" not in desconhecida.atributos


def test_pessoa_mesmo_cpf_e_nome_e_unificada(sessao, resolvido):
    caso, _, _ = resolvido
    pessoas = _entidades(sessao, caso.id, "pessoa")
    ricardo = [p for p in pessoas if p.atributos["nome_normalizado"] == "RICARDO FICTICIO LIMA"]
    assert len(ricardo) == 1
    assert ricardo[0].identificador == "***666666**"
    vinculos = [a for a in _arestas(sessao, caso.id, "socio_de") if a.origem_id == ricardo[0].id]
    assert len(vinculos) == 4  # DELTA, EPSILON, ZETA e THETA
    assert len({f.captura_id for f in ricardo[0].fontes}) == 1


def test_homonimo_gera_sugestao_sem_fundir(sessao, resolvido):
    caso, e1, _ = resolvido
    pessoas = _entidades(sessao, caso.id, "pessoa")
    carlos = sorted(
        (p for p in pessoas if p.atributos["nome_normalizado"] == "CARLOS HOMONIMO SINTETICO"),
        key=lambda p: p.identificador,
    )
    assert [p.identificador for p in carlos] == ["***222222**", "***232323**"]
    sugestoes = list(sessao.execute(select(Sugestao).where(Sugestao.caso_id == caso.id)).scalars())
    pares = {(s.entidade_id, s.candidata_id) for s in sugestoes}
    assert (carlos[0].id, carlos[1].id) in pares and (carlos[1].id, carlos[0].id) in pares
    s = next(
        s for s in sugestoes if s.entidade_id == carlos[0].id and s.candidata_id == carlos[1].id
    )
    assert s.score == 0.5
    assert "homônimo" in s.motivo and "***222222**" in s.motivo and "***232323**" in s.motivo
    assert e1.sugestoes_novas == 4  # 2 pares (Carlos) + 2 pares (sócio estrangeiro sem CPF)


def test_socio_sem_documento_nunca_e_fundido_pelo_nome(sessao, resolvido):
    caso, _, _ = resolvido
    pessoas = _entidades(sessao, caso.id, "pessoa")
    john = [p for p in pessoas if p.atributos["nome_normalizado"] == "JOHN FOREIGN SAMPLE"]
    assert len(john) == 2
    assert all(p.identificador == "" and p.atributos["estrangeiro"] for p in john)
    sugestoes = list(
        sessao.execute(select(Sugestao).where(Sugestao.entidade_id == john[0].id)).scalars()
    )
    assert sugestoes and sugestoes[0].score == 0.7


def test_socio_pessoa_juridica_vira_aresta_entre_empresas(sessao, resolvido):
    caso, _, _ = resolvido
    empresas = {e.identificador: e for e in _entidades(sessao, caso.id, "empresa")}
    arestas = [
        a
        for a in _arestas(sessao, caso.id, "socio_de")
        if a.origem_id == empresas["90000001"].id and a.destino_id == empresas["90000010"].id
    ]
    assert len(arestas) == 1
    assert arestas[0].atributos["identificador_socio"] == "1"


def test_mesmo_endereco_e_derivado_do_estabelecimento(sessao, resolvido):
    caso, _, _ = resolvido
    empresas = {e.identificador: e for e in _entidades(sessao, caso.id, "empresa")}
    grupo = {empresas[b].id for b in ("90000004", "90000005", "90000006")}
    arestas = [a for a in _arestas(sessao, caso.id, "mesmo_endereco") if a.origem_id in grupo]
    assert len(arestas) == 3  # clique de 3 = 3 pares
    assert all(a.destino_id in grupo for a in arestas)
    assert all(a.atributos["derivada"] for a in arestas)
    # Grupo J (12, 13 e 14) também compartilha endereço, embora sem contrato.
    assert len(_arestas(sessao, caso.id, "mesmo_endereco")) == 6


def test_contratos_geram_orgaos_contratos_e_arestas(sessao, resolvido):
    caso, _, e2 = resolvido
    orgaos = _entidades(sessao, caso.id, "orgao")
    contratos = _entidades(sessao, caso.id, "contrato")
    assert {o.identificador for o in orgaos} == {"90100001000109", "90100002000145"}
    assert len(contratos) == 9
    assert len(_arestas(sessao, caso.id, "contratado_por")) == 9
    assert len(_arestas(sessao, caso.id, "fornecedor_de")) == 9
    assert len(_arestas(sessao, caso.id, "contratante_de")) == 9
    assert e2.entidades_novas == 2 + 9 + 1  # órgãos + contratos + empresa desconhecida
    contratado = _arestas(sessao, caso.id, "contratado_por")[0]
    assert contratado.atributos["numero_controle_pncp"] and contratado.captura_id


def test_reincorporar_e_idempotente(sessao, resolvido, registrador, transporte_pncp):
    caso, _, _ = resolvido
    antes = (
        len(_entidades(sessao, caso.id, "empresa")),
        len(list(sessao.execute(select(Aresta)).scalars())),
    )
    cnpj = ConectorCnpjArquivo(dir_base=FIXTURES_CNPJ.parent).coletar(
        {"diretorio": FIXTURES_CNPJ.name}, registrador
    )
    stats = Resolvedor(sessao, caso.id).incorporar(cnpj.registros)
    sessao.commit()
    depois = (
        len(_entidades(sessao, caso.id, "empresa")),
        len(list(sessao.execute(select(Aresta)).scalars())),
    )
    assert antes == depois
    assert stats.entidades_novas == 0 and stats.arestas_novas == 0 and stats.sugestoes_novas == 0
