"""Conector de editais lidos de arquivos locais (H36, tipologia T10; H22, extração por IA).

Lê arquivos de texto (``.txt``) e PDF com camada de texto (``.pdf``) de uma pasta dentro da raiz
de ingestão ``EDITAIS_DIR_BASE``. Cada arquivo vira uma captura (RF07).

Custódia e texto derivado: a captura guarda os BYTES ORIGINAIS do arquivo (o PDF como veio) e o
SHA-256 registrado é o desses bytes. O texto do PDF é derivado: a T10, a rota de texto e a
extração por IA (H22) releem a captura e extraem o texto de novo (``buscador/documentos.py``).
As posições de um trecho (linha, coluna e posição na T10; ``posicao_inicio``/``posicao_fim`` na
IA) referem-se a esse TEXTO EXTRAÍDO, não a deslocamentos dentro do PDF. Para a referência ser
reproduzível, os metadados da captura guardam ``extrator`` (biblioteca e versão), ``paginas`` e
``texto_sha256`` (SHA-256 do texto extraído na coleta, em UTF-8).

PDF, limites defensivos (``LimitesPdf``): tamanho do arquivo (``EDITAIS_PDF_MAX_BYTES``), número
de páginas (``EDITAIS_PDF_MAX_PAGINAS``), total de caracteres e tempo de extração
(``EDITAIS_PDF_TEMPO_SEGUNDOS``; a extração roda num processo separado, morto no prazo). O tipo é
decidido pela assinatura ``%PDF-`` do conteúdo, não só pela extensão. A biblioteca (``pypdf``,
BSD-3-Clause) só lê o texto das páginas: não executa JavaScript nem abre anexos do PDF.

- PDF criptografado, corrompido ou acima dos limites: RECUSADO com o motivo num aviso; não vira
  captura. Se nenhum arquivo da pasta puder ser lido, a coleta falha com os motivos.
- PDF sem camada de texto (escaneado): a captura é guardada (o documento pertence ao caso), o
  edital é registrado com ``texto_extraivel = false`` e um aviso diz "sem camada de texto". OCR
  está fora do escopo; a T10 e a IA não têm o que ler nesse edital.

Sobre o PNCP: a "API PNCP" (integração) tem ``GET /v1/orgaos/{cnpj}/compras/{ano}/{sequencial}/
arquivos`` ("Consultar Documentos de Contratação") e ``.../arquivos/{sequencialDocumento}``
("Baixar Arquivo/Documento de Contratação"), ambos sem ``security`` declarada (OpenAPI:
https://pncp.gov.br/api/pncp/v3/api-docs, conferida em 16 e 17/11/2026) e, no teste de
17/11/2026, respondendo sem autenticação. O download automático pelo conector NÃO foi
implementado: o investigador baixa o edital (ou usa ``scripts/testar_fontes_reais.py --edital``)
e o coloca na pasta; o parâmetro ``numero_controle_pncp_compra`` registra a que contratação o
documento se refere.

Codificação do ``.txt``: UTF-8 e, se falhar, Latin-1 (fica nos metadados da captura).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

from buscador.conectores import arquivos
from buscador.conectores.base import (
    Conector,
    ErroConector,
    RegistradorCaptura,
    RegistroEdital,
    ResultadoColeta,
    Retentativa,
)
from buscador.conectores.parametros import ParametrosEditalArquivo, validar
from buscador.documentos import (
    SEM_CAMADA_DE_TEXTO,
    DocumentoRecusado,
    LimitesPdf,
    eh_pdf,
    extrair_texto,
)
from buscador.resolucao.normalizacao import somente_alfanumericos

VARIAVEL_RAIZ = "EDITAIS_DIR_BASE"
EXTENSOES = (".txt", ".pdf")


class ConectorEditalArquivo(Conector):
    nome = "edital_arquivo"
    # 1.1.0: lê também PDF com camada de texto (custódia sobre os bytes originais do PDF).
    # 1.1.1: aceita o CNPJ alfanumérico (letras de A a Z não são mais descartadas) em ``cnpjOrgao``.
    versao = "1.1.1"

    def __init__(
        self,
        dir_base: Path | str | None = None,
        max_bytes_arquivo: int = 4 * 1024 * 1024,
        max_arquivos: int = 50,
        simulado: bool = False,
        tentativas: int = 3,
        espera_retentativa_segundos: float = 1.0,
        dormir: Callable[[float], None] = time.sleep,
        limites_pdf: LimitesPdf | None = None,
    ) -> None:
        super().__init__(Retentativa(tentativas, espera_retentativa_segundos, dormir))
        self.dir_base = Path(dir_base) if dir_base is not None else None
        self.max_bytes_arquivo = max_bytes_arquivo
        self.max_arquivos = max_arquivos
        self.simulado = simulado
        self.limites_pdf = limites_pdf or LimitesPdf()

    def coletar(self, parametros: dict, registrar_captura: RegistradorCaptura) -> ResultadoColeta:
        validos = validar(ParametrosEditalArquivo, parametros)
        raiz, pasta = arquivos.resolver_pasta(self.dir_base, validos.diretorio, VARIAVEL_RAIZ)
        resultado = ResultadoColeta(fonte=self.nome)
        nomes, sintetico = arquivos.listar_arquivos(pasta, resultado.avisos)
        simulado = self.simulado or sintetico
        documentos = [n for n in nomes if n.lower().endswith(EXTENSOES)]
        outros = [n for n in nomes if not n.lower().endswith(EXTENSOES)]
        if outros:
            resultado.avisos.append(
                f"{len(outros)} arquivo(s) ignorado(s) por não serem .txt nem .pdf."
            )
        if not documentos:
            raise ErroConector("Nenhum arquivo .txt ou .pdf de edital no diretório informado.")
        if len(documentos) > self.max_arquivos:
            raise ErroConector(
                f"A pasta tem {len(documentos)} arquivos de edital e passa do teto de "
                f"{self.max_arquivos} por coleta (EDITAIS_MAX_ARQUIVOS)."
            )
        recusados: list[str] = []
        for nome in documentos:
            pdf = nome.lower().endswith(".pdf")
            conteudo = arquivos.ler_arquivo(
                pasta / nome,
                self.limites_pdf.max_bytes if pdf else self.max_bytes_arquivo,
                "EDITAIS_PDF_MAX_BYTES" if pdf else "EDITAIS_MAX_BYTES_ARQUIVO",
                self.retentativa,
            )
            texto_extraivel = True
            try:
                if pdf and not eh_pdf(conteudo):
                    raise DocumentoRecusado("corrompido")
                documento = extrair_texto(conteudo, self.limites_pdf)
                metadados = {
                    "nome_arquivo": nome,
                    "formato": documento.formato,
                    "codificacao_detectada": documento.codificacao,
                    "caracteres": len(documento.texto),
                    "texto_extraivel": True,
                }
                if documento.formato == "pdf":
                    metadados.update(
                        paginas=documento.paginas,
                        paginas_com_texto=documento.paginas_com_texto,
                        extrator=documento.extrator,
                        # Resumo do texto extraído: é a ele que as posições de trecho se referem.
                        texto_sha256=documento.sha256,
                    )
                    sem_texto = (documento.paginas or 0) - (documento.paginas_com_texto or 0)
                    if sem_texto > 0:
                        resultado.avisos.append(
                            f"{nome}: {sem_texto} de {documento.paginas} página(s) sem camada "
                            "de texto (imagem); o "
                            "texto dessas páginas não foi extraído (OCR fora do escopo)."
                        )
            except DocumentoRecusado as recusa:
                if recusa.motivo != SEM_CAMADA_DE_TEXTO:
                    recusados.append(f"{nome}: {recusa}")
                    continue
                # Escaneado: o arquivo fica na custódia, mas não há texto para analisar.
                texto_extraivel = False
                metadados = {
                    "nome_arquivo": nome,
                    "formato": "pdf",
                    "codificacao_detectada": "pdf",
                    "caracteres": 0,
                    "texto_extraivel": False,
                    "motivo_sem_texto": "sem camada de texto",
                    "paginas": int(recusa.detalhe) if recusa.detalhe.isdigit() else None,
                }
                resultado.avisos.append(
                    f"{nome}: sem camada de texto (PDF provavelmente escaneado). O arquivo foi "
                    "guardado na custódia, mas a T10 e a extração por IA não têm texto para ler; "
                    "OCR está fora do escopo."
                )
            metadados["numero_controle_pncp_compra"] = validos.numero_controle_pncp_compra or ""
            metadados["simulado"] = simulado
            if simulado:
                metadados["origem_simulada"] = (
                    "arquivo sintético (fixture de teste ou de demonstração); não é um edital real"
                )
            captura = registrar_captura(
                self.nome,
                arquivos.origem_do_arquivo(self.nome, VARIAVEL_RAIZ, raiz, pasta, nome, simulado),
                conteudo,
                metadados,
            )
            resultado.capturas.append(captura)
            resultado.registros.append(
                RegistroEdital(
                    captura_id=captura.id,
                    nome_arquivo=nome,
                    codificacao=metadados["codificacao_detectada"],
                    caracteres=metadados["caracteres"],
                    numero_controle_pncp_compra=validos.numero_controle_pncp_compra or "",
                    cnpj_orgao=somente_alfanumericos(validos.cnpjOrgao),
                    formato=metadados["formato"],
                    paginas=metadados.get("paginas"),
                    texto_extraivel=texto_extraivel,
                )
            )
        if recusados:
            resultado.avisos.extend(f"Recusado, {r}." for r in recusados)
        if not resultado.registros:
            raise ErroConector(
                "Nenhum edital pôde ser lido. " + " ".join(f"{r}." for r in recusados)
            )
        return resultado
