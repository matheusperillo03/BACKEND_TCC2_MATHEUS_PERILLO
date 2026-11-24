"""Mostra que um documento LONGO conclui na extração em segundo plano, com o Ollama local.

Gera um edital SINTÉTICO em PDF (várias páginas, nenhum dado real), sobe a aplicação neste
processo com um banco SQLite e uma custódia temporários, coleta o PDF, pede a extração pela
rota HTTP (``202``) e acompanha o andamento por ``/progresso`` até o fim. No final imprime o
resumo: situação, tempo total, blocos concluídos, falhos e divididos, tokens e itens.

Passa pelo caminho inteiro da aplicação (fila, trabalhador, blocos com sobreposição, limites
por bloco, fluxo do Ollama, verificação dos itens e auditoria); só o documento é sintético.
Nada fica gravado depois: a pasta temporária é apagada, e as chaves e o token usados são
sorteados a cada execução e nunca são impressos.

O servidor tem de ser local: só endereço de loopback é aceito. O script não instala modelo.

Uso:

  uv run python scripts/extracao_longa_sintetica.py --modelo qwen2.5:7b --paginas 10
  uv run python scripts/extracao_longa_sintetica.py --modelo qwen2.5:7b --paginas 22 \\
      --bloco-tokens 1000 --num-ctx 8192 --num-predict 2048
"""

from __future__ import annotations

import argparse
import io
import ipaddress
import json
import secrets
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import quote, urlsplit

from fastapi.testclient import TestClient

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from buscador.api import criar_app  # noqa: E402
from buscador.config import Configuracao, ErroConfiguracao  # noqa: E402

LINHAS_POR_PAGINA = 40
ORGAO = "PREFEITURA SINTÉTICA DE EXEMPLÓPOLIS"


def _cadeia_pdf(texto: str) -> bytes:
    """Texto como cadeia literal de PDF, na codificação WinAnsi (cp1252), com os escapes."""
    bruto = texto.encode("cp1252")
    return b"(" + bruto.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)") + b")"


def pdf_sintetico(paginas: list[list[str]]) -> bytes:
    """PDF mínimo e válido, escrito à mão: uma página por lista de linhas, fonte Helvetica."""
    objetos: dict[int, bytes] = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        3: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
    }
    filhos = []
    numero = 4
    for linhas in paginas:
        comandos = [b"BT /F1 10 Tf 13 TL 40 800 Td"]
        comandos += [_cadeia_pdf(linha) + b" Tj T*" for linha in linhas]
        conteudo = b"\n".join([*comandos, b"ET"])
        objetos[numero] = (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
            b"/Resources << /Font << /F1 3 0 R >> >> /Contents %d 0 R >>" % (numero + 1)
        )
        objetos[numero + 1] = (
            b"<< /Length %d >>\nstream\n" % len(conteudo) + conteudo + b"\nendstream"
        )
        filhos.append(b"%d 0 R" % numero)
        numero += 2
    objetos[2] = b"<< /Type /Pages /Kids [" + b" ".join(filhos) + b"] /Count %d >>" % len(filhos)
    saida = io.BytesIO()
    saida.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    posicoes = {}
    for n in sorted(objetos):
        posicoes[n] = saida.tell()
        saida.write(b"%d 0 obj\n" % n + objetos[n] + b"\nendobj\n")
    inicio_xref = saida.tell()
    total = max(objetos) + 1
    saida.write(b"xref\n0 %d\n0000000000 65535 f \n" % total)
    for n in range(1, total):
        saida.write(b"%010d 00000 n \n" % posicoes[n])
    saida.write(
        b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (total, inicio_xref)
    )
    return saida.getvalue()


def pagina_sintetica(numero: int) -> list[str]:
    """Uma página de edital inventado: cláusulas genéricas e, a cada poucas linhas, uma empresa,
    um valor, uma data ou um agente, todos sintéticos (os CNPJs não são de ninguém)."""
    linhas = [
        f"EDITAL SINTÉTICO DE PREGÃO ELETRÔNICO Nº {numero}/2026 - {ORGAO}",
        f"Página {numero} - documento de teste, sem nenhum dado real.",
        "",
    ]
    item = 0
    while len(linhas) < LINHAS_POR_PAGINA:
        item += 1
        c = f"{numero}.{item}"
        dv = (numero + item) % 100
        linhas += [
            f"{c}. O objeto deste item é a contratação sintética de serviços de manutenção",
            f"predial do lote {numero:02d}-{item:02d}, conforme o termo de referência anexo.",
            f"{c}.1. Fica habilitada a empresa CONSTRUTORA SINTÉTICA {numero:02d}{item:02d} LTDA,",
            f"inscrita no CNPJ sob o nº 77.{numero:03d}.{item:03d}/0001-{dv:02d}.",
            f"{c}.2. O valor estimado do lote é de R$ {numero * 10 + item}.{item:03d},00,",
            "incluídos todos os tributos, encargos e despesas indiretas.",
            f"{c}.3. A sessão pública ocorrerá em {item + 1:02d}/{(numero % 12) + 1:02d}/2026,",
            f"conduzida pelo pregoeiro FULANO SINTÉTICO {numero:02d}{item:02d} DE TAL.",
            f"{c}.4. A licitante deverá apresentar declaração de que cumpre os requisitos",
            "de habilitação e de que a proposta foi elaborada de forma independente.",
            "",
        ]
    return linhas[:LINHAS_POR_PAGINA]


def conferir_url_local(url: str) -> str:
    """Só servidor de loopback: é um teste de máquina local."""
    partes = urlsplit(url)
    host = (partes.hostname or "").lower()
    local = host == "localhost"
    if not local:
        try:
            local = ipaddress.ip_address(host).is_loopback
        except ValueError:
            local = False
    if partes.scheme not in ("http", "https") or not local or partes.username:
        raise SystemExit("--url deve apontar para o Ollama desta máquina (loopback).")
    return url.rstrip("/")


def interpretar(argumentos: list[str] | None) -> argparse.Namespace:
    analisador = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    analisador.add_argument("--modelo", required=True, help="modelo instalado no Ollama local")
    analisador.add_argument("--url", default="http://127.0.0.1:11434")
    analisador.add_argument("--paginas", type=int, default=10)
    analisador.add_argument("--bloco-tokens", type=int)
    analisador.add_argument("--sobreposicao-tokens", type=int)
    analisador.add_argument("--num-ctx", type=int)
    analisador.add_argument("--num-predict", type=int)
    analisador.add_argument("--bloco-tempo", type=float, help="tempo limite por bloco (s)")
    analisador.add_argument("--sem-token", type=float, help="tempo máximo sem token (s)")
    analisador.add_argument("--keep-alive", default="30m")
    analisador.add_argument("--intervalo", type=float, default=5.0, help="segundos entre consultas")
    analisador.add_argument("--json", type=Path, help="grava o resumo (só números) neste arquivo")
    args = analisador.parse_args(argumentos)
    if not 1 <= args.paginas <= 200:
        raise SystemExit("--paginas deve ficar entre 1 e 200.")
    args.url = conferir_url_local(args.url)
    return args


def montar_config(args: argparse.Namespace, pasta: Path, token: str) -> Configuracao:
    opcionais = {
        "ia_bloco_tokens": args.bloco_tokens,
        "ia_sobreposicao_tokens": args.sobreposicao_tokens,
        "ia_num_ctx": args.num_ctx,
        "ia_num_predict": args.num_predict,
        "ia_bloco_tempo_segundos": args.bloco_tempo,
        "ia_sem_token_segundos": args.sem_token,
    }
    return Configuracao(
        _env_file=None,
        database_url=f"sqlite:///{pasta / 'teste.db'}",
        dados_dir=pasta / "dados",
        # Chaves e token sorteados para esta execução; somem com a pasta temporária.
        auditoria_chave_hmac=secrets.token_hex(32),
        custodia_chave=secrets.token_hex(32),
        modo_dev=True,
        tokens_dev=f"{token}:teste:analista",
        editais_dir_base=pasta / "editais",
        monitoramento_agendador_ativo=False,
        ia_extracao_ativa=True,
        ia_provedor="ollama",
        ia_ollama_url=args.url,
        ia_modelo=args.modelo,
        ia_keep_alive=args.keep_alive,
        **{nome: valor for nome, valor in opcionais.items() if valor is not None},
    )


def resumir(extracao: dict, progresso: dict, paginas: int, caracteres: int) -> dict:
    """O resumo da execução, só com números e códigos."""
    metricas = extracao.get("metricas") or {}
    blocos = metricas.get("blocos") or []
    janela = metricas.get("num_ctx_efetivo")
    maior = max(
        (
            (b.get("metricas") or {}).get("prompt_eval_count", 0)
            + (b.get("metricas") or {}).get("eval_count", 0)
            for b in blocos
        ),
        default=0,
    )
    geracao_ns = sum((b.get("metricas") or {}).get("eval_duration", 0) for b in blocos)
    duracoes = sorted(b.get("duracao_parede_ms", 0) for b in blocos if b["situacao"] == "concluido")
    parametros = extracao.get("parametros") or {}
    return {
        "situacao": extracao["situacao"],
        "motivo": extracao.get("motivo", ""),
        "modelo": extracao.get("modelo"),
        "paginas": paginas,
        "caracteres": caracteres,
        "bloco_tokens": parametros.get("bloco_tokens"),
        "bloco_caracteres": parametros.get("tamanho_bloco"),
        "sobreposicao_caracteres": parametros.get("sobreposicao_caracteres"),
        "num_ctx": parametros.get("num_ctx"),
        "num_predict": parametros.get("num_predict"),
        "num_ctx_efetivo": janela,
        "duracao_total_s": round((metricas.get("duracao_ms") or 0) / 1000, 1),
        "blocos_total": progresso["blocos_total"],
        "blocos_concluidos": progresso["blocos_concluidos"],
        "blocos_falhos": metricas.get("blocos_falhos") or [],
        "blocos_divididos": [b["numero"] for b in blocos if b["situacao"] == "dividido"],
        "repeticoes": sum(len(b.get("falhas") or []) for b in blocos),
        "bloco_mais_lento_s": round(duracoes[-1] / 1000, 1) if duracoes else None,
        "bloco_mediano_s": round(duracoes[len(duracoes) // 2] / 1000, 1) if duracoes else None,
        "tokens_entrada": metricas.get("tokens_entrada"),
        "tokens_saida": metricas.get("tokens_saida"),
        "tokens_por_segundo_saida": (
            round(metricas.get("tokens_saida", 0) / (geracao_ns / 1e9), 2) if geracao_ns else None
        ),
        "maior_bloco_tokens": maior,
        "uso_maximo_da_janela": round(maior / janela, 3) if janela else None,
        "avisos": metricas.get("avisos") or [],
        "itens_recebidos": extracao["itens_recebidos"],
        "itens_aceitos": extracao["itens_aceitos"],
        "itens_recusados": extracao["itens_recusados"],
        "duplicatas_descartadas": parametros.get("duplicatas_descartadas"),
    }


def principal(argumentos: list[str] | None = None, provedor=None) -> int:
    """``provedor`` substitui o do Ollama (só nos testes do próprio script)."""
    args = interpretar(argumentos)
    token = secrets.token_urlsafe(24)
    cabecalhos = {
        "Authorization": f"Bearer {token}",
        "X-Justificativa": quote("Teste de documento longo com dados sintéticos"),
    }
    with tempfile.TemporaryDirectory(prefix="extracao-sintetica-") as temporaria:
        pasta = Path(temporaria)
        editais = pasta / "editais" / "longo"
        editais.mkdir(parents=True)
        (editais / "SINTETICO.txt").write_text("Arquivos sintéticos. Nenhum dado é real.\n")
        pdf = pdf_sintetico([pagina_sintetica(n) for n in range(1, args.paginas + 1)])
        (editais / "edital-longo.pdf").write_bytes(pdf)
        try:
            app = criar_app(montar_config(args, pasta, token), provedor_ia=provedor)
        except ErroConfiguracao as erro:
            print(f"Configuração recusada: {erro}", file=sys.stderr)
            return 2
        with TestClient(app, base_url="http://localhost") as cliente:
            caso_id = cliente.post(
                "/casos",
                json={
                    "titulo": "Documento longo sintético",
                    "finalidade": "Teste com dados sintéticos",
                    "retencao_ate": "2099-12-31",
                },
                headers=cabecalhos,
            ).json()["id"]
            coleta = cliente.post(
                f"/casos/{caso_id}/coletas",
                json={"fonte": "edital_arquivo", "parametros": {"diretorio": "longo"}},
                headers=cabecalhos,
            )
            coleta.raise_for_status()
            captura_id = coleta.json()["captura_ids"][0]
            texto = cliente.get(f"/casos/{caso_id}/capturas/{captura_id}/texto", headers=cabecalhos)
            caracteres = texto.json()["caracteres"]
            print(
                f"PDF sintético: {args.paginas} páginas, {len(pdf)} bytes, {caracteres} caracteres."
            )
            inicio = time.monotonic()
            pedido = cliente.post(
                f"/casos/{caso_id}/capturas/{captura_id}/extracao-ia", headers=cabecalhos
            )
            print(f"Pedido: HTTP {pedido.status_code} em {time.monotonic() - inicio:.2f} s.")
            if pedido.status_code != 202:
                print(pedido.text, file=sys.stderr)
                return 1
            rota = f"/casos/{caso_id}/extracoes-ia/{pedido.json()['id']}"
            visto = None
            while True:
                progresso = cliente.get(f"{rota}/progresso", headers=cabecalhos).json()
                marca = (
                    progresso["situacao"],
                    progresso["blocos_concluidos"],
                    progresso["blocos_falhos"],
                )
                if marca != visto:
                    visto = marca
                    estimativa = progresso["estimativa_restante_ms"]
                    print(
                        f"[{time.monotonic() - inicio:7.1f} s] {progresso['situacao']}: "
                        f"{progresso['blocos_concluidos']}/{progresso['blocos_total']} blocos, "
                        f"{progresso['blocos_falhos']} falho(s), "
                        f"{progresso['itens_aceitos']} sugestão(ões)"
                        + (f", faltam cerca de {estimativa / 1000:.0f} s" if estimativa else ""),
                        flush=True,
                    )
                if not progresso["ativa"]:
                    break
                time.sleep(args.intervalo)
            extracao = cliente.get(rota, headers=cabecalhos).json()
            resumo = resumir(extracao, progresso, args.paginas, caracteres)
            resumo["relogio_do_pedido_ao_fim_s"] = round(time.monotonic() - inicio, 1)
            cadeia = cliente.get("/auditoria/verificar", headers=cabecalhos)
            resumo["auditoria_integra"] = (
                cadeia.json().get("integra") if cadeia.is_success else None
            )
    print(json.dumps(resumo, ensure_ascii=False, indent=2))
    if args.json is not None:
        args.json.write_text(json.dumps(resumo, ensure_ascii=False, indent=2) + "\n", "utf-8")
    return 0 if resumo["situacao"] == "sucesso" else 1


if __name__ == "__main__":
    raise SystemExit(principal())
