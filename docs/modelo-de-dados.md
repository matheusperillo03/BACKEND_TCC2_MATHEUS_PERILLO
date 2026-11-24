# Modelo de dados

[Voltar ao README](../README.md)

## Mudanças do horizonte 1

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

## Mudanças do horizonte 2

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
- `auditoria.usuario` pode ser `agendador`, e `auditoria.acao` ganha as ações listadas em "Horizonte 2: rotas novas e alteradas", em [api.md](api.md).

Relações e cardinalidades:

- Caso 1 — N Monitoramento; Entidade 1 — 0..1 Monitoramento (restrição única por caso e entidade).
- Monitoramento 1 — N MonitoramentoExecucao; Caso 1 — N MonitoramentoExecucao.
- MonitoramentoExecucao 1 — N Aviso; Monitoramento 1 — N Aviso; Entidade 1 — N Aviso; Caso 1 — N Aviso.
- MonitoramentoExecucao N — N Coleta, sem chave estrangeira: os ids das recoletas ficam no JSON `coletas` da execução, e cada recoleta aponta para a coleta de origem por `coletas.recoleta_de`.
- Aviso N — N Captura, sem chave estrangeira (ids em `captura_ids`).
- No grafo do caso (tabelas `entidades` e `arestas`): Empresa 1 — N Sanção; Órgão 1 — N Servidor (pessoa); Doador N — N Candidato (uma aresta por doação); Órgão 1 — N Edital; Contrato 1 — N Termo (termo como item do JSON `termos` do contrato, não como entidade).

As três tabelas novas são tabelas comuns: o papel da aplicação recebe `SELECT`, `INSERT`, `UPDATE` e `DELETE` nelas, e as tabelas só de acréscimo continuam sendo `auditoria`, `capturas` e `alerta_decisoes`. `python -m buscador.migrar` as cria num banco do horizonte 1 sem tocar nos gatilhos nem nos dados existentes, e pode ser rodado de novo sem efeito.

## Mudanças do horizonte 3

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
| `extracao_ia_blocos` (nova em 18/11/2026) | `id`, `extracao_id` → `extracoes_ia.id`, `numero` (único por execução), `pai_numero`, `nivel`, `inicio`, `fim` (intervalo no texto extraído), `situacao` (`pendente`\|`em_andamento`\|`concluido`\|`falho`\|`dividido`), `tentativas`, `causa`, `motivo`, `metricas` (JSON), `iniciado_em`, `concluido_em` | 1 execução : N blocos. `extracoes_ia` ganhou `justificativa`, `trava` (índice único), `trabalhador`, `enfileirado_em`, `cancelamento_pedido_em`, `cancelamento_pedido_por` e `retomadas`; `situacao` passou de 15 para 30 caracteres (a migração alarga a coluna no PostgreSQL, sem perder dados) |
| `sugestoes_ia` (nova) | `id`, `caso_id` → `casos.id`, `extracao_id` → `extracoes_ia.id`, `captura_id` → `capturas.id`, `tipo`, `valor`, `campos` (JSON), `trecho`, `posicao_inicio`, `posicao_fim`, `confianca`, `situacao`, `decidido_por`, `decidido_em`, `justificativa_decisao`, `entidade_id` → `entidades.id` (opcional) | 1 execução : N sugestões; 1 captura : N sugestões; 0 ou 1 entidade por sugestão aceita (1 entidade : N sugestões) |
| `capturas` (sem coluna nova) | `caminho_relativo` termina em `.gcm` quando a carga está cifrada; `metadados.cifra_em_repouso` traz o algoritmo e o identificador da chave | — |
| `arestas` (sem coluna nova) | tipo novo `citado_em` (entidade aceita de uma sugestão → edital), com o trecho citado nos atributos | — |

Gatilhos novos (SQLite e PostgreSQL): `anotacoes_sem_update`, `anotacoes_sem_delete`, `tarefa_eventos_sem_update`, `tarefa_eventos_sem_delete` e, no PostgreSQL, os de `TRUNCATE`. O papel da aplicação só tem `SELECT` e `INSERT` nessas duas tabelas, como na auditoria, nas capturas e nas decisões de alerta. Nenhum índice novo em `arestas`: os de `origem_id` e `destino_id` já existiam e são os usados pelas consultas do H29.
