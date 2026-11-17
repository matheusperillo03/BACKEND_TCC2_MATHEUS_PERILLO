"""Dependências da API: sessão, autenticação por token, justificativa e registro em auditoria."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from dataclasses import dataclass, field
from urllib.parse import unquote

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from buscador import auditoria, retencao
from buscador.acesso import servico as acesso
from buscador.config import Configuracao, Usuario, resumo_token
from buscador.custodia import Custodia
from buscador.modelos import Auditoria, Caso, CasoMembro
from buscador.seguranca import caractere_proibido

registro = logging.getLogger(__name__)

MAX_JUSTIFICATIVA = auditoria.MAX_JUSTIFICATIVA
MAX_IDENTIFICADOR = 64

# Papéis no caso (H23), do menor para o maior: quem tem um papel pode o que os anteriores podem.
PAPEIS_CASO = ("leitor", "investigador", "responsavel")


def obter_config(request: Request) -> Configuracao:
    return request.app.state.config


def obter_custodia(request: Request) -> Custodia:
    return request.app.state.custodia


def obter_extras_conectores(request: Request) -> dict:
    return request.app.state.extras_conectores


def obter_sessao(request: Request) -> Iterator[Session]:
    sessao = request.app.state.fabrica_sessoes()
    try:
        yield sessao
    finally:
        sessao.close()


def token_do_cabecalho(authorization: str | None) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Informe o cabeçalho Authorization: Bearer <token>.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return authorization.split(" ", 1)[1].strip()


def usuario_atual(
    request: Request,
    authorization: str | None = Header(default=None),
    sessao: Session = Depends(obter_sessao),
) -> Usuario:
    """Identifica quem chama: sessão ativa de um usuário real ou, só com ``MODO_DEV=1``, um
    token fixo de ``TOKENS_DEV``."""
    token = token_do_cabecalho(authorization)
    # Os tokens ficam em memória só como SHA-256: a busca não compara o segredo em claro. Fora do
    # modo de desenvolvimento este mapa é vazio.
    usuario = request.app.state.tokens.get(resumo_token(token))
    if usuario is not None:
        return usuario
    linha = acesso.sessao_ativa(sessao, request.app.state.config, token)
    # Confirma a marca de atividade (ou o encerramento por expiração) antes do trabalho da rota.
    sessao.commit()
    if linha is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Token inválido ou sessão expirada.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    conta = linha.conta
    return Usuario(usuario=conta.usuario, perfil=conta.perfil, origem="sessao", sessao_id=linha.id)


def decodificar_justificativa(valor: str) -> str:
    """Cabeçalhos HTTP só carregam ASCII/Latin-1 com segurança. Aceita o texto percent-encoded
    em UTF-8 (``encodeURIComponent`` no front-end) ou enviado cru; devolve o texto em Unicode."""
    texto = valor
    try:
        texto = texto.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        pass
    if "%" in texto:
        texto = unquote(texto)
    return texto.strip()


def justificativa_obrigatoria(
    x_justificativa: str | None = Header(default=None, alias="X-Justificativa"),
) -> str:
    texto = decodificar_justificativa(x_justificativa or "")
    if not texto:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "O cabeçalho X-Justificativa é obrigatório em toda consulta ou alteração "
            "de dados de caso.",
        )
    if len(texto) > MAX_JUSTIFICATIVA:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"A justificativa deve ter no máximo {MAX_JUSTIFICATIVA} caracteres.",
        )
    if caractere_proibido(texto) is not None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "A justificativa não pode conter quebras de linha, caracteres de controle nem "
            "caracteres invisíveis.",
        )
    if not any(c.isalnum() for c in texto):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "A justificativa precisa conter texto (letras ou números).",
        )
    return texto


def exigir_perfil(*perfis: str):
    def verificar(
        request: Request,
        usuario: Usuario = Depends(usuario_atual),
        sessao: Session = Depends(obter_sessao),
    ) -> Usuario:
        if usuario.perfil not in perfis:
            rota = request.scope.get("route")
            auditar_avulso(
                request,
                sessao,
                usuario=usuario.usuario,
                acao="acesso.perfil_negado",
                detalhes={
                    "perfil": usuario.perfil,
                    "perfis_exigidos": list(perfis),
                    "rota": f"{request.method} {getattr(rota, 'path', request.url.path)}"[:200],
                },
            )
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                f"Acesso restrito aos perfis: {', '.join(perfis)}.",
            )
        return usuario

    return verificar


@dataclass
class Contexto:
    """O que uma rota de caso precisa: sessão, usuário, justificativa e registro em auditoria."""

    sessao: Session
    usuario: Usuario
    justificativa: str
    config: Configuracao
    custodia: Custodia
    extras_conectores: dict
    chave_auditoria: bytes
    ancora: auditoria.Ancora
    # Requisição que altera dados (não é GET): exige, por padrão, o papel de investigador.
    escrita: bool = False
    rota: str = ""
    _pendente: Auditoria | None = field(default=None, repr=False)

    def auditar(self, acao: str, caso_id: str | None, detalhes: dict | None = None) -> None:
        """Acrescenta a entrada na transação corrente. Chamar depois do trabalho da rota: no
        PostgreSQL a trava que serializa a cadeia fica presa até a confirmação."""
        self._pendente = auditoria.registrar(
            self.sessao,
            self.chave_auditoria,
            usuario=self.usuario.usuario,
            acao=acao,
            caso_id=caso_id,
            justificativa=self.justificativa,
            detalhes={"perfil": self.usuario.perfil, **(detalhes or {})},
        )

    def concluir(self) -> None:
        """Confirma a transação e copia o novo topo da cadeia para a âncora externa."""
        self.sessao.commit()
        pendente, self._pendente = self._pendente, None
        if pendente is not None:
            self.ancora.avancar(pendente.seq, pendente.hash)

    def desfazer(self) -> None:
        self.sessao.rollback()
        self._pendente = None

    def papel_no_caso(self, caso_id: str) -> str | None:
        """Papel do usuário no caso (H23), ou ``None`` se não for membro."""
        return self.sessao.execute(
            select(CasoMembro.papel).where(
                CasoMembro.caso_id == caso_id, CasoMembro.usuario == self.usuario.usuario
            )
        ).scalar_one_or_none()

    def caso(self, caso_id: str, papel_minimo: str | None = None) -> Caso:
        """O caso, se existir e o usuário for membro com papel suficiente (H23).

        Sem ``papel_minimo``, a leitura exige ``leitor`` e a alteração (requisição que não é
        GET), ``investigador``. A negação responde ``403`` e fica na trilha de auditoria
        (``caso.acesso_negado``) com a rota tentada."""
        if len(caso_id) > MAX_IDENTIFICADOR:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Caso não encontrado.")
        caso = self.sessao.get(Caso, caso_id)
        if caso is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"Caso {caso_id} não encontrado.")
        if self.usuario.origem == "sistema":
            # Agendador interno: age em nome de quem marcou o monitoramento.
            return caso
        exigido = papel_minimo or ("investigador" if self.escrita else "leitor")
        papel = self.papel_no_caso(caso.id)
        if papel is None or PAPEIS_CASO.index(papel) < PAPEIS_CASO.index(exigido):
            self.desfazer()
            self.auditar(
                "caso.acesso_negado",
                caso.id,
                {"rota": self.rota, "papel_exigido": exigido, "papel_atual": papel},
            )
            self.concluir()
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                "Você não é membro deste caso."
                if papel is None
                else f"Seu papel neste caso ({papel}) não permite esta ação (exige {exigido}).",
            )
        return caso

    def caso_aberto(self, caso_id: str, acao: str, papel_minimo: str | None = None) -> Caso:
        """O caso, se ainda estiver dentro do prazo de retenção (RNF04, H17).

        Com o prazo vencido o caso é só leitura: a ação é recusada com ``403`` e a recusa fica
        na trilha de auditoria (``caso.retencao_recusa``), com a ação tentada."""
        caso = self.caso(caso_id, papel_minimo)
        if retencao.vencida(caso):
            self.auditar(
                "caso.retencao_recusa",
                caso.id,
                {"acao_recusada": acao, "retencao_ate": caso.retencao_ate},
            )
            self.concluir()
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                f"O prazo de retenção deste caso terminou em {caso.retencao_ate}: o caso está "
                "só para leitura. Novas coletas, consultas, execuções de tipologia e decisões "
                "são recusadas, e esta tentativa foi registrada na auditoria.",
            )
        return caso


def contexto(
    request: Request,
    sessao: Session = Depends(obter_sessao),
    usuario: Usuario = Depends(usuario_atual),
    justificativa: str = Depends(justificativa_obrigatoria),
    config: Configuracao = Depends(obter_config),
    custodia: Custodia = Depends(obter_custodia),
    extras_conectores: dict = Depends(obter_extras_conectores),
) -> Contexto:
    rota = request.scope.get("route")
    return Contexto(
        sessao,
        usuario,
        justificativa,
        config,
        custodia,
        extras_conectores,
        request.app.state.chave_auditoria,
        request.app.state.ancora,
        escrita=request.method not in ("GET", "HEAD"),
        rota=f"{request.method} {getattr(rota, 'path', request.url.path)}"[:200],
    )


def auditar_avulso(
    request: Request,
    sessao: Session,
    *,
    usuario: str,
    acao: str,
    detalhes: dict | None = None,
) -> None:
    """Entrada de auditoria fora de um caso e sem justificativa (login, logout, falhas de
    autenticação): grava, confirma a transação e avança a âncora externa."""
    entrada = auditoria.registrar(
        sessao,
        request.app.state.chave_auditoria,
        usuario=usuario,
        acao=acao,
        caso_id=None,
        justificativa=None,
        detalhes=detalhes or {},
    )
    sessao.commit()
    request.app.state.ancora.avancar(entrada.seq, entrada.hash)
