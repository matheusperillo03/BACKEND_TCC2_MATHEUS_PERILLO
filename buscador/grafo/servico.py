from __future__ import annotations

import networkx as nx
from sqlalchemy import select
from sqlalchemy.orm import Session

from buscador.modelos import Aresta, Entidade


class ErroGrafo(Exception):
    pass


def construir(sessao: Session, caso_id: str) -> nx.MultiDiGraph:
    """Monta um MultiDiGraph com todas as entidades e arestas do caso."""
    grafo = nx.MultiDiGraph()
    for e in sessao.execute(select(Entidade).where(Entidade.caso_id == caso_id)).scalars():
        grafo.add_node(e.id, tipo=e.tipo, rotulo=e.rotulo, identificador=e.identificador)
    for a in sessao.execute(select(Aresta).where(Aresta.caso_id == caso_id)).scalars():
        grafo.add_edge(
            a.origem_id,
            a.destino_id,
            key=a.id,
            tipo=a.tipo,
            atributos=a.atributos or {},
            captura_id=a.captura_id,
        )
    return grafo


def grafo_para_dict(grafo: nx.MultiDiGraph) -> dict:
    nos = [
        {
            "id": n,
            "tipo": d.get("tipo"),
            "rotulo": d.get("rotulo"),
            "identificador": d.get("identificador"),
        }
        for n, d in grafo.nodes(data=True)
    ]
    arestas = [
        {
            "id": chave,
            "origem": origem,
            "destino": destino,
            "tipo": d.get("tipo"),
            "atributos": d.get("atributos") or {},
            "captura_id": d.get("captura_id"),
        }
        for origem, destino, chave, d in grafo.edges(keys=True, data=True)
    ]
    nos.sort(key=lambda n: (n["tipo"] or "", n["rotulo"] or ""))
    arestas.sort(key=lambda a: (a["tipo"] or "", a["origem"], a["destino"]))
    return {"nos": nos, "arestas": arestas}


def vizinhanca(grafo: nx.MultiDiGraph, centro: str | None, profundidade: int) -> nx.MultiDiGraph:
    """Subgrafo até ``profundidade`` saltos do centro, sem considerar a direção.

    Sem centro, devolve o grafo inteiro."""
    if centro is None:
        return grafo
    if centro not in grafo:
        raise ErroGrafo(f"Entidade {centro} não pertence ao caso.")
    if profundidade < 0:
        raise ErroGrafo("A profundidade deve ser maior ou igual a zero.")
    ego = nx.ego_graph(grafo, centro, radius=profundidade, undirected=True)
    return grafo.subgraph(ego.nodes).copy()


def caminho_mais_curto(grafo: nx.MultiDiGraph, de: str, para: str) -> nx.MultiDiGraph | None:
    """Caminho mais curto (ignorando direção) como subgrafo; None quando não há ligação."""
    for n in (de, para):
        if n not in grafo:
            raise ErroGrafo(f"Entidade {n} não pertence ao caso.")
    sem_direcao = grafo.to_undirected(as_view=True)
    try:
        nos = nx.shortest_path(sem_direcao, de, para)
    except nx.NetworkXNoPath:
        return None
    resultado = nx.MultiDiGraph()
    for n in nos:
        resultado.add_node(n, **grafo.nodes[n])
    for a, b in zip(nos, nos[1:], strict=False):
        for origem, destino in ((a, b), (b, a)):
            if grafo.has_edge(origem, destino):
                for chave, dados in grafo[origem][destino].items():
                    resultado.add_edge(origem, destino, key=chave, **dados)
    return resultado
