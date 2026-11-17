"""Trilha de auditoria (RNF02): cadeia somente de acréscimo, autenticada por HMAC e ancorada.

Versão 2 da cadeia (decisão registrada no README, seção "Auditoria"):

- ``hash = HMAC-SHA256(chave, json_canonico(entrada_sem_hash))``. A entrada inclui ``versao`` e
  ``hash_anterior``; a chave vem de ``AUDITORIA_CHAVE_HMAC`` e nunca é gravada no banco. Quem só
  tem a credencial do banco não consegue recalcular hashes válidos depois de alterar a história.
- O JSON canônico é ASCII puro (``ensure_ascii=True``) e recusa ``NaN``/``Infinity``
  (``allow_nan=False``).
- O topo ``(seq, hash)`` é copiado para um arquivo fora do banco (``Ancora``) depois de cada
  confirmação; ``verificar`` compara a cadeia com essa âncora e assim detecta o corte do fim.
- A versão 1 (SHA-256 sem chave) não é aceita: não há como autenticar entradas sem chave.
"""

from __future__ import annotations

import fcntl
import hashlib
import hmac
import json
import logging
import os
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import event, func, select, text
from sqlalchemy.orm import Session

from buscador.modelos import Auditoria, iso_utc
from buscador.privacidade import descaracterizar
from buscador.seguranca import resumo_sha256

registro = logging.getLogger(__name__)

VERSAO_CADEIA = 2
HASH_GENESE = "0" * 64

CAMPOS_HASH = (
    "versao",
    "seq",
    "quando",
    "usuario",
    "acao",
    "caso_id",
    "justificativa",
    "detalhes",
    "hash_anterior",
)

# Limites do que a auditoria guarda de valores vindos do cliente ou de fontes externas.
MAX_TEXTO_DETALHE = 500
MAX_ITENS_DETALHE = 200
MAX_DETALHES_BYTES = 8_192
MAX_JUSTIFICATIVA = 500
LOTE_VERIFICACAO = 500

# Chave do pg_advisory_xact_lock que serializa os escritores (dispensa privilégio de LOCK TABLE).
_TRAVA_POSTGRES = 0x0A0D1702


def json_canonico(obj: object) -> str:
    """Serialização determinística: chaves ordenadas, sem espaços, ASCII, sem NaN/Infinity."""
    return json.dumps(
        obj, sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False
    )


def calcular_hash(entrada: dict, chave: bytes) -> str:
    """hash = HMAC-SHA256(chave, json_canonico(entrada_sem_hash)); inclui versão e hash anterior."""
    sem_hash = {campo: entrada.get(campo) for campo in CAMPOS_HASH}
    return hmac.new(chave, json_canonico(sem_hash).encode("ascii"), hashlib.sha256).hexdigest()


# ---- Limite de tamanho dos detalhes -----------------------------------------------------------


def _limitar(valor: object, profundidade: int = 0) -> object:
    if isinstance(valor, str):
        if len(valor) <= MAX_TEXTO_DETALHE:
            return valor
        return {
            "truncado": True,
            "tamanho": len(valor),
            "sha256": resumo_sha256(valor),
            "inicio": valor[:120],
        }
    if profundidade >= 6:
        return {"truncado": True, "motivo": "profundidade"}
    if isinstance(valor, dict):
        itens = list(valor.items())
        limitado = {
            str(k)[:100]: _limitar(v, profundidade + 1) for k, v in itens[:MAX_ITENS_DETALHE]
        }
        if len(itens) > MAX_ITENS_DETALHE:
            limitado["__truncado__"] = {"chaves": len(itens)}
        return limitado
    if isinstance(valor, list | tuple):
        limitado = [_limitar(v, profundidade + 1) for v in valor[:MAX_ITENS_DETALHE]]
        if len(valor) > MAX_ITENS_DETALHE:
            limitado.append({"truncado": True, "itens": len(valor)})
        return limitado
    return valor


def limitar_detalhes(detalhes: dict | None) -> dict:
    """Detalhes com tamanho limitado: valores grandes viram resumo (tamanho e SHA-256).

    Levanta ``ValueError`` se houver ``NaN``/``Infinity`` ou valor não serializável: nada que o
    JSON estrito não represente entra na trilha.
    """
    limitado = _limitar(detalhes or {})
    canonico = json_canonico(limitado)
    if len(canonico) > MAX_DETALHES_BYTES:
        resumo: dict = {
            "truncado": True,
            "tamanho_bytes": len(canonico),
            "sha256": resumo_sha256(canonico),
        }
        if isinstance(limitado, dict) and isinstance(limitado.get("perfil"), str):
            resumo["perfil"] = limitado["perfil"]
        return resumo
    # O que vai para o banco é exatamente o que foi serializado para o hash.
    return json.loads(canonico)


# ---- Âncora externa ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PontoAncora:
    seq: int
    hash: str
    atualizado_em: str


class AncoraInvalida(Exception):
    """O arquivo da âncora existe, mas não pôde ser lido ou não confere com a chave."""


class Ancora:
    """Cópia do topo ``(seq, hash)`` da cadeia em ``<DADOS_DIR>/ancora/auditoria.json``.

    Fica fora do banco: quem apaga as últimas entradas com a credencial do banco não alcança este
    arquivo. O registro leva um MAC com a chave da auditoria e só avança (``seq`` crescente).
    """

    def __init__(self, dados_dir: Path, chave: bytes) -> None:
        self.pasta = Path(dados_dir) / "ancora"
        self.pasta.mkdir(parents=True, exist_ok=True)
        self.arquivo = self.pasta / "auditoria.json"
        self._chave = chave
        self._trava = threading.Lock()

    def _mac(self, seq: int, hash_: str) -> str:
        base = f"ancora|{VERSAO_CADEIA}|{seq}|{hash_}".encode("ascii")
        return hmac.new(self._chave, base, hashlib.sha256).hexdigest()

    def ler(self) -> PontoAncora | None:
        try:
            bruto = self.arquivo.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except OSError as erro:
            raise AncoraInvalida("não foi possível ler o arquivo da âncora") from erro
        try:
            dados = json.loads(bruto)
            seq, hash_ = int(dados["seq"]), str(dados["hash"])
            mac, quando = str(dados["mac"]), str(dados.get("atualizado_em", ""))
        except (ValueError, KeyError, TypeError) as erro:
            raise AncoraInvalida("o arquivo da âncora está corrompido") from erro
        if not hmac.compare_digest(mac, self._mac(seq, hash_)):
            raise AncoraInvalida("o MAC da âncora não confere com a chave da auditoria")
        return PontoAncora(seq=seq, hash=hash_, atualizado_em=quando)

    def avancar(self, seq: int, hash_: str) -> bool:
        """Grava o novo topo se ele for posterior ao registrado. Devolve se gravou."""
        with self._trava, open(self.pasta / ".trava", "w") as trava:
            fcntl.flock(trava, fcntl.LOCK_EX)
            try:
                atual = self.ler()
            except AncoraInvalida:
                # Não sobrescreve uma âncora que não confere: a verificação precisa enxergá-la.
                registro.warning("Âncora da auditoria inválida; o topo não foi atualizado.")
                return False
            if atual is not None and atual.seq >= seq:
                return False
            conteudo = json.dumps(
                {
                    "versao": VERSAO_CADEIA,
                    "seq": seq,
                    "hash": hash_,
                    "atualizado_em": iso_utc(),
                    "mac": self._mac(seq, hash_),
                }
            )
            descritor, temporario = tempfile.mkstemp(dir=self.pasta, prefix=".ancora-")
            try:
                with os.fdopen(descritor, "w", encoding="utf-8") as arquivo:
                    arquivo.write(conteudo)
                    arquivo.flush()
                    os.fsync(arquivo.fileno())
                os.replace(temporario, self.arquivo)
            except BaseException:
                Path(temporario).unlink(missing_ok=True)
                raise
            return True


# ---- Registro e leitura -----------------------------------------------------------------------


def _entrada_para_dict(linha) -> dict:
    return {
        "versao": linha.versao,
        "seq": linha.seq,
        "quando": linha.quando,
        "usuario": linha.usuario,
        "acao": linha.acao,
        "caso_id": linha.caso_id,
        "justificativa": linha.justificativa,
        "detalhes": linha.detalhes or {},
        "hash_anterior": linha.hash_anterior,
        "hash": linha.hash,
    }


def registrar(
    sessao: Session,
    chave: bytes,
    *,
    usuario: str,
    acao: str,
    caso_id: str | None,
    justificativa: str | None,
    detalhes: dict | None = None,
) -> Auditoria:
    """Acrescenta uma entrada encadeada à anterior. Deve rodar na mesma transação da ação.

    No PostgreSQL os escritores são serializados por uma trava consultiva de transação, liberada
    na confirmação; por isso as rotas chamam esta função só depois de terminar o trabalho.
    """
    if justificativa is not None and len(justificativa) > MAX_JUSTIFICATIVA:
        raise ValueError(f"justificativa acima de {MAX_JUSTIFICATIVA} caracteres")
    # RNF04: nenhum CPF completo entra na trilha (os detalhes vêm de parâmetros, termos de busca
    # e mensagens de erro). A justificativa é texto do próprio usuário e fica como foi digitada.
    detalhes_limitados = limitar_detalhes(descaracterizar(detalhes or {}))
    if sessao.bind is not None and sessao.bind.dialect.name == "postgresql":
        sessao.execute(text("SELECT pg_advisory_xact_lock(:chave)"), {"chave": _TRAVA_POSTGRES})
    ultima = sessao.execute(
        select(Auditoria.seq, Auditoria.hash).order_by(Auditoria.seq.desc()).limit(1)
    ).first()
    seq = (ultima.seq + 1) if ultima else 1
    hash_anterior = ultima.hash if ultima else HASH_GENESE
    entrada = {
        "versao": VERSAO_CADEIA,
        "seq": seq,
        "quando": iso_utc(),
        "usuario": usuario,
        "acao": acao,
        "caso_id": caso_id,
        "justificativa": justificativa,
        "detalhes": detalhes_limitados,
        "hash_anterior": hash_anterior,
    }
    linha = Auditoria(**entrada, hash=calcular_hash(entrada, chave))
    sessao.add(linha)
    sessao.flush()
    return linha


def contar(sessao: Session, caso_id: str | None = None) -> int:
    consulta = select(func.count(Auditoria.seq))
    if caso_id:
        consulta = consulta.where(Auditoria.caso_id == caso_id)
    return sessao.execute(consulta).scalar_one()


def listar(
    sessao: Session,
    caso_id: str | None = None,
    *,
    limite: int | None = None,
    deslocamento: int = 0,
    ultimas: bool = False,
) -> list[dict]:
    """Entradas em ordem de ``seq``. Com ``ultimas``, as ``limite`` mais recentes."""
    consulta = select(Auditoria)
    if caso_id:
        consulta = consulta.where(Auditoria.caso_id == caso_id)
    if ultimas:
        consulta = consulta.order_by(Auditoria.seq.desc())
    else:
        consulta = consulta.order_by(Auditoria.seq).offset(deslocamento)
    if limite is not None:
        consulta = consulta.limit(limite)
    entradas = [_entrada_para_dict(linha) for linha in sessao.execute(consulta).scalars()]
    if ultimas:
        entradas.reverse()
    return entradas


# ---- Verificação ------------------------------------------------------------------------------


@dataclass(frozen=True)
class ResultadoCadeia:
    integra: bool
    entradas: int
    primeira_quebra: int | None
    motivo: str | None = None
    ancora_seq: int | None = None


def verificar(sessao: Session, chave: bytes, ancora: Ancora | None = None) -> ResultadoCadeia:
    """Percorre a cadeia em lotes e informa a primeira entrada que não confere.

    Confere a sequência, o encadeamento, o HMAC de cada entrada e, quando há âncora externa, se o
    topo do banco alcança o ponto ancorado com o mesmo hash (detecta corte e reescrita do fim).
    """
    total = contar(sessao)
    ponto: PontoAncora | None = None
    erro_ancora: str | None = None
    if ancora is not None:
        try:
            ponto = ancora.ler()
        except AncoraInvalida as erro:
            erro_ancora = f"âncora externa inválida: {erro}"
    ancora_seq = ponto.seq if ponto else None

    def quebra(seq: int | None, motivo: str) -> ResultadoCadeia:
        return ResultadoCadeia(False, total, seq, motivo, ancora_seq)

    colunas = select(
        Auditoria.versao,
        Auditoria.seq,
        Auditoria.quando,
        Auditoria.usuario,
        Auditoria.acao,
        Auditoria.caso_id,
        Auditoria.justificativa,
        Auditoria.detalhes,
        Auditoria.hash_anterior,
        Auditoria.hash,
    ).order_by(Auditoria.seq)
    anterior_hash = HASH_GENESE
    esperado_seq = 1
    ultimo_seq = 0
    while True:
        # Paginação por chave: memória constante, sem depender de cursor do lado do servidor.
        lote = sessao.execute(
            colunas.where(Auditoria.seq > ultimo_seq).limit(LOTE_VERIFICACAO)
        ).all()
        if not lote:
            break
        for linha in lote:
            ultimo_seq = linha.seq
            if linha.seq != esperado_seq:
                return quebra(
                    linha.seq, f"sequência esperada {esperado_seq}, encontrada {linha.seq}"
                )
            if linha.versao != VERSAO_CADEIA:
                return quebra(
                    linha.seq,
                    f"versão de cadeia {linha.versao} não suportada (esperada {VERSAO_CADEIA})",
                )
            if linha.hash_anterior != anterior_hash:
                return quebra(
                    linha.seq, "hash_anterior não corresponde ao hash da entrada anterior"
                )
            try:
                calculado = calcular_hash(_entrada_para_dict(linha), chave)
            except (TypeError, ValueError):
                return quebra(linha.seq, "conteúdo da entrada não é JSON canônico válido")
            if not hmac.compare_digest(calculado, linha.hash):
                return quebra(linha.seq, "hash da entrada não confere com o conteúdo")
            if ponto is not None and linha.seq == ponto.seq and linha.hash != ponto.hash:
                return quebra(linha.seq, "hash da entrada difere do registrado na âncora externa")
            anterior_hash = linha.hash
            esperado_seq += 1

    if erro_ancora:
        return quebra(ultimo_seq or None, erro_ancora)
    if ancora is not None:
        if ponto is None and ultimo_seq > 0:
            return quebra(
                ultimo_seq, "âncora externa ausente: não é possível confirmar o topo da cadeia"
            )
        if ponto is not None and ponto.seq > ultimo_seq:
            return quebra(
                ultimo_seq + 1,
                f"cadeia truncada: a âncora externa registra a entrada {ponto.seq}, "
                f"mas o banco termina na entrada {ultimo_seq}",
            )
    return ResultadoCadeia(True, total, None, None, ancora_seq)


# Barreira no ORM: nenhum caminho da aplicação atualiza ou apaga linhas da auditoria.
@event.listens_for(Auditoria, "before_update")
def _bloquear_update(_mapper, _conexao, _alvo):  # pragma: no cover - guarda
    raise RuntimeError("A trilha de auditoria é somente de acréscimo: atualização proibida.")


@event.listens_for(Auditoria, "before_delete")
def _bloquear_delete(_mapper, _conexao, _alvo):  # pragma: no cover - guarda
    raise RuntimeError("A trilha de auditoria é somente de acréscimo: exclusão proibida.")
