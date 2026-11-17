"""Editais em PDF: extração de texto, limites defensivos, custódia sobre os bytes originais,
rota de texto, tipologia T10 e extração por IA (H22) sobre o texto extraído.

Só dados sintéticos: todos os PDFs são gerados aqui, em memória (``pdf_sintetico``), com textos
inventados; nenhum documento real entra no repositório.
"""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfReader, PdfWriter
from pypdf.generic import DictionaryObject, NameObject, TextStringObject

from buscador.api import criar_app
from buscador.conectores import ErroConector
from buscador.conectores.edital_arquivo import ConectorEditalArquivo
from buscador.documentos import (
    SEM_CAMADA_DE_TEXTO,
    DocumentoRecusado,
    LimitesPdf,
    eh_pdf,
    extrair_texto,
)
from buscador.ia import servico as ia
from buscador.ia.falso import ProvedorFalso
from buscador.modelos import Captura
from tests.conftest import cabecalhos, corpo_caso, criar_config

H_ANA = cabecalhos("tok-analista")

CNPJ_FORMATADO = "77.123.456/0001-00"
LINHAS_EDITAL = [
    "EDITAL SINTÉTICO DE PREGÃO ELETRÔNICO Nº 9/2026",
    "1.1. Contratação sintética de serviços de pavimentação.",
    f"2.1. Fica habilitada a empresa CONSTRUTORA SINTÉTICA LTDA, CNPJ {CNPJ_FORMATADO}.",
    "3.1. O valor estimado da contratação é de R$ 1.234.567,89 (valor sintético).",
    "4.1. O licitante deverá apresentar certidão negativa de quitação junto ao conselho.",
]


def _cadeia_pdf(texto: str) -> bytes:
    """Texto como cadeia literal de PDF, na codificação WinAnsi (cp1252), com os escapes."""
    bruto = texto.encode("cp1252")
    return b"(" + bruto.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)") + b")"


def pdf_sintetico(paginas: list[list[str]]) -> bytes:
    """PDF mínimo e válido, escrito à mão: uma página por lista de linhas, fonte Helvetica com
    ``WinAnsiEncoding`` (para os acentos). Página com lista vazia fica SEM camada de texto."""
    objetos: dict[int, bytes] = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        3: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
    }
    filhos = []
    numero = 4
    for linhas in paginas:
        comandos = [b"BT /F1 11 Tf 14 TL 50 780 Td"]
        for linha in linhas:
            comandos.append(_cadeia_pdf(linha) + b" Tj T*")
        comandos.append(b"ET")
        conteudo = b"\n".join(comandos) if linhas else b""
        objetos[numero] = (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
            b"/Resources << /Font << /F1 3 0 R >> >> /Contents %d 0 R >>" % (numero + 1)
        )
        objetos[numero + 1] = (
            b"<< /Length %d >>\nstream\n" % len(conteudo) + conteudo + b"\nendstream"
        )
        filhos.append(b"%d 0 R" % numero)
        numero += 2
    objetos[2] = b"<< /Type /Pages /Kids [" + b" ".join(filhos) + b"] /Count %d >>" % len(filhos)
    saida = io.BytesIO()
    saida.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    posicoes = {}
    for n in sorted(objetos):
        posicoes[n] = saida.tell()
        saida.write(b"%d 0 obj\n" % n + objetos[n] + b"\nendobj\n")
    inicio_xref = saida.tell()
    total = max(objetos) + 1
    saida.write(b"xref\n0 %d\n0000000000 65535 f \n" % total)
    for n in range(1, total):
        saida.write(b"%010d 00000 n \n" % posicoes[n])
    saida.write(
        b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (total, inicio_xref)
    )
    return saida.getvalue()


def pdf_criptografado(conteudo: bytes, senha_usuario: str = "") -> bytes:
    escritor = PdfWriter()
    for pagina in PdfReader(io.BytesIO(conteudo)).pages:
        escritor.add_page(pagina)
    escritor.encrypt(
        user_password=senha_usuario, owner_password="dono-sintetico", algorithm="RC4-128"
    )
    saida = io.BytesIO()
    escritor.write(saida)
    return saida.getvalue()


PDF_EDITAL = pdf_sintetico([LINHAS_EDITAL[:2], LINHAS_EDITAL[2:]])
PDF_ESCANEADO = pdf_sintetico([[], []])
PDF_CORROMPIDO = b"%PDF-1.4\n1 0 obj << /Type /Catalog isto nao fecha\n\x00\x01\x02 lixo sintetico"


# ---- Extração (buscador.documentos) -----------------------------------------------------------


def test_pdf_com_camada_de_texto_vira_texto_com_acentos_e_paginas():
    assert eh_pdf(PDF_EDITAL) and not eh_pdf(b"texto puro")
    documento = extrair_texto(PDF_EDITAL)
    assert (documento.formato, documento.paginas, documento.paginas_com_texto) == ("pdf", 2, 2)
    assert documento.extrator.startswith("pypdf ")
    for linha in LINHAS_EDITAL:
        assert linha in documento.texto
    # As páginas saem na ordem, e o resumo do texto é reproduzível.
    assert documento.texto.index(LINHAS_EDITAL[1]) < documento.texto.index(LINHAS_EDITAL[2])
    assert documento.sha256 == extrair_texto(PDF_EDITAL).sha256
    assert documento.sha256 == hashlib.sha256(documento.texto.encode()).hexdigest()
    # Nada de caracteres de controle além de quebra de linha e tabulação.
    assert all(c in "\n\t" or c.isprintable() for c in documento.texto)


def test_texto_puro_continua_sendo_lido_e_binario_e_recusado():
    documento = extrair_texto("licitação nº 1".encode("latin-1"))
    assert (documento.formato, documento.codificacao, documento.texto) == (
        "texto",
        "latin-1",
        "licitação nº 1",
    )
    with pytest.raises(DocumentoRecusado) as recusa:
        extrair_texto(b"PK\x03\x04\x00\x00binario")
    assert recusa.value.motivo == "binario"


def test_pdf_sem_camada_de_texto_e_relatado_como_tal():
    with pytest.raises(DocumentoRecusado) as recusa:
        extrair_texto(PDF_ESCANEADO)
    assert recusa.value.motivo == SEM_CAMADA_DE_TEXTO
    assert "sem camada de texto" in str(recusa.value) and "OCR" in str(recusa.value)


@pytest.mark.parametrize("senha", ["", "senha-sintetica"])
def test_pdf_criptografado_e_recusado_mesmo_com_senha_de_usuario_vazia(senha):
    cifrado = pdf_criptografado(PDF_EDITAL, senha)
    assert PdfReader(io.BytesIO(cifrado)).is_encrypted
    with pytest.raises(DocumentoRecusado) as recusa:
        extrair_texto(cifrado)
    assert recusa.value.motivo == "criptografado" and "criptografado" in str(recusa.value)


@pytest.mark.parametrize("conteudo", [PDF_CORROMPIDO, b"%PDF-1.7\n", PDF_EDITAL[:300]])
def test_pdf_corrompido_e_recusado_com_motivo(conteudo):
    with pytest.raises(DocumentoRecusado) as recusa:
        extrair_texto(conteudo)
    assert recusa.value.motivo == "corrompido"


def test_limites_de_paginas_tamanho_caracteres_e_tempo():
    tres = pdf_sintetico([["Página um."], ["Página dois."], ["Página três."]])
    assert extrair_texto(tres, LimitesPdf(max_paginas=3)).paginas == 3
    with pytest.raises(DocumentoRecusado) as paginas:
        extrair_texto(tres, LimitesPdf(max_paginas=2))
    assert paginas.value.motivo == "paginas" and "3 páginas" in str(paginas.value)
    with pytest.raises(DocumentoRecusado) as tamanho:
        extrair_texto(tres, LimitesPdf(max_bytes=len(tres) - 1))
    assert tamanho.value.motivo == "tamanho"
    with pytest.raises(DocumentoRecusado) as caracteres:
        extrair_texto(tres, LimitesPdf(max_caracteres=10))
    assert caracteres.value.motivo == "caracteres"
    # Prazo: o processo auxiliar é morto e o documento é recusado (não trava a API).
    with pytest.raises(DocumentoRecusado) as tempo:
        extrair_texto(tres, LimitesPdf(tempo_segundos=0.001))
    assert tempo.value.motivo == "tempo"


def test_javascript_e_anexo_do_pdf_nao_sao_executados_nem_lidos(tmp_path):
    """Um PDF com ação JavaScript de abertura e com arquivo anexo: só o texto das páginas sai.

    O extrator só chama ``extract_text``; a prova é que o texto do script e o conteúdo do anexo
    não aparecem na saída e nenhum efeito colateral acontece."""
    marcador = tmp_path / "executou.txt"
    escritor = PdfWriter()
    for pagina in PdfReader(io.BytesIO(PDF_EDITAL)).pages:
        escritor.add_page(pagina)
    acao = DictionaryObject(
        {
            NameObject("/S"): NameObject("/JavaScript"),
            NameObject("/JS"): TextStringObject(f"app.alert('script sintetico'); /* {marcador} */"),
        }
    )
    escritor.root_object[NameObject("/OpenAction")] = escritor._add_object(acao)
    escritor.add_attachment("anexo-sintetico.txt", b"CONTEUDO DO ANEXO SINTETICO")
    saida = io.BytesIO()
    escritor.write(saida)
    assert b"/JavaScript" in saida.getvalue() and b"/EmbeddedFiles" in saida.getvalue()
    documento = extrair_texto(saida.getvalue())
    assert LINHAS_EDITAL[0] in documento.texto
    assert "script sintetico" not in documento.texto
    assert "CONTEUDO DO ANEXO" not in documento.texto
    assert not marcador.exists()


# ---- Conector ---------------------------------------------------------------------------------


class Registrador:
    def __init__(self) -> None:
        self.capturas: list[Captura] = []

    def __call__(self, fonte, origem, conteudo, metadados) -> Captura:
        captura = Captura(
            id=f"cap{len(self.capturas) + 1}",
            caso_id="c1",
            fonte=fonte,
            url_ou_arquivo=origem,
            sha256=hashlib.sha256(conteudo).hexdigest(),
            metadados=metadados,
            tamanho_bytes=len(conteudo),
        )
        self.capturas.append(captura)
        return captura


def _pasta(tmp_path: Path, arquivos: dict[str, bytes]) -> Path:
    raiz = tmp_path / "editais_base"
    pasta = raiz / "pregao9"
    pasta.mkdir(parents=True)
    (pasta / "SINTETICO.txt").write_text("Arquivos sintéticos. Nenhum dado é real.\n", "utf-8")
    for nome, conteudo in arquivos.items():
        (pasta / nome).write_bytes(conteudo)
    return raiz


def test_conector_guarda_os_bytes_originais_do_pdf_e_descreve_o_texto_derivado(tmp_path):
    raiz = _pasta(
        tmp_path,
        {
            "a_edital.pdf": PDF_EDITAL,
            "b_escaneado.pdf": PDF_ESCANEADO,
            "c_cifrado.pdf": pdf_criptografado(PDF_EDITAL),
            "d_corrompido.pdf": PDF_CORROMPIDO,
            "e_falso.pdf": b"isto nao e um PDF",
            "f_texto.txt": "Edital sintético em texto puro.\n".encode(),
            "g_planilha.xlsx": b"PK sintetico",
        },
    )
    registrador = Registrador()
    resultado = ConectorEditalArquivo(dir_base=raiz).coletar({"diretorio": "pregao9"}, registrador)
    assert [c.metadados["nome_arquivo"] for c in registrador.capturas] == [
        "a_edital.pdf",
        "b_escaneado.pdf",
        "f_texto.txt",
    ]
    pdf, escaneado, texto = registrador.capturas
    # Custódia: o hash é o do arquivo PDF, byte a byte; o texto é derivado e descrito à parte.
    assert pdf.sha256 == hashlib.sha256(PDF_EDITAL).hexdigest()
    assert pdf.tamanho_bytes == len(PDF_EDITAL)
    assert pdf.metadados["formato"] == "pdf" and pdf.metadados["paginas"] == 2
    assert pdf.metadados["extrator"].startswith("pypdf ")
    assert pdf.metadados["texto_sha256"] == extrair_texto(PDF_EDITAL).sha256
    assert pdf.metadados["texto_sha256"] != pdf.sha256
    assert pdf.metadados["simulado"] is True and pdf.url_ou_arquivo.startswith("simulado://")
    # Escaneado: guardado, marcado e avisado como "sem camada de texto".
    assert escaneado.sha256 == hashlib.sha256(PDF_ESCANEADO).hexdigest()
    assert escaneado.metadados["texto_extraivel"] is False
    assert escaneado.metadados["motivo_sem_texto"] == "sem camada de texto"
    assert texto.metadados["formato"] == "texto"
    registros = {r.nome_arquivo: r for r in resultado.registros}
    assert registros["a_edital.pdf"].texto_extraivel and registros["a_edital.pdf"].paginas == 2
    assert not registros["b_escaneado.pdf"].texto_extraivel
    avisos = " | ".join(resultado.avisos)
    assert "b_escaneado.pdf: sem camada de texto" in avisos
    assert "Recusado, c_cifrado.pdf: o PDF é criptografado" in avisos
    assert "Recusado, d_corrompido.pdf: o PDF está corrompido" in avisos
    assert "Recusado, e_falso.pdf: o PDF está corrompido" in avisos
    assert "1 arquivo(s) ignorado(s)" in avisos
    assert str(tmp_path) not in avisos  # o caminho do servidor não vaza


def test_conector_falha_com_os_motivos_quando_nenhum_edital_pode_ser_lido(tmp_path):
    raiz = _pasta(tmp_path, {"cifrado.pdf": pdf_criptografado(PDF_EDITAL, "x")})
    registrador = Registrador()
    with pytest.raises(ErroConector, match="criptografado"):
        ConectorEditalArquivo(dir_base=raiz).coletar({"diretorio": "pregao9"}, registrador)
    assert registrador.capturas == []
    # Acima do limite de páginas e acima do tamanho máximo do arquivo.
    raiz2 = _pasta(tmp_path / "outro", {"longo.pdf": pdf_sintetico([["a"], ["b"], ["c"]])})
    with pytest.raises(ErroConector, match="3 páginas"):
        ConectorEditalArquivo(dir_base=raiz2, limites_pdf=LimitesPdf(max_paginas=2)).coletar(
            {"diretorio": "pregao9"}, Registrador()
        )
    with pytest.raises(ErroConector, match="EDITAIS_PDF_MAX_BYTES"):
        ConectorEditalArquivo(dir_base=raiz2, limites_pdf=LimitesPdf(max_bytes=100)).coletar(
            {"diretorio": "pregao9"}, Registrador()
        )


# ---- API: rota de texto, T10 e extração por IA sobre o PDF ------------------------------------


def _app(tmp_path: Path, arquivos: dict[str, bytes], provedor=None, **extras):
    raiz = _pasta(tmp_path, arquivos)
    return criar_app(criar_config(tmp_path, editais_dir_base=raiz, **extras), provedor_ia=provedor)


def _coletar(cliente) -> tuple[str, dict]:
    caso_id = cliente.post("/casos", json=corpo_caso("Caso PDF"), headers=H_ANA).json()["id"]
    r = cliente.post(
        f"/casos/{caso_id}/coletas",
        json={"fonte": "edital_arquivo", "parametros": {"diretorio": "pregao9"}},
        headers=H_ANA,
    )
    assert r.status_code == 200, r.text
    return caso_id, r.json()


def test_api_rota_de_texto_t10_e_ia_funcionam_sobre_o_texto_extraido_do_pdf(tmp_path):
    app = _app(tmp_path, {"edital9.pdf": PDF_EDITAL}, ProvedorFalso())
    with TestClient(app) as cliente:
        caso_id, coleta = _coletar(cliente)
        captura_id = coleta["captura_ids"][0]
        # Custódia: hash dos bytes do PDF, e a verificação recalcula o mesmo hash.
        captura = cliente.get(f"/capturas/{captura_id}", headers=H_ANA).json()
        assert captura["sha256"] == hashlib.sha256(PDF_EDITAL).hexdigest()
        assert captura["conector_versao"] == "1.1.1" and captura["metadados"]["formato"] == "pdf"
        assert cliente.get(f"/capturas/{captura_id}/verificar", headers=H_ANA).json()["integra"]

        # Rota de texto: devolve o texto extraído e diz de onde ele veio.
        r = cliente.get(f"/casos/{caso_id}/capturas/{captura_id}/texto", headers=H_ANA)
        assert r.status_code == 200, r.text
        texto = r.json()
        assert texto["formato"] == "pdf" and texto["paginas"] == 2
        assert texto["sha256"] == captura["sha256"]  # o do arquivo original
        assert texto["texto_sha256"] == captura["metadados"]["texto_sha256"]
        assert texto["confere_com_coleta"] is True and texto["extrator"].startswith("pypdf ")
        assert all(linha in texto["texto"] for linha in LINHAS_EDITAL)

        # T10: a cláusula é achada no texto extraído; a posição refere-se a esse texto.
        r = cliente.post(
            f"/casos/{caso_id}/alertas/executar", json={"tipologias": ["T10"]}, headers=H_ANA
        )
        assert r.status_code == 200, r.text
        alertas = r.json()
        assert len(alertas) == 1 and "edital9.pdf" in alertas[0]["titulo"]
        ocorrencia = alertas[0]["parametros"]["ocorrencias"][0]
        inicio = ocorrencia["posicao"]
        assert texto["texto"][inicio : inicio + len(ocorrencia["trecho"])] == ocorrencia["trecho"]
        assert ocorrencia["trecho"].lower() == "certidão negativa de quitação"
        assert alertas[0]["evidencias"][0]["sha256"] == captura["sha256"]

        # IA (provedor falso): os trechos e as posições referem-se ao texto extraído.
        r = cliente.post(f"/casos/{caso_id}/capturas/{captura_id}/extracao-ia", headers=H_ANA)
        assert r.status_code == 200, r.text
        extracao = r.json()
        assert extracao["situacao"] == "sucesso" and extracao["itens_aceitos"] == 2
        assert extracao["parametros"]["formato"] == "pdf"
        assert extracao["parametros"]["texto_sha256"] == texto["texto_sha256"]
        assert extracao["parametros"]["texto_confere_com_coleta"] is True
        sugestoes = cliente.get(f"/casos/{caso_id}/sugestoes-ia", headers=H_ANA).json()
        assert {(s["tipo"], s["valor"]) for s in sugestoes} == {
            ("empresa", CNPJ_FORMATADO),
            ("valor", "R$ 1.234.567,89"),
        }
        for s in sugestoes:
            assert texto["texto"][s["posicao_inicio"] : s["posicao_fim"]] == s["trecho"]
            assert s["captura_sha256"] == captura["sha256"]


def test_api_pdf_escaneado_fica_na_custodia_mas_sem_texto(tmp_path):
    app = _app(tmp_path, {"escaneado.pdf": PDF_ESCANEADO}, ProvedorFalso())
    with TestClient(app) as cliente:
        caso_id, coleta = _coletar(cliente)
        assert any("sem camada de texto" in a for a in coleta["avisos"])
        captura_id = coleta["captura_ids"][0]
        assert cliente.get(f"/capturas/{captura_id}/verificar", headers=H_ANA).json()["integra"]
        editais = cliente.get(f"/casos/{caso_id}/entidades?tipo=edital", headers=H_ANA).json()
        assert editais[0]["atributos"]["texto_extraivel"] is False
        r = cliente.get(f"/casos/{caso_id}/capturas/{captura_id}/texto", headers=H_ANA)
        assert r.status_code == 422 and "sem camada de texto" in r.json()["detail"]
        # A T10 não tem o que ler e a IA falha com o motivo, sem criar sugestão.
        r = cliente.post(
            f"/casos/{caso_id}/alertas/executar", json={"tipologias": ["T10"]}, headers=H_ANA
        )
        assert r.status_code == 200 and r.json() == []
        r = cliente.post(f"/casos/{caso_id}/capturas/{captura_id}/extracao-ia", headers=H_ANA)
        assert r.status_code >= 400 and "sem camada de texto" in json.dumps(
            r.json(), ensure_ascii=False
        )
        assert cliente.get(f"/casos/{caso_id}/sugestoes-ia", headers=H_ANA).json() == []


def test_texto_extraido_diferente_do_da_coleta_e_sinalizado(tmp_path):
    """Se a biblioteca passar a extrair outro texto (troca de versão), a leitura avisa: as
    posições gravadas antes referem-se ao texto da coleta (``metadados.texto_sha256``)."""
    app = _app(tmp_path, {"edital9.pdf": PDF_EDITAL})
    with TestClient(app) as cliente:
        _caso_id, coleta = _coletar(cliente)
        with app.state.fabrica_sessoes() as sessao:
            captura = sessao.get(Captura, coleta["captura_ids"][0])
            texto = ia.texto_da_captura(app.state.custodia, captura, 10_000)
            assert texto.confere_com_coleta is True and texto.formato == "pdf"
            captura.metadados = {**captura.metadados, "texto_sha256": "0" * 64}
            assert ia.texto_da_captura(app.state.custodia, captura, 10_000).confere_com_coleta is (
                False
            )
            captura.metadados = {"formato": "pdf"}
            assert ia.texto_da_captura(app.state.custodia, captura, 50).confere_com_coleta is None
            assert ia.texto_da_captura(app.state.custodia, captura, 50).truncado is True
            sessao.rollback()


def test_configuracao_dos_limites_de_pdf(tmp_path):
    config = criar_config(
        tmp_path,
        editais_pdf_max_bytes=2048,
        editais_pdf_max_paginas=7,
        editais_pdf_tempo_segundos=5,
        editais_pdf_max_caracteres=9000,
    )
    assert config.limites_pdf() == LimitesPdf(2048, 7, 9000, 5.0)
    assert json.dumps(LimitesPdf().__dict__)  # limites padrão serializáveis (documentação)
