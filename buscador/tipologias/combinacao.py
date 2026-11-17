"""Alerta de combinação (código ``COMB``): a mesma empresa ou pessoa com vários alertas distintos.

Base: Velasco et al. (2021), A decision support system for fraud detection in public procurement,
International Transactions in Operational Research, v. 28, n. 1, seção 3.3 (p. 36): "many patterns
do not represent by themselves individually a significant risk of the existence of fraud or
corruption. However, the risk of fraud increases when combinations of these risk patterns are
identified". O GAFI também diz que a sua lista de indicadores "is not exhaustive".

NÃO É ESCORE DE CULPA: é uma pista para priorizar a análise (RF12, RF14). Os pesos, os mínimos e
as regras de contagem abaixo são DECISÃO DO AUTOR, até haver dados para calibrar:

- só entram alertas ativos e não descartados pelo investigador, das tipologias T e L;
- cada tipologia conta uma vez por entidade, com o seu peso (padrão 1; sinais fracos, 0,5);
- tipologias que descrevem o mesmo fato contam uma vez (``FAMILIAS``: a L1 estende a T2);
- uma composição não soma de novo o que já foi contado (``COMPOSICOES``: a L8 é T1 com T7), por
  isso tem peso zero, mas aparece na lista;
- dispara com ao menos ``comb_minimo_tipologias`` tipologias distintas E soma de pesos de ao
  menos ``comb_peso_minimo``. Com os padrões (2 e 2,0), dois sinais fracos sozinhos não disparam.

Diferente das demais, esta regra lê os ALERTAS do caso, e não as entidades: roda depois das
outras, na mesma execução. Executar só uma tipologia não recalcula a combinação; decidir um
alerta também não (vale na próxima execução).
"""

from __future__ import annotations

import json

from buscador.modelos import Alerta, Entidade
from buscador.seguranca import numero_finito
from buscador.tipologias import politicas
from buscador.tipologias.motor import AlertaDetectado, Evidencia, formatar_numero

CODIGO = "COMB"
PESO_PADRAO = 1.0
PESO_SINAL_FRACO = 0.5
# Tipologias que descrevem o mesmo fato: contam como uma só na combinação.
FAMILIAS = {"L1": "T2"}
# Composições: o alerta existe porque os componentes existem; não soma de novo.
COMPOSICOES = {"L8": ("T1", "T7")}
TIPOS_DE_ENTIDADE = ("empresa", "pessoa")
MAX_EVIDENCIAS_POR_COMPONENTE = 3
MAX_EVIDENCIAS = 30
AVISO = (
    "Não é escore de culpa: é uma pista para priorizar a análise (RF12). Cada alerta da lista "
    "continua valendo por si, com as suas evidências e as suas limitações."
)


def pesos_padrao() -> dict[str, float]:
    """Peso de cada tipologia na combinação (decisão do autor)."""
    pesos = {}
    for codigo, politica in politicas.POLITICAS.items():
        if codigo == CODIGO:
            continue
        if codigo in COMPOSICOES:
            pesos[codigo] = 0.0
        else:
            pesos[codigo] = PESO_SINAL_FRACO if politica.sinal_fraco else PESO_PADRAO
    return pesos


def pesos(json_texto: str = "") -> dict[str, float]:
    """Pesos do código mais os da configuração (``COMB_PESOS``, JSON ``{"T1": 1.0}``)."""
    tabela = pesos_padrao()
    texto = (json_texto or "").strip()
    if not texto:
        return tabela
    try:
        configurado = json.loads(texto)
    except ValueError as erro:
        raise ValueError("COMB_PESOS não é JSON válido.") from erro
    if not isinstance(configurado, dict):
        raise ValueError("COMB_PESOS deve ser um objeto JSON de tipologia para peso.")
    for codigo, valor in configurado.items():
        peso = numero_finito(valor)
        if codigo not in tabela:
            raise ValueError(f"COMB_PESOS: tipologia desconhecida '{str(codigo)[:20]}'.")
        if peso is None or not 0 <= peso <= 10:
            raise ValueError(f"COMB_PESOS[{codigo}] deve ser um número de 0 a 10.")
        tabela[codigo] = peso
    return tabela


def parametros_padrao() -> dict:
    return {"comb_minimo_tipologias": 2, "comb_peso_minimo": 2.0, "comb_pesos": pesos_padrao()}


def detectar(
    alertas: list[Alerta], entidades: dict[str, Entidade], parametros: dict
) -> list[AlertaDetectado]:
    """Um alerta de combinação por empresa ou pessoa que reúne tipologias e peso suficientes."""
    minimo = int(parametros["comb_minimo_tipologias"])
    peso_minimo = float(parametros["comb_peso_minimo"])
    tabela: dict = parametros["comb_pesos"]
    por_entidade: dict[str, list[Alerta]] = {}
    for alerta in alertas:
        if alerta.tipologia == CODIGO or alerta.ativo is False or alerta.situacao == "descartado":
            continue
        for entidade_id in dict.fromkeys(alerta.entidades or []):
            entidade = entidades.get(entidade_id)
            if entidade is not None and entidade.tipo in TIPOS_DE_ENTIDADE:
                por_entidade.setdefault(entidade_id, []).append(alerta)

    detectados = []
    for entidade_id in sorted(por_entidade):
        entidade = entidades[entidade_id]
        do_alvo = sorted(
            por_entidade[entidade_id],
            key=lambda a: (
                politicas.chave_ordenacao(a.severidade, a.tipologia, a.titulo),
                a.id or "",
            ),
        )
        presentes = {a.tipologia for a in do_alvo}
        contadas: dict[str, float] = {}
        contadas_por: dict[str, str] = {}
        componentes = []
        for alerta in do_alvo:
            codigo = alerta.tipologia
            politica = politicas.politica(codigo)
            peso = float(tabela.get(codigo, PESO_PADRAO))
            familia = FAMILIAS.get(codigo, codigo)
            conta, observacao = True, ""
            if codigo in COMPOSICOES:
                partes = COMPOSICOES[codigo]
                conta = False
                observacao = (
                    f"Composição de {' e '.join(partes)}, já contadas separadamente."
                    if set(partes) <= presentes
                    else f"Composição de {' e '.join(partes)}: não soma de novo."
                )
            elif peso <= 0:
                conta, observacao = False, "Peso zero na configuração."
            elif familia in contadas:
                conta = False
                observacao = (
                    "Mesma tipologia já contada para esta entidade."
                    if contadas_por[familia] == codigo
                    else f"Mesmo fato da {contadas_por[familia]}, contado uma vez."
                )
            if conta:
                contadas[familia] = peso
                contadas_por[familia] = codigo
            componentes.append(
                {
                    "alerta_id": alerta.id,
                    "tipologia": codigo,
                    "tipologia_nome": politica.nome if politica else codigo,
                    "titulo": alerta.titulo,
                    "severidade": alerta.severidade,
                    "situacao": alerta.situacao or "pendente",
                    "peso": peso if conta else 0.0,
                    "sinal_fraco": bool(politica.sinal_fraco) if politica else False,
                    "conta": conta,
                    "observacao": observacao,
                }
            )
        soma = round(sum(contadas.values()), 2)
        if len(contadas) < minimo or soma < peso_minimo:
            continue
        lista = "; ".join(
            f"[{c['tipologia']}] {c['titulo']} (peso {formatar_numero(c['peso'])})"
            if c["conta"]
            else f"[{c['tipologia']}] {c['titulo']} (não soma: {c['observacao']})"
            for c in componentes
        )
        motivo = (
            f"{'A empresa' if entidade.tipo == 'empresa' else 'A pessoa'} {entidade.rotulo}"
            f"{' (' + entidade.identificador + ')' if entidade.identificador else ''} reúne "
            f"{len(componentes)} alerta(s) de {len(contadas)} tipologias distintas, com soma de "
            f"pesos {formatar_numero(soma)}: {lista}. Os mínimos configurados são {minimo} "
            f"tipologias distintas e soma de pesos {formatar_numero(peso_minimo)}. Sinais que "
            "isolados pouco dizem pesam mais quando aparecem juntos na mesma entidade; alertas "
            f"descartados não entram. {AVISO}"
        )
        evidencias = []
        for alerta in do_alvo:
            for evidencia in (alerta.evidencias or [])[:MAX_EVIDENCIAS_POR_COMPONENTE]:
                evidencias.append(
                    Evidencia(
                        descricao=f"[{alerta.tipologia}] {evidencia['descricao']}",
                        fonte=evidencia["fonte"],
                        captura_id=evidencia["captura_id"],
                        url_ou_arquivo=evidencia["url_ou_arquivo"],
                        sha256=evidencia["sha256"],
                    )
                )
        detectados.append(
            AlertaDetectado(
                tipologia=CODIGO,
                titulo=(
                    f"{len(contadas)} tipologias distintas em {entidade.rotulo} (soma de pesos "
                    f"{formatar_numero(soma)})"
                ),
                motivo=motivo,
                entidades=[entidade_id],
                evidencias=evidencias[:MAX_EVIDENCIAS],
                parametros={
                    "entidade_id": entidade_id,
                    "entidade_rotulo": entidade.rotulo,
                    "entidade_tipo": entidade.tipo,
                    "tipologias_distintas": len(contadas),
                    "soma_pesos": soma,
                    "comb_minimo_tipologias": minimo,
                    "comb_peso_minimo": peso_minimo,
                    "componentes": componentes,
                    "aviso": AVISO,
                },
            )
        )
    return detectados
