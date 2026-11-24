# Arquitetura

[Voltar ao README](../README.md)

## Visão geral

Back-end do MVP do buscador OSINT para investigação de corrupção e lavagem de dinheiro. Implementa a fatia vertical definida em `docs/mvp-escopo.md` do repositório da monografia: **caso → coleta de fonte oficial → resolução de entidades → grafo de vínculos → alertas de tipologia explicados → cadeia de custódia e auditoria → relatório**.

O horizonte 3 do backlog (usuários com MFA, acesso por caso, criptografia em repouso, anotações e tarefas, exportação do grafo, consultas de vínculo no banco e extração assistida por IA local) está descrito em [seguranca.md](seguranca.md), em [ia-local.md](ia-local.md) e, neste documento, nas seções "Anotações e tarefas (H37)", "Exportação do grafo (H38)" e "Escala do grafo (H29)".

Só há coleta passiva de dados abertos: dados abertos do CNPJ da Receita Federal, PNCP (contratos e termos de contrato), Portal da Transparência (CEIS, CNEP e servidores), dados abertos do TSE (candidatos e receitas de campanha) e editais em texto ou em PDF com camada de texto. O teste dos conectores contra as fontes reais (17/11/2026) e a preparação do piloto com dados públicos reais estão em [dados-reais.md](dados-reais.md). O horizonte 2 do backlog (novas fontes, tipologias T5 a T10 e monitoramento contínuo) está descrito em [conectores.md](conectores.md), em [tipologias.md](tipologias.md) e, neste documento, em "Monitoramento contínuo (H18)". Nenhum dado pessoal real entra nos testes nem na demonstração: as fixtures são sintéticas, no layout oficial, com CPFs já descaracterizados como manda o layout.

**Horizonte 3 do backlog (H23, H37, H38, H29 e H22).** Implementado em 16/11/2026. Resumo: usuários reais com senha, MFA por TOTP e sessão; acesso por caso (membros com papel); criptografia em repouso das capturas; anotações e tarefas; exportação do grafo em formatos abertos; consultas de vínculo feitas no banco, com medição de desempenho; e extração assistida por modelo de linguagem **local**, desligada por padrão. Os testes estão em `tests/test_horizonte3.py`.

## Estrutura do código

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
scripts/benchmark_ia.py    medição da extração por IA com o Ollama local (manual; fora da aplicação)
scripts/testar_fontes_reais.py  teste de fumaça das fontes REAIS (manual, usa a rede; pula o Portal sem chave)
scripts/recortar_cnpj.py   recorta dos ZIPs oficiais do CNPJ só as linhas dos CNPJs do caso (leitura em fluxo)
scripts/gerar_fixtures.py  gera as fixtures (reprodutível)
scripts/gerar_fixtures_h2.py  gera as fixtures do horizonte 2 em tests/fixtures/h2 (reprodutível)
docker/postgres-init/      cria o papel da aplicação quando o volume do PostgreSQL nasce
.github/workflows/ci.yml   pipeline: ruff, Bandit e pytest (com PostgreSQL)
tests/                     pytest (392 testes, 8 deles só com PostgreSQL; em 17/11/2026, com SQLite: 384 passaram e 8 ficaram como "skipped"; test_privacidade_cpf.py, test_cnpj_alfanumerico.py e test_tse_fluxo.py cobrem os três riscos do piloto, com CPFs e CNPJs alfanuméricos sorteados a cada execução por tests/sinteticos.py; test_seguranca.py traz as regressões da auditoria de segurança, test_horizonte1.py as histórias H12 a H17 e H31, test_horizonte2.py as histórias H18, H19, H32 a H36 e H39, test_horizonte3.py as do horizonte 3, test_editais_pdf.py a leitura de PDF com PDFs sintéticos gerados no próprio teste e test_fontes_reais.py as regressões das divergências achadas nas fontes reais, reproduzidas com dados sintéticos) e fixtures sintéticas
```

## Cadeia de custódia, coletas e capturas

**Cadeia de custódia (RF07).** Cada arquivo lido e cada resposta HTTP é guardada exatamente como veio em `<DADOS_DIR>/capturas/<aa>/<sha256>`, com SHA-256, carimbo UTC, fonte, origem e metadados (status HTTP, cabeçalhos selecionados, parâmetros da consulta; tabela, nome do arquivo e codificação para o CNPJ). A cópia e o hash são feitos em blocos. Toda entidade e aresta registra o `captura_id` de origem; a entidade guarda todas as capturas que a alimentaram (`fontes`) e, em `atributos.origem_atributos`, qual captura forneceu o valor em vigor de cada atributo. É essa captura que as tipologias citam como evidência. `GET /capturas/{id}/verificar` recalcula o hash a partir dos bytes. Se a coleta falha no meio, as capturas já guardadas continuam registradas e são citadas na entrada `coleta.falhou`.

**Versão do conector, recoleta e comparação (RF07, H14).** Cada conector tem nome e versão (`Conector.nome` e `Conector.versao`; hoje `cnpj_arquivo 1.1.0`, `pncp 1.1.0` e `portal_transparencia 0.1.0`). A versão muda quando muda o que o conector pede à fonte ou como interpreta a resposta, e fica gravada em cada captura (`capturas.conector_nome` e `conector_versao`) e em cada coleta. `POST /casos/{id}/coletas/{coleta_id}/recoletar` repete a coleta e compara as capturas novas com as antigas, pareadas pela origem: primeiro o SHA-256; se ele difere, as duas cargas guardadas são relidas da custódia e interpretadas pelo conector (`registros_brutos`), e a resposta diz quais registros entraram, saíram ou mudaram e quais campos mudaram, com o valor de antes e o de depois. No CNPJ os campos têm o nome da posição no layout (por exemplo, `capital_social`); no PNCP, o nome do campo do JSON (por exemplo, `valorGlobal`, `orgaoEntidade.razaoSocial`). A recoleta só acrescenta: a captura antiga continua guardada e verificável. As entidades do caso são atualizadas pela resolução como em qualquer coleta (o último valor não vazio prevalece), e a captura de origem de cada atributo continua em `origem_atributos`.

**Falhas de coleta e retentativa (RNF12, H15).** Toda coleta vira uma linha em `coletas`, criada como `em_andamento` antes de qualquer consulta e fechada como `sucesso` ou `falha`, com `tentativas` e `motivo`. Como cada requisição coleta uma fonte, o histórico (`GET /casos/{id}/coletas`) e o relatório mostram o resultado fonte a fonte. A retentativa é uma só para todos os conectores (`conectores/base.py`, classe `Retentativa`): a operação de E/S sinaliza `FalhaTransitoria` e é repetida com espera crescente. No PNCP ela cobre erro de rede e HTTP 429/5xx por página (`PNCP_TENTATIVAS`); no `cnpj_arquivo`, erro de abertura ou de leitura de cada arquivo (`COLETA_TENTATIVAS`); o stub do Portal da Transparência recebe a mesma política, mas ainda não faz E/S. Erros definitivos (parâmetro inválido, arquivo acima do limite, pasta fora da raiz) não são repetidos. `tentativas` é o maior número de tentativas que uma operação da coleta precisou: `0` quando a coleta foi recusada antes de tentar algo, `1` quando deu certo de primeira. Numa falha inesperada, o `motivo` do histórico traz só o tipo do erro; o detalhe fica na auditoria, restrita a `delegado` e `admin`. Controle de taxa e detecção de mudança de formato, também citados no RNF12, não foram implementados.

**Capturas sintéticas.** Uma captura que não veio da fonte oficial nunca se parece com uma que veio. Toda captura grava `metadados.simulado` (`true` ou `false`), e as sintéticas têm a origem com o prefixo `simulado://` (por exemplo, `simulado://pncp.gov.br/api/consulta/v1/contratos?...` e `simulado://cnpj_arquivo/CNPJ_DIR_BASE/cnpj/...`). São sintéticas: (a) as respostas do PNCP servidas por um transporte injetado (testes e `scripts/carregar_demo.py`); (b) os arquivos do CNPJ lidos de uma pasta que contém o arquivo-marcador `SINTETICO` (qualquer extensão; `tests/fixtures/cnpj/SINTETICO.txt` é gerado por `scripts/gerar_fixtures.py`). A regra é imposta na camada de custódia, e a marca segue para as evidências dos alertas, para `url_fonte` dos contratos e para o relatório (campo `contem_capturas_simuladas`, coluna "Sintética" e aviso no topo do Markdown).

## Auditoria

**Auditoria (RNF02).** Tabela `auditoria` só de acréscimo. Cada entrada traz `versao`, `seq`, `quando`, `usuario`, `acao`, `caso_id`, `justificativa`, `detalhes`, `hash_anterior` e `hash`. O ORM recusa `UPDATE`/`DELETE`, e o banco também (gatilhos em SQLite e PostgreSQL; no PostgreSQL há ainda o gatilho de `TRUNCATE`, e o papel da aplicação não tem esses privilégios nem é dono dos gatilhos). Toda rota que lê ou escreve dados de caso registra uma entrada na mesma transação da ação, com o perfil do usuário em `detalhes`. Os `detalhes` têm tamanho limitado: valores grandes vindos do cliente viram um resumo com tamanho e SHA-256.

*Decisão de versão da cadeia (versão 2).* A versão 1 calculava `sha256(json_canonico(entrada) + hash_anterior)`, sem chave: quem tivesse a credencial do banco podia reescrever a história e recalcular hashes válidos. A versão 2 muda três coisas:

1. `hash = HMAC-SHA256(AUDITORIA_CHAVE_HMAC, json_canonico(entrada_sem_hash))`. A entrada inclui `versao` e `hash_anterior`; a primeira encadeia ao hash gênese (64 zeros). A chave fica só no ambiente da API. Sem ela não se produz um hash válido.
2. O JSON canônico passa a ser ASCII puro (`ensure_ascii=True`), com chaves ordenadas e sem espaços, e recusa `NaN`/`Infinity` (`allow_nan=False`). Um valor fora do JSON estrito aborta a requisição em vez de entrar na trilha.
3. **Âncora externa.** Depois de cada confirmação, o topo `(seq, hash)` é copiado para `<DADOS_DIR>/ancora/auditoria.json`, fora do banco, com um MAC da mesma chave; o arquivo só avança. `GET /auditoria/verificar` e o relatório percorrem a cadeia em lotes (sequência, encadeamento e HMAC) e comparam com a âncora: se o banco termina antes da entrada ancorada, a cadeia foi cortada; se o hash da entrada ancorada difere, foi reescrita. Âncora ausente com entradas no banco, ou com MAC inválido, também conta como quebra.

Não há compatibilidade com a versão 1: entradas sem chave não podem ser autenticadas depois, então um banco com a tabela antiga é recusado na inicialização, com a instrução de recriá-lo (os dados do MVP são de demonstração). Consequências para a operação: guardar `AUDITORIA_CHAVE_HMAC` em local seguro (trocá-la invalida a verificação das entradas antigas); fazer cópia de segurança do banco e de `DADOS_DIR` juntos; ao recriar o banco de propósito, apagar também `<DADOS_DIR>/ancora/`, senão a verificação acusa corte. Limite conhecido: quem controla ao mesmo tempo o banco, a chave e o volume de dados ainda consegue reescrever tudo; para isso a âncora precisaria ir para um repositório de escrita única fora do servidor (trabalho futuro). Há testes que adulteram, recalculam e cortam a cadeia por SQL e confirmam a detecção.

## Resolução de entidades

**Resolução de entidades (RF02).** Empresa pela chave `cnpj_basico` (8 dígitos); estabelecimentos são fundidos na empresa (lista `estabelecimentos`, matriz em destaque, `enderecos_normalizados`). Pessoa pela chave `cpf_descaracterizado|nome_normalizado`; sem CPF (sócio estrangeiro), a chave fica restrita ao registro de origem, para que nunca haja fusão só pelo nome. Nome igual com CPF diferente gera `Sugestao` com score 0,5; nome igual com CPF ausente em um dos lados, score 0,7. Nada é fundido automaticamente. Órgão pelo CNPJ do `orgaoEntidade`; contrato pelo `numeroControlePNCP`; `niFornecedor` com 14 dígitos liga ao CNPJ básico (empresa criada a partir do PNCP se não existir nos arquivos). Endereço normalizado = tipo + logradouro + número + bairro + CEP + município + UF (sem o complemento, decisão do autor). Endereço sem logradouro, ou sem CEP e sem município, não gera chave: empresas com endereço em branco não são ligadas entre si. Órgão sem CNPJ e sem nome fica restrito ao próprio contrato. Grupos acima de `RESOLUCAO_MAX_GRUPO` não são expandidos par a par: o endereço compartilhado vira uma estrela (cada empresa ligada à primeira do grupo) e as sugestões de homônimo excedentes não são geradas; os dois casos devolvem aviso na coleta.

## Finalidade e retenção

**Finalidade e retenção (RNF04, H17).** Todo caso é criado com `finalidade` (texto obrigatório) e `retencao_ate` (data). Até o fim desse dia (UTC) o caso funciona normalmente. A partir do dia seguinte ele fica só leitura: novas coletas e recoletas, consultas a entidades, grafo e caminho, execução de tipologias e decisões sobre alertas respondem `403`, e cada recusa entra na auditoria como `caso.retencao_recusa`, com a ação tentada. Continuam disponíveis o próprio caso, o relatório, a lista de alertas e de coletas já produzidos, a comparação de capturas, a custódia (`/capturas/...`) e a trilha de auditoria. Não há rota para prorrogar o prazo nem para alterar a finalidade. Casos criados antes desta versão ficam com finalidade vazia e sem prazo, e por isso não vencem.

*Descarte.* **O protótipo não apaga nada depois do prazo.** As capturas e a trilha de auditoria são só de acréscimo de propósito (é o que sustenta a cadeia de custódia e a detecção de adulteração), e apagar um caso quebraria as duas garantias. O descarte ou o arquivamento dos dados depois do prazo de retenção é decisão do órgão responsável pela investigação, com base nas suas normas, e fica fora do protótipo: a ferramenta só registra a finalidade e o prazo e deixa de permitir uso novo do caso.

## Monitoramento contínuo (H18)

O investigador marca uma entidade do caso e escolhe a periodicidade (em horas). O **agendador interno** (`buscador/agendador.py`, uma thread do próprio serviço, sem serviço externo) acorda a cada `MONITORAMENTO_INTERVALO_SEGUNDOS` e executa os monitoramentos ativos cuja próxima execução já venceu. Cada execução:

1. descobre as **coletas de origem** da entidade: as que produziram capturas citadas por ela e as que a consultaram pelos parâmetros (por exemplo, o CEIS consultado para o CNPJ dela, mesmo sem ter achado nada). Para cada combinação de fonte e parâmetros, vale a coleta bem-sucedida mais recente;
2. **refaz** cada uma como uma recoleta comum (H14): nova linha em `coletas` com `recoleta_de`, capturas novas na custódia e comparação com a coleta anterior;
3. compara a "fotografia" dos vínculos da entidade antes e depois e cria um **aviso** por novidade: sócio ou participação societária nova (`quadro_societario`), contrato novo (`contrato_novo`), sanção nova (`sancao_nova`) e termo de contrato novo (`termo_novo`). A saída de um sócio vem da comparação de capturas do H14 (registro que sumiu do arquivo de sócios), porque a resolução nunca apaga arestas;
4. grava a execução em `monitoramento_execucoes` e na auditoria (`monitoramento.iniciar` e `monitoramento.executar`).

**Retenção (H17).** Caso com o prazo vencido não é monitorado: na primeira execução depois do prazo nada é coletado, a execução fica como `recusada_retencao`, o monitoramento é desativado e a recusa entra na auditoria (`monitoramento.retencao_recusa`).

Recoleta que falha, ou de fonte que deixou de estar configurada, não derruba a execução: ela fica `parcial`. Com mais de um processo da API, ligue o agendador em um só (`MONITORAMENTO_AGENDADOR_ATIVO`).

## Anotações e tarefas (H37)

- **Anotações** em entidades e alertas, com autor, data e texto. São só de acréscimo (gatilhos no banco, como na auditoria): corrigir é anotar de novo. O texto da anotação não é copiado para a trilha de auditoria (só o tamanho e o alvo).
- **Tarefas** do caso, com título, descrição, responsável (membro do caso), situação e prazo. O estado atual fica em `tarefas`; cada criação ou alteração acrescenta uma linha em `tarefa_eventos` (só de acréscimo), com o valor anterior e o novo de cada campo.
- **Retenção (H17):** depois do prazo do caso, anotar, criar e alterar tarefas são recusados (`403`) e a recusa é auditada; ler continua possível.

## Exportação do grafo (H38)

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

## Escala do grafo (H29)

A vizinhança e o caminho deixaram de montar o grafo inteiro do caso em memória (NetworkX) a cada consulta: `buscador/grafo/sql.py` faz a busca em largura **no PostgreSQL**, pelos índices de `arestas`, e carrega só o subgrafo pedido. O caminho usa busca bidirecional. Não há serviço novo. Os números, o método, o script que reproduz (`scripts/medir_grafo.py`) e o motivo de um banco de grafos dedicado ficar como alternativa futura estão em [`docs/desempenho-grafo.md`](desempenho-grafo.md).

Resumo da medição (grafo sintético, PostgreSQL 16 num contêiner local, 16/11/2026; mediana por consulta):

| Arestas (nós) | Antes: NetworkX, grafo inteiro em memória | Agora: busca no banco | Memória do processo (antes → agora) |
|---:|---|---|---|
| 10 mil (5 mil) | vizinhança 141 a 152 ms; caminho 105 ms | vizinhança 1,5 a 6,5 ms; caminho 3,1 ms | 106 → 80 MiB |
| 100 mil (50 mil) | vizinhança 1,7 s; caminho 1,2 s | vizinhança 1,6 a 5,6 ms; caminho 4,6 ms | 369 → 82 MiB |
| 1 milhão (500 mil) | vizinhança 18,8 a 20,0 s; caminho 13,2 s | vizinhança 1,8 a 8,3 ms; caminho 11,3 ms | 2.557 → 83 MiB |

A consulta recursiva única (`WITH RECURSIVE`) também foi medida e ficou mais lenta que a busca nível a nível, com tempo que cresce com o grafo (251 a 667 ms com 1 milhão de arestas); por isso não é a usada. O RNF06 fala em milhões de nós: o que foi medido é 1 milhão de arestas e 500 mil nós.
