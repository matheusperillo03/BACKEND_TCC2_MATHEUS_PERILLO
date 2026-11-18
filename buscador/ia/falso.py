"""Provedor falso, determinístico, para os testes e para a demonstração (sem modelo nenhum).

Por padrão ele "extrai" por expressões regulares os CNPJs e os valores em reais do bloco e cita a
linha em que cada um aparece: serve para demonstrar o fluxo (execução, sugestões, revisão) sem
instalar um modelo. Nos testes, ``respostas`` substitui esse comportamento por respostas prontas,
inclusive inválidas ou com trecho inventado, para provar que o serviço as recusa. Ele não mede
nada: as métricas saem vazias, a não ser as fixas passadas em ``metricas`` (um dicionário para
todas as chamadas ou uma lista, uma por chamada).
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable

from buscador.ia.provedor import Controle, Geracao, Identidade

# CNPJ pontuado, numérico ou alfanumérico (12 posições com dígitos ou letras de A a Z + 2 DVs).
_CNPJ = re.compile(
    r"(?<![0-9A-Za-z])[0-9A-Z]{2}\.[0-9A-Z]{3}\.[0-9A-Z]{3}/[0-9A-Z]{4}-\d{2}(?![0-9A-Za-z])"
)
_VALOR = re.compile(r"R\$ ?\d{1,3}(?:\.\d{3})*,\d{2}")


class ProvedorFalso:
    nome = "falso"

    def __init__(
        self,
        respostas: Callable[[str], str] | list[str] | None = None,
        modelo: str = "modelo-falso",
        versao: str = "falso-1",
        metricas: dict | list[dict] | None = None,
        contexto: int | None = None,
    ) -> None:
        self._respostas = respostas
        self._metricas = metricas
        self._contexto = contexto
        self._modelo = modelo
        self._versao = versao
        self.blocos_recebidos: list[str] = []

    def identidade(self) -> Identidade:
        return Identidade(self.nome, self._modelo, self._versao, "0")

    def parametros(self) -> dict:
        return {"temperature": 0, "seed": 0, "deterministico": True}

    def contexto_efetivo(self) -> int | None:
        return self._contexto

    def _metricas_da_chamada(self) -> dict:
        if isinstance(self._metricas, list):
            if not self._metricas:
                return {}
            return dict(self._metricas[min(len(self.blocos_recebidos), len(self._metricas)) - 1])
        return dict(self._metricas or {})

    def gerar(
        self, instrucoes: str, texto: str, esquema: dict, controle: Controle | None = None
    ) -> Geracao:
        if controle is not None:
            controle.conferir_interrupcao()
        self.blocos_recebidos.append(texto)
        return Geracao(self._texto(texto), self._metricas_da_chamada())

    def _texto(self, texto: str) -> str:
        if callable(self._respostas):
            return self._respostas(texto)
        if isinstance(self._respostas, list):
            indice = min(len(self.blocos_recebidos), len(self._respostas)) - 1
            return self._respostas[indice]
        itens = []
        for linha in texto.splitlines():
            if not linha.strip():
                continue
            for achado in _CNPJ.finditer(linha):
                itens.append(
                    {
                        "tipo": "empresa",
                        "valor": achado.group(),
                        "trecho": linha,
                        "campos": {"cnpj": achado.group()},
                        "confianca": 0.5,
                    }
                )
            for achado in _VALOR.finditer(linha):
                itens.append({"tipo": "valor", "valor": achado.group(), "trecho": linha})
        return json.dumps({"itens": itens}, ensure_ascii=False)
