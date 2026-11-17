"""Tipologias do horizonte 2 (T5 a T10). Mesmo contrato das demais: função pura sobre o
``Contexto``, alerta com regra, motivo em texto com os números reais e evidências ligadas às
capturas. Os parâmetros legais e as listas de termos, com as fontes, estão em ``legislacao``.

Todo alerta é pista (RF12). Nas tipologias que cruzam pessoas (T7 e T9), o alerta nunca afirma
identidade: o CPF dos sócios no CNPJ é descaracterizado (só os seis dígitos centrais são
públicos), então a regra compara esses dígitos E o nome normalizado e fala em "possível
coincidência", com o motivo explícito. Os registros continuam como entidades separadas (RF02).
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date

from buscador.modelos import Aresta, Entidade
from buscador.resolucao.normalizacao import normalizar_nome
from buscador.seguranca import numero_finito
from buscador.tipologias import legislacao
from buscador.tipologias.motor import (
    AlertaDetectado,
    Contexto,
    _captura_do_atributo,
    _data,
    _por_item,
    formatar_data,
    formatar_moeda,
    formatar_numero,
)

MAX_ITENS_NO_MOTIVO = 10


def _atr(entidade: Entidade) -> dict:
    return entidade.atributos or {}


def _arestas(ctx: Contexto, tipo: str) -> Iterable[Aresta]:
    return (a for a in ctx.arestas if a.tipo == tipo)


def _lista(textos: list[str], limite: int = MAX_ITENS_NO_MOTIVO) -> str:
    visiveis = "; ".join(textos[:limite])
    return visiveis + (f"; e mais {len(textos) - limite}" if len(textos) > limite else "")


def _valor_contratado(contrato: Entidade) -> tuple[float | None, str]:
    """Valor inicial do contrato (``valorInicial``); sem ele, o valor global, dizendo qual foi."""
    inicial = numero_finito(_atr(contrato).get("valorInicial"))
    if inicial is not None:
        return inicial, "valor inicial"
    return numero_finito(_atr(contrato).get("valor_global")), "valor global"


# ---- T5: fracionamento de despesa (H32) -------------------------------------------------------


def tipologia_t5(ctx: Contexto, parametros: dict) -> list[AlertaDetectado]:
    limites: dict = parametros["t5_limites_dispensa"]
    minimo = int(parametros["t5_minimo_contratacoes"])
    criterio = str(parametros["t5_criterio_natureza"])
    usa_fornecedor = criterio in ("fornecedor_e_categoria", "fornecedor")
    usa_categoria = criterio in ("fornecedor_e_categoria", "categoria")

    grupos: dict[tuple, list[tuple[Entidade, Entidade, float, str]]] = {}
    for fornecedor, contrato in ctx.contratos_com_fornecedor():
        atributos = _atr(contrato)
        assinatura = _data(atributos.get("data_assinatura"))
        valor, base = _valor_contratado(contrato)
        if assinatura is None or valor is None or valor <= 0:
            continue
        exercicio = str(assinatura.year)
        if exercicio not in limites:
            continue
        categoria = atributos.get("categoriaProcessoId")
        if usa_categoria and categoria is None:
            # Sem classificação do objeto, a "mesma natureza" não pode ser aferida por categoria.
            continue
        chave = (
            str(atributos.get("orgao_cnpj") or ""),
            str(atributos.get("codigoUnidade") or ""),
            exercicio,
            fornecedor.id if usa_fornecedor else "",
            str(categoria) if usa_categoria else "",
        )
        grupos.setdefault(chave, []).append((fornecedor, contrato, valor, base))

    def regra(grupo: tuple[tuple, list]) -> AlertaDetectado | None:
        (orgao_cnpj, unidade, exercicio, _fornecedor, _categoria), itens = grupo
        categorias = {_atr(c).get("categoriaProcessoId") for _, c, _, _ in itens}
        # Inciso I só quando todas as contratações do grupo são de obras ou de serviços de
        # engenharia; em qualquer outro caso vale o limite do inciso II, que é o menor.
        inciso_i = bool(categorias) and all(c in legislacao.CATEGORIAS_INCISO_I for c in categorias)
        inciso = "inciso_i" if inciso_i else "inciso_ii"
        limite = float(limites[exercicio][inciso])
        # Só entram as contratações que, isoladas, ficam abaixo do limite ("valores inferiores a").
        abaixo = sorted(
            (i for i in itens if i[2] < limite),
            key=lambda i: (_atr(i[1]).get("data_assinatura") or "", i[1].identificador),
        )
        if len(abaixo) < minimo:
            return None
        soma = sum(i[2] for i in abaixo)
        if soma < limite:
            return None
        primeiro = abaixo[0][1]
        orgao = _atr(primeiro).get("orgao_razao_social") or "órgão não informado"
        nome_unidade = _atr(primeiro).get("unidadeOrgao") or ""
        fornecedores = {f.id: f for f, _, _, _ in abaixo}
        nomes_categoria = sorted(
            {str(_atr(c).get("categoriaProcessoNome") or "não informada") for _, c, _, _ in abaixo}
        )
        romano = "I" if inciso_i else "II"
        descricao_natureza = legislacao.CRITERIOS_NATUREZA[criterio]
        contratos_texto = _lista(
            [
                f"{c.identificador} ({formatar_moeda(v)}, {b}, assinado em "
                f"{formatar_data(_atr(c).get('data_assinatura'))}, fornecedor {f.rotulo})"
                for f, c, v, b in abaixo
            ]
        )
        motivo = (
            f"Possível fracionamento de despesa (indício a verificar): {orgao} (CNPJ "
            f"{orgao_cnpj or 'não informado'}), unidade {unidade or 'não informada'}"
            f"{' - ' + nome_unidade if nome_unidade else ''}, firmou no exercício de {exercicio} "
            f"{len(abaixo)} contratações com objetos tratados como de mesma natureza "
            f"({descricao_natureza}; categoria: {', '.join(nomes_categoria)}), cada uma abaixo "
            f"do limite de dispensa do art. 75, {romano}, da Lei nº 14.133/2021 "
            f"({formatar_moeda(limite)} em {exercicio}), mas que somam {formatar_moeda(soma)}: "
            f"{contratos_texto}. O art. 75, § 1º, manda somar o que for despendido no exercício "
            "pela unidade gestora com objetos de mesma natureza. Limitações: a 'mesma natureza' "
            "foi aferida pelo critério configurado, e não pelo ramo de atividade; o PNCP não diz "
            "no contrato se cada contratação foi por dispensa; o limite em dobro do § 2º "
            "(consórcio público e agência executiva) não é considerado."
        )
        evidencias = [
            ctx.evidencia(
                c.captura_id,
                f"Contrato {c.identificador}: {b} {formatar_moeda(v)}, assinatura em "
                f"{formatar_data(_atr(c).get('data_assinatura'))}, unidade "
                f"{_atr(c).get('codigoUnidade') or 'não informada'}, categoria "
                f"{_atr(c).get('categoriaProcessoNome') or 'não informada'} (PNCP)",
            )
            for _, c, v, b in abaixo
        ]
        return AlertaDetectado(
            tipologia="T5",
            titulo=(
                f"Possível fracionamento: {len(abaixo)} contratações de {exercicio} somam "
                f"{formatar_moeda(soma)}"
            ),
            motivo=motivo,
            entidades=[c.id for _, c, _, _ in abaixo] + sorted(fornecedores),
            evidencias=evidencias,
            discriminador="|".join(grupo[0]),
            parametros={
                "exercicio": exercicio,
                "orgao_cnpj": orgao_cnpj,
                "codigo_unidade": unidade,
                "inciso": romano,
                "limite_dispensa": limite,
                "fonte_limite": limites[exercicio].get("fonte", ""),
                "soma": round(soma, 2),
                "contratacoes": len(abaixo),
                "criterio_natureza": criterio,
                "t5_minimo_contratacoes": minimo,
            },
        )

    return _por_item("T5", sorted(grupos.items(), key=lambda g: g[0]), regra)


# ---- T6: empresa sancionada (H33) -------------------------------------------------------------


def tipologia_t6(ctx: Contexto, parametros: dict) -> list[AlertaDetectado]:
    sancoes_por_empresa: dict[str, list[Entidade]] = {}
    for a in _arestas(ctx, "sancionada_em"):
        sancao = ctx.entidades.get(a.destino_id)
        if sancao is not None and sancao.tipo == "sancao":
            sancoes_por_empresa.setdefault(a.origem_id, []).append(sancao)

    casos = [
        (fornecedor, contrato, sancao)
        for fornecedor, contrato in ctx.contratos_com_fornecedor()
        if fornecedor.tipo == "empresa"
        for sancao in sancoes_por_empresa.get(fornecedor.id, [])
    ]

    def regra(caso: tuple[Entidade, Entidade, Entidade]) -> AlertaDetectado | None:
        fornecedor, contrato, sancao = caso
        # Só os cadastros com período de sanção: o CEPIM (sem período) é tratado na L10 e na L16.
        if _atr(sancao).get("cadastro") not in ("CEIS", "CNEP"):
            return None
        assinatura = _data(_atr(contrato).get("data_assinatura"))
        inicio = _data(_atr(sancao).get("data_inicio"))
        fim = _data(_atr(sancao).get("data_fim"))
        # Sem a data do contrato ou do início da sanção, a vigência não pode ser aferida.
        if assinatura is None or inicio is None:
            return None
        if assinatura < inicio or (fim is not None and assinatura > fim):
            return None
        s = _atr(sancao)
        cadastro = s.get("cadastro") or "cadastro de sanções"
        orgao = _atr(contrato).get("orgao_razao_social") or "órgão não informado"
        vigencia = (
            f"de {formatar_data(inicio.isoformat())} a {formatar_data(fim.isoformat())}"
            if fim
            else f"desde {formatar_data(inicio.isoformat())}, sem data final informada"
        )
        motivo = (
            f"A empresa {fornecedor.rotulo} (CNPJ {s.get('cnpj_sancionado')}) assinou o contrato "
            f"{contrato.identificador} com {orgao} em {formatar_data(assinatura.isoformat())}, "
            f"data em que tinha registro vigente no {cadastro}: "
            f"{s.get('tipo_sancao') or 'sanção sem tipo informado'}, aplicada por "
            f"{s.get('orgao_sancionador') or 'órgão sancionador não informado'}, {vigencia}"
            f"{' (processo ' + s['numero_processo'] + ')' if s.get('numero_processo') else ''}. "
            "A regra dispara quando a data de assinatura cai dentro do período da sanção; o "
            "alcance da sanção (se impede contratar com este órgão) deve ser conferido no "
            "registro."
        )
        return AlertaDetectado(
            tipologia="T6",
            titulo=f"Contratada com sanção vigente no {cadastro} ({contrato.identificador})",
            motivo=motivo,
            entidades=[fornecedor.id, contrato.id, sancao.id],
            evidencias=[
                ctx.evidencia(
                    sancao.captura_id,
                    f"{cadastro}, registro {s.get('id_registro') or 'sem id'}: "
                    f"{s.get('tipo_sancao') or 'sanção'} {vigencia} (Portal da Transparência)",
                ),
                ctx.evidencia(
                    contrato.captura_id,
                    f"Contrato {contrato.identificador}: data de assinatura "
                    f"{formatar_data(assinatura.isoformat())} (PNCP)",
                ),
            ],
            parametros={
                "cadastro": cadastro,
                "id_registro": s.get("id_registro"),
                "data_inicio_sancao": inicio.isoformat(),
                "data_fim_sancao": fim.isoformat() if fim else None,
                "data_assinatura": assinatura.isoformat(),
            },
        )

    return _por_item("T6", casos, regra)


# ---- Cruzamento de pessoas por CPF descaracterizado e nome (T7 e T9) ---------------------------


def _identidade_parcial(pessoa: Entidade) -> tuple[str, str]:
    atributos = _atr(pessoa)
    return (
        str(atributos.get("cpf_mascarado") or ""),
        str(atributos.get("nome_normalizado") or normalizar_nome(pessoa.rotulo)),
    )


def _coincide(a: Entidade, b: Entidade) -> bool:
    """Mesmos dígitos visíveis do CPF descaracterizado E mesmo nome normalizado. Só o nome, ou só
    os dígitos, não basta."""
    cpf_a, nome_a = _identidade_parcial(a)
    cpf_b, nome_b = _identidade_parcial(b)
    return bool(cpf_a and nome_a and cpf_a == cpf_b and nome_a == nome_b)


AVISO_COINCIDENCIA = (
    "É uma POSSÍVEL COINCIDÊNCIA, não uma identificação: o CPF dos sócios nos dados abertos do "
    "CNPJ é descaracterizado (os três primeiros dígitos e os dois verificadores são ocultos), "
    "então a regra só pôde comparar os seis dígitos visíveis e o nome normalizado. Homônimos "
    "com os mesmos seis dígitos existem. Os dois registros continuam separados (não foram "
    "fundidos) e a confirmação exige fonte com o CPF completo."
)


def _socios_por_empresa(ctx: Contexto) -> dict[str, list[tuple[Entidade, Aresta]]]:
    socios: dict[str, list[tuple[Entidade, Aresta]]] = {}
    for a in _arestas(ctx, "socio_de"):
        socio = ctx.entidades.get(a.origem_id)
        if socio is not None and socio.tipo == "pessoa":
            socios.setdefault(a.destino_id, []).append((socio, a))
    return socios


def _contratos_por_par(ctx: Contexto) -> dict[tuple[str, str], list[Entidade]]:
    """(fornecedor, órgão) -> contratos, pelas arestas ``contratado_por``."""
    pares: dict[tuple[str, str], list[Entidade]] = {}
    for a in _arestas(ctx, "contratado_por"):
        contrato = ctx.entidades.get((a.atributos or {}).get("contrato_id") or "")
        if contrato is not None:
            pares.setdefault((a.origem_id, a.destino_id), []).append(contrato)
    return pares


# ---- T7: sócio servidor do órgão contratante (H34) --------------------------------------------


def tipologia_t7(ctx: Contexto, parametros: dict) -> list[AlertaDetectado]:
    servidores: dict[str, list[tuple[Entidade, Aresta]]] = {}
    for a in _arestas(ctx, "servidor_de"):
        servidor = ctx.entidades.get(a.origem_id)
        if servidor is not None:
            servidores.setdefault(a.destino_id, []).append((servidor, a))
    socios = _socios_por_empresa(ctx)

    casos = []
    for (empresa_id, orgao_id), contratos in sorted(_contratos_por_par(ctx).items()):
        empresa, orgao = ctx.entidades.get(empresa_id), ctx.entidades.get(orgao_id)
        if empresa is None or orgao is None or empresa.tipo != "empresa":
            continue
        for socio, aresta_socio in socios.get(empresa_id, []):
            for servidor, aresta_servidor in servidores.get(orgao_id, []):
                if _coincide(socio, servidor):
                    casos.append(
                        (empresa, orgao, contratos, socio, aresta_socio, servidor, aresta_servidor)
                    )

    def regra(caso) -> AlertaDetectado:
        empresa, orgao, contratos, socio, aresta_socio, servidor, aresta_servidor = caso
        cpf, nome = _identidade_parcial(socio)
        contratos = sorted(contratos, key=lambda c: c.identificador)
        siape = (aresta_servidor.atributos or {}).get("codigo_orgao_siape") or "não informado"
        motivo = (
            f"Possível coincidência entre sócio e servidor: {socio.rotulo} (CPF descaracterizado "
            f"{cpf}) é sócio de {empresa.rotulo} (CNPJ básico {empresa.identificador}), "
            f"contratada por {orgao.rotulo} em {len(contratos)} contrato(s) "
            f"({_lista([c.identificador for c in contratos])}); e há um servidor de nome "
            f"{servidor.rotulo} com os mesmos dígitos visíveis do CPF ({cpf}) no órgão de código "
            f"SIAPE {siape}, que o investigador declarou corresponder a esse órgão contratante. "
            f"Coincidem o nome normalizado ('{nome}') e os seis dígitos centrais do CPF. "
            + AVISO_COINCIDENCIA
        )
        evidencias = [
            ctx.evidencia(
                aresta_socio.captura_id,
                f"Sócio {socio.rotulo} ({cpf}) de {empresa.rotulo} (dados abertos do CNPJ, Sócios)",
            ),
            ctx.evidencia(
                aresta_servidor.captura_id,
                f"Servidor {servidor.rotulo} ({cpf}) no órgão de código SIAPE {siape} "
                "(Portal da Transparência, servidores)",
            ),
        ] + [
            ctx.evidencia(
                c.captura_id,
                f"Contrato {c.identificador} de {empresa.rotulo} com {orgao.rotulo} (PNCP)",
            )
            for c in contratos[:MAX_ITENS_NO_MOTIVO]
        ]
        return AlertaDetectado(
            tipologia="T7",
            titulo=(
                f"Possível coincidência: sócio de {empresa.rotulo} e servidor de {orgao.rotulo}"
            ),
            motivo=motivo,
            entidades=[socio.id, servidor.id, empresa.id, orgao.id] + [c.id for c in contratos],
            evidencias=evidencias,
            discriminador=f"{socio.id}|{servidor.id}|{empresa.id}|{orgao.id}",
            parametros={
                "criterio": "seis dígitos visíveis do CPF e nome normalizado iguais",
                "cpf_descaracterizado": cpf,
                "nome_normalizado": nome,
                "codigo_orgao_siape": siape,
                "contratos": len(contratos),
                "identidade_confirmada": False,
            },
        )

    return _por_item("T7", casos, regra)


# ---- T8: aditivos acima do limite (H35) -------------------------------------------------------


def tipologia_t8(ctx: Contexto, parametros: dict) -> list[AlertaDetectado]:
    limite_padrao = float(parametros["t8_percentual_limite"])
    limite_reforma = float(parametros["t8_percentual_limite_reforma"])

    def regra(par: tuple[Entidade, Entidade]) -> AlertaDetectado | None:
        fornecedor, contrato = par
        atributos = _atr(contrato)
        termos = atributos.get("termos") or []
        # A base é o valor inicial. ``valorGlobal`` e ``valorAcumulado`` NÃO são usados: não foi
        # confirmado na documentação do PNCP se eles já somam os termos.
        inicial = numero_finito(atributos.get("valorInicial"))
        if not termos or inicial is None or inicial <= 0:
            return None
        acrescimos = []
        for termo in termos:
            valor = numero_finito(termo.get("valor_acrescido"))
            # Termo marcado como "sem acréscimo ou supressão" não conta (reajuste, vigência).
            if valor is None or valor <= 0 or termo.get("acrescimo_supressao") is False:
                continue
            acrescimos.append((termo, valor))
        if not acrescimos:
            return None
        reforma = bool(
            legislacao.EXPRESSAO_REFORMA.search(normalizar_nome(str(atributos.get("objeto") or "")))
        )
        limite = limite_reforma if reforma else limite_padrao
        soma = sum(v for _, v in acrescimos)
        percentual = soma / inicial * 100
        if percentual <= limite:
            return None
        orgao = atributos.get("orgao_razao_social") or "órgão não informado"
        termos_texto = _lista(
            [
                f"{t.get('tipo') or 'termo'} nº {t.get('numero') or t.get('sequencial')} "
                f"({formatar_moeda(v)}, assinado em {formatar_data(t.get('data_assinatura'))})"
                for t, v in acrescimos
            ]
        )
        motivo = (
            f"O contrato {contrato.identificador} de {fornecedor.rotulo} com {orgao} tem valor "
            f"inicial de {formatar_moeda(inicial)} e {len(acrescimos)} termo(s) com valor "
            f"acrescido que somam {formatar_moeda(soma)}, ou {formatar_numero(percentual)}% do "
            f"valor inicial: {termos_texto}. O art. 125 da Lei nº 14.133/2021 limita os "
            f"acréscimos a {formatar_numero(limite)}% do valor inicial atualizado do contrato"
            + (
                " (limite de reforma de edifício ou de equipamento, identificada pelo objeto do "
                "contrato)"
                if reforma
                else " (o limite de 50% vale só para reforma de edifício ou de equipamento, que "
                "o objeto deste contrato não indica)"
            )
            + ". Indício a verificar: o campo valorAcrescido do PNCP guarda acréscimo ou "
            "supressão sem distinguir um do outro; a base usada é o valor inicial sem "
            "atualização monetária; e valorGlobal/valorAcumulado não foram usados."
        )
        evidencias = [
            ctx.evidencia(
                contrato.captura_id,
                f"Contrato {contrato.identificador}: valor inicial "
                f"{formatar_moeda(inicial)} (PNCP)",
            )
        ] + [
            ctx.evidencia(
                t["captura_id"],
                f"Termo nº {t.get('numero') or t.get('sequencial')} do contrato "
                f"{contrato.identificador}: valor acrescido {formatar_moeda(v)} (PNCP, termos de "
                "contrato)",
            )
            for t, v in acrescimos
            if t.get("captura_id") in ctx.capturas
        ]
        return AlertaDetectado(
            tipologia="T8",
            titulo=(
                f"Aditivos de {formatar_numero(percentual)}% no contrato {contrato.identificador}"
            ),
            motivo=motivo,
            entidades=[fornecedor.id, contrato.id],
            evidencias=evidencias,
            parametros={
                "valor_inicial": inicial,
                "soma_valor_acrescido": round(soma, 2),
                "percentual_observado": round(percentual, 2),
                "percentual_limite": limite,
                "reforma_identificada": reforma,
                "termos_considerados": len(acrescimos),
                "termos_total": len(termos),
            },
        )

    return _por_item("T8", ctx.contratos_com_fornecedor(), regra)


# ---- T9: doador de campanha contratado (H39) --------------------------------------------------


def _ente_coincide(candidato: Entidade, orgao: Entidade) -> str | None:
    """Descrição do ente em comum entre o candidato e o órgão, ou ``None``.

    Critério territorial e explícito (decisão do autor):
    - município: o nome da unidade eleitoral do candidato (``NM_UE``) e a UF são os do órgão, e o
      órgão é municipal (``esferaId = M``) ou não tem esfera informada;
    - estado ou Distrito Federal: a unidade eleitoral do candidato é a própria UF (``SG_UE`` igual
      a ``SG_UF``) e o órgão é estadual ou distrital (``E``/``D``) da mesma UF;
    - União: a unidade eleitoral é ``BR`` e o órgão é federal (``F``).
    PENDENTE de conferência no leiame do TSE: que ``SG_UE`` traz a sigla da UF nas eleições
    gerais e ``BR`` na presidencial. Se não for assim, a regra apenas deixa de disparar.
    """
    c, o = _atr(candidato), _atr(orgao)
    uf_candidato = normalizar_nome(c.get("uf"))
    sg_ue = normalizar_nome(c.get("sg_ue"))
    nm_ue = normalizar_nome(c.get("nm_ue"))
    uf_orgao = normalizar_nome(o.get("uf"))
    municipio_orgao = normalizar_nome(o.get("municipio"))
    esfera = str(o.get("esfera") or "").upper()
    if esfera == "F":
        return "União" if sg_ue == "BR" else None
    if not uf_candidato or uf_candidato != uf_orgao:
        return None
    if esfera in ("E", "D"):
        return f"UF {uf_candidato}" if sg_ue == uf_candidato else None
    if nm_ue and nm_ue == municipio_orgao and sg_ue != uf_candidato:
        return f"município de {c.get('nm_ue')}/{c.get('uf')}"
    return None


def tipologia_t9(ctx: Contexto, parametros: dict) -> list[AlertaDetectado]:
    eleito = {normalizar_nome(s) for s in parametros["t9_situacoes_eleito"]}
    socios = _socios_por_empresa(ctx)
    contratos_por_par = _contratos_por_par(ctx)
    orgaos_por_empresa: dict[str, set[str]] = {}
    for empresa_id, orgao_id in contratos_por_par:
        orgaos_por_empresa.setdefault(empresa_id, set()).add(orgao_id)

    # (doador, candidato, empresa, órgão) -> doações, contratos e o vínculo societário usado.
    grupos: dict[tuple[str, str, str, str], dict] = {}
    for doacao in _arestas(ctx, "doou_para"):
        doador = ctx.entidades.get(doacao.origem_id)
        candidato = ctx.entidades.get(doacao.destino_id)
        if doador is None or candidato is None:
            continue
        if normalizar_nome(_atr(candidato).get("situacao_turno")) not in eleito:
            continue
        if doador.tipo == "empresa":
            vinculos = [(doador, None, None)]
        else:
            vinculos = [
                (ctx.entidades[empresa_id], socio, aresta)
                for empresa_id, lista in socios.items()
                for socio, aresta in lista
                if empresa_id in ctx.entidades and _coincide(doador, socio)
            ]
        atributos_doacao = doacao.atributos or {}
        referencia = _data(atributos_doacao.get("data"))
        if referencia is None:
            ano = str(_atr(candidato).get("ano") or "")
            referencia = date(int(ano), 1, 1) if ano.isdigit() else None
        for empresa, socio, aresta_socio in vinculos:
            for orgao_id in sorted(orgaos_por_empresa.get(empresa.id, ())):
                orgao = ctx.entidades.get(orgao_id)
                ente = _ente_coincide(candidato, orgao) if orgao is not None else None
                if ente is None:
                    continue
                posteriores = [
                    c
                    for c in contratos_por_par[(empresa.id, orgao_id)]
                    if referencia is not None
                    and (assinatura := _data(_atr(c).get("data_assinatura"))) is not None
                    and assinatura >= referencia
                ]
                if not posteriores:
                    continue
                grupo = grupos.setdefault(
                    (doador.id, candidato.id, empresa.id, orgao_id),
                    {"doacoes": [], "contratos": {}, "socio": socio, "aresta": aresta_socio},
                )
                grupo["ente"] = ente
                grupo["doacoes"].append(doacao)
                grupo["contratos"].update({c.id: c for c in posteriores})

    def regra(item: tuple[tuple[str, str, str, str], dict]) -> AlertaDetectado:
        (doador_id, candidato_id, empresa_id, orgao_id), grupo = item
        doador, candidato = ctx.entidades[doador_id], ctx.entidades[candidato_id]
        empresa, orgao = ctx.entidades[empresa_id], ctx.entidades[orgao_id]
        c = _atr(candidato)
        doacoes = sorted(grupo["doacoes"], key=lambda a: (a.atributos or {}).get("data") or "")
        contratos = sorted(grupo["contratos"].values(), key=lambda e: e.identificador)
        total = sum(numero_finito((a.atributos or {}).get("valor")) or 0 for a in doacoes)
        socio = grupo["socio"]
        doacoes_texto = _lista(
            [
                f"{formatar_moeda(numero_finito((a.atributos or {}).get('valor')))} em "
                f"{formatar_data((a.atributos or {}).get('data'))}"
                for a in doacoes
            ]
        )
        contratos_texto = _lista(
            [
                f"{k.identificador} ({formatar_moeda(_valor_contratado(k)[0])}, assinado em "
                f"{formatar_data(_atr(k).get('data_assinatura'))})"
                for k in contratos
            ]
        )
        candidato_texto = (
            f"{candidato.rotulo} (candidato a {c.get('cargo') or 'cargo não informado'} em "
            f"{c.get('ano')}, {c.get('nm_ue') or c.get('sg_ue') or ''}/{c.get('uf') or ''}, "
            f"situação '{c.get('situacao_turno')}')"
        )
        if socio is None:
            quem = (
                f"A empresa {empresa.rotulo} (CNPJ básico {empresa.identificador}) doou "
                f"{formatar_moeda(total)} ({doacoes_texto}) à campanha de {candidato_texto}"
            )
            ressalva = (
                "O doador pessoa jurídica é identificado pelo CNPJ, que é público e completo."
            )
        else:
            cpf, nome = _identidade_parcial(socio)
            quem = (
                f"Possível coincidência entre doador e sócio: {doador.rotulo} (CPF "
                f"descaracterizado {cpf}) doou {formatar_moeda(total)} ({doacoes_texto}) à "
                f"campanha de {candidato_texto}; e {empresa.rotulo} (CNPJ básico "
                f"{empresa.identificador}) tem um sócio de mesmo nome normalizado ('{nome}') e "
                f"mesmos dígitos visíveis do CPF ({cpf})"
            )
            ressalva = AVISO_COINCIDENCIA
        motivo = (
            f"{quem}. Depois da doação, a empresa assinou {len(contratos)} contrato(s) com "
            f"{orgao.rotulo}, órgão do mesmo ente do candidato eleito ({grupo['ente']}): "
            f"{contratos_texto}. {ressalva} Indício a verificar: o vínculo com o ente é "
            "territorial (unidade eleitoral do candidato e localização e esfera do órgão) e não "
            "prova que o eleito tenha influído na contratação; doação de campanha registrada "
            "no TSE é, em si, lícita."
        )
        evidencias = [
            ctx.evidencia(
                a.captura_id,
                f"Receita de campanha: {doador.rotulo} doou "
                f"{formatar_moeda(numero_finito((a.atributos or {}).get('valor')))} em "
                f"{formatar_data((a.atributos or {}).get('data'))} a {candidato.rotulo} "
                "(TSE, prestação de contas)",
            )
            for a in doacoes[:MAX_ITENS_NO_MOTIVO]
        ]
        evidencias.append(
            ctx.evidencia(
                _captura_do_atributo(ctx, candidato, "situacao_turno", "consulta_cand"),
                f"Candidato {candidato.rotulo}: situação '{c.get('situacao_turno')}' (TSE, "
                "candidatos)",
            )
        )
        if socio is not None:
            evidencias.append(
                ctx.evidencia(
                    grupo["aresta"].captura_id,
                    f"Sócio {socio.rotulo} ({_identidade_parcial(socio)[0]}) de {empresa.rotulo} "
                    "(dados abertos do CNPJ, Sócios)",
                )
            )
        evidencias += [
            ctx.evidencia(
                k.captura_id,
                f"Contrato {k.identificador} de {empresa.rotulo} com {orgao.rotulo}, assinado em "
                f"{formatar_data(_atr(k).get('data_assinatura'))} (PNCP)",
            )
            for k in contratos[:MAX_ITENS_NO_MOTIVO]
        ]
        entidades = [doador.id, candidato.id, empresa.id, orgao.id] + [k.id for k in contratos]
        if socio is not None:
            entidades.append(socio.id)
        return AlertaDetectado(
            tipologia="T9",
            titulo=(
                ("Doadora" if socio is None else "Possível coincidência: doador sócio de empresa")
                + f" contratada por {orgao.rotulo} após doação a {candidato.rotulo}"
            ),
            motivo=motivo,
            entidades=list(dict.fromkeys(entidades)),
            evidencias=evidencias,
            discriminador="|".join(item[0]),
            parametros={
                "tipo_doador": "pessoa_juridica" if socio is None else "pessoa_fisica",
                "identidade_confirmada": socio is None,
                "total_doado": round(total, 2),
                "doacoes": len(doacoes),
                "contratos": len(contratos),
                "ente": grupo["ente"],
                "situacao_candidato": c.get("situacao_turno"),
                "ano_eleicao": c.get("ano"),
            },
        )

    return _por_item("T9", sorted(grupos.items(), key=lambda g: g[0]), regra)


# ---- T10: cláusula potencialmente restritiva em edital (H36) -----------------------------------

CONTEXTO_TRECHO = 120
MAX_OCORRENCIAS = 20


def _posicao(texto: str, indice: int) -> tuple[int, int]:
    """Linha e coluna (a partir de 1) do índice de caractere no texto."""
    linha = texto.count("\n", 0, indice) + 1
    coluna = indice - (texto.rfind("\n", 0, indice) + 1) + 1
    return linha, coluna


def tipologia_t10(ctx: Contexto, parametros: dict) -> list[AlertaDetectado]:
    regras = [
        (r, r.compilar()) for r in legislacao.regras_clausulas(parametros["t10_expressoes_extras"])
    ]
    casos = [
        (edital, regra_, padrao)
        for edital in sorted(ctx.entidades.values(), key=lambda e: e.rotulo)
        if edital.tipo == "edital"
        for regra_, padrao in regras
    ]

    def regra(caso) -> AlertaDetectado | None:
        edital, clausula, padrao = caso
        captura_id = _atr(edital).get("captura_texto_id") or edital.captura_id
        texto = ctx.texto_da_captura(captura_id)
        if texto is None:
            return None
        ocorrencias = []
        for achado in padrao.finditer(texto):
            linha, coluna = _posicao(texto, achado.start())
            inicio = max(0, achado.start() - CONTEXTO_TRECHO)
            fim = min(len(texto), achado.end() + CONTEXTO_TRECHO)
            ocorrencias.append(
                {
                    "linha": linha,
                    "coluna": coluna,
                    "posicao": achado.start(),
                    "trecho": achado.group(0),
                    # Contexto literal do arquivo (com as quebras de linha originais).
                    "contexto": texto[inicio:fim],
                }
            )
        if not ocorrencias:
            return None
        total = len(ocorrencias)
        ocorrencias = ocorrencias[:MAX_OCORRENCIAS]
        nome = _atr(edital).get("nome_arquivo") or edital.rotulo
        citacoes = _lista(
            [
                f"linha {o['linha']}, coluna {o['coluna']} (caractere {o['posicao']}): "
                f'"{" ".join(o["contexto"].split())}"'
                for o in ocorrencias
            ],
            limite=5,
        )
        motivo = (
            f"O edital {nome} contém {total} ocorrência(s) da expressão '{clausula.descricao}', "
            f"tratada como cláusula potencialmente restritiva ({clausula.fonte}). Trecho(s) "
            f"literal(is): {citacoes}. A detecção é textual, por expressão regular explícita "
            f"({clausula.expressao}), sem modelo de linguagem: a expressão pode aparecer em "
            "contexto que não é exigência de habilitação, e a análise jurídica da cláusula é do "
            "investigador."
        )
        return AlertaDetectado(
            tipologia="T10",
            titulo=f"{clausula.descricao} no edital {nome}",
            motivo=motivo,
            entidades=[edital.id],
            evidencias=[
                ctx.evidencia(
                    captura_id,
                    f"Edital {nome}, linha {o['linha']}, coluna {o['coluna']} (caractere "
                    f'{o["posicao"]}): trecho literal "{o["trecho"]}"',
                )
                for o in ocorrencias
            ],
            discriminador=clausula.id,
            parametros={
                "regra": clausula.id,
                "expressao": clausula.expressao,
                "fonte_regra": clausula.fonte,
                "ocorrencias_total": total,
                "ocorrencias": ocorrencias,
            },
        )

    return _por_item("T10", casos, regra)


TIPOLOGIAS_H2 = {
    "T5": tipologia_t5,
    "T6": tipologia_t6,
    "T7": tipologia_t7,
    "T8": tipologia_t8,
    "T9": tipologia_t9,
    "T10": tipologia_t10,
}
