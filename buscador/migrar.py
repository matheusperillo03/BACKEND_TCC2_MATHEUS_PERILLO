"""Cria ou atualiza o esquema, os gatilhos da auditoria e os privilégios do papel da aplicação.

É idempotente: num banco de versão anterior, acrescenta as tabelas e colunas novas sem apagar
nada (``buscador.db.criar_esquema``) e refaz as concessões, que passam a cobrir as tabelas novas.

Roda com a credencial do papel DONO do banco, separada da credencial que a API usa:

    DATABASE_URL=postgresql+psycopg://<dono>:<senha>@host/banco DB_PAPEL_APP=osint_app \\
        python -m buscador.migrar

No Docker Compose este passo é o serviço ``migracao``, que termina antes de a API subir. A API
recebe só a credencial do papel da aplicação e inicia com ``DB_CRIAR_ESQUEMA=0``.
"""

from __future__ import annotations

import os
import sys

from buscador.db import conceder_privilegios, criar_engine, criar_esquema


def main() -> int:
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        print("Defina DATABASE_URL com a credencial do papel dono do banco.", file=sys.stderr)
        return 2
    papel = os.environ.get("DB_PAPEL_APP", "").strip()
    engine = criar_engine(url)
    try:
        acrescentados = criar_esquema(engine)
        if papel:
            conceder_privilegios(engine, papel)
    finally:
        engine.dispose()
    print(
        "Esquema e gatilhos da auditoria criados"
        + (f"; privilégios concedidos ao papel '{papel}'." if papel else ".")
    )
    if acrescentados:
        print("Acrescentado a tabelas que já existiam: " + ", ".join(acrescentados) + ".")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
