"""Extração de editais em segundo plano: fila, blocos, limites, retomada e cancelamento.

Só dados sintéticos e nenhum modelo: o provedor é o falso (com um roteiro por teste) ou o do
Ollama falando com um servidor HTTP de mentira aberto em 127.0.0.1 pelo próprio teste.
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from buscador import auditoria
from buscador.api import criar_app
from buscador.db import conceder_privilegios, criar_engine, criar_esquema
from buscador.ia import servico as ia
from buscador.ia import trabalhador as modulo_trabalhador
from buscador.ia.falso import ProvedorFalso
from buscador.ia.ollama import ProvedorOllama
from buscador.ia.provedor import Controle, ErroProvedor, Geracao, GeracaoInterrompida
from buscador.ia.trabalhador import Trabalhador
from buscador.modelos import Auditoria, ExtracaoIA, ExtracaoIABloco, SugestaoIA
from tests.conftest import (
    CHAVE,
    cabecalhos,
    corpo_caso,
    criar_config,
    esperar_extracao,
    extrair_e_esperar,
)

H_ANA = cabecalhos("tok-analista")
H_ROOT = cabecalhos("tok-admin")
LINHAS_POR_DOCUMENTO = 12


def _linha(numero: int) -> str:
    """Uma linha sintética com um CNPJ e um valor (os dois que o provedor falso extrai)."""
    return (
        f"{numero}.1. Fica habilitada a EMPRESA SINTETICA {numero:02d} LTDA, CNPJ "
        f"77.{numero:03d}.456/0001-{numero:02d}, pelo valor de R$ {numero}.000,00."
    )


def _documento(linhas: int = LINHAS_POR_DOCUMENTO) -> str:
    return "\n".join(
        ["EDITAL SINTETICO LONGO (nenhum dado real)", *map(_linha, range(1, linhas + 1))]
    )


DOCUMENTO = _documento()


def _app(tmp_path: Path, provedor, documentos: int = 1, **config_extra):
    """Aplicação com ``documentos`` editais sintéticos; blocos de 300 caracteres (100 tokens
    estimados x 3), sem sobreposição, a não ser que o teste peça outra coisa."""
    raiz = tmp_path / "editais_base"
    pasta = raiz / "longos"
    if not pasta.exists():
        pasta.mkdir(parents=True)
        (pasta / "SINTETICO.txt").write_text("Arquivos sintéticos. Nenhum dado é real.\n", "utf-8")
        for n in range(1, documentos + 1):
            (pasta / f"edital{n}.txt").write_text(DOCUMENTO, encoding="utf-8")
    valores = {"ia_bloco_tokens": 100, "ia_sobreposicao_tokens": 0, **config_extra}
    return criar_app(criar_config(tmp_path, editais_dir_base=raiz, **valores), provedor_ia=provedor)


def _caso(cliente) -> tuple[str, list[str]]:
    caso_id = cliente.post("/casos", json=corpo_caso("Caso longo"), headers=H_ANA).json()["id"]
    r = cliente.post(
        f"/casos/{caso_id}/coletas",
        json={"fonte": "edital_arquivo", "parametros": {"diretorio": "longos"}},
        headers=H_ANA,
    )
    assert r.status_code == 200, r.text
    return caso_id, r.json()["captura_ids"]


def _pedir(cliente, caso_id, captura_id, h=H_ANA):
    return cliente.post(f"/casos/{caso_id}/capturas/{captura_id}/extracao-ia", headers=h)


def _progresso(cliente, caso_id, extracao_id) -> dict:
    r = cliente.get(f"/casos/{caso_id}/extracoes-ia/{extracao_id}/progresso", headers=H_ANA)
    assert r.status_code == 200, r.text
    return r.json()


def _extracao(cliente, caso_id, extracao_id) -> dict:
    return cliente.get(f"/casos/{caso_id}/extracoes-ia/{extracao_id}", headers=H_ANA).json()


def _acoes_ia(app) -> list[tuple[str, str]]:
    with app.state.fabrica_sessoes() as s:
        linhas = s.execute(
            select(Auditoria.acao, Auditoria.usuario)
            .where(Auditoria.acao.like("ia.extra%"))
            .order_by(Auditoria.seq)
        )
        return [(acao, usuario) for acao, usuario in linhas]


def _cadeia_integra(app) -> bool:
    with app.state.fabrica_sessoes() as s:
        return auditoria.verificar(s, CHAVE, app.state.ancora).integra


class ProvedorRoteirizado(ProvedorFalso):
    """Provedor falso com um roteiro: ``acao(texto, chamada, controle)`` pode devolver uma
    ``Geracao``, levantar um erro ou devolver ``None`` (aí vale a resposta do provedor falso).
    Conta as chamadas e quantas rodaram ao mesmo tempo."""

    def __init__(self, acao=None, **opcoes) -> None:
        super().__init__(**opcoes)
        self.acao = acao
        self.chamadas: list[str] = []
        self.simultaneas = 0
        self.max_simultaneas = 0
        self._trava = threading.Lock()

    def gerar(self, instrucoes, texto, esquema, controle=None):
        with self._trava:
            self.simultaneas += 1
            self.max_simultaneas = max(self.max_simultaneas, self.simultaneas)
            self.chamadas.append(texto)
            chamada = len(self.chamadas)
        try:
            resultado = self.acao(texto, chamada, controle) if self.acao else None
            if resultado is not None:
                return resultado
            return super().gerar(instrucoes, texto, esquema, controle)
        finally:
            with self._trava:
                self.simultaneas -= 1


def _segurar_ate(liberar: threading.Event, na_chamada: int = 1):
    """Roteiro que prende a geração ``na_chamada`` até ``liberar``, atendendo à interrupção."""

    def acao(_texto, chamada, controle):
        if chamada == na_chamada:
            while not liberar.wait(0.005):
                if controle is not None:
                    controle.conferir_interrupcao()
        return None

    return acao


# ---- Divisão em blocos ---------------------------------------------------------------------------


def test_blocos_com_sobreposicao_cobrem_o_texto_e_repetem_a_linha_da_borda():
    texto = DOCUMENTO
    sem = ia.dividir_em_intervalos(texto, 300)
    assert "".join(texto[i:f] for i, f in sem) == texto
    assert all(f - i <= 300 for i, f in sem) and all(texto[f - 1] == "\n" for _, f in sem[:-1])
    com = ia.dividir_em_intervalos(texto, 300, 60)
    assert com[0][0] == 0 and com[-1][1] == len(texto)
    for (inicio_a, fim_a), (inicio_b, _fim_b) in zip(com, com[1:], strict=False):
        # Cada bloco começa antes do fim do anterior, no começo de uma linha, e sempre avança.
        assert inicio_a < inicio_b < fim_a and texto[inicio_b - 1] == "\n"
        assert fim_a - inicio_b <= 120
    assert all(f - i <= 300 for i, f in com)
    # A linha da borda aparece inteira nos dois blocos vizinhos.
    assert all(
        texto[b[0] : a[1]].strip() in DOCUMENTO.splitlines()
        for a, b in zip(com, com[1:], strict=False)
    )
    # Sem quebra de linha o corte é no tamanho, e a sobreposição nunca trava o avanço.
    corrido = "x" * 1000
    assert ia.dividir_em_intervalos(corrido, 300, 150) == [
        (0, 300),
        (150, 450),
        (300, 600),
        (450, 750),
        (600, 900),
        (750, 1000),
    ]
    assert ia.dividir_em_intervalos("   \n\n", 10) == []
    assert ia.dividir_em_blocos(texto, 300, 60) == [texto[i:f] for i, f in com]


def test_ponto_de_corte_prefere_o_fim_de_linha_mais_perto_do_meio():
    texto = "a" * 40 + "\n" + "b" * 100 + "\n" + "c" * 20
    assert ia.ponto_de_corte(texto, 0, len(texto)) == 41
    assert ia.ponto_de_corte("x" * 100, 0, 100) == 50
    assert ia.ponto_de_corte("x\n" + "y" * 100, 0, 102) == 2


# ---- Sucesso, andamento e auditoria --------------------------------------------------------------


def test_pedido_responde_202_e_o_documento_longo_conclui_bloco_a_bloco(tmp_path):
    liberar = threading.Event()
    provedor = ProvedorRoteirizado(_segurar_ate(liberar, na_chamada=3))
    app = _app(tmp_path, provedor)
    with TestClient(app) as cliente:
        caso_id, (captura_id,) = _caso(cliente)
        r = _pedir(cliente, caso_id, captura_id)
        assert r.status_code == 202, r.text
        criada = r.json()
        assert criada["situacao"] in ("na_fila", "em_andamento") and criada["progresso"]["ativa"]
        extracao_id = criada["id"]
        # O terceiro bloco está preso: os dois primeiros já estão gravados e visíveis.
        parcial = esperar_extracao(
            cliente, caso_id, extracao_id, ate=lambda p: p["bloco_atual"] == 3
        )
        assert parcial["situacao"] == "em_andamento" and parcial["ativa"]
        assert (parcial["blocos_concluidos"], parcial["blocos_falhos"]) == (2, 0)
        assert parcial["blocos_total"] == len(ia.dividir_em_intervalos(DOCUMENTO, 300)) > 3
        assert parcial["blocos_pendentes"] == parcial["blocos_total"] - 2
        assert parcial["tempo_decorrido_ms"] >= 0 and parcial["estimativa_restante_ms"] >= 0
        assert [b["situacao"] for b in parcial["blocos"][:3]] == [
            "concluido",
            "concluido",
            "em_andamento",
        ]
        antes = cliente.get(f"/casos/{caso_id}/sugestoes-ia", headers=H_ANA).json()
        assert len(antes) == parcial["itens_aceitos"] > 0
        liberar.set()
        final = esperar_extracao(cliente, caso_id, extracao_id)
        assert final["situacao"] == "sucesso" and not final["ativa"] and not final["retomavel"]
        assert final["blocos_concluidos"] == final["blocos_total"] == len(provedor.chamadas)
        assert final["estimativa_restante_ms"] is None and final["bloco_atual"] is None
        extracao = _extracao(cliente, caso_id, extracao_id)
        # Um CNPJ e um valor por linha do documento, sem repetição.
        assert extracao["itens_aceitos"] == 2 * LINHAS_POR_DOCUMENTO == len(extracao["sugestoes"])
        assert extracao["parametros"]["duplicatas_descartadas"] == 0
        assert extracao["blocos"] == final["blocos_total"]
        assert _acoes_ia(app) == [
            ("ia.extrair", "ana"),
            ("ia.extracao_concluida", "extrator"),
        ]
        with app.state.fabrica_sessoes() as s:
            fim = s.execute(
                select(Auditoria).where(Auditoria.acao == "ia.extracao_concluida")
            ).scalar_one()
        assert fim.caso_id == caso_id and fim.justificativa.endswith("Teste automatizado")
        assert fim.detalhes["blocos_concluidos"] == final["blocos_total"]
        assert fim.detalhes["metricas"]["metricas_sha256"]
        assert _cadeia_integra(app)


def test_andamento_nao_enche_a_auditoria_e_exige_ser_membro_do_caso(tmp_path):
    app = _app(tmp_path, ProvedorRoteirizado())
    with TestClient(app) as cliente:
        caso_id, (captura_id,) = _caso(cliente)
        extracao_id = extrair_e_esperar(cliente, caso_id, captura_id, H_ANA).json()["id"]
        with app.state.fabrica_sessoes() as s:
            antes = auditoria.contar(s)
        for _ in range(5):
            _progresso(cliente, caso_id, extracao_id)
        with app.state.fabrica_sessoes() as s:
            assert auditoria.contar(s) == antes
        rota = f"/casos/{caso_id}/extracoes-ia/{extracao_id}"
        # Quem não é membro do caso não vê nem o andamento; sem justificativa também não.
        assert cliente.get(f"{rota}/progresso", headers=H_ROOT).status_code == 403
        sem_justificativa = cabecalhos("tok-analista", None)
        assert cliente.get(f"{rota}/progresso", headers=sem_justificativa).status_code == 400
        assert cliente.get(f"{rota}/progresso").status_code == 401
        assert cliente.post(f"{rota}/cancelar", headers=H_ROOT).status_code == 403
        assert cliente.post(f"{rota}/retomar", headers=H_ROOT).status_code == 403
        # O leitor acompanha, mas não dispara, não cancela e não retoma.
        r = cliente.post(
            f"/casos/{caso_id}/membros", json={"usuario": "root", "papel": "leitor"}, headers=H_ANA
        )
        assert r.status_code == 200, r.text
        assert cliente.get(f"{rota}/progresso", headers=H_ROOT).status_code == 200
        assert _pedir(cliente, caso_id, captura_id, H_ROOT).status_code == 403
        assert cliente.post(f"{rota}/cancelar", headers=H_ROOT).status_code == 403
        assert cliente.post(f"{rota}/retomar", headers=H_ROOT).status_code == 403
        # Execução de outro caso (ou inexistente) não é encontrada por este caminho.
        outro = cliente.post("/casos", json=corpo_caso("Outro"), headers=H_ANA).json()["id"]
        r = cliente.get(f"/casos/{outro}/extracoes-ia/{extracao_id}/progresso", headers=H_ANA)
        assert r.status_code == 404
        assert cliente.get(f"/casos/{caso_id}/extracoes-ia/nada", headers=H_ANA).status_code == 404
        # Execução concluída não tem o que cancelar nem o que retomar.
        assert cliente.post(f"{rota}/cancelar", headers=H_ANA).status_code == 409
        assert cliente.post(f"{rota}/retomar", headers=H_ANA).status_code == 409


def test_sobreposicao_entre_blocos_gera_duplicatas_que_sao_descartadas(tmp_path):
    app = _app(tmp_path, ProvedorRoteirizado(), ia_sobreposicao_tokens=20)
    with TestClient(app) as cliente:
        caso_id, (captura_id,) = _caso(cliente)
        extracao = extrair_e_esperar(cliente, caso_id, captura_id, H_ANA).json()
        assert extracao["situacao"] == "sucesso"
        assert extracao["parametros"]["sobreposicao_caracteres"] == 60
        # A linha repetida nos dois blocos vizinhos é extraída duas vezes e gravada uma só.
        assert extracao["parametros"]["duplicatas_descartadas"] > 0
        assert extracao["itens_aceitos"] == 2 * LINHAS_POR_DOCUMENTO == len(extracao["sugestoes"])
        chaves = [(s["tipo"], s["valor"], s["posicao_inicio"]) for s in extracao["sugestoes"]]
        assert len(set(chaves)) == len(chaves)
        assert (
            extracao["itens_recebidos"]
            == extracao["itens_aceitos"]
            + extracao["itens_recusados"]
            + extracao["parametros"]["duplicatas_descartadas"]
        )


# ---- Saída cortada, bloco lento, travamento e repetição ------------------------------------------


def test_saida_cortada_divide_o_bloco_ao_meio_e_as_metades_concluem(tmp_path):
    cortada = {"done_reason": "length", "prompt_eval_count": 100, "eval_count": 2048}

    def acao(texto, _chamada, _controle):
        # Blocos de mais de 700 caracteres estouram o teto de saída; as metades cabem.
        return Geracao('{"itens": [{"tipo": "valor", "va', cortada) if len(texto) > 700 else None

    provedor = ProvedorRoteirizado(acao)
    app = _app(tmp_path, provedor, ia_bloco_tokens=300, ia_sobreposicao_tokens=20)
    with TestClient(app) as cliente:
        caso_id, (captura_id,) = _caso(cliente)
        extracao = extrair_e_esperar(cliente, caso_id, captura_id, H_ANA).json()
        originais = len(ia.dividir_em_intervalos(DOCUMENTO, 900, 60))
        assert originais == 2
        # O primeiro bloco (cerca de 900 caracteres) foi cortado e virou duas metades.
        assert extracao["situacao"] == "sucesso" and extracao["blocos"] == originais + 1
        blocos = {b["numero"]: b for b in extracao["metricas"]["blocos"]}
        assert blocos[1]["situacao"] == "dividido" and blocos[1]["causa"] == "saida_cortada"
        assert [(blocos[n]["pai_numero"], blocos[n]["situacao"]) for n in (3, 4)] == [
            (1, "concluido"),
            (1, "concluido"),
        ]
        # As metades cobrem o bloco original, com a sobreposição no meio.
        assert (blocos[3]["inicio"], blocos[4]["fim"]) == (blocos[1]["inicio"], blocos[1]["fim"])
        assert blocos[1]["inicio"] < blocos[4]["inicio"] < blocos[3]["fim"] < blocos[1]["fim"]
        assert extracao["metricas"]["avisos"] == [
            {"tipo": "saida_cortada", "blocos": [1]},
            {"tipo": "blocos_divididos", "blocos": [1]},
        ]
        # Nada da saída cortada foi aproveitado, e nenhum item se perdeu nem se repetiu.
        assert extracao["itens_aceitos"] == 2 * LINHAS_POR_DOCUMENTO == len(extracao["sugestoes"])
        assert extracao["progresso"]["blocos_total"] == originais + 1
        assert [b["pai_numero"] for b in extracao["progresso"]["blocos"]] == [1, 1, None]
        assert extracao["metricas"]["tokens_saida"] == 2048


def test_saida_sempre_cortada_para_de_dividir_e_o_bloco_falha(tmp_path):
    cortada = {"done_reason": "length"}
    provedor = ProvedorRoteirizado(lambda *_: Geracao('{"itens": [', cortada))
    app = _app(tmp_path, provedor, ia_bloco_tokens=600, ia_max_divisoes=1)
    with TestClient(app) as cliente:
        caso_id, (captura_id,) = _caso(cliente)
        extracao = extrair_e_esperar(cliente, caso_id, captura_id, H_ANA).json()
        # Um bloco, dividido uma vez (o máximo configurado); as duas metades falham.
        assert extracao["situacao"] == "falha" and extracao["sugestoes"] == []
        assert [(b["numero"], b["situacao"]) for b in extracao["metricas"]["blocos"]] == [
            (1, "dividido"),
            (2, "falho"),
            (3, "falho"),
        ]
        assert extracao["metricas"]["blocos_falhos"] == [
            {"bloco": 2, "causa": "saida_cortada"},
            {"bloco": 3, "causa": "saida_cortada"},
        ]
        assert "Nenhum bloco foi concluído" in extracao["motivo"]
        assert _acoes_ia(app)[-1] == ("ia.extracao_falha", "extrator")


def test_bloco_lento_passa_do_tempo_limite_e_e_dividido(tmp_path):
    def acao(texto, _chamada, _controle):
        if len(texto) > 700:
            raise ErroProvedor("A geração do bloco passou do tempo limite.", "tempo_esgotado")
        return None

    provedor = ProvedorRoteirizado(acao)
    app = _app(tmp_path, provedor, ia_bloco_tokens=300)
    with TestClient(app) as cliente:
        caso_id, (captura_id,) = _caso(cliente)
        extracao = extrair_e_esperar(cliente, caso_id, captura_id, H_ANA).json()
        assert extracao["situacao"] == "sucesso"
        blocos = {b["numero"]: b for b in extracao["metricas"]["blocos"]}
        # O bloco lento não é repetido igual (levaria o mesmo tempo): vira duas metades.
        assert blocos[1]["situacao"] == "dividido" and blocos[1]["causa"] == "tempo_esgotado"
        assert blocos[1]["tentativas"] == 1
        assert blocos[1]["falhas"][0]["causa"] == "tempo_esgotado"
        assert extracao["itens_aceitos"] == 2 * LINHAS_POR_DOCUMENTO
        assert extracao["parametros"]["bloco_tempo_segundos"] == 600.0


def test_bloco_lento_que_nao_pode_ser_dividido_falha_sem_perder_os_outros(tmp_path):
    def acao(_texto, chamada, _controle):
        if chamada == 2:
            raise ErroProvedor("A geração do bloco passou do tempo limite.", "tempo_esgotado")
        return None

    provedor = ProvedorRoteirizado(acao)
    app = _app(tmp_path, provedor)  # blocos de 300 caracteres: pequenos demais para dividir
    with TestClient(app) as cliente:
        caso_id, (captura_id,) = _caso(cliente)
        extracao = extrair_e_esperar(cliente, caso_id, captura_id, H_ANA).json()
        total = extracao["progresso"]["blocos_total"]
        assert extracao["situacao"] == "concluida_com_falhas"
        assert (extracao["progresso"]["blocos_concluidos"], total) == (total - 1, total)
        assert extracao["metricas"]["blocos_falhos"] == [{"bloco": 2, "causa": "tempo_esgotado"}]
        assert "bloco 2 (tempo_esgotado)" in extracao["motivo"] and "retome" in extracao["motivo"]
        assert len(provedor.chamadas) == total  # o bloco lento não foi repetido
        assert 0 < extracao["itens_aceitos"] < 2 * LINHAS_POR_DOCUMENTO
        assert extracao["progresso"]["retomavel"] is True


def test_falha_transitoria_e_repetida_com_espera_que_dobra(tmp_path):
    def acao(_texto, chamada, _controle):
        if chamada in (2, 3):
            raise ErroProvedor("O servidor local de IA não respondeu.", "conexao", transitoria=True)
        return None

    provedor = ProvedorRoteirizado(acao)
    app = _app(tmp_path, provedor, ia_trabalhador_ativo=False, ia_espera_repeticao_segundos=4.0)
    esperas: list[float] = []
    with TestClient(app) as cliente:
        caso_id, (captura_id,) = _caso(cliente)
        extracao_id = _pedir(cliente, caso_id, captura_id).json()["id"]
        # Sem o trabalhador, a execução fica na fila; o teste a processa com a espera trocada.
        assert _progresso(cliente, caso_id, extracao_id)["situacao"] == "na_fila"
        _processar_direto(app, extracao_id, provedor, dormir=esperas.append)
        extracao = _extracao(cliente, caso_id, extracao_id)
        total = extracao["progresso"]["blocos_total"]
        assert extracao["situacao"] == "sucesso"
        assert len(provedor.chamadas) == total + 2
        # O bloco 2 falhou duas vezes e passou na terceira: esperas de 4 s e de 8 s, em fatias.
        assert sum(esperas) == pytest.approx(12.0) and max(esperas) <= ia.FATIA_DE_ESPERA_SEGUNDOS
        bloco = extracao["metricas"]["blocos"][1]
        assert (bloco["situacao"], bloco["tentativas"]) == ("concluido", 3)
        assert [f["causa"] for f in bloco["falhas"]] == ["conexao", "conexao"]
        assert extracao["itens_aceitos"] == 2 * LINHAS_POR_DOCUMENTO


def test_falha_que_nao_e_transitoria_nao_e_repetida(tmp_path):
    def acao(_texto, chamada, _controle):
        if chamada == 1:
            raise ErroProvedor("O servidor local de IA respondeu HTTP 400.", "http")
        return None

    provedor = ProvedorRoteirizado(acao)
    app = _app(tmp_path, provedor)
    with TestClient(app) as cliente:
        caso_id, (captura_id,) = _caso(cliente)
        extracao = extrair_e_esperar(cliente, caso_id, captura_id, H_ANA).json()
        assert extracao["situacao"] == "concluida_com_falhas"
        assert extracao["metricas"]["blocos"][0]["tentativas"] == 1
        assert len(provedor.chamadas) == extracao["progresso"]["blocos_total"]


def test_servidor_fora_do_ar_interrompe_a_execucao_em_vez_de_falhar_todos_os_blocos(tmp_path):
    def acao(_texto, chamada, _controle):
        if chamada > 1:
            raise ErroProvedor("O servidor local de IA não respondeu.", "conexao", transitoria=True)
        return None

    provedor = ProvedorRoteirizado(acao)
    app = _app(tmp_path, provedor, ia_repeticoes=1, ia_falhas_seguidas_max=2)
    with TestClient(app) as cliente:
        caso_id, (captura_id,) = _caso(cliente)
        extracao = extrair_e_esperar(cliente, caso_id, captura_id, H_ANA).json()
        progresso = extracao["progresso"]
        assert extracao["situacao"] == "interrompida" and "servidor local" in extracao["motivo"]
        # Um bloco concluído, dois falhos (cada um tentado duas vezes) e o resto nem tentado.
        assert (progresso["blocos_concluidos"], progresso["blocos_falhos"]) == (1, 2)
        assert progresso["blocos_pendentes"] == progresso["blocos_total"] - 3 > 0
        assert len(provedor.chamadas) == 1 + 2 * 2 and progresso["retomavel"]
        assert _acoes_ia(app)[-1] == ("ia.extracao_interrompida", "extrator")
        # O servidor voltou: a retomada processa só os blocos falhos e os pendentes.
        provedor.acao = None
        r = cliente.post(f"/casos/{caso_id}/extracoes-ia/{extracao['id']}/retomar", headers=H_ANA)
        assert r.status_code == 202, r.text
        final = esperar_extracao(cliente, caso_id, extracao["id"])
        assert final["situacao"] == "sucesso" and final["retomadas"] == 1
        assert len(provedor.chamadas) == 5 + (final["blocos_total"] - 1)
        assert final["itens_aceitos"] == 2 * LINHAS_POR_DOCUMENTO


# ---- Retomada ------------------------------------------------------------------------------------


def test_retomada_processa_so_os_blocos_que_falharam_e_preserva_os_concluidos(tmp_path):
    def acao(_texto, chamada, _controle):
        return Geracao("isto não é JSON") if chamada in (2, 4) else None

    provedor = ProvedorRoteirizado(acao)
    app = _app(tmp_path, provedor)
    with TestClient(app) as cliente:
        caso_id, (captura_id,) = _caso(cliente)
        primeira = extrair_e_esperar(cliente, caso_id, captura_id, H_ANA).json()
        total = primeira["progresso"]["blocos_total"]
        assert primeira["situacao"] == "concluida_com_falhas"
        assert primeira["metricas"]["blocos_falhos"] == [
            {"bloco": 2, "causa": "json_invalido"},
            {"bloco": 4, "causa": "json_invalido"},
        ]
        assert len(provedor.chamadas) == total
        ids_antes = {s["id"] for s in primeira["sugestoes"]}
        assert 0 < len(ids_antes) < 2 * LINHAS_POR_DOCUMENTO
        # O investigador decide uma sugestão antes de retomar: a decisão não se perde.
        decidida = primeira["sugestoes"][0]["id"]
        r = cliente.post(
            f"/casos/{caso_id}/sugestoes-ia/{decidida}/decisao",
            json={"decisao": "recusada", "justificativa": "Conferido"},
            headers=H_ANA,
        )
        assert r.status_code == 200, r.text
        provedor.acao = None
        h_retomada = cabecalhos("tok-analista", "Nova tentativa dos blocos falhos")
        r = cliente.post(
            f"/casos/{caso_id}/extracoes-ia/{primeira['id']}/retomar", headers=h_retomada
        )
        assert r.status_code == 202 and r.json()["situacao"] == "na_fila"
        assert (r.json()["blocos_pendentes"], r.json()["blocos_concluidos"]) == (2, total - 2)
        esperar_extracao(cliente, caso_id, primeira["id"])
        segunda = _extracao(cliente, caso_id, primeira["id"])
        assert segunda["id"] == primeira["id"] and segunda["situacao"] == "sucesso"
        # Só os dois blocos que tinham falhado voltaram ao modelo.
        assert len(provedor.chamadas) == total + 2
        assert provedor.chamadas[-2:] == [provedor.chamadas[1], provedor.chamadas[3]]
        ids_depois = {s["id"] for s in segunda["sugestoes"]}
        assert ids_antes < ids_depois and len(ids_depois) == 2 * LINHAS_POR_DOCUMENTO
        assert (
            next(s for s in segunda["sugestoes"] if s["id"] == decidida)["situacao"] == "recusada"
        )
        assert segunda["metricas"]["blocos_falhos"] == [] and "falha" not in segunda["metricas"]
        assert segunda["metricas"]["duracao_ms"] >= primeira["metricas"]["duracao_ms"]
        assert [b["tentativas"] for b in segunda["metricas"]["blocos"]][1] == 2
        assert segunda["progresso"]["retomadas"] == 1
        assert _acoes_ia(app) == [
            ("ia.extrair", "ana"),
            ("ia.extracao_concluida", "extrator"),
            ("ia.extracao_retomar", "ana"),
            ("ia.extracao_concluida", "extrator"),
        ]
        with app.state.fabrica_sessoes() as s:
            entradas = list(
                s.execute(
                    select(Auditoria)
                    .where(Auditoria.acao.like("ia.extracao_%"))
                    .order_by(Auditoria.seq)
                ).scalars()
            )
        assert [e.detalhes.get("situacao") for e in entradas[::2]] == [
            "concluida_com_falhas",
            "sucesso",
        ]
        assert entradas[1].detalhes["situacao_anterior"] == "concluida_com_falhas"
        assert entradas[1].detalhes["blocos_pendentes"] == 2
        # A conclusão da retomada leva a justificativa de quem retomou.
        assert entradas[2].justificativa.endswith("Nova tentativa dos blocos falhos")
        assert _cadeia_integra(app)


def test_retomada_e_recusada_se_o_modelo_mudou_desde_o_inicio(tmp_path):
    provedor = ProvedorRoteirizado(lambda _t, chamada, _c: Geracao("x") if chamada == 1 else None)
    app = _app(tmp_path, provedor)
    with TestClient(app) as cliente:
        caso_id, (captura_id,) = _caso(cliente)
        primeira = extrair_e_esperar(cliente, caso_id, captura_id, H_ANA).json()
        assert primeira["situacao"] == "concluida_com_falhas"
        aceitos = primeira["itens_aceitos"]
        provedor._versao = "outro-resumo"  # o modelo instalado foi trocado
        chamadas = len(provedor.chamadas)
        r = cliente.post(f"/casos/{caso_id}/extracoes-ia/{primeira['id']}/retomar", headers=H_ANA)
        assert r.status_code == 202
        esperar_extracao(cliente, caso_id, primeira["id"])
        segunda = _extracao(cliente, caso_id, primeira["id"])
        # Resultado de dois modelos não se mistura numa execução: nada foi enviado ao novo.
        assert segunda["situacao"] == "falha" and "não pode ser retomada" in segunda["motivo"]
        assert segunda["metricas"]["falha"]["causa"] == "execucao_incompativel"
        assert len(provedor.chamadas) == chamadas and segunda["itens_aceitos"] == aceitos
        assert segunda["modelo_versao"] == primeira["modelo_versao"]


# ---- Cancelamento --------------------------------------------------------------------------------


def test_cancelamento_no_meio_da_geracao_preserva_os_blocos_concluidos(tmp_path):
    liberar = threading.Event()
    provedor = ProvedorRoteirizado(_segurar_ate(liberar, na_chamada=2))
    app = _app(tmp_path, provedor)
    with TestClient(app) as cliente:
        caso_id, (captura_id,) = _caso(cliente)
        extracao_id = _pedir(cliente, caso_id, captura_id).json()["id"]
        esperar_extracao(cliente, caso_id, extracao_id, ate=lambda p: p["bloco_atual"] == 2)
        r = cliente.post(f"/casos/{caso_id}/extracoes-ia/{extracao_id}/cancelar", headers=H_ANA)
        assert r.status_code == 200 and r.json()["cancelamento_pedido"] is True
        final = esperar_extracao(cliente, caso_id, extracao_id)
        assert final["situacao"] == "cancelada" and final["retomavel"] and not final["ativa"]
        assert (final["blocos_concluidos"], final["blocos_falhos"]) == (1, 0)
        assert final["blocos_pendentes"] == final["blocos_total"] - 1
        assert len(provedor.chamadas) == 2  # nenhum bloco novo depois do pedido
        extracao = _extracao(cliente, caso_id, extracao_id)
        assert "Cancelada por ana" in extracao["motivo"] and len(extracao["sugestoes"]) > 0
        assert extracao["metricas"]["blocos"][1]["situacao"] == "pendente"
        assert extracao["metricas"]["blocos"][1]["tentativas"] == 0
        assert _acoes_ia(app) == [
            ("ia.extrair", "ana"),
            ("ia.extracao_cancelar", "ana"),
            ("ia.extracao_cancelada", "extrator"),
        ]
        # O documento fica livre: dá para retomar, e a retomada conclui o que faltava.
        liberar.set()
        r = cliente.post(f"/casos/{caso_id}/extracoes-ia/{extracao_id}/retomar", headers=H_ANA)
        assert r.status_code == 202, r.text
        retomada = esperar_extracao(cliente, caso_id, extracao_id)
        assert retomada["situacao"] == "sucesso" and not retomada["cancelamento_pedido"]
        assert retomada["itens_aceitos"] == 2 * LINHAS_POR_DOCUMENTO
        assert _cadeia_integra(app)


def test_cancelamento_na_fila_e_imediato_e_nada_vai_ao_modelo(tmp_path):
    liberar = threading.Event()
    provedor = ProvedorRoteirizado(_segurar_ate(liberar))
    app = _app(tmp_path, provedor, documentos=2)
    with TestClient(app) as cliente:
        caso_id, (primeira, segunda) = _caso(cliente)
        em_curso = _pedir(cliente, caso_id, primeira).json()["id"]
        esperar_extracao(cliente, caso_id, em_curso, ate=lambda p: p["bloco_atual"] == 1)
        na_fila = _pedir(cliente, caso_id, segunda).json()["id"]
        assert _progresso(cliente, caso_id, na_fila)["posicao_na_fila"] == 1
        r = cliente.post(f"/casos/{caso_id}/extracoes-ia/{na_fila}/cancelar", headers=H_ANA)
        assert r.status_code == 200 and r.json()["situacao"] == "cancelada"
        assert "antes de começar" in r.json()["motivo"] and r.json()["blocos_total"] == 0
        liberar.set()
        assert esperar_extracao(cliente, caso_id, em_curso)["situacao"] == "sucesso"
        # Só o primeiro documento foi ao modelo.
        assert all(DOCUMENTO.startswith(provedor.chamadas[0][:40]) for _ in provedor.chamadas)
        assert _progresso(cliente, caso_id, na_fila)["situacao"] == "cancelada"
        with app.state.fabrica_sessoes() as s:
            assert s.get(ExtracaoIA, na_fila).trava is None
        cancelar = [a for a in _acoes_ia(app) if a[0] == "ia.extracao_cancelar"]
        assert cancelar == [("ia.extracao_cancelar", "ana")]


# ---- Uma execução por documento e uma por vez no serviço -----------------------------------------


def test_uma_execucao_por_documento_e_uma_geracao_por_vez_no_servico(tmp_path):
    liberar = threading.Event()
    provedor = ProvedorRoteirizado(_segurar_ate(liberar))
    app = _app(tmp_path, provedor, documentos=3)
    with TestClient(app) as cliente:
        caso_id, capturas = _caso(cliente)
        primeira = _pedir(cliente, caso_id, capturas[0]).json()["id"]
        esperar_extracao(cliente, caso_id, primeira, ate=lambda p: p["situacao"] == "em_andamento")
        # Mesmo documento: recusado enquanto a primeira está ativa, com o id dela na mensagem.
        r = _pedir(cliente, caso_id, capturas[0])
        assert r.status_code == 409 and primeira in r.json()["detail"]
        # Outros documentos: aceitos, mas esperam a vez.
        segunda = _pedir(cliente, caso_id, capturas[1]).json()["id"]
        terceira = _pedir(cliente, caso_id, capturas[2]).json()["id"]
        assert _pedir(cliente, caso_id, capturas[1]).status_code == 409
        esperar_extracao(cliente, caso_id, primeira, ate=lambda p: p["bloco_atual"] == 1)
        time.sleep(0.05)
        fila = [_progresso(cliente, caso_id, e) for e in (segunda, terceira)]
        assert [p["situacao"] for p in fila] == ["na_fila", "na_fila"]
        assert [p["posicao_na_fila"] for p in fila] == [1, 2]
        liberar.set()
        finais = [esperar_extracao(cliente, caso_id, e) for e in (primeira, segunda, terceira)]
        assert [p["situacao"] for p in finais] == ["sucesso"] * 3
        # Nunca houve duas gerações ao mesmo tempo, e a fila andou na ordem dos pedidos.
        assert provedor.max_simultaneas == 1
        with app.state.fabrica_sessoes() as s:
            ordem = [s.get(ExtracaoIA, e).concluido_em for e in (primeira, segunda, terceira)]
            assert ordem == sorted(ordem)
            assert s.execute(select(ExtracaoIA.trava)).scalars().all() == [None, None, None]
        # Terminada a execução, o documento aceita outra.
        assert _pedir(cliente, caso_id, capturas[0]).status_code == 202


def test_trava_do_documento_tambem_segura_dois_pedidos_simultaneos(tmp_path):
    app = _app(tmp_path, ProvedorRoteirizado(), ia_trabalhador_ativo=False)
    with TestClient(app) as cliente:
        caso_id, (captura_id,) = _caso(cliente)
        with app.state.fabrica_sessoes() as s:
            captura = s.get(ia.Captura, captura_id)
            ia.criar(s, provedor_nome="falso", captura=captura, usuario="ana", justificativa="j")
            s.commit()
        # O segundo pedido passou pela consulta antes de o primeiro gravar: o índice único barra.
        with app.state.fabrica_sessoes() as s:
            captura = s.get(ia.Captura, captura_id)
            repetida = ExtracaoIA(
                caso_id=caso_id,
                captura_id=captura_id,
                captura_sha256=captura.sha256,
                provedor="falso",
                modelo="",
                usuario="ana",
                trava=captura_id,
            )
            s.add(repetida)
            with pytest.raises(Exception, match="(?i)unique"):
                s.flush()
        assert _pedir(cliente, caso_id, captura_id).status_code == 409


# ---- Reinício da API -----------------------------------------------------------------------------


def test_reinicio_da_api_marca_a_execucao_orfa_como_interrompida_e_a_retomada_conclui(
    tmp_path, monkeypatch
):
    """A API "cai" com um bloco no meio da geração (a thread fica presa e não grava mais nada).
    Ao subir de novo, a execução órfã vira ``interrompida``; a retomada processa só o que
    faltava; e o trabalhador antigo, se ainda responder, não grava por cima."""
    monkeypatch.setattr(modulo_trabalhador, "ESPERA_AO_PARAR_SEGUNDOS", 0.05)
    solta = threading.Event()

    def presa(_texto, chamada, _controle):
        if chamada == 3:
            solta.wait(30)  # não atende à interrupção: é o processo que morreu
        return None

    antigo = ProvedorRoteirizado(presa)
    app_antigo = _app(tmp_path, antigo)
    with TestClient(app_antigo) as cliente:
        caso_id, (captura_id,) = _caso(cliente)
        extracao_id = _pedir(cliente, caso_id, captura_id).json()["id"]
        esperar_extracao(cliente, caso_id, extracao_id, ate=lambda p: p["bloco_atual"] == 3)
        zumbi = app_antigo.state.trabalhador_ia._thread
    # A API antiga saiu sem conseguir fechar a execução: ela ficou "em_andamento" no banco.
    assert zumbi.is_alive()
    with app_antigo.state.fabrica_sessoes() as s:
        assert s.get(ExtracaoIA, extracao_id).situacao == "em_andamento"
        assert s.get(ExtracaoIA, extracao_id).trava == captura_id

    novo = ProvedorRoteirizado()
    app_novo = _app(tmp_path, novo)
    with TestClient(app_novo) as cliente:
        orfa = _progresso(cliente, caso_id, extracao_id)
        assert orfa["situacao"] == "interrompida" and "reiniciado" in orfa["motivo"]
        assert (orfa["blocos_concluidos"], orfa["bloco_atual"]) == (2, None)
        assert orfa["retomavel"] and orfa["blocos_pendentes"] == orfa["blocos_total"] - 2
        sugestoes = cliente.get(f"/casos/{caso_id}/sugestoes-ia", headers=H_ANA).json()
        assert len(sugestoes) == orfa["itens_aceitos"] > 0  # o que já estava gravado ficou
        assert _acoes_ia(app_novo) == [
            ("ia.extrair", "ana"),
            ("ia.extracao_interrompida", "extrator"),
        ]
        r = cliente.post(f"/casos/{caso_id}/extracoes-ia/{extracao_id}/retomar", headers=H_ANA)
        assert r.status_code == 202, r.text
        final = esperar_extracao(cliente, caso_id, extracao_id)
        assert final["situacao"] == "sucesso"
        assert len(novo.chamadas) == final["blocos_total"] - 2  # só os que faltavam
        assert final["itens_aceitos"] == 2 * LINHAS_POR_DOCUMENTO
        # O trabalhador antigo volta a responder: ele não é mais o dono e não grava nada.
        solta.set()
        zumbi.join(timeout=10)
        assert not zumbi.is_alive()
        depois = _extracao(cliente, caso_id, extracao_id)
        assert depois["situacao"] == "sucesso"
        assert len(depois["sugestoes"]) == 2 * LINHAS_POR_DOCUMENTO
        assert _acoes_ia(app_novo)[2:] == [
            ("ia.extracao_retomar", "ana"),
            ("ia.extracao_concluida", "extrator"),
        ]
        assert _cadeia_integra(app_novo)


def test_parada_normal_do_servico_fecha_a_execucao_como_interrompida_e_a_fila_sobrevive(tmp_path):
    liberar = threading.Event()
    provedor = ProvedorRoteirizado(_segurar_ate(liberar, na_chamada=2))
    app = _app(tmp_path, provedor, documentos=2)
    with TestClient(app) as cliente:
        caso_id, (primeira, segunda) = _caso(cliente)
        em_curso = _pedir(cliente, caso_id, primeira).json()["id"]
        esperar_extracao(cliente, caso_id, em_curso, ate=lambda p: p["bloco_atual"] == 2)
        na_fila = _pedir(cliente, caso_id, segunda).json()["id"]
    # O serviço parou (saída do bloco ``with``): a execução em curso foi fechada na hora.
    liberar.set()
    with app.state.fabrica_sessoes() as s:
        parada = s.get(ExtracaoIA, em_curso)
        assert parada.situacao == "interrompida" and "encerrado" in parada.motivo
        assert parada.trava is None and s.get(ExtracaoIA, na_fila).situacao == "na_fila"
        blocos = s.execute(
            select(ExtracaoIABloco.situacao)
            .where(ExtracaoIABloco.extracao_id == em_curso)
            .order_by(ExtracaoIABloco.numero)
        ).scalars()
        assert list(blocos)[:2] == ["concluido", "pendente"]
    # Ao subir de novo, a que estava na fila é processada sem ninguém pedir.
    novo = _app(tmp_path, ProvedorRoteirizado(), documentos=2)
    with TestClient(novo) as cliente:
        assert esperar_extracao(cliente, caso_id, na_fila)["situacao"] == "sucesso"
        assert _progresso(cliente, caso_id, em_curso)["situacao"] == "interrompida"


def test_erro_inesperado_nao_deixa_a_execucao_presa(tmp_path):
    def acao(_texto, chamada, _controle):
        if chamada == 2:
            raise RuntimeError("defeito sintético do provedor")
        return None

    app = _app(tmp_path, ProvedorRoteirizado(acao))
    with TestClient(app) as cliente:
        caso_id, (captura_id,) = _caso(cliente)
        extracao = extrair_e_esperar(cliente, caso_id, captura_id, H_ANA).json()
        assert extracao["situacao"] == "falha" and "Erro interno" in extracao["motivo"]
        assert extracao["metricas"]["falha"]["causa"] == "erro_interno"
        # O bloco concluído antes do erro ficou gravado, e o documento não ficou travado.
        assert extracao["progresso"]["blocos_concluidos"] == 1 and len(extracao["sugestoes"]) > 0
        assert extracao["progresso"]["retomavel"]
        assert _pedir(cliente, caso_id, captura_id).status_code == 202


def test_limite_de_itens_vale_para_a_execucao_inteira(tmp_path):
    app = _app(tmp_path, ProvedorRoteirizado(), ia_max_itens=5)
    with TestClient(app) as cliente:
        caso_id, (captura_id,) = _caso(cliente)
        extracao = extrair_e_esperar(cliente, caso_id, captura_id, H_ANA).json()
        assert extracao["itens_aceitos"] == 5 == len(extracao["sugestoes"])
        assert {r["motivo"] for r in extracao["recusas"]} == {"acima_do_limite_de_itens"}
        with app.state.fabrica_sessoes() as s:
            assert len(s.execute(select(SugestaoIA.id)).all()) == 5


def _processar_direto(app, extracao_id: str, provedor, dormir=None, **ajustes) -> None:
    """Processa a execução na thread do teste, como o trabalhador faria."""
    trabalhador = Trabalhador(app.state)
    with app.state.fabrica_sessoes() as sessao:
        assert ia.assumir(sessao, trabalhador.id) == extracao_id
        extracao = sessao.get(ExtracaoIA, extracao_id)
        ia.processar(
            trabalhador._contexto(sessao, extracao),
            extracao_id,
            provedor,
            replace(ia.Ajustes.da_configuracao(app.state.config), **ajustes),
            ia.ControleExecucao(trabalhador.id, dormir=dormir or (lambda _s: None)),
        )


# ---- Provedor Ollama com fluxo, contra um servidor HTTP de mentira -------------------------------


class _ServidorDeFluxo:
    """Servidor HTTP em 127.0.0.1 que imita o fluxo do ``/api/chat``: escreve cada linha do
    roteiro depois da espera indicada. Dados sintéticos; nenhum modelo."""

    def __init__(self, roteiro: list[tuple[float, dict]]) -> None:
        servidor = self
        self.roteiro = roteiro
        self.corpos: list[dict] = []
        self.cliente_fechou = threading.Event()

        class Tratador(BaseHTTPRequestHandler):
            def log_message(self, *_argumentos) -> None:  # silencioso
                pass

            def _json(self, objeto: dict) -> None:
                corpo = json.dumps(objeto).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(corpo)))
                self.end_headers()
                self.wfile.write(corpo)

            def do_GET(self) -> None:  # noqa: N802 - nome exigido pela biblioteca
                if self.path == "/api/version":
                    self._json({"version": "0.99.0"})
                elif self.path == "/api/tags":
                    modelo = {"name": "modelo-sintetico:7b", "digest": "d" * 64}
                    self._json({"models": [modelo]})
                else:
                    self._json({"models": []})

            def do_POST(self) -> None:  # noqa: N802 - nome exigido pela biblioteca
                tamanho = int(self.headers.get("Content-Length", "0"))
                servidor.corpos.append(json.loads(self.rfile.read(tamanho)))
                self.send_response(200)
                self.send_header("Content-Type", "application/x-ndjson")
                self.end_headers()
                try:
                    for espera, linha in servidor.roteiro:
                        time.sleep(espera)
                        self.wfile.write(json.dumps(linha).encode() + b"\n")
                        self.wfile.flush()
                except OSError:
                    servidor.cliente_fechou.set()

        self._http = ThreadingHTTPServer(("127.0.0.1", 0), Tratador)
        self._http.daemon_threads = True
        self.url = f"http://127.0.0.1:{self._http.server_address[1]}"
        threading.Thread(target=self._http.serve_forever, daemon=True).start()

    def provedor(self, **opcoes) -> ProvedorOllama:
        return ProvedorOllama(self.url, "modelo-sintetico:7b", timeout_segundos=5, **opcoes)

    def fechar(self) -> None:
        self._http.shutdown()
        self._http.server_close()


def _pedaco(texto: str) -> dict:
    return {"message": {"role": "assistant", "content": texto}, "done": False}


FIM = {
    "message": {"role": "assistant", "content": ""},
    "done": True,
    "done_reason": "stop",
    "prompt_eval_count": 40,
    "eval_count": 3,
}


@pytest.fixture
def servidor_de_fluxo():
    servidores: list[_ServidorDeFluxo] = []

    def criar(roteiro) -> _ServidorDeFluxo:
        servidores.append(_ServidorDeFluxo(roteiro))
        return servidores[-1]

    yield criar
    for servidor in servidores:
        servidor.fechar()


def test_ollama_junta_os_pedacos_do_fluxo_e_envia_a_janela_e_o_teto(servidor_de_fluxo):
    servidor = servidor_de_fluxo(
        [(0, _pedaco('{"ite')), (0.01, _pedaco('ns": ')), (0.01, _pedaco("[]}")), (0, FIM)]
    )
    provedor = servidor.provedor(num_ctx=8192, num_predict=2048)
    geracao = provedor.gerar(ia.INSTRUCOES, "texto sintético", ia.ESQUEMA_RESPOSTA)
    assert geracao.texto == '{"itens": []}'
    assert geracao.metricas["done_reason"] == "stop" and geracao.metricas["eval_count"] == 3
    assert geracao.metricas["primeiro_token_ms"] >= 0
    (corpo,) = servidor.corpos
    assert corpo["stream"] is True
    assert corpo["options"] == {"temperature": 0.0, "seed": 0, "num_ctx": 8192, "num_predict": 2048}
    assert provedor.parametros()["num_ctx"] == 8192 and provedor.parametros()["stream"] is True


def test_ollama_travamento_sem_token_dentro_do_prazo_e_falha_transitoria(servidor_de_fluxo):
    servidor = servidor_de_fluxo([(0, _pedaco('{"itens": [')), (3.0, FIM)])
    antes = time.monotonic()
    with pytest.raises(ErroProvedor) as erro:
        servidor.provedor().gerar(
            ia.INSTRUCOES,
            "texto",
            ia.ESQUEMA_RESPOSTA,
            Controle(limite_segundos=30, sem_token_segundos=0.3),
        )
    assert erro.value.causa == "travamento" and erro.value.transitoria is True
    assert "sem enviar nenhum token" in str(erro.value)
    assert time.monotonic() - antes < 2.5  # não esperou o servidor destravar


def test_ollama_bloco_lento_passa_do_tempo_limite_mesmo_recebendo_tokens(servidor_de_fluxo):
    servidor = servidor_de_fluxo([(0.05, _pedaco("x")) for _ in range(200)] + [(0, FIM)])
    antes = time.monotonic()
    with pytest.raises(ErroProvedor) as erro:
        servidor.provedor().gerar(
            ia.INSTRUCOES,
            "texto",
            ia.ESQUEMA_RESPOSTA,
            Controle(limite_segundos=0.4, sem_token_segundos=5),
        )
    # Os tokens chegavam (não é travamento), mas o bloco passou do tempo dele.
    assert erro.value.causa == "tempo_esgotado" and erro.value.transitoria is False
    assert 0.4 <= time.monotonic() - antes < 3
    # A conexão foi fechada: o servidor para de gerar em vez de continuar até o fim.
    assert servidor.cliente_fechou.wait(5)


def test_ollama_cancelamento_fecha_a_conexao_no_meio_do_fluxo(servidor_de_fluxo):
    servidor = servidor_de_fluxo([(0.02, _pedaco("x")) for _ in range(300)] + [(0, FIM)])
    consultas = {"n": 0}

    def interromper() -> str | None:
        consultas["n"] += 1
        return "cancelada" if consultas["n"] > 4 else None

    with pytest.raises(GeracaoInterrompida) as parada:
        servidor.provedor().gerar(
            ia.INSTRUCOES, "texto", ia.ESQUEMA_RESPOSTA, Controle(interromper=interromper)
        )
    assert parada.value.motivo == "cancelada"
    assert servidor.cliente_fechou.wait(5)


def test_ollama_fluxo_interrompido_pelo_servidor_e_erro_no_fluxo(servidor_de_fluxo):
    cortado = servidor_de_fluxo([(0, _pedaco('{"itens"'))])  # fecha sem ``done``
    with pytest.raises(ErroProvedor) as erro:
        cortado.provedor().gerar(ia.INSTRUCOES, "texto", ia.ESQUEMA_RESPOSTA)
    assert erro.value.causa == "conexao" and erro.value.transitoria is True
    com_erro = servidor_de_fluxo([(0, {"error": "modelo sintético sem memória"})])
    with pytest.raises(ErroProvedor) as erro:
        com_erro.provedor().gerar(ia.INSTRUCOES, "texto", ia.ESQUEMA_RESPOSTA)
    assert erro.value.causa == "resposta_invalida" and "sintético" not in str(erro.value)


def test_travamento_real_e_repetido_e_a_execucao_conclui(tmp_path, servidor_de_fluxo):
    """Do começo ao fim com o provedor do Ollama: a primeira chamada trava (nenhum token no
    prazo), a repetição responde, e a execução termina com sucesso."""
    resposta = json.dumps(
        {"itens": [{"tipo": "valor", "valor": "R$ 1.000,00", "trecho": _linha(1)}]},
        ensure_ascii=False,
    )
    servidor = servidor_de_fluxo([(0, _pedaco(resposta)), (0, FIM)])
    normal = list(servidor.roteiro)
    travado = [(1.5, FIM)]
    chamadas = {"n": 0}

    class Provedor(ProvedorOllama):
        def gerar(self, instrucoes, texto, esquema, controle=None):
            chamadas["n"] += 1
            servidor.roteiro = travado if chamadas["n"] == 1 else normal
            return super().gerar(instrucoes, texto, esquema, controle)

    provedor = Provedor(servidor.url, "modelo-sintetico:7b", timeout_segundos=5)
    app = _app(tmp_path, provedor, ia_bloco_tokens=1000, ia_sem_token_segundos=0.3)
    with TestClient(app) as cliente:
        caso_id, (captura_id,) = _caso(cliente)
        extracao = extrair_e_esperar(cliente, caso_id, captura_id, H_ANA).json()
        assert extracao["situacao"] == "sucesso" and extracao["itens_aceitos"] == 1
        bloco = extracao["metricas"]["blocos"][0]
        assert bloco["tentativas"] == 2 and [f["causa"] for f in bloco["falhas"]] == ["travamento"]
        assert extracao["parametros"]["sem_token_segundos"] == 0.3
        assert extracao["parametros"]["stream"] is True


# ---- PostgreSQL (só com TEST_POSTGRES_URL e TEST_POSTGRES_URL_APP) -------------------------------

URL_DONO = os.environ.get("TEST_POSTGRES_URL", "")
URL_APP = os.environ.get("TEST_POSTGRES_URL_APP", "")
PAPEL_APP = os.environ.get("TEST_POSTGRES_PAPEL_APP", "osint_app")
exige_postgres = pytest.mark.skipif(
    not (URL_DONO and URL_APP), reason="defina TEST_POSTGRES_URL e TEST_POSTGRES_URL_APP"
)


@pytest.fixture
def postgres_vazio():
    dono = criar_engine(URL_DONO)
    with dono.begin() as conexao:
        conexao.execute(text("DROP SCHEMA public CASCADE"))
        conexao.execute(text("CREATE SCHEMA public"))
    yield dono
    dono.dispose()


@exige_postgres
def test_postgres_extracao_em_segundo_plano_com_o_papel_da_aplicacao(postgres_vazio, tmp_path):
    """No PostgreSQL, com o papel da aplicação: a coluna da situação alargada num banco de
    versão anterior, a trava do documento, a fila, a falha parcial, a retomada e o
    cancelamento."""
    dono = postgres_vazio
    criar_esquema(dono)
    # Banco de versão anterior: a situação tinha 15 caracteres ("concluida_com_falhas" tem 20).
    with dono.begin() as conexao:
        conexao.execute(text("ALTER TABLE extracoes_ia ALTER COLUMN situacao TYPE VARCHAR(15)"))
    assert criar_esquema(dono) == ["extracoes_ia.situacao (tamanho 30)"]
    assert criar_esquema(dono) == []
    conceder_privilegios(dono, PAPEL_APP)

    liberar = threading.Event()

    def acao(_texto, chamada, controle):
        if chamada == 2:
            return Geracao("isto não é JSON")
        if chamada == 3:
            while not liberar.wait(0.005):
                controle.conferir_interrupcao()
        return None

    provedor = ProvedorRoteirizado(acao)
    app = _app(tmp_path, provedor, documentos=2, database_url=URL_APP, db_criar_esquema=False)
    with TestClient(app) as cliente:
        caso_id, (primeira, segunda) = _caso(cliente)
        extracao_id = _pedir(cliente, caso_id, primeira).json()["id"]
        esperar_extracao(cliente, caso_id, extracao_id, ate=lambda p: p["bloco_atual"] == 3)
        assert _pedir(cliente, caso_id, primeira).status_code == 409
        na_fila = _pedir(cliente, caso_id, segunda).json()["id"]
        assert _progresso(cliente, caso_id, na_fila)["posicao_na_fila"] == 1
        r = cliente.post(f"/casos/{caso_id}/extracoes-ia/{na_fila}/cancelar", headers=H_ANA)
        assert r.status_code == 200 and r.json()["situacao"] == "cancelada"
        liberar.set()
        final = esperar_extracao(cliente, caso_id, extracao_id)
        assert final["situacao"] == "concluida_com_falhas" and final["blocos_falhos"] == 1
        provedor.acao = None
        r = cliente.post(f"/casos/{caso_id}/extracoes-ia/{extracao_id}/retomar", headers=H_ANA)
        assert r.status_code == 202, r.text
        retomada = esperar_extracao(cliente, caso_id, extracao_id)
        assert retomada["situacao"] == "sucesso"
        assert retomada["itens_aceitos"] == 2 * LINHAS_POR_DOCUMENTO
        # Cancelamento no meio da geração, com a linha da execução travada pelo trabalhador.
        provedor.acao = _segurar_ate(threading.Event(), na_chamada=len(provedor.chamadas) + 2)
        outra = _pedir(cliente, caso_id, segunda).json()["id"]
        esperar_extracao(cliente, caso_id, outra, ate=lambda p: p["bloco_atual"] == 2)
        assert cliente.post(
            f"/casos/{caso_id}/extracoes-ia/{outra}/cancelar", headers=H_ANA
        ).json()["cancelamento_pedido"]
        assert esperar_extracao(cliente, caso_id, outra)["situacao"] == "cancelada"
        assert _acoes_ia(app)[-2:] == [
            ("ia.extracao_cancelar", "ana"),
            ("ia.extracao_cancelada", "extrator"),
        ]
        assert _cadeia_integra(app)
    app.state.engine.dispose()
