#!/bin/sh
# Roda uma única vez, quando o volume do PostgreSQL é criado (docker-entrypoint-initdb.d).
#
# Cria o papel da APLICAÇÃO, separado do papel dono do banco (POSTGRES_USER):
# - não é superusuário, não cria papéis nem bancos e não é dono de nenhuma tabela ou gatilho;
# - não pode criar objetos no esquema public;
# - os privilégios por tabela são dados depois pelo serviço "migracao"
#   (python -m buscador.migrar): na auditoria e nas capturas, só SELECT e INSERT.
# Assim, quem obtém a credencial da API não consegue derrubar os gatilhos que tornam a
# auditoria somente de acréscimo, nem alterar, apagar ou truncar a trilha.
set -eu

: "${DB_PAPEL_APP:?defina DB_PAPEL_APP}"
: "${DB_SENHA_APP:?defina DB_SENHA_APP}"

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
     -v papel="$DB_PAPEL_APP" -v senha="$DB_SENHA_APP" -v banco="$POSTGRES_DB" <<'SQL'
CREATE ROLE :"papel" LOGIN PASSWORD :'senha'
    NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
REVOKE ALL ON DATABASE :"banco" FROM PUBLIC;
GRANT CONNECT ON DATABASE :"banco" TO :"papel";
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO :"papel";
SQL
