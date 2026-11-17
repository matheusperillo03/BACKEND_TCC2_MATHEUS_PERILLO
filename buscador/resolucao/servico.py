"""Resolução de entidades (RF02) e construção das arestas a partir dos registros dos conectores.

Regras:
- Empresa: chave = CNPJ básico (raiz de 8 posições, numérica ou alfanumérica). Estabelecimentos
  são fundidos na empresa.
- Pessoa: chave = CPF descaracterizado + nome normalizado. Mesmo nome com CPF diferente ou ausente
  gera uma ``Sugestao`` (score e motivo), nunca fusão automática (homônimos).
- Órgão: chave = CNPJ do ``orgaoEntidade`` (14 posições).
- Contrato: chave = ``numeroControlePNCP``.
- Arestas: ``socio_de`` (sócio -> empresa), ``contratado_por`` (fornecedor -> órgão),
  ``fornecedor_de`` (fornecedor -> contrato), ``contratante_de`` (órgão -> contrato) e
  ``mesmo_endereco`` (empresa <-> empresa, derivada do endereço normalizado do estabelecimento).

Horizonte 2 (H19):
- Termo de contrato: não é entidade; entra na lista ``termos`` dos atributos do contrato.
- Sanção (CEIS/CNEP): entidade ``sancao`` (chave = cadastro + id), ligada à empresa (CNPJ básico)
  por ``sancionada_em``.
- Servidor: entidade ``pessoa`` com chave própria (``servidor:<órgão>:<CPF descaracterizado>|
  <nome>``), ligada ao órgão por ``servidor_de``. NUNCA é fundido com um sócio de mesmo nome e
  mesmos dígitos visíveis: seis dígitos e o nome não identificam uma pessoa. A coincidência gera
  só uma ``Sugestao`` (e, na tipologia T7, um alerta de "possível coincidência").
- Candidato (TSE): entidade ``candidato`` (chave = ano + ``SQ_CANDIDATO``), sem CPF.
- Doador: pessoa jurídica é a empresa do CNPJ básico; pessoa física é ``pessoa`` com chave própria
  (``doador:<CPF descaracterizado>|<nome>``), também sem fusão. Aresta ``doou_para``.
- Edital: entidade ``edital`` (chave = nome do arquivo), com a captura do texto.
- De qualquer CPF que chegue completo, só os seis dígitos centrais são guardados (RNF04).

Proveniência: ``atributos["origem_atributos"]`` guarda, para cada atributo, a captura que forneceu
o valor em vigor. As tipologias citam essa captura como evidência (e não "a primeira fonte").

Limites: grupos de mesmo endereço ou de mesmo nome acima de ``max_grupo`` não são expandidos par a
par (o custo seria quadrático); o endereço vira uma estrela e os homônimos excedentes geram aviso.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from buscador.conectores.base import (
    Registro,
    RegistroCandidato,
    RegistroContrato,
    RegistroDoacao,
    RegistroEdital,
    RegistroEmpresa,
    RegistroEstabelecimento,
    RegistroSancao,
    RegistroServidor,
    RegistroSocio,
    RegistroTermo,
)
from buscador.modelos import Aresta, Entidade, EntidadeFonte, Sugestao
from buscador.privacidade import descaracterizar_texto
from buscador.resolucao.normalizacao import (
    data_layout_para_iso,
    endereco_legivel,
    limpar_documento,
    mascarar_cpf,
    normalizar_cnpj,
    normalizar_cnpj_basico,
    normalizar_endereco,
    normalizar_nome,
)

# Campos dos registros que carregam o documento em si: seguem como vieram até a resolução, que
# guarda o CNPJ inteiro e, do CPF, só a forma descaracterizada (``mascarar_cpf``).
_CAMPOS_DE_DOCUMENTO = frozenset(
    {"ni_fornecedor", "cnpj_cpf_socio", "cpf", "documento_doador", "representante_legal"}
)


def _texto_livre_sem_cpf(valor: object) -> object:
    # Texto só de dígitos é identificador da fonte (SQ_CANDIDATO, código, data): fica como veio.
    if isinstance(valor, str) and not valor.isdigit():
        return descaracterizar_texto(valor)
    return valor


def sem_cpf_em_texto_livre(registro: Registro) -> Registro:
    """O registro com todo CPF dos campos de TEXTO LIVRE descaracterizado (RNF04).

    As fontes trazem CPF completo fora do campo de documento: o objeto de um contrato do PNCP pode
    citar o CPF de alguém, e o nome empresarial de microempreendedor individual pode terminar
    com o CPF do titular (observação do autor nos dados abertos; sem fonte normativa conferida).
    Como entidades, vínculos, alertas, avisos, relatório e exportações derivam todos dos
    registros, limpar aqui garante que nenhum deles receba CPF completo."""
    trocas: dict[str, object] = {}
    for campo in dataclasses.fields(registro):
        if campo.name in _CAMPOS_DE_DOCUMENTO:
            continue
        valor = getattr(registro, campo.name)
        if isinstance(valor, dict):
            novo: object = {k: _texto_livre_sem_cpf(v) for k, v in valor.items()}
        else:
            novo = _texto_livre_sem_cpf(valor)
        if novo != valor:
            trocas[campo.name] = novo
    return dataclasses.replace(registro, **trocas) if trocas else registro


TIPOS_ENTIDADE = ("empresa", "pessoa", "orgao", "contrato", "sancao", "candidato", "edital")
TIPOS_ARESTA = (
    "socio_de",
    "contratado_por",
    "fornecedor_de",
    "contratante_de",
    "mesmo_endereco",
    "sancionada_em",
    "servidor_de",
    "doou_para",
    "edital_de",
    # Entidade citada num documento, aceita pelo investigador a partir de uma sugestão da IA (H22).
    "citado_em",
)
PREFIXO_SERVIDOR = "servidor:"
PREFIXO_DOADOR = "doador:"
ORIGEM_ATRIBUTOS = "origem_atributos"
MAX_GRUPO_PADRAO = 50

_ORDEM = {
    RegistroEmpresa: 0,
    RegistroEstabelecimento: 1,
    RegistroSocio: 2,
    RegistroContrato: 3,
    RegistroTermo: 4,
    RegistroSancao: 5,
    RegistroServidor: 6,
    RegistroCandidato: 7,
    RegistroDoacao: 8,
    RegistroEdital: 9,
}


@dataclass
class Estatisticas:
    entidades_novas: int = 0
    entidades_atualizadas: int = 0
    arestas_novas: int = 0
    sugestoes_novas: int = 0
    avisos: list[str] = field(default_factory=list)


class Resolvedor:
    def __init__(self, sessao: Session, caso_id: str, max_grupo: int = MAX_GRUPO_PADRAO) -> None:
        self.sessao = sessao
        self.caso_id = caso_id
        self.max_grupo = max(2, max_grupo)
        self.estatisticas = Estatisticas()
        self._entidades: dict[tuple[str, str], Entidade] = {}
        self._arestas: set[tuple[str, str, str, str]] = set()
        self._sugestoes: set[tuple[str, str]] = set()
        self._fontes: set[tuple[str, str]] = set()
        self._pessoas_por_nome: dict[str, list[Entidade]] = {}
        self._nomes_no_limite: set[str] = set()
        self._carregar()

    # ---- Cache --------------------------------------------------------------------------------

    def _carregar(self) -> None:
        for e in self.sessao.execute(
            select(Entidade).where(Entidade.caso_id == self.caso_id)
        ).scalars():
            self._entidades[(e.tipo, e.chave)] = e
            if e.tipo == "pessoa":
                self._indexar_pessoa(e)
        for entidade_id, captura_id in self.sessao.execute(
            select(EntidadeFonte.entidade_id, EntidadeFonte.captura_id)
            .join(Entidade, Entidade.id == EntidadeFonte.entidade_id)
            .where(Entidade.caso_id == self.caso_id)
        ):
            self._fontes.add((entidade_id, captura_id))
        for a in self.sessao.execute(
            select(Aresta).where(Aresta.caso_id == self.caso_id)
        ).scalars():
            self._arestas.add((a.origem_id, a.destino_id, a.tipo, a.chave))
        for s in self.sessao.execute(
            select(Sugestao).where(Sugestao.caso_id == self.caso_id)
        ).scalars():
            self._sugestoes.add((s.entidade_id, s.candidata_id))

    def _indexar_pessoa(self, pessoa: Entidade) -> None:
        nome = (pessoa.atributos or {}).get("nome_normalizado") or ""
        if nome:
            self._pessoas_por_nome.setdefault(nome, []).append(pessoa)

    def _obter_ou_criar(
        self,
        tipo: str,
        chave: str,
        *,
        rotulo: str,
        identificador: str,
        atributos: dict,
        captura_id: str,
        papel: str,
    ) -> tuple[Entidade, bool]:
        entidade = self._entidades.get((tipo, chave))
        if entidade is None:
            entidade = Entidade(
                caso_id=self.caso_id,
                tipo=tipo,
                chave=chave,
                rotulo=rotulo,
                identificador=identificador,
                atributos={
                    **atributos,
                    ORIGEM_ATRIBUTOS: {
                        k: captura_id for k, v in atributos.items() if v not in (None, "")
                    },
                },
                captura_id=captura_id,
            )
            self.sessao.add(entidade)
            self.sessao.flush()
            self._entidades[(tipo, chave)] = entidade
            self.estatisticas.entidades_novas += 1
            self._registrar_fonte(entidade, captura_id, papel)
            return entidade, True
        self._registrar_fonte(entidade, captura_id, papel)
        return entidade, False

    def _registrar_fonte(self, entidade: Entidade, captura_id: str, papel: str) -> None:
        marca = (entidade.id, captura_id)
        if marca in self._fontes:
            return
        self._fontes.add(marca)
        self.sessao.add(EntidadeFonte(entidade_id=entidade.id, captura_id=captura_id, papel=papel))

    def _atualizar(self, entidade: Entidade, captura_id: str, **novos: object) -> None:
        """Último valor não vazio prevalece; a captura que o forneceu fica em ``origem_atributos``.

        A origem só muda quando o valor é de fato escrito: um registro posterior com o campo vazio
        não desloca a citação para uma captura que não contém o valor.
        """
        escritos = {k: v for k, v in novos.items() if v not in (None, "")}
        atributos = dict(entidade.atributos or {})
        origem = dict(atributos.get(ORIGEM_ATRIBUTOS) or {})
        atributos.update(escritos)
        origem.update(dict.fromkeys(escritos, captura_id))
        atributos[ORIGEM_ATRIBUTOS] = origem
        entidade.atributos = atributos
        flag_modified(entidade, "atributos")
        self.estatisticas.entidades_atualizadas += 1

    def _aresta(
        self,
        origem: Entidade,
        destino: Entidade,
        tipo: str,
        captura_id: str,
        chave: str = "",
        atributos: dict | None = None,
    ) -> Aresta | None:
        marca = (origem.id, destino.id, tipo, chave)
        if marca in self._arestas:
            return None
        aresta = Aresta(
            caso_id=self.caso_id,
            origem_id=origem.id,
            destino_id=destino.id,
            tipo=tipo,
            chave=chave,
            atributos=atributos or {},
            captura_id=captura_id,
        )
        self.sessao.add(aresta)
        self._arestas.add(marca)
        self.estatisticas.arestas_novas += 1
        return aresta

    # ---- Entrada ------------------------------------------------------------------------------

    def incorporar(self, registros: list[Registro]) -> Estatisticas:
        for registro in sorted(registros, key=lambda r: _ORDEM[type(r)]):
            registro = sem_cpf_em_texto_livre(registro)
            if isinstance(registro, RegistroEmpresa):
                self._empresa(registro)
            elif isinstance(registro, RegistroEstabelecimento):
                self._estabelecimento(registro)
            elif isinstance(registro, RegistroSocio):
                self._socio(registro)
            elif isinstance(registro, RegistroContrato):
                self._contrato(registro)
            elif isinstance(registro, RegistroTermo):
                self._termo(registro)
            elif isinstance(registro, RegistroSancao):
                self._sancao(registro)
            elif isinstance(registro, RegistroServidor):
                self._servidor(registro)
            elif isinstance(registro, RegistroCandidato):
                self._candidato(registro)
            elif isinstance(registro, RegistroDoacao):
                self._doacao(registro)
            elif isinstance(registro, RegistroEdital):
                self._edital(registro)
        self._derivar_mesmo_endereco()
        self.sessao.flush()
        return self.estatisticas

    # ---- Empresas -----------------------------------------------------------------------------

    def _empresa_por_basico(
        self, cnpj_basico: str, rotulo: str, captura_id: str, papel: str, **atributos
    ) -> Entidade:
        entidade, nova = self._obter_ou_criar(
            "empresa",
            cnpj_basico,
            rotulo=rotulo or f"Empresa {cnpj_basico}",
            identificador=cnpj_basico,
            atributos={"cnpj_basico": cnpj_basico, **atributos},
            captura_id=captura_id,
            papel=papel,
        )
        if not nova and atributos:
            self._atualizar(entidade, captura_id, **atributos)
        return entidade

    def _empresa(self, r: RegistroEmpresa) -> None:
        atributos = {
            "razao_social": r.razao_social,
            "natureza_juridica": r.natureza_juridica,
            "qualificacao_responsavel": r.qualificacao_responsavel,
            "capital_social": r.capital_social,
            "porte": r.porte,
            "ente_federativo": r.ente_federativo,
        }
        entidade = self._empresa_por_basico(
            r.cnpj_basico, r.razao_social, r.captura_id, "empresas", **atributos
        )
        if r.razao_social and entidade.rotulo != r.razao_social:
            entidade.rotulo = r.razao_social

    def _estabelecimento(self, r: RegistroEstabelecimento) -> None:
        empresa = self._empresa_por_basico(r.cnpj_basico, "", r.captura_id, "estabelecimentos")
        chave_endereco = normalizar_endereco(
            r.tipo_logradouro,
            r.logradouro,
            r.numero,
            r.complemento,
            r.bairro,
            r.cep,
            r.municipio,
            r.uf,
        )
        legivel = endereco_legivel(
            r.tipo_logradouro,
            r.logradouro,
            r.numero,
            r.complemento,
            r.bairro,
            r.cep,
            r.municipio,
            r.uf,
        )
        estabelecimento = {
            "cnpj": r.cnpj_completo,
            "matriz_filial": "matriz" if r.matriz_filial == "1" else "filial",
            "nome_fantasia": r.nome_fantasia,
            "situacao_cadastral": r.situacao_cadastral,
            "data_situacao_cadastral": data_layout_para_iso(r.data_situacao_cadastral),
            "data_inicio_atividade": data_layout_para_iso(r.data_inicio_atividade),
            "cnae_fiscal_principal": r.cnae_fiscal_principal,
            "endereco": legivel,
            "endereco_normalizado": chave_endereco,
            "uf": r.uf,
            "municipio": r.municipio,
            "captura_id": r.captura_id,
        }
        atributos = dict(empresa.atributos or {})
        lista = [
            e for e in atributos.get("estabelecimentos", []) if e.get("cnpj") != r.cnpj_completo
        ]
        lista.append(estabelecimento)
        atributos["estabelecimentos"] = lista
        matriz = next((e for e in lista if e["matriz_filial"] == "matriz"), lista[0])
        atributos["cnpj"] = matriz["cnpj"]
        atributos["nome_fantasia"] = matriz["nome_fantasia"]
        atributos["situacao_cadastral"] = matriz["situacao_cadastral"]
        atributos["data_inicio_atividade"] = matriz["data_inicio_atividade"]
        atributos["endereco"] = matriz["endereco"]
        atributos["endereco_normalizado"] = matriz["endereco_normalizado"]
        atributos["cnae_fiscal_principal"] = matriz["cnae_fiscal_principal"]
        atributos["enderecos_normalizados"] = sorted(
            {e["endereco_normalizado"] for e in lista if e["endereco_normalizado"]}
        )
        empresa.atributos = atributos
        flag_modified(empresa, "atributos")
        self.estatisticas.entidades_atualizadas += 1

    # ---- Sócios e pessoas ---------------------------------------------------------------------

    def _pessoa(
        self,
        nome: str,
        documento: str,
        captura_id: str,
        papel: str,
        escopo: str = "",
        prefixo: str = "",
        **atributos,
    ) -> Entidade:
        """Pessoa física. Sem CPF descaracterizado, a chave fica restrita ao ``escopo`` (a empresa
        de origem), para que dois registros só com o mesmo nome nunca sejam fundidos.

        ``prefixo`` separa as pessoas vindas de outras fontes (servidor, doador): mesmo com o
        mesmo nome e os mesmos dígitos visíveis do CPF, elas não são fundidas com o sócio."""
        nome_norm = normalizar_nome(nome)
        cpf = mascarar_cpf(documento)
        chave = f"{prefixo}{cpf}|{nome_norm}" if cpf else f"{prefixo}|{nome_norm}|{escopo}"
        entidade, nova = self._obter_ou_criar(
            "pessoa",
            chave,
            rotulo=nome.strip() or nome_norm,
            identificador=cpf,
            atributos={
                "nome": nome.strip(),
                "nome_normalizado": nome_norm,
                "cpf_mascarado": cpf,
                **atributos,
            },
            captura_id=captura_id,
            papel=papel,
        )
        if nova:
            self._sugerir_homonimos(entidade, nome_norm, cpf)
            self._indexar_pessoa(entidade)
        elif atributos:
            self._atualizar(entidade, captura_id, **atributos)
        return entidade

    def _sugerir_homonimos(self, nova: Entidade, nome_norm: str, cpf: str) -> None:
        # Sem nome não há homônimo; o índice por nome evita varrer todas as entidades do caso.
        if not nome_norm:
            return
        grupo = self._pessoas_por_nome.get(nome_norm, [])
        if len(grupo) >= self.max_grupo:
            if nome_norm not in self._nomes_no_limite:
                self._nomes_no_limite.add(nome_norm)
                self.estatisticas.avisos.append(
                    f"O nome '{nome_norm}' aparece em mais de {self.max_grupo} pessoas: as "
                    "sugestões de homônimo além desse limite não foram geradas "
                    "(RESOLUCAO_MAX_GRUPO)."
                )
            return
        for outra in list(grupo):
            if outra.id == nova.id:
                continue
            cpf_outra = (outra.atributos or {}).get("cpf_mascarado") or ""
            if cpf and cpf_outra and cpf == cpf_outra:
                # Só acontece entre origens diferentes (sócio, servidor, doador): a chave de cada
                # origem tem prefixo próprio, então não houve fusão.
                score, motivo = (
                    0.9,
                    (
                        f"Possível coincidência: mesmo nome normalizado '{nome_norm}' e mesmos "
                        f"dígitos visíveis do CPF descaracterizado ({cpf}) em registros de origens "
                        "diferentes. Seis dígitos e o nome não identificam uma pessoa: confirmar "
                        "em fonte com o CPF completo antes de unificar."
                    ),
                )
            elif cpf and cpf_outra and cpf != cpf_outra:
                score, motivo = (
                    0.5,
                    (
                        f"Mesmo nome normalizado '{nome_norm}' com CPF descaracterizado diferente "
                        f"({cpf} e {cpf_outra}): provável homônimo; confirmar antes de unificar."
                    ),
                )
            else:
                score, motivo = (
                    0.7,
                    (
                        f"Mesmo nome normalizado '{nome_norm}'; um dos registros não traz CPF "
                        f"descaracterizado ({cpf or 'ausente'} e {cpf_outra or 'ausente'}): "
                        "pode ser a "
                        "mesma pessoa, mas a fonte não permite confirmar."
                    ),
                )
            for a, b in ((nova.id, outra.id), (outra.id, nova.id)):
                if (a, b) in self._sugestoes:
                    continue
                self.sessao.add(
                    Sugestao(
                        caso_id=self.caso_id,
                        entidade_id=a,
                        candidata_id=b,
                        score=score,
                        motivo=motivo,
                    )
                )
                self._sugestoes.add((a, b))
                self.estatisticas.sugestoes_novas += 1

    def _socio(self, r: RegistroSocio) -> None:
        empresa = self._empresa_por_basico(r.cnpj_basico, "", r.captura_id, "socios")
        documento = (r.cnpj_cpf_socio or "").strip()
        # Sócio pessoa jurídica: o layout diz "CPF OU CNPJ DO SÓCIO". Nos arquivos de 11/2026 o
        # campo veio com o CNPJ básico (8 posições); aceitam-se os dois formatos (8 ou 14),
        # numéricos ou alfanuméricos.
        cnpj_socio = normalizar_cnpj(documento)
        basico_socio = (
            normalizar_cnpj_basico(documento)
            if len(limpar_documento(documento))
            in (
                8,
                14,
            )
            else ""
        )
        if r.identificador_socio == "1" and basico_socio:
            socio = self._empresa_por_basico(
                basico_socio,
                r.nome_socio,
                r.captura_id,
                "socio_pj",
                **({"cnpj": cnpj_socio} if cnpj_socio else {}),
            )
        else:
            socio = self._pessoa(
                r.nome_socio,
                documento,
                r.captura_id,
                "socios",
                escopo=r.cnpj_basico,
                faixa_etaria=r.faixa_etaria,
                pais=r.pais,
                estrangeiro=r.identificador_socio == "3",
            )
        self._aresta(
            socio,
            empresa,
            "socio_de",
            r.captura_id,
            chave=r.cnpj_basico,
            atributos={
                "qualificacao_socio": r.qualificacao_socio,
                "data_entrada_sociedade": data_layout_para_iso(r.data_entrada_sociedade),
                "identificador_socio": r.identificador_socio,
                "representante_legal": mascarar_cpf(r.representante_legal),
                "nome_representante": r.nome_representante,
            },
        )

    # ---- Contratos ----------------------------------------------------------------------------

    def _contrato(self, r: RegistroContrato) -> None:
        if not r.numero_controle_pncp:
            self.estatisticas.avisos.append("Contrato sem numeroControlePNCP ignorado.")
            return
        orgao, _ = self._obter_ou_criar(
            "orgao",
            self._chave_orgao(r),
            rotulo=r.orgao_razao_social or f"Órgão {r.orgao_cnpj}",
            identificador=r.orgao_cnpj,
            atributos={
                "cnpj": r.orgao_cnpj,
                "razao_social": r.orgao_razao_social,
                "unidade": r.extras.get("unidadeOrgao"),
                "municipio": r.extras.get("municipioOrgao"),
                "uf": r.extras.get("ufOrgao"),
                "esfera": r.extras.get("esferaId"),
                "poder": r.extras.get("poderId"),
            },
            captura_id=r.captura_id,
            papel="orgaoEntidade",
        )
        # Órgão criado antes de a esfera ser coletada (ou só pela coleta de servidores): completa.
        faltando = {
            nome: valor
            for nome, valor in (
                ("esfera", r.extras.get("esferaId")),
                ("poder", r.extras.get("poderId")),
                ("municipio", r.extras.get("municipioOrgao")),
                ("uf", r.extras.get("ufOrgao")),
                ("razao_social", r.orgao_razao_social),
            )
            if valor and not (orgao.atributos or {}).get(nome)
        }
        if faltando:
            self._atualizar(orgao, r.captura_id, **faltando)
            if r.orgao_razao_social and orgao.rotulo.startswith("Órgão "):
                orgao.rotulo = r.orgao_razao_social
        # CPF: 11 dígitos. CNPJ: 14 posições, numérico ou alfanumérico (``normalizar_cnpj``).
        fornecedor_pf = r.tipo_pessoa_fornecedor.upper() == "PF" or (
            len(r.ni_fornecedor) == 11 and r.ni_fornecedor.isdigit()
        )
        contrato, novo = self._obter_ou_criar(
            "contrato",
            r.numero_controle_pncp,
            rotulo=f"Contrato {r.numero_controle_pncp}",
            identificador=r.numero_controle_pncp,
            atributos={
                "numero_controle_pncp": r.numero_controle_pncp,
                "objeto": r.objeto,
                "valor_global": r.valor_global,
                "data_assinatura": r.data_assinatura,
                # Fornecedor pessoa física: o PNCP traz o CPF completo; aqui só fica o
                # descaracterizado (RNF04). O CNPJ é mantido inteiro.
                "ni_fornecedor": mascarar_cpf(r.ni_fornecedor)
                if fornecedor_pf
                else r.ni_fornecedor,
                "tipo_pessoa_fornecedor": r.tipo_pessoa_fornecedor,
                "nome_fornecedor": r.nome_fornecedor,
                "orgao_cnpj": r.orgao_cnpj,
                "orgao_razao_social": r.orgao_razao_social,
                "url_fonte": r.url_fonte,
                **{k: v for k, v in r.extras.items() if v is not None},
            },
            captura_id=r.captura_id,
            papel="contrato",
        )
        self._aresta(orgao, contrato, "contratante_de", r.captura_id, chave=r.numero_controle_pncp)

        fornecedor: Entidade | None = None
        ni = r.ni_fornecedor
        if fornecedor_pf:
            fornecedor = self._pessoa(
                r.nome_fornecedor, ni, r.captura_id, "fornecedor", escopo=r.numero_controle_pncp
            )
        elif normalizar_cnpj(ni):
            ni = normalizar_cnpj(ni)
            fornecedor = self._empresa_por_basico(
                ni[:8], r.nome_fornecedor, r.captura_id, "fornecedor", cnpj=ni
            )
        if fornecedor is None:
            self.estatisticas.avisos.append(
                f"Contrato {r.numero_controle_pncp} sem fornecedor identificável "
                f"(niFornecedor='{descaracterizar_texto(ni, contexto_cpf=True)}')."
            )
            return
        self._aresta(
            fornecedor, contrato, "fornecedor_de", r.captura_id, chave=r.numero_controle_pncp
        )
        self._aresta(
            fornecedor,
            orgao,
            "contratado_por",
            r.captura_id,
            chave=r.numero_controle_pncp,
            atributos={
                "contrato_id": contrato.id,
                "numero_controle_pncp": r.numero_controle_pncp,
                "valor_global": r.valor_global,
                "data_assinatura": r.data_assinatura,
                "objeto": r.objeto,
            },
        )

    @staticmethod
    def _chave_orgao(r: RegistroContrato) -> str:
        """Órgão sem CNPJ: chave pelo nome; sem nome também, a chave fica restrita ao contrato,
        para que órgãos anônimos de contratos diferentes nunca sejam fundidos."""
        if r.orgao_cnpj:
            return r.orgao_cnpj
        nome = normalizar_nome(r.orgao_razao_social)
        if nome:
            return f"sem-cnpj:{nome}"
        return f"sem-cnpj:contrato:{r.numero_controle_pncp}"

    # ---- Horizonte 2: termos, sanções, servidores, candidatos, doações e editais --------------

    def _termo(self, r: RegistroTermo) -> None:
        contrato = self._entidades.get(("contrato", r.numero_controle_pncp))
        if contrato is None:
            self.estatisticas.avisos.append(
                f"Termo do contrato {r.numero_controle_pncp or '(sem número)'} ignorado: o "
                "contrato não está no caso."
            )
            return
        identidade = r.sequencial_termo if r.sequencial_termo is not None else r.numero_termo
        termo = {
            "sequencial": r.sequencial_termo,
            "numero": r.numero_termo,
            "tipo": r.tipo_termo,
            "objeto": r.objeto,
            "valor_acrescido": r.valor_acrescido,
            "acrescimo_supressao": r.acrescimo_supressao,
            "reajuste": r.reajuste,
            "data_assinatura": r.data_assinatura,
            "fundamento_legal": r.fundamento_legal,
            "captura_id": r.captura_id,
        }
        atributos = dict(contrato.atributos or {})
        lista = [
            t
            for t in atributos.get("termos", [])
            if (t.get("sequencial") if t.get("sequencial") is not None else t.get("numero"))
            != identidade
        ]
        lista.append(termo)
        atributos["termos"] = sorted(lista, key=lambda t: t.get("sequencial") or 0)
        contrato.atributos = atributos
        flag_modified(contrato, "atributos")
        self._registrar_fonte(contrato, r.captura_id, "termo")
        self.estatisticas.entidades_atualizadas += 1

    def _sancao(self, r: RegistroSancao) -> None:
        empresa = self._empresa_por_basico(
            r.cnpj_sancionado[:8], r.nome_sancionado, r.captura_id, "sancionado"
        )
        identidade = r.id_registro or f"{r.cnpj_sancionado}:{r.data_inicio}:{r.tipo_sancao}"
        chave = f"{r.cadastro}:{identidade}"
        atributos = {
            "cadastro": r.cadastro,
            "id_registro": r.id_registro,
            "cnpj_sancionado": r.cnpj_sancionado,
            "nome_sancionado": r.nome_sancionado,
            "data_inicio": r.data_inicio,
            "data_fim": r.data_fim,
            "tipo_sancao": r.tipo_sancao,
            "orgao_sancionador": r.orgao_sancionador,
            "numero_processo": r.numero_processo,
            "url_fonte": r.url_fonte,
        }
        sancao, nova = self._obter_ou_criar(
            "sancao",
            chave,
            rotulo=f"{r.cadastro}: {r.tipo_sancao or 'sanção'} ({r.cnpj_sancionado})",
            identificador=f"{r.cadastro}-{r.id_registro}" if r.id_registro else r.cadastro,
            atributos=atributos,
            captura_id=r.captura_id,
            papel=r.cadastro.lower(),
        )
        if not nova:
            self._atualizar(sancao, r.captura_id, **atributos)
        self._aresta(
            empresa,
            sancao,
            "sancionada_em",
            r.captura_id,
            chave=chave,
            atributos={
                "cadastro": r.cadastro,
                "data_inicio": r.data_inicio,
                "data_fim": r.data_fim,
            },
        )

    def _servidor(self, r: RegistroServidor) -> None:
        orgao, _ = self._obter_ou_criar(
            "orgao",
            r.cnpj_orgao,
            rotulo=r.nome_orgao or f"Órgão {r.cnpj_orgao}",
            identificador=r.cnpj_orgao,
            atributos={"cnpj": r.cnpj_orgao},
            captura_id=r.captura_id,
            papel="orgao_servidores",
        )
        # Só o necessário (RNF04): nome, dígitos visíveis do CPF, órgão e situação funcional.
        servidor = self._pessoa(
            r.nome,
            r.cpf,
            r.captura_id,
            "servidor",
            escopo=r.codigo_orgao,
            prefixo=f"{PREFIXO_SERVIDOR}{r.codigo_orgao}:",
            papel_fonte="servidor",
            codigo_orgao_siape=r.codigo_orgao,
            nome_orgao_siape=r.nome_orgao,
            situacao=r.situacao,
            tipo_servidor=r.tipo_servidor,
        )
        self._aresta(
            servidor,
            orgao,
            "servidor_de",
            r.captura_id,
            chave=r.codigo_orgao,
            atributos={
                "vinculo": r.vinculo,
                "codigo_orgao_siape": r.codigo_orgao,
                # A correspondência entre o código SIAPE e o CNPJ do órgão contratante foi
                # declarada pelo investigador nos parâmetros da coleta.
                "correspondencia_declarada": True,
            },
        )

    def _candidato(self, r: RegistroCandidato) -> Entidade:
        atributos = {
            "ano": r.ano,
            "sq_candidato": r.sq_candidato,
            "nome": r.nome,
            "nome_urna": r.nome_urna,
            "cargo": r.cargo,
            "partido": r.partido,
            "uf": r.uf,
            "sg_ue": r.sg_ue,
            "nm_ue": r.nm_ue,
            "situacao_turno": r.situacao_turno,
        }
        candidato, novo = self._obter_ou_criar(
            "candidato",
            f"{r.ano}:{r.sq_candidato}",
            rotulo=r.nome or f"Candidato {r.sq_candidato}",
            identificador=r.sq_candidato,
            atributos=atributos,
            captura_id=r.captura_id,
            papel=r.origem,
        )
        # O arquivo de candidatos é a referência; o de receitas só completa o que faltar.
        if not novo:
            atuais = candidato.atributos or {}
            escrever = {
                k: v
                for k, v in atributos.items()
                if v and (r.origem == "consulta_cand" or not atuais.get(k)) and atuais.get(k) != v
            }
            if escrever:
                self._atualizar(candidato, r.captura_id, **escrever)
            if r.nome and r.origem == "consulta_cand":
                candidato.rotulo = r.nome
        return candidato

    def _doacao(self, r: RegistroDoacao) -> None:
        candidato = self._entidades.get(("candidato", f"{r.ano}:{r.sq_candidato}"))
        if candidato is None:
            candidato = self._candidato(
                RegistroCandidato(
                    captura_id=r.captura_id,
                    ano=r.ano,
                    sq_candidato=r.sq_candidato,
                    nome=r.nome_candidato,
                    origem="receitas",
                )
            )
        documento = limpar_documento(r.documento_doador)
        nome = r.nome_doador_rfb or r.nome_doador
        if normalizar_cnpj(documento):
            doador = self._empresa_por_basico(documento[:8], nome, r.captura_id, "doador")
        elif len(documento) == 11 and documento.isdigit():
            doador = self._pessoa(
                nome,
                documento,
                r.captura_id,
                "doador",
                prefixo=PREFIXO_DOADOR,
                papel_fonte="doador",
            )
        else:
            return
        self._aresta(
            doador,
            candidato,
            "doou_para",
            r.captura_id,
            chave=r.chave,
            atributos={
                "valor": r.valor,
                "data": r.data,
                "ano": r.ano,
                "origem_receita": r.origem_receita,
            },
        )

    def _edital(self, r: RegistroEdital) -> None:
        atributos = {
            "nome_arquivo": r.nome_arquivo,
            "codificacao": r.codificacao,
            "caracteres": r.caracteres,
            "numero_controle_pncp_compra": r.numero_controle_pncp_compra,
            "cnpj_orgao": r.cnpj_orgao,
            "captura_texto_id": r.captura_id,
            "formato": r.formato,
            "texto_extraivel": r.texto_extraivel,
            **({"paginas": r.paginas} if r.paginas is not None else {}),
        }
        edital, novo = self._obter_ou_criar(
            "edital",
            r.nome_arquivo,
            rotulo=f"Edital {r.nome_arquivo}",
            identificador=r.numero_controle_pncp_compra or r.nome_arquivo,
            atributos=atributos,
            captura_id=r.captura_id,
            papel="edital",
        )
        if not novo:
            self._atualizar(edital, r.captura_id, **atributos)
        orgao = self._entidades.get(("orgao", r.cnpj_orgao)) if r.cnpj_orgao else None
        if orgao is not None:
            self._aresta(orgao, edital, "edital_de", r.captura_id, chave=r.nome_arquivo)

    # ---- Derivadas ----------------------------------------------------------------------------

    def _derivar_mesmo_endereco(self) -> None:
        por_endereco: dict[str, list[Entidade]] = {}
        for (tipo, _), e in self._entidades.items():
            if tipo != "empresa":
                continue
            for chave in (e.atributos or {}).get("enderecos_normalizados", []):
                # Chave vazia (endereço em branco ou só com a cidade) nunca liga empresas.
                if chave:
                    por_endereco.setdefault(chave, []).append(e)
        for chave_endereco, empresas in por_endereco.items():
            if len(empresas) < 2:
                continue
            empresas = sorted(empresas, key=lambda e: e.chave)
            if len(empresas) > self.max_grupo:
                # Grupo grande: estrela (n-1 arestas, todas verdadeiras) em vez de n(n-1)/2 pares.
                pares = [(empresas[0], b) for b in empresas[1:]]
                self.estatisticas.avisos.append(
                    f"Endereço compartilhado por {len(empresas)} empresas: acima de "
                    f"{self.max_grupo} (RESOLUCAO_MAX_GRUPO), as arestas mesmo_endereco ligam "
                    "cada empresa só à primeira do grupo; a tipologia T2 continua vendo o "
                    "grupo inteiro."
                )
            else:
                pares = [(a, b) for i, a in enumerate(empresas) for b in empresas[i + 1 :]]
            for a, b in pares:
                self._aresta(
                    a,
                    b,
                    "mesmo_endereco",
                    self._captura_do_endereco(a, chave_endereco),
                    chave=chave_endereco,
                    atributos={
                        "endereco_normalizado": chave_endereco,
                        "endereco": self._endereco_legivel(a, chave_endereco),
                        "derivada": True,
                        "grupo_em_estrela": len(empresas) > self.max_grupo,
                    },
                )

    @staticmethod
    def _captura_do_endereco(empresa: Entidade, chave_endereco: str) -> str:
        for est in (empresa.atributos or {}).get("estabelecimentos", []):
            if est.get("endereco_normalizado") == chave_endereco:
                return est["captura_id"]
        return empresa.captura_id

    @staticmethod
    def _endereco_legivel(empresa: Entidade, chave_endereco: str) -> str:
        for est in (empresa.atributos or {}).get("estabelecimentos", []):
            if est.get("endereco_normalizado") == chave_endereco:
                return est["endereco"]
        return chave_endereco
