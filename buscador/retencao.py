"""Finalidade e prazo de retenção do caso (RNF04, H17).

O caso tem um prazo de retenção (``retencao_ate``, data AAAA-MM-DD, inclusive). Depois dele o caso
fica só leitura: a API recusa novas coletas, novas consultas a entidades e vínculos, a execução de
tipologias e novas decisões, e registra a recusa na auditoria. O que já foi produzido (relatório,
alertas, custódia e trilha) continua legível.

Nada é apagado aqui. As capturas e a auditoria são só de acréscimo, e o descarte dos dados depois
do prazo é decisão do órgão responsável, fora do protótipo (ver README).
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from buscador.modelos import Caso


def hoje_utc() -> date:
    return datetime.now(UTC).date()


def prazo(caso: Caso) -> date | None:
    """Data de retenção do caso; ``None`` para caso sem prazo (anterior à H17) ou valor ilegível."""
    try:
        return date.fromisoformat(caso.retencao_ate) if caso.retencao_ate else None
    except ValueError:
        return None


def vencida(caso: Caso, hoje: date | None = None) -> bool:
    """Verdadeiro a partir do dia seguinte ao prazo (o último dia do prazo ainda vale)."""
    limite = prazo(caso)
    return limite is not None and (hoje or hoje_utc()) > limite
