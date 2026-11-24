"""H49: sinais fracos (L2, L14 e L15) que só pesam no alerta de combinação. A L2 (contato
compartilhado por empresas sem sócio em comum) usa o telefone e o correio eletrônico do leiaute
dos dados abertos do CNPJ. Dados só sintéticos (``tests/apoio_lavagem.py``): os telefones usam o
prefixo fictício 5550 e os endereços de e-mail o domínio reservado ``exemplo.invalid``."""

from __future__ import annotations

import pytest

from buscador import tipologias
from buscador.conectores import cnpj_arquivo
from buscador.modelos import Alerta, AlertaDecisao, Entidade
from buscador.resolucao.normalizacao import (
    mascarar_email,
    mascarar_telefone,
    normalizar_email,
    normalizar_telefone,
)
from buscador.tipologias.politicas import POLITICAS
from tests.apoio_lavagem import Cenario, verificar_explicacao
from tests.conftest import cabecalhos, criar_config

SINAIS = ("L2", "L14", "L15")
TELEFONE = "55501234"
EMAIL = "contato@contabil-sintetica.exemplo.invalid"


@pytest.fixture
def cenario(sessao, custodia, config) -> Cenario:
    return Cenario(sessao, custodia, config)


def _tres_no_mesmo_telefone(cenario: Cenario, quantas: int = 3, **contato) -> list[str]:
    """``quantas`` empresas com o mesmo contato e um sócio diferente em cada uma."""
    contato = contato or {"ddd": "61", "telefone": TELEFONE}
    basicos = []
    for n in range(quantas):
        basico = f"9280000{n + 1}"
        cenario.empresa(basico, f"CONTATO IGUAL {n + 1} SINTETICA LTDA", numero=str(n), **contato)
        cenario.socio(basico, f"SOCIO DISTINTO {n + 1} SINTETICO", f"***82820{n}**")
        basicos.append(basico)
    return basicos


# ==== Conector e resolução: o dado de contato ===================================================


def test_h49_conector_cnpj_le_telefones_e_correio_eletronico_do_leiaute():
    linha = [""] * cnpj_arquivo.CAMPOS_ESTABELECIMENTOS
    linha[0:4] = ["92800001", "0001", "00", "1"]
    posicao = {nome: i for i, nome in enumerate(cnpj_arquivo.NOMES_CAMPOS["estabelecimentos"])}
    linha[posicao["ddd_1"]] = "61"
    linha[posicao["telefone_1"]] = TELEFONE
    linha[posicao["ddd_2"]] = "11"
    linha[posicao["telefone_2"]] = "55509876"
    linha[posicao["ddd_fax"]] = "61"
    linha[posicao["fax"]] = "55500000"
    linha[posicao["correio_eletronico"]] = EMAIL.upper()
    registro = cnpj_arquivo._estabelecimento(linha, "captura-sintetica")
    assert (registro.ddd_1, registro.telefone_1) == ("61", TELEFONE)
    assert (registro.ddd_2, registro.telefone_2) == ("11", "55509876")
    assert registro.correio_eletronico == EMAIL.upper()
    # O fax não entra na regra (decisão do autor): o conector continua sem guardá-lo.
    assert not hasattr(registro, "fax")


def test_h49_normalizacao_de_telefone_e_de_email():
    assert normalizar_telefone("61", "5550-1234") == "6155501234"
    assert normalizar_telefone("061", " 55501234 ") == "6155501234"
    assert normalizar_telefone("61", "955501234") == "61955501234"
    # Sem DDD, curto demais, ou um dígito só repetido: não é contato comparável.
    assert normalizar_telefone("", TELEFONE) == ""
    assert normalizar_telefone("61", "5550123") == ""
    assert normalizar_telefone("61", "00000000") == ""
    assert normalizar_email("  Contato@Contabil-Sintetica.EXEMPLO.invalid ") == EMAIL
    assert normalizar_email("sem-arroba.exemplo.invalid") == ""
    assert normalizar_email("a@b") == "" and normalizar_email("") == ""
    assert mascarar_telefone("6155501234") == "(61) ****-**34"
    assert mascarar_telefone("61955501234") == "(61) *****-**34"
    assert mascarar_email(EMAIL) == "c***@contabil-sintetica.exemplo.invalid"


def test_h49_resolucao_guarda_o_contato_mascarado_e_nunca_o_valor_completo(cenario):
    cenario.empresa(
        "92800001", "CONTATO SINTETICA LTDA", ddd="61", telefone=TELEFONE, email=EMAIL.upper()
    )
    cenario.incorporar()
    empresa = cenario.sessao.query(Entidade).filter_by(tipo="empresa").one()
    (estabelecimento,) = empresa.atributos["estabelecimentos"]
    contatos = estabelecimento["contatos"]
    assert [(c["tipo"], c["mascarado"]) for c in contatos] == [
        ("telefone", "(61) ****-**34"),
        ("email", "c***@contabil-sintetica.exemplo.invalid"),
    ]
    assert all(len(c["chave"]) == 64 for c in contatos)
    # Minimização (RNF04): nem o telefone nem o endereço completos ficam nos atributos.
    texto = repr(empresa.atributos).lower()
    assert TELEFONE not in texto and EMAIL not in texto
    # Empresa sem contato no cadastro: a lista existe e fica vazia.
    cenario.empresa("92800002", "SEM CONTATO SINTETICA LTDA", numero="2")
    cenario.incorporar()
    outra = cenario.sessao.query(Entidade).filter_by(identificador="92800002").one()
    assert outra.atributos["estabelecimentos"][0]["contatos"] == []


# ==== L2: contato compartilhado por empresas sem sócio em comum =================================


def test_h49_l2_dispara_com_tres_empresas_sem_socio_comum_no_mesmo_telefone(cenario):
    _tres_no_mesmo_telefone(cenario)
    sinais = cenario.sinais("L2")
    assert len(sinais) == 3  # um sinal por empresa que divide o contato
    sinal = sinais[0]
    verificar_explicacao(sinal, cenario.sessao)
    assert sinal.tipologia == "L2" and POLITICAS["L2"].sinal_fraco is True
    assert sinal.severidade == "baixa"
    p = sinal.parametros
    assert (p["tipo_contato"], p["contato_mascarado"]) == ("telefone", "(61) ****-**34")
    assert (p["empresas"], p["grupos_sem_socio_comum"], p["l2_minimo_empresas"]) == (3, 3, 3)
    assert p["sinal_fraco"] is True
    assert "Sinal fraco" in sinal.motivo and "só pesa no alerta de combinação" in sinal.motivo
    assert "(61) ****-**34" in sinal.motivo and "contador" in sinal.motivo
    # O valor completo não sai da custódia: nem no motivo, nem nas evidências, nem nos parâmetros.
    texto = sinal.motivo + repr(sinal.evidencias) + repr(p)
    assert TELEFONE not in texto
    # A primeira evidência é o estabelecimento da própria empresa, na captura do CNPJ.
    (empresa_id,) = sinal.entidades
    empresa = cenario.sessao.get(Entidade, empresa_id)
    assert empresa.rotulo in sinal.evidencias[0].descricao
    assert sinal.evidencias[0].captura_id == cenario.captura("cnpj_arquivo")
    assert sinal.evidencias[0].fonte == "cnpj_arquivo" and len(sinal.evidencias) == 3
    assert {s.entidades[0] for s in sinais} == {
        e.id for e in cenario.sessao.query(Entidade).filter_by(tipo="empresa")
    }


def test_h49_l2_nao_dispara_quando_as_empresas_tem_socio_em_comum(cenario):
    basicos = _tres_no_mesmo_telefone(cenario)
    # O mesmo sócio nas três: o contato igual se explica pelo quadro, que já é visível no grafo.
    for basico in basicos:
        cenario.socio(basico, "SOCIO COMUM SINTETICO", "***828299**")
    assert cenario.sinais("L2") == []


def test_h49_l2_socio_comum_em_duas_das_tres_conta_um_grupo_so(cenario):
    basicos = _tres_no_mesmo_telefone(cenario)
    for basico in basicos[:2]:
        cenario.socio(basico, "SOCIO COMUM SINTETICO", "***828299**")
    # Dois grupos independentes (as duas do sócio comum e a terceira): abaixo do mínimo de 3.
    assert cenario.sinais("L2") == []
    (primeiro, *_) = cenario.sinais("L2", l2_minimo_empresas=2)
    assert primeiro.parametros["empresas"] == 3
    assert primeiro.parametros["grupos_sem_socio_comum"] == 2


def test_h49_l2_limite_duas_empresas_nao_disparam_e_o_minimo_e_configuravel(cenario):
    _tres_no_mesmo_telefone(cenario, quantas=2)
    assert cenario.sinais("L2") == []
    assert len(cenario.sinais("L2", l2_minimo_empresas=2)) == 2


def test_h49_l2_email_igual_com_grafia_diferente_dispara_e_contato_invalido_nao(cenario):
    grafias = [EMAIL, EMAIL.upper(), f"  {EMAIL.title()} "]
    for n, email in enumerate(grafias):
        basico = f"9281000{n + 1}"
        # Telefone zerado em todas: valor de preenchimento, que não liga empresa nenhuma.
        cenario.empresa(
            basico, f"EMAIL IGUAL {n + 1} SINTETICA", numero=str(n), email=email,
            ddd="61", telefone="00000000",
        )  # fmt: skip
        cenario.socio(basico, f"SOCIO DE EMAIL {n + 1} SINTETICO", f"***83830{n}**")
    sinais = cenario.sinais("L2")
    assert len(sinais) == 3
    assert {s.parametros["tipo_contato"] for s in sinais} == {"email"}
    assert sinais[0].parametros["contato_mascarado"] == "c***@contabil-sintetica.exemplo.invalid"
    assert EMAIL not in sinais[0].motivo.lower()


def test_h49_l2_segundo_telefone_tambem_conta(cenario):
    for n in range(3):
        basico = f"9282000{n + 1}"
        cenario.empresa(
            basico, f"SEGUNDO TELEFONE {n + 1} SINTETICA", numero=str(n),
            ddd="61", telefone=f"5550200{n}", ddd2="61", telefone2=TELEFONE,
        )  # fmt: skip
        cenario.socio(basico, f"SOCIO DO SEGUNDO {n + 1} SINTETICO", f"***84840{n}**")
    assert len(cenario.sinais("L2")) == 3


def test_h49_l2_avisa_quando_a_coleta_e_anterior_ao_dado_de_contato(cenario):
    cenario.empresa("92830001", "COLETA ANTIGA SINTETICA LTDA")
    cenario.incorporar()
    empresa = cenario.sessao.query(Entidade).filter_by(tipo="empresa").one()
    atributos = dict(empresa.atributos)
    atributos["estabelecimentos"] = [
        {k: v for k, v in e.items() if k != "contatos"} for e in atributos["estabelecimentos"]
    ]
    empresa.atributos = atributos
    cenario.sessao.flush()
    from buscador.modelos import Caso

    cenario.alertas("COMB")
    avisos = tipologias.avisos_do_caso(cenario.sessao.get(Caso, cenario.caso_id))
    assert any(a.startswith("L2:") and "1 empresa" in a and "coletar de novo" in a for a in avisos)


# ==== Sinais fracos não geram alerta próprio; só pesam na combinação ============================


def _recente_subcapitalizada_com_sinais(cenario: Cenario, capital: float = 1_000.0) -> None:
    """Uma contratada com T1 e T3 (se ``capital`` for baixo) e os três sinais fracos."""
    secundarias = ",".join(f"47{n:05d}" for n in range(19))
    cenario.empresa(
        "92840001", "ALVO DOS SINAIS SINTETICA LTDA", inicio="20260402", capital=capital,
        secundarias=secundarias, ddd="61", telefone=TELEFONE,
    )  # fmt: skip
    cenario.socio("92840001", "ADMIN IDOSO SINTETICO", "***858501**", faixa="9")
    cenario.contrato("92840001", "2026-06-01", 150_000.0)
    for n in (2, 3):
        basico = f"9284000{n}"
        cenario.empresa(
            basico, f"VIZINHA DE CONTATO {n} SINTETICA", numero=str(n), ddd="61", telefone=TELEFONE
        )
        cenario.socio(basico, f"SOCIO VIZINHO {n} SINTETICO", f"***85850{n}**")


def test_h49_sinais_fracos_nao_geram_alerta_proprio(cenario):
    _recente_subcapitalizada_com_sinais(cenario)
    # Os três sinais existem para a empresa...
    assert {s.tipologia for s in cenario.sinais(*SINAIS)} == set(SINAIS)
    # ...mas nenhuma execução grava alerta de L2, L14 ou L15: nem pedidos, nem na execução toda.
    assert cenario.alertas(*SINAIS) == []
    todos = cenario.alertas()
    assert {a.tipologia for a in todos} & set(SINAIS) == set()
    gravados = cenario.sessao.query(Alerta).filter(Alerta.tipologia.in_(SINAIS)).count()
    assert gravados == 0


def test_h49_sinais_fracos_pesam_na_combinacao_com_a_evidencia_da_captura(cenario):
    _recente_subcapitalizada_com_sinais(cenario, capital=1_000_000_000.0)  # só a T1 (peso 1)
    alertas = cenario.alertas("T1", "COMB")
    (comb,) = [a for a in alertas if a.tipologia == "COMB"]
    p = comb.parametros
    # T1 (1,0) + L2, L14 e L15 (0,5 cada): 4 tipologias, soma 2,5.
    assert (p["tipologias_distintas"], p["soma_pesos"]) == (4, 2.5)
    por_codigo = {c["tipologia"]: c for c in p["componentes"]}
    assert sorted(por_codigo) == ["L14", "L15", "L2", "T1"]
    for codigo in SINAIS:
        c = por_codigo[codigo]
        assert (c["peso"], c["conta"], c["sinal_fraco"]) == (0.5, True, True)
        # Não há alerta próprio para onde navegar: a explicação vai dentro do componente.
        assert c["alerta_id"] is None and c["situacao"] == "sinal"
        assert c["motivo"].startswith("Sinal fraco.") and c["titulo"]
    assert por_codigo["T1"]["alerta_id"] is not None and por_codigo["T1"]["sinal_fraco"] is False
    verificar_explicacao(comb, cenario.sessao)
    assert {e["descricao"][:5].rstrip() for e in comb.evidencias} >= {"[T1]", "[L2]", "[L14]"}
    l2 = [e for e in comb.evidencias if e["descricao"].startswith("[L2]")]
    assert l2[0]["captura_id"] == cenario.captura("cnpj_arquivo") and l2[0]["sha256"]
    assert "[L2]" in comb.motivo and "sem alerta próprio" in comb.motivo
    assert TELEFONE not in comb.motivo + repr(comb.evidencias) + repr(p)


def test_h49_so_sinais_fracos_nao_disparam_a_combinacao(cenario):
    # Os três sinais sem nenhum alerta comum: soma 1,5, abaixo do peso mínimo de 2,0.
    _recente_subcapitalizada_com_sinais(cenario, capital=1_000_000_000.0)
    assert cenario.alertas("COMB") == []
    assert cenario.padrao["comb_pesos"]["L2"] == 0.5
    # Limite: com o peso mínimo em 1,5 a combinação passa a disparar só com os sinais.
    (comb,) = cenario.alertas("COMB", comb_peso_minimo=1.5)
    assert comb.parametros["soma_pesos"] == 1.5
    assert all(c["alerta_id"] is None for c in comb.parametros["componentes"])


def test_h49_peso_zero_tira_o_sinal_da_combinacao(cenario):
    _recente_subcapitalizada_com_sinais(cenario, capital=1_000_000_000.0)
    pesos = {**cenario.padrao["comb_pesos"], "L2": 0.0}
    cenario.padrao = {**cenario.padrao, "comb_pesos": pesos}
    (comb,) = [a for a in cenario.alertas("T1", "COMB") if a.tipologia == "COMB"]
    por_codigo = {c["tipologia"]: c for c in comb.parametros["componentes"]}
    assert (por_codigo["L2"]["conta"], por_codigo["L2"]["peso"]) == (False, 0.0)
    assert comb.parametros["soma_pesos"] == 2.0


def test_h49_alerta_antigo_de_sinal_fraco_sai_da_fila_na_execucao_seguinte(cenario):
    """Bases criadas antes desta mudança têm alertas L14 e L15 gravados: o pendente sem decisão é
    apagado e o que já tinha decisão fica inativo, como na reconciliação das demais."""
    _recente_subcapitalizada_com_sinais(cenario)
    cenario.incorporar()
    pendente = Alerta(
        caso_id=cenario.caso_id, tipologia="L15", chave="antigo-l15", titulo="Antigo L15",
        motivo="Alerta sintético de versão anterior", severidade="baixa", entidades=[],
        evidencias=[], parametros={},
    )  # fmt: skip
    decidido = Alerta(
        caso_id=cenario.caso_id, tipologia="L14", chave="antigo-l14", titulo="Antigo L14",
        motivo="Alerta sintético de versão anterior", severidade="baixa", entidades=[],
        evidencias=[], parametros={}, situacao="confirmado",
    )  # fmt: skip
    cenario.sessao.add_all([pendente, decidido])
    cenario.sessao.flush()
    cenario.sessao.add(
        AlertaDecisao(
            alerta_id=decidido.id,
            caso_id=cenario.caso_id,
            decisao="confirmado",
            justificativa="Decisão sintética de versão anterior",
            usuario="teste",
        )  # fmt: skip
    )
    cenario.sessao.flush()
    pendente_id, decidido_id = pendente.id, decidido.id
    cenario.alertas()
    cenario.sessao.expire_all()
    assert cenario.sessao.get(Alerta, pendente_id) is None
    assert cenario.sessao.get(Alerta, decidido_id).ativo is False


# ==== Catálogo, API e configuração ==============================================================


def test_h49_l2_esta_na_tabela_unica_no_catalogo_e_na_api(cliente, caso_carregado):
    catalogo = cliente.get("/tipologias", headers={"Authorization": "Bearer tok-analista"}).json()
    por_codigo = {t["codigo"]: t for t in catalogo}
    fracos = [t["codigo"] for t in catalogo if t["sinal_fraco"]]
    assert fracos == ["L2", "L14", "L15"]
    l2 = por_codigo["L2"]
    assert (l2["historia"], l2["grupo"], l2["severidade"], l2["peso"]) == (
        "H49", "lavagem", "baixa", 0.5,
    )  # fmt: skip
    assert "4.001/2020" in l2["base"] and "decisão do autor" in l2["base"]
    assert "não gera alerta próprio" in l2["regra"]
    assert [p["nome"] for p in l2["parametros"]] == ["l2_minimo_empresas"]
    assert l2["parametros"][0]["valor"] == 3 and l2["parametros"][0]["por_execucao"] is True
    for codigo in ("L14", "L15"):
        assert "não gera alerta próprio" in por_codigo[codigo]["regra"]
    # Execução pela API: o limiar da L2 é aceito e nenhum sinal fraco volta como alerta.
    r = cliente.post(
        f"/casos/{caso_carregado}/alertas/executar",
        json={"tipologias": ["L2", "L14", "L15", "COMB"], "parametros": {"l2_minimo_empresas": 2}},
        headers=cabecalhos(),
    )
    assert r.status_code == 200, r.text
    assert {a["tipologia"] for a in r.json()} <= {"COMB"}
    r = cliente.get(f"/casos/{caso_carregado}/alertas", headers=cabecalhos())
    assert {a["tipologia"] for a in r.json()} & set(SINAIS) == set()


def test_h49_limiar_da_l2_e_validado_na_execucao_e_na_configuracao(
    cliente, caso_carregado, tmp_path
):
    r = cliente.post(
        f"/casos/{caso_carregado}/alertas/executar",
        json={"parametros": {"l2_minimo_empresas": 1}},
        headers=cabecalhos(),
    )
    assert r.status_code == 422
    with pytest.raises(ValueError):
        tipologias.parametros_efetivos({}, {"l2_minimo_empresas": 1})
    assert tipologias.parametros_efetivos({}, {"l2_minimo_empresas": 2}) == {
        "l2_minimo_empresas": 2
    }
    with pytest.raises(ValueError):
        criar_config(tmp_path, l2_minimo_empresas=1)
