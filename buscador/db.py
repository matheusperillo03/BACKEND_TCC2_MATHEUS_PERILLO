"""Engine, sessões, criação e evolução do esquema (SQLite local, PostgreSQL no Compose).

No PostgreSQL do Compose há dois papéis: o dono (cria tabelas e gatilhos, usado só por
``python -m buscador.migrar``) e o da aplicação, que não é dono de nada e recebe apenas os
privilégios de ``conceder_privilegios``: na auditoria, nas capturas e no histórico de decisões
dos alertas, só ``SELECT`` e ``INSERT``.

Evolução do esquema: ``criar_esquema`` é idempotente. Além de criar as tabelas que faltam, ele
acrescenta as colunas novas do modelo às tabelas que já existem (``ALTER TABLE ... ADD COLUMN``)
e os índices dessas colunas. Nunca remove nem altera coluna, então nenhum dado é perdido.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import Engine, create_engine, event, inspect, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.schema import CreateColumn

from buscador.modelos import Base

# Gatilhos que tornam a tabela de auditoria somente de acréscimo também no banco (RNF02).
_GATILHOS_SQLITE = [
    """CREATE TRIGGER IF NOT EXISTS auditoria_sem_update BEFORE UPDATE ON auditoria
       BEGIN SELECT RAISE(ABORT, 'auditoria e somente de acrescimo'); END;""",
    """CREATE TRIGGER IF NOT EXISTS auditoria_sem_delete BEFORE DELETE ON auditoria
       BEGIN SELECT RAISE(ABORT, 'auditoria e somente de acrescimo'); END;""",
]

_GATILHOS_POSTGRES = [
    """CREATE OR REPLACE FUNCTION auditoria_bloquear() RETURNS trigger AS $$
       BEGIN RAISE EXCEPTION 'auditoria e somente de acrescimo'; END;
       $$ LANGUAGE plpgsql;""",
    """CREATE OR REPLACE TRIGGER auditoria_sem_update BEFORE UPDATE ON auditoria
       FOR EACH ROW EXECUTE FUNCTION auditoria_bloquear();""",
    """CREATE OR REPLACE TRIGGER auditoria_sem_delete BEFORE DELETE ON auditoria
       FOR EACH ROW EXECUTE FUNCTION auditoria_bloquear();""",
    # TRUNCATE não dispara gatilhos de linha: precisa de um gatilho de comando próprio.
    """CREATE OR REPLACE TRIGGER auditoria_sem_truncate BEFORE TRUNCATE ON auditoria
       FOR EACH STATEMENT EXECUTE FUNCTION auditoria_bloquear();""",
]

# O histórico de decisões dos alertas (RF12) também é só de acréscimo no banco. A função é
# separada da da auditoria, que fica exatamente como estava.
_GATILHOS_DECISOES_SQLITE = [
    """CREATE TRIGGER IF NOT EXISTS alerta_decisoes_sem_update BEFORE UPDATE ON alerta_decisoes
       BEGIN SELECT RAISE(ABORT, 'alerta_decisoes e somente de acrescimo'); END;""",
    """CREATE TRIGGER IF NOT EXISTS alerta_decisoes_sem_delete BEFORE DELETE ON alerta_decisoes
       BEGIN SELECT RAISE(ABORT, 'alerta_decisoes e somente de acrescimo'); END;""",
]

_GATILHOS_DECISOES_POSTGRES = [
    """CREATE OR REPLACE FUNCTION alerta_decisoes_bloquear() RETURNS trigger AS $$
       BEGIN RAISE EXCEPTION 'alerta_decisoes e somente de acrescimo'; END;
       $$ LANGUAGE plpgsql;""",
    """CREATE OR REPLACE TRIGGER alerta_decisoes_sem_update BEFORE UPDATE ON alerta_decisoes
       FOR EACH ROW EXECUTE FUNCTION alerta_decisoes_bloquear();""",
    """CREATE OR REPLACE TRIGGER alerta_decisoes_sem_delete BEFORE DELETE ON alerta_decisoes
       FOR EACH ROW EXECUTE FUNCTION alerta_decisoes_bloquear();""",
    """CREATE OR REPLACE TRIGGER alerta_decisoes_sem_truncate BEFORE TRUNCATE ON alerta_decisoes
       FOR EACH STATEMENT EXECUTE FUNCTION alerta_decisoes_bloquear();""",
]


def _gatilhos_so_acrescimo(tabela: str, sqlite: bool) -> list[str]:
    """Gatilhos que tornam ``tabela`` só de acréscimo (mesmo desenho dos de ``alerta_decisoes``).

    O nome da tabela vem só das constantes deste módulo, nunca de entrada externa."""
    mensagem = f"{tabela} e somente de acrescimo"
    if sqlite:
        return [
            f"""CREATE TRIGGER IF NOT EXISTS {tabela}_sem_{operacao.lower()}
                BEFORE {operacao} ON {tabela}
                BEGIN SELECT RAISE(ABORT, '{mensagem}'); END;"""
            for operacao in ("UPDATE", "DELETE")
        ]
    return [
        f"""CREATE OR REPLACE FUNCTION {tabela}_bloquear() RETURNS trigger AS $$
            BEGIN RAISE EXCEPTION '{mensagem}'; END;
            $$ LANGUAGE plpgsql;""",
        f"""CREATE OR REPLACE TRIGGER {tabela}_sem_update BEFORE UPDATE ON {tabela}
            FOR EACH ROW EXECUTE FUNCTION {tabela}_bloquear();""",
        f"""CREATE OR REPLACE TRIGGER {tabela}_sem_delete BEFORE DELETE ON {tabela}
            FOR EACH ROW EXECUTE FUNCTION {tabela}_bloquear();""",
        f"""CREATE OR REPLACE TRIGGER {tabela}_sem_truncate BEFORE TRUNCATE ON {tabela}
            FOR EACH STATEMENT EXECUTE FUNCTION {tabela}_bloquear();""",
    ]


# Horizonte 3 (H37): anotações e histórico das tarefas também são só de acréscimo no banco.
TABELAS_SO_ACRESCIMO_H3 = ("anotacoes", "tarefa_eventos")

# Tabelas em que o papel da aplicação só acrescenta e lê (auditoria, custódia, decisões,
# anotações e histórico de tarefas).
TABELAS_SO_ACRESCIMO = ("auditoria", "capturas", "alerta_decisoes", *TABELAS_SO_ACRESCIMO_H3)
_IDENTIFICADOR = re.compile(r"[a-z_][a-z0-9_]{0,62}")


class ErroEsquema(RuntimeError):
    """O banco não tem o esquema esperado por esta versão do serviço."""


def criar_engine(database_url: str) -> Engine:
    if database_url.startswith("sqlite"):
        caminho = database_url.removeprefix("sqlite:///")
        if caminho and caminho != ":memory:":
            Path(caminho).parent.mkdir(parents=True, exist_ok=True)
        engine = create_engine(database_url, connect_args={"check_same_thread": False})

        @event.listens_for(engine, "connect")
        def _ativar_fk(conexao, _registro):  # pragma: no cover - configuração
            conexao.execute("PRAGMA foreign_keys=ON")

        return engine
    return create_engine(database_url, pool_pre_ping=True)


def verificar_esquema(engine: Engine) -> None:
    """Confere se as tabelas existem e se a auditoria está no formato da cadeia versão 2."""
    inspetor = inspect(engine)
    existentes = set(inspetor.get_table_names())
    faltando = sorted(set(Base.metadata.tables) - existentes)
    if faltando:
        raise ErroEsquema(
            f"Tabelas ausentes no banco: {', '.join(faltando)}. Crie o esquema com o papel dono "
            "(`python -m buscador.migrar`) ou deixe DB_CRIAR_ESQUEMA=1."
        )
    _recusar_cadeia_v1(engine)
    pendentes = [
        f"{tabela.name}.{coluna.name}" for tabela, coluna in _colunas_ausentes(engine, existentes)
    ]
    if pendentes:
        raise ErroEsquema(
            f"Colunas ausentes no banco: {', '.join(sorted(pendentes))}. Atualize o esquema com o "
            "papel dono (`python -m buscador.migrar`) ou deixe DB_CRIAR_ESQUEMA=1."
        )


def _colunas_ausentes(engine: Engine, existentes: set[str]) -> list[tuple]:
    """Pares (tabela, coluna) do modelo que ainda não existem em tabelas já criadas."""
    inspetor = inspect(engine)
    ausentes = []
    for tabela in Base.metadata.sorted_tables:
        if tabela.name not in existentes:
            continue
        no_banco = {c["name"] for c in inspetor.get_columns(tabela.name)}
        ausentes.extend(
            (tabela, coluna) for coluna in tabela.columns if coluna.name not in no_banco
        )
    return ausentes


def _ddl_nova_coluna(engine: Engine, tabela, coluna) -> str:
    """``ALTER TABLE ... ADD COLUMN`` aceito pelo SQLite e pelo PostgreSQL.

    Uma coluna nova em tabela com linhas precisa aceitar nulo ou ter valor padrão no banco. A
    chave estrangeira vai na própria coluna (``REFERENCES``), única forma que o SQLite aceita.
    """
    if not coluna.nullable and coluna.server_default is None:
        raise ErroEsquema(
            f"A coluna nova {tabela.name}.{coluna.name} é obrigatória e não tem valor padrão no "
            "banco: não há como acrescentá-la a uma tabela que já tem linhas."
        )
    ddl = str(CreateColumn(coluna).compile(dialect=engine.dialect))
    for chave in coluna.foreign_keys:
        ddl += f" REFERENCES {chave.column.table.name} ({chave.column.name})"
    return f"ALTER TABLE {tabela.name} ADD COLUMN {ddl}"


def atualizar_esquema(engine: Engine) -> list[str]:
    """Acrescenta as colunas (e os índices) que o modelo ganhou; devolve o que foi criado.

    As consultas ao catálogo terminam antes de cada transação de DDL: uma conexão por vez.
    """
    existentes = set(inspect(engine).get_table_names())
    colunas = _colunas_ausentes(engine, existentes)
    comandos = [(_ddl_nova_coluna(engine, t, c), f"{t.name}.{c.name}") for t, c in colunas]
    feitos = []
    with engine.begin() as conexao:
        for sql, nome in comandos:
            conexao.execute(text(sql))
            feitos.append(nome)
    # Índices de colunas novas: ``create_all`` não os cria em tabela que já existia.
    inspetor = inspect(engine)
    indices = [
        indice
        for tabela in Base.metadata.sorted_tables
        if tabela.name in existentes
        for indice in tabela.indexes
        if indice.name not in {i["name"] for i in inspetor.get_indexes(tabela.name)}
    ]
    with engine.begin() as conexao:
        for indice in indices:
            indice.create(conexao)
            feitos.append(f"índice {indice.name}")
    return feitos


def _recusar_cadeia_v1(engine: Engine) -> None:
    inspetor = inspect(engine)
    if "auditoria" not in inspetor.get_table_names():
        return
    if "versao" not in {c["name"] for c in inspetor.get_columns("auditoria")}:
        raise ErroEsquema(
            "A tabela de auditoria deste banco é da cadeia versão 1 (SHA-256 sem chave), que não "
            "é mais aceita. Recrie o banco (no Compose: `docker compose down -v`); ver a seção "
            "'Auditoria' do README."
        )


def criar_esquema(engine: Engine) -> list[str]:
    """Cria o que falta (tabelas, colunas, índices e gatilhos); devolve as colunas acrescentadas."""
    # Primeiro as tabelas ausentes (uma coluna nova pode referenciar uma tabela nova); depois
    # as colunas que faltam nas tabelas que já existiam.
    # A cadeia de auditoria versão 1 é recusada antes: ela não é migrável (ver README).
    _recusar_cadeia_v1(engine)
    Base.metadata.create_all(engine)
    feitos = atualizar_esquema(engine)
    verificar_esquema(engine)
    sqlite = engine.dialect.name == "sqlite"
    gatilhos = (_GATILHOS_SQLITE if sqlite else _GATILHOS_POSTGRES) + (
        _GATILHOS_DECISOES_SQLITE if sqlite else _GATILHOS_DECISOES_POSTGRES
    )
    for tabela in TABELAS_SO_ACRESCIMO_H3:
        gatilhos = gatilhos + _gatilhos_so_acrescimo(tabela, sqlite)
    with engine.begin() as conexao:
        for sql in gatilhos:
            conexao.execute(text(sql))
        # Acesso por caso (H23): um caso criado antes da tabela de membros fica com quem o
        # criou como responsável. Idempotente: só alcança casos sem nenhum membro.
        conexao.execute(
            text(
                "INSERT INTO caso_membros (caso_id, usuario, papel, adicionado_por, adicionado_em) "
                "SELECT c.id, c.criado_por, 'responsavel', 'migracao', c.criado_em FROM casos c "
                "WHERE NOT EXISTS (SELECT 1 FROM caso_membros m WHERE m.caso_id = c.id)"
            )
        )
    return feitos


def conceder_privilegios(engine: Engine, papel: str) -> None:
    """Dá ao papel da aplicação o mínimo necessário (PostgreSQL). Roda como o papel dono.

    O papel não é dono de tabelas, funções nem gatilhos, então não consegue derrubá-los; na
    auditoria, nas capturas e nas decisões de alerta não recebe ``UPDATE``, ``DELETE`` nem
    ``TRUNCATE``.
    """
    if engine.dialect.name != "postgresql":
        return
    if not _IDENTIFICADOR.fullmatch(papel):
        raise ValueError("Nome de papel inválido (use letras minúsculas, dígitos e '_').")
    demais = sorted(set(Base.metadata.tables) - set(TABELAS_SO_ACRESCIMO))
    comandos = [
        f'REVOKE ALL ON ALL TABLES IN SCHEMA public FROM "{papel}"',
        f'REVOKE CREATE ON SCHEMA public FROM "{papel}"',
        f'GRANT USAGE ON SCHEMA public TO "{papel}"',
        f'GRANT SELECT, INSERT ON {", ".join(TABELAS_SO_ACRESCIMO)} TO "{papel}"',
        f'GRANT SELECT, INSERT, UPDATE, DELETE ON {", ".join(demais)} TO "{papel}"',
        f'GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO "{papel}"',
    ]
    with engine.begin() as conexao:
        for sql in comandos:
            conexao.execute(text(sql))


def criar_fabrica_sessoes(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)


def sessao_iter(fabrica: sessionmaker[Session]) -> Iterator[Session]:
    sessao = fabrica()
    try:
        yield sessao
    finally:
        sessao.close()
