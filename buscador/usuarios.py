"""Cria o primeiro usuário (ou redefine uma conta) pela linha de comando (RNF01, H23).

A API só cria contas para quem já é ``admin``; este comando resolve o primeiro acesso. Roda no
servidor, com as mesmas variáveis do serviço (``DATABASE_URL``, ``AUDITORIA_CHAVE_HMAC`` e a
chave da custódia), e deixa a criação na trilha de auditoria como o usuário de sistema ``cli``.

    python -m buscador.usuarios criar <usuario> <perfil> [--nome "Nome"]
    python -m buscador.usuarios redefinir <usuario> [--senha] [--mfa]
    docker compose exec api python -m buscador.usuarios criar ana.silva admin

A senha é pedida no terminal (sem eco) ou lida de ``SENHA_INICIAL``, para uso automatizado. O
segundo fator (TOTP) é cadastrado pelo próprio usuário no primeiro login.
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys

from buscador import auditoria
from buscador.acesso import servico as acesso
from buscador.config import PERFIS, Configuracao, ErroConfiguracao
from buscador.db import criar_engine, criar_fabrica_sessoes, verificar_esquema


def _senha() -> str:
    senha = os.environ.get("SENHA_INICIAL", "")
    if senha:
        return senha
    senha = getpass.getpass("Senha: ")
    if senha != getpass.getpass("Repita a senha: "):
        raise SystemExit("As senhas não conferem.")
    return senha


def main(argumentos: list[str] | None = None, config: Configuracao | None = None) -> int:
    analisador = argparse.ArgumentParser(prog="python -m buscador.usuarios")
    comandos = analisador.add_subparsers(dest="comando", required=True)
    criar = comandos.add_parser("criar", help="cria uma conta")
    criar.add_argument("usuario")
    criar.add_argument("perfil", choices=PERFIS)
    criar.add_argument("--nome", default="")
    redefinir = comandos.add_parser("redefinir", help="redefine a senha e/ou o segundo fator")
    redefinir.add_argument("usuario")
    redefinir.add_argument("--senha", action="store_true", help="define uma senha nova")
    redefinir.add_argument("--mfa", action="store_true", help="apaga o segundo fator cadastrado")
    args = analisador.parse_args(argumentos)

    try:
        config = config or Configuracao()
        chave = config.chave_auditoria()
        config.cofre()
    except ErroConfiguracao as erro:
        print(str(erro), file=sys.stderr)
        return 2
    engine = criar_engine(config.database_url)
    try:
        verificar_esquema(engine)
        with criar_fabrica_sessoes(engine)() as sessao:
            try:
                if args.comando == "criar":
                    conta = acesso.criar_conta(
                        sessao,
                        config,
                        usuario=args.usuario,
                        perfil=args.perfil,
                        senha=_senha(),
                        nome=args.nome,
                        criado_por="cli",
                    )
                    acao, detalhes = "usuario.criar", {"perfil_criado": conta.perfil}
                else:
                    conta = acesso.obter_conta(sessao, args.usuario.strip().lower())
                    if conta is None:
                        print("Usuário não encontrado.", file=sys.stderr)
                        return 1
                    if not (args.senha or args.mfa):
                        print("Informe --senha e/ou --mfa.", file=sys.stderr)
                        return 2
                    if args.senha:
                        acesso.definir_senha(sessao, config, conta, _senha())
                    if args.mfa:
                        acesso.redefinir_mfa(conta)
                    encerradas = acesso.encerrar_sessoes(sessao, conta, "alteracao_da_conta")
                    acao = "usuario.alterar"
                    detalhes = {
                        "mudancas": {
                            "senha_redefinida": args.senha,
                            "mfa_redefinido": args.mfa,
                            "sessoes_encerradas": encerradas,
                        }
                    }
            except acesso.ErroAcesso as erro:
                print(str(erro), file=sys.stderr)
                return 1
            entrada = auditoria.registrar(
                sessao,
                chave,
                usuario="cli",
                acao=acao,
                caso_id=None,
                justificativa="Administração de contas pela linha de comando",
                detalhes={"usuario_alterado": conta.usuario, **detalhes},
            )
            sessao.commit()
            auditoria.Ancora(config.dados_dir, chave).avancar(entrada.seq, entrada.hash)
            mfa = "obrigatório" if conta.perfil in config.perfis_com_mfa() else "opcional"
            print(
                f"Conta '{conta.usuario}' ({conta.perfil}) pronta. Segundo fator ({mfa}): "
                "cadastrado pelo usuário no primeiro login."
            )
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
