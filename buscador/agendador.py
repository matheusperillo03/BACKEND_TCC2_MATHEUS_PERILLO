"""Agendador interno do monitoramento contínuo (RF06, H18): uma thread do próprio serviço, sem
serviço externo. A cada intervalo, executa os monitoramentos ativos cuja próxima execução já
venceu. Cada execução usa uma sessão própria e entra na trilha de auditoria como o usuário de
sistema ``agendador``, com a justificativa que o investigador deu ao marcar a entidade.

Com mais de um processo da API, ligue o agendador em um só (``MONITORAMENTO_AGENDADOR_ATIVO``);
no PostgreSQL, a linha do monitoramento é travada com ``SKIP LOCKED`` durante a execução.
"""

from __future__ import annotations

import logging
import threading
from datetime import UTC, datetime

from sqlalchemy import select

from buscador import auditoria
from buscador.config import Usuario
from buscador.modelos import Monitoramento, iso_utc

registro = logging.getLogger(__name__)

USUARIO_SISTEMA = Usuario(usuario="agendador", perfil="sistema", origem="sistema")


def justificativa_automatica(monitorado: Monitoramento) -> str:
    texto = f"Monitoramento contínuo (automático): {monitorado.justificativa}".strip()
    return texto[: auditoria.MAX_JUSTIFICATIVA]


class Agendador:
    def __init__(self, estado, intervalo_segundos: float = 60.0) -> None:
        """``estado`` é ``app.state`` (fábrica de sessões, configuração, custódia e âncora)."""
        self.estado = estado
        self.intervalo = intervalo_segundos
        self._parar = threading.Event()
        self._thread: threading.Thread | None = None

    def iniciar(self) -> None:
        if self._thread is not None:
            return
        self._parar.clear()
        self._thread = threading.Thread(target=self._laco, name="agendador", daemon=True)
        self._thread.start()

    def parar(self) -> None:
        self._parar.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def _laco(self) -> None:
        while not self._parar.wait(self.intervalo):
            try:
                self.ciclo()
            except Exception:  # pragma: no cover - o agendador não pode morrer por um ciclo
                registro.exception("Falha no ciclo do agendador de monitoramento.")

    def ciclo(self, agora: datetime | None = None) -> int:
        """Executa os monitoramentos vencidos; devolve quantos foram executados."""
        # Importação tardia: ``buscador.api`` importa este módulo ao montar a aplicação.
        from buscador.api import monitoramento as servico
        from buscador.api.deps import Contexto

        limite = iso_utc(agora or datetime.now(UTC))
        with self.estado.fabrica_sessoes() as sessao:
            pendentes = list(
                sessao.execute(
                    select(Monitoramento.id)
                    .where(
                        Monitoramento.ativo.is_(True), Monitoramento.proxima_execucao_em <= limite
                    )
                    .order_by(Monitoramento.proxima_execucao_em)
                ).scalars()
            )
        executados = 0
        for monitoramento_id in pendentes:
            if self._parar.is_set():
                break
            with self.estado.fabrica_sessoes() as sessao:
                monitorado = (
                    sessao.execute(
                        select(Monitoramento)
                        .where(
                            Monitoramento.id == monitoramento_id,
                            Monitoramento.ativo.is_(True),
                            Monitoramento.proxima_execucao_em <= limite,
                        )
                        .with_for_update(skip_locked=True)
                    )
                    .scalars()
                    .first()
                )
                if monitorado is None:
                    continue
                ctx = Contexto(
                    sessao,
                    USUARIO_SISTEMA,
                    justificativa_automatica(monitorado),
                    self.estado.config,
                    self.estado.custodia,
                    self.estado.extras_conectores,
                    self.estado.chave_auditoria,
                    self.estado.ancora,
                )
                try:
                    servico.executar(ctx, monitorado, "agendador")
                    executados += 1
                except Exception:
                    sessao.rollback()
                    registro.exception("Falha ao executar o monitoramento %s.", monitoramento_id)
        return executados
