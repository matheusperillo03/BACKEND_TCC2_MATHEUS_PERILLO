# Uso da API

[Voltar ao README](../README.md)

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

## Rotas (contrato v0, com o horizonte 1 do backlog)

As rotas novas e alteradas no horizonte 2 (novas fontes, `GET /tipologias`, monitoramento e avisos) estão em "Horizonte 2: rotas novas e alteradas", logo abaixo desta seção.

As rotas marcadas com **(novo)** ou **(alterado)** mudaram no horizonte 1 (H12 a H17 e H31). O contrato em `docs/mvp-escopo.md` (seção 5) da monografia é o da versão anterior; este documento é a referência atual.

| Método e rota | Descrição |
|---|---|
| `GET /health` | `{"status":"ok","modo_dev":true\|false}`. `modo_dev` (18/11/2026) só diz se os tokens de desenvolvimento existem (`MODO_DEV=1`); a tela de entrada do front-end usa o campo para esconder o acesso por token fora desse modo. Não é segredo: `/docs` já responde `404` fora do modo |
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

## Horizonte 2: rotas novas e alteradas

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

## Horizonte 3: rotas novas e alteradas

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
| `POST /casos/{id}/capturas/{captura_id}/extracao-ia` **(alterada em 18/11/2026)** | Põe na fila a extração do modelo local sobre a captura e responde **`202`** com a execução (`situacao: "na_fila"`, sem sugestões): `{"id","provedor","modelo","modelo_versao","servidor_versao","parametros","prompt_sha256","captura_sha256","situacao","motivo","blocos","itens_recebidos","itens_aceitos","itens_recusados","recusas":[{"motivo","tipo","valor","trecho"}],"metricas","progresso":{...},"sugestoes":[]}`. O processamento roda fora da requisição (ver "Extração em segundo plano", em [ia-local.md](ia-local.md)). `409` com a IA desligada, captura não íntegra ou **outra execução na fila ou em andamento para o mesmo documento**. Não há mais `502`: a falha fica na execução (`situacao: "falha"`) |
| `GET /casos/{id}/extracoes-ia` | Execuções do caso, inclusive as que falharam e as que ainda estão na fila ou em andamento; cada uma traz `progresso` |
| `GET /casos/{id}/extracoes-ia/{eid}` (nova) | Uma execução, com as `sugestoes` que ela já produziu, as recusas e as métricas |
| `GET /casos/{id}/extracoes-ia/{eid}/progresso` (nova) | Andamento, para consulta periódica: `{"id","captura_id","situacao","motivo","ativa","retomavel","cancelamento_pedido","posicao_na_fila","blocos_total","blocos_concluidos","blocos_falhos","blocos_pendentes","bloco_atual","itens_recebidos","itens_aceitos","itens_recusados","tempo_decorrido_ms","estimativa_restante_ms","retomadas","iniciado_em","concluido_em","blocos":[{"numero","situacao","caracteres","tentativas","causa","duracao_ms","pai_numero"}]}`. Só números e códigos. **Não grava entrada de auditoria** (decisão do autor: é consulta periódica, sem conteúdo do documento); exige ser membro do caso |
| `POST /casos/{id}/extracoes-ia/{eid}/cancelar` (nova) | Cancela: na fila, na hora; em andamento, o modelo para no próximo token recebido. Devolve o andamento. Os blocos concluídos ficam gravados. `409` se a execução já terminou |
| `POST /casos/{id}/extracoes-ia/{eid}/retomar` (nova) | `202`. Devolve à fila uma execução `concluida_com_falhas`, `falha`, `cancelada` ou `interrompida`; só os blocos pendentes e os que falharam são processados. `409` nas outras situações ou com outra execução ativa para o documento |
| `GET /casos/{id}/sugestoes-ia?situacao=&captura_id=` | `[{"id","extracao_id","captura_id","captura_sha256","tipo","valor","campos","trecho","posicao_inicio","posicao_fim","confianca","situacao":"pendente"\|"aceita"\|"recusada","decidido_por","decidido_em","justificativa_decisao","entidade_id","modelo","provedor"}]` |
| `POST /casos/{id}/sugestoes-ia/{sid}/decisao` | `{"decisao":"aceita"\|"recusada","justificativa"}`. Só a aceitação pode criar entidade. `409` se já decidida |

Ações novas na auditoria: `auth.senha_aceita`, `auth.login`, `auth.login_falhou`, `auth.mfa_falhou`, `auth.logout`, `auth.trocar_senha`, `auth.troca_de_senha_falhou`, `auth.sessao_script`, `usuario.criar`, `usuario.alterar`, `usuario.listar`, `acesso.perfil_negado`, `caso.acesso_negado`, `membro.listar`, `membro.definir`, `membro.remover`, `anotacao.criar`, `anotacao.listar`, `tarefa.criar`, `tarefa.alterar`, `tarefa.ver`, `tarefa.listar`, `exportacao.gerar`, `captura.texto`, `ia.extrair`, `ia.listar_extracoes`, `ia.listar_sugestoes` e `ia.sugestao_decidir`. Em 18/11/2026 (extração em segundo plano): `ia.extrair` passa a registrar o **pedido**; o desfecho é gravado pelo trabalhador, como usuário de sistema `extrator`, em `ia.extracao_concluida` (sucesso ou concluída com falhas), `ia.extracao_falha`, `ia.extracao_cancelada` ou `ia.extracao_interrompida`; e há `ia.extracao_cancelar`, `ia.extracao_retomar` e `ia.consultar_extracao`.

## Importação sob ordem judicial

As rotas `/importacao-judicial/*` estão em [importacao-judicial.md](importacao-judicial.md).
