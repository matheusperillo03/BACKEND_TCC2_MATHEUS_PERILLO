"""Mede a extração assistida por IA com o Ollama LOCAL, fora da aplicação.

Não usa a API, o banco, a custódia nem a auditoria: lê os PDFs de uma pasta, extrai o texto com
o mesmo extrator do serviço (com o CPF descaracterizado), divide em blocos, chama o provedor
``ProvedorOllama`` bloco a bloco e aplica a mesma verificação (``verificar_itens``). Nenhuma
sugestão é criada.

O que é gravado (pasta ``--saida``):

- ``execucoes.jsonl``: uma linha por bloco (``"registro": "bloco"``) e uma por execução
  (``"registro": "execucao"``), só com contagens, durações, *hashes* e códigos de motivo;
- ``ambiente.json``: máquina (sem número de série nem UUID), sistema, versão do Ollama,
  identidade de cada modelo, commit do back-end, SHA-256 do prompt, parâmetros e carga.

O que NUNCA é gravado nesses dois arquivos: texto do documento, trechos, valores extraídos e
saída do modelo. O nome do arquivo só entra com ``--gravar-nomes``; sem isso o documento é
identificado pelo SHA-256. A saída bruta do modelo (que contém nomes) só é guardada com
``--saidas-brutas``, numa pasta à parte.

Dados reais ficam fora do repositório: ``--saida`` e ``--saidas-brutas`` são recusadas se
apontarem para dentro do repositório e fora de ``dados-reais/`` (que o git ignora). O padrão de
``--saida`` é ``dados-reais/benchmark/<data-hora>``.

O servidor tem de ser local: só endereço de loopback é aceito. Modelo remoto é recusado pelo
próprio provedor. O script não instala modelo (``ollama pull`` fica com quem roda) e não chama
``sudo``.

Condições medidas, por modelo:

1. aquecimento (uma chamada com texto sintético de uma linha, descartada);
2. com ``--frio``: o modelo é descarregado (``keep_alive: 0``) e cada documento é medido uma vez;
3. ``--repeticoes`` execuções a quente de cada documento, em ordem embaralhada
   (``--semente-ordem``), com ``--intervalo`` segundos entre elas.

Uso (comando exato registrado em ``ambiente.json``, sem os caminhos):

  uv run python scripts/benchmark_ia.py --pdfs dados-reais/editais --modelo qwen2.5:7b --frio
  uv run python scripts/benchmark_ia.py --pdfs dados-reais/editais \\
      --modelo qwen2.5:7b=845dbda0ea48 --repeticoes 5 --num-ctx 8192 --keep-alive 30m

``--num-ctx``, ``--num-predict`` e ``--keep-alive`` só valem para este script: a aplicação não
os envia. Sem eles, o campo não vai na chamada e vale o padrão do servidor.
"""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
import platform
import random
import subprocess  # nosec B404 - só comandos fixos de leitura do ambiente, sem shell
import sys
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from pydantic import ValidationError

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from buscador.documentos import DocumentoRecusado, extrair_texto  # noqa: E402
from buscador.ia import servico as ia  # noqa: E402
from buscador.ia.ollama import ProvedorOllama  # noqa: E402
from buscador.ia.provedor import ErroProvedor  # noqa: E402
from buscador.privacidade import descaracterizar_texto  # noqa: E402
from buscador.seguranca import carregar_json_estrito  # noqa: E402

PASTA_IGNORADA = RAIZ / "dados-reais"
TEXTO_DE_AQUECIMENTO = "Documento sintético de aquecimento. Nada a extrair."
# Linhas da ficha de hardware que identificam o aparelho: não entram em ``ambiente.json``.
IDENTIFICADORES_DE_HARDWARE = ("serial", "uuid", "udid")
ESPERA_DESCARGA_SEGUNDOS = 30.0


class ErroDeUso(Exception):
    """Argumento inválido: o script para antes de ler qualquer documento."""


@dataclass
class Documento:
    sha256: str
    nome: str
    tamanho_bytes: int
    paginas: int | None
    caracteres: int
    truncado: bool
    texto: str
    blocos: list[str]


def agora_utc() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def carga_da_maquina() -> list[float] | None:
    try:
        return [round(c, 2) for c in os.getloadavg()]
    except (OSError, AttributeError):
        return None


def conferir_url_local(url: str) -> str:
    """Só servidor de loopback: o texto dos documentos não pode sair da máquina."""
    partes = urlsplit(url)
    host = (partes.hostname or "").lower()
    if partes.scheme not in ("http", "https") or not host or partes.username or partes.password:
        raise ErroDeUso("--url deve ser uma URL http(s) com host, sem usuário nem senha.")
    if host != "localhost":
        try:
            local = ipaddress.ip_address(host).is_loopback
        except ValueError:
            local = False
        if not local:
            raise ErroDeUso("--url deve apontar para o Ollama desta máquina (loopback).")
    return url.rstrip("/")


def conferir_pasta_de_saida(pasta: Path, opcao: str) -> Path:
    """Recusa pasta dentro do repositório que o git não ignora (dados reais nunca sobem)."""
    resolvida = pasta.resolve()
    if resolvida.is_relative_to(RAIZ) and not resolvida.is_relative_to(PASTA_IGNORADA):
        raise ErroDeUso(
            f"{opcao} não pode ficar dentro do repositório, a não ser em dados-reais/ "
            "(ignorada pelo git)."
        )
    return resolvida


def separar_modelo(texto: str) -> tuple[str, str]:
    """``nome`` ou ``nome=digest_esperado`` (o digest pode ser só o começo)."""
    nome, _, digest = texto.partition("=")
    if not nome.strip():
        raise ErroDeUso("--modelo precisa do nome do modelo.")
    return nome.strip(), digest.strip().lower().removeprefix("sha256:")


def ler_documentos(pasta: Path, max_caracteres: int, tamanho_bloco: int):
    """Os PDFs da pasta (sem subpastas), já em texto e em blocos, e os que não puderam ser
    lidos. O texto fica só na memória."""
    if not pasta.is_dir():
        raise ErroDeUso("--pdfs deve ser uma pasta existente.")
    lidos: list[Documento] = []
    recusados: list[dict] = []
    for caminho in sorted(p for p in pasta.iterdir() if p.is_file() and p.suffix.lower() == ".pdf"):
        bruto = caminho.read_bytes()
        resumo = hashlib.sha256(bruto).hexdigest()
        try:
            extraido = extrair_texto(bruto)
        except DocumentoRecusado as recusa:
            recusados.append({"sha256": resumo, "nome": caminho.name, "motivo": recusa.motivo})
            continue
        # Como na aplicação: todo CPF sai descaracterizado antes de ir ao modelo (RNF04).
        texto = descaracterizar_texto(extraido.texto)[:max_caracteres]
        lidos.append(
            Documento(
                sha256=resumo,
                nome=caminho.name,
                tamanho_bytes=len(bruto),
                paginas=extraido.paginas,
                caracteres=len(texto),
                truncado=len(extraido.texto) > max_caracteres,
                texto=texto,
                blocos=ia.dividir_em_blocos(texto, tamanho_bloco),
            )
        )
    return lidos, recusados


def _comando(*argumentos: str) -> str | None:
    """Saída de um comando fixo de leitura do ambiente, ou ``None`` se ele não existe."""
    try:
        resultado = subprocess.run(  # nosec B603 - lista fixa de argumentos, sem shell
            argumentos, capture_output=True, text=True, timeout=30, check=False, cwd=RAIZ
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return resultado.stdout.strip() if resultado.returncode == 0 else None


def ficha_de_hardware() -> list[str] | None:
    """``system_profiler SPHardwareDataType`` (macOS) sem número de série, UUID nem UDID."""
    saida = _comando("system_profiler", "SPHardwareDataType")
    if saida is None:
        return None
    return [
        linha.strip()
        for linha in saida.splitlines()
        if linha.strip() and not any(i in linha.lower() for i in IDENTIFICADORES_DE_HARDWARE)
    ]


def descrever_ambiente() -> dict:
    return {
        "commit_backend": _comando("git", "rev-parse", "HEAD"),
        "prompt_sha256": ia.PROMPT_SHA256,
        "python": platform.python_version(),
        "plataforma": platform.platform(),
        "hardware": ficha_de_hardware(),
        "sistema": _comando("sw_vers"),
        "extrator_pdf": _versao_do_pypdf(),
    }


def _versao_do_pypdf() -> str | None:
    try:
        from importlib.metadata import version

        return f"pypdf {version('pypdf')}"
    except Exception:  # noqa: BLE001 - a versão é só informativa
        return None


def _ms(desde: float) -> int:
    """Milissegundos de relógio monotônico decorridos desde ``desde``."""
    return round((time.monotonic() - desde) * 1000)


def _vazao(tokens: object, duracao_ns: object) -> float | None:
    """Tokens por segundo, ou ``None`` se o servidor não informou os dois valores."""
    if not isinstance(tokens, int) or not isinstance(duracao_ns, int) or duracao_ns <= 0:
        return None
    return round(tokens / (duracao_ns / 1e9), 2)


class Medidor:
    """Roda as execuções de um modelo e escreve as linhas do JSONL."""

    def __init__(
        self,
        provedor: ProvedorOllama,
        escrever: Callable[[dict], None],
        max_itens: int,
        pasta_brutas: Path | None,
        gravar_nomes: bool,
    ) -> None:
        self.provedor = provedor
        self.escrever = escrever
        self.max_itens = max_itens
        self.pasta_brutas = pasta_brutas
        self.gravar_nomes = gravar_nomes
        self.execucoes = 0
        self.falhas = 0

    def executar(self, documento: Documento, condicao: str, repeticao: int) -> dict:
        self.execucoes += 1
        identidade = self.provedor.identidade()
        comum = {
            "execucao": f"{identidade.modelo_versao[:12] or 'modelo'}-{self.execucoes:04d}",
            "modelo": identidade.modelo,
            "modelo_versao": identidade.modelo_versao,
            "documento_sha256": documento.sha256,
            **({"documento_nome": documento.nome} if self.gravar_nomes else {}),
            "condicao": condicao,
            "repeticao": repeticao,
        }
        iniciado_em, carga_inicio, inicio = agora_utc(), carga_da_maquina(), time.monotonic()
        verificacao = ia.Verificacao()
        medidos: list[dict] = []
        situacao, falha = "sucesso", None
        for numero, bloco in enumerate(documento.blocos, start=1):
            medido, erro = self._bloco(documento, numero, bloco, verificacao, comum)
            medidos.append(medido)
            self.escrever({"registro": "bloco", **comum, **medido})
            if erro is not None:
                situacao = "falha"
                falha = falha or {"bloco": numero, "causa": erro}
                if erro != "json_invalido":
                    break  # o servidor não respondeu: os outros blocos não seriam medidos
        resumo = ia.resumir_metricas(medidos, _ms(inicio), self.provedor.contexto_efetivo(), falha)
        janela = resumo["num_ctx_efetivo"]
        maior = max(
            (
                (b.get("metricas") or {}).get("prompt_eval_count", 0)
                + (b.get("metricas") or {}).get("eval_count", 0)
                for b in medidos
            ),
            default=0,
        )
        linha = {
            "registro": "execucao",
            **comum,
            "situacao": situacao,
            "iniciado_em": iniciado_em,
            "paginas": documento.paginas,
            "caracteres": documento.caracteres,
            "texto_truncado": documento.truncado,
            "blocos": len(documento.blocos),
            "blocos_medidos": len(medidos),
            "duracao_ms": resumo["duracao_ms"],
            "tokens_entrada": resumo["tokens_entrada"],
            "tokens_saida": resumo["tokens_saida"],
            "carga_ns": sum((b.get("metricas") or {}).get("load_duration", 0) for b in medidos),
            "num_ctx_efetivo": janela,
            # Maior soma de tokens de entrada e de saída num bloco, e a fração da janela.
            "maior_bloco_tokens": maior,
            "uso_maximo_da_janela": round(maior / janela, 4) if janela else None,
            "avisos": resumo["avisos"],
            "falha": falha,
            "itens_recebidos": verificacao.recebidos,
            "itens_aceitos": len(verificacao.aceitos),
            "itens_recusados": len(verificacao.recusas),
            "duplicatas_descartadas": verificacao.duplicatas,
            "aceitos_pela_segunda_comparacao": verificacao.com_quebra_de_linha,
            "aceitos_por_tipo": dict(Counter(i.tipo for i, _, _ in verificacao.aceitos)),
            "recusas_por_motivo": dict(Counter(r["motivo"] for r in verificacao.recusas)),
            "carga_inicio": carga_inicio,
            "carga_fim": carga_da_maquina(),
        }
        self.escrever(linha)
        self.falhas += situacao != "sucesso"
        return linha

    def _bloco(
        self, documento: Documento, numero: int, bloco: str, verificacao, comum: dict
    ) -> tuple[dict, str | None]:
        """Mede um bloco. Devolve o registro (sem texto) e a causa da falha, se houve."""
        medido: dict = {"numero": numero, "caracteres": len(bloco)}
        antes = time.monotonic()
        try:
            geracao = self.provedor.gerar(ia.INSTRUCOES, bloco, ia.ESQUEMA_RESPOSTA)
        except ErroProvedor as erro:
            medido["duracao_parede_ms"] = _ms(antes)
            medido["falha"] = erro.causa
            return medido, erro.causa
        medido["duracao_parede_ms"] = _ms(antes)
        metricas = dict(geracao.metricas)
        medido["metricas"] = metricas
        medido["tokens_por_segundo_entrada"] = _vazao(
            metricas.get("prompt_eval_count"), metricas.get("prompt_eval_duration")
        )
        medido["tokens_por_segundo_saida"] = _vazao(
            metricas.get("eval_count"), metricas.get("eval_duration")
        )
        medido["saida_sha256"] = hashlib.sha256(geracao.texto.encode("utf-8")).hexdigest()
        medido["saida_caracteres"] = len(geracao.texto)
        if self.pasta_brutas is not None:
            nome = f"{comum['execucao']}-b{numero:03d}.json"
            (self.pasta_brutas / nome).write_text(geracao.texto, encoding="utf-8")
        try:
            resposta = ia.RespostaDoModelo.model_validate(carregar_json_estrito(geracao.texto))
        except (ValueError, ValidationError):
            medido["falha"] = "json_invalido"
            return medido, "json_invalido"
        recebidos, aceitos = verificacao.recebidos, len(verificacao.aceitos)
        recusas, duplicatas = len(verificacao.recusas), verificacao.duplicatas
        com_quebra = verificacao.com_quebra_de_linha
        ia.verificar_itens(documento.texto, resposta.itens, verificacao, self.max_itens)
        medido["itens_recebidos"] = verificacao.recebidos - recebidos
        medido["itens_aceitos"] = len(verificacao.aceitos) - aceitos
        medido["itens_recusados"] = len(verificacao.recusas) - recusas
        medido["duplicatas_descartadas"] = verificacao.duplicatas - duplicatas
        medido["aceitos_pela_segunda_comparacao"] = verificacao.com_quebra_de_linha - com_quebra
        medido["recusas_por_motivo"] = dict(
            Counter(r["motivo"] for r in verificacao.recusas[recusas:])
        )
        return medido, None


def descarregar(url: str, modelo: str, transporte, dormir: Callable[[float], None]) -> bool:
    """Pede ao Ollama que descarregue o modelo (``keep_alive: 0``) e espera ele sair de
    ``/api/ps``. Devolve se a descarga foi confirmada."""
    aceitos = {modelo, f"{modelo}:latest"}
    with httpx.Client(
        base_url=url, timeout=60, transport=transporte, trust_env=False, follow_redirects=False
    ) as cliente:
        try:
            cliente.post("/api/generate", json={"model": modelo, "keep_alive": 0})
            limite = time.monotonic() + ESPERA_DESCARGA_SEGUNDOS
            while True:
                carregados = cliente.get("/api/ps").json().get("models") or []
                if not any(
                    m.get("name") in aceitos or m.get("model") in aceitos for m in carregados
                ):
                    return True
                if time.monotonic() >= limite:
                    return False
                dormir(0.5)
        except (httpx.HTTPError, ValueError, AttributeError):
            return False


def medir_modelo(
    args, nome: str, digest_esperado: str, documentos, escrever, transporte, dormir
) -> tuple[dict, int]:
    """Mede um modelo. Devolve a descrição dele para ``ambiente.json`` e o número de falhas."""
    provedor = ProvedorOllama(
        args.url,
        nome,
        temperatura=args.temperatura,
        semente=args.semente,
        timeout_segundos=args.timeout,
        transporte=transporte,
        keep_alive=args.keep_alive,
        num_ctx=args.num_ctx,
        num_predict=args.num_predict,
    )
    descricao: dict = {"modelo_pedido": nome, "digest_esperado": digest_esperado or None}
    try:
        identidade = provedor.identidade()
    except ErroProvedor as erro:
        print(f"[{nome}] não pôde ser usado: {erro}", file=sys.stderr)
        return {**descricao, "falha": erro.causa}, 1
    descricao.update(
        modelo=identidade.modelo,
        modelo_versao=identidade.modelo_versao,
        servidor_versao=identidade.servidor_versao,
        quantizacao=identidade.quantizacao,
        tamanho_parametros=identidade.tamanho_parametros,
        tamanho_bytes=identidade.tamanho_bytes,
        parametros=provedor.parametros(),
    )
    digest = identidade.modelo_versao.lower().removeprefix("sha256:")
    if digest_esperado and not digest.startswith(digest_esperado):
        print(f"[{nome}] o digest instalado não é o esperado; modelo pulado.", file=sys.stderr)
        return {**descricao, "falha": "digest_divergente"}, 1
    medidor = Medidor(provedor, escrever, args.max_itens, args.saidas_brutas, args.gravar_nomes)
    if not args.sem_aquecimento:
        antes = time.monotonic()
        try:
            provedor.gerar(ia.INSTRUCOES, TEXTO_DE_AQUECIMENTO, ia.ESQUEMA_RESPOSTA)
        except ErroProvedor as erro:
            print(f"[{nome}] o aquecimento falhou: {erro}", file=sys.stderr)
            return {**descricao, "falha": erro.causa}, 1
        descricao["aquecimento_ms"] = _ms(antes)
    fila: list[tuple[Documento, str, int]] = []
    if args.frio:
        fila += [(d, "frio", 0) for d in documentos]
    quentes = [(d, "quente", r) for r in range(1, args.repeticoes + 1) for d in documentos]
    random.Random(args.semente_ordem).shuffle(quentes)  # nosec B311 - ordem, não segredo
    fila += quentes
    descargas_confirmadas = 0
    for posicao, (documento, condicao, repeticao) in enumerate(fila):
        if posicao and args.intervalo > 0:
            dormir(args.intervalo)
        if condicao == "frio":
            descargas_confirmadas += descarregar(args.url, nome, transporte, dormir)
        linha = medidor.executar(documento, condicao, repeticao)
        print(
            f"[{identidade.modelo}] {posicao + 1}/{len(fila)} doc {documento.sha256[:8]} "
            f"{condicao} {linha['situacao']}: {linha['duracao_ms'] / 1000:.1f} s, "
            f"{linha['tokens_entrada']}+{linha['tokens_saida']} tokens, "
            f"{linha['itens_aceitos']}/{linha['itens_recebidos']} itens aceitos"
        )
    if args.frio:
        descricao["descargas_confirmadas"] = descargas_confirmadas
    descricao["execucoes"] = medidor.execucoes
    descricao["execucoes_com_falha"] = medidor.falhas
    return descricao, medidor.falhas


def interpretar(argumentos: list[str] | None) -> argparse.Namespace:
    analisador = argparse.ArgumentParser(
        description="Mede a extração por IA com o Ollama local, fora da aplicação.",
    )
    analisador.add_argument("--pdfs", type=Path, required=True, help="pasta com os PDFs")
    analisador.add_argument(
        "--modelo",
        action="append",
        required=True,
        help="modelo instalado (nome ou nome=digest esperado); pode repetir",
    )
    analisador.add_argument("--saida", type=Path, help="pasta dos resultados (fora do git)")
    analisador.add_argument("--saidas-brutas", type=Path, help="guarda a saída bruta do modelo")
    analisador.add_argument("--gravar-nomes", action="store_true", help="grava o nome do PDF")
    analisador.add_argument("--url", default="http://127.0.0.1:11434")
    analisador.add_argument("--repeticoes", type=int, default=5, help="execuções a quente")
    analisador.add_argument("--frio", action="store_true", help="mede também a partida a frio")
    analisador.add_argument("--sem-aquecimento", action="store_true")
    analisador.add_argument(
        "--intervalo", type=float, default=10.0, help="segundos entre execuções"
    )
    analisador.add_argument("--semente-ordem", type=int, default=0)
    analisador.add_argument("--semente", type=int, default=0)
    analisador.add_argument("--temperatura", type=float, default=0.0)
    analisador.add_argument("--num-ctx", type=int)
    analisador.add_argument("--num-predict", type=int)
    analisador.add_argument("--keep-alive")
    analisador.add_argument("--timeout", type=float, default=3600.0, help="segundos por bloco")
    analisador.add_argument("--tamanho-bloco", type=int, default=6000)
    analisador.add_argument("--max-caracteres", type=int, default=200_000)
    analisador.add_argument("--max-itens", type=int, default=300)
    args = analisador.parse_args(argumentos)
    if args.repeticoes < 0 or (args.repeticoes == 0 and not args.frio):
        raise ErroDeUso("Nada a medir: use --repeticoes maior que zero ou --frio.")
    if args.tamanho_bloco < 200 or args.max_caracteres < 1 or args.max_itens < 1:
        raise ErroDeUso("--tamanho-bloco (mínimo 200), --max-caracteres e --max-itens inválidos.")
    if args.timeout <= 0 or args.intervalo < 0:
        raise ErroDeUso("--timeout deve ser positivo e --intervalo não pode ser negativo.")
    args.url = conferir_url_local(args.url)
    carimbo = datetime.now().strftime("%Y%m%d-%H%M%S")
    args.saida = conferir_pasta_de_saida(
        args.saida or PASTA_IGNORADA / "benchmark" / carimbo, "--saida"
    )
    if args.saidas_brutas is not None:
        args.saidas_brutas = conferir_pasta_de_saida(args.saidas_brutas, "--saidas-brutas")
    return args


def principal(
    argumentos: list[str] | None = None,
    transporte: httpx.BaseTransport | None = None,
    dormir: Callable[[float], None] = time.sleep,
) -> int:
    try:
        args = interpretar(argumentos)
        modelos = [separar_modelo(m) for m in args.modelo]
        documentos, recusados = ler_documentos(args.pdfs, args.max_caracteres, args.tamanho_bloco)
    except ErroDeUso as erro:
        print(f"Erro: {erro}", file=sys.stderr)
        return 2
    if not documentos:
        print("Nenhum PDF com texto na pasta indicada.", file=sys.stderr)
        return 2
    args.saida.mkdir(parents=True, exist_ok=True)
    if args.saidas_brutas is not None:
        args.saidas_brutas.mkdir(parents=True, exist_ok=True)
    ambiente = {
        "iniciado_em": agora_utc(),
        **descrever_ambiente(),
        # Os parâmetros da medição, sem caminhos (um caminho pode ter o nome do usuário).
        "argumentos": {
            "repeticoes": args.repeticoes,
            "frio": args.frio,
            "aquecimento": not args.sem_aquecimento,
            "intervalo_segundos": args.intervalo,
            "semente_ordem": args.semente_ordem,
            "semente": args.semente,
            "temperatura": args.temperatura,
            "num_ctx": args.num_ctx,
            "num_predict": args.num_predict,
            "keep_alive": args.keep_alive,
            "timeout_segundos": args.timeout,
            "tamanho_bloco": args.tamanho_bloco,
            "max_caracteres": args.max_caracteres,
            "max_itens": args.max_itens,
            "saidas_brutas_guardadas": args.saidas_brutas is not None,
        },
        "documentos": [
            {
                "sha256": d.sha256,
                **({"nome": d.nome} if args.gravar_nomes else {}),
                "tamanho_bytes": d.tamanho_bytes,
                "paginas": d.paginas,
                "caracteres": d.caracteres,
                "texto_truncado": d.truncado,
                "blocos": len(d.blocos),
            }
            for d in documentos
        ],
        "documentos_recusados": [
            {k: v for k, v in r.items() if k != "nome" or args.gravar_nomes} for r in recusados
        ],
        "carga_inicio": carga_da_maquina(),
        "modelos": [],
    }
    caminho_ambiente = args.saida / "ambiente.json"

    def gravar_ambiente() -> None:
        caminho_ambiente.write_text(
            json.dumps(ambiente, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    gravar_ambiente()
    falhas = 0
    with (args.saida / "execucoes.jsonl").open("a", encoding="utf-8") as arquivo:

        def escrever(linha: dict) -> None:
            arquivo.write(json.dumps(linha, ensure_ascii=False) + "\n")
            arquivo.flush()

        for nome, digest_esperado in modelos:
            descricao, falhas_do_modelo = medir_modelo(
                args, nome, digest_esperado, documentos, escrever, transporte, dormir
            )
            ambiente["modelos"].append(descricao)
            falhas += falhas_do_modelo
            gravar_ambiente()
    ambiente["concluido_em"] = agora_utc()
    ambiente["carga_fim"] = carga_da_maquina()
    gravar_ambiente()
    print(f"Resultados em {args.saida} (execucoes.jsonl e ambiente.json).")
    return 1 if falhas else 0


if __name__ == "__main__":
    raise SystemExit(principal())
