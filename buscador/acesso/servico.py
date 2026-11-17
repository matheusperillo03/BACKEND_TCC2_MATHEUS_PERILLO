"""Contas, autenticação em duas etapas e sessões (RNF01, H23).

Fluxo: ``autenticar_senha`` confere a senha e abre uma sessão PENDENTE (``mfa`` ou
``cadastro_mfa``), que só serve para apresentar o código do segundo fator;
``confirmar_segundo_fator`` confere o TOTP, encerra a pendente e abre a sessão ATIVA com um token
novo. Para perfil sem MFA obrigatório e sem MFA cadastrado, a senha já abre a sessão ativa.

Sessão: token opaco de 256 bits, guardado no banco só como SHA-256 (quem lê o banco não obtém um
token válido). Decisão: sessão no servidor em vez de JWT, porque o encerramento (logout,
desativação da conta, troca de senha) precisa valer na hora, e um JWT continua válido até expirar.
A sessão expira pela duração máxima e por inatividade.

Força bruta: falhas seguidas de senha ou de código bloqueiam a conta por um tempo. A mensagem de
falha não diz se o login existe.
"""

from __future__ import annotations

import base64
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from buscador.acesso import senhas, totp
from buscador.config import PERFIS, Configuracao, resumo_token
from buscador.cripto import Cofre, ErroCripto
from buscador.modelos import Conta, Sessao, iso_utc

CONTEXTO_TOTP = "totp"
PADRAO_USUARIO = re.compile(r"[a-z0-9][a-z0-9._-]{2,63}")
ETAPAS_PENDENTES = ("mfa", "cadastro_mfa")
# A marca de atividade da sessão só é regravada depois deste intervalo (menos escritas).
_INTERVALO_ATIVIDADE = timedelta(seconds=60)


class ErroAcesso(Exception):
    """Falha de autenticação ou de regra de conta. ``motivo`` vai só para a auditoria."""

    def __init__(self, mensagem: str, motivo: str, status: int = 401) -> None:
        super().__init__(mensagem)
        self.motivo = motivo
        self.status = status


MENSAGEM_CREDENCIAIS = "Usuário ou senha inválidos."
MENSAGEM_BLOQUEIO = (
    "Conta temporariamente bloqueada por excesso de tentativas. Tente de novo mais tarde."
)


@dataclass(frozen=True)
class SessaoAberta:
    token: str
    sessao: Sessao
    conta: Conta
    # Só no cadastro do segundo fator: o segredo em Base32 e a URI para o aplicativo.
    segredo_base32: str | None = None
    uri_cadastro: str | None = None


def _agora() -> datetime:
    return datetime.now(UTC)


def _data(texto: str | None) -> datetime | None:
    if not texto:
        return None
    try:
        return datetime.fromisoformat(texto.replace("Z", "+00:00"))
    except ValueError:
        return None


def validar_usuario(usuario: str) -> str:
    usuario = usuario.strip().lower()
    if not PADRAO_USUARIO.fullmatch(usuario):
        raise ErroAcesso(
            "O nome de usuário deve ter de 3 a 64 caracteres: letras minúsculas, dígitos, ponto, "
            "hífen ou sublinhado, começando por letra ou dígito.",
            "usuario_invalido",
            422,
        )
    return usuario


def obter_conta(sessao: Session, usuario: str) -> Conta | None:
    return sessao.execute(select(Conta).where(Conta.usuario == usuario)).scalars().first()


def criar_conta(
    sessao: Session,
    config: Configuracao,
    *,
    usuario: str,
    perfil: str,
    senha: str,
    nome: str = "",
    criado_por: str,
) -> Conta:
    usuario = validar_usuario(usuario)
    if perfil not in PERFIS:
        raise ErroAcesso(f"Perfil desconhecido (aceitos: {', '.join(PERFIS)}).", "perfil", 422)
    try:
        senhas.validar_politica(senha, usuario, config.senha_tamanho_minimo)
    except senhas.SenhaFraca as erro:
        raise ErroAcesso(str(erro), "senha_fraca", 422) from erro
    if obter_conta(sessao, usuario) is not None:
        raise ErroAcesso(f"Já existe o usuário '{usuario}'.", "usuario_repetido", 409)
    conta = Conta(
        usuario=usuario,
        nome=nome.strip(),
        perfil=perfil,
        senha_hash=senhas.gerar_hash(senha),
        criado_por=criado_por,
    )
    sessao.add(conta)
    sessao.flush()
    return conta


def definir_senha(sessao: Session, config: Configuracao, conta: Conta, senha: str) -> None:
    try:
        senhas.validar_politica(senha, conta.usuario, config.senha_tamanho_minimo)
    except senhas.SenhaFraca as erro:
        raise ErroAcesso(str(erro), "senha_fraca", 422) from erro
    conta.senha_hash = senhas.gerar_hash(senha)
    conta.senha_alterada_em = iso_utc()
    conta.falhas_seguidas = 0
    conta.bloqueado_ate = None


def redefinir_mfa(conta: Conta) -> None:
    """Apaga o segundo fator: o usuário cadastra outro no próximo login."""
    conta.mfa_segredo_cifrado = None
    conta.mfa_ativo = False
    conta.mfa_confirmado_em = None
    conta.mfa_ultimo_passo = None


def encerrar_sessoes(sessao: Session, conta: Conta, motivo: str, exceto: str | None = None) -> int:
    consulta = (
        update(Sessao)
        .where(Sessao.conta_id == conta.id, Sessao.encerrada_em.is_(None))
        .values(encerrada_em=iso_utc(), motivo_encerramento=motivo)
    )
    if exceto:
        consulta = consulta.where(Sessao.id != exceto)
    return sessao.execute(consulta).rowcount or 0


# ---- Segredo do TOTP (cifrado no banco) ----------------------------------------------------------


def _guardar_segredo(cofre: Cofre, conta: Conta, segredo: bytes) -> None:
    blob = cofre.cifrar(CONTEXTO_TOTP, segredo, conta.id.encode("ascii"))
    conta.mfa_segredo_cifrado = base64.b64encode(blob).decode("ascii")


def _ler_segredo(cofre: Cofre, conta: Conta) -> bytes | None:
    if not conta.mfa_segredo_cifrado:
        return None
    try:
        blob = base64.b64decode(conta.mfa_segredo_cifrado, validate=True)
        return cofre.decifrar(CONTEXTO_TOTP, blob, conta.id.encode("ascii"))
    except (ErroCripto, ValueError):
        return None


# ---- Sessões -------------------------------------------------------------------------------------


def _abrir_sessao(sessao: Session, conta: Conta, etapa: str, minutos: int) -> tuple[str, Sessao]:
    token = secrets.token_urlsafe(32)
    agora = _agora()
    linha = Sessao(
        token_resumo=resumo_token(token),
        conta_id=conta.id,
        etapa=etapa,
        criada_em=iso_utc(agora),
        expira_em=iso_utc(agora + timedelta(minutes=minutos)),
        ultima_atividade_em=iso_utc(agora),
    )
    sessao.add(linha)
    sessao.flush()
    return token, linha


def _registrar_falha(config: Configuracao, conta: Conta) -> bool:
    """Conta a falha; devolve verdadeiro se a conta ficou bloqueada agora."""
    conta.falhas_seguidas = (conta.falhas_seguidas or 0) + 1
    if conta.falhas_seguidas >= config.login_max_falhas:
        conta.bloqueado_ate = iso_utc(_agora() + timedelta(minutes=config.login_bloqueio_minutos))
        conta.falhas_seguidas = 0
        return True
    return False


def _bloqueada(conta: Conta) -> bool:
    limite = _data(conta.bloqueado_ate)
    return limite is not None and limite > _agora()


def autenticar_senha(
    sessao: Session, config: Configuracao, cofre: Cofre, usuario: str, senha: str
) -> SessaoAberta:
    """Primeira etapa. Levanta ``ErroAcesso`` (com o motivo para a auditoria) se falhar."""
    usuario = usuario.strip().lower()
    conta = obter_conta(sessao, usuario) if PADRAO_USUARIO.fullmatch(usuario) else None
    if conta is None:
        senhas.conferir(None, senha)  # mesmo custo de uma verificação real
        raise ErroAcesso(MENSAGEM_CREDENCIAIS, "usuario_inexistente")
    if _bloqueada(conta):
        raise ErroAcesso(MENSAGEM_BLOQUEIO, "conta_bloqueada", 429)
    if not senhas.conferir(conta.senha_hash, senha):
        bloqueou = _registrar_falha(config, conta)
        raise ErroAcesso(
            MENSAGEM_CREDENCIAIS, "senha_incorreta_conta_bloqueada" if bloqueou else "senha"
        )
    if not conta.ativo:
        raise ErroAcesso(MENSAGEM_CREDENCIAIS, "conta_inativa")
    if senhas.precisa_atualizar(conta.senha_hash):
        conta.senha_hash = senhas.gerar_hash(senha)
    if conta.mfa_ativo:
        token, linha = _abrir_sessao(sessao, conta, "mfa", config.sessao_pendente_minutos)
        return SessaoAberta(token, linha, conta)
    if conta.perfil in config.perfis_com_mfa():
        # Primeiro acesso: gera um segredo novo a cada tentativa, até a confirmação.
        segredo = totp.gerar_segredo()
        _guardar_segredo(cofre, conta, segredo)
        token, linha = _abrir_sessao(sessao, conta, "cadastro_mfa", config.sessao_pendente_minutos)
        return SessaoAberta(
            token,
            linha,
            conta,
            segredo_base32=totp.segredo_base32(segredo),
            uri_cadastro=totp.uri_de_cadastro(segredo, conta.usuario, config.totp_emissor),
        )
    conta.falhas_seguidas = 0
    token, linha = _abrir_sessao(sessao, conta, "ativa", config.sessao_duracao_minutos)
    return SessaoAberta(token, linha, conta)


def _sessao_valida(sessao: Session, token: str, etapas: tuple[str, ...]) -> Sessao | None:
    linha = (
        sessao.execute(select(Sessao).where(Sessao.token_resumo == resumo_token(token)))
        .scalars()
        .first()
    )
    if linha is None or linha.encerrada_em is not None or linha.etapa not in etapas:
        return None
    expira = _data(linha.expira_em)
    if expira is None or expira <= _agora():
        linha.encerrada_em = iso_utc()
        linha.motivo_encerramento = "expirada"
        return None
    return linha


def confirmar_segundo_fator(
    sessao: Session,
    config: Configuracao,
    cofre: Cofre,
    token_pendente: str,
    codigo: str,
    instante: float | None = None,
) -> SessaoAberta:
    """Segunda etapa: confere o TOTP e troca a sessão pendente por uma ativa (token novo)."""
    pendente = _sessao_valida(sessao, token_pendente, ETAPAS_PENDENTES)
    if pendente is None:
        raise ErroAcesso("Etapa de autenticação inválida ou expirada.", "sessao_pendente_invalida")
    # Trava a conta (PostgreSQL): dois pedidos simultâneos com o mesmo código não passam os dois.
    conta = (
        sessao.execute(select(Conta).where(Conta.id == pendente.conta_id).with_for_update())
        .scalars()
        .one()
    )
    if not conta.ativo:
        raise ErroAcesso(MENSAGEM_CREDENCIAIS, "conta_inativa")
    if _bloqueada(conta):
        raise ErroAcesso(MENSAGEM_BLOQUEIO, "conta_bloqueada", 429)
    segredo = _ler_segredo(cofre, conta)
    passo = None
    if segredo is not None:
        passo = totp.verificar(
            segredo, codigo, instante=instante, ultimo_passo=conta.mfa_ultimo_passo
        )
    if passo is None:
        if _registrar_falha(config, conta):
            pendente.encerrada_em = iso_utc()
            pendente.motivo_encerramento = "bloqueio"
            raise ErroAcesso(MENSAGEM_BLOQUEIO, "mfa_incorreto_conta_bloqueada", 429)
        raise ErroAcesso("Código de verificação inválido.", "mfa_incorreto")
    conta.mfa_ultimo_passo = passo
    conta.falhas_seguidas = 0
    conta.bloqueado_ate = None
    if pendente.etapa == "cadastro_mfa":
        conta.mfa_ativo = True
        conta.mfa_confirmado_em = iso_utc()
    pendente.encerrada_em = iso_utc()
    pendente.motivo_encerramento = "segundo_fator_confirmado"
    token, linha = _abrir_sessao(sessao, conta, "ativa", config.sessao_duracao_minutos)
    return SessaoAberta(token, linha, conta)


def sessao_ativa(sessao: Session, config: Configuracao, token: str) -> Sessao | None:
    """A sessão ativa do token, ou ``None``. Atualiza a marca de atividade e aplica a expiração
    por inatividade. Quem chama confirma a transação."""
    linha = _sessao_valida(sessao, token, ("ativa",))
    if linha is None:
        return None
    agora = _agora()
    ultima = _data(linha.ultima_atividade_em) or agora
    if agora - ultima > timedelta(minutes=config.sessao_inatividade_minutos):
        linha.encerrada_em = iso_utc()
        linha.motivo_encerramento = "inatividade"
        return None
    if not linha.conta.ativo:
        linha.encerrada_em = iso_utc()
        linha.motivo_encerramento = "conta_inativa"
        return None
    if agora - ultima > _INTERVALO_ATIVIDADE:
        linha.ultima_atividade_em = iso_utc(agora)
    return linha


def encerrar(linha: Sessao, motivo: str = "logout") -> None:
    if linha.encerrada_em is None:
        linha.encerrada_em = iso_utc()
        linha.motivo_encerramento = motivo
