"""Exportação do grafo do caso em formatos abertos (RNF09, H38).

Formatos:

- **CSV de entidades** e **CSV de vínculos** (RFC 4180: campos separados por vírgula, aspas
  duplas dobradas, quebra de linha CRLF, primeira linha de cabeçalho; UTF-8). O CSV de vínculos
  repete, em cada linha, os dados das duas pontas: é a forma "uma linha por vínculo" que os
  assistentes de importação tabular esperam (cada linha vira duas entidades e uma ligação).
- **GraphML** (XML do grafo, com ``<key>``/``<data>`` para os atributos de nós e arestas).
- **ZIP** com os três arquivos, um LEIAME e um manifesto com o SHA-256 de cada arquivo.

O que a documentação oficial dos produtos diz sobre importação está no README (seção
"Exportação"), com as URLs consultadas e o que ficou pendente de confirmação.

Custódia: toda linha (entidade ou vínculo) leva o ID da captura de origem, o SHA-256 dessa
captura, a fonte, a origem (URL ou arquivo) e o instante da coleta. Quem recebe o arquivo consegue
voltar à prova: ``GET /capturas/{id}/verificar`` recalcula o SHA-256.

Segurança do CSV: valor de texto que começa com ``=``, ``+``, ``-``, ``@``, tabulação ou retorno
de carro ganha um apóstrofo na frente, para que uma planilha não o execute como fórmula (injeção
de CSV). Os identificadores hexadecimais e os resumos nunca começam com esses caracteres.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import zipfile
from dataclasses import dataclass
from xml.etree import ElementTree as ET  # nosec B405 - só GERA XML; nunca interpreta XML de fora

from sqlalchemy import select
from sqlalchemy.orm import Session

from buscador import __version__
from buscador.modelos import Aresta, Captura, Caso, Entidade, iso_utc
from buscador.seguranca import sanitizar_json

FORMATOS = ("graphml", "csv_entidades", "csv_vinculos", "zip")
NS_GRAPHML = "http://graphml.graphdrawing.org/xmlns"
_XSI = "http://www.w3.org/2001/XMLSchema-instance"
_ESQUEMA = "http://graphml.graphdrawing.org/xmlns/1.0/graphml.xsd"
# Lista da OWASP ("CSV Injection"), com as variantes de largura total.
_INICIO_DE_FORMULA = ("=", "+", "-", "@", "\t", "\r", "\n", "＝", "＋", "－", "＠")
# Caracteres que o XML 1.0 não aceita (controles, fora tab, LF e CR; substitutos; U+FFFE/FFFF).
_INVALIDO_XML = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff￾￿]")

CAMPOS_CAPTURA = (
    "captura_id",
    "captura_sha256",
    "captura_fonte",
    "captura_origem",
    "captura_coletado_em",
)
CAMPOS_ENTIDADE = ("id", "tipo", "rotulo", "identificador", *CAMPOS_CAPTURA, "atributos_json")
CAMPOS_VINCULO = (
    "id",
    "tipo",
    "origem_id",
    "origem_tipo",
    "origem_rotulo",
    "origem_identificador",
    "destino_id",
    "destino_tipo",
    "destino_rotulo",
    "destino_identificador",
    *CAMPOS_CAPTURA,
    "atributos_json",
)


@dataclass(frozen=True)
class Arquivo:
    nome: str
    tipo_mime: str
    conteudo: bytes

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.conteudo).hexdigest()


@dataclass(frozen=True)
class DadosDoGrafo:
    caso: Caso
    exportado_em: str
    entidades: list[dict]
    vinculos: list[dict]


def _json(valor: object) -> str:
    return json.dumps(sanitizar_json(valor or {}), ensure_ascii=False, sort_keys=True)


def carregar(sessao: Session, caso: Caso) -> DadosDoGrafo:
    """Entidades e vínculos do caso, cada um com os dados da captura de origem."""
    capturas = {
        c.id: c for c in sessao.execute(select(Captura).where(Captura.caso_id == caso.id)).scalars()
    }

    def da_captura(captura_id: str | None) -> dict:
        c = capturas.get(captura_id or "")
        return {
            "captura_id": captura_id or "",
            "captura_sha256": c.sha256 if c else "",
            "captura_fonte": c.fonte if c else "",
            "captura_origem": c.url_ou_arquivo if c else "",
            "captura_coletado_em": c.coletado_em if c else "",
        }

    entidades = {}
    for e in sessao.execute(
        select(Entidade).where(Entidade.caso_id == caso.id).order_by(Entidade.tipo, Entidade.rotulo)
    ).scalars():
        entidades[e.id] = {
            "id": e.id,
            "tipo": e.tipo,
            "rotulo": e.rotulo,
            "identificador": e.identificador or "",
            **da_captura(e.captura_id),
            "atributos_json": _json(e.atributos),
        }
    vinculos = []
    for a in sessao.execute(
        select(Aresta)
        .where(Aresta.caso_id == caso.id)
        .order_by(Aresta.tipo, Aresta.origem_id, Aresta.destino_id, Aresta.id)
    ).scalars():
        origem, destino = entidades.get(a.origem_id), entidades.get(a.destino_id)
        if origem is None or destino is None:
            continue
        vinculos.append(
            {
                "id": a.id,
                "tipo": a.tipo,
                **{f"origem_{c}": origem[c] for c in ("id", "tipo", "rotulo", "identificador")},
                **{f"destino_{c}": destino[c] for c in ("id", "tipo", "rotulo", "identificador")},
                **da_captura(a.captura_id),
                "atributos_json": _json(a.atributos),
            }
        )
    return DadosDoGrafo(caso, iso_utc(), list(entidades.values()), vinculos)


def neutralizar_formula(valor: object) -> str:
    texto = "" if valor is None else str(valor)
    return "'" + texto if texto.startswith(_INICIO_DE_FORMULA) else texto


def _csv(campos: tuple[str, ...], linhas: list[dict]) -> bytes:
    saida = io.StringIO(newline="")
    escritor = csv.writer(saida, dialect="excel", lineterminator="\r\n", quoting=csv.QUOTE_ALL)
    escritor.writerow(campos)
    for linha in linhas:
        escritor.writerow([neutralizar_formula(linha[c]) for c in campos])
    return saida.getvalue().encode("utf-8")


def csv_entidades(dados: DadosDoGrafo) -> Arquivo:
    return Arquivo(
        "entidades.csv", "text/csv; charset=utf-8", _csv(CAMPOS_ENTIDADE, dados.entidades)
    )


def csv_vinculos(dados: DadosDoGrafo) -> Arquivo:
    return Arquivo("vinculos.csv", "text/csv; charset=utf-8", _csv(CAMPOS_VINCULO, dados.vinculos))


def _xml(valor: object) -> str:
    return _INVALIDO_XML.sub(" ", "" if valor is None else str(valor))


def graphml(dados: DadosDoGrafo) -> Arquivo:
    """GraphML com um ``<key>`` por atributo (todos ``string``) e grafo dirigido."""
    ET.register_namespace("", NS_GRAPHML)
    ET.register_namespace("xsi", _XSI)
    raiz = ET.Element(
        f"{{{NS_GRAPHML}}}graphml", {f"{{{_XSI}}}schemaLocation": f"{NS_GRAPHML} {_ESQUEMA}"}
    )

    def chave(dominio: str, nome: str) -> str:
        ident = f"{dominio[0]}_{nome}"
        ET.SubElement(
            raiz,
            f"{{{NS_GRAPHML}}}key",
            {"id": ident, "for": dominio, "attr.name": nome, "attr.type": "string"},
        )
        return ident

    campos_no = [c for c in CAMPOS_ENTIDADE if c != "id"]
    campos_aresta = ["tipo", *CAMPOS_CAPTURA, "atributos_json"]
    campos_grafo = ["caso_id", "caso_titulo", "exportado_em", "gerado_por"]
    chaves_grafo = {c: chave("graph", c) for c in campos_grafo}
    chaves_no = {c: chave("node", c) for c in campos_no}
    chaves_aresta = {c: chave("edge", c) for c in campos_aresta}

    def dado(pai: ET.Element, ident: str, valor: object) -> None:
        ET.SubElement(pai, f"{{{NS_GRAPHML}}}data", {"key": ident}).text = _xml(valor)

    grafo = ET.SubElement(
        raiz, f"{{{NS_GRAPHML}}}graph", {"id": f"caso-{dados.caso.id}", "edgedefault": "directed"}
    )
    for campo, valor in (
        ("caso_id", dados.caso.id),
        ("caso_titulo", dados.caso.titulo),
        ("exportado_em", dados.exportado_em),
        ("gerado_por", f"buscador-osint {__version__}"),
    ):
        dado(grafo, chaves_grafo[campo], valor)
    for e in dados.entidades:
        no = ET.SubElement(grafo, f"{{{NS_GRAPHML}}}node", {"id": e["id"]})
        for campo in campos_no:
            dado(no, chaves_no[campo], e[campo])
    for v in dados.vinculos:
        aresta = ET.SubElement(
            grafo,
            f"{{{NS_GRAPHML}}}edge",
            {"id": v["id"], "source": v["origem_id"], "target": v["destino_id"]},
        )
        for campo in campos_aresta:
            dado(aresta, chaves_aresta[campo], v[campo])
    ET.indent(raiz)
    conteudo = ET.tostring(raiz, encoding="utf-8", xml_declaration=True)
    return Arquivo("grafo.graphml", "application/graphml+xml; charset=utf-8", conteudo)


_LEIAME = """Exportação do grafo do caso (buscador OSINT, TCC2 - UnB/FCTE)

Caso: {titulo} ({caso_id})
Exportado em: {exportado_em} (UTC)
Entidades: {entidades} | Vínculos: {vinculos}

Arquivos
- entidades.csv: uma linha por entidade (empresa, pessoa, órgão, contrato etc.).
- vinculos.csv: uma linha por vínculo, com os dados das duas pontas (origem e destino). Num
  assistente de importação tabular, mapeie as colunas origem_* para uma entidade, as colunas
  destino_* para outra e a coluna "tipo" para o rótulo da ligação.
- grafo.graphml: o mesmo grafo em GraphML (nós, arestas e atributos).
- manifesto.json: SHA-256 de cada arquivo acima.

Cadeia de custódia
Cada linha traz captura_id, captura_sha256, captura_fonte, captura_origem e
captura_coletado_em: a captura bruta de que a informação saiu. A integridade da captura pode ser
conferida no sistema de origem (GET /capturas/<captura_id>/verificar), que recalcula o SHA-256.

Texto que começaria com =, +, -, @ recebeu um apóstrofo na frente nos CSV, para não ser
interpretado como fórmula por planilhas. O GraphML traz os valores sem essa alteração.

Dados de investigação: trate este arquivo com o mesmo sigilo do caso.
"""


def pacote_zip(dados: DadosDoGrafo) -> Arquivo:
    arquivos = [csv_entidades(dados), csv_vinculos(dados), graphml(dados)]
    manifesto = {
        "caso_id": dados.caso.id,
        "exportado_em": dados.exportado_em,
        "gerado_por": f"buscador-osint {__version__}",
        "entidades": len(dados.entidades),
        "vinculos": len(dados.vinculos),
        "arquivos": [
            {"nome": a.nome, "sha256": a.sha256, "tamanho_bytes": len(a.conteudo)} for a in arquivos
        ],
    }
    leiame = _LEIAME.format(
        titulo=_xml(dados.caso.titulo),
        caso_id=dados.caso.id,
        exportado_em=dados.exportado_em,
        entidades=len(dados.entidades),
        vinculos=len(dados.vinculos),
    )
    saida = io.BytesIO()
    with zipfile.ZipFile(saida, "w", zipfile.ZIP_DEFLATED) as pacote:
        for a in arquivos:
            pacote.writestr(a.nome, a.conteudo)
        pacote.writestr("manifesto.json", json.dumps(manifesto, ensure_ascii=False, indent=2))
        pacote.writestr("LEIAME.txt", leiame)
    return Arquivo(f"caso-{dados.caso.id}-grafo.zip", "application/zip", saida.getvalue())


GERADORES = {
    "graphml": graphml,
    "csv_entidades": csv_entidades,
    "csv_vinculos": csv_vinculos,
    "zip": pacote_zip,
}


def gerar(sessao: Session, caso: Caso, formato: str) -> tuple[Arquivo, DadosDoGrafo]:
    dados = carregar(sessao, caso)
    return GERADORES[formato](dados), dados
