"""Parâmetros legais e listas de termos das tipologias do horizonte 2, cada um com a fonte oficial.

Tudo aqui foi conferido em fonte oficial em 16/11/2026 (ver docs/sistema/fontes-horizonte2.md, na
monografia). O que não pôde ser conferido está marcado como PENDENTE e é configurável.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from buscador.seguranca import numero_finito

URL_LEI_14133 = "https://www.planalto.gov.br/ccivil_03/_ato2019-2022/2021/lei/l14133.htm"
URL_DECRETO_12807 = "https://www.planalto.gov.br/ccivil_03/_ato2023-2026/2025/decreto/d12807.htm"
URL_MANUAL_PNCP = "https://pncp.gov.br/manual/pt-br/latest/singlehtml/"

# ---- T5: limites de dispensa por valor (Lei 14.133/2021, art. 75, I e II) ----------------------
#
# Art. 75: "É dispensável a licitação: I - para contratação que envolva valores inferiores a
# R$ 100.000,00 [...], no caso de obras e serviços de engenharia ou de serviços de manutenção de
# veículos automotores; II - para contratação que envolva valores inferiores a R$ 50.000,00 [...],
# no caso de outros serviços e compras". Os valores são atualizados todo ano (art. 182).
#
# Valores de 2026: Anexo do Decreto nº 12.807, de 29/12/2025 (em vigor em 01/01/2026):
#   art. 75, caput, inciso I:  R$ 130.984,20
#   art. 75, caput, inciso II: R$ 65.492,11
#
# Só 2026 foi conferido. Outros exercícios entram pela configuração (T5_LIMITES_DISPENSA); sem
# limite configurado para o exercício, a T5 não avalia os contratos daquele ano.
FONTE_LIMITES_2026 = (
    "Decreto nº 12.807, de 29/12/2025, Anexo (art. 75, caput, I e II, da Lei nº 14.133/2021); "
    + URL_DECRETO_12807
)
LIMITES_DISPENSA: dict[str, dict] = {
    "2026": {"inciso_i": 130_984.20, "inciso_ii": 65_492.11, "fonte": FONTE_LIMITES_2026},
}

# Regra do somatório (art. 75, § 1º): "deverão ser observados: I - o somatório do que for
# despendido no exercício financeiro pela respectiva unidade gestora; II - o somatório da despesa
# realizada com objetos de mesma natureza, entendidos como tais aqueles relativos a contratações
# no mesmo ramo de atividade."
#
# "Mesma natureza" não é um campo dos dados. Critérios oferecidos (decisão do autor):
CRITERIOS_NATUREZA = {
    "fornecedor_e_categoria": "mesmo fornecedor e mesma categoria do processo (categoriaProcesso)",
    "categoria": "mesma categoria do processo (categoriaProcesso), qualquer fornecedor",
    "fornecedor": "mesmo fornecedor, qualquer categoria",
}

# Categoria do processo (Manual de Integração do PNCP, seção 7.21 "Categoria do Processo";
# URL_MANUAL_PNCP): 1 Cessão, 2 Compras, 3 Informática (TIC), 4 Internacional, 5 Locação Imóveis,
# 6 Mão de Obra, 7 Obras, 8 Serviços, 9 Serviços de Engenharia, 10 Serviços de Saúde,
# 11 Alienação de bens móveis/imóveis.
# Inciso I do art. 75 (obras e serviços de engenharia): categorias 7 e 9. "Serviços de manutenção
# de veículos automotores" não tem categoria própria e por isso cai no inciso II (limite menor).
CATEGORIAS_INCISO_I = {7: "Obras", 9: "Serviços de Engenharia"}

# Esfera do órgão (Manual de Integração do PNCP, campo ``esferaId``): "F - Federal; E - Estadual;
# M - Municipal; D - Distrital".
ESFERAS = {"F": "federal", "E": "estadual", "M": "municipal", "D": "distrital"}

# ---- T8: acréscimos (Lei 14.133/2021, art. 125) ------------------------------------------------
#
# "[...] acréscimos ou supressões de até 25% (vinte e cinco por cento) do valor inicial atualizado
# do contrato que se fizerem nas obras, nos serviços ou nas compras, e, no caso de reforma de
# edifício ou de equipamento, o limite para os acréscimos será de 50% (cinquenta por cento)."
#
# "Reforma de edifício ou de equipamento" é identificada pelo objeto do contrato (texto sem
# acentos, em maiúsculas). Quando o objeto não permite identificar, vale o limite de 25%.
EXPRESSAO_REFORMA = re.compile(
    r"\bREFORMA\b.{0,60}\b(EDIFICIO|EDIFICIOS|EQUIPAMENTO|EQUIPAMENTOS|PREDIO|PREDIOS)\b"
)

# ---- T10: cláusulas potencialmente restritivas em editais --------------------------------------


@dataclass(frozen=True)
class RegraClausula:
    id: str
    descricao: str
    expressao: str
    fonte: str

    def compilar(self) -> re.Pattern[str]:
        return re.compile(self.expressao, re.IGNORECASE)


# Ponto de partida: os exemplos do Manual da Alice (CGU, 2025), seção 2.2 (avaliação textual de
# editais), citados no backlog (H36): exigência de certidão de quitação ou de certidão negativa
# de quitação. As expressões aceitam o texto com ou sem acentos e quebras de linha entre as
# palavras. Outras expressões entram pela configuração (T10_EXPRESSOES_EXTRAS).
_FONTE_ALICE = "Manual da Alice (CGU, 2025), seção 2.2, exemplo citado no backlog (H36)"
REGRAS_CLAUSULAS: tuple[RegraClausula, ...] = (
    RegraClausula(
        id="certidao_negativa_quitacao",
        descricao="Exigência de certidão negativa de quitação",
        expressao=r"certid(?:ão|ao|ões|oes)\s+negativas?\s+de\s+quita(?:ção|cao|çao|cão)",
        fonte=_FONTE_ALICE,
    ),
    RegraClausula(
        id="certidao_quitacao",
        descricao="Exigência de certidão de quitação",
        expressao=r"certid(?:ão|ao|ões|oes)\s+de\s+quita(?:ção|cao|çao|cão)",
        fonte=_FONTE_ALICE,
    ),
)

MAX_EXPRESSOES_EXTRAS = 20
MAX_TAMANHO_EXPRESSAO = 200


def expressoes_extras(texto: str) -> list[str]:
    """Expressões regulares extras da T10 (``;;`` separa). Recusa expressão inválida."""
    expressoes = [e.strip() for e in (texto or "").split(";;") if e.strip()]
    if len(expressoes) > MAX_EXPRESSOES_EXTRAS:
        raise ValueError(f"T10_EXPRESSOES_EXTRAS aceita até {MAX_EXPRESSOES_EXTRAS} expressões.")
    for expressao in expressoes:
        if len(expressao) > MAX_TAMANHO_EXPRESSAO:
            raise ValueError(
                f"Expressão de T10_EXPRESSOES_EXTRAS acima de {MAX_TAMANHO_EXPRESSAO} caracteres."
            )
        try:
            re.compile(expressao)
        except re.error as erro:
            raise ValueError(
                f"Expressão inválida em T10_EXPRESSOES_EXTRAS: {expressao[:60]}"
            ) from erro
    return expressoes


def regras_clausulas(extras: list[str] | None) -> list[RegraClausula]:
    regras = list(REGRAS_CLAUSULAS)
    for posicao, expressao in enumerate(extras or [], start=1):
        regras.append(
            RegraClausula(
                id=f"extra_{posicao}",
                descricao=f"Expressão configurada pelo operador ({expressao})",
                expressao=expressao,
                fonte="Configuração do operador (T10_EXPRESSOES_EXTRAS)",
            )
        )
    return regras


def validar_criterio_natureza(criterio: str) -> str:
    criterio = (criterio or "").strip()
    if criterio not in CRITERIOS_NATUREZA:
        raise ValueError(
            f"T5_CRITERIO_NATUREZA inválido: '{criterio[:40]}' (aceitos: "
            f"{', '.join(CRITERIOS_NATUREZA)})."
        )
    return criterio


def limites_dispensa(json_texto: str = "") -> dict[str, dict]:
    """Tabela de limites por exercício: a do código mais a da configuração (que prevalece).

    Formato da configuração: ``{"2027": {"inciso_i": 1.0, "inciso_ii": 1.0, "fonte": "..."}}``.
    """
    tabela = {ano: dict(valores) for ano, valores in LIMITES_DISPENSA.items()}
    texto = (json_texto or "").strip()
    if not texto:
        return tabela
    try:
        configurado = json.loads(texto)
    except ValueError as erro:
        raise ValueError("T5_LIMITES_DISPENSA não é JSON válido.") from erro
    if not isinstance(configurado, dict):
        raise ValueError("T5_LIMITES_DISPENSA deve ser um objeto JSON por exercício.")
    for ano, valores in configurado.items():
        if not re.fullmatch(r"\d{4}", str(ano)) or not isinstance(valores, dict):
            raise ValueError("T5_LIMITES_DISPENSA: cada chave é um ano (AAAA) com um objeto.")
        inciso_i = numero_finito(valores.get("inciso_i"))
        inciso_ii = numero_finito(valores.get("inciso_ii"))
        if inciso_i is None or inciso_ii is None or inciso_i <= 0 or inciso_ii <= 0:
            raise ValueError(
                f"T5_LIMITES_DISPENSA[{ano}]: 'inciso_i' e 'inciso_ii' devem ser números positivos."
            )
        tabela[str(ano)] = {
            "inciso_i": inciso_i,
            "inciso_ii": inciso_ii,
            "fonte": str(valores.get("fonte") or "Configuração do operador (T5_LIMITES_DISPENSA)")[
                :300
            ],
        }
    return tabela
