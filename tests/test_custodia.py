import hashlib

from buscador.custodia import Custodia
from buscador.modelos import Caso


def _caso(sessao) -> Caso:
    caso = Caso(titulo="c", criado_por="t")
    sessao.add(caso)
    sessao.flush()
    return caso


def test_registrar_guarda_bytes_como_vieram_e_sha256(sessao, custodia: Custodia):
    caso = _caso(sessao)
    conteudo = "conteúdo bruto ✓".encode()
    captura = custodia.registrar(
        sessao,
        caso_id=caso.id,
        fonte="pncp",
        url_ou_arquivo="https://exemplo.invalid/x",
        conteudo=conteudo,
        metadados={"http_status": 200},
    )
    assert captura.sha256 == hashlib.sha256(conteudo).hexdigest()
    assert captura.tamanho_bytes == len(conteudo)
    assert captura.coletado_em.endswith("Z")
    assert custodia.ler(captura) == conteudo
    assert captura.metadados == {"http_status": 200, "simulado": False}


def test_verificar_detecta_arquivo_alterado_e_ausente(sessao, custodia: Custodia):
    caso = _caso(sessao)
    captura = custodia.registrar(
        sessao, caso_id=caso.id, fonte="cnpj_arquivo", url_ou_arquivo="/a", conteudo=b"abc"
    )
    assert custodia.verificar(captura).integra is True

    caminho = custodia.raiz / captura.caminho_relativo
    caminho.write_bytes(b"abd")
    resultado = custodia.verificar(captura)
    assert resultado.integra is False
    assert resultado.sha256_recalculado != resultado.sha256_registrado

    caminho.unlink()
    resultado = custodia.verificar(captura)
    assert resultado.integra is False
    assert resultado.arquivo_encontrado is False
    assert resultado.sha256_recalculado is None


def test_mesmo_conteudo_gera_capturas_distintas_no_mesmo_arquivo(sessao, custodia: Custodia):
    caso = _caso(sessao)
    a = custodia.registrar(sessao, caso_id=caso.id, fonte="pncp", url_ou_arquivo="u", conteudo=b"x")
    b = custodia.registrar(sessao, caso_id=caso.id, fonte="pncp", url_ou_arquivo="u", conteudo=b"x")
    assert a.id != b.id
    assert a.sha256 == b.sha256
    assert a.caminho_relativo == b.caminho_relativo
