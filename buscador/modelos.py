"""Modelo relacional (SQLAlchemy 2)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def novo_id() -> str:
    return uuid.uuid4().hex


def agora_utc() -> datetime:
    return datetime.now(UTC)


def iso_utc(momento: datetime | None = None) -> str:
    """ISO 8601 em UTC com sufixo Z (formato único usado nas respostas e nos hashes)."""
    momento = momento or agora_utc()
    if momento.tzinfo is None:
        momento = momento.replace(tzinfo=UTC)
    return momento.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


class Base(DeclarativeBase):
    pass


class Caso(Base):
    __tablename__ = "casos"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=novo_id)
    titulo: Mapped[str] = mapped_column(String(300))
    descricao: Mapped[str] = mapped_column(Text, default="")
    numero_procedimento: Mapped[str] = mapped_column(String(100), default="")
    criado_por: Mapped[str] = mapped_column(String(100))
    criado_em: Mapped[str] = mapped_column(String(40), default=iso_utc)
    # RNF04 (H17): finalidade declarada e prazo de retenção (AAAA-MM-DD, inclusive). Casos
    # criados antes destas colunas ficam com finalidade vazia e sem prazo (``None``).
    finalidade: Mapped[str] = mapped_column(Text, default="", server_default="")
    retencao_ate: Mapped[str | None] = mapped_column(String(10), nullable=True)


class Coleta(Base):
    """Uma execução de coleta de uma fonte num caso (RNF12, H15), bem-sucedida ou não.

    A linha nasce como ``em_andamento`` antes de qualquer consulta externa e é fechada como
    ``sucesso`` ou ``falha``. Uma recoleta (RF07, H14) aponta para a coleta que repete.
    """

    __tablename__ = "coletas"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=novo_id)
    caso_id: Mapped[str] = mapped_column(ForeignKey("casos.id"), index=True)
    fonte: Mapped[str] = mapped_column(String(50))
    parametros: Mapped[dict] = mapped_column(JSON, default=dict)
    conector_nome: Mapped[str] = mapped_column(String(50), default="")
    conector_versao: Mapped[str] = mapped_column(String(20), default="")
    situacao: Mapped[str] = mapped_column(String(15), default="em_andamento")
    # Maior número de tentativas que uma operação da coleta precisou (0 = nada foi tentado).
    tentativas: Mapped[int] = mapped_column(Integer, default=0)
    motivo: Mapped[str] = mapped_column(Text, default="")
    registros: Mapped[int] = mapped_column(Integer, default=0)
    avisos: Mapped[list] = mapped_column(JSON, default=list)
    usuario: Mapped[str] = mapped_column(String(100))
    iniciado_em: Mapped[str] = mapped_column(String(40), default=iso_utc)
    concluido_em: Mapped[str | None] = mapped_column(String(40), nullable=True)
    recoleta_de: Mapped[str | None] = mapped_column(ForeignKey("coletas.id"), nullable=True)


class Captura(Base):
    """Carga bruta coletada, guardada como veio, com hash e metadados (RF07)."""

    __tablename__ = "capturas"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=novo_id)
    caso_id: Mapped[str] = mapped_column(ForeignKey("casos.id"), index=True)
    fonte: Mapped[str] = mapped_column(String(50))
    url_ou_arquivo: Mapped[str] = mapped_column(Text)
    coletado_em: Mapped[str] = mapped_column(String(40), default=iso_utc)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    tamanho_bytes: Mapped[int] = mapped_column(Integer)
    caminho_relativo: Mapped[str] = mapped_column(String(200))
    metadados: Mapped[dict] = mapped_column(JSON, default=dict)
    # RF07 (H14): conector que produziu a captura e a coleta a que ela pertence. Capturas
    # anteriores a estas colunas ficam com texto vazio e sem coleta.
    conector_nome: Mapped[str] = mapped_column(String(50), default="", server_default="")
    conector_versao: Mapped[str] = mapped_column(String(20), default="", server_default="")
    coleta_id: Mapped[str | None] = mapped_column(
        ForeignKey("coletas.id"), nullable=True, index=True
    )


class Entidade(Base):
    __tablename__ = "entidades"
    __table_args__ = (UniqueConstraint("caso_id", "tipo", "chave", name="uq_entidade_chave"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=novo_id)
    caso_id: Mapped[str] = mapped_column(ForeignKey("casos.id"), index=True)
    # empresa | pessoa | orgao | contrato | sancao | candidato | edital
    tipo: Mapped[str] = mapped_column(String(20))
    chave: Mapped[str] = mapped_column(String(300))  # chave de resolução (RF02)
    rotulo: Mapped[str] = mapped_column(String(300))
    identificador: Mapped[str] = mapped_column(String(100), default="")
    atributos: Mapped[dict] = mapped_column(JSON, default=dict)
    captura_id: Mapped[str] = mapped_column(ForeignKey("capturas.id"))

    fontes: Mapped[list[EntidadeFonte]] = relationship(
        back_populates="entidade", cascade="all, delete-orphan", order_by="EntidadeFonte.id"
    )


class EntidadeFonte(Base):
    """Todas as capturas que contribuíram para uma entidade."""

    __tablename__ = "entidade_fontes"
    __table_args__ = (UniqueConstraint("entidade_id", "captura_id", name="uq_entidade_fonte"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    entidade_id: Mapped[str] = mapped_column(ForeignKey("entidades.id"), index=True)
    captura_id: Mapped[str] = mapped_column(ForeignKey("capturas.id"))
    papel: Mapped[str] = mapped_column(String(50), default="")

    entidade: Mapped[Entidade] = relationship(back_populates="fontes")


class Aresta(Base):
    __tablename__ = "arestas"
    __table_args__ = (
        UniqueConstraint("caso_id", "origem_id", "destino_id", "tipo", "chave", name="uq_aresta"),
        Index("ix_aresta_caso_tipo", "caso_id", "tipo"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=novo_id)
    caso_id: Mapped[str] = mapped_column(ForeignKey("casos.id"), index=True)
    origem_id: Mapped[str] = mapped_column(ForeignKey("entidades.id"), index=True)
    destino_id: Mapped[str] = mapped_column(ForeignKey("entidades.id"), index=True)
    tipo: Mapped[str] = mapped_column(String(30))
    chave: Mapped[str] = mapped_column(String(200), default="")
    atributos: Mapped[dict] = mapped_column(JSON, default=dict)
    captura_id: Mapped[str] = mapped_column(ForeignKey("capturas.id"))


class Sugestao(Base):
    """Sugestão de resolução (homônimos): nunca funde automaticamente."""

    __tablename__ = "sugestoes"
    __table_args__ = (UniqueConstraint("entidade_id", "candidata_id", name="uq_sugestao"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=novo_id)
    caso_id: Mapped[str] = mapped_column(ForeignKey("casos.id"), index=True)
    entidade_id: Mapped[str] = mapped_column(ForeignKey("entidades.id"), index=True)
    candidata_id: Mapped[str] = mapped_column(ForeignKey("entidades.id"), index=True)
    score: Mapped[float] = mapped_column(Float)
    motivo: Mapped[str] = mapped_column(Text)
    criado_em: Mapped[str] = mapped_column(String(40), default=iso_utc)


class Alerta(Base):
    __tablename__ = "alertas"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=novo_id)
    caso_id: Mapped[str] = mapped_column(ForeignKey("casos.id"), index=True)
    tipologia: Mapped[str] = mapped_column(String(5))
    titulo: Mapped[str] = mapped_column(String(300))
    motivo: Mapped[str] = mapped_column(Text)
    severidade: Mapped[str] = mapped_column(String(10))
    entidades: Mapped[list] = mapped_column(JSON, default=list)
    evidencias: Mapped[list] = mapped_column(JSON, default=list)
    parametros: Mapped[dict] = mapped_column(JSON, default=dict)
    criado_em: Mapped[str] = mapped_column(String(40), default=iso_utc)
    # Identidade estável do alerta entre execuções (tipologia + entidades + discriminador): a
    # reexecução atualiza o alerta em vez de recriá-lo, e a decisão do investigador é mantida.
    chave: Mapped[str] = mapped_column(String(64), default="", server_default="", index=True)
    # Falso quando a última execução da tipologia não detectou mais o alerta, mas ele tem
    # decisão registrada e por isso não é apagado.
    ativo: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    # RF12 (H12): situação e última decisão (cópia da entrada mais recente de AlertaDecisao).
    situacao: Mapped[str] = mapped_column(String(12), default="pendente", server_default="pendente")
    decidido_por: Mapped[str | None] = mapped_column(String(100), nullable=True)
    decidido_em: Mapped[str | None] = mapped_column(String(40), nullable=True)
    justificativa_decisao: Mapped[str | None] = mapped_column(Text, nullable=True)

    decisoes: Mapped[list[AlertaDecisao]] = relationship(
        back_populates="alerta", order_by="AlertaDecisao.id"
    )


class AlertaDecisao(Base):
    """Histórico das decisões sobre um alerta (RF12): só de acréscimo, uma linha por decisão."""

    __tablename__ = "alerta_decisoes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    alerta_id: Mapped[str] = mapped_column(ForeignKey("alertas.id"), index=True)
    caso_id: Mapped[str] = mapped_column(ForeignKey("casos.id"), index=True)
    decisao: Mapped[str] = mapped_column(String(12))  # confirmado | descartado
    justificativa: Mapped[str] = mapped_column(Text)
    usuario: Mapped[str] = mapped_column(String(100))
    perfil: Mapped[str] = mapped_column(String(20), default="")
    decidido_em: Mapped[str] = mapped_column(String(40), default=iso_utc)

    alerta: Mapped[Alerta] = relationship(back_populates="decisoes")


class Monitoramento(Base):
    """Entidade do caso marcada para acompanhamento contínuo (RF06, H18).

    O agendador interno refaz, na periodicidade escolhida, as coletas de origem da entidade e
    compara o resultado com o estado anterior. No máximo um monitoramento por entidade do caso.
    """

    __tablename__ = "monitoramentos"
    __table_args__ = (
        UniqueConstraint("caso_id", "entidade_id", name="uq_monitoramento_entidade"),
        Index("ix_monitoramento_pendente", "ativo", "proxima_execucao_em"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=novo_id)
    caso_id: Mapped[str] = mapped_column(ForeignKey("casos.id"), index=True)
    entidade_id: Mapped[str] = mapped_column(ForeignKey("entidades.id"), index=True)
    periodicidade_horas: Mapped[int] = mapped_column(Integer)
    ativo: Mapped[bool] = mapped_column(Boolean, default=True)
    # Por que deixou de estar ativo (desativado pelo usuário ou prazo de retenção vencido).
    motivo_inativo: Mapped[str] = mapped_column(Text, default="")
    # Justificativa dada por quem marcou a entidade; acompanha cada execução automática na
    # trilha de auditoria.
    justificativa: Mapped[str] = mapped_column(Text, default="")
    criado_por: Mapped[str] = mapped_column(String(100))
    criado_em: Mapped[str] = mapped_column(String(40), default=iso_utc)
    proxima_execucao_em: Mapped[str] = mapped_column(String(40))
    ultima_execucao_em: Mapped[str | None] = mapped_column(String(40), nullable=True)

    execucoes: Mapped[list[MonitoramentoExecucao]] = relationship(
        back_populates="monitoramento", order_by="MonitoramentoExecucao.iniciado_em"
    )


class MonitoramentoExecucao(Base):
    """Uma execução do monitoramento (automática ou manual), com as recoletas que fez."""

    __tablename__ = "monitoramento_execucoes"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=novo_id)
    monitoramento_id: Mapped[str] = mapped_column(ForeignKey("monitoramentos.id"), index=True)
    caso_id: Mapped[str] = mapped_column(ForeignKey("casos.id"), index=True)
    origem: Mapped[str] = mapped_column(String(12))  # agendador | manual
    usuario: Mapped[str] = mapped_column(String(100))
    iniciado_em: Mapped[str] = mapped_column(String(40), default=iso_utc)
    concluido_em: Mapped[str | None] = mapped_column(String(40), nullable=True)
    # em_andamento | sucesso | parcial | falha | sem_coletas | recusada_retencao
    situacao: Mapped[str] = mapped_column(String(20), default="em_andamento")
    # Uma linha por recoleta: coleta nova, coleta comparada, fonte, situação e se mudou.
    coletas: Mapped[list] = mapped_column(JSON, default=list)
    avisos_gerados: Mapped[int] = mapped_column(Integer, default=0)
    motivo: Mapped[str] = mapped_column(Text, default="")

    monitoramento: Mapped[Monitoramento] = relationship(back_populates="execucoes")


class Aviso(Base):
    """Novidade detectada pelo monitoramento sobre uma entidade (sócio, contrato, sanção, termo)."""

    __tablename__ = "avisos"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=novo_id)
    caso_id: Mapped[str] = mapped_column(ForeignKey("casos.id"), index=True)
    monitoramento_id: Mapped[str] = mapped_column(ForeignKey("monitoramentos.id"), index=True)
    execucao_id: Mapped[str] = mapped_column(ForeignKey("monitoramento_execucoes.id"), index=True)
    entidade_id: Mapped[str] = mapped_column(ForeignKey("entidades.id"), index=True)
    # quadro_societario | contrato_novo | sancao_nova | termo_novo
    tipo: Mapped[str] = mapped_column(String(30))
    titulo: Mapped[str] = mapped_column(String(300))
    descricao: Mapped[str] = mapped_column(Text)
    detalhes: Mapped[dict] = mapped_column(JSON, default=dict)
    # Capturas (RF07) em que a novidade aparece.
    captura_ids: Mapped[list] = mapped_column(JSON, default=list)
    criado_em: Mapped[str] = mapped_column(String(40), default=iso_utc)
    lido_em: Mapped[str | None] = mapped_column(String(40), nullable=True)
    lido_por: Mapped[str | None] = mapped_column(String(100), nullable=True)


class Conta(Base):
    """Usuário real do sistema (RNF01, H23): senha com Argon2id e segundo fator por TOTP.

    O segredo do TOTP fica cifrado (AES-256-GCM, chave fora do banco); a senha, só como hash.
    """

    __tablename__ = "usuarios"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=novo_id)
    usuario: Mapped[str] = mapped_column(String(100), unique=True)
    nome: Mapped[str] = mapped_column(String(200), default="")
    perfil: Mapped[str] = mapped_column(String(20))  # analista | delegado | admin
    senha_hash: Mapped[str] = mapped_column(String(300))
    ativo: Mapped[bool] = mapped_column(Boolean, default=True)
    mfa_segredo_cifrado: Mapped[str | None] = mapped_column(Text, nullable=True)
    mfa_ativo: Mapped[bool] = mapped_column(Boolean, default=False)
    mfa_confirmado_em: Mapped[str | None] = mapped_column(String(40), nullable=True)
    # Último passo de tempo aceito: o mesmo código não vale duas vezes (RFC 6238, seção 5.2).
    mfa_ultimo_passo: Mapped[int | None] = mapped_column(Integer, nullable=True)
    falhas_seguidas: Mapped[int] = mapped_column(Integer, default=0)
    bloqueado_ate: Mapped[str | None] = mapped_column(String(40), nullable=True)
    criado_por: Mapped[str] = mapped_column(String(100), default="")
    criado_em: Mapped[str] = mapped_column(String(40), default=iso_utc)
    senha_alterada_em: Mapped[str] = mapped_column(String(40), default=iso_utc)

    sessoes: Mapped[list[Sessao]] = relationship(back_populates="conta")


class Sessao(Base):
    """Sessão de um usuário real. O token é opaco e fica no banco só como SHA-256.

    ``etapa``: ``mfa`` (senha aceita, falta o código), ``cadastro_mfa`` (falta cadastrar o
    segundo fator) ou ``ativa``. Só a sessão ativa dá acesso às rotas.
    """

    __tablename__ = "sessoes"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=novo_id)
    token_resumo: Mapped[str] = mapped_column(String(64), unique=True)
    conta_id: Mapped[str] = mapped_column(ForeignKey("usuarios.id"), index=True)
    etapa: Mapped[str] = mapped_column(String(15))
    criada_em: Mapped[str] = mapped_column(String(40), default=iso_utc)
    expira_em: Mapped[str] = mapped_column(String(40))
    ultima_atividade_em: Mapped[str] = mapped_column(String(40), default=iso_utc)
    encerrada_em: Mapped[str | None] = mapped_column(String(40), nullable=True)
    motivo_encerramento: Mapped[str] = mapped_column(String(40), default="")

    conta: Mapped[Conta] = relationship(back_populates="sessoes")


class CasoMembro(Base):
    """Quem pode ver e agir no caso (RNF01, H23). ``usuario`` é o login (de uma conta real ou,
    no modo de desenvolvimento, de um token de ``TOKENS_DEV``)."""

    __tablename__ = "caso_membros"
    __table_args__ = (UniqueConstraint("caso_id", "usuario", name="uq_caso_membro"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    caso_id: Mapped[str] = mapped_column(ForeignKey("casos.id"), index=True)
    usuario: Mapped[str] = mapped_column(String(100), index=True)
    papel: Mapped[str] = mapped_column(String(15))  # responsavel | investigador | leitor
    adicionado_por: Mapped[str] = mapped_column(String(100))
    adicionado_em: Mapped[str] = mapped_column(String(40), default=iso_utc)


class Anotacao(Base):
    """Anotação de um membro sobre uma entidade ou um alerta do caso (RF08, H37).

    Só de acréscimo: corrigir é anotar de novo, e o histórico fica inteiro."""

    __tablename__ = "anotacoes"
    __table_args__ = (Index("ix_anotacao_alvo", "caso_id", "alvo_tipo", "alvo_id"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=novo_id)
    caso_id: Mapped[str] = mapped_column(ForeignKey("casos.id"), index=True)
    alvo_tipo: Mapped[str] = mapped_column(String(10))  # entidade | alerta
    alvo_id: Mapped[str] = mapped_column(String(32))
    autor: Mapped[str] = mapped_column(String(100))
    criado_em: Mapped[str] = mapped_column(String(40), default=iso_utc)
    texto: Mapped[str] = mapped_column(Text)


class Tarefa(Base):
    """Tarefa do caso (RF08, H37). O estado atual fica aqui; cada mudança, em ``TarefaEvento``."""

    __tablename__ = "tarefas"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=novo_id)
    caso_id: Mapped[str] = mapped_column(ForeignKey("casos.id"), index=True)
    titulo: Mapped[str] = mapped_column(String(300))
    descricao: Mapped[str] = mapped_column(Text, default="")
    responsavel: Mapped[str | None] = mapped_column(String(100), nullable=True)
    situacao: Mapped[str] = mapped_column(String(15), default="aberta")
    prazo: Mapped[str | None] = mapped_column(String(10), nullable=True)  # AAAA-MM-DD
    criado_por: Mapped[str] = mapped_column(String(100))
    criado_em: Mapped[str] = mapped_column(String(40), default=iso_utc)
    atualizado_em: Mapped[str] = mapped_column(String(40), default=iso_utc)

    eventos: Mapped[list[TarefaEvento]] = relationship(
        back_populates="tarefa", order_by="TarefaEvento.id"
    )


class TarefaEvento(Base):
    """Histórico da tarefa: só de acréscimo, uma linha por criação ou alteração."""

    __tablename__ = "tarefa_eventos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tarefa_id: Mapped[str] = mapped_column(ForeignKey("tarefas.id"), index=True)
    caso_id: Mapped[str] = mapped_column(ForeignKey("casos.id"), index=True)
    usuario: Mapped[str] = mapped_column(String(100))
    quando: Mapped[str] = mapped_column(String(40), default=iso_utc)
    # {"campo": {"de": valor anterior, "para": valor novo}}; na criação, "de" é None.
    alteracoes: Mapped[dict] = mapped_column(JSON, default=dict)

    tarefa: Mapped[Tarefa] = relationship(back_populates="eventos")


class ExtracaoIA(Base):
    """Uma execução da extração assistida por IA sobre uma captura (RF13, RNF05, H22).

    Guarda o que permite reconstituir a execução: provedor, modelo, versão (resumo do modelo e
    versão do servidor), parâmetros e o SHA-256 do prompt e do texto analisado."""

    __tablename__ = "extracoes_ia"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=novo_id)
    caso_id: Mapped[str] = mapped_column(ForeignKey("casos.id"), index=True)
    captura_id: Mapped[str] = mapped_column(ForeignKey("capturas.id"), index=True)
    captura_sha256: Mapped[str] = mapped_column(String(64))
    provedor: Mapped[str] = mapped_column(String(30))
    modelo: Mapped[str] = mapped_column(String(200))
    modelo_versao: Mapped[str] = mapped_column(String(200), default="")
    servidor_versao: Mapped[str] = mapped_column(String(50), default="")
    parametros: Mapped[dict] = mapped_column(JSON, default=dict)
    prompt_sha256: Mapped[str] = mapped_column(String(64), default="")
    situacao: Mapped[str] = mapped_column(String(15), default="em_andamento")
    motivo: Mapped[str] = mapped_column(Text, default="")
    blocos: Mapped[int] = mapped_column(Integer, default=0)
    itens_recebidos: Mapped[int] = mapped_column(Integer, default=0)
    itens_aceitos: Mapped[int] = mapped_column(Integer, default=0)
    itens_recusados: Mapped[int] = mapped_column(Integer, default=0)
    # Uma linha por item recusado: motivo e resumo do item (o trecho inventado fica registrado).
    recusas: Mapped[list] = mapped_column(JSON, default=list)
    usuario: Mapped[str] = mapped_column(String(100))
    iniciado_em: Mapped[str] = mapped_column(String(40), default=iso_utc)
    concluido_em: Mapped[str | None] = mapped_column(String(40), nullable=True)

    sugestoes: Mapped[list[SugestaoIA]] = relationship(
        back_populates="extracao", order_by="SugestaoIA.posicao_inicio"
    )


class SugestaoIA(Base):
    """Item extraído pela IA, à espera da decisão do investigador (nunca vira entidade sozinho).

    ``trecho`` é a citação literal e ``posicao_inicio``/``posicao_fim`` dizem onde ela está no
    texto da captura (em caracteres)."""

    __tablename__ = "sugestoes_ia"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=novo_id)
    caso_id: Mapped[str] = mapped_column(ForeignKey("casos.id"), index=True)
    extracao_id: Mapped[str] = mapped_column(ForeignKey("extracoes_ia.id"), index=True)
    captura_id: Mapped[str] = mapped_column(ForeignKey("capturas.id"), index=True)
    tipo: Mapped[str] = mapped_column(String(20))
    valor: Mapped[str] = mapped_column(String(500))
    campos: Mapped[dict] = mapped_column(JSON, default=dict)
    trecho: Mapped[str] = mapped_column(Text)
    posicao_inicio: Mapped[int] = mapped_column(Integer)
    posicao_fim: Mapped[int] = mapped_column(Integer)
    confianca: Mapped[float | None] = mapped_column(Float, nullable=True)
    situacao: Mapped[str] = mapped_column(String(10), default="pendente")
    decidido_por: Mapped[str | None] = mapped_column(String(100), nullable=True)
    decidido_em: Mapped[str | None] = mapped_column(String(40), nullable=True)
    justificativa_decisao: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Entidade criada ou reaproveitada quando a sugestão foi aceita (se o tipo gera entidade).
    entidade_id: Mapped[str | None] = mapped_column(ForeignKey("entidades.id"), nullable=True)

    extracao: Mapped[ExtracaoIA] = relationship(back_populates="sugestoes")


class Auditoria(Base):
    """Trilha somente de acréscimo, encadeada por hash (RNF02)."""

    __tablename__ = "auditoria"

    seq: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # Versão do formato da cadeia (2 = HMAC-SHA256 com chave fora do banco).
    versao: Mapped[int] = mapped_column(Integer)
    quando: Mapped[str] = mapped_column(String(40))
    usuario: Mapped[str] = mapped_column(String(100))
    acao: Mapped[str] = mapped_column(String(60))
    caso_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    justificativa: Mapped[str | None] = mapped_column(Text, nullable=True)
    detalhes: Mapped[dict] = mapped_column(JSON, default=dict)
    hash_anterior: Mapped[str] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64))
