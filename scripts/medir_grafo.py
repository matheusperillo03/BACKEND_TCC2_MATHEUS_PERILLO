"""Mede o desempenho das consultas de vínculo em grafos SINTÉTICOS crescentes (RNF06, H29).

Compara, no PostgreSQL, três formas de responder à vizinhança em N níveis e ao caminho entre duas
entidades:

- ``networkx``: a implementação original (carrega o grafo inteiro do caso em memória a cada
  consulta, ``buscador.grafo.servico``);
- ``sql_niveis``: busca em largura nível a nível no banco (``buscador.grafo.sql``, usada pela API);
- ``sql_recursiva``: a vizinhança numa única consulta ``WITH RECURSIVE``.

Cada estratégia roda num PROCESSO próprio, para que o pico de memória (``ru_maxrss``) seja só dela.
O grafo é sintético e não representa ninguém: nós ``N000001...`` e arestas sorteadas com semente
fixa (80% entre nós quaisquer; 20% chegam a 1% dos nós, os "concentradores", como acontece com
endereços e contadores compartilhados). O número de nós é metade do de arestas.

ATENÇÃO: o script APAGA e recria as tabelas do banco indicado. Use só um banco descartável:

    docker run -d --name medir-grafo-pg -e POSTGRES_USER=osint -e POSTGRES_PASSWORD=medir \\
        -e POSTGRES_DB=osint -p 127.0.0.1:55999:5432 postgres:16-alpine
    uv run python scripts/medir_grafo.py \\
        --url postgresql+psycopg://osint:medir@127.0.0.1:55999/osint --confirmo-banco-descartavel \\
        --tamanhos 10000,100000,1000000 --saida docs/desempenho-grafo-dados.json
    docker rm -f medir-grafo-pg

O resultado (JSON) alimenta as tabelas de ``docs/desempenho-grafo.md``; ``--tabelas`` imprime as
tabelas em Markdown a partir do JSON.
"""

from __future__ import annotations

import argparse
import json
import platform
import random
import resource
import statistics
import subprocess  # nosec B404 - só reexecuta este próprio script, com argumentos fixos
import sys
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

from sqlalchemy import text  # noqa: E402

from buscador import grafo  # noqa: E402
from buscador.db import criar_engine, criar_esquema, criar_fabrica_sessoes  # noqa: E402
from buscador.modelos import Base  # noqa: E402

CASO = "casosinteticodemedicao0000000000"
CAPTURA = "capturasinteticademedicao0000000"
SEMENTE = 20261116
SEM_TETO = 100_000_000
PROFUNDIDADES = (1, 2, 3)


def _no(indice: int) -> str:
    return f"N{indice:031d}"


def carregar(url: str, arestas: int) -> dict:
    """Recria o esquema e carrega um grafo sintético com ``arestas`` arestas (por ``COPY``)."""
    engine = criar_engine(url)
    Base.metadata.drop_all(engine)
    criar_esquema(engine)
    nos = max(2, arestas // 2)
    sorteio = random.Random(SEMENTE + arestas)  # nosec B311 - dado sintético, não é segredo
    concentradores = max(1, nos // 100)
    inicio = time.perf_counter()
    bruta = engine.raw_connection()
    try:
        with bruta.cursor() as cursor:
            cursor.execute(
                "INSERT INTO casos (id, titulo, descricao, numero_procedimento, criado_por, "
                "criado_em, finalidade) VALUES (%s, 'Grafo sintético de medição', '', '', "
                "'medicao', '2026-01-01T00:00:00.000000Z', 'Medição de desempenho')",
                (CASO,),
            )
            cursor.execute(
                "INSERT INTO capturas (id, caso_id, fonte, url_ou_arquivo, coletado_em, sha256, "
                "tamanho_bytes, caminho_relativo, metadados, conector_nome, conector_versao) "
                "VALUES (%s, %s, 'sintetico', 'simulado://medicao', '2026-01-01T00:00:00.000000Z', "
                "%s, 0, 'nenhum', '{\"simulado\": true}', '', '')",
                (CAPTURA, CASO, "0" * 64),
            )
            with cursor.copy(
                "COPY entidades (id, caso_id, tipo, chave, rotulo, identificador, atributos, "
                "captura_id) FROM STDIN"
            ) as copia:
                for i in range(nos):
                    tipo = "empresa" if i % 3 else "pessoa"
                    copia.write_row(
                        (
                            _no(i),
                            CASO,
                            tipo,
                            f"k{i}",
                            f"Entidade sintética {i}",
                            str(i),
                            "{}",
                            CAPTURA,
                        )
                    )
            with cursor.copy(
                "COPY arestas (id, caso_id, origem_id, destino_id, tipo, chave, atributos, "
                "captura_id) FROM STDIN"
            ) as copia:
                for i in range(arestas):
                    origem = sorteio.randrange(nos)
                    if sorteio.random() < 0.2:
                        destino = sorteio.randrange(concentradores)
                    else:
                        destino = sorteio.randrange(nos)
                    copia.write_row(
                        (
                            f"A{i:031d}",
                            CASO,
                            _no(origem),
                            _no(destino),
                            "socio_de",
                            str(i),
                            "{}",
                            CAPTURA,
                        )
                    )
        bruta.commit()
    finally:
        bruta.close()
    carga = time.perf_counter() - inicio
    with engine.begin() as conexao:
        conexao.execute(text("ANALYZE"))
        tamanhos = {
            nome: conexao.execute(text(f"SELECT {funcao}('{tabela}')")).scalar_one()  # nosec B608
            for nome, funcao, tabela in (
                ("entidades_total_bytes", "pg_total_relation_size", "entidades"),
                ("arestas_tabela_bytes", "pg_relation_size", "arestas"),
                ("arestas_indices_bytes", "pg_indexes_size", "arestas"),
                ("arestas_total_bytes", "pg_total_relation_size", "arestas"),
            )
        }
        indices = [
            linha[0]
            for linha in conexao.execute(
                text("SELECT indexname FROM pg_indexes WHERE tablename = 'arestas' ORDER BY 1")
            )
        ]
    engine.dispose()
    return {
        "nos": nos,
        "arestas": arestas,
        "carga_segundos": round(carga, 2),
        "indices_de_arestas": indices,
        **tamanhos,
    }


def _amostra(arestas: int, quantas: int) -> tuple[list[str], list[tuple[str, str]]]:
    """Centros e pares sorteados com semente fixa (os mesmos para todas as estratégias). Um
    quinto dos centros são concentradores, para medir também o pior caso."""
    nos = max(2, arestas // 2)
    sorteio = random.Random(SEMENTE)  # nosec B311 - dado sintético, não é segredo
    concentradores = max(1, nos // 100)
    centros = [
        _no(sorteio.randrange(concentradores) if i % 5 == 0 else sorteio.randrange(nos))
        for i in range(quantas)
    ]
    pares = [(_no(sorteio.randrange(nos)), _no(sorteio.randrange(nos))) for _ in range(quantas)]
    return centros, pares


def _resumo(tempos: list[float], tamanhos: list[int]) -> dict:
    ordenados = sorted(tempos)
    return {
        "consultas": len(tempos),
        "mediana_ms": round(statistics.median(ordenados) * 1000, 1),
        "p95_ms": round(ordenados[min(len(ordenados) - 1, int(len(ordenados) * 0.95))] * 1000, 1),
        "maximo_ms": round(ordenados[-1] * 1000, 1),
        "nos_resposta_mediana": int(statistics.median(tamanhos)) if tamanhos else 0,
        "nos_resposta_maximo": max(tamanhos) if tamanhos else 0,
    }


def medir(url: str, estrategia: str, arestas: int, quantas: int) -> dict:
    """Roda as consultas de uma estratégia (neste processo) e devolve tempos e pico de memória."""
    engine = criar_engine(url)
    centros, pares = _amostra(arestas, quantas)
    resultado: dict = {"estrategia": estrategia, "vizinhanca": {}, "caminho": None}
    with criar_fabrica_sessoes(engine)() as sessao:
        for profundidade in PROFUNDIDADES:
            tempos, tamanhos = [], []
            for centro in centros:
                inicio = time.perf_counter()
                if estrategia == "networkx":
                    # Como a API fazia: monta o grafo inteiro a cada consulta.
                    completo = grafo.construir(sessao, CASO)
                    saida = grafo.grafo_para_dict(grafo.vizinhanca(completo, centro, profundidade))
                    del completo
                elif estrategia == "sql_niveis":
                    saida = grafo.sql.vizinhanca(sessao, CASO, centro, profundidade, SEM_TETO)
                else:
                    saida = grafo.sql.vizinhanca_recursiva(sessao, CASO, centro, profundidade)
                tempos.append(time.perf_counter() - inicio)
                tamanhos.append(len(saida["nos"]))
                sessao.expunge_all()
            resultado["vizinhanca"][str(profundidade)] = _resumo(tempos, tamanhos)
        if estrategia != "sql_recursiva":
            tempos, tamanhos, achados = [], [], 0
            for de, para in pares:
                inicio = time.perf_counter()
                if estrategia == "networkx":
                    completo = grafo.construir(sessao, CASO)
                    sub = grafo.caminho_mais_curto(completo, de, para)
                    saida = grafo.grafo_para_dict(sub) if sub is not None else None
                    del completo
                else:
                    saida = grafo.sql.caminho_mais_curto(sessao, CASO, de, para, SEM_TETO)
                tempos.append(time.perf_counter() - inicio)
                tamanhos.append(len(saida["nos"]) if saida else 0)
                achados += saida is not None
                sessao.expunge_all()
            resultado["caminho"] = {**_resumo(tempos, tamanhos), "encontrados": achados}
    engine.dispose()
    pico = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # ru_maxrss vem em bytes no macOS e em kibibytes no Linux.
    resultado["pico_memoria_mib"] = round(
        pico / (1024 * 1024 if sys.platform == "darwin" else 1024), 1
    )
    return resultado


def _em_subprocesso(url: str, estrategia: str, arestas: int, quantas: int) -> dict:
    comando = [
        sys.executable,
        __file__,
        "--url",
        url,
        "--confirmo-banco-descartavel",
        "--medir",
        estrategia,
        "--arestas",
        str(arestas),
        "--consultas",
        str(quantas),
    ]
    saida = subprocess.run(comando, capture_output=True, text=True, check=True)  # nosec B603
    return json.loads(saida.stdout.strip().splitlines()[-1])


def _mib(valor: int) -> str:
    return f"{valor / 1024 / 1024:.1f}"


def tabelas(dados: dict) -> str:
    """Tabelas em Markdown a partir do JSON gravado por este script."""
    linhas = [
        "| Arestas | Nós | Carga (s) | Tabela arestas (MiB) | Índices de arestas (MiB) | "
        "Arestas total (MiB) | Entidades total (MiB) |",
        "|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in dados["resultados"]:
        g = r["grafo"]
        linhas.append(
            f"| {g['arestas']:,} | {g['nos']:,} | {g['carga_segundos']} | "
            f"{_mib(g['arestas_tabela_bytes'])} | {_mib(g['arestas_indices_bytes'])} | "
            f"{_mib(g['arestas_total_bytes'])} | {_mib(g['entidades_total_bytes'])} |".replace(
                ",", "."
            )
        )
    linhas += [
        "",
        "| Arestas | Consulta | Estratégia | Consultas | Mediana (ms) | p95 (ms) | Máximo (ms) | "
        "Nós na resposta (mediana / máximo) | Pico de memória do processo (MiB) |",
        "|---:|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for r in dados["resultados"]:
        for medida in r["medidas"]:
            consultas = [
                (f"vizinhança até {p} nível(is)", m) for p, m in medida["vizinhanca"].items()
            ]
            if medida["caminho"]:
                consultas.append(("caminho entre duas entidades", medida["caminho"]))
            for nome, m in consultas:
                linhas.append(
                    f"| {r['grafo']['arestas']:,} | {nome} | {medida['estrategia']} | ".replace(
                        ",", "."
                    )
                    + f"{m['consultas']} | {m['mediana_ms']} | {m['p95_ms']} | {m['maximo_ms']} | "
                    f"{m['nos_resposta_mediana']} / {m['nos_resposta_maximo']} | "
                    f"{medida['pico_memoria_mib']} |"
                )
    return "\n".join(linhas)


def main() -> int:
    analisador = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    analisador.add_argument("--url", help="URL do banco DESCARTÁVEL (papel dono)")
    analisador.add_argument("--confirmo-banco-descartavel", action="store_true")
    analisador.add_argument("--tamanhos", default="10000,100000,1000000")
    analisador.add_argument("--consultas", type=int, default=20)
    analisador.add_argument("--consultas-networkx", type=int, default=3)
    analisador.add_argument("--saida", default="")
    analisador.add_argument("--medir", choices=("networkx", "sql_niveis", "sql_recursiva"))
    analisador.add_argument("--arestas", type=int, default=0)
    analisador.add_argument(
        "--tabelas", default="", help="imprime as tabelas de um JSON já gravado"
    )
    args = analisador.parse_args()

    if args.tabelas:
        print(tabelas(json.loads(Path(args.tabelas).read_text(encoding="utf-8"))))
        return 0
    if not args.url or not args.confirmo_banco_descartavel:
        print(
            "Informe --url e --confirmo-banco-descartavel: o script APAGA as tabelas do banco.",
            file=sys.stderr,
        )
        return 2
    if not args.url.startswith("postgresql"):
        print("A medição é feita no PostgreSQL.", file=sys.stderr)
        return 2
    if args.medir:
        print(json.dumps(medir(args.url, args.medir, args.arestas, args.consultas)))
        return 0

    engine = criar_engine(args.url)
    with engine.connect() as conexao:
        versao = conexao.execute(text("SHOW server_version")).scalar_one()
    engine.dispose()
    dados: dict = {
        "medido_em": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "ambiente": {
            "postgresql": versao,
            "python": platform.python_version(),
            "sistema": f"{platform.system()} {platform.release()} ({platform.machine()})",
            "processador": platform.processor(),
        },
        "semente": SEMENTE,
        "resultados": [],
    }
    for arestas in [int(t) for t in args.tamanhos.split(",") if t.strip()]:
        print(f"[{arestas} arestas] carregando...", file=sys.stderr)
        registro = {"grafo": carregar(args.url, arestas), "medidas": []}
        for estrategia in ("sql_niveis", "sql_recursiva", "networkx"):
            quantas = args.consultas_networkx if estrategia == "networkx" else args.consultas
            print(f"[{arestas} arestas] {estrategia} ({quantas} consultas)...", file=sys.stderr)
            registro["medidas"].append(_em_subprocesso(args.url, estrategia, arestas, quantas))
        dados["resultados"].append(registro)
        if args.saida:
            Path(args.saida).parent.mkdir(parents=True, exist_ok=True)
            Path(args.saida).write_text(
                json.dumps(dados, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
    engine = criar_engine(args.url)
    Base.metadata.drop_all(engine)
    engine.dispose()
    print(tabelas(dados))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
