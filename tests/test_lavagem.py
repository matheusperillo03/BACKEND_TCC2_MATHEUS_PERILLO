"""Tipologias de lavagem e ocultação (L) sobre dados já coletados: L1, L3, L4, L5, L7, L8, L10
(parte CEIS/CNEP) e os sinais fracos L14 e L15. Cada regra tem caso positivo, negativo e de limite.

Só dados sintéticos (``tests/apoio_lavagem.py``) e CPF sempre descaracterizado. Os critérios e as
fontes estão em ``buscador/tipologias/lavagem.py``.
"""

from __future__ import annotations

import pytest

from buscador import tipologias
from buscador.config import ErroConfiguracao
from buscador.tipologias import lavagem
from buscador.tipologias.politicas import POLITICAS
from tests.apoio_lavagem import SIAPE, Cenario, verificar_explicacao
from tests.conftest import cabecalhos, criar_config

CODIGOS_CADASTRAIS = ["L1", "L3", "L4", "L5", "L7", "L8", "L10", "L14", "L15"]


@pytest.fixture
def cenario(sessao, custodia, config) -> Cenario:
    return Cenario(sessao, custodia, config)


def _bases(prefixo: int, quantidade: int) -> list[str]:
    return [f"{prefixo}{n:04d}" for n in range(1, quantidade + 1)]


# ==== L1: endereço de registro em massa =========================================================


def test_rf04_l1_dispara_para_cinco_empresas_ativas_no_mesmo_endereco_sem_contrato(cenario):
    for n, basico in enumerate(_bases(9100, 5)):
        cenario.empresa(basico, f"MASSA {n} SINTETICA LTDA", complemento="SALA 1")
    (alerta,) = cenario.alertas("L1")
    verificar_explicacao(alerta, cenario.sessao)
    assert alerta.parametros["empresas_ativas_no_endereco"] == 5
    assert alerta.parametros["abertura_concentrada"] is True  # todas abertas no mesmo dia
    assert "5 empresas" in alerta.motivo and "não conclusão" in alerta.motivo
    assert "centro empresarial" in alerta.motivo and len(alerta.entidades) == 5
    # A T2 exige contratada; a L1, não: sem contrato nenhum, só a L1 dispara.
    assert cenario.alertas("T2") == []


def test_rf04_l1_limite_quatro_empresas_nao_disparam_e_o_limiar_e_configuravel(cenario):
    for n, basico in enumerate(_bases(9100, 4)):
        cenario.empresa(basico, f"MASSA {n} SINTETICA LTDA")
    assert cenario.alertas("L1") == []
    assert len(cenario.alertas("L1", l1_minimo_estabelecimentos=4)) == 1


def test_rf04_l1_conta_so_ativas_e_separa_pelo_complemento(cenario):
    bases = _bases(9100, 6)
    for n, basico in enumerate(bases[:4]):
        cenario.empresa(basico, f"MASSA {n} SINTETICA LTDA", complemento="SALA 1")
    # Mesma rua e número, outra sala: não soma (a T2 somaria, porque ignora o complemento).
    cenario.empresa(bases[4], "OUTRA SALA SINTETICA LTDA", complemento="SALA 2")
    # Mesma sala, mas BAIXADA (código 08): não conta.
    cenario.empresa(bases[5], "BAIXADA SINTETICA LTDA", complemento="SALA 1", situacao="08")
    assert cenario.alertas("L1") == []


def test_rf04_l1_marca_abertura_concentrada_so_dentro_da_janela(cenario):
    inicios = ["20100101", "20120101", "20140101", "20160101", "20180101"]
    for n, (basico, inicio) in enumerate(zip(_bases(9100, 5), inicios, strict=True)):
        cenario.empresa(basico, f"ESPACADA {n} SINTETICA LTDA", inicio=inicio)
    (alerta,) = cenario.alertas("L1")
    assert alerta.parametros["abertura_concentrada"] is False
    assert alerta.parametros["abertas_na_janela"] == 1
    # Com a janela de 800 dias, três aberturas seguidas (2 anos entre cada) cabem nela.
    (alerta,) = cenario.alertas("L1", l1_janela_abertura_dias=1500)
    assert alerta.parametros["abertas_na_janela"] == 3
    assert alerta.parametros["abertura_concentrada"] is True


# ==== L3: mesmo administrador em muitas empresas ================================================


def _administradas(cenario: Cenario, quantidade: int, inicios: list[str] | None = None) -> None:
    for n, basico in enumerate(_bases(9110, quantidade)):
        inicio = inicios[n] if inicios else "20240110"
        cenario.empresa(basico, f"ADMINISTRADA {n} SINTETICA LTDA", inicio=inicio, numero=str(n))
        cenario.socio(basico, "LARANJA PROFISSIONAL SINTETICO", "***777001**", qualificacao="49")


def test_rf03_l3_dispara_para_a_mesma_pessoa_provavel_em_cinco_empresas(cenario):
    _administradas(cenario, 5)
    (alerta,) = cenario.alertas("L3")
    verificar_explicacao(alerta, cenario.sessao)
    assert alerta.parametros["empresas"] == 5 and alerta.parametros["abertas_na_janela"] == 5
    assert alerta.parametros["identidade_confirmada"] is False
    assert "***777001**" in alerta.motivo and "PESSOA PROVÁVEL, não identificada" in alerta.motivo
    assert "LARANJA PROFISSIONAL SINTETICO" in cenario.rotulos(alerta)
    assert len(alerta.entidades) == 6  # a pessoa e as cinco empresas


def test_rf03_l3_limite_quatro_empresas_ou_aberturas_espalhadas_nao_disparam(cenario):
    _administradas(cenario, 4)
    assert cenario.alertas("L3") == []
    assert len(cenario.alertas("L3", l3_minimo_empresas=4)) == 1


def test_rf03_l3_exige_aberturas_concentradas_e_qualificacao_de_administracao(cenario):
    # Cinco empresas, mas abertas ao longo de dez anos: só duas cabem em 12 meses.
    _administradas(cenario, 5, ["20100101", "20130101", "20160101", "20190101", "20190601"])
    assert cenario.alertas("L3") == []
    assert len(cenario.alertas("L3", l3_minimo_abertas_na_janela=2)) == 1
    # Sócio comum (qualificação 22) em cinco empresas não é administração.
    for n, basico in enumerate(_bases(9120, 5)):
        cenario.empresa(basico, f"SO SOCIO {n} SINTETICA LTDA", inicio="20240110", numero=f"9{n}")
        cenario.socio(basico, "SOCIO COMUM SINTETICO", "***777002**", qualificacao="22")
    assert cenario.alertas("L3") == []


def test_rf03_l3_nao_junta_homonimos_de_digitos_diferentes_e_conta_representante(cenario):
    for n, basico in enumerate(_bases(9130, 5)):
        cenario.empresa(basico, f"HOMONIMA {n} SINTETICA LTDA", inicio="20240110", numero=f"7{n}")
        # Mesmo nome, seis dígitos diferentes em cada empresa: são pessoas diferentes.
        cenario.socio(basico, "HOMONIMO SINTETICO", f"***88800{n}**", qualificacao="49")
    assert cenario.alertas("L3") == []
    for n, basico in enumerate(_bases(9140, 5)):
        cenario.empresa(
            basico, f"REPRESENTADA {n} SINTETICA LTDA", inicio="20240110", numero=f"6{n}"
        )
        cenario.socio(
            basico,
            f"MENOR {n} SINTETICO",
            f"***66600{n}**",
            qualificacao="30",
            representante="***555001**",
            nome_representante="REPRESENTANTE SINTETICO",
        )
    (alerta,) = cenario.alertas("L3")
    assert "representante legal" in alerta.motivo and "***555001**" in alerta.motivo


# ==== L4: cadeia societária em camadas ==========================================================


def _cadeia(cenario: Cenario, niveis: int, topo: str = "pf") -> list[str]:
    """ALVO <- HOLDING 1 <- ... <- HOLDING n; ``topo`` diz quem é sócio da última holding."""
    bases = _bases(9150, niveis + 1)
    cenario.empresa(bases[0], "ALVO DA CADEIA SINTETICA LTDA")
    for n in range(1, niveis + 1):
        cenario.empresa(bases[n], f"HOLDING {n} SINTETICA LTDA", numero=str(n))
        cenario.socio(
            bases[n - 1],
            f"HOLDING {n} SINTETICA LTDA",
            bases[n],
            identificador="1",
            qualificacao="22",
        )
    if topo == "pf":
        cenario.socio(bases[-1], "CONTROLADOR SINTETICO", "***414141**")
    elif topo == "estrangeiro":
        cenario.socio(bases[-1], "OFFSHORE SINTETICA INC", "", identificador="3", pais="249")
    return bases


def test_rf03_l4_dispara_com_dois_niveis_de_socio_pessoa_juridica(cenario):
    _cadeia(cenario, 2)
    alertas = cenario.alertas("L4")
    (alerta,) = [a for a in alertas if a.titulo.endswith("ALVO DA CADEIA SINTETICA LTDA")]
    verificar_explicacao(alerta, cenario.sessao)
    assert len(alertas) == 1  # a HOLDING 1 só tem um nível acima dela
    assert alerta.parametros["niveis"] == 2 and alerta.parametros["termina_em"] == "pessoa física"
    assert (
        "HOLDING 1 SINTETICA LTDA" in alerta.motivo and "HOLDING 2 SINTETICA LTDA" in alerta.motivo
    )
    assert alerta.parametros["termino_opaco"] is False


def test_rf03_l4_limite_um_nivel_nao_dispara_e_pessoa_fisica_no_primeiro_nivel_tambem_nao(cenario):
    bases = _cadeia(cenario, 1)
    assert cenario.alertas("L4") == []
    # Três níveis, mas com uma pessoa física no primeiro: o controle direto é visível.
    outras = [f"9160000{n}" for n in range(1, 5)]
    cenario.empresa(outras[0], "ALVO COM SOCIO PF SINTETICA LTDA", numero="50")
    for n in range(1, 4):
        cenario.empresa(outras[n], f"CAMADA {n} SINTETICA LTDA", numero=f"5{n}")
        cenario.socio(outras[n - 1], f"CAMADA {n} SINTETICA LTDA", outras[n], identificador="1")
    cenario.socio(outras[0], "SOCIO DIRETO SINTETICO", "***424242**")
    alertas = cenario.alertas("L4")
    assert [a.parametros["niveis"] for a in alertas] == [2]  # só a CAMADA 1 (sem PF no nível 1)
    assert bases[0] not in {e for a in alertas for e in a.entidades}


def test_rf03_l4_informa_termino_opaco_e_corta_ciclo(cenario):
    _cadeia(cenario, 3, topo="estrangeiro")
    alertas = cenario.alertas("L4", l4_minimo_niveis=3)
    (alerta,) = alertas
    assert alerta.parametros["termina_em"] == "sócio estrangeiro"
    assert alerta.parametros["termino_opaco"] is True
    # Participação cruzada (A <- B <- A): a regra termina, sem laço infinito.
    a, b = "91700001", "91700002"
    cenario.empresa(a, "CRUZADA A SINTETICA LTDA", numero="80")
    cenario.empresa(b, "CRUZADA B SINTETICA LTDA", numero="81")
    cenario.socio(a, "CRUZADA B SINTETICA LTDA", b, identificador="1")
    cenario.socio(b, "CRUZADA A SINTETICA LTDA", a, identificador="1")
    assert all(x.parametros["niveis"] <= 4 for x in cenario.alertas("L4"))


# ==== L5: sócio estrangeiro (nível 1) ===========================================================


def test_rf03_l5_dispara_para_socio_estrangeiro_e_nao_para_brasileiro(cenario):
    cenario.empresa("91800001", "COM ESTRANGEIRO SINTETICA LTDA")
    cenario.socio("91800001", "SOCIO NACIONAL SINTETICO", "***515151**")
    cenario.socio("91800001", "FOREIGN SAMPLE HOLDINGS", "", identificador="3", pais="249")
    cenario.empresa("91800002", "SO NACIONAIS SINTETICA LTDA", numero="2")
    cenario.socio("91800002", "OUTRO NACIONAL SINTETICO", "***525252**")
    (alerta,) = cenario.alertas("L5")
    verificar_explicacao(alerta, cenario.sessao)
    assert "COM ESTRANGEIRO SINTETICA LTDA" in alerta.titulo
    assert alerta.parametros == {**alerta.parametros, "nivel": 1, "paises": ["249"]}
    assert "código do país: 249" in alerta.motivo and "nível 2" in alerta.motivo


def test_rf03_l5_limite_identificador_de_socio_decide_e_nao_ha_lista_de_paises(cenario):
    cenario.empresa("91800003", "PAIS SEM IDENTIFICADOR SINTETICA LTDA")
    # País preenchido, mas identificador 2 (pessoa física): não é sócio estrangeiro no layout.
    cenario.socio("91800003", "RESIDENTE SINTETICO", "***535353**", identificador="2", pais="249")
    assert cenario.alertas("L5") == []
    assert "l5_paises" not in cenario.padrao  # o nível 2 (jurisdições) não foi implementado


# ==== L7: fundadores substituídos logo após a abertura ==========================================


def _trocada(cenario: Cenario, entrada: str, assinatura: str = "2026-06-01") -> str:
    cenario.empresa("91900001", "QUADRO TROCADO SINTETICA LTDA", inicio="20200101")
    cenario.socio("91900001", "NOVO SOCIO UM SINTETICO", "***616161**", entrada=entrada)
    cenario.socio("91900001", "NOVO SOCIO DOIS SINTETICO", "***626262**", entrada=entrada)
    return cenario.contrato("91900001", assinatura)


def test_rf03_l7_dispara_quando_nenhum_socio_atual_e_fundador_e_a_troca_antecede_o_contrato(
    cenario,
):
    numero = _trocada(cenario, "20260101")
    (alerta,) = cenario.alertas("L7")
    verificar_explicacao(alerta, cenario.sessao)
    assert alerta.parametros["ultima_entrada"] == "2026-01-01"
    assert alerta.parametros["data_inicio_atividade"] == "2020-01-01"
    assert numero in alerta.motivo and "01/01/2026" in alerta.motivo
    assert "só o quadro atual" in alerta.motivo and "venda legítima" in alerta.motivo


def test_rf03_l7_nao_dispara_com_fundador_no_quadro_nem_com_troca_antiga(cenario):
    _trocada(cenario, "20260101")
    # Um fundador (entrou no dia da abertura) continua no quadro.
    cenario.socio("91900001", "FUNDADOR SINTETICO", "***636363**", entrada="20200101")
    assert cenario.alertas("L7") == []


def test_rf03_l7_limites_tolerancia_apos_a_abertura_e_janela_antes_do_contrato(cenario):
    # Entrada 30 dias depois da abertura: ainda conta como fundador (limiar de 30 dias).
    cenario.empresa("91900002", "ENTRADA NO LIMITE SINTETICA LTDA", inicio="20260101")
    cenario.socio("91900002", "SOCIO DO LIMITE SINTETICO", "***646464**", entrada="20260131")
    cenario.contrato("91900002", "2026-06-01")
    assert cenario.alertas("L7") == []
    assert len(cenario.alertas("L7", l7_dias_apos_abertura=29)) == 1
    # Troca 12 meses antes do contrato dispara; 12 meses e um dia, não.
    cenario.empresa("91900003", "TROCA ANTIGA SINTETICA LTDA", inicio="20200101", numero="3")
    cenario.socio("91900003", "SOCIO TARDIO SINTETICO", "***656565**", entrada="20250531")
    cenario.contrato("91900003", "2026-06-01")
    assert cenario.alertas("L7") == []
    assert len(cenario.alertas("L7", l7_meses_antes_contrato=13)) == 1
    # Sem data de entrada de um sócio, a regra não afirma nada.
    cenario.empresa("91900004", "SEM DATA SINTETICA LTDA", inicio="20200101", numero="4")
    cenario.socio("91900004", "SOCIO SEM DATA SINTETICO", "***676767**", entrada="")
    cenario.contrato("91900004", "2026-06-01")
    assert cenario.alertas("L7") == []


# ==== L8: empresa recém-aberta com sócio possivelmente agente público ===========================


def _recente_com_servidor(
    cenario: Cenario, inicio: str = "20260301", cpf_servidor="444444"
) -> None:
    cenario.empresa("92000001", "RECEM ABERTA SINTETICA LTDA", inicio=inicio)
    cenario.socio("92000001", "SERVIDOR E SOCIO SINTETICO", "***444444**", entrada=inicio)
    cenario.contrato("92000001", "2026-06-01")
    cenario.servidor("SERVIDOR E SOCIO SINTETICO", f"***.{cpf_servidor[:3]}.{cpf_servidor[3:]}-**")


def test_rf04_l8_dispara_quando_t1_e_t7_coincidem_na_mesma_contratada(cenario):
    _recente_com_servidor(cenario)
    (alerta,) = cenario.alertas("L8")
    verificar_explicacao(alerta, cenario.sessao)
    assert alerta.parametros["composicao"] == ["T1", "T7"]
    assert alerta.parametros["identidade_confirmada"] is False
    assert "POSSÍVEL COINCIDÊNCIA, não uma identificação" in alerta.motivo
    assert "sem empregados" in alerta.motivo and "***444444**" in alerta.motivo
    assert {e["fonte"] for e in alerta.evidencias} == {
        "cnpj_arquivo",
        "pncp",
        "portal_transparencia",
    }
    assert len(cenario.alertas("T1")) == 1 and len(cenario.alertas("T7")) == 1


def test_rf04_l8_nao_dispara_so_com_t1_nem_so_com_t7(cenario):
    # Só T1: empresa recente, servidor com outros dígitos de CPF (homônimo).
    _recente_com_servidor(cenario, cpf_servidor="999999")
    assert len(cenario.alertas("T1")) == 1 and cenario.alertas("T7") == []
    assert cenario.alertas("L8") == []


def test_rf04_l8_limite_segue_o_limiar_de_dias_da_t1(cenario):
    # Aberta 180 dias antes do contrato: a T1 (menos de 180 dias) não dispara, nem a L8.
    _recente_com_servidor(cenario, inicio="20251203")
    assert len(cenario.alertas("T7")) == 1
    assert cenario.alertas("L8") == []
    assert len(cenario.alertas("L8", t1_dias_minimos=181)) == 1


# ==== L10 (CEIS/CNEP): vínculo com empresa sancionada ===========================================


def _sucessora(cenario: Cenario, vinculo: str, inicio_sancao: str = "2025-01-01") -> None:
    mesmo_endereco = vinculo == "endereco"
    cenario.empresa("92100001", "SANCIONADA SINTETICA LTDA", logradouro="DA SANCAO", numero="1")
    cenario.empresa(
        "92100002",
        "SUCESSORA SINTETICA LTDA",
        logradouro="DA SANCAO" if mesmo_endereco else "OUTRA RUA SINTETICA",
        numero="1",
        complemento="SALA 9",
    )
    cenario.socio("92100001", "ADMINISTRADOR COMUM SINTETICO", "***717171**")
    cenario.socio(
        "92100002",
        "ADMINISTRADOR COMUM SINTETICO" if vinculo == "socio" else "OUTRO ADMIN SINTETICO",
        "***717171**" if vinculo == "socio" else "***727272**",
    )
    cenario.sancao("92100001", inicio=inicio_sancao)
    cenario.contrato("92100002", "2026-06-01")


def test_rf04_l10_dispara_por_socio_administrador_comum_com_sancionada(cenario):
    _sucessora(cenario, "socio")
    (alerta,) = cenario.alertas("L10")
    verificar_explicacao(alerta, cenario.sessao)
    assert alerta.parametros["vinculo_por_socio"] is True
    assert alerta.parametros["vinculo_por_endereco"] is False
    assert "SUCESSORA SINTETICA LTDA" in alerta.titulo and "CEIS" in alerta.motivo
    assert "não tem sanção própria vigente" in alerta.motivo
    assert "PESSOA PROVÁVEL" in alerta.motivo and "***717171**" in alerta.motivo
    # A própria contratada não tem sanção: a T6 não dispara (a L10 é a extensão dela).
    assert cenario.alertas("T6") == []


def test_rf04_l10_dispara_por_endereco_e_ignora_o_complemento(cenario):
    _sucessora(cenario, "endereco")
    (alerta,) = cenario.alertas("L10")
    assert alerta.parametros["vinculo_por_endereco"] is True
    assert alerta.parametros["vinculo_por_socio"] is False
    assert "mesmo endereço normalizado" in alerta.motivo


def test_rf04_l10_nao_dispara_sem_vinculo_nem_com_sancao_propria(cenario):
    _sucessora(cenario, "nenhum")
    assert cenario.alertas("L10") == []
    # Com sanção própria vigente, o caso é da T6, não da L10.
    cenario.socio("92100002", "ADMINISTRADOR COMUM SINTETICO", "***717171**")
    cenario.sancao("92100002", identificador="2")
    assert cenario.alertas("L10") == [] and len(cenario.alertas("T6")) == 1


def test_rf04_l10_limite_sancao_iniciada_no_dia_do_contrato_conta_e_no_dia_seguinte_nao(cenario):
    _sucessora(cenario, "socio", inicio_sancao="2026-06-01")
    assert len(cenario.alertas("L10")) == 1


def test_rf04_l10_limite_sancao_posterior_ao_contrato_nao_dispara(cenario):
    _sucessora(cenario, "socio", inicio_sancao="2026-06-02")
    assert cenario.alertas("L10") == []


# ==== L14 (sinal fraco): faixa etária atípica ===================================================


def _faixa(cenario: Cenario, faixas: list[str], valor: float = 150_000.0) -> None:
    cenario.empresa("92200001", "SOCIOS ATIPICOS SINTETICA LTDA")
    for n, faixa in enumerate(faixas):
        cenario.socio("92200001", f"ADMIN {n} SINTETICO", f"***81810{n}**", faixa=faixa)
    cenario.contrato("92200001", "2026-06-01", valor)


def test_rf04_l14_dispara_quando_todos_os_administradores_estao_em_faixa_atipica(cenario):
    _faixa(cenario, ["9", "2"])
    (alerta,) = cenario.alertas("L14")
    verificar_explicacao(alerta, cenario.sessao)
    assert alerta.parametros["sinal_fraco"] is True and POLITICAS["L14"].sinal_fraco is True
    assert alerta.parametros["faixas_observadas"] == ["2", "9"]
    assert "mais de 80 anos" in alerta.motivo and "13 a 20 anos" in alerta.motivo
    assert "Sinal fraco" in alerta.motivo and "só pesa no alerta de combinação" in alerta.motivo
    assert alerta.severidade == "baixa"


def test_rf04_l14_nao_dispara_se_um_administrador_esta_em_faixa_comum(cenario):
    _faixa(cenario, ["9", "5"])
    assert cenario.alertas("L14") == []


def test_rf04_l14_limite_valor_igual_ao_minimo_nao_dispara(cenario):
    _faixa(cenario, ["9"], valor=100_000.0)
    assert cenario.alertas("L14") == []
    assert len(cenario.alertas("L14", l14_valor_minimo=99_999.99)) == 1


# ==== L15 (sinal fraco): número excessivo de atividades =========================================


def _secundarias(quantidade: int) -> str:
    return ",".join(f"47{n:05d}" for n in range(quantidade))


def test_rf04_l15_dispara_com_vinte_cnaes_em_contratada(cenario):
    cenario.empresa("92300001", "FAZ TUDO SINTETICA LTDA", secundarias=_secundarias(19))
    cenario.contrato("92300001", "2026-06-01")
    (alerta,) = cenario.alertas("L15")
    verificar_explicacao(alerta, cenario.sessao)
    assert alerta.parametros["cnaes"] == 20 and alerta.parametros["sinal_fraco"] is True
    assert "20 atividades econômicas" in alerta.motivo and POLITICAS["L15"].sinal_fraco is True


def test_rf04_l15_limite_dezenove_cnaes_nao_disparam_nem_empresa_sem_contrato(cenario):
    cenario.empresa("92300002", "QUASE TUDO SINTETICA LTDA", secundarias=_secundarias(18))
    cenario.contrato("92300002", "2026-06-01")
    # Vinte CNAEs, mas sem contrato público: a regra só olha contratadas.
    cenario.empresa(
        "92300003", "SEM CONTRATO SINTETICA LTDA", numero="3", secundarias=_secundarias(19)
    )
    assert cenario.alertas("L15") == []
    assert len(cenario.alertas("L15", l15_minimo_cnaes=19)) == 1


def test_rf04_l15_dispara_por_contratos_em_muitas_categorias_de_objeto(cenario):
    cenario.empresa("92300004", "OBJETOS DIVERSOS SINTETICA LTDA")
    for categoria in (2, 6, 7):
        cenario.contrato("92300004", "2026-06-01", categoria=categoria)
    assert cenario.alertas("L15") == []
    cenario.contrato("92300004", "2026-07-01", categoria=8)
    (alerta,) = cenario.alertas("L15")
    assert alerta.parametros["categorias_de_objeto"] == 4 and alerta.parametros["cnaes"] == 1


# ==== Catálogo, API e configuração ==============================================================


def test_rf04_tipologias_l_estao_na_tabela_unica_no_catalogo_e_na_api(cliente, caso_carregado):
    catalogo = cliente.get("/tipologias", headers={"Authorization": "Bearer tok-analista"}).json()
    por_codigo = {t["codigo"]: t for t in catalogo}
    assert [c for c in por_codigo if c in CODIGOS_CADASTRAIS] == CODIGOS_CADASTRAIS
    for codigo in CODIGOS_CADASTRAIS:
        item, politica = por_codigo[codigo], POLITICAS[codigo]
        assert item["grupo"] == politica.grupo == "lavagem"
        assert item["sinal_fraco"] is politica.sinal_fraco
        assert item["regra"] and item["base"] and item["politica_erro"] == politica.politica_erro
    assert {c for c in CODIGOS_CADASTRAIS if por_codigo[c]["sinal_fraco"]} == {"L14", "L15"}
    assert por_codigo["T1"]["grupo"] == "contratacao" and por_codigo["T1"]["sinal_fraco"] is False
    assert "FATF" in por_codigo["L1"]["base"] and "COAF" in por_codigo["L8"]["base"]
    # Pela API, com as fixtures do CNPJ: duas empresas com sócio estrangeiro (L5).
    r = cliente.post(
        f"/casos/{caso_carregado}/alertas/executar",
        json={"tipologias": ["L5"]},
        headers=cabecalhos(),
    )
    assert r.status_code == 200, r.text
    alertas = r.json()
    assert len(alertas) == 2
    for alerta in alertas:
        assert (alerta["grupo"], alerta["sinal_fraco"]) == ("lavagem", False)
        assert alerta["tipologia_nome"] == POLITICAS["L5"].nome and alerta["situacao"] == "pendente"
        assert "JOHN FOREIGN SAMPLE" in alerta["motivo"] and "não conclusão" in alerta["motivo"]
        for evidencia in alerta["evidencias"]:
            captura = cliente.get(f"/capturas/{evidencia['captura_id']}", headers=cabecalhos())
            assert captura.json()["sha256"] == evidencia["sha256"]


def test_rf04_limiares_das_l_sao_validados_na_execucao_e_na_configuracao(
    cliente, caso_carregado, tmp_path
):
    for invalido in ({"l1_minimo_estabelecimentos": 1}, {"l4_minimo_niveis": 1}, {"l99_x": 1}):
        r = cliente.post(
            f"/casos/{caso_carregado}/alertas/executar",
            json={"parametros": invalido},
            headers=cabecalhos(),
        )
        assert r.status_code == 422, invalido
    with pytest.raises(ValueError):
        tipologias.parametros_efetivos({}, {"l14_valor_minimo": float("nan")})
    assert tipologias.parametros_efetivos({}, {"l14_valor_minimo": 0}) == {"l14_valor_minimo": 0.0}
    with pytest.raises(ErroConfiguracao):
        criar_config(tmp_path, l14_faixas_atipicas="idoso").validar()
    with pytest.raises(ErroConfiguracao):
        criar_config(tmp_path, qualificacoes_administracao="").validar()
    padrao = criar_config(tmp_path).parametros_tipologias()
    assert padrao["qualificacoes_administracao"] == sorted(lavagem.QUALIFICACOES_ADMINISTRACAO)
    assert {k: padrao[k] for k in lavagem.parametros_padrao()} == lavagem.parametros_padrao()
    assert SIAPE.isdigit()
