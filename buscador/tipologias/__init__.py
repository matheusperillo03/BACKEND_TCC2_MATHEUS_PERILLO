"""Tipologias de alerta (RF04) com explicabilidade (RNF05): regra, motivo em texto e evidências."""

from buscador.tipologias import motor as _motor
from buscador.tipologias.horizonte2 import (
    TIPOLOGIAS_H2,
    tipologia_t5,
    tipologia_t6,
    tipologia_t7,
    tipologia_t8,
    tipologia_t9,
    tipologia_t10,
)
from buscador.tipologias.lavagem import TIPOLOGIAS_LAVAGEM
from buscador.tipologias.motor import (
    NOMES_PARAMETROS,
    SITUACOES_CADASTRAIS,
    TIPOLOGIAS,
    AlertaDetectado,
    Evidencia,
    avisos_do_caso,
    executar,
    ordem_tipologias,
    ordenar,
    parametros_efetivos,
    tipologia_t1,
    tipologia_t2,
    tipologia_t3,
    tipologia_t4,
)
from buscador.tipologias.politicas import POLITICAS, Politica, politica

# As tipologias do horizonte 2 entram no mesmo registro do motor (um só ponto de execução).
_motor.TIPOLOGIAS.update(TIPOLOGIAS_H2)
# As tipologias de lavagem e ocultação (L) também: mesmo contrato, mesma execução.
_motor.TIPOLOGIAS.update(TIPOLOGIAS_LAVAGEM)

__all__ = [
    "NOMES_PARAMETROS",
    "POLITICAS",
    "SITUACOES_CADASTRAIS",
    "TIPOLOGIAS",
    "AlertaDetectado",
    "Evidencia",
    "Politica",
    "avisos_do_caso",
    "executar",
    "ordem_tipologias",
    "ordenar",
    "parametros_efetivos",
    "politica",
    "tipologia_t1",
    "tipologia_t2",
    "tipologia_t3",
    "tipologia_t4",
    "tipologia_t5",
    "tipologia_t6",
    "tipologia_t7",
    "tipologia_t8",
    "tipologia_t9",
    "tipologia_t10",
]
