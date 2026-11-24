# Como rodar, em detalhe

[Voltar ao README](../README.md)

O serviço **falha fechado**: não há tokens, senhas nem chaves padrão. Antes de subir, copie `.env.example` para `.env` e preencha os campos obrigatórios (`AUDITORIA_CHAVE_HMAC`, `CUSTODIA_CHAVE` e, no Docker, `POSTGRES_PASSWORD` e `POSTGRES_APP_PASSWORD`). Gere os valores com `openssl rand -hex 32` (as duas chaves, diferentes entre si) e `openssl rand -hex 24` (senhas).

O acesso é por **usuário real** (senha, MFA por TOTP e sessão). Crie o primeiro com `python -m buscador.usuarios criar <usuario> admin` (ver "H23: usuários, MFA e sessão", em [seguranca.md](seguranca.md)). Os tokens fixos de `TOKENS_DEV` só valem com `MODO_DEV=1`, na máquina do desenvolvedor.

## Com Docker (forma principal)

```bash
cp .env.example .env                  # preencha os campos obrigatórios
docker compose up --build -d          # API em http://127.0.0.1:8100 (o PostgreSQL não é publicado no host)
curl http://127.0.0.1:8100/health     # {"status":"ok","modo_dev":false}
docker compose exec api uv run --no-sync python scripts/carregar_demo.py   # carrega o caso de demonstração
docker compose down                   # para tudo (os volumes osint-tcc-* preservam banco e capturas)
```

O projeto Compose chama-se `osint-tcc`. Sem as variáveis obrigatórias, o `docker compose up` para com a mensagem do que falta. As portas da API e do front-end são publicadas só em `127.0.0.1`; dentro do contêiner a API escuta em 8000.

- **Papéis do banco.** O PostgreSQL tem dois papéis. O dono (`osint`, senha `POSTGRES_PASSWORD`) é usado só pelo serviço `migracao`, que roda `python -m buscador.migrar` uma vez, cria tabelas e gatilhos e concede privilégios. A API conecta com o papel da aplicação (`osint_app`, senha `POSTGRES_APP_PASSWORD`), criado por `docker/postgres-init/01-papel-aplicacao.sh`: ele não é dono de tabela nem de gatilho, e em `auditoria` e `capturas` só tem `SELECT` e `INSERT`.
- **Porta do PostgreSQL.** Não é publicada por padrão. Para acessar o banco do host, habilite de propósito: `docker compose -f docker-compose.yml -f docker-compose.postgres-porta.yml up -d` (publica em `127.0.0.1:55432`).
- **Vindo de uma versão anterior.** Um banco criado antes do horizonte 1 do backlog (sem finalidade do caso, coletas, versão do conector e decisões de alerta) é atualizado no lugar: `python -m buscador.migrar` (o serviço `migracao` do Compose, que roda a cada `up`) cria as tabelas novas, acrescenta as colunas novas às tabelas existentes e refaz as concessões, sem apagar nada (ver [modelo-de-dados.md](modelo-de-dados.md)). Só a cadeia de auditoria versão 1 não é migrável (ver "Auditoria", em [arquitetura.md](arquitetura.md)): nesse caso, recrie os volumes com `docker compose down -v` (os dados do MVP são de demonstração).
- **Documentação OpenAPI.** `/docs`, `/redoc` e `/openapi.json` não exigem token e por isso só existem com `MODO_DEV=1`.
- **Acesso de fora da máquina.** Sem `URL_PUBLICA` a API só responde a requisições feitas pela própria máquina; às demais, `421`. Para atender outra máquina é preciso TLS e `MODO_DEV=0` (ver "Acesso de fora da máquina e TLS", em [seguranca.md](seguranca.md)).

## Sem Docker (desenvolvimento, SQLite)

Requer [uv](https://docs.astral.sh/uv/) e Python 3.12 (o `uv` baixa o interpretador se faltar).

```bash
cp .env.example .env                           # preencha AUDITORIA_CHAVE_HMAC e CUSTODIA_CHAVE
uv sync
uv run python -m buscador.usuarios criar ana.silva admin   # primeiro usuário (ou MODO_DEV=1 + TOKENS_DEV)
uv run uvicorn buscador.main:app --port 8100 --reload
uv run python scripts/carregar_demo.py        # cria o caso de demonstração no banco de .env
uv run pytest                                  # testes
uv run ruff check . && uv run ruff format --check .
uv run bandit -c pyproject.toml -r buscador scripts   # análise estática de segurança
uv run python scripts/testar_fontes_reais.py   # teste de fumaça das fontes REAIS (manual, usa a rede)
```

Os oito testes específicos do PostgreSQL (gatilho de `TRUNCATE`, privilégios do papel da aplicação, trava da auditoria durante o relatório, evolução do esquema e os fluxos dos horizontes 1, 2 e 3 com o papel da aplicação) só rodam com um banco descartável e as variáveis `TEST_POSTGRES_URL` (papel dono) e `TEST_POSTGRES_URL_APP` (papel da aplicação, criado por `docker/postgres-init/01-papel-aplicacao.sh`); sem elas, ficam como "skipped". No pipeline (`.github/workflows/ci.yml`) eles rodam sempre, contra um PostgreSQL descartável do próprio job.

Os tokens fixos (inclusive `dev-analista`, `dev-delegado` e `dev-admin`, publicados em versões anteriores e no front-end em modo simulado) só são aceitos com `MODO_DEV=1`. Sem essa flag, qualquer valor em `TOKENS_DEV` impede a inicialização.

O script `scripts/carregar_demo.py` cria um caso, faz as duas coletas do horizonte 1 (arquivos sintéticos do CNPJ em `tests/fixtures/cnpj` e páginas sintéticas do PNCP em `tests/fixtures/pncp`, servidas por um transporte simulado, sem rede), coleta as fontes do horizonte 2 (termos de contrato, CEIS, CNEP e servidores por transporte simulado, com chave fictícia; TSE e editais das fixtures de `tests/fixtures/h2`), roda as tipologias T1 a T10, marca uma empresa para monitoramento e executa-o uma vez, inclui os demais usuários de desenvolvimento como membros, cria anotações e uma tarefa, exporta o grafo, roda a extração por IA com o provedor **falso** (sem modelo) sobre uma ata sintética de `tests/fixtures/h3`, aceita uma sugestão e deixa a outra pendente, e imprime os alertas, as sugestões de homônimo, a verificação da auditoria e exemplos de `curl`. Com `MODO_DEV=1` ele age como o primeiro usuário de `TOKENS_DEV`; fora dele, informe em `DEMO_USUARIO` o login de um usuário já criado (o script abre para ele uma sessão curta, registrada na auditoria como `auth.sessao_script`). Ele passa pelas rotas HTTP em processo, então a custódia e a auditoria ficam preenchidas como numa sessão real. **Todas as capturas da demonstração saem marcadas como sintéticas** (`simulado://` na origem e `metadados.simulado = true`).
