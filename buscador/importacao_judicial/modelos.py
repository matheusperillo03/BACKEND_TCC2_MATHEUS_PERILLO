"""Tabelas do dado sob ordem judicial. Metadados PRÓPRIOS (``BaseSigilosa``): estas tabelas não
existem na base do dado aberto e são criadas em outra base (``IMPORTACAO_JUDICIAL_DATABASE_URL``).
"""

from __future__ import annotations

from sqlalchemy import Boolean, ForeignKey, Integer, LargeBinary, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from buscador.modelos import iso_utc, novo_id


class BaseSigilosa(DeclarativeBase):
    pass


class DecisaoJudicial(BaseSigilosa):
    """Ato de entrada: a decisão judicial que autoriza a importação. Sem ela, nada é importado.

    O escopo (contas, investigados e descrição) fica cifrado em ``escopo_cifrado``."""

    __tablename__ = "ij_decisoes"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=novo_id)
    # Id do caso na base do dado aberto (sem chave estrangeira: são bases diferentes).
    caso_id: Mapped[str] = mapped_column(String(32), index=True)
    numero_processo: Mapped[str] = mapped_column(String(100))
    numero_decisao: Mapped[str] = mapped_column(String(100))
    juizo: Mapped[str] = mapped_column(String(200))
    data_decisao: Mapped[str] = mapped_column(String(10))
    periodo_inicio: Mapped[str] = mapped_column(String(10))
    periodo_fim: Mapped[str] = mapped_column(String(10))
    escopo_cifrado: Mapped[bytes] = mapped_column(LargeBinary)
    registrado_por: Mapped[str] = mapped_column(String(100))
    registrado_em: Mapped[str] = mapped_column(String(40), default=iso_utc)


class Importacao(BaseSigilosa):
    __tablename__ = "ij_importacoes"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=novo_id)
    decisao_id: Mapped[str] = mapped_column(ForeignKey("ij_decisoes.id"), index=True)
    caso_id: Mapped[str] = mapped_column(String(32), index=True)
    origem: Mapped[str] = mapped_column(String(10))  # simba | oficio | midia
    descricao_origem: Mapped[str] = mapped_column(Text, default="")
    importado_por: Mapped[str] = mapped_column(String(100))
    importado_em: Mapped[str] = mapped_column(String(40), default=iso_utc)


class ArquivoRecebido(BaseSigilosa):
    """Custódia do arquivo recebido (RF07): SHA-256, tamanho e contagens da validação. Os bytes
    ficam cifrados em disco, fora da custódia do dado aberto."""

    __tablename__ = "ij_arquivos"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=novo_id)
    importacao_id: Mapped[str] = mapped_column(ForeignKey("ij_importacoes.id"), index=True)
    tipo: Mapped[str] = mapped_column(String(20))
    nome_arquivo: Mapped[str] = mapped_column(String(200))
    sha256: Mapped[str] = mapped_column(String(64))
    tamanho_bytes: Mapped[int] = mapped_column(Integer)
    linhas: Mapped[int] = mapped_column(Integer, default=0)
    aceitas: Mapped[int] = mapped_column(Integer, default=0)
    rejeitadas: Mapped[int] = mapped_column(Integer, default=0)
    em_quarentena: Mapped[int] = mapped_column(Integer, default=0)
    # Relatório das rejeições (linha, campo e motivo; nunca o conteúdo), cifrado.
    rejeicoes_cifradas: Mapped[bytes] = mapped_column(LargeBinary)


class RegistroSigiloso(BaseSigilosa):
    """Uma linha aceita de um arquivo. Os campos ficam cifrados; em claro, só o que é preciso
    para filtrar (tipo, linha e quarentena)."""

    __tablename__ = "ij_registros"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=novo_id)
    importacao_id: Mapped[str] = mapped_column(ForeignKey("ij_importacoes.id"), index=True)
    decisao_id: Mapped[str] = mapped_column(ForeignKey("ij_decisoes.id"), index=True)
    caso_id: Mapped[str] = mapped_column(String(32), index=True)
    tipo: Mapped[str] = mapped_column(String(20), index=True)
    linha: Mapped[int] = mapped_column(Integer)
    quarentena: Mapped[bool] = mapped_column(Boolean, default=False)
    motivo_quarentena: Mapped[str] = mapped_column(String(200), default="")
    campos_cifrados: Mapped[bytes] = mapped_column(LargeBinary)
