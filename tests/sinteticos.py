"""Documentos sintéticos gerados a cada execução dos testes.

Todo número com dígitos verificadores válidos pode pertencer a alguém; por isso nenhum CPF de DV
válido fica gravado no repositório: o teste sorteia a base e calcula os verificadores (o mesmo
critério de ``src/test/cpfSintetico.ts`` no front-end). Os CNPJs alfanuméricos também são
sorteados, com o DV calculado pela regra oficial (``buscador.resolucao.normalizacao``).
"""

from __future__ import annotations

import secrets

from buscador.resolucao.normalizacao import calcular_dv_cnpj

_ALFABETO = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_LETRAS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def _dv_cpf(base: str) -> int:
    soma = sum(int(d) * (len(base) + 1 - i) for i, d in enumerate(base))
    resto = (soma * 10) % 11
    return 0 if resto == 10 else resto


def cpf_sintetico() -> str:
    """11 dígitos com verificadores válidos, nunca uma sequência de um dígito só."""
    while True:
        base = "".join(str(secrets.randbelow(10)) for _ in range(9))
        if len(set(base)) == 1:
            continue
        d1 = _dv_cpf(base)
        return f"{base}{d1}{_dv_cpf(base + str(d1))}"


def cpf_com_dv_errado(cpf: str) -> str:
    return cpf[:10] + str((int(cpf[10]) + 1) % 10)


def pontuado(cpf: str) -> str:
    return f"{cpf[:3]}.{cpf[3:6]}.{cpf[6:9]}-{cpf[9:]}"


def mascarado(cpf: str) -> str:
    return f"***{cpf[3:9]}**"


def mascarado_pontuado(cpf: str) -> str:
    return f"***.{cpf[3:6]}.{cpf[6:9]}-**"


def raiz_alfanumerica() -> str:
    """Raiz de 8 posições com ao menos uma letra e ao menos um dígito."""
    while True:
        raiz = "".join(secrets.choice(_ALFABETO) for _ in range(8))
        if any(c in _LETRAS for c in raiz) and any(c.isdigit() for c in raiz):
            return raiz


def cnpj_alfanumerico(raiz: str | None = None, ordem: str = "0001") -> str:
    """CNPJ alfanumérico sintético (14 posições) de DV válido."""
    doze = (raiz or raiz_alfanumerica()) + ordem
    return doze + calcular_dv_cnpj(doze)


def cnpj_formatado(cnpj: str) -> str:
    return f"{cnpj[:2]}.{cnpj[2:5]}.{cnpj[5:8]}/{cnpj[8:12]}-{cnpj[12:]}"
