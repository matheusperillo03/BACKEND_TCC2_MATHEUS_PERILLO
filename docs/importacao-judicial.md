# Importação sob ordem judicial (RF10)

[Voltar ao README](../README.md)

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
