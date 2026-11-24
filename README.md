# Back-end do buscador OSINT (MVP, TCC2 - UnB/FCTE)

## O que é

Back-end do MVP de um buscador OSINT para investigação de corrupção e lavagem de dinheiro, feito como TCC na UnB/FCTE. É uma API (FastAPI) que segue o fluxo **caso → coleta de fonte oficial → resolução de entidades → grafo de vínculos → alertas de tipologia explicados → cadeia de custódia e auditoria → relatório**. Só faz coleta passiva de dados abertos (CNPJ da Receita Federal, PNCP, Portal da Transparência, TSE e editais). Os testes e a demonstração usam apenas dados sintéticos.

## Requisitos

- **Com Docker (forma principal):** Docker com Docker Compose. As imagens usadas são `python:3.12-slim-bookworm` (API) e `postgres:16-alpine` (banco). O Compose também constrói o front-end, que precisa estar no repositório irmão `FRONTEND_TCC2_MATHEUS_PERILLO`, na mesma pasta-mãe.
- **Sem Docker:** Python 3.12 (`>=3.12,<3.13`) e [uv](https://docs.astral.sh/uv/) (a imagem Docker usa o uv 0.11; o `uv` baixa o interpretador se faltar). O banco padrão é SQLite.
- `openssl`, para gerar as chaves e as senhas.

## Configuração

O serviço **falha fechado**: não há tokens, senhas nem chaves padrão. Antes de subir:

```bash
cp .env.example .env
openssl rand -hex 32    # gere três valores diferentes: AUDITORIA_CHAVE_HMAC, CUSTODIA_CHAVE e CHAVE_IDENTIFICADORES
openssl rand -hex 24    # gere dois valores: POSTGRES_PASSWORD e POSTGRES_APP_PASSWORD (só no Docker)
```

Preencha no `.env` os campos obrigatórios: `AUDITORIA_CHAVE_HMAC`, `CUSTODIA_CHAVE`, `CHAVE_IDENTIFICADORES` (exigida quando a fonte do TSE está ligada, como no Docker) e, no Docker, `POSTGRES_PASSWORD` e `POSTGRES_APP_PASSWORD`. O `.env` não vai para o git. As demais variáveis estão em [docs/configuracao.md](docs/configuracao.md).

## Como rodar

### Com Docker Compose

```bash
docker compose up --build -d          # API em http://127.0.0.1:8100 e front-end em http://127.0.0.1:8090
curl http://127.0.0.1:8100/health     # {"status":"ok","modo_dev":false}
docker compose exec api python -m buscador.usuarios criar <usuario> admin   # primeiro usuário
docker compose down                   # para tudo (os volumes osint-tcc-* preservam banco e capturas)
```

O PostgreSQL não é publicado no host. Para acessá-lo de fora, use o arquivo extra, que publica em `127.0.0.1:55432`:

```bash
docker compose -f docker-compose.yml -f docker-compose.postgres-porta.yml up -d
```

### Local (sem Docker, com SQLite)

```bash
uv sync
uv run python -m buscador.usuarios criar <usuario> admin   # primeiro usuário
uv run uvicorn buscador.main:app --port 8100 --reload      # API em http://127.0.0.1:8100
```

O acesso é por usuário real (senha, MFA por TOTP e sessão). Os tokens fixos de `TOKENS_DEV` só valem com `MODO_DEV=1`, na máquina do desenvolvedor. O caso de demonstração (dados sintéticos) é carregado por `scripts/carregar_demo.py`; os detalhes estão em [docs/execucao.md](docs/execucao.md).

## Como rodar os testes

```bash
uv sync --locked
uv run ruff check . && uv run ruff format --check .      # lint e formatação
uv run bandit -c pyproject.toml -r buscador scripts      # análise estática de segurança
uv run pytest                                            # testes
```

São os mesmos comandos do pipeline (`.github/workflows/ci.yml`). Os testes específicos do PostgreSQL ficam como "skipped" sem as variáveis `TEST_POSTGRES_URL` e `TEST_POSTGRES_URL_APP`; no pipeline eles rodam sempre.

## Licença

MIT. Ver [LICENSE](LICENSE).

## Documentação

- [docs/execucao.md](docs/execucao.md): como rodar, em detalhe (papéis do banco, migração, demonstração).
- [docs/configuracao.md](docs/configuracao.md): variáveis de ambiente.
- [docs/api.md](docs/api.md): uso da API e rotas.
- [docs/arquitetura.md](docs/arquitetura.md): estrutura do código, custódia, auditoria, retenção, monitoramento, anotações e exportação do grafo.
- [docs/modelo-de-dados.md](docs/modelo-de-dados.md): tabelas e relações.
- [docs/conectores.md](docs/conectores.md): fontes e parâmetros de coleta.
- [docs/tipologias.md](docs/tipologias.md): tipologias T1 a T10, tipologias de lavagem e alerta de combinação.
- [docs/seguranca.md](docs/seguranca.md): usuários, MFA, acesso por caso, criptografia em repouso, dados pessoais e pipeline.
- [docs/ia-local.md](docs/ia-local.md): extração assistida por IA local.
- [docs/importacao-judicial.md](docs/importacao-judicial.md): importação sob ordem judicial (RF10).
- [docs/dados-reais.md](docs/dados-reais.md): fontes reais e piloto com dados públicos.
- [docs/desempenho-grafo.md](docs/desempenho-grafo.md): medição de desempenho do grafo.
- [docs/limitacoes.md](docs/limitacoes.md): limitações do MVP e pendências.
