"""Configuração lida do ambiente (.env ou variáveis de ambiente).

A configuração falha fechada: sem ``AUDITORIA_CHAVE_HMAC`` ou sem a chave da custódia
(``CUSTODIA_CHAVE`` ou ``CUSTODIA_CHAVE_ARQUIVO``), o serviço não inicia.

Autenticação (RNF01, H23): o acesso normal é por usuário real (senha + MFA por TOTP + sessão). Os
tokens fixos de ``TOKENS_DEV`` só existem no modo de desenvolvimento explícito (``MODO_DEV=1``),
que também liga ``/docs``; fora dele, um ``TOKENS_DEV`` preenchido impede o serviço de iniciar.
"""

from __future__ import annotations

import hashlib
import ipaddress
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from buscador import cripto

PERFIS = ("analista", "delegado", "admin")

TAMANHO_MINIMO_CHAVE_HMAC = 32


class ErroConfiguracao(ValueError):
    """Configuração insegura ou incompleta: o serviço não deve iniciar."""


@dataclass(frozen=True)
class Usuario:
    usuario: str
    perfil: str
    # "token_dev" (TOKENS_DEV, só com MODO_DEV=1), "sessao" (usuário real) ou "sistema".
    origem: str = "token_dev"
    sessao_id: str | None = None


def resumo_token(token: str) -> str:
    """Os tokens ficam em memória só como SHA-256; a busca não compara o segredo em claro."""
    return hashlib.sha256(token.encode("utf-8", "surrogatepass")).hexdigest()


# Tokens reservados, na janela de contexto, para as instruções e o molde de conversa do modelo.
RESERVA_INSTRUCOES_TOKENS = 600


class Configuracao(BaseSettings):
    """Todas as variáveis de ambiente usadas pelo serviço (ver .env.example)."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "sqlite:///./dados/buscador.db"
    # Falso quando o papel da aplicação não é dono das tabelas (Compose): o esquema e os gatilhos
    # são criados antes, pelo papel dono, com ``python -m buscador.migrar``.
    db_criar_esquema: bool = True
    dados_dir: Path = Path("./dados")

    # Sem valor padrão: precisam ser definidos pelo operador.
    auditoria_chave_hmac: str = ""
    # Tokens fixos de desenvolvimento: só são aceitos com MODO_DEV=1.
    tokens_dev: str = ""
    modo_dev: bool = False

    # ---- Criptografia em repouso (RNF01, H23) -------------------------------------------------
    # Chave mestra de 32 bytes em hexadecimal, fora do banco: na variável ou num arquivo (um dos
    # dois). Cifra as capturas no disco e os segredos de TOTP no banco (subchaves por HKDF).
    custodia_chave: str = ""
    custodia_chave_arquivo: Path | None = None
    # Chaves mestras anteriores (hexadecimal, separadas por vírgula): só para LER o que foi
    # cifrado antes de uma troca de chave.
    custodia_chaves_anteriores: str = ""

    # ---- Usuários, MFA e sessão (RNF01, H23) --------------------------------------------------
    # Perfis para os quais o segundo fator (TOTP, RFC 6238) é obrigatório.
    mfa_perfis_obrigatorios: str = "analista,delegado,admin"
    totp_emissor: str = "Buscador OSINT"
    senha_tamanho_minimo: int = Field(default=12, ge=8, le=128)
    # Sessão: duração máxima, tempo máximo sem uso e validade da etapa pendente (senha aceita,
    # falta o código do segundo fator).
    sessao_duracao_minutos: int = Field(default=480, ge=1, le=1440)
    sessao_inatividade_minutos: int = Field(default=30, ge=1, le=1440)
    sessao_pendente_minutos: int = Field(default=5, ge=1, le=60)
    # Depois de N falhas seguidas (senha ou código), a conta fica bloqueada por um tempo.
    login_max_falhas: int = Field(default=5, ge=1, le=100)
    login_bloqueio_minutos: int = Field(default=15, ge=1, le=1440)

    # ---- Grafo (RNF06, H29) -------------------------------------------------------------------
    # Tetos das consultas de vínculo feitas direto no banco (ver docs/desempenho-grafo.md).
    grafo_max_nos: int = Field(default=20_000, ge=100, le=1_000_000)
    grafo_max_nos_visitados: int = Field(default=200_000, ge=1_000, le=10_000_000)

    # ---- Extração assistida por IA (RF13, RNF05, H22) -----------------------------------------
    # Desligada por padrão. Só provedor LOCAL: nenhum dado de investigação sai do servidor.
    ia_extracao_ativa: bool = False
    ia_provedor: str = "ollama"
    ia_ollama_url: str = "http://127.0.0.1:11434"
    ia_modelo: str = ""
    # Nomes de host aceitos além de endereços de loopback e de rede privada (ex.: o nome do
    # serviço no Docker Compose), separados por vírgula.
    ia_hosts_locais: str = ""
    # Tempo limite das chamadas curtas ao servidor (identidade do modelo, janela em uso) e da
    # conexão. A geração de cada bloco tem limites próprios (abaixo).
    ia_timeout_segundos: float = Field(default=120.0, gt=0, le=3600)
    # Tempo que o Ollama mantém o modelo carregado depois da chamada ("5m", "1h", "0", "-1").
    # Vazio (o padrão): o campo não é enviado e vale o padrão do servidor.
    ia_keep_alive: str = Field(default="", pattern=r"^\s*(-?\d+|-?(\d+(ms|s|m|h))+)?\s*$")
    ia_max_caracteres: int = Field(default=200_000, ge=1_000, le=5_000_000)
    # Janela de contexto pedida ao servidor e teto de tokens gerados por bloco.
    ia_num_ctx: int = Field(default=8_192, ge=1_024, le=131_072)
    ia_num_predict: int = Field(default=2_048, ge=64, le=32_768)
    # Tamanho do bloco e da sobreposição entre blocos vizinhos, em tokens ESTIMADOS: o serviço
    # não tem o tokenizador do modelo e converte por ``ia_caracteres_por_token``. Em documento
    # denso (um item por linha) a saída medida passou de 1,4 token por token de texto: 700
    # tokens de texto deixam a saída perto da metade do teto de ``ia_num_predict``.
    ia_bloco_tokens: int = Field(default=700, ge=30, le=50_000)
    ia_sobreposicao_tokens: int = Field(default=40, ge=0, le=5_000)
    ia_caracteres_por_token: float = Field(default=3.0, ge=1, le=10, allow_inf_nan=False)
    # Limites por bloco: tempo total da geração e tempo máximo sem receber nenhum token.
    ia_bloco_tempo_segundos: float = Field(default=600.0, gt=0, le=7_200)
    ia_sem_token_segundos: float = Field(default=120.0, gt=0, le=3_600)
    # Repetições de um bloco depois de falha transitória, com espera que dobra a cada uma.
    ia_repeticoes: int = Field(default=2, ge=0, le=10)
    ia_espera_repeticao_segundos: float = Field(default=5.0, ge=0, le=600)
    # Quantas vezes um bloco pode ser dividido ao meio (saída cortada ou tempo esgotado).
    ia_max_divisoes: int = Field(default=3, ge=0, le=8)
    # Blocos seguidos com falha transitória que param a execução como "interrompida".
    ia_falhas_seguidas_max: int = Field(default=3, ge=1, le=100)
    # Trabalhador em segundo plano. Com mais de um processo da API, ligue em um só.
    ia_trabalhador_ativo: bool = True
    ia_trabalhador_intervalo_segundos: float = Field(default=2.0, gt=0, le=300)
    ia_max_itens: int = Field(default=300, ge=1, le=5_000)
    ia_temperatura: float = Field(default=0.0, ge=0, le=2, allow_inf_nan=False)
    ia_semente: int = Field(default=0, ge=0, le=2**31 - 1)

    cors_origens: str = "http://localhost:5190,http://localhost:8090"
    max_corpo_bytes: int = Field(default=262_144, ge=1_024)

    # Raiz de ingestão do conector cnpj_arquivo; sem ela a fonte fica desativada.
    cnpj_dir_base: Path | None = None
    cnpj_max_bytes_arquivo: int = Field(default=16 * 1024 * 1024, ge=1)
    cnpj_max_registros: int = Field(default=100_000, ge=1)

    pncp_url_base: str = "https://pncp.gov.br/api/consulta"
    pncp_timeout_segundos: float = Field(default=30.0, gt=0)
    pncp_tentativas: int = Field(default=3, ge=1, le=10)
    pncp_espera_retentativa_segundos: float = Field(default=1.0, ge=0)
    pncp_max_paginas: int = Field(default=20, ge=1, le=1000)
    pncp_max_dias_periodo: int = Field(default=366, ge=1)
    pncp_max_bytes_pagina: int = Field(default=8 * 1024 * 1024, ge=1_024)
    pncp_prazo_total_segundos: float = Field(default=120.0, gt=0)

    # API de integração do PNCP ("API PNCP"), usada só para consultar termos de contrato.
    pncp_integracao_url_base: str = "https://pncp.gov.br/api/pncp"
    # Sem limite documentado para o PNCP: valor conservador, decisão do autor.
    pncp_termos_requisicoes_por_minuto: float = Field(default=60, ge=0, le=6000)
    pncp_termos_max_contratos: int = Field(default=100, ge=1, le=500)

    # Portal da Transparência (CEIS, CNEP e servidores). Sem chave, a fonte fica "não configurada".
    portal_transparencia_chave: str = ""
    portal_transparencia_url_base: str = "https://api.portaldatransparencia.gov.br"
    portal_transparencia_timeout_segundos: float = Field(default=30.0, gt=0)
    # Controle de taxa (RNF12). Limite documentado: 400 requisições por minuto das 6:00 às 23:59
    # e 700 das 00:00 às 5:59 (https://portaldatransparencia.gov.br/api-de-dados). O padrão fica
    # abaixo do limite diurno; o teto aceito é o limite noturno.
    portal_transparencia_requisicoes_por_minuto: float = Field(default=300, gt=0, le=700)
    portal_transparencia_max_paginas: int = Field(default=200, ge=1, le=1000)

    # Raízes de ingestão dos conectores de arquivo do TSE e de editais; vazias = fonte desativada.
    tse_dir_base: Path | None = None
    tse_max_bytes_arquivo: int = Field(default=64 * 1024 * 1024, ge=1)
    tse_max_registros: int = Field(default=200_000, ge=1)
    # Maior CSV (ou ZIP) do TSE que o conector aceita percorrer EM FLUXO, guardando só o recorte
    # filtrado (decisão do autor: 2 GiB cobre o maior CSV conferido em 17/11/2026,
    # receitas_candidatos_2024_BRASIL, de 1,4 GB). O recorte continua limitado por
    # TSE_MAX_BYTES_ARQUIVO.
    tse_max_bytes_fluxo: int = Field(default=2 * 1024 * 1024 * 1024, ge=1)
    editais_dir_base: Path | None = None
    editais_max_bytes_arquivo: int = Field(default=4 * 1024 * 1024, ge=1)
    editais_max_arquivos: int = Field(default=50, ge=1)
    # Limites defensivos da leitura de PDF (edital_arquivo, rota de texto, T10 e IA): tamanho do
    # arquivo, número de páginas, total de caracteres extraídos e prazo da extração.
    editais_pdf_max_bytes: int = Field(default=50 * 1024 * 1024, ge=1_024)
    editais_pdf_max_paginas: int = Field(default=300, ge=1, le=5_000)
    editais_pdf_max_caracteres: int = Field(default=5_000_000, ge=1_000)
    editais_pdf_tempo_segundos: float = Field(default=60.0, gt=0, le=600)

    # Retentativa dos conectores que não têm ajuste próprio (RNF12): hoje, o cnpj_arquivo e o
    # stub do Portal da Transparência. O PNCP usa PNCP_TENTATIVAS.
    coleta_tentativas: int = Field(default=3, ge=1, le=10)
    coleta_espera_retentativa_segundos: float = Field(default=1.0, ge=0)

    # Tamanho máximo de um grupo (mesmo endereço ou mesmo nome) expandido par a par.
    resolucao_max_grupo: int = Field(default=50, ge=2)

    t1_dias_minimos: int = Field(default=180, ge=0)
    t2_minimo_empresas: int = Field(default=3, ge=2)
    t3_razao_minima: float = Field(default=10.0, gt=0, allow_inf_nan=False)

    # ---- Tipologias do horizonte 2 (T5 a T10) -------------------------------------------------
    # T5 (fracionamento): limites de dispensa por exercício, em JSON. Vazio = só a tabela do
    # código (``tipologias.legislacao.LIMITES_DISPENSA``, com 2026 do Decreto 12.807/2025).
    # Exemplo: {"2027": {"inciso_i": 135000.00, "inciso_ii": 67500.00}}
    t5_limites_dispensa: str = ""
    t5_minimo_contratacoes: int = Field(default=2, ge=2, le=1000)
    # Critério de "mesma natureza": fornecedor_e_categoria | categoria | fornecedor.
    t5_criterio_natureza: str = "fornecedor_e_categoria"
    # T8 (aditivos): percentuais do art. 125 da Lei 14.133/2021.
    t8_percentual_limite: float = Field(default=25.0, gt=0, le=1000, allow_inf_nan=False)
    t8_percentual_limite_reforma: float = Field(default=50.0, gt=0, le=1000, allow_inf_nan=False)
    # T9 (doador contratado): situações de totalização que contam como "eleito", separadas por
    # vírgula. O leiame do TSE não lista os valores; os abaixo são os que apareceram para os
    # eleitos no arquivo real de candidatos de 2026 (DF), conferido em 17/11/2026.
    t9_situacoes_eleito: str = "ELEITO,ELEITO POR QP,ELEITO POR MÉDIA"
    # T10 (cláusula restritiva): expressões regulares extras, separadas por ";;".
    t10_expressoes_extras: str = ""

    # ---- Tipologias de lavagem e ocultação (L) ------------------------------------------------
    # Todos os limiares abaixo são DECISÃO DO AUTOR, a validar com o orientador (critérios e
    # fontes em ``buscador/tipologias/lavagem.py``).
    # L1 (endereço de registro em massa): empresas ativas no mesmo endereço, com complemento, e
    # quantas abertas num intervalo de dias para marcar "abertura concentrada".
    l1_minimo_estabelecimentos: int = Field(default=5, ge=2, le=10_000)
    l1_janela_abertura_dias: int = Field(default=180, ge=1, le=36_500)
    l1_minimo_abertas_na_janela: int = Field(default=3, ge=2, le=10_000)
    # L2 (sinal fraco): empresas, em grupos sem sócio em comum, com o mesmo telefone ou e-mail.
    l2_minimo_empresas: int = Field(default=3, ge=2, le=10_000)
    # L3 (mesmo administrador): empresas administradas e quantas abertas em N meses.
    l3_minimo_empresas: int = Field(default=5, ge=2, le=10_000)
    l3_minimo_abertas_na_janela: int = Field(default=3, ge=1, le=10_000)
    l3_janela_meses: int = Field(default=12, ge=1, le=1_200)
    # L4 (cadeia em camadas): níveis de sócio pessoa jurídica sem pessoa física no primeiro.
    l4_minimo_niveis: int = Field(default=2, ge=2, le=20)
    # L7 (fundadores substituídos): tolerância depois do início e janela antes do contrato.
    l7_dias_apos_abertura: int = Field(default=30, ge=0, le=36_500)
    l7_meses_antes_contrato: int = Field(default=12, ge=1, le=1_200)
    # L14 (sinal fraco): valor contratado mínimo e faixas etárias atípicas (códigos do layout).
    l14_valor_minimo: float = Field(default=100_000.0, ge=0, le=1e12, allow_inf_nan=False)
    l14_faixas_atipicas: str = "1,2,9"
    # L15 (sinal fraco): número de CNAEs e de categorias de objeto contratadas.
    l15_minimo_cnaes: int = Field(default=20, ge=2, le=1_000)
    l15_minimo_categorias: int = Field(default=4, ge=2, le=100)
    # Qualificações de sócio que contam como administração (L3, L10 e L14), por vírgula.
    qualificacoes_administracao: str = "05,10,16,28,49,50,65"
    # L6 (porte ou MEI incompatível): limites do MEI de outros anos-calendário, em JSON. Vazio =
    # só a tabela do código (``tipologias.legislacao.LIMITES_MEI``: 2012, 2018, 2027 e 2028; cada
    # linha vale do seu ano até a linha seguinte). A regra segue a LC nº 123/2006 e não tem
    # tolerância configurável.
    # Exemplo: {"2029": {"anual": 150000.00, "mensal_inicio": 12500.00, "fonte": "lei"}}
    l6_limites_mei: str = ""
    # L16 (entidade sem fins lucrativos convenente): dias mínimos entre o início da atividade e
    # o início da vigência, e naturezas jurídicas tratadas como sem fins lucrativos (tabela de
    # domínio Naturezas do CNPJ: 3069 Fundação Privada, 3301 Organização Social, 3999 Associação
    # Privada).
    l16_dias_minimos: int = Field(default=180, ge=0, le=36_500)
    l16_naturezas_sem_fins_lucrativos: str = "3069,3301,3999"

    # ---- Alerta de combinação (RF14) ----------------------------------------------------------
    # Tipologias distintas e soma de pesos mínimas na mesma empresa ou pessoa (decisão do autor).
    comb_minimo_tipologias: int = Field(default=2, ge=2, le=30)
    comb_peso_minimo: float = Field(default=2.0, gt=0, le=100, allow_inf_nan=False)
    # Pesos diferentes dos do código, em JSON. Exemplo: {"L5": 0.5, "T2": 0.5}
    comb_pesos: str = ""

    # ---- Importação sob ordem judicial (RF10) -------------------------------------------------
    # Módulo separado, DESLIGADO por padrão. Ligado, exige chave de cifra PRÓPRIA (32 bytes em
    # hexadecimal, diferente da chave da custódia) e guarda o dado numa base própria.
    importacao_judicial_ativa: bool = False
    importacao_judicial_chave: str = ""
    # Vazio = arquivo SQLite próprio em ``<DADOS_DIR>/sigiloso/importacao_judicial.db``.
    importacao_judicial_database_url: str = ""
    # Raiz de onde os arquivos recebidos são lidos; sem ela, nada é importado.
    importacao_judicial_dir_base: Path | None = None
    # Perfis que podem registrar a decisão judicial (ato de entrada), por vírgula.
    importacao_judicial_perfis: str = "delegado"
    importacao_judicial_max_bytes_arquivo: int = Field(default=16 * 1024 * 1024, ge=1)
    importacao_judicial_max_linhas: int = Field(default=200_000, ge=1)

    # ---- Monitoramento contínuo (RF06, H18) ---------------------------------------------------
    # Agendador interno (uma thread do próprio serviço). Com mais de um processo da API, ligue-o
    # em um só.
    monitoramento_agendador_ativo: bool = True
    monitoramento_intervalo_segundos: float = Field(default=60.0, ge=1)
    monitoramento_periodicidade_minima_horas: int = Field(default=1, ge=1, le=8760)

    @field_validator(
        "cnpj_dir_base",
        "tse_dir_base",
        "editais_dir_base",
        "custodia_chave_arquivo",
        "importacao_judicial_dir_base",
        mode="before",
    )
    @classmethod
    def _raiz_vazia_e_ausente(cls, valor: object) -> object:
        # CNPJ_DIR_BASE= (vazia) desativa a fonte; não pode virar o diretório corrente.
        return None if isinstance(valor, str) and not valor.strip() else valor

    def tokens(self) -> dict[str, Usuario]:
        """Mapa token -> usuário a partir de TOKENS_DEV (token:usuario:perfil, por vírgula).

        Os tokens fixos só existem no modo de desenvolvimento (``MODO_DEV=1``). Fora dele o mapa é
        vazio e um ``TOKENS_DEV`` preenchido é recusado (falha fechada): o acesso é por usuário
        real, com senha, MFA e sessão. Recusa campo vazio, token repetido e perfil desconhecido.
        """
        if not self.modo_dev:
            if self.tokens_dev.strip():
                raise ErroConfiguracao(
                    "TOKENS_DEV só é aceito no modo de desenvolvimento (MODO_DEV=1). Fora dele, "
                    "deixe TOKENS_DEV vazio e crie os usuários com "
                    "`python -m buscador.usuarios criar` (senha + MFA)."
                )
            return {}
        mapa: dict[str, Usuario] = {}
        for item in self.tokens_dev.split(","):
            item = item.strip()
            if not item:
                continue
            partes = [p.strip() for p in item.split(":")]
            if len(partes) != 3:
                raise ErroConfiguracao(
                    "TOKENS_DEV mal formado: cada item deve ser token:usuario:perfil."
                )
            token, usuario, perfil = partes
            if not token or not usuario:
                raise ErroConfiguracao(
                    "TOKENS_DEV mal formado: token e usuário não podem ser vazios."
                )
            if perfil not in PERFIS:
                raise ErroConfiguracao(
                    f"Perfil desconhecido em TOKENS_DEV: '{perfil}' (aceitos: {PERFIS})"
                )
            if token in mapa:
                raise ErroConfiguracao(f"TOKENS_DEV repete o token do usuário '{usuario}'.")
            mapa[token] = Usuario(usuario=usuario, perfil=perfil)
        return mapa

    def tokens_por_resumo(self) -> dict[str, Usuario]:
        return {resumo_token(token): usuario for token, usuario in self.tokens().items()}

    def chave_auditoria(self) -> bytes:
        """Chave do HMAC da cadeia de auditoria. Fica só no ambiente, nunca no banco."""
        chave = self.auditoria_chave_hmac.strip()
        if len(chave) < TAMANHO_MINIMO_CHAVE_HMAC:
            raise ErroConfiguracao(
                "AUDITORIA_CHAVE_HMAC é obrigatória e deve ter ao menos "
                f"{TAMANHO_MINIMO_CHAVE_HMAC} caracteres (por exemplo, `openssl rand -hex 32`). "
                "Ela autentica a cadeia de auditoria e não pode ficar no banco."
            )
        return chave.encode("utf-8")

    def cofre(self) -> cripto.Cofre:
        """Cofre da criptografia em repouso. A chave mestra fica só no ambiente ou num arquivo."""
        if self.custodia_chave.strip() and self.custodia_chave_arquivo is not None:
            raise ErroConfiguracao(
                "Defina só uma entre CUSTODIA_CHAVE e CUSTODIA_CHAVE_ARQUIVO, não as duas."
            )
        try:
            if self.custodia_chave_arquivo is not None:
                chave = cripto.ler_chave_de_arquivo(self.custodia_chave_arquivo)
            elif self.custodia_chave.strip():
                chave = cripto.interpretar_chave(self.custodia_chave)
            else:
                raise ErroConfiguracao(
                    "CUSTODIA_CHAVE (ou CUSTODIA_CHAVE_ARQUIVO) é obrigatória: é a chave de 32 "
                    "bytes em hexadecimal que cifra as capturas no disco (`openssl rand -hex 32`). "
                    "Ela não pode ficar no banco."
                )
            anteriores = [
                cripto.interpretar_chave(item, "CUSTODIA_CHAVES_ANTERIORES")
                for item in self.custodia_chaves_anteriores.split(",")
                if item.strip()
            ]
        except cripto.ErroCripto as erro:
            raise ErroConfiguracao(str(erro)) from erro
        if chave.hex() == self.auditoria_chave_hmac.strip().lower():
            raise ErroConfiguracao(
                "CUSTODIA_CHAVE e AUDITORIA_CHAVE_HMAC não podem ser a mesma chave."
            )
        return cripto.Cofre(chave, anteriores)

    def perfis_com_mfa(self) -> frozenset[str]:
        perfis = {p.strip() for p in self.mfa_perfis_obrigatorios.split(",") if p.strip()}
        desconhecidos = perfis - set(PERFIS)
        if desconhecidos:
            raise ErroConfiguracao(
                f"Perfil desconhecido em MFA_PERFIS_OBRIGATORIOS: {sorted(desconhecidos)} "
                f"(aceitos: {PERFIS})"
            )
        return frozenset(perfis)

    def perfis_importacao_judicial(self) -> frozenset[str]:
        perfis = {p.strip() for p in self.importacao_judicial_perfis.split(",") if p.strip()}
        desconhecidos = perfis - set(PERFIS)
        if desconhecidos or not perfis:
            raise ErroConfiguracao(
                "IMPORTACAO_JUDICIAL_PERFIS deve listar ao menos um perfil conhecido "
                f"(aceitos: {PERFIS})."
            )
        return frozenset(perfis)

    def chave_importacao_judicial(self) -> bytes:
        """Chave de cifra própria do dado sob ordem judicial: nunca a mesma da custódia."""
        try:
            chave = cripto.interpretar_chave(
                self.importacao_judicial_chave, "IMPORTACAO_JUDICIAL_CHAVE"
            )
        except cripto.ErroCripto as erro:
            raise ErroConfiguracao(str(erro)) from erro
        usadas = {self.custodia_chave.strip().lower(), self.auditoria_chave_hmac.strip().lower()}
        usadas |= {c.strip().lower() for c in self.custodia_chaves_anteriores.split(",")}
        if self.custodia_chave_arquivo is not None:
            try:
                usadas.add(cripto.ler_chave_de_arquivo(self.custodia_chave_arquivo).hex())
            except cripto.ErroCripto:
                pass
        if chave.hex() in usadas:
            raise ErroConfiguracao(
                "IMPORTACAO_JUDICIAL_CHAVE não pode ser igual à chave da custódia nem à da "
                "auditoria: o dado sob ordem judicial tem chave própria."
            )
        return chave

    def url_importacao_judicial(self) -> str:
        url = self.importacao_judicial_database_url.strip()
        if url:
            if url == self.database_url.strip():
                raise ErroConfiguracao(
                    "IMPORTACAO_JUDICIAL_DATABASE_URL não pode ser a base do dado aberto "
                    "(DATABASE_URL): o dado sob ordem judicial fica em base separada."
                )
            return url
        pasta = Path(self.dados_dir) / "sigiloso"
        pasta.mkdir(parents=True, exist_ok=True)
        return f"sqlite:///{pasta / 'importacao_judicial.db'}"

    def validar_importacao_judicial(self) -> None:
        self.perfis_importacao_judicial()
        if not self.importacao_judicial_ativa:
            return
        self.chave_importacao_judicial()
        self.url_importacao_judicial()

    def hosts_locais_ia(self) -> frozenset[str]:
        return frozenset(h.strip().lower() for h in self.ia_hosts_locais.split(",") if h.strip())

    def bloco_caracteres(self) -> int:
        """Tamanho do bloco em caracteres (tokens estimados x caracteres por token)."""
        return max(1, round(self.ia_bloco_tokens * self.ia_caracteres_por_token))

    def sobreposicao_caracteres(self) -> int:
        return round(self.ia_sobreposicao_tokens * self.ia_caracteres_por_token)

    def validar_ia(self) -> None:
        """A extração por IA só aceita servidor local (soberania dos dados, H22)."""
        if not self.ia_extracao_ativa:
            return
        # O bloco, as instruções e a saída precisam caber na janela: o que passa dela é
        # descartado pelo servidor sem aviso.
        if self.ia_bloco_tokens + RESERVA_INSTRUCOES_TOKENS + self.ia_num_predict > self.ia_num_ctx:
            raise ErroConfiguracao(
                "IA_BLOCO_TOKENS + IA_NUM_PREDICT + "
                f"{RESERVA_INSTRUCOES_TOKENS} (instruções) não cabem em IA_NUM_CTX."
            )
        if self.ia_sobreposicao_tokens * 2 > self.ia_bloco_tokens:
            raise ErroConfiguracao(
                "IA_SOBREPOSICAO_TOKENS não pode passar da metade de IA_BLOCO_TOKENS."
            )
        if self.ia_sem_token_segundos > self.ia_bloco_tempo_segundos:
            raise ErroConfiguracao(
                "IA_SEM_TOKEN_SEGUNDOS não pode ser maior que IA_BLOCO_TEMPO_SEGUNDOS."
            )
        if self.ia_provedor not in ("ollama", "falso"):
            raise ErroConfiguracao("IA_PROVEDOR desconhecido (aceitos: ollama, falso).")
        if self.ia_provedor == "falso":
            if not self.modo_dev:
                raise ErroConfiguracao(
                    "IA_PROVEDOR=falso é só para testes e demonstração (exige MODO_DEV=1)."
                )
            return
        if not self.ia_modelo.strip():
            raise ErroConfiguracao("IA_MODELO é obrigatório quando IA_EXTRACAO_ATIVA=1.")
        partes = urlsplit(self.ia_ollama_url)
        host = (partes.hostname or "").lower()
        if partes.scheme not in ("http", "https") or not host:
            raise ErroConfiguracao("IA_OLLAMA_URL deve ser uma URL http(s) com host.")
        if partes.username or partes.password:
            raise ErroConfiguracao("IA_OLLAMA_URL não pode conter usuário nem senha.")
        if host == "localhost" or host in self.hosts_locais_ia():
            return
        try:
            endereco = ipaddress.ip_address(host)
        except ValueError:
            endereco = None
        if endereco is None or not (endereco.is_loopback or endereco.is_private):
            raise ErroConfiguracao(
                "IA_OLLAMA_URL deve apontar para um servidor local (loopback, rede privada ou um "
                "nome listado em IA_HOSTS_LOCAIS): nenhum dado de investigação pode sair do "
                "servidor."
            )

    def validar(self) -> None:
        """Chamada na inicialização: levanta ``ErroConfiguracao`` se algo obrigatório faltar."""
        self.tokens()
        self.chave_auditoria()
        self.cofre()
        self.perfis_com_mfa()
        self.validar_ia()
        self.validar_importacao_judicial()
        for nome, raiz in (
            ("CNPJ_DIR_BASE", self.cnpj_dir_base),
            ("TSE_DIR_BASE", self.tse_dir_base),
            ("EDITAIS_DIR_BASE", self.editais_dir_base),
            ("IMPORTACAO_JUDICIAL_DIR_BASE", self.importacao_judicial_dir_base),
        ):
            if raiz is not None and not Path(raiz).is_dir():
                raise ErroConfiguracao(f"{nome} não aponta para um diretório existente.")
        # Falha fechada também para a configuração das tipologias (limites de dispensa etc.).
        self.parametros_tipologias()

    def limites_pdf(self):
        """Limites da extração de texto de PDF (``buscador.documentos.LimitesPdf``)."""
        from buscador.documentos import LimitesPdf

        return LimitesPdf(
            max_bytes=self.editais_pdf_max_bytes,
            max_paginas=self.editais_pdf_max_paginas,
            max_caracteres=self.editais_pdf_max_caracteres,
            tempo_segundos=self.editais_pdf_tempo_segundos,
        )

    def origens_cors(self) -> list[str]:
        return [o.strip() for o in self.cors_origens.split(",") if o.strip()]

    def parametros_tipologias(self) -> dict:
        """Limiares e configuração das tipologias. Os numéricos simples podem ser sobrescritos por
        execução; os estruturados (limites por ano, listas e expressões) só pela configuração."""
        from buscador.tipologias import combinacao, lavagem, legislacao

        try:
            limites = legislacao.limites_dispensa(self.t5_limites_dispensa)
            criterio = legislacao.validar_criterio_natureza(self.t5_criterio_natureza)
            expressoes = legislacao.expressoes_extras(self.t10_expressoes_extras)
            faixas = lavagem.lista_de_codigos(self.l14_faixas_atipicas, "L14_FAIXAS_ATIPICAS")
            administracao = lavagem.lista_de_codigos(
                self.qualificacoes_administracao, "QUALIFICACOES_ADMINISTRACAO", tamanho=2
            )
            limites_receita = legislacao.limites_de_receita(self.l6_limites_mei)
            naturezas = lavagem.lista_de_codigos(
                self.l16_naturezas_sem_fins_lucrativos, "L16_NATUREZAS_SEM_FINS_LUCRATIVOS"
            )
            pesos = combinacao.pesos(self.comb_pesos)
        except ValueError as erro:
            raise ErroConfiguracao(str(erro)) from erro
        return {
            "l1_minimo_estabelecimentos": self.l1_minimo_estabelecimentos,
            "l1_janela_abertura_dias": self.l1_janela_abertura_dias,
            "l1_minimo_abertas_na_janela": self.l1_minimo_abertas_na_janela,
            "l2_minimo_empresas": self.l2_minimo_empresas,
            "l3_minimo_empresas": self.l3_minimo_empresas,
            "l3_minimo_abertas_na_janela": self.l3_minimo_abertas_na_janela,
            "l3_janela_meses": self.l3_janela_meses,
            "l4_minimo_niveis": self.l4_minimo_niveis,
            "l7_dias_apos_abertura": self.l7_dias_apos_abertura,
            "l7_meses_antes_contrato": self.l7_meses_antes_contrato,
            "l14_valor_minimo": self.l14_valor_minimo,
            "l14_faixas_atipicas": faixas,
            "l15_minimo_cnaes": self.l15_minimo_cnaes,
            "l15_minimo_categorias": self.l15_minimo_categorias,
            "qualificacoes_administracao": administracao,
            "l6_limites": limites_receita,
            "l16_dias_minimos": self.l16_dias_minimos,
            "l16_naturezas_sem_fins_lucrativos": naturezas,
            "comb_minimo_tipologias": self.comb_minimo_tipologias,
            "comb_peso_minimo": self.comb_peso_minimo,
            "comb_pesos": pesos,
            "t1_dias_minimos": self.t1_dias_minimos,
            "t2_minimo_empresas": self.t2_minimo_empresas,
            "t3_razao_minima": self.t3_razao_minima,
            "t5_minimo_contratacoes": self.t5_minimo_contratacoes,
            "t5_criterio_natureza": criterio,
            "t5_limites_dispensa": limites,
            "t8_percentual_limite": self.t8_percentual_limite,
            "t8_percentual_limite_reforma": self.t8_percentual_limite_reforma,
            "t9_situacoes_eleito": [
                s.strip() for s in self.t9_situacoes_eleito.split(",") if s.strip()
            ],
            "t10_expressoes_extras": expressoes,
        }
