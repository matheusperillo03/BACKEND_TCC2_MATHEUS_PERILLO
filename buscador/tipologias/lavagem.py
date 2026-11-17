"""Tipologias de lavagem de dinheiro e ocultação (códigos L). Mesmo contrato das T: função pura
sobre o ``Contexto``, alerta com motivo em texto, números reais e evidências ligadas às capturas.

As T sinalizam possíveis infrações antecedentes em contratações; as L sinalizam possível ocultação
pela interposição de pessoas e empresas (Lei nº 9.613/1998, art. 1º). Todas são INDÍCIO CADASTRAL:
nenhuma prova lavagem, e todo alerta é pista (RF12). Os sinais financeiros (depósitos, "correntes",
beneficiário final de operação) não são verificáveis com dado aberto e ficam para o RF10.

Critérios e fontes (fichas em docs/lavagem/tipologias-lavagem.md e trechos literais em
docs/lavagem/fontes.md, na monografia). Os limiares são DECISÃO DO AUTOR, salvo quando vêm de lei,
e são configuráveis:

- GAFI/Egmont (2018): FATF; Egmont Group. Concealment of Beneficial Ownership. Paris: FATF, 2018,
  Anexo E (indicadores);
- GAFI (2011): FATF. Laundering the Proceeds of Corruption. Paris: FATF/OECD, 2011;
- Velasco et al. (2021): A decision support system for fraud detection in public procurement.
  International Transactions in Operational Research, v. 28, n. 1, p. 27-47;
- Fronzetti Colladon e Remondi (2017): Using social network analysis to prevent money laundering;
- Carta Circular BCB nº 4.001/2020; COAF, Casos e Casos (edição ANR 2021); Lei nº 12.846/2013.

Nas regras que cruzam pessoas, vale a regra do RF02 e da T7: seis dígitos visíveis do CPF E nome
normalizado iguais dão "pessoa provável" ou "possível coincidência", nunca identificação, e os
registros não são fundidos. Parentesco não é inferido: sobrenome igual não entra em regra nenhuma.
"""

from __future__ import annotations

from datetime import date, timedelta

from buscador.modelos import Aresta, Entidade
from buscador.resolucao.normalizacao import normalizar_nome
from buscador.tipologias.horizonte2 import (
    AVISO_COINCIDENCIA,
    MAX_ITENS_NO_MOTIVO,
    _arestas,
    _atr,
    _identidade_parcial,
    _lista,
    _valor_contratado,
    tipologia_t7,
)
from buscador.tipologias.motor import (
    SITUACAO_ATIVA,
    AlertaDetectado,
    Contexto,
    _codigo_situacao,
    _data,
    _por_item,
    formatar_data,
    formatar_moeda,
    tipologia_t1,
)

# ---- Parâmetros (decisão do autor, configuráveis) ----------------------------------------------

# Qualificações do sócio que contam como administração (tabela de domínio Qualificações dos dados
# abertos do CNPJ, conferida no arquivo de 11/2026): 05 Administrador, 10 Diretor, 16 Presidente,
# 28 Sócio-Gerente, 49 Sócio-Administrador, 50 Empresário e 65 Titular Pessoa Física Residente ou
# Domiciliado no Brasil.
QUALIFICACOES_ADMINISTRACAO = ("05", "10", "16", "28", "49", "50", "65")

# Faixa etária do sócio (metadados do CNPJ, nota 4): 1 = 0 a 12 anos, 2 = 13 a 20, ..., 9 = mais
# de 80 anos, 0 = não se aplica. Atípicas (decisão do autor): até 20 anos e mais de 80.
FAIXAS_ETARIAS = {
    "1": "0 a 12 anos",
    "2": "13 a 20 anos",
    "3": "21 a 30 anos",
    "4": "31 a 40 anos",
    "5": "41 a 50 anos",
    "6": "51 a 60 anos",
    "7": "61 a 70 anos",
    "8": "71 a 80 anos",
    "9": "mais de 80 anos",
}
FAIXAS_ATIPICAS = ("1", "2", "9")

# Identificador de sócio (metadados do CNPJ): 1 pessoa jurídica, 2 pessoa física, 3 estrangeiro.
SOCIO_PJ, SOCIO_PF, SOCIO_ESTRANGEIRO = "1", "2", "3"

# Teto de níveis percorridos na L4 (proteção contra cadeias anômalas; ciclos são cortados antes).
L4_MAX_NIVEIS = 20

AVISO_PESSOA_PROVAVEL = (
    "É PESSOA PROVÁVEL, não identificada: o CPF nos dados abertos do CNPJ é descaracterizado, "
    "então a regra só pôde comparar os seis dígitos visíveis e o nome normalizado. Homônimos com "
    "os mesmos seis dígitos existem; a confirmação exige fonte com o CPF completo."
)
AVISO_PISTA = "É indício cadastral a verificar, não conclusão (RF12)."


def parametros_padrao() -> dict:
    """Padrões do código para as tipologias L (os mesmos padrões da configuração)."""
    return {
        "l1_minimo_estabelecimentos": 5,
        "l1_janela_abertura_dias": 180,
        "l1_minimo_abertas_na_janela": 3,
        "l3_minimo_empresas": 5,
        "l3_minimo_abertas_na_janela": 3,
        "l3_janela_meses": 12,
        "l4_minimo_niveis": 2,
        "l7_dias_apos_abertura": 30,
        "l7_meses_antes_contrato": 12,
        "l14_valor_minimo": 100_000.0,
        "l14_faixas_atipicas": list(FAIXAS_ATIPICAS),
        "l15_minimo_cnaes": 20,
        "l15_minimo_categorias": 4,
        "qualificacoes_administracao": list(QUALIFICACOES_ADMINISTRACAO),
    }


def lista_de_codigos(texto: str, nome: str, tamanho: int = 0) -> list[str]:
    """Códigos numéricos separados por vírgula (configuração); ``tamanho`` completa com zeros."""
    codigos = []
    for item in (texto or "").split(","):
        item = item.strip()
        if not item:
            continue
        if not item.isdigit() or len(item) > 4:
            raise ValueError(f"{nome}: '{item[:20]}' não é um código numérico.")
        codigos.append(item.zfill(tamanho))
    if not codigos:
        raise ValueError(f"{nome} não pode ficar vazio.")
    return sorted(set(codigos))


# ---- Apoio ------------------------------------------------------------------------------------


def _mais_meses(dia: date, meses: int) -> date:
    """A data ``meses`` meses depois (o dia é reduzido ao último do mês, se preciso)."""
    total = dia.year * 12 + (dia.month - 1) + meses
    ano, mes = divmod(total, 12)
    mes += 1
    for d in range(dia.day, 27, -1):
        try:
            return date(ano, mes, d)
        except ValueError:
            continue
    return date(ano, mes, min(dia.day, 28))


def _empresas(ctx: Contexto) -> list[Entidade]:
    return sorted((e for e in ctx.entidades.values() if e.tipo == "empresa"), key=lambda e: e.chave)


def _estabelecimentos(empresa: Entidade) -> list[dict]:
    return _atr(empresa).get("estabelecimentos") or []


def _inicio(empresa: Entidade) -> date | None:
    return _data(_atr(empresa).get("data_inicio_atividade"))


def _quadro(ctx: Contexto) -> dict[str, list[tuple[Entidade, Aresta]]]:
    """empresa -> [(sócio, aresta)], com sócios pessoa física, jurídica e estrangeiros."""
    quadro: dict[str, list[tuple[Entidade, Aresta]]] = {}
    for a in _arestas(ctx, "socio_de"):
        socio = ctx.entidades.get(a.origem_id)
        if socio is not None and a.destino_id in ctx.entidades:
            quadro.setdefault(a.destino_id, []).append((socio, a))
    for lista in quadro.values():
        lista.sort(key=lambda par: (par[0].rotulo, par[0].id))
    return quadro


def _contratos_por_empresa(ctx: Contexto) -> dict[str, list[Entidade]]:
    contratos: dict[str, list[Entidade]] = {}
    for fornecedor, contrato in ctx.contratos_com_fornecedor():
        if fornecedor.tipo == "empresa":
            contratos.setdefault(fornecedor.id, []).append(contrato)
    for lista in contratos.values():
        lista.sort(key=lambda c: c.identificador)
    return contratos


def _qualificacao(aresta: Aresta) -> str:
    return str((aresta.atributos or {}).get("qualificacao_socio") or "").strip().zfill(2)


def _identificador_socio(aresta: Aresta) -> str:
    return str((aresta.atributos or {}).get("identificador_socio") or "").strip()


def _maior_concentracao(datas: list[date], dias: int) -> tuple[int, date | None]:
    """Maior número de datas dentro de uma janela de ``dias`` dias e o início dessa janela."""
    datas = sorted(datas)
    melhor, inicio = 0, None
    fim = 0
    for i, primeira in enumerate(datas):
        while fim < len(datas) and (datas[fim] - primeira).days <= dias:
            fim += 1
        if fim - i > melhor:
            melhor, inicio = fim - i, primeira
    return melhor, inicio


def _evidencia_estabelecimento(ctx: Contexto, empresa: Entidade, est: dict, texto: str):
    captura = est.get("captura_id")
    if captura not in ctx.capturas:
        captura = empresa.captura_id
    return ctx.evidencia(
        captura,
        f"Estabelecimento {est.get('cnpj') or empresa.identificador} de {empresa.rotulo}: {texto} "
        "(dados abertos do CNPJ, Estabelecimentos)",
    )


def _evidencia_socio(ctx: Contexto, socio: Entidade, aresta: Aresta, empresa: Entidade):
    atributos = aresta.atributos or {}
    documento = _identidade_parcial(socio)[0] if socio.tipo == "pessoa" else socio.identificador
    return ctx.evidencia(
        aresta.captura_id,
        f"Sócio {socio.rotulo}{' (' + documento + ')' if documento else ''} de {empresa.rotulo}: "
        f"qualificação {_qualificacao(aresta)}, entrada em "
        f"{formatar_data(atributos.get('data_entrada_sociedade'))} "
        "(dados abertos do CNPJ, Sócios)",
    )


# ---- L1: endereço de registro em massa ---------------------------------------------------------
#
# GAFI/Egmont (2018), Anexo E, item 4 ("registered at an address that is also listed against
# numerous other companies") e item 11 ("Address of mass registration"); Velasco et al. (2021),
# seção 3.1 ("Common addresses"). Extensão da T2: não exige contrato, separa as salas pelo
# complemento, conta só estabelecimento ATIVO e olha a concentração das aberturas.


def tipologia_l1(ctx: Contexto, parametros: dict) -> list[AlertaDetectado]:
    minimo = int(parametros["l1_minimo_estabelecimentos"])
    janela = int(parametros["l1_janela_abertura_dias"])
    minimo_janela = int(parametros["l1_minimo_abertas_na_janela"])
    grupos: dict[str, dict[str, tuple[Entidade, dict]]] = {}
    for empresa in _empresas(ctx):
        for est in _estabelecimentos(empresa):
            # Sem situação informada não se sabe se o estabelecimento está ativo: fica de fora.
            if _codigo_situacao(est.get("situacao_cadastral")) != SITUACAO_ATIVA:
                continue
            chave = est.get("endereco_com_complemento") or est.get("endereco_normalizado")
            if chave:
                # Uma empresa conta uma vez por endereço (matriz e filial no mesmo lugar).
                grupos.setdefault(chave, {}).setdefault(empresa.id, (empresa, est))

    def regra(grupo: tuple[str, dict[str, tuple[Entidade, dict]]]) -> AlertaDetectado | None:
        chave, por_empresa = grupo
        if len(por_empresa) < minimo:
            return None
        itens = sorted(por_empresa.values(), key=lambda par: par[0].rotulo)
        datas = [d for _, est in itens if (d := _data(est.get("data_inicio_atividade")))]
        concentradas, inicio_janela = _maior_concentracao(datas, janela)
        concentrada = concentradas >= minimo_janela
        legivel = itens[0][1].get("endereco") or chave
        nomes = _lista(
            [
                f"{e.rotulo} (CNPJ {est.get('cnpj') or e.identificador}, início em "
                f"{formatar_data(est.get('data_inicio_atividade'))})"
                for e, est in itens
            ]
        )
        abertura = (
            f"; {concentradas} delas iniciaram a atividade num intervalo de até {janela} dias, a "
            f"partir de {formatar_data(inicio_janela.isoformat())} (limiar: {minimo_janela})"
            if concentrada and inicio_janela
            else f"; não há {minimo_janela} aberturas num intervalo de {janela} dias"
        )
        motivo = (
            f"O endereço '{legivel}' é o endereço de registro de {len(itens)} empresas com "
            f"estabelecimento ATIVO no CNPJ ({nomes}){abertura}. O limiar configurado é de "
            f"{minimo} empresas ativas no mesmo endereço, contado o complemento. Pode indicar "
            "domicílio de fachada ou endereço de registro em massa, mas também centro "
            "empresarial, escritório de contabilidade, coworking ou residência de "
            "microempreendedor: o dado aberto não diz o tipo do endereço. " + AVISO_PISTA
        )
        return AlertaDetectado(
            tipologia="L1",
            titulo=(
                f"Endereço de registro de {len(itens)} empresas ativas"
                + (f", {concentradas} abertas em {janela} dias" if concentrada else "")
            ),
            motivo=motivo,
            entidades=[e.id for e, _ in itens],
            evidencias=[
                _evidencia_estabelecimento(
                    ctx,
                    e,
                    est,
                    f"endereço '{est.get('endereco')}', situação ATIVA, início em "
                    f"{formatar_data(est.get('data_inicio_atividade'))}",
                )
                for e, est in itens[: MAX_ITENS_NO_MOTIVO * 3]
            ],
            discriminador=chave,
            parametros={
                "l1_minimo_estabelecimentos": minimo,
                "l1_janela_abertura_dias": janela,
                "l1_minimo_abertas_na_janela": minimo_janela,
                "empresas_ativas_no_endereco": len(itens),
                "abertas_na_janela": concentradas,
                "abertura_concentrada": concentrada,
                "endereco_normalizado": chave,
            },
        )

    return _por_item("L1", sorted(grupos.items()), regra)


# ---- L3: mesmo administrador ou representante em muitas empresas -------------------------------
#
# GAFI/Egmont (2018), Anexo E, item 10 ("mass" nominees) e item 4 (dirigentes "listed against the
# accounts of other legal persons [...] indicating the use of professional nominees"); Carta
# Circular BCB nº 4.001/2020, III, h; Fronzetti Colladon e Remondi (2017) (vínculo por "same owner
# or representative"); Lei nº 12.846/2013, art. 5º, III (interposta pessoa).


def tipologia_l3(ctx: Contexto, parametros: dict) -> list[AlertaDetectado]:
    minimo = int(parametros["l3_minimo_empresas"])
    minimo_janela = int(parametros["l3_minimo_abertas_na_janela"])
    meses = int(parametros["l3_janela_meses"])
    administracao = set(parametros["qualificacoes_administracao"])

    # (seis dígitos, nome normalizado) -> empresa -> (papel, aresta, sócio ou None)
    pessoas: dict[tuple[str, str], dict[str, tuple[str, Aresta, Entidade | None]]] = {}
    for empresa_id, quadro in sorted(_quadro(ctx).items()):
        for socio, aresta in quadro:
            atributos = aresta.atributos or {}
            if socio.tipo == "pessoa" and _qualificacao(aresta) in administracao:
                cpf, nome = _identidade_parcial(socio)
                if cpf and nome:
                    pessoas.setdefault((cpf, nome), {}).setdefault(
                        empresa_id,
                        (f"sócio com qualificação {_qualificacao(aresta)}", aresta, socio),
                    )
            cpf_rep = str(atributos.get("representante_legal") or "")
            nome_rep = normalizar_nome(atributos.get("nome_representante"))
            # ``***000000**`` é o preenchimento do layout para "sem representante".
            if cpf_rep and nome_rep and cpf_rep.strip("*") != "000000":
                pessoas.setdefault((cpf_rep, nome_rep), {}).setdefault(
                    empresa_id, ("representante legal de sócio", aresta, None)
                )

    def regra(item) -> AlertaDetectado | None:
        (cpf, nome), vinculos = item
        if len(vinculos) < minimo:
            return None
        empresas = sorted((ctx.entidades[eid] for eid in vinculos), key=lambda e: e.rotulo)
        datas = [d for e in empresas if (d := _inicio(e))]
        # Janela em meses: a maior concentração entre cada abertura e a data ``meses`` depois.
        concentradas, inicio_janela = 0, None
        for primeira in sorted(datas):
            limite = _mais_meses(primeira, meses)
            dentro = sum(1 for d in datas if primeira <= d <= limite)
            if dentro > concentradas:
                concentradas, inicio_janela = dentro, primeira
        if concentradas < minimo_janela:
            return None
        socios = {v[2].id: v[2] for v in vinculos.values() if v[2] is not None}
        nome_legivel = next(iter(socios.values())).rotulo if socios else nome
        lista = _lista(
            [
                f"{e.rotulo} (CNPJ básico {e.identificador}, {vinculos[e.id][0]}, início da "
                f"atividade em {formatar_data(_atr(e).get('data_inicio_atividade'))})"
                for e in empresas
            ]
        )
        motivo = (
            f"A mesma pessoa provável, {nome_legivel} (CPF descaracterizado {cpf}), aparece na "
            f"administração de {len(empresas)} empresas do caso: {lista}. Dessas, {concentradas} "
            f"iniciaram a atividade em até {meses} meses, a partir de "
            f"{formatar_data(inicio_janela.isoformat() if inicio_janela else None)}. Os limiares "
            f"configurados são {minimo} empresas e {minimo_janela} abertas em {meses} meses. Pode "
            "indicar administrador de fachada (laranja profissional), mas também empresário com "
            f"várias empresas ou holding familiar. {AVISO_PESSOA_PROVAVEL} {AVISO_PISTA}"
        )
        evidencias = [
            ctx.evidencia(
                vinculos[e.id][1].captura_id,
                f"{nome_legivel} ({cpf}) em {e.rotulo}: {vinculos[e.id][0]} "
                "(dados abertos do CNPJ, Sócios)",
            )
            for e in empresas[: MAX_ITENS_NO_MOTIVO * 3]
        ]
        return AlertaDetectado(
            tipologia="L3",
            titulo=f"Mesma pessoa provável na administração de {len(empresas)} empresas",
            motivo=motivo,
            entidades=sorted(socios) + [e.id for e in empresas],
            evidencias=evidencias,
            discriminador=f"{cpf}|{nome}",
            parametros={
                "l3_minimo_empresas": minimo,
                "l3_minimo_abertas_na_janela": minimo_janela,
                "l3_janela_meses": meses,
                "empresas": len(empresas),
                "abertas_na_janela": concentradas,
                "cpf_descaracterizado": cpf,
                "nome_normalizado": nome,
                "criterio": "seis dígitos visíveis do CPF e nome normalizado iguais",
                "identidade_confirmada": False,
            },
        )

    return _por_item("L3", sorted(pessoas.items()), regra)


# ---- L4: cadeia societária em camadas ----------------------------------------------------------
#
# GAFI/Egmont (2018), Anexo E, item 8 ("Complex corporate structures that do not appear to
# legitimately require that level of complexity"); GAFI (2011), § 47 ("every examined case
# featured the use of corporate vehicles"); Lei nº 12.846/2013, art. 5º, III (interposta pessoa
# jurídica). É a "cadeia societária em vários níveis" do RF03, como regra sobre o grafo.


def _cadeia_mais_longa(
    empresa_id: str, quadro: dict[str, list[tuple[Entidade, Aresta]]], visitadas: frozenset[str]
) -> list[tuple[Entidade, Aresta]]:
    """Maior sequência de sócios pessoa jurídica acima da empresa (sem repetir empresa)."""
    melhor: list[tuple[Entidade, Aresta]] = []
    if len(visitadas) > L4_MAX_NIVEIS:
        return melhor
    for socio, aresta in quadro.get(empresa_id, []):
        if socio.tipo != "empresa" or socio.id in visitadas:
            continue
        cadeia = [(socio, aresta), *_cadeia_mais_longa(socio.id, quadro, visitadas | {socio.id})]
        if len(cadeia) > len(melhor):
            melhor = cadeia
    return melhor


def tipologia_l4(ctx: Contexto, parametros: dict) -> list[AlertaDetectado]:
    minimo = int(parametros["l4_minimo_niveis"])
    quadro = _quadro(ctx)

    def regra(empresa: Entidade) -> AlertaDetectado | None:
        socios = quadro.get(empresa.id, [])
        # Com pessoa física (ou estrangeiro) no primeiro nível, o controle direto é visível.
        if not socios or any(s.tipo != "empresa" for s, _ in socios):
            return None
        cadeia = _cadeia_mais_longa(empresa.id, quadro, frozenset({empresa.id}))
        if len(cadeia) < minimo:
            return None
        topo = cadeia[-1][0]
        acima = quadro.get(topo.id, [])
        if not acima:
            termino, opaca = "pessoa jurídica sem quadro societário nos dados carregados", True
        elif any(_identificador_socio(a) == SOCIO_ESTRANGEIRO for _, a in acima):
            termino, opaca = "sócio estrangeiro", True
        elif all(s.tipo == "empresa" for s, _ in acima):
            termino, opaca = "pessoa jurídica já percorrida (participação cruzada)", True
        else:
            termino, opaca = "pessoa física", False
        niveis = " ← ".join(
            [empresa.rotulo]
            + [
                f"{s.rotulo} (CNPJ básico {s.identificador})"
                for s, _ in cadeia[:MAX_ITENS_NO_MOTIVO]
            ]
        )
        motivo = (
            f"O controle de {empresa.rotulo} (CNPJ básico {empresa.identificador}) passa por "
            f"{len(cadeia)} níveis de sócio pessoa jurídica, sem pessoa física no primeiro nível: "
            f"{niveis}. A cadeia termina em {termino}. O limiar configurado é de {minimo} níveis. "
            "Pode indicar interposição de pessoas jurídicas para ocultar o controlador, mas "
            "também grupo econômico, fundo ou companhia aberta. A cadeia só é vista até onde o "
            "quadro societário foi coletado neste caso. " + AVISO_PISTA
        )
        anterior = empresa
        evidencias = []
        for socio, aresta in cadeia[: MAX_ITENS_NO_MOTIVO * 2]:
            evidencias.append(_evidencia_socio(ctx, socio, aresta, anterior))
            anterior = socio
        return AlertaDetectado(
            tipologia="L4",
            titulo=f"Cadeia societária de {len(cadeia)} níveis acima de {empresa.rotulo}",
            motivo=motivo,
            entidades=[empresa.id] + [s.id for s, _ in cadeia],
            evidencias=evidencias,
            parametros={
                "l4_minimo_niveis": minimo,
                "niveis": len(cadeia),
                "termina_em": termino,
                "termino_opaco": opaca,
            },
        )

    return _por_item("L4", _empresas(ctx), regra)


# ---- L5 (nível 1): sócio estrangeiro -----------------------------------------------------------
#
# GAFI/Egmont (2018), Anexo E, item 4 ("incorporated/formed in a low-tax jurisdiction or
# international trade or finance centre"). Só o nível 1 (há sócio estrangeiro): o nível 2 (lista
# de jurisdições) depende da lista oficial (IN RFB nº 1.037/2010), ainda não conferida.


def tipologia_l5(ctx: Contexto, parametros: dict) -> list[AlertaDetectado]:
    quadro = _quadro(ctx)

    def regra(empresa: Entidade) -> AlertaDetectado | None:
        estrangeiros = [
            (s, a)
            for s, a in quadro.get(empresa.id, [])
            if _identificador_socio(a) == SOCIO_ESTRANGEIRO
        ]
        if not estrangeiros:
            return None

        def pais(aresta: Aresta) -> str:
            return str((aresta.atributos or {}).get("pais") or "").strip() or "não informado"

        nomes = _lista([f"{s.rotulo} (código do país: {pais(a)})" for s, a in estrangeiros])
        motivo = (
            f"A empresa {empresa.rotulo} (CNPJ básico {empresa.identificador}) tem "
            f"{len(estrangeiros)} sócio(s) estrangeiro(s) no quadro do CNPJ (identificador de "
            f"sócio 3): {nomes}. O código do país é o da tabela de domínio Países da Receita. A "
            "regra não avalia o país: a lista de jurisdições (nível 2) não foi implementada. "
            "Investimento estrangeiro é legítimo; o sinal só interessa junto de outros. "
            + AVISO_PISTA
        )
        return AlertaDetectado(
            tipologia="L5",
            titulo=f"Sócio estrangeiro em {empresa.rotulo}",
            motivo=motivo,
            entidades=[empresa.id] + [s.id for s, _ in estrangeiros],
            evidencias=[
                _evidencia_socio(ctx, s, a, empresa) for s, a in estrangeiros[:MAX_ITENS_NO_MOTIVO]
            ],
            parametros={
                "socios_estrangeiros": len(estrangeiros),
                "paises": sorted({pais(a) for _, a in estrangeiros}),
                "nivel": 1,
            },
        )

    return _por_item("L5", _empresas(ctx), regra)


# ---- L7: fundadores substituídos logo após a abertura ------------------------------------------
#
# GAFI/Egmont (2018), Anexo E, item 7 ("the resignation and replacement of directors or key
# shareholders shortly after incorporation"). Parte (a) da ficha: retrato único do quadro. O dado
# aberto não tem histórico de sócios; a parte (b), entre duas coletas, não está implementada.


def tipologia_l7(ctx: Contexto, parametros: dict) -> list[AlertaDetectado]:
    tolerancia = int(parametros["l7_dias_apos_abertura"])
    meses = int(parametros["l7_meses_antes_contrato"])
    quadro = _quadro(ctx)
    contratos = _contratos_por_empresa(ctx)

    def regra(empresa_id: str) -> AlertaDetectado | None:
        empresa = ctx.entidades[empresa_id]
        inicio = _inicio(empresa)
        socios = quadro.get(empresa_id, [])
        if inicio is None or not socios:
            return None
        entradas = [_data((a.atributos or {}).get("data_entrada_sociedade")) for _, a in socios]
        # Sócio sem data de entrada pode ser fundador: sem a data, a regra não afirma nada.
        if any(d is None for d in entradas):
            return None
        if any(d <= inicio + timedelta(days=tolerancia) for d in entradas):
            return None
        ultima = max(entradas)
        proximos = [
            c
            for c in contratos[empresa_id]
            if (assinatura := _data(_atr(c).get("data_assinatura"))) is not None
            and ultima <= assinatura <= _mais_meses(ultima, meses)
        ]
        if not proximos:
            return None
        quadro_texto = _lista(
            [
                f"{s.rotulo} (entrada em "
                f"{formatar_data((a.atributos or {}).get('data_entrada_sociedade'))})"
                for s, a in socios
            ]
        )
        contratos_texto = _lista(
            [
                f"{c.identificador} (assinado em {formatar_data(_atr(c).get('data_assinatura'))})"
                for c in proximos
            ]
        )
        motivo = (
            f"A empresa {empresa.rotulo} (CNPJ básico {empresa.identificador}) iniciou a "
            f"atividade em {formatar_data(inicio.isoformat())}, mas nenhum dos {len(socios)} "
            f"sócios atuais entrou até {tolerancia} dias depois: {quadro_texto}. A entrada mais "
            f"recente, em {formatar_data(ultima.isoformat())}, é anterior em até {meses} meses a "
            f"{len(proximos)} contrato(s): {contratos_texto}. Pode indicar troca de sócios de "
            "fachada antes da contratação, mas também venda legítima da empresa ou sucessão. O "
            "dado aberto traz só o quadro atual: quem saiu, e quando, não aparece. " + AVISO_PISTA
        )
        est = next(iter(_estabelecimentos(empresa)), None)
        evidencias = []
        if est is not None:
            evidencias.append(
                _evidencia_estabelecimento(
                    ctx, empresa, est, f"início da atividade em {formatar_data(inicio.isoformat())}"
                )
            )
        evidencias += [
            _evidencia_socio(ctx, s, a, empresa) for s, a in socios[:MAX_ITENS_NO_MOTIVO]
        ]
        evidencias += [
            ctx.evidencia(
                c.captura_id,
                f"Contrato {c.identificador}: assinatura em "
                f"{formatar_data(_atr(c).get('data_assinatura'))} (PNCP)",
            )
            for c in proximos[:MAX_ITENS_NO_MOTIVO]
        ]
        return AlertaDetectado(
            tipologia="L7",
            titulo=f"Nenhum fundador no quadro atual de {empresa.rotulo}",
            motivo=motivo,
            entidades=[empresa.id] + [c.id for c in proximos] + [s.id for s, _ in socios],
            evidencias=evidencias,
            parametros={
                "l7_dias_apos_abertura": tolerancia,
                "l7_meses_antes_contrato": meses,
                "data_inicio_atividade": inicio.isoformat(),
                "primeira_entrada": min(entradas).isoformat(),
                "ultima_entrada": ultima.isoformat(),
                "contratos": len(proximos),
                "parte": "a (retrato único)",
            },
        )

    return _por_item("L7", sorted(contratos), regra)


# ---- L8: empresa recém-aberta com sócio que possivelmente é agente público ---------------------
#
# COAF, Casos e Casos (edição ANR 2021), tipologia 2.7: "Empresas recém-criadas, sem empregados
# registrados, cujos sócios têm vínculos públicos". Composição da T1 com a T7: não cria coleta.
# O trecho "sem empregados" NÃO é verificável (o dado aberto do CNPJ não tem o campo). A parte
# com eleito ou PEP (L9) depende de decisão do orientador e não entra.


def tipologia_l8(ctx: Contexto, parametros: dict) -> list[AlertaDetectado]:
    recentes: dict[str, list[AlertaDetectado]] = {}
    for alerta in tipologia_t1(ctx, parametros):
        recentes.setdefault(alerta.entidades[0], []).append(alerta)
    coincidencias: dict[str, list[AlertaDetectado]] = {}
    for alerta in tipologia_t7(ctx, parametros):
        # Entidades da T7: sócio, servidor, empresa, órgão e contratos.
        coincidencias.setdefault(alerta.entidades[2], []).append(alerta)

    def regra(empresa_id: str) -> AlertaDetectado:
        empresa = ctx.entidades[empresa_id]
        t1, t7 = recentes[empresa_id], coincidencias[empresa_id]
        contratos_texto = _lista(
            [
                f"{ctx.entidades[a.entidades[1]].identificador} "
                f"({a.parametros['dias_observados']} dias depois do início da atividade)"
                for a in t1
            ]
        )
        socios_texto = _lista(
            [
                f"{ctx.entidades[a.entidades[0]].rotulo} (CPF descaracterizado "
                f"{a.parametros['cpf_descaracterizado']}) e um servidor de mesmo nome em "
                f"{ctx.entidades[a.entidades[3]].rotulo}"
                for a in t7
            ]
        )
        motivo = (
            f"A empresa {empresa.rotulo} (CNPJ básico {empresa.identificador}) reúne dois "
            f"sinais: (1) foi contratada pouco depois de aberta (T1, limiar de "
            f"{int(parametros['t1_dias_minimos'])} dias): {contratos_texto}; e (2) tem sócio que "
            f"possivelmente coincide com servidor do órgão contratante (T7): {socios_texto}. É o "
            "sinal de alerta de empresa recém-criada cujos sócios têm vínculo público; a parte "
            "'sem empregados registrados' desse sinal não é verificável com dado aberto. "
            + AVISO_COINCIDENCIA
        )
        evidencias, vistas = [], set()
        for alerta in (*t1, *t7):
            for evidencia in alerta.evidencias:
                marca = (evidencia.captura_id, evidencia.descricao)
                if marca not in vistas:
                    vistas.add(marca)
                    evidencias.append(evidencia)
        entidades = [empresa_id]
        for alerta in (*t1, *t7):
            entidades += alerta.entidades
        return AlertaDetectado(
            tipologia="L8",
            titulo=f"Empresa recém-aberta com sócio possivelmente servidor: {empresa.rotulo}",
            motivo=motivo,
            entidades=list(dict.fromkeys(entidades)),
            evidencias=evidencias[: MAX_ITENS_NO_MOTIVO * 3],
            discriminador=empresa_id,
            parametros={
                "t1_dias_minimos": int(parametros["t1_dias_minimos"]),
                "contratos_recentes": len(t1),
                "possiveis_coincidencias": len(t7),
                "composicao": ["T1", "T7"],
                "identidade_confirmada": False,
            },
        )

    return _por_item("L8", sorted(set(recentes) & set(coincidencias)), regra)


# ---- L10: vínculo com empresa sancionada -------------------------------------------------------
#
# GAFI/Egmont (2018), Anexo E, item 3 ("have previously been prohibited from holding a
# directorship role in a company"); Velasco et al. (2021), seção 3 ("Blacklisting", no nível da
# empresa e da pessoa); Lei nº 12.846/2013, art. 5º, IV, e (criar pessoa jurídica de modo
# fraudulento para contratar). Extensão da T6, que só olha a própria contratada.

CADASTROS_COM_PERIODO = ("CEIS", "CNEP")


def _sancoes_por_entidade(ctx: Contexto) -> dict[str, list[Entidade]]:
    sancoes: dict[str, list[Entidade]] = {}
    for a in _arestas(ctx, "sancionada_em"):
        sancao = ctx.entidades.get(a.destino_id)
        if sancao is not None and sancao.tipo == "sancao":
            sancoes.setdefault(a.origem_id, []).append(sancao)
    return sancoes


def _vigente(sancao: Entidade, dia: date) -> bool:
    """A sanção cobre a data? CEIS e CNEP têm período; sem data de início, não é aferível."""
    atributos = _atr(sancao)
    if atributos.get("cadastro") not in CADASTROS_COM_PERIODO:
        return False
    inicio, fim = _data(atributos.get("data_inicio")), _data(atributos.get("data_fim"))
    return inicio is not None and inicio <= dia and (fim is None or dia <= fim)


def _descricao_sancao(sancao: Entidade) -> str:
    s = _atr(sancao)
    periodo = (
        f"de {formatar_data(s.get('data_inicio'))} a {formatar_data(s.get('data_fim'))}"
        if s.get("data_fim")
        else f"desde {formatar_data(s.get('data_inicio'))}, sem data final informada"
        if s.get("data_inicio")
        else "sem período informado no cadastro"
    )
    return (
        f"{s.get('cadastro') or 'cadastro'}, registro {s.get('id_registro') or 'sem id'}: "
        f"{s.get('tipo_sancao') or 'sanção sem tipo informado'}, {periodo}"
    )


def _vinculos_com_sancionadas(ctx: Contexto, parametros: dict):
    """Casos (contratada, sancionada) -> contratos, sanções e o vínculo (sócio ou endereço)."""
    administracao = set(parametros["qualificacoes_administracao"])
    sancoes = _sancoes_por_entidade(ctx)
    quadro = _quadro(ctx)
    # sócio pessoa física com CPF, em qualificação de administração -> empresas
    administra: dict[str, dict[str, Aresta]] = {}
    for empresa_id, socios in quadro.items():
        for socio, aresta in socios:
            if (
                socio.tipo == "pessoa"
                and _identidade_parcial(socio)[0]
                and _qualificacao(aresta) in administracao
            ):
                administra.setdefault(socio.id, {})[empresa_id] = aresta
    por_endereco: dict[str, set[str]] = {}
    for empresa in _empresas(ctx):
        for chave in _atr(empresa).get("enderecos_normalizados") or []:
            if chave:
                por_endereco.setdefault(chave, set()).add(empresa.id)

    casos: dict[tuple[str, str], dict] = {}
    for contratada_id, contratos in sorted(_contratos_por_empresa(ctx).items()):
        for contrato in contratos:
            assinatura = _data(_atr(contrato).get("data_assinatura"))
            if assinatura is None:
                continue
            # Com sanção própria vigente, o caso é da T6.
            if any(_vigente(s, assinatura) for s in sancoes.get(contratada_id, [])):
                continue
            vinculadas: dict[str, list[str]] = {}
            for socio_id, empresas in administra.items():
                if contratada_id in empresas:
                    for outra in empresas:
                        if outra != contratada_id:
                            vinculadas.setdefault(outra, []).append(f"socio:{socio_id}")
            for chave in _atr(ctx.entidades[contratada_id]).get("enderecos_normalizados") or []:
                for outra in por_endereco.get(chave, ()):
                    if outra != contratada_id:
                        vinculadas.setdefault(outra, []).append(f"endereco:{chave}")
            for outra, vinculos in vinculadas.items():
                vigentes = [s for s in sancoes.get(outra, []) if _vigente(s, assinatura)]
                if not vigentes:
                    continue
                caso = casos.setdefault(
                    (contratada_id, outra), {"contratos": {}, "sancoes": {}, "vinculos": set()}
                )
                caso["contratos"][contrato.id] = contrato
                caso["sancoes"].update({s.id: s for s in vigentes})
                caso["vinculos"].update(vinculos)
    return casos, administra


def _l10_sancionadas(ctx: Contexto, parametros: dict) -> list[AlertaDetectado]:
    casos, administra = _vinculos_com_sancionadas(ctx, parametros)

    def regra(item) -> AlertaDetectado:
        (contratada_id, outra_id), caso = item
        contratada, outra = ctx.entidades[contratada_id], ctx.entidades[outra_id]
        contratos = sorted(caso["contratos"].values(), key=lambda c: c.identificador)
        sancoes = sorted(caso["sancoes"].values(), key=lambda s: s.identificador)
        partes, evidencias, entidades = [], [], [contratada_id, outra_id]
        por_socio = False
        for vinculo in sorted(caso["vinculos"]):
            tipo, _, valor = vinculo.partition(":")
            if tipo == "socio":
                por_socio = True
                socio = ctx.entidades[valor]
                cpf = _identidade_parcial(socio)[0]
                partes.append(
                    f"o sócio-administrador provável {socio.rotulo} (CPF descaracterizado {cpf}) "
                    "está no quadro das duas"
                )
                entidades.append(valor)
                for empresa in (contratada, outra):
                    evidencias.append(
                        _evidencia_socio(ctx, socio, administra[valor][empresa.id], empresa)
                    )
            else:
                partes.append("as duas têm estabelecimento no mesmo endereço normalizado")
                for empresa in (contratada, outra):
                    for est in _estabelecimentos(empresa):
                        if est.get("endereco_normalizado") == valor:
                            evidencias.append(
                                _evidencia_estabelecimento(
                                    ctx, empresa, est, f"endereço '{est.get('endereco')}'"
                                )
                            )
                            break
        for sancao in sancoes:
            entidades.append(sancao.id)
            evidencias.append(
                ctx.evidencia(
                    sancao.captura_id,
                    f"{outra.rotulo}: {_descricao_sancao(sancao)} (Portal da Transparência)",
                )
            )
        for contrato in contratos[:MAX_ITENS_NO_MOTIVO]:
            entidades.append(contrato.id)
            evidencias.append(
                ctx.evidencia(
                    contrato.captura_id,
                    f"Contrato {contrato.identificador} de {contratada.rotulo}: assinatura em "
                    f"{formatar_data(_atr(contrato).get('data_assinatura'))} (PNCP)",
                )
            )
        motivo = (
            f"A contratada {contratada.rotulo} (CNPJ básico {contratada.identificador}) não tem "
            f"sanção própria vigente, mas está ligada a {outra.rotulo} (CNPJ básico "
            f"{outra.identificador}), que tinha sanção vigente na data de assinatura de "
            f"{len(contratos)} contrato(s) "
            f"({_lista([c.identificador for c in contratos])}): "
            f"{_lista([_descricao_sancao(s) for s in sancoes])}. Vínculo: {'; '.join(partes)}. "
            "Pode indicar empresa usada no lugar da sancionada, mas também endereço compartilhado "
            "legítimo ou sócio que saiu antes da sanção. "
            + (AVISO_PESSOA_PROVAVEL + " " if por_socio else "")
            + AVISO_PISTA
        )
        return AlertaDetectado(
            tipologia="L10",
            titulo=f"{contratada.rotulo} ligada à sancionada {outra.rotulo}",
            motivo=motivo,
            entidades=list(dict.fromkeys(entidades)),
            evidencias=evidencias[: MAX_ITENS_NO_MOTIVO * 3],
            discriminador=f"empresa|{contratada_id}|{outra_id}",
            parametros={
                "parte": "empresa sancionada (CEIS ou CNEP)",
                "vinculo_por_socio": por_socio,
                "vinculo_por_endereco": any(v.startswith("endereco:") for v in caso["vinculos"]),
                "sancoes": len(sancoes),
                "contratos": len(contratos),
                "identidade_confirmada": False if por_socio else None,
            },
        )

    return _por_item("L10", sorted(casos.items(), key=lambda i: i[0]), regra)


def tipologia_l10(ctx: Contexto, parametros: dict) -> list[AlertaDetectado]:
    return _l10_sancionadas(ctx, parametros)


# ---- L14 (sinal fraco): sócio em faixa etária atípica ------------------------------------------
#
# GAFI/Egmont (2018), Anexo E, item 3 ("transactions which appear strange given an individual's
# age (this is particularly relevant for underage customers)") e item 10 ("informal nominees,
# such as children, spouses, relatives"). Idade é dado pessoal: só a faixa é mostrada.


def tipologia_l14(ctx: Contexto, parametros: dict) -> list[AlertaDetectado]:
    minimo = float(parametros["l14_valor_minimo"])
    atipicas = set(parametros["l14_faixas_atipicas"])
    administracao = set(parametros["qualificacoes_administracao"])
    quadro = _quadro(ctx)
    contratos = _contratos_por_empresa(ctx)

    def regra(empresa_id: str) -> AlertaDetectado | None:
        empresa = ctx.entidades[empresa_id]
        administradores = [
            (s, a)
            for s, a in quadro.get(empresa_id, [])
            if s.tipo == "pessoa" and _qualificacao(a) in administracao
        ]
        if not administradores:
            return None

        def faixa(par: tuple[Entidade, Aresta]) -> str:
            socio, aresta = par
            return str(
                (aresta.atributos or {}).get("faixa_etaria")
                or _atr(socio).get("faixa_etaria")
                or ""
            ).strip()

        if any(faixa(par) not in atipicas for par in administradores):
            return None
        total = sum(_valor_contratado(c)[0] or 0 for c in contratos[empresa_id])
        if total <= minimo:
            return None
        socios_texto = _lista(
            [
                f"{s.rotulo} (qualificação {_qualificacao(a)}, faixa etária "
                f"{FAIXAS_ETARIAS.get(faixa((s, a)), faixa((s, a)))})"
                for s, a in administradores
            ]
        )
        motivo = (
            f"Sinal fraco. Todos os {len(administradores)} sócios-administradores de "
            f"{empresa.rotulo} (CNPJ básico {empresa.identificador}) estão em faixa etária "
            f"atípica: {socios_texto}; e a empresa soma {formatar_moeda(total)} em "
            f"{len(contratos[empresa_id])} contrato(s) públicos, acima do mínimo configurado de "
            f"{formatar_moeda(minimo)}. Pode indicar interposta pessoa informal, mas também "
            "herança, sucessão ou empresa familiar. Só a faixa etária do cadastro é usada, nunca "
            "a idade. Sozinho, este sinal não indica nada: só pesa no alerta de combinação."
        )
        return AlertaDetectado(
            tipologia="L14",
            titulo=f"Sócios-administradores em faixa etária atípica: {empresa.rotulo}",
            motivo=motivo,
            entidades=[empresa_id] + [s.id for s, _ in administradores],
            evidencias=[_evidencia_socio(ctx, s, a, empresa) for s, a in administradores]
            + [
                ctx.evidencia(
                    c.captura_id,
                    f"Contrato {c.identificador}: {_valor_contratado(c)[1]} "
                    f"{formatar_moeda(_valor_contratado(c)[0])} (PNCP)",
                )
                for c in contratos[empresa_id][:MAX_ITENS_NO_MOTIVO]
            ],
            parametros={
                "l14_valor_minimo": minimo,
                "l14_faixas_atipicas": sorted(atipicas),
                "valor_contratado": round(total, 2),
                "faixas_observadas": sorted({faixa(par) for par in administradores}),
                "sinal_fraco": True,
            },
        )

    return _por_item("L14", sorted(contratos), regra)


# ---- L15 (sinal fraco): número excessivo de atividades econômicas ------------------------------
#
# Velasco et al. (2021), seção 3.2 ("Large number of activity classifications"); GAFI/Egmont
# (2018), Anexo E, item 4 (nome que "does not indicate the activity of the company").


def tipologia_l15(ctx: Contexto, parametros: dict) -> list[AlertaDetectado]:
    minimo_cnaes = int(parametros["l15_minimo_cnaes"])
    minimo_categorias = int(parametros["l15_minimo_categorias"])
    contratos = _contratos_por_empresa(ctx)

    def regra(empresa_id: str) -> AlertaDetectado | None:
        empresa = ctx.entidades[empresa_id]
        cnaes: set[str] = set()
        origem, maior = None, 0
        for est in _estabelecimentos(empresa):
            do_estabelecimento = {str(est.get("cnae_fiscal_principal") or "").strip()} | set(
                est.get("cnaes_secundarios") or []
            )
            do_estabelecimento.discard("")
            if len(do_estabelecimento) > maior:
                maior, origem = len(do_estabelecimento), est
            cnaes |= do_estabelecimento
        categorias = {
            str(_atr(c).get("categoriaProcessoNome") or _atr(c).get("categoriaProcessoId"))
            for c in contratos[empresa_id]
            if _atr(c).get("categoriaProcessoId") is not None
        }
        muitos_cnaes = len(cnaes) >= minimo_cnaes
        muitas_categorias = len(categorias) >= minimo_categorias
        if not muitos_cnaes and not muitas_categorias:
            return None
        partes = []
        if muitos_cnaes:
            partes.append(
                f"declara {len(cnaes)} atividades econômicas (CNAE principal e secundárias) no "
                f"CNPJ, com limiar de {minimo_cnaes}"
            )
        if muitas_categorias:
            partes.append(
                f"tem contratos públicos em {len(categorias)} categorias de objeto "
                f"({', '.join(sorted(categorias))}), com limiar de {minimo_categorias}"
            )
        motivo = (
            f"Sinal fraco. A empresa contratada {empresa.rotulo} (CNPJ básico "
            f"{empresa.identificador}) {' e '.join(partes)}. Pode indicar empresa sem atividade "
            "definida, mas muitas empresas regulares registram atividades a mais por precaução. "
            "Sozinho, este sinal não indica nada: só pesa no alerta de combinação."
        )
        evidencias = []
        if muitos_cnaes and origem is not None:
            evidencias.append(
                _evidencia_estabelecimento(
                    ctx,
                    empresa,
                    origem,
                    f"CNAE principal {origem.get('cnae_fiscal_principal') or 'não informado'} e "
                    f"{len(origem.get('cnaes_secundarios') or [])} secundárias",
                )
            )
        if muitas_categorias or not evidencias:
            evidencias += [
                ctx.evidencia(
                    c.captura_id,
                    f"Contrato {c.identificador}: categoria "
                    f"{_atr(c).get('categoriaProcessoNome') or 'não informada'} (PNCP)",
                )
                for c in contratos[empresa_id][:MAX_ITENS_NO_MOTIVO]
            ]
        return AlertaDetectado(
            tipologia="L15",
            titulo=f"Muitas atividades econômicas: {empresa.rotulo}",
            motivo=motivo,
            entidades=[empresa_id],
            evidencias=evidencias,
            parametros={
                "l15_minimo_cnaes": minimo_cnaes,
                "l15_minimo_categorias": minimo_categorias,
                "cnaes": len(cnaes),
                "categorias_de_objeto": len(categorias),
                "sinal_fraco": True,
            },
        )

    return _por_item("L15", sorted(contratos), regra)


TIPOLOGIAS_LAVAGEM = {
    "L1": tipologia_l1,
    "L3": tipologia_l3,
    "L4": tipologia_l4,
    "L5": tipologia_l5,
    "L7": tipologia_l7,
    "L8": tipologia_l8,
    "L10": tipologia_l10,
    "L14": tipologia_l14,
    "L15": tipologia_l15,
}
