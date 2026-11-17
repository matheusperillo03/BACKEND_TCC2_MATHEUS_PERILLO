"""Serviço da importação sob ordem judicial (RF10; desenho em docs/lavagem/proposta.md, seção 3).

O que este protótipo faz:

- **módulo separado e desligado por padrão** (``IMPORTACAO_JUDICIAL_ATIVA``);
- **ato de entrada obrigatório**: a decisão judicial (processo, decisão, juízo, data, período,
  contas e escopo) é registrada antes de qualquer arquivo; a importação só existe ligada a ela;
- **validação pelo leiaute oficial** (``leiaute``): a linha fora do leiaute é rejeitada, com
  relatório (linha, campo e motivo, nunca o conteúdo);
- **conferência do escopo**: o que estiver fora das contas ou do período autorizados fica em
  QUARENTENA, sinalizado;
- **segregação**: base própria (outra URL, outros metadados), chave de cifra própria (AES-256-GCM
  com subchaves por HKDF, como nas capturas) e a marca "sigiloso: ordem judicial nº ..." em todo
  registro. Nada disto vira entidade, aresta, alerta, relatório ou exportação;
- **custódia** (RF07): SHA-256, tamanho, origem e carimbo de tempo de cada arquivo recebido, com
  os bytes cifrados em disco.

SÓ DADOS SINTÉTICOS: a pasta precisa ter o arquivo-marcador ``SINTETICO``; sem ele, a importação
é recusada. Dado real nunca foi usado nem deve ser usado neste protótipo.

Fora do protótipo (trabalho futuro): camada do grafo, alertas financeiros, RIF, leiaute anterior
(Carta-Circular nº 3.454/2010), descarte ao fim da retenção e exibição do CPF completo (a leitura
devolve o CPF sempre descaracterizado, como no restante do sistema).
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from buscador import cripto
from buscador.db import criar_engine
from buscador.importacao_judicial import leiaute
from buscador.importacao_judicial.modelos import (
    ArquivoRecebido,
    BaseSigilosa,
    DecisaoJudicial,
    Importacao,
    RegistroSigiloso,
)
from buscador.privacidade import descaracterizar_texto
from buscador.resolucao.normalizacao import mascarar_cpf, somente_digitos

MARCADOR_SINTETICO = "SINTETICO"
ORIGENS = ("simba", "oficio", "midia")
MAX_REJEICOES_NO_RELATORIO = 50
CONTEXTO_ESCOPO = "importacao-judicial/escopo"
CONTEXTO_REGISTRO = "importacao-judicial/registro"
CONTEXTO_RELATORIO = "importacao-judicial/relatorio"
CONTEXTO_ARQUIVO = "importacao-judicial/arquivo"
_ERRO_DIRETORIO = (
    "Diretório não encontrado dentro da raiz de importação (IMPORTACAO_JUDICIAL_DIR_BASE). "
    "Informe um caminho relativo a essa raiz."
)


class ErroImportacao(Exception):
    """Importação recusada (pasta inválida, sem marcador sintético, sem arquivo do leiaute)."""


def _conta(banco: object, agencia: object, conta: object) -> tuple[str, str, str]:
    """Chave de comparação de conta: sem zeros à esquerda e sem pontuação."""
    numero = "".join(c for c in str(conta or "").upper() if c.isalnum())
    return (
        somente_digitos(str(banco or "")).lstrip("0"),
        somente_digitos(str(agencia or "")).lstrip("0"),
        numero.lstrip("0"),
    )


def documento_para_exibir(valor: object) -> str:
    """CNPJ inteiro; CPF sempre descaracterizado (RNF04)."""
    digitos = somente_digitos(str(valor or ""))
    return mascarar_cpf(digitos) if len(digitos) == 11 else str(valor or "")


def campos_para_exibir(campos: dict) -> dict:
    """Campos de um registro como saem na API: CPF descaracterizado, inclusive em texto livre e
    em chave Pix."""
    saida = {}
    for nome, valor in campos.items():
        if nome in leiaute.DOCUMENTOS:
            saida[nome] = documento_para_exibir(valor)
        elif isinstance(valor, str) and valor:
            digitos = somente_digitos(valor)
            saida[nome] = (
                mascarar_cpf(digitos)
                if nome.startswith("CHAVE_PIX") and len(digitos) == 11 and digitos == valor
                else descaracterizar_texto(valor)
            )
        else:
            saida[nome] = valor
    return saida


@dataclass(frozen=True)
class Escopo:
    contas: frozenset[tuple[str, str, str]]
    inicio: date
    fim: date

    def agencias(self) -> frozenset[tuple[str, str]]:
        return frozenset((banco, agencia) for banco, agencia, _ in self.contas)


class Modulo:
    """Base e cofre próprios do dado sob ordem judicial. Uma instância por serviço."""

    def __init__(
        self,
        database_url: str,
        chave: bytes,
        dados_dir: Path,
        dir_base: Path | None,
        max_bytes_arquivo: int = 16 * 1024 * 1024,
        max_linhas: int = 200_000,
    ) -> None:
        self.cofre = cripto.Cofre(chave)
        self.engine = criar_engine(database_url)
        BaseSigilosa.metadata.create_all(self.engine)
        self.fabrica = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.arquivos_dir = Path(dados_dir) / "sigiloso" / "arquivos"
        self.arquivos_dir.mkdir(parents=True, exist_ok=True)
        self.dir_base = Path(dir_base) if dir_base is not None else None
        self.max_bytes_arquivo = max_bytes_arquivo
        self.max_linhas = max_linhas

    def sessao(self) -> Session:
        return self.fabrica()

    # ---- Cifra --------------------------------------------------------------------------------

    def _cifrar_json(self, contexto: str, dados: object, associado: str) -> bytes:
        texto = json.dumps(dados, ensure_ascii=False, sort_keys=True).encode("utf-8")
        return self.cofre.cifrar(contexto, texto, associado.encode("ascii"))

    def _decifrar_json(self, contexto: str, blob: bytes, associado: str):
        return json.loads(self.cofre.decifrar(contexto, blob, associado.encode("ascii")))

    # ---- Decisão judicial (ato de entrada) ----------------------------------------------------

    @staticmethod
    def marca(decisao: DecisaoJudicial) -> str:
        return (
            f"sigiloso: ordem judicial nº {decisao.numero_decisao} "
            f"(processo {decisao.numero_processo})"
        )

    def registrar_decisao(
        self, sessao: Session, *, caso_id: str, usuario: str, dados: dict
    ) -> DecisaoJudicial:
        decisao = DecisaoJudicial(
            caso_id=caso_id,
            numero_processo=dados["numero_processo"],
            numero_decisao=dados["numero_decisao"],
            juizo=dados["juizo"],
            data_decisao=dados["data_decisao"],
            periodo_inicio=dados["periodo_inicio"],
            periodo_fim=dados["periodo_fim"],
            escopo_cifrado=b"",
            registrado_por=usuario,
        )
        sessao.add(decisao)
        sessao.flush()
        decisao.escopo_cifrado = self._cifrar_json(
            CONTEXTO_ESCOPO,
            {
                "contas": dados["contas"],
                "investigados": dados.get("investigados") or [],
                "descricao_escopo": dados["descricao_escopo"],
            },
            decisao.id,
        )
        sessao.flush()
        return decisao

    def obter_decisao(self, sessao: Session, caso_id: str, decisao_id: str) -> DecisaoJudicial:
        decisao = sessao.get(DecisaoJudicial, decisao_id) if len(decisao_id) <= 64 else None
        if decisao is None or decisao.caso_id != caso_id:
            raise LookupError("Decisão judicial não encontrada neste caso.")
        return decisao

    def listar_decisoes(self, sessao: Session, caso_id: str) -> list[DecisaoJudicial]:
        return list(
            sessao.execute(
                select(DecisaoJudicial)
                .where(DecisaoJudicial.caso_id == caso_id)
                .order_by(DecisaoJudicial.registrado_em, DecisaoJudicial.id)
            ).scalars()
        )

    def decisao_para_dict(self, sessao: Session, decisao: DecisaoJudicial) -> dict:
        escopo = self._decifrar_json(CONTEXTO_ESCOPO, decisao.escopo_cifrado, decisao.id)
        importacoes = sessao.execute(
            select(func.count()).select_from(Importacao).where(Importacao.decisao_id == decisao.id)
        ).scalar_one()
        return {
            "id": decisao.id,
            "caso_id": decisao.caso_id,
            "numero_processo": decisao.numero_processo,
            "numero_decisao": decisao.numero_decisao,
            "juizo": decisao.juizo,
            "data_decisao": decisao.data_decisao,
            "periodo_inicio": decisao.periodo_inicio,
            "periodo_fim": decisao.periodo_fim,
            "contas": escopo["contas"],
            "investigados": [documento_para_exibir(d) for d in escopo["investigados"]],
            "descricao_escopo": escopo["descricao_escopo"],
            "registrado_por": decisao.registrado_por,
            "registrado_em": decisao.registrado_em,
            "marca": self.marca(decisao),
            "importacoes": importacoes,
        }

    def _escopo(self, decisao: DecisaoJudicial) -> Escopo:
        escopo = self._decifrar_json(CONTEXTO_ESCOPO, decisao.escopo_cifrado, decisao.id)
        return Escopo(
            contas=frozenset(
                _conta(c["banco"], c["agencia"], c["conta"]) for c in escopo["contas"]
            ),
            inicio=date.fromisoformat(decisao.periodo_inicio),
            fim=date.fromisoformat(decisao.periodo_fim),
        )

    # ---- Importação ---------------------------------------------------------------------------

    def _resolver_pasta(self, diretorio: str) -> Path:
        if self.dir_base is None:
            raise ErroImportacao(
                "A importação está sem raiz configurada: o operador precisa definir "
                "IMPORTACAO_JUDICIAL_DIR_BASE."
            )
        try:
            raiz = self.dir_base.resolve(strict=True)
            pasta = (raiz / diretorio).resolve(strict=True)
        except (OSError, RuntimeError, ValueError) as erro:
            raise ErroImportacao(_ERRO_DIRETORIO) from erro
        if not pasta.is_relative_to(raiz) or not pasta.is_dir():
            raise ErroImportacao(_ERRO_DIRETORIO)
        return pasta

    def _ler_pasta(self, pasta: Path) -> dict[str, tuple[str, bytes]]:
        """tipo -> (nome, bytes) dos arquivos do leiaute; exige o marcador ``SINTETICO``."""
        sintetico = False
        arquivos: dict[str, tuple[str, bytes]] = {}
        try:
            entradas = sorted(os.scandir(pasta), key=lambda e: e.name)
        except OSError as erro:
            raise ErroImportacao("Não foi possível ler o diretório informado.") from erro
        for entrada in entradas:
            if Path(entrada.name).stem.upper() == MARCADOR_SINTETICO:
                sintetico = True
                continue
            tipo = leiaute.classificar_arquivo(entrada.name)
            # Links simbólicos não são seguidos; só arquivo regular.
            if tipo is None or entrada.is_symlink() or not entrada.is_file(follow_symlinks=False):
                continue
            if tipo in arquivos:
                raise ErroImportacao(f"Há mais de um arquivo {tipo} no diretório.")
            if entrada.stat(follow_symlinks=False).st_size > self.max_bytes_arquivo:
                raise ErroImportacao(
                    f"O arquivo {entrada.name} passa do limite de {self.max_bytes_arquivo} bytes "
                    "(IMPORTACAO_JUDICIAL_MAX_BYTES_ARQUIVO)."
                )
            try:
                descritor = os.open(entrada.path, os.O_RDONLY | os.O_NOFOLLOW)
                with os.fdopen(descritor, "rb") as arquivo:
                    conteudo = arquivo.read(self.max_bytes_arquivo + 1)
            except OSError as erro:
                raise ErroImportacao(f"Não foi possível ler o arquivo {entrada.name}.") from erro
            if len(conteudo) > self.max_bytes_arquivo:
                raise ErroImportacao(f"O arquivo {entrada.name} passa do limite de bytes.")
            arquivos[tipo] = (entrada.name, conteudo)
        if not sintetico:
            raise ErroImportacao(
                "Importação recusada: o diretório não tem o arquivo-marcador SINTETICO. Este "
                "protótipo só aceita dados sintéticos; dado real sob sigilo não pode ser usado."
            )
        if not arquivos:
            raise ErroImportacao(
                "Nenhum arquivo do leiaute no diretório (esperados AGENCIAS, CONTAS, TITULARES, "
                "EXTRATO e ORIGEM-DESTINO, com extensão .txt)."
            )
        return arquivos

    def _guardar_arquivo(self, conteudo: bytes) -> str:
        """Bytes do arquivo recebido, cifrados com a chave do módulo; devolve o SHA-256."""
        digest = hashlib.sha256(conteudo).hexdigest()
        destino = self.arquivos_dir / f"{digest}.gcm"
        if not destino.exists():
            temporario = destino.with_name(f".parcial-{os.urandom(8).hex()}")
            temporario.write_bytes(
                self.cofre.cifrar(CONTEXTO_ARQUIVO, conteudo, digest.encode("ascii"))
            )
            os.replace(temporario, destino)
        return digest

    def ler_arquivo(self, sha256: str) -> bytes:
        blob = (self.arquivos_dir / f"{sha256}.gcm").read_bytes()
        return self.cofre.decifrar(CONTEXTO_ARQUIVO, blob, sha256.encode("ascii"))

    def importar(
        self,
        sessao: Session,
        *,
        decisao: DecisaoJudicial,
        usuario: str,
        diretorio: str,
        origem: str,
        descricao_origem: str = "",
    ) -> Importacao:
        if origem not in ORIGENS:
            raise ErroImportacao(f"Origem desconhecida (aceitas: {', '.join(ORIGENS)}).")
        arquivos = self._ler_pasta(self._resolver_pasta(diretorio))
        escopo = self._escopo(decisao)
        importacao = Importacao(
            decisao_id=decisao.id,
            caso_id=decisao.caso_id,
            origem=origem,
            descricao_origem=descricao_origem,
            importado_por=usuario,
        )
        sessao.add(importacao)
        sessao.flush()
        # Contas de contraparte declaradas no arquivo CONTAS (MOVIMENTACAO_CONTA = 3: "conta da
        # mesma instituição financeira que efetuou transação bancária com uma conta investigada").
        contrapartes: set[tuple[str, str, str]] = set()
        # CODIGO_CHAVE_EXTRATO -> em quarentena? (para ligar o ORIGEM-DESTINO ao lançamento)
        lancamentos: dict[str, bool] = {}
        total = 0
        for tipo in leiaute.TIPOS:
            if tipo not in arquivos:
                continue
            nome, conteudo = arquivos[tipo]
            registro_arquivo = ArquivoRecebido(
                importacao_id=importacao.id,
                tipo=tipo,
                nome_arquivo=nome,
                sha256=self._guardar_arquivo(conteudo),
                tamanho_bytes=len(conteudo),
                rejeicoes_cifradas=b"",
            )
            sessao.add(registro_arquivo)
            sessao.flush()
            rejeicoes: list[leiaute.Rejeicao] = []
            for numero, bruta in enumerate(conteudo.splitlines(), start=1):
                if not bruta.strip():
                    continue
                total += 1
                if total > self.max_linhas:
                    raise ErroImportacao(
                        f"A importação passa do limite de {self.max_linhas} linhas "
                        "(IMPORTACAO_JUDICIAL_MAX_LINHAS)."
                    )
                registro_arquivo.linhas += 1
                lido = leiaute.interpretar_linha(tipo, numero, bruta)
                if isinstance(lido, leiaute.Rejeicao):
                    registro_arquivo.rejeitadas += 1
                    rejeicoes.append(lido)
                    continue
                motivo = self._fora_do_escopo(tipo, lido, escopo, contrapartes, lancamentos)
                linha = RegistroSigiloso(
                    importacao_id=importacao.id,
                    decisao_id=decisao.id,
                    caso_id=decisao.caso_id,
                    tipo=tipo,
                    linha=numero,
                    quarentena=bool(motivo),
                    motivo_quarentena=motivo,
                    campos_cifrados=b"",
                )
                sessao.add(linha)
                sessao.flush()
                linha.campos_cifrados = self._cifrar_json(CONTEXTO_REGISTRO, lido, linha.id)
                registro_arquivo.aceitas += 1
                registro_arquivo.em_quarentena += bool(motivo)
            registro_arquivo.rejeicoes_cifradas = self._cifrar_json(
                CONTEXTO_RELATORIO,
                [
                    {"linha": r.linha, "campo": r.campo, "motivo": r.motivo}
                    for r in rejeicoes[:MAX_REJEICOES_NO_RELATORIO]
                ],
                registro_arquivo.id,
            )
        sessao.flush()
        return importacao

    @staticmethod
    def _fora_do_escopo(
        tipo: str,
        campos: dict,
        escopo: Escopo,
        contrapartes: set[tuple[str, str, str]],
        lancamentos: dict[str, bool],
    ) -> str:
        """Motivo da quarentena, ou ``""`` se a linha está dentro do que a decisão autorizou."""
        if tipo == leiaute.ORIGEM_DESTINO:
            chave = str(campos.get("CODIGO_CHAVE_EXTRATO") or "")
            if chave not in lancamentos:
                return "lançamento de origem não encontrado no EXTRATO desta importação"
            return "o lançamento de origem está em quarentena" if lancamentos[chave] else ""
        banco, agencia = campos.get("COD_COMP_INSTITUIÇÃO"), campos.get("NUMERO_AGÊNCIA")
        if tipo == leiaute.AGENCIAS:
            dentro = _conta(banco, agencia, "")[:2] in escopo.agencias()
            return "" if dentro else "agência sem conta autorizada na decisão"
        conta = _conta(banco, agencia, campos.get("NUMERO_CONTA"))
        motivo = ""
        if conta not in escopo.contas:
            if tipo == leiaute.CONTAS and campos.get("MOVIMENTACAO_CONTA") == "3":
                contrapartes.add(conta)
            elif not (tipo == leiaute.TITULARES and conta in contrapartes):
                motivo = "conta fora das autorizadas na decisão"
        if tipo == leiaute.EXTRATO:
            dia = date.fromisoformat(str(campos["DATA_LANCAMENTO"]))
            if not motivo and not escopo.inicio <= dia <= escopo.fim:
                motivo = "lançamento fora do período autorizado na decisão"
            lancamentos[str(campos["CODIGO_CHAVE_EXTRATO"])] = bool(motivo)
        return motivo

    # ---- Leitura ------------------------------------------------------------------------------

    def listar_importacoes(self, sessao: Session, caso_id: str) -> list[Importacao]:
        return list(
            sessao.execute(
                select(Importacao)
                .where(Importacao.caso_id == caso_id)
                .order_by(Importacao.importado_em, Importacao.id)
            ).scalars()
        )

    def importacao_para_dict(self, sessao: Session, importacao: Importacao) -> dict:
        decisao = sessao.get(DecisaoJudicial, importacao.decisao_id)
        arquivos = []
        ordem = {tipo: n for n, tipo in enumerate(leiaute.TIPOS)}
        for arquivo in sorted(
            sessao.execute(
                select(ArquivoRecebido).where(ArquivoRecebido.importacao_id == importacao.id)
            ).scalars(),
            key=lambda a: ordem.get(a.tipo, 99),
        ):
            arquivos.append(
                {
                    "tipo": arquivo.tipo,
                    "nome_arquivo": arquivo.nome_arquivo,
                    "sha256": arquivo.sha256,
                    "tamanho_bytes": arquivo.tamanho_bytes,
                    "linhas": arquivo.linhas,
                    "aceitas": arquivo.aceitas,
                    "rejeitadas": arquivo.rejeitadas,
                    "em_quarentena": arquivo.em_quarentena,
                    "rejeicoes": self._decifrar_json(
                        CONTEXTO_RELATORIO, arquivo.rejeicoes_cifradas, arquivo.id
                    ),
                }
            )
        return {
            "id": importacao.id,
            "decisao_id": importacao.decisao_id,
            "caso_id": importacao.caso_id,
            "origem": importacao.origem,
            "descricao_origem": importacao.descricao_origem,
            "importado_por": importacao.importado_por,
            "importado_em": importacao.importado_em,
            "marca": self.marca(decisao) if decisao is not None else "",
            "arquivos": arquivos,
            "totais": {
                chave: sum(a[chave] for a in arquivos)
                for chave in ("linhas", "aceitas", "rejeitadas", "em_quarentena")
            },
        }

    def listar_registros(
        self,
        sessao: Session,
        caso_id: str,
        *,
        decisao_id: str | None = None,
        tipo: str | None = None,
        quarentena: bool | None = None,
        limite: int = 50,
        deslocamento: int = 0,
    ) -> dict:
        filtros = [RegistroSigiloso.caso_id == caso_id]
        if decisao_id:
            filtros.append(RegistroSigiloso.decisao_id == decisao_id)
        if tipo:
            filtros.append(RegistroSigiloso.tipo == tipo)
        if quarentena is not None:
            filtros.append(RegistroSigiloso.quarentena.is_(quarentena))
        total = sessao.execute(
            select(func.count()).select_from(RegistroSigiloso).where(*filtros)
        ).scalar_one()
        linhas = sessao.execute(
            select(RegistroSigiloso)
            .where(*filtros)
            .order_by(
                RegistroSigiloso.importacao_id,
                RegistroSigiloso.tipo,
                RegistroSigiloso.linha,
                RegistroSigiloso.id,
            )
            .limit(limite)
            .offset(deslocamento)
        ).scalars()
        marcas: dict[str, str] = {}
        itens = []
        for linha in linhas:
            if linha.decisao_id not in marcas:
                decisao = sessao.get(DecisaoJudicial, linha.decisao_id)
                marcas[linha.decisao_id] = self.marca(decisao) if decisao is not None else ""
            campos = self._decifrar_json(CONTEXTO_REGISTRO, linha.campos_cifrados, linha.id)
            itens.append(
                {
                    "id": linha.id,
                    "importacao_id": linha.importacao_id,
                    "decisao_id": linha.decisao_id,
                    "tipo": linha.tipo,
                    "linha": linha.linha,
                    "quarentena": bool(linha.quarentena),
                    "motivo_quarentena": linha.motivo_quarentena,
                    "marca": marcas[linha.decisao_id],
                    "campos": campos_para_exibir(campos),
                }
            )
        return {"total": total, "itens": itens}
