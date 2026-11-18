# Back-end do buscador OSINT (MVP, TCC2 - UnB/FCTE)

Back-end do MVP do buscador OSINT para investigação de corrupção e lavagem de dinheiro. Implementa a fatia vertical definida em `docs/mvp-escopo.md` do repositório da monografia: **caso → coleta de fonte oficial → resolução de entidades → grafo de vínculos → alertas de tipologia explicados → cadeia de custódia e auditoria → relatório**.

O horizonte 3 do backlog (usuários com MFA, acesso por caso, criptografia em repouso, anotações e tarefas, exportação do grafo, consultas de vínculo no banco e extração assistida por IA local) está descrito em "Horizonte 3 do backlog".

Só há coleta passiva de dados abertos: dados abertos do CNPJ da Receita Federal, PNCP (contratos e termos de contrato), Portal da Transparência (CEIS, CNEP e servidores), dados abertos do TSE (candidatos e receitas de campanha) e editais em texto ou em PDF com camada de texto. O teste dos conectores contra as fontes reais (17/11/2026) e a preparação do piloto com dados públicos reais estão em "Fontes reais e piloto com dados públicos". O horizonte 2 do backlog (novas fontes, tipologias T5 a T10 e monitoramento contínuo) está descrito em "Horizonte 2 do backlog". Nenhum dado pessoal real entra nos testes nem na demonstração: as fixtures são sintéticas, no layout oficial, com CPFs já descaracterizados como manda o layout.

## Como rodar

O serviço **falha fechado**: não há tokens, senhas nem chaves padrão. Antes de subir, copie `.env.example` para `.env` e preencha os campos obrigatórios (`AUDITORIA_CHAVE_HMAC`, `CUSTODIA_CHAVE` e, no Docker, `POSTGRES_PASSWORD` e `POSTGRES_APP_PASSWORD`). Gere os valores com `openssl rand -hex 32` (as duas chaves, diferentes entre si) e `openssl rand -hex 24` (senhas).

O acesso é por **usuário real** (senha, MFA por TOTP e sessão). Crie o primeiro com `python -m buscador.usuarios criar <usuario> admin` (ver "H23: usuários, MFA e sessão"). Os tokens fixos de `TOKENS_DEV` só valem com `MODO_DEV=1`, na máquina do desenvolvedor.

### Com Docker (forma principal)

```bash
cp .env.example .env                  # preencha os campos obrigatórios
docker compose up --build -d          # API em http://127.0.0.1:8100 (o PostgreSQL não é publicado no host)
curl http://127.0.0.1:8100/health     # {"status":"ok"}
docker compose exec api uv run --no-sync python scripts/carregar_demo.py   # carrega o caso de demonstração
docker compose down                   # para tudo (os volumes osint-tcc-* preservam banco e capturas)
```

O projeto Compose chama-se `osint-tcc`. Sem as variáveis obrigatórias, o `docker compose up` para com a mensagem do que falta. As portas da API e do front-end são publicadas só em `127.0.0.1`; dentro do contêiner a API escuta em 8000.

- **Papéis do banco.** O PostgreSQL tem dois papéis. O dono (`osint`, senha `POSTGRES_PASSWORD`) é usado só pelo serviço `migracao`, que roda `python -m buscador.migrar` uma vez, cria tabelas e gatilhos e concede privilégios. A API conecta com o papel da aplicação (`osint_app`, senha `POSTGRES_APP_PASSWORD`), criado por `docker/postgres-init/01-papel-aplicacao.sh`: ele não é dono de tabela nem de gatilho, e em `auditoria` e `capturas` só tem `SELECT` e `INSERT`.
- **Porta do PostgreSQL.** Não é publicada por padrão. Para acessar o banco do host, habilite de propósito: `docker compose -f docker-compose.yml -f docker-compose.postgres-porta.yml up -d` (publica em `127.0.0.1:55432`).
- **Vindo de uma versão anterior.** Um banco criado antes do horizonte 1 do backlog (sem finalidade do caso, coletas, versão do conector e decisões de alerta) é atualizado no lugar: `python -m buscador.migrar` (o serviço `migracao` do Compose, que roda a cada `up`) cria as tabelas novas, acrescenta as colunas novas às tabelas existentes e refaz as concessões, sem apagar nada (ver "Modelo de dados"). Só a cadeia de auditoria versão 1 não é migrável (ver "Auditoria"): nesse caso, recrie os volumes com `docker compose down -v` (os dados do MVP são de demonstração).
- **Documentação OpenAPI.** `/docs`, `/redoc` e `/openapi.json` não exigem token e por isso só existem com `MODO_DEV=1`.

### Sem Docker (desenvolvimento, SQLite)

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

## Variáveis de ambiente

Todas estão em `.env.example`.

| Variável | Padrão | Uso |
|---|---|---|
| `TOKENS_DEV` | vazio | **Só com `MODO_DEV=1`.** `token:usuario:perfil`, separados por vírgula. Perfis: `analista`, `delegado`, `admin`. Com `MODO_DEV=0`, precisa ficar vazia: preenchida, impede a inicialização |
| `CUSTODIA_CHAVE` (ou `CUSTODIA_CHAVE_ARQUIVO`) | **obrigatória, sem padrão** | Chave mestra da criptografia em repouso, 32 bytes em hexadecimal. Fica só no ambiente ou num arquivo, nunca no banco. As demais variáveis do horizonte 3 estão em "Horizonte 3 do backlog" |
| `AUDITORIA_CHAVE_HMAC` | **obrigatória, sem padrão** | Chave do HMAC da cadeia de auditoria (mínimo de 32 caracteres). Fica só no ambiente, nunca no banco |
| `POSTGRES_PASSWORD`, `POSTGRES_APP_PASSWORD` | **obrigatórias no Compose** | Senha do papel dono do banco e senha do papel da aplicação |
| `MODO_DEV` | `0` | `1` aceita os tokens fixos de `TOKENS_DEV` (sem senha e sem MFA) e liga `/docs`, `/redoc` e `/openapi.json`. Só para a máquina do desenvolvedor |
| `DATABASE_URL` | `sqlite:///./dados/buscador.db` | SQLAlchemy. No Compose a API recebe a URL do papel `osint_app` |
| `DB_CRIAR_ESQUEMA` | `1` | `0` quando o esquema é criado antes pelo papel dono (`python -m buscador.migrar`), como no Compose |
| `DADOS_DIR` | `./dados` | Capturas brutas cifradas (`<DADOS_DIR>/capturas/<aa>/<sha256>.gcm`) e âncora da auditoria (`<DADOS_DIR>/ancora/auditoria.json`) |
| `CORS_ORIGENS` | `http://localhost:5190,http://localhost:8090` | Origens do front-end (Vite e nginx). O CORS não aceita credenciais |
| `MAX_CORPO_BYTES` | `262144` | Tamanho máximo do corpo de uma requisição; acima disso, `413` |
| `CNPJ_DIR_BASE` | vazio (fonte desativada) | Raiz de ingestão do `cnpj_arquivo`. O `diretorio` da coleta é relativo a ela e não pode sair dela. No Compose: `/app/tests/fixtures` |
| `CNPJ_MAX_BYTES_ARQUIVO`, `CNPJ_MAX_REGISTROS` | `16777216`, `100000` | Limite de bytes por arquivo e de registros por coleta do CNPJ |
| `PNCP_URL_BASE` | `https://pncp.gov.br/api/consulta` | Base da API de consulta do PNCP |
| `PNCP_TIMEOUT_SEGUNDOS`, `PNCP_TENTATIVAS` | `30`, `3` | Timeout e retentativas (429/5xx e erros de rede, com espera crescente) |
| `PNCP_MAX_PAGINAS`, `PNCP_MAX_DIAS_PERIODO` | `20`, `366` | Teto de páginas e largura máxima do período por coleta |
| `PNCP_MAX_BYTES_PAGINA`, `PNCP_PRAZO_TOTAL_SEGUNDOS` | `8388608`, `120` | Limite de bytes por página e prazo total de uma coleta |
| `PORTAL_TRANSPARENCIA_CHAVE` | vazio | Chave da API do Portal da Transparência; vazia, a fonte fica "não configurada". As demais variáveis do horizonte 2 estão em "Horizonte 2 do backlog" |
| `COLETA_TENTATIVAS`, `COLETA_ESPERA_RETENTATIVA_SEGUNDOS` | `3`, `1` | Retentativa dos conectores sem ajuste próprio (`cnpj_arquivo`, `tse_arquivo`, `edital_arquivo` e Portal da Transparência): tentativas por operação e espera, que cresce a cada tentativa |
| `RESOLUCAO_MAX_GRUPO` | `50` | Maior grupo (mesmo endereço ou mesmo nome) expandido par a par |
| `T1_DIAS_MINIMOS`, `T2_MINIMO_EMPRESAS`, `T3_RAZAO_MINIMA` | `180`, `3`, `10` | Limiares das tipologias (a T4 não tem limiar) |

## Uso da API

Todas as rotas, menos `/health`, `/me`, `/tipologias`, `/ia`, `/importacao-judicial/estado` e `/auth/*`, exigem `Authorization: Bearer <token>` **e** `X-Justificativa: <texto>`. O token é o da sessão, obtido em `POST /auth/login` e `POST /auth/mfa` (ou, só com `MODO_DEV=1`, um token fixo de `TOKENS_DEV`). As rotas de caso exigem ainda que o usuário seja membro do caso (`403` se não for). Sem justificativa a resposta é `400`; sem token ou com token inválido, `401`. Cabeçalhos HTTP só carregam ASCII com segurança: o front-end deve enviar a justificativa com `encodeURIComponent(texto)` (o servidor sempre decodifica as sequências `%XX`; texto cru em UTF-8 também é aceito).

Limites de entrada: a justificativa tem até 500 caracteres, precisa conter letras ou números e não aceita quebra de linha, caractere de controle nem caractere invisível (`400`). Título, número do procedimento, termos de busca e parâmetros também recusam caracteres de controle. O corpo tem até `MAX_CORPO_BYTES` (`413`). `NaN` e `Infinity` são recusados em qualquer número (`422`). As respostas `422` de validação não devolvem o valor recusado.

```bash
# Login em duas etapas (usuário real). O 1º acesso devolve "etapa":"cadastro_mfa" com o segredo.
curl -s -X POST http://127.0.0.1:8100/auth/login -H 'Content-Type: application/json' \
  -d '{"usuario":"ana.silva","senha":"<senha>"}'                      # -> token pendente
curl -s -X POST http://127.0.0.1:8100/auth/mfa -H "Authorization: Bearer <token pendente>" \
  -H 'Content-Type: application/json' -d '{"codigo":"123456"}'         # -> token da sessão
H1="Authorization: Bearer $TOKEN"   # o token da sessão (ou, com MODO_DEV=1, um de TOKENS_DEV)
H2='X-Justificativa: verificacao%20de%20vinculo%20societario'
curl -s -H "$H1" -H "$H2" -X POST http://127.0.0.1:8100/casos \
  -H 'Content-Type: application/json' \
  -d '{"titulo":"Caso X","descricao":"","numero_procedimento":"IP 1/2026","finalidade":"Apurar o contrato 1/2026","retencao_ate":"2027-12-31"}'
curl -s -H "$H1" -H "$H2" -X POST http://127.0.0.1:8100/casos/<id>/coletas \
  -H 'Content-Type: application/json' \
  -d '{"fonte":"cnpj_arquivo","parametros":{"diretorio":"cnpj"}}'
curl -s -H "$H1" -H "$H2" -X POST http://127.0.0.1:8100/casos/<id>/coletas \
  -H 'Content-Type: application/json' \
  -d '{"fonte":"pncp","parametros":{"dataInicial":"20261001","dataFinal":"20261031","cnpjOrgao":"00000000000191","max_paginas":2}}'
curl -s -H "$H1" -H "$H2" -X POST http://127.0.0.1:8100/casos/<id>/alertas/executar \
  -H 'Content-Type: application/json' -d '{"parametros":{"t1_dias_minimos":90}}'
curl -s -H "$H1" -H "$H2" -X POST http://127.0.0.1:8100/casos/<id>/alertas/<alerta_id>/decisao \
  -H 'Content-Type: application/json' \
  -d '{"decisao":"descartado","justificativa":"Empresa regular; conferido no cadastro"}'
curl -s -H "$H1" -H "$H2" -X POST http://127.0.0.1:8100/casos/<id>/coletas/<coleta_id>/recoletar
curl -s -H "$H1" -H "$H2" 'http://127.0.0.1:8100/casos/<id>/relatorio?formato=md'
```

### Rotas (contrato v0, com o horizonte 1 do backlog)

As rotas novas e alteradas no horizonte 2 (novas fontes, `GET /tipologias`, monitoramento e avisos) estão em "Horizonte 2 do backlog", logo abaixo desta seção.

As rotas marcadas com **(novo)** ou **(alterado)** mudaram no horizonte 1 (H12 a H17 e H31). O contrato em `docs/mvp-escopo.md` (seção 5) da monografia é o da versão anterior; este README é a referência atual.

| Método e rota | Descrição |
|---|---|
| `GET /health` | `{"status":"ok"}` |
| `GET /me` | `{"usuario","perfil"}` |
| `POST /casos` **(alterado)** | Corpo `{"titulo","descricao"?,"numero_procedimento"?,"finalidade","retencao_ate"}`. `finalidade` (texto, 1 a 2000 caracteres, com letras ou números) e `retencao_ate` (data `AAAA-MM-DD`, hoje ou futura) são obrigatórias; sem elas, `422`. Resposta `201`: `{"id","titulo","descricao","numero_procedimento","criado_por","criado_em","finalidade","retencao_ate","somente_leitura"}` |
| `GET /casos` **(alterado)** | Lista casos, com `finalidade`, `retencao_ate` e `somente_leitura` |
| `GET /casos/{id}` **(alterado)** | Caso com `contagens` de entidades, alertas, capturas, coletas e sugestões. Continua disponível depois do prazo de retenção |
| `POST /casos/{id}/coletas` **(alterado)** | `{"fonte":"cnpj_arquivo"\|"pncp"\|"portal_transparencia","parametros":{}}` → `{"fonte","captura_ids","entidades_novas","entidades_atualizadas","arestas_novas","sugestoes_novas","registros","avisos","coleta_id","situacao","tentativas","motivo","conector_nome","conector_versao","recoleta_de"}`. Erro de coleta: `422` com `detail` em português. Toda tentativa fica na auditoria (`coleta.iniciar` antes de qualquer consulta, depois `coleta.executar` ou `coleta.falhou`) e no histórico de coletas, e as capturas já guardadas continuam registradas mesmo quando a coleta falha. Caso com retenção vencida: `403` |
| `GET /casos/{id}/coletas` **(novo)** | Histórico das coletas do caso, da mais recente para a mais antiga, inclusive as que falharam: `[{"id","fonte","parametros","conector_nome","conector_versao","situacao":"sucesso"\|"falha"\|"em_andamento","tentativas","motivo","registros","avisos","usuario","iniciado_em","concluido_em","recoleta_de","captura_ids"}]` |
| `POST /casos/{id}/coletas/{coleta_id}/recoletar` **(novo)** | Repete a coleta (mesma fonte e mesmos parâmetros) e compara as capturas novas com as da coleta original. Resposta: a da coleta, com `recoleta_de` e `comparacao` (formato abaixo). Nada da coleta original é alterado nem apagado. Coleta de outro caso: `404`; retenção vencida: `403` |
| `GET /casos/{id}/coletas/{coleta_id}/comparacao?com=` **(novo)** | Compara as capturas de uma coleta com as de outra do mesmo caso e da mesma fonte. Sem `com`, a outra é a coleta que esta repetiu (`recoleta_de`); se não for recoleta, `404` |
| `GET /casos/{id}/entidades?q=&tipo=` | Busca por nome, CNPJ (completo ou básico) ou CPF parcial (`***123456**` ou só os 6 dígitos). `tipo` é opcional. Retenção vencida: `403` |
| `GET /casos/{id}/entidades/{eid}` | Entidade com `atributos`, `captura_id`, `fontes` (todas as capturas que a alimentaram) e `sugestoes`. Retenção vencida: `403` |
| `GET /casos/{id}/grafo?centro=&profundidade=2` | Vizinhança (subgrafo induzido). Sem `centro`, o grafo inteiro. Retenção vencida: `403` |
| `GET /casos/{id}/caminho?de=&para=` | Caminho mais curto ignorando a direção; `{"nos":[],"arestas":[]}` se não houver. Retenção vencida: `403` |
| `POST /casos/{id}/alertas/executar` **(alterado)** | Corpo opcional `{"parametros":{"t1_dias_minimos","t2_minimo_empresas","t3_razao_minima"},"tipologias":["T1","T2","T3","T4"]}`. Reconcilia os alertas das tipologias executadas (as outras ficam): o alerta que continua detectado mantém o `id`, a situação e as decisões; o que deixou de ser detectado é apagado se estava pendente e fica com `ativo: false` se já tinha decisão. A resposta vem por severidade. Limiares fora da faixa (T1 ≥ 0, T2 ≥ 2, T3 > 0), `NaN` e `Infinity` dão `422`. Retenção vencida: `403` |
| `GET /casos/{id}/alertas` **(alterado)** | Alertas por severidade (alta, média, baixa), depois tipologia e título; inativos no fim. Cada um: `{"id","tipologia":"T1"..."T4","tipologia_nome","titulo","motivo","severidade":"alta"\|"media"\|"baixa","prefere_evitar":"falso_negativo"\|"falso_positivo","politica_erro","entidades","evidencias","parametros","criado_em","situacao":"pendente"\|"confirmado"\|"descartado","decisao","decisoes","ativo"}`. `decisao` é a decisão em vigor (`{"decisao","justificativa","usuario","perfil","decidido_em"}` ou `null`) e `decisoes`, o histórico completo |
| `POST /casos/{id}/alertas/{alerta_id}/decisao` **(novo)** | Corpo `{"decisao":"confirmado"\|"descartado","justificativa"}` (justificativa obrigatória, 1 a 500 caracteres, uma linha, com letras ou números; sem ela, `422`). Devolve o alerta atualizado. Cada chamada acrescenta uma decisão ao histórico e uma entrada `alerta.decidir` à auditoria; decidir de novo é rever, e as decisões anteriores ficam. Alerta de outro caso: `404`; retenção vencida: `403` |
| `GET /capturas/{id}` **(alterado)** | Metadados da captura, agora com `conector_nome`, `conector_versao` e `coleta_id` |
| `GET /capturas/{id}/verificar` | Recalcula o SHA-256 dos bytes guardados |
| `GET /capturas/{id}/comparar?com=` **(novo)** | Compara a captura com outra da mesma fonte e do mesmo caso. Sem `com`, com a captura anterior da mesma origem (`url_ou_arquivo`); se não houver, `404`. Resposta: `{"captura_id","anterior_id","fonte","url_ou_arquivo","coletado_em","anterior_coletado_em","sha256","anterior_sha256","mesmo_hash","tamanho_bytes","anterior_tamanho_bytes","conector","anterior_conector","mesma_versao_conector","registros_comparados","registros":{"adicionados","removidos","alterados","iguais"},"campos_alterados":[],"diferencas":[{"registro","tipo":"alterado"\|"adicionado"\|"removido","campos":[{"campo","antes","depois"}]}],"diferencas_omitidas","observacao"}` |
| `GET /auditoria?caso_id=&limite=&deslocamento=` | Trilha (perfis `delegado` e `admin`), paginada: `limite` de 1 a 1000 (padrão 500) |
| `GET /auditoria/verificar` | Percorre a cadeia, compara com a âncora externa e informa a primeira quebra: `{"integra","entradas","primeira_quebra","motivo","ancora_seq"}` |
| `GET /casos/{id}/relatorio?formato=json\|md` **(alterado)** | Relatório com finalidade e prazo de retenção do caso, alertas por severidade (situação, decisões e política de erro de cada um), entidades, tabela de coletas (situação, tentativas e motivo, com aviso quando alguma falhou), tabela de custódia (com o conector e a versão e a coluna de captura sintética) e verificação da auditoria. As entradas da trilha (`auditoria.entradas_do_caso`) só vão para `delegado` e `admin`; os demais recebem o resumo da integridade e `trilha_restrita: true`. Continua disponível depois do prazo de retenção |

A comparação de coletas (`comparacao` da recoleta e `GET .../comparacao`) tem o formato `{"coleta_id","anterior_id","fonte","conector","anterior_conector","mesmo_conteudo","capturas_iguais","capturas_diferentes","capturas":[<comparação de captura>],"capturas_sem_anterior":[],"capturas_anteriores_sem_correspondente":[]}`. As capturas são pareadas pela origem (`url_ou_arquivo`). `diferencas` lista até 200 registros (o que passar disso entra em `diferencas_omitidas`; os totais em `registros` são sempre exatos).

Ações novas na auditoria: `alerta.decidir`, `coleta.listar`, `coleta.comparar`, `captura.comparar` e `caso.retencao_recusa` (com a ação recusada em `detalhes.acao_recusada`). `coleta.iniciar`, `coleta.executar` e `coleta.falhou` passam a trazer `coleta_id`, o conector (nome e versão) e, as duas últimas, as tentativas.

Os parâmetros de cada fonte são validados com pydantic (`buscador/conectores/parametros.py`); chave desconhecida ou valor fora da faixa dá `422` antes de qualquer consulta.

Parâmetros de `pncp`: `dataInicial` e `dataFinal` (AAAAMMDD, obrigatórios, datas válidas, período de até `PNCP_MAX_DIAS_PERIODO` dias), `cnpjOrgao` (opcional), `max_paginas` (padrão 5, teto `PNCP_MAX_PAGINAS`), `tamanhoPagina` (10 a 500, padrão 500, o máximo da API) e `cnpjFornecedor` (opcional, string ou lista de até 50; filtro aplicado **depois** da coleta, pois a rota do PNCP não filtra por fornecedor).

**Coleta incompleta do PNCP (18/11/2026, depois do piloto com dados reais).** Quando a coleta para com páginas restantes (`paginasRestantes > 0` ao atingir `max_paginas`, ou prazo total esgotado), a resposta de `POST /casos/{id}/coletas` traz `"incompleta": true` e `"paginas_restantes"`, e o **primeiro** aviso começa com `Coleta incompleta:` e diz quantas páginas foram lidas, quantas faltam e o total que o PNCP informou (`totalRegistros` e `totalPaginas`). O histórico (`GET /casos/{id}/coletas`) devolve `incompleta` em cada coleta (derivado do aviso gravado; não há coluna nova) e a auditoria (`coleta.executar`) registra `incompleta` e `paginas_restantes`. A situação continua `sucesso`: o que foi lido entrou no caso, mas o caso está parcial até uma nova coleta com mais páginas ou período menor. O `tamanhoPagina` padrão passou de 50 para 500 (máximo declarado na [OpenAPI](https://pncp.gov.br/api/consulta/v3/api-docs), conferido em 18/11/2026; 501 responde `400`), o que reduz o número de páginas por coleta; o conector passou à versão 1.4.0. Uma recoleta de coleta antiga feita sem `tamanhoPagina` passa a pedir páginas de 500, e a comparação aponta a mudança de versão do conector.

Parâmetros de `cnpj_arquivo`: `diretorio`, **relativo a `CNPJ_DIR_BASE`** (um caminho absoluto só é aceito se estiver dentro dessa raiz), com arquivos no layout oficial (sufixos `EMPRECSV`, `ESTABELE`, `SOCIOCSV`; `;`, latin-1, sem cabeçalho). Links simbólicos não são seguidos, cada arquivo é lido em blocos até `CNPJ_MAX_BYTES_ARQUIVO` e a coleta para em `CNPJ_MAX_REGISTROS`. O caminho do servidor não aparece em mensagens nem na custódia: a origem é gravada como `CNPJ_DIR_BASE/<caminho relativo>`. No Compose a raiz é `/app/tests/fixtures`, e as fixtures sintéticas são o `diretorio` `cnpj`. Para usar um recorte real do dump, monte a pasta no contêiner e aponte `CNPJ_DIR_BASE` para ela.

## Horizonte 2 do backlog (H18, H19, H32 a H36 e H39)

Fatos legais e de fonte usados aqui foram conferidos em fonte oficial em 16/11/2026 e estão em `docs/sistema/fontes-horizonte2.md` (repositório da monografia). O que não pôde ser conferido está em "Pendências do horizonte 2", no fim desta seção, e é configurável.

### Rotas novas e alteradas

| Método e rota | Descrição |
|---|---|
| `POST /casos/{id}/coletas` **(alterado)** | `fonte` aceita também `pncp_termos`, `tse_arquivo` e `edital_arquivo`, e `portal_transparencia` deixou de ser stub. `situacao` ganha o valor `nao_configurado`: fonte sem configuração (sem chave de API) responde `200`, sem capturas, com o motivo em `motivo` e em `avisos`; não é falha e não impede as outras coletas. `"do_caso": true` (em `portal_transparencia` com `recurso` `ceis`/`cnep` e em `pncp_termos`) é trocado pela lista explícita das contratadas ou dos contratos do caso, que fica gravada em `parametros` da coleta |
| `GET /casos/{id}/coletas` **(alterado)** | `situacao` pode ser `nao_configurado` |
| `GET /casos/{id}` **(alterado)** | `contagens` ganha `monitoramentos` (ativos) e `avisos_nao_lidos` |
| `GET /casos/{id}/entidades?tipo=` **(alterado)** | `tipo` aceita também `sancao`, `candidato` e `edital` |
| `GET /casos/{id}/grafo` **(alterado)** | Arestas novas: `sancionada_em`, `servidor_de`, `doou_para` e `edital_de` |
| `POST /casos/{id}/alertas/executar` **(alterado)** | `tipologias` aceita `T1` a `T10` (até 10 itens). `parametros` aceita também `t5_minimo_contratacoes` (2 a 1000), `t8_percentual_limite` e `t8_percentual_limite_reforma` (maiores que 0, até 1000). `parametros.limiares` de cada alerta guarda só os limiares numéricos; a configuração estruturada usada (limite e fonte, critério, expressão) vai nos `parametros` do próprio alerta |
| `GET /casos/{id}/alertas` **(alterado)** | `tipologia` vai de `T1` a `T10` |
| `GET /tipologias` **(novo)** | Catálogo das tipologias, só com o token (não é dado de caso, não exige justificativa): `[{"codigo","nome","historia","severidade","prefere_evitar","politica_erro","regra","base","fontes":[],"parametros":[{"nome","valor","descricao","por_execucao"}]}]`. `valor` é a configuração em vigor; `por_execucao` diz se o parâmetro pode ser sobrescrito em `POST .../alertas/executar` |
| `GET /casos/{id}/monitoramentos` **(novo)** | Entidades marcadas para acompanhamento: `[{"id","caso_id","entidade_id","entidade_rotulo","entidade_tipo","entidade_identificador","periodicidade_horas","ativo","motivo_inativo","justificativa","criado_por","criado_em","proxima_execucao_em","ultima_execucao_em","coletas_de_origem":[{"coleta_id","fonte","iniciado_em"}],"ultima_execucao":<execução>\|null}]` |
| `POST /casos/{id}/monitoramentos` **(novo)** | Corpo `{"entidade_id","periodicidade_horas"}` (1 a 8760; padrão 24). `201` com o monitoramento. A justificativa da requisição (`X-Justificativa`) fica gravada e acompanha cada execução automática na auditoria. Marcar de novo a mesma entidade reativa e atualiza a periodicidade (um monitoramento por entidade). Entidade de fora do caso: `404`; retenção vencida: `403` |
| `POST /casos/{id}/monitoramentos/{mid}/situacao` **(novo)** | Corpo `{"ativo":true\|false,"periodicidade_horas"?}`. Desativar é permitido com o caso vencido; reativar, não (`403`) |
| `POST /casos/{id}/monitoramentos/{mid}/executar` **(novo)** | Executa agora (o agendador faz o mesmo na periodicidade escolhida). Resposta: `{"id","monitoramento_id","origem":"manual"\|"agendador","usuario","iniciado_em","concluido_em","situacao":"sucesso"\|"parcial"\|"falha"\|"sem_coletas"\|"recusada_retencao","coletas":[{"coleta_id","recoleta_de","fonte","situacao","mesmo_conteudo","motivo"}],"avisos_gerados","motivo","avisos":[<aviso>]}`. Desativado: `409`; retenção vencida: `403` |
| `GET /casos/{id}/avisos?nao_lidos=` **(novo)** | Avisos de novidade, do mais recente para o mais antigo: `[{"id","caso_id","monitoramento_id","execucao_id","entidade_id","entidade_rotulo","tipo":"quadro_societario"\|"contrato_novo"\|"sancao_nova"\|"termo_novo","titulo","descricao","detalhes","captura_ids":[],"criado_em","lido_em","lido_por"}]` |
| `POST /casos/{id}/avisos/{aid}/lido` **(novo)** | Marca o aviso como lido (quem e quando) e devolve o aviso |
| `GET /casos/{id}/relatorio` **(alterado)** | Ganha `monitoramentos` (com as execuções), `avisos`, `dados_pessoais` (nota sobre CPF descaracterizado) e, em `resumo`, `coletas_nao_configuradas`, `monitoramentos_ativos`, `avisos` e `avisos_nao_lidos`. O Markdown ganha as seções "Monitoramento contínuo e avisos" e "Dados pessoais" |

Ações novas na auditoria: `coleta.nao_configurada`, `monitoramento.criar`, `monitoramento.reativar`, `monitoramento.alterar`, `monitoramento.listar`, `monitoramento.iniciar`, `monitoramento.executar`, `monitoramento.retencao_recusa`, `monitoramento.falhou`, `aviso.listar` e `aviso.marcar_lido`. As execuções do agendador entram com o usuário de sistema `agendador` (perfil `sistema`) e a justificativa `Monitoramento contínuo (automático): <justificativa de quem marcou a entidade>`.

### Novas fontes (H19)

Todo conector tem nome e versão (gravados em cada coleta e em cada captura), usa a retentativa comum e registra a coleta em `coletas`. Os conectores de API usam também o controle de taxa `ControleTaxa` (RNF12): antes de cada requisição, esperam o que faltar para respeitar o máximo de requisições por minuto configurado. Todos fazem só `GET` (coleta passiva).

| Fonte | Conector (versão) | Parâmetros |
|---|---|---|
| `portal_transparencia` | `portal_transparencia` 1.1.0 | `recurso`: `ceis`, `cnep` ou `servidores`. CEIS e CNEP: `codigoSancionado` (CNPJ ou lista de até 100) ou `"do_caso": true` (as contratadas do caso). Servidores: `orgaoServidorLotacao` ou `orgaoServidorExercicio` (código SIAPE, só dígitos) **e** `cnpjOrgao` (o órgão contratante do caso a que o código corresponde). `max_paginas` (padrão 20, teto `PORTAL_TRANSPARENCIA_MAX_PAGINAS`) |
| `pncp_termos` | `pncp_termos` 1.0.1 | `contratos`: lista de `{"cnpj","ano","sequencial","numero_controle_pncp"?}`, ou `"do_caso": true` (todos os contratos do caso com órgão, ano e sequencial; exige a coleta `pncp` antes). Consulta `GET /v1/orgaos/{cnpj}/contratos/{ano}/{sequencial}/termos` da API de integração do PNCP, contrato a contrato |
| `tse_arquivo` | `tse_arquivo` 1.1.0 | `diretorio` (relativo a `TSE_DIR_BASE`), `uf`, `municipio` e `candidatos` (filtros opcionais por `SG_UF`, `NM_UE` e `SQ_CANDIDATO`; `"do_caso": true` é trocado pela lista dos candidatos do caso), `ano` (só quando nem a coluna `ANO_ELEICAO`/`AA_ELEICAO` nem o nome do arquivo o dizem). Lê `consulta_cand_*.csv`, `receitas_candidatos_*.csv` e ZIP com esses CSV |
| `edital_arquivo` | `edital_arquivo` 1.1.1 | `diretorio` (relativo a `EDITAIS_DIR_BASE`), `numero_controle_pncp_compra` e `cnpjOrgao` (opcionais). Lê arquivos `.txt` e `.pdf` com camada de texto |
| `pncp` | `pncp` 1.4.0 | Os mesmos de antes (`cnpjFornecedor` só aceita o CNPJ de 14 posições ou a raiz de 8; CPF é recusado). A versão 1.2.0 passa a guardar no contrato `valorInicial`, `sequencialContrato`, `numeroControlePncpCompra`, `categoriaProcesso` (id e nome) e `codigoUnidade`, e no órgão a esfera e o poder (`esferaId`, `poderId`) |

**Portal da Transparência.** A chave vem só de `PORTAL_TRANSPARENCIA_CHAVE` e vai no cabeçalho `chave-api-dados`; não entra na URL, nos metadados das capturas nem na auditoria. Sem chave, a coleta é fechada como `nao_configurado`. Limite documentado ([API de dados](https://portaldatransparencia.gov.br/api-de-dados)): 400 requisições por minuto das 6:00 às 23:59 e 700 das 00:00 às 5:59; o padrão de `PORTAL_TRANSPARENCIA_REQUISICOES_POR_MINUTO` é 300 e o teto aceito é 700. Sanção de pessoa física (CPF) não vira registro. A consulta de servidores é só por órgão, nunca por CPF. A correspondência entre o código SIAPE e o CNPJ do órgão contratante é declarada pelo investigador nos parâmetros da coleta (não há tabela oficial conferida que ligue os dois).

**TSE.** Como no CNPJ, o conector não baixa o conjunto (o ZIP de prestação de contas de 2024 tem cerca de 1,34 GB): o operador coloca na pasta o CSV da UF de interesse, ou um ZIP com ele. Até `TSE_MAX_BYTES_ARQUIVO` o arquivo é lido inteiro e a captura guarda os bytes dele; acima disso é lido **em fluxo**, com filtro obrigatório, e a captura guarda o recorte (ver "TSE: arquivo de UF grande"). Codificação: tenta UTF-8 e, se falhar, usa Latin-1 (o leiame diz Latin-1; 2024 veio em UTF-8 e 2026, em Latin-1); a codificação detectada fica nos metadados da captura. O CPF do candidato **nunca é lido** (`NR_CPF_CANDIDATO` não era divulgado em 2024; no arquivo de candidatos de 2026 do DF a coluna veio preenchida, e o conector continua sem lê-la): o candidato é a entidade `candidato`, com chave `ano:SQ_CANDIDATO`, e as doações se ligam a ele por `SQ_CANDIDATO`. As receitas de "Recursos próprios" são ignoradas (nelas o doador é o próprio candidato e a linha exporia o CPF dele). Colunas obrigatórias ausentes param a coleta com a mensagem de que o layout pode ter mudado (RNF12, detecção de mudança de formato).

**Editais.** O arquivo fica na custódia e a T10 o relê de lá; por isso o trecho citado num alerta é o do arquivo cujo SHA-256 está registrado.

**Editais em PDF (17/11/2026).** O conector lê também PDF com camada de texto (`buscador/documentos.py` e `buscador/pdf_extrator.py`):

- **Custódia sobre os bytes originais.** A captura guarda o PDF como veio e o SHA-256 registrado é o do arquivo. O texto é **derivado**: a rota de texto, a T10 e a extração por IA (H22) releem a captura e extraem o texto de novo. As posições de um trecho (linha, coluna e posição na T10; `posicao_inicio`/`posicao_fim` nas sugestões da IA) referem-se ao **texto extraído**, não a deslocamentos dentro do PDF. Para a referência ser reproduzível, os metadados da captura guardam `formato`, `paginas`, `extrator` (biblioteca e versão) e `texto_sha256` (SHA-256 do texto extraído na coleta); a rota de texto devolve `texto_sha256` e `confere_com_coleta`, e a execução da IA grava os dois nos parâmetros. Se uma versão nova da biblioteca extrair outro texto, a diferença aparece.
- **Biblioteca.** [`pypdf`](https://pypdf.readthedocs.io/) (7.1.0 no `uv.lock`), licença **BSD-3-Clause** (metadados do pacote instalado, conferidos em 17/11/2026), compatível com a licença MIT do projeto. É Python puro, sem dependência obrigatória. O código só chama `PdfReader` e `extract_text` de cada página: não renderiza, não executa JavaScript nem ações do documento e não abre anexos (há teste com um PDF sintético que tem ação JavaScript e arquivo anexo).
- **Limites defensivos.** Tamanho do arquivo (`EDITAIS_PDF_MAX_BYTES`, 50 MiB), número de páginas (`EDITAIS_PDF_MAX_PAGINAS`, 300), total de caracteres (`EDITAIS_PDF_MAX_CARACTERES`, 5 milhões) e tempo (`EDITAIS_PDF_TEMPO_SEGUNDOS`, 60 s). A extração roda num **processo separado** (`python -I`, ambiente vazio), morto no prazo: um PDF defeituoso não trava a API nem recebe os segredos dela. O tipo é decidido pela assinatura `%PDF-` do conteúdo, não só pela extensão.
- **Recusas, com o motivo.** PDF criptografado (mesmo com senha de usuário vazia), corrompido ou acima dos limites não vira captura; o motivo sai num aviso da coleta e, se nenhum arquivo da pasta puder ser lido, a coleta falha com os motivos.
- **PDF escaneado.** Relatado como **"sem camada de texto"**: o arquivo é guardado na custódia e o edital fica registrado com `texto_extraivel = false`, mas a T10 e a IA não têm o que ler, e a rota de texto responde `422`. **OCR está fora do escopo** e não foi implementado. Página sem texto dentro de um PDF que tem texto gera aviso com a contagem.
- **Custo.** Cada leitura do texto extrai de novo (num edital real de 22 páginas, 0,4 s). A T10 guarda o texto em memória durante a execução.

### Tipologias T5 a T10

Severidade e política de erro estão na tabela única `buscador/tipologias/politicas.py` (decisão do autor, a validar com o orientador) e aparecem em cada alerta e em `GET /tipologias`. Os parâmetros legais e as listas de termos, com a fonte de cada um, estão em `buscador/tipologias/legislacao.py`.

| Tipologia | Regra | Base | Severidade e erro que prefere evitar |
|---|---|---|---|
| **T5** Fracionamento (H32) | Contratações da mesma unidade do órgão (`orgaoEntidade.cnpj` + `unidadeOrgao.codigoUnidade`), no mesmo exercício (ano da assinatura), de objetos de mesma natureza, cada uma abaixo do limite de dispensa, cuja soma atinge o limite. Usa `valorInicial` (ou `valorGlobal`, se faltar, dizendo qual). "Mesma natureza" é o critério `T5_CRITERIO_NATUREZA`: `fornecedor_e_categoria` (padrão: mesmo fornecedor e mesma `categoriaProcesso`), `categoria` ou `fornecedor`. Inciso I (obras e serviços de engenharia) quando todas as contratações do grupo são das categorias 7 ou 9; senão, inciso II | Lei 14.133/2021, art. 75, I e II, e § 1º. Limites de 2026: R$ 130.984,20 (inciso I) e R$ 65.492,11 (inciso II), Anexo do [Decreto 12.807/2025](https://www.planalto.gov.br/ccivil_03/_ato2023-2026/2025/decreto/d12807.htm). Outros exercícios: `T5_LIMITES_DISPENSA`; sem limite para o ano, os contratos daquele ano não são avaliados. Categorias: Manual de Integração do PNCP, seção 7.21 | média; falso positivo |
| **T6** Empresa sancionada (H33) | Contratada com registro no CEIS ou no CNEP cujo período (`dataInicioSancao` a `dataFimSancao`; sem data final = em aberto) cobre a data de assinatura. Sem data de assinatura ou de início, não avalia | Cadastros CEIS e CNEP (Portal da Transparência) | alta; falso negativo |
| **T7** Sócio servidor (H34) | Sócio pessoa física da contratada cujos seis dígitos visíveis do CPF **e** o nome normalizado são iguais aos de um servidor do órgão contratante. Um alerta por sócio, servidor, empresa e órgão. **Possível coincidência, nunca identificação** | Levantamento com o especialista (RF04); CPF descaracterizado no layout do CNPJ | alta; falso positivo |
| **T8** Aditivo acima do limite (H35) | Soma de `valorAcrescido` positivo dos termos (menos os marcados com `qualificacaoAcrescimoSupressao = false`) acima de 25% do `valorInicial`; 50% quando o objeto do contrato indica reforma de edifício ou de equipamento. **Não usa `valorGlobal` nem `valorAcumulado`** (não foi confirmado se já somam os termos) | Lei 14.133/2021, art. 125 | média; falso negativo |
| **T9** Doador contratado (H39) | Doador de campanha de candidato eleito cuja empresa assinou contrato, em data igual ou posterior à da doação, com órgão do ente do candidato. Doador pessoa jurídica: a própria empresa (CNPJ). Doador pessoa física: **possível coincidência** com um sócio (seis dígitos visíveis do CPF e nome normalizado). Ente: município (`NM_UE` e UF iguais aos do órgão, que é municipal ou sem esfera informada), UF (`SG_UE` igual à UF e órgão estadual ou distrital) ou União (`SG_UE = BR` e órgão federal) | Levantamento com o especialista (RF04); prestação de contas do TSE | média; falso positivo |
| **T10** Cláusula restritiva (H36) | Texto do edital com uma das expressões regulares explícitas (sem modelo de linguagem). Um alerta por edital e por regra, com cada ocorrência: linha, coluna, posição do caractere, trecho literal e contexto. Regras do código: "certidão negativa de quitação" e "certidão de quitação" (com ou sem acentos, atravessando quebra de linha); outras em `T10_EXPRESSOES_EXTRAS` | Manual da Alice (CGU, 2025), seção 2.2, exemplos citados no backlog | baixa; falso negativo |

Toda explicação diz a regra, o motivo com os números do caso, as limitações e traz as evidências ligadas às capturas (fonte, origem e SHA-256). T5, T8 e T9 dizem no motivo que são indício a verificar.

**Fixtures sintéticas** (`tests/fixtures/h2`, geradas por `scripts/gerar_fixtures_h2.py`; apoiam-se nas empresas e nos sócios de `tests/fixtures/cnpj`). O órgão é a "PREFEITURA SINTETICA DE EXEMPLOPOLIS" (municipal, GO), com dez contratos de 2026:

| Tipologia | Dispara | Não dispara |
|---|---|---|
| T5 | DELTA: três serviços de R$ 30 mil (soma R$ 90 mil, limite do inciso II R$ 65.492,11) | EPSILON: duas compras de R$ 20 mil (soma abaixo do limite). ALFA: duas obras de R$ 50 mil (soma R$ 100 mil, abaixo do limite do inciso I) |
| T6 | ZETA: CEIS de 01/01/2025 a 01/01/2027, contrato de 01/03/2026 | ETA: CNEP de 2020 a 2021, contrato de 2026 |
| T7 | Servidor "RICARDO FICTICIO LIMA", `***666666**`, sócio de DELTA, EPSILON e ZETA (três alertas). O CPF vem completo na carga (DV inválido) e só o miolo é guardado | "PEDRO EXEMPLO ALVES" com outros dígitos (homônimo de um sócio); servidora sem vínculo |
| T8 | ZETA: aditivos de R$ 20 mil e R$ 10 mil sobre R$ 100 mil (30%) | ETA: reforma de edifício, aditivo de 30% (limite de 50%). MU: aditivo de 20%; o apostilamento de R$ 50 mil não é acréscimo |
| T9 | ETA (pessoa jurídica) doou ao prefeito eleito de Exemplopolis e é contratada; doador "RICARDO FICTICIO LIMA" possivelmente coincide com o sócio de DELTA, EPSILON e ZETA (três alertas) | Candidata não eleita; candidato eleito de outro município; doadora homônima com outros dígitos; receita de "Recursos próprios" (ignorada na coleta) |
| T10 | `edital_restritivo.txt`: as duas expressões, uma delas atravessando a quebra de linha | `edital_regular.txt` ("regularidade fiscal", "quitação de eventuais multas") |

### Dados pessoais (RNF04) e resolução sem fusão (RF02)

- **Nenhum CPF completo é guardado** em entidades, arestas, alertas, avisos, relatório ou auditoria. Quando a fonte traz o CPF completo (doador no TSE; possivelmente o servidor no Portal), a resolução guarda só os seis dígitos centrais, no mesmo formato do CNPJ (`***NNNNNN**`). A API, a interface e o relatório só têm esse formato para mostrar. A carga bruta fica na custódia como veio da fonte (é o que sustenta a prova) e a API não tem rota que devolva os bytes originais; o que a API deriva dela (comparação de capturas, texto da captura, extração por IA) sai com o CPF descaracterizado (ver "CPF completo nas cargas brutas").
- Do servidor guarda-se só o necessário: nome, dígitos visíveis do CPF, código e nome do órgão SIAPE, situação e tipo. Do candidato, nenhum documento pessoal.
- **Sócio, servidor e doador com o mesmo nome e os mesmos seis dígitos não são fundidos.** Cada origem tem chave própria (`servidor:<órgão>:...`, `doador:...`), e a coincidência gera uma sugestão de resolução (score 0,9, "Possível coincidência") e, nas tipologias T7 e T9, um alerta que diz por que não é identificação: seis dígitos e o nome não identificam uma pessoa.

### Monitoramento contínuo (H18)

O investigador marca uma entidade do caso e escolhe a periodicidade (em horas). O **agendador interno** (`buscador/agendador.py`, uma thread do próprio serviço, sem serviço externo) acorda a cada `MONITORAMENTO_INTERVALO_SEGUNDOS` e executa os monitoramentos ativos cuja próxima execução já venceu. Cada execução:

1. descobre as **coletas de origem** da entidade: as que produziram capturas citadas por ela e as que a consultaram pelos parâmetros (por exemplo, o CEIS consultado para o CNPJ dela, mesmo sem ter achado nada). Para cada combinação de fonte e parâmetros, vale a coleta bem-sucedida mais recente;
2. **refaz** cada uma como uma recoleta comum (H14): nova linha em `coletas` com `recoleta_de`, capturas novas na custódia e comparação com a coleta anterior;
3. compara a "fotografia" dos vínculos da entidade antes e depois e cria um **aviso** por novidade: sócio ou participação societária nova (`quadro_societario`), contrato novo (`contrato_novo`), sanção nova (`sancao_nova`) e termo de contrato novo (`termo_novo`). A saída de um sócio vem da comparação de capturas do H14 (registro que sumiu do arquivo de sócios), porque a resolução nunca apaga arestas;
4. grava a execução em `monitoramento_execucoes` e na auditoria (`monitoramento.iniciar` e `monitoramento.executar`).

**Retenção (H17).** Caso com o prazo vencido não é monitorado: na primeira execução depois do prazo nada é coletado, a execução fica como `recusada_retencao`, o monitoramento é desativado e a recusa entra na auditoria (`monitoramento.retencao_recusa`).

Recoleta que falha, ou de fonte que deixou de estar configurada, não derruba a execução: ela fica `parcial`. Com mais de um processo da API, ligue o agendador em um só (`MONITORAMENTO_AGENDADOR_ATIVO`).

### Variáveis de ambiente novas

| Variável | Padrão | Uso |
|---|---|---|
| `PORTAL_TRANSPARENCIA_CHAVE` | vazio | Chave da API do Portal da Transparência. Vazia: a fonte fica "não configurada" |
| `PORTAL_TRANSPARENCIA_URL_BASE`, `PORTAL_TRANSPARENCIA_TIMEOUT_SEGUNDOS` | `https://api.portaldatransparencia.gov.br`, `30` | Base e timeout |
| `PORTAL_TRANSPARENCIA_REQUISICOES_POR_MINUTO`, `PORTAL_TRANSPARENCIA_MAX_PAGINAS` | `300`, `200` | Controle de taxa (até 700) e teto de páginas por consulta |
| `PNCP_INTEGRACAO_URL_BASE` | `https://pncp.gov.br/api/pncp` | Base da API de integração do PNCP (só os termos de contrato) |
| `PNCP_TERMOS_REQUISICOES_POR_MINUTO`, `PNCP_TERMOS_MAX_CONTRATOS` | `60`, `100` | Controle de taxa (0 desliga; o PNCP não documenta limite, o valor é decisão do autor) e teto de contratos por coleta |
| `TSE_DIR_BASE`, `TSE_MAX_BYTES_ARQUIVO`, `TSE_MAX_REGISTROS`, `TSE_MAX_BYTES_FLUXO` | vazio (fonte desativada), `67108864`, `200000`, `2147483648` | Raiz de ingestão e limites do `tse_arquivo`: teto da leitura integral (e do recorte guardado), de registros por coleta e do arquivo percorrido em fluxo |
| `EDITAIS_DIR_BASE`, `EDITAIS_MAX_BYTES_ARQUIVO`, `EDITAIS_MAX_ARQUIVOS` | vazio (fonte desativada), `4194304`, `50` | Raiz de ingestão e limites do `edital_arquivo` (o teto de bytes vale para o `.txt`) |
| `EDITAIS_PDF_MAX_BYTES`, `EDITAIS_PDF_MAX_PAGINAS`, `EDITAIS_PDF_MAX_CARACTERES`, `EDITAIS_PDF_TEMPO_SEGUNDOS` | `52428800`, `300`, `5000000`, `60` | Limites defensivos da leitura de PDF: tamanho, páginas, caracteres extraídos e prazo da extração |
| `T5_LIMITES_DISPENSA` | vazio (só 2026, do código) | JSON por exercício: `{"2027":{"inciso_i":0,"inciso_ii":0,"fonte":"ato"}}` |
| `T5_MINIMO_CONTRATACOES`, `T5_CRITERIO_NATUREZA` | `2`, `fornecedor_e_categoria` | Mínimo de contratações somadas e critério de "mesma natureza" |
| `T8_PERCENTUAL_LIMITE`, `T8_PERCENTUAL_LIMITE_REFORMA` | `25`, `50` | Percentuais do art. 125 |
| `T9_SITUACOES_ELEITO` | `ELEITO,ELEITO POR QP,ELEITO POR MÉDIA` | Situações de totalização que contam como eleito |
| `T10_EXPRESSOES_EXTRAS` | vazio | Expressões regulares extras, separadas por `;;` (até 20, de até 200 caracteres) |
| `MONITORAMENTO_AGENDADOR_ATIVO`, `MONITORAMENTO_INTERVALO_SEGUNDOS`, `MONITORAMENTO_PERIODICIDADE_MINIMA_HORAS` | `1`, `60`, `1` | Agendador interno |

Configuração inválida (JSON de limites malformado, critério desconhecido, expressão regular que não compila) impede a inicialização, como as demais.

### Pendências do horizonte 2

- **Termos do PNCP sem autenticação:** resolvido em 17/11/2026. O `GET .../termos` respondeu sem autenticação no serviço real (`204` para contrato sem termos; array JSON para contrato com termos). Se um dia a resposta for `401`, o conector guarda a resposta na custódia e explica.
- **Download de editais pelo PNCP:** a API de integração tem `GET /v1/orgaos/{cnpj}/compras/{ano}/{sequencial}/arquivos` e `.../arquivos/{sequencialDocumento}`, sem `security` declarada ([OpenAPI](https://pncp.gov.br/api/pncp/v3/api-docs), conferida em 16 e 17/11/2026), e os dois responderam sem autenticação em 17/11/2026. A leitura de PDF já existe (ver "Editais em PDF"), mas o **conector não baixa** o documento: o investigador baixa o edital (ou usa `scripts/testar_fontes_reais.py --fontes edital --baixar-edital CNPJ/ANO/SEQUENCIAL`) e o põe na pasta de ingestão. Um conector de download fica como trabalho futuro.
- **Formatos não documentados:** conferidos nas fontes reais em 17/11/2026 e mantidos com o tratamento defensivo. Portal: datas das sanções em `DD/MM/AAAA` (`dataFimSancao` pode vir "Sem informação"), `cpfFormatado` dos servidores já descaracterizado (`***.NNN.NNN-**`), página de 15 itens. TSE (2026): `VR_RECEITA` com vírgula decimal e `DT_RECEITA` em `DD/MM/AAAA`.
- **T9:** conferido em 17/11/2026. O leiame de candidatos de 2026 confirma `SG_UE`: `BR` na abrangência federal, a sigla da UF na estadual e o código do município na municipal. No arquivo real do DF de 2026, os eleitos aparecem em `DS_SIT_TOT_TURNO` como `ELEITO`, `ELEITO POR QP` e `ELEITO POR MÉDIA` (o padrão de `T9_SITUACOES_ELEITO`); o leiame não lista os valores, por isso a lista continua configurável.
- **T5:** só os limites de 2026 foram conferidos. "Serviços de manutenção de veículos automotores" (inciso I) não tem categoria própria no PNCP e cai no inciso II. O limite em dobro do § 2º não é considerado. O contrato no PNCP não diz se houve dispensa (a modalidade está na contratação, que não é coletada).
- **T8:** `valorAcrescido` guarda acréscimo ou supressão sem distinguir; a base é o valor inicial sem atualização monetária; a reforma só é reconhecida quando o objeto traz "reforma" perto de "edifício", "prédio" ou "equipamento" (incluir "prédio" é decisão do autor).
- **T10:** só as duas expressões do exemplo; o restante do manual da Alice não foi transcrito.
- **Monitoramento:** a recoleta repete os mesmos parâmetros, então um contrato novo só aparece se cair no período da coleta original do PNCP (não há janela móvel). A recoleta consulta de novo tudo o que a coleta de origem consultou, e não só a entidade monitorada. Sem aviso por e-mail ou por outro canal: os avisos ficam na API e na interface. Sem monitoramento do DODF (RF06 cita publicações), que não é fonte coletada.
- **RNF12:** a detecção de mudança de formato cobre as respostas de API (tipo do corpo) e as colunas dos CSV do TSE; não há comparação automática com a OpenAPI publicada.
- **Conta de serviço do agendador:** o usuário `agendador` existe só na auditoria e nas coletas; não tem token e não acessa a API.

## Horizonte 3 do backlog (H23, H37, H38, H29 e H22)

Implementado em 16/11/2026. Resumo: usuários reais com senha, MFA por TOTP e sessão; acesso por caso (membros com papel); criptografia em repouso das capturas; anotações e tarefas; exportação do grafo em formatos abertos; consultas de vínculo feitas no banco, com medição de desempenho; e extração assistida por modelo de linguagem **local**, desligada por padrão. Os testes estão em `tests/test_horizonte3.py`.

### Rotas novas e alteradas

As rotas `/auth/*` não pedem `X-Justificativa`. Todas as demais continuam pedindo o token (agora o da sessão) e a justificativa.

| Método e rota | Descrição |
|---|---|
| `POST /auth/login` | Corpo `{"usuario","senha"}` → `{"etapa","token","expira_em","usuario","perfil","segredo"?,"otpauth_uri"?}`. `etapa`: `mfa` (falta o código), `cadastro_mfa` (primeiro acesso: vêm o segredo em Base32 e a URI `otpauth://` para o aplicativo autenticador) ou `ativa` (perfil sem MFA obrigatório). O token das etapas pendentes só serve em `POST /auth/mfa`. `401` com a mesma mensagem para login inexistente e senha errada; `429` com a conta bloqueada |
| `POST /auth/mfa` | Com `Authorization: Bearer <token pendente>` e corpo `{"codigo":"123456"}` → sessão `ativa`, com **token novo**. `401` para código inválido ou já usado; `429` no bloqueio |
| `GET /auth/sessao` | `{"usuario","perfil","autenticacao":"sessao"\|"token_dev","mfa_ativo","expira_em","inatividade_minutos"}` |
| `POST /auth/logout` | Encerra a sessão (`204`); o token deixa de valer na hora |
| `POST /auth/senha` | `{"senha_atual","senha_nova"}` → `204`; encerra as outras sessões do usuário |
| `GET /usuarios`, `POST /usuarios`, `POST /usuarios/{usuario}` | Só perfil `admin`. Lista as contas; cria (`{"usuario","nome","perfil","senha"}`); altera (`{"ativo"?,"perfil"?,"senha"?,"redefinir_mfa"?}`). Alterar encerra as sessões da conta. Nunca devolvem hash de senha nem segredo de TOTP |
| `GET /casos` (alterada) | Lista **só os casos de que o usuário é membro**; cada caso traz `meu_papel` |
| `POST /casos` (alterada) | Quem cria o caso vira membro com o papel `responsavel` |
| `GET /casos/{id}` (alterada) | `meu_papel` e contagens novas: `membros`, `anotacoes`, `tarefas_abertas`, `sugestoes_ia_pendentes` |
| Todas as rotas de caso e `/capturas/{id}*` (alteradas) | `403` para quem não é membro do caso ou não tem papel suficiente; a negação é auditada (`caso.acesso_negado`) |
| `GET /casos/{id}/membros` | `[{"usuario","papel","adicionado_por","adicionado_em"}]` |
| `POST /casos/{id}/membros` | Só o responsável. `{"usuario","papel":"responsavel"\|"investigador"\|"leitor"}`: acrescenta o membro ou muda o papel; devolve a lista. `422` para login desconhecido ou se o caso ficaria sem responsável |
| `POST /casos/{id}/membros/{usuario}/remover` | Só o responsável; devolve a lista. O caso nunca fica sem responsável |
| `GET /casos/{id}/anotacoes?alvo_tipo=&alvo_id=` | `[{"id","alvo_tipo":"entidade"\|"alerta","alvo_id","alvo_rotulo","autor","criado_em","texto"}]` |
| `POST /casos/{id}/anotacoes` | `{"alvo_tipo","alvo_id","texto"}` → `201`. Só de acréscimo (não há edição nem exclusão) |
| `GET /casos/{id}/tarefas?situacao=` e `GET /casos/{id}/tarefas/{tid}` | Tarefa: `{"id","titulo","descricao","responsavel","situacao","prazo","atrasada","criado_por","criado_em","atualizado_em","historico":[{"usuario","quando","alteracoes":{campo:{"de","para"}}}]}` |
| `POST /casos/{id}/tarefas` | `{"titulo","descricao"?,"responsavel"?,"prazo"?}` → `201`. O responsável precisa ser membro do caso |
| `POST /casos/{id}/tarefas/{tid}` | Altera só os campos enviados (`titulo`, `descricao`, `responsavel`, `situacao`: `aberta`\|`em_andamento`\|`concluida`\|`cancelada`, `prazo`); cada alteração acrescenta uma linha ao histórico |
| `GET /casos/{id}/exportacao?formato=zip\|graphml\|csv_entidades\|csv_vinculos` | Arquivo do grafo do caso (`Content-Disposition: attachment`; `X-Conteudo-SHA256` com o resumo do arquivo). Padrão: `zip` |
| `GET /casos/{id}/grafo` e `GET /casos/{id}/caminho` (alteradas) | Mesmo formato de resposta; passam a consultar o banco (H29). `422` quando a resposta passaria de `GRAFO_MAX_NOS` (ou a busca do caminho, de `GRAFO_MAX_NOS_VISITADOS`) |
| `GET /capturas/{id}/verificar` (alterada) | Campo novo `cifrada`. Numa captura cifrada e adulterada, `sha256_recalculado` é `null` |
| `GET /ia` | `{"ativa","provedor","modelo","local":true,"tipos"}`: diz se a extração por IA está ligada |
| `GET /casos/{id}/capturas/{captura_id}/texto` | `{"captura_id","sha256","codificacao","caracteres","truncado","cpfs_descaracterizados","texto"}`: o documento, para conferir o trecho citado. Todo CPF sai descaracterizado, com o mesmo comprimento (17/11/2026); `cpfs_descaracterizados` diz se o original tinha CPF completo |
| `POST /casos/{id}/capturas/{captura_id}/extracao-ia` | Roda o modelo local sobre a captura → execução `{"id","provedor","modelo","modelo_versao","servidor_versao","parametros","prompt_sha256","captura_sha256","situacao","blocos","itens_recebidos","itens_aceitos","itens_recusados","recusas":[{"motivo","tipo","valor","trecho"}],"sugestoes":[...]}`. `409` com a IA desligada ou captura não íntegra; `502` se o modelo falhar ou responder fora do formato (a execução fica registrada como `falha`) |
| `GET /casos/{id}/extracoes-ia` | Execuções do caso, inclusive as que falharam |
| `GET /casos/{id}/sugestoes-ia?situacao=&captura_id=` | `[{"id","extracao_id","captura_id","captura_sha256","tipo","valor","campos","trecho","posicao_inicio","posicao_fim","confianca","situacao":"pendente"\|"aceita"\|"recusada","decidido_por","decidido_em","justificativa_decisao","entidade_id","modelo","provedor"}]` |
| `POST /casos/{id}/sugestoes-ia/{sid}/decisao` | `{"decisao":"aceita"\|"recusada","justificativa"}`. Só a aceitação pode criar entidade. `409` se já decidida |

Ações novas na auditoria: `auth.senha_aceita`, `auth.login`, `auth.login_falhou`, `auth.mfa_falhou`, `auth.logout`, `auth.trocar_senha`, `auth.troca_de_senha_falhou`, `auth.sessao_script`, `usuario.criar`, `usuario.alterar`, `usuario.listar`, `acesso.perfil_negado`, `caso.acesso_negado`, `membro.listar`, `membro.definir`, `membro.remover`, `anotacao.criar`, `anotacao.listar`, `tarefa.criar`, `tarefa.alterar`, `tarefa.ver`, `tarefa.listar`, `exportacao.gerar`, `captura.texto`, `ia.extrair`, `ia.listar_extracoes`, `ia.listar_sugestoes` e `ia.sugestao_decidir`.

### H23: usuários, MFA e sessão

- **Primeiro usuário.** A API só cria contas para quem já é `admin`. O primeiro sai da linha de comando, no servidor: `python -m buscador.usuarios criar <usuario> admin` (no Compose: `docker compose exec api python -m buscador.usuarios criar <usuario> admin`). A senha é pedida no terminal ou lida de `SENHA_INICIAL`. `python -m buscador.usuarios redefinir <usuario> --senha --mfa` recupera uma conta. As duas operações ficam na auditoria como o usuário `cli`.
- **Senha: Argon2id** (biblioteca `argon2-cffi`). Justificativa: a RFC 9106 recomenda a variante Argon2id quando não se sabe qual ataque é mais provável (resiste a canal lateral e a GPU). Parâmetros padrão da biblioteca (perfil de memória reduzida da RFC 9106: 64 MiB, 3 iterações, 4 vias), gravados no próprio hash. **Pendente:** o texto da RFC 9106 não foi relido nesta entrega; a afirmação vem da documentação do `argon2-cffi` e precisa ser conferida em <https://www.rfc-editor.org/rfc/rfc9106> antes de ir para a monografia. Política: mínimo de 12 caracteres (`SENHA_TAMANHO_MINIMO`, decisão do autor), sem o login dentro da senha e sem regras de composição.
- **MFA por TOTP (RFC 6238).** Conferido em <https://www.rfc-editor.org/rfc/rfc6238> (16/11/2026): `TOTP = HOTP(K, T)`, com `T = (tempo Unix - T0) / X`, passo padrão de 30 s; e "The verifier MUST NOT accept the second attempt of the OTP" (seção 5.2). Implementação própria em `buscador/acesso/totp.py`, só com a biblioteca padrão (HMAC-SHA-1, 6 dígitos, 30 s), em vez de uma dependência: o algoritmo é curto e tem vetores de teste oficiais, e os seis vetores SHA-1 do Apêndice B da RFC estão nos testes. Janela de um passo para trás e um para a frente; o último passo aceito fica gravado, e o mesmo código não vale duas vezes. A URI de cadastro segue o "Key Uri Format" do Google Authenticator (<https://github.com/google/google-authenticator/wiki/Key-Uri-Format>, conferido em 16/11/2026), que não faz parte da RFC.
- **Para quem é obrigatório.** `MFA_PERFIS_OBRIGATORIOS` (padrão: os três perfis). O usuário cadastra o segundo fator no primeiro login; o administrador nunca vê o segredo. O segredo fica no banco cifrado com AES-256-GCM (chave fora do banco).
- **Sessão.** Token opaco de 256 bits, guardado no banco só como SHA-256. Decisão: sessão no servidor em vez de JWT, porque logout, desativação da conta e troca de senha precisam valer na hora, e um JWT continua válido até expirar. Expira pela duração máxima (`SESSAO_DURACAO_MINUTOS`, 480) e por inatividade (`SESSAO_INATIVIDADE_MINUTOS`, 30).
- **Força bruta.** `LOGIN_MAX_FALHAS` falhas seguidas (de senha ou de código) bloqueiam a conta por `LOGIN_BLOQUEIO_MINUTOS`. A resposta de falha é a mesma para login inexistente e senha errada, e o tempo de resposta também (um hash fictício é verificado quando o login não existe).
- **Tokens de desenvolvimento.** `TOKENS_DEV` só vale com `MODO_DEV=1` (que também liga `/docs`). Com `MODO_DEV=0`, um `TOKENS_DEV` preenchido impede o serviço de iniciar. Quem usa token de desenvolvimento não passa por senha nem MFA: é só para a máquina do desenvolvedor e para os testes. **Atenção ao atualizar:** um `.env` de versão anterior, com `TOKENS_DEV` preenchido e `MODO_DEV=0`, precisa esvaziar `TOKENS_DEV` (e criar os usuários) ou declarar `MODO_DEV=1`; e precisa ganhar `CUSTODIA_CHAVE`.

### H23: acesso por caso

- Tabela `caso_membros`, com o papel de cada membro. **Só membro vê e age no caso**; o perfil global (`delegado`, `admin`) não abre caso nenhum.
- Papéis: `leitor` lê (todas as rotas `GET` do caso, inclusive relatório e exportação); `investigador` também altera (coletas, tipologias, decisões, anotações, tarefas, monitoramento, extração por IA); `responsavel` também gerencia os membros. Regra única em `Contexto.caso` (`buscador/api/deps.py`): requisição que não é `GET` exige `investigador`, e as rotas de membros exigem `responsavel`.
- Quem não é membro recebe `403` (e não `404`): os identificadores são UUID aleatórios, então a resposta não ajuda a enumerar casos, e o `403` deixa claro ao usuário por que não entrou. Toda negação fica na auditoria (`caso.acesso_negado`, com a rota, o papel exigido e o papel atual); a negação por perfil também (`acesso.perfil_negado`).
- Um caso criado antes desta versão fica com o criador como `responsavel` (feito pela migração, uma vez).
- **O que continua global, por decisão:** `GET /auditoria` segue restrita aos perfis `delegado` e `admin`, sem exigir que sejam membros do caso consultado. A função de auditor (H06) é justamente olhar o uso da ferramenta de fora da equipe do caso; a própria consulta à trilha é auditada.

### H23: criptografia em repouso das capturas

- **AES-256-GCM** (biblioteca `cryptography`, que usa o OpenSSL). Justificativa: é cifra autenticada, então a adulteração do arquivo é detectada na própria decifragem; e a biblioteca é a implementação mais usada em Python, mantida pela Python Cryptographic Authority.
- **Chave fora do banco:** `CUSTODIA_CHAVE` (32 bytes em hexadecimal) ou `CUSTODIA_CHAVE_ARQUIVO`. É obrigatória (o serviço não sobe sem ela) e não pode ser igual à `AUDITORIA_CHAVE_HMAC`. A chave mestra não cifra nada diretamente: cada uso tem uma subchave derivada por HKDF-SHA256 (`capturas`, `totp`).
- **Formato do arquivo** (`<DADOS_DIR>/capturas/<aa>/<sha256>.gcm`): `OSINTGCM` (8 bytes), versão (1), identificador da chave (8), nonce aleatório (12), texto cifrado e etiqueta (16).
- **O SHA-256 da custódia é o do conteúdo original**, calculado antes de cifrar, e entra nos dados associados da cifra: o arquivo de uma captura não decifra no lugar de outra. `GET /capturas/{id}/verificar` decifra e recalcula o SHA-256; arquivo alterado, trocado ou cifrado com outra chave dá `integra: false`.
- **Capturas antigas, em claro,** continuam legíveis e verificáveis (o caminho gravado em cada captura diz em que forma ela está). Elas **não** são cifradas retroativamente.
- **Rotação de chave (limitação).** Há só a metade da rotação: depois de trocar `CUSTODIA_CHAVE`, a chave antiga vai em `CUSTODIA_CHAVES_ANTERIORES` e o que foi cifrado com ela continua legível; o que é gravado depois usa a chave nova. **Não há** o comando que recifra os arquivos antigos com a chave nova (necessário para aposentar de vez uma chave comprometida) nem o que cifra as capturas em claro de versões anteriores. Também não há cofre de chaves (HSM/KMS): a chave fica numa variável de ambiente ou num arquivo do servidor.
- **O que não é cifrado pela aplicação:** o banco (entidades, alertas, anotações, trilha). Fica para a criptografia do volume ou do disco, na infraestrutura. Só o segredo do TOTP é cifrado no banco.
- **Memória:** a carga cifrada passa inteira pela memória (o AES-GCM autentica a mensagem toda); os conectores limitam cada carga a 64 MiB no máximo.

### H37: anotações e tarefas

- **Anotações** em entidades e alertas, com autor, data e texto. São só de acréscimo (gatilhos no banco, como na auditoria): corrigir é anotar de novo. O texto da anotação não é copiado para a trilha de auditoria (só o tamanho e o alvo).
- **Tarefas** do caso, com título, descrição, responsável (membro do caso), situação e prazo. O estado atual fica em `tarefas`; cada criação ou alteração acrescenta uma linha em `tarefa_eventos` (só de acréscimo), com o valor anterior e o novo de cada campo.
- **Retenção (H17):** depois do prazo do caso, anotar, criar e alterar tarefas são recusados (`403`) e a recusa é auditada; ler continua possível.

### H38: exportação do grafo

Formatos abertos: **GraphML** e **CSV** (de entidades e de vínculos), ou um ZIP com os três, um LEIAME e um manifesto com o SHA-256 de cada arquivo. **Cada entidade e cada vínculo leva `captura_id`, `captura_sha256`, `captura_fonte`, `captura_origem` e `captura_coletado_em`**, de modo que o item exportado continua ligado à cadeia de custódia. A exportação fica na auditoria (`exportacao.gerar`), com o formato e o SHA-256 do arquivo entregue.

O que a documentação oficial dos produtos diz sobre importação (consultada em 16/11/2026):

| Produto | O que foi confirmado | Fonte | Pendente |
|---|---|---|---|
| Maltego | O Maltego Graph (Desktop) tem o assistente "Import Graph - 3rd Party Table", que importa um grafo "from a table structured format": o usuário mapeia colunas para entidades e escolhe como ligá-las (*Meshed*, *Sequential*, *Tree* ou *Manual*) | <https://docs.maltego.com/support/solutions/articles/15000010797-import-graph-from-table> | A página não lista as extensões aceitas (CSV, XLSX): **não confirmado**. Importação de **GraphML não foi encontrada** na documentação consultada (a aba "Import \| Export" cita "Export Graph to Table" e "Export Graph as XML": <https://support.maltego.com/en/support/solutions/articles/15000010796-import-export-tab>). O Maltego Graph (Browser) não importa arquivos: "File imports are not yet supported in Maltego Graph (Browser)" (<https://support.maltego.com/en/support/solutions/articles/15000061789-imports-of-xslx-and-csv-files-into-graph-browser->) |
| i2 Analyst's Notebook | **Nada pôde ser confirmado.** A documentação oficial (<https://docs.i2group.com/anb/>, páginas `import_formats.html` e `importing_data.html`) exige login (Cloudflare Access) e não foi lida | — | Conferir, com acesso à documentação, os formatos de texto aceitos e o uso de especificações de importação |

Consequência: o que se entrega é o **formato aberto**, e a importação em cada produto fica **pendente de teste** (nenhum dos dois foi instalado). O `vinculos.csv` foi desenhado para assistentes de importação tabular: uma linha por vínculo, com as colunas das duas pontas (`origem_*`, `destino_*`) e o tipo da ligação, que é a forma que o assistente do Maltego descreve (colunas mapeadas para entidades, ligadas em sequência).

- **CSV:** RFC 4180 (<https://www.rfc-editor.org/rfc/rfc4180>, conferida em 16/11/2026): registros separados por CRLF, linha de cabeçalho, campos entre aspas duplas e aspa interna dobrada. Codificação UTF-8 (a RFC registra o parâmetro `charset`). A RFC é informativa, não um padrão.
- **Injeção de fórmulas:** valor que começa com `=`, `+`, `-`, `@`, tabulação, retorno de carro ou quebra de linha (e as variantes de largura total) ganha um apóstrofo na frente, e todos os campos vão entre aspas, como recomenda a OWASP (<https://community.owasp.org/attacks/CSV_Injection>, conferida em 16/11/2026). A página avisa que a mitigação pode falhar no Excel depois de salvar e reabrir o arquivo. O GraphML leva os valores sem essa alteração.
- **GraphML:** gerado com `xml.etree` (biblioteca padrão), com `<key>` para cada atributo de nó e de aresta e grafo dirigido. **Pendente:** a especificação oficial (<http://graphml.graphdrawing.org/>) estava fora do ar em 16/11/2026 e não foi conferida; a conformidade é verificada nos testes por um leitor independente (`networkx.read_graphml`).
- **Limite:** a exportação monta o arquivo em memória; serve para casos do tamanho do MVP.

### H29: escala do grafo

A vizinhança e o caminho deixaram de montar o grafo inteiro do caso em memória (NetworkX) a cada consulta: `buscador/grafo/sql.py` faz a busca em largura **no PostgreSQL**, pelos índices de `arestas`, e carrega só o subgrafo pedido. O caminho usa busca bidirecional. Não há serviço novo. Os números, o método, o script que reproduz (`scripts/medir_grafo.py`) e o motivo de um banco de grafos dedicado ficar como alternativa futura estão em [`docs/desempenho-grafo.md`](docs/desempenho-grafo.md).

Resumo da medição (grafo sintético, PostgreSQL 16 num contêiner local, 16/11/2026; mediana por consulta):

| Arestas (nós) | Antes: NetworkX, grafo inteiro em memória | Agora: busca no banco | Memória do processo (antes → agora) |
|---:|---|---|---|
| 10 mil (5 mil) | vizinhança 141 a 152 ms; caminho 105 ms | vizinhança 1,5 a 6,5 ms; caminho 3,1 ms | 106 → 80 MiB |
| 100 mil (50 mil) | vizinhança 1,7 s; caminho 1,2 s | vizinhança 1,6 a 5,6 ms; caminho 4,6 ms | 369 → 82 MiB |
| 1 milhão (500 mil) | vizinhança 18,8 a 20,0 s; caminho 13,2 s | vizinhança 1,8 a 8,3 ms; caminho 11,3 ms | 2.557 → 83 MiB |

A consulta recursiva única (`WITH RECURSIVE`) também foi medida e ficou mais lenta que a busca nível a nível, com tempo que cresce com o grafo (251 a 667 ms com 1 milhão de arestas); por isso não é a usada. O RNF06 fala em milhões de nós: o que foi medido é 1 milhão de arestas e 500 mil nós.

### H22: extração assistida por IA local

- **Desligada por padrão** (`IA_EXTRACAO_ATIVA=0`). Ligada, só fala com um **servidor local**: a configuração recusa endereço que não seja de loopback, de rede privada ou um nome declarado em `IA_HOSTS_LOCAIS` (por exemplo, o nome do serviço no Compose). Não existe provedor de API externa. Motivo: soberania dos dados; nenhum dado de investigação sai do servidor.
- **Provedor plugável** (`buscador/ia/provedor.py`): o provedor só recebe as instruções, um bloco de texto e o esquema, e devolve o texto gerado. Há dois: `ollama` e `falso` (determinístico, para os testes e a demonstração; exige `MODO_DEV=1`).
- **Ollama** (documentação oficial conferida em 16/11/2026): `POST /api/chat` com `format` igual ao esquema JSON ("Can be `json` or a JSON schema"), `stream: false` e `options` (`temperature`, `seed`) — <https://docs.ollama.com/api/chat>; `GET /api/tags` para o `digest` do modelo ("SHA256 digest identifier of the model contents") — <https://docs.ollama.com/api/tags>; `GET /api/version` — <https://docs.ollama.com/api-reference/get-version>. A mesma documentação mostra que um modelo pode ser **remoto** (`remote_host`, `remote_model`): nesse caso o servidor local só repassa a chamada, e o provedor **recusa** o modelo antes de enviar qualquer texto. O cliente HTTP ignora proxies do ambiente e não segue redirecionamentos. **Testado com um servidor Ollama real em 16/11/2026** (modelo `qwen2.5:7b`), além dos testes automatizados com transporte simulado; a qualidade da extração (precisão e revocação) não foi medida.
- **Regras aplicadas a qualquer provedor** (`buscador/ia/servico.py`):
  1. a resposta precisa ser JSON estrito no esquema `{"itens":[{"tipo","valor","trecho","campos"?,"confianca"?}]}`; fora disso a execução falha e nada é criado; item fora do esquema é recusado;
  2. **o trecho citado precisa estar literalmente no texto da captura**: letras, acentos, maiúsculas e pontuação são comparados caractere a caractere. A única equivalência é a de espaços em branco: uma sequência de espaço, tabulação, quebra de linha ou espaço não separável (NBSP) vale um espaço, porque o modelo costuma devolver com espaço simples o texto que o documento quebra em duas linhas (recusa indevida observada no teste com o Ollama, `qwen2.5:7b`, em 16/11/2026). Nenhum espaço é criado nem removido. O `trecho` gravado é o recorte do texto original, e `posicao_inicio`/`posicao_fim` apontam para ele; item com trecho que não está no documento é recusado;
  3. o valor (e cada campo, como o CNPJ) precisa aparecer dentro do próprio trecho citado, com a mesma equivalência de espaços em branco: um trecho verdadeiro não serve de álibi para um valor inventado;
  4. cada execução grava provedor, modelo, versão (resumo do modelo e versão do servidor), parâmetros, SHA-256 do prompt e SHA-256 da captura, na tabela `extracoes_ia` e na auditoria; os itens recusados ficam registrados com o motivo;
  5. o que passa vira **sugestão pendente**. Só a decisão do investigador (com justificativa, auditada) pode criar entidade: empresa ou órgão com CNPJ válido entra com a mesma chave de resolução das outras fontes; pessoa entra como entidade própria, sem fusão; valor, data e objeto ficam como campo confirmado, sem entidade.
- **Texto não confiável.** O documento pode conter instruções para o modelo. As regras acima não dependem de o modelo obedecer ao prompt: o que ele devolver passa pelas mesmas verificações, e nada vira entidade sem um humano.
- **Dados pessoais.** O CPF no campo `cpf` da sugestão é descaracterizado antes de gravar; o trecho literal pode conter o CPF como está no documento (que já está na custódia).
- **Limitações:** texto puro (UTF-8 ou Latin-1) ou PDF com camada de texto (as posições referem-se ao texto extraído; PDF escaneado não é lido, não há OCR); o texto é dividido em blocos de `IA_TAMANHO_BLOCO` caracteres, e um trecho que atravessa o corte pode não ser encontrado; a decisão sobre a sugestão é única (não há revisão); a qualidade da extração com um modelo real não foi medida.

### Variáveis de ambiente novas

| Variável | Padrão | Uso |
|---|---|---|
| `CUSTODIA_CHAVE` / `CUSTODIA_CHAVE_ARQUIVO` | **obrigatória (uma das duas)** | Chave mestra de 32 bytes em hexadecimal (`openssl rand -hex 32`) |
| `CUSTODIA_CHAVES_ANTERIORES` | vazio | Chaves antigas, só para leitura |
| `MFA_PERFIS_OBRIGATORIOS` | `analista,delegado,admin` | Perfis com segundo fator obrigatório |
| `TOTP_EMISSOR` | `Buscador OSINT` | Nome mostrado no aplicativo autenticador |
| `SENHA_TAMANHO_MINIMO` | `12` | Tamanho mínimo da senha |
| `SESSAO_DURACAO_MINUTOS`, `SESSAO_INATIVIDADE_MINUTOS`, `SESSAO_PENDENTE_MINUTOS` | `480`, `30`, `5` | Duração máxima, inatividade e validade da etapa entre senha e código |
| `LOGIN_MAX_FALHAS`, `LOGIN_BLOQUEIO_MINUTOS` | `5`, `15` | Bloqueio por tentativas |
| `GRAFO_MAX_NOS`, `GRAFO_MAX_NOS_VISITADOS` | `20000`, `200000` | Tetos da vizinhança e da busca de caminho |
| `IA_EXTRACAO_ATIVA` | `0` | Liga a extração por IA local |
| `IA_PROVEDOR`, `IA_OLLAMA_URL`, `IA_MODELO`, `IA_HOSTS_LOCAIS` | `ollama`, `http://127.0.0.1:11434`, vazio, vazio | Provedor, endereço local, modelo e nomes de host aceitos |
| `IA_TIMEOUT_SEGUNDOS`, `IA_MAX_CARACTERES`, `IA_TAMANHO_BLOCO`, `IA_MAX_ITENS`, `IA_TEMPERATURA`, `IA_SEMENTE` | `120`, `200000`, `6000`, `300`, `0`, `0` | Limites e parâmetros de geração |

### Modelo de dados (mudanças do horizonte 3)

A migração é a mesma das versões anteriores (`python -m buscador.migrar`, idempotente no SQLite e no PostgreSQL): cria as tabelas novas, os gatilhos e as concessões, sem apagar nada.

| Tabela | Colunas | Relações |
|---|---|---|
| `usuarios` (nova) | `id`, `usuario` (único), `nome`, `perfil`, `senha_hash` (Argon2id), `ativo`, `mfa_segredo_cifrado`, `mfa_ativo`, `mfa_confirmado_em`, `mfa_ultimo_passo`, `falhas_seguidas`, `bloqueado_ate`, `criado_por`, `criado_em`, `senha_alterada_em` | 1 usuário : N sessões |
| `sessoes` (nova) | `id`, `token_resumo` (SHA-256, único), `conta_id` → `usuarios.id`, `etapa`, `criada_em`, `expira_em`, `ultima_atividade_em`, `encerrada_em`, `motivo_encerramento` | N : 1 com `usuarios` |
| `caso_membros` (nova) | `id`, `caso_id` → `casos.id`, `usuario` (login), `papel`, `adicionado_por`, `adicionado_em`; único (`caso_id`, `usuario`) | 1 caso : N membros; cada caso tem ao menos 1 responsável. Um login participa de N casos (N : N entre casos e usuários, resolvida nesta tabela). `usuario` é o login em texto, sem chave estrangeira, porque no modo de desenvolvimento o usuário de um token não tem linha em `usuarios` |
| `anotacoes` (nova, só de acréscimo) | `id`, `caso_id` → `casos.id`, `alvo_tipo` (`entidade`\|`alerta`), `alvo_id`, `autor`, `criado_em`, `texto` | 1 caso : N anotações; 1 entidade ou 1 alerta : N anotações (`alvo_id` é polimórfico, sem chave estrangeira; a API confere que o alvo é do caso) |
| `tarefas` (nova) | `id`, `caso_id` → `casos.id`, `titulo`, `descricao`, `responsavel` (login, opcional), `situacao`, `prazo`, `criado_por`, `criado_em`, `atualizado_em` | 1 caso : N tarefas; 0 ou 1 responsável por tarefa |
| `tarefa_eventos` (nova, só de acréscimo) | `id`, `tarefa_id` → `tarefas.id`, `caso_id` → `casos.id`, `usuario`, `quando`, `alteracoes` (JSON) | 1 tarefa : N eventos (ao menos 1, o da criação) |
| `extracoes_ia` (nova) | `id`, `caso_id` → `casos.id`, `captura_id` → `capturas.id`, `captura_sha256`, `provedor`, `modelo`, `modelo_versao`, `servidor_versao`, `parametros` (JSON), `prompt_sha256`, `situacao`, `motivo`, `blocos`, `itens_recebidos`, `itens_aceitos`, `itens_recusados`, `recusas` (JSON), `usuario`, `iniciado_em`, `concluido_em` | 1 caso : N execuções; 1 captura : N execuções |
| `sugestoes_ia` (nova) | `id`, `caso_id` → `casos.id`, `extracao_id` → `extracoes_ia.id`, `captura_id` → `capturas.id`, `tipo`, `valor`, `campos` (JSON), `trecho`, `posicao_inicio`, `posicao_fim`, `confianca`, `situacao`, `decidido_por`, `decidido_em`, `justificativa_decisao`, `entidade_id` → `entidades.id` (opcional) | 1 execução : N sugestões; 1 captura : N sugestões; 0 ou 1 entidade por sugestão aceita (1 entidade : N sugestões) |
| `capturas` (sem coluna nova) | `caminho_relativo` termina em `.gcm` quando a carga está cifrada; `metadados.cifra_em_repouso` traz o algoritmo e o identificador da chave | — |
| `arestas` (sem coluna nova) | tipo novo `citado_em` (entidade aceita de uma sugestão → edital), com o trecho citado nos atributos | — |

Gatilhos novos (SQLite e PostgreSQL): `anotacoes_sem_update`, `anotacoes_sem_delete`, `tarefa_eventos_sem_update`, `tarefa_eventos_sem_delete` e, no PostgreSQL, os de `TRUNCATE`. O papel da aplicação só tem `SELECT` e `INSERT` nessas duas tabelas, como na auditoria, nas capturas e nas decisões de alerta. Nenhum índice novo em `arestas`: os de `origem_id` e `destino_id` já existiam e são os usados pelas consultas do H29.

### Pendências do horizonte 3

- **H23:** sem recifragem dos arquivos na troca de chave e sem cifrar as capturas antigas em claro; sem cofre de chaves; sem códigos de recuperação do MFA (a recuperação é o administrador redefinir); sem WebAuthn; sem limite de tentativas por endereço de origem (só por conta); o banco não é cifrado pela aplicação; `GET /auditoria` não é filtrada por caso; a RFC 9106 precisa ser conferida.
- **H37:** sem edição ou exclusão de anotações (por desenho), sem notificação ao responsável pela tarefa; anotações e tarefas não entram no relatório do caso.
- **H38:** importação no Maltego e no i2 **não testada**; formatos do i2 e extensões do Maltego não confirmados na documentação; especificação do GraphML não conferida (site fora do ar); sem formato nativo de nenhum dos dois produtos; H24 (CASE/UCO) continua a fazer.
- **H29:** medido só com grafo sintético e num contêiner local; as tipologias (T1 a T10) e a resolução de entidades ainda carregam o caso inteiro em memória; a exportação também.
- **H22:** testado com um servidor Ollama real (`qwen2.5:7b`, 16/11/2026), mas não com editais reais (em 17/11/2026 um edital real em PDF passou só pela extração de texto, sem a IA); sem medição de qualidade (precisão e revocação); PDF só com camada de texto (sem OCR); decisão única por sugestão; sem serviço `ollama` no Compose (o operador o acrescenta e declara o nome em `IA_HOSTS_LOCAIS`).


## Lavagem de dinheiro e ocultação (tipologias L e combinação)

As tipologias T1 a T10 sinalizam possíveis infrações antecedentes em contratações. As tipologias **L** sinalizam possível ocultação pela interposição de pessoas e empresas (Lei nº 9.613/1998, art. 1º). Todas são **indício cadastral**: nenhuma prova lavagem, e todo alerta continua sendo pista (RF12). Os sinais financeiros (depósitos, "correntes", beneficiário final de operação) não são verificáveis com dado aberto. As fichas, com o critério e a fonte de cada uma, estão em `docs/lavagem/tipologias-lavagem.md` e `docs/lavagem/fontes.md` (monografia); no código, em `buscador/tipologias/lavagem.py`. **Os limiares são decisão do autor, a validar com o orientador.**

### Tipologias sobre dados já coletados

| Tipologia | Regra | Base | Severidade e erro que prefere evitar |
|---|---|---|---|
| **L1** Endereço de registro em massa | Endereço normalizado **com o complemento** em que `L1_MINIMO_ESTABELECIMENTOS` (5) ou mais empresas têm estabelecimento ATIVO, mesmo sem contrato. Marca `abertura_concentrada` quando `L1_MINIMO_ABERTAS_NA_JANELA` (3) ou mais iniciaram a atividade em `L1_JANELA_ABERTURA_DIAS` (180). Extensão da T2 | FATF/Egmont (2018), Anexo E, itens 4 e 11; Velasco et al. (2021), seção 3.1 | baixa; falso negativo |
| **L3** Mesmo administrador em muitas empresas | Mesma **pessoa provável** (seis dígitos visíveis do CPF e nome normalizado) com qualificação de administração, ou como representante legal, em `L3_MINIMO_EMPRESAS` (5) ou mais empresas, das quais `L3_MINIMO_ABERTAS_NA_JANELA` (3) abertas em `L3_JANELA_MESES` (12) | FATF/Egmont (2018), itens 4 e 10; Carta Circular BCB 4.001/2020, III, h; Fronzetti Colladon e Remondi (2017); Lei 12.846/2013, art. 5º, III | média; falso positivo |
| **L4** Cadeia societária em camadas | Empresa sem pessoa física no primeiro nível do quadro e com `L4_MINIMO_NIVEIS` (2) ou mais níveis de sócio pessoa jurídica. Informa em que a cadeia termina (pessoa física, sócio estrangeiro ou pessoa jurídica sem quadro nos dados) | FATF/Egmont (2018), item 8; FATF (2011), § 47; Lei 12.846/2013, art. 5º, III | média; falso positivo |
| **L5** Sócio estrangeiro (nível 1) | Empresa com sócio de identificador 3 (estrangeiro) no CNPJ. **Sem lista de países**: o nível 2 depende da lista oficial, não conferida | FATF/Egmont (2018), item 4 | baixa; falso negativo |
| **L7** Fundadores substituídos | Contratada em que nenhum sócio atual entrou até `L7_DIAS_APOS_ABERTURA` (30) dias depois do início da atividade e a entrada mais recente antecede um contrato em até `L7_MESES_ANTES_CONTRATO` (12) meses. Só o retrato único; sócio sem data de entrada impede a regra | FATF/Egmont (2018), item 7 | média; falso positivo |
| **L8** Recém-aberta com sócio possivelmente agente público | A mesma contratada dispara a T1 **e** a T7. Composição, sem coleta nova. **Possível coincidência** | COAF, Casos e Casos (ANR 2021), tipologia 2.7 ("sem empregados" não é verificável) | alta; falso positivo |
| **L10** Vínculo com empresa sancionada (CEIS/CNEP) | Contratada sem sanção própria vigente que divide sócio-administrador provável ou endereço normalizado com empresa de sanção vigente na data do contrato. Extensão da T6 | FATF/Egmont (2018), item 3; Velasco et al. (2021); Lei 12.846/2013, art. 5º, IV, e | média; falso negativo |
| **L14** Faixa etária atípica (**sinal fraco**) | Contratada cujos sócios-administradores estão **todos** nas faixas `L14_FAIXAS_ATIPICAS` (1, 2 e 9: até 20 anos e mais de 80), com valor contratado acima de `L14_VALOR_MINIMO` (R$ 100.000,00). Só a faixa é usada, nunca a idade | FATF/Egmont (2018), itens 3 e 10 | baixa; falso positivo |
| **L15** Muitas atividades econômicas (**sinal fraco**) | Contratada com `L15_MINIMO_CNAES` (20) ou mais CNAEs, ou contratos em `L15_MINIMO_CATEGORIAS` (4) ou mais categorias de objeto | Velasco et al. (2021), seção 3.2 | baixa; falso negativo |

- **Administração** é a lista `QUALIFICACOES_ADMINISTRACAO` (05 Administrador, 10 Diretor, 16 Presidente, 28 Sócio-Gerente, 49 Sócio-Administrador, 50 Empresário, 65 Titular), da tabela de domínio Qualificações do CNPJ.
- **Sinal fraco** (`sinal_fraco: true` no catálogo e no alerta): sozinho não indica nada.
- **Pessoas:** vale a regra do RF02 e da T7. Seis dígitos e o nome dão "pessoa provável" ou "possível coincidência", nunca identificação, e nada é fundido. Sobrenome igual não entra em regra nenhuma (parentesco não é dado aberto).
- **API:** `GET /tipologias` e os alertas ganham `grupo` (`contratacao`, `lavagem` ou `combinacao`) e `sinal_fraco`. `POST /casos/{id}/alertas/executar` aceita os códigos novos em `tipologias` (até 30 itens) e os limiares numéricos novos em `parametros` (os nomes das variáveis acima, em minúsculas).
- **Dados novos guardados na resolução:** em cada estabelecimento, `cnaes_secundarios`, `endereco_com_complemento`, `pais` e `nome_cidade_exterior`; em cada vínculo `socio_de`, `pais`, `faixa_etaria` e `qualificacao_representante`. Casos coletados antes desta versão precisam de nova coleta do CNPJ para a L15 e para a separação por complemento da L1.

### Tipologias com coleta pequena (L6, L10 nas partes CEAF e CEPIM, e L16)

| Tipologia | Regra | Base | Severidade e erro que prefere evitar |
|---|---|---|---|
| **L6** Porte ou MEI incompatível | Soma do valor dos contratos assinados no ano-calendário (valor inicial; sem ele, o global) **acima** do limite legal de receita do regime naquele ano. MEI: opção e datas da tabela Simples, só para contratos assinados enquanto a opção valia. Fora do MEI: porte do cadastro (01 microempresa, 03 pequeno porte). No ano de início de atividade, limite proporcional aos meses (MEI, ME e EPP). Sem tolerância; o alerta diz a faixa do excesso (até 20% ou acima). Um alerta por empresa, ano e regime | Velasco et al. (2021), seção 3.2; LC 123/2006, arts. 3º e 18-A; LC 238/2026, arts. 2º e 10 | média; falso positivo |
| **L10** (CEPIM e CEAF) | CEPIM: a entidade impedida conta como "sancionada" no vínculo por sócio ou endereço (o cadastro não tem período, e o alerta diz isso). CEAF: sócio de contratada ou de convenente com os mesmos seis dígitos do CPF e o mesmo nome de uma pessoa expulsa. **Possível coincidência** | FATF/Egmont (2018), item 3 | média; falso negativo |
| **L16** Entidade sem fins lucrativos convenente | Convenente de natureza jurídica em `L16_NATUREZAS_SEM_FINS_LUCRATIVOS` (3069, 3301, 3999) com ao menos um sinal: (a) aberta menos de `L16_DIAS_MINIMOS` (180) dias antes da vigência; (b) registro no CEPIM; (c) dirigente que possivelmente coincide com servidor do órgão concedente; (d) dirigente que também é sócio de empresa no mesmo endereço, do mesmo CNAE principal ou recém-aberta | COAF, Casos e Casos (ANR 2021), 2.1, 2.8 e 2.9; Velasco et al. (2021), seção 5 | média; falso negativo |

**Limites da L6** (`buscador/tipologias/legislacao.py`, conferidos no texto do Planalto em 18/11/2026):

| Regime | Ano-calendário | Limite anual | Fonte |
|---|---|---|---|
| MEI | 2012 a 2017 | R$ 60.000,00 (R$ 5.000,00 por mês no ano de início) | LC 123/2006, art. 18-A, §§ 1º e 2º (LC 139/2011, art. 7º) |
| MEI | 2018 a 2026 | R$ 81.000,00 (R$ 6.750,00 por mês) | LC 123/2006, art. 18-A, §§ 1º e 2º (LC 155/2016, art. 11, III) |
| MEI | 2027 | R$ 110.000,00 (R$ 9.166,67 por mês) | LC 238/2026, art. 2º e art. 10, II |
| MEI | a partir de 2028 | R$ 140.000,00 (R$ 11.666,67 por mês) | LC 238/2026, arts. 1º e 10, I |
| Microempresa | a partir de 2012 | R$ 360.000,00 | LC 123/2006, art. 3º, I |
| Empresa de pequeno porte | 2012 a 2017 | R$ 3.600.000,00 | LC 123/2006, art. 3º, II (LC 139/2011) |
| Empresa de pequeno porte | a partir de 2018 | R$ 4.800.000,00 | LC 123/2006, art. 3º, II (LC 155/2016) |

Regra, como está na lei:

- **Limite do ano.** O valor contratado num ano é comparado com o limite daquele ano-calendário. Não há tolerância: a lei não prevê margem em que o excesso deixe de ser excesso. A variável `L6_TOLERANCIA_TRANSICAO` foi retirada.
- **Ano de início de atividade.** Limite proporcional aos meses, do mês do início até dezembro, contada a fração de mês como mês inteiro. MEI: valor mensal da lei vezes os meses (art. 18-A, § 2º). ME e EPP: limite anual vezes meses sobre 12 (art. 3º, § 2º).
- **Faixa de 20%.** Só muda a data dos efeitos do excesso, e o alerta diz em qual faixa caiu. MEI: até 20%, desenquadramento a partir de 1º de janeiro do ano seguinte; acima, retroativo a 1º de janeiro do ano do excesso ou, no ano de início, ao início de atividade (art. 18-A, § 7º, III e IV). EPP: até 20%, exclusão no ano seguinte (art. 3º, §§ 9º-A e 12); acima, no mês seguinte ao do excesso (§ 9º) ou, no ano de início, retroativa ao início (§ 10). ME: passa a EPP no ano seguinte (art. 3º, § 7º).
- **Antes de 2012.** Os limites não foram conferidos, e a L6 não avalia esses anos. O alerta da mesma empresa em outro ano lista os anos que ficaram de fora (`anos_nao_avaliados`).

Outros anos do MEI entram por `L6_LIMITES_MEI` (JSON, como os limites da T5); cada linha vale do seu ano até a linha seguinte.

**Coleta nova:**

- `cnpj_arquivo` (versão 1.3.0) lê também a tabela **Simples** (arquivo com `SIMPLES` no nome; 7 campos). A opção e as datas ficam nos atributos da empresa (`opcao_mei`, `data_opcao_mei`, `data_exclusao_mei` e os três do Simples). `scripts/recortar_cnpj.py` recorta também o `Simples.zip`. A leitura do arquivo real não foi testada.
- `portal_transparencia` (versão 1.2.0) aceita três recursos novos, conferidos na API real em 17/11/2026 (uma página de cada, só a estrutura):
  - `{"recurso":"cepim","codigoSancionado":[CNPJ]}`: vira `sancao` com `cadastro: "CEPIM"`, sem período (`data_referencia`);
  - `{"recurso":"convenios","codigoSancionado":[CNPJ do convenente]}`: vira a entidade `convenio`, ligada ao convenente por `convenente_de` e ao órgão por `concedente_de`. Sem filtro a API responde 400; o conector usa `convenente` e confere o CNPJ de cada item;
  - `{"recurso":"ceaf","nomeSancionado":[nome]}`: **só por nome, nunca por CPF**. O CPF do CEAF já vem descaracterizado. Vira `sancao` com `cadastro: "CEAF"` ligada a uma `pessoa` de chave própria (`expulso:...`), nunca fundida. Só entra o registro de nome idêntico ao consultado.
  - Com `"do_caso": true`: CEPIM e convênios consultam as entidades sem fins lucrativos (pela natureza jurídica) e as convenentes do caso; o CEAF consulta os **nomes** dos sócios pessoa física das contratadas e convenentes (até 100).
- A T6 continua só com CEIS e CNEP.

**Limitações:** a L6 compara valor contratado, não receita; o porte é o atual. Na L16, a entidade que só veio do Portal (sem natureza jurídica coletada no CNPJ) não é avaliada; o sinal com eleito ou PEP depende da L9. A contratada que tem ela mesma registro no CEPIM não gera alerta próprio (a T6 não cobre o CEPIM).

### Alerta de combinação (`COMB`)

Uma empresa ou pessoa com alertas de várias tipologias distintas recebe um alerta de combinação, que lista cada alerta, o peso e as evidências. A base é Velasco et al. (2021), seção 3.3: os padrões isolados pouco dizem, e "the risk of fraud increases when combinations of these risk patterns are identified". **Não é escore de culpa**: é pista para priorizar (RF12, RF14). Código em `buscador/tipologias/combinacao.py`; **pesos, mínimos e regras de contagem são decisão do autor**.

- **Regra:** ao menos `COMB_MINIMO_TIPOLOGIAS` (2) tipologias distintas **e** soma de pesos de ao menos `COMB_PESO_MINIMO` (2,0), entre os alertas ativos e **não descartados** em que a entidade aparece.
- **Pesos:** 1 por tipologia; 0,5 para os sinais fracos (L14 e L15); 0 para a L8, que é a composição de T1 e T7 e não soma de novo. `COMB_PESOS` (JSON) troca os pesos. Com os padrões, dois sinais fracos sozinhos não disparam.
- **Mesmo fato conta uma vez:** L1 e T2 falam do mesmo endereço. Vários alertas da mesma tipologia também contam uma vez.
- **Explicação:** `parametros.componentes` traz, de cada alerta, `alerta_id`, `tipologia`, `tipologia_nome`, `titulo`, `severidade`, `situacao`, `peso`, `sinal_fraco`, `conta` e `observacao` (por que não conta); e ainda `tipologias_distintas`, `soma_pesos`, os mínimos usados e o `aviso`. As evidências repetem até três de cada componente, com o código da tipologia.
- **Execução:** a combinação roda por último (`POST .../alertas/executar` sem `tipologias`, ou com `"COMB"`), sobre todos os alertas ativos do caso. Severidade alta: abre a fila. Executar só outra tipologia, ou decidir um alerta, **não** recalcula a combinação; ela é refeita na execução seguinte.
- **API:** catálogo e alertas ganham `peso` (o peso da tipologia na combinação).

### Não implementado

- **L2** (telefone ou e-mail compartilhado): o conector não guarda telefone, e a ficha recomenda só em combinação.
- **L5, nível 2** (lista de jurisdições): depende da IN RFB 1.037/2010, não conferida.
- **L7, parte (b)** (troca de sócios entre duas coletas): o monitoramento já avisa a mudança de quadro, mas não gera alerta de tipologia.
- **L9, L11 (RF05), L12 e L13:** dependem de decisão do orientador.

## Importação sob ordem judicial (RF10)

Módulo **separado e desligado por padrão** (`IMPORTACAO_JUDICIAL_ATIVA=0`), para dados bancários recebidos com o sigilo afastado (LC 105/2001). **Só dados sintéticos neste protótipo:** a pasta importada precisa ter o arquivo-marcador `SINTETICO`; sem ele a importação é recusada. Dado real nunca foi usado. Código em `buscador/importacao_judicial/` e `buscador/api/rotas_importacao_judicial.py`; desenho em `docs/lavagem/proposta.md`, seção 3 (monografia), a validar com o orientador.

- **Leiaute:** Instrução Normativa BCB nº 636/2025, Anexo I, itens I a V (AGÊNCIAS, CONTAS, TITULARES, EXTRATO e ORIGEM-DESTINO), transcrito campo a campo (nome, tipo e tamanho) do texto oficial lido em 17/11/2026 na API de normativos do Banco Central. Regras do art. 3º: `.txt`, ASCII, sem cabeçalho, TAB entre colunas, data `ddmmaaaa`, hora `hhmmss`, valor em centavos sem separador. A norma vigora a partir de 1º/12/2026.
- **Decisão judicial antes de tudo:** `POST /casos/{id}/importacao-judicial/decisoes` registra número do processo, número da decisão, juízo, data, período autorizado, contas autorizadas (banco, agência e conta), investigados (opcional) e a descrição do escopo. Só os perfis de `IMPORTACAO_JUDICIAL_PERFIS` (padrão `delegado`) registram, e precisam ser membros do caso. A importação só existe sob uma decisão (`.../decisoes/{decisao_id}/importacoes`).
- **Validação:** a linha fora do leiaute (número de campos, tipo, tamanho, data, domínio, obrigatório vazio, caractere não ASCII) é **rejeitada**, e o relatório traz linha, campo e motivo, nunca o conteúdo. Arquivos das corretoras (itens VI a XI) não são aceitos.
- **Escopo:** a linha válida que está fora do que a decisão autorizou fica em **quarentena**, sinalizada com o motivo: conta não autorizada, lançamento fora do período, origem-destino de lançamento em quarentena ou não encontrado, agência sem conta autorizada. A conta de contraparte que o próprio leiaute prevê (`MOVIMENTACAO_CONTA = 3`) não vai para a quarentena.
- **Segregação:** base própria (`IMPORTACAO_JUDICIAL_DATABASE_URL`; vazio = SQLite em `<DADOS_DIR>/sigiloso/`), com metadados próprios (tabelas `ij_*`, que não existem na base do dado aberto); chave de cifra própria (`IMPORTACAO_JUDICIAL_CHAVE`, AES-256-GCM, recusada se for igual à da custódia ou à da auditoria); escopo da decisão, campos de cada registro, relatório de rejeições e bytes do arquivo recebido ficam cifrados. Todo registro leva a marca `sigiloso: ordem judicial nº ... (processo ...)`. Nada vira entidade, vínculo, alerta, captura, relatório ou exportação.
- **Custódia:** SHA-256, tamanho, origem (`simba`, `oficio` ou `midia`), usuário e carimbo de tempo de cada arquivo recebido.
- **Auditoria (RNF02):** toda chamada, inclusive de leitura, grava uma entrada `importacao_judicial.*` na trilha encadeada, com a justificativa, os filtros e as contagens, e sem conta, documento ou conteúdo. A entrada é confirmada antes do dado: sem auditoria, nada é gravado nem devolvido.
- **Dados pessoais:** a leitura devolve o CPF sempre descaracterizado (titular, origem-destino, chave Pix e texto livre).

| Método e rota | Descrição |
|---|---|
| `GET /importacao-judicial/estado` | Só com o token: `{"ativa","perfis_autorizados","leiaute","arquivos","origens","somente_sintetico"}`. Com o módulo desligado, as rotas abaixo respondem `404` |
| `POST /casos/{id}/importacao-judicial/decisoes` | Registra a decisão (`201`). Perfil não autorizado: `403`, com auditoria |
| `GET /casos/{id}/importacao-judicial/decisoes` | Decisões do caso, com a `marca` e o número de importações |
| `POST /casos/{id}/importacao-judicial/decisoes/{decisao_id}/importacoes` | Corpo `{"diretorio","origem","descricao_origem"?}`; `diretorio` é relativo a `IMPORTACAO_JUDICIAL_DIR_BASE`. Devolve o relatório por arquivo (`linhas`, `aceitas`, `rejeitadas`, `em_quarentena`, `rejeicoes`, `sha256`) e os totais. `aceitas` inclui as que foram para a quarentena; `linhas = aceitas + rejeitadas` |
| `GET /casos/{id}/importacao-judicial/importacoes` | Importações do caso |
| `GET /casos/{id}/importacao-judicial/registros` | Filtros `decisao_id`, `tipo`, `quarentena`, `limite` (até 200) e `deslocamento`. Datas em ISO e valores em reais |

Todas as rotas de caso exigem `X-Justificativa`, papel de investigador no caso e caso dentro do prazo de retenção.

| Variável | Padrão | Descrição |
|---|---|---|
| `IMPORTACAO_JUDICIAL_ATIVA` | `0` | Liga o módulo |
| `IMPORTACAO_JUDICIAL_CHAVE` | vazio | 32 bytes em hexadecimal (`openssl rand -hex 32`); obrigatória com o módulo ligado |
| `IMPORTACAO_JUDICIAL_DATABASE_URL` | vazio | Base do dado sigiloso; não pode ser a `DATABASE_URL` |
| `IMPORTACAO_JUDICIAL_DIR_BASE` | vazio | Raiz de onde os arquivos são lidos |
| `IMPORTACAO_JUDICIAL_PERFIS` | `delegado` | Perfis que registram a decisão |
| `IMPORTACAO_JUDICIAL_MAX_BYTES_ARQUIVO`, `IMPORTACAO_JUDICIAL_MAX_LINHAS` | `16777216`, `200000` | Limites por arquivo e por importação |

**Fora do protótipo:** a camada do grafo e os alertas financeiros sobre o dado sigiloso; o RIF do COAF (não há leiaute público); o leiaute anterior (Carta-Circular BCB 3.454/2010); o descarte ao fim da retenção; a troca da chave do módulo; a exibição do CPF completo. Não foi conferido que o SIMBA entrega exatamente neste leiaute, e o Anexo II (códigos de tipo de lançamento) não foi transcrito. A exigência de ASCII segue a letra da norma: nome com acento é rejeitado.

## Fontes reais e piloto com dados públicos

Em 17/11/2026 os conectores foram conferidos contra as **fontes reais**, para preparar um piloto em que um delegado simula uma investigação com dados públicos (não é investigação sigilosa). Só houve coleta passiva (`GET`, `HEAD` e a listagem do compartilhamento público da Receita), com no máximo uma requisição por segundo em cada fonte e sem baixar nenhuma base completa. Os testes automatizados continuam **só com dados sintéticos**; os arquivos reais ficam em `dados-reais/`, que está no `.gitignore` e no `.dockerignore`.

### Como repetir

```bash
uv run python scripts/testar_fontes_reais.py                    # todas as fontes; contagens e formatos, sem nomes
uv run python scripts/testar_fontes_reais.py --fontes pncp,portal
uv run python scripts/testar_fontes_reais.py --fontes edital --baixar-edital 00394460005887/2026/72 --trecho 300
```

O script roda os conectores de verdade, com as capturas só em memória (nada vai para o banco nem para a custódia), e termina com código 1 se alguma fonte falhar. O Portal da Transparência é **pulado, com aviso**, se `PORTAL_TRANSPARENCIA_CHAVE` não estiver no ambiente nem no `.env`; a chave só vai no cabeçalho `chave-api-dados` e o script nunca a imprime nem a grava.

### O que foi conferido em cada fonte

| Fonte | Resultado | Divergência corrigida |
|---|---|---|
| **PNCP, contratos** ([OpenAPI](https://pncp.gov.br/api/consulta/v3/api-docs)) | Layout, paginação e limites conferem: `tamanhoPagina` de 10 a 500, `pagina` obrigatória, envelope com `paginasRestantes`, `204` sem dados, `422` para período acima de 365 dias (igual ao `PNCP_MAX_DIAS_PERIODO = 366`, que conta os dois extremos) | A operação filtra pela data de **publicação**, não de assinatura (documentado). Fornecedor pessoa física vem com o **CPF completo**: o contrato guardava esse valor em `ni_fornecedor`; agora guarda só o descaracterizado (o bruto fica na custódia) |
| **PNCP, termos** ([OpenAPI](https://pncp.gov.br/api/pncp/v3/api-docs)) | Responde sem autenticação; `204` sem termos; array JSON com os campos esperados (contrato com três aditivos) | Só a documentação (a pendência "não testado" saiu) |
| **Portal da Transparência** ([OpenAPI](https://api.portaldatransparencia.gov.br/v3/api-docs), [regras de uso](https://portaldatransparencia.gov.br/api-de-dados)) | CEIS, CNEP e servidores conferem com a chave do operador. Limite confirmado na fonte: 400 requisições por minuto das 6:00 às 23:59 e 700 das 00:00 às 5:59. Página de até 15 itens, sem envelope; página vazia encerra | Só a documentação: datas `DD/MM/AAAA`, `dataFimSancao` pode vir "Sem informação", CPF do servidor já descaracterizado, um órgão pode ter mais de um código SIAPE |
| **CNPJ (Receita)** | O layout confere (7, 30 e 11 campos, Latin-1, `;`) em 11,3 milhões de linhas lidas em fluxo | O sócio **pessoa jurídica** vem com o CNPJ básico (8 dígitos), não com 14: a resolução o tratava como pessoa; agora vira empresa nos dois formatos |
| **TSE** | Candidatos e receitas do DF de 2026: 672 candidatos e 10.121 doações lidos, todas as doações ligadas a um candidato por `SQ_CANDIDATO` | O arquivo de receitas chama o ano de `AA_ELEICAO` (o conector lia só `ANO_ELEICAO` e caía no nome do arquivo); 2026 veio em Latin-1 |
| **Edital em PDF (PNCP)** | Um edital real de 618 kB e 22 páginas: 63.398 caracteres em 0,4 s | Leitura de PDF implementada (ver "Editais em PDF") |

### Tamanho das fontes (medido em 17/11/2026, sem baixar tudo)

**CNPJ**, mês 2026-11, no repositório [Arquivos da Receita Federal](https://arquivos.receitafederal.gov.br/) (pasta `Dados/Cadastros/CNPJ/2026-11/`; é para onde aponta o conjunto do dados.gov.br, e o antigo `dadosabertos.rfb.gov.br` não respondeu):

| Arquivos | Quantidade | Compactado | Observação |
|---|---|---|---|
| `Empresas0.zip` a `Empresas9.zip` | 10 | 1,40 GB | o `0` tem 581 MB; os outros, de 78 a 99 MB |
| `Estabelecimentos0.zip` a `Estabelecimentos9.zip` | 10 | 5,46 GB | o `0` tem 2,32 GB; os outros, de 336 a 369 MB |
| `Socios0.zip` a `Socios9.zip` | 10 | 0,70 GB | o `0` tem 256 MB; os outros, cerca de 49 MB |
| `Simples.zip` | 1 | 0,31 GB | não é lido pelo conector |
| `Cnaes`, `Motivos`, `Municipios`, `Naturezas`, `Paises`, `Qualificacoes` | 6 | 0,07 MB | tabelas de domínio; não são lidas pelo conector |
| **Total** | **37** | **7,87 GB** | `scripts/testar_fontes_reais.py --fontes cnpj --detalhes` lista arquivo por arquivo |

Foram baixados `Empresas1.zip` (78 MB), `Estabelecimentos2.zip` (336 MB) e `Socios3.zip` (49 MB), mais as tabelas de domínio: 463 MB. Descompactados eles têm 326 MB, 1,09 GB e 207 MB (taxa de 3,2 a 4,2 vezes); por proporção, as três tabelas completas passam de **26 GB** descompactadas.

**TSE** ([dadosabertos.tse.jus.br](https://dadosabertos.tse.jus.br/), arquivos em `cdn.tse.jus.br/estatistica/sead/odsele/`):

| Arquivo | Compactado | Dentro do ZIP |
|---|---|---|
| `consulta_cand/consulta_cand_2024.zip` | 64,8 MB | CSV do Brasil: 249 MB; maior UF (SP): 42 MB |
| `prestacao_contas/prestacao_de_contas_eleitorais_candidatos_2024.zip` | 1,32 GB | `receitas_candidatos_2024_BRASIL.csv`: 1,41 GB; SP: 264 MB; RR: 3,5 MB |
| `consulta_cand/consulta_cand_2026.zip` | 3,3 MB | CSV do Brasil: 11,8 MB; DF: 0,4 MB |
| `prestacao_contas/prestacao_de_contas_eleitorais_candidatos_2026.zip` | 366 MB | `receitas_candidatos_2026_BRASIL.csv`: 188 MB; DF: 7,5 MB |

O servidor do TSE aceita `Range`: o CSV do DF de 2026 foi extraído do ZIP remoto com 4,2 MB de tráfego, sem baixar os 366 MB.

### CNPJ: o conector não carrega a base completa

O `cnpj_arquivo` **não filtra**: copia cada arquivo da pasta para a custódia e transforma toda linha em registro e, depois, em entidade do caso. Por isso tem teto de 16 MiB por arquivo e de 100 mil registros por coleta, e a resolução de entidades guarda o caso inteiro em memória. Carregar a base completa por ele exigiria tirar os limites, copiar mais de 26 GB para a custódia e criar centenas de milhões de entidades num só caso: pela vazão medida na resolução (cerca de 14 mil registros por segundo num caso pequeno), seriam horas no melhor caso, e a memória acabaria antes. Não é um caminho viável, nem o propósito do conector.

**Forma leve, já implementada:** `scripts/recortar_cnpj.py` percorre os ZIPs oficiais **em fluxo**, sem descompactar em disco e sem banco, e grava só as linhas dos CNPJs do caso (os mesmos bytes das linhas de origem), mais um `MANIFESTO.json` com o SHA-256 de cada ZIP lido. O conector lê esse recorte normalmente.

```bash
uv run python scripts/recortar_cnpj.py --zips dados-reais/cnpj/2026-11/zips \
    --saida dados-reais/cnpj/recorte-caso1 --lista cnpjs-do-caso.txt
```

- **Disco:** só os ZIPs (7,87 GB para o mês inteiro; 7,56 GB sem o `Simples`). O recorte tem kilobytes.
- **Tempo medido:** os três arquivos baixados (1,63 GB descompactados) em 15 s, com 20 CNPJs e o SHA-256 dos ZIPs, cerca de 110 MB/s. Por proporção, a base inteira leva de **4 a 5 minutos** por recorte nesta máquina. O download dos 7,87 GB levou a 3,4 MB/s nos arquivos testados, o que dá perto de 40 minutos.
- **É preciso ter os dez arquivos de cada tabela:** os de mesmo número não cobrem a mesma faixa de CNPJ. Com menos de dez, o script avisa que o recorte pode estar incompleto.
- **Custódia:** o recorte é derivado; a captura registra o SHA-256 do recorte, e o manifesto liga o recorte aos ZIPs de origem.

**Não implementado (proposta):** (a) uma segunda passada só nos `Socios*.zip` (0,70 GB, menos de um minuto) para achar as outras empresas de um sócio do caso, pelo nome e pelo CPF descaracterizado, ou as empresas de que uma empresa do caso é sócia; (b) se o piloto precisar de consultas repetidas, um índice local só com as colunas-chave de Estabelecimentos e Sócios, em vez da carga completa no banco da aplicação.

### Dados reais no Docker

`docker-compose.dados-reais.yml` monta `./dados-reais` só para leitura no contêiner da API e aponta para ela `CNPJ_DIR_BASE`, `TSE_DIR_BASE` e `EDITAIS_DIR_BASE`. Ele não foi aplicado: os contêineres em execução continuam com as fixtures sintéticas e **precisam ser reconstruídos** para ganhar a leitura de PDF (dependência nova, `pypdf`).

### Três riscos do piloto corrigidos em 17/11/2026

**1. CPF completo nas cargas brutas.** O PNCP traz o CPF completo do fornecedor pessoa física (`niFornecedor`), o CEIS/CNEP lista sancionados pessoa física e o arquivo de candidatos do TSE de 2026 trouxe `NR_CPF_CANDIDATO` preenchido. A custódia **não mudou**: guarda os bytes originais e o SHA-256 é sobre eles. O que mudou é o que sai dela. `buscador/privacidade.py` é o ponto único de descaracterização, no padrão dos dados abertos do CNPJ ("ocultação dos três primeiros dígitos e dos dois dígitos verificadores", [metadados do CNPJ](https://www.gov.br/receitafederal/dados/cnpj-metadados.pdf), p. 3–4, acesso em 17/11/2026), preservando o comprimento: `NNN.NNN.NNN-NN` vira `***.NNN.NNN-**` e `NNNNNNNNNNN` vira `***NNNNNN**`.

| Caminho | O que foi feito |
|---|---|
| Comparação de capturas e de coletas (`/capturas/{id}/comparar`, `/coletas/{id}/comparacao`, resposta da recoleta) | Todo valor e toda chave de registro passam pela descaracterização; em campo de documento (`niFornecedor`, `codigoFormatado`, `cpf...`), 11 dígitos são ocultados mesmo sem DV válido |
| Texto da captura (`/casos/{id}/capturas/{id}/texto`) | Devolve o texto com os CPFs descaracterizados, no mesmo comprimento (as posições dos trechos continuam valendo); `cpfs_descaracterizados` avisa; `texto_sha256` continua sendo o do texto original |
| Extração por IA (H22) | O modelo local recebe o texto já descaracterizado; trecho, valor, campos e recusas não têm como conter CPF completo |
| Entidades, vínculos, alertas, avisos, relatório, exportações (GraphML, CSV, ZIP) | Derivam dos registros dos conectores. Além do campo de documento (que já era descaracterizado), o **texto livre** dos registros é limpo na entrada da resolução: o objeto de um contrato pode citar um CPF, e o nome empresarial de microempreendedor individual pode terminar com o CPF do titular (observação do autor nos dados; sem fonte normativa conferida). A T10 relê o edital da custódia e também recebe o texto limpo |
| Parâmetros de coleta (`GET /coletas`) e auditoria | Um CPF digitado num campo de CNPJ é descaracterizado antes de ser gravado; o filtro `cnpjFornecedor` por CPF é recusado; os detalhes da auditoria (termo de busca, parâmetros, erros) passam pela descaracterização antes do encadeamento. A **justificativa** e as anotações são texto do próprio usuário e ficam como foram digitadas |
| Avisos e erros de coleta | `texto_de_linha` (por onde passa toda mensagem de fonte externa) descaracteriza |
| Registros (`logs`) | A fábrica de registros do processo descaracteriza mensagem, argumentos (inclusive a URL do registro de acesso) e texto de exceção |

- **Caminho para ver o original:** não existe na API (não há rota de download da captura) e nenhum foi criado. Quem precisa dos bytes originais para perícia depende hoje do operador, com acesso ao diretório da custódia e à chave. Uma rota de download restrita por papel, com justificativa própria e registro na auditoria, fica como proposta para decisão do orientador.
- **O que conta como CPF** (decisão do autor, a mesma regra da interface): a forma com hífen antes dos dois últimos dígitos, sempre; 11 dígitos isolados com DV válido; e 11 dígitos em campo de documento ou logo depois da palavra "CPF". Cerca de 1 em cada 100 números quaisquer de 11 dígitos tem DV válido e é ocultado: o erro escolhido é esconder um número que não é CPF. CNPJ nunca é alterado. O `SQ_CANDIDATO` do TSE é isento.
- **`NR_CPF_CANDIDATO` (TSE):** o conector continua sem ler a coluna (há teste que mostra que os registros são idênticos com a coluna preenchida, com `-4` ou ausente). Ela existe só na carga bruta; a rota de texto a devolve descaracterizada.
- **Portal, servidores:** `cpfFormatado` já vem descaracterizado da fonte (`***.NNN.NNN-**`); se um dia vier completo, os mesmos caminhos acima o ocultam.
- **Limites:** entidades gravadas **antes** desta versão não são reescritas (num banco antigo, o objeto de um contrato pode ter CPF em texto livre); e CPF escrito de outra forma (com espaços entre os grupos, por extenso) não é reconhecido.

**2. CNPJ alfanumérico.** Conferido nas fontes oficiais em 17/11/2026:

- [página do projeto na Receita Federal](https://www.gov.br/receitafederal/pt-br/acesso-a-informacao/acoes-e-programas/programas-e-atividades/cnpj-alfanumerico): Instrução Normativa RFB nº 2.229, de 15/10/2024; o formato é atribuído "a partir de Julho de 2026, exclusivamente a novas inscrições"; os CNPJs numéricos continuam válidos;
- ["Perguntas e Respostas"](https://www.gov.br/receitafederal/pt-br/centrais-de-conteudo/publicacoes/perguntas-e-respostas/cnpj/cnpj-alfanumerico.pdf) (perguntas 2, 14, 21, 23 e 26): 14 posições com a mesma máscara; as 12 primeiras (raiz e ordem) aceitam "numerais de 0 a 9 e letras maiúsculas de A até Z"; os 2 DVs são numéricos; raiz e ordem podem ser alfanuméricas independentemente; a implementação segue a IN 2.119/2022 alterada pela IN RFB 2.229/2024;
- [manual do cálculo do DV](https://www.gov.br/receitafederal/pt-br/centrais-de-conteudo/publicacoes/documentos-tecnicos/cnpj/manual-dv-cnpj.pdf) (Serpro): valor de cada caractere = código ASCII − 48; pesos de 2 a 9 da direita para a esquerda, recomeçando depois do oitavo; módulo 11; resto 0 ou 1 dá DV 0, senão 11 − resto. Exemplo do manual, usado nos testes: `12.ABC.345/01DE-35`.

O que mudou: `resolucao/normalizacao.py` (`normalizar_cnpj`, `normalizar_cnpj_basico`, `calcular_dv_cnpj`, `cnpj_valido`, `formatar_cnpj`; para CNPJ só de dígitos a conta é a mesma de antes); a resolução (raiz de 8 posições como chave da empresa, sócio pessoa jurídica, fornecedor, sancionado, doador); os conectores `cnpj_arquivo` (raiz e ordem em maiúsculas, sem descartar letras), `pncp`, `pncp_termos`, `portal_transparencia`, `tse_arquivo` e `edital_arquivo`; `scripts/recortar_cnpj.py`; a busca de entidades (aceita o CNPJ alfanumérico com ou sem pontuação, em maiúsculas ou minúsculas); a extração por IA; e, no front-end, a validação com DV e a máscara. Tipologias e exportação não precisaram de mudança além dos testes. **Decisão do autor:** letras minúsculas na entrada são aceitas e convertidas. **Não conferido:** nenhuma das fontes foi vista devolvendo um CNPJ alfanumérico em 17/11/2026 (nas 11,3 milhões de linhas lidas de 2026-11 toda raiz era numérica), e o layout publicado do CNPJ ainda descreve o campo como número; o código apenas deixou de descartar as letras. As letras proibidas pela Receita (pergunta 45) não são conferidas, por orientação da própria Receita.

**3. TSE: arquivo de UF grande.** Até `TSE_MAX_BYTES_ARQUIVO` (64 MiB) nada mudou. Acima disso (ou ZIP com um CSV acima disso), o `tse_arquivo` 1.1.0 lê **em fluxo**, linha a linha, e exige ao menos um filtro: `uf`, `municipio`, `candidatos` ou `do_caso` (os candidatos que já estão no caso; colete antes o arquivo de candidatos). A captura passa a ser o **recorte**: o cabeçalho e as linhas do filtro, com os bytes originais. O SHA-256 da captura é o do recorte; o tamanho e o SHA-256 do CSV inteiro, o filtro e a contagem de linhas ficam em `metadados.recorte`, e a origem leva `#recorte` (num ZIP, `arquivo.zip!membro.csv#recorte`). É decisão do autor, pelo mesmo critério do `recortar_cnpj.py`: guardar 264 MB por coleta na custódia não se justifica quando o caso usa algumas centenas de linhas.

- **Limites que continuam valendo:** o recorte até `TSE_MAX_BYTES_ARQUIVO`; o arquivo percorrido até `TSE_MAX_BYTES_FLUXO` (2 GiB, decisão do autor: cobre o maior CSV conferido, de 1,4 GB); cada linha física até 1 MiB; campo do CSV até o limite da biblioteca padrão; registros até `TSE_MAX_REGISTROS`; link simbólico não é seguido.
- **Medição** (`scripts/medir_tse_fluxo.py`, arquivo **sintético** de 277 MB e 470 mil linhas no layout de receitas, nesta máquina, em 17/11/2026): CSV com filtro por município, 1,8 s e pico de memória do processo de 78,0 para 78,4 MiB; o mesmo CSV dentro de um ZIP, com filtro por 20 candidatos, 2,4 s e pico de 76,8 para 80,9 MiB. O recorte teve 555 KB (940 linhas). O arquivo real de SP não foi baixado.
- **Codificação:** decidida linha a linha (UTF-8 estrito e, se falhar, Latin-1); só difere da detecção sobre o arquivo inteiro num arquivo Latin-1 com uma linha que seja, por coincidência, UTF-8 válido com acento.
- **Comparação entre coletas:** duas coletas do mesmo arquivo com filtros diferentes têm recortes diferentes; a recoleta repete os mesmos parâmetros (a lista de `do_caso` fica gravada).

### Pendências e riscos para o piloto

- **Contêineres em execução:** continuam com a versão anterior; as três correções acima só valem depois de reconstruir a imagem da API (e a do front-end).
- **Download da captura original para perícia:** não existe rota; depende do operador (ver o item 1 acima).
- **Dados gravados antes de 17/11/2026:** não são reescritos; para o piloto, comece com o banco e a custódia vazios.
- **CNPJ alfanumérico nas fontes:** aceito pelo código, mas ainda não visto em nenhuma resposta real.
- **Município do CNPJ:** o campo é o código da tabela `Municipios`; o nome não é resolvido.
- **PNCP:** a consulta de um ano inteiro sem órgão levou 22 s na primeira página (o tempo limite padrão é 30 s); prefira períodos curtos ou `cnpjOrgao`.
- **Download de editais** pelo conector e **OCR** continuam fora.

## Arquitetura

```
buscador/
  config.py        variáveis de ambiente (pydantic-settings); falha fechada sem a chave do HMAC e sem a chave da custódia
  cripto.py        AES-256-GCM com subchaves por HKDF (capturas e segredos de TOTP); chave mestra fora do banco
  acesso/          senhas (Argon2id), TOTP (RFC 6238) e o serviço de contas, login em duas etapas e sessões
  usuarios.py      linha de comando: cria o primeiro usuário e redefine senha ou segundo fator
  exportacao.py    grafo do caso em GraphML e CSV, com a captura e o hash de cada item
  ia/              extração assistida por IA local: interface do provedor, Ollama, provedor falso e as verificações
  documentos.py    texto de um documento da custódia: texto puro ou PDF com camada de texto (limites e motivos de recusa)
  pdf_extrator.py  processo auxiliar, isolado, que extrai o texto do PDF com a pypdf
  seguranca.py     validações de borda: texto sem caractere de controle, JSON só com números finitos
  db.py            engine, sessões, criação e evolução do esquema, gatilhos que bloqueiam UPDATE/DELETE/TRUNCATE na auditoria e nas decisões de alerta, e privilégios do papel da aplicação
  migrar.py        cria ou atualiza esquema, gatilhos e privilégios com o papel dono (serviço "migracao" do Compose)
  modelos.py       Caso, Coleta, Captura, Entidade, EntidadeFonte, Aresta, Sugestao, Alerta, AlertaDecisao, Monitoramento, MonitoramentoExecucao, Aviso, Auditoria
  retencao.py      prazo de retenção do caso (RNF04): depois dele, o caso é só leitura
  comparacao.py    comparação de capturas e de coletas (RF07): hash e diferença por registro e por campo
  privacidade.py   descaracterização de CPF em tudo o que sai da custódia (RNF04): texto, valores JSON, parâmetros e registros de log
  conectores/      interface comum, versão, retentativa e controle de taxa (base.py), parâmetros por fonte (parametros.py), cliente HTTP comum (http_util.py), leitura confinada de arquivos (arquivos.py) + cnpj_arquivo, pncp, pncp_termos, portal_transparencia, tse_arquivo, edital_arquivo; transportes simulados para testes e demonstração (simulados.py)
  monitoramento.py coletas de origem de uma entidade, fotografia dos vínculos e novidades entre duas fotografias (RF06)
  agendador.py     agendador interno do monitoramento contínuo (uma thread do serviço)
  custodia/        guarda bytes brutos endereçados por SHA-256 (do conteúdo original), cifrados no disco, com carimbo UTC e metadados; verificação
  auditoria/       trilha somente de acréscimo, encadeada por HMAC, com âncora externa; verificação da cadeia
  resolucao/       normalização (nome, CPF descaracterizado, endereço, DV do CNPJ) e Resolvedor
  grafo/           sql.py: vizinhança e caminho consultados no banco (usado pela API); servico.py: a versão original com NetworkX, mantida para a comparação
  importacao_judicial/  RF10: leiaute da IN BCB 636/2025, base e chave próprias do dado sob ordem judicial (desligado por padrão)
  tipologias/      T1 a T4 (motor.py), T5 a T10 (horizonte2.py), L de lavagem (lavagem.py) e combinação (combinacao.py), com motivo em português e evidências; severidade e política de erro numa tabela única (politicas.py); parâmetros legais e listas de termos com a fonte (legislacao.py); catálogo para a interface (catalogo.py)
  relatorio/       relatório JSON e Markdown
  coleta.py        conector → custódia → resolução
  api/             FastAPI: deps (sessão ou token de desenvolvimento, justificativa, acesso por caso, auditoria), esquemas, rotas (casos, equipe, exportação, IA, autenticação) e a execução do monitoramento (monitoramento.py)
  main.py          ponto de entrada do uvicorn
scripts/carregar_demo.py   demonstração com as fixtures sintéticas
scripts/medir_grafo.py     medição de desempenho do grafo (docs/desempenho-grafo.md)
scripts/testar_fontes_reais.py  teste de fumaça das fontes REAIS (manual, usa a rede; pula o Portal sem chave)
scripts/recortar_cnpj.py   recorta dos ZIPs oficiais do CNPJ só as linhas dos CNPJs do caso (leitura em fluxo)
scripts/gerar_fixtures.py  gera as fixtures (reprodutível)
scripts/gerar_fixtures_h2.py  gera as fixtures do horizonte 2 em tests/fixtures/h2 (reprodutível)
docker/postgres-init/      cria o papel da aplicação quando o volume do PostgreSQL nasce
.github/workflows/ci.yml   pipeline: ruff, Bandit e pytest (com PostgreSQL)
tests/                     pytest (392 testes, 8 deles só com PostgreSQL; em 17/11/2026, com SQLite: 384 passaram e 8 ficaram como "skipped"; test_privacidade_cpf.py, test_cnpj_alfanumerico.py e test_tse_fluxo.py cobrem os três riscos do piloto, com CPFs e CNPJs alfanuméricos sorteados a cada execução por tests/sinteticos.py; test_seguranca.py traz as regressões da auditoria de segurança, test_horizonte1.py as histórias H12 a H17 e H31, test_horizonte2.py as histórias H18, H19, H32 a H36 e H39, test_horizonte3.py as do horizonte 3, test_editais_pdf.py a leitura de PDF com PDFs sintéticos gerados no próprio teste e test_fontes_reais.py as regressões das divergências achadas nas fontes reais, reproduzidas com dados sintéticos) e fixtures sintéticas
```

**Cadeia de custódia (RF07).** Cada arquivo lido e cada resposta HTTP é guardada exatamente como veio em `<DADOS_DIR>/capturas/<aa>/<sha256>`, com SHA-256, carimbo UTC, fonte, origem e metadados (status HTTP, cabeçalhos selecionados, parâmetros da consulta; tabela, nome do arquivo e codificação para o CNPJ). A cópia e o hash são feitos em blocos. Toda entidade e aresta registra o `captura_id` de origem; a entidade guarda todas as capturas que a alimentaram (`fontes`) e, em `atributos.origem_atributos`, qual captura forneceu o valor em vigor de cada atributo. É essa captura que as tipologias citam como evidência. `GET /capturas/{id}/verificar` recalcula o hash a partir dos bytes. Se a coleta falha no meio, as capturas já guardadas continuam registradas e são citadas na entrada `coleta.falhou`.

**Versão do conector, recoleta e comparação (RF07, H14).** Cada conector tem nome e versão (`Conector.nome` e `Conector.versao`; hoje `cnpj_arquivo 1.1.0`, `pncp 1.1.0` e `portal_transparencia 0.1.0`). A versão muda quando muda o que o conector pede à fonte ou como interpreta a resposta, e fica gravada em cada captura (`capturas.conector_nome` e `conector_versao`) e em cada coleta. `POST /casos/{id}/coletas/{coleta_id}/recoletar` repete a coleta e compara as capturas novas com as antigas, pareadas pela origem: primeiro o SHA-256; se ele difere, as duas cargas guardadas são relidas da custódia e interpretadas pelo conector (`registros_brutos`), e a resposta diz quais registros entraram, saíram ou mudaram e quais campos mudaram, com o valor de antes e o de depois. No CNPJ os campos têm o nome da posição no layout (por exemplo, `capital_social`); no PNCP, o nome do campo do JSON (por exemplo, `valorGlobal`, `orgaoEntidade.razaoSocial`). A recoleta só acrescenta: a captura antiga continua guardada e verificável. As entidades do caso são atualizadas pela resolução como em qualquer coleta (o último valor não vazio prevalece), e a captura de origem de cada atributo continua em `origem_atributos`.

**Falhas de coleta e retentativa (RNF12, H15).** Toda coleta vira uma linha em `coletas`, criada como `em_andamento` antes de qualquer consulta e fechada como `sucesso` ou `falha`, com `tentativas` e `motivo`. Como cada requisição coleta uma fonte, o histórico (`GET /casos/{id}/coletas`) e o relatório mostram o resultado fonte a fonte. A retentativa é uma só para todos os conectores (`conectores/base.py`, classe `Retentativa`): a operação de E/S sinaliza `FalhaTransitoria` e é repetida com espera crescente. No PNCP ela cobre erro de rede e HTTP 429/5xx por página (`PNCP_TENTATIVAS`); no `cnpj_arquivo`, erro de abertura ou de leitura de cada arquivo (`COLETA_TENTATIVAS`); o stub do Portal da Transparência recebe a mesma política, mas ainda não faz E/S. Erros definitivos (parâmetro inválido, arquivo acima do limite, pasta fora da raiz) não são repetidos. `tentativas` é o maior número de tentativas que uma operação da coleta precisou: `0` quando a coleta foi recusada antes de tentar algo, `1` quando deu certo de primeira. Numa falha inesperada, o `motivo` do histórico traz só o tipo do erro; o detalhe fica na auditoria, restrita a `delegado` e `admin`. Controle de taxa e detecção de mudança de formato, também citados no RNF12, não foram implementados.

**Capturas sintéticas.** Uma captura que não veio da fonte oficial nunca se parece com uma que veio. Toda captura grava `metadados.simulado` (`true` ou `false`), e as sintéticas têm a origem com o prefixo `simulado://` (por exemplo, `simulado://pncp.gov.br/api/consulta/v1/contratos?...` e `simulado://cnpj_arquivo/CNPJ_DIR_BASE/cnpj/...`). São sintéticas: (a) as respostas do PNCP servidas por um transporte injetado (testes e `scripts/carregar_demo.py`); (b) os arquivos do CNPJ lidos de uma pasta que contém o arquivo-marcador `SINTETICO` (qualquer extensão; `tests/fixtures/cnpj/SINTETICO.txt` é gerado por `scripts/gerar_fixtures.py`). A regra é imposta na camada de custódia, e a marca segue para as evidências dos alertas, para `url_fonte` dos contratos e para o relatório (campo `contem_capturas_simuladas`, coluna "Sintética" e aviso no topo do Markdown).

**Auditoria (RNF02).** Tabela `auditoria` só de acréscimo. Cada entrada traz `versao`, `seq`, `quando`, `usuario`, `acao`, `caso_id`, `justificativa`, `detalhes`, `hash_anterior` e `hash`. O ORM recusa `UPDATE`/`DELETE`, e o banco também (gatilhos em SQLite e PostgreSQL; no PostgreSQL há ainda o gatilho de `TRUNCATE`, e o papel da aplicação não tem esses privilégios nem é dono dos gatilhos). Toda rota que lê ou escreve dados de caso registra uma entrada na mesma transação da ação, com o perfil do usuário em `detalhes`. Os `detalhes` têm tamanho limitado: valores grandes vindos do cliente viram um resumo com tamanho e SHA-256.

*Decisão de versão da cadeia (versão 2).* A versão 1 calculava `sha256(json_canonico(entrada) + hash_anterior)`, sem chave: quem tivesse a credencial do banco podia reescrever a história e recalcular hashes válidos. A versão 2 muda três coisas:

1. `hash = HMAC-SHA256(AUDITORIA_CHAVE_HMAC, json_canonico(entrada_sem_hash))`. A entrada inclui `versao` e `hash_anterior`; a primeira encadeia ao hash gênese (64 zeros). A chave fica só no ambiente da API. Sem ela não se produz um hash válido.
2. O JSON canônico passa a ser ASCII puro (`ensure_ascii=True`), com chaves ordenadas e sem espaços, e recusa `NaN`/`Infinity` (`allow_nan=False`). Um valor fora do JSON estrito aborta a requisição em vez de entrar na trilha.
3. **Âncora externa.** Depois de cada confirmação, o topo `(seq, hash)` é copiado para `<DADOS_DIR>/ancora/auditoria.json`, fora do banco, com um MAC da mesma chave; o arquivo só avança. `GET /auditoria/verificar` e o relatório percorrem a cadeia em lotes (sequência, encadeamento e HMAC) e comparam com a âncora: se o banco termina antes da entrada ancorada, a cadeia foi cortada; se o hash da entrada ancorada difere, foi reescrita. Âncora ausente com entradas no banco, ou com MAC inválido, também conta como quebra.

Não há compatibilidade com a versão 1: entradas sem chave não podem ser autenticadas depois, então um banco com a tabela antiga é recusado na inicialização, com a instrução de recriá-lo (os dados do MVP são de demonstração). Consequências para a operação: guardar `AUDITORIA_CHAVE_HMAC` em local seguro (trocá-la invalida a verificação das entradas antigas); fazer cópia de segurança do banco e de `DADOS_DIR` juntos; ao recriar o banco de propósito, apagar também `<DADOS_DIR>/ancora/`, senão a verificação acusa corte. Limite conhecido: quem controla ao mesmo tempo o banco, a chave e o volume de dados ainda consegue reescrever tudo; para isso a âncora precisaria ir para um repositório de escrita única fora do servidor (trabalho futuro). Há testes que adulteram, recalculam e cortam a cadeia por SQL e confirmam a detecção.

**Resolução de entidades (RF02).** Empresa pela chave `cnpj_basico` (8 dígitos); estabelecimentos são fundidos na empresa (lista `estabelecimentos`, matriz em destaque, `enderecos_normalizados`). Pessoa pela chave `cpf_descaracterizado|nome_normalizado`; sem CPF (sócio estrangeiro), a chave fica restrita ao registro de origem, para que nunca haja fusão só pelo nome. Nome igual com CPF diferente gera `Sugestao` com score 0,5; nome igual com CPF ausente em um dos lados, score 0,7. Nada é fundido automaticamente. Órgão pelo CNPJ do `orgaoEntidade`; contrato pelo `numeroControlePNCP`; `niFornecedor` com 14 dígitos liga ao CNPJ básico (empresa criada a partir do PNCP se não existir nos arquivos). Endereço normalizado = tipo + logradouro + número + bairro + CEP + município + UF (sem o complemento, decisão do autor). Endereço sem logradouro, ou sem CEP e sem município, não gera chave: empresas com endereço em branco não são ligadas entre si. Órgão sem CNPJ e sem nome fica restrito ao próprio contrato. Grupos acima de `RESOLUCAO_MAX_GRUPO` não são expandidos par a par: o endereço compartilhado vira uma estrela (cada empresa ligada à primeira do grupo) e as sugestões de homônimo excedentes não são geradas; os dois casos devolvem aviso na coleta.

**Tipologias (RF04, RNF05).** T1: `dataAssinatura - data_inicio_atividade < t1_dias_minimos`. T2: mesmo endereço normalizado em `>= t2_minimo_empresas` empresas, ao menos uma com contrato no PNCP. T3: `valorGlobal / capital_social >= t3_razao_minima` (capital zero com contrato também dispara; capital negativo ou valor não finito na fonte é desconsiderado, com aviso na coleta). T4 (H31): a empresa contratada no PNCP tem, no CNPJ, situação cadastral diferente de ATIVA. Cada alerta traz `motivo` em frase completa com os números, `evidencias` (descrição, fonte, `captura_id`, URL ou arquivo, SHA-256) e `parametros` (limiares usados e valores observados).

*T4 em detalhe.* A regra lê o campo "situação cadastral" da tabela Estabelecimentos do layout oficial, cujos códigos são "01 – NULA, 2 – ATIVA, 3 – SUSPENSA, 4 – INAPTA, 08 – BAIXADA" ([metadados do CNPJ, Receita Federal](https://www.gov.br/receitafederal/dados/cnpj-metadados.pdf), conferido em 16/11/2026). O código é comparado como número, porque o arquivo pode trazê-lo com ou sem zero à esquerda. O estabelecimento examinado é o do CNPJ de 14 dígitos do contrato ou, se ele não estiver nos arquivos coletados, a matriz. Dispara para qualquer código diferente de 2, inclusive um código fora do layout (o motivo diz isso). Não dispara para empresa que só aparece no PNCP: falta de dado não é situação irregular. O motivo traz a data do evento da situação e diz se ela é anterior ou posterior à assinatura do contrato (`parametros.situacao_anterior_ao_contrato`); os dois casos disparam, e essa escolha está declarada na política de erro da tipologia. A T4 não tem limiar.

**Severidade e política de erro (RF14, H13).** A severidade (alta, média ou baixa) é da tipologia, e não mais de cada alerta: ela e a política de erro ficam numa tabela única, `buscador/tipologias/politicas.py`, lida pelo motor, pela API e pelo relatório. **Os valores são decisão do autor, a validar com o orientador**; não vêm de norma nem da literatura.

| Tipologia | Severidade | Prefere evitar | Por quê (resumo da política mostrada ao investigador) |
|---|---|---|---|
| T4 situação cadastral não ativa | alta | falso negativo | Fato objetivo do cadastro oficial; dispara para qualquer situação não ativa, mesmo posterior ao contrato. O falso positivo vem da defasagem do arquivo e custa pouco conferir |
| T1 empresa aberta pouco antes do contrato | média | falso negativo | Limiar largo: aponta toda empresa recente, inclusive as regulares |
| T3 capital social incompatível | média | falso positivo | Só a desproporção grosseira dispara, porque capital baixo é comum em empresas regulares; desproporções menores passam |
| T2 endereço compartilhado | baixa | falso negativo | Muitas explicações lícitas (contabilidade, coworking); serve para orientar a análise de vínculos |

A lista de alertas e o relatório vêm nessa ordem (severidade, depois tipologia e título). Antes do horizonte 1, a severidade variava dentro da tipologia conforme o valor observado; os valores observados continuam em `parametros`.

**Alerta como pista (RF12, H12).** Todo alerta nasce `pendente`. O investigador confirma ou descarta com `POST /casos/{id}/alertas/{alerta_id}/decisao`, sempre com justificativa. A decisão fica no alerta (`situacao`, `decidido_por`, `decidido_em`, `justificativa_decisao`), numa linha da tabela `alerta_decisoes` e numa entrada `alerta.decidir` da trilha de auditoria, na mesma transação. Rever um alerta é decidir de novo: a nova decisão passa a valer, gera outra linha e outro evento, e as anteriores continuam no histórico (`decisoes`). A tabela `alerta_decisoes` é só de acréscimo também no banco (gatilhos em SQLite e PostgreSQL; no PostgreSQL o papel da aplicação só tem `SELECT` e `INSERT`). Para a decisão sobreviver à reexecução das tipologias, cada alerta tem uma `chave` estável (tipologia, entidades e, na T2, o endereço): a reexecução atualiza o alerta de mesma chave em vez de recriá-lo. Um alerta decidido que deixa de ser detectado não é apagado: fica com `ativo: false`. Na T2, se o conjunto de empresas do endereço muda, a chave muda e nasce um alerta novo, pendente. Qualquer perfil autenticado pode decidir; não há voto duplo nem volta a `pendente`.

**Finalidade e retenção (RNF04, H17).** Todo caso é criado com `finalidade` (texto obrigatório) e `retencao_ate` (data). Até o fim desse dia (UTC) o caso funciona normalmente. A partir do dia seguinte ele fica só leitura: novas coletas e recoletas, consultas a entidades, grafo e caminho, execução de tipologias e decisões sobre alertas respondem `403`, e cada recusa entra na auditoria como `caso.retencao_recusa`, com a ação tentada. Continuam disponíveis o próprio caso, o relatório, a lista de alertas e de coletas já produzidos, a comparação de capturas, a custódia (`/capturas/...`) e a trilha de auditoria. Não há rota para prorrogar o prazo nem para alterar a finalidade. Casos criados antes desta versão ficam com finalidade vazia e sem prazo, e por isso não vencem.

*Descarte.* **O protótipo não apaga nada depois do prazo.** As capturas e a trilha de auditoria são só de acréscimo de propósito (é o que sustenta a cadeia de custódia e a detecção de adulteração), e apagar um caso quebraria as duas garantias. O descarte ou o arquivamento dos dados depois do prazo de retenção é decisão do órgão responsável pela investigação, com base nas suas normas, e fica fora do protótipo: a ferramenta só registra a finalidade e o prazo e deixa de permitir uso novo do caso.

## Modelo de dados (mudanças do horizonte 1)

Tabelas novas:

- `coletas`: uma linha por execução de coleta de uma fonte num caso. Colunas: `id`, `caso_id` (FK `casos`), `fonte`, `parametros` (JSON), `conector_nome`, `conector_versao`, `situacao` (`em_andamento`, `sucesso` ou `falha`), `tentativas`, `motivo`, `registros`, `avisos` (JSON), `usuario`, `iniciado_em`, `concluido_em`, `recoleta_de` (FK para a própria tabela, opcional).
- `alerta_decisoes`: histórico das decisões sobre alertas, só de acréscimo. Colunas: `id`, `alerta_id` (FK `alertas`), `caso_id` (FK `casos`), `decisao` (`confirmado` ou `descartado`), `justificativa`, `usuario`, `perfil`, `decidido_em`.

Colunas novas em tabelas existentes:

- `casos`: `finalidade` (texto, padrão vazio) e `retencao_ate` (data `AAAA-MM-DD`, opcional no banco e obrigatória na API).
- `capturas`: `conector_nome`, `conector_versao` (padrão vazio) e `coleta_id` (FK `coletas`, opcional).
- `alertas`: `chave` (identidade estável entre execuções), `ativo`, `situacao` (`pendente`, `confirmado` ou `descartado`), `decidido_por`, `decidido_em` e `justificativa_decisao` (cópia da última decisão).

Relações e cardinalidades:

- Caso 1 — N Coleta; Coleta 1 — N Captura (a captura anterior ao horizonte 1 não tem coleta: 0..1 do lado da coleta).
- Coleta 0..1 — N Coleta, por `recoleta_de` (uma coleta pode ser repetida várias vezes; cada recoleta aponta para uma coleta original).
- Alerta 1 — N AlertaDecisao (zero decisões enquanto pendente); Caso 1 — N AlertaDecisao.

`python -m buscador.migrar` (ou a própria API, com `DB_CRIAR_ESQUEMA=1`) cria as tabelas novas e acrescenta as colunas novas com `ALTER TABLE ... ADD COLUMN`, em SQLite e em PostgreSQL. Toda coluna nova aceita nulo ou tem valor padrão no banco, então as linhas antigas continuam válidas. A migração é idempotente e nunca remove nem altera coluna. Com `DB_CRIAR_ESQUEMA=0`, a API recusa subir num banco sem as colunas novas e diz quais faltam. Os gatilhos da auditoria são os mesmos de antes; `alerta_decisoes` ganhou gatilhos próprios de `UPDATE`, `DELETE` e, no PostgreSQL, `TRUNCATE`, e entrou na lista de tabelas em que o papel da aplicação só tem `SELECT` e `INSERT`.

### Mudanças do horizonte 2

Tabelas novas:

- `monitoramentos`: entidade do caso marcada para acompanhamento. Colunas: `id`, `caso_id` (FK `casos`), `entidade_id` (FK `entidades`), `periodicidade_horas`, `ativo`, `motivo_inativo`, `justificativa`, `criado_por`, `criado_em`, `proxima_execucao_em`, `ultima_execucao_em` (opcional). Restrição única `(caso_id, entidade_id)`; índice `(ativo, proxima_execucao_em)`.
- `monitoramento_execucoes`: uma linha por execução, automática ou manual. Colunas: `id`, `monitoramento_id` (FK `monitoramentos`), `caso_id` (FK `casos`), `origem` (`agendador` ou `manual`), `usuario`, `iniciado_em`, `concluido_em` (opcional), `situacao` (`em_andamento`, `sucesso`, `parcial`, `falha`, `sem_coletas` ou `recusada_retencao`), `coletas` (JSON: uma linha por recoleta, com `coleta_id`, `recoleta_de`, `fonte`, `situacao`, `mesmo_conteudo` e `motivo`), `avisos_gerados`, `motivo`.
- `avisos`: novidade detectada sobre uma entidade monitorada. Colunas: `id`, `caso_id` (FK `casos`), `monitoramento_id` (FK `monitoramentos`), `execucao_id` (FK `monitoramento_execucoes`), `entidade_id` (FK `entidades`), `tipo` (`quadro_societario`, `contrato_novo`, `sancao_nova` ou `termo_novo`), `titulo`, `descricao`, `detalhes` (JSON), `captura_ids` (JSON), `criado_em`, `lido_em` e `lido_por` (opcionais).

Nenhuma coluna nova em tabela existente. Valores novos em colunas existentes:

- `coletas.situacao`: `nao_configurado`. `coletas.fonte` e `capturas.fonte`: `pncp_termos`, `tse_arquivo` e `edital_arquivo`. `coletas.usuario` pode ser `agendador`.
- `entidades.tipo`: `sancao` (chave `<CEIS|CNEP>:<id do registro>`), `candidato` (chave `<ano>:<SQ_CANDIDATO>`) e `edital` (chave = nome do arquivo). Em `pessoa`, chaves novas para servidor (`servidor:<código SIAPE>:<CPF descaracterizado>|<nome>`) e doador pessoa física (`doador:<CPF descaracterizado>|<nome>`), com o atributo `papel_fonte`.
- `entidades.atributos` (JSON): no contrato, `termos` (lista de `{sequencial, numero, tipo, objeto, valor_acrescido, acrescimo_supressao, reajuste, data_assinatura, fundamento_legal, captura_id}`), `valorInicial`, `sequencialContrato`, `numeroControlePncpCompra`, `categoriaProcessoId`, `categoriaProcessoNome` e `codigoUnidade`; no órgão, `esfera` e `poder`; no edital, `captura_texto_id`.
- `arestas.tipo`: `sancionada_em` (empresa → sanção), `servidor_de` (pessoa → órgão), `doou_para` (empresa ou pessoa → candidato; uma aresta por receita, com `valor` e `data`) e `edital_de` (órgão → edital).
- `alertas.tipologia`: `T5` a `T10`.
- `auditoria.usuario` pode ser `agendador`, e `auditoria.acao` ganha as ações listadas em "Horizonte 2 do backlog".

Relações e cardinalidades:

- Caso 1 — N Monitoramento; Entidade 1 — 0..1 Monitoramento (restrição única por caso e entidade).
- Monitoramento 1 — N MonitoramentoExecucao; Caso 1 — N MonitoramentoExecucao.
- MonitoramentoExecucao 1 — N Aviso; Monitoramento 1 — N Aviso; Entidade 1 — N Aviso; Caso 1 — N Aviso.
- MonitoramentoExecucao N — N Coleta, sem chave estrangeira: os ids das recoletas ficam no JSON `coletas` da execução, e cada recoleta aponta para a coleta de origem por `coletas.recoleta_de`.
- Aviso N — N Captura, sem chave estrangeira (ids em `captura_ids`).
- No grafo do caso (tabelas `entidades` e `arestas`): Empresa 1 — N Sanção; Órgão 1 — N Servidor (pessoa); Doador N — N Candidato (uma aresta por doação); Órgão 1 — N Edital; Contrato 1 — N Termo (termo como item do JSON `termos` do contrato, não como entidade).

As três tabelas novas são tabelas comuns: o papel da aplicação recebe `SELECT`, `INSERT`, `UPDATE` e `DELETE` nelas, e as tabelas só de acréscimo continuam sendo `auditoria`, `capturas` e `alerta_decisoes`. `python -m buscador.migrar` as cria num banco do horizonte 1 sem tocar nos gatilhos nem nos dados existentes, e pode ser rodado de novo sem efeito.

## Pipeline de integração (RNF11, H16)

`.github/workflows/ci.yml` roda a cada `push` e `pull_request`: instala as dependências travadas (`uv sync --locked`), `ruff check`, `ruff format --check`, Bandit e `pytest`, este com um PostgreSQL descartável do próprio job, para que os testes de gatilhos, privilégios e evolução do esquema rodem de verdade. Um último passo falha se algum teste de PostgreSQL tiver sido pulado. Não há `continue-on-error`: qualquer etapa que falhe derruba o pipeline.

**Por que Bandit, e não Semgrep.** (1) O back-end é só Python, e o Bandit é feito para Python: analisa a árvore sintática atrás de padrões inseguros conhecidos (`eval`, `subprocess` com `shell`, SQL montado por texto, desserialização insegura, segredo fixo no código). (2) Ele é uma dependência de desenvolvimento comum, travada no `uv.lock` como as outras: o resultado é o mesmo na máquina do desenvolvedor e no pipeline, e a análise roda sem rede. O Semgrep com as regras do registro público baixa as regras a cada execução, o que torna o resultado dependente de um serviço externo e da data. (3) A configuração cabe no `pyproject.toml`. O que se perde: o Semgrep cobre várias linguagens e permite escrever regras próprias do projeto, e nenhum dos dois substitui a revisão humana nem os testes de regressão de segurança de `tests/test_seguranca.py`. Análise dinâmica (DAST), SBOM e gestão de segredos, também citados no RNF11, não foram implementados.

**Resultado e supressões.** Na data desta versão o Bandit não aponta nada em `buscador/` e `scripts/`. As supressões são pontuais e justificadas no código: `buscador/documentos.py`, `B404`/`B603` (roda, sem shell e com argumentos fixos, o extrator de PDF do próprio projeto num processo separado, para poder impor o prazo); `buscador/exportacao.py`, `# nosec B405` (o `xml.etree` é usado só para **gerar** o GraphML, nunca para interpretar XML de fora); e, em `scripts/medir_grafo.py`, `B404`/`B603` (o script reexecuta a si mesmo, com argumentos fixos, para medir a memória de cada estratégia), `B311` (sorteio com semente fixa para o grafo sintético, não é segredo) e `B608` (nomes de função e de tabela vindos de constantes do próprio script). Os testes ficam fora da análise (`exclude_dirs`), porque usam `assert` e tokens sintéticos de propósito.

## Limitações do MVP

Conforme `docs/mvp-escopo.md`:

- **Fontes:** dados abertos do CNPJ e do TSE (arquivos locais no layout oficial, sem baixar os conjuntos), PNCP (contratos e termos), Portal da Transparência (CEIS, CNEP e servidores, com chave do operador) e editais em texto ou PDF com camada de texto. O que ficou pendente nessas fontes está em "Pendências do horizonte 2" e em "Fontes reais e piloto com dados públicos". As demais fontes do RF01 (DODF/DOU, TCDF/TCU, TSE, tribunais, juntas, cartórios, redes sociais, WHOIS, notícias) são trabalho futuro.
- **Segurança (RNF01):** usuários reais com senha, MFA por TOTP e sessão; acesso por caso; capturas cifradas no disco (horizonte 3). A trilha de auditoria (`GET /auditoria` e as entradas no relatório) é restrita a `delegado`/`admin` e não é filtrada por caso. `GET /auditoria/verificar` é aberto a todos os perfis e devolve só o resumo da integridade. Sem TLS (as portas ficam em `127.0.0.1`), sem cofre de chaves, sem recifragem na troca de chave, banco não cifrado pela aplicação, sem nuvem soberana (ver "Pendências do horizonte 3").
- **Volume:** a coleta e a resolução rodam dentro da requisição. Os limites de bytes, registros, páginas e período existem para manter isso viável; o dump completo do CNPJ não cabe neles e precisa ser recortado antes.
- **Gestão de casos (RF08):** criar, listar e ver, com membros, anotações e tarefas (horizonte 3); sem notificações e sem anexos.
- **Relatórios (RF09):** JSON e Markdown; sem modelo padronizado de Relatório de Inteligência. A exportação do grafo (RNF09) é em formatos abertos (GraphML e CSV); a importação no i2 e no Maltego não foi testada.
- **Escala (RNF06):** a vizinhança e o caminho são consultados no banco (medidos com até 1 milhão de arestas; `docs/desempenho-grafo.md`). As tipologias, a resolução, o relatório e a exportação ainda carregam o caso inteiro em memória; um banco de grafos fica como alternativa futura.
- **Resolução:** sem fusão manual das sugestões (só leitura) e sem tratamento de grafias diferentes além da normalização de acentos e espaços.
- **Fora do escopo:** RF05 (linha do tempo patrimonial), RF10 (dados sob ordem judicial), RNF03 (anonimato operacional), RNF07. Do RNF11 há só o pipeline com análise estática; do RNF12, só a retentativa e o registro de falha por fonte.
- **Retenção (RNF04):** sem prorrogação do prazo, sem alteração da finalidade e sem descarte de dados (ver "Finalidade e retenção").
- **Alertas (RF12):** decide quem é investigador ou responsável no caso; sem segunda conferência.
- **Concorrência da auditoria:** no PostgreSQL uma trava consultiva de transação (`pg_advisory_xact_lock`) serializa os escritores para manter a cadeia linear. As rotas gravam a entrada no fim do trabalho (o relatório, depois de gerado), para que a trava dure só o instante da gravação. No SQLite não há essa serialização: duas gravações simultâneas podem colidir e uma delas falhar.
- **Custo da verificação:** a verificação da cadeia é linear no número de entradas (memória constante, em lotes) e roda a cada relatório e a cada `GET /auditoria/verificar`.
