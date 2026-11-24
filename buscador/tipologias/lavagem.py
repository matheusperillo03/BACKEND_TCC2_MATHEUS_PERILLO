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
from buscador.seguranca import numero_finito
from buscador.tipologias import legislacao
from buscador.tipologias.horizonte2 import (
    AVISO_COINCIDENCIA,
    MAX_ITENS_NO_MOTIVO,
    _arestas,
    _atr,
    _coincide,
    _contratos_por_par,
    _ente_coincide,
    _identidade_parcial,
    _lista,
    _valor_contratado,
    tipologia_t7,
)
from buscador.tipologias.motor import (
    SITUACAO_ATIVA,
    AlertaDetectado,
    Contexto,
    _captura_do_atributo,
    _codigo_situacao,
    _data,
    _estabelecimento_matriz,
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

# Naturezas jurídicas tratadas como entidade sem fins lucrativos na L16 (tabela de domínio
# Naturezas dos dados abertos do CNPJ, arquivo de 11/2026): 3069 Fundação Privada, 3301
# Organização Social (OS) e 3999 Associação Privada. A escolha das três é decisão do autor.
NATUREZAS_SEM_FINS_LUCRATIVOS = ("3069", "3301", "3999")

# L7 (decisão do autor, depois do piloto com dados reais de 18/11/2026). O quadro dos dados abertos
# mistura sócios e administradores sem participação: em empresa pública, os "sócios" são diretores
# e o presidente, trocados a cada gestão. Por isso a regra:
# (a) não avalia naturezas jurídicas da administração pública (grupo 1xxx da tabela de domínio
#     Naturezas, arquivo de 11/2026) nem 2011 Empresa Pública e 2038 Sociedade de Economia Mista;
# (b) considera só as qualificações de sócio ou titular (tabela Qualificações, arquivo de 11/2026):
#     22 Sócio, 23 Sócio Capitalista, 24 Sócio Comanditado, 25 Sócio Comanditário, 26 Sócio de
#     Indústria, 28 Sócio-Gerente, 29 Sócio Incapaz, 30 Sócio Menor, 31 Sócio Ostensivo, 34 Titular
#     de Empresa Individual Imobiliária, 37 e 38 Sócio no Exterior (PJ e PF), 47 e 48 Sócio no
#     Brasil (PF e PJ), 49 Sócio-Administrador, 50 Empresário, 52 Sócio com Capital, 53 Sócio sem
#     Capital, 55 a 58 Sócio Comanditado ou Comanditário (exterior, incapaz), 65 a 68 Titular
#     Pessoa Física, 74 Sócio-Administrador no Exterior, 78 e 79 Titular Pessoa Jurídica.
#     Ficam de fora os administradores sem participação (05, 08, 10, 16 e outros).
L7_GRUPO_ADMINISTRACAO_PUBLICA = "1"
L7_NATUREZAS_ESTATAIS = ("2011", "2038")
L7_QUALIFICACOES_SOCIO = (
    "22", "23", "24", "25", "26", "28", "29", "30", "31", "34", "37", "38", "47", "48", "49",
    "50", "52", "53", "55", "56", "57", "58", "65", "66", "67", "68", "74", "78", "79",
)  # fmt: skip
QUALIFICACAO_NAO_INFORMADA = "00"

# Identificador de sócio (metadados do CNPJ): 1 pessoa jurídica, 2 pessoa física, 3 estrangeiro.
SOCIO_PJ, SOCIO_PF, SOCIO_ESTRANGEIRO = "1", "2", "3"

# Teto de níveis percorridos na L4 (proteção contra cadeias anômalas; ciclos são cortados antes).
L4_MAX_NIVEIS = 20

# L9 (decisão do autor): o mandato é contado de 1º de janeiro do ano seguinte ao da eleição e dura
# este número de anos. É uma aproximação: a data de posse varia com o cargo e o mandato de
# senador é de oito anos; nenhum dos dois é tratado à parte.
L9_ANOS_MANDATO = 4

# L13: tamanho de um código de subclasse da CNAE (7 dígitos) e do menor prefixo aceito na lista
# de atividades típicas de campanha (2 dígitos, a divisão).
CNAE_DIGITOS, CNAE_PREFIXO_MINIMO = 7, 2

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
        "l9_anos_mandato": L9_ANOS_MANDATO,
        "l13_dias_abertura": 180,
        "l13_cnaes_campanha": [],
        "l14_valor_minimo": 100_000.0,
        "l14_faixas_atipicas": list(FAIXAS_ATIPICAS),
        "l15_minimo_cnaes": 20,
        "l15_minimo_categorias": 4,
        "qualificacoes_administracao": list(QUALIFICACOES_ADMINISTRACAO),
        "l6_limites": legislacao.limites_de_receita(),
        "l16_dias_minimos": 180,
        "l16_naturezas_sem_fins_lucrativos": list(NATUREZAS_SEM_FINS_LUCRATIVOS),
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


def lista_de_cnaes(texto: str, nome: str) -> list[str]:
    """CNAEs separados por vírgula (configuração): o código de sete dígitos ou um prefixo de
    dois a seis (divisão, grupo, classe). A pontuação usual (``1813-0/01``) é aceita. A lista
    pode ficar vazia: é o critério desligado."""
    codigos = []
    for item in (texto or "").split(","):
        item = "".join(c for c in item if c not in " .-/")
        if not item:
            continue
        if not item.isdigit() or not CNAE_PREFIXO_MINIMO <= len(item) <= CNAE_DIGITOS:
            raise ValueError(
                f"{nome}: '{item[:20]}' não é um CNAE (de {CNAE_PREFIXO_MINIMO} a "
                f"{CNAE_DIGITOS} dígitos)."
            )
        codigos.append(item)
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


def _natureza_estatal(empresa: Entidade) -> bool:
    """Administração pública (grupo 1xxx), empresa pública ou sociedade de economia mista."""
    natureza = str(_atr(empresa).get("natureza_juridica") or "").strip()
    if not natureza.isdigit():
        return False
    natureza = natureza.zfill(4)
    return natureza.startswith(L7_GRUPO_ADMINISTRACAO_PUBLICA) or natureza in L7_NATUREZAS_ESTATAIS


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
        # Empresa estatal: o quadro traz dirigentes de mandato, não sócios (decisão do autor).
        if inicio is None or _natureza_estatal(empresa):
            return None
        quadro_atual = quadro.get(empresa_id, [])
        # Sem a qualificação não se sabe se o registro é de sócio: a regra não afirma nada, e a
        # execução avisa que a empresa ficou sem avaliação (decisão do autor).
        sem_qualificacao = sum(
            _qualificacao(a) == QUALIFICACAO_NAO_INFORMADA for _, a in quadro_atual
        )
        if sem_qualificacao:
            ctx.avisos.append(
                f"L7 não avaliada para {empresa.identificador or empresa.rotulo}: "
                f"{sem_qualificacao} registro(s) do QSA sem qualificação."
            )
            return None
        # Administrador sem participação (diretor, presidente) não é sócio nem fundador.
        socios = [(s, a) for s, a in quadro_atual if _qualificacao(a) in L7_QUALIFICACOES_SOCIO]
        if not socios:
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
            "dado aberto traz só o quadro atual: quem saiu, e quando, não aparece. Só contam as "
            "qualificações de sócio ou titular; administradores sem participação ficam de fora. "
            + AVISO_PISTA
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
                "socios_considerados": len(socios),
                "administradores_sem_participacao": len(quadro_atual) - len(socios),
                "natureza_juridica": str(_atr(empresa).get("natureza_juridica") or ""),
                "parte": "a (retrato único)",
            },
        )

    return _por_item("L7", sorted(contratos), regra)


# ---- L8: empresa recém-aberta com sócio que possivelmente é agente público ---------------------
#
# COAF, Casos e Casos (edição ANR 2021), tipologia 2.7: "Empresas recém-criadas, sem empregados
# registrados, cujos sócios têm vínculos públicos". Composição da T1 com a T7: não cria coleta.
# O trecho "sem empregados" NÃO é verificável (o dado aberto do CNPJ não tem o campo). A
# composição com a L9 (eleito) não foi feita: a L8 continua sendo só T1 com T7.


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


# ---- L9: eleito possivelmente sócio de empresa contratada pelo próprio ente (H46) ----------------
#
# FATF (2011), Laundering the Proceeds of Corruption, § 44 ("Self-dealing occurs when a PEP has a
# financial interest in an entity which does business with the state"); Velasco et al. (2021),
# seção 3.3. Decisão do autor de 23/11/2026, opção (a): o candidato e o sócio só são ligados por
# uma SUGESTÃO (seis dígitos visíveis do CPF e nome normalizado iguais), que o investigador
# confirma ou descarta; os registros nunca são fundidos.
#
# Regra: candidato eleito (as mesmas situações da T9) + sugestão candidato x sócio não descartada
# + contrato da empresa do sócio, assinado durante o mandato, com órgão do mesmo ente (o mesmo
# critério territorial da T9: UF, município e esfera do órgão no PNCP contra a unidade eleitoral
# do candidato). Fora da regra: PEP (não há coleta da rota ``/peps`` do Portal) e eleito sem CPF
# divulgado (sem os seis dígitos não há sugestão).

_ESFERAS_COM_UF = ("E", "D", "M")


def _pares_candidato_socio(ctx: Contexto) -> dict[tuple[str, str], tuple[str, str]]:
    """(candidato, pessoa) -> (situação do par, id da sugestão), sem os pares descartados. A
    sugestão existe nos dois sentidos; um sentido descartado descarta o par."""
    situacoes: dict[tuple[str, str], set[str]] = {}
    ids: dict[tuple[str, str], str] = {}
    for sugestao in ctx.sugestoes:
        a = ctx.entidades.get(sugestao.entidade_id)
        b = ctx.entidades.get(sugestao.candidata_id)
        if a is None or b is None or {a.tipo, b.tipo} != {"candidato", "pessoa"}:
            continue
        par = (a.id, b.id) if a.tipo == "candidato" else (b.id, a.id)
        situacoes.setdefault(par, set()).add(sugestao.situacao or "pendente")
        if a.tipo == "candidato" or par not in ids:
            ids[par] = sugestao.id
    return {
        par: ("confirmado" if "confirmado" in marcas else "pendente", ids[par])
        for par, marcas in situacoes.items()
        if "descartado" not in marcas
    }


def _ente_indefinido(orgao: Entidade) -> bool:
    """O órgão não tem o que a regra do ente precisa: a esfera, ou a UF de órgão não federal."""
    esfera = str(_atr(orgao).get("esfera") or "").upper()
    if not esfera:
        return True
    return esfera in _ESFERAS_COM_UF and not normalizar_nome(_atr(orgao).get("uf"))


def tipologia_l9(ctx: Contexto, parametros: dict) -> list[AlertaDetectado]:
    eleito = {normalizar_nome(s) for s in parametros["t9_situacoes_eleito"]}
    anos = int(parametros["l9_anos_mandato"])
    eleitos = [
        e
        for e in ctx.entidades.values()
        if e.tipo == "candidato" and normalizar_nome(_atr(e).get("situacao_turno")) in eleito
    ]
    sem_cpf = sum(1 for e in eleitos if not _atr(e).get("cpf_mascarado"))
    if sem_cpf:
        ctx.avisos.append(
            f"L9: {sem_cpf} candidato(s) eleito(s) sem CPF divulgado no arquivo do TSE: sem os "
            "seis dígitos não há sugestão de ligação com sócio, e a regra não os avalia."
        )
    empresas_do_socio: dict[str, list[tuple[Entidade, Aresta]]] = {}
    for a in _arestas(ctx, "socio_de"):
        empresa = ctx.entidades.get(a.destino_id)
        if empresa is not None and empresa.tipo == "empresa":
            empresas_do_socio.setdefault(a.origem_id, []).append((empresa, a))
    contratos_por_par = _contratos_por_par(ctx)
    orgaos_por_empresa: dict[str, set[str]] = {}
    for empresa_id, orgao_id in contratos_por_par:
        orgaos_por_empresa.setdefault(empresa_id, set()).add(orgao_id)

    # (candidato, sócio, empresa, órgão) -> o que a regra usou.
    grupos: dict[tuple[str, str, str, str], dict] = {}
    for (candidato_id, socio_id), (situacao, sugestao_id) in _pares_candidato_socio(ctx).items():
        candidato = ctx.entidades[candidato_id]
        ano = str(_atr(candidato).get("ano") or "")
        if normalizar_nome(_atr(candidato).get("situacao_turno")) not in eleito:
            continue
        if not ano.isdigit():
            continue
        inicio = date(int(ano) + 1, 1, 1)
        fim = date(int(ano) + anos, 12, 31)
        for empresa, aresta in empresas_do_socio.get(socio_id, []):
            for orgao_id in sorted(orgaos_por_empresa.get(empresa.id, ())):
                orgao = ctx.entidades.get(orgao_id)
                if orgao is None:
                    continue
                no_mandato = [
                    c
                    for c in contratos_por_par[(empresa.id, orgao_id)]
                    if (assinatura := _data(_atr(c).get("data_assinatura"))) is not None
                    and inicio <= assinatura <= fim
                ]
                if not no_mandato:
                    continue
                ente = _ente_coincide(candidato, orgao)
                if ente is None:
                    if _ente_indefinido(orgao):
                        ctx.avisos.append(
                            f"L9 não avaliada para o órgão {orgao.identificador or orgao.rotulo}: "
                            "o cadastro coletado não traz a UF ou a esfera, e sem elas não dá "
                            "para dizer se é o ente do candidato eleito."
                        )
                    continue
                grupos[(candidato_id, socio_id, empresa.id, orgao_id)] = {
                    "contratos": sorted(no_mandato, key=lambda c: c.identificador),
                    "aresta": aresta,
                    "ente": ente,
                    "situacao": situacao,
                    "sugestao_id": sugestao_id,
                    "inicio": inicio,
                    "fim": fim,
                }

    def regra(item: tuple[tuple[str, str, str, str], dict]) -> AlertaDetectado:
        (candidato_id, socio_id, empresa_id, orgao_id), grupo = item
        candidato, socio = ctx.entidades[candidato_id], ctx.entidades[socio_id]
        empresa, orgao = ctx.entidades[empresa_id], ctx.entidades[orgao_id]
        c = _atr(candidato)
        cpf, nome = _identidade_parcial(socio)
        contratos = grupo["contratos"]
        total = sum(_valor_contratado(k)[0] or 0 for k in contratos)
        inicio, fim = grupo["inicio"].isoformat(), grupo["fim"].isoformat()
        confirmada = grupo["situacao"] == "confirmado"
        ligacao = (
            "A ligação entre o candidato e o sócio foi confirmada pelo investigador na sugestão "
            "de resolução"
            if confirmada
            else "A ligação entre o candidato e o sócio é uma sugestão pendente: ainda não foi "
            "confirmada nem descartada pelo investigador"
        )
        contratos_texto = _lista(
            [
                f"{k.identificador} ({formatar_moeda(_valor_contratado(k)[0])}, assinado em "
                f"{formatar_data(_atr(k).get('data_assinatura'))})"
                for k in contratos
            ]
        )
        motivo = (
            f"Possível coincidência entre eleito e sócio: {candidato.rotulo} (candidato a "
            f"{c.get('cargo') or 'cargo não informado'} em {c.get('ano')}, "
            f"{c.get('nm_ue') or c.get('sg_ue') or ''}/{c.get('uf') or ''}, situação "
            f"'{c.get('situacao_turno')}') e um sócio de {empresa.rotulo} (CNPJ básico "
            f"{empresa.identificador}) têm o mesmo nome normalizado ('{nome}') e os mesmos seis "
            f"dígitos visíveis do CPF ({cpf}). No período tratado como mandato, de "
            f"{formatar_data(inicio)} a {formatar_data(fim)} ({anos} anos a partir de 1º de "
            f"janeiro do ano seguinte ao da eleição), a empresa assinou {len(contratos)} "
            f"contrato(s) com {orgao.rotulo}, órgão do mesmo ente do eleito ({grupo['ente']}): "
            f"{contratos_texto}. {ligacao}. Seis dígitos e o nome não identificam uma pessoa: os "
            "dois registros continuam separados (não foram fundidos) e a confirmação exige "
            "fonte com o CPF completo. Indício a verificar: o vínculo com o ente é territorial "
            "(unidade eleitoral do candidato e localização e esfera do órgão), o período do "
            "mandato é aproximado e nada disso prova que o eleito tenha influído na contratação."
        )
        evidencias = [
            ctx.evidencia(
                _captura_do_atributo(ctx, candidato, "situacao_turno", "consulta_cand"),
                f"Candidato {candidato.rotulo} ({c.get('cpf_mascarado')}): {c.get('cargo')} em "
                f"{c.get('ano')}, situação '{c.get('situacao_turno')}' (TSE, candidatos)",
            ),
            ctx.evidencia(
                grupo["aresta"].captura_id,
                f"Sócio {socio.rotulo} ({cpf}) de {empresa.rotulo} (dados abertos do CNPJ, Sócios)",
            ),
        ] + [
            ctx.evidencia(
                k.captura_id,
                f"Contrato {k.identificador} de {empresa.rotulo} com {orgao.rotulo}, assinado em "
                f"{formatar_data(_atr(k).get('data_assinatura'))} (PNCP)",
            )
            for k in contratos[:MAX_ITENS_NO_MOTIVO]
        ]
        return AlertaDetectado(
            tipologia="L9",
            titulo=(
                f"Possível coincidência: eleito {candidato.rotulo} e sócio de {empresa.rotulo}, "
                f"contratada por {orgao.rotulo} durante o mandato"
            ),
            motivo=motivo,
            entidades=[candidato_id, socio_id, empresa_id, orgao_id] + [k.id for k in contratos],
            evidencias=evidencias,
            discriminador="|".join(item[0]),
            parametros={
                "l9_anos_mandato": anos,
                "mandato_inicio": inicio,
                "mandato_fim": fim,
                "sugestao_id": grupo["sugestao_id"],
                "sugestao_situacao": grupo["situacao"],
                "identidade_confirmada": False,
                "cpf_descaracterizado": cpf,
                "contratos": len(contratos),
                "valor_contratado": round(total, 2),
                "ente": grupo["ente"],
                "situacao_candidato": c.get("situacao_turno"),
                "ano_eleicao": c.get("ano"),
            },
        )

    return _por_item("L9", sorted(grupos.items(), key=lambda g: g[0]), regra)


# ---- L13: despesa de campanha com fornecedor de perfil incompatível (H52) -----------------------
#
# Carta Circular BCB nº 4.001/2020, XIV, d ("transferências, a partir das contas de
# candidatos, para pessoas naturais ou jurídicas cuja atividade não guarde aparente relação com
# contas de campanha"). É o fluxo inverso da T9: candidato -> empresa, pela aresta
# ``despesa_de_campanha`` (despesas contratadas do TSE, só fornecedor com CNPJ).
#
# Dois critérios, um basta (limiares e lista: decisão do autor):
# (a) o fornecedor iniciou a atividade menos de ``l13_dias_abertura`` dias antes da despesa (o
#     mesmo cálculo da T1, com limiar próprio);
# (b) o CNAE do fornecedor está fora da lista ``l13_cnaes_campanha``. A lista vem VAZIA por padrão
#     e, vazia, o critério fica desligado: quais atividades são "típicas de campanha" é decisão do
#     autor ainda não tomada. Usa o CNAE principal do cadastro do CNPJ quando a empresa foi
#     coletada; senão, o que a prestação de contas informa.
# Fora da regra: os "sinais de fachada" da ficha (fornecedor que dispara a L1 ou a L6).


def _valor(aresta: Aresta) -> float:
    return numero_finito((aresta.atributos or {}).get("valor")) or 0.0


def tipologia_l13(ctx: Contexto, parametros: dict) -> list[AlertaDetectado]:
    limite = int(parametros["l13_dias_abertura"])
    tipicos = sorted({str(c) for c in parametros["l13_cnaes_campanha"]})
    grupos: dict[tuple[str, str], list[Aresta]] = {}
    for a in _arestas(ctx, "despesa_de_campanha"):
        candidato, empresa = ctx.entidades.get(a.origem_id), ctx.entidades.get(a.destino_id)
        if candidato is not None and empresa is not None and empresa.tipo == "empresa":
            grupos.setdefault((candidato.id, empresa.id), []).append(a)
    if not grupos:
        return []
    if not tipicos:
        ctx.avisos.append(
            "L13: o critério do CNAE está desligado, porque a lista de atividades típicas de "
            "campanha (L13_CNAES_CAMPANHA) está vazia; defini-la é decisão do autor ainda não "
            "tomada. Só a abertura recente do fornecedor foi avaliada."
        )
    sem_cadastro: set[str] = set()
    faltas = {"sem_data": 0, "sem_cnae": 0}

    def regra(item: tuple[tuple[str, str], list[Aresta]]) -> AlertaDetectado | None:
        (candidato_id, empresa_id), despesas = item
        candidato, empresa = ctx.entidades[candidato_id], ctx.entidades[empresa_id]
        despesas = sorted(despesas, key=lambda a: ((a.atributos or {}).get("data") or "", a.id))
        inicio = _inicio(empresa)
        if inicio is None:
            sem_cadastro.add(empresa_id)
        cnae_cadastro = str(_atr(empresa).get("cnae_fiscal_principal") or "").strip()
        origem_cnae = "cadastro_cnpj" if cnae_cadastro else "prestacao_de_contas"
        marcadas: list[tuple[Aresta, int | None, str]] = []  # (despesa, dias, CNAE fora)
        for a in despesas:
            atributos = a.atributos or {}
            dia = _data(atributos.get("data"))
            dias = (dia - inicio).days if inicio is not None and dia is not None else None
            if inicio is not None and dia is None:
                faltas["sem_data"] += 1
            fora = ""
            if tipicos:
                cnae = cnae_cadastro or str(atributos.get("cnae_fornecedor") or "").strip()
                if not cnae:
                    faltas["sem_cnae"] += 1
                elif not any(cnae.startswith(prefixo) for prefixo in tipicos):
                    fora = cnae
            recente = dias if dias is not None and dias < limite else None
            if recente is not None or fora:
                marcadas.append((a, recente, fora))
        if not marcadas:
            return None
        recentes = [dias for _, dias, _ in marcadas if dias is not None]
        cnaes_fora = sorted({fora for _, _, fora in marcadas if fora})
        sinais = sorted(
            (["recem_aberta"] if recentes else []) + (["cnae_fora_da_lista"] if cnaes_fora else [])
        )
        total = sum(_valor(a) for a, _, _ in marcadas)
        despesas_texto = _lista(
            [
                f"{formatar_moeda(_valor(a))} em {formatar_data((a.atributos or {}).get('data'))}"
                for a, _, _ in marcadas
            ]
        )
        partes = []
        if recentes:
            partes.append(
                f"a empresa iniciou a atividade em {formatar_data(inicio.isoformat())}, e a "
                f"despesa mais próxima da abertura foi contratada "
                + (
                    f"{min(recentes)} dias depois"
                    if min(recentes) >= 0
                    else f"{abs(min(recentes))} dias antes do início da atividade"
                )
                + f" (limiar configurado: {limite} dias)"
            )
        if cnaes_fora:
            fonte_cnae = (
                "CNAE principal do cadastro do CNPJ"
                if cnae_cadastro
                else "CNAE informado na prestação de contas, porque a empresa não foi coletada "
                "no CNPJ"
            )
            partes.append(
                f"a atividade do fornecedor ({fonte_cnae}: {', '.join(cnaes_fora)}) está fora "
                f"da lista configurada de atividades típicas de campanha ({', '.join(tipicos)})"
            )
        motivo = (
            f"A campanha de {candidato.rotulo} (eleição de {_atr(candidato).get('ano')}) "
            f"contratou {len(marcadas)} despesa(s), no total de {formatar_moeda(total)}, com "
            f"{empresa.rotulo} (CNPJ básico {empresa.identificador}): {despesas_texto}. "
            f"Sinal(is): {'; '.join(partes)}. O CNAE declarado nem sempre reflete o serviço "
            "prestado, e empresa nova pode fornecer de forma regular; os valores são os da "
            "despesa contratada na prestação de contas ao TSE, não os pagos. " + AVISO_PISTA
        )
        evidencias = []
        matriz = _estabelecimento_matriz(empresa)
        if recentes and matriz is not None:
            evidencias.append(
                _evidencia_estabelecimento(
                    ctx,
                    empresa,
                    matriz,
                    f"data de início da atividade {formatar_data(inicio.isoformat())}",
                )
            )
        if cnaes_fora and cnae_cadastro:
            evidencias.append(
                _evidencia_estabelecimento(
                    ctx, empresa, matriz or {}, f"CNAE principal {cnae_cadastro}"
                )
            )
        for a, _, fora in marcadas[:MAX_ITENS_NO_MOTIVO]:
            atributos = a.atributos or {}
            informado = (
                f"; CNAE informado na prestação de contas: {fora}"
                if fora and not cnae_cadastro
                else ""
            )
            evidencias.append(
                ctx.evidencia(
                    a.captura_id,
                    f"Despesa contratada de campanha: {candidato.rotulo} com {empresa.rotulo} "
                    f"(CNPJ {atributos.get('cnpj_fornecedor') or empresa.identificador}), "
                    f"{formatar_moeda(_valor(a))} em {formatar_data(atributos.get('data'))}"
                    f"{informado} (TSE, prestação de contas)",
                )
            )
        return AlertaDetectado(
            tipologia="L13",
            titulo=(
                f"Despesa de campanha de {candidato.rotulo} com fornecedor de perfil "
                f"incompatível: {empresa.rotulo}"
            ),
            motivo=motivo,
            entidades=[candidato_id, empresa_id],
            evidencias=evidencias,
            parametros={
                "l13_dias_abertura": limite,
                "l13_cnaes_campanha": tipicos,
                "sinais": sinais,
                "total_despesas": round(total, 2),
                "despesas": len(marcadas),
                "despesas_do_par": len(despesas),
                "menor_intervalo_dias": min(recentes) if recentes else None,
                "data_inicio_atividade": inicio.isoformat() if inicio is not None else None,
                "cnae_usado": ", ".join(cnaes_fora) if cnaes_fora else None,
                "origem_do_cnae": origem_cnae if cnaes_fora else None,
                "ano_eleicao": _atr(candidato).get("ano"),
            },
        )

    alertas = _por_item("L13", sorted(grupos.items(), key=lambda g: g[0]), regra)
    if sem_cadastro:
        ctx.avisos.append(
            f"L13: {len(sem_cadastro)} fornecedor(es) de campanha sem data de início de "
            "atividade (empresa não coletada no CNPJ): o critério de abertura recente não foi "
            "avaliado para eles."
        )
    if faltas["sem_data"]:
        ctx.avisos.append(
            f"L13: {faltas['sem_data']} despesa(s) sem data: o critério de abertura recente não "
            "foi avaliado para elas."
        )
    if faltas["sem_cnae"]:
        ctx.avisos.append(
            f"L13: {faltas['sem_cnae']} despesa(s) sem CNAE do fornecedor (nem no cadastro do "
            "CNPJ nem na prestação de contas): o critério do CNAE não foi avaliado para elas."
        )
    return alertas


# ---- L10: vínculo com empresa sancionada -------------------------------------------------------
#
# GAFI/Egmont (2018), Anexo E, item 3 ("have previously been prohibited from holding a
# directorship role in a company"); Velasco et al. (2021), seção 3 ("Blacklisting", no nível da
# empresa e da pessoa); Lei nº 12.846/2013, art. 5º, IV, e (criar pessoa jurídica de modo
# fraudulento para contratar). Extensão da T6, que só olha a própria contratada.

CADASTROS_COM_PERIODO = ("CEIS", "CNEP")
# O CEPIM (entidades privadas sem fins lucrativos impedidas) não traz período de sanção: o registro
# é tratado como impedimento em vigor na data da coleta, e o alerta diz isso.
CADASTRO_SEM_PERIODO = "CEPIM"


def _sancoes_por_entidade(ctx: Contexto) -> dict[str, list[Entidade]]:
    sancoes: dict[str, list[Entidade]] = {}
    for a in _arestas(ctx, "sancionada_em"):
        sancao = ctx.entidades.get(a.destino_id)
        if sancao is not None and sancao.tipo == "sancao":
            sancoes.setdefault(a.origem_id, []).append(sancao)
    return sancoes


def _vigente(sancao: Entidade, dia: date) -> bool:
    """A sanção cobre a data? CEIS e CNEP têm período (sem data de início, não é aferível); o
    CEPIM não tem período e conta como impedimento em vigor."""
    atributos = _atr(sancao)
    if atributos.get("cadastro") == CADASTRO_SEM_PERIODO:
        return True
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
        else "sem período no cadastro"
        + (
            f" (data de referência {formatar_data(s.get('data_referencia'))})"
            if s.get("data_referencia")
            else ""
        )
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
            # Com sanção própria vigente (CEIS ou CNEP), o caso é da T6.
            if any(
                _atr(s).get("cadastro") in CADASTROS_COM_PERIODO and _vigente(s, assinatura)
                for s in sancoes.get(contratada_id, [])
            ):
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
                "parte": "empresa sancionada (CEIS, CNEP ou CEPIM)",
                "vinculo_por_socio": por_socio,
                "vinculo_por_endereco": any(v.startswith("endereco:") for v in caso["vinculos"]),
                "sancoes": len(sancoes),
                "contratos": len(contratos),
                "identidade_confirmada": False if por_socio else None,
            },
        )

    return _por_item("L10", sorted(casos.items(), key=lambda i: i[0]), regra)


def tipologia_l10(ctx: Contexto, parametros: dict) -> list[AlertaDetectado]:
    """As duas partes da L10: empresa sancionada (CEIS, CNEP e CEPIM) e pessoa expulsa (CEAF)."""
    return _l10_sancionadas(ctx, parametros) + _l10_expulsos(ctx, parametros)


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


# ---- L6: porte ou regime incompatível com o valor contratado -----------------------------------
#
# Velasco et al. (2021), seção 3.2: "Incompatible company size: if a self-employed person
# (Microempreendedor Individual [...]) won public contracts above income limits defined by the
# Brazilian legislation"; Carta Circular BCB nº 4.001/2020, III, n. Os limites são LEGAIS (LC nº
# 123/2006 e LC nº 238/2026) e estão em ``legislacao`` (``LIMITES_MEI``, ``LIMITES_ME`` e
# ``LIMITES_EPP``).
# Complementa a T3, que compara com o capital social (declarado e frágil).
#
# Decisões do autor:
# - o valor contratado é o valor inicial do contrato (ou o global, dizendo qual) e é somado no
#   ano da assinatura: contrato plurianual não é receita de um só ano (falso positivo esperado);
# - o regime é o da data de cada assinatura: MEI pela opção e pelas datas da tabela Simples; fora
#   do MEI, o porte do cadastro (01 microempresa, 03 pequeno porte), que é o ATUAL.
#
# O que vem da lei (texto e dispositivos em ``legislacao``):
# - o valor do ano é comparado com o limite daquele ano-calendário, sem tolerância: a lei não
#   prevê margem em que o excesso deixe de ser excesso;
# - no ano de início de atividade o limite é proporcional aos meses, contada a fração de mês como
#   mês inteiro: MEI, art. 18-A, § 2º; microempresa e pequeno porte, art. 3º, § 2º;
# - a faixa de 20% só muda a data dos efeitos do excesso (art. 18-A, § 7º, III e IV; art. 3º,
#   §§ 9º, 9º-A, 10 e 12). O alerta dispara com qualquer excesso e diz em qual faixa caiu;
# - ano anterior aos limites conferidos (antes de 2012) não é avaliado, e o alerta da mesma
#   empresa em outro ano diz quais anos ficaram de fora.

_NOMES_REGIME = {
    "mei": "microempreendedor individual (MEI)",
    "me": "microempresa",
    "epp": "empresa de pequeno porte",
}


def _regime_na_data(empresa: Entidade, dia: date) -> str | None:
    atributos = _atr(empresa)
    opcao = _data(atributos.get("data_opcao_mei"))
    exclusao = _data(atributos.get("data_exclusao_mei"))
    if opcao is not None:
        if opcao <= dia and (exclusao is None or dia < exclusao):
            return "mei"
    elif str(atributos.get("opcao_mei") or "").upper() == "S" and exclusao is None:
        return "mei"
    porte = str(atributos.get("porte") or "").strip().zfill(2)
    return {legislacao.PORTE_ME: "me", legislacao.PORTE_EPP: "epp"}.get(porte)


def tipologia_l6(ctx: Contexto, parametros: dict) -> list[AlertaDetectado]:
    limites: dict = parametros["l6_limites"]
    nao_avaliados: dict[str, set[int]] = {}
    grupos: dict[tuple[str, int, str], list[tuple[Entidade, float, str]]] = {}
    for empresa_id, contratos in sorted(_contratos_por_empresa(ctx).items()):
        empresa = ctx.entidades[empresa_id]
        for contrato in contratos:
            assinatura = _data(_atr(contrato).get("data_assinatura"))
            valor, base = _valor_contratado(contrato)
            if assinatura is None or valor is None or valor <= 0:
                continue
            regime = _regime_na_data(empresa, assinatura)
            if regime is None:
                continue
            if legislacao.limite_de_receita(limites, regime, assinatura.year, None) is None:
                nao_avaliados.setdefault(empresa_id, set()).add(assinatura.year)
            else:
                grupos.setdefault((empresa_id, assinatura.year, regime), []).append(
                    (contrato, valor, base)
                )

    def regra(item) -> AlertaDetectado | None:
        (empresa_id, ano, regime), itens = item
        empresa = ctx.entidades[empresa_id]
        limite = legislacao.limite_de_receita(limites, regime, ano, _inicio(empresa))
        if limite is None:
            return None  # ano anterior aos limites conferidos; ver ``nao_avaliados``
        soma = round(sum(v for _, v, _ in itens), 2)
        if soma <= limite["limite_legal"]:
            return None
        nome = _NOMES_REGIME[regime]
        efeito = legislacao.efeito_do_excesso(
            regime, soma, limite["limite_legal"], limite["meses"] is not None
        )
        excesso = round(soma - limite["limite_legal"], 2)
        atributos = _atr(empresa)
        if regime == "mei":
            situacao = (
                f"era MEI nas datas de assinatura (opção pelo MEI em "
                f"{formatar_data(atributos.get('data_opcao_mei'))}"
                + (
                    f", exclusão em {formatar_data(atributos.get('data_exclusao_mei'))}"
                    if atributos.get("data_exclusao_mei")
                    else ", sem exclusão registrada"
                )
                + "; tabela Simples do CNPJ)"
            )
        else:
            situacao = (
                f"tem porte de {_NOMES_REGIME[regime]} no cadastro do CNPJ (porte "
                f"{str(atributos.get('porte') or '').zfill(2)}, que é o porte ATUAL)"
            )
        inicio_texto = formatar_data(atributos.get("data_inicio_atividade"))
        if limite["meses"] is None:
            limite_texto = (
                f"O limite de receita bruta anual de {_NOMES_REGIME[regime]} em {ano} é de "
                f"{formatar_moeda(limite['limite_legal'])} ({limite['fonte']})."
            )
        else:
            conta = (
                f"{formatar_moeda(limite['mensal_inicio'])} por mês"
                if regime == "mei"
                else f"{limite['meses']}/12 do limite anual"
            )
            limite_texto = (
                f"O limite de receita bruta anual de {_NOMES_REGIME[regime]} em {ano} é de "
                f"{formatar_moeda(limite['limite_anual'])} ({limite['fonte']}). Como {ano} é o "
                f"ano de início de atividade (início em {inicio_texto}), "
                f"vale o limite proporcional a {limite['meses']} "
                f"{'mês' if limite['meses'] == 1 else 'meses'}, contada a fração de mês como mês "
                f"inteiro ({conta}): {formatar_moeda(limite['limite_legal'])} "
                f"({limite['dispositivo_proporcional']})."
            )
        acima_da_epp = ""
        if regime == "me":
            da_epp = legislacao.limite_de_receita(limites, "epp", ano, _inicio(empresa))
            if da_epp is not None and soma > da_epp["limite_legal"]:
                acima_da_epp = (
                    f" A soma também passa do limite de empresa de pequeno porte, "
                    f"{formatar_moeda(da_epp['limite_legal'])} (art. 3º, II)."
                )
        fora = sorted(nao_avaliados.get(empresa_id, ()))
        fora_texto = (
            f" Não avaliados: contratos de {_lista([str(a) for a in fora])}, porque os limites "
            f"anteriores a {limites['primeiro_ano']} não foram conferidos no texto legal."
            if fora
            else ""
        )
        contratos_texto = _lista(
            [
                f"{c.identificador} ({formatar_moeda(v)}, {b}, assinado em "
                f"{formatar_data(_atr(c).get('data_assinatura'))})"
                for c, v, b in itens
            ]
        )
        motivo = (
            f"A empresa {empresa.rotulo} (CNPJ básico {empresa.identificador}) {situacao} e "
            f"assinou em {ano} contratos públicos que somam {formatar_moeda(soma)}: "
            f"{contratos_texto}. {limite_texto} O valor contratado no ano passa do limite em "
            f"{formatar_moeda(excesso)}. Se fosse receita bruta do ano, {efeito['efeito']} "
            f"({efeito['dispositivo']}).{acima_da_epp}{fora_texto} Indício a verificar: valor "
            "contratado não é receita do ano (contrato plurianual é pago ao longo da vigência), "
            "o porte do cadastro pode estar desatualizado e a exclusão do regime pode ter sido "
            "registrada depois. " + AVISO_PISTA
        )
        evidencias = [
            ctx.evidencia(
                _captura_do_atributo(
                    ctx, empresa, "opcao_mei" if regime == "mei" else "porte", "empresas"
                ),
                f"Empresa {empresa.rotulo}: "
                + (
                    f"opção pelo MEI '{atributos.get('opcao_mei') or ''}', desde "
                    f"{formatar_data(atributos.get('data_opcao_mei'))} (dados abertos do CNPJ, "
                    "Simples)"
                    if regime == "mei"
                    else f"porte {str(atributos.get('porte') or '').zfill(2)} (dados abertos do "
                    "CNPJ, Empresas)"
                ),
            )
        ] + [
            ctx.evidencia(
                c.captura_id,
                f"Contrato {c.identificador}: {b} {formatar_moeda(v)}, assinatura em "
                f"{formatar_data(_atr(c).get('data_assinatura'))} (PNCP)",
            )
            for c, v, b in itens[:MAX_ITENS_NO_MOTIVO]
        ]
        return AlertaDetectado(
            tipologia="L6",
            titulo=(
                f"{nome[0].upper()}{nome[1:]} com {formatar_moeda(soma)} contratados em "
                f"{ano}: {empresa.rotulo}"
            ),
            motivo=motivo,
            entidades=[empresa_id] + [c.id for c, _, _ in itens],
            evidencias=evidencias,
            discriminador=f"{ano}|{regime}",
            parametros={
                "regime": regime,
                "ano": ano,
                "soma_contratada": round(soma, 2),
                "limite_anual": limite["limite_anual"],
                "limite_legal": limite["limite_legal"],
                "fonte_limite": limite["fonte"],
                "meses_no_ano_de_inicio": limite["meses"],
                "dispositivo_proporcional": limite["dispositivo_proporcional"],
                "excesso": excesso,
                "faixa_excesso": efeito["faixa"],
                "dispositivo_excesso": efeito["dispositivo"],
                "anos_nao_avaliados": fora,
                "contratos": len(itens),
            },
        )

    return _por_item("L6", sorted(grupos.items(), key=lambda g: g[0]), regra)


# ---- L10 (parte CEAF): sócio que possivelmente coincide com pessoa expulsa ---------------------
#
# GAFI/Egmont (2018), Anexo E, item 3. O CEAF (Cadastro de Expulsões da Administração Federal)
# traz o CPF já descaracterizado: a comparação é por seis dígitos e nome, sem fusão (RF02).


def _l10_expulsos(ctx: Contexto, parametros: dict) -> list[AlertaDetectado]:
    expulsoes: dict[str, list[Entidade]] = {}
    for pessoa_id, sancoes in _sancoes_por_entidade(ctx).items():
        pessoa = ctx.entidades.get(pessoa_id)
        if pessoa is not None and pessoa.tipo == "pessoa":
            ceaf = [s for s in sancoes if _atr(s).get("cadastro") == "CEAF"]
            if ceaf:
                expulsoes[pessoa_id] = ceaf
    if not expulsoes:
        return []
    alvos = set(_contratos_por_empresa(ctx)) | {a.origem_id for a in _arestas(ctx, "convenente_de")}
    quadro = _quadro(ctx)
    casos = [
        (ctx.entidades[empresa_id], socio, aresta, ctx.entidades[expulso_id])
        for empresa_id in sorted(alvos)
        for socio, aresta in quadro.get(empresa_id, [])
        if socio.tipo == "pessoa"
        for expulso_id in sorted(expulsoes)
        if _coincide(socio, ctx.entidades[expulso_id])
    ]

    def regra(caso) -> AlertaDetectado:
        empresa, socio, aresta, expulso = caso
        cpf, nome = _identidade_parcial(socio)
        registros = sorted(expulsoes[expulso.id], key=lambda s: s.identificador)
        motivo = (
            f"Possível coincidência entre sócio e pessoa expulsa da administração federal: "
            f"{socio.rotulo} (CPF descaracterizado {cpf}) é sócio de {empresa.rotulo} (CNPJ "
            f"básico {empresa.identificador}), que tem contrato ou convênio público no caso; e há "
            f"no CEAF um registro de mesmo nome normalizado ('{nome}') e mesmos dígitos visíveis "
            f"do CPF: {_lista([_descricao_sancao(s) for s in registros])}. "
            f"{AVISO_COINCIDENCIA} {AVISO_PISTA}"
        )
        return AlertaDetectado(
            tipologia="L10",
            titulo=f"Possível coincidência: sócio de {empresa.rotulo} e pessoa do CEAF",
            motivo=motivo,
            entidades=[empresa.id, socio.id, expulso.id] + [s.id for s in registros],
            evidencias=[_evidencia_socio(ctx, socio, aresta, empresa)]
            + [
                ctx.evidencia(
                    s.captura_id,
                    f"{expulso.rotulo} ({cpf}): {_descricao_sancao(s)} (Portal da Transparência, "
                    "CEAF)",
                )
                for s in registros
            ],
            discriminador=f"ceaf|{empresa.id}|{socio.id}|{expulso.id}",
            parametros={
                "parte": "pessoa expulsa (CEAF)",
                "criterio": "seis dígitos visíveis do CPF e nome normalizado iguais",
                "cpf_descaracterizado": cpf,
                "nome_normalizado": nome,
                "registros_ceaf": len(registros),
                "identidade_confirmada": False,
            },
        )

    return _por_item("L10", casos, regra)


# ---- L16: entidade sem fins lucrativos convenente com sinais de fachada ------------------------
#
# COAF, Casos e Casos (edição ANR 2021), tipologias 2.1 (entidade que "não realiza os serviços"),
# 2.8 ("dirigentes da organização criam várias empresas com atividade no mesmo segmento de
# atuação [...] sediadas em um mesmo endereço") e 2.9 (subcontratação de empresas de pessoas
# ligadas aos dirigentes); Velasco et al. (2021), seção 5 ("farms of shell companies related to
# social organizations"); GAFI (2011), § 47 ("non-profit entities").
#
# Conferido em 17/11/2026 nos dados abertos do CNPJ (um arquivo de Sócios, só contagens): o quadro
# traz os dirigentes de associações e fundações, com as qualificações 16 (Presidente), 10
# (Diretor), 05 (Administrador), 18 (Secretário) e 33 (Tesoureiro). O sinal com eleito ou PEP
# depende da L9 e não entra; o repasse da entidade às empresas é dado bancário (RF10).


def tipologia_l16(ctx: Contexto, parametros: dict) -> list[AlertaDetectado]:
    dias_minimos = int(parametros["l16_dias_minimos"])
    naturezas = set(parametros["l16_naturezas_sem_fins_lucrativos"])
    quadro = _quadro(ctx)
    sancoes = _sancoes_por_entidade(ctx)
    convenios: dict[str, list[Entidade]] = {}
    for a in _arestas(ctx, "convenente_de"):
        convenio = ctx.entidades.get(a.destino_id)
        if convenio is not None and convenio.tipo == "convenio":
            convenios.setdefault(a.origem_id, []).append(convenio)
    concedentes: dict[str, set[str]] = {}
    for a in _arestas(ctx, "concedente_de"):
        concedentes.setdefault(a.destino_id, set()).add(a.origem_id)
    servidores: dict[str, list[tuple[Entidade, Aresta]]] = {}
    for a in _arestas(ctx, "servidor_de"):
        servidor = ctx.entidades.get(a.origem_id)
        if servidor is not None:
            servidores.setdefault(a.destino_id, []).append((servidor, a))
    empresas_do_socio: dict[str, list[tuple[str, Aresta]]] = {}
    for empresa_id, socios in quadro.items():
        for socio, aresta in socios:
            empresas_do_socio.setdefault(socio.id, []).append((empresa_id, aresta))

    def regra(entidade_id: str) -> AlertaDetectado | None:
        entidade = ctx.entidades.get(entidade_id)
        if entidade is None or entidade.tipo != "empresa":
            return None
        atributos = _atr(entidade)
        natureza = str(atributos.get("natureza_juridica") or "").strip()
        # Sem a natureza jurídica (entidade que só veio do Portal), não se afirma que é sem fins
        # lucrativos: a regra não dispara.
        if natureza not in naturezas:
            return None
        lista = sorted(convenios[entidade_id], key=lambda c: c.identificador)
        vigencias = [d for c in lista if (d := _data(_atr(c).get("data_inicio_vigencia")))]
        inicio = _inicio(entidade)
        dirigentes = [(s, a) for s, a in quadro.get(entidade_id, []) if s.tipo == "pessoa"]
        sinais: dict[str, str] = {}
        evidencias = []
        entidades = [entidade_id] + [c.id for c in lista]

        # (a) aberta pouco antes do início da vigência
        recentes = [
            (c, (d - inicio).days)
            for c in lista
            if inicio is not None
            and (d := _data(_atr(c).get("data_inicio_vigencia"))) is not None
            and (d - inicio).days < dias_minimos
        ]
        if recentes:
            sinais["a"] = (
                f"iniciou a atividade em {formatar_data(inicio.isoformat())}, menos de "
                f"{dias_minimos} dias antes do início da vigência de "
                + _lista([f"{c.identificador} ({dias} dias)" for c, dias in recentes])
            )
            est = next(iter(_estabelecimentos(entidade)), None)
            if est is not None:
                evidencias.append(
                    _evidencia_estabelecimento(
                        ctx,
                        entidade,
                        est,
                        f"início da atividade em {formatar_data(inicio.isoformat())}",
                    )
                )

        # (b) registro no CEPIM
        cepim = [s for s in sancoes.get(entidade_id, []) if _atr(s).get("cadastro") == "CEPIM"]
        if cepim:
            sinais["b"] = "tem registro no CEPIM (entidades privadas sem fins lucrativos " + (
                "impedidas): " + _lista([_descricao_sancao(s) for s in cepim])
            )
            entidades += [s.id for s in cepim]
            evidencias += [
                ctx.evidencia(
                    s.captura_id,
                    f"{entidade.rotulo}: {_descricao_sancao(s)} (Portal da Transparência, CEPIM)",
                )
                for s in cepim
            ]

        # (c) dirigente que possivelmente coincide com servidor do órgão concedente
        coincidencias = []
        for convenio in lista:
            for orgao_id in sorted(concedentes.get(convenio.id, ())):
                for servidor, aresta_servidor in servidores.get(orgao_id, []):
                    for dirigente, aresta in dirigentes:
                        if _coincide(dirigente, servidor):
                            coincidencias.append(
                                (dirigente, aresta, servidor, aresta_servidor, orgao_id)
                            )
        if coincidencias:
            vistos = {}
            for dirigente, aresta, servidor, aresta_servidor, orgao_id in coincidencias:
                vistos.setdefault(
                    (dirigente.id, servidor.id),
                    (dirigente, aresta, servidor, aresta_servidor, orgao_id),
                )
            sinais["c"] = (
                "tem dirigente que possivelmente coincide com servidor do órgão concedente "
                "(seis dígitos visíveis do CPF e nome normalizado iguais): "
                + _lista(
                    [
                        f"{d.rotulo} ({_identidade_parcial(d)[0]}) e servidor de "
                        f"{ctx.entidades[o].rotulo}"
                        for d, _, _, _, o in vistos.values()
                    ]
                )
            )
            for dirigente, aresta, servidor, aresta_servidor, orgao_id in vistos.values():
                entidades += [dirigente.id, servidor.id, orgao_id]
                evidencias.append(_evidencia_socio(ctx, dirigente, aresta, entidade))
                evidencias.append(
                    ctx.evidencia(
                        aresta_servidor.captura_id,
                        f"Servidor {servidor.rotulo} ({_identidade_parcial(servidor)[0]}) em "
                        f"{ctx.entidades[orgao_id].rotulo} (Portal da Transparência, servidores)",
                    )
                )

        # (d) dirigente que também é sócio de empresa no mesmo endereço, do mesmo ramo ou
        # recém-aberta
        enderecos = set(atributos.get("enderecos_normalizados") or [])
        ramo = str(atributos.get("cnae_fiscal_principal") or "").strip()
        primeira_vigencia = min(vigencias) if vigencias else None
        ligadas = []
        for dirigente, aresta in dirigentes:
            # Sem os seis dígitos do CPF, "mesma pessoa" em duas empresas não se sustenta.
            if not _identidade_parcial(dirigente)[0]:
                continue
            for outra_id, aresta_outra in empresas_do_socio.get(dirigente.id, []):
                outra = ctx.entidades.get(outra_id)
                if outra is None or outra_id == entidade_id:
                    continue
                motivos = []
                if enderecos & set(_atr(outra).get("enderecos_normalizados") or []):
                    motivos.append("mesmo endereço da entidade")
                if ramo and ramo == str(_atr(outra).get("cnae_fiscal_principal") or "").strip():
                    motivos.append(f"mesmo CNAE principal ({ramo})")
                abertura = _inicio(outra)
                if (
                    abertura is not None
                    and primeira_vigencia is not None
                    and (primeira_vigencia - abertura).days < dias_minimos
                ):
                    motivos.append(
                        f"aberta em {formatar_data(abertura.isoformat())}, menos de "
                        f"{dias_minimos} dias antes da primeira vigência"
                    )
                if motivos:
                    ligadas.append((dirigente, aresta, outra, aresta_outra, motivos))
        if ligadas:
            sinais["d"] = "tem dirigente que também é sócio de empresa ligada: " + _lista(
                [
                    f"{d.rotulo} ({_identidade_parcial(d)[0]}) em {o.rotulo} (CNPJ básico "
                    f"{o.identificador}; {', '.join(m)})"
                    for d, _, o, _, m in ligadas
                ]
            )
            for dirigente, aresta, outra, aresta_outra, _ in ligadas[:MAX_ITENS_NO_MOTIVO]:
                entidades += [dirigente.id, outra.id]
                evidencias.append(_evidencia_socio(ctx, dirigente, aresta, entidade))
                evidencias.append(_evidencia_socio(ctx, dirigente, aresta_outra, outra))

        if not sinais:
            return None
        convenios_texto = _lista(
            [
                f"{c.identificador} ({formatar_moeda(_atr(c).get('valor'))}, vigência a partir de "
                f"{formatar_data(_atr(c).get('data_inicio_vigencia'))}, concedente "
                f"{_atr(c).get('orgao_nome') or 'não informado'})"
                for c in lista
            ]
        )
        evidencias = [
            ctx.evidencia(
                c.captura_id,
                f"Convênio {c.identificador} de {entidade.rotulo}: vigência a partir de "
                f"{formatar_data(_atr(c).get('data_inicio_vigencia'))}, valor "
                f"{formatar_moeda(_atr(c).get('valor'))} (Portal da Transparência, convênios)",
            )
            for c in lista[:MAX_ITENS_NO_MOTIVO]
        ] + evidencias
        pessoas = bool({"c", "d"} & set(sinais))
        motivo = (
            f"A entidade sem fins lucrativos {entidade.rotulo} (CNPJ básico "
            f"{entidade.identificador}, natureza jurídica {natureza}) é convenente em "
            f"{len(lista)} convênio(s) federal(is): {convenios_texto}. Sinais encontrados: "
            + "; ".join(f"({letra}) {texto}" for letra, texto in sorted(sinais.items()))
            + ". Pode indicar entidade de fachada ou desvio por convênio, mas entidades regulares "
            "também são novas ou têm dirigentes com atuação pública. O repasse da entidade a "
            "empresas é dado bancário e não é visto aqui. "
            + (AVISO_PESSOA_PROVAVEL + " " if pessoas else "")
            + AVISO_PISTA
        )
        return AlertaDetectado(
            tipologia="L16",
            titulo=(
                f"Entidade convenente com {len(sinais)} sinal(is) "
                f"({', '.join(sorted(sinais))}): {entidade.rotulo}"
            ),
            motivo=motivo,
            entidades=list(dict.fromkeys(entidades)),
            evidencias=evidencias[: MAX_ITENS_NO_MOTIVO * 3],
            parametros={
                "l16_dias_minimos": dias_minimos,
                "natureza_juridica": natureza,
                "convenios": len(lista),
                "sinais": sorted(sinais),
                "identidade_confirmada": False if pessoas else None,
            },
        )

    return _por_item("L16", sorted(convenios), regra)


TIPOLOGIAS_LAVAGEM = {
    "L1": tipologia_l1,
    "L3": tipologia_l3,
    "L4": tipologia_l4,
    "L5": tipologia_l5,
    "L6": tipologia_l6,
    "L7": tipologia_l7,
    "L8": tipologia_l8,
    "L9": tipologia_l9,
    "L10": tipologia_l10,
    "L13": tipologia_l13,
    "L14": tipologia_l14,
    "L15": tipologia_l15,
    "L16": tipologia_l16,
}
