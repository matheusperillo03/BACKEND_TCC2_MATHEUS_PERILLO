"""Script de demonstração da extração em segundo plano com um documento longo sintético."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from buscador.ia.falso import ProvedorFalso


def test_script_do_documento_longo_passa_pelo_caminho_inteiro_e_resume_so_numeros(tmp_path, capsys):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    try:
        import extracao_longa_sintetica as roteiro
    finally:
        sys.path.pop(0)
    paginas = [roteiro.pagina_sintetica(n) for n in (1, 2)]
    assert all(len(p) == roteiro.LINHAS_POR_PAGINA for p in paginas)
    assert roteiro.pdf_sintetico(paginas).startswith(b"%PDF-1.4")
    saida = tmp_path / "resumo.json"
    codigo = roteiro.principal(
        ["--modelo", "x", "--paginas", "4", "--intervalo", "0.01", "--json", str(saida)],
        provedor=ProvedorFalso(),
    )
    resumo = json.loads(saida.read_text("utf-8"))
    assert codigo == 0 and resumo["situacao"] == "sucesso" and resumo["paginas"] == 4
    assert resumo["blocos_total"] == resumo["blocos_concluidos"] > 1
    assert resumo["blocos_falhos"] == [] and resumo["itens_aceitos"] > 0
    assert resumo["auditoria_integra"] is True
    # O resumo e a saída só têm números e códigos: nada do texto do documento.
    impresso = capsys.readouterr().out
    assert "Pedido: HTTP 202" in impresso
    assert "SINTÉTIC" not in impresso and "SINTÉTIC" not in saida.read_text("utf-8")
    # Só servidor desta máquina.
    with pytest.raises(SystemExit):
        roteiro.principal(["--modelo", "x", "--url", "http://10.0.0.8:11434"])
