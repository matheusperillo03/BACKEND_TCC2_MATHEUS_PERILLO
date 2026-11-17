import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from buscador import auditoria
from buscador.modelos import Auditoria
from tests.conftest import CHAVE


def _registrar(sessao, n: int) -> list[Auditoria]:
    linhas = [
        auditoria.registrar(
            sessao,
            CHAVE,
            usuario="ana",
            acao=f"acao.{i}",
            caso_id="caso1",
            justificativa=f"justificativa {i}",
            detalhes={"i": i, "texto": "ação com acentuação"},
        )
        for i in range(n)
    ]
    sessao.commit()
    return linhas


def test_cadeia_encadeia_hashes_e_verifica(sessao):
    linhas = _registrar(sessao, 3)
    assert linhas[0].hash_anterior == auditoria.HASH_GENESE
    assert linhas[1].hash_anterior == linhas[0].hash
    assert linhas[2].hash_anterior == linhas[1].hash
    assert [linha.seq for linha in linhas] == [1, 2, 3]
    for linha in linhas:
        entrada = {
            "versao": linha.versao,
            "seq": linha.seq,
            "quando": linha.quando,
            "usuario": linha.usuario,
            "acao": linha.acao,
            "caso_id": linha.caso_id,
            "justificativa": linha.justificativa,
            "detalhes": linha.detalhes,
            "hash_anterior": linha.hash_anterior,
        }
        assert auditoria.calcular_hash(entrada, CHAVE) == linha.hash
    resultado = auditoria.verificar(sessao, CHAVE)
    assert resultado.integra is True
    assert resultado.entradas == 3
    assert resultado.primeira_quebra is None


def test_json_canonico_e_deterministico():
    a = auditoria.json_canonico({"b": 1, "a": {"y": "é", "x": [1, 2]}})
    b = auditoria.json_canonico({"a": {"x": [1, 2], "y": "é"}, "b": 1})
    assert a == b == '{"a":{"x":[1,2],"y":"\\u00e9"},"b":1}'


def test_adulteracao_via_sql_e_detectada(sessao, engine):
    _registrar(sessao, 4)
    # Um atacante com acesso ao banco derruba o gatilho e altera a justificativa da entrada 2.
    with engine.begin() as conexao:
        conexao.execute(text("DROP TRIGGER auditoria_sem_update"))
        conexao.execute(
            text("UPDATE auditoria SET justificativa = 'texto adulterado' WHERE seq = 2")
        )
    sessao.expire_all()
    resultado = auditoria.verificar(sessao, CHAVE)
    assert resultado.integra is False
    assert resultado.primeira_quebra == 2
    assert "não confere" in resultado.motivo


def test_remocao_de_entrada_quebra_a_cadeia(sessao, engine):
    _registrar(sessao, 3)
    with engine.begin() as conexao:
        conexao.execute(text("DROP TRIGGER auditoria_sem_delete"))
        conexao.execute(text("DELETE FROM auditoria WHERE seq = 2"))
    sessao.expire_all()
    resultado = auditoria.verificar(sessao, CHAVE)
    assert resultado.integra is False
    assert resultado.primeira_quebra == 3


def test_gatilho_do_banco_bloqueia_update_e_delete(sessao, engine):
    _registrar(sessao, 1)
    with pytest.raises(DBAPIError), engine.begin() as conexao:
        conexao.execute(text("UPDATE auditoria SET acao = 'x' WHERE seq = 1"))
    with pytest.raises(DBAPIError), engine.begin() as conexao:
        conexao.execute(text("DELETE FROM auditoria WHERE seq = 1"))
    sessao.expire_all()
    assert auditoria.verificar(sessao, CHAVE).integra is True


def test_orm_recusa_update_e_delete(sessao):
    _registrar(sessao, 1)
    linha = sessao.execute(select(Auditoria)).scalar_one()
    linha.acao = "alterada"
    with pytest.raises(RuntimeError):
        sessao.flush()
    sessao.rollback()
    linha = sessao.execute(select(Auditoria)).scalar_one()
    sessao.delete(linha)
    with pytest.raises(RuntimeError):
        sessao.flush()
    sessao.rollback()


def test_listar_filtra_por_caso(sessao):
    _registrar(sessao, 2)
    auditoria.registrar(sessao, CHAVE, usuario="x", acao="a", caso_id="outro", justificativa=None)
    sessao.commit()
    assert len(auditoria.listar(sessao)) == 3
    assert len(auditoria.listar(sessao, "caso1")) == 2
    assert auditoria.listar(sessao, "outro")[0]["justificativa"] is None
