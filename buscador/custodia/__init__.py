"""Cadeia de custódia (RF07): toda carga bruta é guardada como veio, com SHA-256 e metadados."""

from buscador.custodia.servico import (
    PREFIXO_SIMULADO,
    ConteudoGrandeDemais,
    Custodia,
    ResultadoVerificacao,
    marcar_simulado,
    sha256_bytes,
)

__all__ = [
    "PREFIXO_SIMULADO",
    "ConteudoGrandeDemais",
    "Custodia",
    "ResultadoVerificacao",
    "marcar_simulado",
    "sha256_bytes",
]
