"""Parâmetros legais e listas de termos das tipologias do horizonte 2, cada um com a fonte oficial.

Tudo aqui foi conferido em fonte oficial em 16/11/2026 (ver docs/sistema/fontes-horizonte2.md, na
monografia). O que não pôde ser conferido está marcado como PENDENTE e é configurável.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date

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


# ---- L6: limites de receita bruta por regime (Lei Complementar nº 123/2006) --------------------
#
# Conferidos no texto do Planalto em 18/11/2026 (LC nº 123/2006 compilada e as leis que a
# alteraram: LC nº 139/2011, LC nº 155/2016 e LC nº 238/2026).
#
# Valores e vigência:
# - microempresa, art. 3º, I: receita bruta, "em cada ano-calendário", "igual ou inferior a
#   R$ 360.000,00" (LC nº 139/2011, art. 2º; efeitos a partir de 1º/1/2012, art. 7º);
# - empresa de pequeno porte, art. 3º, II: "igual ou inferior a R$ 3.600.000,00" de 2012 a 2017
#   (LC nº 139/2011) e "igual ou inferior a R$ 4.800.000,00" a partir de 1º/1/2018 (LC nº
#   155/2016, art. 11, III);
# - MEI, art. 18-A, § 1º: "receita bruta, no ano-calendário anterior, de até" R$ 60.000,00 de 2012
#   a 2017 (LC nº 139/2011) e R$ 81.000,00 a partir de 1º/1/2018 (LC nº 155/2016; redação atual da
#   LC nº 188/2021, mesmo valor);
# - LC nº 238, de 21/10/2026: art. 2º, "Para o ano-calendário de 2027, o limite [...] será de
#   R$ 110.000,00"; nova redação do art. 18-A, § 1º, R$ 140.000,00, com efeitos "a partir de
#   1º de janeiro de 2028" (art. 10, I). Em 2026 continua valendo R$ 81.000,00.
#
# Ano de início de atividade (limite proporcional):
# - ME e EPP, art. 3º, § 2º: "o limite a que se refere o caput deste artigo será proporcional ao
#   número de meses em que a microempresa ou a empresa de pequeno porte houver exercido atividade,
#   inclusive as frações de meses";
# - MEI, art. 18-A, § 2º: "o limite de que trata o § 1º será de R$ 6.750,00 [...] multiplicados
#   pelo número de meses compreendido entre o início da atividade e o final do respectivo
#   ano-calendário, consideradas as frações de meses como um mês inteiro" (R$ 5.000,00 de 2012 a
#   2017; R$ 9.166,67 em 2027, LC nº 238/2026, art. 2º, parágrafo único; R$ 11.666,67 a partir
#   de 2028).
#
# Efeitos do excesso (a lei não dá tolerância: todo excesso tira a empresa do regime; os 20% só
# mudam a data em que a saída produz efeitos):
# - MEI, art. 18-A, § 7º, III: excesso do limite do § 1º produz efeitos "a) a partir de 1º de
#   janeiro do ano-calendário subseqüente [...], na hipótese de não ter ultrapassado o referido
#   limite em mais de 20%"; "b) retroativamente a 1º de janeiro do ano-calendário da ocorrência
#   do excesso, na hipótese de ter ultrapassado o referido limite em mais de 20%". Inciso IV, o
#   mesmo para o limite proporcional do § 2º, com a alínea b "retroativamente ao início de
#   atividade";
# - EPP, art. 3º, § 9º: "fica excluída, no mês subsequente à ocorrência do excesso"; § 9º-A: "Os
#   efeitos da exclusão prevista no § 9º dar-se-ão no ano-calendário subsequente se o excesso
#   verificado em relação à receita bruta não for superior a 20% (vinte por cento) do limite";
#   § 10: no ano de início, quem passar do limite proporcional é excluído "com efeitos
#   retroativos ao início de suas atividades"; § 12: essa exclusão "não retroagirá" se o excesso
#   "não for superior a 20%", e os efeitos ficam para o ano-calendário subsequente;
# - ME, art. 3º, § 7º: "a microempresa que, no ano-calendário, exceder o limite de receita bruta
#   anual previsto no inciso I do caput deste artigo passa, no ano-calendário seguinte, à
#   condição de empresa de pequeno porte" (sem faixa de 20%).
#
# As tabelas são por ano-calendário: cada linha vale do ano da chave até a linha seguinte, e a
# última vale dali em diante. Anos anteriores a 2012 NÃO foram conferidos (o texto compilado é a
# republicação da LC nº 139/2011): a L6 não avalia esses anos. Outros anos do MEI entram pela
# configuração (L6_LIMITES_MEI).
URL_LC_123 = "https://www.planalto.gov.br/ccivil_03/leis/lcp/lcp123.htm"
URL_LC_139 = "https://www.planalto.gov.br/ccivil_03/leis/lcp/lcp139.htm"
URL_LC_155 = "https://www.planalto.gov.br/ccivil_03/leis/lcp/lcp155.htm"
URL_LC_238 = "https://www.planalto.gov.br/ccivil_03/leis/lcp/lcp238.htm"
_EFEITOS_139 = "LC nº 139/2011, com efeitos a partir de 1º/1/2012, art. 7º"
_EFEITOS_155 = "LC nº 155/2016, com efeitos a partir de 1º/1/2018, art. 11, III"
LIMITES_MEI: dict[str, dict] = {
    "2012": {
        "anual": 60_000.00,
        "mensal_inicio": 5_000.00,
        "fonte": f"LC nº 123/2006, art. 18-A, §§ 1º e 2º (redação da {_EFEITOS_139}); {URL_LC_139}",
    },
    "2018": {
        "anual": 81_000.00,
        "mensal_inicio": 6_750.00,
        "fonte": f"LC nº 123/2006, art. 18-A, §§ 1º e 2º (valores da {_EFEITOS_155}); {URL_LC_123}",
    },
    "2027": {
        "anual": 110_000.00,
        "mensal_inicio": 9_166.67,
        "fonte": "LC nº 238/2026, art. 2º, caput e parágrafo único (efeitos a partir de "
        f"1º/1/2027, art. 10, II); {URL_LC_238}",
    },
    "2028": {
        "anual": 140_000.00,
        "mensal_inicio": 11_666.67,
        "fonte": "LC nº 123/2006, art. 18-A, §§ 1º e 2º (redação da LC nº 238/2026, com efeitos "
        f"a partir de 1º/1/2028, art. 10, I); {URL_LC_238}",
    },
}
LIMITES_ME: dict[str, dict] = {
    "2012": {
        "anual": 360_000.00,
        "fonte": f"LC nº 123/2006, art. 3º, I (redação da {_EFEITOS_139}); {URL_LC_123}",
    },
}
LIMITES_EPP: dict[str, dict] = {
    "2012": {
        "anual": 3_600_000.00,
        "fonte": f"LC nº 123/2006, art. 3º, II (redação da {_EFEITOS_139}); {URL_LC_139}",
    },
    "2018": {
        "anual": 4_800_000.00,
        "fonte": f"LC nº 123/2006, art. 3º, II (redação da {_EFEITOS_155}); {URL_LC_123}",
    },
}
# Primeiro ano-calendário com limites conferidos; antes dele a L6 não avalia.
PRIMEIRO_ANO_CONFERIDO = 2012
# Excesso de até 20% do limite: muda só a data dos efeitos (arts. 3º, §§ 9º-A e 12, e 18-A, § 7º).
FAIXA_EXCESSO = 0.20
# Porte da empresa no layout do CNPJ (metadados): 01 microempresa, 03 empresa de pequeno porte,
# 05 demais, 00 não informado.
PORTE_ME, PORTE_EPP = "01", "03"


def limites_de_receita(json_mei: str = "") -> dict:
    """Limites de receita por regime: as tabelas do código mais a configuração (que prevalece).

    Formato de ``L6_LIMITES_MEI``: ``{"2029": {"anual": 1.0, "mensal_inicio": 1.0, "fonte": ""}}``.
    ``mei``, ``me_por_ano`` e ``epp_por_ano`` são tabelas por ano-calendário (cada linha vale do
    ano da chave até a linha seguinte); ``me`` e ``epp`` repetem a linha mais recente.
    """
    mei = {ano: dict(valores) for ano, valores in LIMITES_MEI.items()}
    texto = (json_mei or "").strip()
    if texto:
        try:
            configurado = json.loads(texto)
        except ValueError as erro:
            raise ValueError("L6_LIMITES_MEI não é JSON válido.") from erro
        if not isinstance(configurado, dict):
            raise ValueError("L6_LIMITES_MEI deve ser um objeto JSON por ano-calendário.")
        for ano, valores in configurado.items():
            if not re.fullmatch(r"\d{4}", str(ano)) or not isinstance(valores, dict):
                raise ValueError("L6_LIMITES_MEI: cada chave é um ano (AAAA) com um objeto.")
            anual = numero_finito(valores.get("anual"))
            mensal = numero_finito(valores.get("mensal_inicio"))
            if anual is None or mensal is None or anual <= 0 or mensal <= 0:
                raise ValueError(
                    f"L6_LIMITES_MEI[{ano}]: 'anual' e 'mensal_inicio' devem ser números positivos."
                )
            mei[str(ano)] = {
                "anual": anual,
                "mensal_inicio": mensal,
                "fonte": str(valores.get("fonte") or "Configuração do operador (L6_LIMITES_MEI)")[
                    :300
                ],
            }
    me = {ano: dict(valores) for ano, valores in LIMITES_ME.items()}
    epp = {ano: dict(valores) for ano, valores in LIMITES_EPP.items()}
    return {
        "mei": dict(sorted(mei.items())),
        "me": dict(me[max(me)]),
        "epp": dict(epp[max(epp)]),
        "me_por_ano": me,
        "epp_por_ano": epp,
        "primeiro_ano": min(int(a) for tabela in (mei, me, epp) for a in tabela),
    }


def _linha_do_ano(tabela: dict[str, dict], ano: int) -> tuple[str, dict] | None:
    """A linha que vale no ano-calendário; ``None`` antes do primeiro ano da tabela."""
    vigentes = [a for a in sorted(tabela) if int(a) <= ano]
    return (vigentes[-1], tabela[vigentes[-1]]) if vigentes else None


def limite_mei_do_ano(limites: dict, ano: int) -> tuple[str, dict] | None:
    """A linha da tabela do MEI que vale para o ano-calendário (e o ano dessa linha)."""
    return _linha_do_ano(limites["mei"], ano)


def meses_no_ano_de_inicio(inicio: date | None, ano: int) -> int | None:
    """Meses de atividade no ano de início, contada a fração de mês como mês inteiro (LC nº
    123/2006, art. 3º, § 2º, e art. 18-A, § 2º). ``None`` fora do ano de início."""
    if inicio is None or inicio.year != ano:
        return None
    return 13 - inicio.month


def limite_de_receita(limites: dict, regime: str, ano: int, inicio: date | None) -> dict | None:
    """Limite legal de receita bruta do regime (``mei``, ``me`` ou ``epp``) no ano-calendário.

    No ano de início de atividade o limite é proporcional aos meses: MEI, valor mensal da lei
    vezes os meses (art. 18-A, § 2º); ME e EPP, limite anual vezes meses sobre 12 (art. 3º, § 2º).
    ``None`` quando o ano é anterior aos limites conferidos.
    """
    tabela = limites["mei"] if regime == "mei" else limites[f"{regime}_por_ano"]
    achado = _linha_do_ano(tabela, ano)
    if achado is None:
        return None
    _, linha = achado
    meses = meses_no_ano_de_inicio(inicio, ano)
    limite, dispositivo = linha["anual"], None
    if meses is not None and regime == "mei":
        limite = round(linha["mensal_inicio"] * meses, 2)
        dispositivo = "LC nº 123/2006, art. 18-A, § 2º"
    elif meses is not None:
        limite = round(linha["anual"] * meses / 12, 2)
        dispositivo = "LC nº 123/2006, art. 3º, § 2º"
    return {
        "limite_anual": linha["anual"],
        "limite_legal": limite,
        "mensal_inicio": linha.get("mensal_inicio"),
        "meses": meses,
        "fonte": linha["fonte"],
        "dispositivo_proporcional": dispositivo,
    }


def efeito_do_excesso(regime: str, soma: float, limite: float, ano_de_inicio: bool) -> dict:
    """O que a lei prevê para quem passa do limite (dispositivo e efeito), pela faixa de 20%."""
    ate_20 = round(soma, 2) <= round(limite * (1 + FAIXA_EXCESSO), 2)
    if regime == "me":
        return {
            "faixa": None,
            "dispositivo": "LC nº 123/2006, art. 3º, § 7º",
            "efeito": "a microempresa que excede o limite do inciso I passa, no ano-calendário "
            "seguinte, à condição de empresa de pequeno porte",
        }
    faixa = "ate_20" if ate_20 else "acima_de_20"
    descricao = (
        "o excesso não passa de 20% do limite" if ate_20 else "o excesso passa de 20% do limite"
    )
    if regime == "mei":
        inciso = "IV" if ano_de_inicio else "III"
        if ate_20:
            alinea, efeito = "a", "a partir de 1º de janeiro do ano-calendário seguinte"
        elif ano_de_inicio:
            alinea, efeito = "b", "retroativamente ao início de atividade"
        else:
            alinea, efeito = "b", "retroativamente a 1º de janeiro do ano-calendário do excesso"
        return {
            "faixa": faixa,
            "dispositivo": f"LC nº 123/2006, art. 18-A, § 7º, {inciso}, {alinea}",
            "efeito": f"{descricao}, e o desenquadramento do MEI produz efeitos {efeito}",
        }
    if ano_de_inicio:
        dispositivo, efeito = (
            ("art. 3º, §§ 10 e 12", "no ano-calendário seguinte, sem retroagir")
            if ate_20
            else ("art. 3º, § 10", "retroativos ao início das atividades")
        )
    else:
        dispositivo, efeito = (
            ("art. 3º, §§ 9º e 9º-A", "no ano-calendário seguinte")
            if ate_20
            else ("art. 3º, § 9º", "no mês seguinte ao do excesso")
        )
    return {
        "faixa": faixa,
        "dispositivo": f"LC nº 123/2006, {dispositivo}",
        "efeito": f"{descricao}, e a exclusão do tratamento diferenciado produz efeitos {efeito}",
    }
