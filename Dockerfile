# Imagem da API do MVP (Python 3.12 + uv). O contêiner escuta em 8000; o Compose publica em 127.0.0.1:8100.
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /usr/local/bin/uv

WORKDIR /app

# Dependências primeiro, para aproveitar o cache de camadas.
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project

# Código, fixtures sintéticas e scripts (a demo é carregada dentro do contêiner).
COPY buscador ./buscador
COPY scripts ./scripts
COPY tests/fixtures ./tests/fixtures
COPY README.md ./

RUN mkdir -p /app/dados && useradd --create-home --uid 1000 osint && chown -R osint:osint /app
USER osint

EXPOSE 8000
CMD ["uvicorn", "buscador.main:app", "--host", "0.0.0.0", "--port", "8000"]
