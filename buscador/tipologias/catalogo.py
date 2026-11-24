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
        "global) acima do limite legal de receita do regime naquele ano: MEI pela tabela "
        "Simples e pelas datas de opção e de exclusão; microempresa e pequeno porte pelo porte "
        "do cadastro. No ano de início de atividade vale o limite proporcional aos meses, "
        "contada a fração de mês como mês inteiro. Qualquer excesso dispara; o alerta diz se o "
        "excesso passa de 20% do limite, o que muda a data dos efeitos previstos na lei. "
        "Contratos anteriores a 2012 não são avaliados (limites não conferidos).",
        "Velasco et al. (2021), seção 3.2; Carta Circular BCB nº 4.001/2020, III, n. Limites: "
        "LC nº 123/2006, art. 3º, I e II, e art. 18-A, § 1º; LC nº 238/2026, arts. 2º e 10 (MEI: "
        "R$ 81.000,00 de 2018 a 2026, R$ 110.000,00 em 2027 e R$ 140.000,00 a partir de 2028). "
        "Ano de início: art. 3º, § 2º, e art. 18-A, § 2º. Efeitos do excesso: art. 3º, §§ 7º, "
        "9º, 9º-A, 10 e 12, e art. 18-A, § 7º, III e IV.",
        ["cnpj_arquivo (com a tabela Simples)", "pncp"],
        ["l6_limites"],
    ),
    "L7": (
        "H44",
        "Contratada em que nenhum sócio atual entrou até X dias depois do início da atividade e "
        "a entrada mais recente é anterior em até Y meses a um contrato (retrato único do "
        "quadro). Só contam as qualificações de sócio ou titular; não avalia administração "
        "pública (grupo 1xxx), empresa pública (2011) nem sociedade de economia mista (2038).",
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
    "L9": (
        "H46",
        "Candidato eleito ligado a um sócio por sugestão de resolução não descartada (mesmos "
        "seis dígitos visíveis do CPF e mesmo nome normalizado; pendente ou confirmada pelo "
        "investigador, sem fusão), cuja empresa assinou contrato, durante o mandato, com órgão "
        "do mesmo ente. O mandato é contado de 1º de janeiro do ano seguinte ao da eleição, por "
        "N anos. Não avalia PEP (não há coleta da rota /peps do Portal da Transparência) nem "
        "eleito sem CPF divulgado.",
        "FATF (2011), Laundering the Proceeds of Corruption, § 44 (self-dealing); Velasco et al. "
        "(2021), seção 3.3. Ligação por sugestão, duração e início do mandato e critério "
        "territorial do ente: decisão do autor (23/11/2026).",
        ["cnpj_arquivo", "pncp", "tse_arquivo (candidatos)"],
        ["l9_anos_mandato", "t9_situacoes_eleito"],
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
    "L2": (
        "H49",
        "Sinal fraco, não gera alerta próprio: só pesa na combinação. O mesmo telefone (DDD e "
        "número) ou o mesmo correio eletrônico no cadastro de K ou mais empresas do caso que "
        "formam K ou mais grupos sem sócio em comum. O contato é comparado normalizado e "
        "mostrado mascarado; o valor completo fica só na captura.",
        "Carta Circular BCB nº 4.001/2020, III, k (mesmo endereço de e-mail por diferentes "
        "pessoas jurídicas); " + _GAFI_EGMONT + ", item 4. Estender ao telefone do cadastro, o "
        "agrupamento por sócio e o limiar: decisão do autor.",
        ["cnpj_arquivo"],
        ["l2_minimo_empresas"],
    ),
    "L13": (
        "H52",
        "Despesas contratadas de campanha de um candidato com uma empresa que (a) iniciou a "
        "atividade menos de N dias antes da despesa ou (b) tem CNAE fora da lista de atividades "
        "típicas de campanha (CNAE principal do cadastro do CNPJ; sem cadastro, o informado na "
        "prestação de contas). Com a lista vazia, o critério (b) fica desligado. Um alerta por "
        "candidato e fornecedor, com as despesas somadas. Não avalia os sinais de fachada do "
        "fornecedor (L1 e L6) nem fornecedor pessoa física.",
        "Carta Circular BCB nº 4.001/2020, XIV, d. Limiar de dias e lista de atividades: decisão "
        "do autor (a lista ainda não foi definida).",
        ["tse_arquivo (despesas contratadas)", "cnpj_arquivo"],
        ["l13_dias_abertura", "l13_cnaes_campanha"],
    ),
    "L14": (
        "H49",
        "Sinal fraco, não gera alerta próprio: só pesa na combinação. Contratada cujos "
        "sócios-administradores estão TODOS em faixa etária atípica, com valor contratado acima "
        "de V. Só a faixa etária é usada.",
        _GAFI_EGMONT + ", itens 3 e 10. Limiares: decisão do autor.",
        ["cnpj_arquivo", "pncp"],
        ["l14_valor_minimo", "l14_faixas_atipicas", "qualificacoes_administracao"],
    ),
    "L15": (
        "H49",
        "Sinal fraco, não gera alerta próprio: só pesa na combinação. Contratada com N ou mais "
        "CNAEs (principal e secundárias) ou com contratos em M ou mais categorias de objeto.",
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
    "l2_minimo_empresas": "Número mínimo de empresas, em grupos sem sócio em comum, com o mesmo "
    "telefone ou correio eletrônico.",
    "l3_minimo_empresas": "Número mínimo de empresas administradas pela mesma pessoa provável.",
    "l3_minimo_abertas_na_janela": "Quantas dessas empresas precisam ter sido abertas no "
    "intervalo.",
    "l3_janela_meses": "Intervalo, em meses, entre as aberturas.",
    "l4_minimo_niveis": "Número mínimo de níveis de sócio pessoa jurídica.",
    "l7_dias_apos_abertura": "Dias depois do início da atividade em que a entrada ainda conta "
    "como de fundador.",
    "l7_meses_antes_contrato": "Meses entre a entrada mais recente no quadro e o contrato.",
    "l9_anos_mandato": "Duração do mandato, em anos, a partir de 1º de janeiro do ano seguinte "
    "ao da eleição (o mandato de senador, de oito anos, não é tratado à parte).",
    "l13_dias_abertura": "Dias mínimos entre o início da atividade do fornecedor e a despesa de "
    "campanha.",
    "l13_cnaes_campanha": "CNAEs tratados como atividade típica de campanha (código de 7 dígitos "
    "ou prefixo). Lista vazia = critério do CNAE desligado.",
    "l14_valor_minimo": "Valor contratado mínimo, em reais, somado por empresa.",
    "l14_faixas_atipicas": "Códigos de faixa etária tratados como atípicos (1 = até 12 anos, "
    "2 = 13 a 20, 9 = mais de 80; metadados do CNPJ, nota 4).",
    "l15_minimo_cnaes": "Número mínimo de CNAEs (principal e secundárias).",
    "l15_minimo_categorias": "Número mínimo de categorias de objeto contratadas.",
    "l6_limites": "Limites de receita bruta por regime, com a fonte: MEI por ano-calendário "
    "(anual e mensal no ano de início), microempresa e empresa de pequeno porte (linha atual "
    "em 'me' e 'epp'; por ano em 'me_por_ano' e 'epp_por_ano'). Cada linha vale do seu ano até "
    "a linha seguinte.",
    "l16_dias_minimos": "Dias mínimos entre o início da atividade e o início da vigência do "
    "convênio.",
    "l16_naturezas_sem_fins_lucrativos": "Naturezas jurídicas tratadas como sem fins lucrativos "
    "(3069 Fundação Privada, 3301 Organização Social, 3999 Associação Privada).",
    "comb_minimo_tipologias": "Número mínimo de tipologias distintas na mesma entidade.",
    "comb_peso_minimo": "Soma mínima dos pesos das tipologias contadas.",
    "comb_pesos": "Peso de cada tipologia na combinação (sinais fracos pesam menos e não têm "
    "alerta próprio; composição pesa zero).",
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
