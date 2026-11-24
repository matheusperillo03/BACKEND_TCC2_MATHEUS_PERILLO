# Segurança

[Voltar ao README](../README.md)

A cadeia de custódia e a trilha de auditoria estão em [arquitetura.md](arquitetura.md).

## H23: usuários, MFA e sessão

- **Primeiro usuário.** A API só cria contas para quem já é `admin`. O primeiro sai da linha de comando, no servidor: `python -m buscador.usuarios criar <usuario> admin` (no Compose: `docker compose exec api python -m buscador.usuarios criar <usuario> admin`). A senha é pedida no terminal ou lida de `SENHA_INICIAL`. `python -m buscador.usuarios redefinir <usuario> --senha --mfa` recupera uma conta. As duas operações ficam na auditoria como o usuário `cli`.
- **Senha: Argon2id** (biblioteca `argon2-cffi`). Justificativa: a RFC 9106 recomenda a variante Argon2id quando não se sabe qual ataque é mais provável (resiste a canal lateral e a GPU). Parâmetros padrão da biblioteca (perfil de memória reduzida da RFC 9106: 64 MiB, 3 iterações, 4 vias), gravados no próprio hash. **Pendente:** o texto da RFC 9106 não foi relido nesta entrega; a afirmação vem da documentação do `argon2-cffi` e precisa ser conferida em <https://www.rfc-editor.org/rfc/rfc9106> antes de ir para a monografia. Política: mínimo de 12 caracteres (`SENHA_TAMANHO_MINIMO`, decisão do autor), sem o login dentro da senha e sem regras de composição.
- **MFA por TOTP (RFC 6238).** Conferido em <https://www.rfc-editor.org/rfc/rfc6238> (16/11/2026): `TOTP = HOTP(K, T)`, com `T = (tempo Unix - T0) / X`, passo padrão de 30 s; e "The verifier MUST NOT accept the second attempt of the OTP" (seção 5.2). Implementação própria em `buscador/acesso/totp.py`, só com a biblioteca padrão (HMAC-SHA-1, 6 dígitos, 30 s), em vez de uma dependência: o algoritmo é curto e tem vetores de teste oficiais, e os seis vetores SHA-1 do Apêndice B da RFC estão nos testes. Janela de um passo para trás e um para a frente; o último passo aceito fica gravado, e o mesmo código não vale duas vezes. A URI de cadastro segue o "Key Uri Format" do Google Authenticator (<https://github.com/google/google-authenticator/wiki/Key-Uri-Format>, conferido em 16/11/2026), que não faz parte da RFC.
- **Para quem é obrigatório.** `MFA_PERFIS_OBRIGATORIOS` (padrão: os três perfis). O usuário cadastra o segundo fator no primeiro login; o administrador nunca vê o segredo. O segredo fica no banco cifrado com AES-256-GCM (chave fora do banco).
- **Sessão.** Token opaco de 256 bits, guardado no banco só como SHA-256. Decisão: sessão no servidor em vez de JWT, porque logout, desativação da conta e troca de senha precisam valer na hora, e um JWT continua válido até expirar. Expira pela duração máxima (`SESSAO_DURACAO_MINUTOS`, 480) e por inatividade (`SESSAO_INATIVIDADE_MINUTOS`, 30).
- **Força bruta.** `LOGIN_MAX_FALHAS` falhas seguidas (de senha ou de código) bloqueiam a conta por `LOGIN_BLOQUEIO_MINUTOS`. A resposta de falha é a mesma para login inexistente e senha errada, e o tempo de resposta também (um hash fictício é verificado quando o login não existe).
- **Tokens de desenvolvimento.** `TOKENS_DEV` só vale com `MODO_DEV=1` (que também liga `/docs`). Com `MODO_DEV=0`, um `TOKENS_DEV` preenchido impede o serviço de iniciar. Quem usa token de desenvolvimento não passa por senha nem MFA: é só para a máquina do desenvolvedor e para os testes. Com `MODO_DEV=1` e `URL_PUBLICA` de fora da máquina o serviço não inicia, e sem `URL_PUBLICA` a API não responde a requisições que chegam por outro nome (ver "Acesso de fora da máquina e TLS"). **Atenção ao atualizar:** um `.env` de versão anterior, com `TOKENS_DEV` preenchido e `MODO_DEV=0`, precisa esvaziar `TOKENS_DEV` (e criar os usuários) ou declarar `MODO_DEV=1`; e precisa ganhar `CUSTODIA_CHAVE`.

### Acesso de fora da máquina e TLS (RNF01, 23/11/2026)

Regra: **na própria máquina o sistema atende em http; fora dela, só por https, com `MODO_DEV=0` e com o endereço declarado.** Quatro travas, todas com teste em `tests/test_acesso_externo.py`:

1. **`URL_PUBLICA` com `http` fora da máquina:** o serviço não inicia (`ErroConfiguracao`). "Própria máquina" é `localhost` ou um IP de loopback.
2. **`MODO_DEV=1` com `URL_PUBLICA` de fora da máquina:** o serviço não inicia. Os tokens fixos dispensam senha e MFA e não podem ficar alcançáveis de fora.
3. **`Host` da requisição:** a API responde `421` quando o `Host` não é local nem o nome de `URL_PUBLICA` (`HostPermitido`, em `buscador/api/app.py`). O nginx do front-end repassa o `Host` que recebeu. Consequência: um ambiente de desenvolvimento (`MODO_DEV=1`, sem `URL_PUBLICA`) publicado por engano num proxy ou numa VPN não responde por esse endereço, nem em `/health`.
4. **Token de desenvolvimento com `MODO_DEV=0`:** `401` em qualquer rota; o mapa de tokens fica vazio.

Duas formas de ter TLS (o código não escolhe; a regra acima vale para as duas):

- **Proxy com TLS na frente do nginx (a forma do guia de implantação do piloto).** O Tailscale Serve termina o HTTPS, com certificado emitido para o nome da máquina na rede privada, e repassa para `http://127.0.0.1:8090`. Nada muda no Compose; no `.env` do ambiente, `URL_PUBLICA=https://<nome>.<rede>.ts.net`. O trecho entre o proxy e o nginx é local (loopback da mesma máquina), sem TLS.
- **TLS no próprio nginx.** `docker compose -f docker-compose.yml -f docker-compose.tls.yml up -d --build`, com `URL_PUBLICA` e `TLS_DIR_CERTIFICADO` (pasta do host com `servidor.crt` e `servidor.key`, montada só para leitura; `tls/` está no `.gitignore` e no `.dockerignore`). O nginx atende HTTPS na 443 (TLS 1.2 e 1.3), envia `Strict-Transport-Security` e redireciona para https o pedido http que não vem de localhost. As portas 443 e 80 são publicadas em `TLS_ENDERECO` (padrão `127.0.0.1`). A configuração está no repositório do front-end (`nginx-tls.conf`).

Limites, para não prometer mais do que há:

- O trecho nginx → API é http, na rede interna do Compose (mesma máquina). Não há TLS entre contêineres nem entre a API e o PostgreSQL.
- A trava 3 confia no cabeçalho `Host`, que o cliente escolhe. Ela barra a exposição por descuido; não barra quem já alcança a porta e forja `Host: localhost`. Contra isso valem as portas publicadas só em `127.0.0.1` e a política de acesso da rede privada.
- Não há cookie: o token da sessão vai no cabeçalho `Authorization` e fica no `sessionStorage` do navegador. Por isso não há atributo `Secure` a configurar; a proteção em trânsito é o próprio TLS, mais o HSTS.
- **Ao atualizar um ambiente já acessado por VPN** (o do piloto), acrescente `URL_PUBLICA=https://<nome>.<rede>.ts.net` ao arquivo de ambiente antes de subir. Sem isso a API passa a responder `421` pelo endereço da VPN.
- Não testado com o Tailscale real nesta entrega: se o Serve repassa o `Host` original ao nginx (é o que a trava 3 supõe). Se ele reescrever o `Host` para `127.0.0.1`, o acesso continua funcionando, mas a trava 3 deixa de distinguir o acesso de fora.

## H23: acesso por caso

- Tabela `caso_membros`, com o papel de cada membro. **Só membro vê e age no caso**; o perfil global (`delegado`, `admin`) não abre caso nenhum.
- Papéis: `leitor` lê (todas as rotas `GET` do caso, inclusive relatório e exportação); `investigador` também altera (coletas, tipologias, decisões, anotações, tarefas, monitoramento, extração por IA); `responsavel` também gerencia os membros. Regra única em `Contexto.caso` (`buscador/api/deps.py`): requisição que não é `GET` exige `investigador`, e as rotas de membros exigem `responsavel`.
- Quem não é membro recebe `403` (e não `404`): os identificadores são UUID aleatórios, então a resposta não ajuda a enumerar casos, e o `403` deixa claro ao usuário por que não entrou. Toda negação fica na auditoria (`caso.acesso_negado`, com a rota, o papel exigido e o papel atual); a negação por perfil também (`acesso.perfil_negado`).
- Um caso criado antes desta versão fica com o criador como `responsavel` (feito pela migração, uma vez).
- **O que continua global, por decisão:** `GET /auditoria` segue restrita aos perfis `delegado` e `admin`, sem exigir que sejam membros do caso consultado. A função de auditor (H06) é justamente olhar o uso da ferramenta de fora da equipe do caso; a própria consulta à trilha é auditada.

## H23: criptografia em repouso das capturas

- **AES-256-GCM** (biblioteca `cryptography`, que usa o OpenSSL). Justificativa: é cifra autenticada, então a adulteração do arquivo é detectada na própria decifragem; e a biblioteca é a implementação mais usada em Python, mantida pela Python Cryptographic Authority.
- **Chave fora do banco:** `CUSTODIA_CHAVE` (32 bytes em hexadecimal) ou `CUSTODIA_CHAVE_ARQUIVO`. É obrigatória (o serviço não sobe sem ela) e não pode ser igual à `AUDITORIA_CHAVE_HMAC`. A chave mestra não cifra nada diretamente: cada uso tem uma subchave derivada por HKDF-SHA256 (`capturas`, `totp`).
- **Formato do arquivo** (`<DADOS_DIR>/capturas/<aa>/<sha256>.gcm`): `OSINTGCM` (8 bytes), versão (1), identificador da chave (8), nonce aleatório (12), texto cifrado e etiqueta (16).
- **O SHA-256 da custódia é o do conteúdo original**, calculado antes de cifrar, e entra nos dados associados da cifra: o arquivo de uma captura não decifra no lugar de outra. `GET /capturas/{id}/verificar` decifra e recalcula o SHA-256; arquivo alterado, trocado ou cifrado com outra chave dá `integra: false`.
- **Capturas antigas, em claro,** continuam legíveis e verificáveis (o caminho gravado em cada captura diz em que forma ela está). Elas **não** são cifradas retroativamente.
- **Rotação de chave (limitação).** Há só a metade da rotação: depois de trocar `CUSTODIA_CHAVE`, a chave antiga vai em `CUSTODIA_CHAVES_ANTERIORES` e o que foi cifrado com ela continua legível; o que é gravado depois usa a chave nova. **Não há** o comando que recifra os arquivos antigos com a chave nova (necessário para aposentar de vez uma chave comprometida) nem o que cifra as capturas em claro de versões anteriores. Também não há cofre de chaves (HSM/KMS): a chave fica numa variável de ambiente ou num arquivo do servidor.
- **O que não é cifrado pela aplicação:** o banco (entidades, alertas, anotações, trilha). Fica para a criptografia do volume ou do disco, na infraestrutura. Só o segredo do TOTP é cifrado no banco.
- **Memória:** a carga cifrada passa inteira pela memória (o AES-GCM autentica a mensagem toda); os conectores limitam cada carga a 64 MiB no máximo.

## Dados pessoais (RNF04) e resolução sem fusão (RF02)

- **Nenhum CPF completo é guardado** em entidades, arestas, alertas, avisos, relatório ou auditoria. Quando a fonte traz o CPF completo (doador no TSE; possivelmente o servidor no Portal), a resolução guarda só os seis dígitos centrais, no mesmo formato do CNPJ (`***NNNNNN**`). A API, a interface e o relatório só têm esse formato para mostrar. A carga bruta fica na custódia como veio da fonte (é o que sustenta a prova) e a API não tem rota que devolva os bytes originais; o que a API deriva dela (comparação de capturas, texto da captura, extração por IA) sai com o CPF descaracterizado (ver "CPF completo nas cargas brutas", em [dados-reais.md](dados-reais.md)).
- Do servidor guarda-se só o necessário: nome, dígitos visíveis do CPF, código e nome do órgão SIAPE, situação e tipo. Do candidato, nenhum documento pessoal.
- **Sócio, servidor e doador com o mesmo nome e os mesmos seis dígitos não são fundidos.** Cada origem tem chave própria (`servidor:<órgão>:...`, `doador:...`), e a coincidência gera uma sugestão de resolução (score 0,9, "Possível coincidência") e, nas tipologias T7 e T9, um alerta que diz por que não é identificação: seis dígitos e o nome não identificam uma pessoa.

## Pipeline de integração (RNF11, H16)

`.github/workflows/ci.yml` roda a cada `push` e `pull_request`: instala as dependências travadas (`uv sync --locked`), `ruff check`, `ruff format --check`, Bandit e `pytest`, este com um PostgreSQL descartável do próprio job, para que os testes de gatilhos, privilégios e evolução do esquema rodem de verdade. Um último passo falha se algum teste de PostgreSQL tiver sido pulado. Não há `continue-on-error`: qualquer etapa que falhe derruba o pipeline.

**Por que Bandit, e não Semgrep.** (1) O back-end é só Python, e o Bandit é feito para Python: analisa a árvore sintática atrás de padrões inseguros conhecidos (`eval`, `subprocess` com `shell`, SQL montado por texto, desserialização insegura, segredo fixo no código). (2) Ele é uma dependência de desenvolvimento comum, travada no `uv.lock` como as outras: o resultado é o mesmo na máquina do desenvolvedor e no pipeline, e a análise roda sem rede. O Semgrep com as regras do registro público baixa as regras a cada execução, o que torna o resultado dependente de um serviço externo e da data. (3) A configuração cabe no `pyproject.toml`. O que se perde: o Semgrep cobre várias linguagens e permite escrever regras próprias do projeto, e nenhum dos dois substitui a revisão humana nem os testes de regressão de segurança de `tests/test_seguranca.py`. Análise dinâmica (DAST), SBOM e gestão de segredos, também citados no RNF11, não foram implementados.

**Resultado e supressões.** Na data desta versão o Bandit não aponta nada em `buscador/` e `scripts/`. As supressões são pontuais e justificadas no código: `buscador/documentos.py`, `B404`/`B603` (roda, sem shell e com argumentos fixos, o extrator de PDF do próprio projeto num processo separado, para poder impor o prazo); `buscador/exportacao.py`, `# nosec B405` (o `xml.etree` é usado só para **gerar** o GraphML, nunca para interpretar XML de fora); e, em `scripts/medir_grafo.py`, `B404`/`B603` (o script reexecuta a si mesmo, com argumentos fixos, para medir a memória de cada estratégia), `B311` (sorteio com semente fixa para o grafo sintético, não é segredo) e `B608` (nomes de função e de tabela vindos de constantes do próprio script). Os testes ficam fora da análise (`exclude_dirs`), porque usam `assert` e tokens sintéticos de propósito.
