"""Conectores de fontes abertas (RF01, RNF10). Um módulo por fonte, interface comum em ``base``."""

from __future__ import annotations

from buscador.conectores.base import (
    Conector,
    ControleTaxa,
    ErroConector,
    FalhaTransitoria,
    FonteNaoConfigurada,
    ResultadoColeta,
    Retentativa,
)
from buscador.conectores.cnpj_arquivo import ConectorCnpjArquivo
from buscador.conectores.edital_arquivo import ConectorEditalArquivo
from buscador.conectores.pncp import ConectorPncp
from buscador.conectores.pncp_termos import ConectorPncpTermos
from buscador.conectores.portal_transparencia import ConectorPortalTransparencia
from buscador.conectores.tse_arquivo import ConectorTseArquivo, ConectorTseBens
from buscador.config import Configuracao

FONTES = (
    "cnpj_arquivo",
    "pncp",
    "portal_transparencia",
    "pncp_termos",
    "tse_arquivo",
    "edital_arquivo",
    "tse_bens",
)


CONECTORES: dict[str, type[Conector]] = {
    "cnpj_arquivo": ConectorCnpjArquivo,
    "pncp": ConectorPncp,
    "portal_transparencia": ConectorPortalTransparencia,
    "pncp_termos": ConectorPncpTermos,
    "tse_arquivo": ConectorTseArquivo,
    "edital_arquivo": ConectorEditalArquivo,
    "tse_bens": ConectorTseBens,
}


def criar_conector(fonte: str, config: Configuracao, **extras) -> Conector:
    """Fábrica: devolve o conector configurado para a fonte pedida."""
    if fonte == "cnpj_arquivo":
        return ConectorCnpjArquivo(
            dir_base=config.cnpj_dir_base,
            max_bytes_arquivo=config.cnpj_max_bytes_arquivo,
            max_registros=config.cnpj_max_registros,
            tentativas=config.coleta_tentativas,
            espera_retentativa_segundos=config.coleta_espera_retentativa_segundos,
            **extras,
        )
    if fonte == "pncp":
        return ConectorPncp(
            url_base=config.pncp_url_base,
            timeout_segundos=config.pncp_timeout_segundos,
            tentativas=config.pncp_tentativas,
            espera_retentativa_segundos=config.pncp_espera_retentativa_segundos,
            max_paginas=config.pncp_max_paginas,
            max_dias_periodo=config.pncp_max_dias_periodo,
            max_bytes_pagina=config.pncp_max_bytes_pagina,
            prazo_total_segundos=config.pncp_prazo_total_segundos,
            **extras,
        )
    if fonte == "portal_transparencia":
        return ConectorPortalTransparencia(
            chave=config.portal_transparencia_chave or None,
            retentativa=Retentativa(
                config.coleta_tentativas,
                config.coleta_espera_retentativa_segundos,
                **({"dormir": extras["dormir"]} if "dormir" in extras else {}),
            ),
            url_base=config.portal_transparencia_url_base,
            timeout_segundos=config.portal_transparencia_timeout_segundos,
            requisicoes_por_minuto=config.portal_transparencia_requisicoes_por_minuto,
            max_paginas=config.portal_transparencia_max_paginas,
            **extras,
        )
    if fonte == "pncp_termos":
        return ConectorPncpTermos(
            url_base=config.pncp_integracao_url_base,
            timeout_segundos=config.pncp_timeout_segundos,
            tentativas=config.pncp_tentativas,
            espera_retentativa_segundos=config.pncp_espera_retentativa_segundos,
            requisicoes_por_minuto=config.pncp_termos_requisicoes_por_minuto,
            max_contratos=config.pncp_termos_max_contratos,
            max_bytes_pagina=config.pncp_max_bytes_pagina,
            **extras,
        )
    if fonte in ("tse_arquivo", "tse_bens"):
        return CONECTORES[fonte](
            # O título eleitoral só é guardado como resumo com chave própria
            # (CHAVE_IDENTIFICADORES; decisão de 23/11/2026). Sem a chave, o conector não é criado.
            resumir_titulo=config.resumidor_de_titulo(),
            dir_base=config.tse_dir_base,
            max_bytes_arquivo=config.tse_max_bytes_arquivo,
            max_registros=config.tse_max_registros,
            max_bytes_fluxo=config.tse_max_bytes_fluxo,
            tentativas=config.coleta_tentativas,
            espera_retentativa_segundos=config.coleta_espera_retentativa_segundos,
            **extras,
        )
    if fonte == "edital_arquivo":
        return ConectorEditalArquivo(
            dir_base=config.editais_dir_base,
            max_bytes_arquivo=config.editais_max_bytes_arquivo,
            max_arquivos=config.editais_max_arquivos,
            limites_pdf=config.limites_pdf(),
            tentativas=config.coleta_tentativas,
            espera_retentativa_segundos=config.coleta_espera_retentativa_segundos,
            **extras,
        )
    raise ErroConector(
        f"Fonte desconhecida: '{fonte[:60]}'. Fontes disponíveis: {', '.join(FONTES)}."
    )


__all__ = [
    "CONECTORES",
    "FONTES",
    "Conector",
    "ConectorCnpjArquivo",
    "ConectorEditalArquivo",
    "ConectorPncp",
    "ConectorPncpTermos",
    "ConectorPortalTransparencia",
    "ConectorTseArquivo",
    "ConectorTseBens",
    "ControleTaxa",
    "ErroConector",
    "FalhaTransitoria",
    "FonteNaoConfigurada",
    "ResultadoColeta",
    "Retentativa",
    "criar_conector",
]
