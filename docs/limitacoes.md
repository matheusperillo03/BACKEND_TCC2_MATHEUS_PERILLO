# Limitações e pendências

[Voltar ao README](../README.md)

## Limitações do MVP

Conforme `docs/mvp-escopo.md`:

- **Fontes:** dados abertos do CNPJ e do TSE (arquivos locais no layout oficial, sem baixar os conjuntos), PNCP (contratos e termos), Portal da Transparência (CEIS, CNEP e servidores, com chave do operador) e editais em texto ou PDF com camada de texto. O que ficou pendente nessas fontes está em "Pendências do horizonte 2", abaixo, e em [dados-reais.md](dados-reais.md). As demais fontes do RF01 (DODF/DOU, TCDF/TCU, TSE, tribunais, juntas, cartórios, redes sociais, WHOIS, notícias) são trabalho futuro.
- **Segurança (RNF01):** usuários reais com senha, MFA por TOTP e sessão; acesso por caso; capturas cifradas no disco (horizonte 3). A trilha de auditoria (`GET /auditoria` e as entradas no relatório) é restrita a `delegado`/`admin` e não é filtrada por caso. `GET /auditoria/verificar` é aberto a todos os perfis e devolve só o resumo da integridade. Sem TLS (as portas ficam em `127.0.0.1`), sem cofre de chaves, sem recifragem na troca de chave, banco não cifrado pela aplicação, sem nuvem soberana (ver "Pendências do horizonte 3", abaixo).
- **Volume:** a coleta e a resolução rodam dentro da requisição. Os limites de bytes, registros, páginas e período existem para manter isso viável; o dump completo do CNPJ não cabe neles e precisa ser recortado antes.
- **Gestão de casos (RF08):** criar, listar e ver, com membros, anotações e tarefas (horizonte 3); sem notificações e sem anexos.
- **Relatórios (RF09):** JSON e Markdown; sem modelo padronizado de Relatório de Inteligência. A exportação do grafo (RNF09) é em formatos abertos (GraphML e CSV); a importação no i2 e no Maltego não foi testada.
- **Escala (RNF06):** a vizinhança e o caminho são consultados no banco (medidos com até 1 milhão de arestas; `docs/desempenho-grafo.md`). As tipologias, a resolução, o relatório e a exportação ainda carregam o caso inteiro em memória; um banco de grafos fica como alternativa futura.
- **Resolução:** sem fusão manual das sugestões (só leitura) e sem tratamento de grafias diferentes além da normalização de acentos e espaços.
- **Fora do escopo:** RF05 (linha do tempo patrimonial), RF10 (dados sob ordem judicial), RNF03 (anonimato operacional), RNF07. Do RNF11 há só o pipeline com análise estática; do RNF12, só a retentativa e o registro de falha por fonte.
- **Retenção (RNF04):** sem prorrogação do prazo, sem alteração da finalidade e sem descarte de dados (ver "Finalidade e retenção", em [arquitetura.md](arquitetura.md)).
- **Alertas (RF12):** decide quem é investigador ou responsável no caso; sem segunda conferência.
- **Concorrência da auditoria:** no PostgreSQL uma trava consultiva de transação (`pg_advisory_xact_lock`) serializa os escritores para manter a cadeia linear. As rotas gravam a entrada no fim do trabalho (o relatório, depois de gerado), para que a trava dure só o instante da gravação. No SQLite não há essa serialização: duas gravações simultâneas podem colidir e uma delas falhar.
- **Custo da verificação:** a verificação da cadeia é linear no número de entradas (memória constante, em lotes) e roda a cada relatório e a cada `GET /auditoria/verificar`.

## Pendências do horizonte 2

- **Termos do PNCP sem autenticação:** resolvido em 17/11/2026. O `GET .../termos` respondeu sem autenticação no serviço real (`204` para contrato sem termos; array JSON para contrato com termos). Se um dia a resposta for `401`, o conector guarda a resposta na custódia e explica.
- **Download de editais pelo PNCP:** a API de integração tem `GET /v1/orgaos/{cnpj}/compras/{ano}/{sequencial}/arquivos` e `.../arquivos/{sequencialDocumento}`, sem `security` declarada ([OpenAPI](https://pncp.gov.br/api/pncp/v3/api-docs), conferida em 16 e 17/11/2026), e os dois responderam sem autenticação em 17/11/2026. A leitura de PDF já existe (ver "Editais em PDF", em [conectores.md](conectores.md)), mas o **conector não baixa** o documento: o investigador baixa o edital (ou usa `scripts/testar_fontes_reais.py --fontes edital --baixar-edital CNPJ/ANO/SEQUENCIAL`) e o põe na pasta de ingestão. Um conector de download fica como trabalho futuro.
- **Formatos não documentados:** conferidos nas fontes reais em 17/11/2026 e mantidos com o tratamento defensivo. Portal: datas das sanções em `DD/MM/AAAA` (`dataFimSancao` pode vir "Sem informação"), `cpfFormatado` dos servidores já descaracterizado (`***.NNN.NNN-**`), página de 15 itens. TSE (2026): `VR_RECEITA` com vírgula decimal e `DT_RECEITA` em `DD/MM/AAAA`.
- **T9:** conferido em 17/11/2026. O leiame de candidatos de 2026 confirma `SG_UE`: `BR` na abrangência federal, a sigla da UF na estadual e o código do município na municipal. No arquivo real do DF de 2026, os eleitos aparecem em `DS_SIT_TOT_TURNO` como `ELEITO`, `ELEITO POR QP` e `ELEITO POR MÉDIA` (o padrão de `T9_SITUACOES_ELEITO`); o leiame não lista os valores, por isso a lista continua configurável.
- **T5:** só os limites de 2026 foram conferidos. "Serviços de manutenção de veículos automotores" (inciso I) não tem categoria própria no PNCP e cai no inciso II. O limite em dobro do § 2º não é considerado. O contrato no PNCP não diz se houve dispensa (a modalidade está na contratação, que não é coletada).
- **T8:** `valorAcrescido` guarda acréscimo ou supressão sem distinguir; a base é o valor inicial sem atualização monetária; a reforma só é reconhecida quando o objeto traz "reforma" perto de "edifício", "prédio" ou "equipamento" (incluir "prédio" é decisão do autor).
- **T10:** só as duas expressões do exemplo; o restante do manual da Alice não foi transcrito.
- **Monitoramento:** a recoleta repete os mesmos parâmetros, então um contrato novo só aparece se cair no período da coleta original do PNCP (não há janela móvel). A recoleta consulta de novo tudo o que a coleta de origem consultou, e não só a entidade monitorada. Sem aviso por e-mail ou por outro canal: os avisos ficam na API e na interface. Sem monitoramento do DODF (RF06 cita publicações), que não é fonte coletada.
- **RNF12:** a detecção de mudança de formato cobre as respostas de API (tipo do corpo) e as colunas dos CSV do TSE; não há comparação automática com a OpenAPI publicada.
- **Conta de serviço do agendador:** o usuário `agendador` existe só na auditoria e nas coletas; não tem token e não acessa a API.

## Pendências do horizonte 3

- **H23:** sem recifragem dos arquivos na troca de chave e sem cifrar as capturas antigas em claro; sem cofre de chaves; sem códigos de recuperação do MFA (a recuperação é o administrador redefinir); sem WebAuthn; sem limite de tentativas por endereço de origem (só por conta); o banco não é cifrado pela aplicação; `GET /auditoria` não é filtrada por caso; a RFC 9106 precisa ser conferida.
- **H37:** sem edição ou exclusão de anotações (por desenho), sem notificação ao responsável pela tarefa; anotações e tarefas não entram no relatório do caso.
- **H38:** importação no Maltego e no i2 **não testada**; formatos do i2 e extensões do Maltego não confirmados na documentação; especificação do GraphML não conferida (site fora do ar); sem formato nativo de nenhum dos dois produtos; H24 (CASE/UCO) continua a fazer.
- **H29:** medido só com grafo sintético e num contêiner local; as tipologias (T1 a T10) e a resolução de entidades ainda carregam o caso inteiro em memória; a exportação também.
- **H22:** testado com um servidor Ollama real (`qwen2.5:7b`, 16/11/2026), mas não com editais reais (em 17/11/2026 um edital real em PDF passou só pela extração de texto, sem a IA); sem medição de qualidade (precisão e revocação); PDF só com camada de texto (sem OCR); decisão única por sugestão; sem serviço `ollama` no Compose (o operador o acrescenta e declara o nome em `IA_HOSTS_LOCAIS`).
