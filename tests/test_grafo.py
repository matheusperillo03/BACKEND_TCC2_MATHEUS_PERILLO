import pytest

from buscador import grafo
from tests.conftest import entidade_por_identificador


@pytest.fixture
def g(cliente, caso_carregado, app):
    sessao = app.state.fabrica_sessoes()
    try:
        yield grafo.construir(sessao, caso_carregado), sessao
    finally:
        sessao.close()


def test_construir_tem_todas_as_entidades_e_arestas(g):
    grafo_, _ = g
    tipos = {d["tipo"] for _, d in grafo_.nodes(data=True)}
    assert tipos == {"empresa", "pessoa", "orgao", "contrato"}
    tipos_arestas = {d["tipo"] for _, _, d in grafo_.edges(data=True)}
    assert tipos_arestas == {
        "socio_de",
        "contratado_por",
        "fornecedor_de",
        "contratante_de",
        "mesmo_endereco",
    }


def test_vizinhanca_por_profundidade(cliente, caso_carregado, g):
    grafo_, _ = g
    ricardo = entidade_por_identificador(cliente, caso_carregado, "***666666**", "pessoa")
    n0 = grafo.vizinhanca(grafo_, ricardo["id"], 0)
    assert set(n0.nodes) == {ricardo["id"]}
    n1 = grafo.vizinhanca(grafo_, ricardo["id"], 1)
    assert {d["tipo"] for _, d in n1.nodes(data=True)} == {"pessoa", "empresa"}
    assert n1.number_of_nodes() == 5  # Ricardo + 4 empresas
    n2 = grafo.vizinhanca(grafo_, ricardo["id"], 2)
    assert n2.number_of_nodes() > n1.number_of_nodes()
    assert "contrato" in {d["tipo"] for _, d in n2.nodes(data=True)}
    assert grafo.vizinhanca(grafo_, None, 1) is grafo_


def test_caminho_mais_curto_ignora_direcao(cliente, caso_carregado, g):
    grafo_, _ = g
    beta_socio = entidade_por_identificador(
        cliente, caso_carregado, "***444444**", "pessoa"
    )  # Pedro
    epsilon_socio = entidade_por_identificador(
        cliente, caso_carregado, "***777777**", "pessoa"
    )  # Lucia
    caminho = grafo.caminho_mais_curto(grafo_, beta_socio["id"], epsilon_socio["id"])
    assert caminho is not None
    # Pedro -> DELTA -> (mesmo_endereco) EPSILON -> Lucia, ou via Ricardo: 4 nós
    assert caminho.number_of_nodes() == 4
    assert caminho.number_of_edges() >= 3


def test_caminho_inexistente_e_erros(g):
    grafo_, _ = g
    ids = list(grafo_.nodes)
    with pytest.raises(grafo.ErroGrafo):
        grafo.vizinhanca(grafo_, "nao-existe", 1)
    with pytest.raises(grafo.ErroGrafo):
        grafo.caminho_mais_curto(grafo_, ids[0], "nao-existe")
    with pytest.raises(grafo.ErroGrafo):
        grafo.vizinhanca(grafo_, ids[0], -1)


def test_grafo_para_dict_segue_o_contrato(g):
    grafo_, _ = g
    d = grafo.grafo_para_dict(grafo_)
    assert set(d) == {"nos", "arestas"}
    assert set(d["nos"][0]) == {"id", "tipo", "rotulo", "identificador"}
    assert set(d["arestas"][0]) == {"id", "origem", "destino", "tipo", "atributos", "captura_id"}
