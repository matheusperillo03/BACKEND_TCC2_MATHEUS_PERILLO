"""Catálogo das tipologias para a interface: regra, base, fontes necessárias e configuração em
vigor. A severidade e a política de erro vêm da tabela única ``politicas.POLITICAS``."""

from __future__ import annotations

from buscador.tipologias import legislacao
from buscador.tipologias.motor import NOMES_PARAMETROS
from buscador.tipologias.politicas import POLITICAS

_LEI = f"Lei nº 14.133/2021 ({legislacao.URL_LEI_14133})"

# codigo -> (história, regra, base, fontes necessárias, parâmetros usados)
_DESCRICOES: dict[str, tuple[str, str, str, list[str], list[str]]] = {
    "T1": (
        "H04",
        "Empresa que iniciou a atividade menos de N dias antes de assinar o contrato.",
        "Levantamento com o especialista (RF04); limiar: decisão do autor.",
        ["cnpj_arquivo", "pncp"],
        ["t1_dias_minimos"],
    ),
    "T2": (
        "H04",
        "Endereço normalizado compartilhado por N ou mais empresas, com ao menos uma contratada.",
        "Levantamento com o especialista (RF04); limiar: decisão do autor.",
        ["cnpj_arquivo", "pncp"],
        ["t2_minimo_empresas"],
    ),
    "T3": (
        "H04",
        "Valor global do contrato igual ou maior que N vezes o capital social da contratada.",
        "Levantamento com o especialista (RF04); limiar: decisão do autor.",
        ["cnpj_arquivo", "pncp"],
        ["t3_razao_minima"],
    ),
    "T4": (
        "H31",
        "Estabelecimento contratado com situação cadastral diferente de ATIVA (código 02).",
        "Layout dos dados abertos do CNPJ (Receita Federal): 01 NULA, 2 ATIVA, 3 SUSPENSA, "
        "4 INAPTA, 08 BAIXADA.",
        ["cnpj_arquivo", "pncp"],
        [],
    ),
    "T5": (
        "H32",
        "Contratações da mesma unidade do órgão, no mesmo exercício, de objetos de mesma "
        "natureza (critério configurável), cada uma abaixo do limite de dispensa, cujo "
        "somatório atinge o limite.",
        f"{_LEI}, art. 75, I e II, e § 1º; valores de 2026: {legislacao.FONTE_LIMITES_2026}.",
        ["pncp"],
        ["t5_minimo_contratacoes", "t5_criterio_natureza", "t5_limites_dispensa"],
    ),
    "T6": (
        "H33",
        "Contratada com registro no CEIS ou no CNEP cujo período cobre a data de assinatura.",
        "Cadastros de sanções do Portal da Transparência (CGU): CEIS e CNEP.",
        ["pncp", "portal_transparencia (ceis, cnep)"],
        [],
    ),
    "T7": (
        "H34",
        "Sócio da contratada cujos seis dígitos visíveis do CPF e o nome normalizado são iguais "
        "aos de um servidor do órgão contratante. Possível coincidência, nunca identificação.",
        "Levantamento com o especialista (RF04); CPF descaracterizado no layout do CNPJ "
        "(Receita Federal); regra de sugestão sem fusão (RF02).",
        ["cnpj_arquivo", "pncp", "portal_transparencia (servidores)"],
        [],
    ),
    "T8": (
        "H35",
        "Soma do valor acrescido dos termos do contrato acima de N% do valor inicial (limite "
        "maior para reforma de edifício ou de equipamento, identificada pelo objeto).",
        f"{_LEI}, art. 125 (25% e 50%).",
        ["pncp", "pncp_termos"],
        ["t8_percentual_limite", "t8_percentual_limite_reforma"],
    ),
    "T9": (
        "H39",
        "Doador de campanha (empresa, ou pessoa física que possivelmente coincide com um sócio) "
        "cuja empresa assinou contrato, depois da doação, com órgão do ente do candidato eleito.",
        "Levantamento com o especialista (RF04); dados abertos de prestação de contas do TSE.",
        ["cnpj_arquivo", "pncp", "tse_arquivo"],
        ["t9_situacoes_eleito"],
    ),
    "T10": (
        "H36",
        "Texto do edital com expressão de cláusula potencialmente restritiva (lista explícita "
        "de expressões regulares, sem modelo de linguagem); cada alerta cita o trecho e a "
        "posição.",
        "Manual da Alice (CGU, 2025), seção 2.2: exigência de certidão de quitação ou de "
        "certidão negativa de quitação.",
        ["edital_arquivo"],
        ["t10_expressoes_extras"],
    ),
}

_DESCRICAO_PARAMETROS = {
    "t1_dias_minimos": "Dias mínimos entre o início da atividade e a assinatura do contrato.",
    "t2_minimo_empresas": "Número mínimo de empresas no mesmo endereço.",
    "t3_razao_minima": "Razão mínima entre o valor do contrato e o capital social.",
    "t5_minimo_contratacoes": "Número mínimo de contratações somadas.",
    "t5_criterio_natureza": "Critério de 'mesma natureza': "
    + "; ".join(f"{k} = {v}" for k, v in legislacao.CRITERIOS_NATUREZA.items())
    + ".",
    "t5_limites_dispensa": "Limites de dispensa por exercício (art. 75, I e II), com a fonte.",
    "t8_percentual_limite": "Limite de acréscimo em % do valor inicial (art. 125).",
    "t8_percentual_limite_reforma": "Limite de acréscimo em % para reforma de edifício ou de "
    "equipamento (art. 125).",
    "t9_situacoes_eleito": "Situações de totalização do TSE que contam como eleito.",
    "t10_expressoes_extras": "Expressões regulares além das do código "
    f"({'; '.join(r.expressao for r in legislacao.REGRAS_CLAUSULAS)}).",
}


def catalogo(parametros: dict) -> list[dict]:
    """Uma entrada por tipologia, em ordem numérica, com os valores de ``parametros`` em vigor."""
    itens = []
    for codigo in sorted(_DESCRICOES, key=lambda c: int(c[1:])):
        historia, regra, base, fontes, nomes = _DESCRICOES[codigo]
        politica = POLITICAS[codigo]
        itens.append(
            {
                "codigo": codigo,
                "nome": politica.nome,
                "historia": historia,
                "severidade": politica.severidade,
                "prefere_evitar": politica.prefere_evitar,
                "politica_erro": politica.politica_erro,
                "regra": regra,
                "base": base,
                "fontes": fontes,
                "parametros": [
                    {
                        "nome": nome,
                        "valor": parametros.get(nome),
                        "descricao": _DESCRICAO_PARAMETROS[nome],
                        "por_execucao": nome in NOMES_PARAMETROS,
                    }
                    for nome in nomes
                ],
            }
        )
    return itens
