"""Alerta de combinação (código ``COMB``): a mesma empresa ou pessoa com vários alertas distintos.

Base: Velasco et al. (2021), A decision support system for fraud detection in public procurement,
International Transactions in Operational Research, v. 28, n. 1, seção 3.3 (p. 36): "many patterns
do not represent by themselves individually a significant risk of the existence of fraud or
corruption. However, the risk of fraud increases when combinations of these risk patterns are
identified". O GAFI também diz que a sua lista de indicadores "is not exhaustive".

NÃO É ESCORE DE CULPA: é uma pista para priorizar a análise (RF12, RF14). Os pesos, os mínimos e
as regras de contagem abaixo são DECISÃO DO AUTOR, até haver dados para calibrar:

- só entram alertas ativos e não descartados pelo investigador, das tipologias T e L;
- os sinais fracos (H49: L2, L14 e L15) não têm alerta próprio: são avaliados na mesma execução
  e entram como componentes sem alerta (``alerta_id`` nulo, situação ``sinal``), com o motivo e
  as evidências. Para tirar um sinal da conta, o operador dá peso zero a ele em ``COMB_PESOS``;
- cada tipologia conta uma vez por entidade, com o seu peso (padrão 1; sinais fracos, 0,5);
- tipologias que descrevem o mesmo fato contam uma vez (``FAMILIAS``: a L1 estende a T2);
- uma composição não soma de novo o que já foi contado (``COMPOSICOES``: a L8 é T1 com T7), por
  isso tem peso zero, mas aparece na lista;
- dispara com ao menos ``comb_minimo_tipologias`` tipologias distintas E soma de pesos de ao
  menos ``comb_peso_minimo``. Com os padrões (2 e 2,0), dois sinais fracos sozinhos não disparam.

Diferente das demais, esta regra lê os ALERTAS do caso, e não as entidades: roda depois das
outras, na mesma execução. Executar só uma tipologia não recalcula a combinação; decidir um
alerta também não (vale na próxima execução). Com os padrões, os três sinais fracos juntos somam
1,5 e não disparam sozinhos: é preciso ao menos um alerta comum na mesma entidade.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass

from buscador.modelos import Alerta, Entidade
from buscador.seguranca import numero_finito
from buscador.tipologias import politicas
from buscador.tipologias.motor import AlertaDetectado, Evidencia, formatar_numero

CODIGO = "COMB"
PESO_PADRAO = 1.0
PESO_SINAL_FRACO = 0.5
# Situação do componente que é sinal fraco (H49): não é alerta, então não tem decisão.
SITUACAO_SINAL = "sinal"
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


@dataclass(frozen=True)
class _Item:
    """O que a combinação soma: um alerta gravado ou um sinal fraco (H49), que não tem alerta
    próprio (``alerta_id`` nulo e situação ``sinal``) e por isso leva o motivo consigo."""

    tipologia: str
    titulo: str
    severidade: str
    situacao: str
    alerta_id: str | None
    entidades: tuple[str, ...]
    evidencias: tuple[dict, ...]
    motivo: str = ""


def _itens(alertas: list[Alerta], sinais: list[AlertaDetectado]) -> list[_Item]:
    itens = []
    for alerta in alertas:
        politica = politicas.politica(alerta.tipologia)
        if alerta.tipologia == CODIGO or alerta.ativo is False or alerta.situacao == "descartado":
            continue
        if politica is not None and politica.sinal_fraco:
            # Alerta de sinal fraco gravado por versão anterior: o sinal vem de ``sinais``.
            continue
        itens.append(
            _Item(
                tipologia=alerta.tipologia,
                titulo=alerta.titulo,
                severidade=alerta.severidade,
                situacao=alerta.situacao or "pendente",
                alerta_id=alerta.id,
                entidades=tuple(alerta.entidades or ()),
                evidencias=tuple(alerta.evidencias or ()),
            )
        )
    for sinal in sinais:
        itens.append(
            _Item(
                tipologia=sinal.tipologia,
                titulo=sinal.titulo,
                severidade=sinal.severidade,
                situacao=SITUACAO_SINAL,
                alerta_id=None,
                entidades=tuple(sinal.entidades),
                evidencias=tuple(asdict(e) for e in sinal.evidencias),
                motivo=sinal.motivo,
            )
        )
    return itens


def detectar(
    alertas: list[Alerta],
    entidades: dict[str, Entidade],
    parametros: dict,
    sinais: list[AlertaDetectado] | None = None,
) -> list[AlertaDetectado]:
    """Um alerta de combinação por empresa ou pessoa que reúne tipologias e peso suficientes.

    ``sinais`` são os sinais fracos avaliados na mesma execução (H49): não têm alerta próprio e
    só existem aqui, como componentes."""
    minimo = int(parametros["comb_minimo_tipologias"])
    peso_minimo = float(parametros["comb_peso_minimo"])
    tabela: dict = parametros["comb_pesos"]
    por_entidade: dict[str, list[_Item]] = {}
    for item in _itens(alertas, sinais or []):
        for entidade_id in dict.fromkeys(item.entidades):
            entidade = entidades.get(entidade_id)
            if entidade is not None and entidade.tipo in TIPOS_DE_ENTIDADE:
                por_entidade.setdefault(entidade_id, []).append(item)

    detectados = []
    for entidade_id in sorted(por_entidade):
        entidade = entidades[entidade_id]
        do_alvo = sorted(
            por_entidade[entidade_id],
            key=lambda a: (
                politicas.chave_ordenacao(a.severidade, a.tipologia, a.titulo),
                a.alerta_id or "",
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
                    "alerta_id": alerta.alerta_id,
                    "tipologia": codigo,
                    "tipologia_nome": politica.nome if politica else codigo,
                    "titulo": alerta.titulo,
                    "severidade": alerta.severidade,
                    "situacao": alerta.situacao,
                    "peso": peso if conta else 0.0,
                    "sinal_fraco": bool(politica.sinal_fraco) if politica else False,
                    "conta": conta,
                    "observacao": observacao,
                    # Só o sinal fraco traz o motivo: ele não tem cartão próprio para abrir.
                    "motivo": alerta.motivo,
                }
            )
        soma = round(sum(contadas.values()), 2)
        if len(contadas) < minimo or soma < peso_minimo:
            continue
        lista = "; ".join(
            f"[{c['tipologia']}] {c['titulo']} ("
            + ("sinal fraco, sem alerta próprio; " if c["alerta_id"] is None else "")
            + (
                f"peso {formatar_numero(c['peso'])})"
                if c["conta"]
                else f"não soma: {c['observacao']})"
            )
            for c in componentes
        )
        motivo = (
            f"{'A empresa' if entidade.tipo == 'empresa' else 'A pessoa'} {entidade.rotulo}"
            f"{' (' + entidade.identificador + ')' if entidade.identificador else ''} reúne "
            f"{len(componentes)} alerta(s) ou sinal(is) de {len(contadas)} tipologias distintas, "
            "com soma de "
            f"pesos {formatar_numero(soma)}: {lista}. Os mínimos configurados são {minimo} "
            f"tipologias distintas e soma de pesos {formatar_numero(peso_minimo)}. Sinais que "
            "isolados pouco dizem pesam mais quando aparecem juntos na mesma entidade; alertas "
            f"descartados não entram. {AVISO}"
        )
        evidencias = []
        for alerta in do_alvo:
            for evidencia in alerta.evidencias[:MAX_EVIDENCIAS_POR_COMPONENTE]:
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
