"""Recorta dos ZIPs oficiais do CNPJ só as linhas das empresas de interesse (leitura em fluxo).

Para que serve. A base completa de dados abertos do CNPJ tem dezenas de gigabytes descompactada
(ver "Fontes reais" no README) e o conector ``cnpj_arquivo`` foi feito para ler um RECORTE: ele
copia cada arquivo para a custódia e tem teto de bytes e de registros. Este script produz esse
recorte sem descompactar nada em disco e sem banco: percorre os arquivos ``Empresas*.zip``,
``Estabelecimentos*.zip`` e ``Socios*.zip`` em blocos, direto do ZIP, e grava só as linhas cujo
CNPJ básico (8 primeiros dígitos) está na lista. A saída são três arquivos no layout oficial
(mesmos bytes das linhas de origem), prontos para a coleta ``cnpj_arquivo``.

Endereço oficial dos ZIPs (conferido em 17/11/2026): o repositório "Arquivos da Receita Federal"
(https://arquivos.receitafederal.gov.br/, pasta ``Dados/Cadastros/CNPJ/<AAAA-MM>/``), para onde
aponta o conjunto "Cadastro Nacional da Pessoa Jurídica - CNPJ" do dados.gov.br. Os arquivos de
um mesmo número (``Empresas3``, ``Socios3``...) NÃO cobrem a mesma faixa de CNPJ: para achar
todos os dados de uma empresa é preciso percorrer os dez arquivos de cada tabela.

Custódia. O recorte é um arquivo DERIVADO: a captura feita pelo conector registra o SHA-256 do
recorte. Para ligar o recorte à fonte, este script grava ``MANIFESTO.json`` na pasta de saída,
com o nome, o tamanho e o SHA-256 de cada ZIP lido, os CNPJs pedidos, as contagens e a data. O
manifesto não é lido pelo conector (o nome não casa com o layout).

Limitação conhecida: o filtro trabalha por linha física. Um registro cujo campo tivesse quebra de
linha dentro das aspas sairia truncado; por isso, ao final, o recorte é relido com o leitor de
CSV e a contagem de campos de cada linha é conferida (7, 30 e 11) — divergência vira erro.

Só leitura de arquivos locais; não acessa a rede.

Uso:
  uv run python scripts/recortar_cnpj.py --zips dados-reais/cnpj/2026-11/zips \\
      --saida dados-reais/cnpj/recorte-caso1 --cnpj 12345678000190 --cnpj 87654321
  # ou uma lista em arquivo, um CNPJ por linha:
  uv run python scripts/recortar_cnpj.py --zips ... --saida ... --lista cnpjs.txt
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
import sys
import time
import zipfile
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO

BLOCO = 8 * 1024 * 1024
# tabela: (prefixo do ZIP, sufixo do arquivo de saída, número de campos do layout)
TABELAS = {
    "empresas": ("Empresas", "EMPRECSV", 7),
    "estabelecimentos": ("Estabelecimentos", "ESTABELE", 30),
    "socios": ("Socios", "SOCIOCSV", 11),
}
MAX_CNPJS = 5_000


def cnpjs_basicos(valores: list[str]) -> list[str]:
    """CNPJ básico (8 posições) de cada valor; aceita a raiz (8) ou o CNPJ completo (14), com ou
    sem pontuação, numéricos ou alfanuméricos (letras de A a Z; IN RFB nº 2.229/2024)."""
    basicos: list[str] = []
    for valor in valores:
        limpo = re.sub(r"[.\-/\s]", "", valor).upper()
        if not re.fullmatch(r"[0-9A-Z]{8}|[0-9A-Z]{12}[0-9]{2}", limpo):
            raise ValueError(
                f"CNPJ inválido (esperadas 8 ou 14 posições, dígitos ou letras de A a Z): "
                f"'{valor.strip()[:30]}'"
            )
        basicos.append(limpo[:8])
    return sorted(set(basicos))


def filtrar_linhas(fluxo: BinaryIO, basicos: list[str], bloco: int = BLOCO) -> Iterator[bytes]:
    """Linhas (com a quebra de linha) cujo primeiro campo é um dos CNPJs básicos, na ordem do
    arquivo. Lê em blocos e procura ``\\n"<basico>"`` com busca de bytes, sem interpretar o CSV."""
    padroes = [b'\n"' + b.encode("ascii") + b'"' for b in basicos]
    resto = b"\n"
    while True:
        dados = fluxo.read(bloco)
        fim_do_arquivo = not dados
        regiao = resto + dados
        if not fim_do_arquivo:
            corte = regiao.rfind(b"\n")
            if corte <= 0:
                resto = regiao
                continue
            regiao, resto = regiao[:corte], regiao[corte:]
        achados: list[tuple[int, bytes]] = []
        for padrao in padroes:
            posicao = regiao.find(padrao)
            while posicao >= 0:
                fim = regiao.find(b"\n", posicao + 1)
                if fim < 0:
                    fim = len(regiao)
                achados.append((posicao, regiao[posicao + 1 : fim] + b"\n"))
                posicao = regiao.find(padrao, fim)
        for _, linha in sorted(achados):
            yield linha
        if fim_do_arquivo:
            return


def sha256_do_arquivo(caminho: Path) -> str:
    resumo = hashlib.sha256()
    with caminho.open("rb") as arquivo:
        while bloco := arquivo.read(BLOCO):
            resumo.update(bloco)
    return resumo.hexdigest()


def conferir_layout(caminho: Path, campos: int) -> int:
    """Relê o recorte com o leitor de CSV e confere o número de campos; devolve as linhas."""
    linhas = 0
    with caminho.open("r", encoding="latin-1", newline="") as arquivo:
        for linha in csv.reader(arquivo, delimiter=";", quotechar='"'):
            if not linha:
                continue
            linhas += 1
            if len(linha) != campos:
                raise ValueError(
                    f"{caminho.name}: linha {linhas} com {len(linha)} campos (esperados "
                    f"{campos}); o layout pode ter mudado ou há quebra de linha num campo."
                )
    return linhas


def recortar(zips: Path, saida: Path, basicos: list[str], com_hash: bool = True) -> dict:
    """Grava o recorte em ``saida`` e devolve o manifesto."""
    if not basicos:
        raise ValueError("Informe ao menos um CNPJ.")
    if len(basicos) > MAX_CNPJS:
        raise ValueError(f"Lista com mais de {MAX_CNPJS} CNPJs: divida o recorte.")
    if not zips.is_dir():
        raise ValueError("A pasta dos ZIPs não existe.")
    saida.mkdir(parents=True, exist_ok=True)
    manifesto: dict = {
        "gerado_em": datetime.now(UTC).isoformat(timespec="seconds"),
        "gerador": "scripts/recortar_cnpj.py",
        "descricao": (
            "Recorte dos dados abertos do CNPJ: só as linhas dos CNPJs básicos pedidos, com os "
            "mesmos bytes das linhas dos arquivos de origem."
        ),
        "cnpjs_basicos": basicos,
        "origem": [],
        "tabelas": {},
    }
    for tabela, (prefixo, sufixo, campos) in TABELAS.items():
        fontes = sorted(
            p for p in zips.iterdir() if p.is_file() and re.fullmatch(rf"{prefixo}\d*\.zip", p.name)
        )
        destino = saida / f"K3241.RECORTE.{sufixo}"
        linhas = 0
        with destino.open("wb") as gravador:
            for fonte in fontes:
                inicio = time.monotonic()
                achadas = 0
                with zipfile.ZipFile(fonte) as pacote:
                    membros = [i for i in pacote.infolist() if not i.is_dir()]
                    for membro in membros:
                        with pacote.open(membro) as fluxo:
                            for linha in filtrar_linhas(fluxo, basicos):
                                gravador.write(linha)
                                achadas += 1
                    descompactado = sum(i.file_size for i in membros)
                linhas += achadas
                manifesto["origem"].append(
                    {
                        "arquivo": fonte.name,
                        "tabela": tabela,
                        "bytes": fonte.stat().st_size,
                        "bytes_descompactado": descompactado,
                        "sha256": sha256_do_arquivo(fonte) if com_hash else None,
                        "linhas_no_recorte": achadas,
                        "segundos": round(time.monotonic() - inicio, 1),
                    }
                )
        if not fontes:
            destino.unlink()
            manifesto["tabelas"][tabela] = {"arquivo": None, "linhas": 0, "zips_lidos": 0}
            continue
        conferir_layout(destino, campos)
        manifesto["tabelas"][tabela] = {
            "arquivo": destino.name,
            "linhas": linhas,
            "bytes": destino.stat().st_size,
            "sha256": sha256_do_arquivo(destino),
            "zips_lidos": len(fontes),
        }
    (saida / "MANIFESTO.json").write_text(
        json.dumps(manifesto, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return manifesto


def principal(argumentos: list[str] | None = None) -> int:
    analisador = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    analisador.add_argument("--zips", required=True, type=Path, help="pasta com os ZIPs oficiais")
    analisador.add_argument("--saida", required=True, type=Path, help="pasta do recorte")
    analisador.add_argument("--cnpj", action="append", default=[], help="CNPJ (8 ou 14 posições)")
    analisador.add_argument("--lista", type=Path, help="arquivo com um CNPJ por linha")
    analisador.add_argument(
        "--sem-hash", action="store_true", help="não calcula o SHA-256 dos ZIPs (mais rápido)"
    )
    args = analisador.parse_args(argumentos)
    valores = list(args.cnpj)
    try:
        if args.lista:
            valores += [
                linha
                for linha in args.lista.read_text(encoding="utf-8").splitlines()
                if linha.strip() and not linha.lstrip().startswith("#")
            ]
        manifesto = recortar(args.zips, args.saida, cnpjs_basicos(valores), not args.sem_hash)
    except (ValueError, OSError, zipfile.BadZipFile) as erro:
        print(f"ERRO: {erro}", file=sys.stderr)
        return 1
    for tabela, dados in manifesto["tabelas"].items():
        print(f"{tabela}: {dados['linhas']} linha(s) de {dados['zips_lidos']} ZIP(s)")
    incompletas = [t for t, d in manifesto["tabelas"].items() if d["zips_lidos"] < 10]
    if incompletas:
        print(
            "AVISO: menos de 10 ZIPs lidos em "
            + ", ".join(incompletas)
            + ". Os arquivos de mesmo número não cobrem a mesma faixa de CNPJ: sem os dez "
            "arquivos de cada tabela o recorte pode estar incompleto."
        )
    print(f"Manifesto: {args.saida / 'MANIFESTO.json'}")
    return 0


if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    raise SystemExit(principal())
