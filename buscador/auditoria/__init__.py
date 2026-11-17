"""Trilha de auditoria imutável (RNF02): somente acréscimo, HMAC encadeado e âncora externa."""

from buscador.auditoria.servico import (
    HASH_GENESE,
    MAX_JUSTIFICATIVA,
    VERSAO_CADEIA,
    Ancora,
    AncoraInvalida,
    PontoAncora,
    ResultadoCadeia,
    calcular_hash,
    contar,
    json_canonico,
    limitar_detalhes,
    listar,
    registrar,
    verificar,
)

__all__ = [
    "HASH_GENESE",
    "MAX_JUSTIFICATIVA",
    "VERSAO_CADEIA",
    "Ancora",
    "AncoraInvalida",
    "PontoAncora",
    "ResultadoCadeia",
    "calcular_hash",
    "contar",
    "json_canonico",
    "limitar_detalhes",
    "listar",
    "registrar",
    "verificar",
]
