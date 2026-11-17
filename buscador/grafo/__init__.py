"""Grafo de vínculos (RF03).

``servico``: grafo inteiro do caso em memória com o NetworkX (implementação original, mantida
para a comparação de desempenho e para os testes). ``sql``: vizinhança e caminho consultados no
banco, carregando só o subgrafo necessário (RNF06, H29); é o que a API usa.
"""

from buscador.grafo import sql
from buscador.grafo.servico import (
    ErroGrafo,
    caminho_mais_curto,
    construir,
    grafo_para_dict,
    vizinhanca,
)
from buscador.grafo.sql import LimiteDoGrafo

__all__ = [
    "ErroGrafo",
    "LimiteDoGrafo",
    "caminho_mais_curto",
    "construir",
    "grafo_para_dict",
    "sql",
    "vizinhanca",
]
