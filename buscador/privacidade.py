"""Descaracterização de CPF em tudo o que sai da custódia para a interface, a API e os registros
(RNF04).

A custódia guarda os bytes ORIGINAIS de cada captura (o SHA-256 é sobre eles; é a prova) e esses
bytes podem conter CPF completo: o PNCP traz o CPF do fornecedor pessoa física em
``niFornecedor``; o CEIS/CNEP do Portal da Transparência lista sancionados pessoa física; o
arquivo de candidatos do TSE de 2026 traz ``NR_CPF_CANDIDATO`` preenchido. Nada disso pode
aparecer fora da custódia. Este módulo é o ponto único por onde passam os textos derivados da
carga bruta: comparação de capturas, texto da captura (e, com ele, a extração por IA), atributos
de entidades e vínculos, avisos de coleta, detalhes de auditoria, exportações e ``logs``.

Padrão da saída: o mesmo dos dados abertos do CNPJ, em que o CPF do sócio é descaracterizado
"por meio da ocultação dos três primeiros dígitos e dos dois dígitos verificadores" (metadados do
CNPJ, Receita Federal, https://www.gov.br/receitafederal/dados/cnpj-metadados.pdf, p. 3-4; acesso
em 11/11/2026 e 17/11/2026). A troca preserva o comprimento e a pontuação: ``NNN.NNN.NNN-NN`` vira
``***.NNN.NNN-**`` (a forma que o Portal da Transparência usa em ``cpfFormatado``) e ``NNNNNNNNNNN``
vira ``***NNNNNN**`` (a forma do arquivo de Sócios). Preservar o comprimento mantém válidas as
posições dos trechos citados no texto da captura.

O que é tratado como CPF (decisão do autor; a mesma regra da interface, ``src/privacidade.ts``
do front-end):

1. ``NNN.NNN.NNN-NN`` e ``NNNNNNNNN-NN`` (hífen antes dos dois últimos dígitos): sempre;
2. 11 dígitos seguidos, sem letra nem dígito colados, cujos dois dígitos verificadores conferem
   (todo CPF de verdade passa aqui; cerca de 1 em cada 100 números quaisquer de 11 dígitos também
   passa e é ocultado: o erro escolhido é esconder um número que não é CPF, nunca mostrar um
   CPF);
3. 11 dígitos (com ou sem pontuação) num campo cujo nome indica documento de pessoa
   (``CAMPOS_DE_DOCUMENTO``), ou logo depois da palavra "CPF": sempre, mesmo sem DV válido.

CNPJ (14 posições, numérico ou alfanumérico) nunca é alterado: 14 dígitos seguidos não têm 11
"sem dígito colado", e o CNPJ pontuado não tem três grupos de três dígitos seguidos de hífen.
"""

from __future__ import annotations

import logging
import re


def cpf_valido(digitos: str) -> bool:
    """Confere os dois dígitos verificadores de um CPF (11 dígitos, sem pontuação). Sequência de
    um dígito só passa na conta, mas não é CPF."""
    if not re.fullmatch(r"\d{11}", digitos or "") or len(set(digitos)) == 1:
        return False

    def dv(quantos: int) -> int:
        soma = sum(int(digitos[i]) * (quantos + 1 - i) for i in range(quantos))
        resto = (soma * 10) % 11
        return 0 if resto == 10 else resto

    return dv(9) == int(digitos[9]) and dv(10) == int(digitos[10])


# Sem dígito, letra ou "_" colados (seria o meio de um identificador maior, de um CNPJ ou de um
# resumo), e sem "dígito + ponto" antes (seria a parte decimal de um número).
_ANTES = r"(?<![0-9A-Za-z_])(?<![0-9*][.])"
_DEPOIS = r"(?![0-9A-Za-z_])"
_CANDIDATO = re.compile(
    _ANTES + r"(\d{3})(\.?)(\d{3})(\.?)(\d{3})(\s?-\s?|/)?(\d{2})" + _DEPOIS + r"(?!\.\d)"
)
_PALAVRA_CPF_ANTES = re.compile(r"\bcpf\b[^\d]{0,30}$", re.IGNORECASE)

# Nome de campo (sem caixa nem separadores) que indica documento de pessoa nas fontes do MVP:
# PNCP (``niFornecedor``), Portal (``codigoFormatado``, ``cpfFormatado``, ``codigoSancionado``),
# TSE (``NR_CPF_CNPJ_DOADOR``, ``NR_CPF_CANDIDATO``), CNPJ (``cnpj_cpf_socio``,
# ``representante_legal``), os nomes internos (``documento_doador``) e os parâmetros de coleta
# que esperam um CNPJ (``cnpjFornecedor``, ``cnpjOrgao``), onde um CPF pode ser digitado por engano.
_CAMPO_DE_DOCUMENTO = re.compile(
    r"cpf|cnpj|nifornecedor|codigoformatado|codigosancionado|documento|representantelegal"
)


# Campos cujo valor é um identificador numérico da fonte que pode ter 11 dígitos sem ser CPF: o
# sequencial do candidato no TSE (``SQ_CANDIDATO``). Ficam como vieram.
_CAMPO_ISENTO = re.compile(r"^(sqcandidato|candidatos)$")


def _nome_plano(nome: object) -> str:
    return re.sub(r"[^a-z]", "", str(nome).lower())


def campo_de_documento(nome: object) -> bool:
    return bool(_CAMPO_DE_DOCUMENTO.search(_nome_plano(nome)))


def descaracterizar_texto(texto: str, *, contexto_cpf: bool = False) -> str:
    """O texto com todo CPF descaracterizado (mesmo comprimento). ``contexto_cpf`` diz que o texto
    vem de um campo de documento: todo candidato de 11 dígitos é ocultado."""
    if not texto or not any(c.isdigit() for c in texto):
        return texto

    def trocar(achado: re.Match) -> str:
        inicio, p1, meio1, p2, meio2, separador, dv = achado.groups()
        digitos = inicio + meio1 + meio2 + dv
        eh_cpf = (
            contexto_cpf
            or (separador is not None and "-" in separador)
            or cpf_valido(digitos)
            or _PALAVRA_CPF_ANTES.search(texto[max(0, achado.start() - 40) : achado.start()])
        )
        if not eh_cpf:
            return achado.group(0)
        return f"***{p1}{meio1}{p2}{meio2}{separador or ''}**"

    return _CANDIDATO.sub(trocar, texto)


def descaracterizar(valor: object, *, campo: object = "", contexto_cpf: bool = False) -> object:
    """Cópia de um valor JSON (texto, lista, dicionário) com todo CPF descaracterizado. O nome do
    campo vale como contexto para o que está dentro dele. Números, booleanos e ``None`` não mudam
    (um CPF nunca é guardado como número: perderia os zeros à esquerda)."""
    if _CAMPO_ISENTO.fullmatch(_nome_plano(campo)):
        return valor
    contexto = contexto_cpf or campo_de_documento(campo)
    if isinstance(valor, str):
        return descaracterizar_texto(valor, contexto_cpf=contexto)
    if isinstance(valor, dict):
        return {
            (descaracterizar_texto(k) if isinstance(k, str) else k): descaracterizar(
                v, campo=k, contexto_cpf=contexto
            )
            for k, v in valor.items()
        }
    if isinstance(valor, list | tuple):
        return type(valor)(descaracterizar(v, campo=campo, contexto_cpf=contexto) for v in valor)
    return valor


def contem_cpf_completo(texto: str) -> bool:
    """Há no texto algo que ``descaracterizar_texto`` ocultaria? (Para testes e conferências.)"""
    return descaracterizar_texto(texto) != texto


# ---- Registros (``logs``) -------------------------------------------------------------------

_FABRICA_ORIGINAL: object = None


def _limpar_argumento(valor: object) -> object:
    if isinstance(valor, str):
        return descaracterizar_texto(valor)
    if isinstance(valor, BaseException):
        return descaracterizar_texto(str(valor))
    return valor


def instalar_filtro_de_registros() -> None:
    """Faz todo registro de ``logging`` do processo sair com CPF descaracterizado: a mensagem, os
    argumentos de texto (inclusive a URL do registro de acesso) e o texto da exceção. Idempotente.

    Usa a fábrica de registros (e não um filtro por ``logger``) porque filtros de um ``logger``
    não valem para os filhos, e os manipuladores são configurados pelo servidor (uvicorn)."""
    global _FABRICA_ORIGINAL
    if _FABRICA_ORIGINAL is not None:
        return
    original = logging.getLogRecordFactory()
    _FABRICA_ORIGINAL = original

    def fabrica(*args, **kwargs) -> logging.LogRecord:
        registro = original(*args, **kwargs)
        try:
            if isinstance(registro.msg, str):
                registro.msg = descaracterizar_texto(registro.msg)
            if isinstance(registro.args, tuple):
                registro.args = tuple(_limpar_argumento(a) for a in registro.args)
            elif isinstance(registro.args, dict):
                registro.args = {k: _limpar_argumento(v) for k, v in registro.args.items()}
            if registro.exc_info and registro.exc_info[0] is not None and not registro.exc_text:
                # O formatador reutiliza ``exc_text`` quando já está preenchido.
                registro.exc_text = descaracterizar_texto(
                    logging.Formatter().formatException(registro.exc_info)
                )
        except Exception:  # nosec B110 - o registro nunca pode falhar por causa da limpeza
            pass
        return registro

    logging.setLogRecordFactory(fabrica)
