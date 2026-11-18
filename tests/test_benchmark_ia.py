"""Script de benchmark da extração por IA (``scripts/benchmark_ia.py``).

Tudo sintético: os PDFs são escritos à mão pelos testes e o Ollama é um transporte simulado, sem
rede. Os números de métrica são inventados.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import httpx
import pytest

from buscador.ia.falso import ProvedorFalso
from scripts import benchmark_ia
from tests.test_editais_pdf import (
    CNPJ_FORMATADO,
    LINHAS_EDITAL,
    PDF_EDITAL,
    PDF_ESCANEADO,
    pdf_sintetico,
)

MODELO = "modelo-sintetico:7b"
DIGEST = "d" * 64
PDF_SEM_ITENS = pdf_sintetico(
    [["ATA SINTÉTICA DE REUNIÃO", "Nada a registrar nesta ata sintética."]]
)
NAO_SERIA_JSON = '{"itens": [{"tipo": "valor", "valor": "R$ 1'


class OllamaSimulado:
    """Guarda os pedidos e o estado (modelo carregado ou não) de um Ollama de mentira."""

    def __init__(self, chat=None, digest: str = DIGEST) -> None:
        self.pedidos: list[httpx.Request] = []
        self.carregado = False
        self._chat = chat
        self._digest = digest

    def corpos(self, rota: str) -> list[dict]:
        return [json.loads(p.content) for p in self.pedidos if p.url.path == rota]

    def __call__(self, pedido: httpx.Request) -> httpx.Response:
        self.pedidos.append(pedido)
        rota = pedido.url.path
        modelo = {
            "name": MODELO,
            "model": MODELO,
            "digest": self._digest,
            "size": 4_000_000_000,
            "details": {"parameter_size": "7.6B", "quantization_level": "Q4_K_M"},
        }
        if rota == "/api/version":
            return httpx.Response(200, json={"version": "0.99.0"})
        if rota == "/api/tags":
            return httpx.Response(200, json={"models": [modelo]})
        if rota == "/api/ps":
            carregados = [{"name": MODELO, "model": MODELO, "context_length": 4096}]
            return httpx.Response(200, json={"models": carregados if self.carregado else []})
        if rota == "/api/generate":
            if json.loads(pedido.content).get("keep_alive") == 0:
                self.carregado = False
            return httpx.Response(200, json={"done": True, "done_reason": "unload"})
        if rota == "/api/chat":
            if self._chat is not None:
                return self._chat(pedido)
            texto = json.loads(pedido.content)["messages"][1]["content"]
            a_frio, self.carregado = not self.carregado, True
            return httpx.Response(
                200,
                json={
                    "message": {
                        "role": "assistant",
                        "content": ProvedorFalso().gerar("", texto, {}).texto,
                    },
                    "done": True,
                    "done_reason": "stop",
                    "total_duration": 9_000_000_000,
                    "load_duration": 19_000_000_000 if a_frio else 40_000_000,
                    "prompt_eval_count": 2000,
                    "prompt_eval_duration": 4_000_000_000,
                    "eval_count": 1000,
                    "eval_duration": 5_000_000_000,
                },
            )
        return httpx.Response(404)


def _pasta_de_pdfs(tmp_path: Path) -> Path:
    pasta = tmp_path / "pdfs"
    pasta.mkdir()
    (pasta / "edital_sintetico.pdf").write_bytes(PDF_EDITAL)
    (pasta / "ata_sintetica.PDF").write_bytes(PDF_SEM_ITENS)
    (pasta / "escaneado_sintetico.pdf").write_bytes(PDF_ESCANEADO)
    (pasta / "leia-me.txt").write_text("Arquivos sintéticos. Nenhum dado é real.\n", "utf-8")
    return pasta


def _rodar(tmp_path: Path, servidor: OllamaSimulado, *extras: str, modelo: str = MODELO) -> int:
    esperas: list[float] = []
    argumentos = [
        "--pdfs",
        str(tmp_path / "pdfs"),
        "--saida",
        str(tmp_path / "saida"),
        "--modelo",
        modelo,
        *extras,
    ]
    return benchmark_ia.principal(argumentos, httpx.MockTransport(servidor), esperas.append)


def _linhas(tmp_path: Path, registro: str) -> list[dict]:
    texto = (tmp_path / "saida" / "execucoes.jsonl").read_text("utf-8")
    linhas = [json.loads(linha) for linha in texto.splitlines()]
    return [linha for linha in linhas if linha["registro"] == registro]


def test_benchmark_mede_frio_e_quente_e_grava_so_metricas(tmp_path, capsys):
    _pasta_de_pdfs(tmp_path)
    servidor = OllamaSimulado()
    brutas = tmp_path / "brutas"
    codigo = _rodar(
        tmp_path,
        servidor,
        "--frio",
        "--repeticoes",
        "2",
        "--saidas-brutas",
        str(brutas),
        modelo=f"{MODELO}=dddddddd",
    )
    assert codigo == 0
    execucoes, blocos = _linhas(tmp_path, "execucao"), _linhas(tmp_path, "bloco")
    # 2 documentos legíveis x (1 a frio + 2 a quente); o aquecimento não entra.
    assert len(execucoes) == 6 == len(blocos)
    assert sorted(e["condicao"] for e in execucoes) == ["frio"] * 2 + ["quente"] * 4
    assert [e["condicao"] for e in execucoes[:2]] == ["frio", "frio"]
    assert all(e["situacao"] == "sucesso" and e["falha"] is None for e in execucoes)
    assert {e["documento_sha256"] for e in execucoes} == {
        hashlib.sha256(PDF_EDITAL).hexdigest(),
        hashlib.sha256(PDF_SEM_ITENS).hexdigest(),
    }
    assert len({e["execucao"] for e in execucoes}) == 6
    for e in execucoes:
        assert (e["modelo"], e["modelo_versao"]) == (MODELO, DIGEST)
        assert (e["tokens_entrada"], e["tokens_saida"], e["blocos"]) == (2000, 1000, 1)
        assert e["num_ctx_efetivo"] == 4096 and e["uso_maximo_da_janela"] == round(3000 / 4096, 4)
        assert e["duracao_ms"] >= 0 and e["avisos"] == []
        # A partida a frio aparece na carga do modelo; a quente, não.
        assert e["carga_ns"] == (19_000_000_000 if e["condicao"] == "frio" else 40_000_000)
        com_itens = e["documento_sha256"] == hashlib.sha256(PDF_EDITAL).hexdigest()
        assert e["itens_recebidos"] == e["itens_aceitos"] == (2 if com_itens else 0)
        assert e["aceitos_por_tipo"] == ({"empresa": 1, "valor": 1} if com_itens else {})
        assert e["recusas_por_motivo"] == {} and e["duplicatas_descartadas"] == 0
    for b in blocos:
        assert b["metricas"]["done_reason"] == "stop" and b["numero"] == 1
        assert (b["tokens_por_segundo_entrada"], b["tokens_por_segundo_saida"]) == (500.0, 200.0)
        assert len(b["saida_sha256"]) == 64 and b["duracao_parede_ms"] >= 0
    # Cada medida a frio foi precedida de um pedido de descarga do modelo.
    assert servidor.corpos("/api/generate") == [{"model": MODELO, "keep_alive": 0}] * 2
    # 1 aquecimento + 6 execuções de um bloco; nada além do padrão foi enviado ao modelo.
    conversas = servidor.corpos("/api/chat")
    assert len(conversas) == 7
    assert conversas[0]["messages"][1]["content"] == benchmark_ia.TEXTO_DE_AQUECIMENTO
    assert all(c["options"] == {"temperature": 0.0, "seed": 0} for c in conversas)
    assert all("keep_alive" not in c for c in conversas)
    assert all(p.url.host == "127.0.0.1" for p in servidor.pedidos)

    ambiente = json.loads((tmp_path / "saida" / "ambiente.json").read_text("utf-8"))
    assert ambiente["prompt_sha256"] == benchmark_ia.ia.PROMPT_SHA256
    assert ambiente["argumentos"]["repeticoes"] == 2 and ambiente["argumentos"]["frio"] is True
    assert ambiente["argumentos"]["num_ctx"] is None
    assert [d["blocos"] for d in ambiente["documentos"]] == [1, 1]
    assert all("nome" not in d for d in ambiente["documentos"])
    assert ambiente["documentos_recusados"] == [
        {"sha256": hashlib.sha256(PDF_ESCANEADO).hexdigest(), "motivo": "sem_camada_de_texto"}
    ]
    modelo = ambiente["modelos"][0]
    assert (modelo["quantizacao"], modelo["tamanho_parametros"]) == ("Q4_K_M", "7.6B")
    assert modelo["execucoes"] == 6 and modelo["execucoes_com_falha"] == 0
    assert (
        modelo["descargas_confirmadas"] == 2 and modelo["parametros"]["timeout_segundos"] == 3600.0
    )
    assert "concluido_em" in ambiente

    # Nem o texto do documento, nem valores extraídos, nem nomes de arquivo, nem caminhos.
    gravado = (tmp_path / "saida" / "execucoes.jsonl").read_text("utf-8") + json.dumps(
        ambiente, ensure_ascii=False
    )
    for proibido in (
        "CONSTRUTORA",
        "SINTÉTIC",
        CNPJ_FORMATADO,
        "1.234.567",
        "edital_sintetico",
        "ata_sintetica",
        str(tmp_path),
        "pavimentação",
    ):
        assert proibido not in gravado
    assert not any(marca in gravado.lower() for marca in ("serial", "uuid", "udid"))
    assert sorted(p.name for p in (tmp_path / "saida").iterdir()) == [
        "ambiente.json",
        "execucoes.jsonl",
    ]
    # A saída bruta (que tem nomes) só existe na pasta pedida à parte.
    assert len(list(brutas.iterdir())) == 6
    assert any(CNPJ_FORMATADO in p.read_text("utf-8") for p in brutas.iterdir())
    assert CNPJ_FORMATADO not in capsys.readouterr().out


def test_benchmark_envia_e_registra_parametros_pedidos_e_nomes_so_a_pedido(tmp_path):
    _pasta_de_pdfs(tmp_path)
    servidor = OllamaSimulado()
    codigo = _rodar(
        tmp_path,
        servidor,
        "--repeticoes",
        "1",
        "--sem-aquecimento",
        "--num-ctx",
        "8192",
        "--num-predict",
        "2048",
        "--keep-alive",
        "30m",
        "--gravar-nomes",
    )
    assert codigo == 0
    conversas = servidor.corpos("/api/chat")
    assert len(conversas) == 2 and all(c["keep_alive"] == "30m" for c in conversas)
    assert all(
        c["options"]["num_ctx"] == 8192 and c["options"]["num_predict"] == 2048 for c in conversas
    )
    assert servidor.corpos("/api/generate") == []
    ambiente = json.loads((tmp_path / "saida" / "ambiente.json").read_text("utf-8"))
    parametros = ambiente["modelos"][0]["parametros"]
    assert (parametros["num_ctx"], parametros["num_predict"], parametros["keep_alive"]) == (
        8192,
        2048,
        "30m",
    )
    assert "aquecimento_ms" not in ambiente["modelos"][0]
    assert {e["documento_nome"] for e in _linhas(tmp_path, "execucao")} == {
        "edital_sintetico.pdf",
        "ata_sintetica.PDF",
    }


def test_benchmark_divide_em_blocos_e_segue_depois_de_json_invalido(tmp_path):
    pasta = tmp_path / "pdfs"
    pasta.mkdir()
    linhas = [
        f"{n}. Cláusula sintética número {n} do edital de exemplo, sem valor." for n in range(40)
    ]
    (pasta / "longo_sintetico.pdf").write_bytes(
        pdf_sintetico([linhas[:20] + LINHAS_EDITAL, linhas[20:]])
    )
    chamadas = {"n": 0}

    def chat(pedido: httpx.Request) -> httpx.Response:
        chamadas["n"] += 1
        texto = json.loads(pedido.content)["messages"][1]["content"]
        conteudo = (
            NAO_SERIA_JSON if chamadas["n"] == 1 else ProvedorFalso().gerar("", texto, {}).texto
        )
        motivo = "length" if chamadas["n"] == 1 else "stop"
        resposta = {
            "message": {"content": conteudo},
            "done_reason": motivo,
            "prompt_eval_count": 10,
            "eval_count": 5,
        }
        return httpx.Response(200, json=resposta)

    codigo = _rodar(
        tmp_path,
        OllamaSimulado(chat),
        "--repeticoes",
        "1",
        "--sem-aquecimento",
        "--tamanho-bloco",
        "600",
    )
    assert codigo == 1
    (execucao,) = _linhas(tmp_path, "execucao")
    blocos = _linhas(tmp_path, "bloco")
    assert execucao["blocos"] == len(blocos) == execucao["blocos_medidos"] > 2
    assert [b["numero"] for b in blocos] == list(range(1, len(blocos) + 1))
    # O bloco com JSON inválido é registrado e os outros continuam sendo medidos.
    assert blocos[0]["falha"] == "json_invalido" and "itens_recebidos" not in blocos[0]
    assert all("falha" not in b for b in blocos[1:])
    assert execucao["situacao"] == "falha" and execucao["falha"] == {
        "bloco": 1,
        "causa": "json_invalido",
    }
    assert execucao["avisos"] == [{"tipo": "saida_cortada", "blocos": [1]}]
    assert execucao["tokens_entrada"] == 10 * len(blocos) and execucao["num_ctx_efetivo"] is None
    assert execucao["uso_maximo_da_janela"] is None
    assert sum(b.get("itens_aceitos", 0) for b in blocos) == execucao["itens_aceitos"]


@pytest.mark.parametrize(
    ("chat", "causa"),
    [
        (lambda p: httpx.Response(500), "http"),
        (
            lambda p: (_ for _ in ()).throw(httpx.ReadTimeout("esgotou", request=p)),
            "tempo_esgotado",
        ),
    ],
)
def test_benchmark_registra_a_falha_do_servidor_e_termina_com_erro(tmp_path, chat, causa):
    _pasta_de_pdfs(tmp_path)
    assert _rodar(tmp_path, OllamaSimulado(chat), "--repeticoes", "1", "--sem-aquecimento") == 1
    execucoes = _linhas(tmp_path, "execucao")
    assert len(execucoes) == 2
    assert all(e["falha"] == {"bloco": 1, "causa": causa} for e in execucoes)
    assert all(b["falha"] == causa and "metricas" not in b for b in _linhas(tmp_path, "bloco"))


def test_benchmark_recusa_saida_no_repositorio_servidor_remoto_e_digest_divergente(
    tmp_path, capsys
):
    _pasta_de_pdfs(tmp_path)
    servidor = OllamaSimulado()
    transporte = httpx.MockTransport(servidor)
    base = ["--pdfs", str(tmp_path / "pdfs"), "--modelo", MODELO]
    dentro = benchmark_ia.RAIZ / "resultados" / "sintetico-nao-criar"
    assert benchmark_ia.principal([*base, "--saida", str(dentro)], transporte) == 2
    assert not dentro.exists()
    brutas_dentro = [
        "--saida",
        str(tmp_path / "s"),
        "--saidas-brutas",
        str(benchmark_ia.RAIZ / "tests" / "x"),
    ]
    assert benchmark_ia.principal([*base, *brutas_dentro], transporte) == 2
    # dados-reais/ é ignorada pelo git: é o único lugar do repositório que serve.
    ignorada = benchmark_ia.PASTA_IGNORADA / "benchmark" / "x"
    assert benchmark_ia.conferir_pasta_de_saida(ignorada, "--saida") == ignorada.resolve()
    for url in (
        "http://198.51.100.7:11434",
        "https://ollama.exemplo",
        "http://u:s@127.0.0.1:11434",
    ):
        assert (
            benchmark_ia.principal(
                [*base, "--saida", str(tmp_path / "s"), "--url", url], transporte
            )
            == 2
        )
    assert (
        benchmark_ia.principal([*base[:1], str(tmp_path / "nao-existe"), *base[2:]], transporte)
        == 2
    )
    assert servidor.pedidos == [] and not (tmp_path / "s").exists()
    assert "Erro:" in capsys.readouterr().err
    # Digest diferente do esperado: o modelo é pulado sem receber nenhum texto.
    assert _rodar(tmp_path, servidor, modelo=f"{MODELO}=abc123") == 1
    assert servidor.corpos("/api/chat") == []
    ambiente = json.loads((tmp_path / "saida" / "ambiente.json").read_text("utf-8"))
    assert ambiente["modelos"][0]["falha"] == "digest_divergente"
    assert (tmp_path / "saida" / "execucoes.jsonl").read_text("utf-8") == ""
