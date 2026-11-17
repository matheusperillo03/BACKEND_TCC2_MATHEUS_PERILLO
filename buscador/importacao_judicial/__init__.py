"""Importação de dados recebidos sob ordem judicial (RF10): módulo separado e desligado por padrão.

O dado sigiloso nunca entra nas tabelas do dado aberto: fica numa base própria, cifrado com chave
própria, marcado com a decisão judicial que o autorizou, e fora do grafo, do relatório e das
exportações. Só dados SINTÉTICOS neste protótipo (ver ``servico``).
"""

from buscador.importacao_judicial.servico import ErroImportacao, Modulo

__all__ = ["ErroImportacao", "Modulo"]
