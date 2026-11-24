"""Cenários SINTÉTICOS para os testes das tipologias de lavagem (L) e do alerta de combinação.

Nada aqui é real: os CNPJs usam bases fictícias (91xxxxxx e 92xxxxxx) com DV calculado, os nomes
são rotulados como sintéticos e os CPFs já nascem descaracterizados (``***NNNNNN**``), como no
layout da Receita. Os registros entram pelo mesmo caminho da coleta (``Resolvedor``), e a captura
de origem fica na custódia, marcada como simulada.
"""

from __future__ import annotations

from buscador import tipologias
from buscador.conectores.base import (
    RegistroContrato,
    RegistroEmpresa,
    RegistroEstabelecimento,
    RegistroSancao,
    RegistroServidor,
    RegistroSocio,
)
from buscador.config import Configuracao
from buscador.modelos import Caso, Entidade
from buscador.resolucao import Resolvedor
from buscador.resolucao.normalizacao import calcular_dv_cnpj

CNPJ_ORGAO = "93100001000199"
SIAPE = "93001"


def cnpj(basico: str, ordem: str = "0001") -> str:
    return f"{basico}{ordem}{calcular_dv_cnpj(basico + ordem)}"


class Cenario:
    """Monta um caso sintético registro a registro e roda uma tipologia sobre ele."""

    def __init__(self, sessao, custodia, config: Configuracao) -> None:
        self.sessao = sessao
        self.padrao = config.parametros_tipologias()
        caso = Caso(titulo="Caso sintético", criado_por="teste")
        sessao.add(caso)
        sessao.flush()
        self.caso_id = caso.id
        self._custodia = custodia
        self._capturas: dict[str, str] = {}
        self.registros: list = []
        self._contratos = 0

    def captura(self, fonte: str) -> str:
        """Uma captura sintética por fonte (o conteúdo só precisa existir na custódia)."""
        if fonte not in self._capturas:
            self._capturas[fonte] = self._custodia.registrar(
                self.sessao,
                caso_id=self.caso_id,
                fonte=fonte,
                url_ou_arquivo=f"simulado://{fonte}/cenario-de-teste",
                conteudo=f"carga sintética de {fonte}".encode(),
                metadados={"simulado": True},
            ).id
        return self._capturas[fonte]

    # ---- Registros ----------------------------------------------------------------------------

    def empresa(
        self,
        basico: str,
        nome: str,
        *,
        inicio: str = "20150101",
        logradouro: str = "DAS AMOSTRAS SINTETICAS",
        numero: str = "100",
        complemento: str = "",
        situacao: str = "02",
        porte: str = "05",
        natureza: str = "2062",
        capital: float = 100_000.0,
        cnae: str = "4120400",
        secundarias: str = "",
        ddd: str = "",
        telefone: str = "",
        ddd2: str = "",
        telefone2: str = "",
        email: str = "",
    ) -> str:
        captura = self.captura("cnpj_arquivo")
        self.registros.append(
            RegistroEmpresa(
                captura_id=captura,
                cnpj_basico=basico,
                razao_social=nome,
                natureza_juridica=natureza,
                capital_social=capital,
                porte=porte,
            )
        )
        self.registros.append(
            RegistroEstabelecimento(
                captura_id=captura,
                cnpj_basico=basico,
                cnpj_ordem="0001",
                cnpj_dv=calcular_dv_cnpj(basico + "0001"),
                matriz_filial="1",
                situacao_cadastral=situacao,
                data_inicio_atividade=inicio,
                cnae_fiscal_principal=cnae,
                cnae_fiscal_secundaria=secundarias,
                tipo_logradouro="RUA",
                logradouro=logradouro,
                numero=numero,
                complemento=complemento,
                bairro="CENTRO",
                cep="70000900",
                uf="DF",
                municipio="9701",
                ddd_1=ddd,
                telefone_1=telefone,
                ddd_2=ddd2,
                telefone_2=telefone2,
                correio_eletronico=email,
            )
        )
        return basico

    def socio(
        self,
        basico: str,
        nome: str,
        documento: str = "***123456**",
        *,
        identificador: str = "2",
        qualificacao: str = "49",
        entrada: str = "20150101",
        pais: str = "",
        faixa: str = "5",
        representante: str = "***000000**",
        nome_representante: str = "",
    ) -> None:
        self.registros.append(
            RegistroSocio(
                captura_id=self.captura("cnpj_arquivo"),
                cnpj_basico=basico,
                identificador_socio=identificador,
                nome_socio=nome,
                cnpj_cpf_socio=documento,
                qualificacao_socio=qualificacao,
                data_entrada_sociedade=entrada,
                pais=pais,
                representante_legal=representante,
                nome_representante=nome_representante,
                faixa_etaria=faixa,
            )
        )

    def contrato(
        self,
        basico: str,
        assinatura: str,
        valor: float = 50_000.0,
        *,
        categoria: int = 2,
        valor_inicial: float | None = None,
    ) -> str:
        self._contratos += 1
        numero = f"{CNPJ_ORGAO}-2-{self._contratos:06d}/{assinatura[:4]}"
        self.registros.append(
            RegistroContrato(
                captura_id=self.captura("pncp"),
                numero_controle_pncp=numero,
                ni_fornecedor=cnpj(basico),
                tipo_pessoa_fornecedor="PJ",
                nome_fornecedor="",
                orgao_cnpj=CNPJ_ORGAO,
                orgao_razao_social="PREFEITURA SINTETICA DE EXEMPLOPOLIS",
                objeto="Objeto sintético",
                valor_global=valor,
                data_assinatura=assinatura,
                url_fonte="simulado://pncp/cenario-de-teste",
                extras={
                    "categoriaProcessoId": categoria,
                    "categoriaProcessoNome": f"Categoria {categoria}",
                    "valorInicial": valor if valor_inicial is None else valor_inicial,
                },
            )
        )
        return numero

    def sancao(
        self,
        basico: str,
        inicio: str = "2025-01-01",
        fim: str = "2027-01-01",
        cadastro: str = "CEIS",
        identificador: str = "1",
    ) -> None:
        self.registros.append(
            RegistroSancao(
                captura_id=self.captura("portal_transparencia"),
                cadastro=cadastro,
                id_registro=identificador,
                cnpj_sancionado=cnpj(basico),
                data_inicio=inicio,
                data_fim=fim,
                tipo_sancao="Impedimento sintético",
                orgao_sancionador="ORGAO SANCIONADOR SINTETICO",
            )
        )

    def servidor(self, nome: str, cpf: str) -> None:
        self.registros.append(
            RegistroServidor(
                captura_id=self.captura("portal_transparencia"),
                nome=nome,
                cpf=cpf,
                codigo_orgao=SIAPE,
                nome_orgao="ORGAO SINTETICO",
                cnpj_orgao=CNPJ_ORGAO,
                vinculo="lotacao",
            )
        )

    # ---- Execução -----------------------------------------------------------------------------

    def incorporar(self) -> None:
        if self.registros:
            Resolvedor(self.sessao, self.caso_id).incorporar(self.registros)
            self.registros = []

    def alertas(self, *codigos: str, **parametros):
        """Roda as tipologias pedidas com os padrões da configuração e os ``parametros`` dados."""
        self.incorporar()
        return tipologias.executar(
            self.sessao, self.caso_id, {**self.padrao, **parametros}, list(codigos)
        )

    def sinais(self, *codigos: str, **parametros):
        """Avalia os sinais fracos pedidos (L2, L14, L15) sem gravar alerta: é o que a combinação
        recebe. Devolve os ``AlertaDetectado``."""
        from buscador.tipologias import motor

        self.incorporar()
        ctx = motor.carregar_contexto(self.sessao, self.caso_id)
        completos = {
            **motor.parametros_padrao_lavagem(),
            **self.padrao,
            **motor.parametros_efetivos({}, parametros),
        }
        return motor.avaliar_sinais_fracos(ctx, completos, list(codigos))

    def rotulos(self, alerta) -> set[str]:
        return {self.sessao.get(Entidade, eid).rotulo for eid in alerta.entidades}


def verificar_explicacao(alerta, sessao) -> None:
    """Todo alerta L: motivo em frase, pista e não conclusão, e evidência com captura e hash.
    Serve também para o sinal fraco (``AlertaDetectado``), cujas evidências são objetos."""
    from dataclasses import asdict, is_dataclass

    from buscador.modelos import Captura
    from buscador.tipologias.politicas import POLITICAS

    politica = POLITICAS[alerta.tipologia]
    assert alerta.severidade == politica.severidade
    assert len(alerta.motivo) > 80 and alerta.evidencias
    for evidencia in alerta.evidencias:
        if is_dataclass(evidencia):
            evidencia = asdict(evidencia)
        captura = sessao.get(Captura, evidencia["captura_id"])
        assert captura.sha256 == evidencia["sha256"]
        assert captura.url_ou_arquivo.startswith("simulado://")


def simples(
    cenario: Cenario,
    basico: str,
    *,
    mei: str = "S",
    opcao_mei: str = "20200101",
    exclusao_mei: str = "00000000",
) -> None:
    """Linha sintética da tabela Simples do CNPJ (opção pelo MEI e datas)."""
    from buscador.conectores.base import RegistroSimples

    cenario.registros.append(
        RegistroSimples(
            captura_id=cenario.captura("cnpj_arquivo"),
            cnpj_basico=basico,
            opcao_simples="S",
            data_opcao_simples=opcao_mei,
            opcao_mei=mei,
            data_opcao_mei=opcao_mei,
            data_exclusao_mei=exclusao_mei,
        )
    )


def convenio(
    cenario: Cenario,
    basico: str,
    vigencia: str = "2026-03-01",
    *,
    identificador: str = "1",
    valor: float = 500_000.0,
) -> None:
    from buscador.conectores.base import RegistroConvenio

    cenario.registros.append(
        RegistroConvenio(
            captura_id=cenario.captura("portal_transparencia"),
            id_registro=identificador,
            cnpj_convenente=cnpj(basico),
            numero=f"CONVENIO SINTETICO {identificador}/2026",
            objeto="Objeto sintético de convênio",
            data_inicio_vigencia=vigencia,
            data_fim_vigencia="2027-12-31",
            valor=valor,
            valor_liberado=valor / 2,
            situacao="EM EXECUÇÃO",
            orgao_nome="PREFEITURA SINTETICA DE EXEMPLOPOLIS",
            orgao_cnpj=CNPJ_ORGAO,
        )
    )


def expulsao(cenario: Cenario, nome: str, cpf: str, identificador: str = "1") -> None:
    from buscador.conectores.base import RegistroExpulsao

    cenario.registros.append(
        RegistroExpulsao(
            captura_id=cenario.captura("portal_transparencia"),
            id_registro=identificador,
            nome=nome,
            cpf=cpf,
            tipo_punicao="Demissão",
            data_publicacao="2024-05-10",
            orgao_lotacao="ORGAO SINTETICO",
        )
    )
