# Fontes reais e piloto com dados públicos

[Voltar ao README](../README.md)

Em 17/11/2026 os conectores foram conferidos contra as **fontes reais**, para preparar um piloto em que um delegado simula uma investigação com dados públicos (não é investigação sigilosa). Só houve coleta passiva (`GET`, `HEAD` e a listagem do compartilhamento público da Receita), com no máximo uma requisição por segundo em cada fonte e sem baixar nenhuma base completa. Os testes automatizados continuam **só com dados sintéticos**; os arquivos reais ficam em `dados-reais/`, que está no `.gitignore` e no `.dockerignore`.

## Como repetir

```bash
uv run python scripts/testar_fontes_reais.py                    # todas as fontes; contagens e formatos, sem nomes
uv run python scripts/testar_fontes_reais.py --fontes pncp,portal
uv run python scripts/testar_fontes_reais.py --fontes edital --baixar-edital 00394460005887/2026/72 --trecho 300
```

O script roda os conectores de verdade, com as capturas só em memória (nada vai para o banco nem para a custódia), e termina com código 1 se alguma fonte falhar. O Portal da Transparência é **pulado, com aviso**, se `PORTAL_TRANSPARENCIA_CHAVE` não estiver no ambiente nem no `.env`; a chave só vai no cabeçalho `chave-api-dados` e o script nunca a imprime nem a grava.

## O que foi conferido em cada fonte

| Fonte | Resultado | Divergência corrigida |
|---|---|---|
| **PNCP, contratos** ([OpenAPI](https://pncp.gov.br/api/consulta/v3/api-docs)) | Layout, paginação e limites conferem: `tamanhoPagina` de 10 a 500, `pagina` obrigatória, envelope com `paginasRestantes`, `204` sem dados, `422` para período acima de 365 dias (igual ao `PNCP_MAX_DIAS_PERIODO = 366`, que conta os dois extremos) | A operação filtra pela data de **publicação**, não de assinatura (documentado). Fornecedor pessoa física vem com o **CPF completo**: o contrato guardava esse valor em `ni_fornecedor`; agora guarda só o descaracterizado (o bruto fica na custódia) |
| **PNCP, termos** ([OpenAPI](https://pncp.gov.br/api/pncp/v3/api-docs)) | Responde sem autenticação; `204` sem termos; array JSON com os campos esperados (contrato com três aditivos) | Só a documentação (a pendência "não testado" saiu) |
| **Portal da Transparência** ([OpenAPI](https://api.portaldatransparencia.gov.br/v3/api-docs), [regras de uso](https://portaldatransparencia.gov.br/api-de-dados)) | CEIS, CNEP e servidores conferem com a chave do operador. Limite confirmado na fonte: 400 requisições por minuto das 6:00 às 23:59 e 700 das 00:00 às 5:59. Página de até 15 itens, sem envelope; página vazia encerra | Só a documentação: datas `DD/MM/AAAA`, `dataFimSancao` pode vir "Sem informação", CPF do servidor já descaracterizado, um órgão pode ter mais de um código SIAPE |
| **CNPJ (Receita)** | O layout confere (7, 30 e 11 campos, Latin-1, `;`) em 11,3 milhões de linhas lidas em fluxo | O sócio **pessoa jurídica** vem com o CNPJ básico (8 dígitos), não com 14: a resolução o tratava como pessoa; agora vira empresa nos dois formatos |
| **TSE** | Candidatos e receitas do DF de 2026: 672 candidatos e 10.121 doações lidos, todas as doações ligadas a um candidato por `SQ_CANDIDATO` | O arquivo de receitas chama o ano de `AA_ELEICAO` (o conector lia só `ANO_ELEICAO` e caía no nome do arquivo); 2026 veio em Latin-1 |
| **Edital em PDF (PNCP)** | Um edital real de 618 kB e 22 páginas: 63.398 caracteres em 0,4 s | Leitura de PDF implementada (ver "Editais em PDF", em [conectores.md](conectores.md)) |

## Tamanho das fontes (medido em 17/11/2026, sem baixar tudo)

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

## CNPJ: o conector não carrega a base completa

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

## Dados reais no Docker

`docker-compose.dados-reais.yml` monta `./dados-reais` só para leitura no contêiner da API e aponta para ela `CNPJ_DIR_BASE`, `TSE_DIR_BASE` e `EDITAIS_DIR_BASE`. Ele não foi aplicado: os contêineres em execução continuam com as fixtures sintéticas e **precisam ser reconstruídos** para ganhar a leitura de PDF (dependência nova, `pypdf`).

## Três riscos do piloto corrigidos em 17/11/2026

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
- **`NR_CPF_CANDIDATO` (TSE):** desde a versão 1.2.0 do conector (decisão do autor, 23/11/2026), a coluna é lida só no arquivo de candidatos e reduzida na leitura aos seis dígitos centrais; o CPF inteiro existe só na carga bruta, e a rota de texto o devolve descaracterizado (ver "TSE, ligação de pessoas", em [conectores.md](conectores.md)).
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

## Pendências e riscos para o piloto

- **Contêineres em execução:** continuam com a versão anterior; as três correções acima só valem depois de reconstruir a imagem da API (e a do front-end).
- **Download da captura original para perícia:** não existe rota; depende do operador (ver o item 1 acima).
- **Dados gravados antes de 17/11/2026:** não são reescritos; para o piloto, comece com o banco e a custódia vazios.
- **CNPJ alfanumérico nas fontes:** aceito pelo código, mas ainda não visto em nenhuma resposta real.
- **Município do CNPJ:** o campo é o código da tabela `Municipios`; o nome não é resolvido.
- **PNCP:** a consulta de um ano inteiro sem órgão levou 22 s na primeira página (o tempo limite padrão é 30 s); prefira períodos curtos ou `cnpjOrgao`.
- **Download de editais** pelo conector e **OCR** continuam fora.
