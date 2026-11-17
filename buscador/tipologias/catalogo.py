"""Catálogo das tipologias para a interface: regra, base, fontes necessárias e configuração em
vigor. A severidade e a política de erro vêm da tabela única ``politicas.POLITICAS``."""

from __future__ import annotations

from buscador.tipologias import legislacao
from buscador.tipologias.motor import NOMES_PARAMETROS, chave_do_codigo
from buscador.tipologias.politicas import POLITICAS

_LEI = f"Lei nº 14.133/2021 ({legislacao.URL_LEI_14133})"
_GAFI_EGMONT = "FATF; Egmont Group (2018), Concealment of Beneficial Ownership, Anexo E"

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
    # ---- Tipologias de lavagem e ocultação (L). As histórias H40 a H49 são as propostas em
    # docs/lavagem/proposta.md (monografia), ainda não aprovadas no backlog.
    "L1": (
        "H40",
        "Endereço normalizado, com complemento, em que N ou mais empresas têm estabelecimento "
        "ATIVO, mesmo sem contrato público; marca se M ou mais iniciaram a atividade num "
        "intervalo de D dias.",
        _GAFI_EGMONT + ", itens 4 e 11; Velasco et al. (2021), seção 3.1. Limiares: decisão do "
        "autor.",
        ["cnpj_arquivo"],
        ["l1_minimo_estabelecimentos", "l1_janela_abertura_dias", "l1_minimo_abertas_na_janela"],
    ),
    "L3": (
        "H41",
        "Mesma pessoa provável (seis dígitos visíveis do CPF e nome normalizado) com "
        "qualificação de administração, ou como representante legal, em K ou mais empresas, "
        "das quais J ou mais abertas em M meses. Pessoa provável, nunca identificação.",
        _GAFI_EGMONT + ", itens 4 e 10; Carta Circular BCB nº 4.001/2020, III, h; Fronzetti "
        "Colladon e Remondi (2017); Lei nº 12.846/2013, art. 5º, III. Limiares: decisão do autor.",
        ["cnpj_arquivo"],
        [
            "l3_minimo_empresas",
            "l3_minimo_abertas_na_janela",
            "l3_janela_meses",
            "qualificacoes_administracao",
        ],
    ),
    "L4": (
        "H42",
        "Empresa sem pessoa física no primeiro nível do quadro e com N ou mais níveis de sócio "
        "pessoa jurídica acima dela; informa em que a cadeia termina.",
        _GAFI_EGMONT + ", item 8; FATF (2011), Laundering the Proceeds of Corruption, § 47; Lei "
        "nº 12.846/2013, art. 5º, III. Limiar: decisão do autor.",
        ["cnpj_arquivo"],
        ["l4_minimo_niveis"],
    ),
    "L5": (
        "H42",
        "Empresa do caso com sócio estrangeiro (identificador de sócio 3 no CNPJ). Só o nível 1: "
        "a lista de jurisdições (nível 2) não foi implementada.",
        _GAFI_EGMONT + ", item 4.",
        ["cnpj_arquivo"],
        [],
    ),
    "L6": (
        "H43",
        "Soma do valor dos contratos assinados no ano-calendário (valor inicial; sem ele, o "
        "global) acima do limite legal de receita do regime: MEI pela tabela Simples e pelas "
        "datas de opção e de exclusão; microempresa e pequeno porte pelo porte do cadastro.",
        "Velasco et al. (2021), seção 3.2; Carta Circular BCB nº 4.001/2020, III, n. Limites: "
        "LC nº 123/2006, arts. 3º e 18-A, e LC nº 238/2026, arts. 2º e 10 (MEI: R$ 81.000,00 até "
        "2026, R$ 110.000,00 em 2027 e R$ 140.000,00 a partir de 2028).",
        ["cnpj_arquivo (com a tabela Simples)", "pncp"],
        ["l6_limites", "l6_tolerancia_transicao"],
    ),
    "L7": (
        "H44",
        "Contratada em que nenhum sócio atual entrou até X dias depois do início da atividade e "
        "a entrada mais recente é anterior em até Y meses a um contrato (retrato único do "
        "quadro).",
        _GAFI_EGMONT + ", item 7. Limiares: decisão do autor.",
        ["cnpj_arquivo", "pncp"],
        ["l7_dias_apos_abertura", "l7_meses_antes_contrato"],
    ),
    "L8": (
        "H45",
        "Contratada que dispara a T1 (aberta pouco antes do contrato) E a T7 (sócio que "
        "possivelmente coincide com servidor do órgão contratante). Composição, sem coleta nova.",
        "COAF, Casos e Casos: Tipologias (edição Avaliação Nacional de Riscos 2021), tipologia "
        "2.7. O trecho 'sem empregados' não é verificável com dado aberto.",
        ["cnpj_arquivo", "pncp", "portal_transparencia (servidores)"],
        ["t1_dias_minimos"],
    ),
    "L10": (
        "H47",
        "Contratada sem sanção própria vigente que divide sócio-administrador provável ou "
        "endereço normalizado com empresa de sanção vigente na data do contrato (CEIS, CNEP) ou "
        "com entidade impedida (CEPIM); ou sócio de contratada ou convenente que possivelmente "
        "coincide com pessoa expulsa da administração federal (CEAF).",
        _GAFI_EGMONT + ", item 3; Velasco et al. (2021), seção 3; Lei nº 12.846/2013, art. 5º, "
        "IV, e.",
        ["cnpj_arquivo", "pncp", "portal_transparencia (ceis, cnep, cepim, ceaf)"],
        ["qualificacoes_administracao"],
    ),
    "L14": (
        "H49",
        "Sinal fraco: contratada cujos sócios-administradores estão TODOS em faixa etária "
        "atípica, com valor contratado acima de V. Só a faixa etária é usada.",
        _GAFI_EGMONT + ", itens 3 e 10. Limiares: decisão do autor.",
        ["cnpj_arquivo", "pncp"],
        ["l14_valor_minimo", "l14_faixas_atipicas", "qualificacoes_administracao"],
    ),
    "L15": (
        "H49",
        "Sinal fraco: contratada com N ou mais CNAEs (principal e secundárias) ou com contratos "
        "em M ou mais categorias de objeto.",
        "Velasco et al. (2021), seção 3.2; " + _GAFI_EGMONT + ", item 4. Limiares: decisão do "
        "autor.",
        ["cnpj_arquivo", "pncp"],
        ["l15_minimo_cnaes", "l15_minimo_categorias"],
    ),
    "L16": (
        "H57",
        "Convenente de natureza jurídica sem fins lucrativos com ao menos um sinal: (a) aberta "
        "menos de N dias antes do início da vigência; (b) registro no CEPIM; (c) dirigente que "
        "possivelmente coincide com servidor do órgão concedente; (d) dirigente que também é "
        "sócio de empresa no mesmo endereço, do mesmo CNAE principal ou recém-aberta.",
        "COAF, Casos e Casos: Tipologias (edição Avaliação Nacional de Riscos 2021), tipologias "
        "2.1, 2.8 e 2.9; Velasco et al. (2021), seção 5; FATF (2011), § 47. Limiar: decisão do "
        "autor.",
        ["cnpj_arquivo", "portal_transparencia (convenios, cepim, servidores)"],
        ["l16_dias_minimos", "l16_naturezas_sem_fins_lucrativos"],
    ),
    "COMB": (
        "H48",
        "Empresa ou pessoa com alertas ativos e não descartados de K ou mais tipologias "
        "distintas e soma de pesos igual ou maior que P. Tipologias do mesmo fato contam uma "
        "vez (L1 com T2) e composições não somam de novo (L8 é T1 com T7).",
        "Velasco et al. (2021), seção 3.3 ('the risk of fraud increases when combinations of "
        "these risk patterns are identified'). Pesos e mínimos: decisão do autor.",
        ["alertas das demais tipologias"],
        ["comb_minimo_tipologias", "comb_peso_minimo", "comb_pesos"],
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
    "l1_minimo_estabelecimentos": "Número mínimo de empresas com estabelecimento ativo no mesmo "
    "endereço (com complemento).",
    "l1_janela_abertura_dias": "Intervalo, em dias, para medir a concentração das aberturas.",
    "l1_minimo_abertas_na_janela": "Aberturas no intervalo para marcar abertura concentrada.",
    "l3_minimo_empresas": "Número mínimo de empresas administradas pela mesma pessoa provável.",
    "l3_minimo_abertas_na_janela": "Quantas dessas empresas precisam ter sido abertas no "
    "intervalo.",
    "l3_janela_meses": "Intervalo, em meses, entre as aberturas.",
    "l4_minimo_niveis": "Número mínimo de níveis de sócio pessoa jurídica.",
    "l7_dias_apos_abertura": "Dias depois do início da atividade em que a entrada ainda conta "
    "como de fundador.",
    "l7_meses_antes_contrato": "Meses entre a entrada mais recente no quadro e o contrato.",
    "l14_valor_minimo": "Valor contratado mínimo, em reais, somado por empresa.",
    "l14_faixas_atipicas": "Códigos de faixa etária tratados como atípicos (1 = até 12 anos, "
    "2 = 13 a 20, 9 = mais de 80; metadados do CNPJ, nota 4).",
    "l15_minimo_cnaes": "Número mínimo de CNAEs (principal e secundárias).",
    "l15_minimo_categorias": "Número mínimo de categorias de objeto contratadas.",
    "l6_limites": "Limites de receita bruta por regime, com a fonte: MEI por ano-calendário "
    "(anual e mensal no ano de início), microempresa e empresa de pequeno porte.",
    "l6_tolerancia_transicao": "Compara o valor do ano com o maior entre o limite do MEI do ano "
    "e o do ano seguinte (transição de limite).",
    "l16_dias_minimos": "Dias mínimos entre o início da atividade e o início da vigência do "
    "convênio.",
    "l16_naturezas_sem_fins_lucrativos": "Naturezas jurídicas tratadas como sem fins lucrativos "
    "(3069 Fundação Privada, 3301 Organização Social, 3999 Associação Privada).",
    "comb_minimo_tipologias": "Número mínimo de tipologias distintas na mesma entidade.",
    "comb_peso_minimo": "Soma mínima dos pesos das tipologias contadas.",
    "comb_pesos": "Peso de cada tipologia na combinação (sinais fracos pesam menos; composição "
    "pesa zero).",
    "qualificacoes_administracao": "Códigos de qualificação de sócio que contam como "
    "administração (tabela de domínio Qualificações do CNPJ).",
    "t10_expressoes_extras": "Expressões regulares além das do código "
    f"({'; '.join(r.expressao for r in legislacao.REGRAS_CLAUSULAS)}).",
}


def catalogo(parametros: dict) -> list[dict]:
    """Uma entrada por tipologia (T1 a T10 e depois as L), com os valores de ``parametros`` em
    vigor."""
    itens = []
    for codigo in sorted(_DESCRICOES, key=chave_do_codigo):
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
                "grupo": politica.grupo,
                "sinal_fraco": politica.sinal_fraco,
                "peso": float((parametros.get("comb_pesos") or {}).get(codigo, 0.0)),
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
