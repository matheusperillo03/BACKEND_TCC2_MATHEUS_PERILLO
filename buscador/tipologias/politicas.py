"""Tabela única de severidade e de política de erro das tipologias (RF14, H13).

DECISÃO DO AUTOR, a validar com o orientador: os valores abaixo não vêm de norma nem da
literatura. São a proposta do autor para ordenar a fila de alertas do protótipo, e este arquivo é
o único lugar em que eles são definidos (o motor, a API, o relatório e os testes leem daqui).

- ``severidade`` (alta, média ou baixa) ordena a lista de alertas. Ela é da tipologia, não do
  alerta: dois alertas da mesma tipologia têm a mesma severidade, e os valores observados (dias,
  razão, quantidade de empresas) continuam em ``parametros`` de cada alerta.
- ``politica_erro`` diz qual erro a regra prefere evitar: falso negativo (deixar passar um caso
  que merecia análise) ou falso positivo (apontar um caso regular), e por quê. Ela é mostrada ao
  investigador junto de cada alerta, para que ele saiba o que esperar da regra.

Todo alerta é pista, nunca conclusão: a confirmação ou o descarte é do investigador (RF12).
"""

from __future__ import annotations

from dataclasses import dataclass

SEVERIDADES = ("alta", "media", "baixa")
# Posição na ordenação da lista de alertas: alta primeiro.
ORDEM_SEVERIDADE = {nome: posicao for posicao, nome in enumerate(SEVERIDADES)}
ROTULOS_SEVERIDADE = {"alta": "alta", "media": "média", "baixa": "baixa"}

# Grupos de tipologia, na ordem do catálogo e da execução.
GRUPOS = ("contratacao", "lavagem", "combinacao")

# Erro que a regra prefere evitar.
EVITA_FALSO_NEGATIVO = "falso_negativo"
EVITA_FALSO_POSITIVO = "falso_positivo"


@dataclass(frozen=True)
class Politica:
    nome: str
    severidade: str
    prefere_evitar: str
    politica_erro: str
    # "contratacao" (T: possível infração antecedente em contratações), "lavagem" (L: possível
    # ocultação por interposição de pessoas e empresas) ou "combinacao".
    grupo: str = "contratacao"
    # Sinal fraco: sozinho não indica nada; só pesa no alerta de combinação.
    sinal_fraco: bool = False

    def __post_init__(self) -> None:
        if self.severidade not in SEVERIDADES:
            raise ValueError(f"severidade desconhecida: {self.severidade}")
        if self.grupo not in GRUPOS:
            raise ValueError(f"grupo desconhecido: {self.grupo}")
        if self.prefere_evitar not in (EVITA_FALSO_NEGATIVO, EVITA_FALSO_POSITIVO):
            raise ValueError(f"política de erro desconhecida: {self.prefere_evitar}")


POLITICAS: dict[str, Politica] = {
    # T4 (H31): fato objetivo do cadastro oficial e de conferência barata, por isso vem primeiro.
    "T4": Politica(
        nome="Empresa contratada sem situação cadastral ativa no CNPJ",
        severidade="alta",
        prefere_evitar=EVITA_FALSO_NEGATIVO,
        politica_erro=(
            "Prefere evitar falso negativo: dispara para qualquer situação diferente de ATIVA "
            "(nula, suspensa, inapta ou baixada), mesmo quando a mudança de situação é posterior "
            "à assinatura do contrato. O falso positivo esperado vem da defasagem do arquivo do "
            "CNPJ e custa pouco: basta conferir a situação e a data no cadastro."
        ),
    ),
    # T1: empresa nova contratada é comum e lícita; o limiar largo aceita ruído para não perder
    # a empresa aberta para o contrato.
    "T1": Politica(
        nome="Empresa aberta pouco antes do contrato",
        severidade="media",
        prefere_evitar=EVITA_FALSO_NEGATIVO,
        politica_erro=(
            "Prefere evitar falso negativo: o limiar de dias é largo e aponta toda empresa "
            "recente, inclusive as regulares. Espera-se falso positivo (empresa nova que "
            "venceu de forma legítima); cabe ao investigador descartar com justificativa."
        ),
    ),
    # T3: só a desproporção grosseira dispara; desproporções menores ficam de fora de propósito.
    "T3": Politica(
        nome="Capital social incompatível com o valor contratado",
        severidade="media",
        prefere_evitar=EVITA_FALSO_POSITIVO,
        politica_erro=(
            "Prefere evitar falso positivo: só dispara quando o contrato vale muitas vezes o "
            "capital social (limiar configurável), porque capital baixo é comum em empresas "
            "regulares. Aceita falso negativo: desproporções menores não são apontadas."
        ),
    ),
    # T2: endereço compartilhado tem muitas explicações lícitas (escritório de contabilidade,
    # coworking, mesmo prédio), por isso fica no fim da fila.
    "T2": Politica(
        nome="Endereço compartilhado por empresas contratadas",
        severidade="baixa",
        prefere_evitar=EVITA_FALSO_NEGATIVO,
        politica_erro=(
            "Prefere evitar falso negativo: aponta todo endereço com várias empresas em que ao "
            "menos uma é contratada. O falso positivo é frequente (escritório de contabilidade, "
            "coworking, endereço fiscal comum), por isso a severidade é baixa e o alerta serve "
            "para orientar a análise de vínculos, não para concluir."
        ),
    ),
    # ---- Horizonte 2 (T5 a T10): também DECISÃO DO AUTOR, a validar com o orientador ----------
    # T6 (H33): registro oficial de sanção com datas; conferência barata, por isso severidade alta.
    "T6": Politica(
        nome="Empresa contratada com sanção vigente (CEIS ou CNEP)",
        severidade="alta",
        prefere_evitar=EVITA_FALSO_NEGATIVO,
        politica_erro=(
            "Prefere evitar falso negativo: dispara para qualquer registro do CEIS ou do CNEP "
            "cujo período cobre a data de assinatura, sem avaliar o alcance da sanção (órgão, "
            "esfera ou tipo). O falso positivo esperado é a sanção que não impede contratar com "
            "aquele órgão; confere-se no próprio registro."
        ),
    ),
    # T7 (H34): conflito de interesse é grave, mas a identidade não é confirmável com dados abertos.
    "T7": Politica(
        nome="Possível coincidência entre sócio da contratada e servidor do órgão contratante",
        severidade="alta",
        prefere_evitar=EVITA_FALSO_POSITIVO,
        politica_erro=(
            "Prefere evitar falso positivo: só dispara quando coincidem os seis dígitos visíveis "
            "do CPF descaracterizado E o nome normalizado; só o nome nunca dispara (vira apenas "
            "sugestão de resolução). Ainda assim é possível coincidência, não identificação: "
            "homônimo com os mesmos seis dígitos é possível. Aceita falso negativo: grafia "
            "diferente do nome ou CPF ausente na fonte não são apontados."
        ),
    ),
    # T8 (H35): limite legal objetivo, mas o campo do PNCP não distingue acréscimo de supressão.
    "T8": Politica(
        nome="Aditivos acima do limite do art. 125 da Lei nº 14.133/2021",
        severidade="media",
        prefere_evitar=EVITA_FALSO_NEGATIVO,
        politica_erro=(
            "Prefere evitar falso negativo: soma todo valor acrescido positivo dos termos e "
            "compara com o valor inicial sem atualização monetária. Espera-se falso positivo "
            "quando o termo é supressão (o PNCP usa o mesmo campo), quando o valor inicial foi "
            "atualizado ou quando a reforma de edifício ou de equipamento não aparece no objeto."
        ),
    ),
    # T5 (H32): o critério de "mesma natureza" é uma aproximação; o padrão é o mais restrito.
    "T5": Politica(
        nome="Possível fracionamento de despesa (art. 75, § 1º, da Lei nº 14.133/2021)",
        severidade="media",
        prefere_evitar=EVITA_FALSO_POSITIVO,
        politica_erro=(
            "Prefere evitar falso positivo: por padrão só soma contratações do mesmo fornecedor "
            "e da mesma categoria, na mesma unidade e no mesmo exercício, cada uma abaixo do "
            "limite de dispensa. Aceita falso negativo: o fracionamento entre fornecedores "
            "diferentes só aparece com o critério 'categoria'. O alerta é indício: o contrato no "
            "PNCP não diz se houve dispensa."
        ),
    ),
    # T9 (H39): doação registrada é lícita; o vínculo com o ente é territorial.
    "T9": Politica(
        nome="Doador de campanha contratado pelo ente do candidato eleito",
        severidade="media",
        prefere_evitar=EVITA_FALSO_POSITIVO,
        politica_erro=(
            "Prefere evitar falso positivo: exige candidato eleito, órgão do mesmo ente e "
            "contrato assinado depois da doação; para doador pessoa física, exige ainda os seis "
            "dígitos visíveis do CPF e o nome iguais aos de um sócio (possível coincidência, "
            "nunca identificação). Aceita falso negativo: doações de 'recursos próprios' e "
            "doadores sem vínculo societário coletado ficam de fora."
        ),
    ),
    # T10 (H36): busca textual; muitos contextos lícitos para a mesma expressão.
    "T10": Politica(
        nome="Cláusula potencialmente restritiva em edital",
        severidade="baixa",
        prefere_evitar=EVITA_FALSO_NEGATIVO,
        politica_erro=(
            "Prefere evitar falso negativo: aponta toda ocorrência textual das expressões "
            "configuradas, mesmo fora do capítulo de habilitação. O falso positivo é frequente "
            "(a expressão citada em outro contexto), por isso a severidade é baixa; cada alerta "
            "traz o trecho literal e a posição para conferência imediata."
        ),
    ),
    # ---- Tipologias de lavagem e ocultação (L): DECISÃO DO AUTOR, a validar com o orientador ---
    # Critérios e fontes de cada uma em ``tipologias.lavagem`` (docs/lavagem/tipologias-lavagem.md
    # na monografia). Todas são indício cadastral: nenhuma prova lavagem (RF12).
    "L1": Politica(
        nome="Endereço de registro em massa",
        severidade="baixa",
        prefere_evitar=EVITA_FALSO_NEGATIVO,
        grupo="lavagem",
        politica_erro=(
            "Prefere evitar falso negativo: aponta todo endereço (com complemento) em que há "
            "muitas empresas ativas, mesmo sem contrato público. O falso positivo é alto: centro "
            "empresarial, escritório de contabilidade usado como domicílio fiscal, coworking e "
            "MEI registrado na residência. O dado aberto não diz se o endereço é residencial ou "
            "comercial."
        ),
    ),
    "L3": Politica(
        nome="Mesmo administrador ou representante em muitas empresas",
        severidade="media",
        prefere_evitar=EVITA_FALSO_POSITIVO,
        grupo="lavagem",
        politica_erro=(
            "Prefere evitar falso positivo: exige os seis dígitos visíveis do CPF E o nome "
            "normalizado iguais, qualificação de administração e várias empresas abertas em pouco "
            "tempo. Ainda assim é pessoa PROVÁVEL, não identificada: homônimo com os mesmos seis "
            "dígitos é possível. Falso positivo esperado: empresário com várias empresas e holding "
            "familiar. Aceita falso negativo: sócio sem CPF na fonte não é contado."
        ),
    ),
    "L4": Politica(
        nome="Cadeia societária em camadas",
        severidade="media",
        prefere_evitar=EVITA_FALSO_POSITIVO,
        grupo="lavagem",
        politica_erro=(
            "Prefere evitar falso positivo: só dispara quando não há pessoa física no primeiro "
            "nível e o controle passa por um número mínimo de pessoas jurídicas. Falso positivo "
            "esperado: grupo econômico legítimo, fundo e companhia aberta. Aceita falso negativo: "
            "a cadeia só é vista até onde o quadro societário foi coletado no caso."
        ),
    ),
    "L5": Politica(
        nome="Sócio estrangeiro (nível 1)",
        severidade="baixa",
        prefere_evitar=EVITA_FALSO_NEGATIVO,
        grupo="lavagem",
        politica_erro=(
            "Prefere evitar falso negativo: aponta toda empresa do caso com sócio estrangeiro, "
            "sem avaliar o país. O falso positivo é alto, porque o investimento estrangeiro é "
            "legítimo. O nível 2 (lista de jurisdições) não foi implementado: depende da lista "
            "oficial, ainda não conferida."
        ),
    ),
    "L6": Politica(
        nome="Porte ou regime incompatível com o valor contratado",
        severidade="media",
        prefere_evitar=EVITA_FALSO_POSITIVO,
        grupo="lavagem",
        politica_erro=(
            "Prefere evitar falso positivo: compara o valor contratado no ano com o limite LEGAL "
            "de receita do regime (MEI, microempresa ou pequeno porte), usa o valor inicial do "
            "contrato, só conta como MEI os contratos assinados enquanto a opção valia e aplica "
            "a tolerância da transição de limite. Ainda há falso positivo: contrato plurianual "
            "não é receita de um ano, e o porte do cadastro é o atual. Aceita falso negativo: "
            "empresa sem a tabela Simples coletada só é avaliada pelo porte."
        ),
    ),
    "L7": Politica(
        nome="Fundadores substituídos logo após a abertura",
        severidade="media",
        prefere_evitar=EVITA_FALSO_POSITIVO,
        grupo="lavagem",
        politica_erro=(
            "Prefere evitar falso positivo: exige que nenhum sócio atual tenha entrado até poucos "
            "dias depois do início da atividade, que todos tenham data de entrada e que a entrada "
            "mais recente seja pouco anterior a um contrato. Falso positivo esperado: venda "
            "legítima da empresa e sucessão familiar. Aceita falso negativo: o dado aberto é o "
            "quadro atual, sem histórico de quem saiu."
        ),
    ),
    "L8": Politica(
        nome="Empresa recém-aberta com sócio que possivelmente é agente público",
        severidade="alta",
        prefere_evitar=EVITA_FALSO_POSITIVO,
        grupo="lavagem",
        politica_erro=(
            "Prefere evitar falso positivo: só dispara quando a mesma contratada dispara a T1 "
            "(aberta pouco antes do contrato) E a T7 (sócio com os mesmos seis dígitos do CPF e o "
            "mesmo nome de um servidor do órgão contratante). Herda os homônimos da T7: é possível "
            "coincidência, não identificação. A parte 'sem empregados' do sinal do COAF não é "
            "verificável com dado aberto."
        ),
    ),
    "L10": Politica(
        nome="Vínculo com empresa sancionada ou com pessoa expulsa da administração",
        severidade="media",
        prefere_evitar=EVITA_FALSO_NEGATIVO,
        grupo="lavagem",
        politica_erro=(
            "Prefere evitar falso negativo: aponta a contratada sem sanção própria que divide "
            "sócio-administrador provável ou endereço com empresa de sanção vigente na data do "
            "contrato (CEIS e CNEP) ou com entidade impedida (CEPIM, que não traz período), e o "
            "sócio que possivelmente coincide com pessoa expulsa (CEAF). Falso positivo "
            "esperado: endereço compartilhado legítimo, sócio que saiu antes da sanção e homônimo."
        ),
    ),
    "L16": Politica(
        nome="Entidade sem fins lucrativos convenente com sinais de fachada",
        severidade="media",
        prefere_evitar=EVITA_FALSO_NEGATIVO,
        grupo="lavagem",
        politica_erro=(
            "Prefere evitar falso negativo: basta um dos sinais (aberta pouco antes da vigência, "
            "registro no CEPIM, dirigente que possivelmente coincide com servidor do concedente, "
            "ou dirigente sócio de empresa no mesmo endereço, do mesmo ramo ou recém-aberta). "
            "Falso positivo esperado: entidade regular nova e dirigente com atuação pública "
            "legítima. O cruzamento de pessoas é por seis dígitos do CPF e nome, sem fusão."
        ),
    ),
    "L14": Politica(
        nome="Sócio em faixa etária atípica em empresa contratada",
        severidade="baixa",
        prefere_evitar=EVITA_FALSO_POSITIVO,
        grupo="lavagem",
        sinal_fraco=True,
        politica_erro=(
            "Prefere evitar falso positivo: é sinal fraco, que sozinho não indica nada e só pesa "
            "no alerta de combinação. Só dispara quando TODOS os sócios-administradores estão em "
            "faixa etária atípica e o valor contratado passa do mínimo. Falso positivo esperado: "
            "herança, sucessão e empresa familiar. Só a faixa etária é mostrada, nunca a idade."
        ),
    ),
    "L15": Politica(
        nome="Número excessivo de atividades econômicas",
        severidade="baixa",
        prefere_evitar=EVITA_FALSO_NEGATIVO,
        grupo="lavagem",
        sinal_fraco=True,
        politica_erro=(
            "Prefere evitar falso negativo: é sinal fraco, que sozinho não indica nada e só pesa "
            "no alerta de combinação. O falso positivo é alto: muitas empresas registram "
            "atividades a mais por precaução."
        ),
    ),
    # ---- Combinação: DECISÃO DO AUTOR (pesos e mínimos em ``tipologias.combinacao``) -----------
    "COMB": Politica(
        nome="Combinação de alertas na mesma entidade",
        severidade="alta",
        prefere_evitar=EVITA_FALSO_POSITIVO,
        grupo="combinacao",
        politica_erro=(
            "Prefere evitar falso positivo: exige um número mínimo de tipologias distintas e de "
            "soma de pesos na mesma empresa ou pessoa, conta uma vez as tipologias que descrevem "
            "o mesmo fato, não soma de novo as composições e ignora os alertas descartados. Não é "
            "escore de culpa: herda as limitações de cada alerta da lista e serve só para "
            "priorizar a análise."
        ),
    ),
}


def politica(tipologia: str) -> Politica | None:
    return POLITICAS.get(tipologia)


def severidade(tipologia: str) -> str:
    return POLITICAS[tipologia].severidade


def chave_ordenacao(severidade_: str, tipologia: str, titulo: str) -> tuple[int, str, str]:
    """Ordem da lista de alertas: severidade (alta primeiro), tipologia e título."""
    return (ORDEM_SEVERIDADE.get(severidade_, len(SEVERIDADES)), tipologia, titulo)
