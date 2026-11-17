"""Mede a leitura em fluxo do conector ``tse_arquivo`` com um arquivo SINTÉTICO grande.

O CSV de receitas de uma UF grande passa do teto de leitura integral (``TSE_MAX_BYTES_ARQUIVO``,
64 MiB): o de SP de 2024 tem 264 MB (conferido em 17/11/2026; ver o conector). Este script gera
um arquivo sintético desse porte, no layout do arquivo de receitas, coleta-o com um filtro e
informa o tempo e o pico de memória do processo. Nenhum dado real é usado nem baixado.

Uso (o arquivo é gerado numa pasta temporária e apagado ao final):

  uv run python scripts/medir_tse_fluxo.py --megabytes 264 --filtro municipio
  uv run python scripts/medir_tse_fluxo.py --megabytes 264 --filtro candidatos --zip
"""

from __future__ import annotations

import argparse
import hashlib
import json
import resource
import sys
import tempfile
import time
import zipfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from buscador.conectores.tse_arquivo import ConectorTseArquivo  # noqa: E402

COLUNAS = (
    "DT_GERACAO", "HH_GERACAO", "AA_ELEICAO", "CD_TIPO_ELEICAO", "NM_TIPO_ELEICAO",
    "CD_ELEICAO", "DS_ELEICAO", "DT_ELEICAO", "ST_TURNO", "TP_PRESTACAO_CONTAS",
    "DT_PRESTACAO_CONTAS", "SQ_PRESTADOR_CONTAS", "SG_UF", "SG_UE", "NM_UE",
    "NR_CNPJ_PRESTADOR_CONTA",
    "CD_CARGO", "DS_CARGO", "SQ_CANDIDATO", "NR_CANDIDATO", "NM_CANDIDATO", "NR_CPF_CANDIDATO",
    "NR_PARTIDO", "SG_PARTIDO", "NM_PARTIDO", "CD_FONTE_RECEITA", "DS_FONTE_RECEITA",
    "CD_ORIGEM_RECEITA", "DS_ORIGEM_RECEITA", "CD_NATUREZA_RECEITA", "DS_NATUREZA_RECEITA",
    "CD_ESPECIE_RECEITA", "DS_ESPECIE_RECEITA", "NR_CPF_CNPJ_DOADOR", "NM_DOADOR", "NM_DOADOR_RFB",
    "SQ_RECEITA", "DT_RECEITA", "DS_RECEITA", "VR_RECEITA",
)  # fmt: skip
MUNICIPIOS_SINTETICOS = 500
CANDIDATOS_POR_MUNICIPIO = 20


def linha_sintetica(n: int) -> str:
    """Linha de receita sintética (doador pessoa jurídica fictícia; nenhum CPF)."""
    municipio = n % MUNICIPIOS_SINTETICOS
    candidato = municipio * CANDIDATOS_POR_MUNICIPIO + n % CANDIDATOS_POR_MUNICIPIO
    valores = {
        "DT_GERACAO": "17/11/2026", "HH_GERACAO": "10:00:00", "AA_ELEICAO": "2024",
        "CD_TIPO_ELEICAO": "2", "NM_TIPO_ELEICAO": "ORDINÁRIA", "CD_ELEICAO": "619",
        "DS_ELEICAO": "ELEIÇÕES MUNICIPAIS SINTÉTICAS", "DT_ELEICAO": "06/10/2024",
        "ST_TURNO": "1", "TP_PRESTACAO_CONTAS": "FINAL", "DT_PRESTACAO_CONTAS": "01/11/2024",
        "SQ_PRESTADOR_CONTAS": str(4_000_000_000 + n), "SG_UF": "ZZ",
        "SG_UE": f"{90000 + municipio}",
        "NM_UE": f"MUNICÍPIO SINTÉTICO {municipio:03d}",
        "NR_CNPJ_PRESTADOR_CONTA": "90900000000000",
        "CD_CARGO": "13", "DS_CARGO": "VEREADOR", "SQ_CANDIDATO": str(990_000_000_000 + candidato),
        "NR_CANDIDATO": "99999", "NM_CANDIDATO": f"CANDIDATO SINTÉTICO {candidato:05d}",
        "NR_CPF_CANDIDATO": "-4", "NR_PARTIDO": "99", "SG_PARTIDO": "PSIN",
        "NM_PARTIDO": "PARTIDO SINTÉTICO", "CD_FONTE_RECEITA": "10010200",
        "DS_FONTE_RECEITA": "OUTROS RECURSOS", "CD_ORIGEM_RECEITA": "10020000",
        "DS_ORIGEM_RECEITA": "Recursos de pessoas jurídicas (sintético)",
        "CD_NATUREZA_RECEITA": "1", "DS_NATUREZA_RECEITA": "FINANCEIRO", "CD_ESPECIE_RECEITA": "2",
        "DS_ESPECIE_RECEITA": "TRANSFERÊNCIA ELETRÔNICA",
        "NR_CPF_CNPJ_DOADOR": f"9{n % 10_000_000:07d}000100",
        "NM_DOADOR": f"DOADORA SINTÉTICA {n % 10_000_000:07d} LTDA",
        "NM_DOADOR_RFB": f"DOADORA SINTÉTICA {n % 10_000_000:07d} LTDA", "SQ_RECEITA": str(n),
        "DT_RECEITA": "15/08/2024", "DS_RECEITA": "DOAÇÃO SINTÉTICA PARA MEDIÇÃO DE DESEMPENHO",
        "VR_RECEITA": f"{100 + n % 900},00",
    }  # fmt: skip
    return ";".join(f'"{valores[c]}"' for c in COLUNAS) + "\r\n"


def gerar(caminho: Path, megabytes: float) -> tuple[int, int]:
    """Grava o CSV sintético (Latin-1) com cerca de ``megabytes``; devolve (bytes, linhas)."""
    alvo = int(megabytes * 1024 * 1024)
    total = linhas = 0
    with caminho.open("wb") as arquivo:
        cabecalho = (";".join(f'"{c}"' for c in COLUNAS) + "\r\n").encode("latin-1")
        arquivo.write(cabecalho)
        total += len(cabecalho)
        while total < alvo:
            bloco = "".join(linha_sintetica(linhas + i) for i in range(2000)).encode("latin-1")
            arquivo.write(bloco)
            total += len(bloco)
            linhas += 2000
    return total, linhas


def medir(megabytes: float, filtro: str, zipado: bool) -> dict:
    with tempfile.TemporaryDirectory(prefix="tse-fluxo-") as temporario:
        raiz = Path(temporario)
        pasta = raiz / "t"
        pasta.mkdir()
        (pasta / "SINTETICO.txt").write_text("dados sintéticos", encoding="utf-8")
        csv_ = pasta / "receitas_candidatos_2024_ZZ.csv"
        inicio = time.perf_counter()
        tamanho, linhas = gerar(csv_, megabytes)
        geracao = time.perf_counter() - inicio
        if zipado:
            with zipfile.ZipFile(pasta / "contas_ZZ.zip", "w", zipfile.ZIP_DEFLATED) as pacote:
                pacote.write(csv_, csv_.name)
            csv_.unlink()
        parametros: dict = {"diretorio": "t"}
        if filtro == "municipio":
            parametros.update(uf="ZZ", municipio="MUNICÍPIO SINTÉTICO 007")
        else:
            parametros["candidatos"] = [str(990_000_000_000 + 140 + i) for i in range(20)]
        capturas: list[dict] = []

        def registrar(fonte, origem, conteudo, metadados):
            capturas.append(
                {
                    "origem": origem,
                    "bytes": len(conteudo),
                    "sha256": hashlib.sha256(conteudo).hexdigest(),
                    "recorte": metadados.get("recorte"),
                }
            )
            return SimpleNamespace(id=f"cap{len(capturas)}", url_ou_arquivo=origem)

        antes = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        inicio = time.perf_counter()
        resultado = ConectorTseArquivo(dir_base=raiz).coletar(parametros, registrar)
        duracao = time.perf_counter() - inicio
        depois = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        # ru_maxrss: bytes no macOS, kibibytes no Linux.
        fator = 1 if sys.platform == "darwin" else 1024
        return {
            "arquivo_bytes": tamanho,
            "arquivo_linhas": linhas,
            "zip": zipado,
            "filtro": parametros,
            "segundos_geracao": round(geracao, 1),
            "segundos_coleta": round(duracao, 1),
            "pico_rss_mib_antes": round(antes * fator / 2**20, 1),
            "pico_rss_mib_depois": round(depois * fator / 2**20, 1),
            "registros": len(resultado.registros),
            "capturas": capturas,
        }


def main() -> int:
    analisador = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    analisador.add_argument("--megabytes", type=float, default=264.0)
    analisador.add_argument("--filtro", choices=("municipio", "candidatos"), default="municipio")
    analisador.add_argument("--zip", action="store_true", help="coloca o CSV dentro de um ZIP")
    args = analisador.parse_args()
    if args.megabytes > 300:
        print("Limite de 300 MB por medição (espaço em disco).", file=sys.stderr)
        return 2
    medida = medir(args.megabytes, args.filtro, args.zip)
    if len(medida["filtro"].get("candidatos", [])) > 3:
        medida["filtro"]["candidatos"] = f"{len(medida['filtro']['candidatos'])} SQ_CANDIDATO"
    print(json.dumps(medida, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
