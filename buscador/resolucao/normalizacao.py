"""Normalização de nomes, documentos, datas e endereços usada na resolução de entidades (RF02)."""

from __future__ import annotations

import re
import unicodedata

MASCARA_CPF = re.compile(r"^\*{3}\d{6}\*{2}$")


def somente_digitos(texto: str | None) -> str:
    return re.sub(r"\D", "", texto or "")


def normalizar_nome(texto: str | None) -> str:
    """Maiúsculas, sem acentos, sem pontuação e com espaços colapsados."""
    if not texto:
        return ""
    sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii")
    sem_pontuacao = re.sub(r"[^A-Za-z0-9 ]+", " ", sem_acento)
    return re.sub(r"\s+", " ", sem_pontuacao).strip().upper()


def mascarar_cpf(cpf: str | None) -> str:
    """Descaracteriza um CPF como o layout da Receita: oculta os 3 primeiros dígitos e os 2 DVs.

    Aceita um CPF já descaracterizado (``***123456**``) e o devolve inalterado. Nunca guardamos
    um CPF completo: se vierem 11 dígitos, só o miolo é mantido.
    """
    cpf = (cpf or "").strip()
    if MASCARA_CPF.fullmatch(cpf):
        return cpf
    digitos = somente_digitos(cpf)
    if len(digitos) == 11:
        return f"***{digitos[3:9]}**"
    if len(digitos) == 6:
        return f"***{digitos}**"
    return ""


def data_layout_para_iso(texto: str | None) -> str:
    """'20260510' (layout do CNPJ) -> '2026-05-10'. Devolve '' se vazio ou inválido."""
    digitos = somente_digitos(texto)
    if len(digitos) != 8 or digitos == "00000000":
        return ""
    return f"{digitos[:4]}-{digitos[4:6]}-{digitos[6:8]}"


def normalizar_endereco(
    tipo_logradouro: str = "",
    logradouro: str = "",
    numero: str = "",
    complemento: str = "",
    bairro: str = "",
    cep: str = "",
    municipio: str = "",
    uf: str = "",
) -> str:
    """Chave de endereço: tipo + logradouro + número + bairro + CEP + município + UF.

    O complemento fica fora da chave (decisão do autor): empresas no mesmo prédio contam como
    mesmo endereço para a tipologia T2. Ele continua guardado no endereço legível.

    Devolve ``""`` quando falta o logradouro ou quando não há CEP nem município: um endereço em
    branco, ou só com a cidade, não identifica um lugar e não pode ligar empresas entre si.
    """
    if not normalizar_nome(logradouro):
        return ""
    if not somente_digitos(cep) and not normalizar_nome(municipio):
        return ""
    partes = [
        normalizar_nome(tipo_logradouro),
        normalizar_nome(logradouro),
        normalizar_nome(numero) or "SN",
        normalizar_nome(bairro),
        somente_digitos(cep),
        normalizar_nome(municipio),
        normalizar_nome(uf),
    ]
    _ = complemento
    return "|".join(partes)


def endereco_legivel(
    tipo_logradouro: str = "",
    logradouro: str = "",
    numero: str = "",
    complemento: str = "",
    bairro: str = "",
    cep: str = "",
    municipio: str = "",
    uf: str = "",
) -> str:
    rua = " ".join(p for p in (tipo_logradouro, logradouro) if p).strip()
    trechos = [t for t in (f"{rua}, {numero}".strip(", "), complemento, bairro) if t]
    cidade = " ".join(p for p in (municipio, uf) if p)
    if cep:
        cidade = f"{cidade} CEP {cep}".strip()
    if cidade:
        trechos.append(cidade)
    return " - ".join(trechos)


# ---- CNPJ -----------------------------------------------------------------------------------
#
# CNPJ alfanumérico. Fontes oficiais (acesso em 17/11/2026):
#
# - Receita Federal, página do projeto: https://www.gov.br/receitafederal/pt-br/
#   acesso-a-informacao/acoes-e-programas/programas-e-atividades/cnpj-alfanumerico. O formato foi
#   instituído pela Instrução Normativa RFB nº 2.229, de 15/10/2024 (altera a IN RFB nº
#   2.119/2022), e é atribuído "a partir de Julho de 2026, exclusivamente a novas inscrições";
#   os CNPJs numéricos existentes não mudam e os dois formatos coexistem.
# - "Perguntas e Respostas" (https://www.gov.br/receitafederal/pt-br/centrais-de-conteudo/
#   publicacoes/perguntas-e-respostas/cnpj/cnpj-alfanumerico.pdf), perguntas 2, 14, 21 e 23: 14
#   posições ``AA.AAA.AAA/AAAA-DV``; as 12 primeiras (raiz de 8 e ordem de 4) podem ter
#   "numerais de 0 a 9 e letras maiúsculas de A até Z"; os 2 DVs continuam numéricos; a máscara é
#   a mesma do CNPJ numérico; a raiz pode ser alfanumérica com ordem numérica, e vice-versa.
# - Serpro/Receita, "Cálculo dos dígitos verificadores de CNPJ alfanumérico"
#   (https://www.gov.br/receitafederal/pt-br/centrais-de-conteudo/publicacoes/
#   documentos-tecnicos/cnpj/manual-dv-cnpj.pdf): cada caractere vale o código ASCII menos 48
#   (0-9 -> 0-9; A -> 17; ... Z -> 42); pesos de 2 a 9 da direita para a esquerda, recomeçando
#   depois do oitavo; resto da soma por 11; resto 0 ou 1 -> DV 0; senão DV = 11 - resto; o segundo
#   DV repete a conta com o primeiro acrescentado. Exemplo do manual: 12.ABC.345/01DE-35.
#
# Para um CNPJ só de dígitos a conta é idêntica à anterior (ASCII - 48 de um dígito é o próprio
# dígito). Decisão do autor: letras minúsculas na ENTRADA são aceitas e convertidas em maiúsculas
# (a fonte só prevê maiúsculas); a pontuação aceita é a da máscara (``.``, ``/``, ``-``) e espaços.

_PONTUACAO_CNPJ = re.compile(r"[.\-/\s]")
CNPJ_COMPLETO = re.compile(r"^[0-9A-Z]{12}[0-9]{2}$")
CNPJ_BASICO = re.compile(r"^[0-9A-Z]{8}$")
_DOZE = re.compile(r"^[0-9A-Z]{12}$")


def somente_alfanumericos(texto: str | None) -> str:
    """Letras ASCII (em maiúsculas) e dígitos do texto; todo o resto é descartado."""
    return re.sub(r"[^0-9A-Z]", "", (texto or "").upper())


def limpar_documento(texto: object) -> str:
    """Documento sem a pontuação da máscara (``.``, ``/``, ``-``, espaços) e em maiúsculas.

    Diferente de ``somente_alfanumericos``, não descarta caractere estranho: quem chama confere o
    resultado com ``CNPJ_COMPLETO``/``CNPJ_BASICO``, e um valor com outro símbolo é recusado."""
    return _PONTUACAO_CNPJ.sub("", texto if isinstance(texto, str) else "").upper()


def normalizar_cnpj(texto: object) -> str:
    """CNPJ de 14 posições (numérico ou alfanumérico) sem pontuação, ou ``""`` se não tem a forma
    de um CNPJ. Não confere os dígitos verificadores (use ``cnpj_valido``)."""
    limpo = limpar_documento(texto)
    return limpo if CNPJ_COMPLETO.fullmatch(limpo) else ""


def normalizar_cnpj_basico(texto: object) -> str:
    """Raiz do CNPJ (8 posições alfanuméricas), a partir da raiz ou do CNPJ completo; ``""`` se
    o texto não tem nenhuma das duas formas. Raiz só de dígitos mais curta é completada com zeros
    à esquerda, como nos arquivos da Receita."""
    limpo = limpar_documento(texto)
    if limpo.isdigit() and 0 < len(limpo) < 8:
        limpo = limpo.zfill(8)
    if CNPJ_BASICO.fullmatch(limpo):
        return limpo
    return limpo[:8] if CNPJ_COMPLETO.fullmatch(limpo) else ""


def calcular_dv_cnpj(doze: str) -> str:
    """Dígitos verificadores do CNPJ (módulo 11) para raiz + ordem, numéricos ou alfanuméricos."""
    base = limpar_documento(doze)
    if not _DOZE.fullmatch(base):
        raise ValueError(
            "São necessárias 12 posições (raiz + ordem), com dígitos ou letras de A a Z, para "
            "calcular o DV."
        )

    def dv(parcial: str) -> str:
        soma = sum((ord(c) - 48) * (2 + posicao % 8) for posicao, c in enumerate(reversed(parcial)))
        resto = soma % 11
        return "0" if resto < 2 else str(11 - resto)

    primeiro = dv(base)
    return primeiro + dv(base + primeiro)


def cnpj_valido(cnpj: str) -> bool:
    limpo = normalizar_cnpj(cnpj)
    return bool(limpo) and calcular_dv_cnpj(limpo[:12]) == limpo[12:]


def formatar_cnpj(cnpj: str) -> str:
    d = normalizar_cnpj(cnpj)
    if not d:
        return cnpj
    return f"{d[:2]}.{d[2:5]}.{d[5:8]}/{d[8:12]}-{d[12:]}"
