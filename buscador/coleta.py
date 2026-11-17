"""Serviço de coleta: conector -> cadeia de custódia -> resolução de entidades.

As capturas são registradas na sessão antes da resolução, e a resolução roda dentro de um
SAVEPOINT: se ela falhar, só o trabalho derivado é desfeito e as capturas continuam na sessão,
para a rota confirmá-las junto com a entrada ``coleta.falhou`` da auditoria.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import BinaryIO

from sqlalchemy.orm import Session

from buscador.conectores import criar_conector
from buscador.config import Configuracao
from buscador.custodia import Custodia
from buscador.modelos import Captura, Caso
from buscador.resolucao import Resolvedor


@dataclass
class AndamentoColeta:
    """O que já se sabe de uma coleta em curso, preenchido conforme ela avança. Quem chama fica
    sabendo o que foi guardado e quantas tentativas houve mesmo quando a coleta falha no meio."""

    captura_ids: list[str] = field(default_factory=list)
    conector_nome: str = ""
    conector_versao: str = ""
    # Maior número de tentativas que uma operação precisou (0 = nada chegou a ser tentado).
    tentativas: int = 0
    ultima_falha_transitoria: str = ""


@dataclass
class ResultadoColetaCaso:
    fonte: str
    captura_ids: list[str]
    entidades_novas: int
    entidades_atualizadas: int
    arestas_novas: int
    sugestoes_novas: int
    registros: int
    avisos: list[str] = field(default_factory=list)

    def para_dict(self) -> dict:
        return {
            "fonte": self.fonte,
            "captura_ids": self.captura_ids,
            "entidades_novas": self.entidades_novas,
            "entidades_atualizadas": self.entidades_atualizadas,
            "arestas_novas": self.arestas_novas,
            "sugestoes_novas": self.sugestoes_novas,
            "registros": self.registros,
            "avisos": self.avisos,
        }


def executar_coleta(
    sessao: Session,
    *,
    config: Configuracao,
    custodia: Custodia,
    caso: Caso,
    fonte: str,
    parametros: dict,
    extras_conector: dict | None = None,
    andamento: AndamentoColeta | None = None,
    coleta_id: str | None = None,
) -> ResultadoColetaCaso:
    """``andamento`` (opcional) recebe o id de cada captura assim que ela é registrada, o conector
    usado e as tentativas feitas. ``coleta_id`` liga as capturas à linha da coleta."""
    andamento = andamento if andamento is not None else AndamentoColeta()
    conector = criar_conector(fonte, config, **(extras_conector or {}))
    andamento.conector_nome = conector.nome
    andamento.conector_versao = conector.versao

    def registrar(
        fonte_: str, url_ou_arquivo: str, conteudo: bytes | BinaryIO, metadados: dict
    ) -> Captura:
        captura = custodia.registrar(
            sessao,
            caso_id=caso.id,
            fonte=fonte_,
            url_ou_arquivo=url_ou_arquivo,
            conteudo=conteudo,
            metadados=metadados,
            conector_nome=conector.nome,
            conector_versao=conector.versao,
            coleta_id=coleta_id,
        )
        andamento.captura_ids.append(captura.id)
        return captura

    try:
        resultado = conector.coletar(parametros, registrar)
    finally:
        andamento.tentativas = conector.tentativas
        andamento.ultima_falha_transitoria = conector.retentativa.ultima_falha
    with sessao.begin_nested():
        resolvedor = Resolvedor(sessao, caso.id, max_grupo=config.resolucao_max_grupo)
        estatisticas = resolvedor.incorporar(resultado.registros)
    return ResultadoColetaCaso(
        fonte=fonte,
        captura_ids=[c.id for c in resultado.capturas],
        entidades_novas=estatisticas.entidades_novas,
        entidades_atualizadas=estatisticas.entidades_atualizadas,
        arestas_novas=estatisticas.arestas_novas,
        sugestoes_novas=estatisticas.sugestoes_novas,
        registros=len(resultado.registros),
        avisos=resultado.avisos + estatisticas.avisos,
    )
