"""Validações de borda compartilhadas: texto sem caractere de controle e JSON só com finitos.

Usadas pelos esquemas da API, pelos conectores, pela auditoria e pelo relatório, para que nenhum
valor vindo de fora (corpo, cabeçalho, arquivo ou resposta HTTP) chegue ao banco ou a um documento
exportado com quebra de linha forjada, caractere invisível, ``NaN`` ou ``Infinity``.
"""

from __future__ import annotations

import hashlib
import json
import math
import unicodedata

from buscador.privacidade import descaracterizar_texto

# Cc: controle; Cf: formato (largura zero, inversão de direção); Cs: substitutos isolados;
# Zl/Zp: separadores de linha e de parágrafo.
_CATEGORIAS_PROIBIDAS = {"Cc", "Cf", "Cs", "Zl", "Zp"}
_QUEBRAS_PERMITIDAS = {"\n", "\t"}


def caractere_proibido(texto: str, *, permitir_quebras: bool = False) -> str | None:
    """Devolve o primeiro caractere proibido do texto, ou ``None`` se não houver."""
    for c in texto:
        if permitir_quebras and c in _QUEBRAS_PERMITIDAS:
            continue
        if unicodedata.category(c) in _CATEGORIAS_PROIBIDAS:
            return c
    return None


def validar_texto(texto: str, *, permitir_quebras: bool = False) -> str:
    """Recusa caracteres de controle, de formato invisível e substitutos isolados."""
    proibido = caractere_proibido(texto, permitir_quebras=permitir_quebras)
    if proibido is not None:
        raise ValueError(
            f"o texto contém um caractere de controle ou invisível (U+{ord(proibido):04X})"
        )
    return texto


def texto_de_linha(texto: object) -> str:
    """Texto de fonte externa reduzido a uma linha: caracteres proibidos viram espaço e todo CPF
    sai descaracterizado (RNF04; ver ``buscador.privacidade``). É por aqui que passam as mensagens
    de erro e os avisos dos conectores, os valores da comparação de capturas e os do relatório."""
    return descaracterizar_texto(
        "".join(" " if unicodedata.category(c) in _CATEGORIAS_PROIBIDAS else c for c in str(texto))
    )


def sanitizar_json(obj: object) -> object:
    """Cópia serializável em JSON estrito: ``NaN`` e ``±Infinity`` viram ``None``."""
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {str(k): sanitizar_json(v) for k, v in obj.items()}
    if isinstance(obj, list | tuple | set | frozenset):
        return [sanitizar_json(v) for v in obj]
    if obj is None or isinstance(obj, str | int | bool):
        return obj
    return str(obj)


def numero_finito(valor: object) -> float | None:
    """``float(valor)`` quando o resultado é finito; ``None`` em qualquer outro caso."""
    if valor is None or isinstance(valor, bool):
        return None
    try:
        numero = float(valor)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        return None
    return numero if math.isfinite(numero) else None


def _recusar_constante(nome: str) -> float:
    raise ValueError(f"constante numérica não permitida em JSON: {nome}")


def carregar_json_estrito(texto: str) -> object:
    """``json.loads`` que recusa ``NaN``, ``Infinity`` e ``-Infinity``."""
    return json.loads(texto, parse_constant=_recusar_constante)


def resumo_sha256(texto: str) -> str:
    return hashlib.sha256(texto.encode("utf-8", "surrogatepass")).hexdigest()
