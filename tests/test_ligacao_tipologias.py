"""Tipologias que dependem da ligação de registros de pessoas (decisão do autor de 23/11/2026,
opção (a)): L9 (H46, eleito possivelmente sócio de empresa contratada pelo próprio ente durante o
mandato) e L13 (H52, despesa de campanha com fornecedor de perfil incompatível).

Só dados sintéticos: os CPFs são sorteados a cada execução (``tests/sinteticos.py``) e entram já
reduzidos aos seis dígitos centrais, como saem dos conectores. Os registros passam pelo mesmo
``Resolvedor`` da coleta. Cada regra tem caso positivo, negativo e de limite."""

from __future__ import annotations

import json

import pytest
from sqlalchemy import select

from buscador import tipologias
from buscador.conectores.base import RegistroCandidato, RegistroContrato, RegistroDespesa
from buscador.config import ErroConfiguracao
from buscador.modelos import Caso, Entidade, Sugestao
from buscador.tipologias import catalogo, combinacao, lavagem
from buscador.tipologias.politicas import POLITICAS
from tests.apoio_lavagem import SIAPE, Cenario, cnpj, verificar_explicacao
from tests.conftest import criar_config
from tests.sinteticos import cpf_sintetico, mascarado

NOME = "FULANO SINTETICO DO MANDATO"
EMPRESA = "91000001"
OUTRA_EMPRESA = "91000002"
ORGAO_DF = "93200001000100"
ORGAO_GO = "93200002000100"
ORGAO_SEM_ENTE = "93200003000100"
SQ = "990000000022"


@pytest.fixture
def cenario(sessao, custodia, config) -> Cenario:
    return Cenario(sessao, custodia, config)


@pytest.fixture
def cpf() -> str:
    return cpf_sintetico()


def candidato(
    cenario: Cenario,
    cpf: str | None,
    *,
    ano: str = "2022",
    sq: str = SQ,
    nome: str = NOME,
    situacao: str = "ELEITO",
    cargo: str = "DEPUTADO DISTRITAL",
    uf: str = "DF",
    sg_ue: str = "DF",
    nm_ue: str = "DISTRITO FEDERAL",
) -> None:
    cenario.registros.append(
        RegistroCandidato(
            captura_id=cenario.captura("tse_arquivo"),
            ano=ano,
            sq_candidato=sq,
            nome=nome,
            cargo=cargo,
            partido="PSIN",
            uf=uf,
            sg_ue=sg_ue,
            nm_ue=nm_ue,
            situacao_turno=situacao,
            # O conector só entrega o CPF reduzido aos seis dígitos centrais.
            cpf_parcial=mascarado(cpf) if cpf else "",
        )
    )


def contrato(
    cenario: Cenario,
    basico: str,
    assinatura: str,
    *,
    orgao: str = ORGAO_DF,
    uf: str | None = "DF",
    esfera: str | None = "D",
    municipio: str | None = "Brasilia",
    valor: float = 80_000.0,
) -> str:
    cenario._contratos += 1
    numero = f"{orgao}-2-{cenario._contratos:06d}/{assinatura[:4]}"
    cenario.registros.append(
        RegistroContrato(
            captura_id=cenario.captura("pncp"),
            numero_controle_pncp=numero,
            ni_fornecedor=cnpj(basico),
            tipo_pessoa_fornecedor="PJ",
            nome_fornecedor="",
            orgao_cnpj=orgao,
            orgao_razao_social=f"ORGAO SINTETICO {orgao[4:8]}",
            objeto="Objeto sintético",
            valor_global=valor,
            data_assinatura=assinatura,
            url_fonte="simulado://pncp/cenario-de-teste",
            extras={"ufOrgao": uf, "esferaId": esfera, "municipioOrgao": municipio},
        )
    )
    return numero


def despesa(
    cenario: Cenario,
    basico: str,
    data: str,
    valor: float,
    *,
    chave: str,
    sq: str = SQ,
    ano: str = "2022",
    cnae: str = "1813001",
    nome_candidato: str = NOME,
) -> None:
    cenario.registros.append(
        RegistroDespesa(
            captura_id=cenario.captura("tse_arquivo"),
            ano=ano,
            sq_candidato=sq,
            nome_candidato=nome_candidato,
            cnpj_fornecedor=cnpj(basico),
            nome_fornecedor=f"FORNECEDOR SINTETICO {basico}",
            cnae=cnae,
            cnae_descricao="Atividade sintética informada na prestação de contas",
            valor=valor,
            data=data,
            descricao="DESPESA SINTETICA DE CAMPANHA",
            chave=chave,
        )
    )


def decidir(cenario: Cenario, situacao: str) -> None:
    """A decisão do investigador vale para os dois sentidos do par (como na rota)."""
    cenario.incorporar()
    for sugestao in cenario.sessao.execute(
        select(Sugestao).where(Sugestao.caso_id == cenario.caso_id)
    ).scalars():
        sugestao.situacao = situacao
    cenario.sessao.flush()


def avisos(cenario: Cenario, codigo: str) -> list[str]:
    return tipologias.avisos_do_caso(cenario.sessao.get(Caso, cenario.caso_id), [codigo])


def texto_do_alerta(alerta) -> str:
    return json.dumps(
        [alerta.titulo, alerta.motivo, alerta.parametros, alerta.evidencias], ensure_ascii=False
    )


def eleito_socio_de_contratada(cenario: Cenario, cpf: str, assinatura: str = "2024-06-10") -> None:
    """Empresa com um sócio, candidato eleito em 2022 com o mesmo nome e os mesmos seis dígitos, e
    contrato da empresa com órgão distrital do DF."""
    cenario.empresa(EMPRESA, "CONTRATADA SINTETICA DO MANDATO LTDA")
    cenario.socio(EMPRESA, NOME, mascarado(cpf))
    candidato(cenario, cpf)
    contrato(cenario, EMPRESA, assinatura)


# ==== L9 (H46): eleito possivelmente sócio de contratada do próprio ente =========================


def test_h46_l9_eleito_com_sugestao_pendente_e_contrato_no_mandato_com_o_proprio_ente_dispara(
    cenario, cpf
):
    eleito_socio_de_contratada(cenario, cpf)
    (alerta,) = cenario.alertas("L9")
    verificar_explicacao(alerta, cenario.sessao)
    assert alerta.severidade == POLITICAS["L9"].severidade
    assert cenario.rotulos(alerta) >= {NOME, "CONTRATADA SINTETICA DO MANDATO LTDA"}
    tipos = [cenario.sessao.get(Entidade, e).tipo for e in alerta.entidades]
    assert tipos[:4] == ["candidato", "pessoa", "empresa", "orgao"] and "contrato" in tipos
    # O motivo diz o que foi comparado, que a ligação está pendente e que não houve fusão.
    assert "seis dígitos" in alerta.motivo and mascarado(cpf) in alerta.motivo
    assert "ainda não foi confirmada" in alerta.motivo and "não foram fundidos" in alerta.motivo
    assert "01/01/2023" in alerta.motivo and "31/12/2026" in alerta.motivo
    assert "10/06/2024" in alerta.motivo and "UF DF" in alerta.motivo
    assert alerta.parametros["sugestao_situacao"] == "pendente"
    assert alerta.parametros["identidade_confirmada"] is False
    assert alerta.parametros["l9_anos_mandato"] == 4
    assert (alerta.parametros["mandato_inicio"], alerta.parametros["mandato_fim"]) == (
        "2023-01-01",
        "2026-12-31",
    )
    assert alerta.parametros["contratos"] == 1 and alerta.parametros["ente"] == "UF DF"
    # Evidências das três fontes: candidato (TSE), sócio (CNPJ) e contrato (PNCP).
    assert [e["fonte"] for e in alerta.evidencias] == ["tse_arquivo", "cnpj_arquivo", "pncp"]
    # Nem o CPF inteiro nem o resumo do título aparecem no alerta.
    assert cpf not in texto_do_alerta(alerta)
    assert "chave_pessoa" not in texto_do_alerta(alerta)
    assert "titulo" not in texto_do_alerta(alerta).lower()


def test_h46_l9_sugestao_confirmada_muda_o_motivo_e_descartada_tira_o_alerta(cenario, cpf):
    eleito_socio_de_contratada(cenario, cpf)
    (pendente,) = cenario.alertas("L9")
    decidir(cenario, "confirmado")
    (confirmado,) = cenario.alertas("L9")
    # É o mesmo alerta (mesma chave): a decisão da sugestão só muda o texto.
    assert confirmado.id == pendente.id
    assert confirmado.parametros["sugestao_situacao"] == "confirmado"
    assert "foi confirmada pelo investigador" in confirmado.motivo
    assert "não foram fundidos" in confirmado.motivo
    decidir(cenario, "descartado")
    assert cenario.alertas("L9") == []
    # Um sentido do par descartado basta para não alertar.
    decidir(cenario, "pendente")
    primeira = cenario.sessao.execute(select(Sugestao)).scalars().first()
    primeira.situacao = "descartado"
    cenario.sessao.flush()
    assert cenario.alertas("L9") == []


def test_h46_l9_candidato_nao_eleito_nao_dispara_e_a_regra_de_eleito_e_a_da_t9(cenario, cpf):
    cenario.empresa(EMPRESA, "CONTRATADA SINTETICA DO MANDATO LTDA")
    cenario.socio(EMPRESA, NOME, mascarado(cpf))
    candidato(cenario, cpf, situacao="NÃO ELEITO")
    contrato(cenario, EMPRESA, "2024-06-10")
    assert cenario.alertas("L9") == []
    assert len(cenario.alertas("L9", t9_situacoes_eleito=["NÃO ELEITO"])) == 1
    # Suplente não é eleito nos padrões.
    assert "SUPLENTE" not in cenario.padrao["t9_situacoes_eleito"]


@pytest.mark.parametrize(
    ("assinatura", "dispara"),
    [
        ("2022-12-31", False),  # antes do mandato (ainda no ano da eleição)
        ("2023-01-01", True),  # primeiro dia
        ("2026-12-31", True),  # último dia
        ("2027-01-01", False),  # depois
    ],
)
def test_h46_l9_limites_do_mandato_de_quatro_anos(cenario, cpf, assinatura, dispara):
    eleito_socio_de_contratada(cenario, cpf, assinatura)
    assert len(cenario.alertas("L9")) == (1 if dispara else 0)


def test_h46_l9_duracao_do_mandato_e_parametro(cenario, cpf):
    eleito_socio_de_contratada(cenario, cpf, "2029-03-01")
    assert cenario.alertas("L9") == []
    (alerta,) = cenario.alertas("L9", l9_anos_mandato=8)
    assert alerta.parametros["mandato_fim"] == "2030-12-31"
    assert alerta.parametros["l9_anos_mandato"] == 8
    # A duração pode ser sobrescrita por execução, dentro da faixa.
    assert tipologias.parametros_efetivos({}, {"l9_anos_mandato": 8}) == {"l9_anos_mandato": 8}
    with pytest.raises(ValueError, match="l9_anos_mandato"):
        tipologias.parametros_efetivos({}, {"l9_anos_mandato": 0})


def test_h46_l9_orgao_de_outro_ente_nao_dispara(cenario, cpf):
    cenario.empresa(EMPRESA, "CONTRATADA SINTETICA DO MANDATO LTDA")
    cenario.socio(EMPRESA, NOME, mascarado(cpf))
    candidato(cenario, cpf)
    # Órgão estadual de outra UF, órgão federal e órgão municipal do DF: não são o ente do
    # deputado distrital.
    contrato(cenario, EMPRESA, "2024-06-10", orgao=ORGAO_GO, uf="GO", esfera="E")
    contrato(cenario, EMPRESA, "2024-06-11", orgao="93200004000100", esfera="F")
    contrato(cenario, EMPRESA, "2024-06-12", orgao="93200005000100", esfera="M")
    assert cenario.alertas("L9") == []
    assert avisos(cenario, "L9") == []


def test_h46_l9_orgao_sem_uf_ou_esfera_nao_dispara_e_gera_aviso(cenario, cpf):
    cenario.empresa(EMPRESA, "CONTRATADA SINTETICA DO MANDATO LTDA")
    cenario.socio(EMPRESA, NOME, mascarado(cpf))
    candidato(cenario, cpf)
    contrato(
        cenario, EMPRESA, "2024-06-10", orgao=ORGAO_SEM_ENTE, uf=None, esfera=None, municipio=None
    )
    assert cenario.alertas("L9") == []
    (aviso,) = avisos(cenario, "L9")
    assert aviso.startswith("L9 não avaliada") and ORGAO_SEM_ENTE in aviso
    assert "UF" in aviso and "esfera" in aviso
    # O aviso não leva dado pessoal.
    assert NOME not in aviso and mascarado(cpf) not in aviso


def test_h46_l9_sem_sugestao_de_candidato_e_socio_nao_ha_alerta(cenario, cpf):
    cenario.empresa(EMPRESA, "CONTRATADA SINTETICA DO MANDATO LTDA")
    # Homônimo: mesmo nome, outros seis dígitos. E outra pessoa com os mesmos dígitos.
    cenario.socio(EMPRESA, NOME, mascarado(cpf_sintetico()))
    cenario.socio(EMPRESA, "BELTRANA SINTETICA DE OUTRO NOME", mascarado(cpf))
    candidato(cenario, cpf)
    contrato(cenario, EMPRESA, "2024-06-10")
    # Servidor com o mesmo nome e os mesmos dígitos gera sugestão, mas não é sócio.
    cenario.servidor(NOME, mascarado(cpf))
    assert cenario.alertas("L9") == []
    assert SIAPE and cenario.sessao.execute(select(Sugestao)).scalars().first() is not None


def test_h46_l9_eleito_sem_cpf_divulgado_nao_e_avaliado_e_gera_aviso(cenario, cpf):
    cenario.empresa(EMPRESA, "CONTRATADA SINTETICA DO MANDATO LTDA")
    cenario.socio(EMPRESA, NOME, mascarado(cpf))
    # Em 2024 o TSE não divulga o CPF: não há seis dígitos para comparar.
    candidato(cenario, None, ano="2024", cargo="PREFEITO", sg_ue="97012", nm_ue="BRASILIA")
    contrato(cenario, EMPRESA, "2025-06-10", esfera="M")
    assert cenario.alertas("L9") == []
    (aviso,) = avisos(cenario, "L9")
    assert "1 candidato(s) eleito(s) sem CPF divulgado" in aviso and NOME not in aviso


def test_h46_l9_um_alerta_por_candidatura_socio_empresa_e_orgao_com_os_contratos_juntos(
    cenario, cpf
):
    eleito_socio_de_contratada(cenario, cpf)
    contrato(cenario, EMPRESA, "2025-02-01", valor=20_000.0)
    cenario.empresa(OUTRA_EMPRESA, "SEGUNDA CONTRATADA SINTETICA LTDA")
    cenario.socio(OUTRA_EMPRESA, NOME, mascarado(cpf))
    contrato(cenario, OUTRA_EMPRESA, "2025-03-01")
    alertas = cenario.alertas("L9")
    assert sorted(a.parametros["contratos"] for a in alertas) == [1, 2]
    dois = next(a for a in alertas if a.parametros["contratos"] == 2)
    assert dois.parametros["valor_contratado"] == 100_000.0
    assert "10/06/2024" in dois.motivo and "01/02/2025" in dois.motivo


# ==== L13 (H52): despesa de campanha com fornecedor de perfil incompatível =======================


def test_h52_l13_fornecedor_aberto_pouco_antes_da_despesa_dispara_com_as_despesas_somadas(
    cenario, cpf
):
    cenario.empresa(EMPRESA, "FORNECEDORA RECENTE SINTETICA LTDA", inicio="20220701")
    candidato(cenario, cpf)
    despesa(cenario, EMPRESA, "2022-08-20", 12_000.0, chave="1")
    despesa(cenario, EMPRESA, "2022-09-10", 8_000.5, chave="2")
    (alerta,) = cenario.alertas("L13")
    verificar_explicacao(alerta, cenario.sessao)
    assert alerta.severidade == POLITICAS["L13"].severidade
    tipos = [cenario.sessao.get(Entidade, e).tipo for e in alerta.entidades]
    assert tipos == ["candidato", "empresa"]
    assert alerta.parametros["sinais"] == ["recem_aberta"]
    assert alerta.parametros["total_despesas"] == 20_000.5 and alerta.parametros["despesas"] == 2
    assert alerta.parametros["menor_intervalo_dias"] == 50
    assert alerta.parametros["l13_dias_abertura"] == 180
    assert "R$ 20.000,50" in alerta.motivo and "01/07/2022" in alerta.motivo
    assert "50 dias" in alerta.motivo and "180 dias" in alerta.motivo
    assert "não conclusão" in alerta.motivo
    # Evidências: a abertura (CNPJ) e cada despesa (TSE).
    assert [e["fonte"] for e in alerta.evidencias] == [
        "cnpj_arquivo",
        "tse_arquivo",
        "tse_arquivo",
    ]
    assert cpf not in texto_do_alerta(alerta)


def test_h52_l13_limite_despesa_no_dia_do_limiar_nao_dispara_e_o_limiar_e_proprio(cenario, cpf):
    cenario.empresa(EMPRESA, "FORNECEDORA SINTETICA LTDA", inicio="20220101")
    candidato(cenario, cpf)
    despesa(cenario, EMPRESA, "2022-06-30", 1_000.0, chave="1")  # 180 dias depois
    assert cenario.alertas("L13") == []
    (alerta,) = cenario.alertas("L13", l13_dias_abertura=181)
    assert alerta.parametros["menor_intervalo_dias"] == 180
    # O limiar da L13 não é o da T1: mudar o da T1 não muda a L13.
    assert cenario.alertas("L13", t1_dias_minimos=3650) == []
    assert cenario.padrao["l13_dias_abertura"] == cenario.padrao["t1_dias_minimos"] == 180


def test_h52_l13_so_as_despesas_dentro_do_intervalo_entram_na_soma(cenario, cpf):
    cenario.empresa(EMPRESA, "FORNECEDORA SINTETICA LTDA", inicio="20220401")
    candidato(cenario, cpf)
    despesa(cenario, EMPRESA, "2022-08-01", 500.0, chave="1")  # 122 dias
    despesa(cenario, EMPRESA, "2022-10-25", 900.0, chave="2")  # 207 dias
    (alerta,) = cenario.alertas("L13")
    assert alerta.parametros["total_despesas"] == 500.0 and alerta.parametros["despesas"] == 1
    assert alerta.parametros["despesas_do_par"] == 2


def test_h52_l13_lista_de_cnae_vazia_desliga_o_criterio_e_avisa(cenario, cpf):
    cenario.empresa(EMPRESA, "CONSTRUTORA ANTIGA SINTETICA LTDA", cnae="4120400")
    candidato(cenario, cpf)
    despesa(cenario, EMPRESA, "2022-08-20", 12_000.0, chave="1")
    assert cenario.padrao["l13_cnaes_campanha"] == []
    assert cenario.alertas("L13") == []
    (aviso,) = avisos(cenario, "L13")
    assert "critério do CNAE está desligado" in aviso and "L13_CNAES_CAMPANHA" in aviso
    assert "decisão do autor" in aviso


def test_h52_l13_sem_despesa_no_caso_nao_ha_aviso(cenario):
    cenario.empresa(EMPRESA, "EMPRESA SINTETICA LTDA")
    assert cenario.alertas("L13") == []
    assert avisos(cenario, "L13") == []


def test_h52_l13_cnae_do_cadastro_fora_da_lista_dispara_e_dentro_da_lista_nao(cenario, cpf):
    cenario.empresa(EMPRESA, "CONSTRUTORA ANTIGA SINTETICA LTDA", cnae="4120400")
    cenario.empresa(OUTRA_EMPRESA, "GRAFICA ANTIGA SINTETICA LTDA", cnae="1813001")
    candidato(cenario, cpf)
    # A prestação de contas informa outro CNAE para a construtora: vale o do cadastro.
    despesa(cenario, EMPRESA, "2022-08-20", 12_000.0, chave="1", cnae="1813001")
    despesa(cenario, OUTRA_EMPRESA, "2022-08-21", 3_000.0, chave="2", cnae="4120400")
    # A lista aceita o código inteiro ou um prefixo (aqui, a divisão 18).
    (alerta,) = cenario.alertas("L13", l13_cnaes_campanha=["18"])
    assert cenario.rotulos(alerta) == {NOME, "CONSTRUTORA ANTIGA SINTETICA LTDA"}
    assert alerta.parametros["sinais"] == ["cnae_fora_da_lista"]
    assert alerta.parametros["cnae_usado"] == "4120400"
    assert alerta.parametros["origem_do_cnae"] == "cadastro_cnpj"
    assert alerta.parametros["l13_cnaes_campanha"] == ["18"]
    assert "4120400" in alerta.motivo and "cadastro do CNPJ" in alerta.motivo
    assert any(
        e["fonte"] == "cnpj_arquivo" and "CNAE principal 4120400" in e["descricao"]
        for e in alerta.evidencias
    )
    assert avisos(cenario, "L13") == []
    assert cenario.alertas("L13", l13_cnaes_campanha=["1813001", "4120400"]) == []


def test_h52_l13_sem_cadastro_usa_o_cnae_da_prestacao_de_contas_e_avisa_da_abertura(cenario, cpf):
    candidato(cenario, cpf)
    # O fornecedor não foi coletado no CNPJ: só existe pelo arquivo de despesas.
    despesa(cenario, EMPRESA, "2022-08-20", 700.0, chave="1", cnae="4120400")
    despesa(cenario, EMPRESA, "2022-08-25", 300.0, chave="2", cnae="4120400")
    (alerta,) = cenario.alertas("L13", l13_cnaes_campanha=["1813001"])
    assert alerta.parametros["origem_do_cnae"] == "prestacao_de_contas"
    assert alerta.parametros["cnae_usado"] == "4120400"
    assert alerta.parametros["total_despesas"] == 1000.0
    assert "prestação de contas" in alerta.motivo
    assert {e["fonte"] for e in alerta.evidencias} == {"tse_arquivo"}
    assert any("CNAE informado na prestação de contas" in e["descricao"] for e in alerta.evidencias)
    (aviso,) = avisos(cenario, "L13")
    assert "1 fornecedor(es)" in aviso and "data de início de atividade" in aviso


def test_h52_l13_os_dois_sinais_no_mesmo_alerta_e_um_alerta_por_candidato_e_fornecedor(
    cenario, cpf
):
    cenario.empresa(EMPRESA, "CONSTRUTORA RECENTE SINTETICA LTDA", inicio="20220701")
    candidato(cenario, cpf)
    candidato(cenario, cpf_sintetico(), sq="990000000023", nome="SICRANA SINTETICA CANDIDATA")
    despesa(cenario, EMPRESA, "2022-08-20", 100.0, chave="1")
    despesa(
        cenario,
        EMPRESA,
        "2022-08-22",
        250.0,
        chave="2",
        sq="990000000023",
        nome_candidato="SICRANA SINTETICA CANDIDATA",
    )
    alertas = cenario.alertas("L13", l13_cnaes_campanha=["1813001"])
    assert len(alertas) == 2
    assert {a.parametros["total_despesas"] for a in alertas} == {100.0, 250.0}
    for alerta in alertas:
        assert alerta.parametros["sinais"] == ["cnae_fora_da_lista", "recem_aberta"]


def test_h52_l13_despesa_sem_data_nao_avalia_a_abertura_e_avisa(cenario, cpf):
    cenario.empresa(EMPRESA, "FORNECEDORA RECENTE SINTETICA LTDA", inicio="20220701")
    candidato(cenario, cpf)
    despesa(cenario, EMPRESA, "", 100.0, chave="1")
    assert cenario.alertas("L13", l13_cnaes_campanha=["4120400"]) == []
    (aviso,) = avisos(cenario, "L13")
    assert "1 despesa(s) sem data" in aviso


# ==== Registro das duas tipologias: catálogo, políticas, parâmetros e configuração ===============


def test_h46_h52_l9_e_l13_estao_no_registro_no_catalogo_nas_politicas_e_na_combinacao(config):
    assert {"L9", "L13"} <= set(tipologias.TIPOLOGIAS) and {"L9", "L13"} <= set(POLITICAS)
    ordem = tipologias.ordem_tipologias()
    assert ordem.index("L8") < ordem.index("L9") < ordem.index("L10") < ordem.index("L13")
    por_codigo = {t["codigo"]: t for t in catalogo.catalogo(config.parametros_tipologias())}
    l9, l13 = por_codigo["L9"], por_codigo["L13"]
    assert (l9["historia"], l13["historia"]) == ("H46", "H52")
    assert (l9["grupo"], l13["grupo"]) == ("lavagem", "lavagem")
    assert {p["nome"]: p["valor"] for p in l9["parametros"]} == {
        "l9_anos_mandato": 4,
        "t9_situacoes_eleito": ["ELEITO", "ELEITO POR QP", "ELEITO POR MÉDIA"],
    }
    assert {p["nome"]: p["valor"] for p in l13["parametros"]} == {
        "l13_dias_abertura": 180,
        "l13_cnaes_campanha": [],
    }
    por_execucao = {p["nome"]: p["por_execucao"] for t in (l9, l13) for p in t["parametros"]}
    assert por_execucao == {
        "l9_anos_mandato": True,
        "t9_situacoes_eleito": False,
        "l13_dias_abertura": True,
        "l13_cnaes_campanha": False,
    }
    # Bases só com o que os documentos da monografia citam; limiares rotulados.
    assert "FATF (2011)" in l9["base"] and "§ 44" in l9["base"] and "Velasco" in l9["base"]
    assert "Carta Circular BCB nº 4.001/2020" in l13["base"] and "XIV, d" in l13["base"]
    for item in (l9, l13):
        assert "decisão do autor" in item["base"].lower()
        assert item["politica_erro"].startswith("Prefere evitar")
    # O que não entra fica dito ao investigador.
    assert "PEP" in l9["regra"] and "fachada" in l13["regra"]
    # Combinação: pesam como as demais (não são sinal fraco nem composição).
    assert (combinacao.pesos_padrao()["L9"], combinacao.pesos_padrao()["L13"]) == (1.0, 1.0)
    assert lavagem.parametros_padrao()["l9_anos_mandato"] == 4
    assert lavagem.parametros_padrao()["l13_dias_abertura"] == 180
    assert lavagem.parametros_padrao()["l13_cnaes_campanha"] == []


def test_h46_h52_parametros_da_l9_e_da_l13_vem_da_configuracao_e_sao_validados(tmp_path):
    config = criar_config(
        tmp_path, l9_anos_mandato=8, l13_dias_abertura=90, l13_cnaes_campanha="1813-0/01, 73.11"
    )
    parametros = config.parametros_tipologias()
    assert parametros["l9_anos_mandato"] == 8 and parametros["l13_dias_abertura"] == 90
    # Pontuação do CNAE é aceita; sobra só o código ou o prefixo, em ordem.
    assert parametros["l13_cnaes_campanha"] == ["1813001", "7311"]
    for invalido in ("grafica", "1", "12345678"):
        with pytest.raises(ErroConfiguracao, match="L13_CNAES_CAMPANHA"):
            criar_config(tmp_path, l13_cnaes_campanha=invalido).validar()
    assert tipologias.parametros_efetivos({}, {"l13_dias_abertura": 30}) == {
        "l13_dias_abertura": 30
    }
    with pytest.raises(ValueError, match="desconhecido"):
        # A lista de CNAE é estruturada: só pela configuração do operador.
        tipologias.parametros_efetivos({}, {"l13_cnaes_campanha": 1})
