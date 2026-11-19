"""Trabalhador da extração de editais: uma thread do próprio serviço que consome a fila.

A fila é a própria tabela ``extracoes_ia`` (situação ``na_fila``): a rota grava a execução e
responde na hora; esta thread assume a mais antiga, processa bloco a bloco
(``buscador.ia.servico.processar``) e grava o resultado de cada bloco assim que ele termina.

Por que uma thread com a fila no banco, e não um serviço de fila à parte (decisão do autor): o
Compose tem só a API, o PostgreSQL e o front-end; a fila no banco não acrescenta nenhuma peça,
sobrevive ao reinício da API (o estado está no banco, não na memória) e uma única thread já é o
limite de concorrência que a máquina aguenta (um modelo por vez). É o mesmo desenho do agendador
do monitoramento (``buscador.agendador``).

Reinício: ao iniciar, toda execução que ficou ``em_andamento`` é de um processo que não existe
mais; ela passa a ``interrompida`` (auditada) e pode ser retomada. As que estavam ``na_fila``
continuam na fila e são processadas normalmente.

Com mais de um processo da API, ligue o trabalhador em um só (``IA_TRABALHADOR_ATIVO``): a
recuperação da partida supõe que não há outro trabalhador vivo.

As entradas de auditoria gravadas aqui saem como o usuário de sistema ``extrator``, com a
justificativa de quem pediu (ou retomou) a execução, que fica nos detalhes como
``solicitada_por``.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid

from sqlalchemy import select

from buscador import auditoria
from buscador.config import Usuario
from buscador.ia import servico
from buscador.modelos import ExtracaoIA

registro = logging.getLogger(__name__)

USUARIO_SISTEMA = Usuario(usuario="extrator", perfil="sistema", origem="sistema")
# De quanto em quanto tempo o pedido de cancelamento é lido do banco durante a geração.
INTERVALO_CANCELAMENTO_SEGUNDOS = 0.3
ESPERA_AO_PARAR_SEGUNDOS = 10.0


def justificativa_automatica(extracao: ExtracaoIA) -> str:
    texto = f"Extração de editais em segundo plano (automático): {extracao.justificativa or ''}"
    return texto.strip()[: auditoria.MAX_JUSTIFICATIVA]


class Trabalhador:
    def __init__(self, estado, intervalo_segundos: float = 2.0) -> None:
        """``estado`` é ``app.state`` (fábrica de sessões, configuração, custódia, âncora e o
        provedor do modelo)."""
        self.estado = estado
        self.intervalo = intervalo_segundos
        self.id = uuid.uuid4().hex
        self._parar = threading.Event()
        self._acordar = threading.Event()
        self._thread: threading.Thread | None = None

    # ---- Ciclo de vida ------------------------------------------------------------------------

    def iniciar(self) -> None:
        if self._thread is not None:
            return
        self._parar.clear()
        try:
            self.recuperar()
        except Exception:  # pragma: no cover - a API sobe mesmo se a recuperação falhar
            registro.exception("Falha ao recuperar as extrações interrompidas.")
        self._thread = threading.Thread(target=self._laco, name="extrator", daemon=True)
        self._thread.start()

    def parar(self) -> None:
        """Pede a parada: a execução em curso é fechada como ``interrompida`` (retomável)."""
        self._parar.set()
        self._acordar.set()
        if self._thread is not None:
            self._thread.join(timeout=ESPERA_AO_PARAR_SEGUNDOS)
            self._thread = None

    def acordar(self) -> None:
        """Chamado pela rota depois de pôr uma execução na fila, para não esperar o intervalo."""
        self._acordar.set()

    def _laco(self) -> None:
        while not self._parar.is_set():
            try:
                trabalhou = self.ciclo()
            except Exception:  # o trabalhador não pode morrer por uma execução
                registro.exception("Falha no ciclo do trabalhador da extração.")
                trabalhou = False
            if not trabalhou:
                self._acordar.wait(self.intervalo)
                self._acordar.clear()

    # ---- Trabalho -----------------------------------------------------------------------------

    def _contexto(self, sessao, extracao: ExtracaoIA):
        # Importação tardia: ``buscador.api`` importa este módulo ao montar a aplicação.
        from buscador.api.deps import Contexto

        return Contexto(
            sessao,
            USUARIO_SISTEMA,
            justificativa_automatica(extracao),
            self.estado.config,
            self.estado.custodia,
            self.estado.extras_conectores,
            self.estado.chave_auditoria,
            self.estado.ancora,
        )

    def recuperar(self, so_minhas: bool = False) -> int:
        """Fecha como ``interrompida`` as execuções ``em_andamento`` sem dono vivo: todas, na
        partida; só as deste trabalhador, depois de um erro que o impediu de fechá-las."""
        with self.estado.fabrica_sessoes() as sessao:
            orfas = servico.marcar_orfas(sessao, self.id if so_minhas else None)
            for extracao in orfas:
                ctx = self._contexto(sessao, extracao)
                ctx.auditar(
                    "ia.extracao_interrompida",
                    extracao.caso_id,
                    servico.detalhes_para_auditoria(extracao),
                )
                ctx.concluir()
            sessao.commit()
            return len(orfas)

    def _interromper(self, extracao_id: str):
        """Função que diz se a execução deve parar: ``parando`` (o serviço está saindo) ou
        ``cancelada`` (pedido gravado no banco, lido em conexão própria e a intervalos)."""
        ultima_leitura = [float("-inf")]
        cancelada = [False]

        def conferir() -> str | None:
            if self._parar.is_set():
                return "parando"
            agora = time.monotonic()
            if not cancelada[0] and agora - ultima_leitura[0] >= INTERVALO_CANCELAMENTO_SEGUNDOS:
                ultima_leitura[0] = agora
                with self.estado.engine.connect() as conexao:
                    cancelada[0] = bool(
                        conexao.execute(
                            select(ExtracaoIA.cancelamento_pedido_em).where(
                                ExtracaoIA.id == extracao_id
                            )
                        ).scalar()
                    )
            return "cancelada" if cancelada[0] else None

        return conferir

    def ciclo(self) -> bool:
        """Assume e processa UMA execução da fila; devolve se havia o que processar."""
        provedor = self.estado.provedor_ia
        if provedor is None:
            return False
        with self.estado.fabrica_sessoes() as sessao:
            extracao_id = servico.assumir(sessao, self.id)
            if extracao_id is None:
                return False
            extracao = sessao.get(ExtracaoIA, extracao_id)
            controle = servico.ControleExecucao(
                trabalhador=self.id, interromper=self._interromper(extracao_id)
            )
            try:
                servico.processar(
                    self._contexto(sessao, extracao),
                    extracao_id,
                    provedor,
                    servico.Ajustes.da_configuracao(self.estado.config),
                    controle,
                )
            except Exception:
                sessao.rollback()
                registro.exception("A extração %s não pôde ser fechada.", extracao_id)
                # Não deixa a execução presa em "em_andamento" (ela seguraria a fila inteira).
                self.recuperar(so_minhas=True)
        return True
