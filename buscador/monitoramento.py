"""Monitoramento contínuo (RF06, H18): o que comparar e que avisos gerar.

Este módulo é a parte pura: descobre as coletas de origem de uma entidade, tira a "fotografia" dos
vínculos dela e calcula as novidades entre duas fotografias. Quem refaz as coletas, grava os
avisos e registra a execução na auditoria é ``buscador.api.monitoramento``; o agendador interno
está em ``buscador.agendador``.

As novidades vêm de duas comparações:
- a fotografia da entidade antes e depois da recoleta (sócio novo, contrato novo, sanção nova,
  termo novo);
- a comparação de capturas do H14 (``buscador.comparacao``), para o que a fotografia não vê:
  registro de sócio que sumiu do arquivo (a resolução nunca apaga arestas).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from buscador.modelos import Aresta, Captura, Coleta, Entidade

TIPOS_AVISO = ("quadro_societario", "contrato_novo", "sancao_nova", "termo_novo")
MAX_AVISOS_POR_EXECUCAO = 50


@dataclass
class Novidade:
    tipo: str
    titulo: str
    descricao: str
    detalhes: dict = field(default_factory=dict)
    captura_ids: list[str] = field(default_factory=list)


def _canonico(parametros: dict | None) -> str:
    return json.dumps(parametros or {}, sort_keys=True, ensure_ascii=False, default=str)


def _parametros_citam(coleta: Coleta, entidade: Entidade, contratos: set[str]) -> bool:
    """A coleta consultou esta entidade mesmo sem ter achado nada (ex.: CEIS sem registro)?"""
    parametros = coleta.parametros or {}
    if coleta.fonte == "portal_transparencia" and entidade.tipo == "empresa":
        codigos = parametros.get("codigoSancionado") or []
        codigos = [codigos] if isinstance(codigos, str) else codigos
        return any(str(c).startswith(entidade.identificador) for c in codigos)
    if coleta.fonte == "pncp_termos":
        pedidos = {
            str(c.get("numero_controle_pncp") or "")
            for c in parametros.get("contratos") or []
            if isinstance(c, dict)
        }
        return bool(pedidos & contratos)
    return False


def coletas_de_origem(sessao: Session, entidade: Entidade) -> list[Coleta]:
    """As coletas a refazer para acompanhar a entidade: as que produziram capturas citadas por
    ela e as que a consultaram pelos parâmetros. Para cada combinação de fonte e parâmetros,
    devolve só a coleta bem-sucedida mais recente (é com ela que a recoleta será comparada)."""
    captura_ids = {f.captura_id for f in entidade.fontes} | {entidade.captura_id}
    contratos: set[str] = set()
    if entidade.tipo == "contrato":
        contratos.add(entidade.identificador)
    else:
        for aresta in sessao.execute(
            select(Aresta).where(
                Aresta.caso_id == entidade.caso_id,
                Aresta.origem_id == entidade.id,
                Aresta.tipo.in_(("fornecedor_de", "contratante_de")),
            )
        ).scalars():
            contratos.add(aresta.chave)
    com_captura = set(
        sessao.execute(
            select(Captura.coleta_id).where(
                Captura.id.in_(captura_ids), Captura.coleta_id.is_not(None)
            )
        ).scalars()
    )
    candidatas = sessao.execute(
        select(Coleta)
        .where(Coleta.caso_id == entidade.caso_id, Coleta.situacao == "sucesso")
        .order_by(Coleta.iniciado_em)
    ).scalars()
    citadas = {
        (c.fonte, _canonico(c.parametros))
        for c in sessao.execute(
            select(Coleta).where(
                Coleta.caso_id == entidade.caso_id,
                or_(
                    Coleta.id.in_(com_captura),
                    Coleta.fonte.in_(("portal_transparencia", "pncp_termos")),
                ),
            )
        ).scalars()
        if c.id in com_captura or _parametros_citam(c, entidade, contratos)
    }
    mais_recente: dict[tuple[str, str], Coleta] = {}
    for coleta in candidatas:
        chave = (coleta.fonte, _canonico(coleta.parametros))
        if chave in citadas:
            mais_recente[chave] = coleta
    return sorted(mais_recente.values(), key=lambda c: (c.fonte, c.iniciado_em))


def fotografia(sessao: Session, entidade: Entidade) -> dict[str, dict[str, dict]]:
    """Vínculos da entidade por categoria: ``{categoria: {chave estável: {rotulo, captura_id}}}``.

    Categorias: ``quadro_societario`` (sócios da empresa e empresas de que a pessoa é sócia),
    ``contrato_novo`` (contratos como fornecedor ou contratante), ``sancao_nova`` (registros do
    CEIS/CNEP) e ``termo_novo`` (termos do contrato; para empresa e órgão, os dos seus contratos).
    """
    sessao.flush()
    foto: dict[str, dict[str, dict]] = {tipo: {} for tipo in TIPOS_AVISO}
    arestas = list(
        sessao.execute(
            select(Aresta).where(
                Aresta.caso_id == entidade.caso_id,
                or_(Aresta.origem_id == entidade.id, Aresta.destino_id == entidade.id),
            )
        ).scalars()
    )
    vizinhos = {
        e.id: e
        for e in sessao.execute(
            select(Entidade).where(
                Entidade.id.in_({a.origem_id for a in arestas} | {a.destino_id for a in arestas})
            )
        ).scalars()
    }
    contratos: list[Entidade] = [entidade] if entidade.tipo == "contrato" else []
    for a in arestas:
        outro = vizinhos.get(a.destino_id if a.origem_id == entidade.id else a.origem_id)
        if outro is None:
            continue
        item = {
            "rotulo": outro.rotulo,
            "identificador": outro.identificador or "",
            "entidade_id": outro.id,
            "captura_id": a.captura_id,
        }
        if a.tipo == "socio_de":
            papel = "sócio" if a.destino_id == entidade.id else "empresa de que é sócio"
            foto["quadro_societario"][f"{a.tipo}:{outro.chave}"] = {**item, "papel": papel}
        elif a.tipo in ("fornecedor_de", "contratante_de") and a.origem_id == entidade.id:
            foto["contrato_novo"][outro.chave] = item
            contratos.append(outro)
        elif a.tipo == "sancionada_em" and a.origem_id == entidade.id:
            foto["sancao_nova"][outro.chave] = item
    for contrato in contratos:
        for termo in (contrato.atributos or {}).get("termos") or []:
            identidade = termo.get("sequencial")
            identidade = identidade if identidade is not None else termo.get("numero")
            foto["termo_novo"][f"{contrato.chave}#{identidade}"] = {
                "rotulo": (
                    f"{termo.get('tipo') or 'Termo'} nº {termo.get('numero') or identidade} do "
                    f"contrato {contrato.identificador}"
                ),
                "identificador": contrato.identificador,
                "entidade_id": contrato.id,
                "captura_id": termo.get("captura_id"),
                "valor_acrescido": termo.get("valor_acrescido"),
            }
    return foto


_TITULOS = {
    "quadro_societario": "Mudança no quadro societário",
    "contrato_novo": "Contrato novo",
    "sancao_nova": "Sanção nova",
    "termo_novo": "Termo de contrato novo",
}


def novidades(entidade: Entidade, antes: dict, depois: dict) -> list[Novidade]:
    """O que existe em ``depois`` e não existia em ``antes``, um aviso por item."""
    achadas: list[Novidade] = []
    for tipo in TIPOS_AVISO:
        for chave in sorted(depois.get(tipo, {}).keys() - antes.get(tipo, {}).keys()):
            item = depois[tipo][chave]
            identificador = f" ({item['identificador']})" if item.get("identificador") else ""
            if tipo == "quadro_societario":
                descricao = (
                    f"Novo vínculo societário de {entidade.rotulo}: {item['rotulo']}"
                    f"{identificador}, como {item.get('papel', 'sócio')}."
                )
            elif tipo == "contrato_novo":
                descricao = f"Novo contrato ligado a {entidade.rotulo}: {item['rotulo']}."
            elif tipo == "sancao_nova":
                descricao = f"Novo registro de sanção de {entidade.rotulo}: {item['rotulo']}."
            else:
                descricao = f"{item['rotulo']} apareceu na recoleta."
            achadas.append(
                Novidade(
                    tipo=tipo,
                    titulo=f"{_TITULOS[tipo]}: {entidade.rotulo}"[:300],
                    descricao=descricao,
                    detalhes={k: v for k, v in item.items() if k != "captura_id"},
                    captura_ids=[item["captura_id"]] if item.get("captura_id") else [],
                )
            )
    return achadas


def remocoes_de_socios(entidade: Entidade, comparacao: dict) -> list[Novidade]:
    """Registros de sócio da empresa que sumiram do arquivo do CNPJ entre a coleta anterior e a
    nova (comparação de capturas do H14). A resolução não apaga arestas; por isso a saída de um
    sócio só aparece aqui."""
    if entidade.tipo != "empresa" or comparacao.get("fonte") != "cnpj_arquivo":
        return []
    prefixo = f"{entidade.identificador}|"
    achadas = []
    for par in comparacao.get("capturas", []):
        if "SOCIO" not in str(par.get("url_ou_arquivo", "")).upper():
            continue
        for diferenca in par.get("diferencas", []):
            registro = str(diferenca.get("registro") or "")
            if diferenca.get("tipo") != "removido" or not registro.startswith(prefixo):
                continue
            achadas.append(
                Novidade(
                    tipo="quadro_societario",
                    titulo=f"Mudança no quadro societário: {entidade.rotulo}"[:300],
                    descricao=(
                        f"O registro de sócio '{registro}' de {entidade.rotulo} constava na "
                        "coleta anterior e não consta mais no arquivo do CNPJ recoletado."
                    ),
                    detalhes={"registro": registro, "mudanca": "removido"},
                    captura_ids=[par["captura_id"], par["anterior_id"]],
                )
            )
    return achadas
