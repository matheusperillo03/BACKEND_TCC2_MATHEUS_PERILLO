# Desempenho das consultas de vínculo no grafo (RNF06, H29)

Medição feita em 16/11/2026 com `scripts/medir_grafo.py`. Os dados brutos estão em [`desempenho-grafo-dados.json`](desempenho-grafo-dados.json).

## O que foi medido

As duas consultas de vínculo da API (`GET /casos/{id}/grafo`, vizinhança em N níveis; `GET /casos/{id}/caminho`, caminho mais curto entre duas entidades), em três implementações:

| Estratégia | Como funciona | Onde está |
|---|---|---|
| `networkx` | A implementação original: a cada consulta, carrega **todas** as entidades e arestas do caso do banco, monta um `MultiDiGraph` em memória e só então calcula a vizinhança ou o caminho | `buscador/grafo/servico.py` |
| `sql_niveis` | Busca em largura **no banco**, nível a nível: cada nível é uma consulta que expande só a fronteira (os nós novos), pelos índices de `arestas.origem_id` e `arestas.destino_id` (`= ANY(vetor)`). O caminho usa busca bidirecional (expande sempre a menor das duas fronteiras). No fim, busca só os nós e as arestas do subgrafo da resposta | `buscador/grafo/sql.py` (usada pela API) |
| `sql_recursiva` | A mesma vizinhança numa única consulta `WITH RECURSIVE` | `buscador/grafo/sql.py` (`vizinhanca_recursiva`; só para a comparação) |

As três devolvem o mesmo resultado: os testes (`tests/test_horizonte3.py`, `test_h29_*` e o teste de PostgreSQL) comparam, nó a nó e profundidade a profundidade, a saída das consultas no banco com a do NetworkX.

## Método

- **Grafo sintético**, sem relação com pessoa ou empresa real: nós `N000…`, metade do número de arestas; arestas sorteadas com semente fixa (`20261116`), 80% entre dois nós quaisquer e 20% chegando a 1% dos nós ("concentradores", como um endereço ou um contador compartilhado por muitas empresas). Tamanhos: 10 mil, 100 mil e 1 milhão de arestas, num único caso.
- **Consultas:** 20 centros e 20 pares sorteados com semente fixa (um quinto dos centros são concentradores) para as estratégias no banco; para o NetworkX, só os 3 primeiros centros e pares, porque cada consulta recarrega o grafo inteiro. Por isso a coluna "nós na resposta" do NetworkX vem de uma amostra menor e não coincide com a das outras.
- **Tempo:** do pedido à resposta pronta no formato da API (dicionário com nós e arestas), medido no processo Python, sem a camada HTTP. Mediana, percentil 95 e máximo. A primeira consulta de cada série inclui a abertura da conexão (é o que aparece no máximo da vizinhança de 1 nível).
- **Memória:** pico de memória residente (`ru_maxrss`) do processo que roda a estratégia; cada estratégia roda num processo próprio. Não inclui a memória do PostgreSQL.
- **Tamanho:** `pg_relation_size`, `pg_indexes_size` e `pg_total_relation_size`, depois de `ANALYZE`.
- **Ambiente:** PostgreSQL 16.14 (imagem `postgres:16-alpine`, configuração padrão) num contêiner Docker descartável (29.8.1; 10 CPUs e 7,7 GiB para a máquina virtual do Docker), numa máquina Apple M4 com 16 GiB, macOS (Darwin 25.6.0, arm64); Python 3.12.13 fora do contêiner, ligado ao banco por TCP em `127.0.0.1`. Índices de `arestas`: os que o esquema já tinha (`caso_id`, `origem_id`, `destino_id`, `(caso_id, tipo)` e a restrição de unicidade); nenhum índice novo foi criado.
- **Ressalvas:** uma execução só por tamanho; a máquina estava em uso durante a medição (outros processos de desenvolvimento), então os números têm ruído e servem para comparar ordens de grandeza, não milissegundos; contêiner local não é o servidor de produção; um único caso no banco.

## Resultados

| Arestas | Nós | Carga (s) | Tabela arestas (MiB) | Índices de arestas (MiB) | Arestas total (MiB) | Entidades total (MiB) |
|---:|---:|---:|---:|---:|---:|---:|
| 10.000 | 5.000 | 0.43 | 2.1 | 3.5 | 5.7 | 2.1 |
| 100.000 | 50.000 | 4.59 | 20.6 | 33.8 | 54.5 | 17.8 |
| 1.000.000 | 500.000 | 57.33 | 205.6 | 333.1 | 538.8 | 179.3 |

| Arestas | Consulta | Estratégia | Consultas | Mediana (ms) | p95 (ms) | Máximo (ms) | Nós na resposta (mediana / máximo) | Pico de memória do processo (MiB) |
|---:|---|---|---:|---:|---:|---:|---:|---:|
| 10.000 | vizinhança até 1 nível(is) | sql_niveis | 20 | 1.5 | 47.4 | 47.4 | 4 / 52 | 80.1 |
| 10.000 | vizinhança até 2 nível(is) | sql_niveis | 20 | 2.4 | 7.0 | 7.0 | 49 / 238 | 80.1 |
| 10.000 | vizinhança até 3 nível(is) | sql_niveis | 20 | 6.5 | 33.3 | 33.3 | 248 / 1373 | 80.1 |
| 10.000 | caminho entre duas entidades | sql_niveis | 20 | 3.1 | 4.8 | 4.8 | 6 / 8 | 80.1 |
| 10.000 | vizinhança até 1 nível(is) | sql_recursiva | 20 | 3.8 | 45.6 | 45.6 | 4 / 52 | 80.1 |
| 10.000 | vizinhança até 2 nível(is) | sql_recursiva | 20 | 5.0 | 23.5 | 23.5 | 49 / 238 | 80.1 |
| 10.000 | vizinhança até 3 nível(is) | sql_recursiva | 20 | 9.9 | 36.6 | 36.6 | 248 / 1373 | 80.1 |
| 10.000 | vizinhança até 1 nível(is) | networkx | 3 | 152.4 | 154.4 | 154.4 | 4 / 43 | 105.8 |
| 10.000 | vizinhança até 2 nível(is) | networkx | 3 | 145.4 | 148.1 | 148.1 | 65 / 213 | 105.8 |
| 10.000 | vizinhança até 3 nível(is) | networkx | 3 | 140.9 | 162.1 | 162.1 | 260 / 1190 | 105.8 |
| 10.000 | caminho entre duas entidades | networkx | 3 | 104.9 | 111.1 | 111.1 | 6 / 7 | 105.8 |
| 100.000 | vizinhança até 1 nível(is) | sql_niveis | 20 | 1.6 | 26.6 | 26.6 | 4 / 58 | 82.0 |
| 100.000 | vizinhança até 2 nível(is) | sql_niveis | 20 | 2.4 | 6.3 | 6.3 | 46 / 304 | 82.0 |
| 100.000 | vizinhança até 3 nível(is) | sql_niveis | 20 | 5.6 | 32.6 | 32.6 | 231 / 2296 | 82.0 |
| 100.000 | caminho entre duas entidades | sql_niveis | 20 | 4.6 | 7.3 | 7.3 | 8 / 10 | 82.0 |
| 100.000 | vizinhança até 1 nível(is) | sql_recursiva | 20 | 27.1 | 174.3 | 174.3 | 4 / 58 | 82.0 |
| 100.000 | vizinhança até 2 nível(is) | sql_recursiva | 20 | 45.0 | 52.0 | 52.0 | 46 / 304 | 82.0 |
| 100.000 | vizinhança até 3 nível(is) | sql_recursiva | 20 | 70.0 | 123.0 | 123.0 | 231 / 2296 | 82.0 |
| 100.000 | vizinhança até 1 nível(is) | networkx | 3 | 1711.1 | 1726.9 | 1726.9 | 3 / 40 | 368.6 |
| 100.000 | vizinhança até 2 nível(is) | networkx | 3 | 1673.3 | 1690.1 | 1690.1 | 7 / 203 | 368.6 |
| 100.000 | vizinhança até 3 nível(is) | networkx | 3 | 1671.0 | 1770.9 | 1770.9 | 23 / 1482 | 368.6 |
| 100.000 | caminho entre duas entidades | networkx | 3 | 1197.6 | 1242.7 | 1242.7 | 7 / 8 | 368.6 |
| 1.000.000 | vizinhança até 1 nível(is) | sql_niveis | 20 | 1.8 | 105.6 | 105.6 | 5 / 55 | 82.5 |
| 1.000.000 | vizinhança até 2 nível(is) | sql_niveis | 20 | 2.8 | 12.5 | 12.5 | 27 / 282 | 82.5 |
| 1.000.000 | vizinhança até 3 nível(is) | sql_niveis | 20 | 8.3 | 90.0 | 90.0 | 223 / 1981 | 82.5 |
| 1.000.000 | caminho entre duas entidades | sql_niveis | 20 | 11.3 | 23.7 | 23.7 | 8 / 11 | 82.5 |
| 1.000.000 | vizinhança até 1 nível(is) | sql_recursiva | 20 | 251.1 | 417.7 | 417.7 | 5 / 55 | 76.1 |
| 1.000.000 | vizinhança até 2 nível(is) | sql_recursiva | 20 | 450.7 | 511.5 | 511.5 | 27 / 282 | 76.1 |
| 1.000.000 | vizinhança até 3 nível(is) | sql_recursiva | 20 | 666.8 | 1036.6 | 1036.6 | 223 / 1981 | 76.1 |
| 1.000.000 | vizinhança até 1 nível(is) | networkx | 3 | 19954.9 | 20188.3 | 20188.3 | 7 / 42 | 2556.8 |
| 1.000.000 | vizinhança até 2 nível(is) | networkx | 3 | 18779.7 | 19183.5 | 19183.5 | 73 / 191 | 2556.8 |
| 1.000.000 | vizinhança até 3 nível(is) | networkx | 3 | 18849.1 | 19503.7 | 19503.7 | 368 / 1434 | 2556.8 |
| 1.000.000 | caminho entre duas entidades | networkx | 3 | 13199.5 | 13988.3 | 13988.3 | 9 / 10 | 2556.8 |

## Leitura dos números

- **NetworkX (antes):** o tempo cresce com o tamanho do caso, e não com o da resposta: cerca de 0,15 s com 10 mil arestas, 1,7 s com 100 mil e **19 a 20 s com 1 milhão**, com pico de **2,5 GiB** de memória no processo, para devolver algumas dezenas ou centenas de nós. Com 1 milhão de arestas a consulta não cabe no contêiner da API (limite de 1 GiB no Compose).
- **Busca nível a nível no banco (agora):** a mediana fica entre **1,5 e 11,3 ms** nos três tamanhos (vizinhança de até 3 níveis e caminho) e praticamente não muda de 10 mil para 1 milhão de arestas; o pior caso medido foi de 105,6 ms (a primeira consulta da série, com a abertura da conexão) e, fora ela, 90 ms (vizinhança de 3 níveis de um concentrador, com 1.981 nós na resposta). A memória do processo fica em cerca de 80 MiB, igual nos três tamanhos. Com 1 milhão de arestas, a consulta ficou **entre mil e 2 mil vezes mais rápida** na mediana (vizinhança de 3 níveis: 18.849 ms contra 8,3 ms; caminho: 13.199 ms contra 11,3 ms), com cerca de 30 vezes menos memória.
- **Consulta recursiva única:** dá o resultado certo, mas, do jeito que foi escrita, **o tempo cresce com o tamanho do grafo** (vizinhança de 1 nível: 3,8 ms, 27 ms e 251 ms). A hipótese, **não verificada com `EXPLAIN`**, é que o planejador não leva a condição da junção para dentro da subconsulta `UNION ALL` das duas direções e por isso varre a tabela de arestas a cada nível. Além disso, ela não tem como parar quando a resposta passa do teto de nós. Por esses dois motivos a API usa a busca nível a nível; a recursiva fica no código só para a comparação.
- **Tamanho:** cerca de 565 bytes por aresta e 375 bytes por entidade em disco, com os índices (538,8 MiB e 179,3 MiB para 1 milhão de arestas e 500 mil nós). Os índices de `arestas` ocupam mais que a tabela (333,1 MiB contra 205,6 MiB), porque os identificadores são textos de 32 caracteres.
- **Meta do requisito:** o RNF06 fala em "grafos com milhões de nós e consultas de vínculo em segundos". O que foi medido é **1 milhão de arestas e 500 mil nós**, com consultas em **milissegundos**. Milhões de nós não foram medidos.

## Tetos

A busca no banco tem dois tetos configuráveis, para que uma consulta não derrube o serviço: `GRAFO_MAX_NOS` (20.000 nós na resposta da vizinhança; acima disso a API responde `422` pedindo profundidade menor ou um centro) e `GRAFO_MAX_NOS_VISITADOS` (200.000 nós visitados na busca do caminho).

## O que ainda carrega o caso inteiro

A medição cobre só a vizinhança e o caminho. As tipologias (T1 a T10), a resolução de entidades (que mantém em memória as entidades e arestas do caso durante a coleta), o relatório e a exportação ainda leem o caso inteiro. Para trabalhar com a base completa do CNPJ num único caso, eles também teriam de mudar.

## Alternativa futura: banco de grafos dedicado

Um banco de grafos (por exemplo, Neo4j ou a extensão Apache AGE do PostgreSQL) fica **registrado como alternativa futura, e não foi adotado**, pelos motivos abaixo. Nenhum deles foi medido aqui: não há número deste projeto comparando o PostgreSQL com um banco de grafos.

- **Não foi necessário para as consultas que existem.** Vizinhança de poucos níveis e caminho mais curto ficaram em milissegundos com 1 milhão de arestas no banco que o projeto já usa.
- **Custo de um serviço a mais.** Um segundo banco traria outra credencial, outro backup, outra superfície de ataque e a sincronização entre os dois; e a cadeia de custódia e a trilha de auditoria teriam de continuar no PostgreSQL, onde os gatilhos e os privilégios do papel da aplicação as protegem.
- **Quando reconsiderar:** se aparecerem consultas que a busca em largura por índices não resolve bem (padrões de subgrafo, como ciclos societários; caminhos ponderados; todos os caminhos entre dois nós; centralidade e detecção de comunidades sobre o grafo inteiro), ou se a medição com milhões de nós e dados reais mostrar tempos fora da meta.

## Como reproduzir

O script **apaga e recria as tabelas** do banco indicado: use só um banco descartável.

```bash
docker run -d --name medir-grafo-pg -e POSTGRES_USER=osint -e POSTGRES_PASSWORD=medir \
    -e POSTGRES_DB=osint -p 127.0.0.1:55999:5432 postgres:16-alpine
uv run python scripts/medir_grafo.py \
    --url postgresql+psycopg://osint:medir@127.0.0.1:55999/osint --confirmo-banco-descartavel \
    --tamanhos 10000,100000,1000000 --consultas 20 --consultas-networkx 3 \
    --saida docs/desempenho-grafo-dados.json
uv run python scripts/medir_grafo.py --tabelas docs/desempenho-grafo-dados.json   # tabelas em Markdown
docker rm -f medir-grafo-pg
```

A medição completa levou cerca de 6 minutos, a maior parte nas 12 consultas do NetworkX com 1 milhão de arestas.
