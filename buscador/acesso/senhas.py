"""Senhas dos usuários (RNF01, H23): Argon2id, pela biblioteca ``argon2-cffi``.

Escolha do algoritmo: o Argon2id é a variante recomendada pela RFC 9106 ("Argon2 Memory-Hard
Function for Password Hashing and Proof-of-Work Applications", seção 4) quando não se sabe de
antemão qual ataque é mais provável, por resistir tanto a ataques de canal lateral quanto a
ataques com GPU. Os parâmetros são os padrões do ``argon2-cffi`` (perfil de memória reduzida da
RFC 9106: 64 MiB, 3 iterações, 4 vias), gravados no próprio hash; ``precisa_atualizar`` permite
elevar o custo depois sem invalidar as senhas.
"""

from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

_hasher = PasswordHasher()
# Hash de uma senha que ninguém usa: verificado quando o usuário não existe, para que a resposta
# leve o mesmo tempo e não revele quais logins existem.
_HASH_FICTICIO = _hasher.hash("senha-que-nao-pertence-a-ninguem")

MAX_SENHA = 256


class SenhaFraca(ValueError):
    """A senha não atende à política mínima."""


def validar_politica(senha: str, usuario: str, tamanho_minimo: int) -> None:
    """Política: tamanho mínimo e não conter o próprio login. Sem regras de composição: o que
    protege é o comprimento, e o segundo fator cobre a senha adivinhada ou vazada."""
    if len(senha) < tamanho_minimo:
        raise SenhaFraca(f"A senha deve ter ao menos {tamanho_minimo} caracteres.")
    if len(senha) > MAX_SENHA:
        raise SenhaFraca(f"A senha deve ter no máximo {MAX_SENHA} caracteres.")
    if usuario and usuario.lower() in senha.lower():
        raise SenhaFraca("A senha não pode conter o nome de usuário.")
    if len(set(senha)) < 5:
        raise SenhaFraca("A senha tem caracteres repetidos demais.")


def gerar_hash(senha: str) -> str:
    return _hasher.hash(senha)


def conferir(hash_gravado: str | None, senha: str) -> bool:
    """Verdadeiro se a senha confere. Sem hash (usuário inexistente), gasta o mesmo tempo."""
    try:
        return _hasher.verify(hash_gravado or _HASH_FICTICIO, senha[:MAX_SENHA]) and bool(
            hash_gravado
        )
    except (VerificationError, InvalidHashError):
        return False


def precisa_atualizar(hash_gravado: str) -> bool:
    return _hasher.check_needs_rehash(hash_gravado)
