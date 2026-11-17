"""Autenticação de usuários reais (RNF01, H23): senha, segundo fator (TOTP) e sessão; e a
administração de contas pelo perfil ``admin``.

As rotas ``/auth/*`` não pedem ``X-Justificativa`` (não são consulta a dado de caso), mas todo
evento entra na trilha de auditoria: login, falha de senha, falha de código, bloqueio e logout.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from buscador.acesso import senhas
from buscador.acesso import servico as acesso
from buscador.api import esquemas_h3 as esquemas
from buscador.api.deps import (
    Contexto,
    auditar_avulso,
    contexto,
    exigir_perfil,
    obter_sessao,
    token_do_cabecalho,
    usuario_atual,
)
from buscador.config import Usuario
from buscador.modelos import Conta, Sessao
from buscador.seguranca import texto_de_linha

roteador = APIRouter(prefix="/auth", tags=["autenticacao"])
roteador_usuarios = APIRouter(
    prefix="/usuarios", tags=["usuarios"], dependencies=[Depends(exigir_perfil("admin"))]
)


def _saida(aberta: acesso.SessaoAberta) -> dict:
    return {
        "etapa": aberta.sessao.etapa,
        "token": aberta.token,
        "expira_em": aberta.sessao.expira_em,
        "usuario": aberta.conta.usuario,
        "perfil": aberta.conta.perfil,
        "segredo": aberta.segredo_base32,
        "otpauth_uri": aberta.uri_cadastro,
    }


def _falhar(request: Request, sessao: Session, usuario: str, acao: str, erro: acesso.ErroAcesso):
    """Confirma o que a tentativa mudou (contador de falhas, bloqueio), audita e responde."""
    sessao.flush()
    auditar_avulso(
        request,
        sessao,
        usuario=texto_de_linha(usuario)[:100] or "(vazio)",
        acao=acao,
        detalhes={"motivo": erro.motivo},
    )
    raise HTTPException(erro.status, str(erro))


@roteador.post("/login", response_model=esquemas.SessaoSaida)
def login(corpo: esquemas.LoginEntrada, request: Request, sessao: Session = Depends(obter_sessao)):
    """Primeira etapa: usuário e senha. A resposta diz a etapa seguinte: ``mfa`` (enviar o
    código), ``cadastro_mfa`` (cadastrar o segundo fator e enviar o primeiro código) ou
    ``ativa`` (perfil sem MFA obrigatório). O token das etapas pendentes só serve em
    ``POST /auth/mfa``."""
    estado = request.app.state
    try:
        aberta = acesso.autenticar_senha(
            sessao, estado.config, estado.cofre, corpo.usuario, corpo.senha
        )
    except acesso.ErroAcesso as erro:
        _falhar(request, sessao, corpo.usuario.strip().lower(), "auth.login_falhou", erro)
    auditar_avulso(
        request,
        sessao,
        usuario=aberta.conta.usuario,
        acao="auth.login" if aberta.sessao.etapa == "ativa" else "auth.senha_aceita",
        detalhes={
            "perfil": aberta.conta.perfil,
            "etapa": aberta.sessao.etapa,
            "sessao_id": aberta.sessao.id,
            "mfa": False,
        },
    )
    return _saida(aberta)


@roteador.post("/mfa", response_model=esquemas.SessaoSaida)
def segundo_fator(
    corpo: esquemas.CodigoEntrada,
    request: Request,
    authorization: str | None = Header(default=None),
    sessao: Session = Depends(obter_sessao),
):
    """Segunda etapa: o código TOTP, com o token pendente em ``Authorization``. Devolve a sessão
    ativa, com um token novo."""
    estado = request.app.state
    token = token_do_cabecalho(authorization)
    pendente = (
        sessao.execute(select(Sessao).where(Sessao.token_resumo == acesso.resumo_token(token)))
        .scalars()
        .first()
    )
    quem = pendente.conta.usuario if pendente is not None else "(desconhecido)"
    try:
        aberta = acesso.confirmar_segundo_fator(
            sessao, estado.config, estado.cofre, token, corpo.codigo
        )
    except acesso.ErroAcesso as erro:
        _falhar(request, sessao, quem, "auth.mfa_falhou", erro)
    auditar_avulso(
        request,
        sessao,
        usuario=aberta.conta.usuario,
        acao="auth.login",
        detalhes={
            "perfil": aberta.conta.perfil,
            "sessao_id": aberta.sessao.id,
            "mfa": True,
            "mfa_cadastrado_agora": pendente is not None and pendente.etapa == "cadastro_mfa",
        },
    )
    return _saida(aberta)


def _sessao_do_usuario(sessao: Session, usuario: Usuario) -> Sessao:
    linha = sessao.get(Sessao, usuario.sessao_id) if usuario.sessao_id else None
    if linha is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Tokens de desenvolvimento não têm sessão: esta rota vale só para usuários reais.",
        )
    return linha


@roteador.get("/sessao", response_model=esquemas.SessaoAtual)
def sessao_atual(
    request: Request,
    usuario: Usuario = Depends(usuario_atual),
    sessao: Session = Depends(obter_sessao),
):
    if usuario.sessao_id is None:
        return {
            "usuario": usuario.usuario,
            "perfil": usuario.perfil,
            "autenticacao": "token_dev",
            "mfa_ativo": False,
            "expira_em": None,
            "inatividade_minutos": None,
        }
    linha = _sessao_do_usuario(sessao, usuario)
    return {
        "usuario": usuario.usuario,
        "perfil": usuario.perfil,
        "autenticacao": "sessao",
        "mfa_ativo": bool(linha.conta.mfa_ativo),
        "expira_em": linha.expira_em,
        "inatividade_minutos": request.app.state.config.sessao_inatividade_minutos,
    }


@roteador.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    request: Request,
    usuario: Usuario = Depends(usuario_atual),
    sessao: Session = Depends(obter_sessao),
):
    linha = _sessao_do_usuario(sessao, usuario)
    acesso.encerrar(linha, "logout")
    auditar_avulso(
        request,
        sessao,
        usuario=usuario.usuario,
        acao="auth.logout",
        detalhes={"sessao_id": linha.id},
    )


@roteador.post("/senha", status_code=status.HTTP_204_NO_CONTENT)
def trocar_senha(
    corpo: esquemas.TrocaSenhaEntrada,
    request: Request,
    usuario: Usuario = Depends(usuario_atual),
    sessao: Session = Depends(obter_sessao),
):
    """Troca a própria senha (exige a atual) e encerra as outras sessões do usuário."""
    linha = _sessao_do_usuario(sessao, usuario)
    conta = linha.conta
    if not senhas.conferir(conta.senha_hash, corpo.senha_atual):
        _falhar(
            request,
            sessao,
            usuario.usuario,
            "auth.troca_de_senha_falhou",
            acesso.ErroAcesso("A senha atual não confere.", "senha_atual", 403),
        )
    try:
        acesso.definir_senha(sessao, request.app.state.config, conta, corpo.senha_nova)
    except acesso.ErroAcesso as erro:
        raise HTTPException(erro.status, str(erro)) from erro
    encerradas = acesso.encerrar_sessoes(sessao, conta, "troca_de_senha", exceto=linha.id)
    auditar_avulso(
        request,
        sessao,
        usuario=usuario.usuario,
        acao="auth.trocar_senha",
        detalhes={"sessoes_encerradas": encerradas},
    )


# ---- Administração de contas (perfil admin) ----------------------------------------------------


def _conta_para_dict(ctx: Contexto, conta: Conta) -> dict:
    return {
        "usuario": conta.usuario,
        "nome": conta.nome or "",
        "perfil": conta.perfil,
        "ativo": bool(conta.ativo),
        "mfa_ativo": bool(conta.mfa_ativo),
        "mfa_obrigatorio": conta.perfil in ctx.config.perfis_com_mfa(),
        "bloqueado_ate": conta.bloqueado_ate,
        "criado_por": conta.criado_por or "",
        "criado_em": conta.criado_em,
    }


@roteador_usuarios.get("", response_model=list[esquemas.UsuarioAdminSaida])
def listar_usuarios(ctx: Contexto = Depends(contexto)):
    contas = ctx.sessao.execute(select(Conta).order_by(Conta.usuario)).scalars().all()
    ctx.auditar("usuario.listar", None, {"total": len(contas)})
    ctx.concluir()
    return [_conta_para_dict(ctx, c) for c in contas]


@roteador_usuarios.post(
    "", response_model=esquemas.UsuarioAdminSaida, status_code=status.HTTP_201_CREATED
)
def criar_usuario(corpo: esquemas.UsuarioEntrada, ctx: Contexto = Depends(contexto)):
    """Cria a conta com uma senha inicial. O segundo fator é cadastrado pelo próprio usuário no
    primeiro login (o administrador nunca vê o segredo)."""
    try:
        conta = acesso.criar_conta(
            ctx.sessao,
            ctx.config,
            usuario=corpo.usuario,
            perfil=corpo.perfil,
            senha=corpo.senha,
            nome=corpo.nome,
            criado_por=ctx.usuario.usuario,
        )
    except acesso.ErroAcesso as erro:
        raise HTTPException(erro.status, str(erro)) from erro
    ctx.auditar(
        "usuario.criar", None, {"usuario_criado": conta.usuario, "perfil_criado": conta.perfil}
    )
    ctx.concluir()
    return _conta_para_dict(ctx, conta)


@roteador_usuarios.post("/{usuario}", response_model=esquemas.UsuarioAdminSaida)
def alterar_usuario(
    usuario: str, corpo: esquemas.UsuarioAlteracao, ctx: Contexto = Depends(contexto)
):
    """Ativa ou desativa, muda o perfil, redefine a senha ou apaga o segundo fator (o usuário
    cadastra outro no próximo login). Qualquer dessas mudanças encerra as sessões da conta."""
    conta = acesso.obter_conta(ctx.sessao, usuario) if len(usuario) <= 100 else None
    if conta is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Usuário não encontrado.")
    mudancas: dict[str, object] = {}
    if corpo.ativo is not None and corpo.ativo != bool(conta.ativo):
        if not corpo.ativo and conta.usuario == ctx.usuario.usuario:
            raise HTTPException(422, "Você não pode desativar a própria conta.")
        conta.ativo = corpo.ativo
        mudancas["ativo"] = corpo.ativo
    if corpo.perfil is not None and corpo.perfil != conta.perfil:
        if conta.usuario == ctx.usuario.usuario:
            raise HTTPException(422, "Você não pode mudar o próprio perfil.")
        mudancas["perfil"] = {"de": conta.perfil, "para": corpo.perfil}
        conta.perfil = corpo.perfil
    if corpo.senha is not None:
        try:
            acesso.definir_senha(ctx.sessao, ctx.config, conta, corpo.senha)
        except acesso.ErroAcesso as erro:
            raise HTTPException(erro.status, str(erro)) from erro
        mudancas["senha_redefinida"] = True
    if corpo.redefinir_mfa:
        acesso.redefinir_mfa(conta)
        mudancas["mfa_redefinido"] = True
    if mudancas:
        mudancas["sessoes_encerradas"] = acesso.encerrar_sessoes(
            ctx.sessao, conta, "alteracao_da_conta", exceto=ctx.usuario.sessao_id
        )
    ctx.auditar("usuario.alterar", None, {"usuario_alterado": conta.usuario, "mudancas": mudancas})
    ctx.concluir()
    return _conta_para_dict(ctx, conta)
