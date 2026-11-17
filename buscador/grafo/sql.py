"""Consultas de vínculo feitas no banco, carregando só o subgrafo necessário (RNF06, H29).

O serviço original (``buscador.grafo.servico``) monta o grafo INTEIRO do caso em memória com o
NetworkX a cada consulta: o custo cresce com o tamanho do caso, e não com o da resposta. Aqui a
busca em largura anda pelos índices de ``arestas`` (``origem_id`` e ``destino_id``), e o custo
passa a depender do que a consulta alcança.

Duas estratégias de vizinhança, comparadas em ``docs/desempenho-grafo.md``:

- ``vizinhanca`` (usada pela API): busca em largura nível a nível; cada nível é uma consulta que
  expande só a fronteira (os nós novos). Permite parar quando a resposta passa do teto de nós.
- ``vizinhanca_recursiva``: uma única consulta recursiva (``WITH RECURSIVE``), suportada pelo
  PostgreSQL e pelo SQLite. Não tem como parar no meio nem como evitar reexpandir um nó que
  reaparece em outro nível; fica para a comparação.

O caminho mais curto usa busca em largura bidirecional (expande sempre a menor fronteira): o
número de nós visitados é da ordem de duas buscas de metade da profundidade, em vez de uma busca
da profundidade inteira.

A direção das arestas é ignorada na travessia, como no serviço original, e a saída tem o mesmo
formato (``{"nos": [...], "arestas": [...]}``, com a mesma ordenação).
"""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import String, any_, literal, select, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Session

from buscador.grafo.servico import ErroGrafo
from buscador.modelos import Aresta, Entidade

# Tamanho dos lotes de identificadores nas consultas com ``IN`` (SQLite). No PostgreSQL a lista
# vai como um único parâmetro de vetor (``= ANY(:ids)``).
_LOTE = 500


class LimiteDoGrafo(ErroGrafo):
    """A consulta alcança mais nós do que o teto configurado."""


def _postgres(sessao: Session) -> bool:
    return sessao.get_bind().dialect.name == "postgresql"


def _lotes(ids: list[str]) -> Iterable[list[str]]:
    for inicio in range(0, len(ids), _LOTE):
        yield ids[inicio : inicio + _LOTE]


def _adjacentes(sessao: Session, caso_id: str, fronteira: Iterable[str]) -> list[tuple[str, str]]:
    """Pares (origem, destino) das arestas do caso que tocam algum nó da fronteira."""
    ids = list(fronteira)
    if not ids:
        return []
    if _postgres(sessao):
        linhas = sessao.execute(
            text(
                "SELECT origem_id, destino_id FROM arestas "
                "WHERE caso_id = :caso AND origem_id = ANY(:ids) "
                "UNION ALL "
                "SELECT origem_id, destino_id FROM arestas "
                "WHERE caso_id = :caso AND destino_id = ANY(:ids)"
            ),
            {"caso": caso_id, "ids": ids},
        )
        return [(o, d) for o, d in linhas]
    pares: list[tuple[str, str]] = []
    for lote in _lotes(ids):
        for coluna in (Aresta.origem_id, Aresta.destino_id):
            pares.extend(
                (o, d)
                for o, d in sessao.execute(
                    select(Aresta.origem_id, Aresta.destino_id).where(
                        Aresta.caso_id == caso_id, coluna.in_(lote)
                    )
                )
            )
    return pares


def _pertence(sessao: Session, caso_id: str, entidade_id: str) -> bool:
    return (
        sessao.execute(
            select(Entidade.id).where(Entidade.id == entidade_id, Entidade.caso_id == caso_id)
        ).first()
        is not None
    )


def _no(linha) -> dict:
    return {
        "id": linha.id,
        "tipo": linha.tipo,
        "rotulo": linha.rotulo,
        "identificador": linha.identificador,
    }


def _aresta(linha) -> dict:
    return {
        "id": linha.id,
        "origem": linha.origem_id,
        "destino": linha.destino_id,
        "tipo": linha.tipo,
        "atributos": linha.atributos or {},
        "captura_id": linha.captura_id,
    }


def _ordenar(nos: list[dict], arestas: list[dict]) -> dict:
    nos.sort(key=lambda n: (n["tipo"] or "", n["rotulo"] or ""))
    arestas.sort(key=lambda a: (a["tipo"] or "", a["origem"], a["destino"]))
    return {"nos": nos, "arestas": arestas}


_COLUNAS_NO = (Entidade.id, Entidade.tipo, Entidade.rotulo, Entidade.identificador)
_COLUNAS_ARESTA = (
    Aresta.id,
    Aresta.origem_id,
    Aresta.destino_id,
    Aresta.tipo,
    Aresta.atributos,
    Aresta.captura_id,
)


def subgrafo(sessao: Session, caso_id: str, ids: set[str]) -> dict:
    """Nós de ``ids`` e as arestas do caso com as duas pontas em ``ids``."""
    lista = sorted(ids)
    if _postgres(sessao):
        vetor = literal(lista, ARRAY(String))
        return _ordenar(
            [
                _no(linha)
                for linha in sessao.execute(
                    select(*_COLUNAS_NO).where(
                        Entidade.caso_id == caso_id, Entidade.id == any_(vetor)
                    )
                )
            ],
            [
                _aresta(linha)
                for linha in sessao.execute(
                    select(*_COLUNAS_ARESTA).where(
                        Aresta.caso_id == caso_id,
                        Aresta.origem_id == any_(vetor),
                        Aresta.destino_id == any_(vetor),
                    )
                )
            ],
        )
    nos: list[dict] = []
    arestas: list[dict] = []
    for lote in _lotes(lista):
        nos.extend(
            _no(linha)
            for linha in sessao.execute(
                select(*_COLUNAS_NO).where(Entidade.caso_id == caso_id, Entidade.id.in_(lote))
            )
        )
        arestas.extend(
            _aresta(linha)
            for linha in sessao.execute(
                select(*_COLUNAS_ARESTA).where(
                    Aresta.caso_id == caso_id, Aresta.origem_id.in_(lote)
                )
            )
            if linha.destino_id in ids
        )
    return _ordenar(nos, arestas)


def grafo_inteiro(sessao: Session, caso_id: str, max_nos: int) -> dict:
    """Todo o grafo do caso, se couber no teto; senão, pede um centro."""
    nos = [
        _no(linha)
        for linha in sessao.execute(
            select(*_COLUNAS_NO).where(Entidade.caso_id == caso_id).limit(max_nos + 1)
        )
    ]
    if len(nos) > max_nos:
        raise LimiteDoGrafo(
            f"O caso tem mais de {max_nos} entidades: informe um centro e uma profundidade para "
            "ver a vizinhança, em vez do grafo inteiro."
        )
    arestas = [
        _aresta(linha)
        for linha in sessao.execute(select(*_COLUNAS_ARESTA).where(Aresta.caso_id == caso_id))
    ]
    return _ordenar(nos, arestas)


def nos_da_vizinhanca(
    sessao: Session, caso_id: str, centro: str, profundidade: int, max_nos: int
) -> set[str]:
    """Busca em largura nível a nível a partir do centro, sem considerar a direção."""
    if profundidade < 0:
        raise ErroGrafo("A profundidade deve ser maior ou igual a zero.")
    if not _pertence(sessao, caso_id, centro):
        raise ErroGrafo(f"Entidade {centro} não pertence ao caso.")
    visitados = {centro}
    fronteira = {centro}
    for _ in range(profundidade):
        nova: set[str] = set()
        for origem, destino in _adjacentes(sessao, caso_id, fronteira):
            for no in (origem, destino):
                if no not in visitados:
                    nova.add(no)
        if not nova:
            break
        visitados |= nova
        if len(visitados) > max_nos:
            raise LimiteDoGrafo(
                f"A vizinhança passa de {max_nos} entidades: reduza a profundidade."
            )
        fronteira = nova
    return visitados


def vizinhanca(
    sessao: Session, caso_id: str, centro: str | None, profundidade: int, max_nos: int
) -> dict:
    """Subgrafo até ``profundidade`` saltos do centro. Sem centro, o grafo inteiro do caso."""
    if centro is None:
        return grafo_inteiro(sessao, caso_id, max_nos)
    return subgrafo(
        sessao, caso_id, nos_da_vizinhanca(sessao, caso_id, centro, profundidade, max_nos)
    )


_SQL_RECURSIVA = text(
    """
    WITH RECURSIVE vizinhos(id, nivel) AS (
        SELECT CAST(:centro AS VARCHAR), 0
        UNION
        SELECT par.outro, vizinhos.nivel + 1
        FROM vizinhos
        JOIN (
            SELECT origem_id AS no, destino_id AS outro FROM arestas WHERE caso_id = :caso
            UNION ALL
            SELECT destino_id AS no, origem_id AS outro FROM arestas WHERE caso_id = :caso
        ) par ON par.no = vizinhos.id
        WHERE vizinhos.nivel < :profundidade
    )
    SELECT DISTINCT id FROM vizinhos
    """
)


def nos_da_vizinhanca_recursiva(
    sessao: Session, caso_id: str, centro: str, profundidade: int
) -> set[str]:
    """A mesma vizinhança, numa única consulta recursiva (para a comparação de desempenho)."""
    if profundidade < 0:
        raise ErroGrafo("A profundidade deve ser maior ou igual a zero.")
    if not _pertence(sessao, caso_id, centro):
        raise ErroGrafo(f"Entidade {centro} não pertence ao caso.")
    linhas = sessao.execute(
        _SQL_RECURSIVA, {"centro": centro, "caso": caso_id, "profundidade": profundidade}
    )
    return {linha[0] for linha in linhas}


def vizinhanca_recursiva(sessao: Session, caso_id: str, centro: str, profundidade: int) -> dict:
    return subgrafo(
        sessao, caso_id, nos_da_vizinhanca_recursiva(sessao, caso_id, centro, profundidade)
    )


def nos_do_caminho(
    sessao: Session, caso_id: str, de: str, para: str, max_visitados: int
) -> list[str] | None:
    """Nós de um caminho mais curto entre ``de`` e ``para`` (sem direção); ``None`` se não há."""
    for no in (de, para):
        if not _pertence(sessao, caso_id, no):
            raise ErroGrafo(f"Entidade {no} não pertence ao caso.")
    if de == para:
        return [de]
    # Uma busca a partir de cada ponta: pai e distância de cada nó alcançado.
    pais = ({de: None}, {para: None})
    distancias = ({de: 0}, {para: 0})
    fronteiras = [{de}, {para}]
    while fronteiras[0] and fronteiras[1]:
        lado = 0 if len(fronteiras[0]) <= len(fronteiras[1]) else 1
        outro = 1 - lado
        fronteira = fronteiras[lado]
        nova: set[str] = set()
        for origem, destino in _adjacentes(sessao, caso_id, fronteira):
            for a, b in ((origem, destino), (destino, origem)):
                if a in fronteira and b not in pais[lado]:
                    pais[lado][b] = a
                    distancias[lado][b] = distancias[lado][a] + 1
                    nova.add(b)
        encontros = nova & pais[outro].keys()
        if encontros:
            # Todos os nós novos estão à mesma distância deste lado; o caminho mais curto passa
            # pelo encontro mais próximo da outra ponta (desempate pelo identificador).
            meio = min(encontros, key=lambda n: (distancias[outro][n], n))
            trecho: list[list[str]] = [[], []]
            for indice in (0, 1):
                no: str | None = meio
                while no is not None:
                    trecho[indice].append(no)
                    no = pais[indice][no]
            return trecho[0][::-1] + trecho[1][1:]
        if len(pais[0]) + len(pais[1]) > max_visitados:
            raise LimiteDoGrafo(
                f"A busca do caminho passou de {max_visitados} entidades visitadas sem encontrar "
                "ligação; as duas entidades estão distantes demais (ou em partes muito grandes "
                "e separadas do grafo)."
            )
        fronteiras[lado] = nova
    return None


def caminho_mais_curto(
    sessao: Session, caso_id: str, de: str, para: str, max_visitados: int
) -> dict | None:
    """Caminho mais curto como subgrafo: os nós do caminho e todas as arestas entre vizinhos
    consecutivos (nos dois sentidos). ``None`` quando não há ligação."""
    nos = nos_do_caminho(sessao, caso_id, de, para, max_visitados)
    if nos is None:
        return None
    resultado = subgrafo(sessao, caso_id, set(nos))
    consecutivos = {frozenset(par) for par in zip(nos, nos[1:], strict=False)}
    resultado["arestas"] = [
        a for a in resultado["arestas"] if frozenset((a["origem"], a["destino"])) in consecutivos
    ]
    return resultado
