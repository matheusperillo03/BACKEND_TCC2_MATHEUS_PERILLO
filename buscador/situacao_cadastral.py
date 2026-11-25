"""Situação cadastral do estabelecimento e o critério único de "conta no endereço".

Códigos do campo "SITUAÇÃO CADASTRAL" da tabela Estabelecimentos no layout oficial dos dados
abertos do CNPJ: "01 – NULA, 2 – ATIVA, 3 – SUSPENSA, 4 – INAPTA, 08 – BAIXADA" (metadados do
CNPJ, Receita Federal, https://www.gov.br/receitafederal/dados/cnpj-metadados.pdf, conferido em
16/11/2026). O arquivo traz o código com ou sem zero à esquerda, por isso a comparação é numérica.

Critério de endereço (decisão do autor), usado pela T2, pela L1, pela aresta ``mesmo_endereco``
e pelo sinal (d) da L16: só o estabelecimento ATIVO (código 02) conta num endereço. Código
reconhecível e diferente de 02 (nula, suspensa, inapta, baixada) fica de fora. Sem código
informado não se sabe se o estabelecimento deixou de existir: ele continua contando (prefere-se
evitar o falso negativo), e quem usa o critério avisa que a situação não foi informada.
"""

from __future__ import annotations

SITUACOES_CADASTRAIS = {1: "NULA", 2: "ATIVA", 3: "SUSPENSA", 4: "INAPTA", 8: "BAIXADA"}
SITUACAO_ATIVA = 2


def codigo_situacao(valor: object) -> int | None:
    texto = str(valor or "").strip()
    return int(texto) if texto.isdigit() else None


def situacao_nao_informada(estabelecimento: dict) -> bool:
    return codigo_situacao(estabelecimento.get("situacao_cadastral")) is None


def conta_no_endereco(estabelecimento: dict) -> bool:
    """O estabelecimento conta no endereço: está ATIVO ou veio sem situação informada."""
    codigo = codigo_situacao(estabelecimento.get("situacao_cadastral"))
    return codigo is None or codigo == SITUACAO_ATIVA


def rotulo_situacao(estabelecimento: dict) -> str:
    """Texto da evidência de um estabelecimento que passou por ``conta_no_endereco``."""
    return "situação não informada" if situacao_nao_informada(estabelecimento) else "situação ATIVA"


def enderecos_em_uso(atributos: dict | None) -> dict[str, list[dict]]:
    """Endereço normalizado -> estabelecimentos da empresa que contam nele. Chave vazia (endereço
    em branco ou só com a cidade) não identifica um lugar e fica de fora."""
    enderecos: dict[str, list[dict]] = {}
    for est in (atributos or {}).get("estabelecimentos") or []:
        chave = est.get("endereco_normalizado")
        if chave and conta_no_endereco(est):
            enderecos.setdefault(chave, []).append(est)
    return enderecos
