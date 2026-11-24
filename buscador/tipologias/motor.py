"""Motor das tipologias (T1 a T4 aqui; T5 a T10 em ``horizonte2`` e as de lavagem, L, em
``lavagem``, registradas pelo pacote).

Cada tipologia é uma função pura sobre um ``Contexto`` (entidades, arestas e capturas do caso já
carregadas) e devolve ``AlertaDetectado`` com:
- ``motivo``: frase completa em português com os números reais;
- ``evidencias``: capturas de origem (fonte, URL ou arquivo, SHA-256) de cada fato usado.
Os limiares vêm da configuração e podem ser sobrescritos por execução; eles são devolvidos em
``parametros`` para que o alerta seja reproduzível.

A severidade não é calculada aqui: vem da tabela única ``politicas.POLITICAS`` (RF14).

Cada alerta tem uma ``chave`` estável (tipologia + entidades + discriminador). A reexecução
atualiza o alerta de mesma chave em vez de recriá-lo, para que a decisão do investigador (RF12)
e o seu histórico não se percam; um alerta com decisão que deixa de ser detectado fica inativo,
nunca é apagado.
"""

from __future__ import annotations

import hashlib
import logging
import math
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from buscador.documentos import DocumentoRecusado, LimitesPdf, extrair_texto
from buscador.modelos import Alerta, Aresta, Captura, Caso, Entidade, Sugestao
from buscador.privacidade import descaracterizar_texto
from buscador.seguranca import numero_finito
from buscador.tipologias import politicas

registro = logging.getLogger(__name__)

# Limiares numéricos que uma execução pode sobrescrever. Os parâmetros estruturados (limites de
# dispensa por ano, critério de natureza, situações de eleito e expressões da T10) vêm só da
# configuração do operador.
NOMES_PARAMETROS = (
    "t1_dias_minimos",
    "t2_minimo_empresas",
    "t3_razao_minima",
    "t5_minimo_contratacoes",
    "t8_percentual_limite",
    "t8_percentual_limite_reforma",
    # Tipologias de lavagem (L): limiares numéricos, todos decisão do autor.
    "l1_minimo_estabelecimentos",
    "l1_janela_abertura_dias",
    "l1_minimo_abertas_na_janela",
    "l2_minimo_empresas",
    "l3_minimo_empresas",
    "l3_minimo_abertas_na_janela",
    "l3_janela_meses",
    "l4_minimo_niveis",
    "l7_dias_apos_abertura",
    "l7_meses_antes_contrato",
    "l9_anos_mandato",
    "l13_dias_abertura",
    "l14_valor_minimo",
    "l15_minimo_cnaes",
    "l15_minimo_categorias",
    "l16_dias_minimos",
    # Alerta de combinação.
    "comb_minimo_tipologias",
    "comb_peso_minimo",
)
# Decimais que precisam ser maiores que zero.
_PARAMETROS_DECIMAIS = (
    "t3_razao_minima",
    "t8_percentual_limite",
    "t8_percentual_limite_reforma",
    "comb_peso_minimo",
)
# Decimais que aceitam zero.
_PARAMETROS_DECIMAIS_COM_ZERO = ("l14_valor_minimo",)


@dataclass(frozen=True)
class Evidencia:
    descricao: str
    fonte: str
    captura_id: str
    url_ou_arquivo: str
    sha256: str


@dataclass
class AlertaDetectado:
    tipologia: str
    titulo: str
    motivo: str
    entidades: list[str]
    evidencias: list[Evidencia]
    parametros: dict = field(default_factory=dict)
    # Distingue alertas da mesma tipologia com as mesmas entidades (ex.: o endereço na T2).
    discriminador: str = ""

    @property
    def severidade(self) -> str:
        return politicas.severidade(self.tipologia)

    @property
    def chave(self) -> str:
        """Identidade estável entre execuções: não depende de limiar nem de valor observado."""
        base = "|".join([self.tipologia, *sorted(self.entidades), self.discriminador])
        return hashlib.sha256(base.encode("utf-8", "surrogatepass")).hexdigest()

    def para_dict(self) -> dict:
        return {**asdict(self), "severidade": self.severidade, "chave": self.chave}


@dataclass
class Contexto:
    entidades: dict[str, Entidade]
    arestas: list[Aresta]
    capturas: dict[str, Captura]
    # Leitura da carga bruta na custódia (só a T10 precisa: o texto do edital).
    ler_captura: Callable[[Captura], bytes] | None = None
    # Limites da extração de texto de PDF (``None`` = padrões de ``LimitesPdf``).
    limites_pdf: LimitesPdf | None = None
    # Sugestões de resolução do caso (a L9 lê as de candidato x sócio e a decisão do
    # investigador sobre cada par).
    sugestoes: list[Sugestao] = field(default_factory=list)
    # Avisos da execução: o que uma tipologia deixou de avaliar por falta de dado (frases
    # curtas, sem dado pessoal). ``executar`` grava no caso, por tipologia.
    avisos: list[str] = field(default_factory=list)
    _textos: dict[str, str | None] = field(default_factory=dict, repr=False)

    def texto_da_captura(self, captura_id: str) -> str | None:
        """Texto da captura (texto puro em UTF-8 ou Latin-1; PDF: o texto extraído), ou ``None``
        se ela não puder ser lida (arquivo ausente, PDF recusado ou sem camada de texto).

        No PDF, as posições calculadas sobre este texto (linha, coluna e posição da T10)
        referem-se ao texto extraído, não a deslocamentos dentro do arquivo."""
        if captura_id not in self._textos:
            texto = None
            captura = self.capturas.get(captura_id)
            if captura is not None and self.ler_captura is not None:
                try:
                    # RNF04: o trecho do edital citado no alerta nunca leva CPF completo.
                    texto = descaracterizar_texto(
                        extrair_texto(self.ler_captura(captura), self.limites_pdf).texto
                    )
                except (OSError, DocumentoRecusado):
                    texto = None
            self._textos[captura_id] = texto
        return self._textos[captura_id]

    def evidencia(self, captura_id: str, descricao: str) -> Evidencia:
        c = self.capturas[captura_id]
        return Evidencia(descricao, c.fonte, c.id, c.url_ou_arquivo, c.sha256)

    def contratos_com_fornecedor(self) -> list[tuple[Entidade, Entidade]]:
        """Pares (fornecedor, contrato) a partir das arestas fornecedor_de."""
        pares = []
        for a in self.arestas:
            if a.tipo == "fornecedor_de":
                pares.append((self.entidades[a.origem_id], self.entidades[a.destino_id]))
        return pares


# ---- Formatação em português ------------------------------------------------------------------


def formatar_moeda(valor: float | None) -> str:
    if valor is None:
        return "valor não informado"
    if not math.isfinite(valor):
        return "valor inválido"
    inteiro, centavos = f"{valor:,.2f}".split(".")
    return f"R$ {inteiro.replace(',', '.')},{centavos}"


def formatar_data(iso: str | None) -> str:
    if not iso or len(iso) < 10:
        return "data não informada"
    return f"{iso[8:10]}/{iso[5:7]}/{iso[:4]}"


def formatar_numero(valor: float) -> str:
    if not math.isfinite(valor):
        return "valor inválido"
    texto = f"{valor:,.1f}"
    return texto.replace(",", "X").replace(".", ",").replace("X", ".")


def _data(iso: str | None) -> date | None:
    try:
        return date.fromisoformat(iso[:10]) if iso else None
    except ValueError:
        return None


def _estabelecimento_matriz(empresa: Entidade) -> dict | None:
    """O estabelecimento de onde vem ``data_inicio_atividade`` (a matriz, ou o primeiro)."""
    estabelecimentos = (empresa.atributos or {}).get("estabelecimentos", [])
    for est in estabelecimentos:
        if est.get("matriz_filial") == "matriz":
            return est
    return estabelecimentos[0] if estabelecimentos else None


def _captura_do_atributo(ctx: Contexto, entidade: Entidade, atributo: str, papel: str) -> str:
    """Captura que forneceu o valor em vigor do atributo (``origem_atributos``).

    Entidades gravadas antes de existir a proveniência por atributo caem na regra antiga: a
    primeira fonte com o papel pedido, na ordem de registro.
    """
    origem = ((entidade.atributos or {}).get("origem_atributos") or {}).get(atributo)
    if origem in ctx.capturas:
        return origem
    for fonte in entidade.fontes:
        if fonte.papel == papel and fonte.captura_id in ctx.capturas:
            return fonte.captura_id
    return entidade.captura_id


def _por_item(nome: str, itens, funcao) -> list[AlertaDetectado]:
    """Aplica a regra a cada item; um registro que falha é pulado, sem derrubar a execução."""
    alertas = []
    for item in itens:
        try:
            alerta = funcao(item)
        except (ArithmeticError, AttributeError, KeyError, TypeError, ValueError):
            registro.warning("Tipologia %s: registro ignorado por dado inválido.", nome)
            continue
        if alerta is not None:
            alertas.append(alerta)
    return alertas


# ---- T1: empresa aberta pouco antes do contrato -----------------------------------------------


def tipologia_t1(ctx: Contexto, parametros: dict) -> list[AlertaDetectado]:
    limite = int(parametros["t1_dias_minimos"])

    def regra(par: tuple[Entidade, Entidade]) -> AlertaDetectado | None:
        fornecedor, contrato = par
        if fornecedor.tipo != "empresa":
            return None
        inicio = _data((fornecedor.atributos or {}).get("data_inicio_atividade"))
        assinatura = _data((contrato.atributos or {}).get("data_assinatura"))
        if inicio is None or assinatura is None:
            return None
        dias = (assinatura - inicio).days
        if dias >= limite:
            return None
        orgao = (contrato.atributos or {}).get("orgao_razao_social") or "órgão não informado"
        # O CNPJ citado é o do estabelecimento que forneceu a data (o atributo ``cnpj`` da
        # empresa pode ter sido sobrescrito pelo CNPJ de uma filial vindo de outra fonte).
        matriz = _estabelecimento_matriz(fornecedor) or {}
        cnpj = (
            matriz.get("cnpj")
            or (fornecedor.atributos or {}).get("cnpj")
            or fornecedor.identificador
        )
        if dias < 0:
            relacao = f"{abs(dias)} dias antes mesmo do início da atividade"
        else:
            relacao = f"apenas {dias} dias depois"
        motivo = (
            f"A empresa {fornecedor.rotulo} (CNPJ {cnpj}) iniciou a atividade em "
            f"{formatar_data(inicio.isoformat())} e assinou o contrato "
            f"{contrato.identificador} com {orgao} em {formatar_data(assinatura.isoformat())}, "
            f"{relacao}; o limiar configurado é de {limite} dias."
        )
        evidencias = []
        captura_est = matriz.get("captura_id")
        if captura_est in ctx.capturas:
            evidencias.append(
                ctx.evidencia(
                    captura_est,
                    f"Estabelecimento {cnpj}: data de início da atividade "
                    f"{formatar_data(inicio.isoformat())} "
                    "(dados abertos do CNPJ, Estabelecimentos)",
                )
            )
        evidencias.append(
            ctx.evidencia(
                contrato.captura_id,
                f"Contrato {contrato.identificador}: data de assinatura "
                f"{formatar_data(assinatura.isoformat())} (PNCP)",
            )
        )
        return AlertaDetectado(
            tipologia="T1",
            titulo=(
                f"Empresa aberta {max(dias, 0)} dias antes do contrato {contrato.identificador}"
            ),
            motivo=motivo,
            entidades=[fornecedor.id, contrato.id],
            evidencias=evidencias,
            parametros={"t1_dias_minimos": limite, "dias_observados": dias},
        )

    return _por_item("T1", ctx.contratos_com_fornecedor(), regra)


# ---- T2: endereço compartilhado ---------------------------------------------------------------


def tipologia_t2(ctx: Contexto, parametros: dict) -> list[AlertaDetectado]:
    minimo = int(parametros["t2_minimo_empresas"])
    contratadas = {f.id for f, _ in ctx.contratos_com_fornecedor()}
    por_endereco: dict[str, list[Entidade]] = {}
    for e in ctx.entidades.values():
        if e.tipo != "empresa":
            continue
        for chave in (e.atributos or {}).get("enderecos_normalizados", []):
            # Chave vazia (endereço em branco ou só com a cidade) não identifica um lugar.
            if chave:
                por_endereco.setdefault(chave, []).append(e)

    def regra(grupo: tuple[str, list[Entidade]]) -> AlertaDetectado | None:
        chave, empresas = grupo
        if len(empresas) < minimo:
            return None
        com_contrato = [e for e in empresas if e.id in contratadas]
        if not com_contrato:
            return None
        empresas = sorted(empresas, key=lambda e: e.rotulo)
        legivel = next(
            (
                est["endereco"]
                for e in empresas
                for est in (e.atributos or {}).get("estabelecimentos", [])
                if est.get("endereco_normalizado") == chave
            ),
            chave,
        )
        nomes = "; ".join(f"{e.rotulo} (CNPJ básico {e.identificador})" for e in empresas)
        nomes_contratadas = ", ".join(e.rotulo for e in com_contrato)
        motivo = (
            f"O endereço '{legivel}' é compartilhado por {len(empresas)} empresas ({nomes}), "
            f"das quais {len(com_contrato)} têm contrato público registrado no PNCP "
            f"({nomes_contratadas}); o limiar configurado é de {minimo} empresas "
            "no mesmo endereço, "
            "com pelo menos uma contratada."
        )
        evidencias = []
        for e in empresas:
            for est in (e.atributos or {}).get("estabelecimentos", []):
                if est.get("endereco_normalizado") == chave:
                    evidencias.append(
                        ctx.evidencia(
                            est["captura_id"],
                            f"Estabelecimento {est['cnpj']} de {e.rotulo} no endereço "
                            f"'{est['endereco']}' "
                            "(dados abertos do CNPJ, Estabelecimentos)",
                        )
                    )
        for fornecedor, contrato in ctx.contratos_com_fornecedor():
            if fornecedor.id in {e.id for e in com_contrato}:
                evidencias.append(
                    ctx.evidencia(
                        contrato.captura_id,
                        f"Contrato {contrato.identificador} de {fornecedor.rotulo} com "
                        f"{(contrato.atributos or {}).get('orgao_razao_social', '')} (PNCP)",
                    )
                )
        return AlertaDetectado(
            tipologia="T2",
            titulo=(
                f"Endereço compartilhado por {len(empresas)} empresas, "
                f"{len(com_contrato)} contratada(s)"
            ),
            motivo=motivo,
            entidades=[e.id for e in empresas],
            evidencias=evidencias,
            discriminador=chave,
            parametros={
                "t2_minimo_empresas": minimo,
                "empresas_no_endereco": len(empresas),
                "empresas_contratadas": len(com_contrato),
                "endereco_normalizado": chave,
            },
        )

    return _por_item("T2", sorted(por_endereco.items()), regra)


# ---- T3: capital social incompatível ----------------------------------------------------------


def tipologia_t3(ctx: Contexto, parametros: dict) -> list[AlertaDetectado]:
    razao_minima = float(parametros["t3_razao_minima"])

    def regra(par: tuple[Entidade, Entidade]) -> AlertaDetectado | None:
        fornecedor, contrato = par
        if fornecedor.tipo != "empresa":
            return None
        # Só números finitos e capital não negativo entram na conta (NaN/Infinity são pulados).
        capital = numero_finito((fornecedor.atributos or {}).get("capital_social"))
        valor = numero_finito((contrato.atributos or {}).get("valor_global"))
        if capital is None or valor is None or capital < 0:
            return None
        if capital == 0:
            razao = None
            disparou = valor > 0
        else:
            razao = valor / capital
            disparou = math.isfinite(razao) and razao >= razao_minima
        if not disparou:
            return None
        orgao = (contrato.atributos or {}).get("orgao_razao_social") or "órgão não informado"
        cnpj = (fornecedor.atributos or {}).get("cnpj") or fornecedor.identificador
        if razao is None:
            comparacao = "o capital social declarado é zero, o que torna a razão indefinida"
        else:
            comparacao = f"o valor do contrato é {formatar_numero(razao)} vezes o capital social"
        motivo = (
            f"A empresa {fornecedor.rotulo} (CNPJ {cnpj}) tem capital social de "
            f"{formatar_moeda(capital)} e assinou o contrato {contrato.identificador} com {orgao} "
            f"no valor global de {formatar_moeda(valor)}: {comparacao}; "
            f"o limiar configurado é de {formatar_numero(razao_minima)} vezes."
        )
        evidencias = [
            ctx.evidencia(
                _captura_do_atributo(ctx, fornecedor, "capital_social", "empresas"),
                f"Empresa {fornecedor.rotulo}: capital social {formatar_moeda(capital)} "
                "(dados abertos do CNPJ, Empresas)",
            ),
            ctx.evidencia(
                contrato.captura_id,
                f"Contrato {contrato.identificador}: valor global {formatar_moeda(valor)} (PNCP)",
            ),
        ]
        return AlertaDetectado(
            tipologia="T3",
            titulo=f"Capital social incompatível com o contrato {contrato.identificador}",
            motivo=motivo,
            entidades=[fornecedor.id, contrato.id],
            evidencias=evidencias,
            parametros={
                "t3_razao_minima": razao_minima,
                "razao_observada": round(razao, 2) if razao is not None else None,
                "capital_social": capital,
                "valor_global": valor,
            },
        )

    return _por_item("T3", ctx.contratos_com_fornecedor(), regra)


# ---- T4: empresa contratada sem situação cadastral ativa (H31) ---------------------------------

# Códigos do campo "SITUAÇÃO CADASTRAL" da tabela Estabelecimentos no layout oficial dos dados
# abertos do CNPJ: "01 – NULA, 2 – ATIVA, 3 – SUSPENSA, 4 – INAPTA, 08 – BAIXADA" (metadados do
# CNPJ, Receita Federal, https://www.gov.br/receitafederal/dados/cnpj-metadados.pdf, conferido em
# 16/11/2026). O arquivo traz o código com ou sem zero à esquerda, por isso a comparação é numérica.
SITUACOES_CADASTRAIS = {1: "NULA", 2: "ATIVA", 3: "SUSPENSA", 4: "INAPTA", 8: "BAIXADA"}
SITUACAO_ATIVA = 2


def _codigo_situacao(valor: object) -> int | None:
    texto = str(valor or "").strip()
    return int(texto) if texto.isdigit() else None


def _estabelecimento_do_contrato(empresa: Entidade, contrato: Entidade) -> dict | None:
    """O estabelecimento contratado (pelo CNPJ de 14 dígitos do contrato) ou, se ele não estiver
    nos arquivos coletados, a matriz."""
    ni = str((contrato.atributos or {}).get("ni_fornecedor") or "")
    for est in (empresa.atributos or {}).get("estabelecimentos", []):
        if ni and est.get("cnpj") == ni:
            return est
    return _estabelecimento_matriz(empresa)


def tipologia_t4(ctx: Contexto, parametros: dict) -> list[AlertaDetectado]:
    def regra(par: tuple[Entidade, Entidade]) -> AlertaDetectado | None:
        fornecedor, contrato = par
        if fornecedor.tipo != "empresa":
            return None
        est = _estabelecimento_do_contrato(fornecedor, contrato)
        # Empresa que só aparece no PNCP não tem situação conhecida: ausência de dado não é
        # situação irregular, então não há alerta.
        codigo = _codigo_situacao((est or {}).get("situacao_cadastral"))
        if est is None or codigo is None or codigo == SITUACAO_ATIVA:
            return None
        captura_est = est.get("captura_id")
        if captura_est not in ctx.capturas:
            return None
        nome = SITUACOES_CADASTRAIS.get(codigo)
        situacao = (
            f"{nome} (código {codigo:02d})"
            if nome
            else f"de código {codigo:02d}, que não consta do layout oficial"
        )
        cnpj = est.get("cnpj") or fornecedor.identificador
        orgao = (contrato.atributos or {}).get("orgao_razao_social") or "órgão não informado"
        desde = _data(est.get("data_situacao_cadastral"))
        assinatura = _data((contrato.atributos or {}).get("data_assinatura"))
        anterior: bool | None = None
        dias: int | None = None
        if desde is None:
            quando = "a data dessa situação não foi informada"
        elif assinatura is None:
            quando = (
                f"essa situação vigora desde {formatar_data(desde.isoformat())} e a data de "
                "assinatura do contrato não foi informada"
            )
        else:
            dias = (assinatura - desde).days
            anterior = dias >= 0
            if anterior:
                quando = (
                    f"essa situação vigora desde {formatar_data(desde.isoformat())}, {dias} dias "
                    "antes da assinatura: a empresa já não estava ativa quando foi contratada"
                )
            else:
                quando = (
                    f"essa situação vigora desde {formatar_data(desde.isoformat())}, {-dias} dias "
                    "depois da assinatura: a empresa deixou de estar ativa depois de contratada"
                )
        motivo = (
            f"A empresa {fornecedor.rotulo} (CNPJ {cnpj}) tem contrato com {orgao} "
            f"({contrato.identificador}, assinado em "
            f"{formatar_data(assinatura.isoformat() if assinatura else None)}), mas a situação "
            f"cadastral do estabelecimento no CNPJ é {situacao}; {quando}. A regra dispara para "
            "qualquer situação diferente de ATIVA (código 02)."
        )
        evidencias = [
            ctx.evidencia(
                captura_est,
                f"Estabelecimento {cnpj}: situação cadastral {situacao}"
                + (f" desde {formatar_data(desde.isoformat())}" if desde else "")
                + " (dados abertos do CNPJ, Estabelecimentos)",
            ),
            ctx.evidencia(
                contrato.captura_id,
                f"Contrato {contrato.identificador}: fornecedor {cnpj}, data de assinatura "
                f"{formatar_data(assinatura.isoformat() if assinatura else None)} (PNCP)",
            ),
        ]
        return AlertaDetectado(
            tipologia="T4",
            titulo=(
                f"Empresa com situação cadastral {nome or f'código {codigo:02d}'} no contrato "
                f"{contrato.identificador}"
            ),
            motivo=motivo,
            entidades=[fornecedor.id, contrato.id],
            evidencias=evidencias,
            parametros={
                "situacao_cadastral_codigo": f"{codigo:02d}",
                "situacao_cadastral": nome or "não reconhecida",
                "data_situacao_cadastral": desde.isoformat() if desde else None,
                "data_assinatura": assinatura.isoformat() if assinatura else None,
                "situacao_anterior_ao_contrato": anterior,
                "dias_entre_situacao_e_assinatura": dias,
                "cnpj_estabelecimento": cnpj,
            },
        )

    return _por_item("T4", ctx.contratos_com_fornecedor(), regra)


TIPOLOGIAS = {"T1": tipologia_t1, "T2": tipologia_t2, "T3": tipologia_t3, "T4": tipologia_t4}


def parametros_padrao_h2() -> dict:
    """Padrões do código para as tipologias T5 a T10 (os mesmos padrões da configuração)."""
    from buscador.tipologias import legislacao

    return {
        "t5_minimo_contratacoes": 2,
        "t5_criterio_natureza": "fornecedor_e_categoria",
        "t5_limites_dispensa": legislacao.limites_dispensa(),
        "t8_percentual_limite": 25.0,
        "t8_percentual_limite_reforma": 50.0,
        "t9_situacoes_eleito": ["ELEITO", "ELEITO POR QP", "ELEITO POR MÉDIA"],
        "t10_expressoes_extras": [],
    }


_ORDEM_PREFIXO = {"T": 0, "L": 1}


def chave_do_codigo(codigo: str) -> tuple[int, int, str]:
    """Ordem do catálogo e da execução: T1 a T10, depois as L em ordem numérica, depois o resto
    (o alerta de combinação, que depende dos demais)."""
    prefixo, numero = codigo[:1], codigo[1:]
    if prefixo in _ORDEM_PREFIXO and numero.isdigit():
        return (_ORDEM_PREFIXO[prefixo], int(numero), codigo)
    return (len(_ORDEM_PREFIXO), 0, codigo)


def parametros_padrao_lavagem() -> dict:
    """Padrões do código para as tipologias L (os mesmos padrões da configuração)."""
    from buscador.tipologias import lavagem

    return lavagem.parametros_padrao()


# O alerta de combinação lê os alertas das demais: roda sempre por último.
CODIGO_COMBINACAO = "COMB"


def ordem_tipologias() -> list[str]:
    """Códigos das tipologias na ordem de execução (T1 a T10, L1 a L16 e a combinação)."""
    return [*sorted(TIPOLOGIAS, key=chave_do_codigo), CODIGO_COMBINACAO]


def sinais_fracos() -> list[str]:
    """Códigos dos sinais fracos (H49): L2, L14 e L15. Não geram alerta próprio; são avaliados
    junto com a combinação e só pesam nela."""
    return [
        codigo
        for codigo in sorted(TIPOLOGIAS, key=chave_do_codigo)
        if (politica := politicas.politica(codigo)) is not None and politica.sinal_fraco
    ]


def avaliar_sinais_fracos(
    ctx: Contexto, parametros: dict, codigos: list[str] | None = None
) -> list[AlertaDetectado]:
    """Avalia os sinais fracos (todos, ou só ``codigos``) sobre o contexto, sem gravar nada. O
    resultado tem o mesmo formato de um alerta detectado (motivo, números e evidências ligadas
    às capturas), mas só existe dentro do alerta de combinação."""
    todos = sinais_fracos()
    detectados: dict[str, AlertaDetectado] = {}
    for codigo in todos if codigos is None else [c for c in codigos if c in todos]:
        for d in TIPOLOGIAS[codigo](ctx, parametros):
            detectados.setdefault(d.chave, d)
    return list(detectados.values())


# ---- Execução ---------------------------------------------------------------------------------


_LIMITES_PARAMETROS = {
    "t1_dias_minimos": (0, 36_500),
    "t2_minimo_empresas": (2, 10_000),
    "t3_razao_minima": (0.0, 1e9),
    "t5_minimo_contratacoes": (2, 1_000),
    "t8_percentual_limite": (0.0, 1_000.0),
    "t8_percentual_limite_reforma": (0.0, 1_000.0),
    "l1_minimo_estabelecimentos": (2, 10_000),
    "l1_janela_abertura_dias": (1, 36_500),
    "l1_minimo_abertas_na_janela": (2, 10_000),
    "l2_minimo_empresas": (2, 10_000),
    "l3_minimo_empresas": (2, 10_000),
    "l3_minimo_abertas_na_janela": (1, 10_000),
    "l3_janela_meses": (1, 1_200),
    "l4_minimo_niveis": (2, 20),
    "l7_dias_apos_abertura": (0, 36_500),
    "l7_meses_antes_contrato": (1, 1_200),
    "l9_anos_mandato": (1, 12),
    "l13_dias_abertura": (0, 36_500),
    "l14_valor_minimo": (0.0, 1e12),
    "l15_minimo_cnaes": (2, 1_000),
    "l15_minimo_categorias": (2, 100),
    "l16_dias_minimos": (0, 36_500),
    "comb_minimo_tipologias": (2, 30),
    "comb_peso_minimo": (0.0, 100.0),
}


def parametros_efetivos(padrao: dict, sobrescrita: dict | None) -> dict:
    """Limiares da execução. Recusa nomes desconhecidos, ``NaN``/``Infinity`` e valores fora da
    faixa (as mesmas faixas da configuração: T1 >= 0, T2 >= 2, T3 > 0)."""
    parametros = dict(padrao)
    for nome, valor in (sobrescrita or {}).items():
        if nome not in NOMES_PARAMETROS:
            raise ValueError(
                f"Parâmetro de tipologia desconhecido: '{str(nome)[:60]}' "
                f"(aceitos: {NOMES_PARAMETROS})"
            )
        if valor is None:
            continue
        numero = numero_finito(valor)
        minimo, maximo = _LIMITES_PARAMETROS[nome]
        if numero is None or not minimo <= numero <= maximo:
            raise ValueError(f"Parâmetro '{nome}' deve ser um número de {minimo} a {maximo:g}.")
        if nome in _PARAMETROS_DECIMAIS:
            if numero <= 0:
                raise ValueError(f"Parâmetro '{nome}' deve ser maior que zero.")
            parametros[nome] = numero
        elif nome in _PARAMETROS_DECIMAIS_COM_ZERO:
            parametros[nome] = numero
        else:
            parametros[nome] = int(numero)
    return parametros


def carregar_contexto(
    sessao: Session,
    caso_id: str,
    ler_captura: Callable[[Captura], bytes] | None = None,
    limites_pdf: LimitesPdf | None = None,
) -> Contexto:
    entidades = {
        e.id: e
        for e in sessao.execute(select(Entidade).where(Entidade.caso_id == caso_id)).scalars()
    }
    arestas = list(sessao.execute(select(Aresta).where(Aresta.caso_id == caso_id)).scalars())
    capturas = {
        c.id: c for c in sessao.execute(select(Captura).where(Captura.caso_id == caso_id)).scalars()
    }
    sugestoes = list(
        sessao.execute(
            select(Sugestao).where(Sugestao.caso_id == caso_id).order_by(Sugestao.id)
        ).scalars()
    )
    return Contexto(
        entidades=entidades,
        arestas=arestas,
        capturas=capturas,
        sugestoes=sugestoes,
        ler_captura=ler_captura,
        limites_pdf=limites_pdf,
    )


MAX_AVISOS_POR_TIPOLOGIA = 100


def _limitar_avisos(codigo: str, avisos: list[str]) -> list[str]:
    """Sem repetição, na ordem, e no máximo ``MAX_AVISOS_POR_TIPOLOGIA`` (o resto é contado)."""
    unicos = list(dict.fromkeys(avisos))
    if len(unicos) <= MAX_AVISOS_POR_TIPOLOGIA:
        return unicos
    resto = len(unicos) - MAX_AVISOS_POR_TIPOLOGIA
    return [*unicos[:MAX_AVISOS_POR_TIPOLOGIA], f"{codigo}: mais {resto} aviso(s) não listado(s)."]


def avisos_do_caso(caso: Caso, codigos: list[str] | None = None) -> list[str]:
    """Avisos gravados na última execução de cada tipologia (todas, ou só ``codigos``), na
    ordem do catálogo."""
    registrados = caso.avisos_tipologias or {}
    escolhidos = registrados if codigos is None else [c for c in codigos if c in registrados]
    return [
        aviso for codigo in sorted(escolhidos, key=chave_do_codigo) for aviso in registrados[codigo]
    ]


def ordenar(alertas: list[Alerta]) -> list[Alerta]:
    """Ordem de apresentação (RF14): severidade (alta primeiro), tipologia e título. Alertas
    inativos (não detectados na última execução, mantidos pela decisão) vão para o fim."""
    return sorted(
        alertas,
        key=lambda a: (
            a.ativo is False,
            politicas.chave_ordenacao(a.severidade, a.tipologia, a.titulo),
        ),
    )


def executar(
    sessao: Session,
    caso_id: str,
    parametros: dict,
    tipologias: list[str] | None = None,
    ler_captura: Callable[[Captura], bytes] | None = None,
    limites_pdf: LimitesPdf | None = None,
) -> list[Alerta]:
    """Roda as tipologias pedidas (todas por padrão) e devolve os alertas detectados, na ordem
    de severidade.

    Os alertas anteriores dessas tipologias são reconciliados pela ``chave``: o que continua
    detectado é atualizado no lugar (mantém o id, a situação e as decisões); o que deixou de ser
    detectado é apagado se nunca recebeu decisão e fica inativo (``ativo = False``) se recebeu.
    Alertas de tipologias não pedidas não são tocados.

    Sinais fracos (H49: L2, L14 e L15) NÃO geram alerta próprio. São avaliados sempre que a
    combinação roda, e só então, e entram nela como componentes sem alerta (``alerta_id`` nulo),
    com o motivo e as evidências. Pedir só um sinal fraco, sem a combinação, não devolve nada.
    Alertas L14 e L15 gravados por versões anteriores saem na reconciliação: o pendente sem
    decisão é apagado e o que tinha decisão fica inativo.

    ``ler_captura`` dá acesso à carga bruta na custódia; sem ele, a T10 (texto de editais) não
    encontra nada.

    O que uma tipologia deixou de avaliar por falta de dado (``Contexto.avisos``) é gravado em
    ``Caso.avisos_tipologias``, por tipologia: cada execução substitui os avisos das tipologias
    que rodou e não toca nos das outras.
    """
    pedidas = list(dict.fromkeys(tipologias or ordem_tipologias()))
    for nome in pedidas:
        if nome not in TIPOLOGIAS and nome != CODIGO_COMBINACAO:
            raise ValueError(f"Tipologia desconhecida: {str(nome)[:20]}")
    combinar = CODIGO_COMBINACAO in pedidas
    pedidas = [nome for nome in pedidas if nome != CODIGO_COMBINACAO]
    # Quem chama com um conjunto parcial de parâmetros recebe os padrões do código para o resto.
    # ``limiares`` (gravado em cada alerta) guarda só os limiares numéricos recebidos; a
    # configuração estruturada usada por uma tipologia vai nos ``parametros`` do próprio alerta.
    limiares = {k: v for k, v in parametros.items() if k in NOMES_PARAMETROS}
    from buscador.tipologias import combinacao

    parametros = {
        **parametros_padrao_h2(),
        **parametros_padrao_lavagem(),
        **combinacao.parametros_padrao(),
        **parametros,
    }
    # Serializa execuções concorrentes do mesmo caso (no SQLite a cláusula é ignorada).
    sessao.execute(select(Caso.id).where(Caso.id == caso_id).with_for_update())
    ctx = carregar_contexto(sessao, caso_id, ler_captura, limites_pdf)
    detectados: dict[str, AlertaDetectado] = {}
    caso = sessao.get(Caso, caso_id)
    avisos = dict((caso.avisos_tipologias if caso is not None else None) or {})
    fracos = sinais_fracos()
    sinais: list[AlertaDetectado] = []
    for nome in [*(p for p in pedidas if p not in fracos), *(fracos if combinar else [])]:
        antes = len(ctx.avisos)
        if nome in fracos:
            sinais += avaliar_sinais_fracos(ctx, parametros, [nome])
        else:
            for d in TIPOLOGIAS[nome](ctx, parametros):
                detectados.setdefault(d.chave, d)
        avisos.pop(nome, None)
        if len(ctx.avisos) > antes:
            avisos[nome] = _limitar_avisos(nome, ctx.avisos[antes:])
    if caso is not None:
        caso.avisos_tipologias = avisos

    # Os sinais fracos entram na reconciliação sem nenhum detectado: é o que tira da fila os
    # alertas L14 e L15 gravados antes de eles deixarem de gerar alerta próprio.
    limpar = [*pedidas, *(f for f in fracos if combinar and f not in pedidas)]
    alertas = _reconciliar(sessao, caso_id, limpar, detectados, limiares)
    if combinar:
        # A combinação lê TODOS os alertas ativos do caso, inclusive os de tipologias que não
        # foram pedidas nesta execução, já com o resultado das que acabaram de rodar, e os
        # sinais fracos avaliados agora.
        ativos = list(
            sessao.execute(
                select(Alerta).where(Alerta.caso_id == caso_id, Alerta.ativo.is_(True))
            ).scalars()
        )
        combinados = {
            d.chave: d for d in combinacao.detectar(ativos, ctx.entidades, parametros, sinais)
        }
        alertas += _reconciliar(sessao, caso_id, [CODIGO_COMBINACAO], combinados, limiares)
    return ordenar(alertas)


def _reconciliar(
    sessao: Session,
    caso_id: str,
    codigos: list[str],
    detectados: dict[str, AlertaDetectado],
    limiares: dict,
) -> list[Alerta]:
    """Grava os detectados das tipologias ``codigos``: atualiza o alerta de mesma chave, apaga o
    que deixou de ser detectado sem decisão e deixa inativo o que tinha decisão."""
    if not codigos:
        return []
    anteriores = list(
        sessao.execute(
            select(Alerta).where(Alerta.caso_id == caso_id, Alerta.tipologia.in_(codigos))
        ).scalars()
    )
    por_chave: dict[str, Alerta] = {}
    for anterior in anteriores:
        if anterior.chave in detectados and anterior.chave not in por_chave:
            por_chave[anterior.chave] = anterior
        elif anterior.situacao == "pendente" and not anterior.decisoes:
            sessao.delete(anterior)
        else:
            anterior.ativo = False

    alertas = []
    for chave, d in detectados.items():
        alerta = por_chave.get(chave)
        if alerta is None:
            alerta = Alerta(caso_id=caso_id, tipologia=d.tipologia, chave=chave)
            sessao.add(alerta)
        alerta.titulo = d.titulo
        alerta.motivo = d.motivo
        alerta.severidade = d.severidade
        alerta.entidades = d.entidades
        alerta.evidencias = [asdict(ev) for ev in d.evidencias]
        alerta.parametros = {**d.parametros, "limiares": dict(limiares)}
        alerta.ativo = True
        alertas.append(alerta)
    sessao.flush()
    return alertas
