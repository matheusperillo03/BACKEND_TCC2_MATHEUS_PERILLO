"""Comparação de capturas e de coletas (RF07, H14): mesmo hash ou não, e o que mudou.

Compara duas capturas da mesma origem (o mesmo arquivo ou a mesma URL, coletados em momentos
diferentes). Primeiro o SHA-256; se ele difere, as duas cargas brutas guardadas na custódia são
relidas e interpretadas pelo conector da fonte (``Conector.registros_brutos``), e a diferença sai
por registro e por campo. Nada é alterado nem apagado: é só leitura da custódia.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from buscador.conectores import CONECTORES
from buscador.custodia import Custodia
from buscador.modelos import Captura, Coleta
from buscador.privacidade import campo_de_documento, descaracterizar_texto
from buscador.seguranca import texto_de_linha

# Tetos do que a resposta lista (os totais são sempre exatos).
MAX_DIFERENCAS = 200
MAX_CAMPOS_POR_REGISTRO = 40
MAX_TEXTO_VALOR = 200


def _valor(valor: object, campo: str = "") -> str | None:
    """Valor apresentável de um campo da carga bruta. A carga guardada na custódia não muda, mas
    o que sai daqui para a API e para a interface leva todo CPF descaracterizado (RNF04):
    ``texto_de_linha`` já oculta o que tem forma de CPF; num campo de documento
    (``niFornecedor``, ``codigoFormatado``...), qualquer valor de 11 dígitos é ocultado."""
    if valor is None:
        return None
    texto = texto_de_linha(valor)
    if campo_de_documento(campo):
        texto = descaracterizar_texto(texto, contexto_cpf=True)
    return texto[:MAX_TEXTO_VALOR]


def _registros(custodia: Custodia, captura: Captura) -> dict[str, dict[str, object]] | None:
    """Registros da carga guardada, ou ``None`` se ela não puder ser lida ou interpretada."""
    classe = CONECTORES.get(captura.fonte)
    if classe is None:
        return None
    try:
        conteudo = custodia.ler(captura)
    except OSError:
        return None
    try:
        registros = classe.registros_brutos(conteudo, captura.metadados or {})
    except (ValueError, TypeError, KeyError):
        return None
    return registros or None


def comparar_capturas(custodia: Custodia, nova: Captura, anterior: Captura) -> dict:
    mesmo_hash = nova.sha256 == anterior.sha256
    resultado: dict = {
        "captura_id": nova.id,
        "anterior_id": anterior.id,
        "fonte": nova.fonte,
        "url_ou_arquivo": nova.url_ou_arquivo,
        "coletado_em": nova.coletado_em,
        "anterior_coletado_em": anterior.coletado_em,
        "sha256": nova.sha256,
        "anterior_sha256": anterior.sha256,
        "mesmo_hash": mesmo_hash,
        "tamanho_bytes": nova.tamanho_bytes,
        "anterior_tamanho_bytes": anterior.tamanho_bytes,
        "conector": {"nome": nova.conector_nome, "versao": nova.conector_versao},
        "anterior_conector": {"nome": anterior.conector_nome, "versao": anterior.conector_versao},
        "mesma_versao_conector": (nova.conector_nome, nova.conector_versao)
        == (anterior.conector_nome, anterior.conector_versao),
        "registros_comparados": mesmo_hash,
        "registros": {"adicionados": 0, "removidos": 0, "alterados": 0, "iguais": None},
        "campos_alterados": [],
        "diferencas": [],
        "diferencas_omitidas": 0,
        "observacao": None,
    }
    if mesmo_hash:
        resultado["observacao"] = "As duas cargas têm o mesmo SHA-256: o conteúdo é idêntico."
        return resultado

    antes = _registros(custodia, anterior)
    depois = _registros(custodia, nova)
    if antes is None or depois is None:
        resultado["observacao"] = (
            "O SHA-256 difere, mas ao menos uma das cargas não pôde ser lida ou interpretada "
            "como registros da fonte; a comparação ficou só no hash."
        )
        return resultado

    diferencas: list[dict] = []
    campos_alterados: set[str] = set()
    contagem = {"adicionados": 0, "removidos": 0, "alterados": 0, "iguais": 0}
    for chave in sorted(antes.keys() | depois.keys()):
        a, d = antes.get(chave), depois.get(chave)
        if a is None or d is None:
            contagem["adicionados" if a is None else "removidos"] += 1
            diferencas.append(
                {"registro": chave, "tipo": "adicionado" if a is None else "removido", "campos": []}
            )
            continue
        campos = [
            {
                "campo": campo,
                "antes": _valor(a.get(campo), campo),
                "depois": _valor(d.get(campo), campo),
            }
            for campo in sorted(a.keys() | d.keys())
            if a.get(campo) != d.get(campo)
        ]
        if not campos:
            contagem["iguais"] += 1
            continue
        contagem["alterados"] += 1
        campos_alterados.update(c["campo"] for c in campos)
        diferencas.append(
            {"registro": chave, "tipo": "alterado", "campos": campos[:MAX_CAMPOS_POR_REGISTRO]}
        )
    for item in diferencas:
        item["registro"] = _valor(item["registro"])
    resultado["registros_comparados"] = True
    resultado["registros"] = contagem
    resultado["campos_alterados"] = sorted(campos_alterados)
    resultado["diferencas"] = diferencas[:MAX_DIFERENCAS]
    resultado["diferencas_omitidas"] = max(0, len(diferencas) - MAX_DIFERENCAS)
    if not diferencas:
        resultado["observacao"] = (
            "O SHA-256 difere, mas os registros interpretados são iguais: a diferença está em "
            "bytes que o conector não lê (ordem, espaços ou campos fora do layout)."
        )
    return resultado


def captura_anterior(sessao: Session, captura: Captura) -> Captura | None:
    """A captura mais recente do mesmo caso, da mesma fonte e da mesma origem, feita antes."""
    return (
        sessao.execute(
            select(Captura)
            .where(
                Captura.caso_id == captura.caso_id,
                Captura.fonte == captura.fonte,
                Captura.url_ou_arquivo == captura.url_ou_arquivo,
                Captura.coletado_em < captura.coletado_em,
                Captura.id != captura.id,
            )
            .order_by(Captura.coletado_em.desc())
            .limit(1)
        )
        .scalars()
        .first()
    )


def capturas_da_coleta(sessao: Session, coleta_id: str) -> list[Captura]:
    return list(
        sessao.execute(
            select(Captura)
            .where(Captura.coleta_id == coleta_id)
            .order_by(Captura.coletado_em, Captura.id)
        ).scalars()
    )


def comparar_coletas(sessao: Session, custodia: Custodia, nova: Coleta, anterior: Coleta) -> dict:
    """Pareia as capturas das duas coletas pela origem (``url_ou_arquivo``) e compara cada par."""
    anteriores: dict[str, Captura] = {}
    for captura in capturas_da_coleta(sessao, anterior.id):
        anteriores.setdefault(captura.url_ou_arquivo, captura)
    pares = []
    sem_anterior = []
    for captura in capturas_da_coleta(sessao, nova.id):
        par = anteriores.pop(captura.url_ou_arquivo, None)
        if par is None:
            sem_anterior.append(captura.id)
        else:
            pares.append(comparar_capturas(custodia, captura, par))
    sem_correspondente = [c.id for c in anteriores.values()]
    return {
        "coleta_id": nova.id,
        "anterior_id": anterior.id,
        "fonte": nova.fonte,
        "conector": {"nome": nova.conector_nome, "versao": nova.conector_versao},
        "anterior_conector": {"nome": anterior.conector_nome, "versao": anterior.conector_versao},
        "mesmo_conteudo": bool(pares)
        and all(p["mesmo_hash"] for p in pares)
        and not sem_anterior
        and not sem_correspondente,
        "capturas_iguais": sum(p["mesmo_hash"] for p in pares),
        "capturas_diferentes": sum(not p["mesmo_hash"] for p in pares),
        "capturas": pares,
        "capturas_sem_anterior": sem_anterior,
        "capturas_anteriores_sem_correspondente": sem_correspondente,
    }
