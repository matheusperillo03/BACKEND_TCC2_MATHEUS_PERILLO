# Variáveis de ambiente

[Voltar ao README](../README.md)

Todas estão em `.env.example`.

| Variável | Padrão | Uso |
|---|---|---|
| `TOKENS_DEV` | vazio | **Só com `MODO_DEV=1`.** `token:usuario:perfil`, separados por vírgula. Perfis: `analista`, `delegado`, `admin`. Com `MODO_DEV=0`, precisa ficar vazia: preenchida, impede a inicialização |
| `CUSTODIA_CHAVE` (ou `CUSTODIA_CHAVE_ARQUIVO`) | **obrigatória, sem padrão** | Chave mestra da criptografia em repouso, 32 bytes em hexadecimal. Fica só no ambiente ou num arquivo, nunca no banco. As demais variáveis do horizonte 3 estão em "Variáveis do horizonte 3", abaixo |
| `CHAVE_IDENTIFICADORES` | **obrigatória com a fonte do TSE ligada** (`TSE_DIR_BASE`; no Compose ela vem ligada) | Chave própria do resumo de identificadores, 32 bytes em hexadecimal (`openssl rand -hex 32`). O título eleitoral do candidato é guardado só como HMAC-SHA256 com esta chave, que liga as candidaturas da mesma pessoa entre eleições. Não deriva da `CUSTODIA_CHAVE` e não pode ser igual a ela, à `AUDITORIA_CHAVE_HMAC` nem à `IMPORTACAO_JUDICIAL_CHAVE`. Sem ela, com o TSE ligado, o serviço não inicia; com o TSE desligado, pode ficar vazia. Trocar a chave muda os resumos: candidaturas já coletadas deixam de coincidir com as novas até serem recoletadas |
| `AUDITORIA_CHAVE_HMAC` | **obrigatória, sem padrão** | Chave do HMAC da cadeia de auditoria (mínimo de 32 caracteres). Fica só no ambiente, nunca no banco |
| `POSTGRES_PASSWORD`, `POSTGRES_APP_PASSWORD` | **obrigatórias no Compose** | Senha do papel dono do banco e senha do papel da aplicação |
| `MODO_DEV` | `0` | `1` aceita os tokens fixos de `TOKENS_DEV` (sem senha e sem MFA) e liga `/docs`, `/redoc` e `/openapi.json`. Só para a máquina do desenvolvedor: com `URL_PUBLICA` de fora da máquina, o serviço não inicia |
| `URL_PUBLICA` | vazio (só a própria máquina) | Endereço pelo qual os usuários chegam de fora: só esquema, nome e porta, por exemplo `https://nome.rede.ts.net`. Fora da máquina exige `https` e `MODO_DEV=0`, senão o serviço não inicia. A API responde `421` a requisição cujo `Host` não é local nem este |
| `TLS_DIR_CERTIFICADO`, `TLS_ENDERECO` | sem padrão, `127.0.0.1` | Só com `docker-compose.tls.yml`: pasta do host com `servidor.crt` e `servidor.key` (montada só para leitura) e endereço em que as portas 443 e 80 são publicadas (ver "Acesso de fora da máquina e TLS", em [seguranca.md](seguranca.md)) |
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
| `PORTAL_TRANSPARENCIA_CHAVE` | vazio | Chave da API do Portal da Transparência; vazia, a fonte fica "não configurada". As demais variáveis do horizonte 2 estão em "Variáveis do horizonte 2", abaixo |
| `COLETA_TENTATIVAS`, `COLETA_ESPERA_RETENTATIVA_SEGUNDOS` | `3`, `1` | Retentativa dos conectores sem ajuste próprio (`cnpj_arquivo`, `tse_arquivo`, `edital_arquivo` e Portal da Transparência): tentativas por operação e espera, que cresce a cada tentativa |
| `RESOLUCAO_MAX_GRUPO` | `50` | Maior grupo (mesmo endereço ou mesmo nome) expandido par a par |
| `T1_DIAS_MINIMOS`, `T2_MINIMO_EMPRESAS`, `T3_RAZAO_MINIMA` | `180`, `3`, `10` | Limiares das tipologias (a T4 não tem limiar) |

## Variáveis do horizonte 2

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
| `L9_ANOS_MANDATO`, `L13_DIAS_ABERTURA`, `L13_CNAES_CAMPANHA` | `4`, `180`, vazio | L9: duração do mandato, em anos, contada de 1º de janeiro do ano seguinte ao da eleição. L13: dias mínimos entre o início da atividade do fornecedor e a despesa de campanha, e CNAEs típicos de campanha (por vírgula; código de 7 dígitos ou prefixo de 2 a 6). Lista vazia deixa o critério do CNAE desligado (ver [tipologias.md](tipologias.md)) |
| `T10_EXPRESSOES_EXTRAS` | vazio | Expressões regulares extras, separadas por `;;` (até 20, de até 200 caracteres) |
| `MONITORAMENTO_AGENDADOR_ATIVO`, `MONITORAMENTO_INTERVALO_SEGUNDOS`, `MONITORAMENTO_PERIODICIDADE_MINIMA_HORAS` | `1`, `60`, `1` | Agendador interno |

Configuração inválida (JSON de limites malformado, critério desconhecido, expressão regular que não compila) impede a inicialização, como as demais.

## Variáveis do horizonte 3

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
| `IA_KEEP_ALIVE` | vazio | Tempo que o Ollama mantém o modelo carregado (`5m`, `1h`, `0`). Vazio: o campo não é enviado e vale o padrão do servidor |
| `IA_TIMEOUT_SEGUNDOS`, `IA_MAX_CARACTERES`, `IA_MAX_ITENS`, `IA_TEMPERATURA`, `IA_SEMENTE` | `120`, `200000`, `300`, `0`, `0` | Tempo limite das chamadas curtas e da conexão, limites e parâmetros de geração |
| `IA_NUM_CTX`, `IA_NUM_PREDICT` | `8192`, `2048` | Janela de contexto pedida ao servidor e teto de tokens gerados por bloco |
| `IA_BLOCO_TOKENS`, `IA_SOBREPOSICAO_TOKENS`, `IA_CARACTERES_POR_TOKEN` | `700`, `40`, `3.0` | Tamanho do bloco e da sobreposição em tokens estimados, e a razão usada na estimativa. `IA_BLOCO_TOKENS + IA_NUM_PREDICT + 600` tem de caber em `IA_NUM_CTX` |
| `IA_BLOCO_TEMPO_SEGUNDOS`, `IA_SEM_TOKEN_SEGUNDOS` | `600`, `120` | Por bloco: tempo limite da geração e tempo máximo sem receber token (travamento) |
| `IA_REPETICOES`, `IA_ESPERA_REPETICAO_SEGUNDOS` | `2`, `5` | Repetições de um bloco depois de falha transitória e a primeira espera (dobra a cada repetição) |
| `IA_MAX_DIVISOES`, `IA_FALHAS_SEGUIDAS_MAX` | `3`, `3` | Divisões ao meio de um bloco (saída cortada ou tempo esgotado) e blocos seguidos com falha transitória que interrompem a execução |
| `IA_TRABALHADOR_ATIVO`, `IA_TRABALHADOR_INTERVALO_SEGUNDOS` | `1`, `2` | Liga o trabalhador da fila neste processo e o intervalo em que ele consulta a fila quando está parado |

## Importação sob ordem judicial

As variáveis `IMPORTACAO_JUDICIAL_*` estão em [importacao-judicial.md](importacao-judicial.md).
