"""Ponto de entrada do servidor: ``uvicorn buscador.main:app``."""

from buscador.api import criar_app

app = criar_app()
