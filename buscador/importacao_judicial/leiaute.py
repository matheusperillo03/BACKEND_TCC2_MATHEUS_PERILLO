"""Leiaute dos arquivos de dados bancários com sigilo afastado (Lei Complementar nº 105/2001).

Fonte: Instrução Normativa BCB nº 636, de 10/6/2025 (DOU de 13/6/2025, Seção 1, p. 188-196), em
vigor a partir de 1º/12/2026 (art. 7º), que revoga a Carta-Circular nº 3.454/2010 (art. 6º). Texto
lido na API oficial de normativos do Banco Central em 17/11/2026:
https://www.bcb.gov.br/api/conteudo/app/normativos/exibenormativo?p1=Instru%C3%A7%C3%A3o%20Normativa%20BCB&p2=636

Art. 2º: onze arquivos; os cinco das instituições em geral são AGÊNCIAS, CONTAS, TITULARES,
EXTRATO e ORIGEM-DESTINO (os arquivos VI a XI são das corretoras e distribuidoras e NÃO são
aceitos aqui). Art. 3º: arquivo texto ".txt", "padrão ASCII", sem cabeçalho, <TAB> como separador
de coluna, datas "ddmmaaaa", horas em UTC "hhmmss", campos numéricos "apenas números" e valores
monetários "até a segunda casa decimal, sem necessidade de zeros à esquerda (por exemplo, usar
10000 para R$100,00)".

As tabelas abaixo transcrevem o Anexo I, itens I a V: nome do campo, tipo (N numérico, T texto,
D data) e tamanho máximo, na ordem do anexo. Os nomes estão como no anexo, inclusive os acentos.

NÃO conferido: que o SIMBA (MPF e PF) entrega exatamente neste leiaute (é inferência, pelos nomes
dos arquivos); o leiaute anterior (Carta-Circular nº 3.454/2010) não é aceito; o Anexo II (códigos
de tipo de lançamento) não foi transcrito, então ``CODIGO_TIPO_LANCAMENTO`` só é validado como
número.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

NORMA = "Instrução Normativa BCB nº 636/2025, Anexo I"
SEPARADOR = "\t"
# "31129999" é o valor que o anexo manda usar para "sem data" (conta não encerrada etc.).
DATAS_SEM_VALOR = ("", "31129999", "00000000")

AGENCIAS = "AGENCIAS"
CONTAS = "CONTAS"
TITULARES = "TITULARES"
EXTRATO = "EXTRATO"
ORIGEM_DESTINO = "ORIGEM_DESTINO"
# Ordem de leitura: cada arquivo depende dos anteriores para a conferência do escopo.
TIPOS = (AGENCIAS, CONTAS, TITULARES, EXTRATO, ORIGEM_DESTINO)

Campo = tuple[str, str, int]

CAMPOS: dict[str, tuple[Campo, ...]] = {
    AGENCIAS: (
        ("COD_COMP_INSTITUIÇÃO", "N", 3),
        ("NUMERO_AGÊNCIA", "N", 5),
        ("NOME_AGÊNCIA", "T", 50),
        ("ENDERECO_LOGRADOURO", "T", 80),
        ("CODIGO_CIDADE", "T", 7),
        ("CODIGO_UF", "T", 2),
        ("ENDERECO_PAIS", "T", 40),
        ("ENDERECO_CEP", "T", 8),
        ("TELEFONE_AGÊNCIA", "T", 20),
        ("DATA_ABERTURA_AGÊNCIA", "D", 8),
        ("DATA_FECHAMENTO_AGÊNCIA", "D", 8),
        ("CNPJ_INSTITUIÇÃO", "T", 14),
        ("NOME_INSTITUIÇÃO", "T", 50),
        ("INSTITUIÇÃO_SEM_AGÊNCIA_FÍSICA", "N", 1),
    ),
    CONTAS: (
        ("COD_COMP_INSTITUIÇÃO", "N", 3),
        ("NUMERO_AGÊNCIA", "N", 5),
        ("NUMERO_CONTA", "T", 20),
        ("TIPO_CONTA", "N", 1),
        ("DATA_ABERTURA_CONTA", "D", 8),
        ("DATA_ENCERRAMENTO_CONTA", "D", 8),
        ("MOVIMENTACAO_CONTA", "N", 1),
        ("CNPJ_INSTITUIÇÃO", "T", 14),
        ("NOME_INSTITUIÇÃO", "T", 50),
        ("SALDO_INICIAL_CONTA", "N", 14),
        ("SALDO_FINAL_CONTA", "N", 14),
        ("CONTA_INSTITUIÇÃO_INCORPORADA", "T", 14),
    ),
    TITULARES: (
        ("COD_COMP_INSTITUIÇÃO", "N", 3),
        ("NUMERO_AGÊNCIA", "N", 5),
        ("NUMERO_CONTA", "T", 20),
        ("TIPO_CONTA", "N", 1),
        ("TIPO_TITULAR", "T", 1),
        ("PESSOA_INVESTIGADA", "N", 1),
        ("TIPO_PESSOA_TITULAR", "N", 1),
        ("CPF_CNPJ_TITULAR", "T", 14),
        ("NOME_TITULAR", "T", 80),
        ("NOME_DOC_IDENTIFICACAO", "T", 50),
        ("NUMERO_DOC_IDENTIFICACAO", "T", 30),
        ("ENDERECO_LOGRADOURO", "T", 80),
        ("CODIGO_CIDADE", "T", 7),
        ("CODIGO_UF", "T", 2),
        ("ENDERECO_PAIS", "T", 40),
        ("ENDERECO_CEP", "T", 8),
        ("TELEFONE_PESSOA", "T", 20),
        ("VALOR_RENDA", "N", 14),
        ("DATA_ATUALIZACAO_RENDA", "D", 8),
        ("DATA_INICIO_RELACIONAMENTO_CONTA", "D", 8),
        ("DATA_FIM_RELACIONAMENTO_CONTA", "D", 8),
        ("CNPJ_INSTITUIÇÃO", "T", 14),
        ("NOME_INSTITUIÇÃO", "T", 50),
        ("NOME_SOCIAL", "T", 50),
        ("E-MAIL", "T", 500),
        ("DISPOSITIVO_MOVEL_CADASTRADO", "T", 500),
        ("COMPUTADOR_CADASTRADO", "T", 500),
        ("CONTRATO_CAMBIO", "N", 1),
    ),
    EXTRATO: (
        ("CODIGO_CHAVE_EXTRATO", "N", 18),
        ("COD_COMP_INSTITUIÇÃO", "N", 3),
        ("NUMERO_AGÊNCIA", "N", 5),
        ("NUMERO_CONTA", "T", 20),
        ("TIPO_CONTA", "N", 1),
        ("DATA_LANCAMENTO", "D", 8),
        ("NUMERO_DOCUMENTO", "T", 20),
        ("DESCRICAO_LANCAMENTO", "T", 100),
        ("CODIGO_TIPO_LANCAMENTO", "N", 3),
        ("VALOR_LANCAMENTO", "N", 14),
        ("NATUREZA_LANCAMENTO", "T", 1),
        ("VALOR_SALDO", "T", 14),
        ("NATUREZA_SALDO", "T", 1),
        ("INFO_ADICIONAIS_LOCAL_TRANSACAO", "T", 100),
        ("CNPJ_INSTITUIÇÃO", "T", 14),
        ("NOME_INSTITUIÇÃO", "T", 50),
        ("INTERFACE_TRANSACAO", "N", 1),
        ("CODIGO_CIDADE_TRANSACAO", "T", 7),
        ("CODIGO_UF_TRANSACAO", "T", 2),
        ("IP_TRANSACAO", "T", 39),
        ("PORTA_IP_HOME_BANKING", "T", 5),
        ("MAC_ADDRESS_TRANSACAO", "T", 30),
        ("HORARIO_LANCAMENTO", "T", 8),
        ("TIPO_CHAVE_PIX_TRANSACAO", "T", 1),
        ("CHAVE_PIX_TRANSACAO", "T", 250),
        ("ID_TRANSACAO_PIX", "T", 35),
        ("IDENTIFICADOR_SESSÃO", "N", 18),
    ),
    ORIGEM_DESTINO: (
        ("CODIGO_CHAVE_OD", "N", 18),
        ("CODIGO_CHAVE_EXTRATO", "N", 18),
        ("VALOR_TRANSACAO", "N", 14),
        ("NUMERO_DOCUMENTO_TRANSACAO", "N", 20),
        ("COD_COMP_INSTITUIÇÃO_OD", "N", 3),
        ("NUMERO_AGÊNCIA_OD", "N", 5),
        ("NUMERO_CONTA_OD", "N", 20),
        ("TIPO_CONTA_OD", "N", 1),
        ("TIPO_PESSOA_OD", "N", 1),
        ("CPF_CNPJ_OD", "T", 14),
        ("NOME_PESSOA_OD", "T", 80),
        ("NOME_DOC_IDENTIFICACAO_OD", "T", 50),
        ("NUMERO_DOC_IDENTIFICACAO_OD", "T", 20),
        ("CODIGO_DE_BARRAS", "T", 100),
        ("NOME_ENDOSSANTE_CHEQUE", "T", 80),
        ("DOC_ENDOSSANTE_CHEQUE", "T", 50),
        ("SITUACAO_IDENTIFICACAO", "N", 1),
        ("OBSERVACAO", "T", 1000),
        ("CNPJ_INSTITUIÇÃO_OD", "T", 14),
        ("BENEFICIARIO_BOLETO", "T", 250),
        ("PAGADOR_BOLETO", "T", 250),
        ("CNPJ_INSTITUICAO_EMISSORA_BOLETO", "T", 14),
        ("NOME_INSTITUIÇÃO_EMISSORA_BOLETO", "T", 50),
        ("VALOR_NOMINAL_BOLETO", "N", 14),
        ("TIPO_CHAVE_PIX_PESSOA_OD", "T", 1),
        ("CHAVE_PIX_PESSOA_OD", "T", 250),
        ("COMENTARIO_PIX", "T", 500),
        ("ID_TRANSACAO_PIX", "T", 35),
    ),
}

# Campos sem os quais a linha não identifica nada (decisão do autor: o anexo manda preencher
# todos, mas admite "inexistência da informação", art. 2º, § 1º).
OBRIGATORIOS: dict[str, tuple[str, ...]] = {
    AGENCIAS: ("COD_COMP_INSTITUIÇÃO", "NUMERO_AGÊNCIA"),
    CONTAS: ("COD_COMP_INSTITUIÇÃO", "NUMERO_AGÊNCIA", "NUMERO_CONTA"),
    TITULARES: ("COD_COMP_INSTITUIÇÃO", "NUMERO_AGÊNCIA", "NUMERO_CONTA", "CPF_CNPJ_TITULAR"),
    EXTRATO: (
        "CODIGO_CHAVE_EXTRATO",
        "COD_COMP_INSTITUIÇÃO",
        "NUMERO_AGÊNCIA",
        "NUMERO_CONTA",
        "DATA_LANCAMENTO",
        "VALOR_LANCAMENTO",
        "NATUREZA_LANCAMENTO",
    ),
    ORIGEM_DESTINO: ("CODIGO_CHAVE_OD", "CODIGO_CHAVE_EXTRATO", "VALOR_TRANSACAO"),
}

# Valores monetários (em centavos no arquivo; em reais depois de lidos).
MONETARIOS = frozenset(
    {
        "SALDO_INICIAL_CONTA",
        "SALDO_FINAL_CONTA",
        "VALOR_RENDA",
        "VALOR_LANCAMENTO",
        "VALOR_SALDO",
        "VALOR_TRANSACAO",
        "VALOR_NOMINAL_BOLETO",
    }
)
# Campos com domínio fechado no anexo.
DOMINIOS: dict[str, tuple[str, ...]] = {
    "NATUREZA_LANCAMENTO": ("C", "D"),
    "NATUREZA_SALDO": ("C", "D", ""),
    "TIPO_PESSOA_TITULAR": ("1", "2"),
    "TIPO_PESSOA_OD": ("1", "2", ""),
    "PESSOA_INVESTIGADA": ("0", "1", ""),
    "MOVIMENTACAO_CONTA": ("1", "2", "3", ""),
}
# Campos de documento de pessoa: CPF (11 dígitos) sai sempre descaracterizado na leitura.
DOCUMENTOS = frozenset({"CPF_CNPJ_TITULAR", "CPF_CNPJ_OD"})


@dataclass(frozen=True)
class Rejeicao:
    linha: int
    campo: str
    motivo: str


def classificar_arquivo(nome: str) -> str | None:
    """Tipo do arquivo pelo nome (``EXTRATO.txt``, ``ORIGEM-DESTINO.txt``...); só ``.txt``."""
    nome = nome.upper()
    if not nome.endswith(".TXT"):
        return None
    base = nome[:-4].replace("Ê", "E")
    if "ORIGEM" in base and "DESTINO" in base:
        return ORIGEM_DESTINO
    for tipo in (AGENCIAS, CONTAS, TITULARES, EXTRATO):
        if tipo in base:
            return tipo
    return None


def data_do_leiaute(texto: str) -> date | None:
    """``ddmmaaaa`` -> data; levanta ``ValueError`` se não for uma data."""
    if len(texto) != 8 or not texto.isdigit():
        raise ValueError("data inválida (esperado ddmmaaaa)")
    try:
        return date(int(texto[4:]), int(texto[2:4]), int(texto[:2]))
    except ValueError as erro:
        raise ValueError("data inválida (esperado ddmmaaaa)") from erro


def interpretar_linha(tipo: str, numero: int, bruta: bytes) -> dict | Rejeicao:
    """Valida uma linha contra o leiaute e devolve os campos (datas em ISO, valores em reais).

    A rejeição diz a linha, o campo e o motivo, nunca o conteúdo recusado."""
    try:
        texto = bruta.decode("ascii")
    except UnicodeDecodeError:
        return Rejeicao(numero, "", "caractere fora do padrão ASCII (art. 3º)")
    campos = CAMPOS[tipo]
    valores = texto.split(SEPARADOR)
    if len(valores) != len(campos):
        return Rejeicao(
            numero,
            "",
            f"{len(valores)} campo(s) separados por TAB; o leiaute tem {len(campos)}",
        )
    lidos: dict[str, object] = {}
    for (nome, tipo_campo, tamanho), valor in zip(campos, valores, strict=True):
        valor = valor.strip()
        if len(valor) > tamanho:
            return Rejeicao(numero, nome, f"tamanho acima de {tamanho} caracteres")
        if not valor and nome in OBRIGATORIOS[tipo]:
            return Rejeicao(numero, nome, "campo obrigatório vazio")
        if nome in DOMINIOS and valor not in DOMINIOS[nome]:
            aceitos = ", ".join(v for v in DOMINIOS[nome] if v)
            return Rejeicao(numero, nome, f"valor fora do domínio do leiaute ({aceitos})")
        if tipo_campo == "D":
            if valor in DATAS_SEM_VALOR:
                lidos[nome] = None
                continue
            try:
                lidos[nome] = data_do_leiaute(valor).isoformat()
            except ValueError as erro:
                return Rejeicao(numero, nome, str(erro))
        elif nome in MONETARIOS:
            if valor and not valor.isdigit():
                return Rejeicao(numero, nome, "valor monetário deve ter só dígitos (centavos)")
            lidos[nome] = int(valor) / 100 if valor else None
        elif tipo_campo == "N":
            if valor and not valor.isdigit():
                return Rejeicao(numero, nome, "campo numérico deve ter só dígitos")
            lidos[nome] = valor
        else:
            lidos[nome] = valor
    horario = str(lidos.get("HORARIO_LANCAMENTO") or "")
    if horario and not (
        len(horario) == 6
        and horario.isdigit()
        and int(horario[:2]) < 24
        and int(horario[2:4]) < 60
        and int(horario[4:]) < 60
    ):
        return Rejeicao(numero, "HORARIO_LANCAMENTO", "hora inválida (esperado hhmmss, UTC)")
    return lidos


def montar_linha(tipo: str, **valores: str) -> bytes:
    """Linha no leiaute a partir de ``NOME_DO_CAMPO=valor`` (demais campos vazios). Serve para
    gerar arquivos SINTÉTICOS de teste e de demonstração."""
    nomes = [nome for nome, _, _ in CAMPOS[tipo]]
    desconhecidos = set(valores) - set(nomes)
    if desconhecidos:
        raise ValueError(f"campo fora do leiaute de {tipo}: {sorted(desconhecidos)}")
    return SEPARADOR.join(str(valores.get(nome, "")) for nome in nomes).encode("ascii") + b"\r\n"
